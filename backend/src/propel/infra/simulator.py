from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from propel.domain.enums import SimulatorFaultStatus, SimulatorFaultType, TelemetryEventType
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Feeder,
    Pole,
    SimulatedFault,
    TopologyEdge,
)
from propel.simulator.models import (
    SimulatedFaultView,
    SimulatorEmissionReceipt,
    SimulatorTelemetryGateway,
)
from propel.telemetry.ingestion import TelemetryCommand


class SimulatorStoreUnavailableError(Exception):
    pass


class SimulatorTelemetryUnavailableError(Exception):
    pass


class ActiveSimulatorFaultError(Exception):
    pass


class SimulatorFaultNotFoundError(Exception):
    pass


class InvalidSimulatorSpanError(Exception):
    pass


class MissingSimulatorDeviceError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SimulatorDevice:
    device_id: str
    installed_firmware: str | None
    last_sequence: int | None
    firmware: str | None


class HttpSimulatorTelemetryGateway:
    def __init__(self, telemetry_url: str, *, timeout_seconds: float) -> None:
        self._telemetry_url = telemetry_url
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def emit(self, command: TelemetryCommand) -> SimulatorEmissionReceipt:
        payload = {
            "device_id": command.device_id,
            "pole_id": command.pole_id,
            "event": command.event.value,
            "energized": command.energized,
            "ts": command.device_timestamp.isoformat().replace("+00:00", "Z"),
            "seq": command.sequence,
            "battery_mv": command.battery_mv,
            "rssi": command.rssi,
            "fw": command.firmware,
        }
        try:
            response = await self._client.post(
                self._telemetry_url,
                json=payload,
                headers={"x-propel-telemetry-origin": "simulator"},
            )
            response.raise_for_status()
            body = response.json()
            return SimulatorEmissionReceipt(
                event_id=UUID(body["event_id"]),
                received_at=datetime.fromisoformat(body["received_at"].replace("Z", "+00:00")),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise SimulatorTelemetryUnavailableError from error

    async def close(self) -> None:
        await self._client.aclose()


class PostgresSimulatorService:
    _INJECTION_LOCK_KEY = 73_672_603

    def __init__(
        self,
        engine: AsyncEngine,
        telemetry_gateway: SimulatorTelemetryGateway,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._telemetry_gateway = telemetry_gateway
        self._clock = clock or (lambda: datetime.now(UTC))

    async def inject_fixed_span_fault(
        self,
        *,
        dt_id: str,
        parent_pole_id: str,
        child_pole_id: str,
    ) -> SimulatedFaultView:
        injected_at = self._clock()
        try:
            async with self._session_factory.begin() as session:
                transformer_id = await session.scalar(
                    select(DistributionTransformer.id).where(DistributionTransformer.dt_id == dt_id)
                )
                if transformer_id is None:
                    raise InvalidSimulatorSpanError
                parent_id, child_id, affected_ids = await self._resolve_span(
                    session,
                    transformer_id,
                    parent_pole_id,
                    child_pole_id,
                )
                active_faults = await self._locked_active_faults(session)
                resumable = next(
                    (
                        fault
                        for fault in active_faults
                        if fault.fault_type == SimulatorFaultType.SPAN_FAULT
                        and fault.dt_id == transformer_id
                        and fault.parent_pole_id == parent_id
                        and fault.child_pole_id == child_id
                        and fault.injection_telemetry_at is None
                    ),
                    None,
                )
                if resumable is not None:
                    fault_id = resumable.fault_id
                else:
                    self._require_independent_scope(active_faults, affected_ids)
                    fault = SimulatedFault(
                        fault_type=SimulatorFaultType.SPAN_FAULT,
                        dt_id=transformer_id,
                        feeder_id=None,
                        parent_pole_id=parent_id,
                        child_pole_id=child_id,
                        status=SimulatorFaultStatus.ACTIVE,
                        deenergized_pole_ids=list(affected_ids),
                        injected_at=injected_at,
                    )
                    session.add(fault)
                    await session.flush()
                    fault_id = fault.fault_id
        except IntegrityError as error:
            raise ActiveSimulatorFaultError from error
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error

        try:
            async with self._session_factory.begin() as session:
                fault = await session.scalar(
                    select(SimulatedFault)
                    .where(SimulatedFault.fault_id == fault_id)
                    .with_for_update()
                )
                if fault is None:
                    raise SimulatorFaultNotFoundError
                if fault.injection_telemetry_at is not None:
                    raise ActiveSimulatorFaultError
                commands = await self._injection_commands(
                    session,
                    parent_pole_id,
                    tuple(fault.deenergized_pole_ids),
                    fault.injected_at,
                )
                receipts = await self._emit_all(commands)
                fault.injection_telemetry_at = max(receipt.received_at for receipt in receipts)
                view = await self._fault_view(session, fault, receipts)
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return view

    async def _inject_fixed_scope_fault(
        self,
        fault_type: SimulatorFaultType,
        *,
        dt_id: str,
        feeder_id: str,
    ) -> SimulatedFaultView:
        if fault_type not in (SimulatorFaultType.DT_FAULT, SimulatorFaultType.FEEDER_FAULT):
            raise InvalidSimulatorSpanError
        injected_at = self._clock()
        try:
            async with self._session_factory.begin() as session:
                feeder_internal_id = await session.scalar(
                    select(Feeder.id).where(Feeder.feeder_id == feeder_id)
                )
                transformer_internal_id = await session.scalar(
                    select(DistributionTransformer.id).where(DistributionTransformer.dt_id == dt_id)
                )
                if feeder_internal_id is None or transformer_internal_id is None:
                    raise InvalidSimulatorSpanError
                pole_statement = select(Pole.pole_id)
                if fault_type == SimulatorFaultType.DT_FAULT:
                    pole_statement = pole_statement.where(Pole.dt_id == transformer_internal_id)
                else:
                    pole_statement = pole_statement.where(Pole.feeder_id == feeder_internal_id)
                affected_ids = tuple(await session.scalars(pole_statement.order_by(Pole.pole_id)))
                active_faults = await self._locked_active_faults(session)
                self._require_independent_scope(active_faults, affected_ids)
                fault = SimulatedFault(
                    fault_type=fault_type,
                    feeder_id=(
                        feeder_internal_id
                        if fault_type == SimulatorFaultType.FEEDER_FAULT
                        else None
                    ),
                    dt_id=(
                        transformer_internal_id
                        if fault_type == SimulatorFaultType.DT_FAULT
                        else None
                    ),
                    parent_pole_id=None,
                    child_pole_id=None,
                    status=SimulatorFaultStatus.ACTIVE,
                    deenergized_pole_ids=list(affected_ids),
                    injected_at=injected_at,
                )
                session.add(fault)
                await session.flush()
                fault_id = fault.fault_id
        except IntegrityError as error:
            raise ActiveSimulatorFaultError from error
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error

        try:
            async with self._session_factory.begin() as session:
                fault = await session.scalar(
                    select(SimulatedFault)
                    .where(SimulatedFault.fault_id == fault_id)
                    .with_for_update()
                )
                if fault is None:
                    raise SimulatorFaultNotFoundError
                commands = await self._injection_commands(
                    session,
                    None,
                    tuple(fault.deenergized_pole_ids),
                    fault.injected_at,
                )
                receipts = await self._emit_all(commands)
                fault.injection_telemetry_at = max(receipt.received_at for receipt in receipts)
                view = await self._fault_view(session, fault, receipts)
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return view

    async def _locked_active_faults(
        self,
        session: AsyncSession,
    ) -> tuple[SimulatedFault, ...]:
        # The advisory transaction lock closes the zero-row race when two independent
        # injections check active scopes concurrently.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": self._INJECTION_LOCK_KEY},
        )
        return tuple(
            await session.scalars(
                select(SimulatedFault)
                .where(SimulatedFault.status == SimulatorFaultStatus.ACTIVE)
                .order_by(SimulatedFault.injected_at, SimulatedFault.fault_id)
                .with_for_update()
            )
        )

    @staticmethod
    def _require_independent_scope(
        active_faults: Sequence[SimulatedFault],
        affected_ids: Sequence[str],
    ) -> None:
        requested = set(affected_ids)
        if any(requested.intersection(fault.deenergized_pole_ids) for fault in active_faults):
            raise ActiveSimulatorFaultError

    async def repair_fault(self, fault_id: UUID) -> SimulatedFaultView:
        repair_at = self._clock()
        try:
            async with self._session_factory.begin() as session:
                fault = await session.scalar(
                    select(SimulatedFault)
                    .where(SimulatedFault.fault_id == fault_id)
                    .with_for_update()
                )
                if fault is None:
                    raise SimulatorFaultNotFoundError
                if fault.status == SimulatorFaultStatus.REPAIRED:
                    return await self._fault_view(session, fault, ())
                commands = await self._repair_commands(
                    session,
                    tuple(fault.deenergized_pole_ids),
                    repair_at,
                )
                receipts = await self._emit_all(commands)
                fault.status = SimulatorFaultStatus.REPAIRED
                fault.repaired_at = repair_at
                view = await self._fault_view(session, fault, receipts)
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return view

    async def reset(self) -> tuple[SimulatedFaultView, ...]:
        try:
            async with self._session_factory() as session:
                active_ids = tuple(
                    await session.scalars(
                        select(SimulatedFault.fault_id)
                        .where(SimulatedFault.status == SimulatorFaultStatus.ACTIVE)
                        .order_by(SimulatedFault.injected_at, SimulatedFault.fault_id)
                    )
                )
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return tuple([await self.repair_fault(fault_id) for fault_id in active_ids])

    async def close(self) -> None:
        await self._telemetry_gateway.close()

    async def _emit_all(
        self, commands: Sequence[TelemetryCommand]
    ) -> tuple[SimulatorEmissionReceipt, ...]:
        return tuple([await self._telemetry_gateway.emit(command) for command in commands])

    async def _resolve_span(
        self,
        session: AsyncSession,
        transformer_id: int,
        parent_external_id: str,
        child_external_id: str,
    ) -> tuple[int, int, tuple[str, ...]]:
        topology_version = await session.scalar(
            select(func.max(TopologyEdge.topology_version)).where(
                TopologyEdge.dt_id == transformer_id
            )
        )
        parent = aliased(Pole)
        child = aliased(Pole)
        edges = (
            await session.execute(
                select(
                    parent.id.label("parent_id"),
                    parent.pole_id.label("parent_external_id"),
                    child.id.label("child_id"),
                    child.pole_id.label("child_external_id"),
                )
                .select_from(TopologyEdge)
                .outerjoin(parent, parent.id == TopologyEdge.parent_pole_id)
                .join(child, child.id == TopologyEdge.child_pole_id)
                .where(
                    TopologyEdge.dt_id == transformer_id,
                    TopologyEdge.topology_version == topology_version,
                )
            )
        ).all()
        requested = next(
            (
                edge
                for edge in edges
                if edge.parent_external_id == parent_external_id
                and edge.child_external_id == child_external_id
            ),
            None,
        )
        if requested is None or requested.parent_id is None:
            raise InvalidSimulatorSpanError
        children: dict[str, list[str]] = {}
        for edge in edges:
            if edge.parent_external_id is not None:
                children.setdefault(edge.parent_external_id, []).append(edge.child_external_id)
        affected: list[str] = []
        pending = [child_external_id]
        while pending:
            pole_id = pending.pop(0)
            affected.append(pole_id)
            pending.extend(sorted(children.get(pole_id, ())))
        return requested.parent_id, requested.child_id, tuple(affected)

    async def _device_rows(
        self, session: AsyncSession, pole_ids: Sequence[str]
    ) -> dict[str, SimulatorDevice]:
        rows = (
            await session.execute(
                select(
                    Pole.pole_id,
                    Device.device_id,
                    Device.installed_firmware,
                    DeviceHealth.last_sequence,
                    DeviceHealth.firmware,
                )
                .join(DeviceBinding, DeviceBinding.pole_id == Pole.id)
                .join(Device, Device.id == DeviceBinding.device_id)
                .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
                .where(Pole.pole_id.in_(pole_ids), DeviceBinding.valid_to.is_(None))
            )
        ).all()
        result = {
            row.pole_id: SimulatorDevice(
                device_id=row.device_id,
                installed_firmware=row.installed_firmware,
                last_sequence=row.last_sequence,
                firmware=row.firmware,
            )
            for row in rows
        }
        if set(result) != set(pole_ids):
            raise MissingSimulatorDeviceError
        return result

    async def _injection_commands(
        self,
        session: AsyncSession,
        parent_pole_id: str | None,
        affected_ids: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[TelemetryCommand, ...]:
        pole_ids = ((parent_pole_id,) if parent_pole_id is not None else ()) + affected_ids
        rows = await self._device_rows(session, pole_ids)
        commands: list[TelemetryCommand] = []
        for pole_id in pole_ids:
            row = rows[pole_id]
            is_affected = pole_id in affected_ids
            commands.append(
                self._command(
                    row,
                    pole_id,
                    TelemetryEventType.POWER_LOST if is_affected else TelemetryEventType.HEARTBEAT,
                    not is_affected,
                    occurred_at,
                    (row.last_sequence or 0) + 1,
                )
            )
        return tuple(commands)

    async def _repair_commands(
        self,
        session: AsyncSession,
        affected_ids: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[TelemetryCommand, ...]:
        rows = await self._device_rows(session, affected_ids)
        commands: list[TelemetryCommand] = []
        for index, pole_id in enumerate(affected_ids):
            row = rows[pole_id]
            boot_at = occurred_at + timedelta(milliseconds=index * 2)
            commands.append(self._command(row, pole_id, TelemetryEventType.BOOT, True, boot_at, 0))
            commands.append(
                self._command(
                    row,
                    pole_id,
                    TelemetryEventType.POWER_RESTORED,
                    True,
                    boot_at + timedelta(milliseconds=1),
                    1,
                )
            )
        return tuple(commands)

    @staticmethod
    def _command(
        row: SimulatorDevice,
        pole_id: str,
        event_type: TelemetryEventType,
        energized: bool,
        occurred_at: datetime,
        sequence: int,
    ) -> TelemetryCommand:
        return TelemetryCommand(
            device_id=row.device_id,
            pole_id=pole_id,
            event=event_type,
            energized=energized,
            device_timestamp=occurred_at,
            sequence=sequence,
            battery_mv=3480,
            rssi=-91,
            firmware=row.firmware or row.installed_firmware or "1.4.2",
        )

    @staticmethod
    async def _fault_view(
        session: AsyncSession,
        fault: SimulatedFault,
        receipts: Sequence[SimulatorEmissionReceipt],
    ) -> SimulatedFaultView:
        parent = aliased(Pole)
        child = aliased(Pole)
        transformer = aliased(DistributionTransformer)
        row = (
            await session.execute(
                select(
                    Feeder.feeder_id,
                    transformer.dt_id,
                    parent.pole_id.label("parent_pole_id"),
                    child.pole_id.label("child_pole_id"),
                )
                .select_from(SimulatedFault)
                .outerjoin(transformer, transformer.id == SimulatedFault.dt_id)
                .outerjoin(
                    Feeder,
                    Feeder.id == func.coalesce(SimulatedFault.feeder_id, transformer.feeder_id),
                )
                .outerjoin(parent, parent.id == SimulatedFault.parent_pole_id)
                .outerjoin(child, child.id == SimulatedFault.child_pole_id)
                .where(SimulatedFault.fault_id == fault.fault_id)
            )
        ).one()
        return SimulatedFaultView(
            fault_id=fault.fault_id,
            fault_type=fault.fault_type,
            feeder_id=row.feeder_id,
            dt_id=row.dt_id,
            parent_pole_id=row.parent_pole_id,
            child_pole_id=row.child_pole_id,
            status=fault.status,
            deenergized_pole_ids=tuple(fault.deenergized_pole_ids),
            injected_at=fault.injected_at,
            injection_telemetry_at=fault.injection_telemetry_at,
            repaired_at=fault.repaired_at,
            emitted_event_ids=tuple(receipt.event_id for receipt in receipts),
        )

    async def inject_fixed_fault(
        self,
        *,
        fault_type: SimulatorFaultType,
        dt_id: str,
        parent_pole_id: str,
        child_pole_id: str,
        feeder_id: str = "FDR-001",
    ) -> SimulatedFaultView:
        if fault_type == SimulatorFaultType.SPAN_FAULT:
            return await self.inject_fixed_span_fault(
                dt_id=dt_id,
                parent_pole_id=parent_pole_id,
                child_pole_id=child_pole_id,
            )
        return await self._inject_fixed_scope_fault(
            fault_type,
            dt_id=dt_id,
            feeder_id=feeder_id,
        )
