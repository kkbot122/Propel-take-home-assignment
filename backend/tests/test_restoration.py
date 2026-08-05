from datetime import UTC, datetime, timedelta

from propel.domain.enums import PoleStatus
from propel.incidents.restoration import (
    REPAIR_NOT_VERIFIED,
    RESTORATION_VERIFIED,
    RestorationPoleEvidence,
    required_span_restoration_pole_id,
    restoration_decision,
)


def evidence(
    pole_id: str,
    state: PoleStatus,
    received_at: datetime,
    *,
    boundary: bool = False,
    eligible: bool = True,
    device_timestamp: datetime | None = None,
) -> RestorationPoleEvidence:
    return RestorationPoleEvidence(
        pole_id=pole_id,
        eligible=eligible,
        is_boundary_child=boundary,
        state=state,
        received_at=received_at,
        device_timestamp=device_timestamp or received_at,
        exclusion_reason=None if eligible else "NO_DEVICE",
    )


def test_corridor_restoration_uses_persisted_downstream_bound() -> None:
    incident_evidence = {
        "candidate": {
            "corridor": {
                "upstream_pole_id": "P-001",
                "downstream_pole_id": "P-003",
                "ordered_pole_ids": ["P-001", "P-002", "P-003"],
                "skipped_pole_ids": ["P-002"],
            }
        }
    }

    assert required_span_restoration_pole_id("P-001..P-003", incident_evidence) == "P-003"
    assert required_span_restoration_pole_id("P-001->P-002", {}) == "P-002"


def test_dark_boundary_keeps_repair_unverified() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    decision = restoration_decision(
        (
            evidence("P-002", PoleStatus.DARK, claimed_at - timedelta(seconds=1), boundary=True),
            evidence("P-003", PoleStatus.LIVE, claimed_at + timedelta(seconds=1)),
            evidence("P-004", PoleStatus.LIVE, claimed_at + timedelta(seconds=1)),
        ),
        repair_claimed_at=claimed_at,
        evaluated_at=claimed_at + timedelta(seconds=20),
        threshold=0.8,
        stabilization_seconds=10,
    )

    assert decision.verified is False
    assert decision.reason == REPAIR_NOT_VERIFIED
    assert decision.remaining_dark_count == 1


def test_old_live_evidence_cannot_verify_current_repair_claim() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    stale_live_at = claimed_at - timedelta(milliseconds=1)
    decision = restoration_decision(
        (
            evidence("P-002", PoleStatus.LIVE, stale_live_at, boundary=True),
            evidence("P-003", PoleStatus.LIVE, stale_live_at),
            evidence("P-004", PoleStatus.LIVE, stale_live_at),
        ),
        repair_claimed_at=claimed_at,
        evaluated_at=claimed_at + timedelta(seconds=20),
        threshold=0.8,
        stabilization_seconds=10,
    )

    assert decision.verified is False
    assert decision.live_count == 0
    assert decision.remaining_dark_count == 3


def test_delayed_pre_claim_device_event_cannot_verify_restoration() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    received_at = claimed_at + timedelta(seconds=1)
    delayed_device_time = claimed_at - timedelta(seconds=30)
    decision = restoration_decision(
        (
            evidence(
                "P-002",
                PoleStatus.LIVE,
                received_at,
                boundary=True,
                device_timestamp=delayed_device_time,
            ),
        ),
        repair_claimed_at=claimed_at,
        evaluated_at=received_at + timedelta(seconds=10),
        threshold=0.8,
        stabilization_seconds=10,
    )

    assert decision.verified is False
    assert decision.live_count == 0


def test_threshold_excludes_no_device_poles_and_requires_stabilization() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    restored_at = claimed_at + timedelta(seconds=2)
    poles = (
        evidence("P-002", PoleStatus.LIVE, restored_at, boundary=True),
        evidence("P-003", PoleStatus.LIVE, restored_at),
        evidence("P-004", PoleStatus.NO_DEVICE, restored_at, eligible=False),
    )

    stabilizing = restoration_decision(
        poles,
        repair_claimed_at=claimed_at,
        evaluated_at=restored_at + timedelta(seconds=9),
        threshold=0.8,
        stabilization_seconds=10,
    )
    verified = restoration_decision(
        poles,
        repair_claimed_at=claimed_at,
        evaluated_at=restored_at + timedelta(seconds=10),
        threshold=0.8,
        stabilization_seconds=10,
    )

    assert stabilizing.verified is False
    assert stabilizing.reason == "RESTORATION_STABILIZING"
    assert verified.verified is True
    assert verified.reason == RESTORATION_VERIFIED
    assert verified.eligible_count == 2
    assert verified.remaining_dark_count == 0


def test_eighty_percent_threshold_rounds_up() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    restored_at = claimed_at + timedelta(seconds=1)
    decision = restoration_decision(
        (
            evidence("P-002", PoleStatus.LIVE, restored_at, boundary=True),
            evidence("P-003", PoleStatus.LIVE, restored_at),
            evidence("P-004", PoleStatus.DARK, restored_at),
        ),
        repair_claimed_at=claimed_at,
        evaluated_at=restored_at + timedelta(seconds=10),
        threshold=0.8,
        stabilization_seconds=10,
    )

    assert decision.verified is False
    assert decision.live_count == 2
    assert decision.remaining_dark_count == 1


def test_transformer_restoration_can_verify_without_boundary_anchor() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    restored_at = claimed_at + timedelta(seconds=1)

    decision = restoration_decision(
        (evidence("P-002", PoleStatus.LIVE, restored_at),),
        repair_claimed_at=claimed_at,
        evaluated_at=restored_at + timedelta(seconds=10),
        threshold=0.8,
        stabilization_seconds=10,
        require_anchor=False,
    )

    assert decision.verified is True
    assert decision.reason == RESTORATION_VERIFIED
    assert decision.remaining_dark_count == 0


def test_span_restoration_still_requires_boundary_anchor() -> None:
    claimed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    restored_at = claimed_at + timedelta(seconds=1)

    decision = restoration_decision(
        (evidence("P-002", PoleStatus.LIVE, restored_at),),
        repair_claimed_at=claimed_at,
        evaluated_at=restored_at + timedelta(seconds=10),
        threshold=0.8,
        stabilization_seconds=10,
    )

    assert decision.verified is False
    assert decision.reason == REPAIR_NOT_VERIFIED
