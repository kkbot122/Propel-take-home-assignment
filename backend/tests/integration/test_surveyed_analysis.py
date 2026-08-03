from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import redis.asyncio as redis
from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from propel.analysis.models import NetworkSnapshot
from propel.domain.enums import (
    DeviceHealthStatus,
    LocalizationPrecision,
    PoleStatus,
    TelemetryEventType,
    TopologySource,
)
from propel.infra.analysis import PostgresDtSnapshotRepository, RedisAnalysisScheduler
from propel.infra.database.models import Device, DeviceHealth, Pole, PoleState, TelemetryEvent
from propel.infra.settings import get_settings
from propel.infra.telemetry import RedisTelemetryPublisher
from propel.infra.telemetry_processor import PostgresTelemetryProcessor
from propel.telemetry.consumer import RedisTelemetryConsumer
from propel.telemetry.ingestion import TelemetryCommand, TelemetryEnvelope

pytestmark = pytest.mark.integration


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


@dataclass(slots=True)
class AnalysisHarness:
    engine: AsyncEngine
    redis: Redis
    stream: str
    group: str
    due_set: str
    dead_letter_stream: str
    pole_ids: dict[str, int]
    device_ids: dict[str, int]
    event_ids: set[UUID] = field(default_factory=set)

    async def publish(
        self,
        pole_id: str,
        event_type: TelemetryEventType,
        energized: bool,
        received_at: datetime,
    ) -> TelemetryEnvelope:
        envelope = TelemetryEnvelope(
            event_id=uuid4(),
            correlation_id=uuid4(),
            received_at=received_at,
            command=TelemetryCommand(
                device_id=f"DEV-{pole_id}",
                pole_id=pole_id,
                event=event_type,
                energized=energized,
                device_timestamp=received_at - timedelta(milliseconds=50),
                sequence=1,
                battery_mv=3480,
                rssi=-91,
                firmware="1.4.2",
            ),
        )
        self.event_ids.add(envelope.event_id)
        await RedisTelemetryPublisher(self.redis, self.stream).publish(envelope)
        return envelope

    def consumer(self) -> RedisTelemetryConsumer:
        return RedisTelemetryConsumer(
            self.redis,
            PostgresTelemetryProcessor(self.engine),
            stream_name=self.stream,
            group_name=self.group,
            consumer_name=f"{self.group}-consumer",
            dead_letter_stream_name=self.dead_letter_stream,
            analysis_due_set_name=self.due_set,
            batch_size=50,
            block_ms=1,
            pending_idle_ms=0,
            max_deliveries=3,
            analysis_debounce_seconds=10,
        )


class FailingSnapshotRepository:
    async def load(self, dt_id: str) -> NetworkSnapshot:
        raise RuntimeError(f"forced snapshot failure for {dt_id}")


@pytest_asyncio.fixture
async def analysis_harness() -> AsyncIterator[AnalysisHarness]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    suffix = uuid4().hex
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory.begin() as session:
        pole_rows = (
            await session.execute(
                select(Pole.pole_id, Pole.id).where(
                    Pole.pole_id.in_(("P-001", "P-002", "P-003", "P-004"))
                )
            )
        ).all()
        device_rows = (
            await session.execute(
                select(Device.device_id, Device.id).where(
                    Device.device_id.in_(("DEV-P-001", "DEV-P-002", "DEV-P-003", "DEV-P-004"))
                )
            )
        ).all()
        pole_ids = dict(pole_rows)
        device_ids = dict(device_rows)
        assert len(pole_ids) == 4 and len(device_ids) == 4

        pole_snapshots = {
            row.pole_id: dict(row._mapping)
            for row in (
                await session.execute(
                    select(Pole.pole_id, PoleState.__table__)
                    .join(PoleState, PoleState.pole_id == Pole.id)
                    .where(Pole.id.in_(pole_ids.values()))
                )
            ).all()
        }
        health_snapshots = {
            row.device_external_id: dict(row._mapping)
            for row in (
                await session.execute(
                    select(Device.device_id.label("device_external_id"), DeviceHealth.__table__)
                    .join(DeviceHealth, DeviceHealth.device_id == Device.id)
                    .where(Device.id.in_(device_ids.values()))
                )
            ).all()
        }
        baseline_at = datetime.now(UTC) - timedelta(seconds=20)
        await session.execute(
            update(PoleState)
            .where(PoleState.pole_id.in_(pole_ids.values()))
            .values(
                state=PoleStatus.LIVE,
                source_event_id=None,
                device_sequence=None,
                device_timestamp=None,
                received_at=baseline_at,
                firmware="1.4.2",
                battery_mv=None,
                rssi=None,
                reason="vs05_test_baseline",
                updated_at=baseline_at,
            )
        )
        await session.execute(
            update(DeviceHealth)
            .where(DeviceHealth.device_id.in_(device_ids.values()))
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
                status_reason="vs05_test_baseline",
                can_report_power_loss=True,
                updated_at=baseline_at,
            )
        )

    harness = AnalysisHarness(
        engine=engine,
        redis=redis_client,
        stream=f"test:analysis:telemetry:{suffix}",
        group=f"test-analysis-workers-{suffix}",
        due_set=f"test:analysis:due:{suffix}",
        dead_letter_stream=f"test:analysis:dlq:{suffix}",
        pole_ids=pole_ids,
        device_ids=device_ids,
    )
    try:
        yield harness
    finally:
        await redis_client.delete(
            harness.stream,
            harness.due_set,
            harness.dead_letter_stream,
        )
        async with session_factory.begin() as session:
            for external_id, snapshot in pole_snapshots.items():
                await session.execute(
                    update(PoleState)
                    .where(PoleState.pole_id == pole_ids[external_id])
                    .values(
                        **{
                            key: value
                            for key, value in snapshot.items()
                            if key not in {"pole_id_1", "pole_id"}
                        }
                    )
                )
            for external_id, snapshot in health_snapshots.items():
                await session.execute(
                    update(DeviceHealth)
                    .where(DeviceHealth.device_id == device_ids[external_id])
                    .values(
                        **{
                            key: value
                            for key, value in snapshot.items()
                            if key not in {"device_external_id", "device_id"}
                        }
                    )
                )
            if harness.event_ids:
                await session.execute(
                    delete(TelemetryEvent).where(TelemetryEvent.event_id.in_(harness.event_ids))
                )
        await redis_client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_debounced_worker_snapshot_localizes_fixed_surveyed_fault(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC) - timedelta(seconds=2)
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()
    await analysis_harness.publish("P-001", TelemetryEventType.HEARTBEAT, True, base)
    await analysis_harness.publish(
        "P-002", TelemetryEventType.POWER_LOST, False, base + timedelta(milliseconds=100)
    )
    await analysis_harness.publish(
        "P-003", TelemetryEventType.POWER_LOST, False, base + timedelta(milliseconds=200)
    )
    await analysis_harness.publish(
        "P-004", TelemetryEventType.POWER_LOST, False, base + timedelta(milliseconds=300)
    )
    assert await consumer.consume_new_once() == 4

    due_at = base + timedelta(seconds=10, milliseconds=300)
    clock = MutableClock(due_at - timedelta(milliseconds=1))
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        clock=clock,
    )

    assert await scheduler.run_due_once() == []
    assert await analysis_harness.redis.zscore(analysis_harness.due_set, "DT-001") == pytest.approx(
        due_at.timestamp()
    )

    clock.value = due_at
    candidates = await scheduler.run_due_once()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.suspected_asset_id == "P-001->P-002"
    assert candidate.affected_pole_ids == ("P-002", "P-003", "P-004")
    assert candidate.precision == LocalizationPrecision.EXACT_SPAN
    assert candidate.topology_source == TopologySource.SURVEYED
    assert candidate.latitude == pytest.approx((12.889250 + 12.889430) / 2)
    assert candidate.longitude == pytest.approx((77.584120 + 77.584260) / 2)
    assert candidate.pin_code == "560078"
    assert candidate.confidence_score == 100
    assert await analysis_harness.redis.zscore(analysis_harness.due_set, "DT-001") is None

    snapshot = await PostgresDtSnapshotRepository(analysis_harness.engine).load("DT-001")
    assert isinstance(snapshot, NetworkSnapshot)
    assert {pole.pole_id for pole in snapshot.poles} == {
        "P-001",
        "P-002",
        "P-003",
        "P-004",
    }
    assert all(pole.device is not None for pole in snapshot.poles)
    assert len(snapshot.spans) == 4
    assert all(span.source == TopologySource.SURVEYED for span in snapshot.spans)


@pytest.mark.asyncio
async def test_failed_analysis_is_rescheduled_without_losing_the_dt(
    analysis_harness: AnalysisHarness,
) -> None:
    now = datetime.now(UTC)
    await analysis_harness.redis.zadd(
        analysis_harness.due_set,
        {"DT-001": now.timestamp()},
    )
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        FailingSnapshotRepository(),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        clock=MutableClock(now),
    )

    assert await scheduler.run_due_once() == []
    assert await analysis_harness.redis.zscore(analysis_harness.due_set, "DT-001") == pytest.approx(
        (now + timedelta(seconds=5)).timestamp()
    )
