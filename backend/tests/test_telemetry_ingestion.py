import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from propel.api.app import create_app
from propel.domain.enums import TelemetryEventType
from propel.infra.health import HealthService
from propel.infra.settings import Settings
from propel.telemetry.ingestion import (
    ResolvedPoleBinding,
    TelemetryCommand,
    TelemetryEnvelope,
    TelemetryIngestionService,
    TelemetryQueueUnavailableError,
)

SAMPLE_PAYLOAD = {
    "device_id": "DEV-P-002",
    "pole_id": "P-002",
    "event": "power_lost",
    "energized": False,
    "ts": "2026-08-03T12:00:00Z",
    "seq": 101,
    "battery_mv": 3480,
    "rssi": -91,
    "fw": "1.4.2",
}
EVENT_ID = UUID("00000000-0000-4000-8000-000000000001")
CORRELATION_ID = UUID("00000000-0000-4000-8000-000000000002")
RECEIVED_AT = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


class StaticBindingResolver:
    def __init__(self, binding: ResolvedPoleBinding | None) -> None:
        self.binding = binding

    async def resolve(self, _pole_id: str) -> ResolvedPoleBinding | None:
        return self.binding

    async def resolve_many(self, pole_ids: tuple[str, ...]) -> dict[str, ResolvedPoleBinding]:
        if self.binding is None or self.binding.pole_id not in pole_ids:
            return {}
        return {self.binding.pole_id: self.binding}


class RecordingPublisher:
    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.delay_seconds = delay_seconds
        self.envelopes: list[TelemetryEnvelope] = []

    async def publish(self, envelope: TelemetryEnvelope) -> str:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        self.envelopes.append(envelope)
        return "1234-0"

    async def publish_many(self, envelopes: tuple[TelemetryEnvelope, ...]) -> list[str]:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        self.envelopes.extend(envelopes)
        return [f"1234-{index}" for index in range(len(envelopes))]


class UnavailablePublisher:
    async def publish(self, _envelope: TelemetryEnvelope) -> str:
        raise TelemetryQueueUnavailableError

    async def publish_many(self, _envelopes: tuple[TelemetryEnvelope, ...]) -> list[str]:
        raise TelemetryQueueUnavailableError


def healthy_service() -> HealthService:
    async def available() -> bool:
        return True

    return HealthService(available, available)


def telemetry_service(
    binding: ResolvedPoleBinding | None,
    publisher: RecordingPublisher | UnavailablePublisher | None = None,
) -> TelemetryIngestionService:
    identifiers = iter((EVENT_ID, CORRELATION_ID))
    return TelemetryIngestionService(
        StaticBindingResolver(binding),
        publisher or RecordingPublisher(),
        clock=lambda: RECEIVED_AT,
        id_factory=lambda: next(identifiers),
    )


@asynccontextmanager
async def api_client(
    service: TelemetryIngestionService,
    *,
    settings: Settings | None = None,
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings=settings or Settings(),
        health_service=healthy_service(),
        telemetry_service=service,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_assignment_payload_is_accepted_after_stream_publication() -> None:
    publisher = RecordingPublisher()
    service = telemetry_service(
        ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-002"), publisher
    )

    async with api_client(service) as client:
        response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "event_id": str(EVENT_ID),
        "correlation_id": str(CORRELATION_ID),
        "received_at": "2026-08-04T00:00:00Z",
        "stream_id": "1234-0",
    }
    assert len(publisher.envelopes) == 1
    assert publisher.envelopes[0].command == TelemetryCommand(
        device_id="DEV-P-002",
        pole_id="P-002",
        event=TelemetryEventType.POWER_LOST,
        energized=False,
        device_timestamp=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        sequence=101,
        battery_mv=3480,
        rssi=-91,
        firmware="1.4.2",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        SAMPLE_PAYLOAD | {"event": "not_an_event"},
        SAMPLE_PAYLOAD | {"energized": True},
        SAMPLE_PAYLOAD | {"seq": "101"},
        {key: value for key, value in SAMPLE_PAYLOAD.items() if key != "rssi"},
    ],
)
async def test_invalid_payloads_return_stable_422(payload: dict[str, object]) -> None:
    service = telemetry_service(ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-002"))

    async with api_client(service) as client:
        response = await client.post("/api/telemetry", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_unknown_pole_and_binding_conflict_are_not_published() -> None:
    async with api_client(telemetry_service(None)) as client:
        unknown_response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)

    conflict_publisher = RecordingPublisher()
    conflict_service = telemetry_service(
        ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-003"), conflict_publisher
    )
    async with api_client(conflict_service) as client:
        conflict_response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)

    assert unknown_response.status_code == 404
    assert unknown_response.json()["error"] == {
        "code": "UNKNOWN_POLE",
        "message": "pole P-002 does not exist",
        "retryable": False,
    }
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "DEVICE_BINDING_CONFLICT"
    assert not conflict_publisher.envelopes


@pytest.mark.asyncio
async def test_queue_failure_and_deadline_return_retryable_503() -> None:
    binding = ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-002")
    async with api_client(telemetry_service(binding, UnavailablePublisher())) as client:
        unavailable_response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)

    slow_publisher = RecordingPublisher(delay_seconds=0.1)
    timeout_settings = Settings(telemetry_request_timeout_seconds=0.01)
    async with api_client(
        telemetry_service(binding, slow_publisher), settings=timeout_settings
    ) as client:
        timeout_response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)

    assert unavailable_response.status_code == 503
    assert unavailable_response.json()["error"]["code"] == "TELEMETRY_QUEUE_UNAVAILABLE"
    assert unavailable_response.json()["error"]["retryable"] is True
    assert timeout_response.status_code == 503
    assert timeout_response.json()["error"]["code"] == "INGESTION_TIMEOUT"
    assert timeout_response.json()["error"]["retryable"] is True
    assert not slow_publisher.envelopes


@pytest.mark.asyncio
async def test_oversized_request_is_rejected_before_ingestion() -> None:
    service = telemetry_service(ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-002"))
    settings = Settings(telemetry_max_request_bytes=1024)
    oversized_payload = SAMPLE_PAYLOAD | {"padding": "x" * 1024}

    async with api_client(service, settings=settings) as client:
        response = await client.post("/api/telemetry", json=oversized_payload)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-correlation-id"]


@pytest.mark.asyncio
async def test_mixed_batch_returns_deterministic_per_item_results() -> None:
    publisher = RecordingPublisher()
    service = telemetry_service(
        ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-002"), publisher
    )
    accepted_event_id = "00000000-0000-4000-8000-000000000011"
    accepted_correlation_id = "00000000-0000-4000-8000-000000000012"
    batch = {
        "items": [
            SAMPLE_PAYLOAD
            | {
                "event_id": accepted_event_id,
                "correlation_id": accepted_correlation_id,
            },
            SAMPLE_PAYLOAD | {"seq": "invalid"},
            SAMPLE_PAYLOAD | {"pole_id": "P-UNKNOWN"},
        ]
    }

    async with api_client(service) as client:
        response = await client.post("/api/telemetry/batch", json=batch)

    assert response.status_code == 207
    assert response.json() == {
        "status": "partial",
        "accepted": 1,
        "rejected": 2,
        "results": [
            {
                "index": 0,
                "status": "accepted",
                "event_id": accepted_event_id,
                "correlation_id": accepted_correlation_id,
                "received_at": "2026-08-04T00:00:00Z",
                "stream_id": "1234-0",
                "error": None,
            },
            {
                "index": 1,
                "status": "rejected",
                "event_id": None,
                "correlation_id": None,
                "received_at": None,
                "stream_id": None,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "telemetry item validation failed",
                    "retryable": False,
                    "issues": [
                        {
                            "location": "seq",
                            "message": "Input should be a valid integer",
                            "type": "int_type",
                        }
                    ],
                },
            },
            {
                "index": 2,
                "status": "rejected",
                "event_id": None,
                "correlation_id": None,
                "received_at": None,
                "stream_id": None,
                "error": {
                    "code": "UNKNOWN_POLE",
                    "message": "pole P-UNKNOWN does not exist",
                    "retryable": False,
                    "issues": [],
                },
            },
        ],
    }
    assert len(publisher.envelopes) == 1


@pytest.mark.asyncio
async def test_batch_limits_and_queue_failure_have_stable_retry_semantics() -> None:
    binding = ResolvedPoleBinding(pole_id="P-002", active_device_id="DEV-P-002")
    limited_settings = Settings(telemetry_batch_max_items=2)
    async with api_client(telemetry_service(binding), settings=limited_settings) as client:
        oversized_response = await client.post(
            "/api/telemetry/batch",
            json={"items": [SAMPLE_PAYLOAD, SAMPLE_PAYLOAD, SAMPLE_PAYLOAD]},
        )

    async with api_client(telemetry_service(binding, UnavailablePublisher())) as client:
        unavailable_response = await client.post(
            "/api/telemetry/batch",
            json={"items": [SAMPLE_PAYLOAD]},
        )

    assert oversized_response.status_code == 413
    assert oversized_response.json()["error"] == {
        "code": "BATCH_TOO_LARGE",
        "message": "telemetry batch exceeds the configured limit of 2 items",
        "retryable": False,
    }
    assert unavailable_response.status_code == 503
    assert unavailable_response.json()["error"]["code"] == "TELEMETRY_QUEUE_UNAVAILABLE"
    assert unavailable_response.json()["error"]["retryable"] is True
    assert "same event IDs" in unavailable_response.json()["error"]["message"]
