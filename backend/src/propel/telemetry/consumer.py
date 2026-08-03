import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from propel.infra.telemetry_processor import TelemetryProcessingResult
from propel.telemetry.messages import InvalidStreamMessageError

logger = logging.getLogger(__name__)


class TelemetryMessageProcessor(Protocol):
    async def process(self, fields: Mapping[str, str]) -> TelemetryProcessingResult: ...


@dataclass(frozen=True, slots=True)
class StreamEntry:
    message_id: str
    fields: dict[str, str]


class RedisTelemetryConsumer:
    def __init__(
        self,
        redis_client: Redis,
        processor: TelemetryMessageProcessor,
        *,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        dead_letter_stream_name: str,
        analysis_due_set_name: str,
        batch_size: int,
        block_ms: int,
        pending_idle_ms: int,
        max_deliveries: int,
        analysis_debounce_seconds: float,
    ) -> None:
        self._redis = redis_client
        self._processor = processor
        self._stream_name = stream_name
        self._group_name = group_name
        self._consumer_name = consumer_name
        self._dead_letter_stream_name = dead_letter_stream_name
        self._analysis_due_set_name = analysis_due_set_name
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._pending_idle_ms = pending_idle_ms
        self._max_deliveries = max_deliveries
        self._analysis_debounce_seconds = analysis_debounce_seconds

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._stream_name,
                self._group_name,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def recover_owned_pending_once(self) -> int:
        response = await self._redis.xreadgroup(
            self._group_name,
            self._consumer_name,
            streams={self._stream_name: "0"},
            count=self._batch_size,
        )
        entries = self._readgroup_entries(response)
        await self._process_entries(entries)
        return len(entries)

    async def claim_abandoned_once(self) -> int:
        response = await self._redis.xautoclaim(
            self._stream_name,
            self._group_name,
            self._consumer_name,
            min_idle_time=self._pending_idle_ms,
            start_id="0-0",
            count=self._batch_size,
        )
        claimed_entries = response[1] if len(response) > 1 else []
        entries = self._stream_entries(claimed_entries)
        await self._process_entries(entries)
        return len(entries)

    async def consume_new_once(self) -> int:
        response = await self._redis.xreadgroup(
            self._group_name,
            self._consumer_name,
            streams={self._stream_name: ">"},
            count=self._batch_size,
            block=self._block_ms,
        )
        entries = self._readgroup_entries(response)
        await self._process_entries(entries)
        return len(entries)

    async def run_cycle(self) -> int:
        claimed_count = await self.claim_abandoned_once()
        if claimed_count:
            return claimed_count
        return await self.consume_new_once()

    async def _process_entries(self, entries: list[StreamEntry]) -> None:
        for entry in entries:
            try:
                result = await self._processor.process(entry.fields)
                if result.state_changed:
                    due_score = result.received_at.timestamp() + self._analysis_debounce_seconds
                    await self._redis.zadd(
                        self._analysis_due_set_name,
                        {result.dt_id: due_score},
                        gt=True,
                    )
                await self._redis.xack(self._stream_name, self._group_name, entry.message_id)
                self._log_result(entry, result)
            except Exception as error:
                await self._handle_failure(entry, error)

    async def _handle_failure(self, entry: StreamEntry, error: Exception) -> None:
        pending = await self._redis.xpending_range(
            self._stream_name,
            self._group_name,
            entry.message_id,
            entry.message_id,
            1,
        )
        deliveries = int(pending[0]["times_delivered"]) if pending else 1
        reason = self._failure_reason(error)
        if deliveries >= self._max_deliveries:
            dead_letter_fields = {
                key: value[:256]
                for key, value in entry.fields.items()
                if key
                in {
                    "event_id",
                    "correlation_id",
                    "received_at",
                    "device_id",
                    "pole_id",
                    "event",
                    "seq",
                }
            }
            dead_letter_fields.update(
                {
                    "source_stream": self._stream_name,
                    "source_message_id": entry.message_id,
                    "failure_reason": reason,
                    "attempts": str(deliveries),
                    "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            await self._redis.xadd(self._dead_letter_stream_name, dead_letter_fields)
            await self._redis.xack(self._stream_name, self._group_name, entry.message_id)
            outcome = "dead_lettered"
        else:
            outcome = "retry_pending"

        logger.warning(
            json.dumps(
                {
                    "event": "telemetry_processing_failure",
                    "outcome": outcome,
                    "stream_message_id": entry.message_id,
                    "event_id": entry.fields.get("event_id"),
                    "device_id": entry.fields.get("device_id"),
                    "pole_id": entry.fields.get("pole_id"),
                    "attempts": deliveries,
                    "reason": reason,
                }
            )
        )

    @staticmethod
    def _failure_reason(error: Exception) -> str:
        if isinstance(error, InvalidStreamMessageError):
            return error.reason[:500]
        return type(error).__name__

    @staticmethod
    def _readgroup_entries(response: Any) -> list[StreamEntry]:
        entries: list[StreamEntry] = []
        for _stream_name, stream_entries in response or []:
            entries.extend(RedisTelemetryConsumer._stream_entries(stream_entries))
        return entries

    @staticmethod
    def _stream_entries(entries: Any) -> list[StreamEntry]:
        return [
            StreamEntry(
                message_id=str(message_id),
                fields={str(key): str(value) for key, value in fields.items()},
            )
            for message_id, fields in entries or []
        ]

    @staticmethod
    def _log_result(entry: StreamEntry, result: TelemetryProcessingResult) -> None:
        logger.info(
            json.dumps(
                {
                    "event": "telemetry_processed",
                    "stream_message_id": entry.message_id,
                    "event_id": str(result.event_id),
                    "device_id": entry.fields.get("device_id"),
                    "pole_id": entry.fields.get("pole_id"),
                    "dt_id": result.dt_id,
                    "processing_outcome": result.outcome.value,
                    "idempotent_replay": result.idempotent_replay,
                    "state_changed": result.state_changed,
                }
            )
        )
