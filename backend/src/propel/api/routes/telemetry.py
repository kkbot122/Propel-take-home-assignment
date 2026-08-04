import asyncio
import json
import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from propel.api.schemas.telemetry import (
    ErrorDetail,
    ErrorResponse,
    TelemetryAcceptedResponse,
    TelemetryBatchItemError,
    TelemetryBatchItemResult,
    TelemetryBatchRequest,
    TelemetryBatchResponse,
    TelemetryRequest,
    ValidationErrorResponse,
    ValidationIssue,
)
from propel.domain.enums import TelemetryOrigin
from propel.telemetry.ingestion import (
    DeviceBindingConflictError,
    IdentityLookupUnavailableError,
    TelemetryIngestionService,
    TelemetryQueueUnavailableError,
    TelemetrySubmission,
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


def telemetry_origin(request: Request) -> TelemetryOrigin:
    return (
        TelemetryOrigin.SIMULATOR
        if request.headers.get("x-propel-telemetry-origin") == "simulator"
        else TelemetryOrigin.DEVICE
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
            receipt = await service.ingest(
                payload.to_command(),
                origin=telemetry_origin(request),
                event_id=payload.event_id,
                correlation_id=payload.correlation_id,
            )
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


@router.post(
    "/telemetry/batch",
    response_model=TelemetryBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_207_MULTI_STATUS: {"model": TelemetryBatchResponse},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def ingest_telemetry_batch(
    payload: TelemetryBatchRequest,
    request: Request,
) -> TelemetryBatchResponse | JSONResponse:
    max_items: int = request.app.state.telemetry_batch_max_items
    if len(payload.items) > max_items:
        return error_response(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "BATCH_TOO_LARGE",
            f"telemetry batch exceeds the configured limit of {max_items} items",
            retryable=False,
        )

    results_by_index: dict[int, TelemetryBatchItemResult] = {}
    submissions: list[TelemetrySubmission] = []
    for index, raw_item in enumerate(payload.items):
        try:
            item = TelemetryRequest.model_validate(raw_item)
        except ValidationError as error:
            issues = [
                ValidationIssue(
                    location=".".join(str(part) for part in detail["loc"]),
                    message=detail["msg"],
                    type=detail["type"],
                )
                for detail in error.errors()
            ]
            results_by_index[index] = TelemetryBatchItemResult(
                index=index,
                status="rejected",
                error=TelemetryBatchItemError(
                    code="VALIDATION_ERROR",
                    message="telemetry item validation failed",
                    retryable=False,
                    issues=issues,
                ),
            )
            continue
        submissions.append(
            TelemetrySubmission(
                index=index,
                command=item.to_command(),
                event_id=item.event_id,
                correlation_id=item.correlation_id,
            )
        )

    service: TelemetryIngestionService = request.app.state.telemetry_ingestion_service
    timeout_seconds: float = request.app.state.telemetry_request_timeout_seconds
    try:
        async with asyncio.timeout(timeout_seconds):
            ingestion = await service.ingest_batch(
                submissions,
                origin=telemetry_origin(request),
            )
    except IdentityLookupUnavailableError:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "IDENTITY_LOOKUP_UNAVAILABLE",
            "pole identity validation is temporarily unavailable; retry the complete batch",
            retryable=True,
        )
    except TelemetryQueueUnavailableError:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "TELEMETRY_QUEUE_UNAVAILABLE",
            "telemetry batch was not queued; retry the complete batch with the same event IDs",
            retryable=True,
        )
    except TimeoutError:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "INGESTION_TIMEOUT",
            "telemetry batch exceeded its processing deadline; retry with the same event IDs",
            retryable=True,
        )

    for index, receipt in ingestion.receipts:
        results_by_index[index] = TelemetryBatchItemResult(
            index=index,
            status="accepted",
            event_id=receipt.event_id,
            correlation_id=receipt.correlation_id,
            received_at=receipt.received_at,
            stream_id=receipt.stream_id,
        )
    for rejection in ingestion.rejections:
        results_by_index[rejection.index] = TelemetryBatchItemResult(
            index=rejection.index,
            status="rejected",
            error=TelemetryBatchItemError(
                code=rejection.code,
                message=rejection.message,
                retryable=rejection.retryable,
            ),
        )

    results = [results_by_index[index] for index in range(len(payload.items))]
    accepted = sum(item.status == "accepted" for item in results)
    rejected = len(results) - accepted
    batch_status = "accepted" if rejected == 0 else "rejected" if accepted == 0 else "partial"
    response = TelemetryBatchResponse(
        status=batch_status,
        accepted=accepted,
        rejected=rejected,
        results=results,
    )
    logger.info(
        json.dumps(
            {
                "event": "telemetry_batch_ingestion",
                "outcome": batch_status,
                "item_count": len(results),
                "accepted": accepted,
                "rejected": rejected,
            }
        )
    )
    if rejected:
        return JSONResponse(
            status_code=status.HTTP_207_MULTI_STATUS,
            content=response.model_dump(mode="json"),
        )
    return response
