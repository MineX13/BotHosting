# Discord Bot Hosting Controller — Deployment Guide

## Table of Contents
1. [Architecture](#architecture)
2. [Windows Development Setup](#windows-development-setup)
3. [Ubuntu VPS Production Setup](#ubuntu-vps-production-setup)
4. [Firewall Configuration](#firewall-configuration)
5. [Scaling Roadmap (1000+ Users)](#scaling-roadmap)
6. [Kubernetes Migration Plan](#kubernetes-migration-plan)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Discord Gateway (WSS)                      │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│               Controller Bot (discord.py)                     │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Commands Layer (Slash Commands)                         │  │
│  │  ├── User Commands (create, start, stop, delete, ...)   │  │
│  │  └── Admin Commands (stats, suspend, broadcast, ...)    │  │
│  └──────────────────────┬──────────────────────────────────┘  │
│                         │                                     │
│  ┌──────────────────────▼──────────────────────────────────┐  │
│  │  Service Layer                                           │  │
│  │  ├── DeploymentService   (build, deploy, lifecycle)      │  │
│  │  ├── DockerService       (container management)          │  │
│  │  ├── EncryptionService   (AES-256-GCM tokens)           │  │
│  │  ├── ValidationService   (ZIP, token, file checks)      │  │
│  │  └── MonitoringService   (health, cleanup, alerts)      │  │
│  └──────────────────────┬──────────────────────────────────┘  │
│                         │                                     │
│  ┌──────────────────────▼──────────────────────────────────┐  │
│  │  Infrastructure                                          │  │
│  │  ├── PostgreSQL (asyncpg)  — user/bot records           │  │
│  │  ├── Redis (rate limiting, caching)                     │  │
│  │  └── Docker Engine (container runtime)                  │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

**Security layers:**
- AES-256-GCM encryption for all bot tokens
- ZIP validation (no traversal, symlinks, or executables)
- Non-root containers with read-only root filesystem
- Per-user rate limiting via Redis
- Structured logging with automatic token redaction

---

## Windows Development Setup

### Prerequisites
- Python 3.11+ → https://python.org
- Docker Desktop → https://docker.com/products/docker-desktop
- Git → https://git-scm.com

### Steps

```powershell
# 1. Navigate to controller directory
cd "c:\VS.prog\MC\GOODCHKR\bot hosting\controller"

# 2. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Generate encryption key
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# Copy the output → paste into .env as ENCRYPTION_KEY

# 5. Copy .env.example to .env and fill in values
copy .env.example .env
# Edit .env with your Discord bot token, encryption key, etc.

# 6. Start infrastructure (PostgreSQL + Redis)
docker-compose up -d postgres redis

# 7. Verify services are running
docker-compose ps

# 8. Run the controller bot
python -m app.main
```

**Windows-specific .env values:**
```ini
DOCKER_HOST=npipe:////./pipe/docker_engine
BASE_BOT_PATH=C:\bots
```

---

## Ubuntu VPS Production Setup

### System Requirements
- Ubuntu 22.04 LTS
- 8GB RAM (minimum)
- 4 CPU cores
- 100GB SSD

### Steps

```bash
# 1. System updates
sudo apt update && sudo apt upgrade -y

# 2. Install Python 3.11+
sudo apt install -y python3.11 python3.11-venv python3-pip

# 3. Install Docker Engine
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Log out and back in for group change

# 4. Install Docker Compose (plugin)
sudo apt install -y docker-compose-plugin

# 5. Clone / upload project
cd /opt
sudo mkdir bot-hosting && sudo chown $USER:$USER bot-hosting
cd bot-hosting
# Upload or clone your controller/ directory here

# 6. Setup Python environment
cd controller
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install uvloop  # Linux-only performance boost

# 7. Configure environment
cp .env.example .env
nano .env
# Set these values:
#   DISCORD_BOT_TOKEN=your_token
#   ENCRYPTION_KEY=your_generated_key
#   DOCKER_HOST=unix:///var/run/docker.sock
#   BASE_BOT_PATH=/srv/bots
#   DATABASE_URL=postgresql://controller:STRONG_PASSWORD@localhost:5432/bot_hosting

# 8. Create bot storage directory
sudo mkdir -p /srv/bots
sudo chown $USER:$USER /srv/bots

# 9. Start infrastructure
docker compose up -d

# 10. Run the bot (foreground test)
python -m app.main

# 11. Create systemd service for production
sudo tee /etc/systemd/system/bot-controller.service > /dev/null << 'EOF'
[Unit]
Description=Discord Bot Hosting Controller
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/bot-hosting/controller
ExecStart=/opt/bot-hosting/controller/venv/bin/python -m app.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bot-controller
sudo systemctl start bot-controller

# 12. Check status
sudo systemctl status bot-controller
sudo journalctl -u bot-controller -f
```

**Linux-specific .env values:**
```ini
DOCKER_HOST=unix:///var/run/docker.sock
BASE_BOT_PATH=/srv/bots
```

---

## Firewall Configuration

```bash
# UFW (Ubuntu)
sudo ufw default deny incoming
sudo ufw default allow outgoing

# SSH (change port if needed)
sudo ufw allow 22/tcp

# PostgreSQL — local only (DO NOT expose externally)
# Already bound to 127.0.0.1 in docker-compose.yml

# Redis — local only
# Already bound to 127.0.0.1 in docker-compose.yml

# Enable firewall
sudo ufw enable
sudo ufw status verbose
```

**Important:** PostgreSQL and Redis are bound to `127.0.0.1` in `docker-compose.yml`. They are **never** exposed to the public internet.

---

## Scaling Roadmap

### Phase 1: Current (100 users / 300 bots)
- Single VPS, 8GB RAM
- Single PostgreSQL + Redis
- Monolithic controller bot

### Phase 2: Vertical Scaling (500 users)
- Upgrade to 16-32GB RAM VPS
- PostgreSQL connection pool tuning (increase pool size)
- Redis maxmemory increase
- Add swap space as safety net
- Implement per-user disk quotas via Docker `--storage-opt`

### Phase 3: Horizontal Scaling (1000+ users)
- **Database:** PostgreSQL read replicas for queries
- **Cache:** Redis Cluster (3-node minimum)
- **Storage:** Shared NFS/EFS for bot files across nodes
- **Orchestration:** Docker Swarm or Kubernetes for container scheduling
- **Controller:** Multiple bot shards (discord.py AutoShardedBot)
- **Load balancing:** HAProxy in front of multiple controller instances
- **Monitoring:** Prometheus + Grafana for metrics

### Phase 4: Enterprise (10,000+ users)
- Kubernetes with Horizontal Pod Autoscaler
- Managed PostgreSQL (RDS/Cloud SQL)
- Managed Redis (ElastiCache/Memorystore)
- Object storage (S3/GCS) for bot ZIPs
- CDN for static assets
- Stripe billing integration
- Multi-region deployment

---

## Kubernetes Migration Plan

### Architecture

```
┌─────────────────────────────────────────────────┐
│                 Kubernetes Cluster               │
│                                                  │
│  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Namespace:   │  │  Namespace:              │  │
│  │  controller   │  │  hosted-bots             │  │
│  │              │  │                          │  │
│  │  Deployment  │  │  Each bot = 1 Pod        │  │
│  │  ├── Bot     │  │  ├── Resource limits     │  │
│  │  │   Shards  │  │  ├── Network policies    │  │
│  │  │   (x3)    │  │  └── PVC for files       │  │
│  │  │           │  │                          │  │
│  │  StatefulSet │  │                          │  │
│  │  ├── PG      │  │                          │  │
│  │  └── Redis   │  │                          │  │
│  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### Helm Chart Structure

```
helm/
├── Chart.yaml
├── values.yaml
├── templates/
│   ├── controller-deployment.yaml
│   ├── controller-service.yaml
│   ├── postgres-statefulset.yaml
│   ├── redis-statefulset.yaml
│   ├── bot-network-policy.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   └── pvc.yaml
```

### Key Changes for K8s Migration
1. **DockerService → KubernetesService:** Replace Docker SDK calls with Kubernetes Python client (`kubernetes` package). Each bot becomes a Pod/Deployment instead of a container.
2. **Storage:** Replace local filesystem with PersistentVolumeClaims (PVCs) per bot.
3. **Networking:** Use NetworkPolicies to isolate bot pods. Each bot pod gets its own network namespace.
4. **Scaling:** Use HorizontalPodAutoscaler for the controller. Bot pods have fixed resource requests/limits.
5. **Secrets:** Store encryption keys in Kubernetes Secrets; bot tokens stay in PostgreSQL.

### Future-Ready Integrations
- **Stripe Billing:** Add a `subscriptions` table; gate `/create-bot` on active subscription. Use Stripe Webhooks via a FastAPI sidecar.
- **Web Dashboard:** FastAPI app sharing the same database. SSO via Discord OAuth2. Real-time logs via WebSocket proxy to container logs.
- **Usage Metering:** Prometheus metrics per container. Store hourly CPU/RAM usage in a `usage_metrics` table for billing.
- **Backup System:** Periodic pg_dump + tar of `/srv/bots` to S3-compatible storage.
