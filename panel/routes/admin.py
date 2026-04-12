"""
Admin API routes.

User management, system stats, resource limits — admin only.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from panel.auth import require_admin
from app.database import queries as db
from app.services.deployment_service import DeploymentError
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger("panel.routes.admin")


def _get_deploy():
    from panel.main import get_deployment_service
    return get_deployment_service()


def _get_monitoring():
    from panel.main import get_monitoring_service
    return get_monitoring_service()


# ── List Users ───────────────────────────────────────────────


@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    users = await db.list_all_users()
    return {
        "users": [
            {
                "id": str(user["id"]),
                "suspended": user["suspended"],
                "max_bots": user["max_bots"],
                "max_ram_mb": user["max_ram_mb"],
                "max_cpu": user["max_cpu"],
                "max_disk_mb": user.get("max_disk_mb", 5120),
                "bot_count": user["bot_count"],
                "created_at": user["created_at"].isoformat(),
            }
            for user in users
        ],
        "total": len(users),
    }


# ── User Bots ────────────────────────────────────────────────


@router.get("/users/{user_id}/bots")
async def get_user_bots(user_id: str, admin: dict = Depends(require_admin)):
    uid = int(user_id)
    bots = await db.get_bots_by_user_id(uid)
    return {
        "bots": [
            {
                "id": str(bot["id"]),
                "name": bot["name"],
                "status": bot["status"],
                "runtime": bot["runtime"],
                "ram_limit_mb": bot["ram_limit_mb"],
                "cpu_limit": bot["cpu_limit"],
                "container_name": bot["container_name"],
                "created_at": bot["created_at"].isoformat(),
            }
            for bot in bots
        ]
    }


# ── User Limits ──────────────────────────────────────────────


class SetLimitsRequest(BaseModel):
    max_bots: int | None = None
    max_ram_mb: int | None = None
    max_cpu: float | None = None
    max_disk_mb: int | None = None


@router.put("/users/{user_id}/limits")
async def set_user_limits(
    user_id: str,
    body: SetLimitsRequest,
    admin: dict = Depends(require_admin),
):
    uid = int(user_id)
    await db.ensure_user(uid)

    updated = await db.update_user_limits(
        user_id=uid,
        max_bots=body.max_bots,
        max_ram_mb=body.max_ram_mb,
        max_cpu=body.max_cpu,
        max_disk_mb=body.max_disk_mb,
    )

    return {
        "user_id": user_id,
        "max_bots": updated["max_bots"],
        "max_ram_mb": updated["max_ram_mb"],
        "max_cpu": updated["max_cpu"],
        "max_disk_mb": updated.get("max_disk_mb", 5120),
    }


@router.get("/users/{user_id}/limits")
async def get_user_limits(user_id: str, admin: dict = Depends(require_admin)):
    uid = int(user_id)
    limits = await db.get_user_limits(uid)
    bot_count = await db.count_user_bots(uid)
    return {
        "user_id": user_id,
        "bot_count": bot_count,
        **limits,
    }


# ── Suspend / Unsuspend ──────────────────────────────────────


@router.post("/users/{user_id}/suspend")
async def suspend_user(user_id: str, admin: dict = Depends(require_admin)):
    uid = int(user_id)
    await db.ensure_user(uid)
    await db.suspend_user(uid)

    # Stop all bots
    deploy = _get_deploy()
    bot_ids = await db.get_user_bot_ids(uid)
    stopped = 0
    for bid in bot_ids:
        try:
            await deploy.stop_bot(bid)
            stopped += 1
        except DeploymentError:
            pass

    return {"suspended": True, "bots_stopped": stopped}


@router.post("/users/{user_id}/unsuspend")
async def unsuspend_user(user_id: str, admin: dict = Depends(require_admin)):
    uid = int(user_id)
    await db.unsuspend_user(uid)
    return {"suspended": False}


# ── System Stats ─────────────────────────────────────────────


@router.get("/stats")
async def get_stats(admin: dict = Depends(require_admin)):
    db_stats = await db.get_stats()
    monitoring = _get_monitoring()
    sys_stats = await monitoring.get_system_stats()

    return {
        "bots": db_stats,
        "system": sys_stats or {},
    }


# ── Admin Delete Bot ─────────────────────────────────────────


@router.delete("/bots/{bot_id}")
async def admin_delete_bot(bot_id: str, admin: dict = Depends(require_admin)):
    uid = UUID(bot_id)
    deploy = _get_deploy()
    try:
        await deploy.delete_bot(uid)
        return {"deleted": True}
    except DeploymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
