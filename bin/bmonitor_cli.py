# -*- coding: utf-8 -*-
################################
# File Name   : bmonitor_cli.py
# Author      : liyanqing.1987
# Created On  : 2026-08-30
# Description : lsfMonitor CLI — exposes LSF/License query capabilities as a
#               command-line tool that outputs JSON.
#
# Usage:
#   bmonitor_cli job <jobid>                          # Job details
#   bmonitor_cli job <jobid> --diagnose pend|slow|fail # Diagnose PEND/SLOW/FAIL
#   bmonitor_cli job-hist <jobid>                     # Job history (bhist -l)
#   bmonitor_cli job-output <jobid>                   # Job stdout (bpeek)
#   bmonitor_cli jobs [--user X] [--status RUN/PEND/DONE/EXIT] [--queue X] [--host X] [--limit N]
#   bmonitor_cli hosts [--status ok/closed_Full/...] [--queue X] [--group X] [--sort-load] [--limit N]
#   bmonitor_cli queues
#   bmonitor_cli queue <queue>                          # queue detail (config + counts)
#   bmonitor_cli users [--sort RUN/NJOBS/PEND]          # per-user job counts
#   bmonitor_cli pending-reasons [--top N]              # cluster-wide pending reasons
#   bmonitor_cli cluster-summary                        # cluster aggregate metrics
#   bmonitor_cli host-load <hostname> [--days N]
#   bmonitor_cli host-detail <hostname>                  # Single host scheduling load (bhosts -l)
#   bmonitor_cli cluster-info
#   bmonitor_cli host-groups
#   bmonitor_cli license [--feature X] [--user X]
#   bmonitor_cli license-expires [--feature X]
#   bmonitor_cli license-usage [--user X]
#
#   # Historical data from sampling DB (trends & finished jobs)
#   bmonitor_cli db clusters
#   bmonitor_cli db job <jobid>
#   bmonitor_cli db jobs [--user X] [--status EXIT] [--queue X] [--exit-code N] [--days N] [--date YYYYMMDD] [--limit N]
#   bmonitor_cli db job-mem <jobid> [--days N]
#   bmonitor_cli db user <user> [--days N] [--date YYYYMMDD] [--limit N]
#   bmonitor_cli db queue <queue> [--days N]
#   bmonitor_cli db queue-hosts <queue> [--days N]      # queue member-host history
#   bmonitor_cli db group-hosts <group> [--days N]      # host-group member history
#   bmonitor_cli db host-jobs <host> [--days N]
#   bmonitor_cli db host-util <host> [--days N] [--daily]
#   bmonitor_cli db license-servers [--feature X]
#   bmonitor_cli db license-usage [--feature X] [--user X] [--days N]
#   bmonitor_cli db license-util [--feature X] [--days N] [--daily]
################################

import os
import sys
import json
import re
import time
import shlex
import argparse
import datetime

_INSTALL_PATH = os.environ.get('LSFMONITOR_INSTALL_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if _INSTALL_PATH not in sys.path:
    sys.path.insert(0, _INSTALL_PATH)

os.environ['PYTHONUNBUFFERED'] = '1'

from common import common
from common import common_lsf
from common import common_config
from common import common_sqlite3
from common import common_db_path


def _bprint_to_stderr(message, color='', background_color='', display_method='', date_format='', level='', indent=0, end='\n', save_file='', save_file_method='a'):
    """Redirect bprint to stderr so stdout stays pure JSON for CLI consumers."""
    prefix = f'*{level}*: ' if level else ''

    if indent:
        prefix = ' ' * indent + prefix

    print(prefix + str(message), file=sys.stderr, end=end)


# CLI outputs pure JSON on stdout; route common.bprint (used by common_lsf /
# common_license / common_sqlite3) to stderr so warnings never pollute it.
common.bprint = _bprint_to_stderr
config_lsf = common_config.load_config('lsf')
config_license = common_config.load_config('license')


def _json_output(data):
    """Print data as formatted JSON."""
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _sample_down(data, max_points=100):
    """Down-sample a column-oriented dict to at most max_points rows.
    Keeps every Nth row so trends survive with bounded output size.
    """
    if not data:
        return data

    keys = list(data.keys())

    if not keys:
        return data

    total = len(data[keys[0]])

    if total <= max_points:
        return data

    # 向上取整,否则 total 落在 (max_points, 2*max_points) 时 step=1 会原样
    # 返回全部点,反而超过 max_points 上限。
    step = (total + max_points - 1) // max_points

    return {k: v[::step] for k, v in data.items()}


def _resolve_lsf_db_root():
    """Resolve LSF DB root and cluster name.
    Uses common_db_path.resolve_db_path (config_lsf.db_path > config.py
    db_path/lsf > <install>/db/lsf) so it works even when config_lsf.db_path
    is empty and falls back to config.py. Mirrors bsample's logic.
    Returns (db_root, cluster).
    """
    db_path = common_db_path.resolve_db_path(config_lsf, 'lsf')

    if not db_path or not os.path.isdir(db_path):
        return ('', '')

    # Try to identify the current cluster from lsid.
    cluster = ''

    try:
        (_tool, _version, cluster, _master) = common_lsf.get_lsid_info('lsid')
    except Exception:
        cluster = ''

    db_root = os.path.join(db_path, cluster) if cluster else ''

    if db_root and os.path.isdir(db_root):
        return (db_root, cluster)

    # Fallback: pick the first sub-directory under db_path as the cluster.
    for entry in os.listdir(db_path):
        full = os.path.join(db_path, entry)

        if os.path.isdir(full):
            return (full, entry)

    return ('', '')


def _license_db_root():
    """Resolve license DB root: <license db_path>/license_server/.
    Uses common_db_path.resolve_db_path (config_license.db_path > config.py
    db_path/license > <install>/db/license) so it works even when
    config_license.db_path is empty and falls back to config.py.
    """
    db_path = common_db_path.resolve_db_path(config_license, 'license')
    root = os.path.join(db_path, 'license_server')

    return root if os.path.isdir(root) else ''


def _date_range(days):
    """Return (begin_second, end_second, date_list) for the last N days.
    date_list holds 'YYYYMMDD' strings from oldest to today, inclusive.
    Used to enumerate date-partitioned DB files (job/<date>.db, user/<date>.db).
    """
    end_dt = datetime.datetime.now()
    begin_dt = end_dt - datetime.timedelta(days=days - 1)
    end_second = int(end_dt.timestamp())
    begin_second = int(begin_dt.timestamp())
    date_list = []

    for offset in range(days):
        d = begin_dt + datetime.timedelta(days=offset)
        date_list.append(d.strftime('%Y%m%d'))

    return (begin_second, end_second, date_list)


# =========================================================================
# LSF cluster info
# =========================================================================

def cmd_cluster_info(args):
    """Get cluster basic info (lsid)."""
    (tool, version, cluster, master) = common_lsf.get_lsid_info('lsid')
    _json_output({
        'tool': tool,
        'version': version,
        'cluster': cluster,
        'master': master,
    })


def cmd_host_groups(args):
    """List all host groups and their members."""
    bmgroup_dic = common_lsf.get_bmgroup_info()
    result = []

    for group, hosts in sorted(bmgroup_dic.items()):
        result.append({'group': group, 'hosts': sorted(hosts)})

    _json_output(result)


# =========================================================================
# LSF jobs
# =========================================================================

def _validate_jobid(jobid):
    """Return True if jobid is a safe LSF job id (digits with optional [index])."""
    return bool(re.match(r'^\d+(\[\d+\])?$', str(jobid)))


def cmd_job(args):
    """Get job details by jobid."""
    jobid = str(args.jobid)

    if not _validate_jobid(jobid):
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    command = 'bjobs -UF ' + jobid
    job_dic = common_lsf.get_bjobs_uf_info(command)

    if not job_dic:
        _json_output({'error': f'Job {jobid} not found.'})
        return

    job = job_dic.get(jobid, {})
    _json_output(job)


def cmd_job_hist(args):
    """Get job history via bhist -l (for completed jobs bjobs can't see)."""
    jobid = str(args.jobid)

    if not _validate_jobid(jobid):
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    command = 'bhist -l ' + shlex.quote(jobid)
    result = common_lsf.get_bhist_info(command)
    _json_output(result)


def cmd_job_output(args):
    """Get running job stdout via bpeek."""
    jobid = str(args.jobid)

    if not _validate_jobid(jobid):
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    result = common_lsf.get_bout_info(jobid)
    _json_output(result)


def cmd_job_diagnose(args):
    """Diagnose why a job is PEND/SLOW/FAIL."""
    jobid = str(args.jobid)

    if not _validate_jobid(jobid):
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    issue = args.diagnose.lower()
    command = 'bjobs -UF ' + jobid
    job_dic = common_lsf.get_bjobs_uf_info(command)

    if not job_dic:
        _json_output({'error': f'Job {jobid} not found.'})
        return

    job = job_dic.get(jobid, {})
    result = {'jobid': jobid, 'status': job.get('status', ''), 'issue': issue}

    if issue == 'pend':
        result['pending_reasons'] = job.get('pending_reasons', '')
        result['queue'] = job.get('queue', '')
        result['requested_resources'] = job.get('requested_resources', '')

        # Check queue slots
        queues_dic = common_lsf.get_bqueues_info()
        queue_name = job.get('queue', '')

        if 'QUEUE_NAME' in queues_dic and queue_name in queues_dic['QUEUE_NAME']:
            idx = queues_dic['QUEUE_NAME'].index(queue_name)
            result['queue_max_slots'] = queues_dic['MAX'][idx]
            result['queue_running'] = queues_dic['RUN'][idx]
            result['queue_pending'] = queues_dic['PEND'][idx]
    elif issue == 'slow':
        result['idle_factor'] = job.get('idle_factor', '')
        result['cpu_time'] = job.get('cpu_time', '')
        result['mem'] = job.get('mem', '')
        result['rusage_mem'] = job.get('rusage_mem', '')
        result['max_mem'] = job.get('max_mem', '')
        result['started_on'] = job.get('started_on', '')
        result['started_time'] = job.get('started_time', '')

        # Check host load
        started_on = job.get('started_on', '').strip().split()
        hosts = started_on[:1]  # first host

        if hosts:
            lsload_dic = common_lsf.get_lsload_info()

            for host in hosts:
                if host in lsload_dic.get('HOST_NAME', []):
                    idx = lsload_dic['HOST_NAME'].index(host)
                    result['host_status'] = lsload_dic.get('status', [''])[idx] if idx < len(lsload_dic.get('status', [])) else ''
                    result['host_ut'] = lsload_dic.get('ut', [''])[idx] if idx < len(lsload_dic.get('ut', [])) else ''
                    result['host_mem'] = lsload_dic.get('mem', [''])[idx] if idx < len(lsload_dic.get('mem', [])) else ''
    elif issue == 'fail':
        result['exit_code'] = job.get('exit_code', '')
        result['term_signal'] = job.get('term_signal', '')
        result['finished_time'] = job.get('finished_time', '')
        result['max_mem'] = job.get('max_mem', '')
        result['rusage_mem'] = job.get('rusage_mem', '')
        result['run_limit'] = job.get('run_limit', '')
        result['cpu_time'] = job.get('cpu_time', '')

        # Interpret exit code and term signal
        exit_code_file = os.path.join(_INSTALL_PATH, 'config', 'lsf', 'exit_code.yaml')
        term_signal_file = os.path.join(_INSTALL_PATH, 'config', 'lsf', 'term_signal.yaml')
        exit_code_str = str(job.get('exit_code', ''))
        term_signal_str = str(job.get('term_signal', ''))

        if exit_code_str and exit_code_str != '0':
            result['exit_code_reason'] = _lookup_yaml(exit_code_file, exit_code_str)

        if term_signal_str:
            result['term_signal_reason'] = _lookup_yaml(term_signal_file, term_signal_str)

        # Collect all possible causes (OOM + timeout may both apply).
        possible_causes = []

        # OOM check
        max_mem = _safe_float(job.get('max_mem', ''))
        rusage_mem = _safe_float(job.get('rusage_mem', ''))

        if max_mem and rusage_mem and max_mem >= rusage_mem * 0.95:
            possible_causes.append('Memory limit exceeded (OOM): max_mem >= rusage_mem')

        # Timeout check
        cpu_time = _safe_float(job.get('cpu_time', ''))
        run_limit = str(job.get('run_limit', ''))

        if run_limit and re.match(r'^\d+', run_limit):
            limit_minutes = int(re.match(r'^(\d+)', run_limit).group(1))
            cpu_minutes = cpu_time / 60

            if cpu_minutes >= limit_minutes:
                possible_causes.append(f'Run limit exceeded: cpu_time {cpu_minutes:.0f}min >= limit {limit_minutes}min')

        if possible_causes:
            result['possible_causes'] = possible_causes

    _json_output(result)


def _lookup_yaml(yaml_file, key):
    """Look up a key in a YAML file (exit_code / term_signal)."""
    import yaml

    if not os.path.exists(yaml_file):
        return ''

    try:
        with open(yaml_file, 'r') as YF:
            dic = yaml.load(YF, Loader=yaml.FullLoader)

        return dic.get(key, dic.get(str(key), ''))
    except Exception:
        return ''


def cmd_jobs(args):
    """List jobs with optional filters."""
    command = 'bjobs -u all -w'

    if args.user:
        user = str(args.user)

        if not re.match(r'^[A-Za-z0-9_\-.]+$', user):
            _json_output({'error': f'Invalid user "{user}".'})
            return

        command = 'bjobs -u ' + user + ' -w'

    # LSF status flag selects a coarse category; the exact status is filtered
    # client-side on the STAT column. PEND uses no flag because `bjobs -p`
    # appends per-job pending-reason lines that break the -w table parsing.
    status_flag = {
        'RUN': '-r',
        'DONE': '-d',
        'EXIT': '-d',
        'PSUSP': '-s',
        'USUSP': '-s',
        'SSUSP': '-s',
    }

    if args.status and args.status in status_flag:
        command = command + ' ' + status_flag[args.status]

    job_dic = common_lsf.get_bjobs_info(command)

    # Client-side status filter on the STAT column (exact match).
    if args.status and 'STAT' in job_dic:
        keep = []

        for i in range(len(job_dic.get('JOBID', []))):
            if i < len(job_dic['STAT']) and job_dic['STAT'][i] == args.status:
                keep.append(i)

        job_dic = _filter_dic_by_indices(job_dic, keep)

    # Host filter
    if args.host and 'EXEC_HOST' in job_dic:
        keep = []

        for i in range(len(job_dic.get('JOBID', []))):
            exec_host = job_dic['EXEC_HOST'][i] if i < len(job_dic['EXEC_HOST']) else ''

            if args.host in exec_host:
                keep.append(i)

        job_dic = _filter_dic_by_indices(job_dic, keep)

    # Queue filter
    if args.queue and 'QUEUE' in job_dic:
        keep = []

        for i in range(len(job_dic.get('JOBID', []))):
            queue = job_dic['QUEUE'][i] if i < len(job_dic['QUEUE']) else ''

            if queue == args.queue:
                keep.append(i)

        job_dic = _filter_dic_by_indices(job_dic, keep)

    # Convert to list of dicts
    result = _dic_to_list(job_dic)

    # Row cap (0 = unlimited). On a 60k-job cluster an unfiltered `jobs` would
    # otherwise emit megabytes of JSON; --limit lets callers bound it.
    if args.limit and args.limit > 0:
        result = result[:args.limit]

    _json_output(result)


def _filter_dic_by_indices(dic, indices):
    """Filter a column-oriented dict by keeping only the given indices."""
    result = {}

    for key, values in dic.items():
        result[key] = [values[i] for i in indices if i < len(values)]

    return result


def _dic_to_list(dic):
    """Convert a column-oriented dict to a list of row dicts."""
    if not dic:
        return []

    keys = list(dic.keys())
    n = len(dic[keys[0]]) if keys else 0
    rows = []

    for i in range(n):
        row = {}

        for key in keys:
            row[key] = dic[key][i] if i < len(dic[key]) else ''

        rows.append(row)

    return rows


# =========================================================================
# LSF hosts
# =========================================================================

def cmd_hosts(args):
    """List hosts with optional filters."""
    bhosts_dic = common_lsf.get_bhosts_info()
    lsload_dic = common_lsf.get_lsload_info()
    lshosts_dic = common_lsf.get_lshosts_info()
    host_queue_dic = common_lsf.get_host_queue_info()
    host_group_dic = common_lsf.get_host_group_info()

    # Build load lookup (use 'load_status' to avoid overwriting bhosts 'status')
    load_lookup = {}

    if 'HOST_NAME' in lsload_dic:
        for i, h in enumerate(lsload_dic['HOST_NAME']):
            load_lookup[h] = {
                'ut': lsload_dic.get('ut', [''])[i] if i < len(lsload_dic.get('ut', [])) else '',
                'mem': lsload_dic.get('mem', [''])[i] if i < len(lsload_dic.get('mem', [])) else '',
            }

    # Build static-config lookup from lshosts (ncpus / maxmem / maxswp / type /
    # model). lshosts -w columns: HOST_NAME type model cpuf ncpus maxmem maxswp
    # server RESOURCES. Only a subset is exposed to keep the output focused.
    config_lookup = {}

    if 'HOST_NAME' in lshosts_dic:
        for i, h in enumerate(lshosts_dic['HOST_NAME']):
            config_lookup[h] = {
                'ncpus': lshosts_dic.get('ncpus', [''])[i] if i < len(lshosts_dic.get('ncpus', [])) else '',
                'maxmem': lshosts_dic.get('maxmem', [''])[i] if i < len(lshosts_dic.get('maxmem', [])) else '',
                'maxswp': lshosts_dic.get('maxswp', [''])[i] if i < len(lshosts_dic.get('maxswp', [])) else '',
                'type': lshosts_dic.get('type', [''])[i] if i < len(lshosts_dic.get('type', [])) else '',
                'model': lshosts_dic.get('model', [''])[i] if i < len(lshosts_dic.get('model', [])) else '',
            }

    # Build queue lookup
    queue_lookup = {}

    for host, queues in host_queue_dic.items():
        queue_lookup[host] = queues

    # Build group lookup. get_host_group_info already returns {host: [groups]}.
    group_lookup = host_group_dic
    result = []
    host_names = bhosts_dic.get('HOST_NAME', [])

    for i, host in enumerate(host_names):
        status = bhosts_dic.get('STATUS', [''])[i] if i < len(bhosts_dic.get('STATUS', [])) else ''

        # Status filter
        if args.status and status != args.status:
            continue

        queues = queue_lookup.get(host, [])
        host_groups = group_lookup.get(host, [])

        # Group filter
        if args.group and args.group not in host_groups:
            continue

        # Queue filter
        if args.queue and args.queue not in queues:
            continue

        entry = {
            'host': host,
            'status': status,
            'max_slots': bhosts_dic.get('MAX', [''])[i] if i < len(bhosts_dic.get('MAX', [])) else '',
            'njobs': bhosts_dic.get('NJOBS', [''])[i] if i < len(bhosts_dic.get('NJOBS', [])) else '',
            'run': bhosts_dic.get('RUN', [''])[i] if i < len(bhosts_dic.get('RUN', [])) else '',
            'ssusp': bhosts_dic.get('SSUSP', [''])[i] if i < len(bhosts_dic.get('SSUSP', [])) else '',
            'queues': queues,
            'groups': host_groups,
        }
        entry.update(load_lookup.get(host, {}))
        entry.update(config_lookup.get(host, {}))
        result.append(entry)

    # Sort by load if requested
    if args.sort_load:
        # ut is a percentage string like "2%"; strip the suffix before sorting.
        result.sort(key=lambda x: _safe_float(str(x.get('ut', '0')).rstrip('%')), reverse=True)

    # Row cap (0 = unlimited).
    if args.limit and args.limit > 0:
        result = result[:args.limit]

    _json_output(result)


# =========================================================================
# LSF queues
# =========================================================================

def cmd_queues(args):
    """List queue status."""
    bqueues_dic = common_lsf.get_bqueues_info()
    result = _dic_to_list(bqueues_dic)
    _json_output(result)


# =========================================================================
# LSF users (busers all)
# =========================================================================

def cmd_users(args):
    """List per-user job counts (busers all).
    Returns each user/group's JL/P, MAX, NJOBS, PEND, RUN, SSUSP, USUSP, RSV.
    Use --sort to order by a column (default: RUN desc) for "who is using the
    cluster most right now".
    """
    busers_dic = common_lsf.get_busers_info()
    rows = _dic_to_list(busers_dic)

    # argparse default='RUN' guarantees args.sort is set; use it directly so an
    # explicit empty string isn't silently replaced (matches the --days fix).
    sort_key = (args.sort if args.sort else 'RUN').upper()

    # busers columns: USER/GROUP JL/P MAX NJOBS PEND RUN SSUSP USUSP RSV
    valid_keys = {'JL/P', 'MAX', 'NJOBS', 'PEND', 'RUN', 'SSUSP', 'USUSP', 'RSV'}

    if sort_key not in valid_keys:
        sort_key = 'RUN'

    def _row_num(row, key):
        return _safe_int(row.get(key, '0'))

    rows.sort(key=lambda r: _row_num(r, sort_key), reverse=True)

    _json_output({
        'sort_by': sort_key,
        'count': len(rows),
        'users': rows,
    })


# =========================================================================
# LSF queue detail (bqueues -l config) / pending reasons / cluster summary
# =========================================================================

def _grab(pattern, text, flags=0):
    """First regex group(1) match in text, or '' if none. Used to parse bqueues -l."""
    match = re.search(pattern, text, flags)

    return match.group(1).strip() if match else ''


def cmd_queue(args):
    """Get one queue's runtime counts (bqueues -w) + config (bqueues -l).
    Returns: bqueues -w row (PRIO/STATUS/MAX/JL_*/NJOBS/PEND/RUN/SUSP) plus the
    real scheduling constraints parsed from bqueues -l: RUNLIMIT / USERS /
    HOSTS / RES_REQ / SCHEDULING POLICIES / USER_SHARES, and the current member
    host count (derived from queue_host_mapping).
    """
    queue = args.queue

    # bqueues -w row.
    bqueues_dic = common_lsf.get_bqueues_info()
    w_row = {}

    if 'QUEUE_NAME' in bqueues_dic and queue in bqueues_dic['QUEUE_NAME']:
        idx = bqueues_dic['QUEUE_NAME'].index(queue)
        w_row = {k: (bqueues_dic[k][idx] if idx < len(bqueues_dic[k]) else '') for k in bqueues_dic}

    # bqueues -l detail text (real constraints).
    bqueues_l_text = ''

    try:
        # shlex.quote: queue comes from a CLI arg; quote it so shell meta-
        # characters can't inject (queue names are normally identifiers, but
        # defense-in-depth).
        (return_code, stdout, stderr) = common.run_command('bqueues -l ' + shlex.quote(queue))
        bqueues_l_text = stdout.decode('utf-8', 'ignore') if stdout else ''
    except Exception:
        bqueues_l_text = ''

    config = {
        'runlimit': _grab(r'RUNLIMIT\s*\n\s*([^\n]+)', bqueues_l_text),
        'users': _grab(r'^\s*USERS:\s*(.+)$', bqueues_l_text, re.MULTILINE),
        'hosts': _grab(r'^\s*HOSTS:\s*(.+)$', bqueues_l_text, re.MULTILINE),
        'res_req': _grab(r'^\s*RES_REQ:\s*(.+)$', bqueues_l_text, re.MULTILINE),
        'scheduling_policies': _grab(r'^\s*SCHEDULING POLICIES:\s*(.+)$', bqueues_l_text, re.MULTILINE),
        'user_shares': _grab(r'^\s*USER_SHARES:\s*(.+)$', bqueues_l_text, re.MULTILINE),
    }

    # Member host count from queue_host_mapping (current).
    member_count = None
    member_hosts = []

    try:
        queue_host_dic = common_lsf.get_queue_host_info()
        member_hosts = queue_host_dic.get(queue, []) or []
        member_count = len(member_hosts)
    except Exception:
        pass

    _json_output({
        'queue': queue,
        'runtime': w_row,
        'config': config,
        'member_host_count': member_count,
        'member_hosts': sorted(member_hosts),
    })


def cmd_pending_reasons(args):
    """Aggregate pending reasons across all pending jobs (bjobs -u all -p).

    Mirrors common_ai._aggregate_pending_reasons so the CLI can return the
    same "why is the cluster queuing" top-N table without going through the
    AI report generator.
    """
    from common import common_ai

    # argparse default=10 already guarantees args.top is set; use it directly
    # instead of `args.top or 10` so that --top 0 is honored (0 is falsy and
    # would otherwise fall back to 10).
    top_n = args.top
    reasons = common_ai._aggregate_pending_reasons('bjobs -u all -p', top_n=top_n)

    # Total pending jobs (bjobs -p lists only PEND jobs) for context.
    total_pend = None

    try:
        bjobs_dic = common_lsf.get_bjobs_info('bjobs -u all -p')

        if 'STAT' in bjobs_dic:
            total_pend = sum(1 for s in bjobs_dic['STAT'] if s == 'PEND')
    except Exception:
        pass

    _json_output({
        'total_pending': total_pend,
        'top_n': top_n,
        'reasons': reasons,
    })


def cmd_cluster_summary(args):
    """Cluster-wide aggregate metrics (slots / cpu / mem utilization, job
    counts, host states, per-queue rows, pending reasons, active users).

    Reuses common_ai.compute_cluster_metrics so the numbers exactly match the
    AI cluster analyze report's data sections. Data is collected in parallel.
    """
    from common import common_ai

    (tool, _version, _cluster, _master) = common_lsf.get_lsid_info('lsid')

    # common_ai auto-detects openlava vs lsf from lsid; pass the resolved tool.
    tool_name = tool or 'lsf'

    raw_data = common_ai._collect_raw_data(tool_name)
    metrics = common_ai.compute_cluster_metrics(tool_name, raw_data=raw_data)

    _json_output(metrics)


# =========================================================================
# LSF host load (historical, from DB)
# =========================================================================

def cmd_host_load(args):
    """Get host load history from DB."""
    hostname = args.hostname
    days = args.days
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    load_db = os.path.join(db_root, 'load.db')

    if not os.path.exists(load_db):
        _json_output({'error': f'load.db not found at {load_db}.'})
        return

    table_name = 'load_' + hostname
    end_second = int(time.time())
    begin_second = end_second - days * 86400
    select_condition = f"WHERE sample_second BETWEEN '{begin_second}' AND '{end_second}'"
    (result, conn) = common_sqlite3.connect_db_file(load_db, mode='read')

    # 'locked' (fresh -journal) 与 'failed' 都不可用,统一提前返回(与 BUG-1
    # 修复后的 get_sql_table_data 风格一致)。read 模式实际不返回 locked,此处
    # 保持全文件判断方式统一。
    if result != 'passed':
        _json_output({'error': f'Failed to connect to {load_db}.'})
        return

    data = common_sqlite3.get_sql_table_data(load_db, conn, table_name, ['sample_time', 'ut', 'tmp', 'swp', 'mem'], select_condition)
    conn.close()

    if not data:
        _json_output({'error': f'No load data for host "{hostname}".'})
        return

    data = _sample_down(data)

    _json_output({
        'host': hostname,
        'cluster': cluster,
        'days': days,
        'data_points': len(data.get('sample_time', [])),
        'load': _dic_to_list(data),
    })


def cmd_host_detail(args):
    """Get single host's scheduling load detail (bhosts -l).
    Reuses common_lsf.get_bhosts_load_info — exposes the full CURRENT LOAD
    USED FOR SCHEDULING table (r15s/r1m/r15m/ut/pg/io/ls/it/tmp/swp/mem/slots
    Total+Reserved) that `hosts` only partially surfaces (ut/mem).
    """
    hostname = args.hostname
    result = common_lsf.get_bhosts_load_info('bhosts -l ' + shlex.quote(hostname))

    if not result:
        _json_output({'error': f'No load detail for host "{hostname}".'})
        return

    # get_bhosts_load_info returns {hostname: {...}}, unwrap to the host's dict.
    detail = dict(result.get(hostname, result))
    detail['host'] = hostname
    _json_output(detail)


# =========================================================================
# LSF historical data (from DB)
# =========================================================================

def _list_sub_dirs(path):
    """List immediate sub-directory names under path."""
    if not os.path.isdir(path):
        return []

    return [e for e in os.listdir(path) if os.path.isdir(os.path.join(path, e))]


def _connect_read(db_file):
    """Connect a DB file for read; return (conn) or '' on failure."""
    if not os.path.exists(db_file):
        return ''

    (result, conn) = common_sqlite3.connect_db_file(db_file, mode='read')

    if result == 'failed':
        return ''

    return conn


def cmd_db_clusters(args):
    """List sampled clusters and their data date range."""
    db_path = common_db_path.resolve_db_path(config_lsf, 'lsf')

    if not db_path or not os.path.isdir(db_path):
        _json_output({'error': 'No db_path configured.'})
        return

    result = []

    for cluster in sorted(_list_sub_dirs(db_path)):
        cluster_root = os.path.join(db_path, cluster)

        # Collect date range from job/<date>.db and user/<date>.db.
        dates_list = []

        for sub in ['job', 'user']:
            sub_dir = os.path.join(cluster_root, sub)

            for name in os.listdir(sub_dir) if os.path.isdir(sub_dir) else []:
                if name.endswith('.db'):
                    dates_list.append(name[:-3])

        dates_list = sorted(set(dates_list))
        entry = {'cluster': cluster}

        if dates_list:
            entry['date_from'] = dates_list[0]
            entry['date_to'] = dates_list[-1]
            entry['date_count'] = len(dates_list)

        # List sampled hosts and queues from load.db / queue.db table names.
        for (db_name, key) in [('load.db', 'hosts'), ('queue.db', 'queues')]:
            db_file = os.path.join(cluster_root, db_name)

            if os.path.exists(db_file):
                conn = _connect_read(db_file)

                if conn:
                    tables_list = common_sqlite3.get_sql_table_list(db_file, conn)
                    conn.close()
                    prefix = 'load_' if key == 'hosts' else 'queue_'
                    entry[key] = sorted([t[len(prefix):] for t in tables_list if t.startswith(prefix)])

        result.append(entry)

    _json_output(result)


def cmd_db_job(args):
    """Get finished job detail from historical job/<date>.db files."""
    jobid = str(args.jobid)

    if not _validate_jobid(jobid):
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    job_dir = os.path.join(db_root, 'job')

    if not os.path.isdir(job_dir):
        _json_output({'error': 'No historical job DB directory.'})
        return

    for db_file in sorted(os.listdir(job_dir), reverse=True):
        if not db_file.endswith('.db'):
            continue

        full = os.path.join(job_dir, db_file)
        conn = _connect_read(full)

        if not conn:
            continue

        select_condition = "WHERE job = '" + jobid + "'"
        data = common_sqlite3.get_sql_table_data(full, conn, 'job', None, select_condition)
        conn.close()

        if data:
            rows = _dic_to_list(data)

            _json_output({
                'jobid': jobid,
                'cluster': cluster,
                'finished_date': db_file[:-3],
                'job': rows[0] if rows else {},
            })

            return

    _json_output({'error': f'Job {jobid} not found in historical DB.'})


def cmd_db_jobs(args):
    """List finished jobs from historical job/<date>.db files."""
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    job_dir = os.path.join(db_root, 'job')

    if not os.path.isdir(job_dir):
        _json_output({'error': 'No historical job DB directory.'})
        return

    # Determine which date DB files to scan.
    if args.date:
        date_list = [args.date]
    else:
        # argparse default=7 guarantees args.days is set; use it directly so
        # --days 0 is honored (0 is falsy and `or 7` would wrongly fall back).
        days = args.days
        (_begin_second, _end_second, date_list) = _date_range(days)

    user_filter = args.user.lower() if args.user else ''
    queue_filter = args.queue
    status_filter = args.status.upper() if args.status else ''
    exit_filter = args.exit_code

    # Build a WHERE clause + bound params so SQLite filters at the DB layer
    # instead of Python loading a 3GB+ full-table SELECT * into memory.
    conditions = []
    params = []

    if user_filter:
        conditions.append("user=?")
        params.append(user_filter)

    if queue_filter:
        conditions.append("queue=?")
        params.append(queue_filter)

    if status_filter:
        conditions.append("status=?")
        params.append(status_filter)

    if exit_filter is not None:
        conditions.append("exit_code=?")
        params.append(str(exit_filter))

    where_clause = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

    # Per-date LIMIT so a single huge day (a 3GB+ job db on a big cluster can
    # hold hundreds of thousands of rows) can't dominate the query. When the
    # caller sets --limit N, cap each day at 2*N (rows are merged across dates
    # then truncated to N). When --limit is 0 (default), apply a 5000/day
    # safety cap so the CLI never streams an unbounded JSON blob back — a
    # 3GB+ day can otherwise exhaust memory. There is deliberately NO way to
    # fetch truly unbounded rows; narrow the search with --user/--status/
    # --queue/--exit-code/--days or raise --limit instead.
    if args.limit and args.limit > 0:
        per_db_limit = args.limit * 2
    else:
        per_db_limit = 5000

    select_condition = where_clause + f' LIMIT {per_db_limit}'
    result = []

    for date_str in date_list:
        db_file = os.path.join(job_dir, date_str + '.db')
        conn = _connect_read(db_file)

        if not conn:
            continue

        data = common_sqlite3.get_sql_table_data(db_file, conn, 'job', select_condition=select_condition, select_params=params or None)
        conn.close()

        for row in _dic_to_list(data):
            row['finished_date'] = date_str
            result.append(row)

        # Global limit across all dates.
        if args.limit and args.limit > 0 and len(result) >= args.limit:
            result = result[:args.limit]
            break

    _json_output({
        'cluster': cluster,
        'count': len(result),
        'jobs': result,
    })


def cmd_db_job_mem(args):
    """Get running job mem/idle_factor time series from job_data DB."""
    jobid = str(args.jobid)
    days = args.days

    if not _validate_jobid(jobid):
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    # job_data/<head>_<tail>.db. range_size changed from 1M to 100K (smaller
    # files = smaller blast radius on malformed). Look up new 100K-shard first,
    # fall back to legacy 1M-shard so old sampled data stays readable until the
    # 30-day cleanup rolls it out.
    job_num = re.sub(r'\[.*', '', jobid)

    try:
        job_num_int = int(job_num)
    except ValueError:
        _json_output({'error': f'Invalid jobid "{jobid}".'})
        return

    db_file = None

    for range_size in (100000, 1000000):
        head = (job_num_int // range_size) * range_size

        candidate = os.path.join(db_root, 'job_data', f'{head}_{head + range_size - 1}.db')

        if os.path.exists(candidate):
            db_file = candidate

            break

    if db_file is None:
        _json_output({'error': f'job_data DB not found for jobid "{jobid}" under {db_root}/job_data/.'})

        return

    end_second = int(time.time())
    begin_second = end_second - days * 86400
    select_condition = f"WHERE job_id = '{jobid}' AND sample_second BETWEEN '{begin_second}' AND '{end_second}' ORDER BY sample_second"
    conn = _connect_read(db_file)

    if not conn:
        _json_output({'error': f'Failed to connect to {db_file}.'})
        return

    data = common_sqlite3.get_sql_table_data(db_file, conn, 'job_data', ['sample_time', 'mem', 'idle_factor'], select_condition)
    conn.close()
    data = _sample_down(data)

    _json_output({
        'jobid': jobid,
        'cluster': cluster,
        'days': days,
        'data_points': len(data.get('sample_time', [])),
        'data': _dic_to_list(data),
    })


def cmd_db_user(args):
    """Get user finished-job summary from user/<date>.db files."""
    username = args.user
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    user_dir = os.path.join(db_root, 'user')

    if not os.path.isdir(user_dir):
        _json_output({'error': 'No historical user DB directory.'})
        return

    if args.date:
        date_list = [args.date]
    else:
        days = args.days
        (_begin_second, _end_second, date_list) = _date_range(days)

    table_name = 'user_' + username
    result = []

    for date_str in sorted(date_list, reverse=True):
        db_file = os.path.join(user_dir, date_str + '.db')
        conn = _connect_read(db_file)

        if not conn:
            continue

        data = common_sqlite3.get_sql_table_data(db_file, conn, table_name)
        conn.close()
        rows = _dic_to_list(data)

        for row in rows:
            row['finished_date'] = date_str
            result.append(row)

        if args.limit and len(result) >= args.limit:
            result = result[:args.limit]
            break

    _json_output({
        'cluster': cluster,
        'user': username,
        'count': len(result),
        'jobs': result,
    })


def _query_trend_table(db_root, db_filename, table_prefix, key, days):
    """Read a single trend table (sample_second-based) and down-sample.
    Layout: <db_root>/<db_filename>, table name "<prefix><key>".
    """
    db_file = os.path.join(db_root, db_filename)

    if not os.path.exists(db_file):
        return None

    end_second = int(time.time())
    begin_second = end_second - days * 86400
    select_condition = f"WHERE sample_second BETWEEN '{begin_second}' AND '{end_second}' ORDER BY sample_second"
    conn = _connect_read(db_file)

    if not conn:
        return None

    table_name = table_prefix + key
    data = common_sqlite3.get_sql_table_data(db_file, conn, table_name, None, select_condition)
    conn.close()
    data = _sample_down(data)

    return data


def cmd_db_queue(args):
    """Get queue NJOBS/PEND/RUN trend from queue.db."""
    queue = args.queue
    days = args.days
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    data = _query_trend_table(db_root, 'queue.db', 'queue_', queue, days)

    if data is None:
        _json_output({'error': f'queue.db not found or no data for queue "{queue}".'})
        return

    rows = _dic_to_list(data)
    _json_output({
        'cluster': cluster,
        'queue': queue,
        'days': days,
        'data_points': len(rows),
        'trend': rows,
    })


def cmd_db_host_jobs(args):
    """Get host NJOBS/RUN trend from host.db."""
    hostname = args.host
    days = args.days
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    data = _query_trend_table(db_root, 'host.db', 'host_', hostname, days)

    if data is None:
        _json_output({'error': f'host.db not found or no data for host "{hostname}".'})
        return

    rows = _dic_to_list(data)
    _json_output({
        'cluster': cluster,
        'host': hostname,
        'days': days,
        'data_points': len(rows),
        'trend': rows,
    })


def cmd_db_host_util(args):
    """Get host utilization (slot/cpu/mem) trend from utilization DB.

    --daily reads utilization_day.db (per-day averages), otherwise utilization.db.
    """
    hostname = args.host
    days = args.days
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    if args.daily:
        # utilization_day.db keyed by sample_date (YYYYMMDD), not sample_second.
        db_file = os.path.join(db_root, 'utilization_day.db')

        if not os.path.exists(db_file):
            _json_output({'error': f'utilization_day.db not found at {db_file}.'})
            return

        conn = _connect_read(db_file)

        if not conn:
            _json_output({'error': f'Failed to connect to {db_file}.'})
            return

        table_name = 'utilization_' + hostname
        (_begin_second, _end_second, date_list) = _date_range(days)
        date_ph = ','.join(["'" + d + "'" for d in date_list])
        select_condition = f"WHERE sample_date IN ({date_ph}) ORDER BY sample_date"
        data = common_sqlite3.get_sql_table_data(db_file, conn, table_name, None, select_condition)
        conn.close()
    else:
        data = _query_trend_table(db_root, 'utilization.db', 'utilization_', hostname, days)

        if data is None:
            _json_output({'error': f'utilization.db not found or no data for host "{hostname}".'})
            return

    rows = _dic_to_list(data)
    _json_output({
        'cluster': cluster,
        'host': hostname,
        'days': days,
        'daily': bool(args.daily),
        'data_points': len(rows),
        'trend': rows,
    })


def _query_mapping_history(db_root, db_filename, table_prefix, key, days):
    """Read a host-list mapping history table (queue_host_mapping / group_host_mapping).
    Layout: <db_root>/<db_filename>, table "<prefix><key>".
    Columns: sample_second, sample_time, hosts (space-separated). bsample only
    inserts a row when the mapping changes, so each returned row is a change
    point. Returns the raw column-oriented dict ordered by sample_second.
    """
    db_file = os.path.join(db_root, db_filename)

    if not os.path.exists(db_file):
        return None

    end_second = int(time.time())
    begin_second = end_second - days * 86400
    select_condition = f"WHERE sample_second BETWEEN '{begin_second}' AND '{end_second}' ORDER BY sample_second"
    conn = _connect_read(db_file)

    if not conn:
        return None

    table_name = table_prefix + key
    data = common_sqlite3.get_sql_table_data(db_file, conn, table_name, ['sample_second', 'sample_time', 'hosts'], select_condition)
    conn.close()

    return data


def cmd_db_queue_hosts(args):
    """Get queue member-host history (queue_host_mapping.db).
    Returns each change point: sample_second / sample_time / hosts (list).
    bsample only records a row when the member set changes, so this is the
    history of queue expansion/shrinkage, not a dense time series.
    """
    queue = args.queue
    days = args.days
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    data = _query_mapping_history(db_root, 'queue_host_mapping.db', 'queue_', queue, days)

    if data is None:
        _json_output({'error': f'queue_host_mapping.db not found or no data for queue "{queue}".'})
        return

    rows = _dic_to_list(data)

    for row in rows:
        row['hosts'] = row.get('hosts', '').split() if row.get('hosts') else []

    _json_output({
        'cluster': cluster,
        'queue': queue,
        'days': days,
        'data_points': len(rows),
        'changes': rows,
    })


def cmd_db_group_hosts(args):
    """Get host-group member history (group_host_mapping.db).

    Returns each change point: sample_second / sample_time / hosts (list).
    """
    group = args.group
    days = args.days
    (db_root, cluster) = _resolve_lsf_db_root()

    if not db_root:
        _json_output({'error': 'No cluster DB found.'})
        return

    data = _query_mapping_history(db_root, 'group_host_mapping.db', 'group_', group, days)

    if data is None:
        _json_output({'error': f'group_host_mapping.db not found or no data for group "{group}".'})
        return

    rows = _dic_to_list(data)

    for row in rows:
        row['hosts'] = row.get('hosts', '').split() if row.get('hosts') else []

    _json_output({
        'cluster': cluster,
        'group': group,
        'days': days,
        'data_points': len(rows),
        'changes': rows,
    })


# =========================================================================
# License
# =========================================================================

def _get_license_dic():
    """Get license dic via lmstat (submitted through bsub).
    Mirrors license_sample: load LM_LICENSE_FILE from config/license/, override
    the env var only when the platform file yields real servers, and run lmstat
    via bsub because the local host usually cannot reach license servers.
    """
    from common import common_license

    LM_LICENSE_FILE = os.path.join(_INSTALL_PATH, 'config', 'license', 'LM_LICENSE_FILE')
    lmstat_path = getattr(config_license, 'lmstat_path', 'lmstat')
    lmstat_bsub_command = getattr(config_license, 'lmstat_bsub_command', '')

    # Collect non-empty/non-comment lines; only override LM_LICENSE_FILE when the
    # platform file actually yields servers (a comment-only file falls back to
    # the shell env). Matches license_sample's behavior.
    parsed_servers = []

    if os.path.exists(LM_LICENSE_FILE):
        with open(LM_LICENSE_FILE, 'r') as LLF:
            for line in LLF.readlines():
                line = line.strip()

                if line and not line.startswith('#'):
                    parsed_servers.append(line)

    if parsed_servers:
        os.environ['LM_LICENSE_FILE'] = ':'.join(parsed_servers)

    # Default to bsub submission: lmstat must run on a host that can reach the
    # license servers, which is rarely the local CLI host.
    if not lmstat_bsub_command:
        lmstat_bsub_command = 'bsub -q normal -Is'

    my_get_license_info = common_license.GetLicenseInfo(lmstat_path=lmstat_path, bsub_command=lmstat_bsub_command)

    return my_get_license_info.get_license_info()


def cmd_license(args):
    """Get license feature usage summary."""
    license_dic = _get_license_dic()

    if not license_dic:
        _json_output({'error': 'No license info found.'})
        return

    result = []
    specified_feature = args.feature.lower() if args.feature else ''
    specified_user = args.user.lower() if args.user else ''

    for server in license_dic:
        for vendor in license_dic[server].get('vendor_daemon', {}):
            for feature in license_dic[server]['vendor_daemon'][vendor].get('feature', {}):
                feat_info = license_dic[server]['vendor_daemon'][vendor]['feature'][feature]

                if specified_feature and specified_feature not in feature.lower():
                    continue

                issued = feat_info.get('issued', '0')
                in_use = feat_info.get('in_use', '0')
                in_use_info = feat_info.get('in_use_info', [])

                # Filter by user
                users = []

                for usage in in_use_info:
                    u = usage.get('user', '')

                    if specified_user and specified_user not in u.lower():
                        continue

                    users.append({
                        'user': u,
                        'num': usage.get('license_num', ''),
                        'version': usage.get('version', ''),
                        'start_time': usage.get('start_time', ''),
                    })

                if specified_user and not users:
                    continue

                entry = {
                    'server': server,
                    'vendor': vendor,
                    'feature': feature,
                    'issued': issued,
                    'in_use': in_use,
                    'available': _safe_int(issued) - _safe_int(in_use),
                    'users': users,
                }
                result.append(entry)

    _json_output(result)


def cmd_license_expires(args):
    """Get license feature expiry dates."""
    license_dic = _get_license_dic()

    if not license_dic:
        _json_output({'error': 'No license info found.'})
        return

    result = []
    specified_feature = args.feature.lower() if args.feature else ''

    for server in license_dic:
        for vendor in license_dic[server].get('vendor_daemon', {}):
            expires_dic = license_dic[server]['vendor_daemon'][vendor].get('expires', {})

            for feature, licenses in expires_dic.items():
                if specified_feature and specified_feature not in feature.lower():
                    continue

                for lic in licenses:
                    result.append({
                        'server': server,
                        'vendor': vendor,
                        'feature': feature,
                        'version': lic.get('version', ''),
                        'expires': lic.get('expires', ''),
                        'license_count': lic.get('license', ''),
                    })

    # Sort by expiry date
    result.sort(key=lambda x: x.get('expires', ''))
    _json_output(result)


def cmd_license_usage(args):
    """Get license usage detail by user."""
    license_dic = _get_license_dic()

    if not license_dic:
        _json_output({'error': 'No license info found.'})
        return

    result = []
    specified_user = args.user.lower() if args.user else ''

    for server in license_dic:
        for vendor in license_dic[server].get('vendor_daemon', {}):
            for feature in license_dic[server]['vendor_daemon'][vendor].get('feature', {}):
                in_use_info = license_dic[server]['vendor_daemon'][vendor]['feature'][feature].get('in_use_info', [])

                for usage in in_use_info:
                    u = usage.get('user', '')

                    if specified_user and specified_user not in u.lower():
                        continue

                    result.append({
                        'server': server,
                        'vendor': vendor,
                        'feature': feature,
                        'user': u,
                        'num': usage.get('license_num', ''),
                        'version': usage.get('version', ''),
                        'start_time': usage.get('start_time', ''),
                        'submit_host': usage.get('submit_host', ''),
                        'execute_host': usage.get('execute_host', ''),
                    })

    _json_output(result)


# =========================================================================
# License historical data (from DB)
# =========================================================================

def _license_vendor_features(specified_feature=''):
    """Yield (vendor_dir, server, vendor, [feature_tables]) tuples.
    Features come from the first available roster DB (usage.db > utilization.db
    > utilization_day.db) so the caller can query any of the three DBs against
    the same feature list. Filters by --feature (case-insensitive substring).
    Grouped per vendor_dir so callers reuse ONE connection across all of a
    vendor_dir's features instead of reconnecting per feature — on a 62-server
    / 3766-feature deployment this cuts ~3700 redundant connect/close cycles.
    """
    root = _license_db_root()

    if not root:
        return

    feat_filter = specified_feature.lower() if specified_feature else ''

    for server in sorted(os.listdir(root)):
        server_dir = os.path.join(root, server)

        if not os.path.isdir(server_dir):
            continue

        for vendor in sorted(os.listdir(server_dir)):
            vendor_dir = os.path.join(server_dir, vendor)

            if not os.path.isdir(vendor_dir):
                continue

            # Roster: first existing DB's table list.
            features = []

            for db_name in ['usage.db', 'utilization.db', 'utilization_day.db']:
                roster_file = os.path.join(vendor_dir, db_name)

                if os.path.exists(roster_file):
                    conn = _connect_read(roster_file)

                    if conn:
                        tables_list = common_sqlite3.get_sql_table_list(roster_file, conn)
                        conn.close()
                        features = [t for t in tables_list if (not feat_filter or feat_filter in t.lower())]

                    break

            if features:
                yield (vendor_dir, server, vendor, features)


def cmd_db_license_servers(args):
    """List sampled license servers / vendors / features."""
    root = _license_db_root()

    if not root:
        _json_output({'error': 'No license DB found (run license_sample first).'})
        return

    feat_filter = args.feature.lower() if args.feature else ''
    tree = {}

    for (_vendor_dir, server, vendor, features) in _license_vendor_features(args.feature):
        tree.setdefault(server, {}).setdefault(vendor, set()).update(features)

    result = []

    for server in sorted(tree):
        vendors_list = []

        for vendor in sorted(tree[server]):
            features_list = sorted(tree[server][vendor])

            if feat_filter:
                features_list = [f for f in features_list if feat_filter in f.lower()]

            if features_list:
                vendors_list.append({'vendor': vendor, 'features': features_list})

        if vendors_list:
            result.append({'server': server, 'vendors': vendors_list})

    _json_output(result)


def cmd_db_license_usage(args):
    """Get historical license usage records (per checkout) from usage.db."""
    root = _license_db_root()

    if not root:
        _json_output({'error': 'No license DB found (run license_sample first).'})
        return

    days = args.days
    user_filter = args.user.lower() if args.user else ''
    end_second = int(time.time())
    begin_second = end_second - days * 86400
    select_condition = f"WHERE sample_second BETWEEN '{begin_second}' AND '{end_second}'"

    # Collect vendor dirs first, then process them in parallel. Each worker
    # opens its own connection (sqlite3 connections are thread-local), so the
    # 3766-feature query fan-out runs across N vendor_dirs concurrently.
    vendors = list(_license_vendor_features(args.feature))

    def _process(vendor_entry):
        (vendor_dir, server, vendor, features) = vendor_entry
        db_file = os.path.join(vendor_dir, 'usage.db')
        conn = _connect_read(db_file)

        if not conn:
            return []

        rows = []

        # try/finally guarantees the connection is closed even if a future
        # code change lets an exception escape get_sql_table_data/_dic_to_list
        # (currently they swallow internally, so this is defensive).
        try:
            for feature in features:
                data = common_sqlite3.get_sql_table_data(db_file, conn, feature, None, select_condition)

                for row in _dic_to_list(data):
                    if user_filter and user_filter not in str(row.get('user', '')).lower():
                        continue

                    row['server'] = server
                    row['vendor'] = vendor
                    row['feature'] = feature
                    rows.append(row)
        finally:
            conn.close()

        return rows

    from concurrent.futures import ThreadPoolExecutor

    result = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        for rows in ex.map(_process, vendors):
            result.extend(rows)

    _json_output({
        'days': days,
        'count': len(result),
        'usage': result,
    })


def cmd_db_license_util(args):
    """Get license feature utilization trend from utilization DB.
    --daily reads utilization_day.db (per-day averages).
    """
    root = _license_db_root()

    if not root:
        _json_output({'error': 'No license DB found (run license_sample first).'})
        return

    days = args.days
    end_second = int(time.time())
    begin_second = end_second - days * 86400
    (_begin_second, _end_second, date_list) = _date_range(days)

    if args.daily:
        date_ph = ','.join(["'" + d + "'" for d in date_list])
        select_condition = f"WHERE sample_date IN ({date_ph}) ORDER BY sample_date"
    else:
        select_condition = f"WHERE sample_second BETWEEN '{begin_second}' AND '{end_second}' ORDER BY sample_second"

    # Collect vendor dirs first, then process in parallel (each worker opens its
    # own connection; sqlite3 connections are thread-local).
    vendors = list(_license_vendor_features(args.feature))

    def _process(vendor_entry):
        (vendor_dir, server, vendor, features) = vendor_entry
        db_file = os.path.join(vendor_dir, 'utilization_day.db' if args.daily else 'utilization.db')
        conn = _connect_read(db_file)

        if not conn:
            return []

        out = []

        # try/finally guarantees the connection is closed even if a future
        # code change lets an exception escape (currently defensive).
        try:
            for feature in features:
                data = common_sqlite3.get_sql_table_data(db_file, conn, feature, None, select_condition)
                data = _sample_down(data)
                rows = _dic_to_list(data)

                out.append({
                    'server': server,
                    'vendor': vendor,
                    'feature': feature,
                    'data_points': len(rows),
                    'trend': rows,
                })
        finally:
            conn.close()

        return out

    from concurrent.futures import ThreadPoolExecutor

    result = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(_process, vendors):
            result.extend(batch)

    _json_output({
        'days': days,
        'daily': bool(args.daily),
        'features': result,
    })


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="""
lsfMonitor CLI — 查询 LSF/License 信息，输出 JSON。

功能概览：
  作业查询      job / jobs
  作业诊断      job --diagnose pend|slow|fail
  主机查询      hosts / host-load
  队列查询      queues / queue <name>
  用户查询      users
  集群诊断      pending-reasons / cluster-summary / cluster-info / host-groups
  License 查询  license / license-expires / license-usage
  历史数据      db clusters / db job / db jobs / db job-mem /
                db user / db queue / db queue-hosts / db group-hosts /
                db host-jobs / db host-util /
                db license-servers / db license-usage / db license-util

所有命令输出 JSON 格式，方便脚本和 AI 助手解析。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：

  # 查作业详情（状态、用户、队列、主机、资源、命令等）
  bmonitor_cli job 12345

  # 诊断作业为什么 PEND（排队不动）
  bmonitor_cli job 12345 --diagnose pend
  → 返回 pending_reason、队列 slot/pend/run 数、请求资源

  # 诊断作业为什么 SLOW（跑得慢）
  bmonitor_cli job 12345 --diagnose slow
  → 返回 idle_factor、CPU/内存、主机负载

  # 诊断作业为什么 FAIL（失败）
  bmonitor_cli job 12345 --diagnose fail
  → 返回 exit_code/term_signal 及解读、OOM 检测、超时检测

  # 列出作业（支持多维度筛选，可组合）
  bmonitor_cli jobs
  bmonitor_cli jobs --user liyanqing
  bmonitor_cli jobs --status RUN
  bmonitor_cli jobs --user liyanqing --status EXIT
  bmonitor_cli jobs --queue normal --status PEND
  bmonitor_cli jobs --host n019-123-001

  # 列出主机状态（支持按状态/队列/分组筛选，可组合）
  bmonitor_cli hosts
  bmonitor_cli hosts --status unavail         # 哪些主机不可用
  bmonitor_cli hosts --status closed          # 哪些主机被关闭
  bmonitor_cli hosts --queue normal            # normal 队列的主机
  bmonitor_cli hosts --group IC_ETX            # IC_ETX 分组的主机
  bmonitor_cli hosts --sort-load               # 按负载从高到低排序

  # 队列状态总览（slot/pend/run 数）
  bmonitor_cli queues

  # 主机历史负载（读采样 DB，画趋势用）
  bmonitor_cli host-load n019-123-001
  bmonitor_cli host-load n019-123-001 --days 30

  # 集群基本信息（集群名、版本、master）
  bmonitor_cli cluster-info

  # host group 列表及成员
  bmonitor_cli host-groups

  # License feature 占用情况
  bmonitor_cli license                          # 全部 feature
  bmonitor_cli license --feature calibre        # 指定 feature（模糊匹配）
  bmonitor_cli license --feature calibre --user someone  # 指定 feature + 用户

  # License 到期时间
  bmonitor_cli license-expires                 # 全部
  bmonitor_cli license-expires --feature calibre

  # License 用户使用详情
  bmonitor_cli license-usage                   # 全部
  bmonitor_cli license-usage --user someone    # 指定用户

  # ===== 历史数据查询（读采样 DB，趋势与已完成作业） =====

  # 列出已采样的集群及日期范围
  bmonitor_cli db clusters

  # 查询单个已完成作业的历史详情（跨 job/<日期>.db）
  bmonitor_cli db job 12345

  # 查询历史完成作业列表（多维度筛选，可组合）
  bmonitor_cli db jobs                              # 近 7 天全部
  bmonitor_cli db jobs --date 20260831               # 指定完成日期
  bmonitor_cli db jobs --user liyanqing --days 30    # 指定用户近 30 天
  bmonitor_cli db jobs --status EXIT --days 14       # 近 14 天失败作业
  bmonitor_cli db jobs --exit-code 1 --queue normal  # normal 队列 exit_code=1
  bmonitor_cli db jobs --limit 100                   # 限制返回条数

  # 运行中作业的内存/idle_factor 时序（读 job_data DB）
  bmonitor_cli db job-mem 12345
  bmonitor_cli db job-mem 12345 --days 1

  # 用户已完成作业汇总（读 user/<日期>.db）
  bmonitor_cli db user liyanqing --days 7
  bmonitor_cli db user liyanqing --date 20260831

  # 队列负载趋势（NJOBS/PEND/RUN 时序）
  bmonitor_cli db queue normal
  bmonitor_cli db queue normal --days 30

  # 主机作业数趋势（NJOBS/RUN 时序）
  bmonitor_cli db host-jobs n019-123-001 --days 7

  # 主机利用率趋势（slot/cpu/mem）
  bmonitor_cli db host-util n019-123-001 --days 7
  bmonitor_cli db host-util n019-123-001 --daily     # 日均

  # 列出已采样的 license server / vendor / feature
  bmonitor_cli db license-servers
  bmonitor_cli db license-servers --feature calibre

  # 历史 license 占用记录（谁何时用了多少）
  bmonitor_cli db license-usage --feature calibre --days 7
  bmonitor_cli db license-usage --user someone --days 7

  # License feature 利用率趋势
  bmonitor_cli db license-util --feature calibre --days 7
  bmonitor_cli db license-util --feature calibre --daily
""",
    )

    subparsers = parser.add_subparsers(dest='command', help='可用子命令')

    # job — 作业详情 / 诊断
    p_job = subparsers.add_parser(
        'job',
        help='job <jobid> [--diagnose pend|slow|fail]  作业详情/诊断',
        description="""
查询指定作业的详细信息（bjobs -UF 解析），可选诊断 PEND/SLOW/FAIL。

不带 --diagnose 时返回作业全部字段：状态、用户、队列、主机、资源、
命令、开始/结束时间、CPU/内存使用等。

带 --diagnose 时返回结构化诊断结果：
  pend — pending reason + 队列 slot/pend/run + 请求资源
  slow — idle_factor + CPU/内存 + 主机负载(ut/mem)
  fail — exit_code/term_signal 解读 + OOM 检测 + 超时检测
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli job 12345
  bmonitor_cli job 12345 --diagnose pend
  bmonitor_cli job 12345 --diagnose slow
  bmonitor_cli job 12345 --diagnose fail
""",
    )
    p_job.add_argument('jobid', type=str, help='作业 ID，例如 12345')
    p_job.add_argument('--diagnose', choices=['pend', 'slow', 'fail'], help='诊断原因：pend=排队不动 / slow=跑得慢 / fail=失败')

    # job-hist — 作业历史(bhist,已完成作业 bjobs 看不到)
    p_job_hist = subparsers.add_parser(
        'job-hist',
        help='job-hist <jobid>  作业历史事件(bhist -l)',
        description="""
查询指定作业的历史事件流水（bhist -l 解析）。已完成作业 bjobs 已查不到，
需用 bhist。返回结构化字段（user/queue/started/finished/cpu/mem 等）+
events 事件流水列表 + raw 原文兜底。

大集群上 bhist 对历史作业可能较慢或超时，超时返回 error 而非崩溃。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n  bmonitor_cli job-hist 12345',
    )
    p_job_hist.add_argument('jobid', type=str, help='作业 ID')

    # job-output — 作业输出(bpeek)
    p_job_output = subparsers.add_parser(
        'job-output',
        help='job-output <jobid>  作业 stdout 输出(bpeek)',
        description='查看运行中作业的 stdout 输出（bpeek）。输出为原始日志文本。',
        epilog='示例:\n  bmonitor_cli job-output 12345',
    )
    p_job_output.add_argument('jobid', type=str, help='作业 ID')

    # jobs — 作业列表
    p_jobs = subparsers.add_parser(
        'jobs',
        help='jobs [--user X] [--status RUN/PEND/DONE/EXIT] [--queue X] [--host X]  作业列表',
        description="""
查询作业列表（bjobs -w 解析），支持多维度筛选，可组合使用。

返回每个作业的：JOBID、USER、STAT、QUEUE、FROM_HOST、EXEC_HOST、
JOB_NAME、SUBMIT_TIME 等。

状态 --status 可选值：RUN/PEND/DONE/EXIT/PSUSP/USUSP/SSUSP
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli jobs
  bmonitor_cli jobs --user liyanqing
  bmonitor_cli jobs --status RUN
  bmonitor_cli jobs --user liyanqing --status EXIT
  bmonitor_cli jobs --queue normal --status PEND
  bmonitor_cli jobs --host n019-123-001
""",
    )
    p_jobs.add_argument('--user', default='', help='按用户筛选（精确匹配）')
    p_jobs.add_argument('--status', default='', help='按状态筛选：RUN/PEND/DONE/EXIT/PSUSP/USUSP/SSUSP')
    p_jobs.add_argument('--queue', default='', help='按队列筛选（精确匹配）')
    p_jobs.add_argument('--host', default='', help='按执行主机筛选（模糊匹配）')
    p_jobs.add_argument('--limit', type=int, default=0, help='限制返回条数，默认 0 不限')

    # hosts — 主机状态
    p_hosts = subparsers.add_parser(
        'hosts',
        help='hosts [--status ok/unavail/closed] [--queue X] [--group X] [--sort-load]  主机状态',
        description="""
查询主机状态（bhosts + lsload 解析），返回每台主机的：
状态、最大 slot 数、运行作业数、队列列表、分组列表、负载(ut/mem)。

支持按状态/队列/分组筛选，可组合。--sort-load 按负载从高到低排序。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli hosts
  bmonitor_cli hosts --status unavail
  bmonitor_cli hosts --status closed
  bmonitor_cli hosts --queue normal
  bmonitor_cli hosts --group IC_ETX
  bmonitor_cli hosts --sort-load
""",
    )
    p_hosts.add_argument('--status', default='', help='按状态筛选（精确匹配 bhosts STATUS），如 ok / closed_Full / closed_Busy / closed_Adm / unavail / unreach')
    p_hosts.add_argument('--queue', default='', help='按队列筛选')
    p_hosts.add_argument('--group', default='', help='按 host group 筛选')
    p_hosts.add_argument('--sort-load', action='store_true', help='按负载(ut)从高到低排序')
    p_hosts.add_argument('--limit', type=int, default=0, help='限制返回条数，默认 0 不限')

    # queues — 队列状态
    subparsers.add_parser(
        'queues',
        help='queues  队列状态总览',
        description='查询所有队列的状态（bqueues -w 解析），返回 QUEUE/PRIO/STATUS/MAX/JL/U/PEND/RUN/SUSP/NJOBS 等。',
        epilog='示例:\n  bmonitor_cli queues',
    )

    # queue — 单队列详情（配置 + 计数）
    p_queue = subparsers.add_parser(
        'queue',
        help='queue <name>  单队列详情（配置 + 计数）',
        description="""
查询指定队列的运行计数（bqueues -w 行）+ 真实调度约束（bqueues -l 解析）。

返回：runtime（PRIO/STATUS/MAX/JL_*/NJOBS/PEND/RUN/SUSP）+ config
（RUNLIMIT/USERS/HOSTS/RES_REQ/SCHEDULING POLICIES/USER_SHARES）+ 当前成员主机列表。
bqueues -w 的 MAX/JL_* 通常是 '-'（不限制），真正约束在 -l 的 RUNLIMIT/USERS/HOSTS/RES_REQ。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli queue normal
""",
    )
    p_queue.add_argument('queue', type=str, help='队列名')

    # users — 用户级作业计数
    p_users = subparsers.add_parser(
        'users',
        help='users [--sort RUN/NJOBS/PEND]  用户级作业计数',
        description="""
查询每个用户/用户组的当前作业计数（busers all 解析），返回
USER/GROUP、JL/P、MAX、NJOBS、PEND、RUN、SSUSP、USUSP、RSV。

--sort 按指定列降序（默认 RUN），快速找出"谁正在占用最多 slot/排队最多"。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli users
  bmonitor_cli users --sort PEND
  bmonitor_cli users --sort NJOBS
""",
    )
    p_users.add_argument('--sort', default='RUN', help='排序列（默认 RUN）：JL/P, MAX, NJOBS, PEND, RUN, SSUSP, USUSP, RSV')

    # pending-reasons — 集群级排队原因
    p_pend = subparsers.add_parser(
        'pending-reasons',
        help='pending-reasons [--top N]  集群级排队原因 Top',
        description="""
汇总全集群排队原因（bjobs -u all -p），返回 Top-N 原因 + 每个原因的作业数。
归一化处理：去掉": N hosts;"和数字，便于聚合相同原因。

用于回答"全集群为什么在排队"，无需逐作业查询。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli pending-reasons
  bmonitor_cli pending-reasons --top 20
""",
    )
    p_pend.add_argument('--top', type=int, default=10, help='返回 Top-N 原因（默认 10）')

    # cluster-summary — 集群汇总指标
    subparsers.add_parser(
        'cluster-summary',
        help='cluster-summary  集群汇总指标（slot/cpu/mem 利用率 + 作业计数）',
        description="""
集群级聚合指标：主机状态分布、slot/cpu/mem 总量与利用率、作业计数
（RUN/PEND/SUSP）、per-queue 行、排队原因 Top、活跃用户 Top。

复用 common_ai.compute_cluster_metrics，数据与 AI 集群分析报告的权威指标完全一致，
数据并行采集。一次调用即可获得集群整体画像，无需 hosts+queues+users 各查一遍再自己算。
""",
        epilog='示例:\n  bmonitor_cli cluster-summary',
    )

    # host-load — 主机历史负载
    p_host_load = subparsers.add_parser(
        'host-load',
        help='host-load <hostname> [--days N]  主机历史负载',
        description="""
查询指定主机的历史负载数据（ut/mem），从采样 DB 读取。
返回最多 100 个采样点，按时间排序。

需要先跑 bsample -l 采集负载数据。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli host-load n019-123-001
  bmonitor_cli host-load n019-123-001 --days 30
""",
    )
    p_host_load.add_argument('hostname', type=str, help='主机名，例如 n019-123-001')
    p_host_load.add_argument('--days', type=int, default=7, help='查询天数（默认 7 天）')

    # host-detail — 单机调度负载详情(bhosts -l)
    p_host_detail = subparsers.add_parser(
        'host-detail',
        help='host-detail <hostname>  单机调度负载详情(bhosts -l)',
        description="""
查询指定主机的完整调度负载（bhosts -l 的 CURRENT LOAD USED FOR SCHEDULING 段），
返回 r15s/r1m/r15m/ut/pg/io/ls/it/tmp/swp/mem/slots 的 Total 与 Reserved 值。

`hosts` 命令只含 ut/mem，本命令暴露全部负载指标，用于排查某台主机为何不调度作业
（load_stop 阈值是否被触发）。
""",
        epilog='示例:\n  bmonitor_cli host-detail n019-123-001',
    )
    p_host_detail.add_argument('hostname', type=str, help='主机名')

    # cluster-info
    subparsers.add_parser(
        'cluster-info',
        help='cluster-info  集群基本信息',
        description='查询集群信息（lsid 解析），返回调度器类型、版本、集群名、master 节点。',
        epilog='示例:\n  bmonitor_cli cluster-info',
    )

    # host-groups
    subparsers.add_parser(
        'host-groups',
        help='host-groups  host group 列表',
        description='查询所有 host group（bmgroup 解析），返回每个 group 及其包含的主机列表。',
        epilog='示例:\n  bmonitor_cli host-groups',
    )

    # license — feature 占用
    p_license = subparsers.add_parser(
        'license',
        help='license [--feature X] [--user X]  License feature 占用',
        description="""
查询 License feature 的使用情况（lmstat 解析），返回每个 feature 的：
issued（总量）、in_use（在用）、available（可用）、当前占用用户列表。

--feature 支持模糊匹配（大小写不敏感）。
--user 可筛选特定用户的占用。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli license
  bmonitor_cli license --feature calibre
  bmonitor_cli license --feature calibre --user someone
""",
    )
    p_license.add_argument('--feature', default='', help='按 feature 名称筛选（模糊匹配，大小写不敏感）')
    p_license.add_argument('--user', default='', help='按用户筛选（模糊匹配，大小写不敏感）')

    # license-expires
    p_expires = subparsers.add_parser(
        'license-expires',
        help='license-expires [--feature X]  License 到期时间',
        description="""
查询 License feature 的到期时间（lmstat 解析），按到期日期排序。
可按 feature 模糊筛选。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli license-expires
  bmonitor_cli license-expires --feature calibre
""",
    )
    p_expires.add_argument('--feature', default='', help='按 feature 名称筛选（模糊匹配）')

    # license-usage
    p_usage = subparsers.add_parser(
        'license-usage',
        help='license-usage [--user X]  License 用户使用详情',
        description="""
查询 License 的用户使用详情（lmstat 解析），返回每条占用记录的：
feature、user、数量、版本、开始时间、提交主机、执行主机。

可按用户筛选。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli license-usage
  bmonitor_cli license-usage --user someone
""",
    )
    p_usage.add_argument('--user', default='', help='按用户筛选（模糊匹配，大小写不敏感）')

    # ===== db — 历史数据查询（读采样 DB） =====
    p_db = subparsers.add_parser(
        'db',
        help='db <clusters|job|jobs|job-mem|user|queue|queue-hosts|group-hosts|host-jobs|host-util|license-servers|license-usage|license-util>  历史采样数据',
        description="""
查询采样 DB 中的历史数据（bsample / license_sample 采集），输出 JSON。
所有子命令只读，不写库。

需要先运行 bsample（LSF）或 license_sample（License）采集数据。

LSF DB 路径：<config_lsf.db_path or config.db_path/lsf>/<cluster>/
License DB 路径：<config_license.db_path or config.db_path/license>/license_server/
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  bmonitor_cli db clusters
  bmonitor_cli db job 12345
  bmonitor_cli db jobs --status EXIT --days 7
  bmonitor_cli db job-mem 12345 --days 1
  bmonitor_cli db user liyanqing --days 7
  bmonitor_cli db queue normal --days 30
  bmonitor_cli db queue-hosts normal --days 30
  bmonitor_cli db group-hosts IC_ETX --days 30
  bmonitor_cli db host-jobs n019-123-001 --days 7
  bmonitor_cli db host-util n019-123-001 --daily
  bmonitor_cli db license-servers --feature calibre
  bmonitor_cli db license-usage --feature calibre --days 7
  bmonitor_cli db license-util --feature calibre --daily
""",
    )
    db_subparsers = p_db.add_subparsers(dest='db_command', metavar='子命令')

    # db clusters
    db_subparsers.add_parser('clusters', help='clusters  已采样集群及数据日期范围')

    # db job
    p_db_job = db_subparsers.add_parser('job', help='job <jobid>  已完成作业详情')
    p_db_job.add_argument('jobid', type=str, help='作业 ID')

    # db jobs
    p_db_jobs = db_subparsers.add_parser('jobs', help='jobs [--user X] [--status X] [--queue X] [--exit-code N] [--date YYYYMMDD] [--days N] [--limit N]  历史完成作业')
    p_db_jobs.add_argument('--user', default='', help='按用户筛选（精确匹配）')
    p_db_jobs.add_argument('--status', default='', help='按状态筛选：RUN/DONE/EXIT 等')
    p_db_jobs.add_argument('--queue', default='', help='按队列筛选（精确匹配）')
    p_db_jobs.add_argument('--exit-code', dest='exit_code', type=int, default=None, help='按退出码筛选')
    p_db_jobs.add_argument('--days', type=int, default=7, help='查询天数，默认 7（与 --date 互斥）')
    p_db_jobs.add_argument('--date', default='', help='指定完成日期 YYYYMMDD（与 --days 互斥）')
    p_db_jobs.add_argument('--limit', type=int, default=0, help='限制返回总条数；默认 0 表示不强制限制但每天仍有 5000 条安全上限（大集群单日作业可达数十万，无上限会爆内存）')

    # db job-mem
    p_db_job_mem = db_subparsers.add_parser('job-mem', help='job-mem <jobid> [--days N]  作业内存/idle_factor 时序')
    p_db_job_mem.add_argument('jobid', type=str, help='作业 ID')
    p_db_job_mem.add_argument('--days', type=int, default=7, help='查询天数，默认 7')

    # db user
    p_db_user = db_subparsers.add_parser('user', help='user <user> [--date YYYYMMDD] [--days N] [--limit N]  用户已完成作业汇总')
    p_db_user.add_argument('user', type=str, help='用户名')
    p_db_user.add_argument('--days', type=int, default=7, help='查询天数，默认 7（与 --date 互斥）')
    p_db_user.add_argument('--date', default='', help='指定完成日期 YYYYMMDD（与 --days 互斥）')
    p_db_user.add_argument('--limit', type=int, default=0, help='限制返回条数，默认 0 不限')

    # db queue
    p_db_queue = db_subparsers.add_parser('queue', help='queue <queue> [--days N]  队列负载趋势')
    p_db_queue.add_argument('queue', type=str, help='队列名')
    p_db_queue.add_argument('--days', type=int, default=7, help='查询天数，默认 7')

    # db queue-hosts
    p_db_qh = db_subparsers.add_parser('queue-hosts', help='queue-hosts <queue> [--days N]  队列成员主机历史')
    p_db_qh.add_argument('queue', type=str, help='队列名')
    p_db_qh.add_argument('--days', type=int, default=30, help='查询天数，默认 30')

    # db group-hosts
    p_db_gh = db_subparsers.add_parser('group-hosts', help='group-hosts <group> [--days N]  主机组成员历史')
    p_db_gh.add_argument('group', type=str, help='主机组名')
    p_db_gh.add_argument('--days', type=int, default=30, help='查询天数，默认 30')

    # db host-jobs
    p_db_host_jobs = db_subparsers.add_parser('host-jobs', help='host-jobs <host> [--days N]  主机作业数趋势')
    p_db_host_jobs.add_argument('host', type=str, help='主机名')
    p_db_host_jobs.add_argument('--days', type=int, default=7, help='查询天数，默认 7')

    # db host-util
    p_db_host_util = db_subparsers.add_parser('host-util', help='host-util <host> [--days N] [--daily]  主机利用率趋势')
    p_db_host_util.add_argument('host', type=str, help='主机名')
    p_db_host_util.add_argument('--days', type=int, default=7, help='查询天数，默认 7')
    p_db_host_util.add_argument('--daily', action='store_true', help='返回日均（utilization_day.db）')

    # db license-servers
    p_db_lic_servers = db_subparsers.add_parser('license-servers', help='license-servers [--feature X]  已采样 license server/vendor/feature')
    p_db_lic_servers.add_argument('--feature', default='', help='按 feature 名称筛选（模糊匹配）')

    # db license-usage
    p_db_lic_usage = db_subparsers.add_parser('license-usage', help='license-usage [--feature X] [--user X] [--days N]  历史 license 占用记录')
    p_db_lic_usage.add_argument('--feature', default='', help='按 feature 名称筛选（模糊匹配）')
    p_db_lic_usage.add_argument('--user', default='', help='按用户筛选（模糊匹配）')
    p_db_lic_usage.add_argument('--days', type=int, default=7, help='查询天数，默认 7')

    # db license-util
    p_db_lic_util = db_subparsers.add_parser('license-util', help='license-util [--feature X] [--days N] [--daily]  license feature 利用率趋势')
    p_db_lic_util.add_argument('--feature', default='', help='按 feature 名称筛选（模糊匹配）')
    p_db_lic_util.add_argument('--days', type=int, default=7, help='查询天数，默认 7')
    p_db_lic_util.add_argument('--daily', action='store_true', help='返回日均（utilization_day.db）')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # db 子命令需要二级命令
    if args.command == 'db' and not getattr(args, 'db_command', None):
        p_db.print_help()
        sys.exit(1)

    # Dispatch
    db_dispatch = {
        'clusters': cmd_db_clusters,
        'job': cmd_db_job,
        'jobs': cmd_db_jobs,
        'job-mem': cmd_db_job_mem,
        'user': cmd_db_user,
        'queue': cmd_db_queue,
        'queue-hosts': cmd_db_queue_hosts,
        'group-hosts': cmd_db_group_hosts,
        'host-jobs': cmd_db_host_jobs,
        'host-util': cmd_db_host_util,
        'license-servers': cmd_db_license_servers,
        'license-usage': cmd_db_license_usage,
        'license-util': cmd_db_license_util,
    }

    dispatch = {
        'job': lambda: cmd_job_diagnose(args) if args.diagnose else cmd_job(args),
        'job-hist': lambda: cmd_job_hist(args),
        'job-output': lambda: cmd_job_output(args),
        'jobs': lambda: cmd_jobs(args),
        'hosts': lambda: cmd_hosts(args),
        'queues': lambda: cmd_queues(args),
        'queue': lambda: cmd_queue(args),
        'users': lambda: cmd_users(args),
        'pending-reasons': lambda: cmd_pending_reasons(args),
        'cluster-summary': lambda: cmd_cluster_summary(args),
        'host-load': lambda: cmd_host_load(args),
        'host-detail': lambda: cmd_host_detail(args),
        'cluster-info': lambda: cmd_cluster_info(args),
        'host-groups': lambda: cmd_host_groups(args),
        'license': lambda: cmd_license(args),
        'license-expires': lambda: cmd_license_expires(args),
        'license-usage': lambda: cmd_license_usage(args),
        'db': lambda: db_dispatch[args.db_command](args),
    }

    handler = dispatch.get(args.command)

    if handler:
        handler()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
