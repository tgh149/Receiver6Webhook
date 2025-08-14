# START OF FILE handlers/start.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import database
from .helpers import escape_markdown

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command for new and existing users."""
    user = update.effective_user
    db_user, is_new_user = database.get_or_create_user(user.id, user.username)

    if is_new_user:
        logger.info(f"New user joined: {user.full_name} (@{user.username}, ID: {user.id})")
        admin_channel_str = context.bot_data.get('admin_channel')
        if admin_channel_str:
            try:
                user_full_name = escape_markdown(user.full_name)
                username = f"@{escape_markdown(user.username)}" if user.username else "_not set_"
                text = f"✅ *New User Alert*\n\n\\- Name: {user_full_name}\n\\- Username: {username}\n\\- ID: `{user.id}`"
                await context.bot.send_message(chat_id=admin_channel_str, text=text, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as e:
                logger.warning(f"Could not send new user notification to admin channel '{admin_channel_str}': {e}")

    if db_user and db_user.get('is_blocked'):
        await update.effective_message.reply_text("You have been blocked from using this bot\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    welcome_text = escape_markdown(context.bot_data.get('welcome_message', "Welcome!"))
    keyboard = [
        [
            InlineKeyboardButton("💼 My Balance", callback_data="nav_balance"),
            # FIX: Changed nav_cap_1 to cap_page_1 to match the callback handler
            InlineKeyboardButton("📋 Countries & Rates", callback_data="cap_page_1")
        ],
        [
            InlineKeyboardButton("📜 Rules", callback_data="nav_rules"),
            InlineKeyboardButton("🆘 Contact Support", callback_data="nav_support")
        ]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
        )
# END OF FILE handlers/start.py