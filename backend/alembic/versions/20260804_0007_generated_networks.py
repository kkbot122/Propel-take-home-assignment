"""Add generated dataset manifests and simulator-only ground truth.

Revision ID: 20260804_0007
Revises: 20260804_0006
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260804_0007"
down_revision: str | None = "20260804_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generated_datasets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("logical_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("seed >= 0", name=op.f("ck_generated_datasets_nonnegative_seed")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generated_datasets")),
        sa.UniqueConstraint("dataset_id", name=op.f("uq_generated_datasets_dataset_id")),
        sa.UniqueConstraint("logical_digest", name=op.f("uq_generated_datasets_logical_digest")),
    )
    op.create_table(
        "simulator_topology_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("dt_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_pole_id", sa.BigInteger(), nullable=True),
        sa.Column("child_pole_id", sa.BigInteger(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "parent_pole_id IS NULL OR parent_pole_id <> child_pole_id",
            name=op.f("ck_simulator_topology_edges_different_parent_and_child"),
        ),
        sa.CheckConstraint(
            "distance_m >= 0",
            name=op.f("ck_simulator_topology_edges_nonnegative_distance"),
        ),
        sa.ForeignKeyConstraint(
            ["child_pole_id", "dt_id"],
            ["poles.id", "poles.dt_id"],
            name=op.f("fk_simulator_topology_edges_child_dt_poles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["generated_datasets.id"],
            name=op.f("fk_simulator_topology_edges_dataset_id_generated_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dt_id"],
            ["distribution_transformers.id"],
            name=op.f("fk_simulator_topology_edges_dt_id_distribution_transformers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_pole_id", "dt_id"],
            ["poles.id", "poles.dt_id"],
            name=op.f("fk_simulator_topology_edges_parent_dt_poles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulator_topology_edges")),
        sa.UniqueConstraint(
            "dataset_id",
            "dt_id",
            "child_pole_id",
            name=op.f("uq_simulator_topology_edges_dataset_child"),
        ),
    )
    op.create_index(
        op.f("ix_simulator_topology_edges_dataset_dt"),
        "simulator_topology_edges",
        ["dataset_id", "dt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_simulator_topology_edges_dataset_dt"),
        table_name="simulator_topology_edges",
    )
    op.drop_table("simulator_topology_edges")
    op.drop_table("generated_datasets")
