from propel.api.schemas.simulator import InjectFixedFaultRequest


def test_fixed_simulator_accepts_inferred_dt_span_fixture() -> None:
    request = InjectFixedFaultRequest.model_validate(
        {
            "fault_type": "SPAN_FAULT",
            "dt_id": "DT-003",
            "parent_pole_id": "P-201",
            "child_pole_id": "P-202",
        }
    )

    assert request.dt_id == "DT-003"
    assert request.parent_pole_id == "P-201"
    assert request.child_pole_id == "P-202"
