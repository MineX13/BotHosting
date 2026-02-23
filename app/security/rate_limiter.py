"""
Redis-backed per-user rate limiter.

Uses sliding window counter pattern.
Provides both a standalone check and a discord.py app_commands check.
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from app.utils.logging import get_logger

logger = get_logger("security.rate_limiter")

_redis = None  # Set at startup


def init_rate_limiter(redis_client) -> None:
    """Store a reference to the Redis client for rate limiting."""
    global _redis
    _redis = redis_client
    logger.info("Rate limiter initialised with Redis")


async def check_rate_limit(
    user_id: int,
    max_calls: int = 5,
    window_seconds: int = 60,
) -> bool:
    """Check if a user is within their rate limit.

    Returns:
        True if the request is ALLOWED, False if rate-limited.
    """
    if _redis is None:
        # If Redis is unavailable, fail-open (allow) but log warning
        logger.warning("Rate limiter has no Redis connection — allowing request")
        return True

    key = f"rate_limit:{user_id}"

    try:
        current = await _redis.incr(key)

        # Set expiry on first request in window
        if current == 1:
            await _redis.expire(key, window_seconds)

        if current > max_calls:
            ttl = await _redis.ttl(key)
            logger.warning(
                "Rate limit exceeded",
                user_id=user_id,
                current=current,
                max_calls=max_calls,
                retry_after=ttl,
            )
            return False

        return True

    except Exception as exc:
        # Fail-open on Redis errors — don't block users due to infra issues
        logger.error("Rate limiter error", error=str(exc))
        return True


def rate_limit_check(max_calls: int = 5, window_seconds: int = 60):
    """discord.py app_commands.check decorator for rate limiting.

    Usage:
        @app_commands.command()
        @rate_limit_check(max_calls=5, window_seconds=60)
        async def my_command(interaction): ...
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        allowed = await check_rate_limit(
            interaction.user.id, max_calls, window_seconds
        )
        if not allowed:
            raise app_commands.CheckFailure(
                "⏳ You're sending commands too fast. Please wait a moment."
            )
        return True

    return app_commands.check(predicate)
