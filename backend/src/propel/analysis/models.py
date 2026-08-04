from dataclasses import dataclass
from datetime import datetime

from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    ScheduledOutageScope,
    SuspectedAssetType,
    TopologySource,
)


@dataclass(frozen=True, slots=True)
class DeviceEvidence:
    device_id: str
    status: DeviceHealthStatus
    last_seen_at: datetime | None
    can_report_power_loss: bool
    firmware: str | None
    battery_mv: int | None
    rssi: int | None


@dataclass(frozen=True, slots=True)
class PoleEvidence:
    pole_id: str
    latitude: float
    longitude: float
    pin_code: str | None
    state: PoleStatus
    state_received_at: datetime | None
    device: DeviceEvidence | None = None


@dataclass(frozen=True, slots=True)
class TopologySpan:
    parent_pole_id: str | None
    child_pole_id: str
    source: TopologySource
    edge_confidence: float


@dataclass(frozen=True, slots=True)
class ScheduledOutageWindow:
    outage_id: str
    scope: ScheduledOutageScope
    scope_id: str
    starts_at: datetime
    ends_at: datetime
    source: str
    reason: str

    def __post_init__(self) -> None:
        if self.starts_at.utcoffset() is None or self.ends_at.utcoffset() is None:
            raise ValueError("scheduled outage timestamps must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("scheduled outage end must be after its start")
        if not self.outage_id or not self.scope_id or not self.source or not self.reason:
            raise ValueError("scheduled outage identifiers, source, and reason are required")


@dataclass(frozen=True, slots=True)
class CandidateSuppression:
    reason: str
    source: str
    external_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "reason": self.reason,
            "source": self.source,
            "external_id": self.external_id,
        }


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    dt_id: str
    feeder_id: str
    topology_version: int
    analysis_at: datetime
    poles: tuple[PoleEvidence, ...]
    spans: tuple[TopologySpan, ...]
    scheduled_outages: tuple[ScheduledOutageWindow, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfidenceComponents:
    topology: int
    boundary_clarity: int
    downstream_corroboration: int
    temporal_coherence: int
    sensor_quality: int
    contradiction_penalty: int

    def as_dict(self) -> dict[str, int]:
        return {
            "topology": self.topology,
            "boundary_clarity": self.boundary_clarity,
            "downstream_corroboration": self.downstream_corroboration,
            "temporal_coherence": self.temporal_coherence,
            "sensor_quality": self.sensor_quality,
            "contradiction_penalty": self.contradiction_penalty,
        }


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    onset_at: datetime
    subtree_pole_ids: tuple[str, ...]
    observable_pole_count: int
    dark_pole_count: int
    post_onset_live_contradictions: tuple[str, ...]
    pre_onset_live_observations: tuple[str, ...]
    dark_observation_spread_seconds: float | None
    positive_reasons: tuple[str, ...]
    negative_reasons: tuple[str, ...]
    components: ConfidenceComponents

    def as_dict(self) -> dict[str, object]:
        return {
            "onset_at": self.onset_at.isoformat(),
            "subtree_pole_ids": list(self.subtree_pole_ids),
            "observable_pole_count": self.observable_pole_count,
            "dark_pole_count": self.dark_pole_count,
            "post_onset_live_contradictions": list(self.post_onset_live_contradictions),
            "pre_onset_live_observations": list(self.pre_onset_live_observations),
            "dark_observation_spread_seconds": self.dark_observation_spread_seconds,
            "positive_reasons": list(self.positive_reasons),
            "negative_reasons": list(self.negative_reasons),
            "components": self.components.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class FaultCandidate:
    dt_id: str
    topology_version: int
    analysis_at: datetime
    classification: FaultClass
    suspected_asset_type: SuspectedAssetType
    suspected_asset_id: str
    parent_pole_id: str
    child_pole_id: str
    affected_pole_ids: tuple[str, ...]
    precision: LocalizationPrecision
    topology_source: TopologySource
    latitude: float
    longitude: float
    pin_code: str | None
    confidence_score: int
    confidence_level: str
    confidence_reason: str
    evidence: CandidateEvidence
    suppression: CandidateSuppression | None = None
