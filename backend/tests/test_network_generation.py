from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from propel.domain.enums import (
    DeviceHealthStatus,
    SimulatorFaultType,
    TelemetryEventType,
    TopologySource,
)
from propel.simulator.generation import (
    NetworkGenerationConfig,
    generate_fault_telemetry,
    generate_network,
    generate_restoration_telemetry,
    validate_generated_network,
)


def test_same_seed_produces_byte_equivalent_logical_ground_truth() -> None:
    first = generate_network()
    second = generate_network()

    assert first.canonical_json().encode() == second.canonical_json().encode()
    assert first.logical_digest == second.logical_digest
    assert len(first.substations) == 2
    assert len(first.feeders) == 4
    assert len(first.transformers) == 16
    assert 1_800 <= len(first.poles) <= 2_200


@pytest.mark.parametrize("seed", [1, 91_337, 4_294_967_291])
def test_different_seeds_preserve_radial_graph_and_identity_invariants(seed: int) -> None:
    config = NetworkGenerationConfig(
        seed=seed,
        substation_count=2,
        feeders_per_substation=2,
        transformers_per_feeder=3,
        min_poles_per_transformer=14,
        max_poles_per_transformer=22,
    )
    network = generate_network(config)
    validate_generated_network(network)

    pole_by_id = {pole.pole_id: pole for pole in network.poles}
    edges_by_dt = defaultdict(list)
    for edge in network.ground_truth_edges:
        edges_by_dt[edge.dt_id].append(edge)
    pole_counts = Counter(pole.dt_id for pole in network.poles)
    depths: set[int] = set()
    for transformer in network.transformers:
        edges = edges_by_dt[transformer.dt_id]
        assert len(edges) == pole_counts[transformer.dt_id]
        parent_by_child = {edge.child_pole_id: edge.parent_pole_id for edge in edges}
        assert len([parent for parent in parent_by_child.values() if parent is None]) == 1
        for pole_id in parent_by_child:
            visited: set[str] = set()
            current: str | None = pole_id
            while current is not None:
                assert current not in visited
                visited.add(current)
                current = parent_by_child[current]
            depths.add(len(visited))
            assert pole_by_id[pole_id].dt_id == transformer.dt_id
    assert len(depths) > 3
    assert any(pole.terminal for pole in network.poles)
    assert len(set(pole_counts.values())) > 1


def test_registry_projection_and_device_ratios_match_configuration() -> None:
    config = NetworkGenerationConfig()
    network = generate_network(config)
    transformer_by_id = {item.dt_id: item for item in network.transformers}

    assert sum(item.surveyed for item in network.transformers) == round(
        len(network.transformers) * config.surveyed_transformer_ratio
    )
    assert len(network.devices) == round(len(network.poles) * config.sensor_coverage_ratio)
    assert sum(
        device.health_status == DeviceHealthStatus.STALE for device in network.devices
    ) == round(len(network.devices) * config.offline_device_ratio)
    assert sum(device.firmware.startswith("1.2.") for device in network.devices) == round(
        len(network.devices) * config.firmware_12_ratio
    )

    visible_sources = defaultdict(set)
    truth_parent = {
        (edge.dt_id, edge.child_pole_id): edge.parent_pole_id for edge in network.ground_truth_edges
    }
    inferred_differs_from_truth = False
    for edge in network.visible_edges:
        visible_sources[edge.dt_id].add(edge.source)
        if (
            not transformer_by_id[edge.dt_id].surveyed
            and edge.parent_pole_id != truth_parent[(edge.dt_id, edge.child_pole_id)]
        ):
            inferred_differs_from_truth = True
    for transformer in network.transformers:
        expected = TopologySource.SURVEYED if transformer.surveyed else TopologySource.INFERRED
        assert visible_sources[transformer.dt_id] == {expected}
    assert inferred_differs_from_truth


def test_noisy_telemetry_respects_bindings_silence_and_sequence_rules() -> None:
    network = generate_network()
    scenario = next(item for item in network.scenarios if item.scenario_id == "noisy-span")
    deliveries = generate_fault_telemetry(
        network,
        scenario,
        datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
    )
    device_by_pole = {device.pole_id: device for device in network.devices}
    roles = Counter(delivery.role for delivery in deliveries)

    assert roles["duplicate_loss"] == 1
    assert roles["out_of_order_prior_state"] == 1
    assert any(delivery.delay_ms == 250 for delivery in deliveries)
    assert not set(scenario.noise.omit_loss_pole_ids).intersection(
        delivery.command.pole_id
        for delivery in deliveries
        if delivery.command.event == TelemetryEventType.POWER_LOST
    )
    for delivery in deliveries:
        device = device_by_pole[delivery.command.pole_id]
        assert delivery.command.device_id == device.device_id
    reordered_pole = scenario.noise.out_of_order_pole_ids[0]
    reordered = [item.command for item in deliveries if item.command.pole_id == reordered_pole]
    assert [item.sequence for item in reordered] == [2, 1]

    dt_scenario = next(item for item in network.scenarios if item.scenario_id == "dt-fault")
    dt_deliveries = generate_fault_telemetry(
        network,
        dt_scenario,
        datetime(2026, 8, 4, 10, 1, tzinfo=UTC),
    )
    loss_poles = {
        item.command.pole_id
        for item in dt_deliveries
        if item.command.event == TelemetryEventType.POWER_LOST
    }
    silent_poles = {
        device.pole_id
        for device in network.devices
        if device.health_status == DeviceHealthStatus.STALE or not device.can_report_power_loss
    }
    assert loss_poles.isdisjoint(silent_poles)


def test_fixed_scenarios_cover_faults_uncertainty_and_restoration() -> None:
    network = generate_network()
    scenarios = {item.scenario_id: item for item in network.scenarios}
    fault_types = {fault.fault_type for scenario in network.scenarios for fault in scenario.faults}

    assert fault_types == {
        SimulatorFaultType.SPAN_FAULT,
        SimulatorFaultType.DT_FAULT,
        SimulatorFaultType.FEEDER_FAULT,
    }
    assert scenarios["scheduled-span"].scheduled
    assert len(scenarios["simultaneous-spans"].faults) == 2
    surveyed_dt = scenarios["surveyed-span"].faults[0].dt_id
    inferred_dt = scenarios["inferred-span"].faults[0].dt_id
    transformer_by_id = {item.dt_id: item for item in network.transformers}
    assert transformer_by_id[surveyed_dt].surveyed
    assert not transformer_by_id[inferred_dt].surveyed

    occurred_at = datetime(2026, 8, 4, 10, 2, tzinfo=UTC)
    partial = scenarios["partial-restoration"]
    loss_count = sum(
        item.command.event == TelemetryEventType.POWER_LOST
        for item in generate_fault_telemetry(network, partial, occurred_at)
    )
    partial_restoration = generate_restoration_telemetry(network, partial, occurred_at)
    assert len(partial_restoration) == 2 * round(loss_count * 0.5)

    complete = replace(scenarios["surveyed-span"], restoration_fraction=1.0)
    complete_loss_count = sum(
        item.command.event == TelemetryEventType.POWER_LOST
        for item in generate_fault_telemetry(network, complete, occurred_at)
    )
    assert len(generate_restoration_telemetry(network, complete, occurred_at)) == (
        2 * complete_loss_count
    )
