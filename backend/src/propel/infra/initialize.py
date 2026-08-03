import asyncio
import json

from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.settings import get_settings


async def initialize() -> None:
    settings = get_settings()
    resources = ApplicationResources.create(settings)
    try:
        health = await HealthService.from_resources(
            resources,
            timeout_seconds=settings.dependency_timeout_seconds,
        ).check()
        if not health.healthy:
            raise RuntimeError("required dependencies are unavailable")
        print(json.dumps({"event": "initialization_complete", "status": "ok"}))
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(initialize())


if __name__ == "__main__":
    main()
