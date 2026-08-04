from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from propel.domain.enums import (
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    PoleStatus,
    SuspectedAssetType,
    TicketStatus,
    TopologySource,
)

OperatorName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=120, strict=True, strip_whitespace=True),
]
Reason = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1_000, strict=True, strip_whitespace=True),
]


class AcknowledgeTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: OperatorName
    reason: Reason | None = None


class AssignTicketRequest(AcknowledgeTicketRequest):
    assigned_crew: OperatorName


class ResolveTicketRequest(AcknowledgeTicketRequest):
    pass


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    fingerprint: str
    status: IncidentStatus
    classification: FaultClass
    suspected_asset_type: SuspectedAssetType
    suspected_asset_id: str
    latitude: float
    longitude: float
    pin_code: str | None
    affected_pole_count: int
    affected_pole_ids: list[str]
    precision: LocalizationPrecision
    confidence_score: Annotated[int, Field(ge=0, le=100)]
    confidence_reason: str
    evidence: dict[str, Any]
    suppression_reason: str | None
    suppression_source: str | None
    suppression_external_id: str | None
    detected_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    ticket_id: UUID | None
    ticket_status: TicketStatus | None
    assigned_crew: str | None


class TicketEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: TicketStatus | None
    to_status: TicketStatus
    actor: str
    reason: str | None
    occurred_at: datetime
    details: dict[str, Any]


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    incident_id: UUID
    status: TicketStatus
    assigned_crew: str | None
    created_at: datetime
    updated_at: datetime
    resolution_claimed_at: datetime | None
    verified_at: datetime | None
    closed_at: datetime | None
    restoration_status: str | None
    remaining_dark_count: Annotated[int | None, Field(ge=0)]
    events: list[TicketEventResponse]


class NetworkPoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pole_id: str
    dt_id: str
    latitude: float
    longitude: float
    pin_code: str
    state: PoleStatus
    state_received_at: datetime | None
    device_id: str | None


class NetworkSpanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    parent_pole_id: str | None
    child_pole_id: str
    source: TopologySource
    edge_confidence: Annotated[float, Field(ge=0, le=1)]
    distance_m: Annotated[float, Field(ge=0)]
    inference_version: str | None


class NetworkTopologyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dt_id: str
    topology_version: int
    source: TopologySource | None
    quality_score: Annotated[float, Field(ge=0, le=1)]
    quality_tier: str
    quality_reasons: list[str]
    inference_version: str | None
    spans: list[NetworkSpanResponse]


class NetworkSubstationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    substation_id: str
    name: str
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    pin_code: str


class NetworkTransformerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dt_id: str
    name: str
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    pin_code: str


class NetworkOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feeder_id: str
    name: str
    substation: NetworkSubstationResponse
    transformers: list[NetworkTransformerResponse]
