"""
Input validation — ZIP files, file paths, and token format.

SECURITY NOTES:
- ZIP validation blocks path traversal (../), symlinks, executables.
- File path validation prevents directory escape.
- Token format validation is a sanity check, not a security boundary.
"""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import List, Set

from app.utils.logging import get_logger

logger = get_logger("security.validators")

# File extensions that are rejected inside uploaded ZIPs.
# Covers compiled binaries, shared libraries, and shell scripts.
BLOCKED_EXTENSIONS: Set[str] = {
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".sh", ".bash", ".bat", ".cmd", ".ps1",
    ".com", ".msi", ".deb", ".rpm",
    ".o", ".obj", ".class", ".pyc", ".pyo",
}


class ZipValidationError(Exception):
    """Raised when a ZIP file fails security validation."""
    pass


def validate_zip(
    zip_path: Path,
    max_size_bytes: int,
    extract_to: Path,
) -> List[str]:
    """Validate a ZIP file for security threats.

    Checks:
    1. Total uncompressed size ≤ max_size_bytes
    2. No path traversal (../ in filenames)
    3. No symlinks
    4. No blocked file extensions (executables, binaries)
    5. No absolute paths in archive

    Args:
        zip_path: Path to the ZIP file on disk.
        max_size_bytes: Maximum allowed uncompressed size.
        extract_to: Target extraction directory (used for traversal check).

    Returns:
        List of validated filenames in the archive.

    Raises:
        ZipValidationError: If any check fails.
    """
    if not zip_path.exists():
        raise ZipValidationError("ZIP file does not exist")

    # Check file size on disk
    file_size = zip_path.stat().st_size
    if file_size > max_size_bytes:
        raise ZipValidationError(
            f"ZIP file too large: {file_size} bytes (max {max_size_bytes})"
        )

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Check for zip bombs (total uncompressed size)
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > max_size_bytes:
                raise ZipValidationError(
                    f"Uncompressed size too large: {total_uncompressed} bytes "
                    f"(max {max_size_bytes})"
                )

            validated_files: List[str] = []
            extract_resolved = extract_to.resolve()

            for info in zf.infolist():
                name = info.filename

                # ── Check 1: Path traversal ──────────────────
                if ".." in name or name.startswith("/") or name.startswith("\\"):
                    raise ZipValidationError(
                        f"Path traversal detected in archive: {name!r}"
                    )

                # Verify resolved path stays within extract directory
                target = (extract_to / name).resolve()
                if not str(target).startswith(str(extract_resolved)):
                    raise ZipValidationError(
                        f"Path traversal via resolution: {name!r} → {target}"
                    )

                # ── Check 2: Symlinks ────────────────────────
                # ZIP external_attr high 16 bits contain Unix mode
                unix_mode = info.external_attr >> 16
                if unix_mode != 0 and stat.S_ISLNK(unix_mode):
                    raise ZipValidationError(
                        f"Symlink detected in archive: {name!r}"
                    )

                # ── Check 3: Blocked extensions ──────────────
                if not info.is_dir():
                    ext = PurePosixPath(name).suffix.lower()
                    if ext in BLOCKED_EXTENSIONS:
                        raise ZipValidationError(
                            f"Blocked file type {ext!r} in archive: {name!r}"
                        )

                validated_files.append(name)

            logger.info(
                "ZIP validation passed",
                file_count=len(validated_files),
                total_size=total_uncompressed,
            )
            return validated_files

    except zipfile.BadZipFile as exc:
        raise ZipValidationError(f"Invalid or corrupted ZIP file: {exc}") from exc


def safe_extract_zip(zip_path: Path, extract_to: Path) -> None:
    """Extract a previously validated ZIP to the target directory.

    This should only be called AFTER validate_zip() passes.
    """
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)
    logger.info("ZIP extracted", target=str(extract_to))


def validate_file_path(base_dir: Path, filename: str) -> Path:
    """Validate that a filename stays within the base directory.

    Args:
        base_dir: The allowed root directory.
        filename: User-provided filename (may contain subdirectories).

    Returns:
        Resolved absolute path that is confirmed inside base_dir.

    Raises:
        ValueError: If the path escapes the base directory.
    """
    # Block obvious traversal patterns
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        raise ValueError(f"Invalid filename: {filename!r}")

    resolved = (base_dir / filename).resolve()
    base_resolved = base_dir.resolve()

    if not str(resolved).startswith(str(base_resolved)):
        raise ValueError(
            f"Path escapes base directory: {filename!r} → {resolved}"
        )

    return resolved


def validate_token_format(token: str) -> bool:
    """Basic Discord bot token format check.

    Discord bot tokens have 3 dot-separated base64 segments.
    This is a sanity check, not a definitive validator.
    The real validation is done by calling the Discord API.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False
    if any(len(p) == 0 for p in parts):
        return False
    return True
