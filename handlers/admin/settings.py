# START OF FILE handlers/admin/settings.py
import logging
from enum import Enum, auto
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode
import telethon

import database
from ..helpers import admin_required, escape_markdown, try_edit_message, create_pagination_keyboard

logger = logging.getLogger(__name__)

class State(Enum):
    EDIT_VALUE = auto()
    ADD_PROXY = auto()
    REMOVE_PROXY = auto()
    ADD_API_ID = auto()
    ADD_API_HASH = auto()

# --- Main Panels ---

@admin_required
async def settings_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main settings dashboard with the new professional UI."""
    query = update.callback_query
    if query:
        await query.answer()

    s = context.bot_data
    
    def get_toggle_text(setting_name, key, on_val='True'):
        is_on = s.get(key, 'False') == on_val
        return f"{setting_name}: {'✅ ON' if is_on else '❌ OFF'}"

    separator = r'\-' * 10
    text = f"""
⚙️ *Bot Settings*

These are the primary operational controls\\.
Select a category for more detailed configuration\\.

*{separator} Primary Toggles {separator}*
Bot Status:       *{'✅ ON' if s.get('bot_status', 'OFF') == 'ON' else '❌ OFF'}*
Account Adding:   *{'✅ ON' if s.get('add_account_status', 'LOCKED') == 'UNLOCKED' else '❌ OFF'}*
Spam Check:       *{'✅ ON' if s.get('enable_spam_check') == 'True' else '❌ OFF'}*
Device Check:     *{'✅ ON' if s.get('enable_device_check') == 'True' else '❌ OFF'}*
2FA Enabler:      *{'✅ ON' if s.get('enable_2fa') == 'True' else '❌ OFF'}*
    """
    
    keyboard = [
        [
            InlineKeyboardButton(get_toggle_text("Bot Status", 'bot_status', 'ON'), callback_data="admin_setting_toggle:bot_status:ON:OFF"),
            InlineKeyboardButton(get_toggle_text("Account Adding", 'add_account_status', 'UNLOCKED'), callback_data="admin_setting_toggle:add_account_status:UNLOCKED:LOCKED")
        ],
        [
            InlineKeyboardButton(get_toggle_text("Spam Check", 'enable_spam_check'), callback_data="admin_setting_toggle:enable_spam_check:True:False"),
            # FIX: Added the missing Device Check button
            InlineKeyboardButton(get_toggle_text("Device Check", 'enable_device_check'), callback_data="admin_setting_toggle:enable_device_check:True:False")
        ],
        [InlineKeyboardButton(get_toggle_text("2FA Enabler", 'enable_2fa'), callback_data="admin_setting_toggle:enable_2fa:True:False")],
        [
            InlineKeyboardButton("✍️ Bot Messages & Text", callback_data="admin_settings_texts"),
            InlineKeyboardButton("🔧 Core Configuration", callback_data="admin_settings_core"),
        ],
        [InlineKeyboardButton("🔑 API & Proxy Management", callback_data="admin_settings_api_proxy")],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel")],
    ]

    if query:
        await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

# ... (rest of the file remains the same, no need to re-paste the whole thing)

@admin_required
async def text_settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "✍️ *Bot Messages & Texts*\n\nSelect a message to edit\\. You can use Markdown for formatting\\."
    keyboard = [
        [InlineKeyboardButton("Welcome Message", callback_data="admin_setting_conv_start:EDIT_VALUE:welcome_message")],
        [InlineKeyboardButton("Help Message", callback_data="admin_setting_conv_start:EDIT_VALUE:help_message")],
        [InlineKeyboardButton("Rules Message", callback_data="admin_setting_conv_start:EDIT_VALUE:rules_message")],
        [InlineKeyboardButton("Support Message", callback_data="admin_setting_conv_start:EDIT_VALUE:support_message")],
        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="admin_settings_main")],
    ]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def core_settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "🔧 *Core Configuration*\n\nSelect a core parameter to edit\\."
    keyboard = [
        [InlineKeyboardButton("Min Withdrawal", callback_data="admin_setting_conv_start:EDIT_VALUE:min_withdraw")],
        [InlineKeyboardButton("Max Withdrawal", callback_data="admin_setting_conv_start:EDIT_VALUE:max_withdraw")],
        [InlineKeyboardButton("Support Admin ID", callback_data="admin_setting_conv_start:EDIT_VALUE:support_id")],
        [InlineKeyboardButton("Admin Channel", callback_data="admin_setting_conv_start:EDIT_VALUE:admin_channel")],
        [InlineKeyboardButton("Spambot Username", callback_data="admin_setting_conv_start:EDIT_VALUE:spambot_username")],
        [InlineKeyboardButton("Default 2FA Password", callback_data="admin_setting_conv_start:EDIT_VALUE:two_step_password")],
        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="admin_settings_main")],
    ]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def api_proxy_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    api_count = len(database.get_all_api_credentials())
    _, proxy_total = database.get_all_proxies()
    text = "🔑 *API & Proxy Management*\n\nManage resources for bot stability and scalability\\."
    keyboard = [
        [InlineKeyboardButton(f"API Credentials ({api_count})", callback_data="admin_settings_api_list")],
        [InlineKeyboardButton(f"Proxy Pool ({proxy_total})", callback_data="admin_settings_proxy_list_1")],
        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="admin_settings_main")],
    ]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def api_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    credentials = database.get_all_api_credentials()
    text = "🔑 *API Credentials*\n\n"
    keyboard = []
    if not credentials:
        text += "No API credentials found\\."
    else:
        for i, cred in enumerate(credentials):
            status_icon = "🟢" if cred['is_active'] else "🔴"
            last_used = "Never"
            if cred['last_used']:
                from datetime import datetime
                last_used = datetime.fromisoformat(cred['last_used']).strftime('%d-%b %H:%M')
            text += f"*{i+1}\\.* `{escape_markdown(cred['api_id'])}` {status_icon}\n  └─ Last Used: {escape_markdown(last_used)}\n"
            keyboard.append([
                InlineKeyboardButton(f"{'🔴 Disable' if cred['is_active'] else '🟢 Enable'}", callback_data=f"admin_setting_api_toggle:{cred['id']}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"admin_setting_api_delete:{cred['id']}")
            ])
    keyboard.append([InlineKeyboardButton("➕ Add New API", callback_data="admin_setting_conv_start:ADD_API_ID")])
    keyboard.append([InlineKeyboardButton("🧪 Test Active APIs", callback_data="admin_setting_api_test")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_settings_api_proxy")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def proxy_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('_')[-1])
    limit = 10
    proxies, total = database.get_all_proxies(page, limit)
    text = f"🌐 *Proxy Pool* \\(Page {page}\\)\n\n"
    if not proxies:
        text += "No proxies have been added\\."
    else:
        text += "\n".join([f"`{p['id']}`: `{escape_markdown(p['proxy'])}`" for p in proxies])
    keyboard = create_pagination_keyboard("admin_settings_proxy_list", page, total, limit)
    keyboard.append([
        InlineKeyboardButton("➕ Add Proxy", callback_data="admin_setting_conv_start:ADD_PROXY"),
        InlineKeyboardButton("➖ Remove Proxy", callback_data="admin_setting_conv_start:REMOVE_PROXY")
    ])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_settings_api_proxy")])
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def toggle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, key, on_val, off_val = query.data.split(':')
    current_val = context.bot_data.get(key)
    new_val = off_val if current_val == on_val else on_val
    database.set_setting(key, new_val)
    context.bot_data[key] = new_val
    database.log_admin_action(update.effective_user.id, "SETTING_TOGGLE", f"Set {key} to {new_val}")
    await query.answer(f"Set {key} to {new_val}")
    await settings_main_panel(update, context)

@admin_required
async def api_toggle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cred_id = int(query.data.split(':')[1])
    database.toggle_api_credential_status(cred_id)
    database.log_admin_action(update.effective_user.id, "API_TOGGLE", f"Toggled status for API ID {cred_id}")
    await query.answer("Status toggled")
    await api_list_panel(update, context)

@admin_required
async def api_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cred_id = int(query.data.split(':')[1])
    database.remove_api_credential(cred_id)
    database.log_admin_action(update.effective_user.id, "API_DELETE", f"Deleted API ID {cred_id}")
    await query.answer("API credential deleted")
    await api_list_panel(update, context)

@admin_required
async def test_apis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    active_creds = [c for c in database.get_all_api_credentials() if c['is_active']]
    if not active_creds:
        await query.message.reply_text("No active APIs to test\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    msg = await query.message.reply_text(f"🧪 Testing *{len(active_creds)}* active API credentials\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    results = ""
    for cred in active_creds:
        session_name = f"sessions/test_{cred['api_id']}"
        client = telethon.TelegramClient(session_name, int(cred['api_id']), cred['api_hash'], device_model="API Test")
        try:
            await client.connect()
            me = await client.get_me(input_peer=False)
            if me:
                results += f"🟢 `{cred['api_id']}`: Success \\(@{me.username}\\)\n"
            else:
                results += f"🟡 `{cred['api_id']}`: Failed \\(Auth Error\\)\n"
        except Exception as e:
            results += f"🔴 `{cred['api_id']}`: Failed \\({escape_markdown(type(e).__name__)}\\)\n"
        finally:
            if client.is_connected():
                await client.disconnect()
    await msg.edit_text(f"🧪 *API Test Results*\n\n{results}", parse_mode=ParseMode.MARKDOWN_V2)

async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(':')
    action = parts[1]
    prompts = {
        'EDIT_VALUE': ("Enter the new value:", State.EDIT_VALUE),
        'ADD_PROXY': ("Enter the proxy string (`ip:port` or `ip:port:user:pass`):", State.ADD_PROXY),
        'REMOVE_PROXY': ("Enter the ID of the proxy to remove:", State.REMOVE_PROXY),
        'ADD_API_ID': ("Enter the new API ID:", State.ADD_API_ID),
    }
    if action in prompts:
        prompt, state = prompts[action]
        if action == 'EDIT_VALUE':
            key = parts[2]
            context.user_data['edit_key'] = key
            current_value = context.bot_data.get(key, "Not Set")
            if 'password' in key.lower(): current_value = "******"
            prompt = f"Editing *{escape_markdown(key)}*\\.\nCurrent value: `{escape_markdown(current_value)}`\n\nSend the new value\\."
        await try_edit_message(query, f"{prompt}\n\nType /cancel to abort\\.", None)
        return state
    return ConversationHandler.END

async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get('edit_key')
    if not key: return ConversationHandler.END
    value = update.message.text.strip()
    database.set_setting(key, value)
    context.bot_data[key] = value
    database.log_admin_action(update.effective_user.id, "SETTING_EDIT", f"Set {key} to '{value[:20]}...'")
    await update.message.reply_text(f"✅ Setting *{escape_markdown(key)}* updated\\.", parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data.clear()
    await settings_main_panel(update, context)
    return ConversationHandler.END

async def handle_add_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxy = update.message.text.strip()
    database.add_proxy(proxy)
    await update.message.reply_text(f"✅ Proxy `{escape_markdown(proxy)}` added\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await api_proxy_panel(update, context)
    return ConversationHandler.END

async def handle_remove_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        proxy_id = int(update.message.text.strip())
        if database.remove_proxy_by_id(proxy_id):
            await update.message.reply_text(f"✅ Proxy with ID `{proxy_id}` removed\\.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text("❌ Proxy ID not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
    except ValueError:
        await update.message.reply_text("❌ Invalid ID\\. Please enter a number\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.REMOVE_PROXY
    await api_proxy_panel(update, context)
    return ConversationHandler.END

async def handle_add_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_id = update.message.text.strip()
    if not api_id.isdigit():
        await update.message.reply_text("API ID must be a number\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return State.ADD_API_ID
    context.user_data['new_api_id'] = api_id
    await update.message.reply_text("Now send the API Hash\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return State.ADD_API_HASH

async def handle_add_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_hash = update.message.text.strip()
    api_id = context.user_data['new_api_id']
    database.add_api_credential(api_id, api_hash)
    database.log_admin_action(update.effective_user.id, "API_ADD", f"Added API ID {api_id}")
    await update.message.reply_text(f"✅ API Credential `{escape_markdown(api_id)}` added\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await api_list_panel(update, context)
    context.user_data.clear()
    return ConversationHandler.END

async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Operation cancelled\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await settings_main_panel(update, context)
    return ConversationHandler.END

def get_conv_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_starter, pattern=r"^admin_setting_conv_start:")],
        states={
            State.EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value)],
            State.ADD_PROXY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_proxy)],
            State.REMOVE_PROXY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_proxy)],
            State.ADD_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_api_id)],
            State.ADD_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_api_hash)],
        },
        fallbacks=[CommandHandler('cancel', conv_cancel)],
        map_to_parent={ ConversationHandler.END: ConversationHandler.END },
        per_user=True, per_chat=True,
        allow_reentry=True,
    )

def get_callback_handlers():
    return [
        CallbackQueryHandler(settings_main_panel, pattern=r"^admin_settings_main$"),
        CallbackQueryHandler(text_settings_panel, pattern=r"^admin_settings_texts$"),
        CallbackQueryHandler(core_settings_panel, pattern=r"^admin_settings_core$"),
        CallbackQueryHandler(api_proxy_panel, pattern=r"^admin_settings_api_proxy$"),
        CallbackQueryHandler(api_list_panel, pattern=r"^admin_settings_api_list$"),
        CallbackQueryHandler(proxy_list_panel, pattern=r"^admin_settings_proxy_list_"),
        CallbackQueryHandler(toggle_setting, pattern=r"^admin_setting_toggle:"),
        CallbackQueryHandler(api_toggle_status, pattern=r"^admin_setting_api_toggle:"),
        CallbackQueryHandler(api_delete, pattern=r"^admin_setting_api_delete:"),
        CallbackQueryHandler(test_apis, pattern=r"^admin_setting_api_test$"),
    ]
# END OF FILE handlers/admin/settings.py