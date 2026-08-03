import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from propel.infra.dependencies import ApplicationResources

Probe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    database: bool
    redis: bool

    @property
    def healthy(self) -> bool:
        return self.database and self.redis


class HealthService:
    def __init__(self, database_probe: Probe, redis_probe: Probe) -> None:
        self._database_probe = database_probe
        self._redis_probe = redis_probe

    @classmethod
    def from_resources(
        cls,
        resources: ApplicationResources,
        timeout_seconds: float,
    ) -> "HealthService":
        async def database_probe() -> bool:
            async def execute() -> bool:
                async with resources.database.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                return True

            return await _bounded_probe(execute, timeout_seconds)

        async def redis_probe() -> bool:
            async def execute() -> bool:
                return bool(await resources.redis.ping())

            return await _bounded_probe(execute, timeout_seconds)

        return cls(database_probe=database_probe, redis_probe=redis_probe)

    async def check(self) -> HealthSnapshot:
        database, redis_available = await asyncio.gather(
            self._database_probe(),
            self._redis_probe(),
        )
        return HealthSnapshot(database=database, redis=redis_available)


async def _bounded_probe(probe: Probe, timeout_seconds: float) -> bool:
    try:
        return await asyncio.wait_for(probe(), timeout=timeout_seconds)
    except (TimeoutError, OSError, ConnectionError):
        return False
    except Exception:
        return False
