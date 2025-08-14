# START OF FILE handlers/commands.py
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError

import database
from . import login, proxy_chat
from .helpers import escape_markdown

logger = logging.getLogger(__name__)

# --- State for conversation handlers ---
WAITING_FOR_ADDRESS = 1

# --- User-Facing Command Handlers ---

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /balance command, showing the new dashboard."""
    await _send_balance_panel(update, context)

async def cap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /cap command, starting the interactive country explorer."""
    await _send_cap_panel(update, context, page=1)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /help command."""
    help_text = escape_markdown(context.bot_data.get('help_message', "Help message not set."))
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /rules command."""
    rules_text = escape_markdown(context.bot_data.get('rules_message', "Rules not set."))
    keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav_start")]]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            rules_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.message.reply_text(
            rules_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )


async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels any ongoing user operation like login or withdrawal."""
    if 'login_flow' in context.user_data:
        await login.cleanup_login_flow(context)
    context.user_data.clear()
    
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    from .start import start
    await start(update, context)
    return ConversationHandler.END


# --- Panel Generators (called by commands and callbacks) ---

async def _send_balance_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    """Generates and sends the user's financial dashboard."""
    user_id = update.effective_user.id
    summary, total_balance, _, _, _ = database.get_user_balance_details(user_id)

    separator = r'\-' * 25
    text = f"""
💼 *Your Financial Dashboard*

💰 Total Withdrawable Balance: `${escape_markdown(f'{total_balance:.2f}')}`

*Account Status \\(Total: {sum(summary.values())}\\)*
✅ Verified & Paid: `{summary.get('ok', 0) + summary.get('restricted', 0)}`
⏳ Pending Review: `{summary.get('pending_confirmation', 0)}`
❌ Rejected/Banned: `{summary.get('banned', 0) + summary.get('error', 0) + summary.get('limited', 0)}`
{separator}
    """
    keyboard = [
        [InlineKeyboardButton("💸 Withdraw Funds", callback_data="withdraw_start")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_start")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_sender = query.edit_message_text if query else update.message.reply_text
    await message_sender(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

async def _send_cap_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, page=1, query=None):
    """Generates and sends the interactive country explorer main menu."""
    countries = list(sorted(database.get_countries_config().values(), key=lambda x: x['name']))
    
    text = "📋 *Supported Countries & Rates*\n\nPlease select a country to view its detailed rates and capacity\\."
    
    limit = 8
    offset = (page - 1) * limit
    paginated_countries = countries[offset : offset + limit]
    
    buttons = [
        InlineKeyboardButton(f"{c.get('flag','')} {c.get('name')}", callback_data=f"cap_view:{c['code']}")
        for c in paginated_countries
    ]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    total_pages = (len(countries) + limit - 1) // limit
    if total_pages > 1:
        row = []
        if page > 1: row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"cap_page_{page-1}"))
        row.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop"))
        if page < total_pages: row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cap_page_{page+1}"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_sender = query.edit_message_text if query else update.message.reply_text
    await message_sender(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)


async def _send_cap_detail_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, query=None):
    """Generates and sends the detailed card for a single country."""
    country = database.get_country_by_code(code)
    if not country:
        await query.answer("Country not found!", show_alert=True)
        return
    
    capacity = country.get('capacity', -1)
    if capacity != -1:
        current_count = database.get_country_account_count(code)
        is_full = current_count >= capacity
        status_text = "❌ Temporarily Full" if is_full else "✅ Accepting New Accounts"
        
        percentage = (current_count / capacity) * 100 if capacity > 0 else 0
        filled_blocks = int(percentage / 10)
        empty_blocks = 10 - filled_blocks
        bar_color = "🔴" if percentage > 85 else "🟡" if percentage > 60 else "🟢"
        capacity_bar = f"{bar_color}" + "█" * filled_blocks + "─" * empty_blocks
        capacity_details = f"📦 Usage: `{capacity_bar}` {current_count}/{capacity} \\({int(percentage)}%\\)"
    else:
        status_text = "✅ Accepting New Accounts"
        capacity_details = "📦 Usage: `Unlimited`"

    text = f"""
{country.get('flag','')} *{escape_markdown(country['name'])} \\| Details & Rates*

{status_text}

*Rates*
💰 OK Account: `${escape_markdown(f"{country.get('price_ok', 0.0):.2f}")}`
⚠️ Restricted Account: `${escape_markdown(f"{country.get('price_restricted', 0.0):.2f}")}`

*Capacity*
{capacity_details}
    """
    keyboard = [[InlineKeyboardButton("⬅️ Back to Country List", callback_data="cap_page_1")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_sender = query.edit_message_text if query else update.message.reply_text
    await message_sender(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)


# --- Withdrawal Conversation ---

async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the start of the withdrawal process."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    _, total_balance, _, _, _ = database.get_user_balance_details(user_id)
    min_withdraw = float(database.get_setting('min_withdraw', 1.0))
    
    if total_balance < min_withdraw:
        await query.answer(f"Your balance is ${total_balance:.2f}. Minimum to withdraw is ${min_withdraw:.2f}.", show_alert=True)
        return ConversationHandler.END

    amount_to_withdraw = total_balance
    context.user_data['withdrawal_amount'] = amount_to_withdraw

    text = f"""
💸 *Withdrawal Request*

Amount to Withdraw: `${escape_markdown(f'{amount_to_withdraw:.2f}')}`

Please send your wallet address below\\.
    """
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    return WAITING_FOR_ADDRESS


async def withdraw_get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives the wallet address from the user."""
    address = update.message.text.strip()
    amount = context.user_data.get('withdrawal_amount')
    
    if not address or not amount:
        await update.message.reply_text("Session expired\\. Please start the withdrawal process again\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    context.user_data['withdrawal_address'] = address
    
    separator = r'\-' * 25
    
    text = f"""
⚠️ *Please Confirm Your Withdrawal*

Double\\-check the details below\\. Transactions are irreversible\\.
{separator}
➡️ Sending: `${escape_markdown(f'{amount:.2f}')}`
➡️ To Address: `{escape_markdown(address)}`
{separator}
Is all the information correct?
    """
    keyboard = [
        [InlineKeyboardButton("✅ Yes, Submit Request", callback_data="withdraw_confirm")],
        [InlineKeyboardButton("✏️ No, Edit Address", callback_data="withdraw_edit")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END


async def withdraw_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the final confirmed withdrawal request."""
    query = update.callback_query
    user_id = update.effective_user.id
    user = update.effective_user
    address = context.user_data.get('withdrawal_address')
    amount = context.user_data.get('withdrawal_amount')

    if not address or not amount:
        await query.edit_message_text("Session expired\\. Please start over\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END
    
    withdrawal_id = database.process_withdrawal_request(user_id, address, amount)
    
    receipt_text = f"""
✅ *Withdrawal Request Submitted\\!*

Your request is now in the queue and will be processed by an administrator\\.

*Your Receipt*
🆔 Request ID: `#{withdrawal_id}`
💰 Amount: `${escape_markdown(f'{amount:.2f}')}`
📬 Address: `{escape_markdown(address)}`
    """
    keyboard = [[InlineKeyboardButton("🏠 Back to Main Menu", callback_data="nav_start")]]
    await query.edit_message_text(receipt_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

    admin_channel_str = context.bot_data.get('admin_channel')
    if admin_channel_str:
        admin_text = f"""
💸 *New Withdrawal Request*

👤 User: @{escape_markdown(user.username or 'NONE')} \\(ID: `{user.id}`\\)
💰 Amount: `${escape_markdown(f'{amount:.2f}')}`
📬 Address: `{escape_markdown(address)}`
🗓️ Time: {escape_markdown(datetime.utcnow().strftime('%d-%b-%Y %H:%M UTC'))}
        """
        admin_keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_withdrawal:{withdrawal_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_withdrawal:{withdrawal_id}")
            ]
        ]
        try:
            await context.bot.send_message(
                chat_id=admin_channel_str,
                text=admin_text,
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except TelegramError as e:
            logger.error(f"Failed to send withdrawal notification to admin channel: {e}")

    context.user_data.clear()
    return ConversationHandler.END


# --- General Text Handler ---

async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The main handler for all non-command text messages."""
    user = update.effective_user
    text = update.message.text.strip()

    if not database.is_admin(user.id):
        database.log_user_message(user.id, user.username, text)
    
    user_data = database.search_user(str(user.id))
    if user_data and user_data['is_blocked']:
        await update.message.reply_text("🚫 Your account has been restricted\\. Contact support for assistance\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    # --- FIX: Check for an ongoing login FIRST ---
    if 'login_flow' in context.user_data:
        await login.handle_login(update, context)
        return

    # If no login is active, check if this message is a new phone number
    if text.startswith("+") and text[1:].isdigit() and 5 < len(text) < 20:
        await login.handle_login(update, context)
        return
    
    # If it's not part of a login and not a new phone number, forward to support
    if not database.is_admin(user.id):
        await proxy_chat.forward_to_admin(update, context)

# END OF FILE handlers/commands.py