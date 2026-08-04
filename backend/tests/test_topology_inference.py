from dataclasses import replace

import pytest

from propel.domain.enums import TopologySource
from propel.topology.inference import (
    InvalidTopologyCoordinatesError,
    infer_geographic_topology,
)
from propel.topology.models import (
    RecordedTopologyEdge,
    TopologyPole,
    TopologyQualityTier,
    TopologyRequest,
)
from propel.topology.providers import CompositeTopologyProvider


def request(*, reverse: bool = False) -> TopologyRequest:
    poles = (
        TopologyPole("P-201", 12.90018, 77.60000),
        TopologyPole("P-202", 12.90036, 77.60000),
        TopologyPole("P-203", 12.90054, 77.60000),
        TopologyPole("P-204", 12.90036, 77.60018),
    )
    return TopologyRequest(
        dt_id="DT-003",
        dt_latitude=12.90000,
        dt_longitude=77.60000,
        topology_version=1,
        poles=tuple(reversed(poles)) if reverse else poles,
    )


def test_same_coordinate_fixture_always_produces_same_rooted_tree() -> None:
    first = infer_geographic_topology(request())
    second = infer_geographic_topology(request(reverse=True))

    assert first == second
    assert first.source == TopologySource.INFERRED
    assert first.quality.tier == TopologyQualityTier.STRONGLY_INFERRED
    assert [(edge.parent_pole_id, edge.child_pole_id) for edge in first.edges] == [
        (None, "P-201"),
        ("P-201", "P-202"),
        ("P-202", "P-203"),
        ("P-202", "P-204"),
    ]


def test_inferred_tree_contains_each_pole_once_and_is_acyclic() -> None:
    topology = infer_geographic_topology(request())

    children = [edge.child_pole_id for edge in topology.edges]
    assert sorted(children) == ["P-201", "P-202", "P-203", "P-204"]
    assert len(children) == len(set(children))
    assert len(topology.edges) == len(request().poles)
    assert all(edge.source == TopologySource.INFERRED for edge in topology.edges)
    assert all(edge.distance_m <= 120 for edge in topology.edges)
    assert all(edge.inference_version == "geo-mst-v1" for edge in topology.edges)


def test_inference_request_has_no_simulator_ground_truth_channel() -> None:
    assert tuple(TopologyRequest.__dataclass_fields__) == (
        "dt_id",
        "dt_latitude",
        "dt_longitude",
        "topology_version",
        "poles",
        "recorded_edges",
    )


def test_unambiguous_near_edge_scores_higher_than_ambiguous_branch_edge() -> None:
    single = infer_geographic_topology(replace(request(), poles=(request().poles[0],)))
    ambiguous = infer_geographic_topology(request())

    assert single.edges[0].edge_confidence > ambiguous.edges[0].edge_confidence


def test_invalid_coordinate_is_rejected_before_candidate_generation() -> None:
    invalid = replace(
        request(),
        poles=request().poles + (TopologyPole("P-299", 91, 77.6),),
    )

    with pytest.raises(InvalidTopologyCoordinatesError):
        infer_geographic_topology(invalid)


def test_disconnected_geography_is_rejected_as_unusable() -> None:
    disconnected = replace(
        request(),
        poles=request().poles + (TopologyPole("P-299", 13.20000, 78.00000),),
    )

    topology = infer_geographic_topology(disconnected)

    assert topology.edges == ()
    assert topology.quality.tier == TopologyQualityTier.UNUSABLE
    assert "disconnected" in topology.quality.limiting_factors[0]


def test_surveyed_topology_takes_precedence_over_inference() -> None:
    surveyed = tuple(
        RecordedTopologyEdge(
            parent_pole_id=(None if index == 0 else request().poles[index - 1].pole_id),
            child_pole_id=pole.pole_id,
            source=TopologySource.SURVEYED,
            distance_m=20,
            edge_confidence=1,
        )
        for index, pole in enumerate(request().poles)
    )
    mixed_request = replace(
        request(),
        recorded_edges=surveyed
        + (
            RecordedTopologyEdge(
                None,
                "P-201",
                TopologySource.INFERRED,
                20,
                0.9,
                "geo-mst-v1",
            ),
        ),
    )

    topology = CompositeTopologyProvider().provide(mixed_request)

    assert topology.source == TopologySource.SURVEYED
    assert topology.quality.tier == TopologyQualityTier.SURVEYED
    assert all(edge.source == TopologySource.SURVEYED for edge in topology.edges)


def test_recorded_inferred_cycle_is_rejected_as_unusable() -> None:
    cyclic_edges = (
        RecordedTopologyEdge("P-202", "P-201", TopologySource.INFERRED, 20, 0.8, "v1"),
        RecordedTopologyEdge("P-201", "P-202", TopologySource.INFERRED, 20, 0.8, "v1"),
        RecordedTopologyEdge(None, "P-203", TopologySource.INFERRED, 20, 0.8, "v1"),
        RecordedTopologyEdge("P-203", "P-204", TopologySource.INFERRED, 20, 0.8, "v1"),
    )

    topology = CompositeTopologyProvider().provide(replace(request(), recorded_edges=cyclic_edges))

    assert topology.edges == ()
    assert topology.quality.tier == TopologyQualityTier.UNUSABLE
