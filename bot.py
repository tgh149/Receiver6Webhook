# main.py
import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response, Header, HTTPException
from rich.logging import RichHandler
from telegram import Update, Bot, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# --- Your Project Imports ---
import database
from config import (
    BOT_TOKEN, INITIAL_ADMIN_ID, SCHEDULER_DB_FILE, SESSION_LOG_CHANNEL_ID,
    ENABLE_SESSION_FORWARDING, CRON_SECRET # NEW: Import a cron secret
)
from handlers import admin, start, commands, login, callbacks, proxy_chat
from handlers.admin import file_manager as admin_file_manager

# --- Configuration for Deployment ---
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8080"))

# --- Logging Setup (Copied from your bot.py) ---
log_level = logging.INFO
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
rich_handler = RichHandler(rich_tracebacks=True, markup=True, show_path=False, log_time_format="[%X]")
root_logger.addHandler(rich_handler)
os.makedirs("logs", exist_ok=True)
file_handler = RotatingFileHandler("logs/bot_activity.log", maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
root_logger.addHandler(file_handler)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- Your Recurring Job (Now callable from a cron endpoint) ---
async def recurring_account_check_job(bot: Bot):
    """This recurring job checks for accounts that need attention."""
    logger.info("Cron job: Running periodic account checks...")
    reprocessing_accounts = database.get_accounts_for_reprocessing()
    stuck_accounts = database.get_stuck_pending_accounts()

    if reprocessing_accounts:
        logger.info(f"Cron job: Found {len(reprocessing_accounts)} account(s) for 24h reprocessing.")
        tasks = [login.reprocess_account(bot, acc) for acc in reprocessing_accounts]
        await asyncio.gather(*tasks)

    if stuck_accounts:
        logger.info(f"Cron job: Found {len(stuck_accounts)} stuck account(s). Retrying initial check.")
        # We need a bot_data-like object for the scheduler jobs
        bot_data = database.get_all_settings()
        
        # In a cron job, we trigger checks immediately, not schedule them for the future
        for acc in stuck_accounts:
             await login.run_confirmation_check(bot, bot_data, acc)

    # The daily topic cleanup logic needs a separate cron job
    # It's better to keep cron jobs focused on a single task.

    if not reprocessing_accounts and not stuck_accounts:
        logger.info("Cron job: No accounts needed attention.")
    logger.info("Cron job: Finished periodic account checks.")


async def daily_cleanup_job():
    """This recurring job cleans old topic data from the database."""
    logger.info("Cron job: Running daily topic cleanup...")
    count = database.clear_old_topics()
    if count > 0:
        logger.info(f"Cron job: Cleared {count} old daily topic records.")
    logger.info("Cron job: Finished daily topic cleanup.")


# --- FastAPI Application Setup ---
# This `lifespan` function replaces your post_init and post_shutdown functions.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown logic for the bot."""
    logger.info("[bold blue]Running application startup tasks...[/bold blue]")

    # --- This is your `post_init` logic ---
    database.init_db()
    logger.info("[green]Database schema checked/initialized.[/green]")

    database.set_setting('session_log_channel_id', str(SESSION_LOG_CHANNEL_ID))
    database.set_setting('enable_session_forwarding', str(ENABLE_SESSION_FORWARDING))
    logger.info("[green]Session forwarding settings synced to database.[/green]")

    if INITIAL_ADMIN_ID:
        if database.add_admin(INITIAL_ADMIN_ID):
             logger.info(f"[green]Granted admin privileges to initial admin ID: {INITIAL_ADMIN_ID}[/green]")
             database.log_admin_action(INITIAL_ADMIN_ID, "SYSTEM_INIT", "Initial admin created.")
        else:
             logger.info(f"[green]Initial admin ID {INITIAL_ADMIN_ID} already exists.[/green]")

    # Load all settings into the bot's context for handlers to use
    ptb_app.bot_data.update(database.get_all_settings())
    ptb_app.bot_data['countries_config'] = database.get_countries_config()
    ptb_app.bot_data['scheduler_db_file'] = SCHEDULER_DB_FILE
    ptb_app.bot_data['initial_admin_id'] = INITIAL_ADMIN_ID
    logger.info("[green]Loaded dynamic settings and country configs into bot context.[/green]")

    if not database.get_all_api_credentials():
        default_api_id = ptb_app.bot_data.get('api_id', '25707049')
        default_api_hash = ptb_app.bot_data.get('api_hash', '676a65f1f7028e4d969c628c73fbfccc')
        database.add_api_credential(default_api_id, default_api_hash)
        logger.info(f"[green]Added default API credential to rotation pool.[/green]")

    # Set bot commands
    user_commands = [
        BotCommand("start", "🚀 Start the bot"), BotCommand("balance", "💼 Check your balance"),
        BotCommand("cap", "📋 View available countries & rates"), BotCommand("help", "🆘 Get help & info"),
        BotCommand("rules", "📜 Read the bot rules"), BotCommand("cancel", "❌ Cancel the current operation"),
    ]
    admin_commands = user_commands + [
        BotCommand("admin", "👑 Access Admin Panel"), BotCommand("zip", "⚡ Quick download (new/old sessions)")
    ]
    await ptb_app.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    logger.info("[green]Default user commands have been set.[/green]")

    admin_count = 0
    for admin_user in database.get_all_admins():
        try:
            await ptb_app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_user['telegram_id']))
            admin_count += 1
        except Exception as e:
            logger.warning(f"Could not set commands for admin {admin_user['telegram_id']}: {e}")
    if admin_count > 0: logger.info(f"[green]Admin-specific commands have been set for {admin_count} admins.[/green]")

    # --- Set Webhook ---
    logger.info(f"Setting webhook to: {WEBHOOK_URL}")
    await ptb_app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    logger.info("[bold green]Webhook has been set. Bot is ready for updates.[/bold green]")
    
    yield  # The application runs here
    
    # --- This is your `post_shutdown` logic ---
    logger.info("[yellow]Application is shutting down, deleting webhook...[/yellow]")
    await ptb_app.bot.delete_webhook()
    logger.info("[yellow]Webhook has been deleted.[/yellow]")


# --- PTB Application Build ---
# We build the application object here so it's globally available
ptb_app = ApplicationBuilder().token(BOT_TOKEN).build()

# --- Register All Your Handlers (Copied from your bot.py) ---
admin_handlers = admin.get_admin_handlers()
admin_handlers.append(CommandHandler("zip", admin_file_manager.zip_command_handler, filters=admin.admin_filter))
ptb_app.add_handlers(admin_handlers, group=0)
logger.info(f"[yellow]Registered {len(admin_handlers)} admin handlers in group 0.[/yellow]")

support_admin_id_str = database.get_setting('support_id') # Fetch from DB
if support_admin_id_str and support_admin_id_str.isdigit():
    support_admin_id = int(support_admin_id_str)
    admin_chat_handler = MessageHandler(
        filters.User(user_id=support_admin_id) & filters.REPLY & ~filters.COMMAND,
        proxy_chat.reply_to_user_by_reply
    )
    ptb_app.add_handler(admin_chat_handler, group=1)
    logger.info("[yellow]Registered admin P2P reply handler in group 1.[/yellow]")

withdrawal_handler = callbacks.get_withdrawal_conv_handler()
user_handlers = [
    CommandHandler("start", start.start),
    CommandHandler("balance", commands.balance_cmd),
    CommandHandler("cap", commands.cap_command),
    CommandHandler("help", commands.help_command),
    CommandHandler("rules", commands.rules_command),
    CommandHandler("cancel", commands.cancel_operation),
    CommandHandler("reply", proxy_chat.reply_to_user_by_command),
    withdrawal_handler,
    CallbackQueryHandler(callbacks.handle_callback_query),
    MessageHandler(filters.TEXT & ~filters.COMMAND, commands.on_text_message),
]
ptb_app.add_handlers(user_handlers, group=2)
logger.info(f"[yellow]Registered {len(user_handlers)} user handlers in group 2.[/yellow]")

# --- FastAPI App ---
app = FastAPI(lifespan=lifespan)

@app.post("/")
async def handle_telegram_update(request: Request):
    """Main webhook endpoint. This is where Telegram sends all updates."""
    try:
        update_data = await request.json()
        update = Update.de_json(data=update_data, bot=ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return Response(status_code=500)

@app.get("/health")
async def health_check():
    """A simple endpoint to confirm the server is running."""
    return {"status": "ok"}

# --- NEW: Secure Cron Job Endpoints ---
@app.post("/cron/account-check")
async def cron_account_check_endpoint(authorization: str | None = Header(None)):
    """Endpoint for Vercel/Koyeb Cron Job to trigger the account check."""
    if not CRON_SECRET or authorization != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    await recurring_account_check_job(ptb_app.bot)
    return {"status": "account check triggered"}

@app.post("/cron/daily-cleanup")
async def cron_daily_cleanup_endpoint(authorization: str | None = Header(None)):
    """Endpoint for Vercel/Koyeb Cron Job to trigger the daily cleanup."""
    if not CRON_SECRET or authorization != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    await daily_cleanup_job()
    return {"status": "daily cleanup triggered"}

# --- Main entry point for local development ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set.")
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set. Local testing requires a tool like ngrok.")

    uvicorn.run(app, host="0.0.0.0", port=PORT)