"""
Deployment service — orchestrates the full bot deploy pipeline.

Handles:
1. create_bot:   validate → extract → Dockerfile → build → create → start → save
2. replace_files: stop → replace → rebuild → start
3. delete_bot:    stop → remove container → remove image → delete files → delete DB

All operations are fully async with comprehensive error handling.
"""

from __future__ import annotations

import shutil
import tempfile
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
from app.services.docker_service import DockerService, DockerServiceError
from app.services.validation_service import (
    validate_discord_token,
    validate_runtime,
    TokenValidationError,
)
from app.utils.helpers import generate_dockerfile, sanitize_container_name, safe_join
from app.utils.logging import get_logger

logger = get_logger("services.deployment")


class DeploymentError(Exception):
    """Raised when a deployment operation fails."""
    pass


class DeploymentService:
    """Manages the full lifecycle of bot deployments."""

    def __init__(self, docker_service: DockerService) -> None:
        self._docker = docker_service

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
        bot_count = await db.count_user_bots(user_id)
        if bot_count >= settings.max_bots_per_user:
            raise DeploymentError(
                f"Bot limit reached ({settings.max_bots_per_user}). "
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
            # Clean up on validation failure
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"ZIP validation failed: {exc}") from exc
        except Exception as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"ZIP extraction failed: {exc}") from exc
        finally:
            # Always remove the uploaded zip
            zip_path.unlink(missing_ok=True)

        # ── Step 6: Generate Dockerfile ──────────────────────
        try:
            dockerfile_content = generate_dockerfile(runtime)
            (bot_dir / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")
        except Exception as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"Dockerfile generation failed: {exc}") from exc

        # ── Step 7: Encrypt token ────────────────────────────
        encrypted_token = encrypt_token(token)

        # ── Step 8: Build Docker image ───────────────────────
        image_tag = f"hosted_bot:{container_name}"
        try:
            await self._docker.build_image(
                build_path=bot_dir,
                image_tag=image_tag,
                timeout=settings.docker_build_timeout,
            )
        except DockerServiceError as exc:
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"Docker build failed: {exc}") from exc

        # ── Step 9: Create and start container ───────────────
        try:
            await self._docker.create_container(
                image_tag=image_tag,
                container_name=container_name,
                bot_token=token,
                ram_limit_mb=settings.bot_ram_limit_mb,
                cpu_limit=settings.bot_cpu_limit,
            )
            await self._docker.start_container(container_name)
        except DockerServiceError as exc:
            # Clean up container and image on failure
            await self._docker.remove_container(container_name)
            await self._docker.remove_image(image_tag)
            shutil.rmtree(bot_dir, ignore_errors=True)
            raise DeploymentError(f"Container creation failed: {exc}") from exc

        # ── Step 10: Save to database ────────────────────────
        try:
            await db.ensure_user(user_id)
            bot_record = await db.create_bot(
                user_id=user_id,
                name=name,
                container_name=container_name,
                encrypted_token=encrypted_token,
                runtime=runtime,
                bot_path=str(bot_dir),
                ram_limit_mb=settings.bot_ram_limit_mb,
                cpu_limit=settings.bot_cpu_limit,
            )
            await db.update_bot_status(bot_record["id"], "running")
        except Exception as exc:
            # If DB fails, clean up everything
            await self._docker.stop_container(container_name)
            await self._docker.remove_container(container_name)
            await self._docker.remove_image(image_tag)
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
        """Replace bot files with new upload, rebuild, and restart.

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
        image_tag = f"hosted_bot:{container_name}"

        # ── Stop container ───────────────────────────────────
        try:
            await self._docker.stop_container(container_name)
            await self._docker.remove_container(container_name)
        except DockerServiceError as exc:
            logger.warning("Error stopping container for replace", error=str(exc))

        # ── Clear old files (keep directory) ─────────────────
        for item in bot_dir.iterdir():
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

        # ── Regenerate Dockerfile ────────────────────────────
        runtime = bot["runtime"]
        dockerfile_content = generate_dockerfile(runtime)
        (bot_dir / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")

        # ── Rebuild ──────────────────────────────────────────
        try:
            await self._docker.build_image(
                build_path=bot_dir,
                image_tag=image_tag,
                timeout=settings.docker_build_timeout,
            )
        except DockerServiceError as exc:
            raise DeploymentError(f"Rebuild failed: {exc}") from exc

        # ── Recreate and start ───────────────────────────────
        try:
            decrypted_token = decrypt_token(bot["encrypted_token"])
            await self._docker.create_container(
                image_tag=image_tag,
                container_name=container_name,
                bot_token=decrypted_token,
                ram_limit_mb=bot["ram_limit_mb"],
                cpu_limit=bot["cpu_limit"],
            )
            await self._docker.start_container(container_name)
            await db.update_bot_status(bot_id, "running")
        except DockerServiceError as exc:
            await db.update_bot_status(bot_id, "error")
            raise DeploymentError(f"Restart after replace failed: {exc}") from exc

        logger.info("Bot files replaced successfully", bot_id=str(bot_id))
        return {"bot_id": str(bot_id), "status": "running"}

    # ── Delete Bot ───────────────────────────────────────────

    async def delete_bot(self, bot_id: UUID) -> None:
        """Fully delete a bot: stop → remove container → remove image → delete files → delete DB.

        Args:
            bot_id: UUID of the bot to delete.

        Raises:
            DeploymentError: If the bot is not found.
        """
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")

        container_name = bot["container_name"]
        image_tag = f"hosted_bot:{container_name}"
        bot_dir = Path(bot["bot_path"])

        # Stop and remove container (ignore errors — may already be stopped)
        try:
            await self._docker.stop_container(container_name)
        except DockerServiceError:
            pass

        try:
            await self._docker.remove_container(container_name)
        except DockerServiceError:
            pass

        # Remove image
        await self._docker.remove_image(image_tag)

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
            await self._docker.start_container(bot["container_name"])
            await db.update_bot_status(bot_id, "running")
        except DockerServiceError as exc:
            raise DeploymentError(f"Failed to start bot: {exc}") from exc

    async def stop_bot(self, bot_id: UUID) -> None:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")
        try:
            await self._docker.stop_container(bot["container_name"])
            await db.update_bot_status(bot_id, "stopped")
        except DockerServiceError as exc:
            raise DeploymentError(f"Failed to stop bot: {exc}") from exc

    async def restart_bot(self, bot_id: UUID) -> None:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")
        try:
            await self._docker.restart_container(bot["container_name"])
            await db.update_bot_status(bot_id, "running")
        except DockerServiceError as exc:
            raise DeploymentError(f"Failed to restart bot: {exc}") from exc

    # ── Logs ─────────────────────────────────────────────────

    async def get_bot_logs(self, bot_id: UUID, tail: int = 100) -> str:
        bot = await db.get_bot(bot_id)
        if bot is None:
            raise DeploymentError("Bot not found")
        return await self._docker.get_logs(bot["container_name"], tail=tail)

    # ── File Editing ─────────────────────────────────────────

    async def read_bot_file(self, bot_id: UUID, filename: str) -> str:
        """Read a file from a bot's directory (with path validation).

        Args:
            bot_id: UUID of the bot.
            filename: Relative path within the bot directory.

        Returns:
            File content as string.

        Raises:
            DeploymentError: If file not found or path escapes directory.
        """
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
        """Write content to a file in a bot's directory, then restart the container.

        Args:
            bot_id: UUID of the bot.
            filename: Relative path within the bot directory.
            content: New file content.

        Raises:
            DeploymentError: If path validation fails or write fails.
        """
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

        # Rebuild and restart after file edit
        container_name = bot["container_name"]
        image_tag = f"hosted_bot:{container_name}"

        try:
            await self._docker.stop_container(container_name)
            await self._docker.remove_container(container_name)

            await self._docker.build_image(
                build_path=bot_dir,
                image_tag=image_tag,
                timeout=get_settings().docker_build_timeout,
            )

            decrypted_token = decrypt_token(bot["encrypted_token"])
            await self._docker.create_container(
                image_tag=image_tag,
                container_name=container_name,
                bot_token=decrypted_token,
                ram_limit_mb=bot["ram_limit_mb"],
                cpu_limit=bot["cpu_limit"],
            )
            await self._docker.start_container(container_name)
            await db.update_bot_status(bot_id, "running")
        except DockerServiceError as exc:
            await db.update_bot_status(bot_id, "error")
            raise DeploymentError(f"Restart after edit failed: {exc}") from exc

        logger.info("Bot file edited and restarted", bot_id=str(bot_id), file=filename)
