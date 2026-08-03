from pathlib import Path

from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[4]


def upgrade_to_head(connection: Connection) -> None:
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def run_migrations(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(upgrade_to_head)
