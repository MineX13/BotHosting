"""
Utility helpers used across the project.

- Dockerfile generation templates (Python / Node)
- Safe path joining
- Text chunking for Discord's 2000-char limit
- Size formatting
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import List


# ── Dockerfile Templates ─────────────────────────────────────

PYTHON_DOCKERFILE = """\
FROM python:3.11-slim

# Security: non-root user
RUN groupadd -r botuser && useradd -r -g botuser -d /app -s /sbin/nologin botuser

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

# Copy application code
COPY . .

# Security: change ownership and drop privileges
RUN chown -R botuser:botuser /app
USER botuser

CMD ["python", "main.py"]
"""

NODE_DOCKERFILE = """\
FROM node:20-slim

# Security: non-root user
RUN groupadd -r botuser && useradd -r -g botuser -d /app -s /sbin/nologin botuser

WORKDIR /app

# Install dependencies first (layer caching)
COPY package*.json ./
RUN npm ci --production 2>/dev/null || npm install --production 2>/dev/null || true

# Copy application code
COPY . .

# Security: change ownership and drop privileges
RUN chown -R botuser:botuser /app
USER botuser

CMD ["node", "index.js"]
"""


def generate_dockerfile(runtime: str) -> str:
    """Return the appropriate Dockerfile content for the given runtime.

    Args:
        runtime: Either 'python' or 'node'.

    Returns:
        Dockerfile content as a string.

    Raises:
        ValueError: If runtime is not supported.
    """
    templates = {
        "python": PYTHON_DOCKERFILE,
        "node": NODE_DOCKERFILE,
    }
    if runtime not in templates:
        raise ValueError(f"Unsupported runtime: {runtime!r}. Choose from: {list(templates.keys())}")
    return templates[runtime]


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
