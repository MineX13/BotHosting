"""
MineNodes Bot Hoster — Main Entrypoint

Initialises:
1. Logging (loguru + token redaction)
2. Configuration (pydantic settings)
3. Encryption (AES-256-GCM)
4. Database pool (asyncpg → PostgreSQL)
5. Redis client (rate limiting)
6. Process service (subprocess management)
7. Deployment & monitoring services
8. Discord bot with slash commands
9. Graceful shutdown

Uses uvloop on Linux for better async performance.
"""

from __future__ import annotations

import asyncio
import platform
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

# ── Performance: use uvloop on Linux ─────────────────────────
if platform.system() != "Windows":
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass  # uvloop not installed — continue with default loop


from app.config.settings import get_settings
from app.database.connection import create_pool, close_pool
from app.security.encryption import init_encryption
from app.security.rate_limiter import init_rate_limiter
from app.services.process_service import ProcessService
from app.services.deployment_service import DeploymentService
from app.services.monitoring_service import MonitoringService
from app.utils.logging import setup_logging, get_logger


async def main() -> None:
    """Application entrypoint."""

    # ── 1. Load config ───────────────────────────────────────
    settings = get_settings()

    # ── 2. Init logging ──────────────────────────────────────
    setup_logging(level=settings.log_level, log_file=settings.log_file)
    logger = get_logger("main")
    logger.info(
        "Starting MineNodes Bot Hoster",
        platform=platform.system(),
        python=sys.version,
    )

    # ── 3. Init encryption ───────────────────────────────────
    init_encryption(settings.encryption_key)

    # ── 4. Create base bot path ──────────────────────────────
    settings.base_path.mkdir(parents=True, exist_ok=True)
    logger.info("Base bot path ready", path=str(settings.base_path))

    # ── 5. Database pool ─────────────────────────────────────
    logger.info("Connecting to PostgreSQL...")
    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )

    # ── 6. Redis client ──────────────────────────────────────
    redis_client = None
    try:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        await redis_client.ping()
        init_rate_limiter(redis_client)
        logger.info("Redis connected", url=settings.redis_url)
    except Exception as exc:
        logger.warning(
            "Redis connection failed — rate limiting disabled",
            error=str(exc),
        )

    # ── 7. Services ──────────────────────────────────────────
    process_service = ProcessService()
    deployment_service = DeploymentService(process_service)
    monitoring_service = MonitoringService(process_service)

    # ── 8. Discord bot ───────────────────────────────────────
    intents = discord.Intents.default()
    intents.message_content = False  # We only use slash commands

    bot = commands.Bot(
        command_prefix="!",  # Not used — slash commands only
        intents=intents,
        help_command=None,
    )

    # Inject services into bot for cog access
    bot.deployment_service = deployment_service  # type: ignore[attr-defined]
    bot.monitoring_service = monitoring_service  # type: ignore[attr-defined]
    bot.boot_time = datetime.now(timezone.utc)   # type: ignore[attr-defined]

    @bot.event
    async def on_ready():
        logger.info(
            "Bot is ready",
            user=str(bot.user),
            guilds=len(bot.guilds),
        )

        # Sync slash commands
        try:
            synced = await bot.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
        except Exception as exc:
            logger.error("Failed to sync commands", error=str(exc))

        # Start monitoring (also recovers processes)
        await monitoring_service.start()
        logger.info("Monitoring service started")

    @bot.event
    async def on_guild_join(guild: discord.Guild):
        logger.info("Joined guild", guild=guild.name, guild_id=guild.id)

    # ── 9. Load cogs ─────────────────────────────────────────
    await bot.load_extension("app.commands.user_commands")
    await bot.load_extension("app.commands.admin_commands")
    logger.info("Command cogs loaded")

    # ── 10. Run bot ──────────────────────────────────────────
    try:
        logger.info("Connecting to Discord...")
        await bot.start(settings.discord_bot_token)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as exc:
        logger.critical("Bot crashed", error=str(exc), exc_info=True)
    finally:
        # ── Graceful shutdown ────────────────────────────────
        logger.info("Shutting down...")

        await monitoring_service.stop()

        if not bot.is_closed():
            await bot.close()

        if redis_client is not None:
            await redis_client.close()

        await close_pool()

        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
