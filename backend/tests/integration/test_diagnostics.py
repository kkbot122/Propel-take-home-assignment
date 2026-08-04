from datetime import UTC, datetime

import pytest

from propel.infra.dependencies import ApplicationResources
from propel.infra.diagnostics import OperationalDiagnosticsService
from propel.infra.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_operational_diagnostics_are_bounded_and_omit_raw_payloads() -> None:
    settings = get_settings()
    resources = ApplicationResources.create(settings)
    heartbeat_key = "propel:test:worker:heartbeat"
    service = OperationalDiagnosticsService(
        resources.database,
        resources.redis,
        telemetry_stream_name=settings.telemetry_stream_name,
        telemetry_consumer_group=settings.telemetry_consumer_group,
        dead_letter_stream_name=settings.telemetry_dead_letter_stream_name,
        analysis_due_set_name=settings.analysis_due_set_name,
        worker_heartbeat_key=heartbeat_key,
        worker_stale_after_seconds=30,
        telemetry_backlog_warning=1_000_000,
    )
    try:
        await resources.redis.set(heartbeat_key, datetime.now(UTC).isoformat(), ex=30)
        overview = await service.overview()
        telemetry = await service.telemetry_history(
            limit=3,
            before_id=None,
            device_id=None,
            pole_id=None,
        )
        devices = await service.device_health(
            limit=3,
            after_device_id=None,
            status=None,
            dt_id=None,
        )
    finally:
        await resources.redis.delete(heartbeat_key)
        await resources.close()

    assert overview["dependencies"] == {
        "database": {"status": "ok"},
        "redis": {"status": "ok"},
    }
    assert overview["worker"]["status"] == "ok"
    assert len(telemetry.items) <= 3
    assert all("raw_payload" not in item for item in telemetry.items)
    assert len(devices.items) == 3
    assert devices.next_cursor is not None
