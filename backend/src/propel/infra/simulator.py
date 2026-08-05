import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from propel.domain.enums import (
    DeviceHealthStatus,
    PoleStatus,
    ScheduledOutageScope,
    SimulatorFaultStatus,
    SimulatorFaultType,
    TelemetryEventType,
)
from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Feeder,
    GeneratedDataset,
    Pole,
    PoleState,
    ScheduledOutage,
    SimulatedFault,
    SimulatorTopologyEdge,
    TopologyEdge,
)
from propel.infra.staleness import PostgresStaleDeviceScanner, StaleScanUnavailableError
from propel.simulator.delivery import (
    DEFAULT_POWER_LOSS_DELIVERY_RATIO,
    DEFAULT_POWER_LOSS_DELIVERY_SEED,
    power_loss_delivery_succeeds,
)
from propel.simulator.models import (
    SimulatedFaultView,
    SimulatorEmissionReceipt,
    SimulatorScenarioRunView,
    SimulatorScenarioView,
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


class InvalidSimulatorNoiseError(Exception):
    pass


class MissingSimulatorDeviceError(Exception):
    pass


class NoSimulatorTelemetryError(Exception):
    pass


class SimulatorDatasetNotFoundError(Exception):
    pass


class SimulatorScenarioNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SimulatorDevice:
    internal_device_id: int
    device_id: str
    installed_firmware: str | None
    last_sequence: int | None
    firmware: str | None
    health_status: DeviceHealthStatus | None
    can_report_power_loss: bool | None
    status_reason: str


class HttpSimulatorTelemetryGateway:
    def __init__(self, telemetry_url: str, *, timeout_seconds: float) -> None:
        self._telemetry_url = telemetry_url
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def emit(self, command: TelemetryCommand) -> SimulatorEmissionReceipt:
        receipts = await self.emit_many((command,))
        return receipts[0]

    @staticmethod
    def _payload(command: TelemetryCommand) -> dict[str, object]:
        return {
            "event_id": str(uuid4()),
            "correlation_id": str(uuid4()),
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

    async def emit_many(
        self, commands: tuple[TelemetryCommand, ...]
    ) -> tuple[SimulatorEmissionReceipt, ...]:
        if not commands:
            return ()
        try:
            response = await self._client.post(
                f"{self._telemetry_url.rstrip('/')}/batch",
                json={"items": [self._payload(command) for command in commands]},
                headers={"x-propel-telemetry-origin": "simulator"},
            )
            response.raise_for_status()
            body = response.json()
            results = body["results"]
            if len(results) != len(commands) or any(
                item.get("status") != "accepted" for item in results
            ):
                raise SimulatorTelemetryUnavailableError
            return tuple(
                SimulatorEmissionReceipt(
                    event_id=UUID(item["event_id"]),
                    received_at=datetime.fromisoformat(item["received_at"].replace("Z", "+00:00")),
                )
                for item in results
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
        power_loss_delivery_ratio: float = DEFAULT_POWER_LOSS_DELIVERY_RATIO,
        power_loss_delivery_seed: int = DEFAULT_POWER_LOSS_DELIVERY_SEED,
        stale_after_seconds: float = 1_920,
    ) -> None:
        if not 0 < power_loss_delivery_ratio <= 1:
            raise ValueError("power-loss delivery ratio must be greater than zero and at most one")
        if power_loss_delivery_seed < 0:
            raise ValueError("power-loss delivery seed cannot be negative")
        if stale_after_seconds <= 0:
            raise ValueError("simulator stale threshold must be positive")
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._engine = engine
        self._telemetry_gateway = telemetry_gateway
        self._clock = clock or (lambda: datetime.now(UTC))
        self._power_loss_delivery_ratio = power_loss_delivery_ratio
        self._power_loss_delivery_seed = power_loss_delivery_seed
        self._stale_after_seconds = stale_after_seconds

    async def inject_fixed_span_fault(
        self,
        *,
        dt_id: str,
        parent_pole_id: str,
        child_pole_id: str,
        missing_device_pole_ids: tuple[str, ...] = (),
        omit_loss_pole_ids: tuple[str, ...] = (),
        duplicate_loss_pole_ids: tuple[str, ...] = (),
        delayed_loss_pole_ids: tuple[str, ...] = (),
        out_of_order_pole_ids: tuple[str, ...] = (),
        force_loss_delivery: bool = False,
    ) -> SimulatedFaultView:
        injected_at = self._clock()
        no_telemetry = False
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
                omitted_ids = self._validate_noise(
                    affected_ids,
                    missing_device_pole_ids,
                    omit_loss_pole_ids,
                    duplicate_loss_pole_ids,
                    delayed_loss_pole_ids,
                    out_of_order_pole_ids,
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
                    omitted_ids,
                    missing_device_pole_ids,
                    loss_delivery_context=(
                        f"{SimulatorFaultType.SPAN_FAULT.value}:"
                        f"{dt_id}:{parent_pole_id}:{child_pole_id}"
                    ),
                    duplicate_loss_pole_ids=duplicate_loss_pole_ids,
                    delayed_loss_pole_ids=delayed_loss_pole_ids,
                    out_of_order_pole_ids=out_of_order_pole_ids,
                    force_loss_delivery=force_loss_delivery,
                )
                receipts = await self._emit_all(
                    commands,
                    delayed_loss_pole_ids=delayed_loss_pole_ids,
                )
                if not receipts:
                    await session.delete(fault)
                    no_telemetry = True
                    view = None
                else:
                    fault.injection_telemetry_at = max(receipt.received_at for receipt in receipts)
                    view = await self._fault_view(session, fault, receipts)
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        if no_telemetry or view is None:
            raise NoSimulatorTelemetryError
        return view

    async def generated_manifest(self, dataset_id: str | None = None) -> dict[str, object]:
        try:
            async with self._session_factory() as session:
                statement = select(GeneratedDataset.manifest)
                if dataset_id is None:
                    statement = statement.order_by(
                        GeneratedDataset.created_at.desc(), GeneratedDataset.id.desc()
                    )
                else:
                    statement = statement.where(GeneratedDataset.dataset_id == dataset_id)
                manifest = await session.scalar(statement.limit(1))
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        if manifest is None:
            raise SimulatorDatasetNotFoundError
        return manifest

    async def list_scenarios(self) -> tuple[SimulatorScenarioView, ...]:
        manifest = await self.generated_manifest()
        scenarios = manifest.get("scenarios")
        if not isinstance(scenarios, list):
            raise SimulatorDatasetNotFoundError
        return tuple(self._scenario_view(item) for item in scenarios)

    async def run_scenario(self, scenario_id: str) -> SimulatorScenarioRunView:
        manifest = await self.generated_manifest()
        scenarios = manifest.get("scenarios")
        if not isinstance(scenarios, list):
            raise SimulatorDatasetNotFoundError
        scenario = next(
            (
                item
                for item in scenarios
                if isinstance(item, dict) and item.get("scenario_id") == scenario_id
            ),
            None,
        )
        if scenario is None:
            raise SimulatorScenarioNotFoundError
        view = self._scenario_view(scenario)
        if scenario_id == "dead-sensor":
            device_id, pole_id = await self._inject_device_failure()
            return SimulatorScenarioRunView(
                scenario_id=view.scenario_id,
                description=view.description,
                faults=(),
                restoration_fraction=view.restoration_fraction,
                failed_device_id=device_id,
                failed_pole_id=pole_id,
            )

        raw_faults = scenario.get("faults")
        if not isinstance(raw_faults, list):
            raise SimulatorScenarioNotFoundError
        raw_noise = scenario.get("noise")
        noise: dict[str, Any] = raw_noise if isinstance(raw_noise, dict) else {}
        force_loss_delivery = bool(scenario.get("complete_delivery", False))
        faults: list[SimulatedFaultView] = []
        for raw_fault in raw_faults:
            if not isinstance(raw_fault, dict):
                raise SimulatorScenarioNotFoundError
            fault_type = SimulatorFaultType(str(raw_fault["fault_type"]))
            fault = await self.inject_fixed_fault(
                fault_type=fault_type,
                feeder_id=str(raw_fault.get("feeder_id") or "FDR-001"),
                dt_id=str(raw_fault.get("dt_id") or "DT-001"),
                parent_pole_id=str(raw_fault.get("parent_pole_id") or "P-001"),
                child_pole_id=str(raw_fault.get("child_pole_id") or "P-002"),
                omit_loss_pole_ids=tuple(noise.get("omit_loss_pole_ids", ())),
                duplicate_loss_pole_ids=tuple(noise.get("duplicate_pole_ids", ())),
                delayed_loss_pole_ids=tuple(noise.get("delayed_pole_ids", ())),
                out_of_order_pole_ids=tuple(noise.get("out_of_order_pole_ids", ())),
                force_loss_delivery=force_loss_delivery,
            )
            faults.append(fault)

        scheduled_outage_id: str | None = None
        if view.scheduled and faults:
            scheduled_outage_id = await self._create_scenario_schedule(faults[0])
        return SimulatorScenarioRunView(
            scenario_id=view.scenario_id,
            description=view.description,
            faults=tuple(faults),
            restoration_fraction=view.restoration_fraction,
            scheduled_outage_id=scheduled_outage_id,
        )

    @staticmethod
    def _scenario_view(raw: object) -> SimulatorScenarioView:
        if not isinstance(raw, dict):
            raise SimulatorScenarioNotFoundError
        noise = raw.get("noise")
        noise_modes = tuple(
            sorted(
                key.removesuffix("_pole_ids").replace("_", "-")
                for key, value in (noise.items() if isinstance(noise, dict) else ())
                if isinstance(value, list) and value
            )
        )
        faults = raw.get("faults")
        return SimulatorScenarioView(
            scenario_id=str(raw.get("scenario_id", "")),
            description=str(raw.get("description", "")),
            fault_count=len(faults) if isinstance(faults, list) else 0,
            scheduled=bool(raw.get("scheduled", False)),
            restoration_fraction=float(raw.get("restoration_fraction", 1.0)),
            noise_modes=noise_modes,
        )

    async def _create_scenario_schedule(self, fault: SimulatedFaultView) -> str:
        if fault.parent_pole_id is None or fault.child_pole_id is None:
            raise InvalidSimulatorSpanError
        now = self._clock()
        outage_id = f"SIM-{fault.fault_id}"
        try:
            async with self._session_factory.begin() as session:
                session.add(
                    ScheduledOutage(
                        outage_id=outage_id,
                        scope=ScheduledOutageScope.SPAN,
                        scope_id=f"{fault.parent_pole_id}->{fault.child_pole_id}",
                        starts_at=now - timedelta(minutes=10),
                        ends_at=now + timedelta(hours=1),
                        source="simulator-scenario",
                        reason="Reviewer-driven scheduled outage scenario",
                    )
                )
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return outage_id

    async def _inject_device_failure(self) -> tuple[str, str]:
        failed_at = self._clock()
        try:
            async with self._session_factory.begin() as session:
                active_scopes = tuple(
                    await session.scalars(
                        select(SimulatedFault.deenergized_pole_ids).where(
                            SimulatedFault.status == SimulatorFaultStatus.ACTIVE
                        )
                    )
                )
                active_pole_ids = {pole_id for scope in active_scopes for pole_id in scope}
                rows = (
                    await session.execute(
                        select(
                            Device.id.label("internal_device_id"),
                            Device.device_id,
                            Pole.pole_id,
                        )
                        .join(DeviceBinding, DeviceBinding.device_id == Device.id)
                        .join(Pole, Pole.id == DeviceBinding.pole_id)
                        .join(DeviceHealth, DeviceHealth.device_id == Device.id)
                        .join(PoleState, PoleState.pole_id == Pole.id)
                        .where(
                            DeviceBinding.valid_to.is_(None),
                            DeviceHealth.status == DeviceHealthStatus.HEALTHY,
                            PoleState.state == PoleStatus.LIVE,
                        )
                        .order_by(Device.device_id)
                    )
                ).all()
                selected = next(
                    (row for row in rows if row.pole_id not in active_pole_ids),
                    None,
                )
                if selected is None:
                    raise NoSimulatorTelemetryError
                await session.execute(
                    update(DeviceHealth)
                    .where(DeviceHealth.device_id == selected.internal_device_id)
                    .values(
                        # Make this the oldest eligible healthy device so the real
                        # bounded stale scanner deterministically selects it.
                        last_seen_at=datetime(1970, 1, 1, tzinfo=UTC),
                        status_reason="simulator_device_failure_pending",
                        updated_at=failed_at,
                    )
                )
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error

        scanner = PostgresStaleDeviceScanner(self._engine)
        try:
            await scanner.scan_once(
                cutoff=failed_at - timedelta(seconds=self._stale_after_seconds),
                scanned_at=failed_at,
                limit=1,
            )
        except StaleScanUnavailableError as error:
            raise SimulatorStoreUnavailableError from error
        try:
            async with self._session_factory.begin() as session:
                await session.execute(
                    update(DeviceHealth)
                    .where(DeviceHealth.device_id == selected.internal_device_id)
                    .values(status_reason="simulator_device_failure", updated_at=failed_at)
                )
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return selected.device_id, selected.pole_id

    async def _restore_failed_devices(self) -> None:
        restored_at = self._clock()
        try:
            async with self._session_factory.begin() as session:
                rows = (
                    await session.execute(
                        select(Pole.pole_id)
                        .join(DeviceBinding, DeviceBinding.pole_id == Pole.id)
                        .join(DeviceHealth, DeviceHealth.device_id == DeviceBinding.device_id)
                        .where(
                            DeviceBinding.valid_to.is_(None),
                            DeviceHealth.status_reason == "simulator_device_failure",
                        )
                        .order_by(Pole.pole_id)
                    )
                ).all()
                devices = await self._device_rows(session, tuple(row.pole_id for row in rows))
                commands = tuple(
                    self._command(
                        devices[row.pole_id],
                        row.pole_id,
                        TelemetryEventType.HEARTBEAT,
                        True,
                        restored_at,
                        max(
                            (devices[row.pole_id].last_sequence or 0) + 1,
                            int(restored_at.timestamp() * 1_000_000) + index,
                        ),
                    )
                    for index, row in enumerate(rows)
                    if row.pole_id in devices
                )
                if devices:
                    await session.execute(
                        update(DeviceHealth)
                        .where(
                            DeviceHealth.device_id.in_(
                                tuple(device.internal_device_id for device in devices.values())
                            )
                        )
                        .values(status_reason="simulator_device_recovery_pending")
                    )
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        if commands:
            await self._emit_all(commands)

    async def _inject_fixed_scope_fault(
        self,
        fault_type: SimulatorFaultType,
        *,
        dt_id: str,
        feeder_id: str,
        missing_device_pole_ids: tuple[str, ...] = (),
        omit_loss_pole_ids: tuple[str, ...] = (),
        duplicate_loss_pole_ids: tuple[str, ...] = (),
        delayed_loss_pole_ids: tuple[str, ...] = (),
        out_of_order_pole_ids: tuple[str, ...] = (),
    ) -> SimulatedFaultView:
        if fault_type not in (SimulatorFaultType.DT_FAULT, SimulatorFaultType.FEEDER_FAULT):
            raise InvalidSimulatorSpanError
        injected_at = self._clock()
        no_telemetry = False
        try:
            async with self._session_factory.begin() as session:
                feeder_internal_id: int | None = None
                transformer_internal_id: int | None = None
                if fault_type == SimulatorFaultType.DT_FAULT:
                    transformer_internal_id = await session.scalar(
                        select(DistributionTransformer.id).where(
                            DistributionTransformer.dt_id == dt_id
                        )
                    )
                    if transformer_internal_id is None:
                        raise InvalidSimulatorSpanError
                else:
                    feeder_internal_id = await session.scalar(
                        select(Feeder.id).where(Feeder.feeder_id == feeder_id)
                    )
                    if feeder_internal_id is None:
                        raise InvalidSimulatorSpanError
                pole_statement = select(Pole.pole_id)
                if fault_type == SimulatorFaultType.DT_FAULT:
                    pole_statement = pole_statement.where(Pole.dt_id == transformer_internal_id)
                else:
                    pole_statement = pole_statement.where(Pole.feeder_id == feeder_internal_id)
                affected_ids = tuple(await session.scalars(pole_statement.order_by(Pole.pole_id)))
                omitted_ids = self._validate_noise(
                    affected_ids,
                    missing_device_pole_ids,
                    omit_loss_pole_ids,
                    duplicate_loss_pole_ids,
                    delayed_loss_pole_ids,
                    out_of_order_pole_ids,
                )
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
                    omitted_ids,
                    missing_device_pole_ids,
                    loss_delivery_context=(
                        f"{fault_type.value}:"
                        f"{dt_id if fault_type == SimulatorFaultType.DT_FAULT else feeder_id}"
                    ),
                    duplicate_loss_pole_ids=duplicate_loss_pole_ids,
                    delayed_loss_pole_ids=delayed_loss_pole_ids,
                    out_of_order_pole_ids=out_of_order_pole_ids,
                )
                receipts = await self._emit_all(
                    commands,
                    delayed_loss_pole_ids=delayed_loss_pole_ids,
                )
                if not receipts:
                    await session.delete(fault)
                    no_telemetry = True
                    view = None
                else:
                    fault.injection_telemetry_at = max(receipt.received_at for receipt in receipts)
                    view = await self._fault_view(session, fault, receipts)
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        if no_telemetry or view is None:
            raise NoSimulatorTelemetryError
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

    @staticmethod
    def _validate_noise(
        affected_ids: Sequence[str],
        missing_device_pole_ids: Sequence[str],
        omit_loss_pole_ids: Sequence[str],
        duplicate_loss_pole_ids: Sequence[str] = (),
        delayed_loss_pole_ids: Sequence[str] = (),
        out_of_order_pole_ids: Sequence[str] = (),
    ) -> tuple[str, ...]:
        affected = set(affected_ids)
        missing = set(missing_device_pole_ids)
        omitted = set(omit_loss_pole_ids)
        noise_groups = (
            (missing, missing_device_pole_ids),
            (omitted, omit_loss_pole_ids),
            (set(duplicate_loss_pole_ids), duplicate_loss_pole_ids),
            (set(delayed_loss_pole_ids), delayed_loss_pole_ids),
            (set(out_of_order_pole_ids), out_of_order_pole_ids),
        )
        if any(
            len(values) != len(source) or not values.issubset(affected)
            for values, source in noise_groups
        ):
            raise InvalidSimulatorNoiseError
        all_omitted = missing | omitted
        if all_omitted == affected:
            raise InvalidSimulatorNoiseError
        return tuple(sorted(all_omitted))

    async def get_fault(self, fault_id: UUID) -> SimulatedFaultView:
        try:
            async with self._session_factory() as session:
                fault = await session.get(SimulatedFault, fault_id)
                if fault is None:
                    raise SimulatorFaultNotFoundError
                return await self._fault_view(session, fault, ())
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error

    async def active_faults(self) -> tuple[SimulatedFaultView, ...]:
        try:
            async with self._session_factory() as session:
                faults = (
                    await session.scalars(
                        select(SimulatedFault)
                        .where(SimulatedFault.status == SimulatorFaultStatus.ACTIVE)
                        .order_by(SimulatedFault.injected_at, SimulatedFault.fault_id)
                    )
                ).all()
                return tuple([await self._fault_view(session, fault, ()) for fault in faults])
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error

    async def repair_fault(
        self,
        fault_id: UUID,
        *,
        restoration_fraction: float = 1.0,
    ) -> SimulatedFaultView:
        if not 0 < restoration_fraction <= 1:
            raise ValueError("restoration fraction must be greater than zero and at most one")
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
                commands, restored_pole_ids = await self._repair_commands(
                    session,
                    tuple(fault.deenergized_pole_ids),
                    repair_at,
                    restoration_fraction=restoration_fraction,
                )
                receipts = await self._emit_all(commands)
                if restoration_fraction == 1:
                    fault.status = SimulatorFaultStatus.REPAIRED
                    fault.repaired_at = repair_at
                view = await self._fault_view(
                    session,
                    fault,
                    receipts,
                    restored_pole_ids=restored_pole_ids,
                    restoration_fraction=restoration_fraction,
                )
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
        repaired = tuple([await self.repair_fault(fault_id) for fault_id in active_ids])
        await self._restore_failed_devices()
        try:
            async with self._session_factory.begin() as session:
                await session.execute(
                    delete(ScheduledOutage).where(ScheduledOutage.source == "simulator-scenario")
                )
        except SQLAlchemyError as error:
            raise SimulatorStoreUnavailableError from error
        return repaired

    async def close(self) -> None:
        await self._telemetry_gateway.close()

    async def _emit_all(
        self,
        commands: Sequence[TelemetryCommand],
        *,
        delayed_loss_pole_ids: Sequence[str] = (),
    ) -> tuple[SimulatorEmissionReceipt, ...]:
        delayed = set(delayed_loss_pole_ids)
        receipts: list[SimulatorEmissionReceipt] = []
        for command in commands:
            if command.pole_id in delayed and command.event == TelemetryEventType.POWER_LOST:
                await asyncio.sleep(0.25)
            receipts.append(await self._telemetry_gateway.emit(command))
        return tuple(receipts)

    async def _resolve_span(
        self,
        session: AsyncSession,
        transformer_id: int,
        parent_external_id: str,
        child_external_id: str,
    ) -> tuple[int, int, tuple[str, ...]]:
        parent = aliased(Pole)
        child = aliased(Pole)
        dataset_id = await session.scalar(
            select(GeneratedDataset.id)
            .order_by(GeneratedDataset.created_at.desc(), GeneratedDataset.id.desc())
            .limit(1)
        )
        edges = (
            await session.execute(
                select(
                    parent.id.label("parent_id"),
                    parent.pole_id.label("parent_external_id"),
                    child.id.label("child_id"),
                    child.pole_id.label("child_external_id"),
                )
                .select_from(SimulatorTopologyEdge)
                .outerjoin(parent, parent.id == SimulatorTopologyEdge.parent_pole_id)
                .join(child, child.id == SimulatorTopologyEdge.child_pole_id)
                .where(
                    SimulatorTopologyEdge.dt_id == transformer_id,
                    SimulatorTopologyEdge.dataset_id == dataset_id,
                )
            )
        ).all()
        if not edges:
            topology_version = await session.scalar(
                select(func.max(TopologyEdge.topology_version)).where(
                    TopologyEdge.dt_id == transformer_id
                )
            )
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
        visited: set[str] = set()
        pending = [child_external_id]
        while pending:
            pole_id = pending.pop(0)
            if pole_id in visited:
                continue
            visited.add(pole_id)
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
                    Device.id.label("internal_device_id"),
                    Device.device_id,
                    Device.installed_firmware,
                    DeviceHealth.last_sequence,
                    DeviceHealth.firmware,
                    DeviceHealth.status,
                    DeviceHealth.can_report_power_loss,
                    DeviceHealth.status_reason,
                )
                .join(DeviceBinding, DeviceBinding.pole_id == Pole.id)
                .join(Device, Device.id == DeviceBinding.device_id)
                .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
                .where(Pole.pole_id.in_(pole_ids), DeviceBinding.valid_to.is_(None))
            )
        ).all()
        result = {
            row.pole_id: SimulatorDevice(
                internal_device_id=row.internal_device_id,
                device_id=row.device_id,
                installed_firmware=row.installed_firmware,
                last_sequence=row.last_sequence,
                firmware=row.firmware,
                health_status=row.status,
                can_report_power_loss=row.can_report_power_loss,
                status_reason=row.status_reason,
            )
            for row in rows
        }
        return result

    async def _injection_commands(
        self,
        session: AsyncSession,
        parent_pole_id: str | None,
        affected_ids: tuple[str, ...],
        occurred_at: datetime,
        omitted_loss_pole_ids: tuple[str, ...] = (),
        missing_device_pole_ids: tuple[str, ...] = (),
        *,
        loss_delivery_context: str,
        duplicate_loss_pole_ids: tuple[str, ...] = (),
        delayed_loss_pole_ids: tuple[str, ...] = (),
        out_of_order_pole_ids: tuple[str, ...] = (),
        force_loss_delivery: bool = False,
    ) -> tuple[TelemetryCommand, ...]:
        pole_ids = ((parent_pole_id,) if parent_pole_id is not None else ()) + affected_ids
        rows = await self._device_rows(session, pole_ids)
        if parent_pole_id is not None and parent_pole_id not in rows:
            raise MissingSimulatorDeviceError
        if missing_device_pole_ids:
            if any(pole_id not in rows for pole_id in missing_device_pole_ids):
                raise MissingSimulatorDeviceError
            missing_device_ids = tuple(
                rows[pole_id].internal_device_id for pole_id in missing_device_pole_ids
            )
            await session.execute(
                update(DeviceHealth)
                .where(DeviceHealth.device_id.in_(missing_device_ids))
                .values(
                    status=DeviceHealthStatus.STALE,
                    status_reason="simulator_missing_device",
                    updated_at=occurred_at,
                )
            )
        commands: list[TelemetryCommand] = []
        forced_delivery_ids = (
            set(duplicate_loss_pole_ids) | set(delayed_loss_pole_ids) | set(out_of_order_pole_ids)
        )
        for pole_id in pole_ids:
            row = rows.get(pole_id)
            if row is None:
                continue
            is_affected = pole_id in affected_ids
            if is_affected and pole_id in omitted_loss_pole_ids:
                continue
            if row.health_status not in (None, DeviceHealthStatus.HEALTHY) and (
                row.status_reason != "device_silence_timeout"
            ):
                continue
            if is_affected and row.can_report_power_loss is False:
                continue
            if (
                is_affected
                and pole_id not in forced_delivery_ids
                and not force_loss_delivery
                and not power_loss_delivery_succeeds(
                    pole_id,
                    context=loss_delivery_context,
                    ratio=self._power_loss_delivery_ratio,
                    seed=self._power_loss_delivery_seed,
                )
            ):
                continue
            command = self._command(
                row,
                pole_id,
                TelemetryEventType.POWER_LOST if is_affected else TelemetryEventType.HEARTBEAT,
                not is_affected,
                occurred_at,
                max(
                    (row.last_sequence or 0) + 1,
                    int(occurred_at.timestamp() * 1_000_000),
                ),
            )
            commands.append(command)
            if is_affected and pole_id in duplicate_loss_pole_ids:
                commands.append(command)
            if is_affected and pole_id in out_of_order_pole_ids:
                commands.append(
                    self._command(
                        row,
                        pole_id,
                        TelemetryEventType.HEARTBEAT,
                        True,
                        occurred_at - timedelta(seconds=1),
                        max(0, command.sequence - 1),
                    )
                )
        return tuple(commands)

    async def _repair_commands(
        self,
        session: AsyncSession,
        affected_ids: tuple[str, ...],
        occurred_at: datetime,
        *,
        restoration_fraction: float,
    ) -> tuple[tuple[TelemetryCommand, ...], tuple[str, ...]]:
        restore_count = max(1, round(len(affected_ids) * restoration_fraction))
        restored_pole_ids = tuple(sorted(affected_ids)[:restore_count])
        rows = await self._device_rows(session, restored_pole_ids)
        commands: list[TelemetryCommand] = []
        for index, pole_id in enumerate(restored_pole_ids):
            row = rows.get(pole_id)
            if row is None or (
                row.health_status not in (None, DeviceHealthStatus.HEALTHY)
                and row.status_reason != "device_silence_timeout"
            ):
                continue
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
        return tuple(commands), restored_pole_ids

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
        *,
        restored_pole_ids: tuple[str, ...] = (),
        restoration_fraction: float | None = None,
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
            restored_pole_ids=restored_pole_ids,
            restoration_fraction=restoration_fraction,
        )

    async def inject_fixed_fault(
        self,
        *,
        fault_type: SimulatorFaultType,
        dt_id: str,
        parent_pole_id: str,
        child_pole_id: str,
        feeder_id: str = "FDR-001",
        missing_device_pole_ids: tuple[str, ...] = (),
        omit_loss_pole_ids: tuple[str, ...] = (),
        duplicate_loss_pole_ids: tuple[str, ...] = (),
        delayed_loss_pole_ids: tuple[str, ...] = (),
        out_of_order_pole_ids: tuple[str, ...] = (),
        force_loss_delivery: bool = False,
    ) -> SimulatedFaultView:
        if fault_type == SimulatorFaultType.SPAN_FAULT:
            return await self.inject_fixed_span_fault(
                dt_id=dt_id,
                parent_pole_id=parent_pole_id,
                child_pole_id=child_pole_id,
                missing_device_pole_ids=missing_device_pole_ids,
                omit_loss_pole_ids=omit_loss_pole_ids,
                duplicate_loss_pole_ids=duplicate_loss_pole_ids,
                delayed_loss_pole_ids=delayed_loss_pole_ids,
                out_of_order_pole_ids=out_of_order_pole_ids,
                force_loss_delivery=force_loss_delivery,
            )
        return await self._inject_fixed_scope_fault(
            fault_type,
            dt_id=dt_id,
            feeder_id=feeder_id,
            missing_device_pole_ids=missing_device_pole_ids,
            omit_loss_pole_ids=omit_loss_pole_ids,
            duplicate_loss_pole_ids=duplicate_loss_pole_ids,
            delayed_loss_pole_ids=delayed_loss_pole_ids,
            out_of_order_pole_ids=out_of_order_pole_ids,
        )
