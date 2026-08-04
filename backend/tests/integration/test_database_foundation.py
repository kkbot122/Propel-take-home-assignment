from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql.base import Executable

from propel.domain.enums import (
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    PoleStatus,
    ProcessingOutcome,
    ScheduledOutageScope,
    SuspectedAssetType,
    TelemetryEventType,
    TopologySource,
)
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DistributionTransformer,
    Feeder,
    Incident,
    Pole,
    PoleState,
    ScheduledOutage,
    TelemetryEvent,
    TopologyEdge,
)
from propel.infra.database.seed import seed_database
from propel.infra.settings import get_settings

pytestmark = pytest.mark.integration

MINIMUM_TABLES = {
    "substations",
    "feeders",
    "distribution_transformers",
    "poles",
    "devices",
    "device_bindings",
    "topology_edges",
    "telemetry_events",
    "pole_states",
    "device_health",
    "scheduled_outages",
    "incidents",
    "incident_poles",
    "tickets",
    "ticket_events",
}


@pytest_asyncio.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_schema_and_seed_are_complete_and_idempotent(database_engine: AsyncEngine) -> None:
    first_summary = await seed_database(database_engine)
    second_summary = await seed_database(database_engine)
    assert first_summary == second_summary
    assert second_summary.transformers == 2
    assert second_summary.poles == 6
    assert second_summary.devices == 6
    assert second_summary.bindings == 6
    assert second_summary.topology_edges == 6
    assert second_summary.live_pole_states == 6
    assert second_summary.scheduled_outages == 1

    async with database_engine.connect() as connection:
        table_names = set(
            await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        )
        assert MINIMUM_TABLES <= table_names

        seeded_schedule = (
            await connection.execute(
                select(
                    ScheduledOutage.outage_id,
                    ScheduledOutage.scope,
                    ScheduledOutage.scope_id,
                ).where(ScheduledOutage.outage_id == "SO-SEED-001")
            )
        ).one_or_none()
        assert seeded_schedule is not None
        assert seeded_schedule.scope == ScheduledOutageScope.SPAN
        assert seeded_schedule.scope_id == "P-003->P-004"

        rows = (
            await connection.execute(
                select(
                    Pole.pole_id,
                    TopologyEdge.parent_pole_id,
                    TopologyEdge.child_pole_id,
                    TopologyEdge.source,
                )
                .join(TopologyEdge, TopologyEdge.child_pole_id == Pole.id)
                .join(DistributionTransformer, TopologyEdge.dt_id == DistributionTransformer.id)
                .where(DistributionTransformer.dt_id == "DT-001")
            )
        ).all()
        assert len(rows) == 4
        assert all(row.source == TopologySource.SURVEYED for row in rows)
        assert Counter(row.pole_id for row in rows) == Counter(
            {"P-001": 1, "P-002": 1, "P-003": 1, "P-004": 1}
        )

        pole_rows = (
            await connection.execute(
                select(Pole.id, Pole.pole_id)
                .join(PoleState)
                .join(DistributionTransformer, Pole.dt_id == DistributionTransformer.id)
                .where(
                    PoleState.state == PoleStatus.LIVE,
                    DistributionTransformer.dt_id == "DT-001",
                )
            )
        ).all()
        pole_names_by_id = {row.id: row.pole_id for row in pole_rows}
        parent_by_child = {row.child_pole_id: row.parent_pole_id for row in rows}
        for pole_id in pole_names_by_id:
            visited: set[int] = set()
            current_id: int | None = pole_id
            while current_id is not None:
                assert current_id not in visited
                visited.add(current_id)
                current_id = parent_by_child[current_id]
            assert pole_id in visited


async def expect_integrity_error(connection: AsyncConnection, statement: Executable) -> None:
    savepoint = await connection.begin_nested()
    with pytest.raises(IntegrityError):
        await connection.execute(statement)
    await savepoint.rollback()


@pytest.mark.asyncio
async def test_topology_and_binding_invariants_are_database_enforced(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        feeder_id = await connection.scalar(select(Feeder.id).where(Feeder.feeder_id == "FDR-001"))
        dt_id = await connection.scalar(
            select(DistributionTransformer.id).where(DistributionTransformer.dt_id == "DT-001")
        )
        p001_id = await connection.scalar(select(Pole.id).where(Pole.pole_id == "P-001"))
        device_id = await connection.scalar(
            select(Device.id).where(Device.device_id == "DEV-P-002")
        )
        assert feeder_id is not None and dt_id is not None
        assert p001_id is not None and device_id is not None

        other_dt_id = await connection.scalar(
            DistributionTransformer.__table__.insert()
            .values(
                dt_id="DT-INVARIANT-TEST",
                feeder_id=feeder_id,
                name="Invariant test transformer",
                latitude=12.89,
                longitude=77.59,
                pin_code="560078",
            )
            .returning(DistributionTransformer.id)
        )
        assert other_dt_id is not None
        other_pole_id = await connection.scalar(
            Pole.__table__.insert()
            .values(
                pole_id="P-INVARIANT-TEST",
                dt_id=other_dt_id,
                feeder_id=feeder_id,
                latitude=12.90,
                longitude=77.60,
                pin_code="560078",
            )
            .returning(Pole.id)
        )
        assert other_pole_id is not None

        await expect_integrity_error(
            connection,
            TopologyEdge.__table__.insert().values(
                dt_id=dt_id,
                parent_pole_id=other_pole_id,
                child_pole_id=p001_id,
                source=TopologySource.SURVEYED,
                distance_m=1,
                edge_confidence=1,
                topology_version=99,
            ),
        )
        await expect_integrity_error(
            connection,
            TopologyEdge.__table__.insert().values(
                dt_id=dt_id,
                parent_pole_id=p001_id,
                child_pole_id=p001_id,
                source=TopologySource.SURVEYED,
                distance_m=1,
                edge_confidence=1,
                topology_version=99,
            ),
        )
        await expect_integrity_error(
            connection,
            DeviceBinding.__table__.insert().values(
                device_id=device_id,
                pole_id=p001_id,
                valid_from=datetime.now(UTC),
            ),
        )
        await transaction.rollback()


@pytest.mark.asyncio
async def test_event_ids_and_active_incident_fingerprints_are_unique(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        device_id = await connection.scalar(
            select(Device.id).where(Device.device_id == "DEV-P-001")
        )
        pole_id = await connection.scalar(select(Pole.id).where(Pole.pole_id == "P-001"))
        assert device_id is not None and pole_id is not None

        event_id = await connection.scalar(
            TelemetryEvent.__table__.insert()
            .values(
                device_id=device_id,
                pole_id=pole_id,
                event_type=TelemetryEventType.HEARTBEAT,
                energized=True,
                device_timestamp=datetime.now(UTC),
                sequence=1,
                firmware="1.4.2",
                processing_outcome=ProcessingOutcome.ACCEPTED,
            )
            .returning(TelemetryEvent.event_id)
        )
        assert isinstance(event_id, UUID)

        await expect_integrity_error(
            connection,
            TelemetryEvent.__table__.insert().values(
                event_id=event_id,
                device_id=device_id,
                pole_id=pole_id,
                event_type=TelemetryEventType.HEARTBEAT,
                energized=True,
                device_timestamp=datetime.now(UTC),
                sequence=2,
                firmware="1.4.2",
                processing_outcome=ProcessingOutcome.ACCEPTED,
            ),
        )

        incident_values = {
            "fingerprint": "test:dt-001:p-001-p-002",
            "status": IncidentStatus.ACTIVE,
            "classification": FaultClass.SPAN_FAULT,
            "suspected_asset_type": SuspectedAssetType.SPAN,
            "suspected_asset_id": "P-001->P-002",
            "latitude": 12.88934,
            "longitude": 77.58419,
            "pin_code": "560078",
            "affected_pole_count": 3,
            "precision": LocalizationPrecision.EXACT_SPAN,
            "confidence_score": 100,
            "confidence_reason": "constraint verification",
        }
        await connection.execute(Incident.__table__.insert().values(**incident_values))
        await expect_integrity_error(
            connection,
            Incident.__table__.insert().values(**incident_values),
        )
        await connection.execute(
            Incident.__table__.insert().values(
                **(incident_values | {"status": IncidentStatus.RESOLVED})
            )
        )
        await transaction.rollback()
