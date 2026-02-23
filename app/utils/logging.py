"""
Structured logging setup using Loguru.

Features:
- JSON-structured output for production
- Token redaction filter (never logs secrets)
- Rotation and retention
- Stderr + file sinks
"""

from __future__ import annotations

import re
import sys
from typing import Any

from loguru import logger

# Regex patterns that look like Discord bot tokens (base64-ish with dots)
_TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}",
)


def _redact_tokens(message: str) -> str:
    """Replace anything that looks like a Discord bot token with [REDACTED]."""
    return _TOKEN_PATTERN.sub("[REDACTED]", message)


def _token_filter(record: dict[str, Any]) -> bool:
    """Loguru filter that redacts tokens from log messages."""
    record["message"] = _redact_tokens(record["message"])
    return True


def setup_logging(level: str = "INFO", log_file: str = "controller.log") -> None:
    """Configure Loguru with structured output and token redaction."""
    # Remove default handler
    logger.remove()

    # ── Stderr (human-readable, coloured) ────────────────────
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        filter=_token_filter,
        colorize=True,
        backtrace=True,
        diagnose=False,  # Don't expose variable values in prod tracebacks
    )

    # ── File (JSON structured, rotated) ──────────────────────
    logger.add(
        log_file,
        level=level,
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {name}:{function}:{line} | {message}",
        filter=_token_filter,
        rotation="50 MB",
        retention="30 days",
        compression="gz",
        serialize=True,  # JSON lines
        backtrace=True,
        diagnose=False,
    )

    logger.info("Logging initialised", level=level, log_file=log_file)


def get_logger(name: str = "controller"):
    """Return a contextualised logger instance."""
    return logger.bind(module=name)
