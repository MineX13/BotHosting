"""
Account API routes — current user info.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from panel.auth import get_current_user
from app.config.settings import get_settings
from app.database import queries as db

router = APIRouter()


@router.get("/account")
async def get_account(user: dict = Depends(get_current_user)):
    """Get current user's account info, limits, and bot count."""
    limits = await db.get_user_limits(user["user_id"])
    bot_count = await db.count_user_bots(user["user_id"])
    settings = get_settings()

    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "bot_count": bot_count,
        "limits": limits,
        "is_admin": user["user_id"] == settings.admin_user_id,
    }
