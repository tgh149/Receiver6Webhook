# START OF FILE handlers/admin/file_manager.py
import logging
import asyncio
import os
import re
import zipfile
import json
import tempfile
import shutil
from enum import Enum, auto
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode

import database
from ..helpers import admin_required, escape_markdown, try_edit_message

logger = logging.getLogger(__name__)

class State(Enum):
    GET_CUSTOM_AMOUNT = auto()

# --- Command Handler for /zip ---
@admin_required
async def zip_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /zip command for quick downloads."""
    args = context.args
    chat = update.effective_chat

    # --- FIX: Removed 'tdata' from usage text as it's not implemented. ---
    usage_text = (
        "⚡️ *Quick Download Command Usage:*\n\n"
        "`/zip <source> <format> <country> <amount> <category>`\n\n"
        "*Examples:*\n"
        "• `/zip new sessions +95 20 free`\n"
        "• `/zip old json +1 50 limit`\n\n"
        "*Arguments:*\n"
        "\\- `source`: `new` or `old`\n"
        "\\- `format`: `sessions`, `json`\n"
        "\\- `country`: e\\.g\\., `+95`\n"
        "\\- `amount`: a number or `all`\n"
        "\\- `category`: `free`, `register`, `limit`"
    )

    if len(args) != 5:
        await chat.send_message(usage_text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    source, export_format, code, amount_str, category_key = [arg.lower() for arg in args]

    # --- Argument Validation ---
    valid_sources = ['new', 'old']
    if source not in valid_sources:
        await chat.send_message(f"❌ Invalid source\\. Use `new` or `old`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    export_status_arg = 'unexported' if source == 'new' else 'exported'

    valid_formats = ['sessions', 'json']
    if export_format not in valid_formats:
        await chat.send_message(f"❌ Invalid format\\. Use one of: `{', '.join(valid_formats)}`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    country = database.get_country_by_code(code)
    if not country:
        await chat.send_message(f"❌ Country with code `{escape_markdown(code)}` not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    if amount_str != 'all' and not amount_str.isdigit():
        await chat.send_message("❌ Amount must be a number or `all`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    limit = None if amount_str == 'all' else int(amount_str)

    category_map = {'free': ['ok'], 'register': ['restricted'], 'limit': ['limited', 'banned']}
    if category_key not in category_map:
        await chat.send_message(f"❌ Invalid category\\. Use one of: `{', '.join(category_map.keys())}`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    statuses = category_map[category_key]

    accounts = database.get_sessions_by_country_and_statuses(
        country_code=code, statuses=statuses, limit=limit, export_status=export_status_arg
    )

    if not accounts:
        await chat.send_message(f"✅ No `{source}` sessions found for category `{category_key}` in {country['name']}\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    msg = await chat.send_message(f"⏳ Preparing your `{export_format}` file for *{len(accounts)}* `{source}` sessions\\.\\.\\. Please wait\\.", parse_mode=ParseMode.MARKDOWN_V2)

    try:
        if export_format == 'sessions':
            await export_sessions_as_zip(context, chat.id, accounts)
        elif export_format == 'json':
            await export_as_json(context, chat.id, accounts)
        
        log_action = "SESSIONS_EXPORT_CMD"
        if source == 'new':
            account_ids = [acc['id'] for acc in accounts]
            database.mark_accounts_as_exported(account_ids)
        else:
            log_action = "SESSIONS_REDOWNLOAD_CMD"

        database.log_admin_action(update.effective_user.id, log_action, f"/zip {' '.join(args)}")
        await msg.delete()
    except Exception as e:
        logger.error(f"Failed to export sessions via /zip command: {e}", exc_info=True)
        await msg.edit_text(f"❌ An error occurred during export: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

# --- Main UI Panels ---
@admin_required
async def file_manager_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    countries = database.get_countries_config()
    text = "🗂️ *File Manager \\(Downloader\\)*\n\nSelect a country to export sessions from\\. All countries with any sessions are shown\\."
    keyboard = []
    countries_with_sessions = [c for c in countries.values() if database.fetch_one("SELECT 1 FROM accounts WHERE phone_number LIKE ?", (f"{c['code']}%",))]
    if countries_with_sessions:
        country_buttons = [InlineKeyboardButton(f"{c.get('flag','')} {c.get('name')}", callback_data=f"admin_fm_country:{c['code']}") for c in sorted(countries_with_sessions, key=lambda x: x['name'])]
        keyboard.extend([country_buttons[i:i + 2] for i in range(0, len(country_buttons), 2)])
    else:
        text += "\n\n_No sessions available for any country\\._"
    keyboard.append([InlineKeyboardButton("💾 Download Database", callback_data="admin_system_get_db")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")])
    if query: await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

@admin_required
async def country_source_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split(':')[1]
    country = database.get_country_by_code(code)
    if not country:
        await query.answer("Country not found!", show_alert=True)
        return
    context.user_data['fm_country_code'] = code
    unexported_count = database.fetch_one("SELECT COUNT(*) as c FROM accounts WHERE phone_number LIKE ? AND exported_at IS NULL", (f"{code}%",))['c']
    exported_count = database.fetch_one("SELECT COUNT(*) as c FROM accounts WHERE phone_number LIKE ? AND exported_at IS NOT NULL", (f"{code}%",))['c']
    text = f"*{country.get('flag','')} {escape_markdown(country['name'])} Downloads*\n\nChoose which pool of sessions you want to download from\\."
    keyboard = []
    if unexported_count > 0:
        keyboard.append([InlineKeyboardButton(f"🆕 New Sessions ({unexported_count})", callback_data=f"admin_fm_source:new")])
    if exported_count > 0:
        keyboard.append([InlineKeyboardButton(f"💾 Re-download Exported ({exported_count})", callback_data=f"admin_fm_source:exported")])
    if not keyboard:
        text += "\n\n_No sessions available for this country\\._"
    keyboard.append([InlineKeyboardButton("⬅️ Back to Country List", callback_data="admin_fm_main")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def source_category_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source = query.data.split(':')[1]
    code = context.user_data['fm_country_code']
    context.user_data['fm_source'] = source
    if source == 'new':
        counts = {s['status']: s['count'] for s in database.get_country_account_counts_by_status(code)}
        title = "🆕 New Sessions"
    else:
        counts = {s['status']: s['count'] for s in database.get_country_exported_account_counts_by_status(code)}
        title = "💾 Exported Sessions"
    text = f"*{title}*\n\nSelect a category to download\\."
    keyboard = []
    status_map = {'ok': '✅ Free (OK)', 'restricted': '⚠️ Register (Restricted)', 'limit': '🚫 Limit (Banned/Limited)'}
    status_groups = {'ok': ['ok'], 'restricted': ['restricted'], 'limit': ['limited', 'banned']}
    for key, display_name in status_map.items():
        count = sum(counts.get(s, 0) for s in status_groups[key])
        if count > 0:
            keyboard.append([InlineKeyboardButton(f"{display_name}: {count}", callback_data=f"admin_fm_category:{key}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_fm_country:{code}")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def category_amount_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_key = query.data.split(':')[1]
    code = context.user_data['fm_country_code']
    source = context.user_data['fm_source']
    if source == 'new':
        counts = {s['status']: s['count'] for s in database.get_country_account_counts_by_status(code)}
    else:
        counts = {s['status']: s['count'] for s in database.get_country_exported_account_counts_by_status(code)}
    status_groups = {'ok': (['ok'], "✅ Free \\(OK\\)"), 'restricted': (['restricted'], "⚠️ Register \\(Restricted\\)"), 'limit': (['limited', 'banned'], "🚫 Limit \\(Banned/Limited\\)")}
    statuses, title = status_groups.get(category_key, ([], "Unknown"))
    total = sum(counts.get(s, 0) for s in statuses)
    context.user_data['fm_category_key'] = category_key
    text = f"*{title}*\n\nAvailable sessions: `{total}`\n\n1\\. *Choose Amount:*"
    keyboard = []
    amounts = [10, 50, 100, 500, 1000]
    row_buttons = [InlineKeyboardButton(f"{amt}", callback_data=f"admin_fm_set_amount:{amt}") for amt in amounts if total >= amt]
    if row_buttons:
        keyboard.extend([row_buttons[i:i + 3] for i in range(0, len(row_buttons), 3)])
    keyboard.append([InlineKeyboardButton("Download All", callback_data="admin_fm_set_amount:all"), InlineKeyboardButton("🔢 Custom Amount", callback_data="admin_fm_conv_start:GET_CUSTOM_AMOUNT")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_fm_source:{source}")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def set_amount_and_show_formats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount_str = query.data.split(':')[1]
    context.user_data['fm_amount'] = int(amount_str) if amount_str.isdigit() else 'all'
    amount_text = "All" if amount_str == "all" else amount_str
    text = f"Amount set to: *{amount_text} sessions*\\.\n\n2\\. *Choose Export Format:*"
    category_key = context.user_data['fm_category_key']
    keyboard = [
        [InlineKeyboardButton(".zip (Session Files)", callback_data=f"admin_fm_export:sessions")],
        [InlineKeyboardButton(".zip (JSON Files)", callback_data=f"admin_fm_export:json")],
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_fm_category:{category_key}")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

# --- Export Logic ---
@admin_required
async def export_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    export_format = query.data.split(':')[1]
    code = context.user_data['fm_country_code']
    source = context.user_data['fm_source']
    category_key = context.user_data['fm_category_key']
    amount = context.user_data['fm_amount']
    status_groups = { 'ok': ['ok'], 'restricted': ['restricted'], 'limit': ['limited', 'banned'] }
    statuses = status_groups.get(category_key, [])
    export_status_arg = 'unexported' if source == 'new' else 'exported'
    limit = None if amount == 'all' else amount
    accounts = database.get_sessions_by_country_and_statuses(code, statuses, limit=limit, export_status=export_status_arg)
    if not accounts:
        await query.message.reply_text("No sessions found to export for this selection\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    msg = await query.message.reply_text(f"⏳ Preparing your `{export_format}` file for *{len(accounts)}* sessions\\.\\.\\. Please wait\\.", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        if export_format == 'sessions':
            await export_sessions_as_zip(context, query.from_user.id, accounts)
        elif export_format == 'json':
            await export_as_json(context, query.from_user.id, accounts)
        if source == 'new':
            account_ids = [acc['id'] for acc in accounts]
            database.mark_accounts_as_exported(account_ids)
            database.log_admin_action(query.from_user.id, "SESSIONS_EXPORT", f"{len(accounts)} sessions, {code}, {category_key}, {export_format}")
        else:
            database.log_admin_action(query.from_user.id, "SESSIONS_RE-DOWNLOAD", f"{len(accounts)} sessions, {code}, {category_key}, {export_format}")
        await msg.delete()
    except Exception as e:
        logger.error(f"Failed to export sessions: {e}", exc_info=True)
        await msg.edit_text(f"❌ An error occurred during export: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

async def export_sessions_as_zip(context, chat_id, accounts):
    """Creates a zip file of .session files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = os.path.join(temp_dir, f"sessions_export_{len(accounts)}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                session_path = acc.get('session_file')
                if not session_path or not os.path.exists(session_path):
                    logger.warning(f"Session file for {acc['phone_number']} not found. Skipping.")
                    continue
                zf.write(session_path, os.path.basename(session_path))
        caption = f"📦 *Session Files Export*\n\nContains `{len(accounts)}` accounts\\."
        with open(zip_path, 'rb') as f:
            await context.bot.send_document(chat_id, document=f, filename=os.path.basename(zip_path), caption=caption, parse_mode=ParseMode.MARKDOWN_V2)

async def export_as_json(context, chat_id, accounts):
    """Creates a zip file containing one .json file for each session."""
    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = os.path.join(temp_dir, f"json_export_{len(accounts)}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                phone_no_plus = acc['phone_number'].lstrip('+')
                json_filename = f"{phone_no_plus}.json"
                app_id = context.bot_data.get('api_id', 2024)
                app_hash = context.bot_data.get('api_hash', 'b18441a1ff607e10a989891a5462e627')
                metadata = {
                    "session_file": os.path.basename(acc.get('session_file', f"{phone_no_plus}.session")),
                    "phone": phone_no_plus, "app_id": int(app_id), "app_hash": app_hash, "sdk": "Windows 11",
                    "app_version": "5.12.3 x64", "device": "MS-7549", "device_model": "MS-7549", "lang_pack": "tdesktop",
                    "system_lang_pack": "en-US", "user_id": 1000000000, "username": "TG", "ipv6": False,
                    "first_name": "Telegram", "last_name": "", "register_time": int(datetime.fromisoformat(acc['reg_time']).timestamp()),
                    "sex": None, "last_check_time": int(datetime.now().timestamp()), "device_token": "FIREBASE_FAILED",
                    "lang_code": "en", "tz_offset": 28800, "perf_cat": 2, "avatar": "img/default.png", "proxy": None,
                    "twoFA": "", "password": "", "block": acc['status'] == 'banned', "package_id": "", "installer": "",
                    "system_lang_code": "en-US", "email": "", "email_id": "", "secret": "", "category": ""
                }
                zf.writestr(json_filename, json.dumps(metadata, indent=4))
        caption = f"📄 *JSON Metadata Export*\n\nContains data for `{len(accounts)}` accounts\\."
        with open(zip_path, 'rb') as f:
            await context.bot.send_document(chat_id, document=f, filename=os.path.basename(zip_path), caption=caption, parse_mode=ParseMode.MARKDOWN_V2)

# --- Conversation Handler for Custom Amount ---
async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await try_edit_message(query, "🔢 Please enter the custom amount of sessions you want to download\\.", None)
    return State.GET_CUSTOM_AMOUNT

async def handle_get_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        if amount <= 0: raise ValueError
        context.user_data['fm_amount'] = amount
        amount_text = str(amount)
        text = f"Amount set to: *{amount_text} sessions*\\.\n\n2\\. *Choose Export Format:*"
        category_key = context.user_data['fm_category_key']
        keyboard = [
            [InlineKeyboardButton(".zip (Session Files)", callback_data=f"admin_fm_export:sessions")],
            [InlineKeyboardButton(".zip (JSON Files)", callback_data=f"admin_fm_export:json")],
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_fm_category:{category_key}")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid amount\\. Please enter a positive number\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.GET_CUSTOM_AMOUNT

async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    # Re-create a fake update object to call the panel function
    class FakeUpdate:
        def __init__(self, message):
            self.message = message
            self.callback_query = None
    await file_manager_main(FakeUpdate(update.message), context)
    return ConversationHandler.END

def get_conv_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_starter, pattern=r"^admin_fm_conv_start:")],
        states={State.GET_CUSTOM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_custom_amount)],},
        fallbacks=[CommandHandler('cancel', conv_cancel)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END},
        per_user=True, per_chat=True, allow_reentry=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(file_manager_main, pattern=r"^admin_fm_main$"),
        CallbackQueryHandler(country_source_panel, pattern=r"^admin_fm_country:"),
        CallbackQueryHandler(source_category_panel, pattern=r"^admin_fm_source:"),
        CallbackQueryHandler(category_amount_panel, pattern=r"^admin_fm_category:"),
        CallbackQueryHandler(set_amount_and_show_formats, pattern=r"^admin_fm_set_amount:"),
        CallbackQueryHandler(export_sessions, pattern=r"^admin_fm_export:"),
    ]