"""
Process service — manages bot lifecycle as native subprocesses.

Replaces DockerService for environments where Docker is unavailable.
Each bot runs as an asyncio subprocess with:
- stdout/stderr captured to log files
- PID tracking for lifecycle management
- psutil for resource monitoring
- SIGTERM/SIGKILL for graceful shutdown
"""

from __future__ import annotations

import asyncio
import os
import signal
import platform
from pathlib import Path
from typing import Dict, Optional

import psutil

from app.config.settings import get_settings
from app.utils.logging import get_logger

logger = get_logger("services.process")


class ProcessServiceError(Exception):
    """Raised when a process operation fails."""
    pass


class ProcessService:
    """Manages bot processes as native subprocesses."""

    def __init__(self) -> None:
        # Map container_name → process info
        self._processes: Dict[str, dict] = {}
        self._settings = get_settings()

    # ── Install Dependencies ─────────────────────────────────

    async def install_deps(self, bot_dir: Path, runtime: str) -> None:
        """Install bot dependencies before starting.

        For Python: pip install -r requirements.txt
        For Node: npm install
        """
        if runtime == "python":
            req_file = bot_dir / "requirements.txt"
            if req_file.exists():
                logger.info("Installing Python dependencies", path=str(bot_dir))
                proc = await asyncio.create_subprocess_exec(
                    "pip3", "install", "-r", str(req_file),
                    "--target", str(bot_dir / ".deps"),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(bot_dir),
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=120
                )
                if proc.returncode != 0:
                    raise ProcessServiceError(
                        f"pip install failed: {stderr.decode()[-500:]}"
                    )
                logger.info("Python dependencies installed")

        elif runtime == "node":
            pkg_file = bot_dir / "package.json"
            if pkg_file.exists():
                logger.info("Installing Node dependencies", path=str(bot_dir))
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install", "--production",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(bot_dir),
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=120
                )
                if proc.returncode != 0:
                    raise ProcessServiceError(
                        f"npm install failed: {stderr.decode()[-500:]}"
                    )
                logger.info("Node dependencies installed")

    # ── Detect Entrypoint ────────────────────────────────────

    def _detect_entrypoint(self, bot_dir: Path, runtime: str) -> str:
        """Auto-detect the bot's main file."""
        if runtime == "python":
            for name in ("bot.py", "main.py", "app.py", "index.py", "run.py"):
                if (bot_dir / name).exists():
                    return name
            # Fallback: first .py file
            py_files = list(bot_dir.glob("*.py"))
            if py_files:
                return py_files[0].name
            raise ProcessServiceError(
                "No Python entrypoint found. "
                "Name your main file bot.py, main.py, or app.py."
            )

        elif runtime == "node":
            for name in ("index.js", "bot.js", "main.js", "app.js"):
                if (bot_dir / name).exists():
                    return name
            js_files = list(bot_dir.glob("*.js"))
            if js_files:
                return js_files[0].name
            raise ProcessServiceError(
                "No Node entrypoint found. "
                "Name your main file index.js, bot.js, or main.js."
            )

        raise ProcessServiceError(f"Unsupported runtime: {runtime}")

    # ── Start Process ────────────────────────────────────────

    async def start_process(
        self,
        container_name: str,
        bot_dir: Path,
        bot_token: str,
        runtime: str,
        cpu_limit: float = 0.5,
        disk_limit_mb: int = 1024,
    ) -> int:
        """Start a bot as a subprocess.

        Args:
            container_name: Unique identifier for this bot process.
            bot_dir: Directory containing the bot files.
            bot_token: Discord bot token passed as env var.
            runtime: 'python' or 'node'.

        Returns:
            PID of the started process.
        """
        if container_name in self._processes:
            info = self._processes[container_name]
            if self._is_pid_alive(info["pid"]):
                raise ProcessServiceError(
                    f"Bot {container_name} is already running (PID {info['pid']})"
                )

        # Detect entrypoint
        entrypoint = self._detect_entrypoint(bot_dir, runtime)

        # Set up log files
        log_dir = bot_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        stdout_log = open(log_dir / "stdout.log", "a", encoding="utf-8")
        stderr_log = open(log_dir / "stderr.log", "a", encoding="utf-8")

        # Build command with OS-level sandboxing (Linux only)
        if platform.system() != "Windows":
            runner_script = bot_dir / "runner.sh"
            
            # CPU limit in %: cpu_limit * 100
            cpu_pct = int(cpu_limit * 100)
            if cpu_pct <= 0: cpu_pct = 50
            
            target_cmd = f"python3 {entrypoint}" if runtime == "python" else f"node {entrypoint}"
            
            # ulimit -f sets max file size in 512-byte blocks
            disk_blocks = disk_limit_mb * 2048
            
            script_content = f"#!/bin/bash\nulimit -f {disk_blocks}\nexec cpulimit -l {cpu_pct} -- {target_cmd}\n"
            runner_script.write_text(script_content, encoding="utf-8")
            os.chmod(runner_script, 0o755)
            
            cmd = ["bash", "runner.sh"]
        else:
            if runtime == "python":
                cmd = ["python3", entrypoint]
            else:
                cmd = ["node", entrypoint]

        # Environment: inherit + set token under all common env var names
        env = os.environ.copy()
        env["BOT_TOKEN"] = bot_token
        env["TOKEN"] = bot_token
        env["DISCORD_TOKEN"] = bot_token
        env["DISCORD_BOT_TOKEN"] = bot_token

        if runtime == "python":
            deps_dir = bot_dir / ".deps"
            if deps_dir.exists():
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = f"{deps_dir}{os.pathsep}{existing}" if existing else str(deps_dir)

        # Spawn process
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=stdout_log,
                stderr=stderr_log,
                cwd=str(bot_dir),
                env=env,
            )
        except Exception as exc:
            stdout_log.close()
            stderr_log.close()
            raise ProcessServiceError(f"Failed to start process: {exc}") from exc

        pid = process.pid

        # Save PID file for crash recovery
        pid_file = bot_dir / ".pid"
        pid_file.write_text(str(pid), encoding="utf-8")

        # Track in memory
        self._processes[container_name] = {
            "pid": pid,
            "process": process,
            "bot_dir": bot_dir,
            "runtime": runtime,
            "stdout_log": stdout_log,
            "stderr_log": stderr_log,
        }

        logger.info(
            "Bot process started",
            name=container_name,
            pid=pid,
            entrypoint=entrypoint,
        )

        return pid

    # ── Stop Process ─────────────────────────────────────────

    async def stop_process(self, container_name: str, timeout: int = 10) -> None:
        """Stop a bot process gracefully.

        Sends SIGTERM, waits `timeout` seconds, then SIGKILL.
        """
        info = self._processes.get(container_name)

        if info is None:
            logger.warning("No tracked process for bot", name=container_name)
            return

        pid = info["pid"]
        process: asyncio.subprocess.Process = info["process"]

        if not self._is_pid_alive(pid):
            self._cleanup_process(container_name)
            return

        # Send SIGTERM (or taskkill on Windows)
        try:
            if platform.system() == "Windows":
                process.terminate()
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            self._cleanup_process(container_name)
            return

        # Wait for graceful exit
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Force kill
            try:
                if platform.system() == "Windows":
                    process.kill()
                else:
                    os.kill(pid, signal.SIGKILL)
                await process.wait()
            except (ProcessLookupError, OSError):
                pass

        self._cleanup_process(container_name)
        logger.info("Bot process stopped", name=container_name, pid=pid)

    # ── Restart Process ──────────────────────────────────────

    async def restart_process(
        self,
        container_name: str,
        bot_dir: Path,
        bot_token: str,
        runtime: str,
        cpu_limit: float = 0.5,
        disk_limit_mb: int = 1024,
    ) -> int:
        """Restart a bot process (stop + start)."""
        await self.stop_process(container_name)
        return await self.start_process(
            container_name, bot_dir, bot_token, runtime, cpu_limit, disk_limit_mb
        )

    # ── Get Status ───────────────────────────────────────────

    def get_status(self, container_name: str) -> str:
        """Get the status of a bot process.

        Returns: 'running', 'exited', or 'not_found'.
        """
        info = self._processes.get(container_name)

        if info is None:
            return "not_found"

        if self._is_pid_alive(info["pid"]):
            return "running"

        return "exited"

    # ── Get Logs ─────────────────────────────────────────────

    async def get_logs(self, container_name: str, tail: int = 100) -> str:
        """Get the last N lines of a bot's combined logs."""
        info = self._processes.get(container_name)

        if info is None:
            # Try to read from disk if process isn't tracked
            # (e.g., after restart of the controller)
            all_bots_dir = self._settings.base_path
            # Search for log files across all user directories
            return "No logs available — bot process not found."

        bot_dir: Path = info["bot_dir"]
        log_file = bot_dir / "logs" / "stdout.log"
        err_file = bot_dir / "logs" / "stderr.log"

        lines = []

        for f in (log_file, err_file):
            if f.exists():
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    lines.extend(content.splitlines())
                except Exception:
                    pass

        if not lines:
            return "No logs available yet."

        # Return last N lines
        return "\n".join(lines[-tail:])

    async def get_logs_by_path(self, bot_dir: Path, tail: int = 100) -> str:
        """Get logs directly from a bot directory path."""
        log_file = bot_dir / "logs" / "stdout.log"
        err_file = bot_dir / "logs" / "stderr.log"

        lines = []
        for f in (log_file, err_file):
            if f.exists():
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    lines.extend(content.splitlines())
                except Exception:
                    pass

        if not lines:
            return "No logs available yet."

        return "\n".join(lines[-tail:])

    # ── Get Process Stats ────────────────────────────────────

    def get_process_stats(self, container_name: str) -> Optional[dict]:
        """Get CPU/memory stats for a bot process using psutil."""
        info = self._processes.get(container_name)
        if info is None:
            return None

        pid = info["pid"]
        try:
            proc = psutil.Process(pid)
            mem_info = proc.memory_info()
            return {
                "pid": pid,
                "cpu_percent": proc.cpu_percent(interval=0.1),
                "memory_mb": mem_info.rss / (1024 * 1024),
                "status": proc.status(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    # ── List All Processes ───────────────────────────────────

    def list_all_processes(self) -> list:
        """List all tracked bot processes with their status."""
        result = []
        for name, info in self._processes.items():
            alive = self._is_pid_alive(info["pid"])
            result.append({
                "container_name": name,
                "pid": info["pid"],
                "status": "running" if alive else "exited",
                "runtime": info["runtime"],
            })
        return result

    # ── Recovery ─────────────────────────────────────────────

    async def recover_processes(self) -> int:
        """Recover tracking of bot processes after controller restart.

        Scans bot directories for .pid files and checks if processes
        are still alive.

        Returns:
            Number of recovered processes.
        """
        recovered = 0
        base = self._settings.base_path

        if not base.exists():
            return 0

        for user_dir in base.iterdir():
            if not user_dir.is_dir():
                continue
            for bot_dir in user_dir.iterdir():
                if not bot_dir.is_dir():
                    continue

                pid_file = bot_dir / ".pid"
                if not pid_file.exists():
                    continue

                try:
                    pid = int(pid_file.read_text().strip())
                    if self._is_pid_alive(pid):
                        # Reconstruct container name from directory
                        container_name = f"hosted_bot_{user_dir.name}_{bot_dir.name}"

                        # Detect runtime
                        runtime = "python"
                        if any(bot_dir.glob("*.js")) and not any(bot_dir.glob("*.py")):
                            runtime = "node"

                        self._processes[container_name] = {
                            "pid": pid,
                            "process": None,  # Can't recover asyncio.Process
                            "bot_dir": bot_dir,
                            "runtime": runtime,
                            "stdout_log": None,
                            "stderr_log": None,
                        }
                        recovered += 1
                        logger.info(
                            "Recovered bot process",
                            name=container_name,
                            pid=pid,
                        )
                    else:
                        # Stale PID file — remove it
                        pid_file.unlink(missing_ok=True)

                except (ValueError, OSError) as exc:
                    logger.warning("Failed to recover process", dir=str(bot_dir), error=str(exc))

        return recovered

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Check if a process with the given PID is still running."""
        if pid <= 0:
            return False
        try:
            if platform.system() == "Windows":
                proc = psutil.Process(pid)
                return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            else:
                os.kill(pid, 0)
                return True
        except (OSError, psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _cleanup_process(self, container_name: str) -> None:
        """Clean up tracked process resources."""
        info = self._processes.pop(container_name, None)
        if info is None:
            return

        # Close log file handles
        for key in ("stdout_log", "stderr_log"):
            fh = info.get(key)
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass

        # Remove PID file
        bot_dir: Path = info.get("bot_dir")
        if bot_dir:
            pid_file = bot_dir / ".pid"
            pid_file.unlink(missing_ok=True)
