import logging
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from propel.infra.diagnostics import OperationalDiagnosticsService


@pytest.mark.asyncio
async def test_dependency_failure_returns_partial_degraded_snapshot_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = OperationalDiagnosticsService(
        cast(AsyncEngine, object()),
        cast(Redis, object()),
        telemetry_stream_name="telemetry",
        telemetry_consumer_group="workers",
        dead_letter_stream_name="dead-letter",
        analysis_due_set_name="analysis",
        worker_heartbeat_key="worker",
        worker_stale_after_seconds=15,
        telemetry_backlog_warning=1_000,
    )

    async def unavailable_database() -> None:
        return None

    async def healthy_redis(_generated_at: Any) -> dict[str, Any]:
        return {
            "worker": {"status": "ok", "last_seen_at": None},
            "queue": {
                "stream_length": 0,
                "pending": 0,
                "lag": 0,
                "dead_letter_count": 0,
                "analysis_pending": 0,
                "analysis_overdue": 0,
            },
        }

    monkeypatch.setattr(service, "_database_summary", unavailable_database)
    monkeypatch.setattr(service, "_redis_summary", healthy_redis)
    caplog.set_level(logging.WARNING, logger="propel.infra.diagnostics")

    overview = await service.overview()

    assert overview["status"] == "degraded"
    assert overview["dependencies"] == {
        "database": {"status": "unavailable"},
        "redis": {"status": "ok"},
    }
    assert overview["warnings"][0]["code"] == "DATABASE_UNAVAILABLE"
    assert "operational_diagnostics_degraded" in caplog.text
    assert "DATABASE_UNAVAILABLE" in caplog.text
