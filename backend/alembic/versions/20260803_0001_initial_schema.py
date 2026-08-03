"""Create the minimum outage-localization schema.

Revision ID: 20260803_0001
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260803_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def text_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.create_table(
        "substations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("substation_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("pin_code", sa.String(length=6), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("substation_id", name="uq_substations_substation_id"),
    )
    op.create_table(
        "feeders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("feeder_id", sa.String(length=64), nullable=False),
        sa.Column("substation_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["substation_id"], ["substations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feeder_id", name="uq_feeders_feeder_id"),
    )
    op.create_index("ix_feeders_substation_id", "feeders", ["substation_id"])
    op.create_table(
        "distribution_transformers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dt_id", sa.String(length=64), nullable=False),
        sa.Column("feeder_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("pin_code", sa.String(length=6), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        sa.ForeignKeyConstraint(["feeder_id"], ["feeders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dt_id", name="uq_distribution_transformers_dt_id"),
        sa.UniqueConstraint("id", "feeder_id", name="uq_distribution_transformers_id_feeder_id"),
    )
    op.create_index(
        "ix_distribution_transformers_feeder_id", "distribution_transformers", ["feeder_id"]
    )
    op.create_table(
        "poles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("pole_id", sa.String(length=64), nullable=False),
        sa.Column("dt_id", sa.BigInteger(), nullable=False),
        sa.Column("feeder_id", sa.BigInteger(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("pin_code", sa.String(length=6), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        sa.ForeignKeyConstraint(
            ["dt_id", "feeder_id"],
            ["distribution_transformers.id", "distribution_transformers.feeder_id"],
            name="fk_poles_dt_id_feeder_id_distribution_transformers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["feeder_id"], ["feeders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "dt_id", name="uq_poles_id_dt_id"),
        sa.UniqueConstraint("pole_id", name="uq_poles_pole_id"),
    )
    op.create_index("ix_poles_dt_id", "poles", ["dt_id"])
    op.create_index("ix_poles_feeder_id", "poles", ["feeder_id"])
    op.create_table(
        "devices",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("installed_firmware", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", name="uq_devices_device_id"),
    )
    op.create_table(
        "device_bindings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("pole_id", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("valid_to", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_time_range"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pole_id"], ["poles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_device_bindings_active_device",
        "device_bindings",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        "uq_device_bindings_active_pole",
        "device_bindings",
        ["pole_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_table(
        "topology_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dt_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_pole_id", sa.BigInteger(), nullable=True),
        sa.Column("child_pole_id", sa.BigInteger(), nullable=False),
        sa.Column("source", text_enum("topology_source", "SURVEYED", "INFERRED"), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("edge_confidence", sa.Float(), nullable=False),
        sa.Column("inference_version", sa.String(length=64), nullable=True),
        sa.Column("topology_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "parent_pole_id IS NULL OR parent_pole_id <> child_pole_id",
            name="different_parent_and_child",
        ),
        sa.CheckConstraint("topology_version >= 1", name="positive_topology_version"),
        sa.CheckConstraint("distance_m >= 0", name="nonnegative_distance"),
        sa.CheckConstraint("edge_confidence BETWEEN 0 AND 1", name="confidence_range"),
        sa.ForeignKeyConstraint(
            ["child_pole_id", "dt_id"],
            ["poles.id", "poles.dt_id"],
            name="fk_topology_edges_child_pole_id_dt_id_poles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_pole_id", "dt_id"],
            ["poles.id", "poles.dt_id"],
            name="fk_topology_edges_parent_pole_id_dt_id_poles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["dt_id"], ["distribution_transformers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dt_id", "child_pole_id", "topology_version", name="uq_topology_edges_child_version"
        ),
    )
    op.create_index("ix_topology_edges_dt_id", "topology_edges", ["dt_id"])
    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "event_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "correlation_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("pole_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "event_type",
            text_enum("telemetry_event_type", "heartbeat", "power_lost", "power_restored", "boot"),
            nullable=False,
        ),
        sa.Column("energized", sa.Boolean(), nullable=False),
        sa.Column("device_timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("boot_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("battery_mv", sa.Integer(), nullable=True),
        sa.Column("rssi", sa.Integer(), nullable=True),
        sa.Column("firmware", sa.String(length=32), nullable=False),
        sa.Column(
            "processing_outcome",
            text_enum(
                "processing_outcome", "accepted", "duplicate", "stale", "invalid", "quarantined"
            ),
            nullable=False,
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 0", name="nonnegative_sequence"),
        sa.CheckConstraint(
            "battery_mv IS NULL OR battery_mv BETWEEN 0 AND 10000", name="battery_range"
        ),
        sa.CheckConstraint("rssi IS NULL OR rssi BETWEEN -200 AND 0", name="rssi_range"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pole_id"], ["poles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_telemetry_events_event_id"),
    )
    op.create_index(
        "ix_telemetry_events_device_id_received_at",
        "telemetry_events",
        ["device_id", "received_at"],
    )
    op.create_index(
        "ix_telemetry_events_pole_id_received_at",
        "telemetry_events",
        ["pole_id", "received_at"],
    )
    op.create_table(
        "pole_states",
        sa.Column("pole_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            text_enum("pole_status", "LIVE", "DARK", "STALE", "UNKNOWN", "NO_DEVICE"),
            nullable=False,
        ),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column("device_sequence", sa.BigInteger(), nullable=True),
        sa.Column("device_timestamp", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("received_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("firmware", sa.String(length=32), nullable=True),
        sa.Column("battery_mv", sa.Integer(), nullable=True),
        sa.Column("rssi", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "device_sequence IS NULL OR device_sequence >= 0", name="nonnegative_sequence"
        ),
        sa.ForeignKeyConstraint(["pole_id"], ["poles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_event_id"], ["telemetry_events.event_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("pole_id"),
    )
    op.create_table(
        "device_health",
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            text_enum("device_health_status", "HEALTHY", "STALE", "UNKNOWN"),
            nullable=False,
        ),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("boot_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=True),
        sa.Column("firmware", sa.String(length=32), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "last_sequence IS NULL OR last_sequence >= 0", name="nonnegative_sequence"
        ),
        sa.CheckConstraint("boot_generation >= 0", name="nonnegative_boot_generation"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_table(
        "incidents",
        sa.Column(
            "incident_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("fingerprint", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            text_enum("incident_status", "ACTIVE", "RESOLVED", "SUPPRESSED"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            text_enum(
                "fault_class",
                "SPAN_FAULT",
                "DT_FAULT",
                "FEEDER_FAULT",
                "SENSOR_ANOMALY",
                "SCHEDULED_OUTAGE",
                "UNCONFIRMED_OUTAGE",
            ),
            nullable=False,
        ),
        sa.Column(
            "suspected_asset_type",
            text_enum("suspected_asset_type", "SPAN", "DISTRIBUTION_TRANSFORMER", "FEEDER"),
            nullable=False,
        ),
        sa.Column("suspected_asset_id", sa.String(length=160), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("pin_code", sa.String(length=6), nullable=True),
        sa.Column("affected_pole_count", sa.Integer(), nullable=False),
        sa.Column(
            "precision",
            text_enum(
                "localization_precision",
                "EXACT_SPAN",
                "PROBABLE_SPAN",
                "CORRIDOR",
                "DT_LEVEL",
                "FEEDER_LEVEL",
            ),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Integer(), nullable=False),
        sa.Column("confidence_reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("affected_pole_count >= 0", name="nonnegative_affected_pole_count"),
        sa.CheckConstraint("confidence_score BETWEEN 0 AND 100", name="confidence_score_range"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        sa.PrimaryKeyConstraint("incident_id"),
    )
    op.create_index(
        "uq_incidents_active_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index("ix_incidents_status_detected_at", "incidents", ["status", "detected_at"])
    op.create_table(
        "incident_poles",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("pole_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_observed_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.incident_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pole_id"], ["poles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("incident_id", "pole_id"),
    )
    op.create_index("ix_incident_poles_pole_id", "incident_poles", ["pole_id"])
    op.create_table(
        "tickets",
        sa.Column(
            "ticket_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            text_enum(
                "ticket_status",
                "DETECTED",
                "ACKNOWLEDGED",
                "CREW_ASSIGNED",
                "RESOLVED",
                "VERIFIED",
                "CLOSED",
            ),
            nullable=False,
        ),
        sa.Column("assigned_crew", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolution_claimed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.incident_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("ticket_id"),
        sa.UniqueConstraint("incident_id", name="uq_tickets_incident_id"),
    )
    op.create_table(
        "ticket_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column(
            "from_status",
            text_enum(
                "ticket_event_from_status",
                "DETECTED",
                "ACKNOWLEDGED",
                "CREW_ASSIGNED",
                "RESOLVED",
                "VERIFIED",
                "CLOSED",
            ),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            text_enum(
                "ticket_event_to_status",
                "DETECTED",
                "ACKNOWLEDGED",
                "CREW_ASSIGNED",
                "RESOLVED",
                "VERIFIED",
                "CLOSED",
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_events_ticket_id_occurred_at",
        "ticket_events",
        ["ticket_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_events_ticket_id_occurred_at", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_table("tickets")
    op.drop_index("ix_incident_poles_pole_id", table_name="incident_poles")
    op.drop_table("incident_poles")
    op.drop_index("ix_incidents_status_detected_at", table_name="incidents")
    op.drop_index("uq_incidents_active_fingerprint", table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("device_health")
    op.drop_table("pole_states")
    op.drop_index("ix_telemetry_events_pole_id_received_at", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_device_id_received_at", table_name="telemetry_events")
    op.drop_table("telemetry_events")
    op.drop_index("ix_topology_edges_dt_id", table_name="topology_edges")
    op.drop_table("topology_edges")
    op.drop_index("uq_device_bindings_active_pole", table_name="device_bindings")
    op.drop_index("uq_device_bindings_active_device", table_name="device_bindings")
    op.drop_table("device_bindings")
    op.drop_table("devices")
    op.drop_index("ix_poles_feeder_id", table_name="poles")
    op.drop_index("ix_poles_dt_id", table_name="poles")
    op.drop_table("poles")
    op.drop_index("ix_distribution_transformers_feeder_id", table_name="distribution_transformers")
    op.drop_table("distribution_transformers")
    op.drop_index("ix_feeders_substation_id", table_name="feeders")
    op.drop_table("feeders")
    op.drop_table("substations")
