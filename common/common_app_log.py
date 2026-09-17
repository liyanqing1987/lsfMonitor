# -*- coding: utf-8 -*-
#
# common_app_log.py
#
# Author: liyanqing.1987
# Created: 2026-09-03
# Description: Tool launch log database operations. Each GUI launch appends one
#   record to a shared SQLite db (<install>/db/log/log.db), one per-user table
#   (app_log_<user>). Writing is best-effort: failures never block the GUI.

import os
import sys
import datetime

sys.path.append(str(os.environ.get('LSFMONITOR_INSTALL_PATH', '')))
from common import common_sqlite3

# 'id' is an auto-increment surrogate key (not a business key) so INSERT OR
# IGNORE never drops a record — a user can legitimately launch twice per second.
TABLE_KEY_LIST = ['id', 'date', 'time', 'user', 'login_user', 'cwd', 'command', 'host']
TABLE_KEY_TYPE_LIST = ['INTEGER PRIMARY KEY AUTOINCREMENT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']


def gen_table_name(user):
    """Generate per-user table name."""
    return f'app_log_{user}'


def init_app_log_db():
    """Return the app launch log db file path, falling back to ~/.lsfMonitor/db/log/
    when the shared path is not writable."""
    from common import common_db_path

    shared_dir = common_db_path.resolve_db_path(None, 'log')
    fallback_dir = os.path.expanduser('~/.lsfMonitor/db/log')

    if os.path.isdir(shared_dir):
        log_db_dir = shared_dir if os.access(shared_dir, os.W_OK) else fallback_dir
    else:
        try:
            os.makedirs(shared_dir, exist_ok=True)
            log_db_dir = shared_dir
        except PermissionError:
            log_db_dir = fallback_dir
            os.makedirs(log_db_dir, exist_ok=True)

    try:
        os.chmod(log_db_dir, 0o1777)
    except PermissionError:
        pass

    return os.path.join(log_db_dir, 'log.db')


def _ensure_user_table(db_file, user):
    """Create the per-user table if it does not exist."""
    db_file_exists = os.path.exists(db_file)

    table_name = gen_table_name(user)
    key_string = common_sqlite3.gen_sql_table_key_string(TABLE_KEY_LIST, TABLE_KEY_TYPE_LIST)
    common_sqlite3.create_sql_table(db_file, '', table_name, key_string)

    if not db_file_exists and os.path.exists(db_file):
        try:
            # 0o666 (sticky bit is for dirs, not files): every user writes here.
            os.chmod(db_file, 0o666)
        except PermissionError:
            pass


def save_app_start(user, cwd, command, host):
    """Insert a launch record into the user's table. Best-effort, never raises."""
    try:
        db_file = init_app_log_db()
        _ensure_user_table(db_file, user)

        now = datetime.datetime.now()
        login_user = os.environ.get('USER', '')

        table_name = gen_table_name(user)
        value_list = ['NULL', now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'), user, login_user, cwd, command, host]
        value_string = common_sqlite3.gen_sql_table_value_string(value_list, autoincrement=True)
        common_sqlite3.insert_into_sql_table(db_file, '', table_name, value_string)
    except Exception:
        pass
