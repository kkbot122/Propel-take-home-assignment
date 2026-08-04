from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from propel.domain.enums import TelemetryEventType
from propel.infra.database.models import Device, DeviceHealth
from propel.infra.settings import get_settings
from propel.infra.simulator_heartbeat import PostgresSimulatorHeartbeatEmitter
from propel.simulator.models import SimulatorEmissionReceipt
from propel.telemetry.ingestion import TelemetryCommand

pytestmark = pytest.mark.integration


@dataclass(slots=True)
class CapturingBatchGateway:
    batches: list[tuple[TelemetryCommand, ...]] = field(default_factory=list)

    async def emit_many(
        self, commands: tuple[TelemetryCommand, ...]
    ) -> tuple[SimulatorEmissionReceipt, ...]:
        self.batches.append(commands)
        received_at = datetime.now(UTC)
        return tuple(
            SimulatorEmissionReceipt(event_id=uuid4(), received_at=received_at) for _ in commands
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_periodic_heartbeats_exclude_intentionally_offline_devices() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    gateway = CapturingBatchGateway()
    emitter = PostgresSimulatorHeartbeatEmitter(engine, gateway)
    emitted_at = datetime.now(UTC)
    try:
        result = await emitter.emit_once(emitted_at=emitted_at, batch_size=500)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            offline_device_ids = set(
                await session.scalars(
                    select(Device.device_id)
                    .join(DeviceHealth, DeviceHealth.device_id == Device.id)
                    .where(
                        DeviceHealth.status_reason.in_(
                            ("generated_offline", "simulator_missing_device")
                        )
                    )
                )
            )
    finally:
        await engine.dispose()

    commands = tuple(command for batch in gateway.batches for command in batch)
    assert result.emitted_events == result.eligible_devices == len(commands)
    assert result.emitted_events > 0
    assert all(len(batch) <= 500 for batch in gateway.batches)
    assert all(command.event == TelemetryEventType.HEARTBEAT for command in commands)
    assert all(command.energized for command in commands)
    assert {command.device_id for command in commands}.isdisjoint(offline_device_ids)
    assert "P-001" in {command.pole_id for command in commands}
