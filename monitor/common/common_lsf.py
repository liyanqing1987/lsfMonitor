import os
import re
import sys
import time
import datetime

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common


def get_command_dict(command):
    """
    Collect LSF command output message into a dict.
    It only works with the "title <-> item" type informations.
    """
    my_dic = {}
    key_list = []
    (return_code, stdout, stderr) = common.run_command(command)
    i = -1

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if line:
            i += 1

            # Some speciall preprocess.
            if re.search(r'lsload', command):
                line = re.sub(r'\*', ' ', line)

            if i == 0:
                key_list = line.split()

                for key in key_list:
                    my_dic[key] = []
            else:
                command_info = line.split()

                if (len(command_info) < len(key_list)) and ('unavail' not in command_info):
                    common.bprint('For command "' + str(command) + '", below info line is incomplate/unexpected.', level='Warning')
                    common.bprint(line, color='yellow', display_method=1, indent=11)

                for j in range(len(key_list)):
                    key = key_list[j]

                    if j < len(command_info):
                        value = command_info[j]
                    else:
                        value = ''

                    my_dic[key].append(value)

    return my_dic


def get_bqueues_info(command='bqueues -w'):
    """
    Get bqueues info with command "bqueues".
    ====
    QUEUE_NAME      PRIO STATUS          MAX JL/U JL/P JL/H NJOBS  PEND   RUN  SUSP  RSV PJOBS
    normal           30  Open:Active       -    -    -    -     2     0     2     0    0     0
    ====
    """
    bqueues_dic = get_command_dict(command)
    return bqueues_dic


def get_bhosts_info(command='bhosts -w'):
    """
    Get bhosts info with command "bhosts".
    ====
    HOST_NAME          STATUS          JL/U    MAX  NJOBS    RUN  SSUSP  USUSP    RSV
    cmp01              ok              -       4    2        2    0      0        0
    ====
    """
    bhosts_dic = get_command_dict(command)
    return bhosts_dic


def get_bjobs_info(command='bjobs -u all -w'):
    """
    Get bjobs info with command "bjobs'.
    ====
    JOBID   USER      STAT  QUEUE      FROM_HOST   EXEC_HOST   JOB_NAME            SUBMIT_TIME
    101     liyanqing RUN   normal     cmp01       2*cmp01     Tesf for lsfMonitor Oct 26 17:43
    ====
    """
    bjobs_dic = {}
    key_list = []
    (return_code, stdout, stderr) = common.run_command(command)
    i = -1

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if line:
            i += 1

            if i == 0:
                key_list = line.split()

                for key in key_list:
                    bjobs_dic[key] = []
            else:
                if not re.match(r'^\s*(\d+(\[\d+\])?)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)\s+((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+ \d+:\d+)\s*$', line):
                    common.bprint('Invalid bjobs information for below line.', level='Warning')
                    common.bprint(line, color='yellow', display_method=1, indent=11)
                else:
                    my_match = re.match(r'^\s*(\d+(\[\d+\])?)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)\s+((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+ \d+:\d+)\s*$', line)
                    bjobs_dic['JOBID'].append(my_match.group(1))
                    bjobs_dic['USER'].append(my_match.group(3))
                    bjobs_dic['STAT'].append(my_match.group(4))
                    bjobs_dic['QUEUE'].append(my_match.group(5))
                    bjobs_dic['FROM_HOST'].append(my_match.group(6))
                    bjobs_dic['EXEC_HOST'].append(my_match.group(7))
                    bjobs_dic['JOB_NAME'].append(my_match.group(8))
                    bjobs_dic['SUBMIT_TIME'].append(my_match.group(9))

    return bjobs_dic


def get_bhosts_load_info(command='bhosts -l'):
    """
    Get "CURRENT LOAD USED FOR SCHEDULING" information with command "bhosts".
    ====
    HOST  n212-206-212
    STATUS           CPUF  JL/U    MAX  NJOBS    RUN  SSUSP  USUSP    RSV DISPATCH_WINDOW
    ok              15.00     -     48      2      2      0      0      0      -

     CURRENT LOAD USED FOR SCHEDULING:
                    r15s   r1m  r15m    ut    pg    io   ls    it   tmp   swp   mem  slots
     Total           0.0   0.0   0.0    2%   0.0     8    0 14324 1667_g 127.2_g  683_g     46
     Reserved        0.0   0.0   0.0    0%   0.0     0    0     0    0_m    0_m  178_g      -
    ====
    """
    bhosts_load_dic = {}
    load_info_mark = False
    hostname = ''
    head_list = []
    (return_code, stdout, stderr) = common.run_command(command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if re.match(r'^\s*HOST\s+(.+?)\s*$', line):
            my_match = re.match(r'^\s*HOST\s+(.+?)\s*$', line)
            hostname = my_match.group(1)
            bhosts_load_dic.setdefault(hostname, {})
            load_info_mark = False
        elif re.match(r'^\s*CURRENT LOAD USED FOR SCHEDULING:\s*$', line):
            load_info_mark = True
        elif load_info_mark:
            if re.match(r'^\s*$', line):
                load_info_mark = False
            elif re.match(r'^\s*Total\s+(.+?)\s*$', line):
                bhosts_load_dic[hostname].setdefault('Total', {})

                my_match = re.match(r'^\s*Total\s+(.+?)\s*$', line)
                total_load_string = my_match.group(1)
                total_load_list = total_load_string.split()

                for (i, head_name) in enumerate(head_list):
                    load = re.sub(r'\*', '', total_load_list[i])
                    bhosts_load_dic[hostname]['Total'].setdefault(head_name, load)
            elif re.match(r'^\s*Reserved\s+(.+?)\s*$', line):
                bhosts_load_dic[hostname].setdefault('Reserved', {})

                my_match = re.match(r'^\s*Reserved\s+(.+?)\s*$', line)
                reserved_load_string = my_match.group(1)
                reserved_load_list = reserved_load_string.split()

                for (i, head_name) in enumerate(head_list):
                    load = re.sub(r'\*', '', reserved_load_list[i])
                    bhosts_load_dic[hostname]['Reserved'].setdefault(head_name, load)
            else:
                head_list = line.split()

    return bhosts_load_dic


def get_lshosts_info(command='lshosts -w'):
    """
    Get lshosts info with command "lshosts".
    ====
    HOST_NAME                     type       model           cpuf     ncpus maxmem maxswp server RESOURCES
    cmp01                         X86_64     Intel_Platinum  15.0     4     1.7_g   1.9_g   Yes    (mg)
    ====
    """
    lshosts_dic = get_command_dict(command)
    return lshosts_dic


def get_lsload_info(command='lsload -l'):
    """
    Get lsload info with command "lsload".
    ====
    HOST_NAME               status  r15s   r1m  r15m   ut    pg    ls    it   tmp    swp   mem
    cmp01                 ok      0.7    0.3  0.2    5%    0.0   1     0    7391_m  1.9_g  931_m
    ====
    """
    lsload_dic = get_command_dict(command)
    return lsload_dic


def get_busers_info(command='busers all'):
    """
    Get lsload info with command "busers".
    ====
    USER/GROUP          JL/P    MAX  NJOBS   PEND    RUN  SSUSP  USUSP    RSV
    liyanqing           -       -    2       0       2    0      0        0
    ====
    """
    busers_dic = get_command_dict(command)
    return busers_dic


def get_lsid_info(command='lsid'):
    """
    Get "tool/tool_version/cluster/master" info with command "lsid".
    """
    tool = ''
    tool_version = ''
    cluster = ''
    master = ''
    (return_code, stdout, stderr) = common.run_command(command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if re.match(r'^\s*My\s+cluster\s+name\s+is\s+(\S+)\s*$', line):
            my_match = re.match(r'^\s*My\s+cluster\s+name\s+is\s+(\S+)\s*$', line)
            cluster = my_match.group(1)
        elif re.match(r'^\s*My\s+master\s+name\s+is\s+(\S+)\s*$', line):
            my_match = re.match(r'^\s*My\s+master\s+name\s+is\s+(\S+)\s*$', line)
            master = my_match.group(1)
        elif re.search(r'LSF', line) or re.search(r'volclava', line) or re.search(r'Open_lava', line) or re.search(r'openlava', line) or re.search(r'Openlava', line) or re.search(r'OpenLava', line):
            if re.search(r'LSF', line):
                tool = 'LSF'
            elif re.search(r'volclava', line):
                tool = 'volclava'
            elif re.search(r'Open_lava', line) or re.search(r'openlava', line) or re.search(r'Openlava', line) or re.search(r'OpenLava', line):
                tool = 'openlava'

            if re.match(r'^.*\s+([\d\.]+),.*$', line):
                my_match = re.match(r'^.*\s+([\d\.]+),.*$', line)
                tool_version = my_match.group(1)

    return tool, tool_version, cluster, master


def get_bjobs_uf_info(command='bjobs -u all -UF', get_lsid_info_command='lsid'):
    """
    Get job information with command "bjobs".
    """
    (tool, tool_version, cluster, master) = get_lsid_info(get_lsid_info_command)
    my_dic = {}

    if (tool == 'LSF') or (tool == 'volclava'):
        my_dic = get_lsf_bjobs_uf_info(command)
    elif tool == 'openlava':
        my_dic = get_openlava_bjobs_uf_info(command)

    return my_dic


def get_lsf_bjobs_uf_info(command='bjobs -u all -UF', get_lsf_unit_for_limits_command='badmin showconf mbd all'):
    """
    Get job info with command "bjobs".
    ====
    Job <101>, Job Name <Tesf for lsfMonitor>, User <liyanqing>, Project <lsf_test>, Status <RUN>, Queue <normal>, Command <sleep 12345>, Share group charged </liyanqing>
    Mon Oct 26 17:43:07: Submitted from host <cmp01>, CWD <$HOME>, 2 Task(s), Requested Resources <span[hosts=1] rusage[mem=123]>;
    Mon Oct 26 17:43:07: Started 2 Task(s) on Host(s) <2*cmp01>, Allocated 2 Slot(s) on Host(s) <2*cmp01>, Execution Home </home/liyanqing>, Execution CWD </home/liyanqing>;
    Mon Oct 26 17:46:17: Resource usage collected. MEM: 2 Mbytes; SWAP: 238 Mbytes; NTHREAD: 4; PGID: 10643; PIDs: 10643 10644 10646;


     MEMORY USAGE:
     MAX MEM: 2 Mbytes;  AVG MEM: 2 Mbytes

     SCHEDULING PARAMETERS:
               r15s   r1m  r15m   ut      pg    io   ls    it    tmp    swp    mem
     load_sched   -     -     -     -       -     -    -     -     -      -      -
     load_stop    -     -     -     -       -     -    -     -     -      -      -

     RESOURCE REQUIREMENT DETAILS:
     Combined: select[type == local] order[r15s:pg] rusage[mem=123.00] span[hosts=1]
     Effective: select[type == local] order[r15s:pg] rusage[mem=123.00] span[hosts=1]
    ====
    """
    job_compile_dic = {'job_compile': re.compile(r'.*Job <([0-9]+(\[[0-9]+\])?)>.*'),
                       'job_name_compile': re.compile(r'.*Job Name <([^>]+)>.*'),
                       'user_compile': re.compile(r'.*User <([^>]+)>.*'),
                       'project_compile': re.compile(r'.*Project <([^>]+)>.*'),
                       'status_compile': re.compile(r'.*Status <([A-Z]+)>*'),
                       'queue_compile': re.compile(r'.*Queue <([^>]+)>.*'),
                       'interactive_mode_compile': re.compile(r'.*Interactive pseudo-terminal shell mode.*'),
                       'command_compile': re.compile(r'.*Command <(.+?\S)>.*$'),
                       'job_description_compile': re.compile(r'.*Job Description <([^>]+)>.*'),
                       'submitted_from_compile': re.compile(r'(.*): Submitted from host <([^>]+)>.*'),
                       'cwd_compile': re.compile(r'.*CWD <([^>]+)>.*'),
                       'processors_requested_compile': re.compile(r'.* (\d+) Task\(s\).*'),
                       'requested_resources_compile': re.compile(r'.*Requested Resources <(.+)>;.*'),
                       'span_hosts_compile': re.compile(r'.*Requested Resources <.*span\[hosts=([1-9][0-9]*).*>.*'),
                       'rusage_mem_compile': re.compile(r'.*Requested Resources <.*rusage\[.*mem=([1-9][0-9]*).*>.*'),
                       'started_on_compile': re.compile(r'(.*): (\[\d+\] )?([sS]tarted|[dD]ispatched) \d+ Task\(s\) on Host\(s\) (.+?), Allocated (\d+) Slot\(s\) on Host\(s\).*'),
                       'resource_usage_collected_compile': re.compile(r'.*Resource usage collected.*'),
                       'cpu_time_compile': re.compile(r'.*The CPU time used is (\d+(\.\d+)?) seconds.*'),
                       'mem_compile': re.compile(r'.*[\.\;]\s+MEM:\s*(\d+(\.\d+)?)\s*([KMGT]bytes).*'),
                       'swap_compile': re.compile(r'.*SWAP:\s*(\d+(\.\d+)?)\s*([KMGT]bytes).*'),
                       'pids_compile': re.compile(r'PIDs:\s+(.+?);'),
                       'finished_time_compile': re.compile(r'(.*): (Done successfully|Exited|Termination request issued).*'),
                       'exit_code_compile': re.compile(r'.*Exited with exit code (\d+)\..*'),
                       'term_signal_compile': re.compile(r'.*(TERM_.+?): (.+?\.).*'),
                       'run_limit_compile': re.compile(r'\s*RUNLIMIT\s*'),
                       'max_mem_compile': re.compile(r'\s*MAX MEM: (\d+(\.\d+)?) ([KMGT]bytes);\s*AVG MEM: (\d+(\.\d+)?) ([KMGT]bytes)\s*'),
                       'pending_reasons_compile': re.compile(r'\s*PENDING REASONS:\s*')}

    my_dic = {}
    job = ''
    run_limit_mark = False
    pending_mark = False
    lsf_unit_for_limits = get_lsf_unit_for_limits(get_lsf_unit_for_limits_command)
    (return_code, stdout, stderr) = common.run_command(command)

    for line in stdout.decode('utf-8', 'ignore').split('\n'):
        line = line.strip()

        if line:
            if job_compile_dic['job_compile'].match(line):
                if re.match(r'Job <' + str(job) + '> is not found', line):
                    continue

                my_match = job_compile_dic['job_compile'].match(line)
                job = my_match.group(1)

                # Initialization for my_dic[job].
                my_dic[job] = {'job_info': '',
                               'job_id': job,
                               'job_name': '',
                               'job_description': '',
                               'user': '',
                               'project': '',
                               'status': '',
                               'interactive_mode': 'False',
                               'queue': '',
                               'command': '',
                               'submitted_from': '',
                               'submitted_time': '',
                               'cwd': '',
                               'processors_requested': '1',
                               'requested_resources': '',
                               'span_hosts': '',
                               'rusage_mem': '',
                               'started_on': '',
                               'started_time': '',
                               'finished_time': '',
                               'exit_code': '',
                               'term_signal': '',
                               'cpu_time': '',
                               'mem': '',
                               'swap': '',
                               'run_limit': [],
                               'pids': [],
                               'max_mem': '',
                               'avg_mem': '',
                               'pending_reasons': []}

                if job_compile_dic['job_name_compile'].match(line):
                    my_match = job_compile_dic['job_name_compile'].match(line)
                    my_dic[job]['job_name'] = my_match.group(1)

                if job_compile_dic['job_description_compile'].match(line):
                    my_match = job_compile_dic['job_description_compile'].match(line)
                    my_dic[job]['job_description'] = my_match.group(1)

                if job_compile_dic['user_compile'].match(line):
                    my_match = job_compile_dic['user_compile'].match(line)
                    my_dic[job]['user'] = my_match.group(1)

                if job_compile_dic['project_compile'].match(line):
                    my_match = job_compile_dic['project_compile'].match(line)
                    my_dic[job]['project'] = my_match.group(1)

                if job_compile_dic['status_compile'].match(line):
                    my_match = job_compile_dic['status_compile'].match(line)
                    my_dic[job]['status'] = my_match.group(1)

                if job_compile_dic['queue_compile'].match(line):
                    my_match = job_compile_dic['queue_compile'].match(line)
                    my_dic[job]['queue'] = my_match.group(1)

                if job_compile_dic['interactive_mode_compile'].match(line):
                    my_dic[job]['interactive_mode'] = 'True'

                if job_compile_dic['command_compile'].match(line):
                    my_match = job_compile_dic['command_compile'].match(line)
                    my_dic[job]['command'] = my_match.group(1)
            elif job_compile_dic['submitted_from_compile'].match(line):
                my_match = job_compile_dic['submitted_from_compile'].match(line)
                my_dic[job]['submitted_time'] = my_match.group(1)
                my_dic[job]['submitted_from'] = my_match.group(2)

                if job_compile_dic['cwd_compile'].match(line):
                    my_match = job_compile_dic['cwd_compile'].match(line)
                    my_dic[job]['cwd'] = my_match.group(1)

                if job_compile_dic['processors_requested_compile'].match(line):
                    my_match = job_compile_dic['processors_requested_compile'].match(line)
                    my_dic[job]['processors_requested'] = my_match.group(1)

                if job_compile_dic['requested_resources_compile'].match(line):
                    my_match = job_compile_dic['requested_resources_compile'].match(line)
                    my_dic[job]['requested_resources'] = my_match.group(1)

                if job_compile_dic['span_hosts_compile'].match(line):
                    my_match = job_compile_dic['span_hosts_compile'].match(line)
                    my_dic[job]['span_hosts'] = my_match.group(1)

                if job_compile_dic['rusage_mem_compile'].match(line):
                    my_match = job_compile_dic['rusage_mem_compile'].match(line)
                    my_dic[job]['rusage_mem'] = my_match.group(1)

                    # Switch rusage_mem unit into "MB".
                    if lsf_unit_for_limits == 'KB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem'])/1024, 1)
                    elif lsf_unit_for_limits == 'MB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem']), 1)
                    elif lsf_unit_for_limits == 'GB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem'])*1024, 1)
                    elif lsf_unit_for_limits == 'TB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem'])*1024*1024, 1)
            elif job_compile_dic['started_on_compile'].match(line):
                my_match = job_compile_dic['started_on_compile'].match(line)
                my_dic[job]['started_time'] = my_match.group(1)
                started_host = my_match.group(4)
                started_host = re.sub(r'<', '', started_host)
                started_host = re.sub(r'>', '', started_host)
                started_host = re.sub(r'\d+\*', '', started_host)
                my_dic[job]['started_on'] = started_host
            elif job_compile_dic['resource_usage_collected_compile'].match(line):
                if job_compile_dic['cpu_time_compile'].match(line):
                    my_match = job_compile_dic['cpu_time_compile'].match(line)
                    my_dic[job]['cpu_time'] = my_match.group(1)

                if job_compile_dic['mem_compile'].match(line) and (not my_dic[job]['mem']):
                    my_match = job_compile_dic['mem_compile'].match(line)
                    my_dic[job]['mem'] = my_match.group(1)
                    unit = my_match.group(3)

                    # Switch mem unit into "MB".
                    if unit == 'Kbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem'])/1024, 1)
                    elif unit == 'Mbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem']), 1)
                    elif unit == 'Gbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem'])*1024, 1)
                    elif unit == 'Tbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem'])*1024*1024, 1)

                if job_compile_dic['swap_compile'].match(line):
                    my_match = job_compile_dic['swap_compile'].match(line)
                    my_dic[job]['swap'] = my_match.group(1)
                    unit = my_match.group(3)

                    # Switch swap unit into "MB".
                    if unit == 'Kbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap'])/1024, 1)
                    elif unit == 'Mbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap']), 1)
                    elif unit == 'Gbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap'])*1024, 1)
                    elif unit == 'Tbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap'])*1024*1024, 1)

                if job_compile_dic['pids_compile'].findall(line):
                    my_match = job_compile_dic['pids_compile'].findall(line)
                    my_string = ' '.join(my_match)
                    my_dic[job]['pids'] = my_string.split()
            elif job_compile_dic['finished_time_compile'].match(line):
                my_match = job_compile_dic['finished_time_compile'].match(line)
                my_dic[job]['finished_time'] = my_match.group(1)

                if job_compile_dic['exit_code_compile'].match(line):
                    my_match = job_compile_dic['exit_code_compile'].match(line)
                    my_dic[job]['exit_code'] = my_match.group(1)
            elif job_compile_dic['term_signal_compile'].match(line):
                my_match = job_compile_dic['term_signal_compile'].match(line)
                my_dic[job]['term_signal'] = my_match.group(1)
            elif job_compile_dic['max_mem_compile'].match(line):
                my_match = job_compile_dic['max_mem_compile'].match(line)
                my_dic[job]['max_mem'] = my_match.group(1)
                unit = my_match.group(3)

                # Switch max_mem unit into "MB".
                if unit == 'Kbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem'])/1024, 1)
                elif unit == 'Mbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem']), 1)
                elif unit == 'Gbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem'])*1024, 1)
                elif unit == 'Tbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem'])*1024*1024, 1)

                my_dic[job]['avg_mem'] = my_match.group(4)
                unit = my_match.group(6)

                # Switch avg_mem unit into "MB".
                if unit == 'Kbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem'])/1024, 1)
                elif unit == 'Mbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem']), 1)
                elif unit == 'Gbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem'])*1024, 1)
                elif unit == 'Tbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem'])*1024*1024, 1)
            else:
                if run_limit_mark:
                    my_dic[job]['run_limit'].append(line.strip())
                    run_limit_mark = False

                if pending_mark:
                    my_dic[job]['pending_reasons'].append(line.strip())
                    pending_mark = False

                if job_compile_dic['run_limit_compile'].match(line):
                    run_limit_mark = True

                if job_compile_dic['pending_reasons_compile'].match(line):
                    pending_mark = True

        if job:
            if my_dic[job]['job_info']:
                my_dic[job]['job_info'] = str(my_dic[job]['job_info']) + '\n' + str(line)
            else:
                my_dic[job]['job_info'] = line

    return my_dic


def get_openlava_bjobs_uf_info(command='bjobs -u all -UF'):
    """
    Get job info with command "bjobs".
    ====
    Job <305>, User <liyanqing.1987>, Project <default>, Status <RUN>, Queue <normal>, Interactive pseudo-terminal shell mode, Command <sleep 1000>, Job Description <this is a test>
    Sun Mar 23 10:08:18: Submitted from host <openlava4-test-cmp1>, CWD <$HOME>, 2 Processors Requested, Requested Resources <rusage[mem=123]>;
    Sun Mar 23 10:08:22: Started on 2 Hosts/Processors <openlava4-test-cmp1> <openlava4-test-cmp1>;
    Sun Mar 23 10:08:36: Resource usage collected. MEM: 3 Mbytes; SWAP: 247 Mbytes; PGID: 23518; PIDs: 23518 ; PGID: 23523; PIDs: 23523 23524;

     MEMORY USAGE:
     MAX MEM: N/A MBytes;  AVG MEM: N/A MBytes

     SCHEDULING PARAMETERS:
               r15s   r1m  r15m   ut      pg    io   ls    it    tmp    swp    mem
     loadSched   -    3.5    -     -    18.0     -   15     -     -      -      -
     loadStop    -    5.0    -     -       -     -    -     -     -      -      -

     RESOURCE REQUIREMENT DETAILS:
     Combined: rusage[mem=123]
     Effective: rusage[mem=123]
    ====
    """
    job_compile_dic = {'job_compile': re.compile(r'.*Job <([0-9]+(\[[0-9]+\])?)>.*'),
                       'job_name_compile': re.compile(r'.*Job Name <([^>]+)>.*'),
                       'user_compile': re.compile(r'.*User <([^>]+)>.*'),
                       'project_compile': re.compile(r'.*Project <([^>]+)>.*'),
                       'status_compile': re.compile(r'.*Status <([A-Z]+)>*'),
                       'queue_compile': re.compile(r'.*Queue <([^>]+)>.*'),
                       'interactive_mode_compile': re.compile(r'.*Interactive pseudo-terminal shell mode.*'),
                       'command_compile': re.compile(r'.*Command <(.+?\S)>.*$'),
                       'job_description_compile': re.compile(r'.*Job Description <([^>]+)>.*'),
                       'submitted_from_compile': re.compile(r'(.*): Submitted from host <([^>]+)>.*'),
                       'cwd_compile': re.compile(r'.*CWD <([^>]+)>.*'),
                       'processors_requested_compile': re.compile(r'.* ([1-9][0-9]*) Processors Requested.*'),
                       'requested_resources_compile': re.compile(r'.*Requested Resources <(.+)>;.*'),
                       'span_hosts_compile': re.compile(r'.*Requested Resources <.*span\[hosts=([1-9][0-9]*).*>.*'),
                       'rusage_mem_compile': re.compile(r'.*Requested Resources <.*rusage\[.*mem=([1-9][0-9]*).*>.*'),
                       'started_on_compile': re.compile(r'(.*): ([sS]tarted|[dD]ispatched) on ([0-9]+ Hosts/Processors )?([^;,]+).*'),
                       'resource_usage_collected_compile': re.compile(r'.*Resource usage collected.*'),
                       'mem_compile': re.compile(r'.*[\.\;]\s+MEM:\s*(\d+(\.\d+)?)\s*([KMGT]bytes).*'),
                       'swap_compile': re.compile(r'.*SWAP:\s*(\d+(\.\d+)?)\s*([KMGT]bytes).*'),
                       'pids_compile': re.compile(r'PIDs:\s+(.+?);'),
                       'finished_time_compile': re.compile(r'(.*): (Done successfully|Exited|Termination request issued).*'),
                       'exit_code_compile': re.compile(r'.*Exited with exit code (\d+)\..*'),
                       'term_signal_compile': re.compile(r'.*TERM_OWNER: (.+?\.).*'),
                       'max_mem_compile': re.compile(r'\s*MAX MEM: (\d+(\.\d+)?) ([KMGT]bytes);\s*AVG MEM: (\d+(\.\d+)?) ([KMGT]bytes)\s*'),
                       'pending_reasons_compile': re.compile(r'\s*PENDING REASONS:\s*')}

    my_dic = {}
    job = ''
    pending_mark = False
    lsf_unit_for_limits = 'MB'
    (return_code, stdout, stderr) = common.run_command(command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if line:
            if job_compile_dic['job_compile'].match(line):
                if re.match(r'Job <' + str(job) + '> is not found', line):
                    continue

                my_match = job_compile_dic['job_compile'].match(line)
                job = my_match.group(1)

                # Initialization for my_dic[job].
                my_dic[job] = {'job_info': '',
                               'job_id': job,
                               'job_name': '',
                               'job_description': '',
                               'user': '',
                               'project': '',
                               'status': '',
                               'interactive_mode': 'False',
                               'queue': '',
                               'command': '',
                               'submitted_from': '',
                               'submitted_time': '',
                               'cwd': '',
                               'processors_requested': '1',
                               'requested_resources': '',
                               'span_hosts': '',
                               'rusage_mem': '',
                               'started_on': '',
                               'started_time': '',
                               'finished_time': '',
                               'exit_code': '',
                               'term_signal': '',
                               'cpu_time': '',
                               'mem': '',
                               'swap': '',
                               'run_limit': [],
                               'pids': [],
                               'max_mem': '',
                               'avg_mem': '',
                               'pending_reasons': []}

                if job_compile_dic['job_name_compile'].match(line):
                    my_match = job_compile_dic['job_name_compile'].match(line)
                    my_dic[job]['job_name'] = my_match.group(1)

                if job_compile_dic['job_description_compile'].match(line):
                    my_match = job_compile_dic['job_description_compile'].match(line)
                    my_dic[job]['job_description'] = my_match.group(1)

                if job_compile_dic['user_compile'].match(line):
                    my_match = job_compile_dic['user_compile'].match(line)
                    my_dic[job]['user'] = my_match.group(1)

                if job_compile_dic['project_compile'].match(line):
                    my_match = job_compile_dic['project_compile'].match(line)
                    my_dic[job]['project'] = my_match.group(1)

                if job_compile_dic['status_compile'].match(line):
                    my_match = job_compile_dic['status_compile'].match(line)
                    my_dic[job]['status'] = my_match.group(1)

                if job_compile_dic['queue_compile'].match(line):
                    my_match = job_compile_dic['queue_compile'].match(line)
                    my_dic[job]['queue'] = my_match.group(1)

                if job_compile_dic['interactive_mode_compile'].match(line):
                    my_dic[job]['interactive_mode'] = 'True'

                if job_compile_dic['command_compile'].match(line):
                    my_match = job_compile_dic['command_compile'].match(line)
                    my_dic[job]['command'] = my_match.group(1)
            elif job_compile_dic['submitted_from_compile'].match(line):
                my_match = job_compile_dic['submitted_from_compile'].match(line)
                my_dic[job]['submitted_time'] = my_match.group(1)
                my_dic[job]['submitted_from'] = my_match.group(2)

                if job_compile_dic['cwd_compile'].match(line):
                    my_match = job_compile_dic['cwd_compile'].match(line)
                    my_dic[job]['cwd'] = my_match.group(1)

                if job_compile_dic['processors_requested_compile'].match(line):
                    my_match = job_compile_dic['processors_requested_compile'].match(line)
                    my_dic[job]['processors_requested'] = my_match.group(1)

                if job_compile_dic['requested_resources_compile'].match(line):
                    my_match = job_compile_dic['requested_resources_compile'].match(line)
                    my_dic[job]['requested_resources'] = my_match.group(1)

                if job_compile_dic['span_hosts_compile'].match(line):
                    my_match = job_compile_dic['span_hosts_compile'].match(line)
                    my_dic[job]['span_hosts'] = my_match.group(1)

                if job_compile_dic['rusage_mem_compile'].match(line):
                    my_match = job_compile_dic['rusage_mem_compile'].match(line)
                    my_dic[job]['rusage_mem'] = my_match.group(1)

                    # Switch rusage_mem unit into "MB".
                    if lsf_unit_for_limits == 'KB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem'])/1024, 1)
                    elif lsf_unit_for_limits == 'MB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem']), 1)
                    elif lsf_unit_for_limits == 'GB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem'])*1024, 1)
                    elif lsf_unit_for_limits == 'TB':
                        my_dic[job]['rusage_mem'] = round(float(my_dic[job]['rusage_mem'])*1024*1024, 1)
            elif job_compile_dic['started_on_compile'].match(line):
                my_match = job_compile_dic['started_on_compile'].match(line)
                my_dic[job]['started_time'] = my_match.group(1)
                started_host = my_match.group(4)
                started_host = re.sub(r'<', '', started_host)
                started_host = re.sub(r'>', '', started_host)
                started_host = re.sub(r'\d+\*', '', started_host)
                my_dic[job]['started_on'] = started_host
            elif job_compile_dic['resource_usage_collected_compile'].match(line):
                if job_compile_dic['mem_compile'].match(line) and (not my_dic[job]['mem']):
                    my_match = job_compile_dic['mem_compile'].match(line)
                    my_dic[job]['mem'] = my_match.group(1)
                    unit = my_match.group(3)

                    # Switch mem unit into "MB".
                    if unit == 'Kbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem'])/1024, 1)
                    elif unit == 'Mbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem']), 1)
                    elif unit == 'Gbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem'])*1024, 1)
                    elif unit == 'Tbytes':
                        my_dic[job]['mem'] = round(float(my_dic[job]['mem'])*1024*1024, 1)

                if job_compile_dic['swap_compile'].match(line):
                    my_match = job_compile_dic['swap_compile'].match(line)
                    my_dic[job]['swap'] = my_match.group(1)
                    unit = my_match.group(3)

                    # Switch swap unit into "MB".
                    if unit == 'Kbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap'])/1024, 1)
                    elif unit == 'Mbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap']), 1)
                    elif unit == 'Gbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap'])*1024, 1)
                    elif unit == 'Tbytes':
                        my_dic[job]['swap'] = round(float(my_dic[job]['swap'])*1024*1024, 1)

                if job_compile_dic['pids_compile'].findall(line):
                    my_match = job_compile_dic['pids_compile'].findall(line)
                    my_string = ' '.join(my_match)
                    my_dic[job]['pids'] = my_string.split()
            elif job_compile_dic['finished_time_compile'].match(line):
                my_match = job_compile_dic['finished_time_compile'].match(line)
                my_dic[job]['finished_time'] = my_match.group(1)

                if job_compile_dic['exit_code_compile'].match(line):
                    my_match = job_compile_dic['exit_code_compile'].match(line)
                    my_dic[job]['exit_code'] = my_match.group(1)
            elif job_compile_dic['term_signal_compile'].match(line):
                my_match = job_compile_dic['term_signal_compile'].match(line)
                my_dic[job]['term_signal'] = my_match.group(1)
            elif job_compile_dic['max_mem_compile'].match(line):
                my_match = job_compile_dic['max_mem_compile'].match(line)
                my_dic[job]['max_mem'] = my_match.group(1)
                unit = my_match.group(3)

                # Switch max_mem unit into "MB".
                if unit == 'Kbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem'])/1024, 1)
                elif unit == 'Mbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem']), 1)
                elif unit == 'Gbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem'])*1024, 1)
                elif unit == 'Tbytes':
                    my_dic[job]['max_mem'] = round(float(my_dic[job]['max_mem'])*1024*1024, 1)

                my_dic[job]['avg_mem'] = my_match.group(4)
                unit = my_match.group(6)

                # Switch avg_mem unit into "MB".
                if unit == 'Kbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem'])/1024, 1)
                elif unit == 'Mbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem']), 1)
                elif unit == 'Gbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem'])*1024, 1)
                elif unit == 'Tbytes':
                    my_dic[job]['avg_mem'] = round(float(my_dic[job]['avg_mem'])*1024*1024, 1)
            else:
                if pending_mark:
                    my_dic[job]['pending_reasons'].append(line.strip())
                    pending_mark = False

                if job_compile_dic['pending_reasons_compile'].match(line):
                    pending_mark = True

        if job:
            if my_dic[job]['job_info']:
                my_dic[job]['job_info'] = str(my_dic[job]['job_info']) + '\n' + str(line)
            else:
                my_dic[job]['job_info'] = line

    return my_dic


def get_host_list(command='bhosts -w'):
    """
    Get host list with command "bhosts".
    """
    host_list = []
    bhosts_dic = get_bhosts_info(command)

    if 'HOST_NAME' in bhosts_dic:
        host_list = bhosts_dic['HOST_NAME']

    return host_list


def get_queue_list(command='bqueues -w'):
    """
    Get queue list with command "bqueues".
    """
    queue_list = []
    bqueues_dic = get_bqueues_info(command)

    if 'QUEUE_NAME' in bqueues_dic:
        queue_list = bqueues_dic['QUEUE_NAME']

    return queue_list


def get_bmgroup_info(command='bmgroup -w -r'):
    """
    Get host group members with command "bmgroup".
    ====
    [yanqing.li@nxnode03 lsfMonitor]$ bmgroup -w -r
    GROUP_NAME    HOSTS                     GROUP_ADMIN
    pd           dm006 dm007 dm010 dm009 dm002 dm003 dm005  ( - )
    [yanqing.li@nxnode03 lsfMonitor]$ bmgroup -w -r pd
    GROUP_NAME    HOSTS
    pd           dm006 dm007 dm010 dm009 dm002 dm003 dm005
    ====
    """
    bmgroup_dic = {}
    group_name_compile = re.compile(r'^\s*GROUP_NAME\s+HOSTS.*$')
    line_compile = re.compile(r'\s*(\S+)\s+(.+?)\s*(\(.*\))?\s*$')
    mark = False
    (return_code, stdout, stderr) = common.run_command(command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if mark and line_compile.match(line):
            my_match = line_compile.match(line)
            group_name = my_match.group(1)
            hosts_string = my_match.group(2).strip()
            host_list = hosts_string.split()
            bmgroup_dic[group_name] = host_list
        elif group_name_compile.match(line):
            mark = True

    return bmgroup_dic


def get_queue_host_info(command='bqueues -l', get_hosts_list_command='bhosts -w', get_bmgroup_info_command='bmgroup -w -r'):
    """
    Get host info of specified queues with command "bqueues/bmgroup".
    """
    queue_host_dic = {}
    queue_compile = re.compile(r'^QUEUE:\s*(\S+)\s*$')
    hosts_compile = re.compile(r'^HOSTS:\s*(.*?)\s*$')
    hosts_all_compile = re.compile(r'\ball\b')
    queue = ''
    (return_code, stdout, stderr) = common.run_command(command)
    bmgroup_dic = get_bmgroup_info(get_bmgroup_info_command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if queue_compile.match(line):
            my_match = queue_compile.match(line)
            queue = my_match.group(1)
            queue_host_dic[queue] = []

        if hosts_compile.match(line):
            my_match = hosts_compile.match(line)
            hosts_string = my_match.group(1)

            if hosts_all_compile.search(hosts_string):
                common.bprint('Queue "' + str(queue) + '" is not well configured, all of the hosts are on the same queue.', level='Warning')
                queue_host_dic[queue] = get_host_list(get_hosts_list_command)
            else:
                queue_host_dic.setdefault(queue, [])
                hosts_list = hosts_string.split()

                for hosts in hosts_list:
                    if re.match(r'\S+/', hosts):
                        host_group_name = re.sub(r'/$', '', hosts)
                        host_list = []

                        if host_group_name in bmgroup_dic.keys():
                            host_list = bmgroup_dic[host_group_name]

                        if len(host_list) > 0:
                            queue_host_dic[queue].extend(host_list)
                    elif re.match(r'^(\S+)\+\d+$', hosts):
                        my_match = re.match(r'^(\S+)\+\d+$', hosts)
                        host = my_match.group(1)
                        queue_host_dic[queue].append(host)
                    else:
                        queue_host_dic[queue].append(hosts)

    return queue_host_dic


def get_host_queue_info(command='bqueues -l', get_hosts_list_command='bhosts -w', get_bmgroup_info_command='bmgroup -w -r'):
    """
    Get queue info of specified hosts with command "bqueues/bmgroup".
    """
    host_queue_dic = {}
    queue_host_dic = get_queue_host_info(command, get_hosts_list_command, get_bmgroup_info_command)
    queue_list = list(queue_host_dic.keys())

    for queue in queue_list:
        host_list = queue_host_dic[queue]

        for host in host_list:
            if host in host_queue_dic.keys():
                host_queue_dic[host].append(queue)
            else:
                host_queue_dic[host] = [queue, ]

    return host_queue_dic


def get_lsf_unit_for_limits(command='badmin showconf mbd all'):
    """
    Get LSF LSF_UNIT_FOR_LIMITS setting, it could be KB/MB/GB/TB.
    """
    lsf_unit_for_limits = 'MB'
    (return_code, stdout, stderr) = common.run_command(command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if re.match(r'^\s*LSF_UNIT_FOR_LIMITS\s*=\s*(\S+)\s*$', line):
            my_match = re.match(r'^\s*LSF_UNIT_FOR_LIMITS\s*=\s*(\S+)\s*$', line)
            lsf_unit_for_limits = my_match.group(1)
            break

    return lsf_unit_for_limits


def switch_bjobs_uf_time(bjobs_uf_time, format=''):
    """
    Switch bjobs_uf_time from "%Y %b %d %H:%M:%S" into specified format.
    """
    new_bjobs_uf_time = bjobs_uf_time

    if bjobs_uf_time and (bjobs_uf_time != 'N/A'):
        # Switch bjobs_uf_time to start_seconds.
        current_year = datetime.date.today().year
        bjobs_uf_time_list = bjobs_uf_time.split()

        current_seconds = time.time()
        bjobs_uf_time_with_year = str(current_year) + ' ' + str(bjobs_uf_time_list[1]) + ' ' + str(bjobs_uf_time_list[2]) + ' ' + str(bjobs_uf_time_list[3])

        try:
            start_seconds = time.mktime(time.strptime(bjobs_uf_time_with_year, '%Y %b %d %H:%M:%S'))
        except Exception:
            return new_bjobs_uf_time

        if int(start_seconds) > int(current_seconds):
            current_year = int(datetime.date.today().year) - 1
            bjobs_uf_time_with_year = str(current_year) + ' ' + str(bjobs_uf_time_list[1]) + ' ' + str(bjobs_uf_time_list[2]) + ' ' + str(bjobs_uf_time_list[3])

            try:
                start_seconds = time.mktime(time.strptime(bjobs_uf_time_with_year, '%Y %b %d %H:%M:%S'))
            except Exception:
                return new_bjobs_uf_time

        # Switch start_seconds to expected time format.
        new_bjobs_uf_time = time.strftime(format, time.localtime(start_seconds))

    return new_bjobs_uf_time
