# MineNodes Bot Hoster — Deployment Guide

Production deployment on a Linux VPS (no Docker required).

---

## Prerequisites

| Requirement       | Version          |
|-------------------|------------------|
| Python            | 3.11+            |
| PostgreSQL        | 14+              |
| Redis             | 7+ (optional)    |
| OS                | Ubuntu 22.04 LTS |

---

## 1. System Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Install PostgreSQL
sudo apt install -y postgresql postgresql-contrib

# Install Redis (optional — enables rate limiting)
sudo apt install -y redis-server
sudo systemctl enable redis-server
```

## 2. Database Setup

```bash
# Create database and user
sudo -u postgres psql <<EOF
CREATE USER controller WITH PASSWORD 'your_secure_password';
CREATE DATABASE bot_hosting OWNER controller;
GRANT ALL PRIVILEGES ON DATABASE bot_hosting TO controller;
EOF
```

## 3. Clone & Install

```bash
# Clone the repository
cd /opt
git clone https://github.com/jpn900013-maker/bot.git minenodes
cd minenodes

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install uvloop  # Linux performance boost

# Install psutil for process monitoring
pip install psutil
```

## 4. Configuration

```bash
# Create .env file
cat > .env <<'EOF'
# Discord
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# Database
DATABASE_URL=postgresql://controller:your_secure_password@localhost:5432/bot_hosting

# Redis (optional)
REDIS_URL=redis://localhost:6379/0

# Encryption key (generate one with: python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())")
ENCRYPTION_KEY=your_base64_key_here

# Paths
BASE_BOT_PATH=/srv/bots

# Admin
ADMIN_USER_ID=941139424580890666
EOF
```

**Generate encryption key:**
```bash
python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

## 5. Initialize Database

```bash
source venv/bin/activate
psql -U controller -d bot_hosting -f app/database/schema.sql
```

## 6. Create Bot Directory

```bash
sudo mkdir -p /srv/bots
sudo chown $USER:$USER /srv/bots
```

## 7. Run (Manual)

```bash
source venv/bin/activate
python3 -m app.main
```

## 8. Run as Systemd Service (Production)

```bash
sudo tee /etc/systemd/system/minenodes.service > /dev/null <<EOF
[Unit]
Description=MineNodes Bot Hoster
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/minenodes
ExecStart=/opt/minenodes/venv/bin/python -m app.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable minenodes
sudo systemctl start minenodes
```

### Service Management

```bash
# View status
sudo systemctl status minenodes

# View logs
sudo journalctl -u minenodes -f

# Restart
sudo systemctl restart minenodes

# Stop
sudo systemctl stop minenodes
```

## 9. Update / Redeploy

```bash
cd /opt/minenodes
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart minenodes
```

## 10. Database Migrations

When `schema.sql` changes (e.g., new columns), apply the migration on the VPS:

```bash
# Add new columns to existing users table
sudo -u postgres psql -d bot_hosting <<EOF
ALTER TABLE users ADD COLUMN IF NOT EXISTS max_bots INTEGER NOT NULL DEFAULT 3;
ALTER TABLE users ADD COLUMN IF NOT EXISTS max_ram_mb INTEGER NOT NULL DEFAULT 512;
ALTER TABLE users ADD COLUMN IF NOT EXISTS max_cpu REAL NOT NULL DEFAULT 0.5;
EOF
```

---

## Architecture

```
Controller (Discord Bot)
  ├── Slash Commands (user + admin)
  ├── ProcessService (manages bot subprocesses)
  ├── DeploymentService (deploy / update / delete)
  ├── MonitoringService (health checks)
  ├── PostgreSQL (bot metadata, user limits)
  └── Redis (optional rate limiting)

User Bot Flow:
  1. User uploads ZIP + token via /create-bot
  2. Controller extracts to /srv/bots/<user>/<bot>/
  3. Installs dependencies (pip/npm)
  4. Starts bot as subprocess with BOT_TOKEN env var
  5. Monitors process health every 30s
```

## Slash Commands

### User Commands
| Command         | Description                          |
|-----------------|--------------------------------------|
| `/status`       | Ping, uptime, service info           |
| `/help`         | Show all available commands          |
| `/create-bot`   | Deploy a new bot from ZIP            |
| `/list-bots`    | List your hosted bots                |
| `/start-bot`    | Start a stopped bot                  |
| `/stop-bot`     | Stop a running bot                   |
| `/restart-bot`  | Restart a bot                        |
| `/delete-bot`   | Permanently delete a bot             |
| `/replace-files`| Upload new code for a bot            |
| `/edit-file`    | View/edit a file in bot directory    |
| `/view-logs`    | View bot's recent logs               |

### Admin Commands
| Command                | Description                          |
|------------------------|--------------------------------------|
| `/admin-users`         | List all registered users            |
| `/admin-user-bots`     | View a specific user's bots          |
| `/admin-set-limits`    | Set user resource limits             |
| `/admin-view-limits`   | View user resource limits            |
| `/admin-suspend-user`  | Suspend a user                       |
| `/admin-unsuspend-user`| Unsuspend a user                     |
| `/admin-delete-bot`    | Force-delete any bot                 |
| `/admin-stats`         | System resource usage                |
| `/admin-broadcast`     | DM all users                         |
