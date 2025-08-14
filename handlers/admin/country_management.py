# START OF FILE handlers/admin/country_management.py
import logging
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# FIX: CommandHandler was missing from this import
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode
import re

import database
from ..helpers import admin_required, escape_markdown, try_edit_message

logger = logging.getLogger(__name__)

class State(Enum):
    ADD_CODE = auto()
    ADD_NAME = auto()
    ADD_FLAG = auto()
    ADD_PRICE_OK = auto()
    ADD_PRICE_RESTRICTED = auto()
    ADD_TIME = auto()
    ADD_CAPACITY = auto()
    EDIT_VALUE = auto()
    DELETE_CODE = auto()
    DELETE_CONFIRM = auto()

# --- Main Panels ---

@admin_required
async def country_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main country management panel with a list of countries."""
    query = update.callback_query
    # This can also be called from a message handler (e.g., after a conv cancel)
    if query:
        await query.answer()

    countries = database.get_countries_config()
    text = "🌐 *Country Management*\n\nSelect a country to edit, or use the buttons below to add/remove countries\\."
    
    keyboard = []
    if countries:
        country_buttons = [
            InlineKeyboardButton(f"{data.get('flag','')} {data.get('name')}", callback_data=f"admin_country_view:{data['code']}")
            for data in sorted(countries.values(), key=lambda x: x['name'])
        ]
        # Group buttons into rows of 2
        keyboard.extend([country_buttons[i:i + 2] for i in range(0, len(country_buttons), 2)])

    keyboard.append([
        InlineKeyboardButton("➕ Add Country", callback_data="admin_country_conv_start:ADD_CODE"),
        InlineKeyboardButton("➖ Delete Country", callback_data="admin_country_conv_start:DELETE_CODE")
    ])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")])

    if query:
        await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)


@admin_required
async def country_view_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the detailed configuration for a single country."""
    query = update.callback_query
    await query.answer()

    code = query.data.split(':')[1]
    country = database.get_country_by_code(code)

    if not country:
        await query.answer("Country not found!", show_alert=True)
        await country_main_panel(update, context)
        return

    c_count = database.get_country_account_count(code)
    cap = country.get('capacity', -1)
    cap_text = "Unlimited" if cap == -1 else f"{c_count}/{cap}"

    text = f"""
{country.get('flag', '')} *{escape_markdown(country['name'])} Settings*

*Code:* `{escape_markdown(country['code'])}`
*Capacity:* `{escape_markdown(cap_text)}`
*Confirmation Time:* `{country.get('time', 0)} seconds`
*Accept Restricted:* `{'Yes' if country.get('accept_restricted') == 'True' else 'No'}`

*Pricing:*
  └─ OK Account: `${escape_markdown(f"{country.get('price_ok', 0.0):.2f}")}`
  └─ Restricted Account: `${escape_markdown(f"{country.get('price_restricted', 0.0):.2f}")}`
    """
    keyboard = [
        [
            InlineKeyboardButton("Edit Price (OK)", callback_data=f"admin_country_conv_start:EDIT_VALUE:{code}:price_ok"),
            InlineKeyboardButton("Edit Price (Restricted)", callback_data=f"admin_country_conv_start:EDIT_VALUE:{code}:price_restricted")
        ],
        [
            InlineKeyboardButton("Edit Capacity", callback_data=f"admin_country_conv_start:EDIT_VALUE:{code}:capacity"),
            InlineKeyboardButton("Edit Time", callback_data=f"admin_country_conv_start:EDIT_VALUE:{code}:time")
        ],
        [
            InlineKeyboardButton("Toggle Accept Restricted", callback_data=f"admin_country_toggle_restricted:{code}")
        ],
        [InlineKeyboardButton("⬅️ Back to Country List", callback_data="admin_country_main")]
    ]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

# --- Actions & Callbacks ---

@admin_required
async def toggle_accept_restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles whether a country accepts restricted accounts."""
    query = update.callback_query
    await query.answer()

    code = query.data.split(':')[1]
    country = database.get_country_by_code(code)
    if not country:
        return

    new_value = 'False' if country.get('accept_restricted') == 'True' else 'True'
    database.update_country_value(code, 'accept_restricted', new_value)
    database.log_admin_action(update.effective_user.id, "COUNTRY_EDIT", f"Toggled accept_restricted to {new_value} for {code}")
    context.bot_data['countries_config'] = database.get_countries_config() # Refresh cache

    # Refresh the view
    await country_view_panel(update, context)

# --- Conversation Handlers ---

async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts a conversation for adding, editing, or deleting countries."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(':')
    action = parts[1]
    
    prompts = {
        'ADD_CODE': ("*Step 1/7:*\nSend the international country code \\(e\\.g\\., `+1`\\)", State.ADD_CODE),
        'EDIT_VALUE': ("Enter the new value:", State.EDIT_VALUE),
        'DELETE_CODE': ("Send the country code of the country to delete \\(e\\.g\\., `+1`\\)", State.DELETE_CODE),
    }

    if action in prompts:
        prompt, state = prompts[action]
        # Pre-fill data for editing
        if action == 'EDIT_VALUE':
            context.user_data['edit_country_code'] = parts[2]
            context.user_data['edit_country_key'] = parts[3]
            country = database.get_country_by_code(parts[2])
            prompt = f"Editing *{escape_markdown(parts[3])}* for {country.get('flag','')} *{escape_markdown(country['name'])}*\\.\n\nCurrent value: `{escape_markdown(country.get(parts[3]))}`\n\nSend the new value\\."

        await try_edit_message(query, f"{prompt}\n\nType /cancel to abort\\.", None)
        return state
    return ConversationHandler.END

# Add Country Flow
async def handle_add_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    if not re.match(r'^\+\d{1,4}$', code):
        await update.message.reply_text("Invalid format\\. Code must start with `+` and be 1-4 digits long\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_CODE
    context.user_data['new_country'] = {'code': code}
    await update.message.reply_text("*Step 2/7:*\nSend the country's name \\(e\\.g\\., `United States`\\)", parse_mode=ParseMode.MARKDOWN_V2)
    return State.ADD_NAME

async def handle_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_country']['name'] = update.message.text.strip()
    await update.message.reply_text("*Step 3/7:*\nSend the country's flag emoji \\(e\\.g\\., 🇺🇸\\)", parse_mode=ParseMode.MARKDOWN_V2)
    return State.ADD_FLAG

async def handle_add_flag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_country']['flag'] = update.message.text.strip()
    await update.message.reply_text("*Step 4/7:*\nSend the price for a clean \\(OK\\) account \\(e\\.g\\., `0.50`\\)", parse_mode=ParseMode.MARKDOWN_V2)
    return State.ADD_PRICE_OK

async def handle_add_price_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_country']['price_ok'] = float(update.message.text.strip())
        await update.message.reply_text("*Step 5/7:*\nSend the price for a restricted account \\(e\\.g\\., `0.10`\\)", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_PRICE_RESTRICTED
    except ValueError:
        await update.message.reply_text("Invalid number\\. Please enter a valid price like `0.50`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_PRICE_OK

async def handle_add_price_restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_country']['price_restricted'] = float(update.message.text.strip())
        await update.message.reply_text("*Step 6/7:*\nSend the confirmation wait time in seconds \\(e\\.g\\., `600` for 10 minutes\\)", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_TIME
    except ValueError:
        await update.message.reply_text("Invalid number\\. Please enter a valid price like `0.10`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_PRICE_RESTRICTED

async def handle_add_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_country']['time'] = int(update.message.text.strip())
        await update.message.reply_text("*Step 7/7:*\nSend the account capacity\\. Use `-1` for unlimited\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_CAPACITY
    except ValueError:
        await update.message.reply_text("Invalid number\\. Please enter whole seconds like `600`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_TIME

async def handle_add_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_country']['capacity'] = int(update.message.text.strip())
        c = context.user_data['new_country']
        database.add_country(c['code'], c['name'], c['flag'], c['time'], c['capacity'], c['price_ok'], c['price_restricted'])
        database.log_admin_action(update.effective_user.id, "COUNTRY_ADD", f"Added {c['name']} ({c['code']})")
        context.bot_data['countries_config'] = database.get_countries_config() # Refresh cache

        await update.message.reply_text(f"✅ Country *{escape_markdown(c['name'])}* added successfully\\!", parse_mode=ParseMode.MARKDOWN_V2)
        await country_main_panel(update, context)
        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid number\\. Please enter a number like `100` or `-1`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_CAPACITY

# Edit Country Flow
async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data['edit_country_key']
    code = context.user_data['edit_country_code']
    value = update.message.text.strip()

    try:
        # Validate input based on key
        if key in ['price_ok', 'price_restricted']:
            value = float(value)
        elif key in ['time', 'capacity']:
            value = int(value)

        database.update_country_value(code, key, value)
        database.log_admin_action(update.effective_user.id, "COUNTRY_EDIT", f"Set {key}={value} for {code}")
        context.bot_data['countries_config'] = database.get_countries_config() # Refresh cache

        await update.message.reply_text(f"✅ Setting *{escape_markdown(key)}* updated for `{escape_markdown(code)}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
        await country_main_panel(update, context)
        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Invalid value for this setting\\. Please try again\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.EDIT_VALUE

# Delete Country Flow
async def handle_delete_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    country = database.get_country_by_code(code)
    if not country:
        await update.message.reply_text("Country code not found\\. Please try again or /cancel\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.DELETE_CODE
    
    context.user_data['delete_country_code'] = code
    await update.message.reply_text(f"You are about to delete *{escape_markdown(country['name'])}* \\(`{escape_markdown(code)}`\\)\\. This cannot be undone\\.\n\nType `CONFIRM` to proceed\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return State.DELETE_CONFIRM

async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == 'CONFIRM':
        code = context.user_data['delete_country_code']
        database.delete_country(code)
        database.log_admin_action(update.effective_user.id, "COUNTRY_DELETE", f"Deleted {code}")
        context.bot_data['countries_config'] = database.get_countries_config() # Refresh cache
        await update.message.reply_text(f"✅ Country `{escape_markdown(code)}` has been deleted\\.", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("❌ Deletion cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)

    await country_main_panel(update, context)
    context.user_data.clear()
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await country_main_panel(update, context)
    return ConversationHandler.END

def get_conv_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_starter, pattern=r"^admin_country_conv_start:")],
        states={
            State.ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_code)],
            State.ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_name)],
            State.ADD_FLAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_flag)],
            State.ADD_PRICE_OK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_price_ok)],
            State.ADD_PRICE_RESTRICTED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_price_restricted)],
            State.ADD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_time)],
            State.ADD_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_capacity)],
            State.EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value)],
            State.DELETE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delete_code)],
            State.DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delete_confirm)],
        },
        fallbacks=[
            CommandHandler('cancel', conv_cancel),
        ],
        map_to_parent={ ConversationHandler.END: ConversationHandler.END },
        per_user=True, per_chat=True,
        allow_reentry=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(country_main_panel, pattern=r"^admin_country_main$"),
        CallbackQueryHandler(country_view_panel, pattern=r"^admin_country_view:"),
        CallbackQueryHandler(toggle_accept_restricted, pattern=r"^admin_country_toggle_restricted:"),
    ]
# END OF FILE handlers/admin/country_management.py