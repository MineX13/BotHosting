"""
Database connection pool management using asyncpg.

Provides:
- Pool creation and teardown
- Schema initialisation from schema.sql
- Pool accessor for use across the application
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import asyncpg

from app.utils.logging import get_logger

logger = get_logger("database.connection")

_pool: Optional[asyncpg.Pool] = None

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def create_pool(database_url: str, min_size: int = 5, max_size: int = 20) -> asyncpg.Pool:
    """Create the asyncpg connection pool and run schema migration."""
    global _pool

    logger.info("Creating database connection pool", min_size=min_size, max_size=max_size)

    _pool = await asyncpg.create_pool(
        database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
    )

    # Run schema on first startup (idempotent)
    await _init_schema()

    logger.info("Database pool created and schema initialised")
    return _pool


async def _init_schema() -> None:
    """Execute schema.sql to ensure tables exist."""
    if _pool is None:
        raise RuntimeError("Pool not initialised")

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with _pool.acquire() as conn:
        await conn.execute(schema_sql)
    logger.info("Database schema applied")


def get_pool() -> asyncpg.Pool:
    """Return the active connection pool.

    Raises:
        RuntimeError: If pool has not been created yet.
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool not initialised. Call create_pool() first."
        )
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")
