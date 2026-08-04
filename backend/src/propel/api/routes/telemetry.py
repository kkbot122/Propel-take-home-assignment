import asyncio
import json
import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from propel.api.schemas.telemetry import (
    ErrorDetail,
    ErrorResponse,
    TelemetryAcceptedResponse,
    TelemetryRequest,
    ValidationErrorResponse,
)
from propel.domain.enums import TelemetryOrigin
from propel.telemetry.ingestion import (
    DeviceBindingConflictError,
    IdentityLookupUnavailableError,
    TelemetryIngestionService,
    TelemetryQueueUnavailableError,
    UnknownPoleError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["telemetry"])


def error_response(status_code: int, code: str, message: str, *, retryable: bool) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message, retryable=retryable))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def log_ingestion_outcome(outcome: str, payload: TelemetryRequest, **identifiers: str) -> None:
    logger.info(
        json.dumps(
            {
                "event": "telemetry_ingestion",
                "outcome": outcome,
                "device_id": payload.device_id,
                "pole_id": payload.pole_id,
                **identifiers,
            }
        )
    )


@router.post(
    "/telemetry",
    response_model=TelemetryAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def ingest_telemetry(
    payload: TelemetryRequest,
    request: Request,
) -> TelemetryAcceptedResponse | JSONResponse:
    service: TelemetryIngestionService = request.app.state.telemetry_ingestion_service
    timeout_seconds: float = request.app.state.telemetry_request_timeout_seconds
    try:
        async with asyncio.timeout(timeout_seconds):
            origin = (
                TelemetryOrigin.SIMULATOR
                if request.headers.get("x-propel-telemetry-origin") == "simulator"
                else TelemetryOrigin.DEVICE
            )
            receipt = await service.ingest(payload.to_command(), origin=origin)
    except UnknownPoleError:
        log_ingestion_outcome("unknown_pole", payload)
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "UNKNOWN_POLE",
            f"pole {payload.pole_id} does not exist",
            retryable=False,
        )
    except DeviceBindingConflictError:
        log_ingestion_outcome("device_binding_conflict", payload)
        return error_response(
            status.HTTP_409_CONFLICT,
            "DEVICE_BINDING_CONFLICT",
            "device is not actively bound to the supplied pole",
            retryable=False,
        )
    except IdentityLookupUnavailableError:
        log_ingestion_outcome("identity_lookup_unavailable", payload)
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "IDENTITY_LOOKUP_UNAVAILABLE",
            "pole identity validation is temporarily unavailable",
            retryable=True,
        )
    except TelemetryQueueUnavailableError:
        log_ingestion_outcome("queue_unavailable", payload)
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "TELEMETRY_QUEUE_UNAVAILABLE",
            "telemetry queue is temporarily unavailable",
            retryable=True,
        )
    except TimeoutError:
        log_ingestion_outcome("request_timeout", payload)
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "INGESTION_TIMEOUT",
            "telemetry ingestion exceeded its processing deadline",
            retryable=True,
        )

    log_ingestion_outcome(
        "accepted",
        payload,
        event_id=str(receipt.event_id),
        correlation_id=str(receipt.correlation_id),
    )
    return TelemetryAcceptedResponse(
        event_id=receipt.event_id,
        correlation_id=receipt.correlation_id,
        received_at=receipt.received_at,
        stream_id=receipt.stream_id,
    )
