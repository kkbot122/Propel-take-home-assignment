import asyncio
import json
import signal

from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.settings import get_settings


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
        print(json.dumps({"event": "worker_ready", "status": "ok"}))
        await stop_event.wait()
    finally:
        await resources.close()
        print(json.dumps({"event": "worker_stopped", "status": "ok"}))


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
