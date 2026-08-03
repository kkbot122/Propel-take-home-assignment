from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from propel.domain.enums import DeviceHealthStatus, PoleStatus, TopologySource
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Feeder,
    Pole,
    PoleState,
    Substation,
    TopologyEdge,
)


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

    await session.execute(
        insert(DistributionTransformer)
        .values(
            dt_id="DT-001",
            feeder_id=feeder_id,
            name="JP Nagar DT 1",
            latitude=12.889100,
            longitude=77.584000,
            pin_code="560078",
        )
        .on_conflict_do_nothing(constraint="uq_distribution_transformers_dt_id")
    )
    dt_id = require_seed_value(
        await session.scalar(
            select(DistributionTransformer.id).where(DistributionTransformer.dt_id == "DT-001")
        ),
        "DT-001",
    )

    pole_specs = (
        ("P-001", 12.889250, 77.584120),
        ("P-002", 12.889430, 77.584260),
        ("P-003", 12.889610, 77.584400),
        ("P-004", 12.889790, 77.584540),
    )
    for external_id, latitude, longitude in pole_specs:
        await session.execute(
            insert(Pole)
            .values(
                pole_id=external_id,
                dt_id=dt_id,
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
        raise RuntimeError("deterministic seed did not resolve all four poles")

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
        raise RuntimeError("deterministic seed did not resolve all four devices")

    for pole_external_id, pole_id in poles.items():
        device_id = devices[f"DEV-{pole_external_id}"]
        await session.execute(
            insert(DeviceBinding)
            .values(device_id=device_id, pole_id=pole_id, valid_from=seeded_at)
            .on_conflict_do_nothing()
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
        (None, "P-001", 20.0),
        ("P-001", "P-002", 25.0),
        ("P-002", "P-003", 25.0),
        ("P-003", "P-004", 25.0),
    )
    for parent_external_id, child_external_id, distance_m in edge_specs:
        await session.execute(
            insert(TopologyEdge)
            .values(
                dt_id=dt_id,
                parent_pole_id=(poles[parent_external_id] if parent_external_id else None),
                child_pole_id=poles[child_external_id],
                source=TopologySource.SURVEYED,
                distance_m=distance_m,
                edge_confidence=1.0,
                topology_version=1,
            )
            .on_conflict_do_nothing(constraint="uq_topology_edges_child_version")
        )

    binding_count = await session.scalar(
        select(func.count(DeviceBinding.id)).where(DeviceBinding.device_id.in_(devices.values()))
    )
    edge_count = await session.scalar(
        select(func.count(TopologyEdge.id)).where(
            TopologyEdge.dt_id == dt_id, TopologyEdge.topology_version == 1
        )
    )
    live_count = await session.scalar(
        select(func.count(PoleState.pole_id)).where(
            PoleState.pole_id.in_(poles.values()), PoleState.state == PoleStatus.LIVE
        )
    )

    return SeedSummary(
        substations=1,
        feeders=1,
        transformers=1,
        poles=len(poles),
        devices=len(devices),
        bindings=binding_count or 0,
        topology_edges=edge_count or 0,
        live_pole_states=live_count or 0,
    )
