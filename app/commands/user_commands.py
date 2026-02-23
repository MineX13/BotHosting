"""
User-facing slash commands for MineNodes Bot Hoster.

Commands:
- /create-bot — Upload ZIP + token → deploy a new bot
- /list-bots  — Show all user's bots with status
- /start-bot  — Start a stopped bot
- /stop-bot   — Stop a running bot
- /restart-bot — Restart a bot
- /delete-bot — Permanently delete a bot
- /replace-files — Upload new ZIP → rebuild bot
- /edit-file  — Read/write a file in the bot directory
- /view-logs  — Show last 100 lines of bot logs
- /status     — Bot hoster status, ping, uptime
- /help       — Show all available commands

All commands use:
- Rate limiting (Redis-backed)
- Suspension check (blocked if suspended)
- Bot ownership verification
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from app.commands.middleware import not_suspended, verify_bot_ownership
from app.config.settings import get_settings
from app.database import queries as db
from app.services.deployment_service import DeploymentService, DeploymentError
from app.security.rate_limiter import rate_limit_check
from app.utils.helpers import chunk_text
from app.utils.logging import get_logger

logger = get_logger("commands.user")


class UserCommands(commands.Cog):
    """Slash commands available to all (non-suspended) users."""

    def __init__(self, bot: commands.Bot, deployment_service: DeploymentService) -> None:
        self.bot = bot
        self.deploy = deployment_service

    # ── /status ──────────────────────────────────────────────

    @app_commands.command(
        name="status",
        description="View MineNodes Bot Hoster status, ping, and uptime",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            # Ping
            latency_ms = round(self.bot.latency * 1000)

            # Uptime
            boot_time = getattr(self.bot, "boot_time", None)
            if boot_time:
                delta = datetime.now(timezone.utc) - boot_time
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
            else:
                uptime_str = "N/A"

            # Stats
            stats = await db.get_stats()
            guild_count = len(self.bot.guilds)

            embed = discord.Embed(
                title="🌐 MineNodes Bot Hoster",
                description="Cloud bot hosting powered by MineNodes",
                color=discord.Color.teal(),
            )
            embed.add_field(name="🏓 Ping", value=f"{latency_ms}ms", inline=True)
            embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)
            embed.add_field(name="🌍 Servers", value=str(guild_count), inline=True)
            embed.add_field(
                name="🤖 Bots Hosted",
                value=f"{stats['running_bots']} running / {stats['total_bots']} total",
                inline=True,
            )
            embed.add_field(
                name="👥 Users",
                value=str(stats["total_users"]),
                inline=True,
            )
            embed.add_field(
                name="📡 Status",
                value="🟢 Online" if latency_ms < 500 else "🟡 Degraded",
                inline=True,
            )
            embed.set_footer(text="MineNodes Bot Hoster • Reliable 24/7 Hosting")

            await interaction.followup.send(embed=embed)

        except Exception as exc:
            logger.error("Error in status", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to get status.", ephemeral=True
            )

    # ── /help ────────────────────────────────────────────────

    @app_commands.command(
        name="help",
        description="Show all available MineNodes Bot Hoster commands",
    )
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📖 MineNodes Bot Hoster — Commands",
            description="Host your Discord bots with ease! Here are all available commands:",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="🚀 Bot Management",
            value=(
                "`/create-bot` — Deploy a new bot from a ZIP file\n"
                "`/list-bots` — List all your hosted bots\n"
                "`/start-bot` — Start a stopped bot\n"
                "`/stop-bot` — Stop a running bot\n"
                "`/restart-bot` — Restart a bot\n"
                "`/delete-bot` — Permanently delete a bot"
            ),
            inline=False,
        )
        embed.add_field(
            name="📁 File Management",
            value=(
                "`/replace-files` — Upload new code to an existing bot\n"
                "`/edit-file` — View or edit a file in your bot's directory"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Information",
            value=(
                "`/view-logs` — View your bot's recent logs\n"
                "`/status` — Service status, ping, and uptime\n"
                "`/help` — Show this help message"
            ),
            inline=False,
        )

        # Show admin commands if the user is admin
        settings = get_settings()
        if interaction.user.id == settings.admin_user_id:
            embed.add_field(
                name="🔒 Admin Commands",
                value=(
                    "`/admin-users` — List all registered users\n"
                    "`/admin-user-bots` — View a user's bots\n"
                    "`/admin-set-limits` — Set user resource limits\n"
                    "`/admin-view-limits` — View user resource limits\n"
                    "`/admin-suspend-user` — Suspend a user\n"
                    "`/admin-unsuspend-user` — Unsuspend a user\n"
                    "`/admin-delete-bot` — Force-delete any bot\n"
                    "`/admin-stats` — System resource usage\n"
                    "`/admin-broadcast` — DM all users"
                ),
                inline=False,
            )

        embed.set_footer(text="MineNodes Bot Hoster • /status for service info")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /create-bot ──────────────────────────────────────────

    @app_commands.command(
        name="create-bot",
        description="Deploy a new Discord bot from a ZIP file",
    )
    @app_commands.describe(
        zip_file="ZIP file containing your bot code (max 50MB)",
        token="Your Discord bot token (encrypted before storage)",
        runtime="Runtime: python (default) or node",
        name="Display name for your bot",
    )
    @not_suspended()
    @rate_limit_check()
    async def create_bot(
        self,
        interaction: discord.Interaction,
        zip_file: discord.Attachment,
        token: str,
        runtime: str = "python",
        name: str | None = None,
    ) -> None:
        settings = get_settings()

        # Defer (ephemeral) — deployment takes time
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # Validate file size
            if zip_file.size > settings.max_zip_size_bytes:
                await interaction.followup.send(
                    f"❌ File too large. Maximum size: {settings.max_zip_size_mb}MB",
                    ephemeral=True,
                )
                return

            # Validate file type
            if not zip_file.filename.endswith(".zip"):
                await interaction.followup.send(
                    "❌ Please upload a `.zip` file.",
                    ephemeral=True,
                )
                return

            # Download ZIP
            zip_data = await zip_file.read()

            # Deploy
            result = await self.deploy.create_bot(
                user_id=interaction.user.id,
                zip_data=zip_data,
                token=token,
                runtime=runtime,
                name=name,
            )

            embed = discord.Embed(
                title="✅ Bot Deployed Successfully",
                color=discord.Color.green(),
            )
            embed.add_field(name="Bot ID", value=result["bot_id"], inline=False)
            embed.add_field(name="Name", value=result["name"], inline=True)
            embed.add_field(name="Runtime", value=result["runtime"], inline=True)
            embed.add_field(name="Status", value="🟢 Running", inline=True)
            embed.set_footer(text="Your bot token has been encrypted and stored securely.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except DeploymentError as exc:
            await interaction.followup.send(
                f"❌ Deployment failed: {exc}",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Unexpected error in create_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred. Please try again later.",
                ephemeral=True,
            )

    # ── /list-bots ───────────────────────────────────────────

    @app_commands.command(
        name="list-bots",
        description="List all your hosted bots",
    )
    @not_suspended()
    @rate_limit_check()
    async def list_bots(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            bots = await db.list_user_bots(interaction.user.id)

            if not bots:
                await interaction.followup.send(
                    "📭 You don't have any bots yet. Use `/create-bot` to deploy one!",
                    ephemeral=True,
                )
                return

            STATUS_ICONS = {
                "running": "🟢",
                "stopped": "🔴",
                "crashed": "💥",
                "building": "🔨",
                "error": "⚠️",
            }

            embed = discord.Embed(
                title=f"🤖 Your Bots ({len(bots)})",
                color=discord.Color.blue(),
            )

            for bot in bots:
                icon = STATUS_ICONS.get(bot["status"], "❓")
                created = bot["created_at"].strftime("%Y-%m-%d %H:%M")
                embed.add_field(
                    name=f"{icon} {bot['name']}",
                    value=(
                        f"**ID:** `{bot['id']}`\n"
                        f"**Status:** {bot['status']}\n"
                        f"**Runtime:** {bot['runtime']}\n"
                        f"**Created:** {created}"
                    ),
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("Error in list_bots", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to list bots. Please try again.",
                ephemeral=True,
            )

    # ── /start-bot ───────────────────────────────────────────

    @app_commands.command(
        name="start-bot",
        description="Start a stopped bot",
    )
    @app_commands.describe(bot_id="The UUID of the bot to start")
    @not_suspended()
    @rate_limit_check()
    async def start_bot(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.start_bot(uid)
            await interaction.followup.send(
                f"✅ Bot `{bot_id}` started successfully.", ephemeral=True
            )
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in start_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /stop-bot ────────────────────────────────────────────

    @app_commands.command(
        name="stop-bot",
        description="Stop a running bot",
    )
    @app_commands.describe(bot_id="The UUID of the bot to stop")
    @not_suspended()
    @rate_limit_check()
    async def stop_bot(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.stop_bot(uid)
            await interaction.followup.send(
                f"✅ Bot `{bot_id}` stopped.", ephemeral=True
            )
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in stop_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /restart-bot ─────────────────────────────────────────

    @app_commands.command(
        name="restart-bot",
        description="Restart a bot",
    )
    @app_commands.describe(bot_id="The UUID of the bot to restart")
    @not_suspended()
    @rate_limit_check()
    async def restart_bot(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.restart_bot(uid)
            await interaction.followup.send(
                f"✅ Bot `{bot_id}` restarted.", ephemeral=True
            )
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in restart_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /delete-bot ──────────────────────────────────────────

    @app_commands.command(
        name="delete-bot",
        description="Permanently delete a bot (cannot be undone!)",
    )
    @app_commands.describe(bot_id="The UUID of the bot to delete")
    @not_suspended()
    @rate_limit_check()
    async def delete_bot(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.delete_bot(uid)
            await interaction.followup.send(
                f"🗑️ Bot `{bot_id}` has been permanently deleted.",
                ephemeral=True,
            )
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in delete_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /replace-files ───────────────────────────────────────

    @app_commands.command(
        name="replace-files",
        description="Upload new code to replace an existing bot's files",
    )
    @app_commands.describe(
        bot_id="The UUID of the bot to update",
        zip_file="New ZIP file with updated code",
    )
    @not_suspended()
    @rate_limit_check()
    async def replace_files(
        self,
        interaction: discord.Interaction,
        bot_id: str,
        zip_file: discord.Attachment,
    ) -> None:
        settings = get_settings()
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            if zip_file.size > settings.max_zip_size_bytes:
                await interaction.followup.send(
                    f"❌ File too large. Maximum: {settings.max_zip_size_mb}MB",
                    ephemeral=True,
                )
                return

            if not zip_file.filename.endswith(".zip"):
                await interaction.followup.send(
                    "❌ Please upload a `.zip` file.", ephemeral=True
                )
                return

            zip_data = await zip_file.read()
            result = await self.deploy.replace_files(uid, zip_data)

            await interaction.followup.send(
                f"✅ Bot `{bot_id}` files replaced and restarted.\n"
                f"Status: {result['status']}",
                ephemeral=True,
            )

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in replace_files", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /edit-file ───────────────────────────────────────────

    @app_commands.command(
        name="edit-file",
        description="View or edit a file in your bot's directory",
    )
    @app_commands.describe(
        bot_id="The UUID of the bot",
        filename="File path relative to bot root (e.g. main.py)",
        content="New content (leave empty to view the file)",
    )
    @not_suspended()
    @rate_limit_check()
    async def edit_file(
        self,
        interaction: discord.Interaction,
        bot_id: str,
        filename: str,
        content: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            if content is None:
                # READ mode
                file_content = await self.deploy.read_bot_file(uid, filename)
                chunks = chunk_text(f"```\n{file_content}\n```")
                for chunk in chunks:
                    await interaction.followup.send(chunk, ephemeral=True)
            else:
                # WRITE mode
                await self.deploy.write_bot_file(uid, filename, content)
                await interaction.followup.send(
                    f"✅ File `{filename}` updated and bot restarted.",
                    ephemeral=True,
                )

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in edit_file", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /view-logs ───────────────────────────────────────────

    @app_commands.command(
        name="view-logs",
        description="View the last 100 lines of your bot's logs",
    )
    @app_commands.describe(bot_id="The UUID of the bot")
    @not_suspended()
    @rate_limit_check()
    async def view_logs(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            logs = await self.deploy.get_bot_logs(uid)

            if not logs.strip():
                await interaction.followup.send(
                    "📋 No logs available yet.", ephemeral=True
                )
                return

            chunks = chunk_text(f"```\n{logs}\n```")
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True)

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in view_logs", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── Error Handler ────────────────────────────────────────

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Global error handler for this cog's slash commands."""
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)
        else:
            logger.error("Unhandled command error", error=str(error), exc_info=True)
            msg = "❌ An unexpected error occurred. Please try again."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Called by bot.load_extension() — registers the cog."""
    # DeploymentService is injected via bot.deployment_service
    deployment_service = getattr(bot, "deployment_service", None)
    if deployment_service is None:
        raise RuntimeError("bot.deployment_service must be set before loading this cog")
    await bot.add_cog(UserCommands(bot, deployment_service))
