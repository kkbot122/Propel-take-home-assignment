from enum import StrEnum

import pytest

from propel.domain.enums import FaultClass, LocalizationPrecision, PoleStatus, TicketStatus


@pytest.mark.parametrize(
    ("enum_class", "valid_value"),
    [
        (FaultClass, "SPAN_FAULT"),
        (LocalizationPrecision, "EXACT_SPAN"),
        (PoleStatus, "LIVE"),
        (TicketStatus, "DETECTED"),
    ],
)
def test_domain_enums_reject_unknown_values(enum_class: type[StrEnum], valid_value: str) -> None:
    assert enum_class(valid_value).value == valid_value

    with pytest.raises(ValueError):
        enum_class("NOT_A_DOMAIN_VALUE")
