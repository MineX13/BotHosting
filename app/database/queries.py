"""
Database query functions — all CRUD operations for users and bots.

Every function acquires a connection from the pool, performs the query,
and returns plain dicts / records. No ORM overhead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg

from app.database.connection import get_pool
from app.utils.logging import get_logger

logger = get_logger("database.queries")


# =====================================================================
# USER QUERIES
# =====================================================================

async def ensure_user(user_id: int) -> asyncpg.Record:
    """Insert user if not exists, return the user row."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (id)
            VALUES ($1)
            ON CONFLICT (id) DO NOTHING
            RETURNING *
            """,
            user_id,
        )
        if row is None:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return row  # type: ignore[return-value]


async def get_user(user_id: int) -> Optional[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)


async def suspend_user(user_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET suspended = TRUE WHERE id = $1", user_id
        )
    logger.info("User suspended", user_id=user_id)


async def unsuspend_user(user_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET suspended = FALSE WHERE id = $1", user_id
        )
    logger.info("User unsuspended", user_id=user_id)


async def is_suspended(user_id: int) -> bool:
    user = await get_user(user_id)
    if user is None:
        return False
    return bool(user["suspended"])


async def list_all_users() -> List[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT u.*, COUNT(b.id) AS bot_count
            FROM users u
            LEFT JOIN bots b ON b.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
        )


async def update_user_limits(
    user_id: int,
    max_bots: Optional[int] = None,
    max_ram_mb: Optional[int] = None,
    max_cpu: Optional[float] = None,
) -> asyncpg.Record:
    """Update per-user resource limits. Only updates non-None values."""
    pool = get_pool()
    async with pool.acquire() as conn:
        # Build SET clause dynamically
        sets = []
        params = []
        idx = 1

        if max_bots is not None:
            sets.append(f"max_bots = ${idx}")
            params.append(max_bots)
            idx += 1
        if max_ram_mb is not None:
            sets.append(f"max_ram_mb = ${idx}")
            params.append(max_ram_mb)
            idx += 1
        if max_cpu is not None:
            sets.append(f"max_cpu = ${idx}")
            params.append(max_cpu)
            idx += 1

        if not sets:
            return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(sets)} WHERE id = ${idx} RETURNING *"
        row = await conn.fetchrow(query, *params)

    logger.info("User limits updated", user_id=user_id,
                max_bots=max_bots, max_ram_mb=max_ram_mb, max_cpu=max_cpu)
    return row  # type: ignore[return-value]


async def get_user_limits(user_id: int) -> Dict[str, Any]:
    """Get per-user resource limits. Falls back to defaults if user not found."""
    user = await get_user(user_id)
    if user is None:
        return {"max_bots": 3, "max_ram_mb": 512, "max_cpu": 0.5}
    return {
        "max_bots": user["max_bots"],
        "max_ram_mb": user["max_ram_mb"],
        "max_cpu": user["max_cpu"],
    }


# =====================================================================
# BOT QUERIES
# =====================================================================

async def create_bot(
    user_id: int,
    name: str,
    container_name: str,
    encrypted_token: bytes,
    runtime: str,
    bot_path: str,
    ram_limit_mb: int = 512,
    cpu_limit: float = 0.5,
) -> asyncpg.Record:
    """Insert a new bot record and return it."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO bots (user_id, name, container_name, encrypted_token,
                              runtime, bot_path, ram_limit_mb, cpu_limit)
            VALUES ($1, $2, $3, $4, $5::bot_runtime, $6, $7, $8)
            RETURNING *
            """,
            user_id, name, container_name, encrypted_token,
            runtime, bot_path, ram_limit_mb, cpu_limit,
        )
    logger.info("Bot record created", bot_id=str(row["id"]), user_id=user_id)
    return row  # type: ignore[return-value]


async def get_bot(bot_id: UUID) -> Optional[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM bots WHERE id = $1", bot_id)


async def get_bot_by_container(container_name: str) -> Optional[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM bots WHERE container_name = $1", container_name
        )


async def list_user_bots(user_id: int) -> List[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM bots
            WHERE user_id = $1
            ORDER BY created_at DESC
            """,
            user_id,
        )


async def count_user_bots(user_id: int) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM bots WHERE user_id = $1",
            user_id,
        )
        return int(row["cnt"])  # type: ignore[index]


async def update_bot_status(bot_id: UUID, status: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE bots SET status = $1::bot_status WHERE id = $2",
            status, bot_id,
        )
    logger.info("Bot status updated", bot_id=str(bot_id), status=status)


async def delete_bot(bot_id: UUID) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM bots WHERE id = $1", bot_id)
    logger.info("Bot record deleted", bot_id=str(bot_id))


async def list_all_bots() -> List[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM bots ORDER BY created_at DESC")


async def get_next_bot_increment(user_id: int) -> int:
    """Return the next bot increment number for a user's directory."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) + 1 AS next_inc FROM bots WHERE user_id = $1",
            user_id,
        )
        return int(row["next_inc"])  # type: ignore[index]


async def get_stats() -> Dict[str, Any]:
    """Aggregate statistics for admin dashboard."""
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM bots")
        running = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM bots WHERE status = 'running'"
        )
        users = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM users")
        return {
            "total_bots": int(total["cnt"]),  # type: ignore[index]
            "running_bots": int(running["cnt"]),  # type: ignore[index]
            "total_users": int(users["cnt"]),  # type: ignore[index]
        }


async def get_bots_by_user_id(user_id: int) -> List[asyncpg.Record]:
    """Admin: list bots for a specific user."""
    return await list_user_bots(user_id)


async def get_user_bot_ids(user_id: int) -> List[UUID]:
    """Return all bot UUIDs for a user."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM bots WHERE user_id = $1", user_id
        )
        return [row["id"] for row in rows]
