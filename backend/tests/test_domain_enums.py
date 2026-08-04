from enum import StrEnum

import pytest

from propel.domain.enums import (
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    ScheduledOutageScope,
    SuspectedAssetType,
    TicketStatus,
)


@pytest.mark.parametrize(
    ("enum_class", "valid_value"),
    [
        (FaultClass, "SPAN_FAULT"),
        (FaultClass, "SENSOR_ANOMALY"),
        (FaultClass, "SCHEDULED_OUTAGE"),
        (LocalizationPrecision, "POLE_LEVEL"),
        (SuspectedAssetType, "DEVICE"),
        (ScheduledOutageScope, "SPAN"),
        (LocalizationPrecision, "EXACT_SPAN"),
        (PoleStatus, "LIVE"),
        (TicketStatus, "DETECTED"),
    ],
)
def test_domain_enums_reject_unknown_values(enum_class: type[StrEnum], valid_value: str) -> None:
    assert enum_class(valid_value).value == valid_value

    with pytest.raises(ValueError):
        enum_class("NOT_A_DOMAIN_VALUE")
