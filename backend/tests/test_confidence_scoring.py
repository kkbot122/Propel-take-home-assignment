import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from propel.analysis.confidence import EvidenceScoreInput, score_evidence
from propel.analysis.models import DeviceEvidence, PoleEvidence
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    TopologySource,
)

ANALYSIS_AT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
FRESHNESS = timedelta(minutes=32)
CALIBRATION_PATH = Path(__file__).parents[2] / "docs" / "PB06-CALIBRATION.json"


def pole(
    pole_id: str,
    *,
    state: PoleStatus = PoleStatus.DARK,
    status: DeviceHealthStatus = DeviceHealthStatus.HEALTHY,
    firmware: str | None = "1.4.2",
    battery_mv: int | None = 3_480,
    rssi: int | None = -91,
) -> PoleEvidence:
    observed_at = ANALYSIS_AT - timedelta(seconds=5)
    return PoleEvidence(
        pole_id=pole_id,
        latitude=12.889,
        longitude=77.584,
        pin_code="560078",
        state=state,
        state_received_at=observed_at,
        device=DeviceEvidence(
            device_id=f"DEV-{pole_id}",
            status=status,
            last_seen_at=observed_at,
            can_report_power_loss=True,
            firmware=firmware,
            battery_mv=battery_mv,
            rssi=rssi,
        ),
    )


def score_input(**changes: object) -> EvidenceScoreInput:
    values: dict[str, object] = {
        "classification": FaultClass.SPAN_FAULT,
        "precision": LocalizationPrecision.EXACT_SPAN,
        "topology_source": TopologySource.SURVEYED,
        "topology_quality_score": 1.0,
        "boundary_evidence": 30,
        "corroborating_count": 2,
        "eligible_count": 2,
        "temporal_spread_seconds": 2.0,
        "evidence_poles": (pole("P-001"), pole("P-002")),
        "analysis_at": ANALYSIS_AT,
        "freshness": FRESHNESS,
    }
    values.update(changes)
    return EvidenceScoreInput(**values)  # type: ignore[arg-type]


def test_surveyed_exact_scores_above_equivalent_inferred_evidence() -> None:
    surveyed = score_evidence(score_input())
    inferred = score_evidence(
        score_input(
            precision=LocalizationPrecision.PROBABLE_SPAN,
            topology_source=TopologySource.INFERRED,
            topology_quality_score=0.84,
            boundary_evidence=24,
        )
    )

    assert surveyed.score == 100
    assert inferred.raw_score > 79
    assert inferred.score == 79
    assert surveyed.score > inferred.score
    assert inferred.caps[0].name == "probable-span"


def test_recorded_calibration_retains_versioned_raw_results() -> None:
    calibration = json.loads(CALIBRATION_PATH.read_text())

    assert calibration["policy_version"] == "evidence-score-v1"
    assert calibration["fixtures"]["surveyed_exact_span"]["raw_score"] == 100
    assert calibration["fixtures"]["inferred_probable_span"]["raw_score"] == 88
    assert calibration["fixtures"]["unbounded_dt_level"]["score_cap"] == 49


def test_precision_cap_cannot_be_bypassed_by_maximum_corroboration() -> None:
    result = score_evidence(
        score_input(
            precision=LocalizationPrecision.CORRIDOR,
            boundary_evidence=30,
        )
    )

    assert result.raw_score == 100
    assert result.score == result.score_cap == 79


def test_each_post_onset_contradiction_applies_twenty_points_up_to_forty() -> None:
    no_contradiction = score_evidence(score_input())
    one_contradiction = score_evidence(score_input(contradiction_count=1))
    many_contradictions = score_evidence(score_input(contradiction_count=7))

    assert one_contradiction.components.contradiction_penalty == -20
    assert one_contradiction.raw_score == no_contradiction.raw_score - 20
    assert many_contradictions.components.contradiction_penalty == -40


def test_missing_device_reduces_evidence_without_increasing_dark_votes() -> None:
    healthy = pole("P-001")
    missing = replace(
        pole("P-002"),
        state=PoleStatus.NO_DEVICE,
        state_received_at=None,
        device=None,
    )
    complete = score_evidence(
        score_input(
            corroborating_count=1,
            eligible_count=1,
            evidence_poles=(healthy,),
        )
    )
    incomplete = score_evidence(
        score_input(
            corroborating_count=1,
            eligible_count=1,
            evidence_poles=(healthy, missing),
        )
    )

    assert incomplete.components.downstream_corroboration == 25
    assert incomplete.components.missing_evidence_penalty == -5
    assert incomplete.components.sensor_quality < complete.components.sensor_quality
    assert incomplete.score < complete.score


def test_sensor_quality_uses_firmware_battery_and_signal_evidence() -> None:
    healthy = score_evidence(score_input())
    weak_poles = (
        pole("P-001", firmware="1.2.9", battery_mv=2_400, rssi=-140),
        pole("P-002", firmware="1.2.9", battery_mv=2_400, rssi=-140),
    )
    weak = score_evidence(score_input(evidence_poles=weak_poles))

    assert healthy.components.sensor_quality == 10
    assert weak.components.sensor_quality == 6
    assert weak.score < healthy.score


def test_more_eligible_dark_corroboration_scores_higher() -> None:
    eight_dark = score_evidence(score_input(corroborating_count=8, eligible_count=10))
    three_dark = score_evidence(score_input(corroborating_count=3, eligible_count=10))

    assert eight_dark.components.downstream_corroboration == 20
    assert three_dark.components.downstream_corroboration == 8
    assert eight_dark.score > three_dark.score


def test_tighter_loss_cluster_scores_higher() -> None:
    twenty_seconds = score_evidence(score_input(temporal_spread_seconds=20))
    ten_minutes = score_evidence(score_input(temporal_spread_seconds=600))

    assert twenty_seconds.components.temporal_coherence == 5
    assert ten_minutes.components.temporal_coherence == 0
    assert twenty_seconds.score > ten_minutes.score


def test_reordering_identical_pole_evidence_is_score_stable() -> None:
    poles = (pole("P-003"), pole("P-001"), pole("P-002"))

    first = score_evidence(
        score_input(corroborating_count=3, eligible_count=3, evidence_poles=poles)
    )
    second = score_evidence(
        score_input(
            corroborating_count=3,
            eligible_count=3,
            evidence_poles=tuple(reversed(poles)),
        )
    )

    assert first == second


@pytest.mark.parametrize("contradiction_count", [0, 1, 2, 10_000])
def test_scores_remain_within_documented_bounds(contradiction_count: int) -> None:
    result = score_evidence(score_input(contradiction_count=contradiction_count))

    assert 0 <= result.score <= 100
    assert result.level in {"LOW", "MEDIUM", "HIGH"}
