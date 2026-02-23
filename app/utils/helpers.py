"""
Utility helpers used across the project.

- Safe path joining
- Text chunking for Discord's 2000-char limit
- Size formatting
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import List


# ── Path Safety ──────────────────────────────────────────────

def safe_join(base: Path, *parts: str) -> Path:
    """Join path components and verify the result stays inside *base*.

    Raises:
        ValueError: If the resolved path escapes the base directory.
    """
    joined = base.joinpath(*parts).resolve()
    base_resolved = base.resolve()
    if not str(joined).startswith(str(base_resolved)):
        raise ValueError(f"Path traversal detected: {joined} escapes {base_resolved}")
    return joined


# ── Text Helpers ─────────────────────────────────────────────

def chunk_text(text: str, max_length: int = 1990) -> List[str]:
    """Split text into chunks that fit within Discord's message limit.

    Splits on newlines to avoid breaking mid-line.
    """
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    current_chunk: List[str] = []
    current_length = 0

    for line in text.splitlines(keepends=True):
        if current_length + len(line) > max_length:
            if current_chunk:
                chunks.append("".join(current_chunk))
            current_chunk = [line]
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line)

    if current_chunk:
        chunks.append("".join(current_chunk))

    return chunks


def format_size(size_bytes: int) -> str:
    """Format bytes as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


def sanitize_container_name(user_id: int, bot_increment: int) -> str:
    """Generate a deterministic, Docker-safe container name."""
    return f"hosted_bot_{user_id}_{bot_increment}"
