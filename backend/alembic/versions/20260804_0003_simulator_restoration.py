"""Add simulator state and telemetry-based restoration evidence.

Revision ID: 20260804_0003
Revises: 20260804_0002
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260804_0003"
down_revision: str | None = "20260804_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def text_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.add_column(
        "telemetry_events",
        sa.Column(
            "origin",
            text_enum("telemetry_origin", "DEVICE", "SIMULATOR"),
            server_default="DEVICE",
            nullable=False,
        ),
    )
    op.add_column("tickets", sa.Column("restoration_status", sa.String(length=64)))
    op.add_column("tickets", sa.Column("remaining_dark_count", sa.Integer()))
    op.create_check_constraint(
        "ck_tickets_nonnegative_remaining_dark_count",
        "tickets",
        "remaining_dark_count IS NULL OR remaining_dark_count >= 0",
    )
    op.create_table(
        "ticket_restoration_poles",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("pole_id", sa.BigInteger(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("is_boundary_child", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(length=64)),
        sa.Column("frozen_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "eligible OR exclusion_reason IS NOT NULL",
            name="excluded_pole_has_reason",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.ticket_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pole_id"], ["poles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("ticket_id", "pole_id"),
    )
    op.create_table(
        "simulated_faults",
        sa.Column(
            "fault_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("dt_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_pole_id", sa.BigInteger(), nullable=False),
        sa.Column("child_pole_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            text_enum("simulator_fault_status", "ACTIVE", "REPAIRED"),
            nullable=False,
        ),
        sa.Column(
            "deenergized_pole_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("injected_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("injection_telemetry_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("repaired_at", postgresql.TIMESTAMP(timezone=True)),
        sa.ForeignKeyConstraint(["dt_id"], ["distribution_transformers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_pole_id"], ["poles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["child_pole_id"], ["poles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("fault_id"),
    )
    op.create_index(
        "uq_simulated_faults_active_dt",
        "simulated_faults",
        ["dt_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_simulated_faults_active_dt", table_name="simulated_faults")
    op.drop_table("simulated_faults")
    op.drop_table("ticket_restoration_poles")
    op.drop_constraint("ck_tickets_nonnegative_remaining_dark_count", "tickets", type_="check")
    op.drop_column("tickets", "remaining_dark_count")
    op.drop_column("tickets", "restoration_status")
    op.drop_column("telemetry_events", "origin")
