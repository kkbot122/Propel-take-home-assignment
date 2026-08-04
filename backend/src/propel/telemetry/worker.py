import asyncio
import json
import logging
import signal
from datetime import UTC, datetime, timedelta
from time import monotonic

from redis.exceptions import RedisError

from propel.infra.analysis import PostgresDtSnapshotRepository, RedisAnalysisScheduler
from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.incidents import IncidentStoreUnavailableError, PostgresIncidentService
from propel.infra.settings import get_settings
from propel.infra.simulator import HttpSimulatorTelemetryGateway, SimulatorTelemetryUnavailableError
from propel.infra.simulator_heartbeat import (
    PostgresSimulatorHeartbeatEmitter,
    SimulatorHeartbeatStoreUnavailableError,
)
from propel.infra.staleness import PostgresStaleDeviceScanner, StaleScanUnavailableError
from propel.infra.telemetry_processor import PostgresTelemetryProcessor
from propel.telemetry.consumer import RedisTelemetryConsumer


async def wait_for_retry(stop_event: asyncio.Event, delay_seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
    except TimeoutError:
        pass


async def run_worker() -> None:
    settings = get_settings()
    resources = ApplicationResources.create(settings)
    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    simulator_gateway: HttpSimulatorTelemetryGateway | None = None

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        event_loop.add_signal_handler(shutdown_signal, stop_event.set)

    try:
        health = await HealthService.from_resources(
            resources,
            timeout_seconds=settings.dependency_timeout_seconds,
        ).check()
        if not health.healthy:
            raise RuntimeError("worker dependencies are unavailable")
        consumer = RedisTelemetryConsumer(
            resources.redis,
            PostgresTelemetryProcessor(resources.database),
            stream_name=settings.telemetry_stream_name,
            group_name=settings.telemetry_consumer_group,
            consumer_name=settings.telemetry_consumer_name,
            dead_letter_stream_name=settings.telemetry_dead_letter_stream_name,
            analysis_due_set_name=settings.analysis_due_set_name,
            batch_size=settings.telemetry_consumer_batch_size,
            block_ms=settings.telemetry_consumer_block_ms,
            pending_idle_ms=settings.telemetry_pending_idle_ms,
            max_deliveries=settings.telemetry_max_deliveries,
            analysis_debounce_seconds=settings.analysis_debounce_seconds,
            processing_concurrency=settings.telemetry_processing_concurrency,
        )
        incident_service = PostgresIncidentService(resources.database)
        stale_scanner = PostgresStaleDeviceScanner(resources.database)
        heartbeat_emitter: PostgresSimulatorHeartbeatEmitter | None = None
        if settings.simulator_enabled:
            simulator_gateway = HttpSimulatorTelemetryGateway(
                settings.simulator_telemetry_url,
                timeout_seconds=settings.simulator_request_timeout_seconds,
            )
            heartbeat_emitter = PostgresSimulatorHeartbeatEmitter(
                resources.database,
                simulator_gateway,
            )
        analysis_scheduler = RedisAnalysisScheduler(
            resources.redis,
            PostgresDtSnapshotRepository(resources.database),
            due_set_name=settings.analysis_due_set_name,
            live_freshness_seconds=settings.analysis_live_freshness_seconds,
            retry_delay_seconds=settings.analysis_retry_delay_seconds,
            dt_fault_ratio=settings.analysis_dt_fault_ratio,
            dt_min_branches=settings.analysis_dt_min_branches,
            feeder_fault_ratio=settings.analysis_feeder_fault_ratio,
            feeder_min_dts=settings.analysis_feeder_min_dts,
            correlation_window_seconds=settings.analysis_correlation_window_seconds,
            schedule_early_grace_seconds=settings.scheduled_outage_early_grace_seconds,
            schedule_overrun_grace_seconds=settings.scheduled_outage_overrun_grace_seconds,
            candidate_sink=incident_service,
        )
        await consumer.ensure_group()
        recovered_count = await consumer.recover_owned_pending_once()
        print(json.dumps({"event": "worker_ready", "status": "ok"}))
        if recovered_count:
            print(
                json.dumps(
                    {
                        "event": "worker_pending_recovered",
                        "count": recovered_count,
                    }
                )
            )
        next_stale_scan_at = monotonic()
        next_heartbeat_at = monotonic()
        next_worker_heartbeat_at = monotonic()
        while not stop_event.is_set():
            try:
                if monotonic() >= next_worker_heartbeat_at:
                    worker_heartbeat_at = datetime.now(UTC)
                    await resources.redis.set(
                        settings.worker_heartbeat_key,
                        worker_heartbeat_at.isoformat(),
                        ex=settings.worker_heartbeat_ttl_seconds,
                    )
                    next_worker_heartbeat_at = (
                        monotonic() + settings.worker_heartbeat_interval_seconds
                    )
                await consumer.run_cycle()
                if heartbeat_emitter is not None and monotonic() >= next_heartbeat_at:
                    heartbeat_at = datetime.now(UTC)
                    heartbeat_result = await heartbeat_emitter.emit_once(
                        emitted_at=heartbeat_at,
                        batch_size=min(
                            settings.simulator_heartbeat_batch_size,
                            settings.telemetry_batch_max_items,
                        ),
                    )
                    print(
                        json.dumps(
                            {
                                "event": "simulator_heartbeat_emitted",
                                "eligible_devices": heartbeat_result.eligible_devices,
                                "emitted_events": heartbeat_result.emitted_events,
                                "excluded_fault_poles": heartbeat_result.excluded_fault_poles,
                            }
                        )
                    )
                    next_heartbeat_at = monotonic() + settings.simulator_heartbeat_interval_seconds
                if monotonic() >= next_stale_scan_at:
                    scanned_at = datetime.now(UTC)
                    stale_result = await stale_scanner.scan_once(
                        cutoff=scanned_at
                        - timedelta(seconds=settings.telemetry_stale_after_seconds),
                        scanned_at=scanned_at,
                        limit=settings.telemetry_stale_scan_batch_size,
                    )
                    if stale_result.dt_ids:
                        due_score = scanned_at.timestamp() + settings.analysis_debounce_seconds
                        await resources.redis.zadd(
                            settings.analysis_due_set_name,
                            {dt_id: due_score for dt_id in stale_result.dt_ids},
                            gt=True,
                        )
                    next_stale_scan_at = (
                        monotonic() + settings.telemetry_stale_scan_interval_seconds
                    )
                await analysis_scheduler.run_due_once()
                await incident_service.verify_restorations_once(
                    threshold=settings.restoration_threshold,
                    stabilization_seconds=settings.restoration_stabilization_seconds,
                )
            except RedisError as error:
                print(
                    json.dumps(
                        {
                            "event": "worker_dependency_error",
                            "dependency": "redis",
                            "error_type": type(error).__name__,
                            "error": str(error)[:500],
                        }
                    )
                )
                await wait_for_retry(stop_event, settings.worker_retry_delay_seconds)
                if not stop_event.is_set():
                    await consumer.ensure_group()
            except (IncidentStoreUnavailableError, StaleScanUnavailableError) as error:
                print(
                    json.dumps(
                        {
                            "event": "worker_dependency_error",
                            "dependency": "database",
                            "error_type": type(error).__name__,
                        }
                    )
                )
                await wait_for_retry(stop_event, settings.worker_retry_delay_seconds)
            except (
                SimulatorHeartbeatStoreUnavailableError,
                SimulatorTelemetryUnavailableError,
            ) as error:
                print(
                    json.dumps(
                        {
                            "event": "simulator_heartbeat_error",
                            "error_type": type(error).__name__,
                        }
                    )
                )
                await wait_for_retry(stop_event, settings.worker_retry_delay_seconds)
    finally:
        if simulator_gateway is not None:
            await simulator_gateway.close()
        await resources.close()
        print(json.dumps({"event": "worker_stopped", "status": "ok"}))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
