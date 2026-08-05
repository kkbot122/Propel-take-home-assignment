from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from propel.api.app import create_app
from propel.domain.enums import (
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    SuspectedAssetType,
    TicketStatus,
)
from propel.incidents.explanations import (
    ExplanationInput,
    GeneratedExplanation,
    IncidentExplanationService,
)
from propel.incidents.models import IncidentView, TicketView
from propel.infra.incidents import IncidentNotFoundError, IncidentStoreUnavailableError
from propel.infra.settings import Settings

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
INCIDENT_ID = UUID("00000000-0000-0000-0000-000000000001")
TICKET_ID = UUID("00000000-0000-0000-0000-000000000002")


def incident_view() -> IncidentView:
    return IncidentView(
        incident_id=INCIDENT_ID,
        fingerprint="span:DT-001:P-001->P-002",
        status=IncidentStatus.ACTIVE,
        classification=FaultClass.SPAN_FAULT,
        suspected_asset_type=SuspectedAssetType.SPAN,
        suspected_asset_id="P-001->P-002",
        latitude=12.88,
        longitude=77.58,
        pin_code="560078",
        affected_pole_count=3,
        affected_pole_ids=("P-002", "P-003", "P-004"),
        precision=LocalizationPrecision.EXACT_SPAN,
        confidence_score=92,
        confidence_reason="A fresh live-to-dark boundary matches the outage pattern.",
        evidence={
            "topology_source": "SURVEYED",
            "candidate": {
                "positive_reasons": ["upstream pole is live"],
                "negative_reasons": [],
            },
        },
        suppression_reason=None,
        suppression_source=None,
        suppression_external_id=None,
        detected_at=NOW,
        updated_at=NOW,
        resolved_at=None,
        ticket_id=TICKET_ID,
        ticket_status=TicketStatus.DETECTED,
        assigned_crew=None,
    )


def ticket_view() -> TicketView:
    return TicketView(
        ticket_id=TICKET_ID,
        incident_id=INCIDENT_ID,
        status=TicketStatus.DETECTED,
        assigned_crew=None,
        created_at=NOW,
        updated_at=NOW,
        resolution_claimed_at=None,
        verified_at=None,
        closed_at=None,
        restoration_status=None,
        remaining_dark_count=None,
        events=(),
    )


class FakeIncidentService:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    async def get_incident(self, incident_id: UUID) -> IncidentView:
        if self.failure is not None:
            raise self.failure
        assert incident_id == INCIDENT_ID
        return incident_view()

    async def get_ticket(self, ticket_id: UUID) -> TicketView:
        assert ticket_id == TICKET_ID
        return ticket_view()


class FakeGateway:
    async def generate(self, explanation_input: ExplanationInput) -> GeneratedExplanation:
        assert explanation_input.values["incident"]["suspected_asset_id"] == "P-001->P-002"
        return GeneratedExplanation(
            what_happened="Three downstream poles lost power.",
            why_this_cause="The fresh live-to-dark boundary points to this span.",
            what_happens_next="Acknowledge the incident and then assign a crew.",
        )

    async def close(self) -> None:
        pass


async def request_explanation(
    incident_service: FakeIncidentService,
    explanation_service: IncidentExplanationService,
):
    app = create_app(
        settings=Settings(simulator_enabled=False),
        incident_service=incident_service,  # type: ignore[arg-type]
        explanation_service=explanation_service,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.post(f"/api/incidents/{INCIDENT_ID}/explanation")


@pytest.mark.asyncio
async def test_explanation_endpoint_returns_generated_three_part_summary() -> None:
    response = await request_explanation(
        FakeIncidentService(),
        IncidentExplanationService(FakeGateway()),
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": "AI_GENERATED",
        "what_happened": "Three downstream poles lost power.",
        "why_this_cause": "The fresh live-to-dark boundary points to this span.",
        "what_happens_next": "Acknowledge the incident and then assign a crew.",
        "incident_updated_at": "2026-08-05T12:00:00Z",
        "ticket_updated_at": "2026-08-05T12:00:00Z",
        "fallback_reason": None,
    }


@pytest.mark.asyncio
async def test_explanation_endpoint_returns_deterministic_summary_without_configuration() -> None:
    response = await request_explanation(
        FakeIncidentService(),
        IncidentExplanationService(),
    )

    assert response.status_code == 200
    assert response.json()["source"] == "DETERMINISTIC"
    assert response.json()["fallback_reason"] == "NOT_CONFIGURED"
    assert "acknowledge" in response.json()["what_happens_next"]


@pytest.mark.parametrize(
    ("failure", "status_code", "error_code"),
    [
        (IncidentNotFoundError(), 404, "INCIDENT_NOT_FOUND"),
        (IncidentStoreUnavailableError(), 503, "INCIDENT_STORE_UNAVAILABLE"),
    ],
)
@pytest.mark.asyncio
async def test_explanation_endpoint_preserves_stable_incident_errors(
    failure: Exception,
    status_code: int,
    error_code: str,
) -> None:
    response = await request_explanation(
        FakeIncidentService(failure),
        IncidentExplanationService(),
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code
