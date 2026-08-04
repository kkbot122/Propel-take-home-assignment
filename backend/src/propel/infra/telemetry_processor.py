from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from propel.domain.enums import DeviceHealthStatus, PoleStatus, ProcessingOutcome
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Pole,
    PoleState,
    TelemetryEvent,
)
from propel.telemetry.ingestion import TelemetryCommand
from propel.telemetry.messages import parse_stream_message
from propel.telemetry.ordering import DeviceCursor, OrderingDecision, decide_event


@dataclass(frozen=True, slots=True)
class TelemetryProcessingResult:
    event_id: UUID
    outcome: ProcessingOutcome
    idempotent_replay: bool
    state_changed: bool
    dt_id: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessingIdentity:
    device_id: int
    pole_id: int
    dt_id: str


class StreamIdentityConflictError(Exception):
    pass


def firmware_can_report_power_loss(firmware: str) -> bool:
    return not (firmware == "1.2" or firmware.startswith("1.2."))


class PostgresTelemetryProcessor:
    def __init__(self, engine: AsyncEngine) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def process(self, fields: Mapping[str, str]) -> TelemetryProcessingResult:
        envelope = parse_stream_message(fields)
        async with self._session_factory.begin() as session:
            existing = await self._existing_result(session, envelope.event_id)
            if existing is not None:
                return existing

            identity = await self._resolve_identity(
                session,
                envelope.command.device_id,
                envelope.command.pole_id,
                envelope.received_at,
            )
            health = await self._lock_device_health(session, identity.device_id)
            decision = decide_event(
                envelope.command.event,
                envelope.command.energized,
                envelope.command.sequence,
                envelope.command.device_timestamp,
                DeviceCursor(
                    boot_generation=health.boot_generation,
                    last_sequence=health.last_sequence,
                    last_event_type=health.last_event_type,
                    last_device_timestamp=health.last_device_timestamp,
                ),
            )
            pole_state = await self._lock_pole_state(
                session, identity.pole_id, decision.target_pole_state
            )
            state_changed = decision.target_pole_state is not None and (
                pole_state is None or pole_state.state != decision.target_pole_state
            )

            inserted_id = await session.scalar(
                insert(TelemetryEvent)
                .values(
                    event_id=envelope.event_id,
                    correlation_id=envelope.correlation_id,
                    device_id=identity.device_id,
                    pole_id=identity.pole_id,
                    event_type=envelope.command.event,
                    energized=envelope.command.energized,
                    device_timestamp=envelope.command.device_timestamp,
                    received_at=envelope.received_at,
                    sequence=envelope.command.sequence,
                    boot_generation=decision.boot_generation,
                    battery_mv=envelope.command.battery_mv,
                    rssi=envelope.command.rssi,
                    firmware=envelope.command.firmware,
                    processing_outcome=decision.outcome,
                    origin=envelope.origin,
                    state_changed=state_changed,
                    raw_payload=dict(fields),
                )
                .on_conflict_do_nothing(constraint="uq_telemetry_events_event_id")
                .returning(TelemetryEvent.id)
            )
            if inserted_id is None:
                replay = await self._existing_result(session, envelope.event_id)
                if replay is None:
                    raise RuntimeError("event conflict occurred without an existing event")
                return replay

            if decision.outcome == ProcessingOutcome.ACCEPTED:
                self._update_device_health(health, envelope.received_at, envelope.command, decision)
                if decision.target_pole_state is not None:
                    self._update_pole_state(
                        session,
                        pole_state,
                        identity.pole_id,
                        envelope.event_id,
                        envelope.received_at,
                        envelope.command.device_timestamp,
                        envelope.command.sequence,
                        envelope.command.firmware,
                        envelope.command.battery_mv,
                        envelope.command.rssi,
                        decision,
                    )

            return TelemetryProcessingResult(
                event_id=envelope.event_id,
                outcome=decision.outcome,
                idempotent_replay=False,
                state_changed=state_changed,
                dt_id=identity.dt_id,
                received_at=envelope.received_at,
            )

    async def _existing_result(
        self, session: AsyncSession, event_id: UUID
    ) -> TelemetryProcessingResult | None:
        row = (
            await session.execute(
                select(
                    TelemetryEvent.event_id,
                    TelemetryEvent.processing_outcome,
                    TelemetryEvent.state_changed,
                    TelemetryEvent.received_at,
                    DistributionTransformer.dt_id,
                )
                .join(Pole, Pole.id == TelemetryEvent.pole_id)
                .join(DistributionTransformer, DistributionTransformer.id == Pole.dt_id)
                .where(TelemetryEvent.event_id == event_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return TelemetryProcessingResult(
            event_id=row.event_id,
            outcome=row.processing_outcome,
            idempotent_replay=True,
            state_changed=row.state_changed,
            dt_id=row.dt_id,
            received_at=row.received_at,
        )

    async def _resolve_identity(
        self,
        session: AsyncSession,
        device_external_id: str,
        pole_external_id: str,
        received_at: datetime,
    ) -> ProcessingIdentity:
        row = (
            await session.execute(
                select(
                    Device.id.label("device_id"),
                    Pole.id.label("pole_id"),
                    DistributionTransformer.dt_id.label("dt_id"),
                )
                .join(DeviceBinding, DeviceBinding.device_id == Device.id)
                .join(Pole, Pole.id == DeviceBinding.pole_id)
                .join(DistributionTransformer, DistributionTransformer.id == Pole.dt_id)
                .where(
                    Device.device_id == device_external_id,
                    Pole.pole_id == pole_external_id,
                    DeviceBinding.valid_from <= received_at,
                    (DeviceBinding.valid_to.is_(None) | (DeviceBinding.valid_to > received_at)),
                )
            )
        ).one_or_none()
        if row is None:
            raise StreamIdentityConflictError
        return ProcessingIdentity(device_id=row.device_id, pole_id=row.pole_id, dt_id=row.dt_id)

    async def _lock_device_health(self, session: AsyncSession, device_id: int) -> DeviceHealth:
        health = await session.scalar(
            select(DeviceHealth).where(DeviceHealth.device_id == device_id).with_for_update()
        )
        if health is not None:
            return health
        health = DeviceHealth(
            device_id=device_id,
            status=DeviceHealthStatus.UNKNOWN,
            boot_generation=0,
            status_reason="created_during_ingestion",
            can_report_power_loss=True,
        )
        session.add(health)
        await session.flush()
        return health

    async def _lock_pole_state(
        self,
        session: AsyncSession,
        pole_id: int,
        target_state: PoleStatus | None,
    ) -> PoleState | None:
        if target_state is None:
            return None
        return await session.scalar(
            select(PoleState).where(PoleState.pole_id == pole_id).with_for_update()
        )

    @staticmethod
    def _update_device_health(
        health: DeviceHealth,
        received_at: datetime,
        command: TelemetryCommand,
        decision: OrderingDecision,
    ) -> None:
        health.status = DeviceHealthStatus.HEALTHY
        health.last_seen_at = received_at
        health.boot_generation = decision.boot_generation
        health.last_sequence = decision.next_sequence
        health.last_event_type = command.event
        health.last_device_timestamp = command.device_timestamp
        health.firmware = command.firmware
        health.battery_mv = command.battery_mv
        health.rssi = command.rssi
        health.status_reason = decision.reason
        health.can_report_power_loss = firmware_can_report_power_loss(command.firmware)
        health.updated_at = received_at

    @staticmethod
    def _update_pole_state(
        session: AsyncSession,
        pole_state: PoleState | None,
        pole_id: int,
        event_id: UUID,
        received_at: datetime,
        device_timestamp: datetime,
        sequence: int,
        firmware: str,
        battery_mv: int,
        rssi: int,
        decision: OrderingDecision,
    ) -> None:
        if decision.target_pole_state is None:
            return
        if pole_state is None:
            pole_state = PoleState(
                pole_id=pole_id,
                state=decision.target_pole_state,
                received_at=received_at,
                reason=decision.reason,
            )
            session.add(pole_state)
        pole_state.state = decision.target_pole_state
        pole_state.source_event_id = event_id
        pole_state.device_sequence = sequence
        pole_state.device_timestamp = device_timestamp
        pole_state.received_at = received_at
        pole_state.firmware = firmware
        pole_state.battery_mv = battery_mv
        pole_state.rssi = rssi
        pole_state.reason = decision.reason
        pole_state.updated_at = received_at
