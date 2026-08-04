"""Allow multiple independent active simulated faults.

Revision ID: 20260804_0006
Revises: 20260804_0005
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260804_0006"
down_revision: str | None = "20260804_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_simulated_faults_single_active", table_name="simulated_faults")
    op.create_index(
        "ix_simulated_faults_active_status",
        "simulated_faults",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE simulated_faults SET status = 'REPAIRED', repaired_at = NOW() "
        "WHERE status = 'ACTIVE' AND fault_id NOT IN "
        "(SELECT fault_id FROM simulated_faults WHERE status = 'ACTIVE' "
        "ORDER BY injected_at, fault_id LIMIT 1)"
    )
    op.drop_index("ix_simulated_faults_active_status", table_name="simulated_faults")
    op.create_index(
        "uq_simulated_faults_single_active",
        "simulated_faults",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
