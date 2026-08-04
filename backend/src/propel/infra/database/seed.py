from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from propel.domain.enums import (
    DeviceHealthStatus,
    PoleStatus,
    ScheduledOutageScope,
    TopologySource,
)
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Feeder,
    Pole,
    PoleState,
    ScheduledOutage,
    Substation,
    TopologyEdge,
)
from propel.topology.inference import infer_geographic_topology
from propel.topology.models import TopologyPole, TopologyRequest


@dataclass(frozen=True, slots=True)
class SeedSummary:
    substations: int
    feeders: int
    transformers: int
    poles: int
    devices: int
    bindings: int
    topology_edges: int
    live_pole_states: int
    scheduled_outages: int


def require_seed_value[T](value: T | None, label: str) -> T:
    if value is None:
        raise RuntimeError(f"seed dependency is missing after insert: {label}")
    return value


async def seed_database(engine: AsyncEngine) -> SeedSummary:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        return await seed_surveyed_network(session)


async def seed_surveyed_network(session: AsyncSession) -> SeedSummary:
    seeded_at = datetime.now(UTC)
    binding_valid_from = datetime(2020, 1, 1, tzinfo=UTC)

    await session.execute(
        insert(Substation)
        .values(
            substation_id="SUB-001",
            name="JP Nagar Substation",
            latitude=12.889000,
            longitude=77.583900,
            pin_code="560078",
        )
        .on_conflict_do_nothing(constraint="uq_substations_substation_id")
    )
    substation_id = require_seed_value(
        await session.scalar(select(Substation.id).where(Substation.substation_id == "SUB-001")),
        "SUB-001",
    )

    await session.execute(
        insert(Feeder)
        .values(feeder_id="FDR-001", substation_id=substation_id, name="JP Nagar Feeder 1")
        .on_conflict_do_nothing(constraint="uq_feeders_feeder_id")
    )
    feeder_id = require_seed_value(
        await session.scalar(select(Feeder.id).where(Feeder.feeder_id == "FDR-001")),
        "FDR-001",
    )

    transformer_specs = (
        ("DT-001", "JP Nagar DT 1", 12.889100, 77.584000),
        ("DT-002", "JP Nagar DT 2", 12.890050, 77.585000),
        ("DT-003", "JP Nagar DT 3", 12.891000, 77.586000),
    )
    for external_id, name, latitude, longitude in transformer_specs:
        await session.execute(
            insert(DistributionTransformer)
            .values(
                dt_id=external_id,
                feeder_id=feeder_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                pin_code="560078",
            )
            .on_conflict_do_nothing(constraint="uq_distribution_transformers_dt_id")
        )
    transformers = {
        external_id: internal_id
        for external_id, internal_id in (
            await session.execute(
                select(DistributionTransformer.dt_id, DistributionTransformer.id).where(
                    DistributionTransformer.dt_id.in_([spec[0] for spec in transformer_specs])
                )
            )
        ).all()
    }
    if len(transformers) != len(transformer_specs):
        raise RuntimeError("deterministic seed did not resolve all transformers")

    pole_specs = (
        ("P-001", "DT-001", 12.889250, 77.584120),
        ("P-002", "DT-001", 12.889430, 77.584260),
        ("P-003", "DT-001", 12.889610, 77.584400),
        ("P-004", "DT-001", 12.889790, 77.584540),
        ("P-101", "DT-002", 12.890220, 77.585140),
        ("P-102", "DT-002", 12.890400, 77.585300),
        ("P-201", "DT-003", 12.891180, 77.586000),
        ("P-202", "DT-003", 12.891360, 77.586000),
        ("P-203", "DT-003", 12.891540, 77.586000),
        ("P-204", "DT-003", 12.891360, 77.586180),
    )
    for external_id, transformer_external_id, latitude, longitude in pole_specs:
        await session.execute(
            insert(Pole)
            .values(
                pole_id=external_id,
                dt_id=transformers[transformer_external_id],
                feeder_id=feeder_id,
                latitude=latitude,
                longitude=longitude,
                pin_code="560078",
            )
            .on_conflict_do_nothing(constraint="uq_poles_pole_id")
        )

    poles = {
        external_id: internal_id
        for external_id, internal_id in (
            await session.execute(
                select(Pole.pole_id, Pole.id).where(
                    Pole.pole_id.in_([spec[0] for spec in pole_specs])
                )
            )
        ).all()
    }
    if len(poles) != len(pole_specs):
        raise RuntimeError("deterministic seed did not resolve all poles")

    for pole_external_id in poles:
        device_external_id = f"DEV-{pole_external_id}"
        await session.execute(
            insert(Device)
            .values(device_id=device_external_id, installed_firmware="1.4.2")
            .on_conflict_do_nothing(constraint="uq_devices_device_id")
        )

    devices = {
        external_id: internal_id
        for external_id, internal_id in (
            await session.execute(
                select(Device.device_id, Device.id).where(
                    Device.device_id.in_([f"DEV-{pole_id}" for pole_id in poles])
                )
            )
        ).all()
    }
    if len(devices) != len(poles):
        raise RuntimeError("deterministic seed did not resolve all devices")

    for pole_external_id, pole_id in poles.items():
        device_id = devices[f"DEV-{pole_external_id}"]
        await session.execute(
            insert(DeviceBinding)
            .values(device_id=device_id, pole_id=pole_id, valid_from=binding_valid_from)
            .on_conflict_do_nothing()
        )
        await session.execute(
            update(DeviceBinding)
            .where(
                DeviceBinding.device_id == device_id,
                DeviceBinding.pole_id == pole_id,
                DeviceBinding.valid_to.is_(None),
            )
            .values(valid_from=binding_valid_from)
        )
        await session.execute(
            insert(PoleState)
            .values(
                pole_id=pole_id,
                state=PoleStatus.LIVE,
                received_at=seeded_at,
                firmware="1.4.2",
                reason="deterministic_seed",
            )
            .on_conflict_do_nothing(index_elements=[PoleState.pole_id])
        )
        await session.execute(
            insert(DeviceHealth)
            .values(
                device_id=device_id,
                status=DeviceHealthStatus.HEALTHY,
                last_seen_at=seeded_at,
                firmware="1.4.2",
            )
            .on_conflict_do_nothing(index_elements=[DeviceHealth.device_id])
        )

    edge_specs = (
        ("DT-001", None, "P-001", 20.0),
        ("DT-001", "P-001", "P-002", 25.0),
        ("DT-001", "P-002", "P-003", 25.0),
        ("DT-001", "P-003", "P-004", 25.0),
        ("DT-002", None, "P-101", 20.0),
        ("DT-002", "P-101", "P-102", 25.0),
    )
    for transformer_external_id, parent_external_id, child_external_id, distance_m in edge_specs:
        await session.execute(
            insert(TopologyEdge)
            .values(
                dt_id=transformers[transformer_external_id],
                parent_pole_id=(poles[parent_external_id] if parent_external_id else None),
                child_pole_id=poles[child_external_id],
                source=TopologySource.SURVEYED,
                distance_m=distance_m,
                edge_confidence=1.0,
                topology_version=1,
            )
            .on_conflict_do_nothing(constraint="uq_topology_edges_child_version")
        )

    inferred_topology = infer_geographic_topology(
        TopologyRequest(
            dt_id="DT-003",
            dt_latitude=transformer_specs[2][2],
            dt_longitude=transformer_specs[2][3],
            topology_version=1,
            poles=tuple(
                TopologyPole(pole_id, latitude, longitude)
                for pole_id, transformer_id, latitude, longitude in pole_specs
                if transformer_id == "DT-003"
            ),
        )
    )
    if not inferred_topology.edges:
        raise RuntimeError("deterministic inferred topology fixture is unusable")
    for edge in inferred_topology.edges:
        inferred_edge_insert = insert(TopologyEdge).values(
            dt_id=transformers["DT-003"],
            parent_pole_id=(poles[edge.parent_pole_id] if edge.parent_pole_id else None),
            child_pole_id=poles[edge.child_pole_id],
            source=edge.source,
            distance_m=edge.distance_m,
            edge_confidence=edge.edge_confidence,
            inference_version=edge.inference_version,
            topology_version=inferred_topology.topology_version,
        )
        await session.execute(
            inferred_edge_insert.on_conflict_do_update(
                constraint="uq_topology_edges_child_version",
                set_={
                    "parent_pole_id": inferred_edge_insert.excluded.parent_pole_id,
                    "distance_m": inferred_edge_insert.excluded.distance_m,
                    "edge_confidence": inferred_edge_insert.excluded.edge_confidence,
                    "inference_version": inferred_edge_insert.excluded.inference_version,
                },
                where=TopologyEdge.source == TopologySource.INFERRED,
            )
        )

    await session.execute(
        insert(ScheduledOutage)
        .values(
            outage_id="SO-SEED-001",
            scope=ScheduledOutageScope.SPAN,
            scope_id="P-003->P-004",
            starts_at=datetime(2099, 1, 1, 10, 0, tzinfo=UTC),
            ends_at=datetime(2099, 1, 1, 12, 0, tzinfo=UTC),
            source="deterministic-seed",
            reason="Future maintenance fixture; inactive during normal demonstrations",
        )
        .on_conflict_do_nothing(constraint="uq_scheduled_outages_outage_id")
    )

    binding_count = await session.scalar(
        select(func.count(DeviceBinding.id)).where(DeviceBinding.device_id.in_(devices.values()))
    )
    edge_count = await session.scalar(
        select(func.count(TopologyEdge.id)).where(
            TopologyEdge.dt_id.in_(transformers.values()),
            TopologyEdge.topology_version == 1,
        )
    )
    live_count = await session.scalar(
        select(func.count(PoleState.pole_id)).where(
            PoleState.pole_id.in_(poles.values()), PoleState.state == PoleStatus.LIVE
        )
    )
    scheduled_outage_count = await session.scalar(
        select(func.count(ScheduledOutage.id)).where(ScheduledOutage.outage_id == "SO-SEED-001")
    )

    return SeedSummary(
        substations=1,
        feeders=1,
        transformers=len(transformers),
        poles=len(poles),
        devices=len(devices),
        bindings=binding_count or 0,
        topology_edges=edge_count or 0,
        live_pole_states=live_count or 0,
        scheduled_outages=scheduled_outage_count or 0,
    )
