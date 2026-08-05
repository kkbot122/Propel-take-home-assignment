import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from math import cos, radians, sin

from propel.domain.enums import (
    DeviceHealthStatus,
    SimulatorFaultType,
    TelemetryEventType,
    TopologySource,
)
from propel.simulator.delivery import (
    DEFAULT_POWER_LOSS_DELIVERY_RATIO,
    DEFAULT_POWER_LOSS_DELIVERY_SEED,
    power_loss_delivery_succeeds,
)
from propel.telemetry.ingestion import TelemetryCommand
from propel.topology.inference import haversine_distance_m, infer_geographic_topology
from propel.topology.models import TopologyPole, TopologyRequest

GENERATOR_VERSION = "subdivision-v3"
EARTH_METRES_PER_DEGREE = 111_320.0


@dataclass(frozen=True, slots=True)
class NetworkGenerationConfig:
    seed: int = 7_307
    substation_count: int = 2
    feeders_per_substation: int = 2
    transformers_per_feeder: int = 4
    min_poles_per_transformer: int = 115
    max_poles_per_transformer: int = 135
    surveyed_transformer_ratio: float = 0.4
    sensor_coverage_ratio: float = 0.91
    offline_device_ratio: float = 0.04
    firmware_12_ratio: float = 0.08
    min_span_distance_m: float = 24.0
    max_span_distance_m: float = 42.0
    max_branches_per_transformer: int = 5
    base_latitude: float = 12.889
    base_longitude: float = 77.584
    subdivision_radius_m: float = 12_000.0

    def __post_init__(self) -> None:
        if self.seed < 0:
            raise ValueError("generation seed cannot be negative")
        if not 1 <= self.substation_count <= 20:
            raise ValueError("substation count must be between 1 and 20")
        if not 1 <= self.feeders_per_substation <= 20:
            raise ValueError("feeders per substation must be between 1 and 20")
        if not 1 <= self.transformers_per_feeder <= 50:
            raise ValueError("transformers per feeder must be between 1 and 50")
        if not 4 <= self.min_poles_per_transformer <= self.max_poles_per_transformer <= 500:
            raise ValueError("pole bounds must be ordered between 4 and 500")
        for name, value in (
            ("surveyed transformer ratio", self.surveyed_transformer_ratio),
            ("sensor coverage ratio", self.sensor_coverage_ratio),
            ("offline device ratio", self.offline_device_ratio),
            ("firmware 1.2 ratio", self.firmware_12_ratio),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if not 0 < self.surveyed_transformer_ratio < 1:
            raise ValueError("generated networks require both surveyed and inferred transformers")
        transformer_count = (
            self.substation_count * self.feeders_per_substation * self.transformers_per_feeder
        )
        surveyed_count = round(transformer_count * self.surveyed_transformer_ratio)
        if not 1 <= surveyed_count < transformer_count:
            raise ValueError(
                "surveyed ratio and hierarchy must select at least one surveyed and one inferred DT"
            )
        if not 5 <= self.min_span_distance_m <= self.max_span_distance_m <= 100:
            raise ValueError("span bounds must be ordered between 5 and 100 metres")
        if not 1 <= self.max_branches_per_transformer <= 5:
            raise ValueError("maximum branches must be between 1 and 5")
        if not -90 <= self.base_latitude <= 90 or not -180 <= self.base_longitude <= 180:
            raise ValueError("base coordinates are outside valid bounds")
        if self.subdivision_radius_m <= 0:
            raise ValueError("subdivision radius must be positive")


@dataclass(frozen=True, slots=True)
class GeneratedSubstation:
    substation_id: str
    name: str
    latitude: float
    longitude: float
    pin_code: str


@dataclass(frozen=True, slots=True)
class GeneratedFeeder:
    feeder_id: str
    substation_id: str
    name: str


@dataclass(frozen=True, slots=True)
class GeneratedTransformer:
    dt_id: str
    feeder_id: str
    name: str
    latitude: float
    longitude: float
    pin_code: str
    topology_version: int
    surveyed: bool


@dataclass(frozen=True, slots=True)
class GeneratedPole:
    pole_id: str
    dt_id: str
    feeder_id: str
    latitude: float
    longitude: float
    pin_code: str
    ward: str
    terminal: bool


@dataclass(frozen=True, slots=True)
class GeneratedDevice:
    device_id: str
    pole_id: str
    firmware: str
    battery_mv: int
    rssi: int
    health_status: DeviceHealthStatus
    can_report_power_loss: bool


@dataclass(frozen=True, slots=True)
class GeneratedEdge:
    dt_id: str
    parent_pole_id: str | None
    child_pole_id: str
    distance_m: float
    source: TopologySource
    edge_confidence: float
    topology_version: int
    inference_version: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioNoise:
    omit_loss_pole_ids: tuple[str, ...] = ()
    duplicate_pole_ids: tuple[str, ...] = ()
    delayed_pole_ids: tuple[str, ...] = ()
    out_of_order_pole_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratedFault:
    fault_type: SimulatorFaultType
    feeder_id: str | None = None
    dt_id: str | None = None
    parent_pole_id: str | None = None
    child_pole_id: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedScenario:
    scenario_id: str
    description: str
    faults: tuple[GeneratedFault, ...]
    noise: ScenarioNoise = ScenarioNoise()
    restoration_fraction: float = 1.0
    scheduled: bool = False
    complete_delivery: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedTelemetryDelivery:
    command: TelemetryCommand
    delay_ms: int
    role: str


@dataclass(frozen=True, slots=True)
class GeneratedNetwork:
    dataset_id: str
    generator_version: str
    config: NetworkGenerationConfig
    substations: tuple[GeneratedSubstation, ...]
    feeders: tuple[GeneratedFeeder, ...]
    transformers: tuple[GeneratedTransformer, ...]
    poles: tuple[GeneratedPole, ...]
    devices: tuple[GeneratedDevice, ...]
    ground_truth_edges: tuple[GeneratedEdge, ...]
    visible_edges: tuple[GeneratedEdge, ...]
    scenarios: tuple[GeneratedScenario, ...]

    def as_manifest(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "generator_version": self.generator_version,
            "seed": self.config.seed,
            "config": asdict(self.config),
            "counts": {
                "substations": len(self.substations),
                "feeders": len(self.feeders),
                "transformers": len(self.transformers),
                "poles": len(self.poles),
                "devices": len(self.devices),
                "ground_truth_edges": len(self.ground_truth_edges),
                "visible_edges": len(self.visible_edges),
            },
            "substations": [asdict(item) for item in self.substations],
            "feeders": [asdict(item) for item in self.feeders],
            "transformers": [asdict(item) for item in self.transformers],
            "poles": [asdict(item) for item in self.poles],
            "devices": [_enum_dict(item) for item in self.devices],
            "ground_truth_edges": [_enum_dict(item) for item in self.ground_truth_edges],
            "visible_edges": [_enum_dict(item) for item in self.visible_edges],
            "scenarios": [_scenario_dict(item) for item in self.scenarios],
        }

    def canonical_json(self) -> str:
        return json.dumps(self.as_manifest(), sort_keys=True, separators=(",", ":"))

    @property
    def logical_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def generate_network(config: NetworkGenerationConfig | None = None) -> GeneratedNetwork:
    config = config or NetworkGenerationConfig()
    config_digest = hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:8]
    prefix = f"GN-{config.seed:X}-{config_digest.upper()}"
    substations = _generate_substations(config, prefix)
    feeders = _generate_feeders(config, prefix, substations)
    transformer_shells = _generate_transformer_shells(config, prefix, substations, feeders)
    surveyed_ids = _select_ratio(
        tuple(item.dt_id for item in transformer_shells),
        config.surveyed_transformer_ratio,
        config.seed,
        "surveyed",
    )
    transformers = tuple(
        GeneratedTransformer(
            item.dt_id,
            item.feeder_id,
            item.name,
            item.latitude,
            item.longitude,
            item.pin_code,
            item.topology_version,
            item.dt_id in surveyed_ids,
        )
        for item in transformer_shells
    )
    poles: list[GeneratedPole] = []
    truth_edges: list[GeneratedEdge] = []
    for transformer in transformers:
        dt_poles, dt_edges = _generate_transformer_tree(config, prefix, transformer)
        poles.extend(dt_poles)
        truth_edges.extend(dt_edges)
    devices = _generate_devices(config, tuple(poles))
    visible_edges = _visible_topology(transformers, tuple(poles), tuple(truth_edges))
    network = GeneratedNetwork(
        dataset_id=f"{prefix}-{GENERATOR_VERSION}",
        generator_version=GENERATOR_VERSION,
        config=config,
        substations=substations,
        feeders=feeders,
        transformers=transformers,
        poles=tuple(sorted(poles, key=lambda item: item.pole_id)),
        devices=devices,
        ground_truth_edges=tuple(
            sorted(truth_edges, key=lambda item: (item.dt_id, item.child_pole_id))
        ),
        visible_edges=visible_edges,
        scenarios=(),
    )
    network = replace(network, scenarios=_generate_scenarios(network))
    validate_generated_network(network)
    return network


def validate_generated_network(network: GeneratedNetwork) -> None:
    _require_unique(item.substation_id for item in network.substations)
    _require_unique(item.feeder_id for item in network.feeders)
    _require_unique(item.dt_id for item in network.transformers)
    _require_unique(item.pole_id for item in network.poles)
    _require_unique(item.device_id for item in network.devices)
    feeder_ids = {item.feeder_id for item in network.feeders}
    transformer_by_id = {item.dt_id: item for item in network.transformers}
    pole_by_id = {item.pole_id: item for item in network.poles}
    if any(item.feeder_id not in feeder_ids for item in network.transformers):
        raise ValueError("generated transformer references an unknown feeder")
    for pole in network.poles:
        transformer = transformer_by_id.get(pole.dt_id)
        if transformer is None or transformer.feeder_id != pole.feeder_id:
            raise ValueError("generated pole has inconsistent DT or feeder membership")
        _validate_coordinate(pole.latitude, pole.longitude)
        if (
            haversine_distance_m(
                network.config.base_latitude,
                network.config.base_longitude,
                pole.latitude,
                pole.longitude,
            )
            > network.config.subdivision_radius_m
        ):
            raise ValueError("generated pole is outside the subdivision boundary")
    if any(item.pole_id not in pole_by_id for item in network.devices):
        raise ValueError("generated device references an unknown pole")
    _validate_edges(network.ground_truth_edges, transformer_by_id, pole_by_id)
    _validate_edges(network.visible_edges, transformer_by_id, pole_by_id)
    truth_by_dt = _edges_by_dt(network.ground_truth_edges)
    visible_by_dt = _edges_by_dt(network.visible_edges)
    for transformer in network.transformers:
        pole_count = sum(pole.dt_id == transformer.dt_id for pole in network.poles)
        if len(truth_by_dt[transformer.dt_id]) != pole_count:
            raise ValueError("ground-truth tree edge count does not match its pole count")
        if len(visible_by_dt[transformer.dt_id]) != pole_count:
            raise ValueError("visible topology does not cover every generated pole")
        visible_sources = {edge.source for edge in visible_by_dt[transformer.dt_id]}
        expected_source = (
            TopologySource.SURVEYED if transformer.surveyed else TopologySource.INFERRED
        )
        if visible_sources != {expected_source}:
            raise ValueError("visible topology provenance disagrees with registry projection")
    if len(network.devices) != round(len(network.poles) * network.config.sensor_coverage_ratio):
        raise ValueError("generated sensor coverage does not match configuration")
    device_poles = {device.pole_id for device in network.devices}
    if len(device_poles) != len(network.devices):
        raise ValueError("more than one generated device is bound to a pole")
    for scenario in network.scenarios:
        for fault in scenario.faults:
            if fault.feeder_id is not None and fault.feeder_id not in feeder_ids:
                raise ValueError("scenario references an unknown feeder")
            if fault.dt_id is not None and fault.dt_id not in transformer_by_id:
                raise ValueError("scenario references an unknown transformer")
            for pole_id in (fault.parent_pole_id, fault.child_pole_id):
                if pole_id is not None and pole_id not in pole_by_id:
                    raise ValueError("scenario references an unknown pole")


def _fault_delivery_context(network: GeneratedNetwork, scenario: GeneratedScenario) -> str:
    if len(scenario.faults) != 1:
        return f"{network.dataset_id}:{scenario.scenario_id}"
    fault = scenario.faults[0]
    if fault.fault_type == SimulatorFaultType.SPAN_FAULT:
        return (
            f"{fault.fault_type.value}:{fault.dt_id}:{fault.parent_pole_id}:{fault.child_pole_id}"
        )
    scope_id = fault.dt_id if fault.fault_type == SimulatorFaultType.DT_FAULT else fault.feeder_id
    return f"{fault.fault_type.value}:{scope_id}"


def generate_fault_telemetry(
    network: GeneratedNetwork,
    scenario: GeneratedScenario,
    occurred_at: datetime,
    *,
    power_loss_delivery_ratio: float = DEFAULT_POWER_LOSS_DELIVERY_RATIO,
    power_loss_delivery_seed: int = DEFAULT_POWER_LOSS_DELIVERY_SEED,
) -> tuple[GeneratedTelemetryDelivery, ...]:
    if occurred_at.utcoffset() is None:
        raise ValueError("scenario time must be timezone-aware")
    pole_by_id = {pole.pole_id: pole for pole in network.poles}
    device_by_pole = {device.pole_id: device for device in network.devices}
    parent_by_child = {
        edge.child_pole_id: edge.parent_pole_id for edge in network.ground_truth_edges
    }
    children: defaultdict[str, list[str]] = defaultdict(list)
    for child_id, parent_id in parent_by_child.items():
        if parent_id is not None:
            children[parent_id].append(child_id)
    affected: set[str] = set()
    upstream: set[str] = set()
    for fault in scenario.faults:
        if fault.fault_type == SimulatorFaultType.SPAN_FAULT:
            assert fault.child_pole_id is not None
            affected.update(_subtree(fault.child_pole_id, children))
            if fault.parent_pole_id is not None:
                upstream.add(fault.parent_pole_id)
        elif fault.fault_type == SimulatorFaultType.DT_FAULT:
            affected.update(pole.pole_id for pole in network.poles if pole.dt_id == fault.dt_id)
        else:
            affected.update(
                pole.pole_id for pole in network.poles if pole.feeder_id == fault.feeder_id
            )
    omitted = set(scenario.noise.omit_loss_pole_ids)
    forced_delivery = (
        set(scenario.noise.duplicate_pole_ids)
        | set(scenario.noise.delayed_pole_ids)
        | set(scenario.noise.out_of_order_pole_ids)
    )
    loss_delivery_context = _fault_delivery_context(network, scenario)
    deliveries: list[GeneratedTelemetryDelivery] = []
    for pole_id in sorted(upstream):
        device = device_by_pole.get(pole_id)
        if device is not None and device.health_status == DeviceHealthStatus.HEALTHY:
            deliveries.append(
                _delivery(device, pole_id, TelemetryEventType.HEARTBEAT, True, occurred_at, 1)
            )
    for pole_id in sorted(affected):
        device = device_by_pole.get(pole_id)
        if (
            device is None
            or device.health_status != DeviceHealthStatus.HEALTHY
            or not device.can_report_power_loss
            or pole_id in omitted
            or (
                not scenario.complete_delivery
                and pole_id not in forced_delivery
                and not power_loss_delivery_succeeds(
                    pole_id,
                    context=loss_delivery_context,
                    ratio=power_loss_delivery_ratio,
                    seed=power_loss_delivery_seed,
                )
            )
        ):
            continue
        loss = _delivery(
            device,
            pole_id,
            TelemetryEventType.POWER_LOST,
            False,
            occurred_at + timedelta(milliseconds=_rank(network.config.seed, "loss", pole_id) % 80),
            2,
            delay_ms=250 if pole_id in scenario.noise.delayed_pole_ids else 0,
        )
        if pole_id in scenario.noise.out_of_order_pole_ids:
            deliveries.append(loss)
            deliveries.append(
                _delivery(
                    device,
                    pole_id,
                    TelemetryEventType.HEARTBEAT,
                    True,
                    occurred_at - timedelta(seconds=1),
                    1,
                    role="out_of_order_prior_state",
                )
            )
        else:
            deliveries.append(loss)
        if pole_id in scenario.noise.duplicate_pole_ids:
            deliveries.append(
                GeneratedTelemetryDelivery(loss.command, loss.delay_ms, "duplicate_loss")
            )
    if not deliveries:
        raise ValueError("scenario produced no deliverable telemetry")
    if any(delivery.command.pole_id not in pole_by_id for delivery in deliveries):
        raise ValueError("scenario produced telemetry for an unknown pole")
    return tuple(deliveries)


def generate_restoration_telemetry(
    network: GeneratedNetwork,
    scenario: GeneratedScenario,
    occurred_at: datetime,
    *,
    power_loss_delivery_ratio: float = DEFAULT_POWER_LOSS_DELIVERY_RATIO,
    power_loss_delivery_seed: int = DEFAULT_POWER_LOSS_DELIVERY_SEED,
) -> tuple[GeneratedTelemetryDelivery, ...]:
    fault_deliveries = generate_fault_telemetry(
        network,
        scenario,
        occurred_at,
        power_loss_delivery_ratio=power_loss_delivery_ratio,
        power_loss_delivery_seed=power_loss_delivery_seed,
    )
    affected_ids = tuple(
        sorted(
            {
                delivery.command.pole_id
                for delivery in fault_deliveries
                if delivery.command.event == TelemetryEventType.POWER_LOST
            }
        )
    )
    restore_count = round(len(affected_ids) * scenario.restoration_fraction)
    restored_ids = set(affected_ids[:restore_count])
    device_by_pole = {device.pole_id: device for device in network.devices}
    deliveries: list[GeneratedTelemetryDelivery] = []
    for index, pole_id in enumerate(sorted(restored_ids)):
        device = device_by_pole[pole_id]
        boot_at = occurred_at + timedelta(milliseconds=index * 2)
        deliveries.extend(
            (
                _delivery(device, pole_id, TelemetryEventType.BOOT, True, boot_at, 0),
                _delivery(
                    device,
                    pole_id,
                    TelemetryEventType.POWER_RESTORED,
                    True,
                    boot_at + timedelta(milliseconds=1),
                    1,
                ),
            )
        )
    return tuple(deliveries)


def _generate_substations(
    config: NetworkGenerationConfig, prefix: str
) -> tuple[GeneratedSubstation, ...]:
    result: list[GeneratedSubstation] = []
    for index in range(config.substation_count):
        angle = 360 * index / config.substation_count
        distance = 0 if index == 0 else 3_600 + 400 * index
        latitude, longitude = _offset(config.base_latitude, config.base_longitude, distance, angle)
        result.append(
            GeneratedSubstation(
                f"{prefix}-S{index + 1:02d}",
                f"Synthetic Substation {index + 1}",
                latitude,
                longitude,
                f"{560100 + index:06d}",
            )
        )
    return tuple(result)


def _generate_feeders(
    config: NetworkGenerationConfig,
    prefix: str,
    substations: tuple[GeneratedSubstation, ...],
) -> tuple[GeneratedFeeder, ...]:
    result: list[GeneratedFeeder] = []
    global_index = 0
    for substation in substations:
        for _ in range(config.feeders_per_substation):
            global_index += 1
            result.append(
                GeneratedFeeder(
                    f"{prefix}-F{global_index:03d}",
                    substation.substation_id,
                    f"Synthetic Feeder {global_index}",
                )
            )
    return tuple(result)


def _generate_transformer_shells(
    config: NetworkGenerationConfig,
    prefix: str,
    substations: tuple[GeneratedSubstation, ...],
    feeders: tuple[GeneratedFeeder, ...],
) -> tuple[GeneratedTransformer, ...]:
    substation_by_id = {item.substation_id: item for item in substations}
    result: list[GeneratedTransformer] = []
    dt_index = 0
    for feeder_index, feeder in enumerate(feeders):
        substation = substation_by_id[feeder.substation_id]
        feeder_slot = feeder_index % config.feeders_per_substation
        base_angle = 360 * feeder_slot / config.feeders_per_substation + 25 * (
            feeder_index // config.feeders_per_substation
        )
        for dt_slot in range(config.transformers_per_feeder):
            dt_index += 1
            angle = base_angle + _signed_unit(config.seed, "dt-angle", dt_index) * 12
            distance = 350 + dt_slot * 520 + 80 * _unit(config.seed, "dt-distance", dt_index)
            latitude, longitude = _offset(
                substation.latitude, substation.longitude, distance, angle
            )
            result.append(
                GeneratedTransformer(
                    dt_id=f"{prefix}-D{dt_index:04d}",
                    feeder_id=feeder.feeder_id,
                    name=f"Synthetic DT {dt_index}",
                    latitude=latitude,
                    longitude=longitude,
                    pin_code=substation.pin_code,
                    topology_version=1,
                    surveyed=False,
                )
            )
    return tuple(result)


def _generate_transformer_tree(
    config: NetworkGenerationConfig,
    prefix: str,
    transformer: GeneratedTransformer,
) -> tuple[tuple[GeneratedPole, ...], tuple[GeneratedEdge, ...]]:
    pole_count = _bounded_int(
        config.seed,
        config.min_poles_per_transformer,
        config.max_poles_per_transformer,
        "pole-count",
        transformer.dt_id,
    )
    branch_count = _bounded_int(
        config.seed,
        1,
        min(config.max_branches_per_transformer, max(1, pole_count // 8)),
        "branch-count",
        transformer.dt_id,
    )
    main_count = max(3, round(pole_count * 0.55))
    main_angle = 360 * _unit(config.seed, "main-angle", transformer.dt_id)
    coordinates: dict[str, tuple[float, float]] = {}
    parents: dict[str, str | None] = {}
    order: list[str] = []
    current_latitude = transformer.latitude
    current_longitude = transformer.longitude
    previous_id: str | None = None
    for index in range(main_count):
        pole_id = f"{prefix}-P{transformer.dt_id.rsplit('D', 1)[1]}-{index + 1:03d}"
        distance = _span_distance(config, transformer.dt_id, "main", index)
        angle = main_angle + _signed_unit(config.seed, "main-jitter", transformer.dt_id, index) * 8
        current_latitude, current_longitude = _offset(
            current_latitude, current_longitude, distance, angle
        )
        coordinates[pole_id] = (current_latitude, current_longitude)
        parents[pole_id] = previous_id
        order.append(pole_id)
        previous_id = pole_id
    remaining = pole_count - main_count
    branch_lengths = [remaining // branch_count] * branch_count
    for index in range(remaining % branch_count):
        branch_lengths[index] += 1
    next_index = main_count + 1
    for branch_index, branch_length in enumerate(branch_lengths):
        attachment_index = max(
            1, min(main_count - 2, round((branch_index + 1) * main_count / (branch_count + 1)))
        )
        parent_id = order[attachment_index]
        current_latitude, current_longitude = coordinates[parent_id]
        side = -1 if branch_index % 2 else 1
        branch_angle = main_angle + side * (52 + 13 * branch_index)
        for offset_index in range(branch_length):
            pole_id = f"{prefix}-P{transformer.dt_id.rsplit('D', 1)[1]}-{next_index:03d}"
            next_index += 1
            distance = _span_distance(
                config, transformer.dt_id, f"branch-{branch_index}", offset_index
            )
            angle = (
                branch_angle
                + _signed_unit(
                    config.seed, "branch-jitter", transformer.dt_id, branch_index, offset_index
                )
                * 10
            )
            current_latitude, current_longitude = _offset(
                current_latitude, current_longitude, distance, angle
            )
            coordinates[pole_id] = (current_latitude, current_longitude)
            parents[pole_id] = parent_id
            order.append(pole_id)
            parent_id = pole_id
    parent_ids = {parent_id for parent_id in parents.values() if parent_id is not None}
    poles: list[GeneratedPole] = []
    for pole_id in order:
        latitude, longitude = coordinates[pole_id]
        latitude, longitude = _offset(
            latitude,
            longitude,
            1.5 * _unit(config.seed, "gps-distance", pole_id),
            360 * _unit(config.seed, "gps-angle", pole_id),
        )
        coordinates[pole_id] = (latitude, longitude)
        poles.append(
            GeneratedPole(
                pole_id,
                transformer.dt_id,
                transformer.feeder_id,
                latitude,
                longitude,
                transformer.pin_code,
                f"WARD-{1 + _rank(config.seed, 'ward', transformer.dt_id) % 12:02d}",
                pole_id not in parent_ids,
            )
        )
    edges = tuple(
        GeneratedEdge(
            transformer.dt_id,
            parents[pole_id],
            pole_id,
            round(
                haversine_distance_m(
                    transformer.latitude
                    if parents[pole_id] is None
                    else coordinates[parents[pole_id]][0],
                    transformer.longitude
                    if parents[pole_id] is None
                    else coordinates[parents[pole_id]][1],
                    coordinates[pole_id][0],
                    coordinates[pole_id][1],
                ),
                3,
            ),
            TopologySource.SURVEYED,
            1.0,
            transformer.topology_version,
        )
        for pole_id in order
    )
    return tuple(poles), edges


def _generate_devices(
    config: NetworkGenerationConfig, poles: tuple[GeneratedPole, ...]
) -> tuple[GeneratedDevice, ...]:
    pole_ids = tuple(pole.pole_id for pole in poles)
    covered = _select_ratio(pole_ids, config.sensor_coverage_ratio, config.seed, "coverage")
    covered_ids = tuple(sorted(covered))
    offline = _select_ratio(covered_ids, config.offline_device_ratio, config.seed, "offline")
    firmware_12 = _select_ratio(covered_ids, config.firmware_12_ratio, config.seed, "firmware-12")
    return tuple(
        GeneratedDevice(
            device_id=f"DEV-{pole_id}",
            pole_id=pole_id,
            firmware="1.2.9" if pole_id in firmware_12 else "1.4.2",
            battery_mv=2_850 + _rank(config.seed, "battery", pole_id) % 851,
            rssi=-112 + _rank(config.seed, "rssi", pole_id) % 35,
            health_status=(
                DeviceHealthStatus.STALE if pole_id in offline else DeviceHealthStatus.HEALTHY
            ),
            can_report_power_loss=pole_id not in firmware_12,
        )
        for pole_id in covered_ids
    )


def _visible_topology(
    transformers: tuple[GeneratedTransformer, ...],
    poles: tuple[GeneratedPole, ...],
    truth_edges: tuple[GeneratedEdge, ...],
) -> tuple[GeneratedEdge, ...]:
    poles_by_dt: defaultdict[str, list[GeneratedPole]] = defaultdict(list)
    truth_by_dt = _edges_by_dt(truth_edges)
    result: list[GeneratedEdge] = []
    for pole in poles:
        poles_by_dt[pole.dt_id].append(pole)
    for transformer in transformers:
        if transformer.surveyed:
            result.extend(truth_by_dt[transformer.dt_id])
            continue
        inferred = infer_geographic_topology(
            TopologyRequest(
                dt_id=transformer.dt_id,
                dt_latitude=transformer.latitude,
                dt_longitude=transformer.longitude,
                topology_version=transformer.topology_version,
                poles=tuple(
                    TopologyPole(pole.pole_id, pole.latitude, pole.longitude)
                    for pole in sorted(
                        poles_by_dt[transformer.dt_id], key=lambda item: item.pole_id
                    )
                ),
            )
        )
        if not inferred.edges:
            raise ValueError(f"generated geography for {transformer.dt_id} is not inferable")
        result.extend(
            GeneratedEdge(
                transformer.dt_id,
                edge.parent_pole_id,
                edge.child_pole_id,
                edge.distance_m,
                edge.source,
                edge.edge_confidence,
                transformer.topology_version,
                edge.inference_version,
            )
            for edge in inferred.edges
        )
    return tuple(sorted(result, key=lambda item: (item.dt_id, item.child_pole_id)))


def _generate_scenarios(network: GeneratedNetwork) -> tuple[GeneratedScenario, ...]:
    truth_by_dt = _edges_by_dt(network.ground_truth_edges)
    devices = {
        device.pole_id: device
        for device in network.devices
        if device.health_status == DeviceHealthStatus.HEALTHY and device.can_report_power_loss
    }
    surveyed, surveyed_edge = _select_scenario_transformer(
        network.transformers,
        truth_by_dt,
        devices,
        require_surveyed=True,
        require_complete_subtree=True,
    )
    inferred, inferred_edge = _select_scenario_transformer(
        network.transformers,
        truth_by_dt,
        devices,
        require_surveyed=False,
    )
    noisy_edge = _scenario_edge(truth_by_dt[surveyed.dt_id], devices, surveyed.dt_id)
    feeder_transformers = tuple(
        item for item in network.transformers if item.feeder_id == surveyed.feeder_id
    )
    second_transformer, second_edge = _select_scenario_transformer(
        network.transformers,
        truth_by_dt,
        devices,
        excluded_dt_ids={surveyed.dt_id},
        require_complete_subtree=True,
    )
    third_transformer, third_edge = _select_scenario_transformer(
        network.transformers,
        truth_by_dt,
        devices,
        excluded_dt_ids={surveyed.dt_id, second_transformer.dt_id},
        require_complete_subtree=True,
    )
    children: defaultdict[str, list[str]] = defaultdict(list)
    for edge in truth_by_dt[surveyed.dt_id]:
        if edge.parent_pole_id is not None:
            children[edge.parent_pole_id].append(edge.child_pole_id)
    noisy_ids = tuple(
        pole_id for pole_id in _subtree(noisy_edge.child_pole_id, children) if pole_id in devices
    )[:4]
    return (
        GeneratedScenario(
            "surveyed-span",
            "Surveyed live-to-dark boundary with complete telemetry",
            (
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=surveyed.dt_id,
                    parent_pole_id=surveyed_edge.parent_pole_id,
                    child_pole_id=surveyed_edge.child_pole_id,
                ),
            ),
            complete_delivery=True,
        ),
        GeneratedScenario(
            "inferred-span",
            "Hidden physical span on a topology-missing transformer",
            (
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=inferred.dt_id,
                    parent_pole_id=inferred_edge.parent_pole_id,
                    child_pole_id=inferred_edge.child_pole_id,
                ),
            ),
        ),
        GeneratedScenario(
            "dt-fault",
            "Transformer-wide loss with partial sensor coverage",
            (GeneratedFault(SimulatorFaultType.DT_FAULT, dt_id=surveyed.dt_id),),
        ),
        GeneratedScenario(
            "feeder-fault",
            "Correlated DT-wide loss across one feeder",
            (
                GeneratedFault(
                    SimulatorFaultType.FEEDER_FAULT,
                    feeder_id=surveyed.feeder_id,
                ),
            ),
        ),
        GeneratedScenario(
            "scheduled-span",
            "Span loss overlapping a planned-work window",
            (
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=surveyed.dt_id,
                    parent_pole_id=surveyed_edge.parent_pole_id,
                    child_pole_id=surveyed_edge.child_pole_id,
                ),
            ),
            scheduled=True,
            complete_delivery=True,
        ),
        GeneratedScenario(
            "noisy-span",
            "Loss messages include omission, duplication, delay, and reordering",
            (
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=surveyed.dt_id,
                    parent_pole_id=noisy_edge.parent_pole_id,
                    child_pole_id=noisy_edge.child_pole_id,
                ),
            ),
            ScenarioNoise(
                omit_loss_pole_ids=noisy_ids[:1],
                duplicate_pole_ids=noisy_ids[1:2],
                delayed_pole_ids=noisy_ids[2:3],
                out_of_order_pole_ids=noisy_ids[3:4],
            ),
        ),
        GeneratedScenario(
            "dead-sensor",
            "One healthy powered pole device stops reporting without a grid fault",
            (),
        ),
        GeneratedScenario(
            "simultaneous-spans",
            "Three independent physical span faults",
            (
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=surveyed.dt_id,
                    parent_pole_id=surveyed_edge.parent_pole_id,
                    child_pole_id=surveyed_edge.child_pole_id,
                ),
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=second_transformer.dt_id,
                    parent_pole_id=second_edge.parent_pole_id,
                    child_pole_id=second_edge.child_pole_id,
                ),
                GeneratedFault(
                    SimulatorFaultType.SPAN_FAULT,
                    dt_id=third_transformer.dt_id,
                    parent_pole_id=third_edge.parent_pole_id,
                    child_pole_id=third_edge.child_pole_id,
                ),
            ),
            complete_delivery=True,
        ),
        GeneratedScenario(
            "partial-restoration",
            "Only half of delivered loss observations restore",
            (GeneratedFault(SimulatorFaultType.DT_FAULT, dt_id=feeder_transformers[0].dt_id),),
            restoration_fraction=0.5,
        ),
    )


def _validate_edges(
    edges: tuple[GeneratedEdge, ...],
    transformers: dict[str, GeneratedTransformer],
    poles: dict[str, GeneratedPole],
) -> None:
    by_dt = _edges_by_dt(edges)
    for dt_id, dt_edges in by_dt.items():
        if dt_id not in transformers:
            raise ValueError("topology references an unknown transformer")
        children = [edge.child_pole_id for edge in dt_edges]
        if len(children) != len(set(children)):
            raise ValueError("topology gives a pole more than one parent")
        parent_by_child = {edge.child_pole_id: edge.parent_pole_id for edge in dt_edges}
        roots = tuple(
            child_id for child_id, parent_id in parent_by_child.items() if parent_id is None
        )
        if len(roots) != 1:
            raise ValueError("generated topology must have exactly one transformer root")
        if any(poles[child_id].dt_id != dt_id for child_id in children):
            raise ValueError("topology child crosses a transformer boundary")
        if any(
            parent_id is not None and poles[parent_id].dt_id != dt_id
            for parent_id in parent_by_child.values()
        ):
            raise ValueError("topology parent crosses a transformer boundary")
        for child_id in children:
            visited: set[str] = set()
            current: str | None = child_id
            while current is not None:
                if current in visited:
                    raise ValueError("generated topology contains a cycle")
                visited.add(current)
                current = parent_by_child.get(current)
            if roots[0] not in visited:
                raise ValueError("generated topology is disconnected from its transformer root")


def _scenario_edge(
    edges: list[GeneratedEdge],
    eligible_devices: dict[str, GeneratedDevice],
    dt_id: str,
    *,
    require_complete_subtree: bool = False,
) -> GeneratedEdge:
    eligible_edges = tuple(
        item
        for item in edges
        if item.parent_pole_id in eligible_devices and item.child_pole_id in eligible_devices
    )
    if require_complete_subtree:
        children: defaultdict[str, list[str]] = defaultdict(list)
        for item in edges:
            if item.parent_pole_id is not None:
                children[item.parent_pole_id].append(item.child_pole_id)
        complete_edges = tuple(
            (item, _subtree(item.child_pole_id, children))
            for item in eligible_edges
            if all(
                pole_id in eligible_devices for pole_id in _subtree(item.child_pole_id, children)
            )
        )
        edge = (
            min(
                complete_edges,
                key=lambda pair: (-len(pair[1]), pair[0].child_pole_id),
            )[0]
            if complete_edges
            else None
        )
    else:
        edge = next(iter(eligible_edges), None)
    if edge is None:
        raise ValueError(f"device configuration leaves {dt_id} without a usable scenario span")
    return edge


def _select_scenario_transformer(
    transformers: tuple[GeneratedTransformer, ...],
    edges_by_dt: defaultdict[str, list[GeneratedEdge]],
    eligible_devices: dict[str, GeneratedDevice],
    *,
    excluded_dt_ids: set[str] | None = None,
    require_surveyed: bool | None = None,
    require_complete_subtree: bool = False,
) -> tuple[GeneratedTransformer, GeneratedEdge]:
    excluded = excluded_dt_ids or set()
    for transformer in transformers:
        if transformer.dt_id in excluded:
            continue
        if require_surveyed is not None and transformer.surveyed != require_surveyed:
            continue
        try:
            edge = _scenario_edge(
                edges_by_dt[transformer.dt_id],
                eligible_devices,
                transformer.dt_id,
                require_complete_subtree=require_complete_subtree,
            )
        except ValueError:
            continue
        return transformer, edge
    raise ValueError("device configuration leaves no transformer with a usable scenario span")


def _edges_by_dt(edges: tuple[GeneratedEdge, ...]) -> defaultdict[str, list[GeneratedEdge]]:
    result: defaultdict[str, list[GeneratedEdge]] = defaultdict(list)
    for edge in edges:
        result[edge.dt_id].append(edge)
    return result


def _subtree(root_id: str, children: defaultdict[str, list[str]]) -> tuple[str, ...]:
    result: list[str] = []
    pending = [root_id]
    while pending:
        pole_id = pending.pop()
        result.append(pole_id)
        pending.extend(reversed(sorted(children[pole_id])))
    return tuple(result)


def _delivery(
    device: GeneratedDevice,
    pole_id: str,
    event: TelemetryEventType,
    energized: bool,
    occurred_at: datetime,
    sequence: int,
    *,
    delay_ms: int = 0,
    role: str | None = None,
) -> GeneratedTelemetryDelivery:
    return GeneratedTelemetryDelivery(
        TelemetryCommand(
            device_id=device.device_id,
            pole_id=pole_id,
            event=event,
            energized=energized,
            device_timestamp=occurred_at,
            sequence=sequence,
            battery_mv=device.battery_mv,
            rssi=device.rssi,
            firmware=device.firmware,
        ),
        delay_ms,
        role or event.value,
    )


def _scenario_dict(scenario: GeneratedScenario) -> dict[str, object]:
    return {
        "scenario_id": scenario.scenario_id,
        "description": scenario.description,
        "faults": [_enum_dict(fault) for fault in scenario.faults],
        "noise": asdict(scenario.noise),
        "restoration_fraction": scenario.restoration_fraction,
        "scheduled": scenario.scheduled,
        "complete_delivery": scenario.complete_delivery,
    }


def _enum_dict(value: object) -> dict[str, object]:
    result = asdict(value)
    for key, item in tuple(result.items()):
        if hasattr(item, "value"):
            result[key] = item.value
    return result


def _require_unique(values: Iterable[str]) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError("generated external identifiers are not unique")


def _select_ratio(
    values: tuple[str, ...], ratio: float, seed: int, namespace: str
) -> frozenset[str]:
    count = round(len(values) * ratio)
    ranked = sorted(values, key=lambda value: (_rank(seed, namespace, value), value))
    return frozenset(ranked[:count])


def _span_distance(config: NetworkGenerationConfig, dt_id: str, segment: str, index: int) -> float:
    return config.min_span_distance_m + (
        config.max_span_distance_m - config.min_span_distance_m
    ) * _unit(config.seed, "span", dt_id, segment, index)


def _bounded_int(seed: int, minimum: int, maximum: int, *parts: object) -> int:
    return minimum + _rank(seed, *parts) % (maximum - minimum + 1)


def _unit(seed: int, *parts: object) -> float:
    return _rank(seed, *parts) / ((1 << 64) - 1)


def _signed_unit(seed: int, *parts: object) -> float:
    return 2 * _unit(seed, *parts) - 1


def _rank(seed: int, *parts: object) -> int:
    payload = "|".join((str(seed), *(str(part) for part in parts)))
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big")


def _offset(
    latitude: float, longitude: float, distance_m: float, angle_degrees: float
) -> tuple[float, float]:
    angle = radians(angle_degrees)
    latitude_delta = distance_m * cos(angle) / EARTH_METRES_PER_DEGREE
    longitude_scale = max(0.1, cos(radians(latitude)))
    longitude_delta = distance_m * sin(angle) / (EARTH_METRES_PER_DEGREE * longitude_scale)
    return round(latitude + latitude_delta, 7), round(longitude + longitude_delta, 7)


def _validate_coordinate(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("generated coordinate is outside valid bounds")
