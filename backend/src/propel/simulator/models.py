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
    restored_pole_ids: tuple[str, ...] = ()
    restoration_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class SimulatorScenarioView:
    scenario_id: str
    description: str
    fault_count: int
    scheduled: bool
    restoration_fraction: float
    noise_modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimulatorScenarioRunView:
    scenario_id: str
    description: str
    faults: tuple[SimulatedFaultView, ...]
    restoration_fraction: float
    failed_device_id: str | None = None
    failed_pole_id: str | None = None
    scheduled_outage_id: str | None = None


class SimulatorTelemetryGateway(Protocol):
    async def emit(self, command: TelemetryCommand) -> SimulatorEmissionReceipt: ...

    async def close(self) -> None: ...


class SimulatorTelemetryBatchGateway(Protocol):
    async def emit_many(
        self, commands: tuple[TelemetryCommand, ...]
    ) -> tuple[SimulatorEmissionReceipt, ...]: ...

    async def close(self) -> None: ...
