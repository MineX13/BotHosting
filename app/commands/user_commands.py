"""
User-facing slash commands for MineNodes Bot Hoster.

Commands:
- /create-bot — DMs user for private token input, then deploys
- /list-bots  — Show all user's bots with status
- /manage-bot — Rich management panel with buttons
- /start-bot  — Start a stopped bot
- /stop-bot   — Stop a running bot
- /restart-bot — Restart a bot
- /delete-bot — Permanently delete a bot
- /replace-files — Upload new ZIP → rebuild bot
- /edit-file  — Read/write a file in the bot directory
- /view-logs  — Show last 100 lines of bot logs
- /status     — Bot hoster status, ping, uptime
- /help       — Show all available commands
"""

from __future__ import annotations

import asyncio
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
# Bot Management Panel — Persistent View with Buttons
# ═══════════════════════════════════════════════════════════════


class BotManagementView(discord.ui.View):
    """Interactive button panel for managing a hosted bot."""

    def __init__(self, bot_id: str, deploy: DeploymentService) -> None:
        super().__init__(timeout=None)  # Persistent
        self.bot_id = bot_id
        self.deploy = deploy

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.start_bot(uid)
            await interaction.followup.send("✅ Bot started!", ephemeral=True)
            # Refresh the management embed
            await self._refresh_panel(interaction)
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.secondary, emoji="⏸️", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.stop_bot(uid)
            await interaction.followup.send("✅ Bot stopped.", ephemeral=True)
            await self._refresh_panel(interaction)
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.restart_bot(uid)
            await interaction.followup.send("✅ Bot restarted!", ephemeral=True)
            await self._refresh_panel(interaction)
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def logs_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            logs = await self.deploy.get_bot_logs(uid)
            if not logs.strip():
                await interaction.followup.send("📋 No logs available yet.", ephemeral=True)
                return
            chunks = chunk_text(f"```\n{logs}\n```")
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            from app.services.process_service import ProcessService
            process_svc = self.deploy._process
            bot_record = await db.get_bot(uid)
            if not bot_record:
                await interaction.followup.send("❌ Bot not found.", ephemeral=True)
                return
            stats = await process_svc.get_process_stats(bot_record["container_name"])
            if stats:
                embed = discord.Embed(title="📊 Live Stats", color=discord.Color.teal())
                embed.add_field(name="CPU", value=f"{stats.get('cpu_percent', 0):.1f}%", inline=True)
                embed.add_field(name="Memory", value=f"{stats.get('memory_mb', 0):.1f} MB", inline=True)
                embed.add_field(name="PID", value=str(stats.get('pid', 'N/A')), inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send("📊 Bot is not running — no stats.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(self.bot_id)
            await verify_bot_ownership(interaction, uid)
            # Confirm deletion
            confirm_view = ConfirmDeleteView(self.bot_id, self.deploy)
            await interaction.followup.send(
                "⚠️ **Are you sure?** This will permanently delete the bot and all its files.",
                view=confirm_view,
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    async def _refresh_panel(self, interaction: discord.Interaction) -> None:
        """Update the management embed with fresh data."""
        try:
            uid = UUID(self.bot_id)
            bot_record = await db.get_bot(uid)
            if bot_record:
                embed = build_management_embed(bot_record)
                view = BotManagementView(self.bot_id, self.deploy)
                await interaction.message.edit(embed=embed, view=view)
        except Exception:
            pass  # Silently fail refresh — panel stays as-is


class ConfirmDeleteView(discord.ui.View):
    """Confirmation dialog for bot deletion."""

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
            await interaction.followup.send(
                f"🗑️ Bot `{self.bot_id}` permanently deleted.", ephemeral=True
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Cancelled.", ephemeral=True)


def build_management_embed(bot_record) -> discord.Embed:
    """Build the rich management embed for a bot (similar to VPS management panel)."""
    STATUS_MAP = {
        "running": ("🟢 RUNNING", discord.Color.green()),
        "stopped": ("🔴 STOPPED", discord.Color.red()),
        "crashed": ("💥 CRASHED", discord.Color.dark_red()),
        "building": ("🔨 BUILDING", discord.Color.orange()),
        "error": ("⚠️ ERROR", discord.Color.dark_orange()),
    }

    status_text, color = STATUS_MAP.get(
        bot_record["status"], ("❓ UNKNOWN", discord.Color.greyple())
    )

    embed = discord.Embed(
        title=f"⭐ MineNodes — Bot Management",
        description=f"Managing bot: **{bot_record['name']}**",
        color=color,
    )

    # Allocated Resources section
    resources = (
        f"**Status:** {status_text}\n"
        f"**Runtime:** {bot_record['runtime'].capitalize()}\n"
        f"**RAM Limit:** {bot_record['ram_limit_mb']} MB\n"
        f"**CPU Limit:** {bot_record['cpu_limit']} cores\n"
        f"**Bot ID:** `{bot_record['id']}`"
    )
    embed.add_field(
        name="▸ 📦 Allocated Resources",
        value=resources,
        inline=False,
    )

    # Timing info
    created = bot_record["created_at"].strftime("%Y-%m-%d %H:%M")
    updated = bot_record["updated_at"].strftime("%Y-%m-%d %H:%M")
    timing = (
        f"**Created:** {created}\n"
        f"**Last Updated:** {updated}"
    )
    embed.add_field(
        name="▸ 🕐 Timing",
        value=timing,
        inline=False,
    )

    # Controls hint
    embed.add_field(
        name="▸ 🎛️ Controls",
        value="Use the buttons below to manage your bot",
        inline=False,
    )

    embed.set_footer(text=f"MineNodes Bot Hoster • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

    return embed


# ═══════════════════════════════════════════════════════════════
# DM-based Bot Creation Flow
# ═══════════════════════════════════════════════════════════════


class CreateBotModal(discord.ui.Modal, title="🤖 Create New Bot"):
    """Modal that appears in DM to collect bot token and config."""

    token = discord.ui.TextInput(
        label="Bot Token",
        placeholder="Paste your Discord bot token here...",
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
        placeholder="Display name for your bot (optional)",
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

            # Fetch full bot record for management panel
            bot_id = result["bot_id"]
            bot_record = await db.get_bot(UUID(str(bot_id)))

            if bot_record:
                embed = build_management_embed(bot_record)
                view = BotManagementView(str(bot_id), self.deploy)
                await interaction.followup.send(
                    "✅ **Bot deployed successfully!** Your token is encrypted and secure.",
                    embed=embed,
                    view=view,
                )
            else:
                embed = discord.Embed(
                    title="✅ Bot Deployed",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Bot ID", value=str(bot_id), inline=False)
                embed.add_field(name="Name", value=result["name"], inline=True)
                embed.add_field(name="Runtime", value=result["runtime"], inline=True)
                embed.set_footer(text="Token encrypted • Use /manage-bot to control")
                await interaction.followup.send(embed=embed)

        except DeploymentError as exc:
            await interaction.followup.send(f"❌ Deployment failed: {exc}")
        except Exception as exc:
            logger.error("Error in create_bot modal", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.")


# ═══════════════════════════════════════════════════════════════
# Main Cog
# ═══════════════════════════════════════════════════════════════


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
            embed.add_field(name="👥 Users", value=str(stats["total_users"]), inline=True)
            embed.add_field(
                name="📡 Status",
                value="🟢 Online" if latency_ms < 500 else "🟡 Degraded",
                inline=True,
            )
            embed.set_footer(text="MineNodes Bot Hoster • Reliable 24/7 Hosting")
            await interaction.followup.send(embed=embed)

        except Exception as exc:
            logger.error("Error in status", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ Failed to get status.", ephemeral=True)

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
                "`/manage-bot` — Open bot management panel\n"
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

    # ── /create-bot (DM Flow) ────────────────────────────────

    @app_commands.command(
        name="create-bot",
        description="Deploy a new Discord bot — DMs you for secure token input",
    )
    @app_commands.describe(
        zip_file="ZIP file containing your bot code (max 50MB)",
    )
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
            # Validate file
            if zip_file.size > settings.max_zip_size_bytes:
                await interaction.followup.send(
                    f"❌ File too large. Maximum size: {settings.max_zip_size_mb}MB",
                    ephemeral=True,
                )
                return

            if not zip_file.filename.endswith(".zip"):
                await interaction.followup.send(
                    "❌ Please upload a `.zip` file.", ephemeral=True
                )
                return

            # Download ZIP data first
            zip_data = await zip_file.read()

            # DM the user for token input
            try:
                dm_channel = await interaction.user.create_dm()

                dm_embed = discord.Embed(
                    title="🔐 MineNodes — Secure Bot Setup",
                    description=(
                        "Your bot code has been received! For security, "
                        "please provide your bot token here in DMs.\n\n"
                        "**Your token is encrypted with AES-256-GCM** before storage "
                        "and never visible to anyone."
                    ),
                    color=discord.Color.green(),
                )
                dm_embed.add_field(
                    name="📁 File",
                    value=f"`{zip_file.filename}` ({zip_file.size / 1024:.0f} KB)",
                    inline=True,
                )
                dm_embed.set_footer(text="Click the button below to continue setup")

                modal_button = CreateBotButton(self.deploy, zip_data, interaction.user.id)
                await dm_channel.send(embed=dm_embed, view=modal_button)

                await interaction.followup.send(
                    "📩 **Check your DMs!** I've sent you a secure setup message "
                    "to enter your bot token privately.",
                    ephemeral=True,
                )

            except discord.Forbidden:
                # Cannot DM — fallback: open modal directly in server (still private)
                fallback_view = CreateBotButton(self.deploy, zip_data, interaction.user.id)
                await interaction.followup.send(
                    "⚠️ I couldn't DM you, but no worries! "
                    "Click the button below to enter your token securely.\n"
                    "*Modal inputs are private — only you can see them.*",
                    view=fallback_view,
                    ephemeral=True,
                )

        except Exception as exc:
            logger.error("Error in create_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred.", ephemeral=True
            )

    # ── /manage-bot ──────────────────────────────────────────

    @app_commands.command(
        name="manage-bot",
        description="Open the bot management panel with controls",
    )
    @app_commands.describe(bot_id="The UUID of the bot to manage")
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
                await interaction.followup.send("❌ Bot not found.", ephemeral=True)
                return

            embed = build_management_embed(bot_record)
            view = BotManagementView(bot_id, self.deploy)

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in manage_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

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
                        f"**Created:** {created}\n"
                        f"Use `/manage-bot` with the ID above"
                    ),
                    inline=False,
                )

            embed.set_footer(text="Tip: Use /manage-bot <id> for full controls")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("Error in list_bots", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to list bots.", ephemeral=True
            )

    # ── /start-bot ───────────────────────────────────────────

    @app_commands.command(name="start-bot", description="Start a stopped bot")
    @app_commands.describe(bot_id="The UUID of the bot to start")
    @not_suspended()
    @rate_limit_check()
    async def start_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.start_bot(uid)
            await interaction.followup.send(f"✅ Bot `{bot_id}` started.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in start_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── /stop-bot ────────────────────────────────────────────

    @app_commands.command(name="stop-bot", description="Stop a running bot")
    @app_commands.describe(bot_id="The UUID of the bot to stop")
    @not_suspended()
    @rate_limit_check()
    async def stop_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.stop_bot(uid)
            await interaction.followup.send(f"✅ Bot `{bot_id}` stopped.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in stop_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── /restart-bot ─────────────────────────────────────────

    @app_commands.command(name="restart-bot", description="Restart a bot")
    @app_commands.describe(bot_id="The UUID of the bot to restart")
    @not_suspended()
    @rate_limit_check()
    async def restart_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.restart_bot(uid)
            await interaction.followup.send(f"✅ Bot `{bot_id}` restarted.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in restart_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── /delete-bot ──────────────────────────────────────────

    @app_commands.command(name="delete-bot", description="Permanently delete a bot (cannot be undone!)")
    @app_commands.describe(bot_id="The UUID of the bot to delete")
    @not_suspended()
    @rate_limit_check()
    async def delete_bot(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)
            await self.deploy.delete_bot(uid)
            await interaction.followup.send(f"🗑️ Bot `{bot_id}` permanently deleted.", ephemeral=True)
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in delete_bot", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── /replace-files ───────────────────────────────────────

    @app_commands.command(name="replace-files", description="Upload new code to replace an existing bot's files")
    @app_commands.describe(bot_id="The UUID of the bot to update", zip_file="New ZIP file with updated code")
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
                await interaction.followup.send(f"❌ File too large. Max: {settings.max_zip_size_mb}MB", ephemeral=True)
                return
            if not zip_file.filename.endswith(".zip"):
                await interaction.followup.send("❌ Please upload a `.zip` file.", ephemeral=True)
                return

            zip_data = await zip_file.read()
            result = await self.deploy.replace_files(uid, zip_data)
            await interaction.followup.send(
                f"✅ Bot `{bot_id}` files replaced and restarted.\nStatus: {result['status']}",
                ephemeral=True,
            )

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in replace_files", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── /edit-file ───────────────────────────────────────────

    @app_commands.command(name="edit-file", description="View or edit a file in your bot's directory")
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
                file_content = await self.deploy.read_bot_file(uid, filename)
                chunks = chunk_text(f"```\n{file_content}\n```")
                for chunk in chunks:
                    await interaction.followup.send(chunk, ephemeral=True)
            else:
                await self.deploy.write_bot_file(uid, filename, content)
                await interaction.followup.send(
                    f"✅ File `{filename}` updated and bot restarted.", ephemeral=True
                )

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in edit_file", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── /view-logs ───────────────────────────────────────────

    @app_commands.command(name="view-logs", description="View the last 100 lines of your bot's logs")
    @app_commands.describe(bot_id="The UUID of the bot")
    @not_suspended()
    @rate_limit_check()
    async def view_logs(self, interaction: discord.Interaction, bot_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await verify_bot_ownership(interaction, uid)

            logs = await self.deploy.get_bot_logs(uid)
            if not logs.strip():
                await interaction.followup.send("📋 No logs available yet.", ephemeral=True)
                return
            chunks = chunk_text(f"```\n{logs}\n```")
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True)

        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in view_logs", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    # ── Error Handler ────────────────────────────────────────

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
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


# ═══════════════════════════════════════════════════════════════
# DM Button (opens the setup modal)
# ═══════════════════════════════════════════════════════════════


class CreateBotButton(discord.ui.View):
    """Button sent via DM that opens the token input modal."""

    def __init__(self, deploy: DeploymentService, zip_data: bytes, user_id: int):
        super().__init__(timeout=300)  # 5 min to respond
        self.deploy = deploy
        self.zip_data = zip_data
        self.user_id = user_id

    @discord.ui.button(label="🔑 Enter Bot Token", style=discord.ButtonStyle.success)
    async def enter_token(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateBotModal(self.deploy, self.zip_data, self.user_id)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot) -> None:
    """Called by bot.load_extension() — registers the cog."""
    deployment_service = getattr(bot, "deployment_service", None)
    if deployment_service is None:
        raise RuntimeError("bot.deployment_service must be set before loading this cog")
    await bot.add_cog(UserCommands(bot, deployment_service))
