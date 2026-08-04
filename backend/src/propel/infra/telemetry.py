from collections.abc import Sequence
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

    async def resolve_many(self, pole_ids: Sequence[str]) -> dict[str, ResolvedPoleBinding]:
        unique_pole_ids = tuple(dict.fromkeys(pole_ids))
        if not unique_pole_ids:
            return {}
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
            .where(Pole.pole_id.in_(unique_pole_ids))
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).all()
        except SQLAlchemyError as error:
            raise IdentityLookupUnavailableError from error
        return {
            row.pole_id: ResolvedPoleBinding(
                pole_id=row.pole_id,
                active_device_id=row.device_id,
            )
            for row in rows
        }


class RedisTelemetryPublisher:
    def __init__(self, redis_client: Redis, stream_name: str) -> None:
        self._redis = redis_client
        self._stream_name = stream_name

    async def publish(self, envelope: TelemetryEnvelope) -> str:
        try:
            stream_id = await self._redis.xadd(self._stream_name, self._fields(envelope))
        except RedisError as error:
            raise TelemetryQueueUnavailableError from error
        return str(stream_id)

    async def publish_many(self, envelopes: Sequence[TelemetryEnvelope]) -> list[str]:
        if not envelopes:
            return []
        try:
            async with self._redis.pipeline(transaction=True) as pipeline:
                for envelope in envelopes:
                    pipeline.xadd(self._stream_name, self._fields(envelope))
                stream_ids = await pipeline.execute()
        except RedisError as error:
            raise TelemetryQueueUnavailableError from error
        return [str(stream_id) for stream_id in stream_ids]

    @staticmethod
    def _fields(envelope: TelemetryEnvelope) -> dict[str, str]:
        command = envelope.command
        return {
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
