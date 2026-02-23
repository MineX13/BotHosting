"""
Validation service — validates Discord bot tokens via the Discord API.

Uses aiohttp to make an authenticated request to GET /api/v10/users/@me.
If the token is valid, Discord returns bot user info.
If invalid, it returns 401.
"""

from __future__ import annotations

from typing import Dict, Optional

import aiohttp

from app.utils.logging import get_logger

logger = get_logger("services.validation")

DISCORD_API_BASE = "https://discord.com/api/v10"


class TokenValidationError(Exception):
    """Raised when a Discord token fails validation."""
    pass


async def validate_discord_token(token: str) -> Dict[str, str]:
    """Validate a Discord bot token by calling the Discord API.

    Args:
        token: The bot token to validate.

    Returns:
        Dict with bot user info (id, username, discriminator).

    Raises:
        TokenValidationError: If the token is invalid or API call fails.
    """
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "BotHostingController (https://github.com/bot-hosting, 1.0)",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DISCORD_API_BASE}/users/@me",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    raise TokenValidationError(
                        "Invalid bot token — Discord returned 401 Unauthorized"
                    )
                if resp.status == 403:
                    raise TokenValidationError(
                        "Bot token is valid but lacks required permissions"
                    )
                if resp.status != 200:
                    raise TokenValidationError(
                        f"Discord API returned unexpected status {resp.status}"
                    )

                data = await resp.json()

                # Verify this is actually a bot account
                if not data.get("bot", False):
                    raise TokenValidationError(
                        "Token belongs to a user account, not a bot"
                    )

                bot_info = {
                    "id": data["id"],
                    "username": data["username"],
                    "discriminator": data.get("discriminator", "0"),
                }

                logger.info(
                    "Bot token validated",
                    bot_username=bot_info["username"],
                    bot_id=bot_info["id"],
                )
                return bot_info

    except aiohttp.ClientError as exc:
        raise TokenValidationError(
            f"Failed to connect to Discord API: {exc}"
        ) from exc


def validate_runtime(runtime: str) -> str:
    """Validate and normalise the runtime parameter.

    Args:
        runtime: User-provided runtime string.

    Returns:
        Normalised runtime ('python' or 'node').

    Raises:
        ValueError: If runtime is not supported.
    """
    normalised = runtime.lower().strip()
    if normalised in ("python", "py", "python3"):
        return "python"
    if normalised in ("node", "nodejs", "js", "javascript"):
        return "node"
    raise ValueError(
        f"Unsupported runtime: {runtime!r}. Supported: python, node"
    )
