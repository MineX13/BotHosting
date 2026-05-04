import discord
import os

# Load configuration from environment variables
TOKEN = os.getenv("BOT_TOKEN")

# Optional: Set a specific channel ID (comma-separated if multiple, or single).
# If left empty, it will reply in any channel.
ALLOWED_CHANNEL_ID = os.getenv("ALLOWED_CHANNEL_ID", "")

class MySimpleBot(discord.Client):
    async def on_ready(self):
        print(f'✅ Logged in as {self.user} (ID: {self.user.id})')
        print(f'Listening for "hi"...')

    async def on_message(self, message):
        # Ignore messages sent by the bot itself
        if message.author.id == self.user.id:
            return

        # Check if the message is in an allowed channel 
        # (Skip this check if no channel is set in the environment)
        if ALLOWED_CHANNEL_ID:
            # We convert it to string because environment variables are always strings
            if str(message.channel.id) != ALLOWED_CHANNEL_ID:
                return

        # Check if the user said "hi" (case insensitive)
        if message.content.lower().strip() == "hi":
            await message.reply("hello!")

# Set up the bot with necessary intents
intents = discord.Intents.default()
intents.message_content = True  # Required to read message text

bot = MySimpleBot(intents=intents)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Error: BOT_TOKEN environment variable not found.")
    else:
        bot.run(TOKEN)
