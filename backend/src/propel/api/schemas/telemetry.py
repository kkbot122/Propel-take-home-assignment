from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from propel.domain.enums import TelemetryEventType
from propel.telemetry.ingestion import TelemetryCommand

ExternalId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        strict=True,
        strip_whitespace=True,
    ),
]
FirmwareVersion = Annotated[
    str,
    StringConstraints(min_length=1, max_length=32, strict=True, strip_whitespace=True),
]


class TelemetryRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "device_id": "DEV-P-002",
                    "pole_id": "P-002",
                    "event": "power_lost",
                    "energized": False,
                    "ts": "2026-08-03T12:00:00Z",
                    "seq": 101,
                    "battery_mv": 3480,
                    "rssi": -91,
                    "fw": "1.4.2",
                }
            ]
        },
    )

    device_id: ExternalId
    pole_id: ExternalId
    event: TelemetryEventType
    energized: Annotated[bool, Field(strict=True)]
    ts: AwareDatetime
    seq: Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]
    battery_mv: Annotated[int, Field(strict=True, ge=0, le=10_000)]
    rssi: Annotated[int, Field(strict=True, ge=-200, le=0)]
    fw: FirmwareVersion

    @model_validator(mode="after")
    def validate_power_state(self) -> "TelemetryRequest":
        required_power_state = {
            TelemetryEventType.POWER_LOST: False,
            TelemetryEventType.POWER_RESTORED: True,
        }.get(self.event)
        if required_power_state is not None and self.energized is not required_power_state:
            expected = str(required_power_state).lower()
            raise ValueError(f"{self.event.value} requires energized={expected}")
        return self

    def to_command(self) -> TelemetryCommand:
        return TelemetryCommand(
            device_id=self.device_id,
            pole_id=self.pole_id,
            event=self.event,
            energized=self.energized,
            device_timestamp=self.ts,
            sequence=self.seq,
            battery_mv=self.battery_mv,
            rssi=self.rssi,
            firmware=self.fw,
        )


class TelemetryAcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    event_id: UUID
    correlation_id: UUID
    received_at: datetime
    stream_id: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ValidationIssue(BaseModel):
    location: str
    message: str
    type: str


class ValidationErrorDetail(ErrorDetail):
    issues: list[ValidationIssue]


class ValidationErrorResponse(BaseModel):
    error: ValidationErrorDetail
