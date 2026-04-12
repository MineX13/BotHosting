"""
Bot management API routes.

Covers: list, get, create, start, stop, restart, delete, replace files, logs.
"""

from __future__ import annotations

import io
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from panel.auth import get_current_user
from app.database import queries as db
from app.services.deployment_service import DeploymentError
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger("panel.routes.bots")


def _get_deploy():
    from panel.main import get_deployment_service
    return get_deployment_service()


def _get_process():
    from panel.main import get_process_service
    return get_process_service()


async def _verify_ownership(user_id: int, bot_id: UUID):
    """Verify the user owns the bot."""
    bot = await db.get_bot(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if bot["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You don't own this bot")
    return bot


# ── List User Bots ───────────────────────────────────────────


@router.get("/bots")
async def list_bots(user: dict = Depends(get_current_user)):
    bots = await db.list_user_bots(user["user_id"])
    process_svc = _get_process()

    result = []
    for bot in bots:
        stats = process_svc.get_process_stats(bot["container_name"])
        result.append({
            "id": str(bot["id"]),
            "name": bot["name"],
            "status": bot["status"],
            "runtime": bot["runtime"],
            "ram_limit_mb": bot["ram_limit_mb"],
            "cpu_limit": bot["cpu_limit"],
            "disk_limit_mb": bot.get("disk_limit_mb", 5120),
            "created_at": bot["created_at"].isoformat(),
            "updated_at": bot["updated_at"].isoformat(),
            "stats": {
                "cpu_percent": stats.get("cpu_percent", 0) if stats else 0,
                "memory_mb": round(stats.get("memory_mb", 0), 1) if stats else 0,
                "pid": stats.get("pid") if stats else None,
            } if stats else None,
        })

    return {"bots": result}


# ── Get Bot Details ──────────────────────────────────────────


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: str, user: dict = Depends(get_current_user)):
    uid = UUID(bot_id)
    bot = await _verify_ownership(user["user_id"], uid)
    process_svc = _get_process()
    stats = process_svc.get_process_stats(bot["container_name"])

    return {
        "id": str(bot["id"]),
        "name": bot["name"],
        "status": bot["status"],
        "runtime": bot["runtime"],
        "container_name": bot["container_name"],
        "bot_path": bot["bot_path"],
        "ram_limit_mb": bot["ram_limit_mb"],
        "cpu_limit": bot["cpu_limit"],
        "disk_limit_mb": bot.get("disk_limit_mb", 5120),
        "created_at": bot["created_at"].isoformat(),
        "updated_at": bot["updated_at"].isoformat(),
        "stats": {
            "cpu_percent": stats.get("cpu_percent", 0) if stats else 0,
            "memory_mb": round(stats.get("memory_mb", 0), 1) if stats else 0,
            "pid": stats.get("pid") if stats else None,
            "status": stats.get("status") if stats else None,
        } if stats else None,
    }


# ── Create Bot ───────────────────────────────────────────────


@router.post("/bots")
async def create_bot(
    zip_file: UploadFile = File(...),
    token: str = Form(...),
    runtime: str = Form(default="python"),
    name: str = Form(default=""),
    user: dict = Depends(get_current_user),
):
    deploy = _get_deploy()

    zip_data = await zip_file.read()
    if len(zip_data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ZIP file too large (max 50MB)")

    try:
        result = await deploy.create_bot(
            user_id=user["user_id"],
            zip_data=zip_data,
            token=token,
            runtime=runtime,
            name=name or None,
        )
        return result
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Power Controls ───────────────────────────────────────────


@router.post("/bots/{bot_id}/start")
async def start_bot(bot_id: str, user: dict = Depends(get_current_user)):
    uid = UUID(bot_id)
    await _verify_ownership(user["user_id"], uid)
    deploy = _get_deploy()
    try:
        await deploy.start_bot(uid)
        return {"status": "running"}
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/bots/{bot_id}/stop")
async def stop_bot(bot_id: str, user: dict = Depends(get_current_user)):
    uid = UUID(bot_id)
    await _verify_ownership(user["user_id"], uid)
    deploy = _get_deploy()
    try:
        await deploy.stop_bot(uid)
        return {"status": "stopped"}
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/bots/{bot_id}/restart")
async def restart_bot(bot_id: str, user: dict = Depends(get_current_user)):
    uid = UUID(bot_id)
    await _verify_ownership(user["user_id"], uid)
    deploy = _get_deploy()
    try:
        await deploy.restart_bot(uid)
        return {"status": "running"}
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Delete Bot ───────────────────────────────────────────────


@router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: str, user: dict = Depends(get_current_user)):
    uid = UUID(bot_id)
    await _verify_ownership(user["user_id"], uid)
    deploy = _get_deploy()
    try:
        await deploy.delete_bot(uid)
        return {"deleted": True}
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Replace Files ────────────────────────────────────────────


@router.post("/bots/{bot_id}/replace")
async def replace_files(
    bot_id: str,
    zip_file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    uid = UUID(bot_id)
    await _verify_ownership(user["user_id"], uid)
    deploy = _get_deploy()

    zip_data = await zip_file.read()
    if len(zip_data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ZIP file too large (max 50MB)")

    try:
        result = await deploy.replace_files(uid, zip_data)
        return result
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Logs ─────────────────────────────────────────────────────


@router.get("/bots/{bot_id}/logs")
async def get_logs(
    bot_id: str,
    tail: int = 200,
    user: dict = Depends(get_current_user),
):
    uid = UUID(bot_id)
    await _verify_ownership(user["user_id"], uid)
    deploy = _get_deploy()

    try:
        logs = await deploy.get_bot_logs(uid, tail=tail)
        return {"logs": logs}
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
