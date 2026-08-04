from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from propel.domain.enums import TelemetryEventType, TelemetryOrigin


@dataclass(frozen=True, slots=True)
class TelemetryCommand:
    device_id: str
    pole_id: str
    event: TelemetryEventType
    energized: bool
    device_timestamp: datetime
    sequence: int
    battery_mv: int
    rssi: int
    firmware: str


@dataclass(frozen=True, slots=True)
class ResolvedPoleBinding:
    pole_id: str
    active_device_id: str | None


@dataclass(frozen=True, slots=True)
class TelemetryEnvelope:
    event_id: UUID
    correlation_id: UUID
    received_at: datetime
    command: TelemetryCommand
    origin: TelemetryOrigin = TelemetryOrigin.DEVICE


@dataclass(frozen=True, slots=True)
class TelemetryReceipt:
    event_id: UUID
    correlation_id: UUID
    received_at: datetime
    stream_id: str


class PoleBindingResolver(Protocol):
    async def resolve(self, pole_id: str) -> ResolvedPoleBinding | None: ...


class TelemetryPublisher(Protocol):
    async def publish(self, envelope: TelemetryEnvelope) -> str: ...


class UnknownPoleError(Exception):
    def __init__(self, pole_id: str) -> None:
        super().__init__(f"pole {pole_id} does not exist")
        self.pole_id = pole_id


class DeviceBindingConflictError(Exception):
    def __init__(self, pole_id: str, device_id: str) -> None:
        super().__init__(f"device {device_id} is not actively bound to pole {pole_id}")
        self.pole_id = pole_id
        self.device_id = device_id


class IdentityLookupUnavailableError(Exception):
    pass


class TelemetryQueueUnavailableError(Exception):
    pass


class TelemetryIngestionService:
    def __init__(
        self,
        binding_resolver: PoleBindingResolver,
        publisher: TelemetryPublisher,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._binding_resolver = binding_resolver
        self._publisher = publisher
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    async def ingest(
        self,
        command: TelemetryCommand,
        *,
        origin: TelemetryOrigin = TelemetryOrigin.DEVICE,
    ) -> TelemetryReceipt:
        received_at = self._clock().astimezone(UTC)
        binding = await self._binding_resolver.resolve(command.pole_id)
        if binding is None:
            raise UnknownPoleError(command.pole_id)
        if binding.active_device_id != command.device_id:
            raise DeviceBindingConflictError(command.pole_id, command.device_id)

        envelope = TelemetryEnvelope(
            event_id=self._id_factory(),
            correlation_id=self._id_factory(),
            received_at=received_at,
            origin=origin,
            command=TelemetryCommand(
                device_id=command.device_id,
                pole_id=command.pole_id,
                event=command.event,
                energized=command.energized,
                device_timestamp=command.device_timestamp.astimezone(UTC),
                sequence=command.sequence,
                battery_mv=command.battery_mv,
                rssi=command.rssi,
                firmware=command.firmware,
            ),
        )
        stream_id = await self._publisher.publish(envelope)
        return TelemetryReceipt(
            event_id=envelope.event_id,
            correlation_id=envelope.correlation_id,
            received_at=envelope.received_at,
            stream_id=stream_id,
        )
