from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from propel.api.routes.telemetry import error_response
from propel.api.schemas.incidents import (
    AcknowledgeTicketRequest,
    AssignTicketRequest,
    IncidentResponse,
    NetworkPoleResponse,
    NetworkTopologyResponse,
    ResolveTicketRequest,
    TicketResponse,
)
from propel.api.schemas.telemetry import ErrorResponse
from propel.domain.enums import IncidentStatus, TicketStatus
from propel.incidents.workflow import (
    AutomaticTransitionOnlyError,
    InvalidTicketTransitionError,
)
from propel.infra.incidents import (
    IncidentNotFoundError,
    IncidentStoreUnavailableError,
    NetworkTransformerNotFoundError,
    PostgresIncidentService,
    TicketNotFoundError,
)

router = APIRouter(prefix="/api", tags=["operations"])
ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


def incident_service(request: Request) -> PostgresIncidentService:
    return request.app.state.incident_service


def unavailable_response() -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "INCIDENT_STORE_UNAVAILABLE",
        "incident and ticket data is temporarily unavailable",
        retryable=True,
    )


@router.get(
    "/incidents",
    response_model=list[IncidentResponse],
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse}},
)
async def list_incidents(
    request: Request,
    incident_status: Annotated[IncidentStatus, Query(alias="status")] = IncidentStatus.ACTIVE,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[IncidentResponse] | JSONResponse:
    try:
        views = await incident_service(request).list_incidents(
            status=incident_status,
            limit=limit,
        )
    except IncidentStoreUnavailableError:
        return unavailable_response()
    return [IncidentResponse.model_validate(view) for view in views]


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentResponse,
    responses=ERROR_RESPONSES,
)
async def get_incident(incident_id: UUID, request: Request) -> IncidentResponse | JSONResponse:
    try:
        view = await incident_service(request).get_incident(incident_id)
    except IncidentNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "INCIDENT_NOT_FOUND",
            "incident does not exist",
            retryable=False,
        )
    except IncidentStoreUnavailableError:
        return unavailable_response()
    return IncidentResponse.model_validate(view)


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    responses=ERROR_RESPONSES,
)
async def get_ticket(ticket_id: UUID, request: Request) -> TicketResponse | JSONResponse:
    try:
        view = await incident_service(request).get_ticket(ticket_id)
    except TicketNotFoundError:
        return ticket_not_found_response()
    except IncidentStoreUnavailableError:
        return unavailable_response()
    return TicketResponse.model_validate(view)


def ticket_not_found_response() -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        "TICKET_NOT_FOUND",
        "ticket does not exist",
        retryable=False,
    )


async def apply_ticket_transition(
    request: Request,
    ticket_id: UUID,
    requested_status: TicketStatus,
    payload: AcknowledgeTicketRequest,
    *,
    assigned_crew: str | None = None,
) -> TicketResponse | JSONResponse:
    try:
        view = await incident_service(request).transition_ticket(
            ticket_id,
            requested_status,
            actor=payload.actor,
            reason=payload.reason,
            assigned_crew=assigned_crew,
        )
    except TicketNotFoundError:
        return ticket_not_found_response()
    except InvalidTicketTransitionError as error:
        return error_response(
            status.HTTP_409_CONFLICT,
            "INVALID_TICKET_TRANSITION",
            str(error),
            retryable=False,
        )
    except AutomaticTransitionOnlyError as error:
        return automatic_transition_response(error.requested)
    except IncidentStoreUnavailableError:
        return unavailable_response()
    return TicketResponse.model_validate(view)


@router.post(
    "/tickets/{ticket_id}/acknowledge",
    response_model=TicketResponse,
    responses=ERROR_RESPONSES,
)
async def acknowledge_ticket(
    ticket_id: UUID,
    payload: AcknowledgeTicketRequest,
    request: Request,
) -> TicketResponse | JSONResponse:
    return await apply_ticket_transition(
        request,
        ticket_id,
        TicketStatus.ACKNOWLEDGED,
        payload,
    )


@router.post(
    "/tickets/{ticket_id}/assign",
    response_model=TicketResponse,
    responses=ERROR_RESPONSES,
)
async def assign_ticket(
    ticket_id: UUID,
    payload: AssignTicketRequest,
    request: Request,
) -> TicketResponse | JSONResponse:
    return await apply_ticket_transition(
        request,
        ticket_id,
        TicketStatus.CREW_ASSIGNED,
        payload,
        assigned_crew=payload.assigned_crew,
    )


@router.post(
    "/tickets/{ticket_id}/resolve",
    response_model=TicketResponse,
    responses=ERROR_RESPONSES,
)
async def resolve_ticket(
    ticket_id: UUID,
    payload: ResolveTicketRequest,
    request: Request,
) -> TicketResponse | JSONResponse:
    return await apply_ticket_transition(
        request,
        ticket_id,
        TicketStatus.RESOLVED,
        payload,
    )


def automatic_transition_response(requested: TicketStatus) -> JSONResponse:
    return error_response(
        status.HTTP_403_FORBIDDEN,
        "AUTOMATIC_TRANSITION_ONLY",
        f"{requested.value} requires fresh telemetry verification",
        retryable=False,
    )


@router.post(
    "/tickets/{ticket_id}/verify",
    response_model=ErrorResponse,
    status_code=status.HTTP_403_FORBIDDEN,
)
async def reject_manual_verification(
    ticket_id: UUID,
    payload: AcknowledgeTicketRequest,
) -> JSONResponse:
    del ticket_id, payload
    return automatic_transition_response(TicketStatus.VERIFIED)


@router.post(
    "/tickets/{ticket_id}/close",
    response_model=ErrorResponse,
    status_code=status.HTTP_403_FORBIDDEN,
)
async def reject_manual_closure(
    ticket_id: UUID,
    payload: AcknowledgeTicketRequest,
) -> JSONResponse:
    del ticket_id, payload
    return automatic_transition_response(TicketStatus.CLOSED)


@router.get(
    "/network/poles",
    response_model=list[NetworkPoleResponse],
    responses=ERROR_RESPONSES,
)
async def list_network_poles(
    request: Request,
    dt_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> list[NetworkPoleResponse] | JSONResponse:
    try:
        poles = await incident_service(request).list_network_poles(dt_id)
    except NetworkTransformerNotFoundError:
        return transformer_not_found_response(dt_id)
    except IncidentStoreUnavailableError:
        return unavailable_response()
    return [NetworkPoleResponse.model_validate(pole) for pole in poles]


@router.get(
    "/network/topology/{dt_id}",
    response_model=NetworkTopologyResponse,
    responses=ERROR_RESPONSES,
)
async def get_network_topology(
    dt_id: str,
    request: Request,
) -> NetworkTopologyResponse | JSONResponse:
    try:
        topology = await incident_service(request).get_network_topology(dt_id)
    except NetworkTransformerNotFoundError:
        return transformer_not_found_response(dt_id)
    except IncidentStoreUnavailableError:
        return unavailable_response()
    return NetworkTopologyResponse.model_validate(topology)


def transformer_not_found_response(dt_id: str) -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        "TRANSFORMER_NOT_FOUND",
        f"distribution transformer {dt_id} does not exist",
        retryable=False,
    )
