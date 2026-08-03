from collections import defaultdict
from datetime import timedelta

from propel.analysis.models import (
    CandidateEvidence,
    ConfidenceComponents,
    FaultCandidate,
    NetworkSnapshot,
    PoleEvidence,
    TopologySpan,
)
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    SuspectedAssetType,
    TopologySource,
)

DEFAULT_LIVE_FRESHNESS = timedelta(minutes=32)
CORRELATION_WINDOW_SECONDS = 10.0


class InvalidTopologySnapshotError(Exception):
    pass


def localize_known_topology(
    snapshot: NetworkSnapshot,
    *,
    live_freshness: timedelta = DEFAULT_LIVE_FRESHNESS,
) -> list[FaultCandidate]:
    poles = _pole_index(snapshot)
    spans = tuple(span for span in snapshot.spans if span.source == TopologySource.SURVEYED)
    children = _build_children(poles, spans)
    candidates: list[FaultCandidate] = []

    for span in sorted(
        spans,
        key=lambda item: (item.parent_pole_id or "", item.child_pole_id),
    ):
        if span.parent_pole_id is None:
            continue
        parent = poles[span.parent_pole_id]
        child = poles[span.child_pole_id]
        if not _is_recent_live(parent, snapshot, live_freshness):
            continue
        if child.state != PoleStatus.DARK or child.state_received_at is None:
            continue
        subtree_ids = _collect_subtree(span.child_pole_id, children)
        candidates.append(_build_candidate(snapshot, span, poles, subtree_ids))

    return sorted(candidates, key=lambda candidate: candidate.suspected_asset_id)


def _pole_index(snapshot: NetworkSnapshot) -> dict[str, PoleEvidence]:
    poles: dict[str, PoleEvidence] = {}
    for pole in snapshot.poles:
        if pole.pole_id in poles:
            raise InvalidTopologySnapshotError(f"duplicate pole {pole.pole_id}")
        poles[pole.pole_id] = pole
    if not poles:
        raise InvalidTopologySnapshotError("snapshot has no poles")
    return poles


def _build_children(
    poles: dict[str, PoleEvidence], spans: tuple[TopologySpan, ...]
) -> dict[str, tuple[str, ...]]:
    children: defaultdict[str, list[str]] = defaultdict(list)
    parent_by_child: dict[str, str | None] = {}
    for span in spans:
        if span.child_pole_id not in poles:
            raise InvalidTopologySnapshotError(f"unknown child pole {span.child_pole_id}")
        if span.parent_pole_id is not None and span.parent_pole_id not in poles:
            raise InvalidTopologySnapshotError(f"unknown parent pole {span.parent_pole_id}")
        if span.child_pole_id in parent_by_child:
            raise InvalidTopologySnapshotError(
                f"multiple surveyed parents for {span.child_pole_id}"
            )
        parent_by_child[span.child_pole_id] = span.parent_pole_id
        if span.parent_pole_id is not None:
            children[span.parent_pole_id].append(span.child_pole_id)

    normalized = {parent: tuple(sorted(child_ids)) for parent, child_ids in children.items()}
    for pole_id in poles:
        _collect_subtree(pole_id, normalized)
    return normalized


def _collect_subtree(root_pole_id: str, children: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    ordered: list[str] = []
    pending = [root_pole_id]
    visited: set[str] = set()
    while pending:
        pole_id = pending.pop()
        if pole_id in visited:
            raise InvalidTopologySnapshotError(f"cycle through pole {pole_id}")
        visited.add(pole_id)
        ordered.append(pole_id)
        pending.extend(reversed(children.get(pole_id, ())))
    return tuple(ordered)


def _is_recent_live(
    pole: PoleEvidence,
    snapshot: NetworkSnapshot,
    live_freshness: timedelta,
) -> bool:
    if pole.state != PoleStatus.LIVE or pole.state_received_at is None:
        return False
    age = snapshot.analysis_at - pole.state_received_at
    return timedelta(0) <= age <= live_freshness


def _build_candidate(
    snapshot: NetworkSnapshot,
    boundary: TopologySpan,
    poles: dict[str, PoleEvidence],
    subtree_ids: tuple[str, ...],
) -> FaultCandidate:
    assert boundary.parent_pole_id is not None
    parent = poles[boundary.parent_pole_id]
    child = poles[boundary.child_pole_id]
    assert child.state_received_at is not None
    onset_at = child.state_received_at

    subtree = tuple(poles[pole_id] for pole_id in subtree_ids)
    observable = tuple(
        pole for pole in subtree if pole.device is not None and pole.state != PoleStatus.NO_DEVICE
    )
    dark = tuple(pole for pole in observable if pole.state == PoleStatus.DARK)
    contradictions = tuple(
        sorted(
            pole.pole_id
            for pole in observable
            if pole.state == PoleStatus.LIVE
            and pole.state_received_at is not None
            and pole.state_received_at > onset_at
        )
    )
    pre_onset_live = tuple(
        sorted(
            pole.pole_id
            for pole in observable
            if pole.state == PoleStatus.LIVE
            and pole.state_received_at is not None
            and pole.state_received_at <= onset_at
        )
    )
    affected_ids = tuple(sorted(pole.pole_id for pole in dark))
    spread = _dark_observation_spread(dark)
    components = _confidence_components(observable, dark, contradictions, spread)
    score = max(0, min(100, sum(components.as_dict().values())))
    positive_reasons, negative_reasons = _confidence_reasons(
        parent,
        child,
        observable,
        dark,
        contradictions,
        pre_onset_live,
        spread,
    )
    confidence_level = "HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW"
    confidence_reason = (
        f"Surveyed live-to-dark boundary with {len(dark)}/{len(observable)} "
        f"dark observable poles and {len(contradictions)} post-onset live contradictions."
    )

    return FaultCandidate(
        dt_id=snapshot.dt_id,
        classification=FaultClass.SPAN_FAULT,
        suspected_asset_type=SuspectedAssetType.SPAN,
        suspected_asset_id=f"{parent.pole_id}->{child.pole_id}",
        parent_pole_id=parent.pole_id,
        child_pole_id=child.pole_id,
        affected_pole_ids=affected_ids,
        precision=LocalizationPrecision.EXACT_SPAN,
        topology_source=TopologySource.SURVEYED,
        latitude=(parent.latitude + child.latitude) / 2,
        longitude=(parent.longitude + child.longitude) / 2,
        pin_code=child.pin_code or parent.pin_code,
        confidence_score=score,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        evidence=CandidateEvidence(
            onset_at=onset_at,
            subtree_pole_ids=subtree_ids,
            observable_pole_count=len(observable),
            dark_pole_count=len(dark),
            post_onset_live_contradictions=contradictions,
            pre_onset_live_observations=pre_onset_live,
            dark_observation_spread_seconds=spread,
            positive_reasons=positive_reasons,
            negative_reasons=negative_reasons,
            components=components,
        ),
    )


def _dark_observation_spread(dark: tuple[PoleEvidence, ...]) -> float | None:
    timestamps = sorted(
        pole.state_received_at for pole in dark if pole.state_received_at is not None
    )
    if not timestamps:
        return None
    return (timestamps[-1] - timestamps[0]).total_seconds()


def _confidence_components(
    observable: tuple[PoleEvidence, ...],
    dark: tuple[PoleEvidence, ...],
    contradictions: tuple[str, ...],
    spread: float | None,
) -> ConfidenceComponents:
    observable_count = len(observable)
    corroboration = round(25 * len(dark) / observable_count) if observable_count else 0
    if spread is None:
        temporal = 0
    elif spread <= CORRELATION_WINDOW_SECONDS:
        temporal = 10
    elif spread <= 60:
        temporal = 5
    else:
        temporal = 0
    healthy_capable_count = sum(
        1
        for pole in observable
        if pole.device is not None
        and pole.device.status == DeviceHealthStatus.HEALTHY
        and pole.device.can_report_power_loss
    )
    sensor_quality = round(10 * healthy_capable_count / observable_count) if observable_count else 0
    return ConfidenceComponents(
        topology=25,
        boundary_clarity=30,
        downstream_corroboration=corroboration,
        temporal_coherence=temporal,
        sensor_quality=sensor_quality,
        contradiction_penalty=-min(40, len(contradictions) * 20),
    )


def _confidence_reasons(
    parent: PoleEvidence,
    child: PoleEvidence,
    observable: tuple[PoleEvidence, ...],
    dark: tuple[PoleEvidence, ...],
    contradictions: tuple[str, ...],
    pre_onset_live: tuple[str, ...],
    spread: float | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    positive = [
        "surveyed topology supports exact-span precision",
        f"recent LIVE evidence at upstream pole {parent.pole_id}",
        f"explicit DARK evidence at boundary child {child.pole_id}",
        f"{len(dark)}/{len(observable)} observable subtree poles are DARK",
    ]
    if spread is not None and spread <= CORRELATION_WINDOW_SECONDS:
        positive.append(f"dark observations arrived within {spread:.3f} seconds")
    if pre_onset_live:
        positive.append(
            "pre-onset LIVE observations retained as prior-state evidence: "
            + ", ".join(pre_onset_live)
        )

    negative: list[str] = []
    if contradictions:
        negative.append(
            "post-onset LIVE contradictions below boundary: " + ", ".join(contradictions)
        )
    weak_devices = sorted(
        pole.pole_id
        for pole in observable
        if pole.device is not None
        and (
            pole.device.status != DeviceHealthStatus.HEALTHY
            or not pole.device.can_report_power_loss
        )
    )
    if weak_devices:
        negative.append("weak or incapable device evidence: " + ", ".join(weak_devices))
    return tuple(positive), tuple(negative)
