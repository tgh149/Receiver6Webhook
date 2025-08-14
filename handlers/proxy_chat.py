# START OF FILE handlers/proxy_chat.py
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import database
from .helpers import escape_markdown

logger = logging.getLogger(__name__)

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forwards a user's message to the support admin."""
    support_id_str = context.bot_data.get('support_id')
    if not support_id_str or not support_id_str.isdigit():
        return

    user = update.effective_user
    user_name = escape_markdown(user.full_name or f"User {user.id}")
    
    try:
        # FIX: The parentheses around the user ID must be escaped for MarkdownV2.
        # The double backslash is needed because it's an f-string.
        text_to_forward = f"👤 *{user_name}* \\(`{user.id}`\\):\n\n{escape_markdown(update.message.text)}"
        
        await context.bot.send_message(
            chat_id=int(support_id_str),
            text=text_to_forward,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Acknowledge receipt to the user
        await update.message.reply_text("✅ Your message has been sent to support\\. You will receive a reply shortly\\.", parse_mode=ParseMode.MARKDOWN_V2)
        
    except Exception as e:
        logger.error(f"Failed to forward message to admin {support_id_str}: {e}")


async def reply_to_user_by_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows an admin to reply to a user by replying to the forwarded message."""
    admin_user = update.effective_user
    admin_id_str = context.bot_data.get('support_id')

    if not admin_id_str or str(admin_user.id) != admin_id_str:
        return

    # The replied-to message's text will be in MarkdownV2, but we can still parse it
    replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
    
    # Extract user ID from the format: User Name (`123456789`):
    if replied_text:
        match = re.search(r'\\(`(\d+)`\\)', replied_text)
        if match:
            target_user_id = int(match.group(1))
            reply_message = update.message.text
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"💬 *Support Reply:*\n\n{escape_markdown(reply_message)}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await update.message.reply_text("✅ Reply sent\\.", parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as e:
                await update.message.reply_text(f"❌ Could not send reply: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
            return

    # Fallback if ID can't be found
    await update.message.reply_text("Could not detect user ID from replied message\\. Please use `/reply USER_ID message`\\.", parse_mode=ParseMode.MARKDOWN_V2)


async def reply_to_user_by_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows an admin to reply to a user using the /reply command."""
    admin_user = update.effective_user
    if not database.is_admin(admin_user.id):
        return

    try:
        _, target_user_id_str, reply_message = update.message.text.split(' ', 2)
        target_user_id = int(target_user_id_str)
        
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💬 *Support Reply:*\n\n{escape_markdown(reply_message)}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await update.message.reply_text(f"✅ Reply sent to user `{target_user_id}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: `/reply USER_ID Your message here`", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Could not send reply: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

# END OF FILE handlers/proxy_chat.py