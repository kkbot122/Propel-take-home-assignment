from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from propel.api.routes.telemetry import error_response
from propel.api.schemas.diagnostics import (
    DeviceHealthDiagnosticPageResponse,
    OperationalOverviewResponse,
    TelemetryDiagnosticPageResponse,
)
from propel.api.schemas.telemetry import ErrorResponse
from propel.infra.diagnostics import DiagnosticsUnavailableError, OperationalDiagnosticsService

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])
UNAVAILABLE_RESPONSE = {status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse}}


def diagnostics_service(request: Request) -> OperationalDiagnosticsService:
    return request.app.state.diagnostics_service


def unavailable_response() -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "DIAGNOSTICS_UNAVAILABLE",
        "bounded operational diagnostics are temporarily unavailable",
        retryable=True,
    )


@router.get("/overview", response_model=OperationalOverviewResponse)
async def overview(request: Request) -> OperationalOverviewResponse:
    result = await diagnostics_service(request).overview()
    return OperationalOverviewResponse.model_validate(result)


@router.get(
    "/telemetry",
    response_model=TelemetryDiagnosticPageResponse,
    responses=UNAVAILABLE_RESPONSE,
)
async def telemetry_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    device_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    pole_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> TelemetryDiagnosticPageResponse | JSONResponse:
    try:
        page = await diagnostics_service(request).telemetry_history(
            limit=limit,
            before_id=cursor,
            device_id=device_id,
            pole_id=pole_id,
        )
    except DiagnosticsUnavailableError:
        return unavailable_response()
    return TelemetryDiagnosticPageResponse(items=list(page.items), next_cursor=page.next_cursor)


@router.get(
    "/devices",
    response_model=DeviceHealthDiagnosticPageResponse,
    responses=UNAVAILABLE_RESPONSE,
)
async def device_health(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    device_status: Annotated[
        Literal["HEALTHY", "STALE", "UNKNOWN"] | None, Query(alias="status")
    ] = None,
    dt_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> DeviceHealthDiagnosticPageResponse | JSONResponse:
    try:
        page = await diagnostics_service(request).device_health(
            limit=limit,
            after_device_id=cursor,
            status=device_status,
            dt_id=dt_id,
        )
    except DiagnosticsUnavailableError:
        return unavailable_response()
    return DeviceHealthDiagnosticPageResponse(items=list(page.items), next_cursor=page.next_cursor)
