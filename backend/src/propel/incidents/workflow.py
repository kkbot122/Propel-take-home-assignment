from propel.analysis.models import FaultCandidate
from propel.domain.enums import FaultClass, LocalizationPrecision, SuspectedAssetType, TicketStatus

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
    if candidate.classification == FaultClass.SPAN_FAULT:
        if candidate.precision == LocalizationPrecision.CORRIDOR:
            return (
                f"corridor:{candidate.dt_id}:{candidate.parent_pole_id}..{candidate.child_pole_id}"
            )
        return f"span:{candidate.dt_id}:{candidate.parent_pole_id}->{candidate.child_pole_id}"
    if candidate.classification == FaultClass.DT_FAULT:
        return f"dt:{candidate.dt_id}"
    if candidate.classification == FaultClass.FEEDER_FAULT:
        return f"feeder:{candidate.feeder_id}"
    if candidate.classification == FaultClass.UNCONFIRMED_OUTAGE:
        if candidate.suspected_asset_type == SuspectedAssetType.DISTRIBUTION_TRANSFORMER:
            return f"unconfirmed:dt:{candidate.dt_id}"
        return f"unconfirmed:feeder:{candidate.feeder_id}"
    if candidate.classification == FaultClass.SENSOR_ANOMALY:
        if candidate.suppression is None:
            raise UnsupportedIncidentCandidateError(candidate.classification.value)
        return f"sensor:{candidate.dt_id}:{candidate.suspected_asset_id}"
    if candidate.classification == FaultClass.SCHEDULED_OUTAGE:
        if candidate.suppression is None or candidate.suppression.external_id is None:
            raise UnsupportedIncidentCandidateError(candidate.classification.value)
        if candidate.suspected_asset_type == SuspectedAssetType.SPAN:
            asset_scope = f"{candidate.dt_id}:{candidate.parent_pole_id}->{candidate.child_pole_id}"
        else:
            asset_scope = candidate.suspected_asset_id
        return f"scheduled:{candidate.suppression.external_id}:{asset_scope}"
    raise UnsupportedIncidentCandidateError(candidate.classification.value)


def require_operator_transition(
    current: TicketStatus,
    requested: TicketStatus,
) -> TicketStatus:
    if requested in AUTOMATIC_ONLY_STATUSES:
        raise AutomaticTransitionOnlyError(requested)
    if OPERATOR_TRANSITIONS.get(current) != requested:
        raise InvalidTicketTransitionError(current, requested)
    return requested
