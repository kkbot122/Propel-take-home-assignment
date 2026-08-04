from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from propel.domain.enums import DeviceHealthStatus, PoleStatus
from propel.infra.database.models import (
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Pole,
    PoleState,
)


class StaleScanUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class StaleScanResult:
    scanned_devices: int
    stale_poles: int
    dt_ids: tuple[str, ...]


class PostgresStaleDeviceScanner:
    def __init__(self, engine: AsyncEngine) -> None:
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def scan_once(
        self,
        *,
        cutoff: datetime,
        scanned_at: datetime,
        limit: int,
    ) -> StaleScanResult:
        try:
            async with self._session_factory.begin() as session:
                device_ids = tuple(
                    (
                        await session.scalars(
                            select(DeviceHealth.device_id)
                            .where(
                                DeviceHealth.status == DeviceHealthStatus.HEALTHY,
                                DeviceHealth.last_seen_at.is_not(None),
                                DeviceHealth.last_seen_at < cutoff,
                            )
                            .order_by(DeviceHealth.last_seen_at, DeviceHealth.device_id)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
                if not device_ids:
                    return StaleScanResult(scanned_devices=0, stale_poles=0, dt_ids=())

                pole_rows = (
                    await session.execute(
                        select(
                            Pole.id.label("pole_id"),
                            DistributionTransformer.dt_id,
                        )
                        .join(DeviceBinding, DeviceBinding.pole_id == Pole.id)
                        .join(
                            DistributionTransformer,
                            DistributionTransformer.id == Pole.dt_id,
                        )
                        .where(
                            DeviceBinding.device_id.in_(device_ids),
                            DeviceBinding.valid_from <= scanned_at,
                            (
                                DeviceBinding.valid_to.is_(None)
                                | (DeviceBinding.valid_to > scanned_at)
                            ),
                        )
                    )
                ).all()
                pole_ids = tuple(row.pole_id for row in pole_rows)
                await session.execute(
                    update(DeviceHealth)
                    .where(DeviceHealth.device_id.in_(device_ids))
                    .values(
                        status=DeviceHealthStatus.STALE,
                        status_reason="device_silence_timeout",
                        updated_at=scanned_at,
                    )
                )
                stale_poles = 0
                if pole_ids:
                    result = await session.execute(
                        update(PoleState)
                        .where(
                            PoleState.pole_id.in_(pole_ids),
                            PoleState.state != PoleStatus.STALE,
                        )
                        .values(
                            state=PoleStatus.STALE,
                            reason="device_silence_timeout",
                            updated_at=scanned_at,
                        )
                    )
                    stale_poles = result.rowcount
        except SQLAlchemyError as error:
            raise StaleScanUnavailableError from error

        return StaleScanResult(
            scanned_devices=len(device_ids),
            stale_poles=stale_poles,
            dt_ids=tuple(sorted({row.dt_id for row in pole_rows})),
        )
