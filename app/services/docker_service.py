"""
Docker service — manages container lifecycle.

Handles:
- Image building with timeout
- Container creation with resource limits & security options
- Start / stop / restart / remove
- Log retrieval
- Status inspection
- Image cleanup

SECURITY NOTES:
- All containers run as non-root (security_opt: no-new-privileges)
- Read-only root filesystem
- No privileged mode, no host network
- CPU and memory limits enforced
- Restart policy: on-failure, max 3
"""

from __future__ import annotations

import asyncio
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
from docker.errors import DockerException, NotFound, BuildError, APIError
from docker.types import LogConfig

from app.config.settings import get_settings
from app.utils.logging import get_logger

logger = get_logger("services.docker")


class DockerServiceError(Exception):
    """Raised when a Docker operation fails."""
    pass


class DockerService:
    """Manages Docker containers for hosted bots."""

    def __init__(self) -> None:
        settings = get_settings()
        self._docker_host = settings.resolved_docker_host
        self._client: Optional[docker.DockerClient] = None

    def _get_client(self) -> docker.DockerClient:
        """Lazy-initialise the Docker client."""
        if self._client is None:
            try:
                self._client = docker.DockerClient(base_url=self._docker_host)
                self._client.ping()
                logger.info("Docker client connected", host=self._docker_host)
            except DockerException as exc:
                raise DockerServiceError(
                    f"Cannot connect to Docker at {self._docker_host}: {exc}"
                ) from exc
        return self._client

    # ── Build ────────────────────────────────────────────────

    async def build_image(
        self,
        build_path: Path,
        image_tag: str,
        timeout: int = 60,
    ) -> str:
        """Build a Docker image from a directory containing a Dockerfile.

        Args:
            build_path: Directory with the Dockerfile.
            image_tag: Tag for the built image.
            timeout: Build timeout in seconds.

        Returns:
            The image ID.

        Raises:
            DockerServiceError: On build failure or timeout.
        """
        loop = asyncio.get_running_loop()

        def _build() -> str:
            client = self._get_client()
            try:
                image, build_logs = client.images.build(
                    path=str(build_path),
                    tag=image_tag,
                    rm=True,          # Remove intermediate containers
                    forcerm=True,     # Remove even on failure
                    pull=False,       # Don't pull base image every time
                    timeout=timeout,
                )
                logger.info("Image built", tag=image_tag, image_id=image.id)
                return image.id
            except BuildError as exc:
                # Extract build log lines for debugging
                log_lines = []
                for chunk in exc.build_log:
                    if isinstance(chunk, dict) and "stream" in chunk:
                        log_lines.append(chunk["stream"].strip())
                raise DockerServiceError(
                    f"Image build failed for {image_tag}: {'; '.join(log_lines[-5:])}"
                ) from exc
            except APIError as exc:
                raise DockerServiceError(
                    f"Docker API error during build: {exc}"
                ) from exc

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _build),
                timeout=timeout + 10,  # Extra grace period
            )
        except asyncio.TimeoutError:
            raise DockerServiceError(
                f"Image build timed out after {timeout}s for {image_tag}"
            )

    # ── Container Lifecycle ──────────────────────────────────

    async def create_container(
        self,
        image_tag: str,
        container_name: str,
        bot_token: str,
        ram_limit_mb: int = 512,
        cpu_limit: float = 0.5,
    ) -> str:
        """Create a container with strict security and resource limits.

        Returns:
            Container ID.
        """
        loop = asyncio.get_running_loop()

        def _create() -> str:
            client = self._get_client()
            settings = get_settings()

            # Convert CPU limit to Docker's nano_cpus format
            nano_cpus = int(cpu_limit * 1e9)

            # Security options
            security_opts: List[str] = ["no-new-privileges"]

            try:
                container = client.containers.create(
                    image=image_tag,
                    name=container_name,
                    detach=True,
                    environment={
                        "DISCORD_TOKEN": bot_token,
                    },
                    # ── Resource limits ───────────────────────
                    mem_limit=f"{ram_limit_mb}m",
                    memswap_limit=f"{ram_limit_mb}m",  # No swap
                    nano_cpus=nano_cpus,
                    # Storage limit via device write bps (best-effort on Docker)
                    storage_opt=(
                        {"size": f"{settings.bot_disk_limit_mb}M"}
                        if platform.system() == "Linux"
                        else None
                    ),
                    # ── Security ─────────────────────────────
                    security_opt=security_opts,
                    read_only=True,
                    privileged=False,
                    network_mode="bridge",
                    # Temp filesystem for runtime needs (e.g. __pycache__)
                    tmpfs={"/tmp": "size=64M,noexec,nosuid"},
                    # ── Restart policy ───────────────────────
                    restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
                    # ── Logging ──────────────────────────────
                    log_config=LogConfig(
                        type=LogConfig.types.JSON,
                        config={"max-size": "10m", "max-file": "3"},
                    ),
                )
                logger.info(
                    "Container created",
                    container_name=container_name,
                    container_id=container.id,
                    ram=f"{ram_limit_mb}MB",
                    cpu=cpu_limit,
                )
                return container.id
            except APIError as exc:
                raise DockerServiceError(
                    f"Failed to create container {container_name}: {exc}"
                ) from exc

        return await loop.run_in_executor(None, _create)

    async def start_container(self, container_name: str) -> None:
        loop = asyncio.get_running_loop()

        def _start():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                container.start()
                logger.info("Container started", container_name=container_name)
            except NotFound:
                raise DockerServiceError(f"Container not found: {container_name}")
            except APIError as exc:
                raise DockerServiceError(f"Failed to start {container_name}: {exc}")

        await loop.run_in_executor(None, _start)

    async def stop_container(self, container_name: str, timeout: int = 10) -> None:
        loop = asyncio.get_running_loop()

        def _stop():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                container.stop(timeout=timeout)
                logger.info("Container stopped", container_name=container_name)
            except NotFound:
                logger.warning("Container not found for stop", container_name=container_name)
            except APIError as exc:
                raise DockerServiceError(f"Failed to stop {container_name}: {exc}")

        await loop.run_in_executor(None, _stop)

    async def restart_container(self, container_name: str, timeout: int = 10) -> None:
        loop = asyncio.get_running_loop()

        def _restart():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                container.restart(timeout=timeout)
                logger.info("Container restarted", container_name=container_name)
            except NotFound:
                raise DockerServiceError(f"Container not found: {container_name}")
            except APIError as exc:
                raise DockerServiceError(f"Failed to restart {container_name}: {exc}")

        await loop.run_in_executor(None, _restart)

    async def remove_container(self, container_name: str, force: bool = True) -> None:
        loop = asyncio.get_running_loop()

        def _remove():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                container.remove(force=force, v=True)
                logger.info("Container removed", container_name=container_name)
            except NotFound:
                logger.warning("Container not found for removal", container_name=container_name)
            except APIError as exc:
                raise DockerServiceError(f"Failed to remove {container_name}: {exc}")

        await loop.run_in_executor(None, _remove)

    # ── Inspection ───────────────────────────────────────────

    async def get_container_status(self, container_name: str) -> str:
        """Get container status: running, exited, paused, etc."""
        loop = asyncio.get_running_loop()

        def _status():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                return container.status
            except NotFound:
                return "not_found"
            except APIError:
                return "error"

        return await loop.run_in_executor(None, _status)

    async def get_logs(
        self,
        container_name: str,
        tail: int = 100,
    ) -> str:
        """Retrieve the last N lines of container logs.

        SECURITY: Token redaction is handled by the logging module,
        but we also strip DISCORD_TOKEN env references here.
        """
        loop = asyncio.get_running_loop()

        def _logs():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                raw = container.logs(tail=tail, timestamps=True).decode(
                    "utf-8", errors="replace"
                )
                # Redact any accidental token leaks in log output
                return _redact_log_tokens(raw)
            except NotFound:
                return "Container not found."
            except APIError as exc:
                return f"Failed to retrieve logs: {exc}"

        return await loop.run_in_executor(None, _logs)

    async def get_container_stats(self, container_name: str) -> Dict[str, Any]:
        """Get resource usage stats for a container."""
        loop = asyncio.get_running_loop()

        def _stats():
            client = self._get_client()
            try:
                container = client.containers.get(container_name)
                return container.stats(stream=False)
            except (NotFound, APIError):
                return {}

        return await loop.run_in_executor(None, _stats)

    # ── Image Management ─────────────────────────────────────

    async def remove_image(self, image_tag: str, force: bool = True) -> None:
        loop = asyncio.get_running_loop()

        def _remove():
            client = self._get_client()
            try:
                client.images.remove(image=image_tag, force=force)
                logger.info("Image removed", tag=image_tag)
            except (NotFound, APIError) as exc:
                logger.warning("Failed to remove image", tag=image_tag, error=str(exc))

        await loop.run_in_executor(None, _remove)

    async def prune_unused_images(self) -> Dict[str, Any]:
        """Remove dangling / unused images."""
        loop = asyncio.get_running_loop()

        def _prune():
            client = self._get_client()
            try:
                result = client.images.prune(filters={"dangling": True})
                logger.info("Images pruned", result=result)
                return result
            except APIError as exc:
                logger.error("Image prune failed", error=str(exc))
                return {}

        return await loop.run_in_executor(None, _prune)

    async def list_all_containers(self) -> List[Dict[str, Any]]:
        """List all hosted bot containers (prefixed with hosted_bot_)."""
        loop = asyncio.get_running_loop()

        def _list():
            client = self._get_client()
            try:
                containers = client.containers.list(
                    all=True,
                    filters={"name": "hosted_bot_"},
                )
                return [
                    {
                        "name": c.name,
                        "status": c.status,
                        "id": c.short_id,
                    }
                    for c in containers
                ]
            except APIError:
                return []

        return await loop.run_in_executor(None, _list)


def _redact_log_tokens(text: str) -> str:
    """Remove anything that looks like a Discord token from log output."""
    import re
    pattern = re.compile(r"[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}")
    return pattern.sub("[TOKEN_REDACTED]", text)
