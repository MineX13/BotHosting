import subprocess
import sys
import time

def main():
    print("Starting MineNodes — Bot Only Mode...")
    
    # Start the Discord Management Bot
    bot_process = subprocess.Popen([sys.executable, "-m", "app.main"])
    print("✓ Discord Bot process started")
    
    try:
        # Keep the main script running while the bot runs
        while True:
            time.sleep(1)
            
            # Check if process crashed
            if bot_process.poll() is not None:
                print("\n[ERROR] Discord Bot stopped unexpectedly.")
                sys.exit(1)
                
    except KeyboardInterrupt:
        print("\nShutting down MineNodes bot...")
        bot_process.terminate()
        
        # Wait for graceful shutdown
        bot_process.wait()
        print("Shutdown complete.")

    sys.exit(0)

if __name__ == "__main__":
    main()
