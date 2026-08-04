from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    IncidentStatus,
    LocalizationPrecision,
    PoleStatus,
    ProcessingOutcome,
    SimulatorFaultStatus,
    SuspectedAssetType,
    TelemetryEventType,
    TelemetryOrigin,
    TicketStatus,
    TopologySource,
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def text_enum(enum_class: type[StrEnum], name: str) -> SqlEnum:
    return SqlEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Substation(Base):
    __tablename__ = "substations"
    __table_args__ = (
        UniqueConstraint("substation_id", name="uq_substations_substation_id"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    substation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    pin_code: Mapped[str] = mapped_column(String(6), nullable=False)


class Feeder(Base):
    __tablename__ = "feeders"
    __table_args__ = (UniqueConstraint("feeder_id", name="uq_feeders_feeder_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    feeder_id: Mapped[str] = mapped_column(String(64), nullable=False)
    substation_id: Mapped[int] = mapped_column(
        ForeignKey("substations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class DistributionTransformer(Base):
    __tablename__ = "distribution_transformers"
    __table_args__ = (
        UniqueConstraint("dt_id", name="uq_distribution_transformers_dt_id"),
        UniqueConstraint("id", "feeder_id", name="uq_distribution_transformers_id_feeder_id"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    feeder_id: Mapped[int] = mapped_column(
        ForeignKey("feeders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    pin_code: Mapped[str] = mapped_column(String(6), nullable=False)


class Pole(Base):
    __tablename__ = "poles"
    __table_args__ = (
        UniqueConstraint("pole_id", name="uq_poles_pole_id"),
        UniqueConstraint("id", "dt_id", name="uq_poles_id_dt_id"),
        ForeignKeyConstraint(
            ["dt_id", "feeder_id"],
            ["distribution_transformers.id", "distribution_transformers.feeder_id"],
            name="fk_poles_dt_id_feeder_id_distribution_transformers",
            ondelete="RESTRICT",
        ),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    pole_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dt_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    feeder_id: Mapped[int] = mapped_column(
        ForeignKey("feeders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    pin_code: Mapped[str] = mapped_column(String(6), nullable=False)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("device_id", name="uq_devices_device_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    installed_firmware: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class DeviceBinding(Base):
    __tablename__ = "device_bindings"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_time_range"),
        Index(
            "uq_device_bindings_active_device",
            "device_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
        Index(
            "uq_device_bindings_active_pole",
            "pole_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class TopologyEdge(Base):
    __tablename__ = "topology_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_pole_id", "dt_id"],
            ["poles.id", "poles.dt_id"],
            name="fk_topology_edges_parent_pole_id_dt_id_poles",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["child_pole_id", "dt_id"],
            ["poles.id", "poles.dt_id"],
            name="fk_topology_edges_child_pole_id_dt_id_poles",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "dt_id", "child_pole_id", "topology_version", name="uq_topology_edges_child_version"
        ),
        CheckConstraint(
            "parent_pole_id IS NULL OR parent_pole_id <> child_pole_id",
            name="different_parent_and_child",
        ),
        CheckConstraint("topology_version >= 1", name="positive_topology_version"),
        CheckConstraint("distance_m >= 0", name="nonnegative_distance"),
        CheckConstraint("edge_confidence BETWEEN 0 AND 1", name="confidence_range"),
        Index("ix_topology_edges_dt_id", "dt_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dt_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_transformers.id", ondelete="CASCADE"), nullable=False
    )
    parent_pole_id: Mapped[int | None] = mapped_column(BigInteger)
    child_pole_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[TopologySource] = mapped_column(
        text_enum(TopologySource, "topology_source"), nullable=False
    )
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    edge_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    inference_version: Mapped[str | None] = mapped_column(String(64))
    topology_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_telemetry_events_event_id"),
        CheckConstraint("sequence >= 0", name="nonnegative_sequence"),
        CheckConstraint(
            "battery_mv IS NULL OR battery_mv BETWEEN 0 AND 10000", name="battery_range"
        ),
        CheckConstraint("rssi IS NULL OR rssi BETWEEN -200 AND 0", name="rssi_range"),
        Index("ix_telemetry_events_device_id_received_at", "device_id", "received_at"),
        Index("ix_telemetry_events_pole_id_received_at", "pole_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        default=uuid4, nullable=False, server_default=text("gen_random_uuid()")
    )
    correlation_id: Mapped[UUID] = mapped_column(
        default=uuid4, nullable=False, server_default=text("gen_random_uuid()")
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[TelemetryEventType] = mapped_column(
        text_enum(TelemetryEventType, "telemetry_event_type"), nullable=False
    )
    energized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    device_timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    boot_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    battery_mv: Mapped[int | None] = mapped_column(Integer)
    rssi: Mapped[int | None] = mapped_column(Integer)
    firmware: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_outcome: Mapped[ProcessingOutcome] = mapped_column(
        text_enum(ProcessingOutcome, "processing_outcome"), nullable=False
    )
    origin: Mapped[TelemetryOrigin] = mapped_column(
        text_enum(TelemetryOrigin, "telemetry_origin"),
        nullable=False,
        server_default=TelemetryOrigin.DEVICE.value,
    )
    state_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class PoleState(Base):
    __tablename__ = "pole_states"
    __table_args__ = (
        CheckConstraint(
            "device_sequence IS NULL OR device_sequence >= 0", name="nonnegative_sequence"
        ),
    )

    pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[PoleStatus] = mapped_column(text_enum(PoleStatus, "pole_status"), nullable=False)
    source_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("telemetry_events.event_id", ondelete="RESTRICT")
    )
    device_sequence: Mapped[int | None] = mapped_column(BigInteger)
    device_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    received_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    firmware: Mapped[str | None] = mapped_column(String(32))
    battery_mv: Mapped[int | None] = mapped_column(Integer)
    rssi: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class DeviceHealth(Base):
    __tablename__ = "device_health"
    __table_args__ = (
        CheckConstraint("last_sequence IS NULL OR last_sequence >= 0", name="nonnegative_sequence"),
        CheckConstraint("boot_generation >= 0", name="nonnegative_boot_generation"),
        CheckConstraint(
            "battery_mv IS NULL OR battery_mv BETWEEN 0 AND 10000", name="battery_range"
        ),
        CheckConstraint("rssi IS NULL OR rssi BETWEEN -200 AND 0", name="rssi_range"),
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[DeviceHealthStatus] = mapped_column(
        text_enum(DeviceHealthStatus, "device_health_status"), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    boot_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_sequence: Mapped[int | None] = mapped_column(BigInteger)
    last_event_type: Mapped[TelemetryEventType | None] = mapped_column(
        text_enum(TelemetryEventType, "device_health_last_event_type")
    )
    last_device_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    firmware: Mapped[str | None] = mapped_column(String(32))
    battery_mv: Mapped[int | None] = mapped_column(Integer)
    rssi: Mapped[int | None] = mapped_column(Integer)
    status_reason: Mapped[str] = mapped_column(
        String(160), nullable=False, server_default="seeded_health"
    )
    can_report_power_loss: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("affected_pole_count >= 0", name="nonnegative_affected_pole_count"),
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="confidence_score_range"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index(
            "uq_incidents_active_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_incidents_status_detected_at", "status", "detected_at"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        text_enum(IncidentStatus, "incident_status"), nullable=False
    )
    classification: Mapped[FaultClass] = mapped_column(
        text_enum(FaultClass, "fault_class"), nullable=False
    )
    suspected_asset_type: Mapped[SuspectedAssetType] = mapped_column(
        text_enum(SuspectedAssetType, "suspected_asset_type"), nullable=False
    )
    suspected_asset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    pin_code: Mapped[str | None] = mapped_column(String(6))
    affected_pole_count: Mapped[int] = mapped_column(Integer, nullable=False)
    precision: Mapped[LocalizationPrecision] = mapped_column(
        text_enum(LocalizationPrecision, "localization_precision"), nullable=False
    )
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    detected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class IncidentPole(Base):
    __tablename__ = "incident_poles"
    __table_args__ = (Index("ix_incident_poles_pole_id", "pole_id"),)

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.incident_id", ondelete="CASCADE"), primary_key=True
    )
    pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="RESTRICT"), primary_key=True
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("incident_id", name="uq_tickets_incident_id"),
        CheckConstraint(
            "remaining_dark_count IS NULL OR remaining_dark_count >= 0",
            name="nonnegative_remaining_dark_count",
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.incident_id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        text_enum(TicketStatus, "ticket_status"), nullable=False
    )
    assigned_crew: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    resolution_claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    restoration_status: Mapped[str | None] = mapped_column(String(64))
    remaining_dark_count: Mapped[int | None] = mapped_column(Integer)


class TicketRestorationPole(Base):
    __tablename__ = "ticket_restoration_poles"
    __table_args__ = (
        CheckConstraint(
            "eligible OR exclusion_reason IS NOT NULL",
            name="excluded_pole_has_reason",
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"), primary_key=True
    )
    pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="RESTRICT"), primary_key=True
    )
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_boundary_child: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(64))
    frozen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    __table_args__ = (Index("ix_ticket_events_ticket_id_occurred_at", "ticket_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.ticket_id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[TicketStatus | None] = mapped_column(
        text_enum(TicketStatus, "ticket_event_from_status")
    )
    to_status: Mapped[TicketStatus] = mapped_column(
        text_enum(TicketStatus, "ticket_event_to_status"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class SimulatedFault(Base):
    __tablename__ = "simulated_faults"
    __table_args__ = (
        Index(
            "uq_simulated_faults_active_dt",
            "dt_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    fault_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    dt_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_transformers.id", ondelete="RESTRICT"), nullable=False
    )
    parent_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="RESTRICT"), nullable=False
    )
    child_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SimulatorFaultStatus] = mapped_column(
        text_enum(SimulatorFaultStatus, "simulator_fault_status"), nullable=False
    )
    deenergized_pole_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    injected_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    injection_telemetry_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    repaired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
