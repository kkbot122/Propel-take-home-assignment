from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from propel.api.routes.telemetry import error_response
from propel.api.schemas.simulator import (
    InjectFixedFaultRequest,
    SimulatedFaultResponse,
    SimulatorResetResponse,
)
from propel.api.schemas.telemetry import ErrorResponse
from propel.infra.simulator import (
    ActiveSimulatorFaultError,
    InvalidSimulatorSpanError,
    MissingSimulatorDeviceError,
    PostgresSimulatorService,
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
        )
    except ActiveSimulatorFaultError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "ACTIVE_SIMULATOR_FAULT",
            "the transformer already has an active simulated fault",
            retryable=False,
        )
    except InvalidSimulatorSpanError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "INVALID_SIMULATOR_SPAN",
            "the requested surveyed span does not exist",
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
