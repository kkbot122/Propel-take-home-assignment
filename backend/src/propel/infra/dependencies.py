from dataclasses import dataclass

import redis.asyncio as redis
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from propel.infra.settings import Settings


def async_psycopg_url(database_url: str) -> str:
    """Select psycopg 3 when a managed service supplies a driverless PostgreSQL URL."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


@dataclass(slots=True)
class ApplicationResources:
    database: AsyncEngine
    redis: Redis

    @classmethod
    def create(cls, settings: Settings) -> "ApplicationResources":
        database = create_async_engine(
            async_psycopg_url(settings.database_url),
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
