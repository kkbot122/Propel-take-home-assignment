import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import aliased

from propel.analysis.localization import localize_known_topology
from propel.analysis.models import (
    DeviceEvidence,
    FaultCandidate,
    FeederTransformerEvidence,
    NetworkSnapshot,
    PoleEvidence,
    ScheduledOutageWindow,
    TopologySpan,
)
from propel.domain.enums import DeviceHealthStatus, PoleStatus, ScheduledOutageScope
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Feeder,
    Pole,
    PoleState,
    ScheduledOutage,
    TopologyEdge,
)
from propel.topology.models import RecordedTopologyEdge, TopologyPole, TopologyRequest
from propel.topology.providers import CompositeTopologyProvider, TopologyProvider

logger = logging.getLogger(__name__)
SCHEDULE_LOAD_HORIZON = timedelta(days=1)
MAX_RELEVANT_SCHEDULES = 100

CLAIM_DUE_SCRIPT = """
local entry = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
if #entry == 0 then
    return {}
end
if tonumber(entry[2]) > tonumber(ARGV[1]) then
    return {}
end
redis.call('ZREM', KEYS[1], entry[1])
return entry
"""


class UnknownDistributionTransformerError(Exception):
    pass


class DtSnapshotRepository(Protocol):
    async def load(self, dt_id: str) -> NetworkSnapshot: ...


class FaultCandidateSink(Protocol):
    async def persist_candidates(self, candidates: Sequence[FaultCandidate]) -> object: ...


class PostgresDtSnapshotRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def load(self, dt_id: str) -> NetworkSnapshot:
        async with self._engine.connect() as connection:
            await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                analysis_at = await connection.scalar(select(func.now()))
                transformer_row = (
                    await connection.execute(
                        select(
                            DistributionTransformer.id,
                            DistributionTransformer.latitude,
                            DistributionTransformer.longitude,
                            DistributionTransformer.pin_code,
                            Feeder.id.label("feeder_internal_id"),
                            Feeder.feeder_id,
                        )
                        .join(Feeder, Feeder.id == DistributionTransformer.feeder_id)
                        .where(DistributionTransformer.dt_id == dt_id)
                    )
                ).one_or_none()
                if analysis_at is None or transformer_row is None:
                    raise UnknownDistributionTransformerError(dt_id)
                feeder_id = transformer_row.feeder_id

                pole_rows = (
                    await connection.execute(
                        select(
                            DistributionTransformer.dt_id.label("dt_external_id"),
                            DistributionTransformer.latitude.label("dt_latitude"),
                            DistributionTransformer.longitude.label("dt_longitude"),
                            DistributionTransformer.pin_code.label("dt_pin_code"),
                            Pole.pole_id,
                            Pole.latitude,
                            Pole.longitude,
                            Pole.pin_code,
                            PoleState.state,
                            PoleState.received_at.label("state_received_at"),
                            Device.device_id,
                            DeviceHealth.status.label("device_status"),
                            DeviceHealth.last_seen_at,
                            DeviceHealth.can_report_power_loss,
                            DeviceHealth.firmware,
                            DeviceHealth.battery_mv,
                            DeviceHealth.rssi,
                        )
                        .select_from(DistributionTransformer)
                        .join(Pole, Pole.dt_id == DistributionTransformer.id)
                        .outerjoin(PoleState, PoleState.pole_id == Pole.id)
                        .outerjoin(
                            DeviceBinding,
                            and_(
                                DeviceBinding.pole_id == Pole.id,
                                DeviceBinding.valid_from <= analysis_at,
                                or_(
                                    DeviceBinding.valid_to.is_(None),
                                    DeviceBinding.valid_to > analysis_at,
                                ),
                            ),
                        )
                        .outerjoin(Device, Device.id == DeviceBinding.device_id)
                        .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
                        .where(
                            DistributionTransformer.feeder_id == transformer_row.feeder_internal_id
                        )
                        .order_by(DistributionTransformer.dt_id, Pole.pole_id)
                    )
                ).all()

                latest_topology = (
                    select(
                        TopologyEdge.dt_id,
                        func.max(TopologyEdge.topology_version).label("topology_version"),
                    )
                    .group_by(TopologyEdge.dt_id)
                    .subquery()
                )
                parent = aliased(Pole)
                child = aliased(Pole)
                span_rows = (
                    await connection.execute(
                        select(
                            DistributionTransformer.dt_id.label("dt_external_id"),
                            TopologyEdge.topology_version,
                            parent.pole_id.label("parent_pole_id"),
                            child.pole_id.label("child_pole_id"),
                            TopologyEdge.source,
                            TopologyEdge.distance_m,
                            TopologyEdge.edge_confidence,
                            TopologyEdge.inference_version,
                        )
                        .select_from(TopologyEdge)
                        .join(
                            latest_topology,
                            and_(
                                latest_topology.c.dt_id == TopologyEdge.dt_id,
                                latest_topology.c.topology_version == TopologyEdge.topology_version,
                            ),
                        )
                        .join(
                            DistributionTransformer,
                            DistributionTransformer.id == TopologyEdge.dt_id,
                        )
                        .outerjoin(parent, parent.id == TopologyEdge.parent_pole_id)
                        .join(child, child.id == TopologyEdge.child_pole_id)
                        .where(
                            DistributionTransformer.feeder_id == transformer_row.feeder_internal_id
                        )
                        .order_by(
                            DistributionTransformer.dt_id,
                            parent.pole_id.nullsfirst(),
                            child.pole_id,
                        )
                    )
                ).all()

                poles_by_dt: dict[str, list[PoleEvidence]] = defaultdict(list)
                transformer_metadata: dict[str, tuple[float, float, str | None]] = {}
                for row in pole_rows:
                    poles_by_dt[row.dt_external_id].append(self._pole_evidence(row))
                    transformer_metadata[row.dt_external_id] = (
                        row.dt_latitude,
                        row.dt_longitude,
                        row.dt_pin_code,
                    )

                spans_by_dt: dict[str, list[TopologySpan]] = defaultdict(list)
                versions_by_dt: dict[str, int] = defaultdict(int)
                for row in span_rows:
                    versions_by_dt[row.dt_external_id] = row.topology_version
                    spans_by_dt[row.dt_external_id].append(
                        TopologySpan(
                            parent_pole_id=row.parent_pole_id,
                            child_pole_id=row.child_pole_id,
                            source=row.source,
                            edge_confidence=row.edge_confidence,
                            distance_m=row.distance_m,
                            inference_version=row.inference_version,
                        )
                    )

                transformers = tuple(
                    FeederTransformerEvidence(
                        dt_id=transformer_external_id,
                        latitude=metadata[0],
                        longitude=metadata[1],
                        pin_code=metadata[2],
                        topology_version=versions_by_dt[transformer_external_id],
                        poles=tuple(poles_by_dt[transformer_external_id]),
                        spans=tuple(spans_by_dt[transformer_external_id]),
                    )
                    for transformer_external_id, metadata in sorted(transformer_metadata.items())
                )
                focal_transformer = next(
                    transformer for transformer in transformers if transformer.dt_id == dt_id
                )
                span_scope_ids = tuple(
                    f"{row.parent_pole_id}->{row.child_pole_id}"
                    for row in span_rows
                    if row.parent_pole_id is not None
                )
                relevant_scope = or_(
                    and_(
                        ScheduledOutage.scope == ScheduledOutageScope.FEEDER,
                        ScheduledOutage.scope_id == feeder_id,
                    ),
                    and_(
                        ScheduledOutage.scope == ScheduledOutageScope.DISTRIBUTION_TRANSFORMER,
                        ScheduledOutage.scope_id.in_(tuple(poles_by_dt)),
                    ),
                    and_(
                        ScheduledOutage.scope == ScheduledOutageScope.SPAN,
                        ScheduledOutage.scope_id.in_(span_scope_ids),
                    ),
                )
                scheduled_outage_rows = (
                    await connection.execute(
                        select(
                            ScheduledOutage.outage_id,
                            ScheduledOutage.scope,
                            ScheduledOutage.scope_id,
                            ScheduledOutage.starts_at,
                            ScheduledOutage.ends_at,
                            ScheduledOutage.source,
                            ScheduledOutage.reason,
                        )
                        .where(
                            relevant_scope,
                            ScheduledOutage.starts_at <= analysis_at + SCHEDULE_LOAD_HORIZON,
                            ScheduledOutage.ends_at > analysis_at - SCHEDULE_LOAD_HORIZON,
                        )
                        .order_by(ScheduledOutage.starts_at, ScheduledOutage.outage_id)
                        .limit(MAX_RELEVANT_SCHEDULES)
                    )
                ).all()

        return NetworkSnapshot(
            dt_id=dt_id,
            feeder_id=feeder_id,
            dt_latitude=transformer_row.latitude,
            dt_longitude=transformer_row.longitude,
            dt_pin_code=transformer_row.pin_code,
            topology_version=focal_transformer.topology_version,
            analysis_at=analysis_at,
            poles=focal_transformer.poles,
            spans=focal_transformer.spans,
            scheduled_outages=tuple(
                ScheduledOutageWindow(
                    outage_id=outage.outage_id,
                    scope=outage.scope,
                    scope_id=outage.scope_id,
                    starts_at=outage.starts_at,
                    ends_at=outage.ends_at,
                    source=outage.source,
                    reason=outage.reason,
                )
                for outage in scheduled_outage_rows
            ),
            feeder_transformers=transformers,
        )

    @staticmethod
    def _pole_evidence(row: Any) -> PoleEvidence:
        device = None
        if row.device_id is not None:
            device = DeviceEvidence(
                device_id=row.device_id,
                status=row.device_status or DeviceHealthStatus.UNKNOWN,
                last_seen_at=row.last_seen_at,
                can_report_power_loss=(
                    row.can_report_power_loss if row.can_report_power_loss is not None else False
                ),
                firmware=row.firmware,
                battery_mv=row.battery_mv,
                rssi=row.rssi,
            )
        if device is None:
            state = PoleStatus.NO_DEVICE
        else:
            state = row.state or PoleStatus.UNKNOWN
        return PoleEvidence(
            pole_id=row.pole_id,
            latitude=row.latitude,
            longitude=row.longitude,
            pin_code=row.pin_code,
            state=state,
            state_received_at=row.state_received_at,
            device=device,
        )


class RedisAnalysisScheduler:
    def __init__(
        self,
        redis_client: Redis,
        snapshot_repository: DtSnapshotRepository,
        *,
        due_set_name: str,
        live_freshness_seconds: float,
        retry_delay_seconds: float,
        dt_fault_ratio: float = 0.6,
        dt_min_branches: int = 2,
        feeder_fault_ratio: float = 0.6,
        feeder_min_dts: int = 2,
        correlation_window_seconds: float = 10,
        schedule_early_grace_seconds: float = 600,
        schedule_overrun_grace_seconds: float = 2_400,
        candidate_sink: FaultCandidateSink | None = None,
        topology_provider: TopologyProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis = redis_client
        self._snapshot_repository = snapshot_repository
        self._due_set_name = due_set_name
        self._live_freshness = timedelta(seconds=live_freshness_seconds)
        self._schedule_early_grace = timedelta(seconds=schedule_early_grace_seconds)
        self._schedule_overrun_grace = timedelta(seconds=schedule_overrun_grace_seconds)
        self._dt_fault_ratio = dt_fault_ratio
        self._dt_min_branches = dt_min_branches
        self._feeder_fault_ratio = feeder_fault_ratio
        self._feeder_min_dts = feeder_min_dts
        self._correlation_window_seconds = correlation_window_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._candidate_sink = candidate_sink
        self._topology_provider = topology_provider or CompositeTopologyProvider()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._claim_script = redis_client.register_script(CLAIM_DUE_SCRIPT)

    async def run_due_once(self) -> list[FaultCandidate]:
        claimed = await self._claim_script(
            keys=[self._due_set_name],
            args=[self._clock().timestamp()],
            client=self._redis,
        )
        if not claimed:
            return []
        dt_id = str(claimed[0])
        try:
            snapshot = await self._snapshot_repository.load(dt_id)
            snapshot = self._resolve_topology(snapshot)
            candidates = localize_known_topology(
                snapshot,
                live_freshness=self._live_freshness,
                schedule_early_grace=self._schedule_early_grace,
                schedule_overrun_grace=self._schedule_overrun_grace,
                dt_fault_ratio=self._dt_fault_ratio,
                dt_min_branches=self._dt_min_branches,
                feeder_fault_ratio=self._feeder_fault_ratio,
                feeder_min_dts=self._feeder_min_dts,
                correlation_window_seconds=self._correlation_window_seconds,
            )
            if self._candidate_sink is not None and candidates:
                await self._candidate_sink.persist_candidates(candidates)
        except Exception as error:
            retry_at = self._clock().timestamp() + self._retry_delay_seconds
            await self._redis.zadd(self._due_set_name, {dt_id: retry_at}, gt=True)
            logger.warning(
                json.dumps(
                    {
                        "event": "dt_analysis_failed",
                        "dt_id": dt_id,
                        "error_type": type(error).__name__,
                        "retry_at": retry_at,
                    }
                )
            )
            return []

        self._log_result(snapshot, candidates)
        return candidates

    def _resolve_topology(self, snapshot: NetworkSnapshot) -> NetworkSnapshot:
        topology = self._topology_provider.provide(
            TopologyRequest(
                dt_id=snapshot.dt_id,
                dt_latitude=snapshot.dt_latitude,
                dt_longitude=snapshot.dt_longitude,
                topology_version=max(1, snapshot.topology_version),
                poles=tuple(
                    TopologyPole(pole.pole_id, pole.latitude, pole.longitude)
                    for pole in snapshot.poles
                ),
                recorded_edges=tuple(
                    RecordedTopologyEdge(
                        parent_pole_id=span.parent_pole_id,
                        child_pole_id=span.child_pole_id,
                        source=span.source,
                        distance_m=span.distance_m,
                        edge_confidence=span.edge_confidence,
                        inference_version=span.inference_version,
                    )
                    for span in snapshot.spans
                ),
            )
        )
        return replace(
            snapshot,
            topology_version=topology.topology_version,
            spans=tuple(
                TopologySpan(
                    parent_pole_id=edge.parent_pole_id,
                    child_pole_id=edge.child_pole_id,
                    source=edge.source,
                    edge_confidence=edge.edge_confidence,
                    distance_m=edge.distance_m,
                    inference_version=edge.inference_version,
                )
                for edge in topology.edges
            ),
            topology_quality_score=topology.quality.score,
            topology_quality_tier=topology.quality.tier.value,
            topology_quality_reasons=topology.quality.limiting_factors,
            inference_version=topology.inference_version,
        )

    @staticmethod
    def _log_result(snapshot: NetworkSnapshot, candidates: list[FaultCandidate]) -> None:
        if not candidates:
            logger.info(
                json.dumps(
                    {
                        "event": "dt_analysis_completed",
                        "dt_id": snapshot.dt_id,
                        "topology_version": snapshot.topology_version,
                        "candidate_count": 0,
                    }
                )
            )
            return
        for candidate in candidates:
            logger.info(
                json.dumps(
                    {
                        "event": "fault_candidate_localized",
                        "feeder_id": candidate.feeder_id,
                        "dt_id": candidate.dt_id,
                        "classification": candidate.classification.value,
                        "suspected_asset_id": candidate.suspected_asset_id,
                        "affected_pole_ids": candidate.affected_pole_ids,
                        "precision": candidate.precision.value,
                        "topology_source": candidate.topology_source.value,
                        "confidence_score": candidate.confidence_score,
                        "evidence": candidate.evidence.as_dict(),
                    }
                )
            )
