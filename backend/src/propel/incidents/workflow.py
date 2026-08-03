from propel.analysis.models import FaultCandidate
from propel.domain.enums import FaultClass, TicketStatus

OPERATOR_TRANSITIONS = {
    TicketStatus.DETECTED: TicketStatus.ACKNOWLEDGED,
    TicketStatus.ACKNOWLEDGED: TicketStatus.CREW_ASSIGNED,
    TicketStatus.CREW_ASSIGNED: TicketStatus.RESOLVED,
}
AUTOMATIC_ONLY_STATUSES = {TicketStatus.VERIFIED, TicketStatus.CLOSED}


class UnsupportedIncidentCandidateError(Exception):
    pass


class InvalidTicketTransitionError(Exception):
    def __init__(self, current: TicketStatus, requested: TicketStatus) -> None:
        super().__init__(f"cannot transition ticket from {current.value} to {requested.value}")
        self.current = current
        self.requested = requested


class AutomaticTransitionOnlyError(Exception):
    def __init__(self, requested: TicketStatus) -> None:
        super().__init__(f"{requested.value} requires automatic telemetry verification")
        self.requested = requested


def incident_fingerprint(candidate: FaultCandidate) -> str:
    if candidate.classification != FaultClass.SPAN_FAULT:
        raise UnsupportedIncidentCandidateError(candidate.classification.value)
    return f"span:{candidate.dt_id}:{candidate.parent_pole_id}->{candidate.child_pole_id}"


def require_operator_transition(
    current: TicketStatus,
    requested: TicketStatus,
) -> TicketStatus:
    if requested in AUTOMATIC_ONLY_STATUSES:
        raise AutomaticTransitionOnlyError(requested)
    if OPERATOR_TRANSITIONS.get(current) != requested:
        raise InvalidTicketTransitionError(current, requested)
    return requested
