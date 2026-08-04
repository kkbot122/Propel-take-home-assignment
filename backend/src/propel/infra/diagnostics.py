from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from propel.infra.database.models import (
    Device,
    DeviceBinding,
    DeviceHealth,
    DistributionTransformer,
    Incident,
    Pole,
    PoleState,
    TelemetryEvent,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiagnosticPage:
    items: tuple[dict[str, Any], ...]
    next_cursor: str | None


class OperationalDiagnosticsService:
    """Build bounded operational views without exposing raw telemetry payloads."""

    def __init__(
        self,
        database: AsyncEngine,
        redis: Redis,
        *,
        telemetry_stream_name: str,
        telemetry_consumer_group: str,
        dead_letter_stream_name: str,
        analysis_due_set_name: str,
        worker_heartbeat_key: str,
        worker_stale_after_seconds: float,
        telemetry_backlog_warning: int,
    ) -> None:
        self._database = database
        self._redis = redis
        self._telemetry_stream_name = telemetry_stream_name
        self._telemetry_consumer_group = telemetry_consumer_group
        self._dead_letter_stream_name = dead_letter_stream_name
        self._analysis_due_set_name = analysis_due_set_name
        self._worker_heartbeat_key = worker_heartbeat_key
        self._worker_stale_after_seconds = worker_stale_after_seconds
        self._telemetry_backlog_warning = telemetry_backlog_warning

    async def overview(self) -> dict[str, Any]:
        generated_at = datetime.now(UTC)
        warnings: list[dict[str, str]] = []
        database_summary = await self._database_summary()
        redis_summary = await self._redis_summary(generated_at)

        if database_summary is None:
            warnings.append(
                self._warning(
                    "DATABASE_UNAVAILABLE",
                    "critical",
                    "PostgreSQL diagnostics are unavailable; durable state may be inaccessible.",
                )
            )
        if redis_summary is None:
            warnings.append(
                self._warning(
                    "REDIS_UNAVAILABLE",
                    "critical",
                    "Redis diagnostics are unavailable; telemetry buffering and analysis "
                    "are degraded.",
                )
            )

        worker = (redis_summary or {}).get("worker", {"status": "unknown", "last_seen_at": None})
        queue = (redis_summary or {}).get(
            "queue",
            {
                "stream_length": None,
                "pending": None,
                "lag": None,
                "dead_letter_count": None,
                "analysis_pending": None,
                "analysis_overdue": None,
            },
        )
        if redis_summary is not None:
            if worker["status"] != "ok":
                warnings.append(
                    self._warning(
                        "WORKER_STALE",
                        "critical",
                        "The telemetry worker heartbeat is missing or stale.",
                    )
                )
            lag = queue["lag"]
            if lag is not None and lag > self._telemetry_backlog_warning:
                warnings.append(
                    self._warning(
                        "TELEMETRY_BACKLOG",
                        "warning",
                        f"Telemetry consumer lag is {lag} events.",
                    )
                )
            if queue["dead_letter_count"]:
                warnings.append(
                    self._warning(
                        "DEAD_LETTER_EVENTS",
                        "warning",
                        f"{queue['dead_letter_count']} telemetry events require investigation.",
                    )
                )
            if queue["analysis_overdue"]:
                warnings.append(
                    self._warning(
                        "ANALYSIS_RETRY_OVERDUE",
                        "warning",
                        f"{queue['analysis_overdue']} DT analyses are due or retrying.",
                    )
                )

        degraded = any(item["severity"] in {"critical", "warning"} for item in warnings)
        if degraded:
            logger.warning(
                json.dumps(
                    {
                        "event": "operational_diagnostics_degraded",
                        "database_status": (
                            "ok" if database_summary is not None else "unavailable"
                        ),
                        "redis_status": "ok" if redis_summary is not None else "unavailable",
                        "worker_status": worker["status"],
                        "warning_codes": [item["code"] for item in warnings],
                    }
                )
            )
        return {
            "status": "degraded" if degraded else "healthy",
            "generated_at": generated_at,
            "dependencies": {
                "database": {"status": "ok" if database_summary is not None else "unavailable"},
                "redis": {"status": "ok" if redis_summary is not None else "unavailable"},
            },
            "worker": worker,
            "queue": queue,
            "device_counts": (database_summary or {}).get("device_counts", {}),
            "pole_state_counts": (database_summary or {}).get("pole_state_counts", {}),
            "incident_counts": (database_summary or {}).get("incident_counts", {}),
            "latest_processed_at": (database_summary or {}).get("latest_processed_at"),
            "warnings": warnings,
        }

    async def telemetry_history(
        self,
        *,
        limit: int,
        before_id: int | None,
        device_id: str | None,
        pole_id: str | None,
    ) -> DiagnosticPage:
        query: Select[Any] = (
            select(
                TelemetryEvent.id,
                TelemetryEvent.event_id,
                TelemetryEvent.correlation_id,
                Device.device_id,
                Pole.pole_id,
                TelemetryEvent.event_type,
                TelemetryEvent.energized,
                TelemetryEvent.device_timestamp,
                TelemetryEvent.received_at,
                TelemetryEvent.processed_at,
                TelemetryEvent.sequence,
                TelemetryEvent.processing_outcome,
                TelemetryEvent.origin,
                TelemetryEvent.state_changed,
            )
            .join(Device, Device.id == TelemetryEvent.device_id)
            .join(Pole, Pole.id == TelemetryEvent.pole_id)
            .order_by(TelemetryEvent.id.desc())
            .limit(limit + 1)
        )
        if before_id is not None:
            query = query.where(TelemetryEvent.id < before_id)
        if device_id is not None:
            query = query.where(Device.device_id == device_id)
        if pole_id is not None:
            query = query.where(Pole.pole_id == pole_id)

        try:
            async with self._database.connect() as connection:
                rows = (await connection.execute(query)).mappings().all()
        except SQLAlchemyError as error:
            raise DiagnosticsUnavailableError("telemetry history is unavailable") from error

        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = tuple(
            {
                "event_id": str(row["event_id"]),
                "correlation_id": str(row["correlation_id"]),
                "device_id": row["device_id"],
                "pole_id": row["pole_id"],
                "event_type": row["event_type"],
                "energized": row["energized"],
                "device_timestamp": row["device_timestamp"],
                "received_at": row["received_at"],
                "processed_at": row["processed_at"],
                "sequence": row["sequence"],
                "processing_outcome": row["processing_outcome"],
                "origin": row["origin"],
                "state_changed": row["state_changed"],
            }
            for row in visible_rows
        )
        next_cursor = str(visible_rows[-1]["id"]) if has_more else None
        return DiagnosticPage(items=items, next_cursor=next_cursor)

    async def device_health(
        self,
        *,
        limit: int,
        after_device_id: str | None,
        status: str | None,
        dt_id: str | None,
    ) -> DiagnosticPage:
        query: Select[Any] = (
            select(
                Device.device_id,
                Pole.pole_id,
                DistributionTransformer.dt_id,
                DeviceHealth.status,
                PoleState.state.label("pole_state"),
                DeviceHealth.last_seen_at,
                DeviceHealth.last_sequence,
                DeviceHealth.last_event_type,
                DeviceHealth.firmware,
                DeviceHealth.battery_mv,
                DeviceHealth.rssi,
                DeviceHealth.status_reason,
                DeviceHealth.can_report_power_loss,
            )
            .outerjoin(
                DeviceBinding,
                (DeviceBinding.device_id == Device.id) & (DeviceBinding.valid_to.is_(None)),
            )
            .outerjoin(Pole, Pole.id == DeviceBinding.pole_id)
            .outerjoin(DistributionTransformer, DistributionTransformer.id == Pole.dt_id)
            .outerjoin(DeviceHealth, DeviceHealth.device_id == Device.id)
            .outerjoin(PoleState, PoleState.pole_id == Pole.id)
            .order_by(Device.device_id)
            .limit(limit + 1)
        )
        if after_device_id is not None:
            query = query.where(Device.device_id > after_device_id)
        if status is not None:
            query = query.where(DeviceHealth.status == status)
        if dt_id is not None:
            query = query.where(DistributionTransformer.dt_id == dt_id)

        try:
            async with self._database.connect() as connection:
                rows = (await connection.execute(query)).mappings().all()
        except SQLAlchemyError as error:
            raise DiagnosticsUnavailableError("device health is unavailable") from error

        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = tuple(
            {
                "device_id": row["device_id"],
                "pole_id": row["pole_id"],
                "dt_id": row["dt_id"],
                "status": row["status"] or "UNKNOWN",
                "pole_state": row["pole_state"] or "UNKNOWN",
                "last_seen_at": row["last_seen_at"],
                "last_sequence": row["last_sequence"],
                "last_event_type": row["last_event_type"],
                "firmware": row["firmware"],
                "battery_mv": row["battery_mv"],
                "rssi": row["rssi"],
                "status_reason": row["status_reason"] or "no health observation",
                "can_report_power_loss": bool(row["can_report_power_loss"]),
            }
            for row in visible_rows
        )
        next_cursor = visible_rows[-1]["device_id"] if has_more else None
        return DiagnosticPage(items=items, next_cursor=next_cursor)

    async def _database_summary(self) -> dict[str, Any] | None:
        try:
            async with self._database.connect() as connection:
                device_rows = (
                    await connection.execute(
                        select(DeviceHealth.status, func.count()).group_by(DeviceHealth.status)
                    )
                ).all()
                pole_rows = (
                    await connection.execute(
                        select(PoleState.state, func.count()).group_by(PoleState.state)
                    )
                ).all()
                incident_rows = (
                    await connection.execute(
                        select(Incident.status, func.count()).group_by(Incident.status)
                    )
                ).all()
                latest_processed_at = await connection.scalar(
                    select(func.max(TelemetryEvent.processed_at))
                )
        except SQLAlchemyError:
            return None
        return {
            "device_counts": {str(key): value for key, value in device_rows},
            "pole_state_counts": {str(key): value for key, value in pole_rows},
            "incident_counts": {str(key): value for key, value in incident_rows},
            "latest_processed_at": latest_processed_at,
        }

    async def _redis_summary(self, generated_at: datetime) -> dict[str, Any] | None:
        try:
            stream_length = await self._redis.xlen(self._telemetry_stream_name)
            dead_letter_count = await self._redis.xlen(self._dead_letter_stream_name)
            analysis_pending = await self._redis.zcard(self._analysis_due_set_name)
            analysis_overdue = await self._redis.zcount(
                self._analysis_due_set_name, "-inf", generated_at.timestamp()
            )
            heartbeat_value = await self._redis.get(self._worker_heartbeat_key)
            pending = 0
            lag = stream_length
            try:
                groups = await self._redis.xinfo_groups(self._telemetry_stream_name)
            except ResponseError:
                groups = []
            for group in groups:
                if group.get("name") == self._telemetry_consumer_group:
                    pending = int(group.get("pending", 0))
                    reported_lag = group.get("lag")
                    lag = int(reported_lag) if reported_lag is not None else stream_length
                    break
        except RedisError:
            return None

        heartbeat_at: datetime | None = None
        if heartbeat_value:
            try:
                heartbeat_at = datetime.fromisoformat(heartbeat_value)
            except ValueError:
                heartbeat_at = None
        worker_ok = (
            heartbeat_at is not None
            and (generated_at - heartbeat_at).total_seconds() <= self._worker_stale_after_seconds
        )
        return {
            "worker": {
                "status": "ok" if worker_ok else "stale",
                "last_seen_at": heartbeat_at,
            },
            "queue": {
                "stream_length": stream_length,
                "pending": pending,
                "lag": lag,
                "dead_letter_count": dead_letter_count,
                "analysis_pending": analysis_pending,
                "analysis_overdue": analysis_overdue,
            },
        }

    @staticmethod
    def _warning(code: str, severity: str, message: str) -> dict[str, str]:
        return {"code": code, "severity": severity, "message": message}


class DiagnosticsUnavailableError(RuntimeError):
    pass
