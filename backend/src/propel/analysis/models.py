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
    distance_m: float = 0.0
    inference_version: str | None = None


@dataclass(frozen=True, slots=True)
class FeederTransformerEvidence:
    dt_id: str
    latitude: float
    longitude: float
    pin_code: str | None
    topology_version: int
    poles: tuple[PoleEvidence, ...]
    spans: tuple[TopologySpan, ...]


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
    dt_latitude: float = 0.0
    dt_longitude: float = 0.0
    dt_pin_code: str | None = None
    scheduled_outages: tuple[ScheduledOutageWindow, ...] = ()
    feeder_transformers: tuple[FeederTransformerEvidence, ...] = ()
    topology_quality_score: float = 1.0
    topology_quality_tier: str = "SURVEYED"
    topology_quality_reasons: tuple[str, ...] = ()
    inference_version: str | None = None


@dataclass(frozen=True, slots=True)
class ConfidenceComponents:
    topology_provenance: int
    boundary_evidence: int
    downstream_corroboration: int
    temporal_coherence: int
    sensor_quality: int
    contradiction_penalty: int
    missing_evidence_penalty: int

    @property
    def topology(self) -> int:
        """Compatibility alias for incidents created before PB-06."""
        return self.topology_provenance

    @property
    def boundary_clarity(self) -> int:
        """Compatibility alias for incidents created before PB-06."""
        return self.boundary_evidence

    def as_dict(self) -> dict[str, int]:
        return {
            "topology_provenance": self.topology_provenance,
            "boundary_evidence": self.boundary_evidence,
            "downstream_corroboration": self.downstream_corroboration,
            "temporal_coherence": self.temporal_coherence,
            "sensor_quality": self.sensor_quality,
        }

    def penalties_as_dict(self) -> dict[str, int]:
        return {
            "post_onset_live_contradictions": self.contradiction_penalty,
            "missing_or_unhealthy_evidence": self.missing_evidence_penalty,
        }


@dataclass(frozen=True, slots=True)
class ConfidenceCap:
    name: str
    maximum: int
    reason: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "maximum": self.maximum,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LocalizationCorridor:
    upstream_pole_id: str
    downstream_pole_id: str
    ordered_pole_ids: tuple[str, ...]
    skipped_pole_ids: tuple[str, ...]
    ambiguous_pole_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.ordered_pole_ids) < 3 or not (self.skipped_pole_ids or self.ambiguous_pole_ids):
            raise ValueError("a corridor requires two bounds and an uncertain interior pole")
        if self.ordered_pole_ids[0] != self.upstream_pole_id:
            raise ValueError("corridor must start at its upstream bound")
        if self.ordered_pole_ids[-1] != self.downstream_pole_id:
            raise ValueError("corridor must end at its downstream bound")
        interior = self.ordered_pole_ids[1:-1]
        if interior != self.skipped_pole_ids and interior != self.ambiguous_pole_ids:
            raise ValueError("corridor uncertainty must be ordered between its bounds")

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "upstream_pole_id": self.upstream_pole_id,
            "downstream_pole_id": self.downstream_pole_id,
            "ordered_pole_ids": list(self.ordered_pole_ids),
            "skipped_pole_ids": list(self.skipped_pole_ids),
        }
        if self.ambiguous_pole_ids:
            result["ambiguous_pole_ids"] = list(self.ambiguous_pole_ids)
        return result


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
    unusable_pole_ids: tuple[str, ...] = ()
    corridor: LocalizationCorridor | None = None
    topology_quality_score: float = 1.0
    topology_quality_tier: str = "SURVEYED"
    topology_quality_reasons: tuple[str, ...] = ()
    score_kind: str = "EVIDENCE_SCORE"
    score_interpretation: str = "Deterministic evidence score; not a probability."
    score_policy_version: str = "evidence-score-v1"
    raw_score: int = 0
    score_cap: int = 100
    caps: tuple[ConfidenceCap, ...] = ()

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
            "penalties": self.components.penalties_as_dict(),
            "unusable_pole_ids": list(self.unusable_pole_ids),
            "corridor": self.corridor.as_dict() if self.corridor is not None else None,
            "topology_quality_score": self.topology_quality_score,
            "topology_quality_tier": self.topology_quality_tier,
            "topology_quality_reasons": list(self.topology_quality_reasons),
            "score_kind": self.score_kind,
            "score_interpretation": self.score_interpretation,
            "score_policy_version": self.score_policy_version,
            "raw_score": self.raw_score,
            "score_cap": self.score_cap,
            "caps": [cap.as_dict() for cap in self.caps],
        }


@dataclass(frozen=True, slots=True)
class FaultCandidate:
    dt_id: str
    feeder_id: str
    affected_dt_ids: tuple[str, ...]
    topology_version: int
    analysis_at: datetime
    classification: FaultClass
    suspected_asset_type: SuspectedAssetType
    suspected_asset_id: str
    parent_pole_id: str | None
    child_pole_id: str | None
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
