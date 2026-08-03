"""Add worker processing and device-health metadata.

Revision ID: 20260804_0002
Revises: 20260803_0001
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260804_0002"
down_revision: str | None = "20260803_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telemetry_events",
        sa.Column("state_changed", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("device_health", sa.Column("battery_mv", sa.Integer(), nullable=True))
    op.add_column("device_health", sa.Column("rssi", sa.Integer(), nullable=True))
    op.add_column(
        "device_health",
        sa.Column(
            "last_event_type",
            sa.Enum(
                "heartbeat",
                "power_lost",
                "power_restored",
                "boot",
                name="device_health_last_event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "device_health",
        sa.Column("last_device_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device_health",
        sa.Column(
            "status_reason", sa.String(length=160), server_default="seeded_health", nullable=False
        ),
    )
    op.add_column(
        "device_health",
        sa.Column("can_report_power_loss", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_check_constraint(
        "ck_device_health_battery_range",
        "device_health",
        "battery_mv IS NULL OR battery_mv BETWEEN 0 AND 10000",
    )
    op.create_check_constraint(
        "ck_device_health_rssi_range",
        "device_health",
        "rssi IS NULL OR rssi BETWEEN -200 AND 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_device_health_rssi_range", "device_health", type_="check")
    op.drop_constraint("ck_device_health_battery_range", "device_health", type_="check")
    op.drop_column("device_health", "can_report_power_loss")
    op.drop_column("device_health", "status_reason")
    op.drop_column("device_health", "last_device_timestamp")
    op.drop_column("device_health", "last_event_type")
    op.drop_column("device_health", "rssi")
    op.drop_column("device_health", "battery_mv")
    op.drop_column("telemetry_events", "state_changed")
