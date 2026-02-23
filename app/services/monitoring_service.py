"""
Monitoring service — background tasks for container health and maintenance.

Runs as asyncio tasks alongside the Discord bot:
- Periodic health checks (mark crashed containers)
- Weekly image cleanup
- Memory usage monitoring with alerts
"""

from __future__ import annotations

import asyncio
import platform
from typing import Optional

import psutil

from app.database import queries as db
from app.services.docker_service import DockerService
from app.utils.logging import get_logger

logger = get_logger("services.monitoring")


class MonitoringService:
    """Background monitoring tasks for the bot hosting system."""

    def __init__(self, docker_service: DockerService) -> None:
        self._docker = docker_service
        self._health_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._memory_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start all background monitoring tasks."""
        self._running = True

        self._health_task = asyncio.create_task(
            self._health_check_loop(), name="health_check"
        )
        self._cleanup_task = asyncio.create_task(
            self._image_cleanup_loop(), name="image_cleanup"
        )
        self._memory_task = asyncio.create_task(
            self._memory_monitor_loop(), name="memory_monitor"
        )

        logger.info("Monitoring service started")

    async def stop(self) -> None:
        """Cancel all background tasks."""
        self._running = False

        for task in (self._health_task, self._cleanup_task, self._memory_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Monitoring service stopped")

    # ── Health Checks ────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """Periodically check container health and update DB status."""
        while self._running:
            try:
                await self._run_health_checks()
            except Exception as exc:
                logger.error("Health check loop error", error=str(exc))

            await asyncio.sleep(30)  # Check every 30 seconds

    async def _run_health_checks(self) -> None:
        """Check all bots and sync DB status with Docker reality."""
        all_bots = await db.list_all_bots()

        for bot in all_bots:
            try:
                container_status = await self._docker.get_container_status(
                    bot["container_name"]
                )

                # Map Docker status to our status enum
                if container_status == "running":
                    db_status = "running"
                elif container_status in ("exited", "dead"):
                    db_status = "crashed"
                elif container_status == "not_found":
                    db_status = "stopped"
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
                logger.warning(
                    "Health check failed for bot",
                    bot_id=str(bot["id"]),
                    error=str(exc),
                )

    # ── Image Cleanup ────────────────────────────────────────

    async def _image_cleanup_loop(self) -> None:
        """Weekly cleanup of unused Docker images."""
        while self._running:
            # Wait 7 days between cleanups
            await asyncio.sleep(7 * 24 * 3600)

            try:
                result = await self._docker.prune_unused_images()
                logger.info("Weekly image cleanup completed", result=str(result))
            except Exception as exc:
                logger.error("Image cleanup failed", error=str(exc))

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
