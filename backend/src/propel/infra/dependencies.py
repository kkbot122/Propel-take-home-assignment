from dataclasses import dataclass

import redis.asyncio as redis
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from propel.infra.settings import Settings


@dataclass(slots=True)
class ApplicationResources:
    database: AsyncEngine
    redis: Redis

    @classmethod
    def create(cls, settings: Settings) -> "ApplicationResources":
        database = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_pool_overflow,
        )
        redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.dependency_timeout_seconds,
            socket_timeout=settings.dependency_timeout_seconds,
            health_check_interval=30,
        )
        return cls(database=database, redis=redis_client)

    async def close(self) -> None:
        await self.redis.aclose()
        await self.database.dispose()
