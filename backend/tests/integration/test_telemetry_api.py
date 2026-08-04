from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from propel.api.app import create_app
from propel.infra.settings import Settings

pytestmark = pytest.mark.integration

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


@asynccontextmanager
async def running_api(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    settings = Settings()
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.delete(settings.telemetry_stream_name)
        yield client
    finally:
        await client.delete(settings.telemetry_stream_name)
        await client.aclose()


@pytest.mark.asyncio
async def test_valid_telemetry_crosses_http_to_redis_boundary(redis_client: Redis) -> None:
    settings = Settings()
    async with running_api(settings) as client:
        response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)
        unknown_response = await client.post(
            "/api/telemetry", json=SAMPLE_PAYLOAD | {"pole_id": "P-UNKNOWN"}
        )
        conflict_response = await client.post(
            "/api/telemetry", json=SAMPLE_PAYLOAD | {"device_id": "DEV-P-003"}
        )

    assert response.status_code == 202
    response_body = response.json()
    assert response_body["event_id"]
    assert response_body["correlation_id"]
    assert response_body["received_at"].endswith("Z")

    entries = await redis_client.xrange(settings.telemetry_stream_name)
    assert len(entries) == 1
    stream_id, fields = entries[0]
    assert response_body["stream_id"] == stream_id
    assert fields == {
        "event_id": response_body["event_id"],
        "correlation_id": response_body["correlation_id"],
        "received_at": response_body["received_at"],
        "device_id": "DEV-P-002",
        "pole_id": "P-002",
        "event": "power_lost",
        "energized": "false",
        "ts": "2026-08-03T12:00:00Z",
        "seq": "101",
        "battery_mv": "3480",
        "rssi": "-91",
        "fw": "1.4.2",
        "origin": "DEVICE",
    }
    assert unknown_response.status_code == 404
    assert unknown_response.json()["error"]["code"] == "UNKNOWN_POLE"
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "DEVICE_BINDING_CONFLICT"


@pytest.mark.asyncio
async def test_real_redis_connection_failure_is_retryable() -> None:
    settings = Settings(
        redis_url="redis://redis:6399/0",
        dependency_timeout_seconds=0.1,
        telemetry_request_timeout_seconds=0.5,
    )
    async with running_api(settings) as client:
        response = await client.post("/api/telemetry", json=SAMPLE_PAYLOAD)

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "TELEMETRY_QUEUE_UNAVAILABLE",
        "message": "telemetry queue is temporarily unavailable",
        "retryable": True,
    }
