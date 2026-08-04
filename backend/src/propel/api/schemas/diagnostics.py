from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DiagnosticDependencyResponse(BaseModel):
    status: Literal["ok", "unavailable"]


class WorkerDiagnosticResponse(BaseModel):
    status: Literal["ok", "stale", "unknown"]
    last_seen_at: datetime | None


class QueueDiagnosticResponse(BaseModel):
    stream_length: int | None
    pending: int | None
    lag: int | None
    dead_letter_count: int | None
    analysis_pending: int | None
    analysis_overdue: int | None


class DiagnosticWarningResponse(BaseModel):
    code: str
    severity: Literal["critical", "warning", "info"]
    message: str


class OperationalOverviewResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    generated_at: datetime
    dependencies: dict[str, DiagnosticDependencyResponse]
    worker: WorkerDiagnosticResponse
    queue: QueueDiagnosticResponse
    device_counts: dict[str, int]
    pole_state_counts: dict[str, int]
    incident_counts: dict[str, int]
    latest_processed_at: datetime | None
    warnings: list[DiagnosticWarningResponse]


class TelemetryDiagnosticResponse(BaseModel):
    event_id: str
    correlation_id: str
    device_id: str
    pole_id: str
    event_type: str
    energized: bool
    device_timestamp: datetime
    received_at: datetime
    processed_at: datetime
    sequence: int
    processing_outcome: str
    origin: str
    state_changed: bool


class TelemetryDiagnosticPageResponse(BaseModel):
    items: list[TelemetryDiagnosticResponse]
    next_cursor: str | None


class DeviceHealthDiagnosticResponse(BaseModel):
    device_id: str
    pole_id: str | None
    dt_id: str | None
    status: str
    pole_state: str
    last_seen_at: datetime | None
    last_sequence: int | None
    last_event_type: str | None
    firmware: str | None
    battery_mv: int | None
    rssi: int | None
    status_reason: str
    can_report_power_loss: bool


class DeviceHealthDiagnosticPageResponse(BaseModel):
    items: list[DeviceHealthDiagnosticResponse]
    next_cursor: str | None
