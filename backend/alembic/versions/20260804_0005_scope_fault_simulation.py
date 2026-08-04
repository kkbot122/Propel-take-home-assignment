"""Generalize simulated faults to DT and feeder scopes.

Revision ID: 20260804_0005
Revises: 20260804_0004
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0005"
down_revision: str | None = "20260804_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def text_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.add_column(
        "simulated_faults",
        sa.Column(
            "fault_type",
            text_enum(
                "simulator_fault_type",
                "SPAN_FAULT",
                "DT_FAULT",
                "FEEDER_FAULT",
            ),
            server_default="SPAN_FAULT",
            nullable=False,
        ),
    )
    op.add_column("simulated_faults", sa.Column("feeder_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_simulated_faults_feeder_id_feeders"),
        "simulated_faults",
        "feeders",
        ["feeder_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("simulated_faults", "dt_id", nullable=True)
    op.alter_column("simulated_faults", "parent_pole_id", nullable=True)
    op.alter_column("simulated_faults", "child_pole_id", nullable=True)
    op.drop_index("uq_simulated_faults_active_dt", table_name="simulated_faults")
    op.create_index(
        "uq_simulated_faults_single_active",
        "simulated_faults",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_check_constraint(
        "valid_scope",
        "simulated_faults",
        "(fault_type = 'SPAN_FAULT' AND dt_id IS NOT NULL AND feeder_id IS NULL "
        "AND parent_pole_id IS NOT NULL AND child_pole_id IS NOT NULL) OR "
        "(fault_type = 'DT_FAULT' AND dt_id IS NOT NULL AND feeder_id IS NULL "
        "AND parent_pole_id IS NULL AND child_pole_id IS NULL) OR "
        "(fault_type = 'FEEDER_FAULT' AND dt_id IS NULL AND feeder_id IS NOT NULL "
        "AND parent_pole_id IS NULL AND child_pole_id IS NULL)",
    )
    op.alter_column("simulated_faults", "fault_type", server_default=None)


def downgrade() -> None:
    op.execute("DELETE FROM simulated_faults WHERE fault_type <> 'SPAN_FAULT'")
    op.drop_constraint(
        op.f("ck_simulated_faults_valid_scope"),
        "simulated_faults",
        type_="check",
    )
    op.drop_index("uq_simulated_faults_single_active", table_name="simulated_faults")
    op.create_index(
        "uq_simulated_faults_active_dt",
        "simulated_faults",
        ["dt_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.alter_column("simulated_faults", "child_pole_id", nullable=False)
    op.alter_column("simulated_faults", "parent_pole_id", nullable=False)
    op.alter_column("simulated_faults", "dt_id", nullable=False)
    op.drop_constraint(
        op.f("fk_simulated_faults_feeder_id_feeders"),
        "simulated_faults",
        type_="foreignkey",
    )
    op.drop_column("simulated_faults", "feeder_id")
    op.drop_column("simulated_faults", "fault_type")
