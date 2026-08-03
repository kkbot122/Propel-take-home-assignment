from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from propel.domain.enums import (
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    PoleStatus,
    SuspectedAssetType,
    TicketStatus,
    TopologySource,
)


@dataclass(frozen=True, slots=True)
class IncidentTicketReference:
    incident_id: UUID
    ticket_id: UUID
    fingerprint: str


@dataclass(frozen=True, slots=True)
class IncidentView:
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
    affected_pole_ids: tuple[str, ...]
    precision: LocalizationPrecision
    confidence_score: int
    confidence_reason: str
    evidence: dict[str, Any]
    detected_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    ticket_id: UUID | None
    ticket_status: TicketStatus | None
    assigned_crew: str | None


@dataclass(frozen=True, slots=True)
class TicketEventView:
    from_status: TicketStatus | None
    to_status: TicketStatus
    actor: str
    reason: str | None
    occurred_at: datetime
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TicketView:
    ticket_id: UUID
    incident_id: UUID
    status: TicketStatus
    assigned_crew: str | None
    created_at: datetime
    updated_at: datetime
    resolution_claimed_at: datetime | None
    verified_at: datetime | None
    closed_at: datetime | None
    events: tuple[TicketEventView, ...]


@dataclass(frozen=True, slots=True)
class NetworkPoleView:
    pole_id: str
    dt_id: str
    latitude: float
    longitude: float
    pin_code: str
    state: PoleStatus
    state_received_at: datetime | None
    device_id: str | None


@dataclass(frozen=True, slots=True)
class NetworkSpanView:
    parent_pole_id: str | None
    child_pole_id: str
    source: TopologySource
    edge_confidence: float


@dataclass(frozen=True, slots=True)
class NetworkTopologyView:
    dt_id: str
    topology_version: int
    spans: tuple[NetworkSpanView, ...]
