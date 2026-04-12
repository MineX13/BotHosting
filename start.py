import subprocess
import sys
import time

def main():
    print("Starting MineNodes — Bot and Panel...")
    
    # Start the Discord Management Bot
    bot_process = subprocess.Popen([sys.executable, "-m", "app.main"])
    print("✓ Discord Bot process started")
    
    # Start the Website Backend API
    panel_process = subprocess.Popen([sys.executable, "-m", "panel.main"])
    print("✓ Web Panel process started")
    
    try:
        # Keep the main script running while the subprocesses run
        while True:
            time.sleep(1)
            
            # Check if either process crashed
            if bot_process.poll() is not None:
                print("\n[ERROR] Discord Bot stopped unexpectedly.")
                panel_process.terminate()
                sys.exit(1)
                
            if panel_process.poll() is not None:
                print("\n[ERROR] Web Panel stopped unexpectedly.")
                bot_process.terminate()
                sys.exit(1)
                
    except KeyboardInterrupt:
        print("\nShutting down both MineNodes services...")
        bot_process.terminate()
        panel_process.terminate()
        
        # Wait for graceful shutdown
        bot_process.wait()
        panel_process.wait()
        print("Shutdown complete.")

    sys.exit(0)

if __name__ == "__main__":
    main()
