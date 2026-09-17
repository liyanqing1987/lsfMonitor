# -*- coding: utf-8 -*-
################################
# File Name   : license_sample.py
# Author      : liyanqing.1987
# Created On  : 2023-04-23 17:30:35
# Description :
################################
import os
import re
import sys
import time
import datetime
import argparse
from multiprocessing import Process

sys.path.append(os.environ['LSFMONITOR_INSTALL_PATH'])
sys.path.append(os.path.join(os.environ['LSFMONITOR_INSTALL_PATH'], 'license'))
from common import common
from common import common_config
from common import common_db_path
from common import common_license
from common import common_sqlite3

# License config loaded via the unified loader (base config/config_license.py
# overlaid by ~/.lsfMonitor/config/config_license.py).
config = common_config.load_config('license')
os.environ['PYTHONUNBUFFERED'] = '1'


def _q(value):
    """Escape a value for a single-quoted SQL string literal (SQLite '' rule).

    Used for the WHERE clauses in sample_usage_info whose values come from
    lmstat output (feature/user/...). update_sql_table_data has no parameter
    binding hook, so we escape manually instead.
    """
    return str(value).replace("'", "''")


def read_args():
    """
    Read arguments.
    """
    parser = argparse.ArgumentParser(
        description="""
license_sample — EDA License 数据采样工具

采集 FlexNet license 服务器的 feature 使用情况，存入 SQLite 数据库，
供 license_monitor GUI 查询和展示。

从 config/license/LM_LICENSE_FILE 读取 license 服务器列表。
数据存储在 config_license.py 的 db_path 下（为空时回退到 config.py 的 db_path/license，
默认 <install>/db/license/）。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：

  # 采集 usage（feature 占用快照：谁在用、用多少）
  license_sample -u

  # 采集 utilization（feature 利用率统计）
  license_sample -U

  # 同时采集两者（推荐）
  license_sample -u -U

  # 清理过期数据（按 config_license.py 的 cleanup_expire_days 配置）
  license_sample -c

推荐 crontab 配置：
  */30 * * * * license_sample -u -U    # 每 30 分钟采集一次
  3 0 * * * license_sample -c         # 每天清理过期数据
""",
    )

    parser.add_argument('-c', '--cleanup',
                        action='store_true',
                        default=False,
                        help='清理过期数据（按 config_license.py 的 cleanup_expire_days 配置）')
    parser.add_argument('-u', '--usage',
                        action='store_true',
                        default=False,
                        help='采集 feature 使用快照（lmstat -a -i）：谁在用、用了多少')
    parser.add_argument('-U', '--utilization',
                        action='store_true',
                        default=False,
                        help='采集 feature 利用率统计（基于历史数据计算）')

    args = parser.parse_args()

    if (not args.usage) and (not args.utilization) and (not args.cleanup):
        common.bprint('At least one argument of "usage/utilization/cleanup" must be selected.', level='Error')
        sys.exit(1)

    return args.usage, args.utilization, args.cleanup


class Sampling:
    """
    Sample and save license feature information.
    """
    def __init__(self, usage_sampling, utilization_sampling, cleanup_sampling=False):
        self.usage_sampling = usage_sampling
        self.utilization_sampling = utilization_sampling
        self.cleanup_sampling = cleanup_sampling

        # Get sample time.
        self.sample_second = int(time.time())
        self.sample_date = datetime.datetime.today().strftime('%Y%m%d')
        self.sample_time = datetime.datetime.today().strftime('%Y%m%d_%H%M%S')

        # Data retention days for cleanup. Mirrors bsample's mechanism: a dict
        # keyed by item name, overridable via config_license.py's
        # cleanup_expire_days. license_sample has no cluster reload so we read
        # it directly here.
        default_cleanup_expire_days = {
            'usage': 90,
            'utilization': 90,
            'utilization_day': 365,
        }

        if hasattr(config, 'cleanup_expire_days') and isinstance(config.cleanup_expire_days, dict):
            default_cleanup_expire_days.update(config.cleanup_expire_days)

        self.cleanup_expire_days = default_cleanup_expire_days

        # Cleanup mode does not need live license data (avoids a slow lmstat
        # round-trip just to delete old rows).
        if self.cleanup_sampling and not self.usage_sampling and not self.utilization_sampling:
            self.license_dic = {}

            return

        # Get self.license_dic.
        common.bprint('>>> Sampling license usage information ...', date_format='%Y-%m-%d %H:%M:%S')

        LM_LICENSE_FILE_file = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/config/license/LM_LICENSE_FILE'

        # Collect non-empty/non-comment lines first; only override the
        # LM_LICENSE_FILE environment variable when the platform file
        # actually yields servers. An empty (comment-only) file must fall
        # back to the shell's LM_LICENSE_FILE instead of clobbering it.
        parsed_servers = []

        if os.path.exists(LM_LICENSE_FILE_file):
            with open(LM_LICENSE_FILE_file, 'r') as LLF:
                for line in LLF.readlines():
                    line = line.strip()

                    if (not re.match(r'^\s*$', line)) and (not re.match(r'^\s*#.*$', line)):
                        parsed_servers.append(line)

        if parsed_servers:
            os.environ['LM_LICENSE_FILE'] = ':'.join(parsed_servers)

        if not hasattr(config, 'lmstat_path'):
            config.lmstat_path = ''

        if not hasattr(config, 'lmstat_bsub_command'):
            config.lmstat_bsub_command = ''

        my_get_license_info = common_license.GetLicenseInfo(lmstat_path=config.lmstat_path, bsub_command=config.lmstat_bsub_command)
        self.license_dic = my_get_license_info.get_license_info()

    def create_db_path(self, db_path):
        """
        Create db_path if not exists.
        """
        if not os.path.exists(db_path):
            try:
                common.bprint(f'Create directory "{db_path}".', date_format='%Y-%m-%d %H:%M:%S', indent=4)
                # 0o755: license data is sampled by the dedicated sampler (owner)
                # and only read by others via the GUI. makedirs(mode=) sets only
                # the leaf; intermediate dirs follow umask (0o755).
                os.makedirs(db_path, mode=0o755, exist_ok=True)
                os.chmod(db_path, 0o755)
            except Exception as error:
                common.bprint('Failed on creating database directory "' + str(db_path) + '".', level='Error')
                common.bprint(error, color='red', display_method=1, indent=9)

                if not re.search('File exists', str(error)):
                    sys.exit(1)

    def cleanup_db(self):
        """
        Clean up license databases by deleting rows older than
        self.cleanup_expire_days. Mirrors bsample's time-based cleanup.

        Layout: <db_path>/license_server/<server>/<vendor>/{usage.db,
        utilization.db, utilization_day.db}. Each .db holds one table per
        feature. usage/utilization use sample_second (INTEGER); utilization_day
        uses sample_date (TEXT 'YYYYMMDD').
        """
        # resolve_db_path: config_license.db_path if set, else config.py db_path/license.
        db_root = common_db_path.resolve_db_path(config, 'license')
        license_server_root = db_root + '/license_server'

        if not os.path.isdir(license_server_root):
            common.bprint(f'License server directory "{license_server_root}" does not exist, nothing to clean up.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

            return

        # (db_filename, item_key, time_column, is_text_date)
        cleanup_targets = [
            ('usage.db', 'usage', 'sample_second', False),
            ('utilization.db', 'utilization', 'sample_second', False),
            ('utilization_day.db', 'utilization_day', 'sample_date', True),
        ]

        for license_server in os.listdir(license_server_root):
            server_dir = os.path.join(license_server_root, license_server)

            if not os.path.isdir(server_dir):
                continue

            for vendor_daemon in os.listdir(server_dir):
                vendor_dir = os.path.join(server_dir, vendor_daemon)

                if not os.path.isdir(vendor_dir):
                    continue

                for db_filename, item_key, time_column, is_text_date in cleanup_targets:
                    db_file = os.path.join(vendor_dir, db_filename)

                    if not os.path.exists(db_file):
                        continue

                    expire_days = self.cleanup_expire_days.get(item_key, 365)
                    common.bprint(f'>>> Clean up "{db_file}" (remove {item_key} data older than {expire_days} days) ...', date_format='%Y-%m-%d %H:%M:%S')

                    (result, conn) = common_sqlite3.connect_db_file(db_file, mode='write')

                    if result != 'passed':
                        continue

                    try:
                        table_list = common_sqlite3.get_sql_table_list(db_file, conn)

                        if is_text_date:
                            expire_value = (datetime.datetime.today() - datetime.timedelta(days=expire_days)).strftime('%Y%m%d')
                        else:
                            expire_value = int(time.time()) - expire_days * 86400

                        total_deleted = 0
                        cleaned_tables = 0

                        for table_name in table_list:
                            # Skip SQLite internal tables (sqlite_sequence is
                            # auto-created when a table uses AUTOINCREMENT; it has
                            # no sample_second/sample_date column and must not be
                            # touched, otherwise the whole-db cleanup aborts on
                            # "no such column").
                            if table_name.startswith('sqlite_'):
                                continue

                            curs = conn.cursor()
                            curs.execute(f"DELETE FROM '{table_name}' WHERE {time_column} < ?", (expire_value,))
                            total_deleted += curs.rowcount
                            cleaned_tables += 1
                            curs.close()

                        conn.commit()

                        if total_deleted > 0:
                            common.bprint(f'Deleted {total_deleted} expired rows from {cleaned_tables} tables.', date_format='%Y-%m-%d %H:%M:%S', indent=4)
                            conn.execute('VACUUM')
                    except Exception as error:
                        common.bprint(f'Failed on cleaning up "{db_file}": {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                    finally:
                        conn.close()

                # Sweep stale/orphaned malformed-suspect counter files in this
                # vendor dir (db files removed above, or features that stopped
                # sampling). Counter dir is created lazily by the self-heal path.
                try:
                    common_sqlite3.cleanup_suspect_counters(vendor_dir)
                except Exception:
                    pass

    def sample_usage_info(self):
        """
        Sample license feature usage info and save it into sqlite db.
        """
        common.bprint('>>> Sampling usage info ...', date_format='%Y-%m-%d %H:%M:%S')

        feature_count = 0

        for license_server in self.license_dic.keys():
            for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                db_path = str(common_db_path.resolve_db_path(config, 'license')) + '/license_server/' + str(license_server) + '/' + str(vendor_daemon)

                self.create_db_path(db_path)

                usage_db_file = str(db_path) + '/usage.db'
                (result, usage_db_conn) = common_sqlite3.connect_db_file(usage_db_file, mode='write')

                if result == 'passed':
                    usage_table_list = common_sqlite3.get_sql_table_list(usage_db_file, usage_db_conn)

                    key_list = ['id', 'sample_second', 'sample_time', 'server', 'vendor', 'feature', 'user', 'submit_host', 'execute_host', 'num', 'version', 'start_second', 'start_time']
                    key_type_list = ['INTEGER PRIMARY KEY AUTOINCREMENT', 'INTEGER', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'INTEGER', 'TEXT']

                    for feature in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                        usage_table_name = feature
                        feature_count += 1

                        common.bprint(f'Sampling usage info for "{license_server}/{vendor_daemon}/{feature}" ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)

                        if usage_table_name not in usage_table_list:
                            # Generate database table title.
                            key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)
                            common_sqlite3.create_sql_table(usage_db_file, usage_db_conn, usage_table_name, key_string, commit=False)

                            # Insert sql table value.
                            for usage_dic in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['in_use_info']:
                                start_second = common_license.switch_start_time(usage_dic['start_time'], compare_second=self.sample_second)
                                value_list = ['NULL', self.sample_second, self.sample_time, license_server, vendor_daemon, feature, usage_dic['user'], usage_dic['submit_host'], usage_dic['execute_host'], usage_dic['license_num'], usage_dic['version'], start_second, usage_dic['start_time']]
                                value_string = common_sqlite3.gen_sql_table_value_string(value_list, autoincrement=True)
                                common_sqlite3.insert_into_sql_table(usage_db_file, usage_db_conn, usage_table_name, value_string, commit=False)
                        else:
                            # Row-count cleanup (100000-item cap) removed; expiry
                            # is now time-based via `license_sample -c` reading
                            # config_license.py's cleanup_expire_days.

                            for usage_dic in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['in_use_info']:
                                select_condition = "WHERE server=? AND vendor=? AND feature=? AND user=? AND submit_host=? AND execute_host=? AND num=? AND version=? AND start_time=?"
                                select_params = [str(license_server), str(vendor_daemon), str(feature), str(usage_dic['user']), str(usage_dic['submit_host']), str(usage_dic['execute_host']), str(usage_dic['license_num']), str(usage_dic['version']), str(usage_dic['start_time'])]
                                usage_db_data_dic = common_sqlite3.get_sql_table_data(usage_db_file, usage_db_conn, usage_table_name, ['server', 'vendor', 'feature'], select_condition, select_params=select_params)

                                if usage_db_data_dic:
                                    # Replace sql table value.
                                    set_condition = "SET sample_second='" + str(self.sample_second) + "', sample_time='" + str(self.sample_time) + "'"
                                    where_condition = "WHERE server='" + _q(license_server) + "' AND vendor='" + _q(vendor_daemon) + "' AND feature='" + _q(feature) + "' AND user='" + _q(usage_dic['user']) + "' AND submit_host='" + _q(usage_dic['submit_host']) + "' AND execute_host='" + _q(usage_dic['execute_host']) + "' AND num='" + _q(usage_dic['license_num']) + "' AND version='" + _q(usage_dic['version']) + "' AND start_time='" + _q(usage_dic['start_time']) + "'"
                                    common_sqlite3.update_sql_table_data(usage_db_file, usage_db_conn, usage_table_name, set_condition, where_condition, commit=False)
                                else:
                                    # Insert sql table value.
                                    start_second = common_license.switch_start_time(usage_dic['start_time'], compare_second=self.sample_second)
                                    value_list = ['NULL', self.sample_second, self.sample_time, license_server, vendor_daemon, feature, usage_dic['user'], usage_dic['submit_host'], usage_dic['execute_host'], usage_dic['license_num'], usage_dic['version'], start_second, usage_dic['start_time']]
                                    value_string = common_sqlite3.gen_sql_table_value_string(value_list, autoincrement=True)
                                    common_sqlite3.insert_into_sql_table(usage_db_file, usage_db_conn, usage_table_name, value_string, commit=False)

                    usage_db_conn.commit()
                    usage_db_conn.close()

        common.bprint(f'Done ({feature_count} features).', date_format='%Y-%m-%d %H:%M:%S', indent=4)

    def sample_utilization_info(self):
        """
        Sample license feature utilization info and save it into sqlite db.
        """
        common.bprint('>>> Sampling utilization info ...', date_format='%Y-%m-%d %H:%M:%S')

        feature_count = 0

        for license_server in self.license_dic.keys():
            for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                db_path = str(common_db_path.resolve_db_path(config, 'license')) + '/license_server/' + str(license_server) + '/' + str(vendor_daemon)

                self.create_db_path(db_path)

                utilization_db_file = str(db_path) + '/utilization.db'
                (result, utilization_db_conn) = common_sqlite3.connect_db_file(utilization_db_file, mode='write')

                if result == 'passed':
                    utilization_table_list = common_sqlite3.get_sql_table_list(utilization_db_file, utilization_db_conn)
                    feature_utilization_dic = self.get_feature_utilization_info(specified_license_server=license_server, specified_vendor_daemon=vendor_daemon)

                    key_list = ['sample_second', 'sample_time', 'issued', 'in_use', 'utilization']
                    key_type_list = ['INTEGER PRIMARY KEY', 'TEXT', 'TEXT', 'INTEGER', 'TEXT']

                    for (feature, feature_dic) in feature_utilization_dic.items():
                        utilization_table_name = feature
                        feature_count += 1

                        common.bprint(f'Sampling utilization info for "{license_server}/{vendor_daemon}/{feature}" ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)

                        # Row-count cleanup (100000-item cap) removed; expiry is
                        # now time-based via `license_sample -c`.

                        # Generate sql table.
                        if utilization_table_name not in utilization_table_list:
                            key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)
                            common_sqlite3.create_sql_table(utilization_db_file, utilization_db_conn, utilization_table_name, key_string, commit=False)

                        # Insert sql table value.
                        value_list = [self.sample_second, self.sample_time, feature_dic['issued'], feature_dic['in_use'], feature_dic['utilization']]
                        value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                        common_sqlite3.insert_into_sql_table(utilization_db_file, utilization_db_conn, utilization_table_name, value_string, commit=False)

                    utilization_db_conn.commit()
                    utilization_db_conn.close()

        common.bprint(f'Done ({feature_count} features).', date_format='%Y-%m-%d %H:%M:%S', indent=4)

        self.count_utilization_day_info()

    def get_feature_utilization_info(self, specified_license_server, specified_vendor_daemon):
        """
        Get issued/in_use info from self.license_dic.
        Reture issued/in_use/utilization info with feature_utilization_dic.
        """
        # Get feature issed/in_use information.
        feature_utilization_dic = {}

        for license_server in self.license_dic.keys():
            if license_server == specified_license_server:
                for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                    if vendor_daemon == specified_vendor_daemon:
                        for (feature, feature_dic) in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].items():
                            feature_utilization_dic.setdefault(feature, [])
                            feature_utilization_dic[feature].append({'issued': feature_dic['issued'], 'in_use': feature_dic['in_use']})

        # Get feature utilization information.
        for (feature, feature_dic_list) in feature_utilization_dic.items():
            feature_utilization_dic[feature] = {}
            issued_sum = 0
            in_use_sum = 0

            for feature_dic in feature_dic_list:
                issued_num = feature_dic['issued']
                in_use_num = int(feature_dic['in_use'])
                in_use_sum += in_use_num

                if issued_num == 'Uncounted':
                    issued_sum = 'Uncounted'

                    if in_use_num == 0:
                        issued_num = 1
                    else:
                        issued_num = in_use_num
                else:
                    if issued_sum != 'Uncounted':
                        issued_num = int(issued_num)
                        issued_sum += issued_num

            if issued_sum == 'Uncounted':
                if in_use_sum == 0:
                    utilization = 0
                else:
                    utilization = 100
            else:
                utilization = round(100 * in_use_sum / issued_sum, 1) if issued_sum else 0

            feature_utilization_dic[feature] = {'issued': issued_sum, 'in_use': in_use_sum, 'utilization': utilization}

        return feature_utilization_dic

    def count_utilization_day_info(self):
        """
        Count license feature utilization day info and save it into sqlite db.
        """
        common.bprint('', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint('>>> Counting utilization (day average) info ...', date_format='%Y-%m-%d %H:%M:%S')

        table_count = 0

        for license_server in self.license_dic.keys():
            for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                utilization_day_db_file = str(common_db_path.resolve_db_path(config, 'license')) + '/license_server/' + str(license_server) + '/' + str(vendor_daemon) + '/utilization_day.db'
                (result, utilization_day_db_conn) = common_sqlite3.connect_db_file(utilization_day_db_file, mode='write')

                if result == 'passed':
                    utilization_day_table_list = common_sqlite3.get_sql_table_list(utilization_day_db_file, utilization_day_db_conn)
                    utilization_day_dic = self.get_utilization_day_info(specified_license_server=license_server, specified_vendor_daemon=vendor_daemon)

                    key_list = ['sample_date', 'issued', 'in_use', 'utilization']
                    key_type_list = ['TEXT PRIMARY KEY', 'TEXT', 'INTEGER', 'TEXT']

                    for (utilization_day_table_name, utilization_day_table_dic) in utilization_day_dic.items():
                        table_count += 1

                        common.bprint(f'Counting utilization (day average) info for "{license_server}/{vendor_daemon}/{utilization_day_table_name}" ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)

                        # Generate sql table.
                        if utilization_day_table_name not in utilization_day_table_list:
                            key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)
                            common_sqlite3.create_sql_table(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, key_string, commit=False)

                            # Insert sql table value.
                            value_list = [self.sample_date, utilization_day_table_dic['issued'], utilization_day_table_dic['in_use'], utilization_day_table_dic['utilization']]
                            value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                            common_sqlite3.insert_into_sql_table(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, value_string, commit=False)
                        else:
                            # Row-count cleanup (3650-item cap) removed; expiry is
                            # now time-based via `license_sample -c`.

                            select_condition = "WHERE sample_date='" + str(self.sample_date) + "'"
                            utilization_day_db_data_dic = common_sqlite3.get_sql_table_data(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, ['issued', 'in_use', 'utilization'], select_condition)

                            if utilization_day_db_data_dic:
                                # Replace sql table value.
                                set_condition = "SET issued='" + str(utilization_day_table_dic['issued']) + "', in_use='" + str(utilization_day_table_dic['in_use']) + "', utilization='" + str(utilization_day_table_dic['utilization']) + "'"
                                where_condition = "WHERE sample_date='" + str(self.sample_date) + "'"
                                common_sqlite3.update_sql_table_data(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, set_condition, where_condition, commit=False)
                            else:
                                # Insert sql table value.
                                value_list = [self.sample_date, utilization_day_table_dic['issued'], utilization_day_table_dic['in_use'], utilization_day_table_dic['utilization']]
                                value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                                common_sqlite3.insert_into_sql_table(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, value_string, commit=False)

                    utilization_day_db_conn.commit()
                    utilization_day_db_conn.close()

        common.bprint(f'Done ({table_count} features).', date_format='%Y-%m-%d %H:%M:%S', indent=4)

    def get_utilization_day_info(self, specified_license_server, specified_vendor_daemon):
        """
        Get current day issued/in_use/utilization info from sqlite3 database.
        Reture issued_avg/in_use_avg/utilization_avg info with utilization_day_dic.
        """
        utilization_day_dic = {}
        begin_time = str(self.sample_date) + ' 00:00:00'
        begin_second = time.mktime(time.strptime(begin_time, '%Y%m%d %H:%M:%S'))
        end_time = str(self.sample_date) + ' 23:59:59'
        end_second = time.mktime(time.strptime(end_time, '%Y%m%d %H:%M:%S'))
        select_condition = "WHERE sample_second BETWEEN '" + str(begin_second) + "' AND '" + str(end_second) + "'"

        for license_server in self.license_dic.keys():
            if license_server == specified_license_server:
                for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                    if vendor_daemon == specified_vendor_daemon:
                        utilization_db_file = str(common_db_path.resolve_db_path(config, 'license')) + '/license_server/' + str(license_server) + '/' + str(vendor_daemon) + '/utilization.db'

                        if os.path.exists(utilization_db_file):
                            (result, utilization_db_conn) = common_sqlite3.connect_db_file(utilization_db_file, mode='read')

                            if result == 'passed':
                                utilization_table_list = common_sqlite3.get_sql_table_list(utilization_db_file, utilization_db_conn)

                                for utilization_table_name in utilization_table_list:
                                    # Get current day issued/in_use/utilization from sqlite3 database.
                                    utilization_db_data_dic = common_sqlite3.get_sql_table_data(utilization_db_file, utilization_db_conn, utilization_table_name, ['issued', 'in_use', 'utilization'], select_condition)

                                    if utilization_db_data_dic:
                                        # Get issued_sum/in_use_sum/utilization_sum info.
                                        issued_sum = 0
                                        in_use_sum = 0
                                        utilization_sum = 0

                                        for (i, issued) in enumerate(utilization_db_data_dic['issued']):
                                            if (issued == 'Uncounted') or (issued_sum == 'Uncounted'):
                                                issued_sum = 'Uncounted'
                                            else:
                                                issued_sum += int(issued)

                                            in_use_sum += int(utilization_db_data_dic['in_use'][i])
                                            utilization_sum += float(utilization_db_data_dic['utilization'][i])

                                        # Get issued_avg/in_use_avg/utilization_avg info.
                                        if issued_sum == 'Uncounted':
                                            issued_avg = 'Uncounted'
                                        else:
                                            issued_avg = round(issued_sum / len(utilization_db_data_dic['issued']), 1)

                                        in_use_avg = round(in_use_sum / len(utilization_db_data_dic['issued']), 1)
                                        utilization_avg = round(utilization_sum / len(utilization_db_data_dic['issued']), 1)

                                        utilization_day_dic[utilization_table_name] = {'issued': issued_avg, 'in_use': in_use_avg, 'utilization': utilization_avg}

                                utilization_db_conn.close()

        return utilization_day_dic

    def sampling(self):
        # db_path is resolved per-method via common_db_path.resolve_db_path
        # (config_license.db_path if set, else config.py db_path/license).
        # Cleanup runs on the main process (no lmstat needed); sampling
        # runs in subprocesses. -c can be combined with -u/-U.
        process_list = []

        if self.cleanup_sampling:
            p = Process(target=self.cleanup_db)
            p.start()
            process_list.append(p)

        if self.usage_sampling:
            p = Process(target=self.sample_usage_info)
            p.start()
            process_list.append(p)

        if self.utilization_sampling:
            p = Process(target=self.sample_utilization_info)
            p.start()
            process_list.append(p)

        for p in process_list:
            p.join(timeout=600)

            if p.is_alive():
                common.bprint(f'Sampling process {p.name} timed out, terminating ...', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                p.terminate()
                p.join(timeout=10)


################
# Main Process #
################
def main():
    start_time = time.time()

    (usage, utilization, cleanup) = read_args()

    # Ensure db_path root is 0o1777 before Sampling.sampling() creates
    # license/ subdirs under it. For a custom db_path created on first run,
    # makedirs would otherwise leave the root at 0o755 (umask) and lock out
    # other sampler accounts from creating their own subsystem subdirs.
    try:
        license_root = common_db_path.resolve_db_path(config, 'license')
        common_db_path.ensure_db_root(os.path.dirname(license_root))
    except Exception:
        pass

    my_sampling = Sampling(usage, utilization, cleanup)
    my_sampling.sampling()

    elapsed = time.time() - start_time
    common.bprint('', date_format='%Y-%m-%d %H:%M:%S')

    if elapsed >= 60:
        common.bprint(f'Total elapsed time: {elapsed / 60:.1f}m.', date_format='%Y-%m-%d %H:%M:%S')
    else:
        common.bprint(f'Total elapsed time: {elapsed:.1f}s.', date_format='%Y-%m-%d %H:%M:%S')


if __name__ == '__main__':
    main()
