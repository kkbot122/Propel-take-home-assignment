from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from propel.infra.database.models import Device, DeviceBinding, Pole
from propel.telemetry.ingestion import (
    IdentityLookupUnavailableError,
    ResolvedPoleBinding,
    TelemetryEnvelope,
    TelemetryQueueUnavailableError,
)


def redis_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class PostgresPoleBindingResolver:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def resolve(self, pole_id: str) -> ResolvedPoleBinding | None:
        statement = (
            select(Pole.pole_id, Device.device_id)
            .outerjoin(
                DeviceBinding,
                and_(
                    DeviceBinding.pole_id == Pole.id,
                    DeviceBinding.valid_from <= func.now(),
                    DeviceBinding.valid_to.is_(None),
                ),
            )
            .outerjoin(Device, Device.id == DeviceBinding.device_id)
            .where(Pole.pole_id == pole_id)
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise IdentityLookupUnavailableError from error

        if row is None:
            return None
        return ResolvedPoleBinding(pole_id=row.pole_id, active_device_id=row.device_id)


class RedisTelemetryPublisher:
    def __init__(self, redis_client: Redis, stream_name: str) -> None:
        self._redis = redis_client
        self._stream_name = stream_name

    async def publish(self, envelope: TelemetryEnvelope) -> str:
        command = envelope.command
        fields = {
            "event_id": str(envelope.event_id),
            "correlation_id": str(envelope.correlation_id),
            "received_at": redis_timestamp(envelope.received_at),
            "device_id": command.device_id,
            "pole_id": command.pole_id,
            "event": command.event.value,
            "energized": "true" if command.energized else "false",
            "ts": redis_timestamp(command.device_timestamp),
            "seq": str(command.sequence),
            "battery_mv": str(command.battery_mv),
            "rssi": str(command.rssi),
            "fw": command.firmware,
            "origin": envelope.origin.value,
        }
        try:
            stream_id = await self._redis.xadd(self._stream_name, fields)
        except RedisError as error:
            raise TelemetryQueueUnavailableError from error
        return str(stream_id)
