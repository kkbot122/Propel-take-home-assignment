from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from propel.domain.enums import SimulatorFaultStatus, TelemetryEventType
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    Pole,
    SimulatedFault,
)
from propel.simulator.models import SimulatorTelemetryBatchGateway
from propel.telemetry.ingestion import TelemetryCommand


class SimulatorHeartbeatStoreUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SimulatorHeartbeatResult:
    eligible_devices: int
    emitted_events: int
    excluded_fault_poles: int


class PostgresSimulatorHeartbeatEmitter:
    """Refresh online simulated devices without reviving modeled offline devices."""

    _OFFLINE_REASONS = (
        "generated_offline",
        "simulator_missing_device",
        "simulator_device_failure",
        "simulator_device_failure_pending",
        "simulator_device_recovery_pending",
    )

    def __init__(
        self,
        engine: AsyncEngine,
        gateway: SimulatorTelemetryBatchGateway,
    ) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._gateway = gateway

    async def emit_once(
        self,
        *,
        emitted_at: datetime,
        batch_size: int,
    ) -> SimulatorHeartbeatResult:
        try:
            async with self._session_factory() as session:
                active_scopes = tuple(
                    await session.scalars(
                        select(SimulatedFault.deenergized_pole_ids).where(
                            SimulatedFault.status == SimulatorFaultStatus.ACTIVE
                        )
                    )
                )
                fault_pole_ids = {pole_id for scope in active_scopes for pole_id in scope}
                rows = (
                    await session.execute(
                        select(
                            Device.device_id,
                            Device.installed_firmware,
                            Pole.pole_id,
                            DeviceHealth.last_sequence,
                            DeviceHealth.firmware,
                        )
                        .join(DeviceBinding, DeviceBinding.device_id == Device.id)
                        .join(Pole, Pole.id == DeviceBinding.pole_id)
                        .join(DeviceHealth, DeviceHealth.device_id == Device.id)
                        .where(
                            DeviceBinding.valid_from <= emitted_at,
                            (
                                DeviceBinding.valid_to.is_(None)
                                | (DeviceBinding.valid_to > emitted_at)
                            ),
                            DeviceHealth.status_reason.not_in(self._OFFLINE_REASONS),
                        )
                        .order_by(Device.device_id)
                    )
                ).all()
        except SQLAlchemyError as error:
            raise SimulatorHeartbeatStoreUnavailableError from error

        sequence_floor = int(emitted_at.timestamp() * 1_000_000)
        commands = tuple(
            TelemetryCommand(
                device_id=row.device_id,
                pole_id=row.pole_id,
                event=TelemetryEventType.HEARTBEAT,
                energized=True,
                device_timestamp=emitted_at,
                sequence=max((row.last_sequence or 0) + 1, sequence_floor + index),
                battery_mv=3480,
                rssi=-91,
                firmware=row.firmware or row.installed_firmware or "1.4.2",
            )
            for index, row in enumerate(rows)
            if row.pole_id not in fault_pole_ids
        )
        emitted_events = 0
        for offset in range(0, len(commands), batch_size):
            receipts = await self._gateway.emit_many(commands[offset : offset + batch_size])
            emitted_events += len(receipts)
        return SimulatorHeartbeatResult(
            eligible_devices=len(commands),
            emitted_events=emitted_events,
            excluded_fault_poles=len({row.pole_id for row in rows} & fault_pole_ids),
        )
