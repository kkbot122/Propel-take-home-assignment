import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from propel.analysis.models import FaultCandidate
from propel.domain.enums import (
    DeviceHealthStatus,
    IncidentStatus,
    PoleStatus,
    SuspectedAssetType,
    TicketStatus,
)
from propel.incidents.models import (
    IncidentTicketReference,
    IncidentView,
    NetworkOverviewView,
    NetworkPoleView,
    NetworkSpanView,
    NetworkSubstationView,
    NetworkTopologyView,
    NetworkTransformerView,
    TicketEventView,
    TicketView,
)
from propel.incidents.restoration import (
    REPAIR_NOT_VERIFIED,
    RESTORATION_VERIFIED,
    RestorationPoleEvidence,
    required_span_restoration_pole_id,
    restoration_decision,
)
from propel.incidents.workflow import incident_fingerprint, require_operator_transition
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Feeder,
    Incident,
    IncidentPole,
    Pole,
    PoleState,
    Substation,
    Ticket,
    TicketEvent,
    TicketRestorationPole,
    TopologyEdge,
)

logger = logging.getLogger(__name__)


class IncidentStoreUnavailableError(Exception):
    pass


class IncidentNotFoundError(Exception):
    pass


class TicketNotFoundError(Exception):
    pass


class NetworkTransformerNotFoundError(Exception):
    pass


class NetworkFeederNotFoundError(Exception):
    pass


class UnknownCandidatePoleError(Exception):
    pass


class PostgresIncidentService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def persist_candidates(
        self, candidates: Sequence[FaultCandidate]
    ) -> list[IncidentTicketReference]:
        ordered_candidates = tuple(sorted(candidates, key=incident_fingerprint))
        try:
            async with self._session_factory.begin() as session:
                references = [
                    await self._persist_candidate(session, candidate)
                    for candidate in ordered_candidates
                ]
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        for reference in references:
            logger.info(
                json.dumps(
                    {
                        "event": "incident_candidate_persisted",
                        "incident_id": str(reference.incident_id),
                        "ticket_id": (
                            str(reference.ticket_id) if reference.ticket_id is not None else None
                        ),
                        "fingerprint": reference.fingerprint,
                    }
                )
            )
        return references

    async def _persist_candidate(
        self,
        session: AsyncSession,
        candidate: FaultCandidate,
    ) -> IncidentTicketReference:
        fingerprint = incident_fingerprint(candidate)
        incident_status = (
            IncidentStatus.SUPPRESSED
            if candidate.suppression is not None
            else IncidentStatus.ACTIVE
        )
        evidence = {
            "analysis_at": candidate.analysis_at.isoformat(),
            "topology_version": candidate.topology_version,
            "topology_source": candidate.topology_source.value,
            "confidence_level": candidate.confidence_level,
            "feeder_id": candidate.feeder_id,
            "affected_dt_ids": list(candidate.affected_dt_ids),
            "candidate": candidate.evidence.as_dict(),
            "suppression": (
                candidate.suppression.as_dict() if candidate.suppression is not None else None
            ),
        }
        incident_insert = insert(Incident).values(
            fingerprint=fingerprint,
            status=incident_status,
            classification=candidate.classification,
            suspected_asset_type=candidate.suspected_asset_type,
            suspected_asset_id=candidate.suspected_asset_id,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            pin_code=candidate.pin_code,
            affected_pole_count=len(candidate.affected_pole_ids),
            precision=candidate.precision,
            confidence_score=candidate.confidence_score,
            confidence_reason=candidate.confidence_reason,
            evidence=evidence,
            suppression_reason=(
                candidate.suppression.reason if candidate.suppression is not None else None
            ),
            suppression_source=(
                candidate.suppression.source if candidate.suppression is not None else None
            ),
            suppression_external_id=(
                candidate.suppression.external_id if candidate.suppression is not None else None
            ),
            detected_at=candidate.analysis_at,
            updated_at=candidate.analysis_at,
        )
        incident_id = await session.scalar(
            incident_insert.on_conflict_do_update(
                index_elements=[Incident.fingerprint],
                index_where=text("status IN ('ACTIVE', 'SUPPRESSED')"),
                set_={
                    "classification": incident_insert.excluded.classification,
                    "suspected_asset_type": incident_insert.excluded.suspected_asset_type,
                    "suspected_asset_id": incident_insert.excluded.suspected_asset_id,
                    "latitude": incident_insert.excluded.latitude,
                    "longitude": incident_insert.excluded.longitude,
                    "pin_code": incident_insert.excluded.pin_code,
                    "precision": incident_insert.excluded.precision,
                    "confidence_score": incident_insert.excluded.confidence_score,
                    "confidence_reason": incident_insert.excluded.confidence_reason,
                    "evidence": incident_insert.excluded.evidence,
                    "suppression_reason": incident_insert.excluded.suppression_reason,
                    "suppression_source": incident_insert.excluded.suppression_source,
                    "suppression_external_id": incident_insert.excluded.suppression_external_id,
                    "updated_at": incident_insert.excluded.updated_at,
                },
                where=incident_insert.excluded.updated_at >= Incident.updated_at,
            ).returning(Incident.incident_id)
        )
        candidate_applied = incident_id is not None
        if not candidate_applied:
            incident_id = await session.scalar(
                select(Incident.incident_id)
                .where(
                    Incident.fingerprint == fingerprint,
                    Incident.status.in_((IncidentStatus.ACTIVE, IncidentStatus.SUPPRESSED)),
                )
                .with_for_update()
            )
        if incident_id is None:
            raise RuntimeError("active incident upsert did not return an incident")

        pole_rows = (
            await session.execute(
                select(Pole.id, Pole.pole_id)
                .join(DistributionTransformer, DistributionTransformer.id == Pole.dt_id)
                .where(
                    DistributionTransformer.dt_id.in_(candidate.affected_dt_ids),
                    Pole.pole_id.in_(candidate.affected_pole_ids),
                )
            )
        ).all()
        pole_ids = {row.pole_id: row.id for row in pole_rows}
        if set(pole_ids) != set(candidate.affected_pole_ids):
            raise UnknownCandidatePoleError
        if candidate_applied:
            for pole_id in candidate.affected_pole_ids:
                await session.execute(
                    insert(IncidentPole)
                    .values(
                        incident_id=incident_id,
                        pole_id=pole_ids[pole_id],
                        first_observed_at=candidate.evidence.onset_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[IncidentPole.incident_id, IncidentPole.pole_id]
                    )
                )
            affected_count = await session.scalar(
                select(func.count(IncidentPole.pole_id)).where(
                    IncidentPole.incident_id == incident_id
                )
            )
            await session.execute(
                update(Incident)
                .where(Incident.incident_id == incident_id)
                .values(affected_pole_count=affected_count or 0)
            )

        if candidate.suppression is not None:
            return IncidentTicketReference(
                incident_id=incident_id,
                ticket_id=None,
                fingerprint=fingerprint,
            )

        ticket_id = await session.scalar(
            insert(Ticket)
            .values(
                incident_id=incident_id,
                status=TicketStatus.DETECTED,
                created_at=candidate.analysis_at,
                updated_at=candidate.analysis_at,
            )
            .on_conflict_do_nothing(constraint="uq_tickets_incident_id")
            .returning(Ticket.ticket_id)
        )
        if ticket_id is not None:
            await session.execute(
                insert(TicketEvent).values(
                    ticket_id=ticket_id,
                    from_status=None,
                    to_status=TicketStatus.DETECTED,
                    actor="propel-analysis",
                    reason="actionable fault candidate detected",
                    occurred_at=candidate.analysis_at,
                    details={"fingerprint": fingerprint},
                )
            )
        else:
            ticket_id = await session.scalar(
                select(Ticket.ticket_id).where(Ticket.incident_id == incident_id)
            )
        if ticket_id is None:
            raise RuntimeError("incident ticket upsert did not return a ticket")
        return IncidentTicketReference(
            incident_id=incident_id,
            ticket_id=ticket_id,
            fingerprint=fingerprint,
        )

    async def list_incidents(
        self,
        *,
        status: IncidentStatus = IncidentStatus.ACTIVE,
        limit: int = 100,
    ) -> list[IncidentView]:
        try:
            async with self._session_factory() as session:
                return await self._incident_views(session, status=status, limit=limit)
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error

    async def get_incident(self, incident_id: UUID) -> IncidentView:
        try:
            async with self._session_factory() as session:
                views = await self._incident_views(session, incident_id=incident_id, limit=1)
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        if not views:
            raise IncidentNotFoundError
        return views[0]

    async def _incident_views(
        self,
        session: AsyncSession,
        *,
        status: IncidentStatus | None = None,
        incident_id: UUID | None = None,
        limit: int,
    ) -> list[IncidentView]:
        statement = select(Incident, Ticket).outerjoin(
            Ticket, Ticket.incident_id == Incident.incident_id
        )
        if status is not None:
            statement = statement.where(Incident.status == status)
        if incident_id is not None:
            statement = statement.where(Incident.incident_id == incident_id)
        rows = (
            await session.execute(
                statement.order_by(Incident.detected_at.desc(), Incident.incident_id).limit(limit)
            )
        ).all()
        incident_ids = [row[0].incident_id for row in rows]
        affected: dict[UUID, list[str]] = {item: [] for item in incident_ids}
        if incident_ids:
            affected_rows = (
                await session.execute(
                    select(IncidentPole.incident_id, Pole.pole_id)
                    .join(Pole, Pole.id == IncidentPole.pole_id)
                    .where(IncidentPole.incident_id.in_(incident_ids))
                    .order_by(IncidentPole.incident_id, Pole.pole_id)
                )
            ).all()
            for affected_row in affected_rows:
                affected[affected_row.incident_id].append(affected_row.pole_id)
        return [
            self._incident_view(incident, ticket, tuple(affected[incident.incident_id]))
            for incident, ticket in rows
        ]

    @staticmethod
    def _incident_view(
        incident: Incident,
        ticket: Ticket | None,
        affected_pole_ids: tuple[str, ...],
    ) -> IncidentView:
        return IncidentView(
            incident_id=incident.incident_id,
            fingerprint=incident.fingerprint,
            status=incident.status,
            classification=incident.classification,
            suspected_asset_type=incident.suspected_asset_type,
            suspected_asset_id=incident.suspected_asset_id,
            latitude=incident.latitude,
            longitude=incident.longitude,
            pin_code=incident.pin_code,
            affected_pole_count=incident.affected_pole_count,
            affected_pole_ids=affected_pole_ids,
            precision=incident.precision,
            confidence_score=incident.confidence_score,
            confidence_reason=incident.confidence_reason,
            evidence=incident.evidence,
            suppression_reason=incident.suppression_reason,
            suppression_source=incident.suppression_source,
            suppression_external_id=incident.suppression_external_id,
            detected_at=incident.detected_at,
            updated_at=incident.updated_at,
            resolved_at=incident.resolved_at,
            ticket_id=ticket.ticket_id if ticket else None,
            ticket_status=ticket.status if ticket else None,
            assigned_crew=ticket.assigned_crew if ticket else None,
        )

    async def get_ticket(self, ticket_id: UUID) -> TicketView:
        try:
            async with self._session_factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                if ticket is None:
                    raise TicketNotFoundError
                return await self._ticket_view(session, ticket)
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error

    async def transition_ticket(
        self,
        ticket_id: UUID,
        requested_status: TicketStatus,
        *,
        actor: str,
        reason: str | None,
        assigned_crew: str | None = None,
    ) -> TicketView:
        occurred_at = self._clock()
        try:
            async with self._session_factory.begin() as session:
                ticket = await session.scalar(
                    select(Ticket).where(Ticket.ticket_id == ticket_id).with_for_update()
                )
                if ticket is None:
                    raise TicketNotFoundError
                previous_status = ticket.status
                require_operator_transition(previous_status, requested_status)
                if requested_status == TicketStatus.CREW_ASSIGNED:
                    if assigned_crew is None:
                        raise ValueError("assigned crew is required")
                    ticket.assigned_crew = assigned_crew
                if requested_status == TicketStatus.RESOLVED:
                    ticket.resolution_claimed_at = occurred_at
                    await self._freeze_restoration_set(session, ticket, occurred_at)
                ticket.status = requested_status
                ticket.updated_at = occurred_at
                details = {"assigned_crew": assigned_crew} if assigned_crew else {}
                session.add(
                    TicketEvent(
                        ticket_id=ticket.ticket_id,
                        from_status=previous_status,
                        to_status=requested_status,
                        actor=actor,
                        reason=reason,
                        occurred_at=occurred_at,
                        details=details,
                    )
                )
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        view = await self.get_ticket(ticket_id)
        logger.info(
            json.dumps(
                {
                    "event": "ticket_transitioned",
                    "ticket_id": str(ticket_id),
                    "incident_id": str(view.incident_id),
                    "from_status": previous_status.value,
                    "to_status": requested_status.value,
                    "actor": actor,
                }
            )
        )
        return view

    async def _freeze_restoration_set(
        self,
        session: AsyncSession,
        ticket: Ticket,
        frozen_at: datetime,
    ) -> None:
        incident = await session.get(Incident, ticket.incident_id)
        if incident is None:
            raise RuntimeError("ticket incident does not exist")
        rows = (
            await session.execute(
                select(
                    Pole.id,
                    Pole.pole_id,
                    Device.id.label("device_id"),
                    DeviceHealth.status.label("health_status"),
                )
                .join(IncidentPole, IncidentPole.pole_id == Pole.id)
                .outerjoin(
                    DeviceBinding,
                    (DeviceBinding.pole_id == Pole.id) & DeviceBinding.valid_to.is_(None),
                )
                .outerjoin(Device, Device.id == DeviceBinding.device_id)
                .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
                .where(IncidentPole.incident_id == ticket.incident_id)
                .order_by(Pole.pole_id)
            )
        ).all()
        required_pole_ids: set[str]
        if incident.suspected_asset_type == SuspectedAssetType.SPAN:
            required_span_pole_id = required_span_restoration_pole_id(
                incident.suspected_asset_id,
                incident.evidence,
            )
            required_pole_ids = {required_span_pole_id} if required_span_pole_id else set()
        else:
            incident_pole_ids = tuple(row.id for row in rows)
            latest_topology = (
                select(
                    TopologyEdge.dt_id,
                    func.max(TopologyEdge.topology_version).label("topology_version"),
                )
                .group_by(TopologyEdge.dt_id)
                .subquery()
            )
            required_pole_ids = set(
                await session.scalars(
                    select(Pole.pole_id)
                    .join(TopologyEdge, TopologyEdge.child_pole_id == Pole.id)
                    .join(
                        latest_topology,
                        (latest_topology.c.dt_id == TopologyEdge.dt_id)
                        & (latest_topology.c.topology_version == TopologyEdge.topology_version),
                    )
                    .where(
                        Pole.id.in_(incident_pole_ids),
                        TopologyEdge.parent_pole_id.is_(None),
                    )
                )
            )
        eligible_count = 0
        for row in rows:
            eligible = row.device_id is not None and row.health_status == DeviceHealthStatus.HEALTHY
            if row.device_id is None:
                exclusion_reason = "NO_DEVICE"
            elif row.health_status != DeviceHealthStatus.HEALTHY:
                exclusion_reason = "DEVICE_UNHEALTHY"
            else:
                exclusion_reason = None
                eligible_count += 1
            session.add(
                TicketRestorationPole(
                    ticket_id=ticket.ticket_id,
                    pole_id=row.id,
                    eligible=eligible,
                    is_boundary_child=row.pole_id in required_pole_ids,
                    exclusion_reason=exclusion_reason,
                    frozen_at=frozen_at,
                )
            )
        ticket.restoration_status = REPAIR_NOT_VERIFIED
        ticket.remaining_dark_count = eligible_count

    async def verify_restorations_once(
        self,
        *,
        threshold: float,
        stabilization_seconds: float,
        limit: int = 100,
    ) -> int:
        evaluated_at = self._clock()
        verified_ticket_ids: list[UUID] = []
        try:
            async with self._session_factory.begin() as session:
                tickets = (
                    await session.scalars(
                        select(Ticket)
                        .where(Ticket.status == TicketStatus.RESOLVED)
                        .order_by(Ticket.resolution_claimed_at, Ticket.ticket_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                for ticket in tickets:
                    if ticket.resolution_claimed_at is None:
                        continue
                    rows = (
                        await session.execute(
                            select(
                                Pole.pole_id,
                                TicketRestorationPole.eligible,
                                TicketRestorationPole.is_boundary_child,
                                TicketRestorationPole.exclusion_reason,
                                PoleState.state,
                                PoleState.received_at,
                                PoleState.device_timestamp,
                            )
                            .join(Pole, Pole.id == TicketRestorationPole.pole_id)
                            .outerjoin(PoleState, PoleState.pole_id == Pole.id)
                            .where(TicketRestorationPole.ticket_id == ticket.ticket_id)
                            .order_by(Pole.pole_id)
                        )
                    ).all()
                    evidence = tuple(
                        RestorationPoleEvidence(
                            pole_id=row.pole_id,
                            eligible=row.eligible,
                            is_boundary_child=row.is_boundary_child,
                            state=row.state or PoleStatus.UNKNOWN,
                            received_at=row.received_at,
                            device_timestamp=row.device_timestamp,
                            exclusion_reason=row.exclusion_reason,
                        )
                        for row in rows
                    )
                    decision = restoration_decision(
                        evidence,
                        repair_claimed_at=ticket.resolution_claimed_at,
                        evaluated_at=evaluated_at,
                        threshold=threshold,
                        stabilization_seconds=stabilization_seconds,
                    )
                    ticket.restoration_status = decision.reason
                    ticket.remaining_dark_count = decision.remaining_dark_count
                    ticket.updated_at = evaluated_at
                    if not decision.verified:
                        continue

                    common_details = {
                        "eligible_poles": decision.eligible_count,
                        "fresh_live_poles": decision.live_count,
                        "threshold": threshold,
                        "stabilization_seconds": stabilization_seconds,
                        "stable_since": (
                            decision.stable_since.isoformat() if decision.stable_since else None
                        ),
                    }
                    session.add(
                        TicketEvent(
                            ticket_id=ticket.ticket_id,
                            from_status=TicketStatus.RESOLVED,
                            to_status=TicketStatus.VERIFIED,
                            actor="propel-restoration-verifier",
                            reason="fresh telemetry verified restoration",
                            occurred_at=evaluated_at,
                            details=common_details,
                        )
                    )
                    session.add(
                        TicketEvent(
                            ticket_id=ticket.ticket_id,
                            from_status=TicketStatus.VERIFIED,
                            to_status=TicketStatus.CLOSED,
                            actor="propel-restoration-verifier",
                            reason="verified restoration closed ticket",
                            occurred_at=evaluated_at,
                            details=common_details,
                        )
                    )
                    ticket.status = TicketStatus.CLOSED
                    ticket.verified_at = evaluated_at
                    ticket.closed_at = evaluated_at
                    ticket.restoration_status = RESTORATION_VERIFIED
                    incident = await session.get(Incident, ticket.incident_id)
                    if incident is not None:
                        incident.status = IncidentStatus.RESOLVED
                        incident.resolved_at = evaluated_at
                        incident.updated_at = evaluated_at
                    verified_ticket_ids.append(ticket.ticket_id)
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        for ticket_id in verified_ticket_ids:
            logger.info(
                json.dumps(
                    {
                        "event": "restoration_verified",
                        "ticket_id": str(ticket_id),
                    }
                )
            )
        return len(verified_ticket_ids)

    async def _ticket_view(self, session: AsyncSession, ticket: Ticket) -> TicketView:
        events = (
            await session.scalars(
                select(TicketEvent)
                .where(TicketEvent.ticket_id == ticket.ticket_id)
                .order_by(TicketEvent.occurred_at, TicketEvent.id)
            )
        ).all()
        return TicketView(
            ticket_id=ticket.ticket_id,
            incident_id=ticket.incident_id,
            status=ticket.status,
            assigned_crew=ticket.assigned_crew,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            resolution_claimed_at=ticket.resolution_claimed_at,
            verified_at=ticket.verified_at,
            closed_at=ticket.closed_at,
            restoration_status=ticket.restoration_status,
            remaining_dark_count=ticket.remaining_dark_count,
            events=tuple(
                TicketEventView(
                    from_status=event.from_status,
                    to_status=event.to_status,
                    actor=event.actor,
                    reason=event.reason,
                    occurred_at=event.occurred_at,
                    details=event.details,
                )
                for event in events
            ),
        )

    async def list_network_poles(self, dt_id: str) -> list[NetworkPoleView]:
        try:
            async with self._session_factory() as session:
                transformer_id = await session.scalar(
                    select(DistributionTransformer.id).where(DistributionTransformer.dt_id == dt_id)
                )
                if transformer_id is None:
                    raise NetworkTransformerNotFoundError
                rows = (
                    await session.execute(
                        select(
                            Pole.pole_id,
                            Pole.latitude,
                            Pole.longitude,
                            Pole.pin_code,
                            PoleState.state,
                            PoleState.received_at,
                            Device.device_id,
                        )
                        .outerjoin(PoleState, PoleState.pole_id == Pole.id)
                        .outerjoin(
                            DeviceBinding,
                            (DeviceBinding.pole_id == Pole.id) & DeviceBinding.valid_to.is_(None),
                        )
                        .outerjoin(Device, Device.id == DeviceBinding.device_id)
                        .where(Pole.dt_id == transformer_id)
                        .order_by(Pole.pole_id)
                    )
                ).all()
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        return [
            NetworkPoleView(
                pole_id=row.pole_id,
                dt_id=dt_id,
                latitude=row.latitude,
                longitude=row.longitude,
                pin_code=row.pin_code,
                state=(
                    PoleStatus.NO_DEVICE
                    if row.device_id is None
                    else row.state or PoleStatus.UNKNOWN
                ),
                state_received_at=row.received_at,
                device_id=row.device_id,
            )
            for row in rows
        ]

    async def get_network_overview(self, feeder_id: str) -> NetworkOverviewView:
        try:
            async with self._session_factory() as session:
                feeder_row = (
                    await session.execute(
                        select(
                            Feeder.id,
                            Feeder.feeder_id,
                            Feeder.name,
                            Substation.substation_id,
                            Substation.name.label("substation_name"),
                            Substation.latitude,
                            Substation.longitude,
                            Substation.pin_code,
                        )
                        .join(Substation, Substation.id == Feeder.substation_id)
                        .where(Feeder.feeder_id == feeder_id)
                    )
                ).one_or_none()
                if feeder_row is None:
                    raise NetworkFeederNotFoundError
                transformer_rows = (
                    await session.execute(
                        select(
                            DistributionTransformer.dt_id,
                            DistributionTransformer.name,
                            DistributionTransformer.latitude,
                            DistributionTransformer.longitude,
                            DistributionTransformer.pin_code,
                        )
                        .where(DistributionTransformer.feeder_id == feeder_row.id)
                        .order_by(DistributionTransformer.dt_id)
                    )
                ).all()
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        return NetworkOverviewView(
            feeder_id=feeder_row.feeder_id,
            name=feeder_row.name,
            substation=NetworkSubstationView(
                substation_id=feeder_row.substation_id,
                name=feeder_row.substation_name,
                latitude=feeder_row.latitude,
                longitude=feeder_row.longitude,
                pin_code=feeder_row.pin_code,
            ),
            transformers=tuple(
                NetworkTransformerView(
                    dt_id=row.dt_id,
                    name=row.name,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    pin_code=row.pin_code,
                )
                for row in transformer_rows
            ),
        )

    async def get_network_topology(self, dt_id: str) -> NetworkTopologyView:
        try:
            async with self._session_factory() as session:
                transformer_id = await session.scalar(
                    select(DistributionTransformer.id).where(DistributionTransformer.dt_id == dt_id)
                )
                if transformer_id is None:
                    raise NetworkTransformerNotFoundError
                topology_version = (
                    await session.scalar(
                        select(func.max(TopologyEdge.topology_version)).where(
                            TopologyEdge.dt_id == transformer_id
                        )
                    )
                    or 0
                )
                parent = aliased(Pole)
                child = aliased(Pole)
                rows = (
                    await session.execute(
                        select(
                            parent.pole_id.label("parent_pole_id"),
                            child.pole_id.label("child_pole_id"),
                            TopologyEdge.source,
                            TopologyEdge.edge_confidence,
                        )
                        .select_from(TopologyEdge)
                        .outerjoin(parent, parent.id == TopologyEdge.parent_pole_id)
                        .join(child, child.id == TopologyEdge.child_pole_id)
                        .where(
                            TopologyEdge.dt_id == transformer_id,
                            TopologyEdge.topology_version == topology_version,
                        )
                        .order_by(parent.pole_id.nullsfirst(), child.pole_id)
                    )
                ).all()
        except SQLAlchemyError as error:
            raise IncidentStoreUnavailableError from error
        return NetworkTopologyView(
            dt_id=dt_id,
            topology_version=topology_version,
            spans=tuple(
                NetworkSpanView(
                    parent_pole_id=row.parent_pole_id,
                    child_pole_id=row.child_pole_id,
                    source=row.source,
                    edge_confidence=row.edge_confidence,
                )
                for row in rows
            ),
        )
