import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from propel.domain.enums import (
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    SuspectedAssetType,
    TicketStatus,
)
from propel.incidents.explanations import (
    ExplanationFallbackReason,
    ExplanationInput,
    ExplanationProviderError,
    ExplanationSource,
    IncidentExplanationService,
    build_explanation_input,
    deterministic_explanation,
)
from propel.incidents.models import IncidentView, TicketView
from propel.infra.ai_explanations import OpenAICompatibleExplanationGateway

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def incident_view(**changes: object) -> IncidentView:
    values = {
        "incident_id": UUID("00000000-0000-0000-0000-000000000001"),
        "fingerprint": "span:DT-001:P-001->P-002",
        "status": IncidentStatus.ACTIVE,
        "classification": FaultClass.SPAN_FAULT,
        "suspected_asset_type": SuspectedAssetType.SPAN,
        "suspected_asset_id": "P-001->P-002",
        "latitude": 12.88,
        "longitude": 77.58,
        "pin_code": "560078",
        "affected_pole_count": 3,
        "affected_pole_ids": ("P-002", "P-003", "P-004"),
        "precision": LocalizationPrecision.EXACT_SPAN,
        "confidence_score": 92,
        "confidence_reason": "A fresh live-to-dark boundary matches the outage pattern.",
        "evidence": {
            "topology_source": "SURVEYED",
            "candidate": {
                "positive_reasons": ["upstream pole P-001 is fresh and live"],
                "negative_reasons": [],
                "caps": [],
            },
        },
        "suppression_reason": None,
        "suppression_source": None,
        "suppression_external_id": None,
        "detected_at": NOW,
        "updated_at": NOW,
        "resolved_at": None,
        "ticket_id": UUID("00000000-0000-0000-0000-000000000002"),
        "ticket_status": TicketStatus.DETECTED,
        "assigned_crew": "sensitive-crew-name",
    }
    values.update(changes)
    return IncidentView(**values)  # type: ignore[arg-type]


def ticket_view(status: TicketStatus = TicketStatus.DETECTED) -> TicketView:
    repair_claimed = status in {
        TicketStatus.RESOLVED,
        TicketStatus.VERIFIED,
        TicketStatus.CLOSED,
    }
    return TicketView(
        ticket_id=UUID("00000000-0000-0000-0000-000000000002"),
        incident_id=UUID("00000000-0000-0000-0000-000000000001"),
        status=status,
        assigned_crew="sensitive-crew-name",
        created_at=NOW,
        updated_at=NOW,
        resolution_claimed_at=NOW if repair_claimed else None,
        verified_at=NOW if status in {TicketStatus.VERIFIED, TicketStatus.CLOSED} else None,
        closed_at=NOW if status == TicketStatus.CLOSED else None,
        restoration_status="REPAIR_NOT_VERIFIED" if status == TicketStatus.RESOLVED else None,
        remaining_dark_count=2 if status == TicketStatus.RESOLVED else None,
        events=(),
    )


def gateway(client: httpx.AsyncClient, *, max_input_bytes: int = 12_288):
    return OpenAICompatibleExplanationGateway(
        base_url="https://provider.example/v1",
        api_key="secret-key",
        model="small-explainer",
        timeout_seconds=3,
        max_input_bytes=max_input_bytes,
        max_output_tokens=300,
        client=client,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TicketStatus.DETECTED, "acknowledge"),
        (TicketStatus.ACKNOWLEDGED, "Assign a crew"),
        (TicketStatus.CREW_ASSIGNED, "claim physical repair"),
        (TicketStatus.RESOLVED, "fresh, stable live telemetry"),
        (TicketStatus.VERIFIED, "close the ticket automatically"),
        (TicketStatus.CLOSED, "ticket is closed"),
    ],
)
def test_deterministic_explanation_tracks_ticket_workflow(
    status: TicketStatus,
    expected: str,
) -> None:
    explanation = deterministic_explanation(incident_view(), ticket_view(status))

    assert expected in explanation.what_happens_next
    assert "P-001 to P-002" in explanation.what_happened


def test_deterministic_explanation_covers_corridor_inferred_and_suppressed_findings() -> None:
    corridor = incident_view(
        precision=LocalizationPrecision.CORRIDOR,
        suspected_asset_id="P-001..P-003",
    )
    inferred = incident_view(
        precision=LocalizationPrecision.PROBABLE_SPAN,
        evidence={
            "topology_source": "INFERRED",
            "candidate": {
                "positive_reasons": ["geographic topology quality is strong"],
                "negative_reasons": ["inferred topology cannot prove an exact span"],
            },
        },
    )
    suppressed = incident_view(
        status=IncidentStatus.SUPPRESSED,
        classification=FaultClass.SENSOR_ANOMALY,
        suppression_reason="isolated sensor behavior",
        ticket_id=None,
        ticket_status=None,
    )

    assert "corridor precision" in deterministic_explanation(corridor, ticket_view()).what_happened
    assert "Limiting evidence" in deterministic_explanation(inferred, ticket_view()).why_this_cause
    assert "No dispatch ticket" in deterministic_explanation(suppressed, None).what_happens_next


def test_model_input_is_bounded_and_excludes_sensitive_or_raw_data() -> None:
    incident = incident_view(
        evidence={
            "topology_source": "SURVEYED",
            "raw_telemetry": {"secret": "must-not-leak"},
            "candidate": {
                "positive_reasons": ["x" * 500] * 20,
                "negative_reasons": ["operator-visible contradiction"],
            },
        }
    )

    input_json = build_explanation_input(incident, ticket_view()).as_json()

    assert len(json.loads(input_json)["incident"]["positive_reasons"]) == 8
    assert "must-not-leak" not in input_json
    assert "sensitive-crew-name" not in input_json
    assert "560078" not in input_json
    assert "12.88" not in input_json


@pytest.mark.asyncio
async def test_provider_sends_strict_schema_and_parses_three_sections() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "what_happened": "Three poles lost power.",
                                    "why_this_cause": "The boundary evidence points here.",
                                    "what_happens_next": "Acknowledge and assign a crew.",
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://provider.example/v1"
    ) as client:
        result = await gateway(client).generate(
            ExplanationInput({"incident": {"status": "ACTIVE"}})
        )

    request_body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer secret-key"
    assert request_body["response_format"]["type"] == "json_schema"
    assert request_body["response_format"]["json_schema"]["strict"] is True
    assert result.what_happened == "Three poles lost power."


@pytest.mark.parametrize(
    ("response_body", "expected"),
    [
        (
            {"choices": [{"message": {"refusal": "no", "content": ""}}]},
            ExplanationFallbackReason.REFUSAL,
        ),
        (
            {"choices": [{"message": {"content": "not-json"}}]},
            ExplanationFallbackReason.INVALID_RESPONSE,
        ),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "what_happened": "x" * 321,
                                    "why_this_cause": "reason",
                                    "what_happens_next": "next",
                                }
                            )
                        }
                    }
                ]
            },
            ExplanationFallbackReason.INVALID_RESPONSE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_provider_rejects_refusal_malformed_and_oversized_output(
    response_body: dict[str, object],
    expected: ExplanationFallbackReason,
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=response_body))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://provider.example/v1"
    ) as client:
        with pytest.raises(ExplanationProviderError) as caught:
            await gateway(client).generate(ExplanationInput({"incident": {}}))

    assert caught.value.reason == expected


@pytest.mark.asyncio
async def test_provider_maps_timeout_http_failure_and_large_input_to_fallbacks() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    cases = [
        (httpx.MockTransport(timeout), 12_288, ExplanationFallbackReason.TIMEOUT),
        (
            httpx.MockTransport(lambda _request: httpx.Response(500)),
            12_288,
            ExplanationFallbackReason.PROVIDER_ERROR,
        ),
        (
            httpx.MockTransport(lambda _request: httpx.Response(200)),
            1,
            ExplanationFallbackReason.INPUT_TOO_LARGE,
        ),
    ]
    for transport, max_input_bytes, expected in cases:
        async with httpx.AsyncClient(
            transport=transport, base_url="https://provider.example/v1"
        ) as client:
            with pytest.raises(ExplanationProviderError) as caught:
                await gateway(client, max_input_bytes=max_input_bytes).generate(
                    ExplanationInput({"incident": {}})
                )
        assert caught.value.reason == expected


@pytest.mark.asyncio
async def test_service_uses_deterministic_fallback_without_configuration() -> None:
    result = await IncidentExplanationService().explain(incident_view(), ticket_view())

    assert result.source == ExplanationSource.DETERMINISTIC
    assert result.fallback_reason == ExplanationFallbackReason.NOT_CONFIGURED
    assert result.incident_updated_at == NOW.isoformat()


@pytest.mark.asyncio
async def test_gateway_closes_owned_client() -> None:
    provider = OpenAICompatibleExplanationGateway(
        base_url="https://provider.example/v1",
        api_key="secret-key",
        model="small-explainer",
        timeout_seconds=3,
        max_input_bytes=12_288,
        max_output_tokens=300,
    )

    await provider.close()

    assert provider._client.is_closed  # noqa: SLF001
