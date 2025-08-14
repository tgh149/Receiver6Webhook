# START OF FILE handlers/admin/session_vault.py
import logging
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

import database
from ..helpers import admin_required, escape_markdown, try_edit_message, create_advanced_pagination
from .. import login # Import the login module to access the confirmation logic

logger = logging.getLogger(__name__)

# --- Main Panels ---

@admin_required
async def session_vault_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main Session Vault panel for selecting a country."""
    query = update.callback_query
    if query:
        await query.answer()

    countries = database.get_countries_config()
    text = "🏦 *Session Vault \\(Viewer\\)*\n\nSelect a country to inspect its sessions\\. All sessions, including exported ones, are visible here\\."
    
    keyboard = []
    countries_with_accounts = [
        c for c in countries.values() 
        if database.fetch_one("SELECT 1 FROM accounts WHERE phone_number LIKE ?", (f"{c['code']}%",))
    ]

    if countries_with_accounts:
        country_buttons = [
            InlineKeyboardButton(f"{c.get('flag','')} {c.get('name')}", callback_data=f"admin_sv_country:{c['code']}")
            for c in sorted(countries_with_accounts, key=lambda x: x['name'])
        ]
        keyboard.extend([country_buttons[i:i + 2] for i in range(0, len(country_buttons), 2)])
    else:
        text += "\n\n_No sessions found for any country\\._"

    keyboard.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")])
    
    if query:
        await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)


@admin_required
async def country_status_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows session status categories for a selected country."""
    query = update.callback_query
    await query.answer()

    code = query.data.split(':')[1]
    country = database.get_country_by_code(code)
    if not country:
        await query.answer("Country not found!", show_alert=True)
        return

    context.user_data['sv_country_code'] = code
    all_counts = database.fetch_all("SELECT status, COUNT(*) as count FROM accounts WHERE phone_number LIKE ? GROUP BY status", (f"{code}%",))
    counts = {s['status']: s['count'] for s in all_counts}
    
    # --- NEW: Check for stuck sessions ---
    _, stuck_total = database.get_paginated_stuck_accounts_by_country(code, 1, 1)

    status_order = ['ok', 'restricted', 'pending_confirmation', 'limited', 'banned', 'error', 'withdrawn']
    
    text = f"*{country.get('flag','')} {escape_markdown(country['name'])} Session Vault*\n\nSelect a status category to view the list of sessions\\."
    keyboard = []

    if stuck_total > 0:
        keyboard.append([InlineKeyboardButton(f"🚨 Stuck Sessions ({stuck_total})", callback_data=f"admin_sv_stucklist:{code}_1")])
    
    for status in status_order:
        if counts.get(status, 0) > 0:
            keyboard.append([InlineKeyboardButton(f"{status.replace('_', ' ').title()}: {counts[status]} sessions", callback_data=f"admin_sv_list:{status}_1")])

    keyboard.append([InlineKeyboardButton("⬅️ Back to Session Vault", callback_data="admin_sv_main")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))


@admin_required
async def session_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a paginated list of sessions for a given country and status."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(':')[-1].split('_')
    status = '_'.join(parts[:-1])
    page = int(parts[-1])
    code = context.user_data.get('sv_country_code')

    if not code:
        await query.answer("Session expired, please go back.", show_alert=True)
        return

    sessions, total_items = database.get_paginated_sessions_by_country_and_status(code, status, page, limit=10)
    
    title = f"🏦 *{status.replace('_', ' ').title()} Sessions* \\(Page {page}\\)"
    text = f"{title}\n\n"
    
    if not sessions:
        text += "No sessions in this category\\."
    else:
        from datetime import datetime
        for s in sessions:
            reg_date = datetime.fromisoformat(s['reg_time']).strftime('%Y-%m-%d')
            text += f"📱 `{escape_markdown(s['phone_number'])}`\n"
            text += f"  ├─ User ID: `{s['user_id']}`\n"
            text += f"  └─ Added: `{escape_markdown(reg_date)}`\n"

    prefix = f"admin_sv_list:{status}"
    keyboard = create_advanced_pagination(prefix, page, total_items, 10)
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_sv_country:{code}")])
    
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

# --- NEW: Handlers for Stuck Sessions ---

@admin_required
async def stuck_session_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a paginated list of STUCK sessions with force confirm buttons."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(':')[-1].split('_')
    code = parts[0]
    page = int(parts[1])
    context.user_data['sv_country_code'] = code # Ensure code is in context for back button

    sessions, total_items = database.get_paginated_stuck_accounts_by_country(code, page, limit=5)
    
    title = f"🚨 *Stuck Sessions* \\(Page {page}\\)"
    text = f"{title}\n\n"
    
    keyboard = []
    if not sessions:
        text += "No stuck sessions found for this country\\."
    else:
        from datetime import datetime
        for s in sessions:
            reg_date = datetime.fromisoformat(s['reg_time']).strftime('%Y-%m-%d')
            text += f"📱 `{escape_markdown(s['phone_number'])}`\n"
            text += f"  ├─ User ID: `{s['user_id']}`\n"
            text += f"  └─ Added: `{escape_markdown(reg_date)}`\n\n"
            keyboard.append([InlineKeyboardButton("✅ Force Confirm", callback_data=f"admin_sv_forceconfirm:{s['id']}_{page}")])

    pagination_prefix = f"admin_sv_stucklist:{code}"
    keyboard.extend(create_advanced_pagination(pagination_prefix, page, total_items, 5))
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_sv_country:{code}")])
    
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def force_confirm_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the 'Force Confirm' button."""
    query = update.callback_query
    admin_user_id = update.effective_user.id
    
    parts = query.data.split(':')[-1].split('_')
    account_id = int(parts[0])
    page_to_return_to = int(parts[1])

    await query.answer("Processing confirmation...")

    account = database.find_account_by_id(account_id)
    if not account or account['status'] != 'pending_confirmation':
        await query.message.reply_text("❌ This account is no longer stuck or has already been processed.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # Call the reusable confirmation logic
    await login.run_confirmation_check(context.bot, context.bot_data, account)
    
    database.log_admin_action(admin_user_id, "FORCE_CONFIRM", f"Admin forced confirmation for account ID {account_id}")

    await query.message.reply_text(f"✅ Confirmation process triggered for `{escape_markdown(account['phone_number'])}`\\. The user will be notified of the result.", parse_mode=ParseMode.MARKDOWN_V2)
    
    # Refresh the stuck list view
    context.user_data['sv_country_code'] = next((c['code'] for c in database.get_countries_config().values() if account['phone_number'].startswith(c['code'])), None)
    query.data = f"admin_sv_stucklist:{context.user_data['sv_country_code']}_{page_to_return_to}"
    await stuck_session_list_panel(update, context)


# --- Handler Registration ---
def get_callback_handlers():
    return [
        CallbackQueryHandler(session_vault_main, pattern=r"^admin_sv_main$"),
        CallbackQueryHandler(country_status_panel, pattern=r"^admin_sv_country:"),
        CallbackQueryHandler(session_list_panel, pattern=r"^admin_sv_list:"),
        # Add new handlers
        CallbackQueryHandler(stuck_session_list_panel, pattern=r"^admin_sv_stucklist:"),
        CallbackQueryHandler(force_confirm_session, pattern=r"^admin_sv_forceconfirm:"),
    ]
# END OF FILE handlers/admin/session_vault.py