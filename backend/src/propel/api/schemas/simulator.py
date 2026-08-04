from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from propel.domain.enums import SimulatorFaultStatus, SimulatorFaultType


class InjectFixedFaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_type: SimulatorFaultType = SimulatorFaultType.SPAN_FAULT
    feeder_id: str = Field(default="FDR-001", min_length=1, max_length=64)
    dt_id: str = Field(default="DT-001", min_length=1, max_length=64)
    parent_pole_id: str = Field(default="P-001", min_length=1, max_length=64)
    child_pole_id: str = Field(default="P-002", min_length=1, max_length=64)
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


class GeneratedNetworkManifestResponse(BaseModel):
    dataset_id: str
    generator_version: str
    seed: int
    config: dict[str, Any]
    counts: dict[str, int]
    substations: list[dict[str, Any]]
    feeders: list[dict[str, Any]]
    transformers: list[dict[str, Any]]
    poles: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    ground_truth_edges: list[dict[str, Any]]
    visible_edges: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
