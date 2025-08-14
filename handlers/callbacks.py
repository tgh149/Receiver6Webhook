# START OF FILE handlers/callbacks.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, CommandHandler, filters

from . import commands, login
from .helpers import escape_markdown

logger = logging.getLogger(__name__)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all non-admin, user-facing callback queries."""
    query = update.callback_query
    data = query.data
    
    if data == "noop":
        await query.answer()
        return
        
    try:
        await query.answer()

        # --- Main Navigation ---
        if data == "nav_start":
            from .start import start
            await start(update, context)
            return
        if data == "nav_balance":
            await commands._send_balance_panel(update, context, query=query)
            return
        if data.startswith("cap_page_"):
            page = int(data.split('_')[-1])
            await commands._send_cap_panel(update, context, page=page, query=query)
            return
        if data.startswith("cap_view:"):
            code = data.split(':')[1]
            await commands._send_cap_detail_panel(update, context, code=code, query=query)
            return
        if data == "nav_rules":
            await commands.rules_command(update, context)
            return
        if data == "nav_support":
            # --- New Professional Contact Support Feature ---
            support_id = context.bot_data.get('support_id')
            if support_id and support_id.isdigit():
                support_text = (
                    "Please click the button below to open a direct chat with our support admin\\.\n\n"
                    "You can tap the message below to copy it and start the conversation:"
                )
                suggested_message = "Hello, I need help with the Account Receiver bot."
                keyboard = [
                    # This URL will open a direct chat with the admin
                    [InlineKeyboardButton("💬 Open Chat with Support", url=f"tg://user?id={support_id}")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav_start")]
                ]
                await query.edit_message_text(
                    f"{support_text}\n\n`{escape_markdown(suggested_message)}`",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="MarkdownV2"
                )
            else:
                support_text = context.bot_data.get('support_message', "Support is not configured. Please try again later.")
                await query.edit_message_text(escape_markdown(support_text), parse_mode="MarkdownV2")
            return
        
        # --- Login Flow ---
        if data.startswith("check_account_status:"):
            await login.handle_account_status_check(update, context)
            return
        
    except Exception as e:
        logger.error(f"Error in callback handler for data '{data}': {e}", exc_info=True)
        try:
            await query.answer("❌ An error occurred. Please try again.", show_alert=True)
        except Exception:
            pass

def get_withdrawal_conv_handler():
    """Returns the conversation handler for the withdrawal process."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(commands.withdraw_start, pattern=r"^withdraw_start$")
        ],
        states={
            commands.WAITING_FOR_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, commands.withdraw_get_address)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(commands.withdraw_confirm, pattern=r"^withdraw_confirm$"),
            CallbackQueryHandler(commands.withdraw_start, pattern=r"^withdraw_edit$"),
            CallbackQueryHandler(commands._send_balance_panel, pattern=r"^withdraw_cancel$"),
            CommandHandler('cancel', commands.cancel_operation)
        ],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END,
        },
        per_user=True, per_chat=True, allow_reentry=True,
    )

# END OF FILE handlers/callbacks.py