from datetime import UTC, datetime, timedelta

import pytest

from propel.domain.enums import PoleStatus, ProcessingOutcome, TelemetryEventType
from propel.telemetry.messages import InvalidStreamMessageError, parse_stream_message
from propel.telemetry.ordering import DeviceCursor, decide_event

OBSERVED_AT = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("event_type", "energized", "expected_state", "expected_reason"),
    [
        (TelemetryEventType.HEARTBEAT, True, PoleStatus.LIVE, "energized_heartbeat"),
        (TelemetryEventType.HEARTBEAT, False, None, "accepted_without_state_transition"),
        (TelemetryEventType.POWER_LOST, False, PoleStatus.DARK, "power_lost"),
        (TelemetryEventType.POWER_RESTORED, True, PoleStatus.LIVE, "power_restored"),
    ],
)
def test_accepted_events_derive_only_supported_state_transitions(
    event_type: TelemetryEventType,
    energized: bool,
    expected_state: PoleStatus | None,
    expected_reason: str,
) -> None:
    decision = decide_event(
        event_type,
        energized,
        101,
        OBSERVED_AT,
        DeviceCursor(boot_generation=0, last_sequence=100),
    )

    assert decision.outcome == ProcessingOutcome.ACCEPTED
    assert decision.boot_generation == 0
    assert decision.next_sequence == 101
    assert decision.target_pole_state == expected_state
    assert decision.reason == expected_reason


@pytest.mark.parametrize(
    ("sequence", "expected_outcome"),
    [(101, ProcessingOutcome.DUPLICATE), (100, ProcessingOutcome.STALE)],
)
def test_sequence_cannot_repeat_or_regress_within_generation(
    sequence: int, expected_outcome: ProcessingOutcome
) -> None:
    decision = decide_event(
        TelemetryEventType.POWER_RESTORED,
        True,
        sequence,
        OBSERVED_AT,
        DeviceCursor(boot_generation=4, last_sequence=101),
    )

    assert decision.outcome == expected_outcome
    assert decision.boot_generation == 4
    assert decision.next_sequence == 101
    assert decision.target_pole_state is None


def test_boot_starts_generation_without_changing_pole_state() -> None:
    decision = decide_event(
        TelemetryEventType.BOOT,
        True,
        0,
        OBSERVED_AT,
        DeviceCursor(boot_generation=4, last_sequence=101),
    )

    assert decision.outcome == ProcessingOutcome.ACCEPTED
    assert decision.boot_generation == 5
    assert decision.next_sequence == 0
    assert decision.target_pole_state is None
    assert decision.reason == "boot_generation_started"


def test_replayed_boot_does_not_start_another_generation() -> None:
    decision = decide_event(
        TelemetryEventType.BOOT,
        True,
        0,
        OBSERVED_AT,
        DeviceCursor(
            boot_generation=5,
            last_sequence=0,
            last_event_type=TelemetryEventType.BOOT,
            last_device_timestamp=OBSERVED_AT + timedelta(seconds=1),
        ),
    )

    assert decision.outcome == ProcessingOutcome.DUPLICATE
    assert decision.boot_generation == 5
    assert decision.reason == "duplicate_boot"


def test_delayed_boot_does_not_replace_a_newer_generation_cursor() -> None:
    decision = decide_event(
        TelemetryEventType.BOOT,
        True,
        0,
        OBSERVED_AT,
        DeviceCursor(
            boot_generation=5,
            last_sequence=1,
            last_event_type=TelemetryEventType.POWER_RESTORED,
            last_device_timestamp=OBSERVED_AT + timedelta(seconds=1),
        ),
    )

    assert decision.outcome == ProcessingOutcome.STALE
    assert decision.boot_generation == 5
    assert decision.next_sequence == 1
    assert decision.reason == "stale_boot"


def test_stream_parser_rejects_inconsistent_power_state() -> None:
    fields = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "correlation_id": "00000000-0000-4000-8000-000000000002",
        "received_at": "2026-08-04T01:00:00Z",
        "device_id": "DEV-P-002",
        "pole_id": "P-002",
        "event": "power_lost",
        "energized": "true",
        "ts": "2026-08-04T01:00:00Z",
        "seq": "101",
        "battery_mv": "3480",
        "rssi": "-91",
        "fw": "1.4.2",
    }

    with pytest.raises(InvalidStreamMessageError, match="power_lost_requires_deenergized"):
        parse_stream_message(fields)
