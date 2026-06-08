# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import datetime
import argparse
from multiprocessing import Process

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from common import common_lsf
from common import common_sqlite3

from common import common_config

config = common_config.load_config()

os.environ['LSB_NTRIES'] = '3'
os.environ["PYTHONUNBUFFERED"] = '1'


def read_args():
    """
    Read arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-c", "--cleanup",
                        action="store_true",
                        default=False,
                        help='Clean up database with expire days limitation.')
    parser.add_argument("-j", "--job",
                        action="store_true",
                        default=False,
                        help='Sample (finished) job info with command "bjobs -u all -d -UF".')
    parser.add_argument("-m", "--job_mem",
                        action="store_true",
                        default=False,
                        help='Sample (running) job mem and idle_factor(cputime/runtime) with command "bjobs -o".')
    parser.add_argument("-q", "--queue",
                        action="store_true",
                        default=False,
                        help='Sample queue info with command "bqueues".')
    parser.add_argument("-qH", "--queue_host_mapping",
                        action="store_true",
                        default=False,
                        help='Sample queue-host mapping info with command "bqueues -l".')
    parser.add_argument("-H", "--host",
                        action="store_true",
                        default=False,
                        help='Sample host info with command "bhosts".')
    parser.add_argument("-l", "--load",
                        action="store_true",
                        default=False,
                        help='Sample host load (ut/tmp/swp/mem) info with command "lsload".')
    parser.add_argument("-u", "--user",
                        action="store_true",
                        default=False,
                        help='Sample user (finished) job info with command "bjobs -u all -d -UF".')
    parser.add_argument("-U", "--utilization",
                        action="store_true",
                        default=False,
                        help='Sample utilization (slot/cpu/mem) info with command "lsload/bhosts/lshosts".')
    parser.add_argument("-UD", "--utilization_day",
                        action="store_true",
                        default=False,
                        help='Count and save utilization-day info with utilization data.')
    parser.add_argument("-A", "--analysis",
                        action="store_true",
                        default=False,
                        help='Generate an AI cluster analysis HTML report (requires AI config).')

    args = parser.parse_args()

    if not any([args.cleanup, args.job, args.job_mem, args.queue, args.queue_host_mapping, args.host, args.load, args.user, args.utilization, args.utilization_day, args.analysis]):
        common.bprint('At least one argument of "cleanup/job/job_mem/queue/queue_host_mapping/host/load/user/utilization/utilization_day/analysis" must be selected.', level='Error')
        sys.exit(1)

    return args.cleanup, args.job, args.job_mem, args.queue, args.queue_host_mapping, args.host, args.load, args.user, args.utilization, args.utilization_day, args.analysis


class Sampling:
    """
    Sample LSF basic information with LSF bjobs/bqueues/bhosts/lshosts/lsload/busers commands.
    Save the infomation into sqlite3 DB.
    """
    def __init__(self, cleanup, job_sampling, job_mem_sampling, queue_sampling, queue_host_mapping_sampling, host_sampling, load_sampling, user_sampling, utilization_sampling, utilization_day_sampling, analysis_sampling):
        self.cleanup = cleanup
        self.job_sampling = job_sampling
        self.job_mem_sampling = job_mem_sampling
        self.queue_sampling = queue_sampling
        self.queue_host_mapping_sampling = queue_host_mapping_sampling
        self.host_sampling = host_sampling
        self.load_sampling = load_sampling
        self.user_sampling = user_sampling
        self.utilization_sampling = utilization_sampling
        self.utilization_day_sampling = utilization_day_sampling
        self.analysis_sampling = analysis_sampling

        # Get sample time (use single datetime to avoid midnight race).
        now = datetime.datetime.now()
        self.sample_second = int(now.timestamp())
        self.sample_date = now.strftime('%Y%m%d')
        self.sample_time = now.strftime('%Y%m%d_%H%M%S')

        # Update self.db_path with cluster information.
        self.db_path = str(config.db_path) + '/monitor'
        (self.tool, cluster) = self.check_cluster_info()

        # Reload cluster-specific config if exists.
        common_config.reload_config_for_cluster(cluster)

        if cluster:
            self.db_path = str(config.db_path) + '/' + str(cluster)

        # Data retention days for cleanup (after cluster config reload).
        default_cleanup_expire_days = {
            'job': 90,
            'job_data': 90,
            'user': 365,
            'queue': 365,
            'queue_host_mapping': 365,
            'host': 365,
            'load': 365,
            'utilization': 365,
            'utilization_day': 365,
        }

        if hasattr(config, 'cleanup_expire_days') and isinstance(config.cleanup_expire_days, dict):
            default_cleanup_expire_days.update(config.cleanup_expire_days)

        self.cleanup_expire_days = default_cleanup_expire_days

        # Create db path.
        self.job_db_path = str(self.db_path) + '/job'
        self.job_data_db_path = str(self.db_path) + '/job_data'
        self.user_db_path = str(self.db_path) + '/user'

        common.create_dir(self.db_path, 0o1777)
        common.create_dir(self.job_db_path, 0o1777)
        common.create_dir(self.job_data_db_path, 0o1777)
        common.create_dir(self.user_db_path, 0o1777)

    def check_cluster_info(self):
        """
        Make sure LSF or Openlava environment exists.
        """
        (tool, tool_version, cluster, master) = common_lsf.get_lsid_info()

        if tool == '':
            common.bprint('Not find any LSF or Openlava environment!', date_format='%Y-%m-%d %H:%M:%S', level='Error')
            sys.exit(1)

        return tool, cluster

    def cleanup_db(self):
        """
        Clean up sqlite3 databases based on time-based expiration (self.cleanup_expire_days).
        """
        process_list = []

        p = Process(target=self._cleanup_single_db_files)
        p.start()
        process_list.append(p)

        p = Process(target=self._cleanup_date_dir, args=(self.user_db_path, 'user'))
        p.start()
        process_list.append(p)

        p = Process(target=self._cleanup_job_data_db)
        p.start()
        process_list.append(p)

        p = Process(target=self._cleanup_date_dir, args=(self.job_db_path, 'job'))
        p.start()
        process_list.append(p)

        for p in process_list:
            p.join(timeout=600)

            if p.is_alive():
                common.bprint(f'Cleanup process {p.name} timed out, terminating ...', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                p.terminate()
                p.join(timeout=10)

    def _cleanup_date_dir(self, dir_path, item_name):
        """
        Clean up date-based db directory (job/ or user/).
        Remove YYYYMMDD.db files older than expire_days.
        """
        if not os.path.exists(dir_path):
            return

        expire_days = self.cleanup_expire_days.get(item_name, 90)
        today = datetime.datetime.today()
        common.bprint(f'>>> Clean up "{dir_path}" (remove data older than {expire_days} days) ...', date_format='%Y-%m-%d %H:%M:%S')

        removed_count = 0

        for db_file_name in os.listdir(dir_path):
            if not db_file_name.endswith('.db'):
                continue

            date_str = db_file_name.replace('.db', '')

            try:
                file_date = datetime.datetime.strptime(date_str, '%Y%m%d')
            except ValueError:
                continue

            if (today - file_date).days > expire_days:
                db_file = os.path.join(dir_path, db_file_name)

                try:
                    os.remove(db_file)
                    removed_count += 1
                except Exception as error:
                    common.bprint(f'Failed on removing "{db_file}": {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)

        if removed_count > 0:
            common.bprint(f'Removed {removed_count} expired db files.', date_format='%Y-%m-%d %H:%M:%S', indent=4)

    def _cleanup_job_data_db(self):
        """
        Clean up job_data/ db files by deleting rows with sample_second older than expire_days.
        Remove empty db files after cleanup.
        """
        if not os.path.exists(self.job_data_db_path):
            return

        expire_days = self.cleanup_expire_days.get('job_data', 90)
        expire_second = int(time.time()) - expire_days * 86400
        common.bprint(f'>>> Clean up "{self.job_data_db_path}" (remove data older than {expire_days} days) ...', date_format='%Y-%m-%d %H:%M:%S')

        for db_file_name in os.listdir(self.job_data_db_path):
            if not db_file_name.endswith('.db'):
                continue

            db_file = os.path.join(self.job_data_db_path, db_file_name)
            (result, db_conn) = common_sqlite3.connect_db_file(db_file, mode='write')

            if result == 'passed':
                try:
                    curs = db_conn.cursor()
                    curs.execute("DELETE FROM job_data WHERE sample_second < ?", (expire_second,))
                    deleted = curs.rowcount
                    curs.close()
                    db_conn.commit()

                    if deleted > 0:
                        common.bprint(f'Deleted {deleted} expired rows from "{db_file_name}".', date_format='%Y-%m-%d %H:%M:%S', indent=4)

                    # Remove empty db file or VACUUM non-empty ones.
                    curs = db_conn.cursor()
                    curs.execute("SELECT COUNT(*) FROM job_data")
                    remaining = curs.fetchone()[0]
                    curs.close()

                    if remaining == 0:
                        db_conn.close()

                        try:
                            os.remove(db_file)
                            common.bprint(f'Removed empty file "{db_file_name}".', date_format='%Y-%m-%d %H:%M:%S', indent=4)
                        except Exception as error:
                            common.bprint(f'Failed on removing empty file "{db_file_name}": {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)

                        continue
                    elif deleted > 0:
                        db_conn.execute('VACUUM')
                except Exception as error:
                    common.bprint(f'Failed on cleaning up "{db_file_name}": {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)

                db_conn.close()

    def _cleanup_single_db_files(self):
        """
        Clean up single-file databases (queue.db, host.db, load.db, utilization.db, utilization_day.db)
        by deleting rows older than expire_days.
        Uses sample_second (INTEGER PK) for most dbs, sample_date (TEXT PK) for utilization_day.
        """
        item_list = ['queue', 'queue_host_mapping', 'host', 'load', 'utilization', 'utilization_day']

        for item in item_list:
            item_db_file = str(self.db_path) + '/' + str(item) + '.db'

            if not os.path.exists(item_db_file):
                continue

            expire_days = self.cleanup_expire_days.get(item, 365)
            common.bprint(f'>>> Clean up "{item_db_file}" (remove data older than {expire_days} days) ...', date_format='%Y-%m-%d %H:%M:%S')

            (result, item_db_conn) = common_sqlite3.connect_db_file(item_db_file, mode='write')

            if result == 'passed':
                try:
                    item_table_list = common_sqlite3.get_sql_table_list(item_db_file, item_db_conn)
                    total_deleted = 0

                    if item == 'utilization_day':
                        expire_date = (datetime.datetime.today() - datetime.timedelta(days=expire_days)).strftime('%Y%m%d')

                        for item_table_name in item_table_list:
                            curs = item_db_conn.cursor()
                            curs.execute(f"DELETE FROM '{item_table_name}' WHERE sample_date < ?", (expire_date,))
                            total_deleted += curs.rowcount
                            curs.close()
                    else:
                        expire_second = int(time.time()) - expire_days * 86400

                        for item_table_name in item_table_list:
                            curs = item_db_conn.cursor()
                            curs.execute(f"DELETE FROM '{item_table_name}' WHERE sample_second < ?", (expire_second,))
                            total_deleted += curs.rowcount
                            curs.close()

                    item_db_conn.commit()

                    if total_deleted > 0:
                        common.bprint(f'Deleted {total_deleted} expired rows from {len(item_table_list)} tables.', date_format='%Y-%m-%d %H:%M:%S', indent=4)
                        item_db_conn.execute('VACUUM')
                except Exception as error:
                    common.bprint(f'Failed on cleaning up "{item_db_file}": {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                finally:
                    item_db_conn.close()

    def sample_job_info(self):
        """
        Sample (finished) job information.
        """
        common.bprint('>>> Sampling job info ...', date_format='%Y-%m-%d %H:%M:%S', )
        common.bprint('* Getting finished job information with command "bjobs -u all -d -UF" ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)
        bjobs_dic = common_lsf.get_bjobs_uf_info('bjobs -u all -d -UF')

        # Re-organize jobs_dic with finished_date.
        date_bjobs_dic = {}

        for job in bjobs_dic.keys():
            finished_date = common_lsf.switch_bjobs_uf_time(bjobs_dic[job]['finished_time'], '%Y%m%d')

            if finished_date not in date_bjobs_dic:
                date_bjobs_dic[finished_date] = {}

            date_bjobs_dic[finished_date][job] = bjobs_dic[job]

        # Write db_file with finished_date.
        common.bprint('* Saving finished job information ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)
        key_list = ['job', 'job_name', 'job_description', 'user', 'project', 'status', 'interactive_mode', 'queue', 'command', 'submitted_from', 'submitted_time', 'cwd', 'processors_requested', 'requested_resources', 'span_hosts', 'rusage_mem', 'specified_hosts', 'started_on', 'started_time', 'finished_time', 'exit_code', 'term_signal', 'cpu_time', 'idle_factor', 'mem', 'swap', 'run_limit', 'pids', 'max_mem', 'avg_mem', 'pending_reasons', 'job_info']
        key_type_list = ['PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']
        key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

        for finished_date in date_bjobs_dic.keys():
            finished_date_db_file = str(self.job_db_path) + '/' + str(finished_date) + '.db'
            common.bprint(f'Writing {finished_date_db_file} ...', date_format='%Y-%m-%d %H:%M:%S', indent=6)
            (result, finished_date_db_conn) = common_sqlite3.connect_db_file(finished_date_db_file, mode='write')

            if result == 'passed':
                try:
                    common_sqlite3.create_sql_table(finished_date_db_file, finished_date_db_conn, 'job', key_string, commit=False)

                    for job in date_bjobs_dic[finished_date].keys():
                        # Insert sql table value if not exists.
                        value_list = [job, date_bjobs_dic[finished_date][job]['job_name'], date_bjobs_dic[finished_date][job]['job_description'], date_bjobs_dic[finished_date][job]['user'], date_bjobs_dic[finished_date][job]['project'], date_bjobs_dic[finished_date][job]['status'], date_bjobs_dic[finished_date][job]['interactive_mode'], date_bjobs_dic[finished_date][job]['queue'], date_bjobs_dic[finished_date][job]['command'], date_bjobs_dic[finished_date][job]['submitted_from'], date_bjobs_dic[finished_date][job]['submitted_time'], date_bjobs_dic[finished_date][job]['cwd'], date_bjobs_dic[finished_date][job]['processors_requested'], date_bjobs_dic[finished_date][job]['requested_resources'], date_bjobs_dic[finished_date][job]['span_hosts'], date_bjobs_dic[finished_date][job]['rusage_mem'], date_bjobs_dic[finished_date][job]['specified_hosts'], date_bjobs_dic[finished_date][job]['started_on'], date_bjobs_dic[finished_date][job]['started_time'], date_bjobs_dic[finished_date][job]['finished_time'], date_bjobs_dic[finished_date][job]['exit_code'], date_bjobs_dic[finished_date][job]['term_signal'], date_bjobs_dic[finished_date][job]['cpu_time'], date_bjobs_dic[finished_date][job]['idle_factor'], date_bjobs_dic[finished_date][job]['mem'], date_bjobs_dic[finished_date][job]['swap'], ' '.join(date_bjobs_dic[finished_date][job]['run_limit']), ' '.join(date_bjobs_dic[finished_date][job]['pids']), date_bjobs_dic[finished_date][job]['max_mem'], date_bjobs_dic[finished_date][job]['avg_mem'], ' '.join(date_bjobs_dic[finished_date][job]['pending_reasons']), date_bjobs_dic[finished_date][job]['job_info']]
                        value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                        common_sqlite3.insert_into_sql_table(finished_date_db_file, finished_date_db_conn, 'job', value_string, commit=False)

                    finished_date_db_conn.commit()
                except Exception as error:
                    common.bprint(f'Failed on sampling job info for {finished_date}: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                finally:
                    finished_date_db_conn.close()

        common.bprint(f'Done ({len(bjobs_dic.keys())} jobs).', date_format='%Y-%m-%d %H:%M:%S', indent=4)

    def get_bjobs_mem_idle_factor_info(self):
        """
        Get running job mem and idle_factor with "bjobs -u all -r -UF".
        Returns dict: {jobid: {'mem': <MB>, 'idle_factor': <float>}, ...}
        The mem field from get_bjobs_uf_info() is already converted to MB.
        """
        bjobs_dic = common_lsf.get_bjobs_uf_info('bjobs -u all -r -UF')

        if not bjobs_dic:
            return {}

        my_dic = {}

        for job, info in bjobs_dic.items():
            mem = info.get('mem', '')
            idle_factor = info.get('idle_factor', '')

            if mem == '':
                mem = 0

            if idle_factor:
                try:
                    idle_factor = round(float(idle_factor), 2)
                except (ValueError, TypeError):
                    idle_factor = ''

            my_dic[job] = {'mem': mem, 'idle_factor': idle_factor}

        return my_dic

    def _write_job_data_db(self, job_range_dic, bjobs_dic):
        """
        Write job mem and idle_factor data to job_data DB files using single-table schema.
        Detects recycled job IDs by checking time gap and removes stale data.
        """
        # If a job_id has no sample in the last 24 hours, treat it as a recycled ID.
        stale_gap = 24 * 3600
        batch_size = 500

        for job_range in job_range_dic.keys():
            db_file = str(self.job_data_db_path) + '/' + str(job_range) + '.db'

            (result, db_conn) = common_sqlite3.connect_db_file(db_file, mode='write')

            if result == 'passed':
                try:
                    db_conn.execute('PRAGMA synchronous=NORMAL')
                    curs = db_conn.cursor()
                    curs.execute("CREATE TABLE IF NOT EXISTS job_data (job_id TEXT, sample_second INTEGER, sample_time TEXT, mem TEXT, idle_factor TEXT, PRIMARY KEY (job_id, sample_second))")

                    # Detect and remove stale data from recycled job IDs (batched to avoid SQLite variable limit).
                    job_list = job_range_dic[job_range]
                    stale_jobs = []

                    for i in range(0, len(job_list), batch_size):
                        batch = job_list[i:i + batch_size]
                        placeholders = ','.join(['?'] * len(batch))
                        curs.execute(f"SELECT job_id, MAX(sample_second) FROM job_data WHERE job_id IN ({placeholders}) GROUP BY job_id", batch)
                        stale_jobs.extend([row[0] for row in curs.fetchall() if self.sample_second - row[1] > stale_gap])

                    if stale_jobs:
                        for i in range(0, len(stale_jobs), batch_size):
                            batch = stale_jobs[i:i + batch_size]
                            placeholders = ','.join(['?'] * len(batch))
                            curs.execute(f"DELETE FROM job_data WHERE job_id IN ({placeholders})", batch)

                    rows = [(job, self.sample_second, self.sample_time, str(bjobs_dic[job]['mem']), str(bjobs_dic[job]['idle_factor'])) for job in job_list]
                    curs.executemany("INSERT OR IGNORE INTO job_data VALUES (?, ?, ?, ?, ?)", rows)

                    curs.close()
                    db_conn.commit()
                except Exception as error:
                    common.bprint(f'Failed on writing job data to "{db_file}": {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
                finally:
                    db_conn.close()

    def sample_job_mem_info(self):
        """
        Sample (running) job mem and idle_factor, save to job_data/ with single-table schema.
        """
        common.bprint('>>> Sampling job mem/idle_factor info ...', date_format='%Y-%m-%d %H:%M:%S')

        t0 = time.time()
        bjobs_dic = self.get_bjobs_mem_idle_factor_info()
        t1 = time.time()
        job_list = list(bjobs_dic.keys())
        job_range_dic = common.get_job_range_dic(job_list, range_size=1000000)
        self._write_job_data_db(job_range_dic, bjobs_dic)
        t2 = time.time()

        common.bprint(f'Done ({len(job_list)} jobs, bjobs: {t1-t0:.1f}s, db_write: {t2-t1:.1f}s).', date_format='%Y-%m-%d %H:%M:%S', indent=4)

    def sample_queue_info(self):
        """
        Sample queue info and save it into sqlite db.
        """
        common.bprint('>>> Sampling queue info ...', date_format='%Y-%m-%d %H:%M:%S')

        queue_db_file = str(self.db_path) + '/queue.db'
        (result, queue_db_conn) = common_sqlite3.connect_db_file(queue_db_file, mode='write')

        if result == 'passed':
            try:
                queue_table_list = common_sqlite3.get_sql_table_list(queue_db_file, queue_db_conn)
                bhosts_dic = common_lsf.get_bhosts_info()
                queue_host_dic = common_lsf.get_queue_host_info()
                bqueues_dic = common_lsf.get_bqueues_info()
                queue_list = bqueues_dic['QUEUE_NAME'] + ['ALL']

                key_list = ['sample_second', 'sample_time', 'TOTAL', 'NJOBS', 'PEND', 'RUN', 'SUSP']
                key_type_list = ['INTEGER PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']
                key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

                for i in range(len(queue_list)):
                    queue = queue_list[i]
                    queue_table_name = 'queue_' + str(queue)

                    # Generate sql table if not exitst.
                    if queue_table_name not in queue_table_list:
                        common_sqlite3.create_sql_table(queue_db_file, queue_db_conn, queue_table_name, key_string, commit=False)

                    # Insert sql table value.
                    total_slots = 0

                    if queue == 'ALL':
                        for host_max in bhosts_dic['MAX']:
                            if re.match(r'^\d+$', host_max):
                                total_slots += int(host_max)

                        value_list = [self.sample_second, self.sample_time, total_slots, sum([int(i) for i in bqueues_dic['NJOBS'] if re.match(r'^\d+$', str(i))]), sum([int(i) for i in bqueues_dic['PEND'] if re.match(r'^\d+$', str(i))]), sum([int(i) for i in bqueues_dic['RUN'] if re.match(r'^\d+$', str(i))]), sum([int(i) for i in bqueues_dic['SUSP'] if re.match(r'^\d+$', str(i))])]
                    elif queue == 'lost_and_found':
                        value_list = [self.sample_second, self.sample_time, 'N/A', bqueues_dic['NJOBS'][i], bqueues_dic['PEND'][i], bqueues_dic['RUN'][i], bqueues_dic['SUSP'][i]]
                    else:
                        for queue_host in queue_host_dic.get(queue, []):
                            if queue_host in bhosts_dic['HOST_NAME']:
                                host_index = bhosts_dic['HOST_NAME'].index(queue_host)
                                host_max = bhosts_dic['MAX'][host_index]

                                if re.match(r'^\d+$', host_max):
                                    total_slots += int(host_max)

                        value_list = [self.sample_second, self.sample_time, total_slots, bqueues_dic['NJOBS'][i], bqueues_dic['PEND'][i], bqueues_dic['RUN'][i], bqueues_dic['SUSP'][i]]

                    value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                    common_sqlite3.insert_into_sql_table(queue_db_file, queue_db_conn, queue_table_name, value_string, commit=False)

                queue_db_conn.commit()
            except Exception as error:
                common.bprint(f'Failed on sampling queue info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                queue_db_conn.close()

    def sample_host_info(self):
        """
        Sample host info and save it into sqlite db.
        """
        common.bprint('>>> Sampling host info ...', date_format='%Y-%m-%d %H:%M:%S')

        host_db_file = str(self.db_path) + '/host.db'
        (result, host_db_conn) = common_sqlite3.connect_db_file(host_db_file, mode='write')

        if result == 'passed':
            try:
                host_table_list = common_sqlite3.get_sql_table_list(host_db_file, host_db_conn)
                bhosts_dic = common_lsf.get_bhosts_info()
                host_list = bhosts_dic['HOST_NAME']

                key_list = ['sample_second', 'sample_time', 'NJOBS', 'RUN', 'SSUSP', 'USUSP']
                key_type_list = ['INTEGER PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']
                key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

                for i in range(len(host_list)):
                    host = host_list[i]
                    host_table_name = 'host_' + str(host)

                    # Generate sql table if not exists.
                    if host_table_name not in host_table_list:
                        common_sqlite3.create_sql_table(host_db_file, host_db_conn, host_table_name, key_string, commit=False)

                    # Insert sql table value.
                    value_list = [self.sample_second, self.sample_time, bhosts_dic['NJOBS'][i], bhosts_dic['RUN'][i], bhosts_dic['SSUSP'][i], bhosts_dic['USUSP'][i]]
                    value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                    common_sqlite3.insert_into_sql_table(host_db_file, host_db_conn, host_table_name, value_string, commit=False)

                host_db_conn.commit()
            except Exception as error:
                common.bprint(f'Failed on sampling host info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                host_db_conn.close()

    def sample_load_info(self):
        """
        Sample host load info and save it into sqlite db.
        """
        common.bprint('>>> Sampling host load info ...', date_format='%Y-%m-%d %H:%M:%S')

        load_db_file = str(self.db_path) + '/load.db'
        (result, load_db_conn) = common_sqlite3.connect_db_file(load_db_file, mode='write')

        if result == 'passed':
            try:
                load_table_list = common_sqlite3.get_sql_table_list(load_db_file, load_db_conn)

                if self.tool == 'openlava':
                    lsload_dic = common_lsf.get_lsload_info(command='lsload -l')
                else:
                    lsload_dic = common_lsf.get_lsload_info()

                host_list = lsload_dic['HOST_NAME']

                key_list = ['sample_second', 'sample_time', 'ut', 'tmp', 'swp', 'mem']
                key_type_list = ['INTEGER PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']
                key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

                for i in range(len(host_list)):
                    host = host_list[i]
                    load_table_name = 'load_' + str(host)

                    # Generate sql table if not exists.
                    if load_table_name not in load_table_list:
                        common_sqlite3.create_sql_table(load_db_file, load_db_conn, load_table_name, key_string, commit=False)

                    # Update "ut" value.
                    if not lsload_dic['ut'][i]:
                        lsload_dic['ut'][i] = '0%'
                    else:
                        ut = re.sub(r'%', '', lsload_dic['ut'][i])

                        if re.match(r'^\d+\.\d+$', ut):
                            ut = str(int(float(ut)))

                        if not re.match(r'^\d+$', str(ut)):
                            ut = '0'
                        elif int(ut) > 100:
                            ut = '100'

                        lsload_dic['ut'][i] = str(ut) + '%'

                    # Insert sql table value.
                    value_list = [self.sample_second, self.sample_time, lsload_dic['ut'][i], lsload_dic['tmp'][i], lsload_dic['swp'][i], lsload_dic['mem'][i]]
                    value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                    common_sqlite3.insert_into_sql_table(load_db_file, load_db_conn, load_table_name, value_string, commit=False)

                load_db_conn.commit()
            except Exception as error:
                common.bprint(f'Failed on sampling host load info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                load_db_conn.close()

    def sample_user_info(self):
        """
        Sample user info.
        """
        common.bprint('>>> Sampling job info ...', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint('* Getting finished job information with command "bjobs -u all -d -UF" ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)
        bjobs_dic = common_lsf.get_bjobs_uf_info('bjobs -u all -d -UF')

        # Re-organize jobs_dic with finished_date.
        date_bjobs_dic = {}

        for job in bjobs_dic.keys():
            finished_date = common_lsf.switch_bjobs_uf_time(bjobs_dic[job]['finished_time'], '%Y%m%d')
            date_bjobs_dic.setdefault(finished_date, {})
            user = bjobs_dic[job]['user']
            date_bjobs_dic[finished_date].setdefault(user, {})
            date_bjobs_dic[finished_date][user][job] = {'status': bjobs_dic[job]['status'], 'queue': bjobs_dic[job]['queue'], 'project': bjobs_dic[job]['project'], 'rusage_mem': bjobs_dic[job]['rusage_mem'], 'max_mem': bjobs_dic[job]['max_mem']}

        # Write db_file with finished_date.
        common.bprint('* Saving user job information ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)
        key_list = ['job', 'status', 'queue', 'project', 'rusage_mem', 'max_mem']
        key_type_list = ['PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']
        key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

        for finished_date in date_bjobs_dic.keys():
            finished_date_db_file = str(self.user_db_path) + '/' + str(finished_date) + '.db'
            common.bprint(f'Writing {finished_date_db_file} ...', date_format='%Y-%m-%d %H:%M:%S', indent=6)
            (result, finished_date_db_conn) = common_sqlite3.connect_db_file(finished_date_db_file, mode='write')

            if result == 'passed':
                try:
                    user_table_list = common_sqlite3.get_sql_table_list(finished_date_db_file, finished_date_db_conn)

                    for user in date_bjobs_dic[finished_date]:
                        user_table_name = 'user_' + str(user)

                        # Generate sql table (user) if not exitst.
                        if user_table_name not in user_table_list:
                            common_sqlite3.create_sql_table(finished_date_db_file, finished_date_db_conn, user_table_name, key_string, commit=False)

                        for job in date_bjobs_dic[finished_date][user]:
                            # Insert sql table value if not exists.
                            value_list = [job, bjobs_dic[job]['status'], bjobs_dic[job]['queue'], bjobs_dic[job]['project'], bjobs_dic[job]['rusage_mem'], bjobs_dic[job]['max_mem']]
                            value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                            common_sqlite3.insert_into_sql_table(finished_date_db_file, finished_date_db_conn, user_table_name, value_string, commit=False)

                    finished_date_db_conn.commit()
                except Exception as error:
                    common.bprint(f'Failed on sampling user info for {finished_date}: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                finally:
                    finished_date_db_conn.close()

        common.bprint(f'Done ({len(bjobs_dic.keys())} jobs).', date_format='%Y-%m-%d %H:%M:%S', indent=4)

    def sample_queue_host_mapping_info(self):
        """
        Sample queue-host mapping info and save into sqlite db.
        Each queue has its own table named 'queue_<queue_name>'.
        Only saves if the mapping is different from the last recorded entry for that queue.
        """
        common.bprint('>>> Sampling queue-host mapping info ...', date_format='%Y-%m-%d %H:%M:%S')

        # Get current queue-host mapping info.
        current_queue_host_dic = common_lsf.get_queue_host_info()

        queue_host_mapping_db_file = str(self.db_path) + '/queue_host_mapping.db'
        (result, queue_host_mapping_db_conn) = common_sqlite3.connect_db_file(queue_host_mapping_db_file, mode='write')

        if result == 'passed':
            try:
                queue_host_mapping_table_list = common_sqlite3.get_sql_table_list(queue_host_mapping_db_file, queue_host_mapping_db_conn)

                saved_count = 0
                skipped_count = 0

                for queue, host_list in current_queue_host_dic.items():
                    # Generate table name for this queue
                    table_name = 'queue_' + str(queue)
                    hosts_string = ' '.join(host_list)

                    # Generate sql table if not exists.
                    if table_name not in queue_host_mapping_table_list:
                        key_list = ['sample_second', 'sample_time', 'hosts']
                        key_type_list = ['INTEGER PRIMARY KEY', 'TEXT', 'TEXT']
                        key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)
                        common_sqlite3.create_sql_table(queue_host_mapping_db_file, queue_host_mapping_db_conn, table_name, key_string, commit=False)

                    # Check if current mapping is same as last recorded mapping for this queue
                    skip_save = False

                    # Get the last recorded entry for this queue
                    last_data = common_sqlite3.get_sql_table_data(queue_host_mapping_db_file, queue_host_mapping_db_conn, table_name, ['hosts'], 'ORDER BY sample_second DESC LIMIT 1')

                    if last_data and 'hosts' in last_data and len(last_data['hosts']) > 0:
                        last_hosts_string = last_data['hosts'][0]
                        # Sort hosts for comparison
                        last_hosts = sorted(last_hosts_string.split())
                        current_hosts = sorted(host_list)

                        if current_hosts == last_hosts:
                            skip_save = True
                            skipped_count += 1

                    # Insert sql table value only if mapping changed or no previous data
                    if not skip_save:
                        value_list = [self.sample_second, self.sample_time, hosts_string]
                        value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                        common_sqlite3.insert_into_sql_table(queue_host_mapping_db_file, queue_host_mapping_db_conn, table_name, value_string, commit=False)
                        saved_count += 1

                queue_host_mapping_db_conn.commit()

                if saved_count > 0:
                    common.bprint(f'Saved queue-host mapping for {saved_count} queues ({skipped_count} unchanged).', date_format='%Y-%m-%d %H:%M:%S', indent=4)
                else:
                    common.bprint(f'Queue-host mapping unchanged for all {skipped_count} queues, skipping save.', date_format='%Y-%m-%d %H:%M:%S', indent=4)
            except Exception as error:
                common.bprint(f'Failed on sampling queue-host mapping info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                queue_host_mapping_db_conn.close()

    def sample_utilization_info(self):
        """
        Sample host resource utilization info and save it into sqlite db.
        """
        common.bprint('>>> Sampling utilization info ...', date_format='%Y-%m-%d %H:%M:%S')

        utilization_db_file = str(self.db_path) + '/utilization.db'
        (result, utilization_db_conn) = common_sqlite3.connect_db_file(utilization_db_file, mode='write')

        if result == 'passed':
            try:
                utilization_table_list = common_sqlite3.get_sql_table_list(utilization_db_file, utilization_db_conn)
                bhosts_dic = common_lsf.get_bhosts_info()
                lshosts_dic = common_lsf.get_lshosts_info()

                if self.tool == 'openlava':
                    lsload_dic = common_lsf.get_lsload_info(command='lsload -l')
                else:
                    lsload_dic = common_lsf.get_lsload_info()

                host_list = lsload_dic['HOST_NAME']

                key_list = ['sample_second', 'sample_time', 'slot', 'cpu', 'mem']
                key_type_list = ['INTEGER PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT']
                key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

                for i in range(len(host_list)):
                    host = host_list[i]
                    utilization_table_name = 'utilization_' + str(host)

                    # Generate sql table if not exists.
                    if utilization_table_name not in utilization_table_list:
                        common_sqlite3.create_sql_table(utilization_db_file, utilization_db_conn, utilization_table_name, key_string, commit=False)

                    # Get slot_utilization.
                    slot_utilization = 0

                    for (j, host_name) in enumerate(bhosts_dic['HOST_NAME']):
                        if (host_name == host) and re.match(r'^\d+$', bhosts_dic['NJOBS'][j]) and re.match(r'^\d+$', bhosts_dic['MAX'][j]) and (int(bhosts_dic['MAX'][j]) != 0):
                            slot_utilization = round(int(bhosts_dic['NJOBS'][j])/int(bhosts_dic['MAX'][j])*100, 1)

                            if slot_utilization > 100:
                                common.bprint(f'For host "{host}", invalid slot utilization "{slot_utilization}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)

                                if bhosts_dic['STATUS'][j] == 'unavail':
                                    slot_utilization = 0.0
                                else:
                                    slot_utilization = 100.0

                            break

                    # Get cpu_utilization.
                    cpu_utilization = 0

                    if re.match(r'^\d+%$', lsload_dic['ut'][i]):
                        cpu_utilization = re.sub('%', '', lsload_dic['ut'][i])

                    # Get mem_utilization.
                    mem_utilization = 0

                    for (k, host_name) in enumerate(lshosts_dic['HOST_NAME']):
                        if (host_name == host) and re.match(r'^(\d+(\.\d+)?)([MGT])$', lshosts_dic['maxmem'][k]) and re.match(r'^(\d+(\.\d+)?)([MGT])$', lsload_dic['mem'][i]):
                            # Get maxmem with MB.
                            maxmem_match = re.match(r'^(\d+(\.\d+)?)([MGT])$', lshosts_dic['maxmem'][k])
                            maxmem = float(maxmem_match.group(1))
                            maxmem_unit = maxmem_match.group(3)

                            if maxmem_unit == 'G':
                                maxmem = maxmem*1024
                            elif maxmem_unit == 'T':
                                maxmem = maxmem*1024*1024

                            # Get mem with MB.
                            mem_match = re.match(r'^(\d+(\.\d+)?)([MGT])$', lsload_dic['mem'][i])
                            mem = float(mem_match.group(1))
                            mem_unit = mem_match.group(3)

                            if mem_unit == 'G':
                                mem = mem*1024
                            elif mem_unit == 'T':
                                mem = mem*1024*1024

                            mem_utilization = round((maxmem-mem)*100/maxmem, 1) if maxmem > 0 else 0.0

                            if mem_utilization > 100:
                                common.bprint(f'For host "{host}", invalid mem utilization "{mem_utilization}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
                                mem_utilization = 100.0

                            break

                    # Insert sql table value.
                    value_list = [self.sample_second, self.sample_time, slot_utilization, cpu_utilization, mem_utilization]
                    value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                    common_sqlite3.insert_into_sql_table(utilization_db_file, utilization_db_conn, utilization_table_name, value_string, commit=False)

                utilization_db_conn.commit()
            except Exception as error:
                common.bprint(f'Failed on sampling utilization info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                utilization_db_conn.close()

    def get_utilization_day_info(self):
        """
        Get current day slot/cpu/mem utilizaiton info from sqlite3 database.
        Return slot/cpu/mem average utilization info with utilization_day_dic.
        """
        utilization_day_dic = {}
        begin_time = f'{self.sample_date} 00:00:00'
        begin_second = time.mktime(time.strptime(begin_time, '%Y%m%d %H:%M:%S'))
        end_time = f'{self.sample_date} 23:59:59'
        end_second = time.mktime(time.strptime(end_time, '%Y%m%d %H:%M:%S'))
        select_condition = f"WHERE sample_second BETWEEN {int(begin_second)} AND {int(end_second)}"

        utilization_db_file = str(self.db_path) + '/utilization.db'
        (result, utilization_db_conn) = common_sqlite3.connect_db_file(utilization_db_file, mode='write')

        if result == 'passed':
            try:
                utilization_table_list = common_sqlite3.get_sql_table_list(utilization_db_file, utilization_db_conn)

                for utilization_table_name in utilization_table_list:
                    # Get current day issued/in_use/utilization from sqlite3 database.
                    utilization_db_data_dic = common_sqlite3.get_sql_table_data(utilization_db_file, utilization_db_conn, utilization_table_name, ['slot', 'cpu', 'mem'], select_condition)

                    if utilization_db_data_dic:
                        # Get slot_sum/cpu_sum/mem_sum info.
                        slot_utilization_sum = 0
                        cpu_utilization_sum = 0
                        mem_utilization_sum = 0

                        for (i, slot) in enumerate(utilization_db_data_dic['slot']):
                            slot_utilization_sum += float(utilization_db_data_dic['slot'][i])
                            cpu_utilization_sum += float(utilization_db_data_dic['cpu'][i])
                            mem_utilization_sum += float(utilization_db_data_dic['mem'][i])

                        # Get slot_avg/cpu_avg/mem_avg utilizaiton info.
                        slot_avg_utilization = round(slot_utilization_sum/len(utilization_db_data_dic['slot']), 1)
                        cpu_avg_utilization = round(cpu_utilization_sum/len(utilization_db_data_dic['slot']), 1)
                        mem_avg_utilization = round(mem_utilization_sum/len(utilization_db_data_dic['slot']), 1)

                        if int(slot_avg_utilization) > 100:
                            common.bprint(f'For db table "{utilization_table_name}", invalid slot average utilization "{slot_avg_utilization}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
                            slot_avg_utilization = 100.0

                        if int(cpu_avg_utilization) > 100:
                            common.bprint(f'For db table "{utilization_table_name}", invalid cpu average utilization "{cpu_avg_utilization}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
                            cpu_avg_utilization = 100.0

                        if int(mem_avg_utilization) > 100:
                            common.bprint(f'For db table "{utilization_table_name}", invalid mem average utilization "{mem_avg_utilization}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
                            mem_avg_utilization = 100.0

                        utilization_day_dic[utilization_table_name] = {'slot': slot_avg_utilization, 'cpu': cpu_avg_utilization, 'mem': mem_avg_utilization}
            except Exception as error:
                common.bprint(f'Failed on getting utilization day info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                utilization_db_conn.close()

        return utilization_day_dic

    def count_utilization_day_info(self):
        """
        Count host resource utilization day average info and save it into sqlite db.
        """
        common.bprint('>>> Counting utilization (day average) info ...', date_format='%Y-%m-%d %H:%M:%S')

        utilization_day_db_file = str(self.db_path) + '/utilization_day.db'
        (result, utilization_day_db_conn) = common_sqlite3.connect_db_file(utilization_day_db_file, mode='write')

        if result == 'passed':
            try:
                utilization_day_table_list = common_sqlite3.get_sql_table_list(utilization_day_db_file, utilization_day_db_conn)
                utilization_day_dic = self.get_utilization_day_info()

                key_list = ['sample_date', 'slot', 'cpu', 'mem']
                key_type_list = ['TEXT PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT']
                key_string = common_sqlite3.gen_sql_table_key_string(key_list, key_type_list)

                for (utilization_day_table_name, utilization_day_table_dic) in utilization_day_dic.items():
                    host = re.sub('utilization_', '', utilization_day_table_name)
                    common.bprint(f'Counting utilization (day average) info for host "{host}" ...', date_format='%Y-%m-%d %H:%M:%S', indent=4)

                    # Generate sql table.
                    if utilization_day_table_name not in utilization_day_table_list:
                        common_sqlite3.create_sql_table(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, key_string, commit=False)

                        # Insert sql table value.
                        value_list = [self.sample_date, utilization_day_table_dic['slot'], utilization_day_table_dic['cpu'], utilization_day_table_dic['mem']]
                        value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                        common_sqlite3.insert_into_sql_table(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, value_string, commit=False)
                    else:
                        select_condition = "WHERE sample_date='" + str(self.sample_date) + "'"
                        utilization_day_db_data_dic = common_sqlite3.get_sql_table_data(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, ['slot', 'cpu', 'mem'], select_condition)

                        if utilization_day_db_data_dic:
                            # Replace sql table value.
                            set_condition = "SET slot='" + str(utilization_day_table_dic['slot']) + "', cpu='" + str(utilization_day_table_dic['cpu']) + "', mem='" + str(utilization_day_table_dic['mem']) + "'"
                            where_condition = "WHERE sample_date='" + str(self.sample_date) + "'"
                            common_sqlite3.update_sql_table_data(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, set_condition, where_condition, commit=False)
                        else:
                            # Insert sql table value.
                            value_list = [self.sample_date, utilization_day_table_dic['slot'], utilization_day_table_dic['cpu'], utilization_day_table_dic['mem']]
                            value_string = common_sqlite3.gen_sql_table_value_string(value_list)
                            common_sqlite3.insert_into_sql_table(utilization_day_db_file, utilization_day_db_conn, utilization_day_table_name, value_string, commit=False)

                utilization_day_db_conn.commit()
            except Exception as error:
                common.bprint(f'Failed on counting utilization day info: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            finally:
                utilization_day_db_conn.close()

    def sample_cluster_analysis(self):
        """
        Generate an AI cluster analysis HTML report and save it (timestamped) under
        <db_path>/ai_report/.
        """
        common.bprint('>>> Generating AI cluster analysis report ...', date_format='%Y-%m-%d %H:%M:%S')

        # Require AI configuration.
        if not (getattr(config, 'ai_api_base_url', '') and getattr(config, 'ai_api_key', '') and getattr(config, 'ai_model_name', '')):
            common.bprint('AI is not configured (ai_api_base_url/ai_api_key/ai_model_name), skip cluster analysis.', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
            return

        try:
            from common import common_ai
        except Exception as error:
            common.bprint(f'Failed to import common_ai for cluster analysis: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
            return

        try:
            # Load RAG documents (optional) so the report can cite best practices.
            docs_dir = os.path.join(str(os.environ['LSFMONITOR_INSTALL_PATH']), 'db', 'ai')
            doc_chunks = common_ai.load_ai_documents(docs_dir)

            content = common_ai.generate_cluster_analysis_report(
                api_base_url=config.ai_api_base_url,
                api_key=config.ai_api_key,
                model_name=config.ai_model_name,
                tool=self.tool,
                db_path=self.db_path,
                lmstat_path=getattr(config, 'lmstat_path', 'lmstat'),
                lmstat_bsub_command=getattr(config, 'lmstat_bsub_command', ''),
                doc_chunks=doc_chunks,
                embedding_model=getattr(config, 'ai_embedding_model_name', ''),
                embedding_api_base_url=getattr(config, 'ai_embedding_api_base_url', ''),
                embedding_api_key=getattr(config, 'ai_embedding_api_key', ''),
            )

            if not content:
                common.bprint('AI returned an empty report, nothing saved.', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)
                return

            html = common_ai.wrap_html_report(content, meta_line=f'Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Cluster db: {self.db_path}')

            report_dir = common_ai.resolve_report_dir(self.db_path)
            report_file = report_dir + '/cluster_analysis_' + str(self.sample_time) + '.html'

            with open(report_file, 'w', encoding='utf-8') as RF:
                RF.write(html)

            common.bprint(f'Cluster analysis report saved: {report_file}', date_format='%Y-%m-%d %H:%M:%S', indent=4)
        except Exception as error:
            common.bprint(f'Failed on generating cluster analysis report: {error}', date_format='%Y-%m-%d %H:%M:%S', level='Warning', indent=4)

    def sampling(self):
        start_time = time.time()

        # Cleanup.
        if self.cleanup:
            self.cleanup_db()

        # Sample.
        process_list = []

        if self.job_sampling:
            p = Process(target=self.sample_job_info)
            p.start()
            process_list.append(p)

        if self.job_mem_sampling:
            p = Process(target=self.sample_job_mem_info)
            p.start()
            process_list.append(p)

        if self.queue_sampling:
            p = Process(target=self.sample_queue_info)
            p.start()
            process_list.append(p)

        if self.queue_host_mapping_sampling:
            p = Process(target=self.sample_queue_host_mapping_info)
            p.start()
            process_list.append(p)

        if self.host_sampling:
            p = Process(target=self.sample_host_info)
            p.start()
            process_list.append(p)

        if self.load_sampling:
            p = Process(target=self.sample_load_info)
            p.start()
            process_list.append(p)

        if self.user_sampling:
            p = Process(target=self.sample_user_info)
            p.start()
            process_list.append(p)

        if self.utilization_sampling:
            p = Process(target=self.sample_utilization_info)
            p.start()
            process_list.append(p)

        if self.utilization_day_sampling:
            p = Process(target=self.count_utilization_day_info)
            p.start()
            process_list.append(p)

        for p in process_list:
            p.join(timeout=600)

            if p.is_alive():
                common.bprint(f'Sampling process {p.name} timed out, terminating ...', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                p.terminate()
                p.join(timeout=10)

        # AI cluster analysis is a single (slow) LLM call; run it inline after the
        # parallel samplers so its output and errors are visible.
        if self.analysis_sampling:
            self.sample_cluster_analysis()

        elapsed = time.time() - start_time
        common.bprint('', date_format='%Y-%m-%d %H:%M:%S')

        if elapsed >= 60:
            common.bprint(f'Total elapsed time: {elapsed / 60:.1f}m.', date_format='%Y-%m-%d %H:%M:%S')
        else:
            common.bprint(f'Total elapsed time: {elapsed:.1f}s.', date_format='%Y-%m-%d %H:%M:%S')


#################
# Main Function #
#################
def main():
    (cleanup, job, job_mem, queue, queue_host_mapping, host, load, user, utilization, utilization_day, analysis) = read_args()
    my_sampling = Sampling(cleanup, job, job_mem, queue, queue_host_mapping, host, load, user, utilization, utilization_day, analysis)
    my_sampling.sampling()


if __name__ == '__main__':
    main()
