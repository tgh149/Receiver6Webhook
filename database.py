# START OF FILE database.py
import sqlite3
import logging
import json
from datetime import datetime, date
import threading
from functools import wraps
import os
import re

logger = logging.getLogger(__name__)

DB_FILE = os.path.abspath("bot.db")
db_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def db_transaction(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with db_lock:
            conn = get_db_connection()
            try:
                result = func(conn, *args, **kwargs)
                conn.commit()
                return result
            except Exception as e:
                conn.rollback()
                logger.error(f"DB transaction failed in {func.__name__}: {e}", exc_info=True)
                raise
            finally:
                conn.close()
    return wrapper

def _execute(query, params=(), fetch=None):
    with db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch == 'one':
                result = cursor.fetchone()
                return dict(result) if result else None
            if fetch == 'all':
                results = cursor.fetchall()
                return [dict(row) for row in results]
            conn.commit()
            return cursor.lastrowid if "INSERT" in query.upper() else cursor.rowcount
        except Exception as e:
            conn.rollback()
            logger.error(f"DB execute failed for query '{query[:100]}...': {e}", exc_info=True)
            raise
        finally:
            conn.close()

def fetch_one(query, params=()):
    return _execute(query, params, fetch='one')
def fetch_all(query, params=()):
    return _execute(query, params, fetch='all')
def execute_query(query, params=()):
    return _execute(query, params)

@db_transaction
def init_db(conn):
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, is_blocked INTEGER DEFAULT 0, join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, manual_balance_adjustment REAL DEFAULT 0.0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (telegram_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, phone_number TEXT NOT NULL UNIQUE, reg_time TIMESTAMP NOT NULL, status TEXT NOT NULL, status_details TEXT, job_id TEXT, session_file TEXT, last_status_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP, exported_at TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users (telegram_id) ON DELETE CASCADE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL NOT NULL, address TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'pending', account_ids TEXT, processed_by INTEGER, rejection_reason TEXT, FOREIGN KEY (user_id) REFERENCES users (telegram_id) ON DELETE CASCADE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS countries (code TEXT PRIMARY KEY, name TEXT, flag TEXT, time INTEGER, capacity INTEGER DEFAULT -1, price_ok REAL DEFAULT 0.0, price_restricted REAL DEFAULT 0.0, forum_topic_id TEXT, accept_restricted TEXT DEFAULT 'True', accept_gmail TEXT DEFAULT 'False')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxies (id INTEGER PRIMARY KEY AUTOINCREMENT, proxy TEXT UNIQUE NOT NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS api_credentials (id INTEGER PRIMARY KEY AUTOINCREMENT, api_id TEXT UNIQUE NOT NULL, api_hash TEXT NOT NULL, is_active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_used TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, message_text TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_read INTEGER DEFAULT 0, FOREIGN KEY (user_id) REFERENCES users (telegram_id) ON DELETE CASCADE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admin_log (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, details TEXT, FOREIGN KEY (admin_id) REFERENCES admins (telegram_id) ON DELETE SET NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS daily_topics (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_name TEXT NOT NULL, topic_id INTEGER NOT NULL, date_created DATE NOT NULL, UNIQUE(topic_name, date_created))''')

    table_info_accounts = {row['name'] for row in cursor.execute("PRAGMA table_info(accounts)").fetchall()}
    if 'exported_at' not in table_info_accounts:
        cursor.execute("ALTER TABLE accounts ADD COLUMN exported_at TIMESTAMP")
    
    table_info_countries = {row['name'] for row in cursor.execute("PRAGMA table_info(countries)").fetchall()}
    if 'forum_topic_id' not in table_info_countries:
        cursor.execute("ALTER TABLE countries ADD COLUMN forum_topic_id TEXT")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts (status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_phone ON accounts (phone_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts (user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals (status)")
    default_settings = {'api_id': '25707049', 'api_hash': '676a65f1f7028e4d969c628c73fbfccc', 'admin_channel': '@RAESUPPORT', 'support_id': str(6158106622), 'spambot_username': '@SpamBot', 'two_step_password': '123456', 'enable_spam_check': 'True', 'enable_device_check': 'False', 'enable_2fa': 'False', 'bot_status': 'ON', 'add_account_status': 'UNLOCKED', 'min_withdraw': '1.0', 'max_withdraw': '100.0', 'welcome_message': "🎉 Welcome to the Account Receiver Bot!\n\nTo add an account, simply send the phone number with the country code (e.g., `+12025550104`).\n\nUse the buttons below to navigate.", 'help_message': "🆘 Bot Help & Guide\n\n🔹 `/start` - Displays the main welcome message.\n🔹 `/balance` - Shows your detailed balance and allows withdrawal.\n🔹 `/rules` - View the bot's rules.\n🔹 `/cancel` - Stops any ongoing process you started.", 'rules_message': "📜 Bot Rules\n\n1. Do not use the same phone number multiple times.\n2. Any attempt to exploit or cheat the bot will result in a permanent ban without appeal.\n3. The administration is not responsible for any account limitations or issues that arise after a successful confirmation.", 'support_message': "If you need help, please describe your issue in a message below. Our support team will get back to you shortly."}
    for key, value in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    if cursor.execute("SELECT COUNT(*) FROM countries").fetchone()[0] == 0:
        default_countries = [("+44", "UK", "🇬🇧", 600, 100, 0.62, 0.10, None, "True", "False"), ("+95", "Myanmar", "🇲🇲", 60, 50, 0.18, 0.0, None, "True", "False"),]
        cursor.executemany("INSERT OR IGNORE INTO countries (code, name, flag, time, capacity, price_ok, price_restricted, forum_topic_id, accept_restricted, accept_gmail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", default_countries)
    logger.info("Database initialized/checked successfully.")

def get_daily_topic(topic_name: str) -> int | None:
    """Fetches the topic ID for a given name for today's date."""
    today = date.today()
    topic = fetch_one("SELECT topic_id FROM daily_topics WHERE topic_name = ? AND date_created = ?", (topic_name, today))
    return topic['topic_id'] if topic else None

def store_daily_topic(topic_name: str, topic_id: int):
    """Stores a new topic ID for a given name for today's date."""
    today = date.today()
    execute_query("INSERT OR IGNORE INTO daily_topics (topic_name, topic_id, date_created) VALUES (?, ?, ?)", (topic_name, topic_id, today))

# --- NEW: Function to delete a stale topic record ---
def delete_daily_topic(topic_name: str):
    """Deletes the topic record for today to allow for recreation."""
    today = date.today()
    return execute_query("DELETE FROM daily_topics WHERE topic_name = ? AND date_created = ?", (topic_name, today))

def clear_old_topics():
    """Removes topic records older than 2 days to keep the table clean."""
    count = execute_query("DELETE FROM daily_topics WHERE date_created < date('now', '-2 days')")
    if count > 0:
        logger.info(f"Cron job: Cleared {count} old daily topic records from the database.")
# --- END NEW ---

def get_withdrawal_by_id(withdrawal_id):
    return fetch_one("SELECT w.*, u.username FROM withdrawals w JOIN users u ON w.user_id = u.telegram_id WHERE w.id = ?", (withdrawal_id,))
@db_transaction
def process_withdrawal_request(conn, user_id, address, amount):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO withdrawals (user_id, amount, address, status) VALUES (?, ?, ?, 'pending')", (user_id, amount, address))
    return cursor.lastrowid
@db_transaction
def update_withdrawal_status(conn, withdrawal_id, new_status, admin_id, reason=None):
    cursor = conn.cursor()
    withdrawal = conn.execute("SELECT * FROM withdrawals WHERE id = ? AND status = 'pending'", (withdrawal_id,)).fetchone()
    if not withdrawal:
        return None, None
    user_id, amount = withdrawal['user_id'], withdrawal['amount']
    if new_status == 'completed':
        accounts = conn.execute("SELECT id, phone_number, status FROM accounts WHERE user_id = ? AND status IN ('ok', 'restricted')", (user_id,)).fetchall()
        cfg = {row['code']: dict(row) for row in conn.execute("SELECT * FROM countries").fetchall()}
        earned_balance, account_ids_to_withdraw = 0.0, []
        for acc in accounts:
            account_ids_to_withdraw.append(acc['id'])
            mc_code = next((c for c in sorted(cfg.keys(), key=len, reverse=True) if acc['phone_number'].startswith(c)), None)
            if mc_code:
                country_cfg = cfg.get(mc_code, {})
                if acc['status'] == 'ok': earned_balance += country_cfg.get('price_ok', 0.0)
                elif acc['status'] == 'restricted': earned_balance += country_cfg.get('price_restricted', 0.0)
        cursor.execute("UPDATE withdrawals SET status = 'completed', processed_by = ?, account_ids = ? WHERE id = ?", (admin_id, json.dumps(account_ids_to_withdraw), withdrawal_id))
        if account_ids_to_withdraw:
            placeholders = ','.join('?' for _ in account_ids_to_withdraw)
            cursor.execute(f"UPDATE accounts SET status = 'withdrawn' WHERE id IN ({placeholders})", account_ids_to_withdraw)
        manual_part_of_withdrawal = max(0, amount - earned_balance)
        if manual_part_of_withdrawal > 0:
            cursor.execute("UPDATE users SET manual_balance_adjustment = manual_balance_adjustment - ? WHERE telegram_id = ?", (manual_part_of_withdrawal, user_id))
        log_admin_action(conn, admin_id, "WITHDRAWAL_APPROVE", f"ID: {withdrawal_id}, User: {user_id}, Amount: ${amount:.2f}")
        return dict(withdrawal), "approved"
    elif new_status == 'rejected':
        cursor.execute("UPDATE withdrawals SET status = 'rejected', processed_by = ?, rejection_reason = ? WHERE id = ?", (admin_id, reason, withdrawal_id))
        log_admin_action(conn, admin_id, "WITHDRAWAL_REJECT", f"ID: {withdrawal_id}, User: {user_id}, Reason: {reason}")
        return dict(withdrawal), "rejected"
    return None, None
def get_user_balance_details(uid):
    pending_amount = (fetch_one("SELECT SUM(amount) FROM withdrawals WHERE user_id = ? AND status = 'pending'", (uid,)) or {'SUM(amount)': 0.0})['SUM(amount)'] or 0.0
    accs = fetch_all("SELECT id, phone_number, status FROM accounts WHERE user_id = ?", (uid,))
    manual_adjustment = (fetch_one("SELECT manual_balance_adjustment FROM users WHERE telegram_id = ?", (uid,)) or {'manual_balance_adjustment': 0.0})['manual_balance_adjustment']
    summary, earned_balance, withdrawable_accs = {}, 0.0, []
    cfg = get_countries_config()
    for acc in accs:
        summary[acc['status']] = summary.get(acc['status'], 0) + 1
        if acc['status'] in ['ok', 'restricted']:
            withdrawable_accs.append(acc)
            mc_code = next((c for c in sorted(cfg.keys(), key=len, reverse=True) if acc['phone_number'].startswith(c)), None)
            if mc_code:
                country_cfg = cfg.get(mc_code, {})
                if acc['status'] == 'ok': earned_balance += country_cfg.get('price_ok', 0.0)
                elif acc['status'] == 'restricted': earned_balance += country_cfg.get('price_restricted', 0.0)
    total_balance = round(max(0, earned_balance + manual_adjustment - pending_amount), 2)
    return summary, total_balance, earned_balance, manual_adjustment, withdrawable_accs
def get_countries_config(): return {row['code']: row for row in fetch_all("SELECT * FROM countries ORDER BY name")}
def get_country_by_code(code): return fetch_one("SELECT * FROM countries WHERE code = ?", (code,))
def get_country_account_count(code): return (fetch_one("SELECT COUNT(*) as c FROM accounts WHERE phone_number LIKE ? AND status NOT IN ('withdrawn', 'exported')", (f"{code}%",)) or {'c': 0})['c']
def get_country_account_counts_by_status(code_prefix: str): return fetch_all("SELECT status, COUNT(*) as count FROM accounts WHERE phone_number LIKE ? AND exported_at IS NULL GROUP BY status", (f"{code_prefix}%",))
def get_country_exported_account_counts_by_status(code_prefix: str):
    return fetch_all("SELECT status, COUNT(*) as count FROM accounts WHERE phone_number LIKE ? AND exported_at IS NOT NULL GROUP BY status", (f"{code_prefix}%",))
def update_country_value(code, key, value): return execute_query(f"UPDATE countries SET {key} = ? WHERE code = ?", (value, code))
def add_country(code, name, flag, time, capacity, price_ok, price_restricted): return execute_query("INSERT INTO countries (code, name, flag, time, capacity, price_ok, price_restricted) VALUES (?, ?, ?, ?, ?, ?, ?)", (code, name, flag, time, capacity, price_ok, price_restricted))
def delete_country(code): return execute_query("DELETE FROM countries WHERE code = ?", (code,))

def get_country_topic_ids(code: str):
    """Parses the comma-separated topic IDs from the database."""
    country = get_country_by_code(code)
    if not country or not country.get('forum_topic_id'):
        return None, None, None
    try:
        parts = str(country['forum_topic_id']).split(',')
        return tuple(int(p) if p and p.isdigit() else None for p in parts) if len(parts) == 3 else (None, None, None)
    except (ValueError, IndexError):
        return None, None, None

def update_country_topic_ids(code, free=None, register=None, limit=None):
    """Updates the comma-separated topic IDs for a country."""
    c_free, c_register, c_limit = get_country_topic_ids(code)
    topic_str = f"{free or c_free or ''},{register or c_register or ''},{limit or c_limit or ''}"
    return update_country_value(code, 'forum_topic_id', topic_str)

def get_sessions_by_country_and_statuses(country_code, statuses: list, limit=None, export_status='unexported'):
    placeholders = ', '.join('?' for _ in statuses)
    query = f"SELECT * FROM accounts WHERE phone_number LIKE ? AND status IN ({placeholders})"
    params = [f"{country_code}%"] + statuses

    if export_status == 'unexported':
        query += " AND exported_at IS NULL"
    elif export_status == 'exported':
        query += " AND exported_at IS NOT NULL"
    
    query += " ORDER BY reg_time DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    return fetch_all(query, params)
def mark_accounts_as_exported(account_ids: list):
    if not account_ids: return 0
    placeholders = ', '.join('?' for _ in account_ids)
    return execute_query(f"UPDATE accounts SET exported_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", account_ids)
def get_paginated_sessions_by_country_and_status(country_code, status, page=1, limit=10):
    offset = (page - 1) * limit
    base_query = "FROM accounts WHERE phone_number LIKE ? AND status = ?"
    params = [f"{country_code}%", status]
    total_items = fetch_one(f"SELECT COUNT(*) as c {base_query}", params)['c']
    sessions = fetch_all(f"SELECT * {base_query} ORDER BY reg_time DESC LIMIT ? OFFSET ?", params + [limit, offset])
    return sessions, total_items
def get_or_create_user(tid, username=None):
    user = fetch_one("SELECT * FROM users WHERE telegram_id = ?", (tid,))
    if not user:
        execute_query("INSERT INTO users (telegram_id, username) VALUES (?, ?)", (tid, username))
        return fetch_one("SELECT * FROM users WHERE telegram_id = ?", (tid,)), True
    elif username and user.get('username') != username:
        execute_query("UPDATE users SET username = ? WHERE telegram_id = ?", (username, tid))
    return user, False
def search_user(identifier):
    """
    Searches for a user by username (e.g., '@test') or by numeric ID.
    """
    if identifier.startswith('@'):
        return fetch_one("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (identifier[1:],))
    try:
        user_id = int(identifier)
        return fetch_one("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
    except ValueError:
        return None

def get_all_users(page=1, limit=10, filter_by='all'):
    offset = (page - 1) * limit
    base_query = "SELECT u.*, (SELECT COUNT(*) FROM accounts WHERE user_id = u.telegram_id) as account_count FROM users u"
    count_query = "SELECT COUNT(*) as c FROM users u"
    params = []
    
    if filter_by == 'blocked':
        where_clause = " WHERE u.is_blocked = 1"
        base_query += where_clause
        count_query += where_clause

    total_users = fetch_one(count_query, params)['c']
    users_query = f"{base_query} ORDER BY join_date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    users = fetch_all(users_query, tuple(params))
    return users, total_users

def get_top_users_by_balance(limit=10):
    all_users = fetch_all("SELECT telegram_id, username FROM users WHERE is_blocked = 0")
    if not all_users: return []
    balances = [(user, get_user_balance_details(user['telegram_id'])[1]) for user in all_users]
    sorted_users = sorted(balances, key=lambda item: item[1], reverse=True)
    top_users_data = [user for user, balance in sorted_users[:limit] if balance > 0]
    return top_users_data

def add_admin(tid): return execute_query("INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)", (tid,))
def remove_admin(tid): return execute_query("DELETE FROM admins WHERE telegram_id = ?", (tid,))
def is_admin(tid): return fetch_one("SELECT 1 FROM admins WHERE telegram_id = ?", (tid,)) is not None
def get_all_admins(): return fetch_all("SELECT * FROM admins")
@db_transaction
def log_admin_action(conn, admin_id, action, details=None):
    conn.execute("INSERT INTO admin_log (admin_id, action, details) VALUES (?, ?, ?)", (admin_id, action, details))
def get_admin_log(page=1, limit=20):
    offset = (page - 1) * limit
    total = fetch_one("SELECT COUNT(*) as c FROM admin_log")['c']
    query = "SELECT l.*, a.telegram_id as admin_tid FROM admin_log l LEFT JOIN admins a ON l.admin_id = a.telegram_id ORDER BY l.timestamp DESC LIMIT ? OFFSET ?"
    logs = fetch_all(query, (limit, offset))
    return logs, total
def get_setting(key, default=None): return (fetch_one("SELECT value FROM settings WHERE key = ?", (key,)) or {}).get('value', default)
def get_all_settings(): return {row['key']: row['value'] for row in fetch_all("SELECT * FROM settings")}
def set_setting(key, value): return execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
def block_user(tid): return execute_query("UPDATE users SET is_blocked = 1 WHERE telegram_id = ?", (tid,))
def unblock_user(tid): return execute_query("UPDATE users SET is_blocked = 0 WHERE telegram_id = ?", (tid,))
def adjust_user_balance(user_id, amount): return execute_query("UPDATE users SET manual_balance_adjustment = manual_balance_adjustment + ? WHERE telegram_id = ?", (amount, user_id))
def add_proxy(proxy_str): return execute_query("INSERT OR IGNORE INTO proxies (proxy) VALUES (?)", (proxy_str,))
def remove_proxy_by_id(pid): return execute_query("DELETE FROM proxies WHERE id = ?", (pid,))
def get_all_proxies(page=1, limit=10):
    proxies = fetch_all("SELECT * FROM proxies ORDER BY id LIMIT ? OFFSET ?", (limit, (page - 1) * limit))
    total = fetch_one("SELECT COUNT(*) as c FROM proxies")['c']
    return proxies, total
def get_random_proxy(): return (fetch_one("SELECT proxy FROM proxies ORDER BY RANDOM() LIMIT 1") or {}).get('proxy')
def check_phone_exists(p_num): return fetch_one("SELECT 1 FROM accounts WHERE phone_number = ?", (p_num,)) is not None
def add_account(uid, p_num, status, jid, sfile): return execute_query("INSERT INTO accounts (user_id, phone_number, reg_time, status, job_id, session_file) VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)", (uid, p_num, status, jid, sfile))
def update_account_status(jid, status, details=""): return execute_query("UPDATE accounts SET status = ?, status_details = ?, last_status_update = CURRENT_TIMESTAMP WHERE job_id = ?", (status, details, jid))
def find_account_by_job_id(jid): return fetch_one("SELECT * FROM accounts WHERE job_id = ?", (jid,))
def find_account_by_id(account_id: int): return fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
def get_accounts_for_reprocessing(): return fetch_all("SELECT * FROM accounts WHERE status = 'pending_session_termination' AND last_status_update <= datetime('now', '-24 hours')")
def get_stuck_pending_accounts(): return fetch_all("SELECT * FROM accounts WHERE status = 'pending_confirmation' AND reg_time <= datetime('now', '-30 minutes')")
def get_paginated_stuck_accounts_by_country(country_code, page=1, limit=10):
    offset = (page - 1) * limit
    base_query = "FROM accounts WHERE phone_number LIKE ? AND status = 'pending_confirmation' AND reg_time <= datetime('now', '-30 minutes')"
    params = [f"{country_code}%"]
    total_items = fetch_one(f"SELECT COUNT(*) as c {base_query}", params)['c']
    sessions = fetch_all(f"SELECT * {base_query} ORDER BY reg_time ASC LIMIT ? OFFSET ?", params + [limit, offset])
    return sessions, total_items
def get_all_withdrawals(page=1, limit=10, status='completed'):
    withdrawals = fetch_all("SELECT w.*, u.username FROM withdrawals w JOIN users u ON w.user_id = u.telegram_id WHERE w.status = ? ORDER BY w.timestamp DESC LIMIT ? OFFSET ?", (status, limit, (page-1)*limit))
    total = fetch_one("SELECT COUNT(*) as c FROM withdrawals WHERE status = ?", (status,))['c']
    return withdrawals, total
def get_bot_stats():
    stats = fetch_one("SELECT (SELECT COUNT(*) FROM users) as total_users, (SELECT COUNT(*) FROM users WHERE is_blocked = 1) as blocked_users, (SELECT COUNT(*) FROM accounts) as total_accounts, (SELECT COUNT(*) FROM accounts WHERE status IN ('ok', 'restricted', 'limited', 'banned') AND exported_at IS NULL) as available_sessions, (SELECT SUM(amount) FROM withdrawals WHERE status = 'completed') as total_withdrawals_amount, (SELECT COUNT(*) FROM withdrawals) as total_withdrawals_count, (SELECT COUNT(*) FROM proxies) as total_proxies")
    stats['accounts_by_status'] = {r['status']: r['c'] for r in fetch_all("SELECT status, COUNT(*) as c FROM accounts GROUP BY status")}
    stats['total_withdrawals_amount'] = stats.get('total_withdrawals_amount') or 0.0
    return stats
@db_transaction
def purge_user_data(conn, user_id):
    cursor = conn.cursor()
    sessions = cursor.execute("SELECT session_file FROM accounts WHERE user_id = ?", (user_id,)).fetchall()
    cursor.execute("DELETE FROM users WHERE telegram_id = ?", (user_id,))
    deleted_count = cursor.rowcount
    if deleted_count == 0: return 0, []
    session_files_to_delete = [row['session_file'] for row in sessions if row['session_file']]
    return deleted_count, session_files_to_delete
def add_api_credential(api_id, api_hash): return execute_query("INSERT OR IGNORE INTO api_credentials (api_id, api_hash) VALUES (?, ?)", (api_id, api_hash))
def remove_api_credential(cid): return execute_query("DELETE FROM api_credentials WHERE id = ?", (cid,))
def get_all_api_credentials(): return fetch_all("SELECT * FROM api_credentials ORDER BY created_at")
def get_next_api_credential():
    credential = fetch_one("SELECT * FROM api_credentials WHERE is_active = 1 ORDER BY COALESCE(last_used, '1970-01-01') ASC LIMIT 1")
    if credential:
        execute_query("UPDATE api_credentials SET last_used = CURRENT_TIMESTAMP WHERE id = ?", (credential['id'],))
    return credential
def toggle_api_credential_status(cid): return execute_query("UPDATE api_credentials SET is_active = 1 - is_active WHERE id = ?", (cid,))
def log_user_message(user_id, username, message_text):
    get_or_create_user(user_id, username)
    return execute_query("INSERT INTO user_messages (user_id, username, message_text) VALUES (?, ?, ?)", (user_id, username, message_text))
def get_user_chat_history(user_id, limit=50): return fetch_all("SELECT * FROM user_messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit))
def get_unread_message_count(): return (fetch_one("SELECT COUNT(*) as count FROM user_messages WHERE is_read = 0") or {'count': 0})['count']
def get_users_with_unread_messages(): return fetch_all("SELECT user_id, username, COUNT(*) as unread_count, MAX(timestamp) as last_message FROM user_messages WHERE is_read = 0 GROUP BY user_id, username ORDER BY last_message DESC")
def mark_messages_as_read(user_id): return execute_query("UPDATE user_messages SET is_read = 1 WHERE user_id = ?", (user_id,))