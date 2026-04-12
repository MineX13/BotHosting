"""
File manager API routes.

Browse, read, write, create, delete, rename files in bot directories.
All paths are validated to prevent directory traversal attacks.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from panel.auth import get_current_user
from app.database import queries as db
from app.utils.helpers import safe_join
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger("panel.routes.files")

# Files/dirs to hide from the file manager
HIDDEN_NAMES = {".pid", ".deps", "__pycache__", "node_modules", "_upload.zip", "runner.sh", ".git"}


async def _get_bot_dir(user_id: int, bot_id: str) -> tuple:
    """Get and verify bot directory."""
    uid = UUID(bot_id)
    bot = await db.get_bot(uid)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if bot["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You don't own this bot")
    return bot, Path(bot["bot_path"])


# ── List Files ───────────────────────────────────────────────


@router.get("/bots/{bot_id}/files")
async def list_files(
    bot_id: str,
    path: str = "/",
    user: dict = Depends(get_current_user),
):
    """List files and directories at a given path within the bot directory."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    # Resolve the target directory
    if path == "/" or path == "":
        target = bot_dir
    else:
        clean_path = path.lstrip("/")
        try:
            target = safe_join(bot_dir, clean_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")

    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    items = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name in HIDDEN_NAMES:
                continue

            item = {
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "path": str(entry.relative_to(bot_dir)).replace("\\", "/"),
            }

            if entry.is_file():
                try:
                    item["size"] = entry.stat().st_size
                    item["modified"] = entry.stat().st_mtime
                except OSError:
                    item["size"] = 0
                    item["modified"] = 0

                # Detect if file is editable (text)
                item["editable"] = _is_text_file(entry.name)

            items.append(item)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {
        "path": path,
        "items": items,
    }


# ── Read File ────────────────────────────────────────────────


@router.get("/bots/{bot_id}/files/content")
async def read_file(
    bot_id: str,
    path: str,
    user: dict = Depends(get_current_user),
):
    """Read the content of a file."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    clean_path = path.lstrip("/")
    try:
        file_path = safe_join(bot_dir, clean_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    # Check file size (max 1MB for web editor)
    if file_path.stat().st_size > 1 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large for web editor (max 1MB)")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read file: {exc}")

    return {
        "path": clean_path,
        "name": file_path.name,
        "content": content,
        "size": file_path.stat().st_size,
        "editable": _is_text_file(file_path.name),
    }


# ── Write File ───────────────────────────────────────────────


class SaveFileRequest(BaseModel):
    path: str
    content: str


@router.put("/bots/{bot_id}/files/content")
async def save_file(
    bot_id: str,
    body: SaveFileRequest,
    user: dict = Depends(get_current_user),
):
    """Save/update a file's content."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    clean_path = body.path.lstrip("/")
    try:
        file_path = safe_join(bot_dir, clean_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(body.content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cannot write file: {exc}")

    logger.info("File saved", bot_id=bot_id, path=clean_path)
    return {"saved": True, "path": clean_path}


# ── Create File/Directory ────────────────────────────────────


class CreateRequest(BaseModel):
    path: str
    is_dir: bool = False
    content: str = ""


@router.post("/bots/{bot_id}/files/create")
async def create_file(
    bot_id: str,
    body: CreateRequest,
    user: dict = Depends(get_current_user),
):
    """Create a new file or directory."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    clean_path = body.path.lstrip("/")
    try:
        target = safe_join(bot_dir, clean_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if target.exists():
        raise HTTPException(status_code=409, detail="Path already exists")

    try:
        if body.is_dir:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body.content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cannot create: {exc}")

    return {"created": True, "path": clean_path, "is_dir": body.is_dir}


# ── Delete File/Directory ────────────────────────────────────


class DeleteRequest(BaseModel):
    path: str


@router.delete("/bots/{bot_id}/files")
async def delete_file(
    bot_id: str,
    path: str,
    user: dict = Depends(get_current_user),
):
    """Delete a file or directory."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    clean_path = path.lstrip("/")
    if not clean_path:
        raise HTTPException(status_code=400, detail="Cannot delete root directory")

    try:
        target = safe_join(bot_dir, clean_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cannot delete: {exc}")

    logger.info("File deleted", bot_id=bot_id, path=clean_path)
    return {"deleted": True, "path": clean_path}


# ── Rename File ──────────────────────────────────────────────


class RenameRequest(BaseModel):
    old_path: str
    new_path: str


@router.post("/bots/{bot_id}/files/rename")
async def rename_file(
    bot_id: str,
    body: RenameRequest,
    user: dict = Depends(get_current_user),
):
    """Rename or move a file/directory."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    try:
        old = safe_join(bot_dir, body.old_path.lstrip("/"))
        new = safe_join(bot_dir, body.new_path.lstrip("/"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not old.exists():
        raise HTTPException(status_code=404, detail="Source path not found")

    if new.exists():
        raise HTTPException(status_code=409, detail="Destination already exists")

    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cannot rename: {exc}")

    return {"renamed": True, "old_path": body.old_path, "new_path": body.new_path}


# ── Upload Files ─────────────────────────────────────────────


@router.post("/bots/{bot_id}/files/upload")
async def upload_files(
    bot_id: str,
    path: str = "/",
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload one or more files to a directory."""
    bot, bot_dir = await _get_bot_dir(user["user_id"], bot_id)

    clean_path = path.lstrip("/")
    if clean_path:
        try:
            target_dir = safe_join(bot_dir, clean_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid path")
    else:
        target_dir = bot_dir

    target_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for f in files:
        try:
            file_path = safe_join(target_dir, f.filename)
            content = await f.read()
            file_path.write_bytes(content)
            uploaded.append(f.filename)
        except ValueError:
            continue  # Skip files with invalid names
        except Exception as exc:
            logger.error("Upload failed", file=f.filename, error=str(exc))

    return {"uploaded": uploaded, "count": len(uploaded)}


# ── Helpers ──────────────────────────────────────────────────


TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yml", ".yaml",
    ".toml", ".cfg", ".ini", ".env", ".txt", ".md", ".html", ".css",
    ".sh", ".bat", ".ps1", ".xml", ".csv", ".log", ".gitignore",
    ".dockerfile", ".conf", ".properties", ".sql",
}


def _is_text_file(filename: str) -> bool:
    """Check if a file is likely a text file based on extension."""
    ext = Path(filename).suffix.lower()
    return ext in TEXT_EXTENSIONS or not ext  # Extensionless files are probably text
