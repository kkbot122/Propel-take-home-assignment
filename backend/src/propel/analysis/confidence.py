from dataclasses import dataclass
from datetime import datetime, timedelta

from propel.analysis.models import ConfidenceCap, ConfidenceComponents, PoleEvidence
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    TopologySource,
)

EVIDENCE_SCORE_POLICY_VERSION = "evidence-score-v1"
MAX_TOPOLOGY_POINTS = 25
MAX_BOUNDARY_POINTS = 30
MAX_CORROBORATION_POINTS = 25
MAX_TEMPORAL_POINTS = 10
MAX_SENSOR_POINTS = 10
MAX_CONTRADICTION_PENALTY = 40
MAX_MISSING_EVIDENCE_PENALTY = 20


@dataclass(frozen=True, slots=True)
class EvidenceScoreInput:
    classification: FaultClass
    precision: LocalizationPrecision
    topology_source: TopologySource
    topology_quality_score: float
    boundary_evidence: int
    corroborating_count: int
    eligible_count: int
    temporal_spread_seconds: float | None
    evidence_poles: tuple[PoleEvidence, ...]
    analysis_at: datetime
    freshness: timedelta
    contradiction_count: int = 0
    additional_missing_count: int = 0


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    score: int
    raw_score: int
    level: str
    components: ConfidenceComponents
    caps: tuple[ConfidenceCap, ...]

    @property
    def score_cap(self) -> int:
        return min((cap.maximum for cap in self.caps), default=100)


def score_evidence(value: EvidenceScoreInput) -> EvidenceScore:
    """Apply the versioned PB-06 evidence policy without performing I/O."""
    _validate_input(value)
    topology = (
        MAX_TOPOLOGY_POINTS
        if value.topology_source == TopologySource.SURVEYED
        else round(MAX_TOPOLOGY_POINTS * value.topology_quality_score)
    )
    corroboration = (
        round(MAX_CORROBORATION_POINTS * value.corroborating_count / value.eligible_count)
        if value.eligible_count
        else 0
    )
    temporal = _temporal_points(value.temporal_spread_seconds)
    sensor_quality = _sensor_quality_points(
        value.evidence_poles,
        analysis_at=value.analysis_at,
        freshness=value.freshness,
    )
    missing_count = (
        sum(
            not _has_usable_evidence(pole, value.analysis_at, value.freshness)
            for pole in value.evidence_poles
        )
        + value.additional_missing_count
    )
    contradiction_penalty = (
        0
        if value.classification == FaultClass.SENSOR_ANOMALY
        else -min(MAX_CONTRADICTION_PENALTY, value.contradiction_count * 20)
    )
    missing_penalty = -min(MAX_MISSING_EVIDENCE_PENALTY, missing_count * 5)
    components = ConfidenceComponents(
        topology_provenance=topology,
        boundary_evidence=value.boundary_evidence,
        downstream_corroboration=corroboration,
        temporal_coherence=temporal,
        sensor_quality=sensor_quality,
        contradiction_penalty=contradiction_penalty,
        missing_evidence_penalty=missing_penalty,
    )
    raw_score = sum(components.as_dict().values()) + sum(components.penalties_as_dict().values())
    caps = _score_caps(value)
    score_cap = min((cap.maximum for cap in caps), default=100)
    score = min(score_cap, max(0, raw_score))
    return EvidenceScore(
        score=score,
        raw_score=raw_score,
        level="HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW",
        components=components,
        caps=caps,
    )


def cap_reasons(score: EvidenceScore) -> tuple[str, ...]:
    return tuple(f"evidence score capped at {cap.maximum}: {cap.reason}" for cap in score.caps)


def penalty_reasons(
    score: EvidenceScore,
    *,
    contradiction_ids: tuple[str, ...] = (),
    unusable_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    reasons: list[str] = []
    if score.components.contradiction_penalty:
        suffix = f": {', '.join(contradiction_ids)}" if contradiction_ids else ""
        reasons.append(
            f"post-onset LIVE contradictions apply "
            f"{score.components.contradiction_penalty} points{suffix}"
        )
    if score.components.missing_evidence_penalty:
        suffix = f": {', '.join(unusable_ids)}" if unusable_ids else ""
        reasons.append(
            f"missing or unhealthy device evidence applies "
            f"{score.components.missing_evidence_penalty} points{suffix}"
        )
    reasons.extend(cap_reasons(score))
    return tuple(reasons)


def _score_caps(value: EvidenceScoreInput) -> tuple[ConfidenceCap, ...]:
    caps: list[ConfidenceCap] = []
    if value.precision == LocalizationPrecision.PROBABLE_SPAN:
        caps.append(
            ConfidenceCap(
                name="probable-span",
                maximum=79,
                reason="inferred topology cannot support a HIGH span evidence score",
            )
        )
    elif value.precision == LocalizationPrecision.CORRIDOR:
        caps.append(
            ConfidenceCap(
                name="corridor",
                maximum=79,
                reason="an uncertain boundary cannot support exact-span confidence",
            )
        )
    if (
        value.precision == LocalizationPrecision.DT_LEVEL
        and value.classification == FaultClass.UNCONFIRMED_OUTAGE
    ):
        caps.append(
            ConfidenceCap(
                name="unbounded-dt-level",
                maximum=49,
                reason="no defensible live-to-dark bound exists",
            )
        )
    if value.classification == FaultClass.UNCONFIRMED_OUTAGE and not caps:
        caps.append(
            ConfidenceCap(
                name="unconfirmed-result",
                maximum=49,
                reason="the evidence does not meet a supported fault-class rule",
            )
        )
    return tuple(caps)


def _temporal_points(spread: float | None) -> int:
    if spread is None:
        return 0
    if spread <= 10:
        return MAX_TEMPORAL_POINTS
    if spread <= 60:
        return 5
    return 0


def _sensor_quality_points(
    poles: tuple[PoleEvidence, ...],
    *,
    analysis_at: datetime,
    freshness: timedelta,
) -> int:
    if not poles:
        return 0
    total = sum(
        _device_quality(pole, analysis_at=analysis_at, freshness=freshness) for pole in poles
    )
    return round(MAX_SENSOR_POINTS * total / len(poles))


def _device_quality(
    pole: PoleEvidence,
    *,
    analysis_at: datetime,
    freshness: timedelta,
) -> float:
    if not _has_usable_evidence(pole, analysis_at, freshness):
        return 0.0
    assert pole.device is not None
    device = pole.device
    quality = 0.6
    if device.firmware is None:
        quality += 0.05
    elif not device.firmware.startswith("1.2"):
        quality += 0.15
    if device.rssi is None:
        quality += 0.05
    elif device.rssi >= -110:
        quality += 0.125
    elif device.rssi >= -125:
        quality += 0.05
    if device.battery_mv is None:
        quality += 0.05
    elif device.battery_mv >= 3_000:
        quality += 0.125
    elif device.battery_mv >= 2_500:
        quality += 0.05
    return min(1.0, quality)


def _has_usable_evidence(
    pole: PoleEvidence,
    analysis_at: datetime,
    freshness: timedelta,
) -> bool:
    device = pole.device
    if (
        device is None
        or device.status != DeviceHealthStatus.HEALTHY
        or not device.can_report_power_loss
        or device.last_seen_at is None
        or pole.state in (PoleStatus.NO_DEVICE, PoleStatus.STALE, PoleStatus.UNKNOWN)
    ):
        return False
    age = analysis_at - device.last_seen_at
    return timedelta(0) <= age <= freshness


def _validate_input(value: EvidenceScoreInput) -> None:
    if not 0 <= value.topology_quality_score <= 1:
        raise ValueError("topology quality score must be between 0 and 1")
    if not 0 <= value.boundary_evidence <= MAX_BOUNDARY_POINTS:
        raise ValueError("boundary evidence must be between 0 and 30")
    if value.corroborating_count < 0 or value.eligible_count < 0:
        raise ValueError("corroboration counts cannot be negative")
    if value.corroborating_count > value.eligible_count:
        raise ValueError("corroborating count cannot exceed eligible count")
    if value.contradiction_count < 0 or value.additional_missing_count < 0:
        raise ValueError("penalty counts cannot be negative")
