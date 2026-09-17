import os
import sys
import time
import json
import sqlite3

if 'LSFMONITOR_INSTALL_PATH' in os.environ:
    sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']))

from common import common

JOURNAL_STALE_SECONDS = 600

# A malformed db is only archived after this many *consecutive* quick_check
# failures, so a transient glitch (NFS hiccup, a still-running write, OOM kill
# of the previous sampler) does NOT cause premature archiving + data loss.
# Counters persist across sampler processes via on-disk files under
# <db_dir>/.malformed-suspect/. Reset to 0 on any successful quick_check.
MALFORMED_CONFIRM_THRESHOLD = 3


def _malformed_suspect_count(db_file, mode='read'):
    """Read/write the consecutive-malformed failure count for db_file.

    mode='read'  → return current count (0 if no counter file).
    mode='bump'  → increment by 1, return the new count.
    mode='reset' → delete the counter file, return 0.
    Counter files live in a hidden subdir next to the db so they don't
    pollute the data dir and are easy to clean up.
    """
    db_dir = os.path.dirname(db_file)
    suspect_dir = os.path.join(db_dir, '.malformed-suspect')

    if mode == 'reset':
        count_file = os.path.join(suspect_dir, os.path.basename(db_file) + '.count')

        try:
            os.remove(count_file)
        except OSError:
            pass

        return 0

    count_file = os.path.join(suspect_dir, os.path.basename(db_file) + '.count')

    if mode == 'read':
        try:
            with open(count_file) as CF:
                return int(CF.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    if mode == 'bump':
        try:
            os.makedirs(suspect_dir, exist_ok=True)
            current = 0

            try:
                with open(count_file) as CF:
                    current = int(CF.read().strip() or 0)
            except (OSError, ValueError):
                current = 0

            current += 1

            with open(count_file, 'w') as CF:
                CF.write(str(current))

            return current
        except OSError:
            # Can't persist counter (read-only fs, perms) — assume threshold
            # reached so we still archive a genuinely malformed file.
            return MALFORMED_CONFIRM_THRESHOLD

    return 0


# Max seconds to spend row-recovering a malformed db before archiving. Bounds
# sampler latency on very large job_data files (millions of rows). Whatever was
# recovered by then is kept; the rest is lost with the archived file.
MALFORMED_RECOVER_TIMEOUT = 60


def _recover_malformed_db(old_db_file, new_conn):
    """Row-by-row recover what's readable from a malformed db into new_conn.

    .recover (sqlite3 CLI dot-command) isn't available (CLI missing / no
    libreadline.so.6), and conn.iterdump() bails on the first malformed page.
    This walks each table's rows with fetchone(), which yields everything up to
    the first corrupt page, then stops. Inserted into the already-empty new db
    with INSERT OR IGNORE so partial PK collisions don't fail.
    Returns the number of rows recovered across all tables.
    """
    recovered = 0

    try:
        src = sqlite3.connect(old_db_file)
        src.execute('PRAGMA busy_timeout=30000')
    except Exception:
        return 0

    start_second = time.time()

    try:
        # Discover tables (sqlite_master is usually intact even when data
        # pages are corrupt — schema lives in page 1).
        try:
            tables = [row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        except Exception:
            tables = []

        for table in tables:
            if time.time() - start_second > MALFORMED_RECOVER_TIMEOUT:
                break

            # Read the CREATE statement so the new db gets the same schema.
            try:
                create_sql = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()

                if create_sql and create_sql[0]:
                    new_conn.execute(create_sql[0])
            except Exception:
                # Can't read schema for this table — skip it.
                continue

            # Get column list for this table.
            try:
                col_info = src.execute(f"PRAGMA table_info({table})").fetchall()
                cols = [c[1] for c in col_info]

                if not cols:
                    continue

                col_list = ', '.join(cols)
                placeholders = ', '.join(['?'] * len(cols))
            except Exception:
                continue

            # Walk rows with fetchone(); the first corrupt page raises
            # DatabaseError, which ends recovery for this table.
            try:
                cursor = src.execute(f"SELECT {col_list} FROM {table}")
                batch = []

                while True:
                    if time.time() - start_second > MALFORMED_RECOVER_TIMEOUT:
                        break

                    try:
                        row = cursor.fetchone()
                    except sqlite3.DatabaseError:
                        break

                    if row is None:
                        break

                    batch.append(row)

                    if len(batch) >= 1000:
                        new_conn.executemany(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})", batch)
                        recovered += len(batch)
                        batch = []

                if batch:
                    new_conn.executemany(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})", batch)
                    recovered += len(batch)
            except Exception:
                # Best-effort: keep whatever we got so far, move on.
                pass

        new_conn.commit()
    except Exception:
        pass
    finally:
        try:
            src.close()
        except Exception:
            pass

    return recovered


# Suspect counter files older than this (seconds) are removed by
# cleanup_suspect_counters, so the .malformed-suspect/ dir doesn't accumulate
# stale entries for db files that stopped being sampled.
SUSPECT_COUNTER_STALE_SECONDS = 7 * 86400


def cleanup_suspect_counters(db_dir):
    """Remove stale/orphaned malformed-suspect counter files under db_dir.

    Called from bsample/license_sample -c cleanup. A counter file is removed if:
      - its db file no longer exists (orphan: db was deleted by -c cleanup), or
      - it hasn't been touched in SUSPECT_COUNTER_STALE_SECONDS (stale: that
        range stopped sampling and the counter was never reset by a success).
    """
    suspect_dir = os.path.join(db_dir, '.malformed-suspect')

    if not os.path.isdir(suspect_dir):
        return 0

    removed = 0
    now = time.time()

    for name in os.listdir(suspect_dir):
        if not name.endswith('.count'):
            continue

        count_file = os.path.join(suspect_dir, name)

        # Each counter is named "<db_file_basename>.count" — recover the db path.
        db_name = name[:-len('.count')]
        db_file = os.path.join(db_dir, db_name)

        try:
            is_orphan = not os.path.exists(db_file)
            is_stale = (now - os.path.getmtime(count_file)) > SUSPECT_COUNTER_STALE_SECONDS
        except OSError:
            is_orphan = True
            is_stale = False

        if is_orphan or is_stale:
            try:
                os.remove(count_file)
                removed += 1
            except OSError:
                pass

    # Remove the suspect dir itself if empty (keep things tidy).
    try:
        os.rmdir(suspect_dir)
    except OSError:
        pass

    if removed > 0:
        log_malformed_event('suspect_cleanup', db_dir, removed=removed)

    return removed


def _malformed_event_log_path():
    """Resolve the JSONL event log path for db-health events.

    Lives under <db_path>/log/malformed_event.log (same dir as log.db), falling
    back to ~/.lsfMonitor/db/log/ when the shared dir isn't writable — mirrors
    common_app_log.init_app_log_db's resolution so the log always lands
    somewhere, never blocks the caller.
    """
    try:
        from common import common_db_path

        shared_dir = common_db_path.resolve_db_path(None, 'log')
        fallback_dir = os.path.expanduser('~/.lsfMonitor/db/log')

        if os.path.isdir(shared_dir):
            log_dir = shared_dir if os.access(shared_dir, os.W_OK) else fallback_dir
        else:
            try:
                os.makedirs(shared_dir, exist_ok=True)
                log_dir = shared_dir
            except PermissionError:
                log_dir = fallback_dir
                os.makedirs(log_dir, exist_ok=True)

        return os.path.join(log_dir, 'malformed_event.log')
    except Exception:
        return os.path.join(os.path.expanduser('~/.lsfMonitor/db/log'), 'malformed_event.log')


def log_malformed_event(event, db_file, **detail):
    """Append a db-health event as one JSON line to malformed_event.log.

    Best-effort: any failure is swallowed so logging never blocks the sampler.
    Events:
      - quick_check_fail   : quick_check failed (detail: fail_count, threshold)
      - recover_ok         : rows recovered before rebuild (detail: rows_recovered)
      - archive_rebuild    : corrupt file replaced by recovered/empty db
      - suspect_cleanup    : stale/orphan counter files removed (detail: removed)
    Each line: {"ts": "...", "event": "...", "db_file": "...", "user": "...",
                 "detail": {...}}
    """
    try:
        log_path = _malformed_event_log_path()

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
        except OSError:
            pass

        record = {
            'ts': datetime_strftime(),
            'event': event,
            'db_file': str(db_file),
            'user': _safe_getuser(),
            'detail': detail,
        }

        with open(log_path, 'a') as LF:
            LF.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


def datetime_strftime():
    """ISO8601 timestamp for log records (avoids importing datetime at top)."""
    import datetime

    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _safe_getuser():
    """getpass.getuser with a fallback (never raises)."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return ''


def connect_db_file(db_file, mode='read'):
    """
    Connect specified db_file with read/write mode.
    """
    result = 'passed'
    conn = ''

    if mode == 'write':
        journal_db_file = str(db_file) + '-journal'

        if os.path.exists(journal_db_file):
            try:
                journal_mtime = os.path.getmtime(journal_db_file)
                journal_age = time.time() - journal_mtime
            except OSError:
                journal_age = 0

            if journal_age > JOURNAL_STALE_SECONDS:
                common.bprint(f'Stale journal file "{journal_db_file}" detected (age: {int(journal_age)}s), removing it.', level='Warning')

                try:
                    os.remove(journal_db_file)
                except OSError as error:
                    common.bprint(f'Failed to remove stale journal file: {error}', level='Error')
                    result = 'locked'
                    return result, conn
            else:
                common.bprint(f'Database file "{db_file}" is on another connection (journal age: {int(journal_age)}s), will not connect it.', level='Warning')
                result = 'locked'
                return result, conn
    elif mode == 'read':
        if not os.path.exists(db_file):
            common.bprint(f'"{db_file}" No such database file.', level='Error')
            result = 'failed'
            return result, conn

    try:
        # sqlite3.connect auto-creates the file; track existence beforehand.
        db_file_existed = os.path.exists(db_file)

        conn = sqlite3.connect(db_file)
        conn.execute('PRAGMA busy_timeout=30000')

        # New db files: 0o644 so the sampler owner writes, others only read.
        # Shared multi-user logs (ai_log/run_log) re-chmod to 0o666 themselves.
        if mode == 'write' and not db_file_existed and os.path.exists(db_file):
            try:
                os.chmod(db_file, 0o644)
            except OSError:
                pass

        # Self-heal is passive: quick_check is O(db size) and prohibitive on
        # multi-GB NFS dbs, so it is NOT run on every connect. A malformed db
        # fails on the first write; the write wrappers catch that and call
        # _heal_malformed_db (quick_check → archive → recover → rebuild) then
        # retry once. Zero cost on the healthy path.
    except Exception as error:
        common.bprint(f'Failed on connecting database file "{db_file}".', level='Error')
        common.bprint(error, color='red', display_method=1, indent=9)
        result = 'failed'

        if conn != '':
            try:
                conn.close()
            except Exception:
                pass

            conn = ''

    return result, conn


def _heal_malformed_db(db_file, conn):
    """Passive self-heal: called by the write wrappers when a write fails.

    quick_checks the db; if malformed, follows the same archive → recover →
    rebuild flow that connect_db_file used to run eagerly on every connect.
    Returns (result, new_conn):
      - ('passed', conn)  : db was healthy (transient error); caller may retry
                             the write on the original conn (closed+reopened).
      - ('locked', '')     : malformed but under MALFORMED_CONFIRM_THRESHOLD;
                             caller should skip this write (next run retries).
      - ('failed', '')      : recovered+rebuilt (or recovery failed); caller
                             should reconnect via connect_db_file and retry once.
    """
    # Caller's conn is no longer trusted after a failed write — close it and
    # open a fresh one for quick_check.
    if conn is not None and conn != '':
        try:
            conn.close()
        except Exception:
            pass

    is_malformed = False

    try:
        conn = sqlite3.connect(db_file)
        conn.execute('PRAGMA busy_timeout=30000')
        check_result = conn.execute('PRAGMA quick_check').fetchone()
        is_malformed = (not check_result) or (str(check_result[0]).strip() != 'ok')
    except Exception:
        # Retry once — a transient I/O error on NFS / a still-running
        # write should not be misread as permanent corruption.
        try:
            conn.close()
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_file)
            conn.execute('PRAGMA busy_timeout=30000')
            check_result = conn.execute('PRAGMA quick_check').fetchone()
            is_malformed = (not check_result) or (str(check_result[0]).strip() != 'ok')
        except Exception:
            is_malformed = True

    # Healthy db: the write failure was transient (not corruption). Close the
    # check conn and let the caller retry on a freshly opened connection.
    if not is_malformed:
        _malformed_suspect_count(db_file, mode='reset')

        try:
            conn.close()
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_file)
            conn.execute('PRAGMA busy_timeout=30000')
        except Exception:
            return ('failed', '')

        return ('passed', conn)

    fail_count = _malformed_suspect_count(db_file, mode='bump')

    if fail_count < MALFORMED_CONFIRM_THRESHOLD:
        common.bprint(f'Database file "{db_file}" failed quick_check ({fail_count}/{MALFORMED_CONFIRM_THRESHOLD}), skipping this write.', level='Warning')
        log_malformed_event('quick_check_fail', db_file, fail_count=fail_count, threshold=MALFORMED_CONFIRM_THRESHOLD)

        try:
            conn.close()
        except Exception:
            pass

        return ('locked', '')

    common.bprint(f'Database file "{db_file}" is malformed after {fail_count} consecutive failures, recovering and rebuilding it.', level='Warning')
    log_malformed_event('archive_rebuild', db_file, fail_count=fail_count)

    try:
        conn.close()
    except Exception:
        pass

    _malformed_suspect_count(db_file, mode='reset')

    # Recover readable rows into a temp db, then swap it in for the
    # corrupt one. .recover (CLI) isn't available here; row-by-row
    # fetchone yields everything up to the first corrupt page.
    # Whatever is recovered is kept; the rest is lost.
    recovered_db = str(db_file) + '.recovering.' + str(int(time.time()))
    recovered_rows = 0
    new_conn = None

    try:
        new_conn = sqlite3.connect(recovered_db)
        new_conn.execute('PRAGMA busy_timeout=30000')
        recovered_rows = _recover_malformed_db(db_file, new_conn)
        new_conn.close()
        new_conn = None
    except Exception as error:
        common.bprint(f'Failed on recovering malformed db file "{db_file}": {error}', level='Error')

        if new_conn is not None:
            try:
                new_conn.close()
            except Exception:
                pass

    # Swap: remove the corrupt file, move the recovered db into place.
    try:
        os.remove(db_file)

        if os.path.exists(recovered_db):
            os.rename(recovered_db, db_file)
    except OSError as error:
        common.bprint(f'Failed to swap recovered db for "{db_file}": {error}', level='Error')

        try:
            if os.path.exists(recovered_db):
                os.remove(recovered_db)
        except OSError:
            pass

        return ('failed', '')

    if recovered_rows > 0:
        common.bprint(f'Recovered {recovered_rows} rows from malformed db "{db_file}".', level='Warning')

    log_malformed_event('recover_ok', db_file, rows_recovered=recovered_rows)

    # Connect the rebuilt db.
    try:
        conn = sqlite3.connect(db_file)
        conn.execute('PRAGMA busy_timeout=30000')

        if os.path.exists(db_file):
            try:
                os.chmod(db_file, 0o644)
            except OSError:
                pass
    except Exception as error:
        common.bprint(f'Failed on connecting rebuilt db file "{db_file}".', level='Error')
        common.bprint(error, color='red', display_method=1, indent=9)

        return ('failed', '')

    return ('passed', conn)


def connect_preprocess(db_file, orig_conn, mode='read'):
    """
    Extension for connect_db_file(), can use orig_conn instead of repeated connection.
    """
    if orig_conn == '':
        (result, conn) = connect_db_file(db_file, mode)
    else:
        result = 'passed'
        conn = orig_conn

    if result == 'failed' or result == 'locked':
        return result, conn, None

    curs = conn.cursor()

    return result, conn, curs


def get_sql_table_list(db_file, orig_conn):
    """
    Get all of the tables from the specified db file.
    """
    table_list = []
    (result, conn, curs) = connect_preprocess(db_file, orig_conn)

    if result != 'passed':
        return table_list

    try:
        command = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        results = curs.execute(command)
        all_items = results.fetchall()

        for item in all_items:
            (key,) = item
            table_list.append(key)

        curs.close()
    except Exception as error:
        common.bprint(f'Failed on getting table list on db_file "{db_file}".', level='Error')
        common.bprint(error, color='red', display_method=1, indent=9)
    finally:
        if orig_conn == '':
            conn.close()

    return table_list


def get_sql_table_key_list(db_file, orig_conn, table_name, key):
    """
    Get key list from the specified table on specified db file.
    """
    key_list = []
    (result, conn, curs) = connect_preprocess(db_file, orig_conn)

    # 'locked' (fresh -journal) 与 'failed' 都不可用,统一提前返回,避免对
    # conn='' / curs=None 调用 .execute() / .close() 抛 AttributeError。
    if result != 'passed':
        return key_list

    try:
        command = "SELECT " + str(key) + " FROM '" + str(table_name) + "'"
        curs.execute(command)
        key_list = [row[0] for row in curs.fetchall()]
        curs.close()
    except Exception as error:
        common.bprint(f'Failed on getting table key list on db_file "{db_file}".', level='Error')
        common.bprint(error, color='red', display_method=1, indent=9)
    finally:
        if orig_conn == '':
            conn.close()

    return key_list


def get_sql_table_data(db_file, orig_conn, table_name, key_list=None, select_condition='', select_params=None):
    """
    With specified db_file-table_name, get all data from specified key_list.
    """
    data_dic = {}
    (result, conn, curs) = connect_preprocess(db_file, orig_conn)

    # 'locked' (fresh -journal) 与 'failed' 都不可用,统一提前返回,避免对
    # conn='' / curs=None 调用 .execute() / .close() 抛 AttributeError。
    if result != 'passed':
        return data_dic

    try:
        command = "SELECT * FROM '" + str(table_name) + "'"

        if select_condition:
            command = str(command) + ' ' + str(select_condition)

        if select_params:
            results = curs.execute(command, select_params)
        else:
            results = curs.execute(command)

        all_items = results.fetchall()
        table_key_list = [row[0] for row in curs.description]
        curs.close()

        if not key_list:
            key_list = table_key_list
        else:
            for key in key_list:
                if key not in table_key_list:
                    common.bprint(f'"{key}": invalid key on specified key list.', level='Error')
                    return data_dic

        for item in all_items:
            value_list = list(item)

            for i in range(len(table_key_list)):
                key = table_key_list[i]

                if key in key_list:
                    value = value_list[i]

                    if key in data_dic.keys():
                        data_dic[key].append(value)
                    else:
                        data_dic[key] = [value, ]
    except Exception as error:
        common.bprint(f'Failed on getting table info from table "{table_name}" of db_file "{db_file}".', level='Warning')
        common.bprint(error, color='yellow', display_method=1, indent=11)
    finally:
        if orig_conn == '':
            conn.close()

    return data_dic


def create_sql_table(db_file, orig_conn, table_name, init_string, commit=True):
    """
    Create a table if it not exists, initialization the setting.
    """
    (result, conn, curs) = connect_preprocess(db_file, orig_conn, mode='write')

    if (result == 'failed') or (result == 'locked'):
        return

    try:
        command = "CREATE TABLE IF NOT EXISTS '" + str(table_name) + "' " + str(init_string)
        curs.execute(command)
        curs.close()

        if commit:
            conn.commit()
    except Exception as error:
        common.bprint(f'Failed on creating table "{table_name}" on db file "{db_file}".', level='Error')
        common.bprint(error, color='red', display_method=1, indent=9)

        # Passive self-heal: heal malformed db and retry once. Skipped for
        # orig_conn callers (healing a shared conn mid-transaction is unsafe).
        if orig_conn == '':
            (heal_result, healed_conn) = _heal_malformed_db(db_file, conn)

            if heal_result == 'passed' and healed_conn != '':
                try:
                    healed_curs = healed_conn.cursor()
                    healed_curs.execute(command)
                    healed_curs.close()

                    if commit:
                        healed_conn.commit()
                except Exception as retry_error:
                    common.bprint(f'Retry after heal also failed on creating table "{table_name}" on db file "{db_file}".', level='Error')
                    common.bprint(retry_error, color='red', display_method=1, indent=9)

                try:
                    healed_conn.close()
                except Exception:
                    pass
    finally:
        if commit and orig_conn == '':
            conn.close()


def insert_into_sql_table(db_file, orig_conn, table_name, value_string, commit=True):
    """
    Insert new value into sql table.
    """
    (result, conn, curs) = connect_preprocess(db_file, orig_conn, mode='write')

    if (result == 'failed') or (result == 'locked'):
        return

    try:
        command = "INSERT OR IGNORE INTO '" + str(table_name) + "' VALUES " + str(value_string)
        curs.execute(command)
        curs.close()

        if commit:
            conn.commit()
    except Exception as error:
        common.bprint(f'Failed on inserting specified values into table "{table_name}" on db file "{db_file}".', level='Error')
        common.bprint(error, color='red', display_method=1, indent=9)

        # Passive self-heal: heal malformed db and retry once. Skipped for
        # orig_conn callers (healing a shared conn mid-transaction is unsafe).
        if orig_conn == '':
            (heal_result, healed_conn) = _heal_malformed_db(db_file, conn)

            if heal_result == 'passed' and healed_conn != '':
                try:
                    healed_curs = healed_conn.cursor()
                    healed_curs.execute(command)
                    healed_curs.close()

                    if commit:
                        healed_conn.commit()
                except Exception as retry_error:
                    common.bprint(f'Retry after heal also failed on inserting into table "{table_name}" on db file "{db_file}".', level='Error')
                    common.bprint(retry_error, color='red', display_method=1, indent=9)

                try:
                    healed_conn.close()
                except Exception:
                    pass
    finally:
        if commit and orig_conn == '':
            conn.close()


def update_sql_table_data(db_file, orig_conn, table_name, set_condition='', where_condition='', commit=True):
    """
    Update sql table with set_condition on where_condition.
    """
    if set_condition and where_condition:
        (result, conn, curs) = connect_preprocess(db_file, orig_conn, mode='write')

        if (result == 'failed') or (result == 'locked'):
            return

        try:
            command = "UPDATE '" + str(table_name) + "' " + str(set_condition) + " " + str(where_condition)
            curs.execute(command)
            curs.close()

            if commit:
                conn.commit()
        except Exception as error:
            common.bprint(f'Failed on updating table "{table_name}" on db file "{db_file}".', level='Error')
            common.bprint(error, color='red', display_method=1, indent=9)

            # Passive self-heal: a write failure may indicate a malformed db
            # (the common case this catches). quick_check → recover → rebuild, then
            # retry the write once on the rebuilt connection. orig_conn callers
            # manage their own connection — for them, just report and let the next
            # run retry (healing a shared orig_conn mid-transaction is unsafe).
            if orig_conn == '':
                (heal_result, healed_conn) = _heal_malformed_db(db_file, conn)

                if heal_result == 'passed' and healed_conn != '':
                    try:
                        healed_curs = healed_conn.cursor()
                        healed_curs.execute(command)
                        healed_curs.close()

                        if commit:
                            healed_conn.commit()
                    except Exception as retry_error:
                        common.bprint(f'Retry after heal also failed on updating table "{table_name}" on db file "{db_file}".', level='Error')
                        common.bprint(retry_error, color='red', display_method=1, indent=9)

                    try:
                        healed_conn.close()
                    except Exception:
                        pass
        finally:
            if commit and orig_conn == '':
                conn.close()


def gen_sql_table_key_string(key_list, key_type_list=None):
    """
    Switch the input key_list into the sqlite table key string.
    """
    key_string = '('

    for i in range(len(key_list)):
        key = key_list[i]

        if key_type_list and len(key_type_list) == len(key_list):
            key_type = key_type_list[i]
        else:
            key_type = 'TEXT'

        if i > 0:
            key_string += ' '

        key_string = str(key_string) + "'" + str(key) + "' " + str(key_type)

        if i < len(key_list) - 1:
            key_string = str(key_string) + ","
        else:
            key_string = str(key_string) + ");"

    return key_string


def gen_sql_table_value_string(value_list, autoincrement=False):
    """
    Switch the input value_list into the sqlite table value string.
    """
    value_string = '('

    for i in range(len(value_list)):
        value = str(value_list[i]).replace("'", "''")

        if i == 0:
            if autoincrement and (value == 'NULL'):
                value_string = str(value_string) + 'NULL,'
            else:
                value_string = str(value_string) + "'" + str(value) + "',"
        elif i == len(value_list) - 1:
            if autoincrement and (value == 'NULL'):
                value_string = str(value_string) + ' NULL);'
            else:
                value_string = str(value_string) + " '" + str(value) + "');"
        else:
            if autoincrement and (value == 'NULL'):
                value_string = str(value_string) + ' NULL,'
            else:
                value_string = str(value_string) + " '" + str(value) + "',"

    return value_string
