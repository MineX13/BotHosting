"""
Admin-only slash commands for MineNodes Bot Hoster.

Only accessible by ADMIN_USER_ID.

Commands:
- /admin-users         — List all users with bot counts
- /admin-user-bots     — List bots for a specific user
- /admin-suspend-user  — Suspend a user (stops all bots)
- /admin-unsuspend-user — Unsuspend a user
- /admin-delete-bot    — Force-delete any bot
- /admin-set-limits    — Set per-user resource limits (bots, RAM, CPU)
- /admin-view-limits   — View a user's current resource limits
- /admin-stats         — System resource usage
- /admin-broadcast     — Send a DM to all users
"""

from __future__ import annotations

from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from app.commands.middleware import is_admin
from app.config.settings import get_settings
from app.database import queries as db
from app.services.deployment_service import DeploymentService, DeploymentError
from app.services.monitoring_service import MonitoringService
from app.utils.logging import get_logger

logger = get_logger("commands.admin")


class AdminCommands(commands.Cog):
    """Slash commands restricted to the configured administrator."""

    def __init__(
        self,
        bot: commands.Bot,
        deployment_service: DeploymentService,
        monitoring_service: MonitoringService,
    ) -> None:
        self.bot = bot
        self.deploy = deployment_service
        self.monitoring = monitoring_service

    # ── /admin-users ─────────────────────────────────────────

    @app_commands.command(
        name="admin-users",
        description="[ADMIN] List all registered users",
    )
    @is_admin()
    async def admin_users(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            users = await db.list_all_users()

            if not users:
                await interaction.followup.send(
                    "📭 No users registered yet.", ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"👥 Registered Users ({len(users)})",
                color=discord.Color.gold(),
            )

            for user in users[:25]:  # Discord embed limit
                status = "🚫 Suspended" if user["suspended"] else "✅ Active"
                embed.add_field(
                    name=f"User {user['id']}",
                    value=(
                        f"**Bots:** {user['bot_count']}/{user['max_bots']}\n"
                        f"**RAM:** {user['max_ram_mb']}MB | **CPU:** {user['max_cpu']}\n"
                        f"**Status:** {status}\n"
                        f"**Joined:** {user['created_at'].strftime('%Y-%m-%d')}"
                    ),
                    inline=True,
                )

            if len(users) > 25:
                embed.set_footer(text=f"Showing 25 of {len(users)} users")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("Error in admin_users", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to list users.", ephemeral=True
            )

    # ── /admin-user-bots ─────────────────────────────────────

    @app_commands.command(
        name="admin-user-bots",
        description="[ADMIN] List all bots for a specific user",
    )
    @app_commands.describe(user_id="Discord user ID to look up")
    @is_admin()
    async def admin_user_bots(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)
            bots = await db.get_bots_by_user_id(uid)

            if not bots:
                await interaction.followup.send(
                    f"📭 No bots found for user `{user_id}`.", ephemeral=True
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
                title=f"🤖 Bots for User {user_id} ({len(bots)})",
                color=discord.Color.blue(),
            )

            for bot in bots:
                icon = STATUS_ICONS.get(bot["status"], "❓")
                embed.add_field(
                    name=f"{icon} {bot['name']}",
                    value=(
                        f"**ID:** `{bot['id']}`\n"
                        f"**Container:** `{bot['container_name']}`\n"
                        f"**Status:** {bot['status']}\n"
                        f"**Runtime:** {bot['runtime']}\n"
                        f"**RAM:** {bot['ram_limit_mb']}MB"
                    ),
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.followup.send(
                "❌ Invalid user ID. Must be a number.", ephemeral=True
            )
        except Exception as exc:
            logger.error("Error in admin_user_bots", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to list user bots.", ephemeral=True
            )

    # ── /admin-suspend-user ──────────────────────────────────

    @app_commands.command(
        name="admin-suspend-user",
        description="[ADMIN] Suspend a user and stop all their bots",
    )
    @app_commands.describe(user_id="Discord user ID to suspend")
    @is_admin()
    async def admin_suspend_user(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)

            # Suspend user
            await db.ensure_user(uid)
            await db.suspend_user(uid)

            # Stop all their bots
            bot_ids = await db.get_user_bot_ids(uid)
            stopped = 0
            for bid in bot_ids:
                try:
                    await self.deploy.stop_bot(bid)
                    stopped += 1
                except DeploymentError:
                    pass

            await interaction.followup.send(
                f"🚫 User `{user_id}` suspended. {stopped}/{len(bot_ids)} bots stopped.",
                ephemeral=True,
            )

        except ValueError:
            await interaction.followup.send(
                "❌ Invalid user ID.", ephemeral=True
            )
        except Exception as exc:
            logger.error("Error in admin_suspend_user", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to suspend user.", ephemeral=True
            )

    # ── /admin-unsuspend-user ────────────────────────────────

    @app_commands.command(
        name="admin-unsuspend-user",
        description="[ADMIN] Unsuspend a user",
    )
    @app_commands.describe(user_id="Discord user ID to unsuspend")
    @is_admin()
    async def admin_unsuspend_user(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)
            await db.unsuspend_user(uid)
            await interaction.followup.send(
                f"✅ User `{user_id}` unsuspended.", ephemeral=True
            )
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid user ID.", ephemeral=True
            )
        except Exception as exc:
            logger.error("Error in admin_unsuspend_user", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to unsuspend user.", ephemeral=True
            )

    # ── /admin-delete-bot ────────────────────────────────────

    @app_commands.command(
        name="admin-delete-bot",
        description="[ADMIN] Force-delete any bot by ID",
    )
    @app_commands.describe(bot_id="UUID of the bot to delete")
    @is_admin()
    async def admin_delete_bot(
        self,
        interaction: discord.Interaction,
        bot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = UUID(bot_id)
            await self.deploy.delete_bot(uid)
            await interaction.followup.send(
                f"🗑️ Bot `{bot_id}` force-deleted.", ephemeral=True
            )
        except (ValueError, DeploymentError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in admin_delete_bot", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to delete bot.", ephemeral=True
            )

    # ── /admin-set-limits ────────────────────────────────────

    @app_commands.command(
        name="admin-set-limits",
        description="[ADMIN] Set per-user resource limits (bots, RAM, CPU)",
    )
    @app_commands.describe(
        user_id="Discord user ID to configure",
        max_bots="Max bots allowed (e.g. 5)",
        max_ram_mb="Max RAM per bot in MB (e.g. 1024)",
        max_cpu="Max CPU per bot (e.g. 1.0 = 1 core)",
    )
    @is_admin()
    async def admin_set_limits(
        self,
        interaction: discord.Interaction,
        user_id: str,
        max_bots: int = None,
        max_ram_mb: int = None,
        max_cpu: float = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)

            if max_bots is None and max_ram_mb is None and max_cpu is None:
                await interaction.followup.send(
                    "❌ Provide at least one limit to set (max_bots, max_ram_mb, or max_cpu).",
                    ephemeral=True,
                )
                return

            # Validate ranges
            if max_bots is not None and max_bots < 1:
                await interaction.followup.send("❌ max_bots must be ≥ 1.", ephemeral=True)
                return
            if max_ram_mb is not None and max_ram_mb < 64:
                await interaction.followup.send("❌ max_ram_mb must be ≥ 64.", ephemeral=True)
                return
            if max_cpu is not None and max_cpu <= 0:
                await interaction.followup.send("❌ max_cpu must be > 0.", ephemeral=True)
                return

            # Ensure user exists first
            await db.ensure_user(uid)

            updated = await db.update_user_limits(
                user_id=uid,
                max_bots=max_bots,
                max_ram_mb=max_ram_mb,
                max_cpu=max_cpu,
            )

            embed = discord.Embed(
                title=f"⚙️ Limits Updated — User {user_id}",
                color=discord.Color.green(),
            )
            embed.add_field(name="🤖 Max Bots", value=str(updated["max_bots"]), inline=True)
            embed.add_field(name="🧠 Max RAM", value=f"{updated['max_ram_mb']} MB", inline=True)
            embed.add_field(name="⚡ Max CPU", value=str(updated["max_cpu"]), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        except Exception as exc:
            logger.error("Error in admin_set_limits", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ Failed to set limits.", ephemeral=True)

    # ── /admin-view-limits ───────────────────────────────────

    @app_commands.command(
        name="admin-view-limits",
        description="[ADMIN] View a user's current resource limits",
    )
    @app_commands.describe(user_id="Discord user ID to view")
    @is_admin()
    async def admin_view_limits(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)
            limits = await db.get_user_limits(uid)
            bot_count = await db.count_user_bots(uid)

            embed = discord.Embed(
                title=f"📋 Resource Limits — User {user_id}",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="🤖 Bots",
                value=f"{bot_count}/{limits['max_bots']} used",
                inline=True,
            )
            embed.add_field(
                name="🧠 Max RAM / Bot",
                value=f"{limits['max_ram_mb']} MB",
                inline=True,
            )
            embed.add_field(
                name="⚡ Max CPU / Bot",
                value=str(limits["max_cpu"]),
                inline=True,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        except Exception as exc:
            logger.error("Error in admin_view_limits", error=str(exc), exc_info=True)
            await interaction.followup.send("❌ Failed to view limits.", ephemeral=True)

    # ── /admin-stats ─────────────────────────────────────────

    @app_commands.command(
        name="admin-stats",
        description="[ADMIN] View system resource usage and bot statistics",
    )
    @is_admin()
    async def admin_stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            # Database stats
            db_stats = await db.get_stats()

            # System stats
            sys_stats = await self.monitoring.get_system_stats()

            embed = discord.Embed(
                title="📊 System Statistics",
                color=discord.Color.purple(),
            )

            # Bot stats
            embed.add_field(
                name="🤖 Bots",
                value=(
                    f"**Total:** {db_stats['total_bots']}\n"
                    f"**Running:** {db_stats['running_bots']}\n"
                    f"**Users:** {db_stats['total_users']}"
                ),
                inline=True,
            )

            # CPU & Memory
            if sys_stats:
                embed.add_field(
                    name="🖥️ System",
                    value=(
                        f"**CPU:** {sys_stats.get('cpu_percent', 'N/A')}%\n"
                        f"**RAM:** {sys_stats.get('memory_used_mb', 'N/A')}/"
                        f"{sys_stats.get('memory_total_mb', 'N/A')} MB "
                        f"({sys_stats.get('memory_percent', 'N/A')}%)\n"
                        f"**Disk:** {sys_stats.get('disk_used_gb', 'N/A')}/"
                        f"{sys_stats.get('disk_total_gb', 'N/A')} GB "
                        f"({sys_stats.get('disk_percent', 'N/A')}%)"
                    ),
                    inline=True,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("Error in admin_stats", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "❌ Failed to get stats.", ephemeral=True
            )

    # ── /admin-broadcast ─────────────────────────────────────

    @app_commands.command(
        name="admin-broadcast",
        description="[ADMIN] Send a DM to all registered users",
    )
    @app_commands.describe(message="Message to broadcast")
    @is_admin()
    async def admin_broadcast(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            users = await db.list_all_users()
            sent = 0
            failed = 0

            embed = discord.Embed(
                title="📢 System Announcement",
                description=message,
                color=discord.Color.orange(),
            )
            embed.set_footer(
                text=f"From {interaction.user.display_name} — MineNodes Bot Hoster"
            )

            for user_record in users:
                try:
                    discord_user = await self.bot.fetch_user(user_record["id"])
                    await discord_user.send(embed=embed)
                    sent += 1
                except Exception:
                    failed += 1

            await interaction.followup.send(
                f"📢 Broadcast complete: {sent} sent, {failed} failed.",
                ephemeral=True,
            )

        except Exception as exc:
            logger.error("Error in admin_broadcast", error=str(exc), exc_info=True)
            await interaction.followup.send(
                "Broadcast failed.", ephemeral=True
            )

    # ── /admin-send-backup ────────────────────────────────────

    @app_commands.command(
        name="admin-send-backup",
        description="DM each user a ZIP backup of their bot files",
    )
    @is_admin()
    async def admin_send_backup(self, interaction: discord.Interaction) -> None:
        import io
        import zipfile
        from pathlib import Path

        await interaction.response.defer(ephemeral=True, thinking=True)

        settings = get_settings()
        users = await db.list_all_users()

        if not users:
            await interaction.followup.send("No users found.", ephemeral=True)
            return

        sent = 0
        failed = 0
        skipped = 0

        for user_row in users:
            user_id = user_row["id"]
            bots = await db.list_user_bots(user_id)

            if not bots:
                skipped += 1
                continue

            # Build a ZIP with all their bot files
            buf = io.BytesIO()
            try:
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for bot in bots:
                        bot_dir = Path(bot["bot_path"])
                        if not bot_dir.exists():
                            continue
                        for file in bot_dir.rglob("*"):
                            if file.is_file() and file.name != "_upload.zip":
                                arcname = f"{bot['name']}/{file.relative_to(bot_dir)}"
                                zf.write(file, arcname)

                buf.seek(0)

                # Skip empty archives
                if buf.getbuffer().nbytes <= 22:
                    skipped += 1
                    continue

                # DM the user
                try:
                    discord_user = await self.bot.fetch_user(user_id)
                    dm = await discord_user.create_dm()
                    file = discord.File(buf, filename=f"minenodes_backup_{user_id}.zip")
                    await dm.send(
                        "Here's a backup of your bot files from MineNodes Bot Hoster.",
                        file=file,
                    )
                    sent += 1
                except discord.Forbidden:
                    failed += 1
                except discord.NotFound:
                    failed += 1

            except Exception as exc:
                logger.error("Backup failed for user", user_id=user_id, error=str(exc))
                failed += 1

        await interaction.followup.send(
            f"Backup done — sent: {sent}, failed: {failed}, skipped (no bots): {skipped}",
            ephemeral=True,
        )

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
            logger.error("Admin command error", error=str(error), exc_info=True)
            msg = "Something went wrong."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Called by bot.load_extension() — registers the cog."""
    deployment_service = getattr(bot, "deployment_service", None)
    monitoring_service = getattr(bot, "monitoring_service", None)
    if deployment_service is None:
        raise RuntimeError("bot.deployment_service must be set before loading this cog")
    if monitoring_service is None:
        raise RuntimeError("bot.monitoring_service must be set before loading this cog")
    await bot.add_cog(AdminCommands(bot, deployment_service, monitoring_service))
