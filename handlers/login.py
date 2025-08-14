# START OF FILE handlers/login.py
import os
import logging
import asyncio
import random
import re
import sqlite3
import shutil
import json
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError, SessionPasswordNeededError, PhoneNumberInvalidError,
    FloodWaitError, PhoneCodeExpiredError, PasswordHashInvalidError, AuthKeyError, ApiIdInvalidError, UserBannedInChannelError
)
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

import database
from config import BOT_TOKEN
from .helpers import escape_markdown

logger = logging.getLogger(__name__)

ACTIVE_CLIENTS = {}

DEVICE_PROFILES = [
    {"device_model": "Desktop", "system_version": "Windows 10", "app_version": "4.8.1 x64"},
    {"device_model": "PC 64bit", "system_version": "Windows 11", "app_version": "4.9.9 x64"},
    {"device_model": "Laptop", "system_version": "Windows 10", "app_version": "4.10.2 x64"},
]

def _get_country_info(phone_number: str, countries_config: dict):
    code = next((c for c in sorted(countries_config.keys(), key=len, reverse=True) if phone_number.startswith(c)), None)
    return (countries_config.get(code), code) if code else (None, None)

def _get_session_path(phone_number: str, user_id: str, status: str, country_name: str):
    folder_name = country_name.replace(" ", "_").lower()
    sessions_dir_path = os.path.join("sessions", folder_name, status)
    os.makedirs(sessions_dir_path, exist_ok=True)
    return os.path.join(sessions_dir_path, f"{phone_number}.session")

async def _move_session_file(old_path, phone, user_id, new_status, country_name):
    if not old_path or not os.path.exists(old_path):
        return None
    new_path = _get_session_path(phone, str(user_id), new_status, country_name)
    try:
        shutil.move(old_path, new_path)
        return new_path
    except Exception as e:
        logger.error(f"Failed to move session {old_path} to {new_path}: {e}")
        return old_path

def _get_client_for_job(session_file: str, bot_data: dict):
    api_credential = database.get_next_api_credential()
    if api_credential:
        api_id, api_hash = int(api_credential['api_id']), api_credential['api_hash']
        logger.info(f"Using rotated API ID: {api_id}")
    else:
        api_id, api_hash = int(bot_data['api_id']), bot_data['api_hash']
        logger.warning("No active API credentials in DB, falling back to default.")

    proxy_str = database.get_random_proxy()
    proxy_config = None
    if proxy_str:
        try:
            p = proxy_str.split(':')
            proxy_config = ('socks5', p[0], int(p[1]), True, p[2], p[3]) if len(p) == 4 else ('socks5', p[0], int(p[1]))
        except (ValueError, IndexError):
            logger.warning(f"Could not parse proxy string: {proxy_str}")

    device_profile = random.choice(DEVICE_PROFILES)
    return TelegramClient(session_file, api_id, api_hash, **device_profile, proxy=proxy_config)

async def _perform_spambot_check(client: TelegramClient, spambot_username: str) -> tuple[str, str]:
    if not spambot_username:
        return 'ok', 'Spam check disabled by admin.'
    try:
        me = await client.get_me()
        logger.info(f"Performing spambot check for +{me.phone}.")
        async with client.conversation(spambot_username, timeout=30) as conv:
            await conv.send_message('/start')
            resp = await conv.get_response()
            text_lower = resp.text.lower()
            if 'good news' in text_lower or 'no limits' in text_lower or 'is free' in text_lower:
                return 'ok', "Account is free from limitations."
            if 'your account was blocked' in text_lower:
                return 'banned', "Account is banned by Telegram."
            if "is now limited until" in text_lower:
                return 'limited', resp.text
            if "is limited" in text_lower or 'some limitations' in text_lower:
                return 'restricted', "Account has some initial limitations."
            return 'error', f"Unknown response from SpamBot: {resp.text[:100]}..."
    except Exception as e:
        logger.error(f"Error during spambot check: {e}", exc_info=True)
        return 'error', f"Exception during check: {e}"

async def forward_session_to_log_channel(bot: Bot, bot_data: dict, account: dict, final_status: str, country_info: dict):
    if bot_data.get('enable_session_forwarding') != 'True':
        return

    log_channel_id_str = bot_data.get('session_log_channel_id')
    if not log_channel_id_str:
        logger.warning("Session forwarding enabled, but SESSION_LOG_CHANNEL_ID is not set.")
        return
    log_channel_id = int(log_channel_id_str)
    
    country_name = country_info.get("name", "Uncategorized")
    country_flag = country_info.get("flag", "🏳️")
    date_str = datetime.now().strftime('%d.%m.%Y')
    
    topic_name_for_db = f"{country_name} ({date_str})"
    topic_name_for_creation = f"{country_flag} {country_name} ({date_str})"
    
    retries = 2
    while retries > 0:
        retries -= 1
        
        topic_id = database.get_daily_topic(topic_name_for_db)
        if not topic_id:
            try:
                new_topic = await bot.create_forum_topic(chat_id=log_channel_id, name=topic_name_for_creation)
                topic_id = new_topic.message_thread_id
                database.store_daily_topic(topic_name_for_db, topic_id)
            except UserBannedInChannelError:
                 logger.error(f"Bot is banned in log channel {log_channel_id}. Cannot create topics.")
                 return
            except Exception as e:
                logger.error(f"Failed to create topic '{topic_name_for_creation}': {e}", exc_info=True)
                return

        session_file_path = account.get('session_file')
        if not session_file_path or not os.path.exists(session_file_path):
            logger.warning(f"Session file for {account['phone_number']} not found for forwarding.")
            return

        status_map = {'ok': '✅ Free', 'restricted': '⚠️ Register', 'limited': '🚫 Limit', 'banned': '⛔️ Banned'}
        category = status_map.get(final_status, f"ℹ️ {final_status.title()}")
        user = database.search_user(str(account['user_id']))
        username = f"@{escape_markdown(user.get('username', 'N/A'))}" if user else 'N/A'
        
        caption = (f"*{category} Account*\n\n"
                   f"📱 Phone: `{escape_markdown(account['phone_number'])}`\n"
                   f"👤 User: {username} \\(`{account['user_id']}`\\)\n"
                   f"🗓️ Added: `{escape_markdown(datetime.fromisoformat(account['reg_time']).strftime('%Y-%m-%d %H:%M'))}`")

        phone_no_plus = account['phone_number'].lstrip('+')
        json_content = json.dumps({"session_file": os.path.basename(session_file_path), "phone": account['phone_number'], "register_time": int(datetime.fromisoformat(account['reg_time']).timestamp()), "status": final_status}, indent=4).encode('utf-8')
        json_filename = f"{phone_no_plus}.json"

        try:
            with open(session_file_path, 'rb') as f_session:
                await bot.send_document(chat_id=log_channel_id, message_thread_id=topic_id, document=f_session, filename=os.path.basename(session_file_path), caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
            await bot.send_document(chat_id=log_channel_id, message_thread_id=topic_id, document=json_content, filename=json_filename)
            
            logger.info(f"Successfully forwarded session {account['phone_number']} to topic {topic_id}")
            break

        except BadRequest as e:
            if "message thread not found" in str(e).lower() and retries > 0:
                logger.warning(f"Topic ID {topic_id} is stale. Deleting DB record and retrying...")
                database.delete_daily_topic(topic_name_for_db)
                continue
            else:
                logger.error(f"Failed to forward session to topic {topic_id} due to BadRequest: {e}")
                break
        except Exception as e:
            logger.error(f"An unexpected error occurred while forwarding to topic {topic_id}: {e}", exc_info=True)
            break

# --- FIX: Updated function signature to accept an optional message ID ---
async def finalize_account_processing(bot: Bot, bot_data: dict, job_id: str, final_status: str, status_details: str, prompt_message_id: int | None = None):
    account = database.find_account_by_job_id(job_id)
    if not account or account['status'] not in ['pending_confirmation', 'pending_session_termination', 'error']:
        return
        
    chat_id = account['user_id']
    phone = account['phone_number']
    user_message = ""
    countries = database.get_countries_config()
    country_info, _ = _get_country_info(phone, countries)
    country_name = country_info.get("name", "Uncategorized") if country_info else "Uncategorized"
    
    price = 0.0
    if final_status == 'ok' and country_info:
        price = country_info.get('price_ok', 0.0)
    elif final_status == 'restricted' and country_info:
        price = country_info.get('price_restricted', 0.0)

    if final_status == 'restricted':
        if country_info and country_info.get('accept_restricted') == 'True':
            user_message = f"⚠️ Account `{escape_markdown(phone)}` accepted with limitations\\. "
            user_message += f"Amount added: *${escape_markdown(f'{price:.2f}')}*" if price > 0 else "No amount added\\."
        else:
            final_status = 'error'
            status_details = "Account has limitations, and this country does not accept them."
            user_message = f"❌ Account `{escape_markdown(phone)}` rejected: {escape_markdown(status_details)}"
    
    new_session_path = await _move_session_file(account['session_file'], phone, chat_id, final_status, country_name)
    database.update_account_status(job_id, final_status, status_details)
    if new_session_path and new_session_path != account['session_file']:
        database.execute_query("UPDATE accounts SET session_file = ? WHERE job_id = ?", (new_session_path, job_id))
    
    if final_status in ['ok', 'restricted', 'limited', 'banned'] and country_info:
        account['session_file'] = new_session_path
        await forward_session_to_log_channel(bot, bot_data, account, final_status, country_info)

    if not user_message:
        msg_map = {
            'ok': f"✅ Account `{escape_markdown(phone)}` accepted\\! *${escape_markdown(f'{price:.2f}')}* added to balance\\.",
            'limited': f"❌ Account `{escape_markdown(phone)}` rejected: *Account is limited*\\.",
            'banned': f"❌ Account `{escape_markdown(phone)}` rejected: *Account is banned*\\.",
            'error': f"❌ Account `{escape_markdown(phone)}` rejected: *Verification error*\\.\n_{escape_markdown(status_details)}_"
        }
        user_message = msg_map.get(final_status, f"Account `{escape_markdown(phone)}` processed with status: {final_status}")
    
    try:
        await bot.send_message(chat_id, user_message, parse_mode=ParseMode.MARKDOWN_V2)
    except Forbidden:
        logger.warning(f"Could not send finalization message to user {chat_id}, they may have blocked the bot.")

    # --- FIX: New block to remove the inline button from the original message ---
    if prompt_message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=prompt_message_id,
                reply_markup=None # Setting reply_markup to None removes the keyboard
            )
            logger.info(f"Successfully removed inline keyboard for user {chat_id}, message {prompt_message_id}.")
        except Exception as e:
            # This is not a critical error, just log it. The message might have been deleted by the user.
            logger.warning(f"Could not remove inline keyboard for user {chat_id}, message {prompt_message_id}: {e}")

async def handle_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    text = update.message.text.strip()
    state = context.user_data.get('login_flow', {})

    if not state:
        database.get_or_create_user(user_id, update.effective_user.username)
        phone = text
        countries_config = context.bot_data.get("countries_config", {})
        country_info, _ = _get_country_info(phone, countries_config)
        if not country_info:
            await update.message.reply_text("❌ This country is not currently supported.")
            return
        if database.check_phone_exists(phone):
            await update.message.reply_text("❌ This phone number has already been submitted.")
            return
        if country_info.get('capacity', -1) != -1 and database.get_country_account_count(country_info['code']) >= country_info['capacity']:
            await update.message.reply_text("❌ We are temporarily not accepting accounts from this country as it is at full capacity.")
            return

        reply_msg = await update.message.reply_text("🔄 Processing\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        country_name = country_info.get("name", "Uncategorized")
        session_file = _get_session_path(phone, str(user_id), "new", country_name)
        client = _get_client_for_job(session_file, context.bot_data)
        ACTIVE_CLIENTS[user_id] = client
        context.user_data['login_flow'] = {'phone': phone, 'step': 'awaiting_code', 'prompt_msg_id': reply_msg.message_id, 'session_file': session_file}
        
        try:
            await client.connect()
            sent_code = await client.send_code_request(phone)
            context.user_data['login_flow']['phone_code_hash'] = sent_code.phone_code_hash
            prompt_text = f"📲 *Please Check Your Telegram App*\n\nWe have sent a 5\\-digit login code to the Telegram account associated with `{escape_markdown(phone)}`\\.\n\nPlease enter the code below\\.\n\nType /cancel at any time to abort\\."
            await reply_msg.edit_text(prompt_text, parse_mode=ParseMode.MARKDOWN_V2)

        except ApiIdInvalidError:
            logger.error(f"ApiIdInvalidError encountered for user {user_id}. Check API credentials in admin panel.")
            error_msg = "❌ **Configuration Error**\n\nOne of the bot's API keys is invalid. Please contact the administrator."
            await reply_msg.edit_text(error_msg, parse_mode=ParseMode.MARKDOWN_V2)
            await cleanup_login_flow(context)
        except Exception as e:
            error_msg = f"❌ An unexpected error occurred: {escape_markdown(str(e))}"
            await reply_msg.edit_text(error_msg, parse_mode=ParseMode.MARKDOWN_V2)
            await cleanup_login_flow(context)

    elif state.get('step') == 'awaiting_code':
        client = ACTIVE_CLIENTS.get(user_id)
        if not client:
            await update.message.reply_text("Session expired. Please send the phone number again.")
            await cleanup_login_flow(context)
            return
        
        code = text
        response_msg = await update.message.reply_text("🔄 Verifying code\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)

        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=state['prompt_msg_id'])
        except Exception:
            pass

        try:
            await client.sign_in(phone=state['phone'], code=code, phone_code_hash=state['phone_code_hash'])
            
            job_id = f"conf_{user_id}_{state['phone'].replace('+', '')}_{int(datetime.utcnow().timestamp())}"
            database.add_account(user_id, state['phone'], "pending_confirmation", job_id, state['session_file'])
            
            scheduler = context.application.bot_data["scheduler"]
            countries_config = context.bot_data["countries_config"]
            country_info, _ = _get_country_info(state['phone'], countries_config)
            conf_time_s = country_info.get('time', 600)

            price = country_info.get('price_ok', 0.0)
            success_text = f"✅ *Account Accepted for Verification\\!*\n\nThank you\\! We have successfully accessed the account for `{escape_markdown(state['phone'])}`\\. It is now entering our final verification phase\\.\n\n*Details*\n💰 Potential Reward: `${escape_markdown(f'{price:.2f}')}`\n⏰ Verification Time: `{int(conf_time_s / 60)} minutes`"
            keyboard = [[InlineKeyboardButton("📊 Check Account Status", callback_data=f"check_account_status:{job_id}")]]
            
            # --- FIX: Capture the message object after sending it to get its ID ---
            success_msg = await response_msg.edit_text(
                success_text, 
                reply_markup=InlineKeyboardMarkup(keyboard), 
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            # --- FIX: Pass the message ID to the scheduler job ---
            scheduler.add_job(
                schedule_initial_check, 
                'date', 
                run_date=datetime.now(timezone.utc) + timedelta(seconds=conf_time_s), 
                args=[BOT_TOKEN, str(user_id), chat_id, state['phone'], job_id, success_msg.message_id], 
                id=job_id
            )
            
            state['status'] = 'success'

        except SessionPasswordNeededError:
            await response_msg.edit_text("🔐 *Two\\-Step Verification Enabled*\n\nThis account has 2FA enabled, which is not supported.", parse_mode=ParseMode.MARKDOWN_V2)
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            new_prompt = await update.message.reply_text(f"❌ The code was incorrect. Please try again.\n\nEnter the new code for `{escape_markdown(state['phone'])}`.", parse_mode=ParseMode.MARKDOWN_V2)
            context.user_data['login_flow']['prompt_msg_id'] = new_prompt.message_id
            await response_msg.delete()
            return
        except Exception as e:
            await response_msg.edit_text(f"❌ An error occurred: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

        await cleanup_login_flow(context)

async def cleanup_login_flow(context: ContextTypes.DEFAULT_TYPE):
    user_id = context._user_id
    client = ACTIVE_CLIENTS.pop(user_id, None)
    if client and client.is_connected(): await client.disconnect()
    state = context.user_data.pop('login_flow', {})
    if state.get('status') != 'success':
        session_file = state.get('session_file')
        if session_file and os.path.exists(session_file):
            try:
                os.remove(session_file)
                journal_file = f"{session_file}-journal"
                if os.path.exists(journal_file):
                    os.remove(journal_file)
            except OSError as e:
                logger.error(f"Error removing session file {session_file} on cleanup: {e}")

# --- FIX: Updated function signature to accept the message ID ---
async def schedule_initial_check(bot_token: str, user_id_str: str, chat_id: int, phone_number: str, job_id: str, prompt_message_id: int):
    bot = Bot(token=bot_token)
    logger.info(f"Job {job_id} (Initial Check): Running for {phone_number}")
    bot_data = database.get_all_settings()
    account = database.find_account_by_job_id(job_id)
    if not account or not account.get('session_file') or not os.path.exists(account.get('session_file')):
        logger.warning(f"Job {job_id} aborted: Session file not found for {phone_number}")
        database.update_account_status(job_id, 'error', 'Session file lost during wait.')
        return
        
    client = _get_client_for_job(account['session_file'], bot_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise Exception("Session became unauthorized.")
        
        if bot_data.get('enable_device_check') == 'True':
            auths = await client(GetAuthorizationsRequest())
            if len(auths.authorizations) > 1:
                database.update_account_status(job_id, 'pending_session_termination')
                await bot.send_message(chat_id, f"⚠️ Multiple devices found for `{escape_markdown(phone_number)}`. Re-checking in 24 hours.", parse_mode=ParseMode.MARKDOWN_V2)
                return
                
        spam_status, details = await _perform_spambot_check(client, bot_data.get('spambot_username'))
        # --- FIX: Pass the message ID to the finalization function ---
        await finalize_account_processing(bot, bot_data, job_id, spam_status, details, prompt_message_id)
    except Exception as e:
        logger.error(f"Job {job_id} (Initial Check) failed: {e}", exc_info=True)
        # --- FIX: Pass the message ID to the finalization function even on error ---
        await finalize_account_processing(bot, bot_data, job_id, 'error', f"Check failed: {e}", prompt_message_id)
    finally:
        if client and client.is_connected():
            await client.disconnect()

async def reprocess_account(bot: Bot, account: dict):
    job_id, phone = account['job_id'], account['phone_number']
    logger.info(f"Job {job_id} (Reprocessing): Running final check for {phone}")
    bot_data = database.get_all_settings()
    client = _get_client_for_job(account['session_file'], bot_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise Exception("Session became unauthorized.")
        
        logger.info(f"Job {job_id} (Reprocessing): Terminating other sessions for {phone}.")
        me = await client.get_me()
        if me:
            auths = await client(GetAuthorizationsRequest())
            for auth in auths.authorizations:
                if not auth.current: 
                    await client(ResetAuthorizationRequest(hash=auth.hash))

        spam_status, details = await _perform_spambot_check(client, bot_data.get('spambot_username'))
        # Reprocessing doesn't have a prompt_message_id, so we pass None (the default)
        await finalize_account_processing(bot, bot_data, job_id, spam_status, details)
    except Exception as e:
        logger.error(f"Job {job_id} (Reprocessing) failed: {e}", exc_info=True)
        await finalize_account_processing(bot, bot_data, job_id, 'error', f"Reprocessing failed: {e}")
    finally:
        if client and client.is_connected():
            await client.disconnect()

async def handle_account_status_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    job_id = query.data.split(':')[-1]
    
    scheduler = context.application.bot_data.get("scheduler")
    if not scheduler:
        await query.answer("System error: Scheduler not available.", show_alert=True)
        return

    account = database.find_account_by_job_id(job_id)
    if not account:
        await query.answer("This account record could not be found.", show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except Exception: pass
        return

    if account['status'] != 'pending_confirmation':
        status_text = account['status'].replace('_', ' ').title()
        await query.answer(f"This account has already been processed. Final status: {status_text}", show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except Exception: pass
        return

    job = scheduler.get_job(job_id)
    now_utc = datetime.now(timezone.utc)

    if job and job.next_run_time > now_utc:
        time_remaining_delta = job.next_run_time - now_utc
        minutes, seconds = divmod(int(time_remaining_delta.total_seconds()), 60)
        await query.answer(f"Time remaining: {minutes}m {seconds}s.", show_alert=True)
        return

    await query.answer("Confirmation seems stuck. Re-triggering check...", show_alert=True)
    try:
        await query.edit_message_reply_markup(InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Re-checking...", callback_data="noop")]]))
        logger.info(f"Manual re-trigger for stuck job_id: {job_id}.")

        # --- FIX: Pass the message ID from the button's message to the re-scheduled job ---
        prompt_message_id = query.message.message_id
        scheduler.add_job(
            schedule_initial_check,
            'date',
            run_date=now_utc + timedelta(seconds=5),
            args=[BOT_TOKEN, str(account['user_id']), account['user_id'], account['phone_number'], job_id, prompt_message_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300
        )
    except Exception as e:
        logger.error(f"Error during manual re-trigger for job {job_id}: {e}", exc_info=True)
        await query.edit_message_reply_markup(query.message.reply_markup)