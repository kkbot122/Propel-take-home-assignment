from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import timedelta

from propel.analysis.models import (
    CandidateEvidence,
    CandidateSuppression,
    ConfidenceComponents,
    FaultCandidate,
    FeederTransformerEvidence,
    LocalizationCorridor,
    NetworkSnapshot,
    PoleEvidence,
    ScheduledOutageWindow,
    TopologySpan,
)
from propel.domain.enums import (
    DeviceHealthStatus,
    FaultClass,
    LocalizationPrecision,
    PoleStatus,
    ScheduledOutageScope,
    SuspectedAssetType,
    TopologySource,
)

DEFAULT_LIVE_FRESHNESS = timedelta(minutes=32)
DEFAULT_SCHEDULE_EARLY_GRACE = timedelta(minutes=10)
DEFAULT_SCHEDULE_OVERRUN_GRACE = timedelta(minutes=40)
CORRELATION_WINDOW_SECONDS = 10.0
DEFAULT_DT_FAULT_RATIO = 0.6
DEFAULT_DT_MIN_BRANCHES = 2
DEFAULT_FEEDER_FAULT_RATIO = 0.6
DEFAULT_FEEDER_MIN_DTS = 2


class InvalidTopologySnapshotError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _BoundaryLocalization:
    upstream_pole_id: str
    downstream_pole_id: str
    subtree_root_pole_id: str
    source: TopologySource
    edge_confidence: float
    corridor: LocalizationCorridor | None = None


def localize_known_topology(
    snapshot: NetworkSnapshot,
    *,
    live_freshness: timedelta = DEFAULT_LIVE_FRESHNESS,
    schedule_early_grace: timedelta = DEFAULT_SCHEDULE_EARLY_GRACE,
    schedule_overrun_grace: timedelta = DEFAULT_SCHEDULE_OVERRUN_GRACE,
    dt_fault_ratio: float = DEFAULT_DT_FAULT_RATIO,
    dt_min_branches: int = DEFAULT_DT_MIN_BRANCHES,
    feeder_fault_ratio: float = DEFAULT_FEEDER_FAULT_RATIO,
    feeder_min_dts: int = DEFAULT_FEEDER_MIN_DTS,
    correlation_window_seconds: float = CORRELATION_WINDOW_SECONDS,
) -> list[FaultCandidate]:
    poles = _pole_index(snapshot)
    spans = snapshot.spans
    children = _build_children(poles, spans)
    transformers = _snapshot_transformers(snapshot)
    raw_dt_candidates = tuple(
        candidate
        for transformer in transformers
        if (
            candidate := _build_dt_candidate(
                snapshot,
                transformer,
                live_freshness=live_freshness,
                dt_fault_ratio=dt_fault_ratio,
                dt_min_branches=dt_min_branches,
            )
        )
        is not None
    )
    dt_candidates = tuple(
        _apply_scheduled_suppression(
            snapshot,
            candidate,
            early_grace=schedule_early_grace,
            overrun_grace=schedule_overrun_grace,
        )
        for candidate in raw_dt_candidates
    )
    feeder_candidate = _build_feeder_candidate(
        snapshot,
        tuple(
            candidate
            for candidate in dt_candidates
            if candidate.classification == FaultClass.DT_FAULT
        ),
        transformers,
        live_freshness=live_freshness,
        feeder_fault_ratio=feeder_fault_ratio,
        feeder_min_dts=feeder_min_dts,
        correlation_window_seconds=correlation_window_seconds,
    )
    if feeder_candidate is not None:
        feeder_candidate = _apply_scheduled_suppression(
            snapshot,
            feeder_candidate,
            early_grace=schedule_early_grace,
            overrun_grace=schedule_overrun_grace,
        )

    span_candidates = _build_span_candidates(
        snapshot,
        poles,
        spans,
        children,
        live_freshness=live_freshness,
        schedule_early_grace=schedule_early_grace,
        schedule_overrun_grace=schedule_overrun_grace,
    )
    if feeder_candidate is not None:
        if snapshot.dt_id in feeder_candidate.affected_dt_ids:
            return [feeder_candidate]
        return sorted(
            [feeder_candidate, *span_candidates],
            key=lambda candidate: (candidate.classification.value, candidate.suspected_asset_id),
        )

    focal_dt_candidate = next(
        (candidate for candidate in dt_candidates if candidate.dt_id == snapshot.dt_id),
        None,
    )
    if focal_dt_candidate is not None:
        return [focal_dt_candidate]
    return span_candidates


def _build_span_candidates(
    snapshot: NetworkSnapshot,
    poles: dict[str, PoleEvidence],
    spans: tuple[TopologySpan, ...],
    children: dict[str, tuple[str, ...]],
    *,
    live_freshness: timedelta,
    schedule_early_grace: timedelta,
    schedule_overrun_grace: timedelta,
) -> list[FaultCandidate]:
    topology_source = _snapshot_topology_source(snapshot)
    if topology_source == TopologySource.INFERRED and snapshot.topology_quality_score < 0.7:
        dark_ids = tuple(
            pole.pole_id
            for pole in sorted(poles.values(), key=lambda item: item.pole_id)
            if _is_credible_dark(pole, snapshot, live_freshness)
        )
        return (
            [_build_degraded_dt_candidate(snapshot, poles, dark_ids, live_freshness)]
            if dark_ids
            else []
        )

    candidates: list[FaultCandidate] = []
    boundaries: list[_BoundaryLocalization] = []
    subtrees: dict[str, tuple[str, ...]] = {}
    parent_by_child = {span.child_pole_id: span.parent_pole_id for span in spans}
    span_by_child = {span.child_pole_id: span for span in spans}
    for child in sorted(poles.values(), key=lambda item: item.pole_id):
        if not _is_credible_dark(child, snapshot, live_freshness):
            continue
        parent_id = parent_by_child.get(child.pole_id)
        if parent_id is None:
            continue
        parent = poles[parent_id]
        if _is_credible_dark(parent, snapshot, live_freshness):
            continue
        if _is_recent_live(parent, snapshot, live_freshness):
            span = span_by_child[child.pole_id]
            boundary = _BoundaryLocalization(
                parent.pole_id,
                child.pole_id,
                child.pole_id,
                span.source,
                span.edge_confidence,
            )
        else:
            boundary = _find_corridor_boundary(
                child,
                poles,
                parent_by_child,
                snapshot,
                live_freshness,
                topology_source,
            )
            if boundary is None:
                continue
        boundaries.append(boundary)
        subtrees[boundary.subtree_root_pole_id] = _collect_subtree(
            boundary.subtree_root_pole_id, children
        )

    assigned_dark: dict[str, list[str]] = defaultdict(list)
    for pole in sorted(poles.values(), key=lambda item: item.pole_id):
        if not _is_credible_dark(pole, snapshot, live_freshness):
            continue
        containing_boundaries = tuple(
            boundary
            for boundary in boundaries
            if pole.pole_id in subtrees[boundary.subtree_root_pole_id]
        )
        if not containing_boundaries:
            continue
        nearest = min(
            containing_boundaries,
            key=lambda boundary: (
                len(subtrees[boundary.subtree_root_pole_id]),
                boundary.downstream_pole_id,
            ),
        )
        assigned_dark[nearest.downstream_pole_id].append(pole.pole_id)

    for boundary in boundaries:
        subtree_ids = subtrees[boundary.subtree_root_pole_id]
        assigned_ids = tuple(assigned_dark[boundary.downstream_pole_id])
        if not assigned_ids:
            continue
        candidate = (
            _build_candidate(
                snapshot,
                TopologySpan(
                    boundary.upstream_pole_id,
                    boundary.downstream_pole_id,
                    boundary.source,
                    boundary.edge_confidence,
                ),
                poles,
                subtree_ids,
                assigned_ids,
            )
            if boundary.corridor is None
            else _build_corridor_candidate(
                snapshot,
                boundary.corridor,
                poles,
                subtree_ids,
                assigned_ids,
            )
        )
        candidates.append(
            _classify_span_candidate(
                snapshot,
                candidate,
                poles,
                live_freshness=live_freshness,
                schedule_early_grace=schedule_early_grace,
                schedule_overrun_grace=schedule_overrun_grace,
            )
        )

    assigned_ids = {pole_id for pole_ids in assigned_dark.values() for pole_id in pole_ids}
    unbounded_dark_ids = tuple(
        pole.pole_id
        for pole in sorted(poles.values(), key=lambda item: item.pole_id)
        if _is_credible_dark(pole, snapshot, live_freshness) and pole.pole_id not in assigned_ids
    )
    if unbounded_dark_ids:
        candidates.append(
            _build_degraded_dt_candidate(snapshot, poles, unbounded_dark_ids, live_freshness)
        )

    return sorted(candidates, key=lambda candidate: candidate.suspected_asset_id)


def _find_corridor_boundary(
    downstream: PoleEvidence,
    poles: dict[str, PoleEvidence],
    parent_by_child: dict[str, str | None],
    snapshot: NetworkSnapshot,
    live_freshness: timedelta,
    topology_source: TopologySource,
) -> _BoundaryLocalization | None:
    skipped_downstream_to_upstream: list[str] = []
    parent_id = parent_by_child.get(downstream.pole_id)
    while parent_id is not None:
        parent = poles[parent_id]
        if _is_credible_dark(parent, snapshot, live_freshness):
            return None
        if _is_recent_live(parent, snapshot, live_freshness):
            skipped = tuple(reversed(skipped_downstream_to_upstream))
            if not skipped:
                return None
            corridor = LocalizationCorridor(
                upstream_pole_id=parent.pole_id,
                downstream_pole_id=downstream.pole_id,
                ordered_pole_ids=(parent.pole_id, *skipped, downstream.pole_id),
                skipped_pole_ids=skipped,
            )
            return _BoundaryLocalization(
                upstream_pole_id=parent.pole_id,
                downstream_pole_id=downstream.pole_id,
                subtree_root_pole_id=downstream.pole_id,
                source=topology_source,
                edge_confidence=0,
                corridor=corridor,
            )
        skipped_downstream_to_upstream.append(parent.pole_id)
        parent_id = parent_by_child.get(parent.pole_id)
    return None


def _snapshot_transformers(
    snapshot: NetworkSnapshot,
) -> tuple[FeederTransformerEvidence, ...]:
    if snapshot.feeder_transformers:
        return tuple(sorted(snapshot.feeder_transformers, key=lambda item: item.dt_id))
    return (
        FeederTransformerEvidence(
            dt_id=snapshot.dt_id,
            latitude=snapshot.dt_latitude,
            longitude=snapshot.dt_longitude,
            pin_code=snapshot.dt_pin_code,
            topology_version=snapshot.topology_version,
            poles=snapshot.poles,
            spans=snapshot.spans,
        ),
    )


def _build_dt_candidate(
    snapshot: NetworkSnapshot,
    transformer: FeederTransformerEvidence,
    *,
    live_freshness: timedelta,
    dt_fault_ratio: float,
    dt_min_branches: int,
) -> FaultCandidate | None:
    poles = {pole.pole_id: pole for pole in transformer.poles}
    topology_spans = transformer.spans
    children = _build_children(poles, topology_spans)
    observable = tuple(
        pole
        for pole in transformer.poles
        if _has_usable_device_evidence(pole, snapshot, live_freshness)
    )
    dark = tuple(pole for pole in observable if pole.state == PoleStatus.DARK)
    if not observable or not dark or any(pole.state_received_at is None for pole in dark):
        return None

    root_ids = tuple(
        sorted(span.child_pole_id for span in topology_spans if span.parent_pole_id is None)
    )
    if not root_ids or any(poles[root_id].state != PoleStatus.DARK for root_id in root_ids):
        return None
    branch_ids = tuple(
        sorted(
            branch_id for root_id in root_ids for branch_id in (children.get(root_id) or (root_id,))
        )
    )
    affected_branches = tuple(
        branch_id
        for branch_id in branch_ids
        if any(
            poles[pole_id].state == PoleStatus.DARK
            for pole_id in _collect_subtree(branch_id, children)
        )
    )
    dark_ratio = len(dark) / len(observable)
    all_observable_dark = len(dark) == len(observable)
    if not all_observable_dark and (
        dark_ratio < dt_fault_ratio or len(affected_branches) < dt_min_branches
    ):
        return None

    onset_at = min(pole.state_received_at for pole in dark if pole.state_received_at is not None)
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
    spread = _dark_observation_spread(dark)
    base_components = _confidence_components(observable, dark, contradictions, spread)
    components = replace(base_components, boundary_clarity=20)
    score = max(0, min(100, sum(components.as_dict().values())))
    affected_ids = tuple(sorted(pole.pole_id for pole in dark))
    positive_reasons = (
        f"{len(dark)}/{len(observable)} recently healthy observable poles are DARK",
        f"dark evidence covers {len(affected_branches)} rooted topology branches",
        "no lower live-to-dark span boundary explains loss of the transformer root",
    )
    negative_reasons = (
        ("post-onset LIVE poles inside transformer scope: " + ", ".join(contradictions),)
        if contradictions
        else ()
    )
    return FaultCandidate(
        dt_id=transformer.dt_id,
        feeder_id=snapshot.feeder_id,
        affected_dt_ids=(transformer.dt_id,),
        topology_version=transformer.topology_version,
        analysis_at=snapshot.analysis_at,
        classification=FaultClass.DT_FAULT,
        suspected_asset_type=SuspectedAssetType.DISTRIBUTION_TRANSFORMER,
        suspected_asset_id=transformer.dt_id,
        parent_pole_id=None,
        child_pole_id=None,
        affected_pole_ids=affected_ids,
        precision=LocalizationPrecision.DT_LEVEL,
        topology_source=(topology_spans[0].source if topology_spans else TopologySource.SURVEYED),
        latitude=transformer.latitude,
        longitude=transformer.longitude,
        pin_code=transformer.pin_code,
        confidence_score=score,
        confidence_level="HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW",
        confidence_reason=(
            f"Transformer-wide loss with {len(dark)}/{len(observable)} observable poles DARK "
            f"across {len(affected_branches)} root branches."
        ),
        evidence=CandidateEvidence(
            onset_at=onset_at,
            subtree_pole_ids=tuple(sorted(poles)),
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


def _build_feeder_candidate(
    snapshot: NetworkSnapshot,
    dt_candidates: tuple[FaultCandidate, ...],
    transformers: tuple[FeederTransformerEvidence, ...],
    *,
    live_freshness: timedelta,
    feeder_fault_ratio: float,
    feeder_min_dts: int,
    correlation_window_seconds: float,
) -> FaultCandidate | None:
    eligible_transformers = tuple(
        transformer
        for transformer in transformers
        if any(
            _has_usable_device_evidence(pole, snapshot, live_freshness)
            for pole in transformer.poles
        )
    )
    affected_dt_ids = tuple(sorted(candidate.dt_id for candidate in dt_candidates))
    if not eligible_transformers or len(affected_dt_ids) < feeder_min_dts:
        return None
    affected_ratio = len(affected_dt_ids) / len(eligible_transformers)
    if affected_ratio < feeder_fault_ratio:
        return None

    onset_at = min(candidate.evidence.onset_at for candidate in dt_candidates)
    onset_spread = (
        max(candidate.evidence.onset_at for candidate in dt_candidates) - onset_at
    ).total_seconds()
    correlated = onset_spread <= correlation_window_seconds
    classification = FaultClass.FEEDER_FAULT if correlated else FaultClass.UNCONFIRMED_OUTAGE
    affected_pole_ids = tuple(
        sorted({pole_id for candidate in dt_candidates for pole_id in candidate.affected_pole_ids})
    )
    observable_count = sum(candidate.evidence.observable_pole_count for candidate in dt_candidates)
    dark_count = sum(candidate.evidence.dark_pole_count for candidate in dt_candidates)
    components = ConfidenceComponents(
        topology=25,
        boundary_clarity=20 if correlated else 5,
        downstream_corroboration=round(25 * affected_ratio),
        temporal_coherence=10 if correlated else 0,
        sensor_quality=10,
        contradiction_penalty=0 if correlated else -20,
    )
    score = max(0, min(100, sum(components.as_dict().values())))
    latitude = sum(candidate.latitude for candidate in dt_candidates) / len(dt_candidates)
    longitude = sum(candidate.longitude for candidate in dt_candidates) / len(dt_candidates)
    pin_codes = tuple(
        sorted({candidate.pin_code for candidate in dt_candidates if candidate.pin_code})
    )
    timing_reason = (
        f"DT onsets are correlated within {onset_spread:.3f} seconds"
        if correlated
        else (
            f"DT onset spread of {onset_spread:.3f} seconds exceeds the "
            f"{correlation_window_seconds:.3f}-second feeder correlation window"
        )
    )
    return FaultCandidate(
        dt_id=affected_dt_ids[0],
        feeder_id=snapshot.feeder_id,
        affected_dt_ids=affected_dt_ids,
        topology_version=max(candidate.topology_version for candidate in dt_candidates),
        analysis_at=snapshot.analysis_at,
        classification=classification,
        suspected_asset_type=SuspectedAssetType.FEEDER,
        suspected_asset_id=snapshot.feeder_id,
        parent_pole_id=None,
        child_pole_id=None,
        affected_pole_ids=affected_pole_ids,
        precision=LocalizationPrecision.FEEDER_LEVEL,
        topology_source=(
            TopologySource.INFERRED
            if any(
                candidate.topology_source == TopologySource.INFERRED for candidate in dt_candidates
            )
            else TopologySource.SURVEYED
        ),
        latitude=latitude,
        longitude=longitude,
        pin_code=pin_codes[0] if len(pin_codes) == 1 else None,
        confidence_score=score,
        confidence_level="HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW",
        confidence_reason=(
            f"{len(affected_dt_ids)}/{len(eligible_transformers)} observable DTs are affected; "
            f"{timing_reason}."
        ),
        evidence=CandidateEvidence(
            onset_at=onset_at,
            subtree_pole_ids=affected_pole_ids,
            observable_pole_count=observable_count,
            dark_pole_count=dark_count,
            post_onset_live_contradictions=(),
            pre_onset_live_observations=(),
            dark_observation_spread_seconds=onset_spread,
            positive_reasons=(
                f"{len(affected_dt_ids)}/{len(eligible_transformers)} feeder DTs have DT-wide loss",
                timing_reason,
            ),
            negative_reasons=(() if correlated else (timing_reason,)),
            components=components,
        ),
    )


def _classify_span_candidate(
    snapshot: NetworkSnapshot,
    candidate: FaultCandidate,
    poles: dict[str, PoleEvidence],
    *,
    live_freshness: timedelta,
    schedule_early_grace: timedelta,
    schedule_overrun_grace: timedelta,
) -> FaultCandidate:
    candidate = _apply_scheduled_suppression(
        snapshot,
        candidate,
        early_grace=schedule_early_grace,
        overrun_grace=schedule_overrun_grace,
    )
    if candidate.classification == FaultClass.SCHEDULED_OUTAGE:
        return candidate

    child = poles[candidate.child_pole_id]
    credible_live_descendants = tuple(
        pole_id
        for pole_id in candidate.evidence.post_onset_live_contradictions
        if _is_recent_live(poles[pole_id], snapshot, live_freshness)
    )
    isolated_dark_pole = candidate.evidence.dark_pole_count == 1
    if isolated_dark_pole and credible_live_descendants and child.device is not None:
        reason = (
            f"Pole {child.pole_id} reports DARK while surveyed downstream poles "
            f"{', '.join(credible_live_descendants)} have fresh post-onset LIVE telemetry."
        )
        return replace(
            candidate,
            classification=FaultClass.SENSOR_ANOMALY,
            suspected_asset_type=SuspectedAssetType.DEVICE,
            suspected_asset_id=child.device.device_id,
            precision=LocalizationPrecision.POLE_LEVEL,
            latitude=child.latitude,
            longitude=child.longitude,
            pin_code=child.pin_code,
            confidence_reason=reason,
            evidence=replace(
                candidate.evidence,
                positive_reasons=candidate.evidence.positive_reasons
                + (
                    "surveyed dependency makes downstream LIVE telemetry physically "
                    "inconsistent with an upstream power loss",
                ),
            ),
            suppression=CandidateSuppression(
                reason=reason,
                source="telemetry-consistency-rule",
            ),
        )
    return candidate


def _apply_scheduled_suppression(
    snapshot: NetworkSnapshot,
    candidate: FaultCandidate,
    *,
    early_grace: timedelta,
    overrun_grace: timedelta,
) -> FaultCandidate:
    scheduled_outage = _matching_scheduled_outage(
        snapshot,
        candidate,
        early_grace=early_grace,
        overrun_grace=overrun_grace,
    )
    if scheduled_outage is None:
        return candidate
    reason = (
        f"Observation overlaps scheduled outage {scheduled_outage.outage_id} "
        f"for {scheduled_outage.scope.value} {scheduled_outage.scope_id}: "
        f"{scheduled_outage.reason}"
    )
    return replace(
        candidate,
        classification=FaultClass.SCHEDULED_OUTAGE,
        confidence_reason=reason,
        evidence=replace(
            candidate.evidence,
            positive_reasons=candidate.evidence.positive_reasons
            + (f"scheduled outage {scheduled_outage.outage_id} covers this observation",),
        ),
        suppression=CandidateSuppression(
            reason=reason,
            source=scheduled_outage.source,
            external_id=scheduled_outage.outage_id,
        ),
    )


def _matching_scheduled_outage(
    snapshot: NetworkSnapshot,
    candidate: FaultCandidate,
    *,
    early_grace: timedelta,
    overrun_grace: timedelta,
) -> ScheduledOutageWindow | None:
    scope_priority = {
        ScheduledOutageScope.SPAN: 0,
        ScheduledOutageScope.DISTRIBUTION_TRANSFORMER: 1,
        ScheduledOutageScope.FEEDER: 2,
    }

    def covers(scope: ScheduledOutageScope, scope_id: str) -> bool:
        if scope == ScheduledOutageScope.SPAN:
            return (
                candidate.suspected_asset_type == SuspectedAssetType.SPAN
                and candidate.precision == LocalizationPrecision.EXACT_SPAN
                and scope_id == f"{candidate.parent_pole_id}->{candidate.child_pole_id}"
            )
        if scope == ScheduledOutageScope.DISTRIBUTION_TRANSFORMER:
            return candidate.affected_dt_ids == (scope_id,)
        return scope_id == snapshot.feeder_id

    matches = (
        outage
        for outage in snapshot.scheduled_outages
        if outage.starts_at - early_grace
        <= candidate.evidence.onset_at
        < outage.ends_at + overrun_grace
        and covers(outage.scope, outage.scope_id)
    )
    return next(
        iter(sorted(matches, key=lambda outage: (scope_priority[outage.scope], outage.outage_id))),
        None,
    )


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
    if (
        pole.state != PoleStatus.LIVE
        or pole.state_received_at is None
        or not _has_usable_device_evidence(pole, snapshot, live_freshness)
    ):
        return False
    age = snapshot.analysis_at - pole.state_received_at
    return timedelta(0) <= age <= live_freshness


def _is_credible_dark(
    pole: PoleEvidence,
    snapshot: NetworkSnapshot,
    freshness: timedelta,
) -> bool:
    if (
        pole.state != PoleStatus.DARK
        or pole.state_received_at is None
        or not _has_usable_device_evidence(pole, snapshot, freshness)
    ):
        return False
    age = snapshot.analysis_at - pole.state_received_at
    return timedelta(0) <= age <= freshness


def _has_usable_device_evidence(
    pole: PoleEvidence,
    snapshot: NetworkSnapshot,
    freshness: timedelta,
) -> bool:
    device = pole.device
    if (
        device is None
        or device.status != DeviceHealthStatus.HEALTHY
        or not device.can_report_power_loss
        or device.last_seen_at is None
        or pole.state in (PoleStatus.NO_DEVICE, PoleStatus.STALE, PoleStatus.UNKNOWN)
    ):
        return False
    age = snapshot.analysis_at - device.last_seen_at
    return timedelta(0) <= age <= freshness


def _build_candidate(
    snapshot: NetworkSnapshot,
    boundary: TopologySpan,
    poles: dict[str, PoleEvidence],
    subtree_ids: tuple[str, ...],
    assigned_dark_ids: tuple[str, ...],
) -> FaultCandidate:
    assert boundary.parent_pole_id is not None
    parent = poles[boundary.parent_pole_id]
    child = poles[boundary.child_pole_id]
    assert child.state_received_at is not None
    onset_at = child.state_received_at

    subtree = tuple(poles[pole_id] for pole_id in subtree_ids)
    observable = tuple(
        pole
        for pole in subtree
        if _has_usable_device_evidence(pole, snapshot, DEFAULT_LIVE_FRESHNESS)
    )
    assigned_dark = set(assigned_dark_ids)
    dark = tuple(
        pole
        for pole in observable
        if pole.state == PoleStatus.DARK and pole.pole_id in assigned_dark
    )
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
    if boundary.source == TopologySource.INFERRED:
        components = replace(
            components,
            topology=round(25 * snapshot.topology_quality_score),
            boundary_clarity=20,
        )
    score_cap = 100 if boundary.source == TopologySource.SURVEYED else 79
    score = max(0, min(score_cap, sum(components.as_dict().values())))
    positive_reasons, negative_reasons = _confidence_reasons(
        parent,
        child,
        observable,
        dark,
        contradictions,
        pre_onset_live,
        spread,
    )
    if boundary.source == TopologySource.INFERRED:
        positive_reasons = (
            f"inferred topology quality is {snapshot.topology_quality_score:.2f}",
            *tuple(
                reason
                for reason in positive_reasons
                if reason != "surveyed topology supports exact-span precision"
            ),
        )
        negative_reasons += (
            "geographic topology is inferred and cannot support exact-span precision",
            *snapshot.topology_quality_reasons,
        )
    confidence_level = "HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW"
    provenance_label = "Surveyed" if boundary.source == TopologySource.SURVEYED else "Inferred"
    confidence_reason = (
        f"{provenance_label} live-to-dark boundary with {len(dark)}/{len(observable)} "
        f"dark observable poles and {len(contradictions)} post-onset live contradictions."
    )

    return FaultCandidate(
        dt_id=snapshot.dt_id,
        feeder_id=snapshot.feeder_id,
        affected_dt_ids=(snapshot.dt_id,),
        topology_version=snapshot.topology_version,
        analysis_at=snapshot.analysis_at,
        classification=FaultClass.SPAN_FAULT,
        suspected_asset_type=SuspectedAssetType.SPAN,
        suspected_asset_id=f"{parent.pole_id}->{child.pole_id}",
        parent_pole_id=parent.pole_id,
        child_pole_id=child.pole_id,
        affected_pole_ids=affected_ids,
        precision=(
            LocalizationPrecision.EXACT_SPAN
            if boundary.source == TopologySource.SURVEYED
            else LocalizationPrecision.PROBABLE_SPAN
        ),
        topology_source=boundary.source,
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
            topology_quality_score=snapshot.topology_quality_score,
            topology_quality_tier=snapshot.topology_quality_tier,
            topology_quality_reasons=snapshot.topology_quality_reasons,
        ),
    )


def _build_corridor_candidate(
    snapshot: NetworkSnapshot,
    corridor: LocalizationCorridor,
    poles: dict[str, PoleEvidence],
    subtree_ids: tuple[str, ...],
    assigned_dark_ids: tuple[str, ...],
) -> FaultCandidate:
    upstream = poles[corridor.upstream_pole_id]
    downstream = poles[corridor.downstream_pole_id]
    assert downstream.state_received_at is not None
    onset_at = downstream.state_received_at
    subtree = tuple(poles[pole_id] for pole_id in subtree_ids)
    observable = tuple(
        pole
        for pole in subtree
        if _has_usable_device_evidence(pole, snapshot, DEFAULT_LIVE_FRESHNESS)
    )
    assigned_dark = set(assigned_dark_ids)
    dark = tuple(
        pole
        for pole in observable
        if pole.state == PoleStatus.DARK and pole.pole_id in assigned_dark
    )
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
    unusable_ids = tuple(
        sorted(
            {
                *corridor.skipped_pole_ids,
                *(
                    pole.pole_id
                    for pole in subtree
                    if not _has_usable_device_evidence(pole, snapshot, DEFAULT_LIVE_FRESHNESS)
                ),
            }
        )
    )
    spread = _dark_observation_spread(dark)
    topology_source = _snapshot_topology_source(snapshot)
    components = replace(
        _confidence_components(observable, dark, contradictions, spread),
        topology=round(25 * snapshot.topology_quality_score),
        boundary_clarity=15,
    )
    score = min(79, max(0, sum(components.as_dict().values())))
    affected_ids = tuple(sorted(pole.pole_id for pole in dark))
    negative_reasons = (
        "unusable state evidence prevents an exact span claim: "
        + ", ".join(corridor.skipped_pole_ids),
    )
    if topology_source == TopologySource.INFERRED:
        negative_reasons += (
            "geographic topology is inferred and cannot support exact-span precision",
            *snapshot.topology_quality_reasons,
        )
    if contradictions:
        negative_reasons += (
            "post-onset LIVE contradictions below corridor: " + ", ".join(contradictions),
        )
    return FaultCandidate(
        dt_id=snapshot.dt_id,
        feeder_id=snapshot.feeder_id,
        affected_dt_ids=(snapshot.dt_id,),
        topology_version=snapshot.topology_version,
        analysis_at=snapshot.analysis_at,
        classification=FaultClass.SPAN_FAULT,
        suspected_asset_type=SuspectedAssetType.SPAN,
        suspected_asset_id=(f"{corridor.upstream_pole_id}..{corridor.downstream_pole_id}"),
        parent_pole_id=corridor.upstream_pole_id,
        child_pole_id=corridor.downstream_pole_id,
        affected_pole_ids=affected_ids,
        precision=LocalizationPrecision.CORRIDOR,
        topology_source=topology_source,
        latitude=(upstream.latitude + downstream.latitude) / 2,
        longitude=(upstream.longitude + downstream.longitude) / 2,
        pin_code=downstream.pin_code or upstream.pin_code,
        confidence_score=score,
        confidence_level="MEDIUM" if score >= 50 else "LOW",
        confidence_reason=(
            f"{topology_source.value.title()} corridor from {corridor.upstream_pole_id} LIVE to "
            f"{corridor.downstream_pole_id} DARK; exact boundary is hidden by "
            f"{len(corridor.skipped_pole_ids)} unusable pole observation(s)."
        ),
        evidence=CandidateEvidence(
            onset_at=onset_at,
            subtree_pole_ids=subtree_ids,
            observable_pole_count=len(observable),
            dark_pole_count=len(dark),
            post_onset_live_contradictions=contradictions,
            pre_onset_live_observations=pre_onset_live,
            dark_observation_spread_seconds=spread,
            positive_reasons=(
                f"credible LIVE upper bound at {corridor.upstream_pole_id}",
                f"credible DARK lower bound at {corridor.downstream_pole_id}",
                f"{len(dark)} usable downstream DARK observation(s)",
            ),
            negative_reasons=negative_reasons,
            components=components,
            unusable_pole_ids=unusable_ids,
            corridor=corridor,
            topology_quality_score=snapshot.topology_quality_score,
            topology_quality_tier=snapshot.topology_quality_tier,
            topology_quality_reasons=snapshot.topology_quality_reasons,
        ),
    )


def _build_degraded_dt_candidate(
    snapshot: NetworkSnapshot,
    poles: dict[str, PoleEvidence],
    dark_ids: tuple[str, ...],
    freshness: timedelta,
) -> FaultCandidate:
    dark = tuple(poles[pole_id] for pole_id in dark_ids)
    onset_at = min(pole.state_received_at for pole in dark if pole.state_received_at is not None)
    observable = tuple(
        pole
        for pole in sorted(poles.values(), key=lambda item: item.pole_id)
        if _has_usable_device_evidence(pole, snapshot, freshness)
    )
    unusable_ids = tuple(
        pole.pole_id
        for pole in sorted(poles.values(), key=lambda item: item.pole_id)
        if not _has_usable_device_evidence(pole, snapshot, freshness)
    )
    spread = _dark_observation_spread(dark)
    topology_source = _snapshot_topology_source(snapshot)
    components = replace(
        _confidence_components(observable, dark, (), spread),
        topology=round(25 * snapshot.topology_quality_score),
        boundary_clarity=0,
    )
    score = min(49, max(0, sum(components.as_dict().values())))
    return FaultCandidate(
        dt_id=snapshot.dt_id,
        feeder_id=snapshot.feeder_id,
        affected_dt_ids=(snapshot.dt_id,),
        topology_version=snapshot.topology_version,
        analysis_at=snapshot.analysis_at,
        classification=FaultClass.UNCONFIRMED_OUTAGE,
        suspected_asset_type=SuspectedAssetType.DISTRIBUTION_TRANSFORMER,
        suspected_asset_id=snapshot.dt_id,
        parent_pole_id=None,
        child_pole_id=None,
        affected_pole_ids=dark_ids,
        precision=LocalizationPrecision.DT_LEVEL,
        topology_source=topology_source,
        latitude=snapshot.dt_latitude,
        longitude=snapshot.dt_longitude,
        pin_code=snapshot.dt_pin_code,
        confidence_score=score,
        confidence_level="LOW",
        confidence_reason=(
            f"{len(dark)} credible DARK pole(s) exist, but missing or unhealthy evidence "
            "prevents a defensible live-to-dark corridor bound."
        ),
        evidence=CandidateEvidence(
            onset_at=onset_at,
            subtree_pole_ids=tuple(sorted(poles)),
            observable_pole_count=len(observable),
            dark_pole_count=len(dark),
            post_onset_live_contradictions=(),
            pre_onset_live_observations=(),
            dark_observation_spread_seconds=spread,
            positive_reasons=(
                f"{len(dark)} usable DARK observation(s) confirm an outage inside {snapshot.dt_id}",
            ),
            negative_reasons=(
                "no unique credible upstream LIVE bound exists",
                "precision degraded to transformer level instead of inventing a span",
            ),
            components=components,
            unusable_pole_ids=unusable_ids,
            topology_quality_score=snapshot.topology_quality_score,
            topology_quality_tier=snapshot.topology_quality_tier,
            topology_quality_reasons=snapshot.topology_quality_reasons,
        ),
    )


def _snapshot_topology_source(snapshot: NetworkSnapshot) -> TopologySource:
    sources = {span.source for span in snapshot.spans}
    if len(sources) == 1:
        return next(iter(sources))
    if snapshot.inference_version is not None:
        return TopologySource.INFERRED
    return TopologySource.SURVEYED


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
