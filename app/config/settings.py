"""
Application settings — loaded from environment variables via Pydantic.

All resource limits and paths are configurable.
"""

from __future__ import annotations

import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from .env / environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Discord ──────────────────────────────────────────────
    discord_bot_token: str = Field(..., description="Controller bot token")
    admin_user_id: int = Field(
        default=941139424580890666,
        description="Primary admin Discord user ID",
    )

    # ── Database ─────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://controller:securepassword@localhost:5432/bot_hosting",
        description="asyncpg connection string",
    )
    db_pool_min: int = Field(default=5, ge=1)
    db_pool_max: int = Field(default=20, ge=2)

    # ── Redis ────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # ── Encryption ───────────────────────────────────────────
    encryption_key: str = Field(
        ...,
        description="Base64-encoded 32-byte key for AES-256-GCM",
    )

    # ── Paths ────────────────────────────────────────────────
    base_bot_path: str = Field(
        default="C:\\bots" if platform.system() == "Windows" else "/srv/bots",
        description="Root directory for user bot files",
    )

    # ── Resource Limits ──────────────────────────────────────
    max_bots_per_user: int = Field(default=3, ge=1)
    bot_ram_limit_mb: int = Field(default=4096, ge=64)
    bot_cpu_limit: float = Field(default=1.0, gt=0)
    bot_disk_limit_mb: int = Field(default=5120, ge=100)
    max_user_ram_mb: int = Field(default=1024, ge=256)
    max_zip_size_mb: int = Field(default=50, ge=1)

    # ── Rate Limiting ────────────────────────────────────────
    rate_limit_commands: int = Field(default=5, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=10)

    # ── Logging ──────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="controller.log")

    # ── Derived Properties ───────────────────────────────────


    @property
    def base_path(self) -> Path:
        return Path(self.base_bot_path)

    @property
    def bot_ram_limit_bytes(self) -> int:
        return self.bot_ram_limit_mb * 1024 * 1024

    @property
    def max_zip_size_bytes(self) -> int:
        return self.max_zip_size_mb * 1024 * 1024

    @property
    def is_windows(self) -> bool:
        return platform.system() == "Windows"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor — parsed once and cached."""
    return Settings()  # type: ignore[call-arg]
