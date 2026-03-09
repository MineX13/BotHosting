"""
Deployment service — orchestrates the full bot deploy pipeline.

Handles:
1. create_bot:   validate → extract → install deps → start process → save
2. replace_files: stop → replace → reinstall deps → restart
3. delete_bot:    kill process → delete files → delete DB

All operations are fully async with comprehensive error handling.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, Optional
from uuid import UUID

import discord

from app.config.settings import get_settings
from app.database import queries as db
from app.security.encryption import encrypt_token, decrypt_token
from app.security.validators import (
    validate_zip,
    safe_extract_zip,
    validate_token_format,
    ZipValidationError,
)
from app.services.process_service import ProcessService, ProcessServiceError
from app.services.validation_service import (
    validate_discord_token,
    validate_runtime,
    TokenValidationError,
)
from app.utils.helpers import sanitize_container_name, safe_join
from app.utils.logging import get_logger

logger = get_logger("services.deployment")


class DeploymentError(Exception):
    """Raised when a deployment operation fails."""
    pass


class DeploymentService:
    """Manages the full lifecycle of bot deployments."""

    def __init__(self, process_service: ProcessService) -> None:
        self._process = process_service

    # ── Create Bot ───────────────────────────────────────────

    async def create_bot(
        self,
        user_id: int,
        zip_data: bytes,
        token: str,
        runtime: str = "python",
        name: Optional[str] = None,
    ) -> Dict[str, str]:
        """Full deployment pipeline for a new bot.

        Args:
            user_id: Discord user ID.
            zip_data: Raw bytes of the uploaded ZIP file.
            token: Discord bot token (plaintext — will be encrypted).
            runtime: 'python' or 'node'.
            name: Optional display name for the bot.

        Returns:
            Dict with bot_id, container_name, status.

        Raises:
            DeploymentError: If any step fails.
        """
        settings = get_settings()

        # ── Step 1: Validate inputs ──────────────────────────
        try:
            runtime = validate_runtime(runtime)
        except ValueError as exc:
            raise DeploymentError(str(exc)) from exc

        if not validate_token_format(token):
            raise DeploymentError(
                "Invalid token format. Discord bot tokens have 3 dot-separated segments."
            )

        # ── Step 2: Validate token against Discord API ───────
        try:
            bot_info = await validate_discord_token(token)
        except TokenValidationError as exc:
            raise DeploymentError(f"Token validation failed: {exc}") from exc

        if name is None:
            name = bot_info.get("username", f"bot_{user_id}")

        # ── Step 3: Check user limits ────────────────────────
        user_limits = await db.get_user_limits(user_id)
        bot_count = await db.count_user_bots(user_id)
        if bot_count >= user_limits["max_bots"]:
            raise DeploymentError(
                f"Bot limit reached ({user_limits['max_bots']}). "
                "Delete an existing bot before creating a new one."
            )

        # ── Step 4: Prepare directories ──────────────────────
        increment = await db.get_next_bot_increment(user_id)
        container_name = sanitize_container_name(user_id, increment)
        bot_dir = settings.base_path / str(user_id) / f"bot_{increment}"

        try:
            bot_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DeploymentError(f"Cannot create bot directory: {exc}") from exc

        # ── Step 5: Write and validate ZIP ───────────────────
        zip_path = bot_dir / "_upload.zip"
        try:
            zip_path.write_bytes(zip_data)

            validate_zip(
                zip_path=zip_path,
                max_size_bytes=settings.max_zip_size_bytes,
                extract_to=bot_dir,
            )

            # Extract to bot directory
            safe_extract_zip(zip_path, bot_dir)
        except ZipValidationError as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"ZIP validation failed: {exc}") from exc
        except Exception as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"ZIP extraction failed: {exc}") from exc
        finally:
            zip_path.unlink(missing_ok=True)

        # ── Step 6: Install dependencies ─────────────────────
        try:
            await self._process.install_deps(bot_dir, runtime)
        except ProcessServiceError as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"Dependency installation failed: {exc}") from exc

        # ── Step 7: Encrypt token ────────────────────────────
        encrypted_token = encrypt_token(token)

        # ── Step 8: Start bot process ────────────────────────
        try:
            await self._process.start_process(
                container_name=container_name,
                bot_dir=bot_dir,
                bot_token=token,
                runtime=runtime,
                cpu_limit=user_limits["max_cpu"],
                disk_limit_mb=user_limits.get("max_disk_mb", settings.bot_disk_limit_mb),
            )
        except ProcessServiceError as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"Failed to start bot: {exc}") from exc

        # ── Step 9: Save to database ─────────────────────────
        try:
            await db.ensure_user(user_id)
            bot_record = await db.create_bot(
                user_id=user_id,
                name=name,
                container_name=container_name,
                encrypted_token=encrypted_token,
                runtime=runtime,
                bot_path=str(bot_dir),
                ram_limit_mb=user_limits["max_ram_mb"],
                cpu_limit=user_limits["max_cpu"],
                disk_limit_mb=user_limits.get("max_disk_mb", settings.bot_disk_limit_mb),
            )
            await db.update_bot_status(bot_record["id"], "running")
        except Exception as exc:
            await self._process.stop_process(container_name)
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"Database save failed: {exc}") from exc

        logger.info(
            "Bot deployed successfully",
            bot_id=str(bot_record["id"]),
            user_id=user_id,
            container=container_name,
        )

        return {
            "bot_id": str(bot_record["id"]),
            "container_name": container_name,
            "name": name,
            "status": "running",
            "runtime": runtime,
        }

    # ── Replace Files ────────────────────────────────────────

    async def replace_files(
        self,
        bot_id: UUID,
        zip_data: bytes,
    ) -> Dict[str, str]:
        """Replace bot files with new upload and restart.

        Args:
            bot_id: UUID of the bot to update.
            zip_data: Raw bytes of the new ZIP file.

        Returns:
            Dict with updated status.

        Raises:
            DeploymentError: If any step fails.
        """
        settings = get_settings()

        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")

        container_name = bot["container_name"]
        bot_dir = Path(bot["bot_path"])
        runtime = bot["runtime"]

        # ── Stop process ─────────────────────────────────────
        try:
            await self._process.stop_process(container_name)
        except ProcessServiceError as exc:
            logger.warning("Error stopping process for replace", error=str(exc))

        # ── Clear old files (keep logs dir) ──────────────────
        for item in bot_dir.iterdir():
            if item.name == "logs":
                continue  # Preserve log history
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

        # ── Write and validate new ZIP ───────────────────────
        zip_path = bot_dir / "_upload.zip"
        try:
            zip_path.write_bytes(zip_data)
            validate_zip(zip_path, settings.max_zip_size_bytes, bot_dir)
            safe_extract_zip(zip_path, bot_dir)
        except ZipValidationError as exc:
            raise DeploymentError(f"ZIP validation failed: {exc}") from exc
        finally:
            zip_path.unlink(missing_ok=True)

        # ── Reinstall dependencies ───────────────────────────
        try:
            await self._process.install_deps(bot_dir, runtime)
        except ProcessServiceError as exc:
            raise DeploymentError(f"Dependency installation failed: {exc}") from exc

        # ── Restart process ──────────────────────────────────
        try:
            decrypted_token = decrypt_token(bot["encrypted_token"])
            await self._process.start_process(
                container_name=container_name,
                bot_dir=bot_dir,
                bot_token=decrypted_token,
                runtime=runtime,
                cpu_limit=bot["cpu_limit"],
                disk_limit_mb=bot.get("disk_limit_mb", settings.bot_disk_limit_mb),
            )
            await db.update_bot_status(bot_id, "running")
        except ProcessServiceError as exc:
            await db.update_bot_status(bot_id, "error")
            raise DeploymentError(f"Restart after replace failed: {exc}") from exc

        logger.info("Bot files replaced successfully", bot_id=str(bot_id))
        return {"bot_id": str(bot_id), "status": "running"}

    # ── Delete Bot ───────────────────────────────────────────

    async def delete_bot(self, bot_id: UUID) -> None:
        """Fully delete a bot: kill process → delete files → delete DB.

        Args:
            bot_id: UUID of the bot to delete.

        Raises:
            DeploymentError: If the bot is not found.
        """
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")

        container_name = bot["container_name"]
        bot_dir = Path(bot["bot_path"])

        # Stop process
        try:
            await self._process.stop_process(container_name)
        except ProcessServiceError:
            pass

        # Delete files
        if bot_dir.exists():
            shutil.rmtree(bot_dir, ignore_errors=True)
            logger.info("Bot files deleted", path=str(bot_dir))

        # Delete DB record
        await db.delete_bot(bot_id)

        logger.info("Bot fully deleted", bot_id=str(bot_id), container=container_name)

    # ── Start / Stop / Restart ───────────────────────────────

    async def start_bot(self, bot_id: UUID) -> None:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")
        try:
            decrypted_token = decrypt_token(bot["encrypted_token"])
            await self._process.start_process(
                container_name=bot["container_name"],
                bot_dir=Path(bot["bot_path"]),
                bot_token=decrypted_token,
                runtime=bot["runtime"],
                cpu_limit=bot["cpu_limit"],
                disk_limit_mb=bot.get("disk_limit_mb", get_settings().bot_disk_limit_mb),
            )
            await db.update_bot_status(bot_id, "running")
        except ProcessServiceError as exc:
            raise DeploymentError(f"Failed to start bot: {exc}") from exc

    async def stop_bot(self, bot_id: UUID) -> None:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")
        try:
            await self._process.stop_process(bot["container_name"])
            await db.update_bot_status(bot_id, "stopped")
        except ProcessServiceError as exc:
            raise DeploymentError(f"Failed to stop bot: {exc}") from exc

    async def restart_bot(self, bot_id: UUID) -> None:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")
        try:
            decrypted_token = decrypt_token(bot["encrypted_token"])
            await self._process.restart_process(
                container_name=bot["container_name"],
                bot_dir=Path(bot["bot_path"]),
                bot_token=decrypted_token,
                runtime=bot["runtime"],
                cpu_limit=bot["cpu_limit"],
                disk_limit_mb=bot.get("disk_limit_mb", get_settings().bot_disk_limit_mb),
            )
            await db.update_bot_status(bot_id, "running")
        except ProcessServiceError as exc:
            raise DeploymentError(f"Failed to restart bot: {exc}") from exc

    # ── Logs ─────────────────────────────────────────────────

    async def get_bot_logs(self, bot_id: UUID, tail: int = 100) -> str:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")

        # Try tracked process first, fall back to file path
        logs = await self._process.get_logs(bot["container_name"], tail=tail)
        if logs == "No logs available — bot process not found.":
            logs = await self._process.get_logs_by_path(
                Path(bot["bot_path"]), tail=tail
            )
        return logs

    # ── File Editing ─────────────────────────────────────────

    async def read_bot_file(self, bot_id: UUID, filename: str) -> str:
        """Read a file from a bot's directory (with path validation)."""
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")

        bot_dir = Path(bot["bot_path"])
        try:
            file_path = safe_join(bot_dir, filename)
        except ValueError as exc:
            raise DeploymentError(f"Invalid file path: {exc}") from exc

        if not file_path.is_file():
            raise DeploymentError(f"File not found: {filename}")

        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as exc:
            raise DeploymentError(f"Cannot read file: {exc}") from exc

    async def write_bot_file(
        self,
        bot_id: UUID,
        filename: str,
        content: str,
    ) -> None:
        """Write content to a file in a bot's directory, then restart."""
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")

        bot_dir = Path(bot["bot_path"])
        try:
            file_path = safe_join(bot_dir, filename)
        except ValueError as exc:
            raise DeploymentError(f"Invalid file path: {exc}") from exc

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise DeploymentError(f"Cannot write file: {exc}") from exc

        # Restart process after file edit
        try:
            decrypted_token = decrypt_token(bot["encrypted_token"])
            await self._process.restart_process(
                container_name=bot["container_name"],
                bot_dir=bot_dir,
                bot_token=decrypted_token,
                runtime=bot["runtime"],
                cpu_limit=bot["cpu_limit"],
                disk_limit_mb=bot.get("disk_limit_mb", get_settings().bot_disk_limit_mb),
            )
            await db.update_bot_status(bot_id, "running")
        except ProcessServiceError as exc:
            await db.update_bot_status(bot_id, "error")
            raise DeploymentError(f"Restart after edit failed: {exc}") from exc

        logger.info("Bot file edited and restarted", bot_id=str(bot_id), file=filename)
