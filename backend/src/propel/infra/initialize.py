import asyncio
import json
from dataclasses import asdict

from propel.infra.database.migrations import run_migrations
from propel.infra.database.seed import seed_database
from propel.infra.dependencies import ApplicationResources
from propel.infra.health import HealthService
from propel.infra.settings import get_settings
from propel.simulator.generation import NetworkGenerationConfig


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
        await run_migrations(resources.database)
        generation_config = NetworkGenerationConfig(
            seed=settings.simulator_generation_seed,
            substation_count=settings.simulator_generation_substations,
            feeders_per_substation=settings.simulator_generation_feeders_per_substation,
            transformers_per_feeder=settings.simulator_generation_transformers_per_feeder,
            min_poles_per_transformer=(settings.simulator_generation_min_poles_per_transformer),
            max_poles_per_transformer=(settings.simulator_generation_max_poles_per_transformer),
            surveyed_transformer_ratio=(settings.simulator_generation_surveyed_transformer_ratio),
            sensor_coverage_ratio=settings.simulator_generation_sensor_coverage_ratio,
            offline_device_ratio=settings.simulator_generation_offline_device_ratio,
            firmware_12_ratio=settings.simulator_generation_firmware_12_ratio,
        )
        seed_summary = await seed_database(
            resources.database,
            generation_config=generation_config,
            include_generated_network=settings.simulator_generated_network_enabled,
        )
        print(
            json.dumps(
                {
                    "event": "initialization_complete",
                    "status": "ok",
                    "seed": asdict(seed_summary),
                }
            )
        )
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(initialize())


if __name__ == "__main__":
    main()
