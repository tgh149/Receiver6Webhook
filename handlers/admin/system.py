# START OF FILE handlers/admin/system.py
import logging
import os
import shutil
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode

import database
from ..helpers import admin_required, escape_markdown, try_edit_message, create_pagination_keyboard

logger = logging.getLogger(__name__)

class State(Enum):
    ADD_ADMIN_ID = auto()
    REMOVE_ADMIN_ID = auto()
    PURGE_USER_ID = auto()
    PURGE_CONFIRM = auto()
    FACTORY_RESET_CONFIRM = auto()

# --- Main Panels ---
@admin_required
async def system_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    text = "🔧 *System & Admins*\n\nManage the bot's core data and the administrative team\\."
    keyboard = [
        [InlineKeyboardButton("👑 Admin Management", callback_data="admin_system_admins_main")],
        [InlineKeyboardButton("📜 Admin Activity Log", callback_data="admin_system_log_1")],
        [InlineKeyboardButton("🔥 Purge User Data", callback_data="admin_system_conv_start:PURGE_USER_ID")],
        [InlineKeyboardButton("‼️ FACTORY RESET BOT", callback_data="admin_system_conv_start:FACTORY_RESET_CONFIRM")],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")],
    ]
    if query: await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

@admin_required
async def admin_management_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    if 'admin_usernames' not in context.bot_data: context.bot_data['admin_usernames'] = {}
    admins = database.get_all_admins()
    initial_admin_id = context.bot_data.get('initial_admin_id')
    text = "👑 *Admin Management*\n\n"
    for admin_db in admins:
        admin_id = admin_db['telegram_id']
        username = context.bot_data['admin_usernames'].get(admin_id)
        if not username:
            try:
                chat = await context.bot.get_chat(admin_id)
                username = chat.username or "Unknown"
                context.bot_data['admin_usernames'][admin_id] = username
            except Exception: username = "Fetch Failed"
        flair = "🎖️ Super Admin" if admin_id == initial_admin_id else "🛡️ Admin"
        text += f"\\- @{escape_markdown(username)} \\(`{admin_id}`\\) \\- {flair}\n"
    keyboard = [
        [InlineKeyboardButton("➕ Add Admin", callback_data="admin_system_conv_start:ADD_ADMIN_ID"), InlineKeyboardButton("➖ Remove Admin", callback_data="admin_system_conv_start:REMOVE_ADMIN_ID")],
        [InlineKeyboardButton("⬅️ Back to System Menu", callback_data="admin_system_main")]
    ]
    if query: await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

@admin_required
async def admin_log_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('_')[-1])
    logs, total = database.get_admin_log(page, limit=15)
    text = f"📜 *Admin Activity Log* \\(Page {page}\\)\n\n"
    if not logs:
        text += "No activity recorded yet\\."
    else:
        from datetime import datetime
        for log in logs:
            ts = escape_markdown(datetime.fromisoformat(log['timestamp']).strftime('%Y-%m-%d %H:%M'))
            admin_id = log['admin_id']
            username = context.bot_data.get('admin_usernames', {}).get(admin_id)
            if not username:
                 try:
                    chat = await context.bot.get_chat(admin_id)
                    username = chat.username or f"ID:{admin_id}"
                    context.bot_data.setdefault('admin_usernames', {})[admin_id] = username
                 except Exception: username = f"ID:{admin_id}"
            admin_name = f"@{escape_markdown(username)}"
            text += f"`{ts}`: {admin_name} -> *{escape_markdown(log['action'])}*\n"
            if log['details']:
                text += f"  └─ _{escape_markdown(log['details'])}_\n"
    keyboard = create_pagination_keyboard("admin_system_log", page, total, 15)
    keyboard.append([InlineKeyboardButton("⬅️ Back to System Menu", callback_data="admin_system_main")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def get_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_document(update.effective_chat.id, document=InputFile(database.DB_FILE, filename="bot.db"))

async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(':')
    action = parts[1]
    prompts = {'ADD_ADMIN_ID': ("Enter the Telegram ID of the new admin:", State.ADD_ADMIN_ID), 'REMOVE_ADMIN_ID': ("Enter the Telegram ID of the admin to remove:", State.REMOVE_ADMIN_ID), 'PURGE_USER_ID': ("🔥 Enter the User ID or @username of the user to *PURGE ALL DATA* for:", State.PURGE_USER_ID), 'FACTORY_RESET_CONFIRM': ("⚠️ *DANGER ZONE* ⚠️\n\nThis will *PERMANENTLY DELETE EVERYTHING*\\.\n\nType `I UNDERSTAND THE RISK, RESET ALL DATA` to proceed\\.", State.FACTORY_RESET_CONFIRM)}
    if action in prompts:
        prompt, state = prompts[action]
        if action == 'PURGE_USER_ID' and len(parts) > 2:
            user_id = int(parts[2])
            # FIX: Use the correct function search_user()
            user = database.search_user(str(user_id))
            if user:
                context.user_data['purge_user_id'] = user_id
                await try_edit_message(query, f"You are about to purge all data for @{escape_markdown(user.get('username'))} \\(`{user['telegram_id']}`\\)\\.\n\nThis is irreversible\\.\n\nType `PURGE` to confirm\\.", None)
                return State.PURGE_CONFIRM
        await try_edit_message(query, f"{prompt}\n\nType /cancel to abort\\.", None)
        return state
    return ConversationHandler.END

async def handle_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
        database.add_admin(user_id)
        database.log_admin_action(update.effective_user.id, "ADMIN_ADD", f"Added admin {user_id}")
        await update.message.reply_text(f"✅ User `{user_id}` is now an admin\\.", parse_mode=ParseMode.MARKDOWN_V2)
        await admin_management_panel(update, context)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid ID\\. Please enter a numeric Telegram ID\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_ADMIN_ID

async def handle_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
        if user_id == context.bot_data.get('initial_admin_id'):
            await update.message.reply_text("❌ The initial Super Admin cannot be removed\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return State.REMOVE_ADMIN_ID
        if database.remove_admin(user_id):
            database.log_admin_action(update.effective_user.id, "ADMIN_REMOVE", f"Removed admin {user_id}")
            context.bot_data.get('admin_usernames', {}).pop(user_id, None)
            await update.message.reply_text(f"✅ Admin access for `{user_id}` revoked\\.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text("❌ Admin not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        await admin_management_panel(update, context)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid ID\\. Please enter a numeric Telegram ID\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.REMOVE_ADMIN_ID

async def handle_purge_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    identifier = update.message.text.strip()
    user = database.search_user(identifier)
    if not user:
        await update.message.reply_text("User not found\\. Please try again\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.PURGE_USER_ID
    context.user_data['purge_user_id'] = user['telegram_id']
    await update.message.reply_text(f"You are about to purge all data for @{escape_markdown(user.get('username'))} \\(`{user['telegram_id']}`\\)\\.\n\nThis will delete the user, their balance, all associated accounts, and their session files\\. This is irreversible\\.\n\nType `PURGE` to confirm\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return State.PURGE_CONFIRM

async def handle_purge_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'PURGE':
        user_id = context.user_data['purge_user_id']
        _, files_to_delete = database.purge_user_data(user_id)
        deleted_files = 0
        for f in files_to_delete:
            if f and os.path.exists(f):
                try: os.remove(f); deleted_files += 1
                except Exception as e: logger.error(f"Failed to delete session file on purge: {f} - {e}")
        database.log_admin_action(update.effective_user.id, "USER_PURGE", f"Purged data for user {user_id}")
        await update.message.reply_text(f"🔥 User `{user_id}` purged\\. {deleted_files} session files deleted\\.", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("❌ Purge cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await system_main_panel(update, context)
    context.user_data.clear()
    return ConversationHandler.END

async def handle_factory_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == 'I UNDERSTAND THE RISK, RESET ALL DATA':
        msg = await update.message.reply_text("💥 Initiating factory reset\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        try:
            database.db_lock.acquire()
            if os.path.exists(database.DB_FILE): os.remove(database.DB_FILE)
            if os.path.exists(context.application.bot_data.get('scheduler_db_file', 'scheduler.sqlite')): os.remove(context.application.bot_data.get('scheduler_db_file', 'scheduler.sqlite'))
            if os.path.exists('sessions'): shutil.rmtree('sessions')
            database.db_lock.release()
            await msg.edit_text("✅ Reset complete\\. Please restart the bot process NOW to re\\-initialize the database\\.", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await msg.edit_text(f"❌ An error occurred during reset: {e}\\. Manual intervention may be required\\.", parse_mode=ParseMode.MARKDOWN_V2)
            database.db_lock.release()
    else:
        await update.message.reply_text("❌ Confirmation text did not match\\. Factory reset aborted\\.", parse_mode=ParseMode.MARKDOWN_V2)
        await system_main_panel(update, context)
    context.user_data.clear()
    return ConversationHandler.END

async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await system_main_panel(update, context)
    return ConversationHandler.END

def get_conv_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_starter, pattern=r"^admin_system_conv_start:")],
        states={
            State.ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_admin)],
            State.REMOVE_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_admin)],
            State.PURGE_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_purge_user_id)],
            State.PURGE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_purge_confirm)],
            State.FACTORY_RESET_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_factory_reset)],
        },
        fallbacks=[CommandHandler('cancel', conv_cancel)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END},
        per_user=True, per_chat=True, allow_reentry=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(system_main_panel, pattern=r"^admin_system_main$"),
        CallbackQueryHandler(admin_management_panel, pattern=r"^admin_system_admins_main$"),
        CallbackQueryHandler(admin_log_panel, pattern=r"^admin_system_log_"),
        CallbackQueryHandler(get_db, pattern=r"^admin_system_get_db$"),
    ]
# END OF FILE handlers/admin/system.py