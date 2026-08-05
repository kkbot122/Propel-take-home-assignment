import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from propel.domain.enums import IncidentStatus, TicketStatus, TopologySource
from propel.incidents.models import IncidentView, TicketView

logger = logging.getLogger(__name__)

MAX_REASON_COUNT = 8
MAX_REASON_LENGTH = 300
MAX_SECTION_LENGTH = 320


class ExplanationSource(StrEnum):
    AI_GENERATED = "AI_GENERATED"
    DETERMINISTIC = "DETERMINISTIC"


class ExplanationFallbackReason(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    REFUSAL = "REFUSAL"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class ExplanationProviderError(Exception):
    def __init__(self, reason: ExplanationFallbackReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class GeneratedExplanation:
    what_happened: str
    why_this_cause: str
    what_happens_next: str


@dataclass(frozen=True, slots=True)
class IncidentExplanation:
    source: ExplanationSource
    what_happened: str
    why_this_cause: str
    what_happens_next: str
    incident_updated_at: str
    ticket_updated_at: str | None
    fallback_reason: ExplanationFallbackReason | None


@dataclass(frozen=True, slots=True)
class ExplanationInput:
    values: dict[str, Any]

    def as_json(self) -> str:
        return json.dumps(self.values, separators=(",", ":"), sort_keys=True)


class ExplanationGateway(Protocol):
    async def generate(self, explanation_input: ExplanationInput) -> GeneratedExplanation: ...

    async def close(self) -> None: ...


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded_reasons(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item[:MAX_REASON_LENGTH] for item in value if isinstance(item, str)][:MAX_REASON_COUNT]


def _bounded_caps(value: object) -> list[dict[str, int | str]]:
    if not isinstance(value, list):
        return []
    caps: list[dict[str, int | str]] = []
    for item in value[:MAX_REASON_COUNT]:
        cap = _record(item)
        maximum = cap.get("maximum")
        reason = cap.get("reason")
        if isinstance(maximum, int) and isinstance(reason, str):
            caps.append({"maximum": maximum, "reason": reason[:MAX_REASON_LENGTH]})
    return caps


def _ticket_evidence(ticket: TicketView | None) -> dict[str, object]:
    if ticket is None:
        return {"status": None, "remaining_dark_count": None, "restoration_status": None}
    return {
        "status": ticket.status.value,
        "remaining_dark_count": ticket.remaining_dark_count,
        "restoration_status": ticket.restoration_status,
    }


def build_explanation_input(
    incident: IncidentView,
    ticket: TicketView | None,
) -> ExplanationInput:
    candidate = _record(incident.evidence.get("candidate"))
    corridor = _record(candidate.get("corridor"))
    skipped_pole_ids = corridor.get("skipped_pole_ids")
    topology_source = incident.evidence.get("topology_source")
    input_values: dict[str, Any] = {
        "incident": {
            "status": incident.status.value,
            "classification": incident.classification.value,
            "suspected_asset_type": incident.suspected_asset_type.value,
            "suspected_asset_id": incident.suspected_asset_id[:160],
            "precision": incident.precision.value,
            "affected_pole_count": incident.affected_pole_count,
            "confidence_score": incident.confidence_score,
            "confidence_reason": incident.confidence_reason[:MAX_REASON_LENGTH],
            "positive_reasons": _bounded_reasons(candidate.get("positive_reasons")),
            "negative_reasons": _bounded_reasons(candidate.get("negative_reasons")),
            "confidence_caps": _bounded_caps(candidate.get("caps")),
            "topology_source": (
                topology_source
                if topology_source in {item.value for item in TopologySource}
                else None
            ),
            "topology_quality_tier": (
                candidate.get("topology_quality_tier")
                if isinstance(candidate.get("topology_quality_tier"), str)
                else None
            ),
            "corridor": (
                {
                    "upstream_pole_id": corridor.get("upstream_live_pole_id"),
                    "downstream_pole_id": corridor.get("downstream_dark_pole_id"),
                    "skipped_pole_count": (
                        len(skipped_pole_ids) if isinstance(skipped_pole_ids, list) else 0
                    ),
                }
                if corridor
                else None
            ),
            "suppression_reason": (
                incident.suppression_reason[:MAX_REASON_LENGTH]
                if incident.suppression_reason
                else None
            ),
        },
        "ticket": _ticket_evidence(ticket),
    }
    return ExplanationInput(input_values)


def _asset_label(incident: IncidentView) -> str:
    return incident.suspected_asset_id.replace("->", " to ").replace("..", " through ")


def _next_step(incident: IncidentView, ticket: TicketView | None) -> str:
    if incident.status == IncidentStatus.SUPPRESSED:
        return (
            "No dispatch ticket was created because this finding is suppressed. "
            "Review the diagnostic evidence if the condition changes."
        )
    if ticket is None:
        return "No ticket is currently attached. Keep the structured finding under review."
    messages = {
        TicketStatus.DETECTED: (
            "An operator should acknowledge the incident. After that, a crew can be assigned."
        ),
        TicketStatus.ACKNOWLEDGED: "Assign a crew to inspect and repair the suspected asset.",
        TicketStatus.CREW_ASSIGNED: (
            "The crew should inspect the suspected asset, complete the repair, and then claim "
            "physical repair."
        ),
        TicketStatus.RESOLVED: (
            "The repair has been claimed. Propel will wait for fresh, stable live telemetry "
            "before verifying or closing the ticket."
        ),
        TicketStatus.VERIFIED: (
            "Fresh telemetry has verified restoration. Propel will close the ticket automatically."
        ),
        TicketStatus.CLOSED: (
            "Fresh telemetry verified restoration and the ticket is closed. "
            "No operator transition remains."
        ),
    }
    return messages[ticket.status]


def deterministic_explanation(
    incident: IncidentView,
    ticket: TicketView | None,
) -> GeneratedExplanation:
    precision = incident.precision.value.replace("_", " ").lower()
    happened = (
        f"Propel found {incident.affected_pole_count} affected pole"
        f"{'s' if incident.affected_pole_count != 1 else ''} and localized the finding to "
        f"{_asset_label(incident)} at {precision} precision."
    )
    candidate = _record(incident.evidence.get("candidate"))
    positive = _bounded_reasons(candidate.get("positive_reasons"))
    negative = _bounded_reasons(candidate.get("negative_reasons"))
    reason = incident.confidence_reason.rstrip(".")
    if positive:
        reason = f"{reason}. Strongest supporting evidence: {positive[0].rstrip('.')}"
    if negative:
        reason = f"{reason}. Limiting evidence: {negative[0].rstrip('.')}"
    else:
        reason = f"{reason}. No contradictory post-onset evidence was found"
    return GeneratedExplanation(
        what_happened=happened[:MAX_SECTION_LENGTH],
        why_this_cause=f"{reason}."[:MAX_SECTION_LENGTH],
        what_happens_next=_next_step(incident, ticket)[:MAX_SECTION_LENGTH],
    )


class IncidentExplanationService:
    def __init__(self, gateway: ExplanationGateway | None = None) -> None:
        self._gateway = gateway

    async def explain(
        self,
        incident: IncidentView,
        ticket: TicketView | None,
    ) -> IncidentExplanation:
        fallback_reason: ExplanationFallbackReason | None = None
        generated: GeneratedExplanation
        source = ExplanationSource.AI_GENERATED
        if self._gateway is None:
            fallback_reason = ExplanationFallbackReason.NOT_CONFIGURED
            generated = deterministic_explanation(incident, ticket)
            source = ExplanationSource.DETERMINISTIC
        else:
            try:
                generated = await self._gateway.generate(build_explanation_input(incident, ticket))
            except ExplanationProviderError as error:
                fallback_reason = error.reason
                generated = deterministic_explanation(incident, ticket)
                source = ExplanationSource.DETERMINISTIC
        logger.info(
            json.dumps(
                {
                    "event": "incident_explanation_created",
                    "incident_id": str(incident.incident_id),
                    "ticket_id": str(ticket.ticket_id) if ticket else None,
                    "source": source.value,
                    "fallback_reason": fallback_reason.value if fallback_reason else None,
                }
            )
        )
        return IncidentExplanation(
            source=source,
            what_happened=generated.what_happened,
            why_this_cause=generated.why_this_cause,
            what_happens_next=generated.what_happens_next,
            incident_updated_at=incident.updated_at.isoformat(),
            ticket_updated_at=ticket.updated_at.isoformat() if ticket else None,
            fallback_reason=fallback_reason,
        )

    async def close(self) -> None:
        if self._gateway is not None:
            await self._gateway.close()
