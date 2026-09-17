# -*- coding: utf-8 -*-
import os
import re
import sys
import argparse

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']))
from common import common
from common import common_db_path
from common import common_sqlite3
from common import common_config

config_lsf = common_config.load_config('lsf')

os.environ['PYTHONUNBUFFERED'] = '1'


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser(
        description='seedb — SQLite 数据库查看工具\n\n查看 SQLite 数据库中的表和数据，支持指定表名、字段和行数。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
使用示例：

  # 列出数据库中的所有表
  seedb -d /path/to/db.db

  # 查看指定表的数据
  seedb -d db.db -t queue_all host_h01

  # 查看指定表的指定字段
  seedb -d db.db -t queue_all -k NJOBS RUN

  # 只查看前 10 行
  seedb -d db.db -t queue_all -n 10
""")
    parser.add_argument("-d", "--database",
                        required=True,
                        help='必选参数，指定数据库文件')
    parser.add_argument("-t", "--tables",
                        nargs='+',
                        default=[],
                        help='指定要查看的表名（确保表存在）')
    parser.add_argument("-k", "--keys",
                        nargs='+',
                        default=[],
                        help='指定要查看的表字段（确保字段存在）')
    parser.add_argument("-n", "--number",
                        type=int,
                        default=0,
                        help='显示的行数（0 表示全部）')

    args = parser.parse_args()

    if not os.path.exists(args.database):
        if not re.match('^/.*$', args.database):
            database = str(common_db_path.resolve_db_path(config_lsf, 'lsf')) + '/' + str(args.database)

            if os.path.exists(database):
                args.database = database
            else:
                common.bprint(f'{args.database}: No such database file.', level='Error')
                sys.exit(1)
        else:
            common.bprint(f'{args.database}: No such database file.', level='Error')
            sys.exit(1)

    return args.database, args.tables, args.keys, args.number


def get_length(input_list):
    """
    Get the length of the longest item on the input list.
    """
    length = 0

    for item in input_list:
        item_length = len(item)

        if item_length > length:
            length = item_length

    return length


def get_table_columns(db_file, table_name):
    """Return the column names of a table (works even when the table is empty)."""
    columns = []
    conn = None

    try:
        (result, conn) = common_sqlite3.connect_db_file(db_file, '')

        if result == 'failed' or conn == '':
            return columns

        # Validate table_name to avoid PRAGMA injection (PRAGMA does not support binding).
        valid_tables = common_sqlite3.get_sql_table_list(db_file, conn)

        if table_name not in valid_tables:
            return columns

        curs = conn.cursor()
        rows = curs.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        columns = [row[1] for row in rows]
        curs.close()
    except Exception:
        pass
    finally:
        if conn is not None and conn != '':
            conn.close()

    return columns


def seedb(db_file, table_list, key_list, number):
    common.bprint(f'DB_FILE : {db_file}')

    if len(table_list) == 0:
        table_list = common_sqlite3.get_sql_table_list(db_file, '')

        common.bprint('TABLES  :')
        common.bprint('========')

        for table in table_list:
            common.bprint(table)

        common.bprint('========')
    else:
        for table in table_list:
            common.bprint(f'TABLE   : {table}')
            common.bprint('========')

            select_condition = ''

            if number > 0:
                select_condition = 'limit ' + str(number)

            data_dic = common_sqlite3.get_sql_table_data(db_file, '', table, key_list, select_condition)
            display_key_list = list(data_dic.keys())

            # get_sql_table_data returns an empty dict both when the user
            # specifies an invalid -k column AND when the table is simply empty.
            # Distinguish the two: on an empty result, fall back to the actual
            # table columns (PRAGMA table_info) so an empty table still shows
            # its header rather than a misleading "No valid key_list" error.
            if len(display_key_list) == 0:
                all_columns = get_table_columns(db_file, table)

                if key_list:
                    # User-specified keys that don't exist on the table.
                    invalid = [k for k in key_list if k not in all_columns]

                    if invalid:
                        common.bprint(f'Invalid key(s): {invalid}. Available: {all_columns}', level='Error')
                        common.bprint('========')
                        continue

                if not all_columns:
                    common.bprint('No such table or table has no columns.', level='Error')
                    common.bprint('========')
                    continue

                display_key_list = all_columns

            length = get_length(display_key_list)
            format_string = '%-' + str(length + 10) + 's'

            for key in display_key_list:
                common.bprint(format_string % (key), end='')

            common.bprint('')

            for key in display_key_list:
                common.bprint(format_string % ('----'), end='')

            common.bprint('')

            first_key = display_key_list[0]
            first_value_list = data_dic.get(first_key, [])

            if not first_value_list:
                common.bprint('(empty table, 0 rows)')
            else:
                for i in range(len(first_value_list)):
                    for j in range(len(display_key_list)):
                        key = display_key_list[j]
                        value_list = data_dic.get(key, [])
                        value = value_list[i] if i < len(value_list) else ''

                        common.bprint(format_string % (value), end='')

                    common.bprint('')

            common.bprint('========')


################
# Main Process #
################
def main():
    (db_file, table_list, key_list, number) = read_args()
    seedb(db_file, table_list, key_list, number)


if __name__ == '__main__':
    main()
