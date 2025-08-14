# START OF FILE handlers/admin/messaging.py
import logging
import asyncio
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

import database
from ..helpers import admin_required, escape_markdown, try_edit_message

logger = logging.getLogger(__name__)

class State(Enum):
    COMPOSE_BODY = auto()
    COMPOSE_PHOTO = auto()
    COMPOSE_BUTTON = auto()
    CONFIRM = auto()
    GET_TARGET_ID = auto()

# --- Main Panel ---

@admin_required
async def broadcast_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main broadcast and messaging dashboard."""
    query = update.callback_query
    await query.answer()

    text = """
📢 *Broadcast & Messaging*

Choose a tool to communicate with your users\\.
    """
    keyboard = [
        [InlineKeyboardButton("🚀 New Mass Broadcast", callback_data="admin_broadcast_conv_start:MASS")],
        [InlineKeyboardButton("🎯 New Targeted Broadcast", callback_data="admin_broadcast_conv_start:TARGETED")],
        [InlineKeyboardButton("👤 Send to Single User", callback_data="admin_broadcast_conv_start:SINGLE")],
        # [InlineKeyboardButton("📊 View Broadcast History", callback_data="admin_broadcast_history")], # Future
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")],
    ]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))


# --- Conversation Handlers ---

async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts a broadcast conversation."""
    query = update.callback_query
    await query.answer()

    context.user_data['broadcast'] = {}
    mode = query.data.split(':')[1]
    context.user_data['broadcast']['mode'] = mode

    if mode == 'SINGLE':
        await try_edit_message(query, "👤 *Send to Single User*\n\nEnter the user's Telegram ID or @username\\.", None)
        return State.GET_TARGET_ID
    
    # For MASS and TARGETED, start composition
    await try_edit_message(query, "*Step 1: Message Content*\n\nSend the text for your message\\. You can use Markdown for formatting\\.", None)
    return State.COMPOSE_BODY


async def handle_get_target_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gets the target user ID for a single message."""
    identifier = update.message.text.strip()
    user = database.search_user(identifier)

    if not user:
        await update.message.reply_text("❌ User not found\\. Please try again or /cancel\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.GET_TARGET_ID

    context.user_data['broadcast']['user_ids'] = [user['telegram_id']]
    await update.message.reply_text(f"✅ Target: @{escape_markdown(user.get('username'))} \\(`{user['telegram_id']}`\\)\\.\n\n*Step 1: Message Content*\n\nSend the text for your message\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return State.COMPOSE_BODY


async def handle_compose_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles receiving the main text body of the broadcast."""
    context.user_data['broadcast']['text'] = update.message.text_markdown_v2
    context.user_data['broadcast']['photo_id'] = None
    context.user_data['broadcast']['button'] = None

    keyboard = [
        [InlineKeyboardButton("🖼️ Add Photo", callback_data="broadcast_add_photo")],
        [InlineKeyboardButton("➡️ Skip & Preview", callback_data="broadcast_preview")],
    ]
    await update.message.reply_text("*Step 2: Add Media \\(Optional\\)*\n\nWould you like to add a photo to this message?", reply_markup=InlineKeyboardMarkup(keyboard))


async def prompt_for_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please send the photo you want to attach\\.")
    return State.COMPOSE_PHOTO


async def handle_compose_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles receiving the photo for the broadcast."""
    if not update.message.photo:
        await update.message.reply_text("That's not a photo\\. Please send an image or skip this step\\.")
        return State.COMPOSE_PHOTO

    context.user_data['broadcast']['photo_id'] = update.message.photo[-1].file_id
    
    keyboard = [
        [InlineKeyboardButton("🔗 Add URL Button", callback_data="broadcast_add_button")],
        [InlineKeyboardButton("➡️ Skip & Preview", callback_data="broadcast_preview")],
    ]
    await update.message.reply_text("*Step 3: Add Button \\(Optional\\)*\n\nWould you like to add a URL button below the message?", reply_markup=InlineKeyboardMarkup(keyboard))


async def prompt_for_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please send the button details in the format:\n`Button Text - https://your-link.com`")
    return State.COMPOSE_BUTTON


async def handle_compose_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles receiving the button text and URL."""
    try:
        text, url = map(str.strip, update.message.text.split('-', 1))
        if not url.startswith(('http://', 'https://')):
            raise ValueError
        context.user_data['broadcast']['button'] = (text, url)
        await show_preview(update, context)
        return State.CONFIRM
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid format\\. Please use: `Button Text - https://your-link.com`")
        return State.COMPOSE_BUTTON


async def show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the final preview of the broadcast message before sending."""
    query = update.callback_query
    if query:
        await query.answer()

    broadcast = context.user_data['broadcast']
    
    # Determine target audience
    if broadcast['mode'] == 'MASS':
        users = database.fetch_all("SELECT telegram_id FROM users WHERE is_blocked = 0")
        broadcast['user_ids'] = [u['telegram_id'] for u in users]
        target_desc = f"ALL active users \\(Approx\\. {len(broadcast['user_ids'])} users\\)"
    elif broadcast['mode'] == 'SINGLE':
        user = database.get_user_by_id(broadcast['user_ids'][0])
        target_desc = f"Single user: @{escape_markdown(user.get('username'))} \\(`{user['telegram_id']}`\\)"
    else: # TARGETED mode (future extension)
        target_desc = "A targeted segment \\(feature coming soon\\)"
        broadcast['user_ids'] = []

    await (query.message if query else update.message).reply_text(f"✨ *Broadcast Preview* ✨\n\n*Target:* {target_desc}", parse_mode=ParseMode.MARKDOWN_V2)

    # Send the actual preview message
    text = broadcast['text']
    photo_id = broadcast['photo_id']
    button = broadcast['button']
    
    reply_markup = None
    if button:
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(button[0], url=button[1])]])

    if photo_id:
        await (query.message if query else update.message).reply_photo(photo=photo_id, caption=text, caption_parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
    else:
        await (query.message if query else update.message).reply_text(text=text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup, disable_web_page_preview=True)

    # Confirmation
    confirm_keyboard = [
        [InlineKeyboardButton("✅ Confirm & Send", callback_data="broadcast_send")],
        [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")],
    ]
    await (query.message if query else update.message).reply_text("Is this correct?", reply_markup=InlineKeyboardMarkup(confirm_keyboard))
    return State.CONFIRM


async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes the broadcast after final confirmation."""
    query = update.callback_query
    await query.answer()
    
    broadcast = context.user_data['broadcast']
    user_ids = broadcast.get('user_ids', [])

    if not user_ids:
        await query.edit_message_text("❌ No users to send to\\. Broadcast cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    await query.edit_message_text(f"⏳ Broadcast in progress\\.\\.\\. targeting {len(user_ids)} users\\.", parse_mode=ParseMode.MARKDOWN_V2)
    
    text = broadcast['text']
    photo_id = broadcast['photo_id']
    button = broadcast['button']
    
    reply_markup = None
    if button:
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(button[0], url=button[1])]])

    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            if photo_id:
                await context.bot.send_photo(chat_id=user_id, photo=photo_id, caption=text, caption_parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
            else:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup, disable_web_page_preview=True)
            success_count += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for user {user_id}: {e}")
            fail_count += 1
        await asyncio.sleep(0.05) # Rate limit: 20 messages per second

    final_report = f"✅ *Broadcast Complete*\n\nSent: `{success_count}`\nFailed: `{fail_count}`"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=final_report, parse_mode=ParseMode.MARKDOWN_V2)
    
    context.user_data.clear()
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ Broadcast cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END


def get_conv_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_starter, pattern=r"^admin_broadcast_conv_start:")],
        states={
            State.GET_TARGET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_target_id)],
            State.COMPOSE_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_compose_body)],
            State.COMPOSE_PHOTO: [
                CallbackQueryHandler(prompt_for_button, pattern=r"^broadcast_preview$"),
                MessageHandler(filters.PHOTO, handle_compose_photo)
            ],
            State.COMPOSE_BUTTON: [
                CallbackQueryHandler(show_preview, pattern=r"^broadcast_preview$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_compose_button)
            ],
            State.CONFIRM: [
                CallbackQueryHandler(execute_broadcast, pattern=r"^broadcast_send$"),
                CallbackQueryHandler(conv_cancel, pattern=r"^broadcast_cancel$")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(prompt_for_photo, pattern=r"^broadcast_add_photo$"),
            CallbackQueryHandler(prompt_for_button, pattern=r"^broadcast_add_button$"),
            CallbackQueryHandler(show_preview, pattern=r"^broadcast_preview$"),
            CallbackQueryHandler(conv_cancel, pattern=r"^broadcast_cancel$"),
        ],
        map_to_parent={ ConversationHandler.END: ConversationHandler.END },
        per_user=True, per_chat=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(broadcast_main_panel, pattern=r"^admin_broadcast_main$"),
    ]
# END OF FILE handlers/admin/messaging.py