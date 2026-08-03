from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.responses import Response

from propel.api.routes.telemetry import error_response
from propel.api.routes.telemetry import router as telemetry_router
from propel.api.schemas.health import DependencyStatus, HealthResponse
from propel.api.schemas.telemetry import (
    ValidationErrorDetail,
    ValidationErrorResponse,
    ValidationIssue,
)
from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.settings import Settings, get_settings
from propel.infra.telemetry import PostgresPoleBindingResolver, RedisTelemetryPublisher
from propel.telemetry.ingestion import TelemetryIngestionService


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
    telemetry_service: TelemetryIngestionService | None = None,
) -> FastAPI:
    application_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resources: ApplicationResources | None = None
        if health_service is None or telemetry_service is None:
            resources = ApplicationResources.create(application_settings)

        if health_service is None:
            if resources is None:
                raise RuntimeError("application resources were not created")
            application.state.health_service = HealthService.from_resources(
                resources,
                timeout_seconds=application_settings.dependency_timeout_seconds,
            )
        else:
            application.state.health_service = health_service

        if telemetry_service is None:
            if resources is None:
                raise RuntimeError("application resources were not created")
            application.state.telemetry_ingestion_service = TelemetryIngestionService(
                PostgresPoleBindingResolver(resources.database),
                RedisTelemetryPublisher(
                    resources.redis, application_settings.telemetry_stream_name
                ),
            )
        else:
            application.state.telemetry_ingestion_service = telemetry_service
        application.state.telemetry_request_timeout_seconds = (
            application_settings.telemetry_request_timeout_seconds
        )
        try:
            yield
        finally:
            if resources is not None:
                await resources.close()

    application = FastAPI(
        title="Propel Outage Localization API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def enforce_telemetry_request_size(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == "/api/telemetry":
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    request_bytes = int(content_length)
                except ValueError:
                    request_bytes = application_settings.telemetry_max_request_bytes + 1
                if (
                    request_bytes < 0
                    or request_bytes > application_settings.telemetry_max_request_bytes
                ):
                    return error_response(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        "REQUEST_TOO_LARGE",
                        "telemetry request body exceeds the configured limit",
                        retryable=False,
                    )
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        issues = [
            ValidationIssue(
                location=".".join(str(part) for part in item["loc"]),
                message=item["msg"],
                type=item["type"],
            )
            for item in error.errors()
        ]
        payload = ValidationErrorResponse(
            error=ValidationErrorDetail(
                code="VALIDATION_ERROR",
                message="request payload validation failed",
                retryable=False,
                issues=issues,
            )
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=payload.model_dump(mode="json"),
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

    application.include_router(telemetry_router)

    return application


app = create_app()
