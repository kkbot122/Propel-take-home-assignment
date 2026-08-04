from typing import Protocol

from propel.domain.enums import TopologySource
from propel.topology.inference import infer_geographic_topology
from propel.topology.models import (
    RecordedTopologyEdge,
    RootedTopology,
    TopologyQuality,
    TopologyQualityTier,
    TopologyRequest,
)


class TopologyProvider(Protocol):
    def provide(self, request: TopologyRequest) -> RootedTopology: ...


class SurveyedTopologyProvider:
    def provide(self, request: TopologyRequest) -> RootedTopology:
        edges = tuple(
            edge for edge in request.recorded_edges if edge.source == TopologySource.SURVEYED
        )
        expected_children = {pole.pole_id for pole in request.poles}
        actual_children = {edge.child_pole_id for edge in edges}
        if (
            not edges
            or actual_children != expected_children
            or len(edges) != len(actual_children)
            or not _is_rooted_tree(edges, expected_children)
        ):
            return RootedTopology(
                dt_id=request.dt_id,
                topology_version=request.topology_version,
                source=TopologySource.SURVEYED if edges else None,
                edges=(),
                quality=TopologyQuality(
                    0,
                    TopologyQualityTier.UNUSABLE,
                    ("surveyed topology is incomplete or has duplicate children",),
                ),
            )
        return RootedTopology(
            dt_id=request.dt_id,
            topology_version=request.topology_version,
            source=TopologySource.SURVEYED,
            edges=tuple(sorted(edges, key=_edge_key)),
            quality=TopologyQuality(1, TopologyQualityTier.SURVEYED),
        )


class InferredTopologyProvider:
    def provide(self, request: TopologyRequest) -> RootedTopology:
        recorded = tuple(
            edge for edge in request.recorded_edges if edge.source == TopologySource.INFERRED
        )
        if not recorded:
            return infer_geographic_topology(request)
        expected_children = {pole.pole_id for pole in request.poles}
        actual_children = {edge.child_pole_id for edge in recorded}
        inference_versions = {edge.inference_version for edge in recorded}
        if (
            actual_children != expected_children
            or len(recorded) != len(actual_children)
            or None in inference_versions
            or len(inference_versions) != 1
            or not _is_rooted_tree(recorded, expected_children)
        ):
            return RootedTopology(
                dt_id=request.dt_id,
                topology_version=request.topology_version,
                source=TopologySource.INFERRED,
                edges=(),
                quality=TopologyQuality(
                    0,
                    TopologyQualityTier.UNUSABLE,
                    ("recorded inferred topology is incomplete or inconsistent",),
                ),
            )
        confidences = tuple(edge.edge_confidence for edge in recorded)
        score = round(0.6 * (sum(confidences) / len(confidences)) + 0.4 * min(confidences), 4)
        tier = (
            TopologyQualityTier.STRONGLY_INFERRED
            if score >= 0.7
            else TopologyQualityTier.WEAKLY_INFERRED
        )
        reasons = ("surveyed connectivity is unavailable; topology is inferred from geography",)
        if tier == TopologyQualityTier.WEAKLY_INFERRED:
            reasons += ("one or more inferred edges have weak geographic separation",)
        return RootedTopology(
            dt_id=request.dt_id,
            topology_version=request.topology_version,
            source=TopologySource.INFERRED,
            edges=tuple(sorted(recorded, key=_edge_key)),
            quality=TopologyQuality(score, tier, reasons),
            inference_version=next(iter(inference_versions)),
        )


class CompositeTopologyProvider:
    def __init__(self) -> None:
        self._surveyed = SurveyedTopologyProvider()
        self._inferred = InferredTopologyProvider()

    def provide(self, request: TopologyRequest) -> RootedTopology:
        if any(edge.source == TopologySource.SURVEYED for edge in request.recorded_edges):
            return self._surveyed.provide(request)
        return self._inferred.provide(request)


def _edge_key(edge: RecordedTopologyEdge) -> tuple[str, str]:
    return (edge.parent_pole_id or "", edge.child_pole_id)


def _is_rooted_tree(edges: tuple[RecordedTopologyEdge, ...], expected_children: set[str]) -> bool:
    children: dict[str | None, list[str]] = {}
    for edge in edges:
        if edge.parent_pole_id is not None and edge.parent_pole_id not in expected_children:
            return False
        children.setdefault(edge.parent_pole_id, []).append(edge.child_pole_id)
    pending = sorted(children.get(None, ()), reverse=True)
    visited: set[str] = set()
    while pending:
        pole_id = pending.pop()
        if pole_id in visited:
            return False
        visited.add(pole_id)
        pending.extend(sorted(children.get(pole_id, ()), reverse=True))
    return visited == expected_children
