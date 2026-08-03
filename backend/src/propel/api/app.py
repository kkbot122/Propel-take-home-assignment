from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from propel.api.schemas.health import DependencyStatus, HealthResponse
from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
) -> FastAPI:
    application_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if health_service is not None:
            application.state.health_service = health_service
            yield
            return

        resources = ApplicationResources.create(application_settings)
        application.state.health_service = HealthService.from_resources(
            resources,
            timeout_seconds=application_settings.dependency_timeout_seconds,
        )
        try:
            yield
        finally:
            await resources.close()

    application = FastAPI(
        title="Propel Outage Localization API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
        tags=["system"],
    )
    async def health() -> HealthResponse | JSONResponse:
        snapshot = await application.state.health_service.check()
        payload = HealthResponse(
            status="healthy" if snapshot.healthy else "unhealthy",
            service=application_settings.service_name,
            dependencies={
                "database": DependencyStatus(status="ok" if snapshot.database else "unavailable"),
                "redis": DependencyStatus(status="ok" if snapshot.redis else "unavailable"),
            },
        )
        if snapshot.healthy:
            return payload
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(mode="json"),
        )

    return application


app = create_app()
