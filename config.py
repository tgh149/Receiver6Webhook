# START OF FILE config.py

# Your bot's token from BotFather. This is the only mandatory value.
BOT_TOKEN = "7482708717:AAGBoyi1M5P2Xe9PQ5vM5ErSOmVLZU3ccnI"  # Replace with your bot's token

# The Telegram ID of the user who will be the first super-admin.
INITIAL_ADMIN_ID = 6158106622

# --- NEW: SECRET FOR CRON JOBS ---
# Generate a random, long string for this value.
# It ensures only Vercel/Koyeb can trigger your scheduled tasks.
CRON_SECRET = "your_super_secret_random_string_here_12345" # <-- IMPORTANT: REPLACE THIS

# Filename for the persistent scheduler database (no longer used by the bot directly)
SCHEDULER_DB_FILE = "scheduler.sqlite"

# (Optional) The ID of the Telegram group where session files should be sent.
SESSION_LOG_CHANNEL_ID = -1002528192959 # <<-- IMPORTANT: REPLACE WITH YOUR REAL GROUP ID

# (Optional) Set to True to enable sending session files to the log group.
ENABLE_SESSION_FORWARDING = True