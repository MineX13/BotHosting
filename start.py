import subprocess
import sys
import time
import os

def start_process(command, name, env_vars=None):
    """Helper to start a process with custom environment variables."""
    print(f"Starting {name}...")
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    return subprocess.Popen(command, env=env)

def main():
    hosting_name = "MineNodes"
    if os.path.exists("config.env"):
        with open("config.env", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("HOSTING_NAME="):
                    hosting_name = line.strip().split("=", 1)[1]
                    break
                    
    print(f"Starting {hosting_name} — Full Stack Mode...")
    print("System made by MineX13")
    print("yt https://www.youtube.com/@minexz13")
    print("github https://github.com/MineX13")
    print("discord profile minex.13.")
    print("dc server https://discord.gg/n3Ed8zbduQ")
    print("-" * 40)
    
    processes = []
    
    # 1. Start Redis if not already running (Assuming local binary availability for Railway Nixpacks / Ubuntu)
    # Railway passes REDIS_URL if using their addon, but if local we can attempt to start it.
    try:
        redis_proc = start_process(["redis-server", "--daemonize", "no", "--port", "6379"], "Redis Server")
        processes.append((redis_proc, "Redis Server"))
    except FileNotFoundError:
        print("Redis server binary not found. Assuming external Redis is configured.")

    # Wait a moment for databases
    time.sleep(2)
    
    # 2. Start the Discord Management Bot
    bot_proc = start_process([sys.executable, "-m", "app.main"], "Discord Bot")
    processes.append((bot_proc, "Discord Bot"))
    
    # 3. Start the FastAPI Web Panel on port 7000
    panel_proc = start_process([sys.executable, "-m", "panel.main"], "Web Panel", env_vars={"PANEL_PORT": "7000"})
    processes.append((panel_proc, "Web Panel"))

    print("✓ All systems started")
    
    try:
        # Keep the main script running while monitoring processes
        while True:
            time.sleep(1)
            
            # Check if any crucial process crashed
            for proc, name in processes:
                if proc.poll() is not None:
                    # Ignore Redis crash if it was just port already in use
                    if name == "Redis Server" and proc.returncode != 0:
                        continue
                    print(f"\n[ERROR] {name} stopped unexpectedly.")
                    sys.exit(1)
                
    except KeyboardInterrupt:
        print("\nShutting down MineNodes...")
        for proc, name in processes:
            proc.terminate()
            
        print("Waiting for graceful shutdown...")
        for proc, name in processes:
            proc.wait()
            
        print("Shutdown complete.")
        sys.exit(0)

if __name__ == "__main__":
    main()
