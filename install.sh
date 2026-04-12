#!/bin/bash
# ============================================================
# MineNodes VPS Auto-Installer (For Ubuntu/Debian Linux)
# ============================================================

echo "============================================="
echo " MineNodes — Automated VPS Setup Script"
echo "============================================="

# 1. Install System Dependencies
echo ">>> [1/4] Installing system dependencies (PostgreSQL, Redis, Python)..."
sudo apt-get update -y
sudo apt-get install -y cpulimit postgresql postgresql-contrib redis-server python3 python3-pip python3-venv

# 2. Start and Enable Background Services
echo ">>> [2/4] Starting Redis and PostgreSQL services..."
sudo systemctl enable redis-server
sudo systemctl start redis-server
sudo systemctl enable postgresql
sudo systemctl start postgresql

# 3. Setup PostgreSQL Database
echo ">>> [3/4] Configuring PostgreSQL Database..."
# Set up a secure database user and the required 'bot_hosting' database
DB_PASS=$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 16)
sudo -u postgres psql -c "CREATE USER controller WITH PASSWORD '${DB_PASS}';"
sudo -u postgres psql -c "CREATE DATABASE bot_hosting OWNER controller;"

# 4. Install Python code requirements
echo ">>> [4/4] Installing Python requirements..."
pip3 install -r requirements.txt

echo "========================================================================"
echo " ✅ SETUP COMPLETE!"
echo ""
echo " IMPORTANT: Copy the following line into your config.env file:"
echo " DATABASE_URL=postgresql://controller:${DB_PASS}@localhost:5432/bot_hosting"
echo ""
echo " Once your config.env is ready, you can start everything by running:"
echo " python3 start.py"
echo "========================================================================"
