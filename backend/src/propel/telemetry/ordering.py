from dataclasses import dataclass
from datetime import datetime

from propel.domain.enums import PoleStatus, ProcessingOutcome, TelemetryEventType


@dataclass(frozen=True, slots=True)
class DeviceCursor:
    boot_generation: int
    last_sequence: int | None
    last_event_type: TelemetryEventType | None = None
    last_device_timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class OrderingDecision:
    outcome: ProcessingOutcome
    boot_generation: int
    next_sequence: int | None
    target_pole_state: PoleStatus | None
    reason: str


def decide_event(
    event_type: TelemetryEventType,
    energized: bool,
    sequence: int,
    device_timestamp: datetime,
    cursor: DeviceCursor,
) -> OrderingDecision:
    if event_type == TelemetryEventType.BOOT:
        if (
            cursor.last_device_timestamp is not None
            and device_timestamp <= cursor.last_device_timestamp
        ):
            duplicate = (
                cursor.last_event_type == TelemetryEventType.BOOT
                and cursor.last_sequence == sequence
            )
            return OrderingDecision(
                outcome=(ProcessingOutcome.DUPLICATE if duplicate else ProcessingOutcome.STALE),
                boot_generation=cursor.boot_generation,
                next_sequence=cursor.last_sequence,
                target_pole_state=None,
                reason="duplicate_boot" if duplicate else "stale_boot",
            )
        return OrderingDecision(
            outcome=ProcessingOutcome.ACCEPTED,
            boot_generation=cursor.boot_generation + 1,
            next_sequence=sequence,
            target_pole_state=None,
            reason="boot_generation_started",
        )

    if cursor.last_sequence is not None and sequence <= cursor.last_sequence:
        outcome = (
            ProcessingOutcome.DUPLICATE
            if sequence == cursor.last_sequence
            else ProcessingOutcome.STALE
        )
        return OrderingDecision(
            outcome=outcome,
            boot_generation=cursor.boot_generation,
            next_sequence=cursor.last_sequence,
            target_pole_state=None,
            reason=(
                "duplicate_sequence" if outcome == ProcessingOutcome.DUPLICATE else "stale_sequence"
            ),
        )

    target_state: PoleStatus | None = None
    reason = "accepted_without_state_transition"
    if event_type == TelemetryEventType.HEARTBEAT and energized:
        target_state = PoleStatus.LIVE
        reason = "energized_heartbeat"
    elif event_type == TelemetryEventType.POWER_LOST:
        target_state = PoleStatus.DARK
        reason = "power_lost"
    elif event_type == TelemetryEventType.POWER_RESTORED:
        target_state = PoleStatus.LIVE
        reason = "power_restored"

    return OrderingDecision(
        outcome=ProcessingOutcome.ACCEPTED,
        boot_generation=cursor.boot_generation,
        next_sequence=sequence,
        target_pole_state=target_state,
        reason=reason,
    )
