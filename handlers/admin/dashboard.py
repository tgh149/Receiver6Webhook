# START OF FILE handlers/admin/dashboard.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from datetime import datetime

import database
from ..helpers import admin_required, escape_markdown, try_edit_message

logger = logging.getLogger(__name__)

@admin_required
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main admin navigation panel."""
    query = update.callback_query
    if query:
        await query.answer()

    separator = r'\-' * 25
    text = f"""
👑 *Licensed Administrator Panel* 👑
Status: Active & Operational
{separator}
Select a function from the menu below\\.
    """

    keyboard = [
        [
            InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings_main"),
        ],
        [
            InlineKeyboardButton("👥 User Management", callback_data="admin_users_main"),
            InlineKeyboardButton("🌐 Country Management", callback_data="admin_country_main"),
        ],
        [
            InlineKeyboardButton("🗂️ File Manager", callback_data="admin_fm_main"),
            InlineKeyboardButton("🏦 Session Vault", callback_data="admin_sv_main"),
        ],
        [
            InlineKeyboardButton("💰 Financials", callback_data="admin_finance_main"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast_main"),
        ],
        [InlineKeyboardButton("🔧 System & Admins", callback_data="admin_system_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await try_edit_message(query, text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

@admin_required
async def stats_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the new, merged bot statistics panel."""
    query = update.callback_query
    if query:
        await query.answer()

    stats = database.get_bot_stats()
    settings = context.bot_data

    # User Analytics
    user_text = f"""
🎯 *User Analytics*
  └─👥 Active Users: `{stats.get('total_users', 0) - stats.get('blocked_users', 0)}`
  └─🚫 Blocked Users: `{stats.get('blocked_users', 0)}`
    """

    # Session Statistics
    status_counts = stats.get('accounts_by_status', {})
    session_text = f"""
📈 *Session Statistics*
  └─🔄 Total Sessions: `{stats.get('total_accounts', 0)}`
  └─⏳ Pending Review: `{status_counts.get('pending_confirmation', 0)}`
  └─✅ Verified & Ready: `{status_counts.get('ok', 0)}`
  └─⚠️ Restricted: `{status_counts.get('restricted', 0)}`
  └─🚫 Banned/Error: `{status_counts.get('banned', 0) + status_counts.get('error', 0)}`
  └─📦 Available to Export: `{stats.get('available_sessions', 0)}`
    """

    # System Configuration
    # FIX: Correctly escape minimum withdrawal which may contain a period.
    min_withdraw_str = escape_markdown(settings.get('min_withdraw', '1.0'))
    s_id_str = str(settings.get('support_id', 'Not Set'))
    support_id = escape_markdown(s_id_str if len(s_id_str) < 6 else f"{s_id_str[:4]}...{s_id_str[-2:]}")
    admin_ch_str = str(settings.get('admin_channel', 'Not Set'))
    admin_channel = escape_markdown(admin_ch_str if len(admin_ch_str) < 15 else f"{admin_ch_str[:12]}...")
    
    config_text = f"""
🔧 *System Configuration*
  └─🔕 Spam Check: *{'ON' if settings.get('enable_spam_check') == 'True' else 'OFF'}*
  └─🔐 Default 2FA: *{'SET' if settings.get('two_step_password') else 'NONE'}*
  └─💵 Min Withdrawal: *${min_withdraw_str}*
  └─✅ Account Reception: *{settings.get('add_account_status', 'UNLOCKED')}*
  └─🗣️ Support Admin: `{support_id}`
  └─📢 Admin Channel: `{admin_channel}`
    """

    full_text = f"📊 *Bot Statistics*\n{user_text}\n{session_text}\n{config_text}\n_Last updated: {escape_markdown(datetime.now().strftime('%H:%M:%S %Z'))}_"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await try_edit_message(query, full_text, reply_markup=reply_markup)

# END OF FILE handlers/admin/dashboard.py