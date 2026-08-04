from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from propel.api.routes.telemetry import error_response
from propel.api.schemas.simulator import (
    GeneratedNetworkManifestResponse,
    InjectFixedFaultRequest,
    SimulatedFaultResponse,
    SimulatorResetResponse,
)
from propel.api.schemas.telemetry import ErrorResponse
from propel.infra.simulator import (
    ActiveSimulatorFaultError,
    InvalidSimulatorNoiseError,
    InvalidSimulatorSpanError,
    MissingSimulatorDeviceError,
    NoSimulatorTelemetryError,
    PostgresSimulatorService,
    SimulatorDatasetNotFoundError,
    SimulatorFaultNotFoundError,
    SimulatorStoreUnavailableError,
    SimulatorTelemetryUnavailableError,
)

router = APIRouter(prefix="/api/simulator", tags=["simulator"])
ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


def simulator_service(request: Request) -> PostgresSimulatorService:
    return request.app.state.simulator_service


def simulator_unavailable(code: str, message: str) -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        code,
        message,
        retryable=True,
    )


@router.get(
    "/manifest",
    response_model=GeneratedNetworkManifestResponse,
    responses=ERROR_RESPONSES,
)
async def generated_manifest(
    request: Request,
    dataset_id: str | None = None,
) -> GeneratedNetworkManifestResponse | JSONResponse:
    try:
        manifest = await simulator_service(request).generated_manifest(dataset_id)
    except SimulatorDatasetNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "SIMULATOR_DATASET_NOT_FOUND",
            "generated simulator dataset does not exist",
            retryable=False,
        )
    except SimulatorStoreUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_STORE_UNAVAILABLE",
            "simulator state is temporarily unavailable",
        )
    return GeneratedNetworkManifestResponse.model_validate(manifest)


@router.post(
    "/faults",
    response_model=SimulatedFaultResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def inject_fault(
    payload: InjectFixedFaultRequest,
    request: Request,
) -> SimulatedFaultResponse | JSONResponse:
    try:
        fault = await simulator_service(request).inject_fixed_fault(
            fault_type=payload.fault_type,
            dt_id=payload.dt_id,
            parent_pole_id=payload.parent_pole_id,
            child_pole_id=payload.child_pole_id,
            feeder_id=payload.feeder_id,
            missing_device_pole_ids=tuple(payload.missing_device_pole_ids),
            omit_loss_pole_ids=tuple(payload.omit_loss_pole_ids),
        )
    except ActiveSimulatorFaultError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "SIMULATOR_FAULT_OVERLAP",
            "the requested scope overlaps an active simulated fault",
            retryable=False,
        )
    except InvalidSimulatorSpanError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "INVALID_SIMULATOR_SPAN",
            "the requested surveyed span does not exist",
            retryable=False,
        )
    except InvalidSimulatorNoiseError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "INVALID_SIMULATOR_NOISE",
            "noise poles must be unique affected poles and cannot suppress every loss message",
            retryable=False,
        )
    except MissingSimulatorDeviceError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "SIMULATOR_DEVICE_MISSING",
            "every simulated pole must have an active device",
            retryable=False,
        )
    except NoSimulatorTelemetryError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "SIMULATOR_NO_TELEMETRY",
            "the requested fault has no report-capable simulator devices",
            retryable=False,
        )
    except SimulatorTelemetryUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_TELEMETRY_UNAVAILABLE",
            "simulator telemetry could not enter the public ingestion endpoint",
        )
    except SimulatorStoreUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_STORE_UNAVAILABLE",
            "simulator state is temporarily unavailable",
        )
    return SimulatedFaultResponse.model_validate(fault)


@router.post(
    "/faults/{fault_id}/repair",
    response_model=SimulatedFaultResponse,
    responses=ERROR_RESPONSES,
)
async def repair_fault(fault_id: UUID, request: Request) -> SimulatedFaultResponse | JSONResponse:
    try:
        fault = await simulator_service(request).repair_fault(fault_id)
    except SimulatorFaultNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "SIMULATOR_FAULT_NOT_FOUND",
            "simulated fault does not exist",
            retryable=False,
        )
    except MissingSimulatorDeviceError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "SIMULATOR_DEVICE_MISSING",
            "every simulated pole must have an active device",
            retryable=False,
        )
    except SimulatorTelemetryUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_TELEMETRY_UNAVAILABLE",
            "repair telemetry could not enter the public ingestion endpoint",
        )
    except SimulatorStoreUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_STORE_UNAVAILABLE",
            "simulator state is temporarily unavailable",
        )
    return SimulatedFaultResponse.model_validate(fault)


@router.post(
    "/reset",
    response_model=SimulatorResetResponse,
    responses=ERROR_RESPONSES,
)
async def reset_simulator(request: Request) -> SimulatorResetResponse | JSONResponse:
    try:
        repaired_faults = await simulator_service(request).reset()
    except SimulatorTelemetryUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_TELEMETRY_UNAVAILABLE",
            "reset telemetry could not enter the public ingestion endpoint",
        )
    except MissingSimulatorDeviceError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "SIMULATOR_DEVICE_MISSING",
            "every simulated pole must have an active device",
            retryable=False,
        )
    except SimulatorStoreUnavailableError:
        return simulator_unavailable(
            "SIMULATOR_STORE_UNAVAILABLE",
            "simulator state is temporarily unavailable",
        )
    return SimulatorResetResponse(
        repaired_faults=[SimulatedFaultResponse.model_validate(item) for item in repaired_faults]
    )
