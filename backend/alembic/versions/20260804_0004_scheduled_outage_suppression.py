"""Add scheduled outages and auditable incident suppression.

Revision ID: 20260804_0004
Revises: 20260804_0003
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260804_0004"
down_revision: str | None = "20260804_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def text_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.create_table(
        "scheduled_outages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("outage_id", sa.String(length=64), nullable=False),
        sa.Column(
            "scope",
            text_enum(
                "scheduled_outage_scope",
                "SPAN",
                "DISTRIBUTION_TRANSFORMER",
                "FEEDER",
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.String(length=160), nullable=False),
        sa.Column("starts_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name="valid_time_window"),
        sa.CheckConstraint("length(outage_id) > 0", name="nonempty_outage_id"),
        sa.CheckConstraint("length(scope_id) > 0", name="nonempty_scope_id"),
        sa.CheckConstraint("length(source) > 0", name="nonempty_source"),
        sa.CheckConstraint("length(reason) > 0", name="nonempty_reason"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outage_id", name="uq_scheduled_outages_outage_id"),
    )
    op.create_index(
        "ix_scheduled_outages_scope_window",
        "scheduled_outages",
        ["scope", "scope_id", "starts_at", "ends_at"],
    )

    op.drop_constraint(
        op.f("ck_incidents_suspected_asset_type"), "incidents", type_="check"
    )
    op.create_check_constraint(
        "suspected_asset_type",
        "incidents",
        "suspected_asset_type IN "
        "('DEVICE', 'SPAN', 'DISTRIBUTION_TRANSFORMER', 'FEEDER')",
    )
    op.drop_constraint(
        op.f("ck_incidents_localization_precision"), "incidents", type_="check"
    )
    op.create_check_constraint(
        "localization_precision",
        "incidents",
        "precision IN "
        "('POLE_LEVEL', 'EXACT_SPAN', 'PROBABLE_SPAN', 'CORRIDOR', "
        "'DT_LEVEL', 'FEEDER_LEVEL')",
    )

    op.add_column("incidents", sa.Column("suppression_reason", sa.Text()))
    op.add_column("incidents", sa.Column("suppression_source", sa.String(length=120)))
    op.add_column("incidents", sa.Column("suppression_external_id", sa.String(length=64)))
    op.create_check_constraint(
        "suppressed_incident_has_reason",
        "incidents",
        "status <> 'SUPPRESSED' OR suppression_reason IS NOT NULL",
    )

    op.drop_index("uq_incidents_active_fingerprint", table_name="incidents")
    op.create_index(
        "uq_incidents_current_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('ACTIVE', 'SUPPRESSED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_incidents_current_fingerprint", table_name="incidents")
    op.create_index(
        "uq_incidents_active_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_constraint(
        op.f("ck_incidents_suppressed_incident_has_reason"),
        "incidents",
        type_="check",
    )
    op.drop_column("incidents", "suppression_external_id")
    op.drop_column("incidents", "suppression_source")
    op.drop_column("incidents", "suppression_reason")

    op.drop_constraint(
        op.f("ck_incidents_localization_precision"), "incidents", type_="check"
    )
    op.create_check_constraint(
        "localization_precision",
        "incidents",
        "precision IN "
        "('EXACT_SPAN', 'PROBABLE_SPAN', 'CORRIDOR', 'DT_LEVEL', 'FEEDER_LEVEL')",
    )
    op.drop_constraint(
        op.f("ck_incidents_suspected_asset_type"), "incidents", type_="check"
    )
    op.create_check_constraint(
        "suspected_asset_type",
        "incidents",
        "suspected_asset_type IN ('SPAN', 'DISTRIBUTION_TRANSFORMER', 'FEEDER')",
    )

    op.drop_index("ix_scheduled_outages_scope_window", table_name="scheduled_outages")
    op.drop_table("scheduled_outages")
