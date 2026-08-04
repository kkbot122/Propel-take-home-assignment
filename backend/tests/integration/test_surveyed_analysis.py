import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from propel.analysis.models import NetworkSnapshot
from propel.api.app import create_app
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    PoleStatus,
    ScheduledOutageScope,
    SimulatorFaultStatus,
    SimulatorFaultType,
    TelemetryEventType,
    TelemetryOrigin,
    TicketStatus,
    TopologySource,
)
from propel.infra.analysis import PostgresDtSnapshotRepository, RedisAnalysisScheduler
from propel.infra.database.models import (
    Device,
    DeviceHealth,
    Incident,
    Pole,
    PoleState,
    ScheduledOutage,
    SimulatedFault,
    TelemetryEvent,
    Ticket,
    TicketEvent,
)
from propel.infra.incidents import PostgresIncidentService
from propel.infra.settings import Settings, get_settings
from propel.infra.simulator import PostgresSimulatorService
from propel.infra.telemetry import (
    PostgresPoleBindingResolver,
    RedisTelemetryPublisher,
)
from propel.infra.telemetry_processor import PostgresTelemetryProcessor
from propel.simulator.generation import generate_network
from propel.simulator.models import SimulatorEmissionReceipt
from propel.telemetry.consumer import RedisTelemetryConsumer
from propel.telemetry.ingestion import (
    TelemetryCommand,
    TelemetryEnvelope,
    TelemetryIngestionService,
)

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
    incident_ids: set[UUID] = field(default_factory=set)
    simulated_fault_ids: set[UUID] = field(default_factory=set)
    scheduled_outage_ids: set[str] = field(default_factory=set)

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


class AsgiSimulatorTelemetryGateway:
    def __init__(self) -> None:
        self.app = None

    async def emit(self, command: TelemetryCommand) -> SimulatorEmissionReceipt:
        if self.app is None:
            raise RuntimeError("ASGI simulator gateway has not been attached")
        async with AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://simulator-loopback"
        ) as client:
            response = await client.post(
                "/api/telemetry",
                json={
                    "device_id": command.device_id,
                    "pole_id": command.pole_id,
                    "event": command.event.value,
                    "energized": command.energized,
                    "ts": command.device_timestamp.isoformat(),
                    "seq": command.sequence,
                    "battery_mv": command.battery_mv,
                    "rssi": command.rssi,
                    "fw": command.firmware,
                },
                headers={"x-propel-telemetry-origin": "simulator"},
            )
        assert response.status_code == 202
        payload = response.json()
        return SimulatorEmissionReceipt(
            event_id=UUID(payload["event_id"]),
            received_at=datetime.fromisoformat(payload["received_at"]),
        )

    async def close(self) -> None:
        pass


@asynccontextmanager
async def running_operations_api(
    settings: Settings,
    incident_service: PostgresIncidentService,
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings=settings, incident_service=incident_service)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def analysis_harness() -> AsyncIterator[AnalysisHarness]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    suffix = uuid4().hex
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded_pole_ids = (
        "P-001",
        "P-002",
        "P-003",
        "P-004",
        "P-101",
        "P-102",
        "P-201",
        "P-202",
        "P-203",
        "P-204",
    )

    async with session_factory.begin() as session:
        pole_rows = (
            await session.execute(
                select(Pole.pole_id, Pole.id).where(Pole.pole_id.in_(seeded_pole_ids))
            )
        ).all()
        device_rows = (
            await session.execute(
                select(Device.device_id, Device.id).where(
                    Device.device_id.in_(tuple(f"DEV-{pole_id}" for pole_id in seeded_pole_ids))
                )
            )
        ).all()
        pole_ids = dict(pole_rows)
        device_ids = dict(device_rows)
        assert len(pole_ids) == 10 and len(device_ids) == 10

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
            if harness.scheduled_outage_ids:
                await session.execute(
                    delete(ScheduledOutage).where(
                        ScheduledOutage.outage_id.in_(harness.scheduled_outage_ids)
                    )
                )
            if harness.simulated_fault_ids:
                await session.execute(
                    delete(SimulatedFault).where(
                        SimulatedFault.fault_id.in_(harness.simulated_fault_ids)
                    )
                )
            if harness.incident_ids:
                await session.execute(
                    delete(Ticket).where(Ticket.incident_id.in_(harness.incident_ids))
                )
                await session.execute(
                    delete(Incident).where(Incident.incident_id.in_(harness.incident_ids))
                )
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
    incident_service = PostgresIncidentService(analysis_harness.engine)
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        candidate_sink=incident_service,
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

    incident = next(
        item
        for item in await incident_service.list_incidents()
        if item.fingerprint == "span:DT-001:P-001->P-002"
    )
    assert incident.fingerprint == "span:DT-001:P-001->P-002"
    assert incident.suspected_asset_id == "P-001->P-002"
    assert incident.affected_pole_ids == ("P-002", "P-003", "P-004")
    assert incident.ticket_id is not None
    persisted_score = incident.evidence["candidate"]
    assert persisted_score["score_kind"] == "EVIDENCE_SCORE"
    assert persisted_score["score_policy_version"] == "evidence-score-v1"
    assert persisted_score["raw_score"] == 100
    assert persisted_score["score_cap"] == 100
    assert persisted_score["components"] == {
        "topology_provenance": 25,
        "boundary_evidence": 30,
        "downstream_corroboration": 25,
        "temporal_coherence": 10,
        "sensor_quality": 10,
    }
    assert persisted_score["penalties"] == {
        "post_onset_live_contradictions": 0,
        "missing_or_unhealthy_evidence": 0,
    }
    analysis_harness.incident_ids.add(incident.incident_id)

    concurrent_results = await asyncio.gather(
        incident_service.persist_candidates(candidates),
        incident_service.persist_candidates(candidates),
    )
    assert {result[0].incident_id for result in concurrent_results} == {incident.incident_id}
    await incident_service.persist_candidates(
        [
            replace(
                candidate,
                analysis_at=candidate.analysis_at + timedelta(seconds=1),
                confidence_score=88,
                confidence_reason="updated corroborating evidence",
            )
        ]
    )

    updated_incident = await incident_service.get_incident(incident.incident_id)
    assert updated_incident.confidence_score == 88
    assert updated_incident.confidence_reason == "updated corroborating evidence"
    assert updated_incident.affected_pole_ids == ("P-002", "P-003", "P-004")
    session_factory = async_sessionmaker(analysis_harness.engine, expire_on_commit=False)
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(Incident.incident_id)).where(
                    Incident.fingerprint == "span:DT-001:P-001->P-002",
                    Incident.status == IncidentStatus.ACTIVE,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.incident_id == incident.incident_id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(TicketEvent.id))
                .join(Ticket)
                .where(Ticket.incident_id == incident.incident_id)
            )
            == 1
        )

    settings = get_settings()
    async with running_operations_api(settings, incident_service) as client:
        incident_list_response = await client.get("/api/incidents")
        incident_response = await client.get(f"/api/incidents/{incident.incident_id}")
        ticket_response = await client.get(f"/api/tickets/{incident.ticket_id}")
        overview_response = await client.get("/api/network/overview/FDR-001")
        poles_response = await client.get("/api/network/poles", params={"dt_id": "DT-001"})
        topology_response = await client.get("/api/network/topology/DT-001")
        subdivision_response = await client.get("/api/network/subdivision")
        subdivision_poles_response = await client.get("/api/network/subdivision/poles")
        skipped_response = await client.post(
            f"/api/tickets/{incident.ticket_id}/resolve",
            json={"actor": "operator-1", "reason": "skipped transition attempt"},
        )
        acknowledge_response = await client.post(
            f"/api/tickets/{incident.ticket_id}/acknowledge",
            json={"actor": "operator-1", "reason": "alarm reviewed"},
        )
        assign_response = await client.post(
            f"/api/tickets/{incident.ticket_id}/assign",
            json={
                "actor": "operator-1",
                "assigned_crew": "Crew-7",
                "reason": "nearest crew",
            },
        )
        resolve_response = await client.post(
            f"/api/tickets/{incident.ticket_id}/resolve",
            json={"actor": "operator-1", "reason": "repair claimed"},
        )
        verify_response = await client.post(
            f"/api/tickets/{incident.ticket_id}/verify",
            json={"actor": "operator-1"},
        )
        final_ticket_response = await client.get(f"/api/tickets/{incident.ticket_id}")

    assert incident_list_response.status_code == 200
    assert any(
        item["incident_id"] == str(incident.incident_id) for item in incident_list_response.json()
    )
    assert incident_response.status_code == 200
    assert incident_response.json()["affected_pole_ids"] == ["P-002", "P-003", "P-004"]
    assert ticket_response.status_code == 200
    assert ticket_response.json()["status"] == "DETECTED"
    assert overview_response.status_code == 200
    assert overview_response.json()["feeder_id"] == "FDR-001"
    assert overview_response.json()["substation"]["substation_id"] == "SUB-001"
    assert [item["dt_id"] for item in overview_response.json()["transformers"]] == [
        "DT-001",
        "DT-002",
        "DT-003",
    ]
    assert poles_response.status_code == 200
    assert [pole["pole_id"] for pole in poles_response.json()] == [
        "P-001",
        "P-002",
        "P-003",
        "P-004",
    ]
    assert topology_response.status_code == 200
    assert topology_response.json()["topology_version"] == 1
    assert len(topology_response.json()["spans"]) == 4
    assert subdivision_response.status_code == 200
    subdivision = subdivision_response.json()
    subdivision_poles = subdivision_poles_response.json()
    assert subdivision["name"] == "South Bengaluru subdivision"
    assert subdivision["neighborhoods"] == [
        "Anjanapura",
        "Konanakunte",
        "Kothnur",
        "JP Nagar",
    ]
    assert len(subdivision["substations"]) == 3
    assert len(subdivision["feeders"]) == 5
    assert len(subdivision["transformers"]) == 19
    assert len(subdivision["topologies"]) == 19
    assert subdivision_poles_response.status_code == 200
    assert 1_810 <= len(subdivision_poles) <= 2_210
    assert sum(len(topology["spans"]) for topology in subdivision["topologies"]) == len(
        subdivision_poles
    )
    assert {topology["source"] for topology in subdivision["topologies"]} == {
        "SURVEYED",
        "INFERRED",
    }
    assert all(
        subdivision["bounds"]["south"] <= pole["latitude"] <= subdivision["bounds"]["north"]
        and subdivision["bounds"]["west"] <= pole["longitude"] <= subdivision["bounds"]["east"]
        for pole in subdivision_poles
    )
    assert "simulator_topology_edges" not in subdivision_response.text
    assert "ground_truth" not in subdivision_response.text
    assert skipped_response.status_code == 409
    assert skipped_response.json()["error"]["code"] == "INVALID_TICKET_TRANSITION"
    assert acknowledge_response.status_code == 200
    assert acknowledge_response.json()["status"] == "ACKNOWLEDGED"
    assert assign_response.status_code == 200
    assert assign_response.json()["status"] == "CREW_ASSIGNED"
    assert assign_response.json()["assigned_crew"] == "Crew-7"
    assert resolve_response.status_code == 200
    assert resolve_response.json()["status"] == "RESOLVED"
    assert resolve_response.json()["resolution_claimed_at"] is not None
    assert verify_response.status_code == 403
    assert verify_response.json()["error"]["code"] == "AUTOMATIC_TRANSITION_ONLY"
    assert [event["to_status"] for event in final_ticket_response.json()["events"]] == [
        "DETECTED",
        "ACKNOWLEDGED",
        "CREW_ASSIGNED",
        "RESOLVED",
    ]

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
async def test_unknown_topology_localizes_through_persisted_inferred_tree_and_api(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC) - timedelta(seconds=2)
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()
    await analysis_harness.publish("P-201", TelemetryEventType.HEARTBEAT, True, base)
    await analysis_harness.publish(
        "P-202", TelemetryEventType.POWER_LOST, False, base + timedelta(milliseconds=100)
    )
    await analysis_harness.publish(
        "P-203", TelemetryEventType.POWER_LOST, False, base + timedelta(milliseconds=200)
    )
    await analysis_harness.publish(
        "P-204", TelemetryEventType.POWER_LOST, False, base + timedelta(milliseconds=300)
    )
    assert await consumer.consume_new_once() == 4

    clock = MutableClock(base + timedelta(seconds=11))
    incidents = PostgresIncidentService(analysis_harness.engine, clock=clock)
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        candidate_sink=incidents,
        clock=clock,
    )

    candidates = await scheduler.run_due_once()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.suspected_asset_id == "P-201->P-202"
    assert candidate.affected_pole_ids == ("P-202", "P-203", "P-204")
    assert candidate.precision == LocalizationPrecision.PROBABLE_SPAN
    assert candidate.precision != LocalizationPrecision.EXACT_SPAN
    assert candidate.topology_source == TopologySource.INFERRED
    assert candidate.confidence_score <= 79
    persisted = next(
        item
        for item in await incidents.list_incidents()
        if item.fingerprint == "probable-span:DT-003:P-201->P-202"
    )
    analysis_harness.incident_ids.add(persisted.incident_id)

    async with running_operations_api(get_settings(), incidents) as client:
        topology_response = await client.get("/api/network/topology/DT-003")
        incident_response = await client.get(f"/api/incidents/{persisted.incident_id}")

    assert topology_response.status_code == 200
    topology = topology_response.json()
    assert topology["source"] == "INFERRED"
    assert topology["quality_tier"] == "STRONGLY_INFERRED"
    assert topology["inference_version"] == "geo-mst-v1"
    assert len(topology["spans"]) == 4
    assert all(span["source"] == "INFERRED" for span in topology["spans"])
    assert all(span["distance_m"] > 0 for span in topology["spans"])
    assert incident_response.status_code == 200
    assert incident_response.json()["precision"] == "PROBABLE_SPAN"
    assert incident_response.json()["evidence"]["topology_source"] == "INFERRED"


@pytest.mark.asyncio
async def test_isolated_dark_sensor_is_audited_without_dispatch_ticket(
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
        "P-003", TelemetryEventType.HEARTBEAT, True, base + timedelta(milliseconds=200)
    )
    assert await consumer.consume_new_once() == 3

    clock = MutableClock(base + timedelta(seconds=11))
    incident_service = PostgresIncidentService(analysis_harness.engine)
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        candidate_sink=incident_service,
        clock=clock,
    )

    candidates = await scheduler.run_due_once()

    assert len(candidates) == 1
    assert candidates[0].classification == FaultClass.SENSOR_ANOMALY
    assert candidates[0].suppression is not None
    suppressed = next(
        item
        for item in await incident_service.list_incidents(status=IncidentStatus.SUPPRESSED)
        if item.fingerprint == "sensor:DT-001:DEV-P-002"
    )
    analysis_harness.incident_ids.add(suppressed.incident_id)
    assert suppressed.ticket_id is None
    assert suppressed.suppression_source == "telemetry-consistency-rule"
    assert suppressed.suppression_reason is not None

    repeated = await asyncio.gather(
        incident_service.persist_candidates(candidates),
        incident_service.persist_candidates(candidates),
    )
    assert {result[0].incident_id for result in repeated} == {suppressed.incident_id}
    assert all(result[0].ticket_id is None for result in repeated)

    settings = get_settings()
    async with running_operations_api(settings, incident_service) as client:
        response = await client.get("/api/incidents", params={"status": "SUPPRESSED"})

    assert response.status_code == 200
    payload = next(
        item for item in response.json() if item["incident_id"] == str(suppressed.incident_id)
    )
    assert payload["classification"] == "SENSOR_ANOMALY"
    assert payload["ticket_id"] is None
    assert payload["suppression_reason"] == suppressed.suppression_reason


@pytest.mark.asyncio
async def test_active_schedule_is_audited_without_dispatch_ticket(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC) - timedelta(seconds=2)
    outage_id = f"SO-TEST-{uuid4().hex}"
    analysis_harness.scheduled_outage_ids.add(outage_id)
    session_factory = async_sessionmaker(analysis_harness.engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        session.add(
            ScheduledOutage(
                outage_id=outage_id,
                scope=ScheduledOutageScope.SPAN,
                scope_id="P-001->P-002",
                starts_at=base - timedelta(minutes=5),
                ends_at=base + timedelta(minutes=5),
                source="integration-test-feed",
                reason="Planned jumper replacement",
            )
        )

    consumer = analysis_harness.consumer()
    await consumer.ensure_group()
    await analysis_harness.publish("P-001", TelemetryEventType.HEARTBEAT, True, base)
    for index, pole_id in enumerate(("P-002", "P-003", "P-004"), start=1):
        await analysis_harness.publish(
            pole_id,
            TelemetryEventType.POWER_LOST,
            False,
            base + timedelta(milliseconds=index * 100),
        )
    assert await consumer.consume_new_once() == 4

    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        candidate_sink=PostgresIncidentService(analysis_harness.engine),
        clock=MutableClock(base + timedelta(seconds=11)),
    )
    candidates = await scheduler.run_due_once()

    assert len(candidates) == 1
    assert candidates[0].classification == FaultClass.SCHEDULED_OUTAGE
    assert candidates[0].suppression is not None
    assert candidates[0].suppression.external_id == outage_id
    incident_service = PostgresIncidentService(analysis_harness.engine)
    suppressed = next(
        item
        for item in await incident_service.list_incidents(status=IncidentStatus.SUPPRESSED)
        if item.suppression_external_id == outage_id
    )
    analysis_harness.incident_ids.add(suppressed.incident_id)
    assert suppressed.ticket_id is None
    assert suppressed.classification == FaultClass.SCHEDULED_OUTAGE
    assert suppressed.suppression_source == "integration-test-feed"

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.incident_id == suppressed.incident_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_http_telemetry_reaches_one_persisted_incident(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC)
    clock = MutableClock(base)
    incidents = PostgresIncidentService(analysis_harness.engine, clock=clock)
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    app = create_app(
        settings=get_settings(),
        telemetry_service=ingestion,
        incident_service=incidents,
    )
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()

    events = (
        ("P-001", TelemetryEventType.HEARTBEAT, True),
        ("P-002", TelemetryEventType.POWER_LOST, False),
        ("P-003", TelemetryEventType.POWER_LOST, False),
        ("P-004", TelemetryEventType.POWER_LOST, False),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = []
            for index, (pole_id, event_type, energized) in enumerate(events):
                response = await client.post(
                    "/api/telemetry",
                    json={
                        "device_id": f"DEV-{pole_id}",
                        "pole_id": pole_id,
                        "event": event_type.value,
                        "energized": energized,
                        "ts": (base + timedelta(milliseconds=index)).isoformat(),
                        "seq": 101,
                        "battery_mv": 3480,
                        "rssi": -91,
                        "fw": "1.4.2",
                    },
                )
                assert response.status_code == 202, response.json()
                responses.append(response.json())

    analysis_harness.event_ids.update(UUID(item["event_id"]) for item in responses)
    assert await analysis_harness.redis.xlen(analysis_harness.stream) == 4
    assert await consumer.consume_new_once() == 4

    clock.value = base + timedelta(seconds=11)
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        candidate_sink=incidents,
        clock=clock,
    )
    candidates = await scheduler.run_due_once()

    assert len(candidates) == 1
    assert candidates[0].suspected_asset_id == "P-001->P-002"
    assert candidates[0].affected_pole_ids == ("P-002", "P-003", "P-004")
    persisted = next(
        item
        for item in await incidents.list_incidents()
        if item.fingerprint == "span:DT-001:P-001->P-002"
    )
    analysis_harness.incident_ids.add(persisted.incident_id)
    assert persisted.ticket_id is not None
    assert persisted.affected_pole_count == 3


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


@pytest.mark.parametrize(
    ("fault_type", "expected_classification", "expected_fingerprint", "event_count"),
    [
        (
            SimulatorFaultType.DT_FAULT,
            FaultClass.DT_FAULT,
            "dt:DT-001",
            4,
        ),
        (
            SimulatorFaultType.FEEDER_FAULT,
            FaultClass.FEEDER_FAULT,
            "feeder:FDR-001",
            10,
        ),
    ],
)
@pytest.mark.asyncio
async def test_scope_faults_classify_through_public_simulator_telemetry(
    analysis_harness: AnalysisHarness,
    fault_type: SimulatorFaultType,
    expected_classification: FaultClass,
    expected_fingerprint: str,
    event_count: int,
) -> None:
    base = datetime.now(UTC)
    clock = MutableClock(base)
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway, clock=clock)
    incidents = PostgresIncidentService(analysis_harness.engine, clock=clock)
    app = create_app(
        settings=get_settings(),
        telemetry_service=ingestion,
        incident_service=incidents,
        simulator_service=simulator,
    )
    gateway.app = app
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/simulator/faults",
                json={"fault_type": fault_type.value},
            )

    assert response.status_code == 201, response.json()
    injected = response.json()
    fault_id = UUID(injected["fault_id"])
    analysis_harness.simulated_fault_ids.add(fault_id)
    analysis_harness.event_ids.update(UUID(item) for item in injected["emitted_event_ids"])
    assert injected["fault_type"] == fault_type.value
    assert len(injected["emitted_event_ids"]) == event_count
    assert await consumer.consume_new_once() == event_count

    clock.value = base + timedelta(seconds=11)
    scheduler = RedisAnalysisScheduler(
        analysis_harness.redis,
        PostgresDtSnapshotRepository(analysis_harness.engine),
        due_set_name=analysis_harness.due_set,
        live_freshness_seconds=1_920,
        retry_delay_seconds=5,
        candidate_sink=incidents,
        clock=clock,
    )
    candidates = await scheduler.run_due_once()

    assert len(candidates) == 1
    assert candidates[0].classification == expected_classification
    persisted = next(
        item
        for item in await incidents.list_incidents()
        if item.fingerprint == expected_fingerprint
    )
    analysis_harness.incident_ids.add(persisted.incident_id)
    assert persisted.classification == expected_classification
    assert persisted.ticket_id is not None
    if fault_type == SimulatorFaultType.FEEDER_FAULT:
        assert persisted.affected_pole_count == 10
        repeated = await scheduler.run_due_once()
        assert len(repeated) == 1
        assert repeated[0].classification == FaultClass.FEEDER_FAULT
        assert (await incidents.get_incident(persisted.incident_id)).incident_id == (
            persisted.incident_id
        )


@pytest.mark.asyncio
async def test_generated_hidden_span_uses_physical_truth_and_public_ingestion(
    analysis_harness: AnalysisHarness,
) -> None:
    network = generate_network()
    scenario = next(item for item in network.scenarios if item.scenario_id == "inferred-span")
    fault = scenario.faults[0]
    assert fault.dt_id is not None
    assert fault.parent_pole_id is not None
    assert fault.child_pole_id is not None
    children: dict[str, list[str]] = {}
    for edge in network.ground_truth_edges:
        if edge.dt_id == fault.dt_id and edge.parent_pole_id is not None:
            children.setdefault(edge.parent_pole_id, []).append(edge.child_pole_id)
    expected_affected: list[str] = []
    pending = [fault.child_pole_id]
    while pending:
        pole_id = pending.pop(0)
        expected_affected.append(pole_id)
        pending.extend(sorted(children.get(pole_id, ())))

    clock = MutableClock(datetime.now(UTC))
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway, clock=clock)
    app = create_app(
        settings=get_settings(),
        telemetry_service=ingestion,
        simulator_service=simulator,
    )
    gateway.app = app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/simulator/faults",
                json={
                    "fault_type": "SPAN_FAULT",
                    "dt_id": fault.dt_id,
                    "parent_pole_id": fault.parent_pole_id,
                    "child_pole_id": fault.child_pole_id,
                },
            )

    assert response.status_code == 201, response.json()
    injected = response.json()
    simulated_fault_id = UUID(injected["fault_id"])
    analysis_harness.simulated_fault_ids.add(simulated_fault_id)
    analysis_harness.event_ids.update(UUID(item) for item in injected["emitted_event_ids"])
    assert injected["deenergized_pole_ids"] == expected_affected
    assert 0 < len(injected["emitted_event_ids"]) < len(expected_affected) + 1


@pytest.mark.asyncio
async def test_missing_device_noise_persists_and_serves_corridor_precision(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC)
    clock = MutableClock(base)
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway, clock=clock)
    incidents = PostgresIncidentService(analysis_harness.engine, clock=clock)
    app = create_app(
        settings=get_settings(),
        telemetry_service=ingestion,
        incident_service=incidents,
        simulator_service=simulator,
    )
    gateway.app = app
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/simulator/faults",
                json={
                    "fault_type": "SPAN_FAULT",
                    "dt_id": "DT-001",
                    "parent_pole_id": "P-001",
                    "child_pole_id": "P-002",
                    "missing_device_pole_ids": ["P-002"],
                },
            )
            assert response.status_code == 201, response.json()
            injected = response.json()
            fault_id = UUID(injected["fault_id"])
            analysis_harness.simulated_fault_ids.add(fault_id)
            analysis_harness.event_ids.update(UUID(item) for item in injected["emitted_event_ids"])
            assert len(injected["emitted_event_ids"]) == 3
            assert await consumer.consume_new_once() == 3

            clock.value = base + timedelta(seconds=11)
            scheduler = RedisAnalysisScheduler(
                analysis_harness.redis,
                PostgresDtSnapshotRepository(analysis_harness.engine),
                due_set_name=analysis_harness.due_set,
                live_freshness_seconds=1_920,
                retry_delay_seconds=5,
                candidate_sink=incidents,
                clock=clock,
            )
            candidates = await scheduler.run_due_once()

            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate.precision == LocalizationPrecision.CORRIDOR
            assert candidate.suspected_asset_id == "P-001..P-003"
            assert candidate.affected_pole_ids == ("P-003", "P-004")
            assert candidate.evidence.corridor is not None

            persisted = next(
                item
                for item in await incidents.list_incidents()
                if item.fingerprint == "corridor:DT-001:P-001..P-003"
            )
            analysis_harness.incident_ids.add(persisted.incident_id)
            detail_response = await client.get(f"/api/incidents/{persisted.incident_id}")

            assert detail_response.status_code == 200
            detail = detail_response.json()
            assert detail["precision"] == "CORRIDOR"
            assert detail["suspected_asset_id"] == "P-001..P-003"
            assert detail["suspected_asset_id"] != "P-001->P-003"
            assert detail["evidence"]["candidate"]["corridor"] == {
                "upstream_pole_id": "P-001",
                "downstream_pole_id": "P-003",
                "ordered_pole_ids": ["P-001", "P-002", "P-003"],
                "skipped_pole_ids": ["P-002"],
            }

            assert persisted.ticket_id is not None
            await client.post(
                f"/api/tickets/{persisted.ticket_id}/acknowledge",
                json={"actor": "operator-1"},
            )
            await client.post(
                f"/api/tickets/{persisted.ticket_id}/assign",
                json={"actor": "operator-1", "assigned_crew": "Crew-4"},
            )
            clock.value = base + timedelta(seconds=12)
            await client.post(
                f"/api/tickets/{persisted.ticket_id}/resolve",
                json={"actor": "operator-1", "reason": "corridor repair claimed"},
            )

            clock.value = base + timedelta(seconds=13)
            repair_response = await client.post(f"/api/simulator/faults/{fault_id}/repair")
            assert repair_response.status_code == 200
            repair_events = repair_response.json()["emitted_event_ids"]
            analysis_harness.event_ids.update(UUID(item) for item in repair_events)
            # The simulated missing device remains silent during restoration;
            # only the two healthy devices emit boot/restored pairs.
            assert await consumer.consume_new_once() == 4

            clock.value = base + timedelta(seconds=24)
            verified_count = await incidents.verify_restorations_once(
                threshold=0.8,
                stabilization_seconds=10,
            )
            restored_ticket = await incidents.get_ticket(persisted.ticket_id)
            assert verified_count == 1 or restored_ticket.status == TicketStatus.CLOSED
            assert restored_ticket.status == TicketStatus.CLOSED


@pytest.mark.asyncio
async def test_two_independent_simulated_faults_remain_separate_through_restoration(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC)
    clock = MutableClock(base)
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway, clock=clock)
    incidents = PostgresIncidentService(analysis_harness.engine, clock=clock)
    app = create_app(
        settings=get_settings(),
        telemetry_service=ingestion,
        incident_service=incidents,
        simulator_service=simulator,
    )
    gateway.app = app
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_response = await client.post(
                "/api/simulator/faults",
                json={
                    "fault_type": "SPAN_FAULT",
                    "dt_id": "DT-001",
                    "parent_pole_id": "P-001",
                    "child_pole_id": "P-002",
                },
            )
            second_response = await client.post(
                "/api/simulator/faults",
                json={
                    "fault_type": "SPAN_FAULT",
                    "dt_id": "DT-002",
                    "parent_pole_id": "P-101",
                    "child_pole_id": "P-102",
                },
            )
            overlap_response = await client.post(
                "/api/simulator/faults",
                json={"fault_type": "FEEDER_FAULT", "feeder_id": "FDR-001"},
            )

            assert first_response.status_code == 201, first_response.json()
            assert second_response.status_code == 201, second_response.json()
            assert overlap_response.status_code == 409
            assert overlap_response.json()["error"]["code"] == "SIMULATOR_FAULT_OVERLAP"
            first_fault = first_response.json()
            second_fault = second_response.json()
            first_fault_id = UUID(first_fault["fault_id"])
            second_fault_id = UUID(second_fault["fault_id"])
            analysis_harness.simulated_fault_ids.update((first_fault_id, second_fault_id))
            analysis_harness.event_ids.update(
                UUID(item)
                for item in first_fault["emitted_event_ids"] + second_fault["emitted_event_ids"]
            )
            assert await consumer.consume_new_once() == 6

            clock.value = base + timedelta(seconds=11)
            scheduler = RedisAnalysisScheduler(
                analysis_harness.redis,
                PostgresDtSnapshotRepository(analysis_harness.engine),
                due_set_name=analysis_harness.due_set,
                live_freshness_seconds=1_920,
                retry_delay_seconds=5,
                candidate_sink=incidents,
                clock=clock,
            )
            candidates = [*await scheduler.run_due_once(), *await scheduler.run_due_once()]
            assert [candidate.suspected_asset_id for candidate in candidates] == [
                "P-001->P-002",
                "P-101->P-102",
            ]
            assert set(candidates[0].affected_pole_ids).isdisjoint(candidates[1].affected_pole_ids)

            await asyncio.gather(
                incidents.persist_candidates(candidates),
                incidents.persist_candidates(tuple(reversed(candidates))),
            )
            active = {
                item.fingerprint: item
                for item in await incidents.list_incidents()
                if item.fingerprint
                in {
                    "span:DT-001:P-001->P-002",
                    "span:DT-002:P-101->P-102",
                }
            }
            assert set(active) == {
                "span:DT-001:P-001->P-002",
                "span:DT-002:P-101->P-102",
            }
            assert all(item.ticket_id is not None for item in active.values())
            for item in active.values():
                analysis_harness.incident_ids.add(item.incident_id)

            first_incident = active["span:DT-001:P-001->P-002"]
            second_incident = active["span:DT-002:P-101->P-102"]
            assert first_incident.ticket_id is not None
            assert second_incident.ticket_id is not None
            await client.post(
                f"/api/tickets/{first_incident.ticket_id}/acknowledge",
                json={"actor": "operator-1"},
            )
            await client.post(
                f"/api/tickets/{first_incident.ticket_id}/assign",
                json={"actor": "operator-1", "assigned_crew": "Crew-3"},
            )
            clock.value = base + timedelta(seconds=12)
            await client.post(
                f"/api/tickets/{first_incident.ticket_id}/resolve",
                json={"actor": "operator-1", "reason": "first repair claimed"},
            )

            clock.value = base + timedelta(seconds=31)
            repair_response = await client.post(f"/api/simulator/faults/{first_fault_id}/repair")
            assert repair_response.status_code == 200
            repair_events = repair_response.json()["emitted_event_ids"]
            analysis_harness.event_ids.update(UUID(item) for item in repair_events)
            assert await consumer.consume_new_once() == 6

            clock.value = base + timedelta(seconds=42)
            assert (
                await incidents.verify_restorations_once(
                    threshold=0.8,
                    stabilization_seconds=10,
                )
                == 1
            )
            assert (await incidents.get_ticket(first_incident.ticket_id)).status == (
                TicketStatus.CLOSED
            )
            assert (await incidents.get_ticket(second_incident.ticket_id)).status == (
                TicketStatus.DETECTED
            )

            session_factory = async_sessionmaker(
                analysis_harness.engine,
                expire_on_commit=False,
            )
            async with session_factory() as session:
                second_pole_state = await session.scalar(
                    select(PoleState.state)
                    .join(Pole, Pole.id == PoleState.pole_id)
                    .where(Pole.pole_id == "P-102")
                )
                second_fault_status = await session.scalar(
                    select(SimulatedFault.status).where(SimulatedFault.fault_id == second_fault_id)
                )
            assert second_pole_state == PoleStatus.DARK
            assert second_fault_status == SimulatorFaultStatus.ACTIVE

            clock.value = base + timedelta(seconds=43)
            reset_response = await client.post("/api/simulator/reset")
            assert reset_response.status_code == 200
            reset_events = reset_response.json()["repaired_faults"][0]["emitted_event_ids"]
            analysis_harness.event_ids.update(UUID(item) for item in reset_events)
            assert await consumer.consume_new_once() == 2


@pytest.mark.asyncio
async def test_simulator_fault_injection_recovers_silence_stale_devices(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC)
    session_factory = async_sessionmaker(analysis_harness.engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        await session.execute(
            update(DeviceHealth)
            .where(DeviceHealth.device_id.in_(analysis_harness.device_ids.values()))
            .values(
                status=DeviceHealthStatus.STALE,
                status_reason="device_silence_timeout",
                updated_at=base,
            )
        )
        await session.execute(
            update(PoleState)
            .where(PoleState.pole_id.in_(analysis_harness.pole_ids.values()))
            .values(
                state=PoleStatus.STALE,
                reason="device_silence_timeout",
                updated_at=base,
            )
        )

    clock = MutableClock(base)
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway, clock=clock)
    app = create_app(
        settings=get_settings(),
        telemetry_service=ingestion,
        simulator_service=simulator,
    )
    gateway.app = app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/simulator/faults", json={})

    assert response.status_code == 201, response.json()
    fault_id = UUID(response.json()["fault_id"])
    analysis_harness.simulated_fault_ids.add(fault_id)
    emitted_ids = {UUID(item) for item in response.json()["emitted_event_ids"]}
    analysis_harness.event_ids.update(emitted_ids)
    assert len(emitted_ids) == 4


@pytest.mark.asyncio
async def test_simulator_fault_without_reporters_returns_stable_conflict(
    analysis_harness: AnalysisHarness,
) -> None:
    session_factory = async_sessionmaker(analysis_harness.engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        await session.execute(
            update(DeviceHealth)
            .where(DeviceHealth.device_id.in_(analysis_harness.device_ids.values()))
            .values(
                status=DeviceHealthStatus.STALE,
                status_reason="generated_offline",
            )
        )

    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway)
    app = create_app(settings=get_settings(), simulator_service=simulator)
    gateway.app = app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/simulator/faults", json={})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SIMULATOR_NO_TELEMETRY"
    async with session_factory() as session:
        active_count = await session.scalar(
            select(func.count(SimulatedFault.fault_id)).where(
                SimulatedFault.status == SimulatorFaultStatus.ACTIVE,
                SimulatedFault.deenergized_pole_ids == ["P-002", "P-003", "P-004"],
            )
        )
    assert active_count == 0


@pytest.mark.asyncio
async def test_simulator_fault_repair_is_verified_through_public_telemetry(
    analysis_harness: AnalysisHarness,
) -> None:
    base = datetime.now(UTC)
    clock = MutableClock(base)
    ingestion = TelemetryIngestionService(
        PostgresPoleBindingResolver(analysis_harness.engine),
        RedisTelemetryPublisher(analysis_harness.redis, analysis_harness.stream),
        clock=clock,
    )
    gateway = AsgiSimulatorTelemetryGateway()
    simulator = PostgresSimulatorService(analysis_harness.engine, gateway, clock=clock)
    incidents = PostgresIncidentService(analysis_harness.engine, clock=clock)
    settings = get_settings()
    app = create_app(
        settings=settings,
        telemetry_service=ingestion,
        incident_service=incidents,
        simulator_service=simulator,
    )
    gateway.app = app
    consumer = analysis_harness.consumer()
    await consumer.ensure_group()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            inject_response = await client.post("/api/simulator/faults", json={})
            assert inject_response.status_code == 201, inject_response.json()
            injected = inject_response.json()
            fault_id = UUID(injected["fault_id"])
            analysis_harness.simulated_fault_ids.add(fault_id)
            analysis_harness.event_ids.update(UUID(item) for item in injected["emitted_event_ids"])
            assert injected["status"] == SimulatorFaultStatus.ACTIVE
            assert injected["deenergized_pole_ids"] == ["P-002", "P-003", "P-004"]
            assert await consumer.consume_new_once() == 4

            clock.value = base + timedelta(seconds=11)
            scheduler = RedisAnalysisScheduler(
                analysis_harness.redis,
                PostgresDtSnapshotRepository(analysis_harness.engine),
                due_set_name=analysis_harness.due_set,
                live_freshness_seconds=1_920,
                retry_delay_seconds=5,
                candidate_sink=incidents,
                clock=clock,
            )
            candidates = await scheduler.run_due_once()
            assert len(candidates) == 1
            incident = next(
                item
                for item in await incidents.list_incidents()
                if item.fingerprint == "span:DT-001:P-001->P-002"
            )
            assert incident.ticket_id is not None
            analysis_harness.incident_ids.add(incident.incident_id)

            await client.post(
                f"/api/tickets/{incident.ticket_id}/acknowledge",
                json={"actor": "operator-1"},
            )
            await client.post(
                f"/api/tickets/{incident.ticket_id}/assign",
                json={"actor": "operator-1", "assigned_crew": "Crew-7"},
            )
            clock.value = base + timedelta(seconds=12)
            resolve_response = await client.post(
                f"/api/tickets/{incident.ticket_id}/resolve",
                json={"actor": "operator-1", "reason": "repair claimed"},
            )
            assert resolve_response.status_code == 200
            assert resolve_response.json()["restoration_status"] == "REPAIR_NOT_VERIFIED"
            assert resolve_response.json()["remaining_dark_count"] == 3

            clock.value = base + timedelta(seconds=30)
            assert (
                await incidents.verify_restorations_once(threshold=0.8, stabilization_seconds=10)
                == 0
            )
            assert (await incidents.get_ticket(incident.ticket_id)).status.value == "RESOLVED"

            clock.value = base + timedelta(seconds=31)
            repair_response = await client.post(f"/api/simulator/faults/{fault_id}/repair")
            assert repair_response.status_code == 200
            repaired = repair_response.json()
            assert repaired["status"] == SimulatorFaultStatus.REPAIRED
            assert len(repaired["emitted_event_ids"]) == 6
            analysis_harness.event_ids.update(UUID(item) for item in repaired["emitted_event_ids"])
            assert await consumer.consume_new_once() == 6

            session_factory = async_sessionmaker(analysis_harness.engine, expire_on_commit=False)
            async with session_factory() as session:
                states = tuple(
                    await session.scalars(
                        select(PoleState.state)
                        .join(Pole, Pole.id == PoleState.pole_id)
                        .where(Pole.pole_id.in_(("P-002", "P-003", "P-004")))
                        .order_by(Pole.pole_id)
                    )
                )
                simulator_event_count = await session.scalar(
                    select(func.count(TelemetryEvent.id)).where(
                        TelemetryEvent.event_id.in_(analysis_harness.event_ids),
                        TelemetryEvent.origin == TelemetryOrigin.SIMULATOR,
                    )
                )
            assert states == (PoleStatus.LIVE, PoleStatus.LIVE, PoleStatus.LIVE)
            assert simulator_event_count == 10

            clock.value = base + timedelta(seconds=40)
            assert (
                await incidents.verify_restorations_once(threshold=0.8, stabilization_seconds=10)
                == 0
            )
            clock.value = base + timedelta(seconds=41)
            assert (
                await incidents.verify_restorations_once(threshold=0.8, stabilization_seconds=10)
                == 1
            )
            ticket = await incidents.get_ticket(incident.ticket_id)
            assert ticket.status.value == "CLOSED"
            assert ticket.restoration_status == "RESTORATION_VERIFIED"
            assert [event.to_status.value for event in ticket.events][-2:] == [
                "VERIFIED",
                "CLOSED",
            ]

            repeated_repair = await client.post(f"/api/simulator/faults/{fault_id}/repair")
            assert repeated_repair.status_code == 200
            assert repeated_repair.json()["emitted_event_ids"] == []
            assert (
                await incidents.verify_restorations_once(threshold=0.8, stabilization_seconds=10)
                == 0
            )
            final_ticket = await incidents.get_ticket(incident.ticket_id)
            assert [event.to_status.value for event in final_ticket.events].count("VERIFIED") == 1
            assert [event.to_status.value for event in final_ticket.events].count("CLOSED") == 1

            reset_response = await client.post("/api/simulator/reset")
            assert reset_response.status_code == 200
            assert reset_response.json() == {"status": "reset", "repaired_faults": []}
