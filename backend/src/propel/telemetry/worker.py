import asyncio
import json
import logging
import signal

from redis.exceptions import RedisError

from propel.infra.analysis import PostgresDtSnapshotRepository, RedisAnalysisScheduler
from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.incidents import IncidentStoreUnavailableError, PostgresIncidentService
from propel.infra.settings import get_settings
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
        )
        incident_service = PostgresIncidentService(resources.database)
        analysis_scheduler = RedisAnalysisScheduler(
            resources.redis,
            PostgresDtSnapshotRepository(resources.database),
            due_set_name=settings.analysis_due_set_name,
            live_freshness_seconds=settings.analysis_live_freshness_seconds,
            retry_delay_seconds=settings.analysis_retry_delay_seconds,
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
        while not stop_event.is_set():
            try:
                await consumer.run_cycle()
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
                        }
                    )
                )
                await wait_for_retry(stop_event, settings.worker_retry_delay_seconds)
                if not stop_event.is_set():
                    await consumer.ensure_group()
            except IncidentStoreUnavailableError as error:
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
    finally:
        await resources.close()
        print(json.dumps({"event": "worker_stopped", "status": "ok"}))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
