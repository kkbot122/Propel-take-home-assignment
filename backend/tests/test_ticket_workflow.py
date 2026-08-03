from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from propel.analysis.localization import localize_known_topology
from propel.analysis.models import (
    DeviceEvidence,
    FaultCandidate,
    NetworkSnapshot,
    PoleEvidence,
    TopologySpan,
)
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    PoleStatus,
    TicketStatus,
    TopologySource,
)
from propel.incidents.workflow import (
    AutomaticTransitionOnlyError,
    InvalidTicketTransitionError,
    UnsupportedIncidentCandidateError,
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


def test_non_span_candidate_is_not_given_a_span_fingerprint() -> None:
    candidate = span_candidate()

    with pytest.raises(UnsupportedIncidentCandidateError):
        incident_fingerprint(replace(candidate, classification=FaultClass.DT_FAULT))
