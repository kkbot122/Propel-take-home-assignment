import pytest

from propel.simulator.delivery import power_loss_delivery_succeeds


def test_power_loss_delivery_is_repeatable_and_approximately_configured_ratio() -> None:
    pole_ids = tuple(f"P-{index:05d}" for index in range(10_000))

    first = tuple(
        pole_id
        for pole_id in pole_ids
        if power_loss_delivery_succeeds(
            pole_id,
            context="FEEDER_FAULT:FDR-TEST",
            ratio=0.70,
            seed=287,
        )
    )
    second = tuple(
        pole_id
        for pole_id in pole_ids
        if power_loss_delivery_succeeds(
            pole_id,
            context="FEEDER_FAULT:FDR-TEST",
            ratio=0.70,
            seed=287,
        )
    )

    assert first == second
    assert len(first) / len(pole_ids) == pytest.approx(0.70, abs=0.01)


def test_power_loss_delivery_validates_policy_inputs() -> None:
    with pytest.raises(ValueError, match="ratio"):
        power_loss_delivery_succeeds("P-001", context="fault", ratio=1.01)
    with pytest.raises(ValueError, match="seed"):
        power_loss_delivery_succeeds("P-001", context="fault", seed=-1)
    with pytest.raises(ValueError, match="pole and fault context"):
        power_loss_delivery_succeeds("", context="fault")
