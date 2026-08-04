from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

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
    GeneratedDataset,
    Pole,
    PoleState,
    ScheduledOutage,
    SimulatorTopologyEdge,
    Substation,
    TopologyEdge,
)
from propel.simulator.generation import (
    GeneratedDevice,
    GeneratedNetwork,
    NetworkGenerationConfig,
    generate_network,
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
    generated_dataset_id: str | None = None
    generated_substations: int = 0
    generated_feeders: int = 0
    generated_transformers: int = 0
    generated_poles: int = 0
    generated_devices: int = 0
    generated_bindings: int = 0
    generated_topology_edges: int = 0
    generated_ground_truth_edges: int = 0
    generated_scheduled_outages: int = 0
    generated_logical_digest: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedSeedSummary:
    generated_dataset_id: str
    generated_substations: int
    generated_feeders: int
    generated_transformers: int
    generated_poles: int
    generated_devices: int
    generated_bindings: int
    generated_topology_edges: int
    generated_ground_truth_edges: int
    generated_scheduled_outages: int
    generated_logical_digest: str


def require_seed_value[T](value: T | None, label: str) -> T:
    if value is None:
        raise RuntimeError(f"seed dependency is missing after insert: {label}")
    return value


async def seed_database(
    engine: AsyncEngine,
    *,
    generation_config: NetworkGenerationConfig | None = None,
    include_generated_network: bool = True,
) -> SeedSummary:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory.begin() as session:
        fixture_summary = await seed_surveyed_network(session)
        if not include_generated_network:
            return fixture_summary
        network = generate_network(generation_config)
        generated_summary = await seed_generated_network(session, network)
        return replace(fixture_summary, **asdict(generated_summary))


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


async def seed_generated_network(
    session: AsyncSession,
    network: GeneratedNetwork,
) -> GeneratedSeedSummary:
    seeded_at = datetime.now(UTC)
    binding_valid_from = datetime(2020, 1, 1, tzinfo=UTC)
    manifest = network.as_manifest()
    await session.execute(
        insert(GeneratedDataset)
        .values(
            dataset_id=network.dataset_id,
            generator_version=network.generator_version,
            seed=network.config.seed,
            config=asdict(network.config),
            manifest=manifest,
            logical_digest=network.logical_digest,
        )
        .on_conflict_do_nothing(constraint="uq_generated_datasets_dataset_id")
    )
    dataset_row = (
        await session.execute(
            select(GeneratedDataset.id, GeneratedDataset.logical_digest).where(
                GeneratedDataset.dataset_id == network.dataset_id
            )
        )
    ).one_or_none()
    if dataset_row is None:
        raise RuntimeError("generated dataset manifest was not persisted")
    if dataset_row.logical_digest != network.logical_digest:
        raise RuntimeError(
            "generated dataset changed without a generator version change; bump the version"
        )

    await session.execute(
        insert(Substation)
        .values(
            [
                {
                    "substation_id": item.substation_id,
                    "name": item.name,
                    "latitude": item.latitude,
                    "longitude": item.longitude,
                    "pin_code": item.pin_code,
                }
                for item in network.substations
            ]
        )
        .on_conflict_do_nothing(constraint="uq_substations_substation_id")
    )
    substation_ids = await _external_id_map(
        session,
        Substation.substation_id,
        Substation.id,
        tuple(item.substation_id for item in network.substations),
    )

    await session.execute(
        insert(Feeder)
        .values(
            [
                {
                    "feeder_id": item.feeder_id,
                    "substation_id": substation_ids[item.substation_id],
                    "name": item.name,
                }
                for item in network.feeders
            ]
        )
        .on_conflict_do_nothing(constraint="uq_feeders_feeder_id")
    )
    feeder_ids = await _external_id_map(
        session,
        Feeder.feeder_id,
        Feeder.id,
        tuple(item.feeder_id for item in network.feeders),
    )

    await session.execute(
        insert(DistributionTransformer)
        .values(
            [
                {
                    "dt_id": item.dt_id,
                    "feeder_id": feeder_ids[item.feeder_id],
                    "name": item.name,
                    "latitude": item.latitude,
                    "longitude": item.longitude,
                    "pin_code": item.pin_code,
                }
                for item in network.transformers
            ]
        )
        .on_conflict_do_nothing(constraint="uq_distribution_transformers_dt_id")
    )
    transformer_ids = await _external_id_map(
        session,
        DistributionTransformer.dt_id,
        DistributionTransformer.id,
        tuple(item.dt_id for item in network.transformers),
    )

    await session.execute(
        insert(Pole)
        .values(
            [
                {
                    "pole_id": item.pole_id,
                    "dt_id": transformer_ids[item.dt_id],
                    "feeder_id": feeder_ids[item.feeder_id],
                    "latitude": item.latitude,
                    "longitude": item.longitude,
                    "pin_code": item.pin_code,
                }
                for item in network.poles
            ]
        )
        .on_conflict_do_nothing(constraint="uq_poles_pole_id")
    )
    pole_ids = await _external_id_map(
        session,
        Pole.pole_id,
        Pole.id,
        tuple(item.pole_id for item in network.poles),
    )

    await session.execute(
        insert(Device)
        .values(
            [
                {
                    "device_id": item.device_id,
                    "installed_firmware": item.firmware,
                }
                for item in network.devices
            ]
        )
        .on_conflict_do_nothing(constraint="uq_devices_device_id")
    )
    device_ids = await _external_id_map(
        session,
        Device.device_id,
        Device.id,
        tuple(item.device_id for item in network.devices),
    )
    device_by_pole = {item.pole_id: item for item in network.devices}

    await session.execute(
        insert(DeviceBinding)
        .values(
            [
                {
                    "device_id": device_ids[item.device_id],
                    "pole_id": pole_ids[item.pole_id],
                    "valid_from": binding_valid_from,
                }
                for item in network.devices
            ]
        )
        .on_conflict_do_nothing()
    )
    await session.execute(
        insert(PoleState)
        .values(
            [
                {
                    "pole_id": pole_ids[pole.pole_id],
                    "state": _initial_pole_status(device_by_pole.get(pole.pole_id)),
                    "received_at": seeded_at,
                    "firmware": (
                        device_by_pole[pole.pole_id].firmware
                        if pole.pole_id in device_by_pole
                        else None
                    ),
                    "battery_mv": (
                        device_by_pole[pole.pole_id].battery_mv
                        if pole.pole_id in device_by_pole
                        else None
                    ),
                    "rssi": (
                        device_by_pole[pole.pole_id].rssi
                        if pole.pole_id in device_by_pole
                        else None
                    ),
                    "reason": "generated_network_seed",
                }
                for pole in network.poles
            ]
        )
        .on_conflict_do_nothing(index_elements=[PoleState.pole_id])
    )
    await session.execute(
        insert(DeviceHealth)
        .values(
            [
                {
                    "device_id": device_ids[item.device_id],
                    "status": item.health_status,
                    "last_seen_at": (
                        seeded_at
                        if item.health_status == DeviceHealthStatus.HEALTHY
                        else seeded_at - timedelta(hours=1)
                    ),
                    "firmware": item.firmware,
                    "battery_mv": item.battery_mv,
                    "rssi": item.rssi,
                    "status_reason": (
                        "generated_healthy"
                        if item.health_status == DeviceHealthStatus.HEALTHY
                        else "generated_offline"
                    ),
                    "can_report_power_loss": item.can_report_power_loss,
                }
                for item in network.devices
            ]
        )
        .on_conflict_do_nothing(index_elements=[DeviceHealth.device_id])
    )
    await session.execute(
        insert(TopologyEdge)
        .values(
            [
                {
                    "dt_id": transformer_ids[item.dt_id],
                    "parent_pole_id": (
                        pole_ids[item.parent_pole_id] if item.parent_pole_id is not None else None
                    ),
                    "child_pole_id": pole_ids[item.child_pole_id],
                    "source": item.source,
                    "distance_m": item.distance_m,
                    "edge_confidence": item.edge_confidence,
                    "inference_version": item.inference_version,
                    "topology_version": item.topology_version,
                }
                for item in network.visible_edges
            ]
        )
        .on_conflict_do_nothing(constraint="uq_topology_edges_child_version")
    )
    await session.execute(
        insert(SimulatorTopologyEdge)
        .values(
            [
                {
                    "dataset_id": dataset_row.id,
                    "dt_id": transformer_ids[item.dt_id],
                    "parent_pole_id": (
                        pole_ids[item.parent_pole_id] if item.parent_pole_id is not None else None
                    ),
                    "child_pole_id": pole_ids[item.child_pole_id],
                    "distance_m": item.distance_m,
                }
                for item in network.ground_truth_edges
            ]
        )
        .on_conflict_do_nothing(constraint="uq_simulator_topology_edges_dataset_child")
    )

    scheduled_scenario = next(item for item in network.scenarios if item.scheduled)
    scheduled_fault = scheduled_scenario.faults[0]
    assert scheduled_fault.parent_pole_id is not None
    assert scheduled_fault.child_pole_id is not None
    scheduled_outage_id = f"SO-{network.dataset_id}"
    await session.execute(
        insert(ScheduledOutage)
        .values(
            outage_id=scheduled_outage_id,
            scope=ScheduledOutageScope.SPAN,
            scope_id=(f"{scheduled_fault.parent_pole_id}->{scheduled_fault.child_pole_id}"),
            starts_at=datetime(2099, 1, 2, 10, 0, tzinfo=UTC),
            ends_at=datetime(2099, 1, 2, 12, 0, tzinfo=UTC),
            source="generated-network-manifest",
            reason="Deterministic scheduled-fault scenario",
        )
        .on_conflict_do_nothing(constraint="uq_scheduled_outages_outage_id")
    )

    binding_count = await session.scalar(
        select(func.count(DeviceBinding.id)).where(
            DeviceBinding.device_id.in_(tuple(device_ids.values())),
            DeviceBinding.valid_to.is_(None),
        )
    )
    topology_count = await session.scalar(
        select(func.count(TopologyEdge.id)).where(
            TopologyEdge.dt_id.in_(tuple(transformer_ids.values()))
        )
    )
    ground_truth_count = await session.scalar(
        select(func.count(SimulatorTopologyEdge.id)).where(
            SimulatorTopologyEdge.dataset_id == dataset_row.id
        )
    )
    scheduled_count = await session.scalar(
        select(func.count(ScheduledOutage.id)).where(
            ScheduledOutage.outage_id == scheduled_outage_id
        )
    )
    return GeneratedSeedSummary(
        generated_dataset_id=network.dataset_id,
        generated_substations=len(substation_ids),
        generated_feeders=len(feeder_ids),
        generated_transformers=len(transformer_ids),
        generated_poles=len(pole_ids),
        generated_devices=len(device_ids),
        generated_bindings=binding_count or 0,
        generated_topology_edges=topology_count or 0,
        generated_ground_truth_edges=ground_truth_count or 0,
        generated_scheduled_outages=scheduled_count or 0,
        generated_logical_digest=network.logical_digest,
    )


async def _external_id_map(
    session: AsyncSession,
    external_column: InstrumentedAttribute[str],
    internal_column: InstrumentedAttribute[int],
    external_ids: tuple[str, ...],
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(external_column, internal_column).where(external_column.in_(external_ids))
        )
    ).all()
    result = {external_id: internal_id for external_id, internal_id in rows}
    if len(result) != len(external_ids):
        raise RuntimeError("generated seed did not resolve every external identifier")
    return result


def _initial_pole_status(device: GeneratedDevice | None) -> PoleStatus:
    if device is None:
        return PoleStatus.NO_DEVICE
    if device.health_status == DeviceHealthStatus.STALE:
        return PoleStatus.STALE
    return PoleStatus.LIVE
