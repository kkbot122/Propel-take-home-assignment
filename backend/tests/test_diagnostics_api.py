import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from propel.api.app import create_app
from propel.infra.diagnostics import DiagnosticPage
from propel.infra.health import HealthService
from propel.infra.settings import Settings


def probe(result: bool) -> Callable[[], Awaitable[bool]]:
    async def execute() -> bool:
        return result

    return execute


class FakeDiagnosticsService:
    def __init__(self) -> None:
        self.telemetry_limit: int | None = None

    async def overview(self) -> dict[str, Any]:
        return {
            "status": "degraded",
            "generated_at": datetime(2026, 8, 4, tzinfo=UTC),
            "dependencies": {
                "database": {"status": "ok"},
                "redis": {"status": "unavailable"},
            },
            "worker": {"status": "unknown", "last_seen_at": None},
            "queue": {
                "stream_length": None,
                "pending": None,
                "lag": None,
                "dead_letter_count": None,
                "analysis_pending": None,
                "analysis_overdue": None,
            },
            "device_counts": {"HEALTHY": 3},
            "pole_state_counts": {"LIVE": 3},
            "incident_counts": {},
            "latest_processed_at": None,
            "warnings": [
                {
                    "code": "REDIS_UNAVAILABLE",
                    "severity": "critical",
                    "message": "Redis diagnostics are unavailable.",
                }
            ],
        }

    async def telemetry_history(
        self,
        *,
        limit: int,
        before_id: int | None,
        device_id: str | None,
        pole_id: str | None,
    ) -> DiagnosticPage:
        self.telemetry_limit = limit
        return DiagnosticPage(
            items=(
                {
                    "event_id": "00000000-0000-0000-0000-000000000001",
                    "correlation_id": "00000000-0000-0000-0000-000000000002",
                    "device_id": device_id or "DEV-001",
                    "pole_id": pole_id or "P-001",
                    "event_type": "heartbeat",
                    "energized": True,
                    "device_timestamp": datetime(2026, 8, 4, tzinfo=UTC),
                    "received_at": datetime(2026, 8, 4, tzinfo=UTC),
                    "processed_at": datetime(2026, 8, 4, tzinfo=UTC),
                    "sequence": 1,
                    "processing_outcome": "accepted",
                    "origin": "DEVICE",
                    "state_changed": False,
                },
            ),
            next_cursor=str(before_id) if before_id else None,
        )

    async def device_health(self, **_kwargs: Any) -> DiagnosticPage:
        return DiagnosticPage(items=(), next_cursor=None)


@pytest.mark.asyncio
async def test_diagnostics_exposes_degraded_dependency_and_bounded_history() -> None:
    diagnostics = FakeDiagnosticsService()
    app = create_app(
        settings=Settings(service_name="test-api"),
        health_service=HealthService(probe(True), probe(True)),
        diagnostics_service=diagnostics,  # type: ignore[arg-type]
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            overview = await client.get("/api/diagnostics/overview")
            history = await client.get("/api/diagnostics/telemetry?limit=2&device_id=DEV-001")
            oversized = await client.get("/api/diagnostics/telemetry?limit=101")

    assert overview.status_code == 200
    assert overview.json()["status"] == "degraded"
    assert overview.json()["warnings"][0]["code"] == "REDIS_UNAVAILABLE"
    assert history.status_code == 200
    assert diagnostics.telemetry_limit == 2
    assert history.json()["items"][0]["device_id"] == "DEV-001"
    assert "raw_payload" not in history.text
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_api_adds_security_and_correlation_headers_without_logging_query_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        settings=Settings(service_name="test-api"),
        health_service=HealthService(probe(True), probe(True)),
        diagnostics_service=FakeDiagnosticsService(),  # type: ignore[arg-type]
    )
    correlation_id = "00000000-0000-0000-0000-000000000099"
    caplog.set_level(logging.INFO, logger="propel.http")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/health?secret=must-not-be-logged",
                headers={"X-Correlation-ID": correlation_id},
            )

    assert response.headers["x-correlation-id"] == correlation_id
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert correlation_id in caplog.text
    assert "must-not-be-logged" not in caplog.text


def test_production_configuration_rejects_enabled_simulator() -> None:
    with pytest.raises(RuntimeError, match="SIMULATOR_ENABLED"):
        create_app(settings=Settings(environment="production", simulator_enabled=True))
