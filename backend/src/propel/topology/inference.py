from collections import defaultdict, deque
from dataclasses import dataclass
from math import asin, cos, floor, radians, sin, sqrt

from propel.domain.enums import TopologySource
from propel.topology.models import (
    RecordedTopologyEdge,
    RootedTopology,
    TopologyPole,
    TopologyQuality,
    TopologyQualityTier,
    TopologyRequest,
)

EARTH_RADIUS_M = 6_371_000.0
INFERENCE_VERSION = "geo-mst-v1"
DEFAULT_MAX_CANDIDATE_DISTANCE_M = 120.0
DEFAULT_MAX_NEIGHBORS = 6
ROOT_NODE_ID = "__DT_ROOT__"


class InvalidTopologyCoordinatesError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _CandidateEdge:
    left_id: str
    right_id: str
    distance_m: float
    confidence: float


class _DisjointSet:
    def __init__(self, node_ids: tuple[str, ...]) -> None:
        self._parent = {node_id: node_id for node_id in node_ids}

    def find(self, node_id: str) -> str:
        parent = self._parent[node_id]
        if parent != node_id:
            self._parent[node_id] = self.find(parent)
        return self._parent[node_id]

    def union(self, left_id: str, right_id: str) -> bool:
        left_root = self.find(left_id)
        right_root = self.find(right_id)
        if left_root == right_root:
            return False
        lower, upper = sorted((left_root, right_root))
        self._parent[upper] = lower
        return True


def haversine_distance_m(
    left_latitude: float,
    left_longitude: float,
    right_latitude: float,
    right_longitude: float,
) -> float:
    _validate_coordinates(left_latitude, left_longitude)
    _validate_coordinates(right_latitude, right_longitude)
    latitude_delta = radians(right_latitude - left_latitude)
    longitude_delta = radians(right_longitude - left_longitude)
    left_latitude_radians = radians(left_latitude)
    right_latitude_radians = radians(right_latitude)
    haversine = sin(latitude_delta / 2) ** 2 + (
        cos(left_latitude_radians) * cos(right_latitude_radians) * sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * asin(sqrt(haversine))


def infer_geographic_topology(
    request: TopologyRequest,
    *,
    max_candidate_distance_m: float = DEFAULT_MAX_CANDIDATE_DISTANCE_M,
    max_neighbors: int = DEFAULT_MAX_NEIGHBORS,
) -> RootedTopology:
    if not request.poles:
        return _unusable(request, "transformer has no eligible poles")
    if max_candidate_distance_m <= 0 or max_neighbors < 1:
        raise ValueError("candidate distance and neighbor bound must be positive")
    _validate_coordinates(request.dt_latitude, request.dt_longitude)
    if len({pole.pole_id for pole in request.poles}) != len(request.poles):
        return _unusable(request, "duplicate pole identifiers prevent inference")
    for pole in request.poles:
        _validate_coordinates(pole.latitude, pole.longitude)

    candidates = _bounded_candidate_edges(
        request,
        max_candidate_distance_m=max_candidate_distance_m,
        max_neighbors=max_neighbors,
    )
    node_ids = (ROOT_NODE_ID, *(pole.pole_id for pole in sorted(request.poles, key=_pole_key)))
    disjoint_set = _DisjointSet(node_ids)
    selected: list[_CandidateEdge] = []
    for candidate in candidates:
        if disjoint_set.union(candidate.left_id, candidate.right_id):
            selected.append(candidate)
        if len(selected) == len(node_ids) - 1:
            break
    if len(selected) != len(node_ids) - 1:
        return _unusable(
            request,
            f"geography is disconnected at {max_candidate_distance_m:.0f} metre candidate limit",
        )

    rooted_edges = _orient_from_transformer(request, tuple(selected))
    confidences = tuple(edge.edge_confidence for edge in rooted_edges)
    score = round(0.6 * (sum(confidences) / len(confidences)) + 0.4 * min(confidences), 4)
    limiting_factors = ["surveyed connectivity is unavailable; topology is inferred from geography"]
    if min(confidences) < 0.7:
        limiting_factors.append("one or more long or geographically ambiguous inferred edges")
    tier = (
        TopologyQualityTier.STRONGLY_INFERRED
        if score >= 0.7
        else TopologyQualityTier.WEAKLY_INFERRED
    )
    return RootedTopology(
        dt_id=request.dt_id,
        topology_version=max(1, request.topology_version),
        source=TopologySource.INFERRED,
        edges=rooted_edges,
        quality=TopologyQuality(score, tier, tuple(limiting_factors)),
        inference_version=INFERENCE_VERSION,
    )


def _bounded_candidate_edges(
    request: TopologyRequest,
    *,
    max_candidate_distance_m: float,
    max_neighbors: int,
) -> tuple[_CandidateEdge, ...]:
    nodes = {
        ROOT_NODE_ID: TopologyPole(
            ROOT_NODE_ID,
            request.dt_latitude,
            request.dt_longitude,
        ),
        **{pole.pole_id: pole for pole in request.poles},
    }
    latitude_cell = max_candidate_distance_m / 111_320.0
    longitude_scale = max(0.1, cos(radians(request.dt_latitude)))
    longitude_cell = max_candidate_distance_m / (111_320.0 * longitude_scale)
    buckets: defaultdict[tuple[int, int], list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        buckets[_cell(node, latitude_cell, longitude_cell)].append(node_id)

    selected_pairs: dict[tuple[str, str], float] = {}
    neighbor_distances: defaultdict[str, list[float]] = defaultdict(list)
    for node_id in sorted(nodes):
        node = nodes[node_id]
        latitude_index, longitude_index = _cell(node, latitude_cell, longitude_cell)
        nearby: list[tuple[float, str]] = []
        for latitude_offset in (-1, 0, 1):
            for longitude_offset in (-1, 0, 1):
                for other_id in buckets.get(
                    (latitude_index + latitude_offset, longitude_index + longitude_offset), ()
                ):
                    if other_id == node_id:
                        continue
                    other = nodes[other_id]
                    distance = haversine_distance_m(
                        node.latitude,
                        node.longitude,
                        other.latitude,
                        other.longitude,
                    )
                    if distance <= max_candidate_distance_m:
                        nearby.append((distance, other_id))
        for distance, other_id in sorted(nearby)[:max_neighbors]:
            pair = tuple(sorted((node_id, other_id)))
            selected_pairs[pair] = min(distance, selected_pairs.get(pair, distance))
            neighbor_distances[node_id].append(distance)

    candidates: list[_CandidateEdge] = []
    for (left_id, right_id), distance in selected_pairs.items():
        ambiguity_penalty = max(
            _ambiguity_penalty(distance, neighbor_distances[left_id]),
            _ambiguity_penalty(distance, neighbor_distances[right_id]),
        )
        distance_ratio = distance / max_candidate_distance_m
        confidence = round(max(0.05, 1 - 0.55 * distance_ratio - ambiguity_penalty), 4)
        candidates.append(_CandidateEdge(left_id, right_id, distance, confidence))
    return tuple(
        sorted(candidates, key=lambda edge: (edge.distance_m, edge.left_id, edge.right_id))
    )


def _ambiguity_penalty(distance: float, distances: list[float]) -> float:
    alternatives = sorted(distances)
    try:
        alternatives.remove(distance)
    except ValueError:
        pass
    if not alternatives or distance == 0:
        return 0
    nearest_ratio = alternatives[0] / distance
    return 0.15 if 0.85 <= nearest_ratio <= 1.15 else 0


def _orient_from_transformer(
    request: TopologyRequest,
    selected: tuple[_CandidateEdge, ...],
) -> tuple[RecordedTopologyEdge, ...]:
    adjacency: defaultdict[str, list[tuple[str, _CandidateEdge]]] = defaultdict(list)
    for edge in selected:
        adjacency[edge.left_id].append((edge.right_id, edge))
        adjacency[edge.right_id].append((edge.left_id, edge))
    pending = deque([(ROOT_NODE_ID, None)])
    visited: set[str] = set()
    rooted: list[RecordedTopologyEdge] = []
    while pending:
        node_id, parent_id = pending.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        for child_id, edge in sorted(adjacency[node_id], key=lambda item: item[0]):
            if child_id == parent_id or child_id in visited:
                continue
            rooted.append(
                RecordedTopologyEdge(
                    parent_pole_id=None if node_id == ROOT_NODE_ID else node_id,
                    child_pole_id=child_id,
                    source=TopologySource.INFERRED,
                    distance_m=round(edge.distance_m, 3),
                    edge_confidence=edge.confidence,
                    inference_version=INFERENCE_VERSION,
                )
            )
            pending.append((child_id, node_id))
    return tuple(sorted(rooted, key=lambda edge: (edge.parent_pole_id or "", edge.child_pole_id)))


def _unusable(request: TopologyRequest, reason: str) -> RootedTopology:
    return RootedTopology(
        dt_id=request.dt_id,
        topology_version=max(1, request.topology_version),
        source=None,
        edges=(),
        quality=TopologyQuality(0, TopologyQualityTier.UNUSABLE, (reason,)),
        inference_version=INFERENCE_VERSION,
    )


def _cell(pole: TopologyPole, latitude_cell: float, longitude_cell: float) -> tuple[int, int]:
    return floor(pole.latitude / latitude_cell), floor(pole.longitude / longitude_cell)


def _pole_key(pole: TopologyPole) -> str:
    return pole.pole_id


def _validate_coordinates(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise InvalidTopologyCoordinatesError("topology coordinates are outside valid bounds")
