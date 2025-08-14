# START OF FILE handlers/admin/user_management.py
import logging
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode

import database
from ..helpers import admin_required, escape_markdown, try_edit_message, create_pagination_keyboard

logger = logging.getLogger(__name__)
class State(Enum):
    GET_USER_ID = auto()
    ADJUST_BALANCE_ID = auto()
    ADJUST_BALANCE_AMOUNT = auto()

@admin_required
async def user_profile_card(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    query = update.callback_query
    # FIX: Use the correct function search_user()
    user = database.search_user(str(user_id))
    if not user:
        if query: await query.answer("User not found.", show_alert=True)
        return
    summary, total_balance, _, _, _ = database.get_user_balance_details(user_id)
    total_withdrawn = database.fetch_one("SELECT SUM(amount) as s FROM withdrawals WHERE user_id = ? AND status = 'completed'", (user_id,))['s'] or 0.0
    text = f"""
👤 *User Profile: @{escape_markdown(user.get('username') or 'DELETED')}*
ID: `{user['telegram_id']}`
Status: *{'🟢 Active' if not user['is_blocked'] else '🔴 Blocked'}*
Joined: {escape_markdown(user['join_date'].split(' ')[0])}

*Financials:*
  └─💰 Balance: `${escape_markdown(f'{total_balance:.2f}')}`
  └─💸 Withdrawn: `${escape_markdown(f'{total_withdrawn:.2f}')}`

*Account Stats \\(Total: {sum(summary.values())}\\):*
  └─✅ OK: `{summary.get('ok', 0) + summary.get('restricted', 0)}`
  └─⏳ Pending: `{summary.get('pending_confirmation', 0)}`
  └─🚫 Rejected: `{summary.get('banned', 0) + summary.get('error', 0) + summary.get('limited', 0)}`
    """
    keyboard = [
        [
            InlineKeyboardButton("🚫 Block" if not user['is_blocked'] else "✅ Unblock", callback_data=f"admin_user_toggle_block:{user_id}"),
            InlineKeyboardButton("💰 Adjust Balance", callback_data=f"admin_user_conv_start:ADJUST_BALANCE_ID:{user_id}")
        ],
        [InlineKeyboardButton("🔥 Purge All Data", callback_data=f"admin_system_conv_start:PURGE_USER_ID:{user_id}")],
        [InlineKeyboardButton("⬅️ Back to User Menu", callback_data="admin_users_main")]
    ]
    if query: await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
#... (The rest of the file is correct and unchanged)
#... (I will include it for completeness)
@admin_required
async def users_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    stats = database.get_bot_stats()
    total, blocked = stats.get('total_users', 0), stats.get('blocked_users', 0)
    active = total - blocked
    text = f"""
👥 *User Management*
Total Users: `{total}` • Active: `{active}` • Blocked: `{blocked}`
Select an option below to manage or view users\\.
    """
    keyboard = [
        [InlineKeyboardButton("🔍 Search for User", callback_data="admin_user_conv_start:GET_USER_ID")],
        [InlineKeyboardButton("📋 View All Users", callback_data="admin_users_list_all_1")],
        [InlineKeyboardButton("🥇 View Top Users (by Balance)", callback_data="admin_users_list_top_1")],
        [InlineKeyboardButton("🚫 View Blocked Users", callback_data="admin_users_list_blocked_1")],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")],
    ]
    if query: await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

@admin_required
async def user_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    filter_by, page = parts[3], int(parts[4])
    limit = 10
    if filter_by == 'top':
        users_data = database.get_top_users_by_balance(limit)
        total_users = len(users_data)
        title = "🥇 Top Users by Balance"
        text = f"{title}\n\n"
        if not users_data: text += "No users with a balance found\\."
        else:
            for i, user_data in enumerate(users_data):
                _, balance, _, _, _ = database.get_user_balance_details(user_data['telegram_id'])
                username = f"@{escape_markdown(user_data.get('username') or 'DELETED')}"
                rank = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}\\."
                text += f"{rank} {username} \\- `${escape_markdown(f'{balance:.2f}')}`\n"
    else:
        users, total_users = database.get_all_users(page, limit, filter_by=filter_by)
        title = "📋 All Users" if filter_by == 'all' else "🚫 Blocked Users"
        text = f"{title} \\(Page {page}\\)\n\n"
        if not users: text += "No users found in this category\\."
        else:
            for user in users:
                status = "🔴" if user['is_blocked'] else "🟢"
                username = f"@{escape_markdown(user.get('username') or 'DELETED')}"
                text += f"{status} {username} \\(`{user['telegram_id']}`\\) • Accs: {user['account_count']}\n"
    pagination_prefix = f"admin_users_list_{filter_by}"
    keyboard = create_pagination_keyboard(pagination_prefix, page, total_users, limit) if filter_by != 'top' else []
    keyboard.append([InlineKeyboardButton("⬅️ Back to User Menu", callback_data="admin_users_main")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def toggle_block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = int(query.data.split(':')[1])
    user = database.search_user(str(user_id))
    if not user: await query.answer("User not found!", show_alert=True); return
    if user['is_blocked']:
        database.unblock_user(user_id)
        await query.answer("User Unblocked", show_alert=True)
        database.log_admin_action(update.effective_user.id, "USER_UNBLOCK", f"User: {user_id}")
    else:
        database.block_user(user_id)
        await query.answer("User Blocked", show_alert=True)
        database.log_admin_action(update.effective_user.id, "USER_BLOCK", f"User: {user_id}")
    await user_profile_card(update, context, user_id)

async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(':')
    action = parts[1]
    prompts = {'GET_USER_ID': ("🔍 Enter User ID or @username to search:", State.GET_USER_ID), 'ADJUST_BALANCE_ID': ("💰 Enter User ID to adjust balance for:", State.ADJUST_BALANCE_ID)}
    if action in prompts:
        prompt, state = prompts[action]
        if len(parts) > 2:
            context.user_data['target_user_id'] = int(parts[2])
            await query.message.reply_text(f"Adjusting balance for user `{parts[2]}`\\. Please send the amount to add \\(negative to subtract\\)\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return State.ADJUST_BALANCE_AMOUNT
        await try_edit_message(query, f"{prompt}\n\nType /cancel to abort\\.", None)
        return state
    return ConversationHandler.END

async def handle_get_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    identifier = update.message.text.strip()
    user = database.search_user(identifier)
    if not user:
        await update.message.reply_text("❌ User not found\\. Please try again or /cancel\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.GET_USER_ID
    await user_profile_card(update, context, user['telegram_id'])
    return ConversationHandler.END

async def handle_adjust_balance_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = context.user_data.get('target_user_id')
        if not user_id:
            identifier = update.message.text.strip()
            user_db = database.search_user(identifier)
            if not user_db:
                await update.message.reply_text("❌ User not found\\. Please try again or /cancel\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return State.ADJUST_BALANCE_ID
            user_id = user_db['telegram_id']
        user = database.search_user(str(user_id))
        context.user_data['target_user_id'] = user_id
        _, balance, _, _, _ = database.get_user_balance_details(user_id)
        await update.message.reply_text(f"User @{escape_markdown(user.get('username'))} has a balance of `${escape_markdown(f'{balance:.2f}')}`\\.\n\nEnter amount to add \\(use negative to subtract, e\\.g\\., `-5.50`\\)\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADJUST_BALANCE_AMOUNT
    except (ValueError, KeyError):
        await update.message.reply_text("Invalid ID\\. Please enter a numeric Telegram User ID or @username\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADJUST_BALANCE_ID

async def handle_adjust_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        user_id = context.user_data['target_user_id']
        database.adjust_user_balance(user_id, amount)
        database.log_admin_action(update.effective_user.id, "BALANCE_ADJUST", f"User: {user_id}, Amount: {amount:+.2f}")
        await update.message.reply_text(f"✅ Balance for user `{user_id}` adjusted by `${escape_markdown(f'{amount:+.2f}')}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        await user_profile_card(update, context, user_id)
    except ValueError:
        await update.message.reply_text("Invalid amount\\. Please enter a number like `10.50` or `-5`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADJUST_BALANCE_AMOUNT
    except KeyError:
        await update.message.reply_text("Session expired\\. Please start over\\.", parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data.clear()
    return ConversationHandler.END

async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    class FakeQuery:
        def __init__(self, message): self.message = message
        async def answer(self, *args, **kwargs): pass
        async def edit_message_text(self, *args, **kwargs): await self.message.reply_text(*args, **kwargs)
    await users_main_panel(FakeQuery(update.message), context)
    return ConversationHandler.END

def get_conv_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_starter, pattern=r"^admin_user_conv_start:")],
        states={
            State.GET_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_user_id)],
            State.ADJUST_BALANCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_adjust_balance_id)],
            State.ADJUST_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_adjust_balance_amount)],
        },
        fallbacks=[ CommandHandler('cancel', conv_cancel), ],
        map_to_parent={ ConversationHandler.END: ConversationHandler.END, },
        per_user=True, per_chat=True, allow_reentry=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(users_main_panel, pattern=r"^admin_users_main$"),
        CallbackQueryHandler(user_list_panel, pattern=r"^admin_users_list_"),
        CallbackQueryHandler(toggle_block_user, pattern=r"^admin_user_toggle_block:"),
    ]
# END OF FILE handlers/admin/user_management.py