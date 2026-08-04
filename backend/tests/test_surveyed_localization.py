from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from propel.analysis.localization import InvalidTopologySnapshotError, localize_known_topology
from propel.analysis.models import (
    DeviceEvidence,
    NetworkSnapshot,
    PoleEvidence,
    ScheduledOutageWindow,
    TopologySpan,
)
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    ScheduledOutageScope,
    SuspectedAssetType,
    TopologySource,
)

ANALYSIS_AT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
ONSET_AT = ANALYSIS_AT - timedelta(seconds=10)


def pole(
    pole_number: int,
    state: PoleStatus,
    observed_at: datetime | None,
) -> PoleEvidence:
    return PoleEvidence(
        pole_id=f"P-{pole_number:03d}",
        latitude=12.889 + pole_number / 10_000,
        longitude=77.584 + pole_number / 10_000,
        pin_code="560078",
        state=state,
        state_received_at=observed_at,
        device=DeviceEvidence(
            device_id=f"DEV-P-{pole_number:03d}",
            status=DeviceHealthStatus.HEALTHY,
            last_seen_at=observed_at,
            can_report_power_loss=True,
            firmware="1.4.2",
            battery_mv=3480,
            rssi=-91,
        ),
    )


def linear_snapshot(
    states: tuple[
        tuple[PoleStatus, datetime | None],
        tuple[PoleStatus, datetime | None],
        tuple[PoleStatus, datetime | None],
        tuple[PoleStatus, datetime | None],
    ],
    *,
    reverse: bool = False,
    scheduled_outages: tuple[ScheduledOutageWindow, ...] = (),
) -> NetworkSnapshot:
    poles = tuple(pole(index, *state) for index, state in enumerate(states, start=1))
    spans = (
        TopologySpan(None, "P-001", TopologySource.SURVEYED, 1.0),
        TopologySpan("P-001", "P-002", TopologySource.SURVEYED, 1.0),
        TopologySpan("P-002", "P-003", TopologySource.SURVEYED, 1.0),
        TopologySpan("P-003", "P-004", TopologySource.SURVEYED, 1.0),
    )
    return NetworkSnapshot(
        dt_id="DT-001",
        feeder_id="FDR-001",
        topology_version=1,
        analysis_at=ANALYSIS_AT,
        poles=tuple(reversed(poles)) if reverse else poles,
        spans=tuple(reversed(spans)) if reverse else spans,
        scheduled_outages=scheduled_outages,
    )


def fixed_fault_snapshot(*, reverse: bool = False) -> NetworkSnapshot:
    return linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=20)),
            (PoleStatus.DARK, ONSET_AT),
            (PoleStatus.DARK, ONSET_AT + timedelta(seconds=1)),
            (PoleStatus.DARK, ONSET_AT + timedelta(seconds=2)),
        ),
        reverse=reverse,
    )


def test_live_to_dark_tree_returns_one_exact_surveyed_span() -> None:
    candidates = localize_known_topology(fixed_fault_snapshot())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.classification == FaultClass.SPAN_FAULT
    assert candidate.suspected_asset_type == SuspectedAssetType.SPAN
    assert candidate.suspected_asset_id == "P-001->P-002"
    assert candidate.parent_pole_id == "P-001"
    assert candidate.child_pole_id == "P-002"
    assert candidate.affected_pole_ids == ("P-002", "P-003", "P-004")
    assert candidate.evidence.subtree_pole_ids == ("P-002", "P-003", "P-004")
    assert candidate.evidence.dark_pole_count == 3
    assert candidate.evidence.observable_pole_count == 3
    assert candidate.precision == LocalizationPrecision.EXACT_SPAN
    assert candidate.topology_source == TopologySource.SURVEYED
    assert candidate.pin_code == "560078"
    assert candidate.latitude == pytest.approx((12.8891 + 12.8892) / 2)
    assert candidate.longitude == pytest.approx((77.5841 + 77.5842) / 2)
    assert candidate.confidence_score == 100
    assert candidate.confidence_level == "HIGH"


def test_dark_to_dark_edges_do_not_create_downstream_root_candidates() -> None:
    candidates = localize_known_topology(fixed_fault_snapshot())

    assert [candidate.suspected_asset_id for candidate in candidates] == ["P-001->P-002"]


def test_loss_event_order_does_not_change_final_candidate() -> None:
    assert localize_known_topology(fixed_fault_snapshot()) == localize_known_topology(
        fixed_fault_snapshot(reverse=True)
    )


def test_post_onset_live_descendant_is_a_contradiction() -> None:
    snapshot = linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=20)),
            (PoleStatus.DARK, ONSET_AT),
            (PoleStatus.LIVE, ONSET_AT + timedelta(seconds=1)),
            (PoleStatus.UNKNOWN, None),
        )
    )

    candidate = localize_known_topology(snapshot)[0]

    assert candidate.classification == FaultClass.SENSOR_ANOMALY
    assert candidate.suspected_asset_type == SuspectedAssetType.DEVICE
    assert candidate.suspected_asset_id == "DEV-P-002"
    assert candidate.precision == LocalizationPrecision.POLE_LEVEL
    assert candidate.suppression is not None
    assert candidate.suppression.source == "telemetry-consistency-rule"
    assert candidate.evidence.post_onset_live_contradictions == ("P-003",)
    assert candidate.evidence.pre_onset_live_observations == ()
    assert candidate.evidence.components.contradiction_penalty == -20
    assert candidate.confidence_score < 100
    assert candidate.evidence.negative_reasons == (
        "post-onset LIVE contradictions below boundary: P-003",
    )


def test_pre_onset_live_descendant_is_prior_evidence_not_a_contradiction() -> None:
    snapshot = linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=20)),
            (PoleStatus.DARK, ONSET_AT),
            (PoleStatus.LIVE, ONSET_AT - timedelta(seconds=1)),
            (PoleStatus.UNKNOWN, None),
        )
    )

    candidate = localize_known_topology(snapshot)[0]

    assert candidate.classification == FaultClass.SPAN_FAULT
    assert candidate.evidence.post_onset_live_contradictions == ()
    assert candidate.evidence.pre_onset_live_observations == ("P-003",)
    assert any("prior-state evidence" in reason for reason in candidate.evidence.positive_reasons)


def test_stale_silence_state_does_not_create_anomaly_or_fault_candidate() -> None:
    snapshot = linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=20)),
            (PoleStatus.STALE, ANALYSIS_AT - timedelta(minutes=33)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=2)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=1)),
        )
    )

    assert localize_known_topology(snapshot) == []


def test_terminal_pole_loss_is_not_treated_as_sensor_anomaly() -> None:
    snapshot = linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=20)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=15)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=12)),
            (PoleStatus.DARK, ONSET_AT),
        )
    )

    candidate = localize_known_topology(snapshot)[0]

    assert candidate.classification == FaultClass.SPAN_FAULT
    assert candidate.suspected_asset_id == "P-003->P-004"
    assert candidate.suppression is None


def scheduled_outage(
    scope: ScheduledOutageScope,
    scope_id: str,
    *,
    starts_at: datetime = ONSET_AT - timedelta(minutes=1),
    ends_at: datetime = ONSET_AT + timedelta(minutes=1),
    outage_id: str = "SO-TEST-001",
) -> ScheduledOutageWindow:
    return ScheduledOutageWindow(
        outage_id=outage_id,
        scope=scope,
        scope_id=scope_id,
        starts_at=starts_at,
        ends_at=ends_at,
        source="test-schedule-feed",
        reason="Planned maintenance",
    )


@pytest.mark.parametrize(
    ("scope", "scope_id"),
    [
        (ScheduledOutageScope.SPAN, "P-001->P-002"),
        (ScheduledOutageScope.DISTRIBUTION_TRANSFORMER, "DT-001"),
        (ScheduledOutageScope.FEEDER, "FDR-001"),
    ],
)
def test_active_schedule_scopes_suppress_matching_span(
    scope: ScheduledOutageScope,
    scope_id: str,
) -> None:
    snapshot = replace(
        fixed_fault_snapshot(),
        scheduled_outages=(scheduled_outage(scope, scope_id),),
    )

    candidate = localize_known_topology(snapshot)[0]

    assert candidate.classification == FaultClass.SCHEDULED_OUTAGE
    assert candidate.suspected_asset_id == "P-001->P-002"
    assert candidate.suppression is not None
    assert candidate.suppression.external_id == "SO-TEST-001"
    assert candidate.suppression.source == "test-schedule-feed"


@pytest.mark.parametrize(
    "outage",
    [
        scheduled_outage(
            ScheduledOutageScope.SPAN,
            "P-001->P-002",
            starts_at=ONSET_AT - timedelta(hours=2),
            ends_at=ONSET_AT - timedelta(hours=1),
        ),
        scheduled_outage(
            ScheduledOutageScope.SPAN,
            "P-001->P-002",
            starts_at=ONSET_AT + timedelta(hours=1),
            ends_at=ONSET_AT + timedelta(hours=2),
        ),
        scheduled_outage(ScheduledOutageScope.SPAN, "P-003->P-004"),
        scheduled_outage(ScheduledOutageScope.DISTRIBUTION_TRANSFORMER, "DT-999"),
        scheduled_outage(ScheduledOutageScope.FEEDER, "FDR-999"),
    ],
)
def test_expired_future_or_nonoverlapping_schedule_does_not_suppress(
    outage: ScheduledOutageWindow,
) -> None:
    snapshot = replace(fixed_fault_snapshot(), scheduled_outages=(outage,))

    candidate = localize_known_topology(snapshot)[0]

    assert candidate.classification == FaultClass.SPAN_FAULT
    assert candidate.suppression is None


def test_scheduled_suppression_is_deterministic_when_schedule_order_changes() -> None:
    broad = scheduled_outage(
        ScheduledOutageScope.FEEDER,
        "FDR-001",
        outage_id="SO-BROAD",
    )
    exact = scheduled_outage(
        ScheduledOutageScope.SPAN,
        "P-001->P-002",
        outage_id="SO-EXACT",
    )
    first = localize_known_topology(
        replace(fixed_fault_snapshot(), scheduled_outages=(broad, exact))
    )[0]
    second = localize_known_topology(
        replace(fixed_fault_snapshot(), scheduled_outages=(exact, broad))
    )[0]

    assert first == second
    assert first.suppression is not None
    assert first.suppression.external_id == "SO-EXACT"


def test_no_dark_poles_returns_no_candidate() -> None:
    snapshot = linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=4)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=3)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=2)),
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(seconds=1)),
        )
    )

    assert localize_known_topology(snapshot) == []


def test_stale_live_parent_cannot_establish_an_exact_boundary() -> None:
    snapshot = linear_snapshot(
        (
            (PoleStatus.LIVE, ANALYSIS_AT - timedelta(minutes=33)),
            (PoleStatus.DARK, ONSET_AT),
            (PoleStatus.DARK, ONSET_AT + timedelta(seconds=1)),
            (PoleStatus.DARK, ONSET_AT + timedelta(seconds=2)),
        )
    )

    assert localize_known_topology(snapshot) == []


def test_invalid_surveyed_cycle_is_rejected() -> None:
    snapshot = NetworkSnapshot(
        dt_id="DT-001",
        feeder_id="FDR-001",
        topology_version=1,
        analysis_at=ANALYSIS_AT,
        poles=(
            pole(1, PoleStatus.LIVE, ANALYSIS_AT),
            pole(2, PoleStatus.DARK, ONSET_AT),
        ),
        spans=(
            TopologySpan("P-002", "P-001", TopologySource.SURVEYED, 1.0),
            TopologySpan("P-001", "P-002", TopologySource.SURVEYED, 1.0),
        ),
    )

    with pytest.raises(InvalidTopologySnapshotError, match="cycle"):
        localize_known_topology(snapshot)
