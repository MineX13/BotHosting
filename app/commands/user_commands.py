"""
User-facing slash commands for MineNodes Bot Hoster.
"""

from __future__ import annotations

import asyncio
import traceback
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


# ═══════════════════════════════════════════════════════════════
# Management Panel — Buttons
# ═══════════════════════════════════════════════════════════════


class BotManagementView(discord.ui.View):
    """Interactive control panel for a hosted bot."""

    def __init__(self, bot_id: str, deploy: DeploymentService) -> None:
        super().__init__(timeout=None)
        self.bot_id = bot_id
        self.deploy = deploy

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.start_bot(uid)
            await interaction.followup.send("Bot started.", ephemeral=True)
            await self._refresh(interaction)
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.secondary, emoji="⏸️", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.stop_bot(uid)
            await interaction.followup.send("Bot stopped.", ephemeral=True)
            await self._refresh(interaction)
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.restart_bot(uid)
            await interaction.followup.send("Bot restarted.", ephemeral=True)
            await self._refresh(interaction)
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def logs_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            logs = await self.deploy.get_bot_logs(uid)
            if not logs.strip():
                await interaction.followup.send("No logs yet.", ephemeral=True)
                return
            chunks = chunk_text(f"```\n{logs}\n```")
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            process_svc = self.deploy._process
            bot_record = await db.get_bot(uid)
            if not bot_record:
                await interaction.followup.send("Bot not found.", ephemeral=True)
                return
            stats = process_svc.get_process_stats(bot_record["container_name"])
            if stats:
                embed = discord.Embed(title="Live Usage", color=0x2b2d31)
                embed.add_field(name="CPU", value=f"{stats.get('cpu_percent', 0):.1f}%", inline=True)
                embed.add_field(name="Memory", value=f"{stats.get('memory_mb', 0):.1f} MB", inline=True)
                embed.add_field(name="PID", value=str(stats.get('pid', 'N/A')), inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send("Bot isn't running, no stats available.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            confirm_view = ConfirmDeleteView(self.bot_id, self.deploy)
            await interaction.followup.send(
                "This will **permanently delete** the bot and all its files. Are you sure?",
                view=confirm_view,
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        try:
            uid = UUID(self.bot_id)
            bot_record = await db.get_bot(uid)
            if bot_record:
                embed = build_management_embed(bot_record)
                view = BotManagementView(self.bot_id, self.deploy)
                await interaction.message.edit(embed=embed, view=view)
        except Exception:
            pass


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, bot_id: str, deploy: DeploymentService) -> None:
        super().__init__(timeout=30)
        self.bot_id = bot_id
        self.deploy = deploy

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await self.deploy.delete_bot(uid)
            await interaction.followup.send(f"Bot `{self.bot_id}` deleted.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)


def build_management_embed(bot_record) -> discord.Embed:
    """Build the management panel embed."""
    STATUS_MAP = {
        "running": ("RUNNING", 0x57f287),
        "stopped": ("STOPPED", 0xed4245),
        "crashed": ("CRASHED", 0xa12d33),
        "building": ("BUILDING", 0xfaa61a),
        "error": ("ERROR", 0xe67e22),
    }

    status_text, color = STATUS_MAP.get(
        bot_record["status"], ("UNKNOWN", 0x99aab5)
    )

    embed = discord.Embed(
        title=f"MineNodes — Bot Management",
        description=f"Managing bot: **{bot_record['name']}**",
        color=color,
    )

    embed.add_field(
        name="▸ Allocated Resources",
        value=(
            f"**Status:** {status_text}\n"
            f"**Runtime:** {bot_record['runtime'].capitalize()}\n"
            f"**RAM:** {bot_record['ram_limit_mb']} MB\n"
            f"**CPU:** {bot_record['cpu_limit']} cores\n"
            f"**Bot ID:** `{bot_record['id']}`"
        ),
        inline=False,
    )

    created = bot_record["created_at"].strftime("%Y-%m-%d %H:%M")
    updated = bot_record["updated_at"].strftime("%Y-%m-%d %H:%M")
    embed.add_field(
        name="▸ Timing",
        value=f"**Created:** {created}\n**Last Updated:** {updated}",
        inline=False,
    )

    embed.add_field(
        name="▸ Controls",
        value="Use the buttons below to manage your bot",
        inline=False,
    )

    embed.set_footer(
        text=f"MineNodes Bot Hoster • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    return embed


# ═══════════════════════════════════════════════════════════════
# Create Bot — Modal for token input
# ═══════════════════════════════════════════════════════════════


class CreateBotModal(discord.ui.Modal, title="Create New Bot"):

    token = discord.ui.TextInput(
        label="Bot Token",
        placeholder="Your Discord bot token",
        style=discord.TextStyle.short,
        required=True,
    )

    runtime = discord.ui.TextInput(
        label="Runtime",
        placeholder="python or node (default: python)",
        style=discord.TextStyle.short,
        required=False,
        default="python",
        max_length=10,
    )

    bot_name = discord.ui.TextInput(
        label="Bot Name",
        placeholder="Display name (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=128,
    )

    def __init__(self, deploy: DeploymentService, zip_data: bytes, user_id: int):
        super().__init__()
        self.deploy = deploy
        self.zip_data = zip_data
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        runtime = self.runtime.value.strip().lower() or "python"
        name = self.bot_name.value.strip() or None
        token_val = self.token.value.strip()

        try:
            result = await self.deploy.create_bot(
                user_id=self.user_id,
                zip_data=self.zip_data,
                token=token_val,
                runtime=runtime,
                name=name,
            )

            bot_id = result["bot_id"]
            bot_record = await db.get_bot(UUID(str(bot_id)))

            if bot_record:
                embed = build_management_embed(bot_record)
                view = BotManagementView(str(bot_id), self.deploy)
                await interaction.followup.send(
                    "Bot deployed successfully. Your token has been encrypted.",
                    embed=embed,
                    view=view,
                )
            else:
                embed = discord.Embed(
                    title="Bot Deployed",
                    color=0x57f287,
                )
                embed.add_field(name="Bot ID", value=str(bot_id), inline=False)
                embed.add_field(name="Name", value=result["name"], inline=True)
                embed.add_field(name="Runtime", value=result["runtime"], inline=True)
                embed.set_footer(text="Use /manage-bot to control your bot")
                await interaction.followup.send(embed=embed)

        except DeploymentError as exc:
            await interaction.followup.send(f"Deployment failed: {exc}")
        except Exception as exc:
            logger.error(
                "Error in create_bot modal",
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            await interaction.followup.send(
                f"Something went wrong: `{exc}`\nPlease try again or contact an admin."
            )


class CreateBotButton(discord.ui.View):
    """Button that opens the token input modal."""

    def __init__(self, deploy: DeploymentService, zip_data: bytes, user_id: int):
        super().__init__(timeout=300)
        self.deploy = deploy
        self.zip_data = zip_data
        self.user_id = user_id

    @discord.ui.button(label="Enter Bot Token", style=discord.ButtonStyle.success, emoji="🔑")
    async def enter_token(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateBotModal(self.deploy, self.zip_data, self.user_id)
        await interaction.response.send_modal(modal)


# ═══════════════════════════════════════════════════════════════
# Main Cog
# ═══════════════════════════════════════════════════════════════


class UserCommands(commands.Cog):
    """Slash commands available to all users."""

    def __init__(self, bot: commands.Bot, deployment_service: DeploymentService) -> None:
        self.bot = bot
        self.deploy = deployment_service

    # ── /status ──────────────────────────────────────────────

    @app_commands.command(
        name="status",
        description="Check service status and uptime",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            latency_ms = round(self.bot.latency * 1000)

            boot_time = getattr(self.bot, "boot_time", None)
            if boot_time:
                delta = datetime.now(timezone.utc) - boot_time
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
            else:
                uptime_str = "N/A"

            stats = await db.get_stats()

            embed = discord.Embed(
                title="MineNodes Bot Hoster",
                color=0x2b2d31,
            )
            embed.add_field(name="Ping", value=f"{latency_ms}ms", inline=True)
            embed.add_field(name="Uptime", value=uptime_str, inline=True)
            embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
            embed.add_field(
                name="Bots Hosted",
                value=f"{stats['running_bots']} running / {stats['total_bots']} total",
                inline=True,
            )
            embed.add_field(name="Users", value=str(stats["total_users"]), inline=True)
            embed.add_field(
                name="Status",
                value="Online" if latency_ms < 500 else "Degraded",
                inline=True,
            )
            embed.set_footer(text="MineNodes Bot Hoster")
            await interaction.followup.send(embed=embed)

        except Exception as exc:
            logger.error("Error in status", error=str(exc), exc_info=True)
            await interaction.followup.send("Couldn't load status right now.", ephemeral=True)

    # ── /help ────────────────────────────────────────────────

    @app_commands.command(
        name="help",
        description="Show available commands",
    )
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="MineNodes Bot Hoster — Commands",
            description="Everything you need to host and manage your Discord bots.",
            color=0x2b2d31,
        )
        embed.add_field(
            name="Bot Management",
            value=(
                "`/create-bot` — Deploy a new bot\n"
                "`/manage-bot` — Open control panel\n"
                "`/list-bots` — See your bots\n"
                "`/start-bot` / `/stop-bot` / `/restart-bot`\n"
                "`/delete-bot` — Remove a bot permanently"
            ),
            inline=False,
        )
        embed.add_field(
            name="Files & Logs",
            value=(
                "`/replace-files` — Update bot code\n"
                "`/edit-file` — View or edit a file\n"
                "`/view-logs` — Recent bot output"
            ),
            inline=False,
        )
        embed.add_field(
            name="Info",
            value="`/status` — Service info\n`/help` — This message",
            inline=False,
        )

        settings = get_settings()
        if interaction.user.id == settings.admin_user_id:
            embed.add_field(
                name="Admin",
                value=(
                    "`/admin-users` · `/admin-user-bots`\n"
                    "`/admin-set-limits` · `/admin-view-limits`\n"
                    "`/admin-suspend-user` · `/admin-unsuspend-user`\n"
                    "`/admin-delete-bot` · `/admin-stats`\n"
                    "`/admin-broadcast`"
                ),
                inline=False,
            )

        embed.set_footer(text="MineNodes Bot Hoster")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /create-bot ──────────────────────────────────────────

    @app_commands.command(
        name="create-bot",
        description="Deploy a new bot from a ZIP file",
    )
    @app_commands.describe(zip_file="ZIP file with your bot code (max 50MB)")
    @not_suspended()
    @rate_limit_check()
    async def create_bot(
        self,
        interaction: discord.Interaction,
        zip_file: discord.Attachment,
    ) -> None:
        settings = get_settings()
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            if zip_file.size > settings.max_zip_size_bytes:
                await interaction.followup.send(
                    f"File too large — max {settings.max_zip_size_mb}MB.", ephemeral=True
                )
                return

            if not zip_file.filename.endswith(".zip"):
                await interaction.followup.send(
                    "Please upload a `.zip` file.", ephemeral=True
                )
                return

            zip_data = await zip_file.read()

            # Try to DM the user for private token input
            try:
                dm_channel = await interaction.user.create_dm()

                dm_embed = discord.Embed(
                    title="MineNodes — Secure Bot Setup",
                    description=(
                        f"Got your code (`{zip_file.filename}`, "
                        f"{zip_file.size / 1024:.0f} KB). "
                        "Click below to enter your bot token.\n\n"
                        "Your token is encrypted before storage and "
                        "is never visible to anyone."
                    ),
                    color=0x57f287,
                )
                dm_embed.set_footer(text="Token is encrypted with AES-256-GCM")

                view = CreateBotButton(self.deploy, zip_data, interaction.user.id)
                await dm_channel.send(embed=dm_embed, view=view)

                await interaction.followup.send(
                    "Check your DMs — I sent you a setup message for your bot token.",
                    ephemeral=True,
                )

            except discord.Forbidden:
                # Can't DM — ask user to DM the bot first, then retry
                await interaction.followup.send(
                    "I can't message you because your DMs are closed.\n\n"
                    "**To fix this:** Send me any message first (just type `hi` in my DMs), "
                    "then run `/create-bot` again.\n\n"
                    "Or you can open **User Settings → Privacy & Safety** and "
                    "enable \"Allow direct messages from server members\".",
                    ephemeral=True,
                )

        except Exception as exc:
            logger.error("Error in create_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "Something went wrong. Please try again.", ephemeral=True
            )

    # ── /manage-bot ──────────────────────────────────────────

    @app_commands.command(
        name="manage-bot",
        description="Open the control panel for a bot",
    )
    @app_commands.describe(bot_id="Bot ID (from /list-bots)")
    @not_suspended()
    @rate_limit_check()
    async def manage_bot(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            bot_record = await db.get_bot(uid)
            if not bot_record:
                await interaction.followup.send("Bot not found.", ephemeral=True)
                return

            embed = build_management_embed(bot_record)
            view = BotManagementView(bot_id, self.deploy)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in manage_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /list-bots ───────────────────────────────────────────

    @app_commands.command(name="list-bots", description="List your hosted bots")
    @not_suspended()
    @rate_limit_check()
    async def list_bots(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            bots = await db.list_user_bots(interaction.user.id)

            if not bots:
                await interaction.followup.send(
                    "You don't have any bots yet. Use `/create-bot` to get started.",
                    ephemeral=True,
                )
                return

            STATUS_ICONS = {
                "running": "🟢", "stopped": "🔴", "crashed": "💥",
                "building": "🔨", "error": "⚠️",
            }

            embed = discord.Embed(title=f"Your Bots ({len(bots)})", color=0x2b2d31)

            for bot in bots:
                icon = STATUS_ICONS.get(bot["status"], "❓")
                created = bot["created_at"].strftime("%Y-%m-%d %H:%M")
                embed.add_field(
                    name=f"{icon} {bot['name']}",
                    value=(
                        f"ID: `{bot['id']}`\n"
                        f"Status: {bot['status']} · Runtime: {bot['runtime']}\n"
                        f"Created: {created}"
                    ),
                    inline=False,
                )

            embed.set_footer(text="Use /manage-bot <id> to control a bot")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("Error in list_bots", error=str(exc), exc_info=True)
            await interaction.followup.send("Couldn't load bots.", ephemeral=True)

    # ── /start-bot ───────────────────────────────────────────

    @app_commands.command(name="start-bot", description="Start a stopped bot")
    @app_commands.describe(bot_id="Bot ID")
    @not_suspended()
    @rate_limit_check()
    async def start_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.start_bot(uid)
            await interaction.followup.send(f"Bot `{bot_id}` started.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in start_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /stop-bot ────────────────────────────────────────────

    @app_commands.command(name="stop-bot", description="Stop a running bot")
    @app_commands.describe(bot_id="Bot ID")
    @not_suspended()
    @rate_limit_check()
    async def stop_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.stop_bot(uid)
            await interaction.followup.send(f"Bot `{bot_id}` stopped.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in stop_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /restart-bot ─────────────────────────────────────────

    @app_commands.command(name="restart-bot", description="Restart a bot")
    @app_commands.describe(bot_id="Bot ID")
    @not_suspended()
    @rate_limit_check()
    async def restart_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.restart_bot(uid)
            await interaction.followup.send(f"Bot `{bot_id}` restarted.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in restart_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /delete-bot ──────────────────────────────────────────

    @app_commands.command(name="delete-bot", description="Permanently delete a bot")
    @app_commands.describe(bot_id="Bot ID")
    @not_suspended()
    @rate_limit_check()
    async def delete_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.delete_bot(uid)
            await interaction.followup.send(f"Bot `{bot_id}` deleted.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in delete_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /replace-files ───────────────────────────────────────

    @app_commands.command(name="replace-files", description="Upload new code for an existing bot")
    @app_commands.describe(bot_id="Bot ID", zip_file="New ZIP file")
    @not_suspended()
    @rate_limit_check()
    async def replace_files(
        self, interaction: discord.Interaction, bot_id: str, zip_file: discord.Attachment,
    ) -> None:
        settings = get_settings()
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            if zip_file.size > settings.max_zip_size_bytes:
                await interaction.followup.send(f"File too large — max {settings.max_zip_size_mb}MB.", ephemeral=True)
                return
            if not zip_file.filename.endswith(".zip"):
                await interaction.followup.send("Upload a `.zip` file.", ephemeral=True)
                return

            zip_data = await zip_file.read()
            result = await self.deploy.replace_files(uid, zip_data)
            await interaction.followup.send(
                f"Files updated for `{bot_id}`. Status: {result['status']}", ephemeral=True
            )

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in replace_files", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /edit-file ───────────────────────────────────────────

    @app_commands.command(name="edit-file", description="View or edit a file in your bot's directory")
    @app_commands.describe(
        bot_id="Bot ID", filename="File path (e.g. main.py)",
        content="New content (leave empty to view)",
    )
    @not_suspended()
    @rate_limit_check()
    async def edit_file(
        self, interaction: discord.Interaction, bot_id: str,
        filename: str, content: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            if content is None:
                file_content = await self.deploy.read_bot_file(uid, filename)
                chunks = chunk_text(f"```\n{file_content}\n```")
                for chunk in chunks:
                    await interaction.followup.send(chunk, ephemeral=True)
            else:
                await self.deploy.write_bot_file(uid, filename, content)
                await interaction.followup.send(f"`{filename}` updated, bot restarted.", ephemeral=True)

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in edit_file", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── /view-logs ───────────────────────────────────────────

    @app_commands.command(name="view-logs", description="View recent bot logs")
    @app_commands.describe(bot_id="Bot ID")
    @not_suspended()
    @rate_limit_check()
    async def view_logs(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            logs = await self.deploy.get_bot_logs(uid)
            if not logs.strip():
                await interaction.followup.send("No logs yet.", ephemeral=True)
                return
            chunks = chunk_text(f"```\n{logs}\n```")
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True)

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"{exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in view_logs", error=str(exc), exc_info=True)
            await interaction.followup.send("Something went wrong.", ephemeral=True)

    # ── Error Handler ────────────────────────────────────────

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error)
        else:
            logger.error("Unhandled command error", error=str(error), exc_info=True)
            msg = "Something went wrong. Try again."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    deployment_service = getattr(bot, "deployment_service", None)
    if deployment_service is None:
        raise RuntimeError("bot.deployment_service must be set before loading this cog")
    await bot.add_cog(UserCommands(bot, deployment_service))
