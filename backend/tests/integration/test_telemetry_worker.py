from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import redis.asyncio as redis
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from propel.domain.enums import (
    DeviceHealthStatus,
    PoleStatus,
    ProcessingOutcome,
    TelemetryEventType,
)
from propel.infra.database.models import Device, DeviceHealth, Pole, PoleState, TelemetryEvent
from propel.infra.settings import get_settings
from propel.infra.telemetry import RedisTelemetryPublisher
from propel.infra.telemetry_processor import PostgresTelemetryProcessor
from propel.telemetry.consumer import RedisTelemetryConsumer
from propel.telemetry.ingestion import TelemetryCommand, TelemetryEnvelope
from propel.telemetry.messages import parse_stream_message

pytestmark = pytest.mark.integration


@dataclass(slots=True)
class WorkerHarness:
    engine: AsyncEngine
    redis: Redis
    stream: str
    group: str
    consumer_name: str
    dead_letter_stream: str
    analysis_due_set: str
    pole_id: int
    device_id: int
    event_ids: set[UUID] = field(default_factory=set)

    def consumer(
        self,
        processor: object | None = None,
        *,
        max_deliveries: int = 3,
        pending_idle_ms: int = 0,
    ) -> RedisTelemetryConsumer:
        return RedisTelemetryConsumer(
            self.redis,
            processor or PostgresTelemetryProcessor(self.engine),
            stream_name=self.stream,
            group_name=self.group,
            consumer_name=self.consumer_name,
            dead_letter_stream_name=self.dead_letter_stream,
            analysis_due_set_name=self.analysis_due_set,
            batch_size=50,
            block_ms=1,
            pending_idle_ms=pending_idle_ms,
            max_deliveries=max_deliveries,
            analysis_debounce_seconds=10,
        )

    async def publish(
        self,
        event_type: TelemetryEventType,
        energized: bool,
        sequence: int,
        received_at: datetime,
        *,
        event_id: UUID | None = None,
    ) -> TelemetryEnvelope:
        envelope = TelemetryEnvelope(
            event_id=event_id or uuid4(),
            correlation_id=uuid4(),
            received_at=received_at,
            command=TelemetryCommand(
                device_id="DEV-P-002",
                pole_id="P-002",
                event=event_type,
                energized=energized,
                device_timestamp=received_at - timedelta(milliseconds=100),
                sequence=sequence,
                battery_mv=3480,
                rssi=-91,
                firmware="1.4.2",
            ),
        )
        self.event_ids.add(envelope.event_id)
        await RedisTelemetryPublisher(self.redis, self.stream).publish(envelope)
        return envelope


@pytest_asyncio.fixture
async def worker_harness() -> AsyncIterator[WorkerHarness]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    suffix = uuid4().hex
    stream = f"test:telemetry:{suffix}"
    dead_letter_stream = f"test:telemetry:dlq:{suffix}"
    analysis_due_set = f"test:analysis:due:{suffix}"
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory.begin() as session:
        pole_id = await session.scalar(select(Pole.id).where(Pole.pole_id == "P-002"))
        device_id = await session.scalar(select(Device.id).where(Device.device_id == "DEV-P-002"))
        assert pole_id is not None and device_id is not None
        pole_snapshot = dict(
            (await session.execute(select(PoleState.__table__).where(PoleState.pole_id == pole_id)))
            .one()
            ._mapping
        )
        health_snapshot = dict(
            (
                await session.execute(
                    select(DeviceHealth.__table__).where(DeviceHealth.device_id == device_id)
                )
            )
            .one()
            ._mapping
        )
        baseline_at = datetime.now(UTC)
        await session.execute(
            update(PoleState)
            .where(PoleState.pole_id == pole_id)
            .values(
                state=PoleStatus.LIVE,
                source_event_id=None,
                device_sequence=None,
                device_timestamp=None,
                received_at=baseline_at,
                firmware="1.4.2",
                battery_mv=None,
                rssi=None,
                reason="vs04_test_baseline",
                updated_at=baseline_at,
            )
        )
        await session.execute(
            update(DeviceHealth)
            .where(DeviceHealth.device_id == device_id)
            .values(
                status=DeviceHealthStatus.HEALTHY,
                last_seen_at=baseline_at,
                boot_generation=0,
                last_sequence=None,
                last_event_type=None,
                last_device_timestamp=None,
                firmware="1.4.2",
                battery_mv=None,
                rssi=None,
                status_reason="vs04_test_baseline",
                can_report_power_loss=True,
                updated_at=baseline_at,
            )
        )

    harness = WorkerHarness(
        engine=engine,
        redis=redis_client,
        stream=stream,
        group=f"test-workers-{suffix}",
        consumer_name=f"test-worker-{suffix}",
        dead_letter_stream=dead_letter_stream,
        analysis_due_set=analysis_due_set,
        pole_id=pole_id,
        device_id=device_id,
    )
    try:
        yield harness
    finally:
        await redis_client.delete(stream, dead_letter_stream, analysis_due_set)
        async with session_factory.begin() as session:
            await session.execute(
                update(PoleState)
                .where(PoleState.pole_id == pole_id)
                .values(**{key: value for key, value in pole_snapshot.items() if key != "pole_id"})
            )
            await session.execute(
                update(DeviceHealth)
                .where(DeviceHealth.device_id == device_id)
                .values(
                    **{key: value for key, value in health_snapshot.items() if key != "device_id"}
                )
            )
            if harness.event_ids:
                await session.execute(
                    delete(TelemetryEvent).where(TelemetryEvent.event_id.in_(harness.event_ids))
                )
        await redis_client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_is_idempotent_and_sequence_aware(worker_harness: WorkerHarness) -> None:
    consumer = worker_harness.consumer()
    await consumer.ensure_group()
    first_at = datetime.now(UTC) + timedelta(seconds=1)

    power_lost = await worker_harness.publish(TelemetryEventType.POWER_LOST, False, 101, first_at)
    assert await consumer.consume_new_once() == 1

    session_factory = async_sessionmaker(worker_harness.engine, expire_on_commit=False)
    async with session_factory() as session:
        state = await session.get(PoleState, worker_harness.pole_id)
        health = await session.get(DeviceHealth, worker_harness.device_id)
        raw = await session.scalar(
            select(TelemetryEvent).where(TelemetryEvent.event_id == power_lost.event_id)
        )
        assert state is not None and health is not None and raw is not None
        assert state.state == PoleStatus.DARK
        assert state.source_event_id == power_lost.event_id
        assert health.boot_generation == 0
        assert health.last_sequence == 101
        assert raw.processing_outcome == ProcessingOutcome.ACCEPTED
        assert raw.state_changed is True

    due_score = await worker_harness.redis.zscore(worker_harness.analysis_due_set, "DT-001")
    assert due_score == pytest.approx(first_at.timestamp() + 10)

    await worker_harness.publish(
        TelemetryEventType.POWER_LOST,
        False,
        101,
        first_at,
        event_id=power_lost.event_id,
    )
    assert await consumer.consume_new_once() == 1
    assert await worker_harness.redis.zscore(
        worker_harness.analysis_due_set, "DT-001"
    ) == pytest.approx(due_score)

    stale = await worker_harness.publish(
        TelemetryEventType.POWER_RESTORED, True, 100, first_at + timedelta(seconds=1)
    )
    assert await consumer.consume_new_once() == 1
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(TelemetryEvent.id)).where(
                    TelemetryEvent.event_id == power_lost.event_id
                )
            )
            == 1
        )
        stale_raw = await session.scalar(
            select(TelemetryEvent).where(TelemetryEvent.event_id == stale.event_id)
        )
        state = await session.get(PoleState, worker_harness.pole_id)
        assert stale_raw is not None and state is not None
        assert stale_raw.processing_outcome == ProcessingOutcome.STALE
        assert stale_raw.state_changed is False
        assert state.state == PoleStatus.DARK
        assert state.source_event_id == power_lost.event_id

    boot = await worker_harness.publish(
        TelemetryEventType.BOOT, True, 0, first_at + timedelta(seconds=2)
    )
    restored = await worker_harness.publish(
        TelemetryEventType.POWER_RESTORED, True, 1, first_at + timedelta(seconds=3)
    )
    assert await consumer.consume_new_once() == 2
    async with session_factory() as session:
        health = await session.get(DeviceHealth, worker_harness.device_id)
        state = await session.get(PoleState, worker_harness.pole_id)
        boot_raw = await session.scalar(
            select(TelemetryEvent).where(TelemetryEvent.event_id == boot.event_id)
        )
        assert health is not None and state is not None and boot_raw is not None
        assert boot_raw.boot_generation == 1
        assert boot_raw.state_changed is False
        assert health.boot_generation == 1
        assert health.last_sequence == 1
        assert state.state == PoleStatus.LIVE
        assert state.source_event_id == restored.event_id

    assert (await worker_harness.redis.xpending(worker_harness.stream, worker_harness.group))[
        "pending"
    ] == 0


@pytest.mark.asyncio
async def test_worker_restart_recovers_owned_pending_message(
    worker_harness: WorkerHarness,
) -> None:
    consumer = worker_harness.consumer()
    await consumer.ensure_group()
    envelope = await worker_harness.publish(
        TelemetryEventType.POWER_LOST, False, 7, datetime.now(UTC) + timedelta(seconds=1)
    )
    delivered = await worker_harness.redis.xreadgroup(
        worker_harness.group,
        worker_harness.consumer_name,
        streams={worker_harness.stream: ">"},
        count=1,
    )
    assert len(delivered[0][1]) == 1
    assert (await worker_harness.redis.xpending(worker_harness.stream, worker_harness.group))[
        "pending"
    ] == 1

    restarted_consumer = worker_harness.consumer()
    assert await restarted_consumer.recover_owned_pending_once() == 1

    session_factory = async_sessionmaker(worker_harness.engine, expire_on_commit=False)
    async with session_factory() as session:
        raw = await session.scalar(
            select(TelemetryEvent).where(TelemetryEvent.event_id == envelope.event_id)
        )
        state = await session.get(PoleState, worker_harness.pole_id)
        assert raw is not None and state is not None
        assert state.state == PoleStatus.DARK
    assert (await worker_harness.redis.xpending(worker_harness.stream, worker_harness.group))[
        "pending"
    ] == 0


class RollbackProcessor:
    def __init__(self, engine: AsyncEngine, harness: WorkerHarness) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._harness = harness

    async def process(self, fields: Mapping[str, str]) -> None:
        envelope = parse_stream_message(fields)
        async with self._session_factory.begin() as session:
            session.add(
                TelemetryEvent(
                    event_id=envelope.event_id,
                    correlation_id=envelope.correlation_id,
                    device_id=self._harness.device_id,
                    pole_id=self._harness.pole_id,
                    event_type=envelope.command.event,
                    energized=envelope.command.energized,
                    device_timestamp=envelope.command.device_timestamp,
                    received_at=envelope.received_at,
                    sequence=envelope.command.sequence,
                    boot_generation=0,
                    battery_mv=envelope.command.battery_mv,
                    rssi=envelope.command.rssi,
                    firmware=envelope.command.firmware,
                    processing_outcome=ProcessingOutcome.ACCEPTED,
                    state_changed=True,
                    raw_payload=dict(fields),
                )
            )
            await session.flush()
            raise RuntimeError("forced rollback")


@pytest.mark.asyncio
async def test_failed_transaction_is_rolled_back_and_left_pending(
    worker_harness: WorkerHarness,
) -> None:
    consumer = worker_harness.consumer(
        RollbackProcessor(worker_harness.engine, worker_harness), max_deliveries=3
    )
    await consumer.ensure_group()
    envelope = await worker_harness.publish(
        TelemetryEventType.POWER_LOST, False, 9, datetime.now(UTC) + timedelta(seconds=1)
    )

    assert await consumer.consume_new_once() == 1
    assert (await worker_harness.redis.xpending(worker_harness.stream, worker_harness.group))[
        "pending"
    ] == 1
    session_factory = async_sessionmaker(worker_harness.engine, expire_on_commit=False)
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(TelemetryEvent.id)).where(
                    TelemetryEvent.event_id == envelope.event_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_poison_message_moves_to_dead_letter_after_delivery_limit(
    worker_harness: WorkerHarness,
) -> None:
    consumer = worker_harness.consumer(max_deliveries=2)
    await consumer.ensure_group()
    message_id = await worker_harness.redis.xadd(
        worker_harness.stream,
        {
            "event_id": str(uuid4()),
            "device_id": "DEV-P-002",
            "pole_id": "P-002",
            "event": "power_lost",
        },
    )

    assert await consumer.consume_new_once() == 1
    assert (await worker_harness.redis.xpending(worker_harness.stream, worker_harness.group))[
        "pending"
    ] == 1
    assert await consumer.claim_abandoned_once() == 1

    assert (await worker_harness.redis.xpending(worker_harness.stream, worker_harness.group))[
        "pending"
    ] == 0
    dead_letters = await worker_harness.redis.xrange(worker_harness.dead_letter_stream)
    assert len(dead_letters) == 1
    assert dead_letters[0][1]["source_message_id"] == message_id
    assert dead_letters[0][1]["failure_reason"] == "missing_required_fields"
    assert dead_letters[0][1]["attempts"] == "2"
