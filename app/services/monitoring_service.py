"""
Monitoring service — background tasks for process health and maintenance.

Runs as asyncio tasks alongside the Discord bot:
- Periodic health checks (mark crashed processes)
- Process recovery after controller restart
- Auto-restart bots that were running before shutdown
- Memory usage monitoring with alerts
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import platform
import psutil

from app.database import queries as db
from app.security.encryption import decrypt_token
from app.services.process_service import ProcessService
from app.utils.logging import get_logger
from app.config.settings import get_settings

if TYPE_CHECKING:
    from app.services.deployment_service import DeploymentService

logger = get_logger("services.monitoring")


class MonitoringService:
    """Background monitoring tasks for the bot hosting system."""

    def __init__(
        self,
        process_service: ProcessService,
        deployment_service: "DeploymentService | None" = None,
    ) -> None:
        self._process = process_service
        self._deploy = deployment_service
        self._health_task: Optional[asyncio.Task] = None
        self._memory_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start all background monitoring tasks."""
        self._running = True

        # Recover any processes from before controller restart
        recovered = await self._process.recover_processes()
        if recovered > 0:
            logger.info(f"Recovered {recovered} bot processes from PID files")

        # Auto-restart bots that were running before shutdown
        await self._restart_dead_bots()

        self._health_task = asyncio.create_task(
            self._health_check_loop(), name="health_check"
        )
        self._memory_task = asyncio.create_task(
            self._memory_monitor_loop(), name="memory_monitor"
        )

        logger.info("Monitoring service started")

    async def _restart_dead_bots(self) -> None:
        """Restart bots that the DB says were running but aren't anymore."""
        all_bots = await db.list_all_bots()
        restarted = 0
        failed = 0

        for bot in all_bots:
            if bot["status"] != "running":
                continue

            # Check if already tracked and alive
            proc_status = self._process.get_status(bot["container_name"])
            if proc_status == "running":
                continue

            # Bot was running but process is gone — restart it
            try:
                token = decrypt_token(bot["encrypted_token"])
                await self._process.start_process(
                    container_name=bot["container_name"],
                    bot_dir=Path(bot["bot_path"]),
                    bot_token=token,
                    runtime=bot["runtime"],
                )
                restarted += 1
                logger.info(
                    "Auto-restarted bot",
                    bot_id=str(bot["id"]),
                    name=bot["name"],
                )
            except Exception as exc:
                failed += 1
                await db.update_bot_status(bot["id"], "crashed")
                logger.warning(
                    "Failed to auto-restart bot",
                    bot_id=str(bot["id"]),
                    error=str(exc),
                )

        if restarted > 0 or failed > 0:
            logger.info(
                f"Auto-restart complete: {restarted} restarted, {failed} failed"
            )

    async def stop(self) -> None:
        """Cancel all background tasks."""
        self._running = False

        for task in (self._health_task, self._memory_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Monitoring service stopped")

    # ── Health Checks ────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """Periodically check process health and update DB status."""
        while self._running:
            try:
                await self._run_health_checks()
            except Exception as exc:
                logger.error("Health check loop error", error=str(exc))

            await asyncio.sleep(30)  # Check every 30 seconds

    async def _run_health_checks(self) -> None:
        """Check all bots and sync DB status with process reality."""
        all_bots = await db.list_all_bots()

        for bot in all_bots:
            try:
                bot_dir = Path(bot["bot_path"])
                container_name = bot["container_name"]
                
                # 1. Enforce Disk Limit (Linux Only)
                disk_limit = bot.get("disk_limit_mb", get_settings().bot_disk_limit_mb)
                if platform.system() != "Windows" and bot_dir.exists():
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "du", "-sm", str(bot_dir),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, _ = await proc.communicate()
                        if proc.returncode == 0:
                            size_mb_str_with_tab = stdout.decode().split("\t")[0]
                            size_mb = int(size_mb_str_with_tab)
                            if size_mb > disk_limit:
                                logger.warning(
                                    "Bot exceeded disk limit",
                                    bot_id=str(bot["id"]),
                                    size_mb=size_mb,
                                    limit_mb=disk_limit
                                )
                                # Stop process and mark as error
                                await self._process.stop_process(container_name)
                                await db.update_bot_status(bot["id"], "error")
                                continue  # Skip normal health check
                    except Exception as exc:
                        logger.error("Failed to check disk size", bot_id=str(bot["id"]), error=str(exc))

                # 2. Check Process Status
                process_status = self._process.get_status(container_name)

                # Map process status to our status enum
                if process_status == "running":
                    db_status = "running"
                elif process_status == "exited":
                    db_status = "crashed"
                elif process_status == "not_found":
                    # If DB says running but process not found → crashed
                    if bot["status"] == "running":
                        db_status = "crashed"
                    else:
                        db_status = bot["status"]  # Keep current DB status
                else:
                    db_status = "stopped"

                # Only update if status actually changed
                if bot["status"] != db_status:
                    await db.update_bot_status(bot["id"], db_status)
                    logger.info(
                        "Bot status synced",
                        bot_id=str(bot["id"]),
                        old_status=bot["status"],
                        new_status=db_status,
                    )

            except Exception as exc:
                import traceback
                error_trace = traceback.format_exc()
                logger.warning(f"Health check failed for bot {bot['id']}: {error_trace}")

    # ── Memory Monitoring ────────────────────────────────────

    async def _memory_monitor_loop(self) -> None:
        """Monitor system memory and log warnings if usage is high."""
        while self._running:
            try:
                mem = psutil.virtual_memory()
                usage_pct = mem.percent

                if usage_pct > 90:
                    logger.critical(
                        "CRITICAL: System memory usage above 90%",
                        used_pct=usage_pct,
                        available_mb=mem.available // (1024 * 1024),
                    )
                elif usage_pct > 80:
                    logger.warning(
                        "High system memory usage",
                        used_pct=usage_pct,
                        available_mb=mem.available // (1024 * 1024),
                    )

            except Exception as exc:
                logger.error("Memory monitor error", error=str(exc))

            await asyncio.sleep(60)  # Check every minute

    # ── System Stats ─────────────────────────────────────────

    async def get_system_stats(self) -> dict:
        """Get current system resource usage."""
        try:
            mem = psutil.virtual_memory()
            cpu_pct = psutil.cpu_percent(interval=1)
            disk = psutil.disk_usage("/")

            return {
                "cpu_percent": cpu_pct,
                "memory_used_mb": mem.used // (1024 * 1024),
                "memory_total_mb": mem.total // (1024 * 1024),
                "memory_percent": mem.percent,
                "disk_used_gb": disk.used // (1024 * 1024 * 1024),
                "disk_total_gb": disk.total // (1024 * 1024 * 1024),
                "disk_percent": disk.percent,
            }
        except Exception as exc:
            logger.error("Failed to get system stats", error=str(exc))
            return {}
