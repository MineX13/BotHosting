"""
Discord OAuth2 + JWT authentication for the MineNodes Panel.

Flow:
1. User visits /api/auth/login → redirected to Discord OAuth2
2. Discord redirects back to /api/auth/callback with a code
3. We exchange the code for a Discord access token
4. We fetch the user's Discord profile (id, username, avatar)
5. We ensure the user exists in our DB
6. We issue a JWT containing the user's Discord ID
7. The JWT is stored client-side and sent as Bearer token
"""

from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlencode

import aiohttp
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import get_settings
from app.database import queries as db
from app.utils.logging import get_logger

logger = get_logger("panel.auth")
router = APIRouter()
security = HTTPBearer(auto_error=False)

# Discord OAuth2 endpoints
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"
DISCORD_USER_URL = f"{DISCORD_API_BASE}/users/@me"

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days


def _create_jwt(user_id: int, username: str) -> str:
    """Create a signed JWT for a user."""
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, settings.panel_secret_key, algorithm=JWT_ALGORITHM)


def _decode_jwt(token: str) -> dict:
    """Decode and verify a JWT."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.panel_secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Dependency: extract the authenticated user from the JWT."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = _decode_jwt(credentials.credentials)
    user_id = int(payload["sub"])

    # Check if user is suspended
    if await db.is_suspended(user_id):
        raise HTTPException(status_code=403, detail="Account suspended")

    return {
        "user_id": user_id,
        "username": payload.get("username", "Unknown"),
    }


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: require the user to be the admin."""
    settings = get_settings()
    if user["user_id"] != settings.admin_user_id:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── OAuth2 Routes ────────────────────────────────────────────


@router.get("/login")
async def login(request: Request):
    """Redirect user to Discord OAuth2 authorization page."""
    settings = get_settings()

    # Build the callback URL
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/callback"

    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
    }

    return RedirectResponse(f"{DISCORD_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def callback(request: Request, code: str):
    """Handle Discord OAuth2 callback."""
    settings = get_settings()

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/callback"

    # Exchange code for access token
    async with aiohttp.ClientSession() as session:
        token_data = {
            "client_id": settings.discord_client_id,
            "client_secret": settings.discord_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }

        async with session.post(DISCORD_TOKEN_URL, data=token_data) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error("Discord token exchange failed", status=resp.status, error=error_text)
                raise HTTPException(status_code=400, detail="Discord authentication failed")

            token_resp = await resp.json()

        access_token = token_resp["access_token"]

        # Fetch user profile
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(DISCORD_USER_URL, headers=headers) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=400, detail="Failed to fetch Discord profile")

            discord_user = await resp.json()

    user_id = int(discord_user["id"])
    username = discord_user.get("username", "Unknown")
    avatar = discord_user.get("avatar")
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png"
        if avatar
        else f"https://cdn.discordapp.com/embed/avatars/{int(user_id) % 5}.png"
    )

    # Ensure user exists in DB
    await db.ensure_user(user_id)

    logger.info("User authenticated via Discord", user_id=user_id, username=username)

    # Issue JWT
    token = _create_jwt(user_id, username)

    # Redirect to frontend with token
    frontend_url = str(request.base_url).rstrip("/")
    return RedirectResponse(
        f"{frontend_url}/login?token={token}&username={username}&avatar={avatar_url}"
    )


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return the current authenticated user's info."""
    limits = await db.get_user_limits(user["user_id"])
    bot_count = await db.count_user_bots(user["user_id"])

    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "bot_count": bot_count,
        "limits": limits,
        "is_admin": user["user_id"] == get_settings().admin_user_id,
    }
