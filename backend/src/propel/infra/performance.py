import argparse
import asyncio
import json
import os
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from propel.infra.database.models import TelemetryEvent
from propel.infra.settings import get_settings


@dataclass(frozen=True, slots=True)
class BoundDevice:
    device_id: str
    pole_id: str


@dataclass(frozen=True, slots=True)
class RepetitionResult:
    requested: int
    accepted: int
    rejected: int
    processed: int
    lost: int
    send_seconds: float
    accepted_messages_per_second: float
    batch_latency_ms_p50: float
    batch_latency_ms_p95: float
    queue_delay_ms_p50: float | None
    queue_delay_ms_p95: float | None
    processing_delay_ms_p50: float | None
    processing_delay_ms_p95: float | None
    drain_seconds: float
    ordering_guard_state: str | None


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.5)))
    return round(ordered[index], 3)


async def bound_devices(client: httpx.AsyncClient) -> tuple[str, list[BoundDevice]]:
    subdivision_response, poles_response = await asyncio.gather(
        client.get("/api/network/subdivision"),
        client.get("/api/network/subdivision/poles"),
    )
    subdivision_response.raise_for_status()
    poles_response.raise_for_status()
    poles = [
        BoundDevice(device_id=item["device_id"], pole_id=item["pole_id"])
        for item in poles_response.json()
        if item["device_id"] is not None
    ]
    if not poles:
        raise RuntimeError("the subdivision has no active device bindings")
    return subdivision_response.json()["dataset_id"], poles


async def processed_rows(event_ids: list[UUID]) -> list[tuple[datetime, datetime, datetime]]:
    if not event_ids:
        return []
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            return list(
                (
                    await session.execute(
                        select(
                            TelemetryEvent.received_at,
                            TelemetryEvent.processing_started_at,
                            TelemetryEvent.processed_at,
                        ).where(TelemetryEvent.event_id.in_(event_ids))
                    )
                ).all()
            )
    finally:
        await engine.dispose()


async def wait_for_processing(
    event_ids: list[UUID],
    *,
    timeout_seconds: float,
) -> tuple[list[tuple[datetime, datetime, datetime]], float]:
    started_at = perf_counter()
    deadline = started_at + timeout_seconds
    while True:
        rows = await processed_rows(event_ids)
        if len(rows) == len(event_ids) or perf_counter() >= deadline:
            return rows, perf_counter() - started_at
        await asyncio.sleep(0.25)


def payload(
    binding: BoundDevice,
    *,
    event_id: UUID,
    sequence: int,
    device_timestamp: datetime,
    event: str = "boot",
    energized: bool = True,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id),
        "correlation_id": str(uuid4()),
        "device_id": binding.device_id,
        "pole_id": binding.pole_id,
        "event": event,
        "energized": energized,
        "ts": device_timestamp.isoformat().replace("+00:00", "Z"),
        "seq": sequence,
        "battery_mv": 3480,
        "rssi": -91,
        "fw": "1.4.2",
    }


async def run_repetition(
    client: httpx.AsyncClient,
    devices: list[BoundDevice],
    *,
    message_count: int,
    duration_seconds: float,
    batch_size: int,
    drain_timeout_seconds: float,
    mode: str,
) -> RepetitionResult:
    event_ids: list[UUID] = []
    accepted_event_ids: list[UUID] = []
    rejected = 0
    batch_latencies: list[float] = []
    base_timestamp = datetime.now(UTC)
    sequence_base = int(base_timestamp.timestamp() * 1_000_000)
    started_at = perf_counter()
    sent = 0
    while sent < message_count:
        current_size = min(batch_size, message_count - sent)
        target_elapsed = sent / message_count * duration_seconds
        remaining = target_elapsed - (perf_counter() - started_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        batch_event_ids = [uuid4() for _ in range(current_size)]
        event_ids.extend(batch_event_ids)
        items = [
            payload(
                devices[0] if mode == "ordering-noise" else devices[(sent + offset) % len(devices)],
                event_id=batch_event_ids[offset],
                sequence=(
                    sequence_base
                    if mode == "ordering-noise" and sent + offset > 0
                    else sequence_base + sent + offset
                ),
                device_timestamp=base_timestamp + timedelta(microseconds=sent + offset),
                event=(
                    "power_lost"
                    if mode == "ordering-noise" and sent + offset > 0
                    else "heartbeat"
                    if mode == "ordering-noise"
                    else "boot"
                ),
                energized=not (mode == "ordering-noise" and sent + offset > 0),
            )
            for offset in range(current_size)
        ]
        request_started_at = perf_counter()
        response = await client.post("/api/telemetry/batch", json={"items": items})
        batch_latencies.append((perf_counter() - request_started_at) * 1_000)
        if response.status_code not in (202, 207):
            raise RuntimeError(
                f"batch ingestion failed with {response.status_code}: {response.text[:500]}"
            )
        for item in response.json()["results"]:
            if item["status"] == "accepted":
                accepted_event_ids.append(UUID(item["event_id"]))
            else:
                rejected += 1
        sent += current_size
    send_seconds = perf_counter() - started_at
    rows, drain_seconds = await wait_for_processing(
        accepted_event_ids,
        timeout_seconds=drain_timeout_seconds,
    )
    queue_delays = [
        (processing_started_at - received_at).total_seconds() * 1_000
        for received_at, processing_started_at, _ in rows
    ]
    processing_delays = [
        (processed_at - processing_started_at).total_seconds() * 1_000
        for _, processing_started_at, processed_at in rows
    ]
    ordering_guard_state = None
    if mode == "ordering-noise":
        poles_response = await client.get("/api/network/subdivision/poles")
        poles_response.raise_for_status()
        ordering_guard_state = next(
            item["state"] for item in poles_response.json() if item["pole_id"] == devices[0].pole_id
        )
        if ordering_guard_state != "LIVE":
            raise RuntimeError(
                f"duplicate/stale ordering guard regressed {devices[0].pole_id} to "
                f"{ordering_guard_state}"
            )
    accepted = len(accepted_event_ids)
    return RepetitionResult(
        requested=len(event_ids),
        accepted=accepted,
        rejected=rejected,
        processed=len(rows),
        lost=accepted - len(rows),
        send_seconds=round(send_seconds, 3),
        accepted_messages_per_second=round(accepted / send_seconds, 3),
        batch_latency_ms_p50=percentile(batch_latencies, 0.5),
        batch_latency_ms_p95=percentile(batch_latencies, 0.95),
        queue_delay_ms_p50=percentile(queue_delays, 0.5) if queue_delays else None,
        queue_delay_ms_p95=percentile(queue_delays, 0.95) if queue_delays else None,
        processing_delay_ms_p50=(percentile(processing_delays, 0.5) if processing_delays else None),
        processing_delay_ms_p95=(
            percentile(processing_delays, 0.95) if processing_delays else None
        ),
        drain_seconds=round(drain_seconds, 3),
        ordering_guard_state=ordering_guard_state,
    )


async def incident_list_latencies(client: httpx.AsyncClient, samples: int = 20) -> list[float]:
    latencies: list[float] = []
    for _ in range(samples):
        started_at = perf_counter()
        response = await client.get("/api/incidents?status=ACTIVE&limit=100")
        response.raise_for_status()
        latencies.append((perf_counter() - started_at) * 1_000)
    return latencies


async def run(args: argparse.Namespace) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(args.request_timeout),
        limits=limits,
    ) as client:
        dataset_id, devices = await bound_devices(client)
        repetitions = [
            await run_repetition(
                client,
                devices,
                message_count=args.messages,
                duration_seconds=args.duration,
                batch_size=args.batch_size,
                drain_timeout_seconds=args.drain_timeout,
                mode=args.mode,
            )
            for _ in range(args.repetitions)
        ]
        incident_latencies = await incident_list_latencies(client)
    return {
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mode": args.mode,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "dataset_id": dataset_id,
            "bound_devices": len(devices),
            "batch_size": args.batch_size,
            "target_messages": args.messages,
            "target_duration_seconds": args.duration,
            "repetitions": args.repetitions,
        },
        "repetitions": [asdict(item) for item in repetitions],
        "summary": {
            "accepted_messages_per_second_p50": percentile(
                [item.accepted_messages_per_second for item in repetitions], 0.5
            ),
            "accepted_messages_per_second_p95": percentile(
                [item.accepted_messages_per_second for item in repetitions], 0.95
            ),
            "lost_messages": sum(item.lost for item in repetitions),
            "incident_list_ms_p50": percentile(incident_latencies, 0.5),
            "incident_list_ms_p95": percentile(incident_latencies, 0.95),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record the PB-08 ingestion performance suite")
    parser.add_argument("--mode", choices=("steady", "burst", "ordering-noise"), default="steady")
    parser.add_argument("--base-url", default="http://backend-api:8000")
    parser.add_argument("--messages", type=int, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--request-timeout", type=float, default=10)
    parser.add_argument("--drain-timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.messages is None:
        args.messages = 5_000
    if args.duration is None:
        args.duration = 10.0
    if args.messages < 1 or args.duration <= 0 or args.batch_size < 1 or args.repetitions < 1:
        parser.error("messages, duration, batch size, and repetitions must be positive")
    if args.batch_size > get_settings().telemetry_batch_max_items:
        parser.error("batch size exceeds TELEMETRY_BATCH_MAX_ITEMS")
    return args


def main() -> None:
    args = parse_args()
    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
