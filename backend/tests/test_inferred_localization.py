from datetime import UTC, datetime, timedelta

from propel.analysis.localization import localize_known_topology
from propel.analysis.models import DeviceEvidence, NetworkSnapshot, PoleEvidence, TopologySpan
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    TopologySource,
)
from propel.topology.inference import infer_geographic_topology
from propel.topology.models import TopologyPole, TopologyRequest

ANALYSIS_AT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
ONSET_AT = ANALYSIS_AT - timedelta(seconds=5)


def pole(pole_id: str, latitude: float, longitude: float, state: PoleStatus) -> PoleEvidence:
    observed_at = ANALYSIS_AT - timedelta(seconds=10) if state == PoleStatus.LIVE else ONSET_AT
    return PoleEvidence(
        pole_id=pole_id,
        latitude=latitude,
        longitude=longitude,
        pin_code="560078",
        state=state,
        state_received_at=observed_at,
        device=DeviceEvidence(
            device_id=f"DEV-{pole_id}",
            status=DeviceHealthStatus.HEALTHY,
            last_seen_at=observed_at,
            can_report_power_loss=True,
            firmware="1.4.2",
            battery_mv=3480,
            rssi=-91,
        ),
    )


def inferred_fault_snapshot(*, quality_score: float | None = None) -> NetworkSnapshot:
    poles = (
        pole("P-201", 12.90018, 77.60000, PoleStatus.LIVE),
        pole("P-202", 12.90036, 77.60000, PoleStatus.DARK),
        pole("P-203", 12.90054, 77.60000, PoleStatus.DARK),
        pole("P-204", 12.90036, 77.60018, PoleStatus.DARK),
    )
    topology = infer_geographic_topology(
        TopologyRequest(
            dt_id="DT-003",
            dt_latitude=12.90000,
            dt_longitude=77.60000,
            topology_version=1,
            poles=tuple(
                TopologyPole(item.pole_id, item.latitude, item.longitude) for item in poles
            ),
        )
    )
    score = topology.quality.score if quality_score is None else quality_score
    return NetworkSnapshot(
        dt_id="DT-003",
        feeder_id="FDR-001",
        topology_version=1,
        analysis_at=ANALYSIS_AT,
        poles=poles,
        spans=tuple(
            TopologySpan(
                edge.parent_pole_id,
                edge.child_pole_id,
                edge.source,
                edge.edge_confidence,
                edge.distance_m,
                edge.inference_version,
            )
            for edge in topology.edges
        ),
        dt_latitude=12.90000,
        dt_longitude=77.60000,
        dt_pin_code="560078",
        topology_quality_score=score,
        topology_quality_tier=(
            topology.quality.tier.value if quality_score is None else "WEAKLY_INFERRED"
        ),
        topology_quality_reasons=(
            () if quality_score is None else ("forced weak topology fixture",)
        ),
        inference_version=topology.inference_version,
    )


def test_strong_inferred_boundary_returns_probable_span_never_exact() -> None:
    candidate = localize_known_topology(inferred_fault_snapshot())[0]

    assert candidate.classification == FaultClass.SPAN_FAULT
    assert candidate.suspected_asset_id == "P-201->P-202"
    assert candidate.affected_pole_ids == ("P-202", "P-203", "P-204")
    assert candidate.precision == LocalizationPrecision.PROBABLE_SPAN
    assert candidate.precision != LocalizationPrecision.EXACT_SPAN
    assert candidate.topology_source == TopologySource.INFERRED
    assert candidate.confidence_score == 79
    assert candidate.evidence.raw_score == 88
    assert candidate.evidence.score_cap == 79
    assert candidate.evidence.topology_quality_tier == "STRONGLY_INFERRED"
    assert any("inferred" in reason for reason in candidate.evidence.negative_reasons)


def test_weak_inferred_topology_degrades_confirmed_dark_poles_to_dt_level() -> None:
    candidate = localize_known_topology(inferred_fault_snapshot(quality_score=0.55))[0]

    assert candidate.classification == FaultClass.UNCONFIRMED_OUTAGE
    assert candidate.precision == LocalizationPrecision.DT_LEVEL
    assert candidate.topology_source == TopologySource.INFERRED
    assert candidate.confidence_score <= 49
    assert candidate.evidence.topology_quality_score == 0.55
    assert candidate.evidence.topology_quality_reasons == ("forced weak topology fixture",)


def test_hidden_fault_edge_is_contained_without_becoming_localizer_input() -> None:
    hidden_fault = frozenset(("P-201", "P-202"))

    candidate = localize_known_topology(inferred_fault_snapshot())[0]

    reported_boundary = frozenset((candidate.parent_pole_id, candidate.child_pole_id))
    assert hidden_fault == reported_boundary


def test_inferred_corridor_contains_hidden_edge_when_recovered_edge_differs() -> None:
    hidden_fault = frozenset(("P-201", "P-202"))
    poles = (
        pole("P-201", 12.90018, 77.60000, PoleStatus.LIVE),
        pole("P-202", 12.90036, 77.60000, PoleStatus.DARK),
        pole("P-203", 12.90054, 77.60000, PoleStatus.DARK),
        PoleEvidence(
            pole_id="P-204",
            latitude=12.90027,
            longitude=77.60000,
            pin_code="560078",
            state=PoleStatus.NO_DEVICE,
            state_received_at=None,
            device=None,
        ),
    )
    topology = infer_geographic_topology(
        TopologyRequest(
            dt_id="DT-003",
            dt_latitude=12.90000,
            dt_longitude=77.60000,
            topology_version=1,
            poles=tuple(
                TopologyPole(item.pole_id, item.latitude, item.longitude) for item in poles
            ),
        )
    )
    inferred_edges = {
        frozenset((edge.parent_pole_id, edge.child_pole_id)) for edge in topology.edges
    }
    assert hidden_fault not in inferred_edges
    snapshot = NetworkSnapshot(
        dt_id="DT-003",
        feeder_id="FDR-001",
        topology_version=1,
        analysis_at=ANALYSIS_AT,
        poles=poles,
        spans=tuple(
            TopologySpan(
                edge.parent_pole_id,
                edge.child_pole_id,
                edge.source,
                edge.edge_confidence,
                edge.distance_m,
                edge.inference_version,
            )
            for edge in topology.edges
        ),
        dt_latitude=12.90000,
        dt_longitude=77.60000,
        dt_pin_code="560078",
        topology_quality_score=topology.quality.score,
        topology_quality_tier=topology.quality.tier.value,
        topology_quality_reasons=topology.quality.limiting_factors,
        inference_version=topology.inference_version,
    )

    candidate = localize_known_topology(snapshot)[0]

    assert candidate.precision == LocalizationPrecision.CORRIDOR
    assert candidate.evidence.corridor is not None
    assert hidden_fault <= set(candidate.evidence.corridor.ordered_pole_ids)
