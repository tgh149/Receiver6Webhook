# START OF FILE handlers/helpers.py
import logging
import re
from functools import wraps
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

import database

logger = logging.getLogger(__name__)

def admin_required(func):
    """Decorator to ensure a user is an admin before executing a function."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not database.is_admin(user_id):
            if update.callback_query:
                await update.callback_query.answer("🚫 Access Denied", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def escape_markdown(text: str, version: int = 2) -> str:
    """Helper function to escape telegram markdown characters."""
    if not isinstance(text, str):
        text = str(text)
    if version == 1:
        escape_chars = r'_*`['
        return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)
    else: # version 2
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def try_edit_message(query: 'CallbackQuery', text: str, reply_markup: InlineKeyboardMarkup):
    """A safe wrapper to edit messages, ignoring 'not modified' errors."""
    try:
        if query and query.message:
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True
            )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"BadRequest editing message for cb {query.data}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error editing message for cb {query.data}: {e}", exc_info=True)

def create_pagination_keyboard(prefix, current_page, total_items, items_per_page=5):
    """Creates a simple 'Prev'/'Next' pagination keyboard."""
    buttons = []
    total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
    if total_pages <= 1:
        return []
    
    row = []
    if current_page > 1:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}_{current_page-1}"))
    if current_page < total_pages:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_{current_page+1}"))
    if row:
        buttons.append(row)
    return buttons

def create_advanced_pagination(prefix, current_page, total_items, items_per_page=10):
    """Creates the advanced pagination bar: First, -5, Prev, Next, +5, Last."""
    buttons = []
    total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
    if total_pages <= 1:
        return []
    
    row = []
    # First and -5
    if current_page > 1:
        row.append(InlineKeyboardButton("⏪ First", callback_data=f"{prefix}_1"))
    if current_page > 5:
        row.append(InlineKeyboardButton("◀️ -5", callback_data=f"{prefix}_{current_page-5}"))
    
    # Prev and Next
    if current_page > 1:
        row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}_{current_page-1}"))
    
    # Page indicator
    if total_pages > 1:
        row.append(InlineKeyboardButton(f"Page {current_page}/{total_pages}", callback_data="noop"))
        
    if current_page < total_pages:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_{current_page+1}"))

    # +5 and Last
    if current_page < total_pages - 4:
        row.append(InlineKeyboardButton("+5 ▶️", callback_data=f"{prefix}_{current_page+5}"))
    if current_page < total_pages:
        row.append(InlineKeyboardButton("Last ⏩", callback_data=f"{prefix}_{total_pages}"))

    if row:
        buttons.append(row)
    return buttons
# END OF FILE handlers/helpers.py