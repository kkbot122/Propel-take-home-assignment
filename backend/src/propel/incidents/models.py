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
    ticket_id: UUID | None
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
    suppression_reason: str | None
    suppression_source: str | None
    suppression_external_id: str | None
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
    restoration_status: str | None
    remaining_dark_count: int | None
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
    distance_m: float
    inference_version: str | None


@dataclass(frozen=True, slots=True)
class NetworkTopologyView:
    dt_id: str
    topology_version: int
    source: TopologySource | None
    quality_score: float
    quality_tier: str
    quality_reasons: tuple[str, ...]
    inference_version: str | None
    spans: tuple[NetworkSpanView, ...]


@dataclass(frozen=True, slots=True)
class NetworkSubstationView:
    substation_id: str
    name: str
    latitude: float
    longitude: float
    pin_code: str


@dataclass(frozen=True, slots=True)
class NetworkTransformerView:
    dt_id: str
    name: str
    latitude: float
    longitude: float
    pin_code: str


@dataclass(frozen=True, slots=True)
class NetworkOverviewView:
    feeder_id: str
    name: str
    substation: NetworkSubstationView
    transformers: tuple[NetworkTransformerView, ...]


@dataclass(frozen=True, slots=True)
class NetworkFeederView:
    feeder_id: str
    name: str
    substation_id: str


@dataclass(frozen=True, slots=True)
class NetworkSubdivisionTransformerView:
    dt_id: str
    feeder_id: str
    name: str
    latitude: float
    longitude: float
    pin_code: str


@dataclass(frozen=True, slots=True)
class NetworkBoundsView:
    south: float
    west: float
    north: float
    east: float


@dataclass(frozen=True, slots=True)
class NetworkSubdivisionView:
    dataset_id: str
    generator_version: str
    name: str
    neighborhoods: tuple[str, ...]
    bounds: NetworkBoundsView
    substations: tuple[NetworkSubstationView, ...]
    feeders: tuple[NetworkFeederView, ...]
    transformers: tuple[NetworkSubdivisionTransformerView, ...]
    topologies: tuple[NetworkTopologyView, ...]
