import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from propel.api.routes.diagnostics import router as diagnostics_router
from propel.api.routes.incidents import router as incidents_router
from propel.api.routes.simulator import router as simulator_router
from propel.api.routes.telemetry import error_response
from propel.api.routes.telemetry import router as telemetry_router
from propel.api.schemas.health import DependencyStatus, HealthResponse
from propel.api.schemas.telemetry import (
    ValidationErrorDetail,
    ValidationErrorResponse,
    ValidationIssue,
)
from propel.incidents.explanations import IncidentExplanationService
from propel.infra.ai_explanations import OpenAICompatibleExplanationGateway
from propel.infra.dependencies import ApplicationResources
from propel.infra.diagnostics import OperationalDiagnosticsService
from propel.infra.health import HealthService
from propel.infra.incidents import PostgresIncidentService
from propel.infra.settings import Settings, get_settings
from propel.infra.simulator import HttpSimulatorTelemetryGateway, PostgresSimulatorService
from propel.infra.telemetry import PostgresPoleBindingResolver, RedisTelemetryPublisher
from propel.telemetry.ingestion import TelemetryIngestionService


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
    telemetry_service: TelemetryIngestionService | None = None,
    incident_service: PostgresIncidentService | None = None,
    simulator_service: PostgresSimulatorService | None = None,
    diagnostics_service: OperationalDiagnosticsService | None = None,
    explanation_service: IncidentExplanationService | None = None,
) -> FastAPI:
    application_settings = settings or get_settings()
    application_logger = logging.getLogger("propel")
    application_logger.setLevel(logging.INFO)
    if not any(
        getattr(item, "propel_application_handler", False) for item in application_logger.handlers
    ):
        application_handler = logging.StreamHandler()
        application_handler.setFormatter(logging.Formatter("%(message)s"))
        application_handler.propel_application_handler = True  # type: ignore[attr-defined]
        application_logger.addHandler(application_handler)
    if (
        application_settings.environment.lower() == "production"
        and application_settings.simulator_enabled
    ):
        raise RuntimeError("SIMULATOR_ENABLED must be false when ENVIRONMENT=production")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resources: ApplicationResources | None = None
        owns_explanation_service = explanation_service is None
        if (
            health_service is None
            or telemetry_service is None
            or incident_service is None
            or diagnostics_service is None
            or (application_settings.simulator_enabled and simulator_service is None)
        ):
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
        if incident_service is None:
            if resources is None:
                raise RuntimeError("application resources were not created")
            application.state.incident_service = PostgresIncidentService(resources.database)
        else:
            application.state.incident_service = incident_service
        if explanation_service is not None:
            application.state.explanation_service = explanation_service
        elif application_settings.ai_explainer_configured:
            application.state.explanation_service = IncidentExplanationService(
                OpenAICompatibleExplanationGateway(
                    base_url=application_settings.ai_explainer_base_url,
                    api_key=application_settings.ai_explainer_api_key.get_secret_value(),
                    model=application_settings.ai_explainer_model,
                    timeout_seconds=application_settings.ai_explainer_timeout_seconds,
                    max_input_bytes=application_settings.ai_explainer_max_input_bytes,
                    max_output_tokens=application_settings.ai_explainer_max_output_tokens,
                )
            )
        else:
            application.state.explanation_service = IncidentExplanationService()
        if diagnostics_service is None:
            if resources is None:
                raise RuntimeError("application resources were not created")
            application.state.diagnostics_service = OperationalDiagnosticsService(
                resources.database,
                resources.redis,
                telemetry_stream_name=application_settings.telemetry_stream_name,
                telemetry_consumer_group=application_settings.telemetry_consumer_group,
                dead_letter_stream_name=application_settings.telemetry_dead_letter_stream_name,
                analysis_due_set_name=application_settings.analysis_due_set_name,
                worker_heartbeat_key=application_settings.worker_heartbeat_key,
                worker_stale_after_seconds=(
                    application_settings.diagnostics_worker_stale_after_seconds
                ),
                telemetry_backlog_warning=(
                    application_settings.diagnostics_telemetry_backlog_warning
                ),
            )
        else:
            application.state.diagnostics_service = diagnostics_service
        if application_settings.simulator_enabled and simulator_service is None:
            if resources is None:
                raise RuntimeError("application resources were not created")
            application.state.simulator_service = PostgresSimulatorService(
                resources.database,
                HttpSimulatorTelemetryGateway(
                    application_settings.simulator_telemetry_url,
                    timeout_seconds=application_settings.simulator_request_timeout_seconds,
                ),
                power_loss_delivery_ratio=(
                    application_settings.simulator_power_loss_delivery_ratio
                ),
                power_loss_delivery_seed=application_settings.simulator_power_loss_delivery_seed,
                stale_after_seconds=application_settings.telemetry_stale_after_seconds,
            )
        elif simulator_service is not None:
            application.state.simulator_service = simulator_service
        application.state.telemetry_request_timeout_seconds = (
            application_settings.telemetry_request_timeout_seconds
        )
        application.state.telemetry_batch_max_items = application_settings.telemetry_batch_max_items
        try:
            yield
        finally:
            if owns_explanation_service:
                await application.state.explanation_service.close()
            if application_settings.simulator_enabled and simulator_service is None:
                await application.state.simulator_service.close()
            if resources is not None:
                await resources.close()

    application = FastAPI(
        title="Propel Outage Localization API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=application_settings.trusted_hosts or ["*"],
    )
    if application_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=application_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Correlation-ID"],
        )

    request_logger = logging.getLogger("propel.http")

    async def add_request_diagnostics(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_correlation_id = request.headers.get("x-correlation-id", "")[:128]
        try:
            correlation_id = str(UUID(supplied_correlation_id))
        except ValueError:
            correlation_id = str(uuid4())
        request.state.correlation_id = correlation_id
        started_at = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            request_logger.exception(
                json.dumps(
                    {
                        "event": "request_failed",
                        "correlation_id": correlation_id,
                        "method": request.method,
                        "path": request.url.path,
                    }
                )
            )
            raise
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        request_logger.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((monotonic() - started_at) * 1_000, 2),
                }
            )
        )
        return response

    @application.middleware("http")
    async def enforce_telemetry_request_size(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        telemetry_limit = {
            "/api/telemetry": application_settings.telemetry_max_request_bytes,
            "/api/telemetry/batch": application_settings.telemetry_batch_max_request_bytes,
        }.get(request.url.path)
        if telemetry_limit is not None:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    request_bytes = int(content_length)
                except ValueError:
                    request_bytes = telemetry_limit + 1
                if request_bytes < 0 or request_bytes > telemetry_limit:
                    return error_response(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        "REQUEST_TOO_LARGE",
                        "telemetry request body exceeds the configured limit",
                        retryable=False,
                    )
            body = await request.body()
            if len(body) > telemetry_limit:
                return error_response(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "REQUEST_TOO_LARGE",
                    "telemetry request body exceeds the configured limit",
                    retryable=False,
                )
        return await call_next(request)

    # Register this last so correlation, logging, and security headers also wrap
    # request-limit rejections and trusted-host/CORS responses.
    application.middleware("http")(add_request_diagnostics)

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
    application.include_router(incidents_router)
    application.include_router(diagnostics_router)
    if application_settings.simulator_enabled:
        application.include_router(simulator_router)

    return application


app = create_app()
