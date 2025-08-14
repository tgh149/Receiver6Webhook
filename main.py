# main.py (Corrected Final Version)
import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import uvicorn
from fastapi import FastAPI, Request, Response, Header, HTTPException
from rich.logging import RichHandler
from telegram import Update, Bot, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# --- Your Project Imports ---
import database
from config import (
    BOT_TOKEN, INITIAL_ADMIN_ID, SCHEDULER_DB_FILE, SESSION_LOG_CHANNEL_ID,
    ENABLE_SESSION_FORWARDING, CRON_SECRET
)
from handlers import admin, start, commands, login, callbacks, proxy_chat
from handlers.admin import file_manager as admin_file_manager

# --- Configuration for Deployment ---
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8080"))

# --- Logging Setup ---
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
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Your Recurring Jobs ---
async def recurring_account_check_job(bot: Bot):
    logger.info("Cron job: Running periodic account checks...")
    reprocessing_accounts = database.get_accounts_for_reprocessing()
    stuck_accounts = database.get_stuck_pending_accounts()

    if reprocessing_accounts:
        logger.info(f"Cron job: Found {len(reprocessing_accounts)} account(s) for 24h reprocessing.")
        tasks = [login.reprocess_account(bot, acc) for acc in reprocessing_accounts]
        await asyncio.gather(*tasks)

    # --- THIS BLOCK IS THE FIX ---
    if stuck_accounts:
        logger.info(f"Cron job: Found {len(stuck_accounts)} stuck account(s). Re-scheduling initial check.")
        # We re-schedule the original job, just as your polling bot did.
        # This calls the correct function from your login.py
        tasks = [
            login.schedule_initial_check(
                bot_token=BOT_TOKEN,
                user_id_str=str(acc['user_id']),
                chat_id=acc['user_id'],
                phone_number=acc['phone_number'],
                job_id=acc['job_id'],
                prompt_message_id=None  # No message to update in a background job
            ) for acc in stuck_accounts
        ]
        await asyncio.gather(*tasks)
    # --- END OF FIX ---

    if not reprocessing_accounts and not stuck_accounts:
        logger.info("Cron job: No accounts needed attention.")
    logger.info("Cron job: Finished periodic account checks.")

async def daily_cleanup_job():
    logger.info("Cron job: Running daily topic cleanup...")
    database.clear_old_topics()
    logger.info("Cron job: Finished daily topic cleanup.")

# --- FastAPI Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app
    logger.info("[bold blue]Running application startup tasks...[/bold blue]")
    database.init_db()
    logger.info("[green]Database schema checked/initialized.[/green]")
    database.set_setting('session_log_channel_id', str(SESSION_LOG_CHANNEL_ID))
    database.set_setting('enable_session_forwarding', str(ENABLE_SESSION_FORWARDING))
    if INITIAL_ADMIN_ID and database.add_admin(INITIAL_ADMIN_ID):
         logger.info(f"[green]Granted admin privileges to initial admin ID: {INITIAL_ADMIN_ID}[/green]")
         database.log_admin_action(INITIAL_ADMIN_ID, "SYSTEM_INIT", "Initial admin created.")

    ptb_app.bot_data.update(database.get_all_settings())
    ptb_app.bot_data['countries_config'] = database.get_countries_config()
    ptb_app.bot_data['scheduler_db_file'] = SCHEDULER_DB_FILE
    ptb_app.bot_data['initial_admin_id'] = INITIAL_ADMIN_ID
    if not database.get_all_api_credentials():
        default_api_id = ptb_app.bot_data.get('api_id', '25707049')
        default_api_hash = ptb_app.bot_data.get('api_hash', '676a65f1f7028e4d969c628c73fbfccc')
        database.add_api_credential(default_api_id, default_api_hash)

    user_commands = [
        BotCommand("start", "🚀 Start the bot"), BotCommand("balance", "💼 Check your balance"),
        BotCommand("cap", "📋 View available countries & rates"), BotCommand("help", "🆘 Get help & info"),
        BotCommand("rules", "📜 Read the bot rules"), BotCommand("cancel", "❌ Cancel the current operation"),
    ]
    admin_commands = user_commands + [
        BotCommand("admin", "👑 Access Admin Panel"), BotCommand("zip", "⚡ Quick download (new/old sessions)")
    ]
    await ptb_app.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    for admin_user in database.get_all_admins():
        try:
            await ptb_app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_user['telegram_id']))
        except Exception as e:
            logger.warning(f"Could not set commands for admin {admin_user['telegram_id']}: {e}")
    
    await ptb_app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    logger.info(f"[bold green]Webhook set to {WEBHOOK_URL}. Bot is ready.[/bold green]")
    yield
    logger.info("[yellow]Application is shutting down, deleting webhook...[/yellow]")
    await ptb_app.bot.delete_webhook()

ptb_app = ApplicationBuilder().token(BOT_TOKEN).build()

admin_handlers = admin.get_admin_handlers()
admin_handlers.append(CommandHandler("zip", admin_file_manager.zip_command_handler, filters=admin.admin_filter))
ptb_app.add_handlers(admin_handlers, group=0)
support_admin_id_str = database.get_setting('support_id')
if support_admin_id_str and support_admin_id_str.isdigit():
    admin_chat_handler = MessageHandler(filters.User(user_id=int(support_admin_id_str)) & filters.REPLY & ~filters.COMMAND, proxy_chat.reply_to_user_by_reply)
    ptb_app.add_handler(admin_chat_handler, group=1)
withdrawal_handler = callbacks.get_withdrawal_conv_handler()
user_handlers = [
    CommandHandler("start", start.start), CommandHandler("balance", commands.balance_cmd),
    CommandHandler("cap", commands.cap_command), CommandHandler("help", commands.help_command),
    CommandHandler("rules", commands.rules_command), CommandHandler("cancel", commands.cancel_operation),
    CommandHandler("reply", proxy_chat.reply_to_user_by_command), withdrawal_handler,
    CallbackQueryHandler(callbacks.handle_callback_query),
    MessageHandler(filters.TEXT & ~filters.COMMAND, commands.on_text_message),
]
ptb_app.add_handlers(user_handlers, group=2)

app = FastAPI(lifespan=lifespan)

@app.post("/")
async def handle_telegram_update(request: Request):
    try:
        update = Update.de_json(await request.json(), bot=ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return Response(status_code=500)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/cron/{job_name}")
async def cron_endpoint(job_name: str, authorization: str | None = Header(None)):
    if not CRON_SECRET or authorization != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    if job_name == "account-check":
        await recurring_account_check_job(ptb_app.bot)
        return {"status": "account check triggered"}
    if job_name == "daily-cleanup":
        await daily_cleanup_job()
        return {"status": "daily cleanup triggered"}
    raise HTTPException(status_code=404, detail="Job not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
