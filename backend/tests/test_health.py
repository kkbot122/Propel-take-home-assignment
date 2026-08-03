from collections.abc import Awaitable, Callable

import pytest
from httpx import ASGITransport, AsyncClient

from propel.api.app import create_app
from propel.infra.health import HealthService
from propel.infra.settings import Settings


def probe(result: bool) -> Callable[[], Awaitable[bool]]:
    async def execute() -> bool:
        return result

    return execute


@pytest.mark.asyncio
async def test_health_reports_healthy_dependencies() -> None:
    app = create_app(
        settings=Settings(service_name="test-api"),
        health_service=HealthService(probe(True), probe(True)),
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "test-api",
        "dependencies": {
            "database": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }


@pytest.mark.asyncio
async def test_health_returns_503_when_a_dependency_is_unavailable() -> None:
    app = create_app(
        settings=Settings(service_name="test-api"),
        health_service=HealthService(probe(True), probe(False)),
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert response.json()["dependencies"]["redis"] == {"status": "unavailable"}
