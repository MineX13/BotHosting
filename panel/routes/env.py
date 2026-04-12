"""
Environment variables API routes.

Users can manage per-bot environment variables through the web panel.
Token-related variables are masked in responses for security.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from panel.auth import get_current_user
from app.database import queries as db
from app.utils.helpers import safe_join
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger("panel.routes.env")

# Token env var names that are automatically set and should be masked
TOKEN_VARS = {"BOT_TOKEN", "TOKEN", "DISCORD_TOKEN", "DISCORD_BOT_TOKEN"}


def _parse_env_file(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict."""
    env_vars = {}
    if not env_path.exists():
        return env_vars

    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                env_vars[key] = value
    except Exception:
        pass

    return env_vars


def _write_env_file(env_path: Path, env_vars: dict[str, str]) -> None:
    """Write a dict to a .env file."""
    lines = []
    for key, value in sorted(env_vars.items()):
        # Quote values with spaces or special characters
        if " " in value or "#" in value or "'" in value:
            value = f'"{value}"'
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _get_bot_dir(user_id: int, bot_id: str) -> tuple:
    """Get and verify bot directory."""
    uid = UUID(bot_id)
    bot = await db.get_bot(uid)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if bot["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You don't own this bot")
    return bot, Path(bot["bot_path"])


# ── Get Environment Variables ────────────────────────────────


@router.get("/bots/{bot_id}/env")
async def get_env(bot_id: str, user: dict = Depends(get_current_user)):
    """Get bot's environment variables. Token variables are masked."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    env_path = bot_dir / ".env"
    env_vars = _parse_env_file(env_path)

    # Mask token variables
    masked = {}
    for key, value in env_vars.items():
        if key.upper() in TOKEN_VARS:
            masked[key] = "••••••••" + value[-6:] if len(value) > 6 else "••••••••"
        else:
            masked[key] = value

    return {
        "variables": masked,
        "count": len(masked),
        "token_vars": list(TOKEN_VARS),
    }


# ── Update Environment Variables ─────────────────────────────


class UpdateEnvRequest(BaseModel):
    variables: dict[str, str]


@router.put("/bots/{bot_id}/env")
async def update_env(
    bot_id: str,
    body: UpdateEnvRequest,
    user: dict = Depends(get_current_user),
):
    """Update bot's environment variables.

    Token variables (BOT_TOKEN, etc.) cannot be modified through this endpoint.
    They are managed automatically by the system.
    """
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    env_path = bot_dir / ".env"
    existing = _parse_env_file(env_path)

    # Preserve token variables — users can't change them through the panel
    for key in TOKEN_VARS:
        if key in existing:
            body.variables[key] = existing[key]

    # Remove any token vars the user tried to add
    for key in list(body.variables.keys()):
        if key.upper() in TOKEN_VARS and key not in existing:
            del body.variables[key]

    # Validate keys (no spaces, must be valid env var names)
    for key in body.variables:
        if not key or " " in key or not key.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(
                status_code=400,
                detail=f"Invalid variable name: '{key}'. Use only letters, numbers, and underscores.",
            )

    _write_env_file(env_path, body.variables)

    logger.info("Environment variables updated", bot_id=bot_id, count=len(body.variables))

    return {
        "saved": True,
        "count": len(body.variables),
    }
