import re
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from propel.domain.enums import TelemetryEventType, TelemetryOrigin
from propel.telemetry.ingestion import TelemetryCommand, TelemetryEnvelope

EXTERNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
REQUIRED_FIELDS = {
    "event_id",
    "correlation_id",
    "received_at",
    "device_id",
    "pole_id",
    "event",
    "energized",
    "ts",
    "seq",
    "battery_mv",
    "rssi",
    "fw",
}


class InvalidStreamMessageError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidStreamMessageError(f"invalid_{field_name}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidStreamMessageError(f"timezone_required_for_{field_name}")
    return parsed


def parse_integer(value: str, field_name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise InvalidStreamMessageError(f"invalid_{field_name}") from error
    if not minimum <= parsed <= maximum:
        raise InvalidStreamMessageError(f"{field_name}_out_of_range")
    return parsed


def parse_stream_message(fields: Mapping[str, str]) -> TelemetryEnvelope:
    missing_fields = REQUIRED_FIELDS - fields.keys()
    if missing_fields:
        raise InvalidStreamMessageError("missing_required_fields")

    device_id = fields["device_id"]
    pole_id = fields["pole_id"]
    firmware = fields["fw"].strip()
    if not EXTERNAL_ID_PATTERN.fullmatch(device_id):
        raise InvalidStreamMessageError("invalid_device_id")
    if not EXTERNAL_ID_PATTERN.fullmatch(pole_id):
        raise InvalidStreamMessageError("invalid_pole_id")
    if not firmware or len(firmware) > 32:
        raise InvalidStreamMessageError("invalid_firmware")

    try:
        event_id = UUID(fields["event_id"])
        correlation_id = UUID(fields["correlation_id"])
        event_type = TelemetryEventType(fields["event"])
        origin = TelemetryOrigin(fields.get("origin", TelemetryOrigin.DEVICE.value))
    except ValueError as error:
        raise InvalidStreamMessageError("invalid_identifier_or_event") from error

    energized_value = fields["energized"]
    if energized_value not in {"true", "false"}:
        raise InvalidStreamMessageError("invalid_energized")
    energized = energized_value == "true"
    if event_type == TelemetryEventType.POWER_LOST and energized:
        raise InvalidStreamMessageError("power_lost_requires_deenergized")
    if event_type == TelemetryEventType.POWER_RESTORED and not energized:
        raise InvalidStreamMessageError("power_restored_requires_energized")

    return TelemetryEnvelope(
        event_id=event_id,
        correlation_id=correlation_id,
        received_at=parse_datetime(fields["received_at"], "received_at"),
        origin=origin,
        command=TelemetryCommand(
            device_id=device_id,
            pole_id=pole_id,
            event=event_type,
            energized=energized,
            device_timestamp=parse_datetime(fields["ts"], "device_timestamp"),
            sequence=parse_integer(fields["seq"], "sequence", 0, 9_223_372_036_854_775_807),
            battery_mv=parse_integer(fields["battery_mv"], "battery_mv", 0, 10_000),
            rssi=parse_integer(fields["rssi"], "rssi", -200, 0),
            firmware=firmware,
        ),
    )
