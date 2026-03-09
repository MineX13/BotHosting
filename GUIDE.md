# MineNodes Bot Hoster — Quick Start Guide

This guide covers how to set up, start, and restart the MineNodes Bot Hoster controller on your VPS.

---

## 🚀 1. Quick Setup (First Time)

If you are setting up for the first time:

```bash
# 1. Clone & Enter
git clone https://github.com/jpn900013-maker/bot.git /bot
cd /bot

# 2. Setup environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Initialize Database
# Ensure PostgreSQL is running, then:
psql -U controller -d bot_hosting -f app/database/schema.sql

# 4. Start Redis (for rate limiting)
redis-server --daemonize yes
```

---

## 🏁 2. Starting the Bot

### Manual Start (for testing)
```bash
source venv/bin/activate
python3 -m app.main
```

### Background Start (recommended)
```bash
# Start as a background process
nohup venv/bin/python3 -m app.main > controller.log 2>&1 &
```

---

## 🔄 3. Restart & Update

When you want to apply updates or just restart:

```bash
# 1. Pull latest code
cd /bot
git pull origin main

# 2. Kill existing controller process
pkill -f "python3.*app.main"

# 3. Restart in background
nohup venv/bin/python3 -m app.main > controller.log 2>&1 &
```

---

## 🛠 4. Common Commands

| Task | Command |
|---|---|
| **View Logs** | `tail -f controller.log` |
| **Check Redis** | `redis-cli ping` (Should reply `PONG`) |
| **Check Port 5432** | `ss -lntp | grep 5432` |
| **Kill All Bots** | `pkill -f "python3.*bot.py"` (Warning: Stops ALL hosted bots) |

---

## 💡 Notes

- **Auto-Restart**: Any bots that were "Running" when the system restarts will automatically restart when you launch the controller.
- **Environment**: Ensure your `.env` file contains correct `DISCORD_BOT_TOKEN`, `DATABASE_URL`, and `REDIS_URL`.
- **Encryption**: If you lose your `ENCRYPTION_KEY`, you won't be able to retrieve bot tokens from the database!




apt update && apt install -y redis-server
redis-server --daemonize yes
cd /bot && git pull origin main

redis-server --daemonize yes
echo "REDIS_URL=redis://localhost:6379" >> /bot/.env
cd /bot && git pull origin main
redis-cli ping
pkill -f "python.*main" ; sleep 1 && cd /bot && python -m app.main &
