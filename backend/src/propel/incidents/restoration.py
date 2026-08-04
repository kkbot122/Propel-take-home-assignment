from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from propel.domain.enums import PoleStatus

REPAIR_NOT_VERIFIED = "REPAIR_NOT_VERIFIED"
RESTORATION_VERIFIED = "RESTORATION_VERIFIED"


def required_span_restoration_pole_id(
    suspected_asset_id: str,
    incident_evidence: Mapping[str, object],
) -> str | None:
    candidate = incident_evidence.get("candidate")
    if isinstance(candidate, dict):
        corridor = candidate.get("corridor")
        if isinstance(corridor, dict):
            downstream = corridor.get("downstream_pole_id")
            if isinstance(downstream, str) and downstream:
                return downstream
    _, separator, child_pole_id = suspected_asset_id.partition("->")
    return child_pole_id if separator and child_pole_id else None


@dataclass(frozen=True, slots=True)
class RestorationPoleEvidence:
    pole_id: str
    eligible: bool
    is_boundary_child: bool
    state: PoleStatus
    received_at: datetime | None
    device_timestamp: datetime | None
    exclusion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RestorationDecision:
    verified: bool
    reason: str
    eligible_count: int
    live_count: int
    remaining_dark_count: int
    stable_since: datetime | None


def restoration_decision(
    evidence: tuple[RestorationPoleEvidence, ...],
    *,
    repair_claimed_at: datetime,
    evaluated_at: datetime,
    threshold: float,
    stabilization_seconds: float,
) -> RestorationDecision:
    eligible = tuple(item for item in evidence if item.eligible)
    fresh_live = tuple(
        item
        for item in eligible
        if item.state == PoleStatus.LIVE
        and item.received_at is not None
        and item.received_at > repair_claimed_at
        and item.device_timestamp is not None
        and item.device_timestamp > repair_claimed_at
    )
    remaining_dark_count = len(eligible) - len(fresh_live)
    common = {
        "eligible_count": len(eligible),
        "live_count": len(fresh_live),
        "remaining_dark_count": remaining_dark_count,
    }
    if not eligible:
        return RestorationDecision(False, REPAIR_NOT_VERIFIED, stable_since=None, **common)

    required_roots = tuple(item for item in evidence if item.is_boundary_child)
    if not required_roots or any(item not in fresh_live for item in required_roots):
        return RestorationDecision(False, REPAIR_NOT_VERIFIED, stable_since=None, **common)

    required_live_count = ceil(len(eligible) * threshold)
    if len(fresh_live) < required_live_count:
        return RestorationDecision(False, REPAIR_NOT_VERIFIED, stable_since=None, **common)

    stable_since = max(item.received_at for item in fresh_live if item.received_at is not None)
    if evaluated_at < stable_since + timedelta(seconds=stabilization_seconds):
        return RestorationDecision(
            False, "RESTORATION_STABILIZING", stable_since=stable_since, **common
        )
    return RestorationDecision(True, RESTORATION_VERIFIED, stable_since=stable_since, **common)
