from dataclasses import dataclass
from enum import StrEnum

from propel.domain.enums import TopologySource


class TopologyQualityTier(StrEnum):
    SURVEYED = "SURVEYED"
    STRONGLY_INFERRED = "STRONGLY_INFERRED"
    WEAKLY_INFERRED = "WEAKLY_INFERRED"
    UNUSABLE = "UNUSABLE"


@dataclass(frozen=True, slots=True)
class TopologyPole:
    pole_id: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class RecordedTopologyEdge:
    parent_pole_id: str | None
    child_pole_id: str
    source: TopologySource
    distance_m: float
    edge_confidence: float
    inference_version: str | None = None


@dataclass(frozen=True, slots=True)
class TopologyRequest:
    dt_id: str
    dt_latitude: float
    dt_longitude: float
    topology_version: int
    poles: tuple[TopologyPole, ...]
    recorded_edges: tuple[RecordedTopologyEdge, ...] = ()


@dataclass(frozen=True, slots=True)
class TopologyQuality:
    score: float
    tier: TopologyQualityTier
    limiting_factors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1:
            raise ValueError("topology quality score must be between zero and one")


@dataclass(frozen=True, slots=True)
class RootedTopology:
    dt_id: str
    topology_version: int
    source: TopologySource | None
    edges: tuple[RecordedTopologyEdge, ...]
    quality: TopologyQuality
    inference_version: str | None = None
