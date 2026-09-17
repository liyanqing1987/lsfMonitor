# -*- coding: utf-8 -*-
#
# common_run_log.py
#
# Author: liyanqing.1987
# Created: 2026-08-31
# Description: RUN panel command history database operations. Stores the
#   command-history index in a shared SQLite db (<db_path>/run_log.db), one
#   per-user table (run_history_<user>). Full stdout detail stays in text
#   files (<db_path>/run/<user>/<date>_<time>.log); only its path is stored.

import os
import sys
import json

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']))
from common import common_sqlite3

# 'id' is an auto-increment surrogate key (not a business key) so INSERT OR
# IGNORE never drops a record — a user can legitimately run two commands/sec.
TABLE_KEY_LIST = ['id', 'date', 'time', 'user', 'login_user', 'command', 'hosts', 'log']
TABLE_KEY_TYPE_LIST = ['INTEGER PRIMARY KEY AUTOINCREMENT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']


def gen_table_name(user):
    """Generate per-user table name."""
    return f'run_history_{user}'


def init_run_log_db(db_path):
    """Return the run_log.db path under db_path, falling back to
    ~/.lsfMonitor/db/run/ when the shared path is not writable."""
    run_db_dir = str(db_path)

    try:
        os.makedirs(run_db_dir, exist_ok=True)
    except PermissionError:
        run_db_dir = os.path.expanduser('~/.lsfMonitor/db/run')
        os.makedirs(run_db_dir, exist_ok=True)

    try:
        os.chmod(run_db_dir, 0o1777)
    except PermissionError:
        pass

    db_file = os.path.join(run_db_dir, 'run_log.db')

    return db_file


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


def save_run_history(db_file, user, date, time, login_user, command, hosts_list, log_path):
    """Insert a run-history record into the user's table."""
    _ensure_user_table(db_file, user)

    table_name = gen_table_name(user)
    hosts_json = json.dumps(hosts_list or [], ensure_ascii=False)

    value_list = ['NULL', date, time, user, login_user, command, hosts_json, log_path]
    value_string = common_sqlite3.gen_sql_table_value_string(value_list, autoincrement=True)
    common_sqlite3.insert_into_sql_table(db_file, '', table_name, value_string)


def _get_target_tables(db_file, user):
    """Return the tables to operate on: the user's table if user is given,
    otherwise all run_history tables."""
    table_list = common_sqlite3.get_sql_table_list(db_file, '')

    if user:
        table_name = gen_table_name(user)
        return [table_name] if table_name in table_list else []

    return [t for t in table_list if t.startswith('run_history_')]


def search_run_history(db_file, user='', date_start='', date_end='', keyword='', limit=1000):
    """Search run history by user / date range / keyword. Returns entry dicts
    [{date, time, user, login_user, command, hosts, log}, ...] newest-first;
    'hosts' is deserialized back into a list."""
    if not os.path.exists(db_file):
        return []

    conditions = []

    if date_start:
        safe_date_start = str(date_start).replace("'", "''")
        conditions.append(f"date >= '{safe_date_start}'")

    if date_end:
        safe_date_end = str(date_end).replace("'", "''")
        conditions.append(f"date <= '{safe_date_end}'")

    if keyword:
        # Escape LIKE wildcards so a literal "100%" search works.
        safe_keyword = (keyword.replace('\\', '\\\\')
                                .replace('%', '\\%')
                                .replace('_', '\\_')
                                .replace("'", "''"))
        conditions.append(f"command LIKE '%{safe_keyword}%' ESCAPE '\\'")

    where_clause = 'WHERE ' + ' AND '.join(conditions) if conditions else ''

    entries = []

    for table_name in _get_target_tables(db_file, user):
        select_condition = f"{where_clause} ORDER BY date DESC, time DESC LIMIT {int(limit)}"
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', table_name, select_condition=select_condition)

        if not data_dic or 'date' not in data_dic:
            continue

        count = len(data_dic['date'])

        for i in range(count):
            hosts_raw = data_dic.get('hosts', [''])[i] or '[]'

            try:
                hosts = json.loads(hosts_raw)
            except Exception:
                hosts = []

            entries.append({
                'date': data_dic['date'][i] or '',
                'time': data_dic.get('time', [''])[i] or '',
                'user': data_dic.get('user', [''])[i] or '',
                'login_user': data_dic.get('login_user', [''])[i] or '',
                'command': data_dic.get('command', [''])[i] or '',
                'hosts': hosts,
                'log': data_dic.get('log', [''])[i] or '',
            })

    entries.sort(key=lambda e: (e.get('date', ''), e.get('time', '')), reverse=True)

    return entries[:limit]
