"""
Command middleware — access control checks for slash commands.

Provides:
- Admin check (only ADMIN_USER_ID)
- Suspension check (block suspended users)
- Bot ownership check (user owns the bot)
"""

from __future__ import annotations

from uuid import UUID

import discord
from discord import app_commands

from app.config.settings import get_settings
from app.database import queries as db
from app.utils.logging import get_logger

logger = get_logger("commands.middleware")


# ── Admin Check ──────────────────────────────────────────────

def is_admin():
    """discord.py check: only allows the configured admin user."""

    async def predicate(interaction: discord.Interaction) -> bool:
        settings = get_settings()
        if interaction.user.id != settings.admin_user_id:
            raise app_commands.CheckFailure(
                "🚫 This command is restricted to administrators."
            )
        return True

    return app_commands.check(predicate)


# ── Suspension Check ─────────────────────────────────────────

def not_suspended():
    """discord.py check: blocks suspended users from all commands."""

    async def predicate(interaction: discord.Interaction) -> bool:
        suspended = await db.is_suspended(interaction.user.id)
        if suspended:
            raise app_commands.CheckFailure(
                "🚫 Your account has been suspended. Contact an administrator."
            )
        return True

    return app_commands.check(predicate)


# ── Bot Ownership Check ─────────────────────────────────────

async def verify_bot_ownership(
    interaction: discord.Interaction,
    bot_id: UUID,
    allow_admin: bool = True,
) -> bool:
    """Verify that the interaction user owns the specified bot.

    Admin users bypass this check if allow_admin is True.

    Args:
        interaction: The Discord interaction.
        bot_id: UUID of the bot to check.
        allow_admin: Whether admin can bypass ownership.

    Returns:
        True if the user is authorized.

    Raises:
        app_commands.CheckFailure: If the user doesn't own the bot.
    """
    settings = get_settings()

    # Admin bypass
    if allow_admin and interaction.user.id == settings.admin_user_id:
        return True

    bot = await db.get_bot(bot_id)
    if bot is None:
        raise app_commands.CheckFailure("❌ Bot not found.")

    if bot["user_id"] != interaction.user.id:
        logger.warning(
            "Unauthorized bot access attempt",
            user_id=interaction.user.id,
            bot_id=str(bot_id),
            owner_id=bot["user_id"],
        )
        raise app_commands.CheckFailure(
            "🚫 You don't own this bot."
        )

    return True
