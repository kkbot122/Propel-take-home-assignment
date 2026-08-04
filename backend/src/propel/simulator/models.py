from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from propel.domain.enums import SimulatorFaultStatus, SimulatorFaultType
from propel.telemetry.ingestion import TelemetryCommand


@dataclass(frozen=True, slots=True)
class SimulatorEmissionReceipt:
    event_id: UUID
    received_at: datetime


@dataclass(frozen=True, slots=True)
class SimulatedFaultView:
    fault_id: UUID
    fault_type: SimulatorFaultType
    feeder_id: str | None
    dt_id: str | None
    parent_pole_id: str | None
    child_pole_id: str | None
    status: SimulatorFaultStatus
    deenergized_pole_ids: tuple[str, ...]
    injected_at: datetime
    injection_telemetry_at: datetime | None
    repaired_at: datetime | None
    emitted_event_ids: tuple[UUID, ...] = ()


class SimulatorTelemetryGateway(Protocol):
    async def emit(self, command: TelemetryCommand) -> SimulatorEmissionReceipt: ...

    async def close(self) -> None: ...


class SimulatorTelemetryBatchGateway(Protocol):
    async def emit_many(
        self, commands: tuple[TelemetryCommand, ...]
    ) -> tuple[SimulatorEmissionReceipt, ...]: ...

    async def close(self) -> None: ...
