from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from propel.domain.enums import SimulatorFaultStatus, SimulatorFaultType


class InjectFixedFaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_type: SimulatorFaultType = SimulatorFaultType.SPAN_FAULT
    feeder_id: Literal["FDR-001"] = "FDR-001"
    dt_id: Literal["DT-001", "DT-002"] = "DT-001"
    parent_pole_id: Literal["P-001", "P-101"] = "P-001"
    child_pole_id: Literal["P-002", "P-102"] = "P-002"
    missing_device_pole_ids: list[str] = Field(default_factory=list, max_length=6)
    omit_loss_pole_ids: list[str] = Field(default_factory=list, max_length=6)


class SimulatedFaultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fault_id: UUID
    fault_type: SimulatorFaultType
    feeder_id: str | None
    dt_id: str | None
    parent_pole_id: str | None
    child_pole_id: str | None
    status: SimulatorFaultStatus
    deenergized_pole_ids: list[str]
    injected_at: datetime
    injection_telemetry_at: datetime | None
    repaired_at: datetime | None
    emitted_event_ids: list[UUID]


class SimulatorResetResponse(BaseModel):
    status: Literal["reset"] = "reset"
    repaired_faults: list[SimulatedFaultResponse]
