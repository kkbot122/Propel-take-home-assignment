from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from propel.analysis.localization import localize_known_topology
from propel.analysis.models import (
    CandidateSuppression,
    DeviceEvidence,
    FaultCandidate,
    NetworkSnapshot,
    PoleEvidence,
    TopologySpan,
)
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    SuspectedAssetType,
    TicketStatus,
    TopologySource,
)
from propel.incidents.workflow import (
    AutomaticTransitionOnlyError,
    InvalidTicketTransitionError,
    incident_fingerprint,
    require_operator_transition,
)


def span_candidate() -> FaultCandidate:
    analysis_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    device = DeviceEvidence(
        device_id="DEV-P-001",
        status=DeviceHealthStatus.HEALTHY,
        last_seen_at=analysis_at,
        can_report_power_loss=True,
        firmware="1.4.2",
        battery_mv=3480,
        rssi=-91,
    )
    snapshot = NetworkSnapshot(
        dt_id="DT-001",
        feeder_id="FDR-001",
        topology_version=1,
        analysis_at=analysis_at,
        poles=(
            PoleEvidence(
                "P-001",
                12.88925,
                77.58412,
                "560078",
                PoleStatus.LIVE,
                analysis_at - timedelta(seconds=2),
                device,
            ),
            PoleEvidence(
                "P-002",
                12.88943,
                77.58426,
                "560078",
                PoleStatus.DARK,
                analysis_at - timedelta(seconds=1),
                replace(device, device_id="DEV-P-002"),
            ),
        ),
        spans=(
            TopologySpan(None, "P-001", TopologySource.SURVEYED, 1.0),
            TopologySpan("P-001", "P-002", TopologySource.SURVEYED, 1.0),
        ),
    )
    return localize_known_topology(snapshot)[0]


def test_span_fingerprint_is_stable_for_same_boundary() -> None:
    candidate = span_candidate()

    assert incident_fingerprint(candidate) == "span:DT-001:P-001->P-002"
    assert incident_fingerprint(replace(candidate, confidence_score=72)) == incident_fingerprint(
        candidate
    )


def test_corridor_fingerprint_does_not_claim_an_exact_span() -> None:
    candidate = replace(
        span_candidate(),
        precision=LocalizationPrecision.CORRIDOR,
        suspected_asset_id="P-001..P-003",
        child_pole_id="P-003",
    )

    assert incident_fingerprint(candidate) == "corridor:DT-001:P-001..P-003"


def test_suppressed_candidate_fingerprints_are_stable_domain_keys() -> None:
    candidate = span_candidate()
    sensor = replace(
        candidate,
        classification=FaultClass.SENSOR_ANOMALY,
        suspected_asset_type=SuspectedAssetType.DEVICE,
        suspected_asset_id="DEV-P-002",
        suppression=CandidateSuppression(
            reason="fresh downstream live contradiction",
            source="telemetry-consistency-rule",
        ),
    )
    scheduled = replace(
        candidate,
        classification=FaultClass.SCHEDULED_OUTAGE,
        suppression=CandidateSuppression(
            reason="planned maintenance",
            source="schedule-feed",
            external_id="SO-001",
        ),
    )

    assert incident_fingerprint(sensor) == "sensor:DT-001:DEV-P-002"
    assert incident_fingerprint(scheduled) == "scheduled:SO-001:DT-001:P-001->P-002"


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (TicketStatus.DETECTED, TicketStatus.ACKNOWLEDGED),
        (TicketStatus.ACKNOWLEDGED, TicketStatus.CREW_ASSIGNED),
        (TicketStatus.CREW_ASSIGNED, TicketStatus.RESOLVED),
    ],
)
def test_operator_ticket_transitions_are_explicit(
    current: TicketStatus,
    requested: TicketStatus,
) -> None:
    assert require_operator_transition(current, requested) == requested


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (TicketStatus.DETECTED, TicketStatus.CREW_ASSIGNED),
        (TicketStatus.DETECTED, TicketStatus.RESOLVED),
        (TicketStatus.ACKNOWLEDGED, TicketStatus.DETECTED),
        (TicketStatus.RESOLVED, TicketStatus.ACKNOWLEDGED),
    ],
)
def test_skipped_and_backward_ticket_transitions_are_rejected(
    current: TicketStatus,
    requested: TicketStatus,
) -> None:
    with pytest.raises(InvalidTicketTransitionError):
        require_operator_transition(current, requested)


@pytest.mark.parametrize("requested", [TicketStatus.VERIFIED, TicketStatus.CLOSED])
def test_verification_and_closure_are_automatic_only(requested: TicketStatus) -> None:
    with pytest.raises(AutomaticTransitionOnlyError):
        require_operator_transition(TicketStatus.RESOLVED, requested)


def test_scope_candidate_fingerprints_are_stable_domain_keys() -> None:
    candidate = span_candidate()

    dt_candidate = replace(candidate, classification=FaultClass.DT_FAULT)
    feeder_candidate = replace(
        candidate,
        classification=FaultClass.FEEDER_FAULT,
        affected_dt_ids=("DT-001", "DT-002"),
    )
    unconfirmed = replace(feeder_candidate, classification=FaultClass.UNCONFIRMED_OUTAGE)

    assert incident_fingerprint(dt_candidate) == "dt:DT-001"
    assert incident_fingerprint(feeder_candidate) == "feeder:FDR-001"
    assert incident_fingerprint(unconfirmed) == "unconfirmed:feeder:FDR-001"
