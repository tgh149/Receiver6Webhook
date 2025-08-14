# START OF FILE handlers/admin/financials.py
import logging
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# FIX: CommandHandler was missing from this import
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode
from datetime import datetime

import database
from ..helpers import admin_required, escape_markdown, try_edit_message, create_pagination_keyboard

logger = logging.getLogger(__name__)

class State(Enum):
    GET_REJECTION_REASON = auto()

# --- Main Panels & Callbacks ---

@admin_required
async def finance_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main financial dashboard."""
    query = update.callback_query
    if query:
        await query.answer()

    stats = database.get_bot_stats()
    pending_count = database.fetch_one("SELECT COUNT(*) as c FROM withdrawals WHERE status = 'pending'")['c']
    
    text = f"""
💰 *Financial Management*

This is the central hub for managing all withdrawals and financial data\\.

*Overview:*
  └─ Pending Requests: `{pending_count}`
  └─ Total Withdrawn: `${escape_markdown(f"{stats.get('total_withdrawals_amount', 0.0):.2f}")}`
    """
    keyboard = [
        [InlineKeyboardButton(f"⏳ View Pending ({pending_count})", callback_data="admin_finance_list_pending_1")],
        [InlineKeyboardButton("📜 View History (Completed)", callback_data="admin_finance_list_completed_1")],
        [InlineKeyboardButton("❌ View History (Rejected)", callback_data="admin_finance_list_rejected_1")],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")],
    ]
    if query:
        await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

@admin_required
async def withdrawal_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a paginated list of withdrawals by status."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split('_')
    status = parts[2]
    page = int(parts[3])
    limit = 5

    withdrawals, total = database.get_all_withdrawals(page, limit, status=status)
    
    status_map = {
        'pending': ('⏳ Pending Withdrawals', '⏳'),
        'completed': ('✅ Completed Withdrawals', '✅'),
        'rejected': ('❌ Rejected Withdrawals', '❌'),
    }
    title, emoji = status_map.get(status, ('Unknown', '❓'))

    text = f"{title} \\(Page {page}\\)\n\n"
    if not withdrawals:
        text += "No withdrawals found in this category\\."
    else:
        for w in withdrawals:
            ts = datetime.fromisoformat(w['timestamp']).strftime('%d-%b-%y %H:%M')
            username = escape_markdown(w.get('username') or f"ID:{w['user_id']}")
            text += f"{emoji} *@{username}* \\(`{w['user_id']}`\\)\n"
            text += f"  └─ Amount: `${escape_markdown(f"{w['amount']:.2f}")}`\n"
            text += f"  └─ Address: `{escape_markdown(w['address'])}`\n"
            text += f"  └─ Date: `{escape_markdown(ts)}`\n"
            if w.get('rejection_reason'):
                text += f"  └─ Reason: _{escape_markdown(w['rejection_reason'])}_\n"
            text += f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"

    pagination_prefix = f"admin_finance_list_{status}"
    keyboard = create_pagination_keyboard(pagination_prefix, page, total, limit)
    keyboard.append([InlineKeyboardButton("⬅️ Back to Financials", callback_data="admin_finance_main")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

# --- In-Channel Actions ---

@admin_required
async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the ✅ Approve button click from the admin channel."""
    query = update.callback_query
    withdrawal_id = int(query.data.split(':')[1])
    admin_user = update.effective_user

    withdrawal, status = database.update_withdrawal_status(withdrawal_id, 'completed', admin_user.id)

    if not withdrawal:
        await query.answer("⚠️ This request has already been processed or does not exist.", show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except Exception:
            pass
        return

    # Notify user
    user_msg = f"✅ Great news\\! Your withdrawal request for `${escape_markdown(f"{withdrawal['amount']:.2f}")}` has been approved and processed\\."
    try:
        await context.bot.send_message(withdrawal['user_id'], user_msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Failed to send approval notification to user {withdrawal['user_id']}: {e}")

    # Edit channel message
    original_text = query.message.text_markdown_v2
    new_text = f"{original_text}\n\n*\\-\\-\\-*\n👍 *Approved by @{escape_markdown(admin_user.username)}*"
    await query.edit_message_text(new_text, parse_mode=ParseMode.MARKDOWN_V2)
    await query.answer("✅ Withdrawal Approved!")

@admin_required
async def handle_reject_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the rejection process by asking for a reason."""
    query = update.callback_query
    withdrawal_id = int(query.data.split(':')[1])

    withdrawal = database.get_withdrawal_by_id(withdrawal_id)
    if not withdrawal or withdrawal['status'] != 'pending':
        await query.answer("⚠️ This request has already been processed or does not exist.", show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except Exception:
            pass
        return

    context.user_data['rejection_flow'] = {
        'withdrawal_id': withdrawal_id,
        'channel_message_id': query.message.message_id,
        'chat_id': query.message.chat_id,
        'original_text': query.message.text_markdown_v2
    }
    
    await query.answer()
    await context.bot.send_message(update.effective_user.id, f"Please provide a brief reason for rejecting the withdrawal for @{escape_markdown(withdrawal['username'])}\\. This will be sent to the user\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return State.GET_REJECTION_REASON


async def handle_rejection_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives the reason, processes the rejection, and cleans up."""
    reason = update.message.text.strip()
    admin_user = update.effective_user
    flow_data = context.user_data.get('rejection_flow', {})

    if not flow_data:
        await update.message.reply_text("Rejection session expired\\. Please try again\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    withdrawal_id = flow_data['withdrawal_id']
    withdrawal, status = database.update_withdrawal_status(withdrawal_id, 'rejected', admin_user.id, reason=reason)

    if not withdrawal:
        await update.message.reply_text(f"Could not process rejection for withdrawal ID {withdrawal_id}\\. It may have already been handled\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    # Notify user
    user_msg = f"⚠️ Your withdrawal request for `${escape_markdown(f"{withdrawal['amount']:.2f}")}` has been rejected\\.\n\n*Reason:* _{escape_markdown(reason)}_"
    try:
        await context.bot.send_message(withdrawal['user_id'], user_msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Failed to send rejection notification to user {withdrawal['user_id']}: {e}")

    # Edit channel message
    new_text = f"{flow_data['original_text']}\n\n*\\-\\-\\-*\n👎 *Rejected by @{escape_markdown(admin_user.username)}*\n📝 *Reason:* {escape_markdown(reason)}"
    try:
        await context.bot.edit_message_text(new_text, chat_id=flow_data['chat_id'], message_id=flow_data['channel_message_id'], parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Failed to edit channel message for rejection: {e}")

    await update.message.reply_text(f"✅ Rejection processed for withdrawal ID {withdrawal_id}\\.", parse_mode=ParseMode.MARKDOWN_V2)
    
    context.user_data.pop('rejection_flow', None)
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

def get_conv_handler():
    # This conversation is only for the rejection reason
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_reject_start, pattern=r"^admin_reject_withdrawal:")],
        states={
            State.GET_REJECTION_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rejection_reason)],
        },
        fallbacks=[CommandHandler('cancel', conv_cancel)],
        conversation_timeout=300,
        per_user=True, per_chat=True,
        allow_reentry=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(finance_main_panel, pattern=r"^admin_finance_main$"),
        CallbackQueryHandler(withdrawal_list_panel, pattern=r"^admin_finance_list_"),
        CallbackQueryHandler(handle_approve, pattern=r"^admin_approve_withdrawal:"),
    ]
# END OF FILE handlers/admin/financials.py