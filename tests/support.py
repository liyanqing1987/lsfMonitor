"""Shared fixtures for lsfMonitor tests."""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BJOBUS_W = """JOBID   USER      STAT  QUEUE      FROM_HOST   EXEC_HOST   JOB_NAME            SUBMIT_TIME
101     alice     RUN   normal     cmp01       2*cmp01     regression_top      Oct 26 17:43
102     bob       EXIT  normal     cmp01       cmp02       smoke_case          Oct 26 18:00
"""

BHOSTS_W = """HOST_NAME          STATUS          JL/U    MAX  NJOBS    RUN  SSUSP  USUSP    RSV
cmp01              ok              -       4    2        2    0      0        0
cmp02              closed_Full     -       4    1        0    0      0        0
"""

BQUEUES_W = """QUEUE_NAME      PRIO STATUS          MAX JL/U JL/P JL/H NJOBS  PEND   RUN  SUSP  RSV PJOBS
normal           30  Open:Active       -    -    -    -     2     0     2     0    0     0
"""

BQUEUES_L = """QUEUE: normal
  -- Default queue of the cluster.
  PARAMETERS/STATISTICS
  PRIO  NICE  STATUS          MAX JL/U JL/P JL/H NJOBS  PEND   RUN  SUSP
  30    0     Open:Active       -    -    -    -     2     0     2     0
  USERS: all
  HOSTS: cmp01 cmp02
"""

BHOSTS_L = """HOST  cmp01
STATUS           CPUF  JL/U    MAX  NJOBS    RUN  SSUSP  USUSP    RSV DISPATCH_WINDOW
ok              15.00     -     48      2      2      0      0      0      -

 CURRENT LOAD USED FOR SCHEDULING:
                r15s   r1m  r15m    ut    pg    io   ls    it   tmp   swp   mem  slots
 Total           0.0   0.0   0.0    2%   0.0     8    0 14324 1667_g 127.2_g  683_g     46
 Reserved        0.0   0.0   0.0    0%   0.0     0    0     0    0_m    0_m  178_g      -
"""

BUSERS = """USER/GROUP          JL/P    MAX  NJOBS   PEND    RUN  SSUSP  USUSP    RSV
alice               -       -    2       0       2    0      0        0
bob                 -       -    1       1       0    0      0        0
"""

LSID = """IBM Spectrum LSF 10.1.0.12, Jan 01 2024
My cluster name is DEMO_CLUSTER
My master name is demo-master
"""

LSID_VOLCLAVA = """volclava 1.0.0, Jan 01 2024
My cluster name is DEMO_CLUSTER
My master name is demo-master
"""

LSID_OPENLAVA = """Open_lava 3.3.1, Jan 01 2024
My cluster name is DEMO_CLUSTER
My master name is demo-master
"""

LSID_FIXTURES = {'lsf': LSID, 'volclava': LSID_VOLCLAVA, 'openlava': LSID_OPENLAVA}

BJOBUS_UF_LSF = """Job <101>, Job Name <regression_top>, User <alice>, Project <demo>, Status <RUN>, Queue <normal>, Command <make run>, Share group charged </alice>
Mon Oct 26 17:43:07: Submitted from host <cmp01>, CWD <$HOME>, 2 Task(s), Requested Resources <span[hosts=1] rusage[mem=123]>;
Mon Oct 26 17:43:07: Started 2 Task(s) on Host(s) <2*cmp01>, Allocated 2 Slot(s) on Host(s) <2*cmp01>, Execution Home </home/alice>, Execution CWD </home/alice>;
Mon Oct 26 17:46:17: Resource usage collected. The CPU time used is 13065 seconds. IDLE_FACTOR(cputime/runtime):   0.00; MEM: 2 Mbytes; SWAP: 238 Mbytes; NTHREAD: 4; PGID: 10643; PIDs: 10643 10644 10646;

 MEMORY USAGE:
 MAX MEM: 2 Mbytes;  AVG MEM: 2 Mbytes
"""

BJOBUS_UF_OPENLAVA = """Job <305>, User <alice>, Project <default>, Status <RUN>, Queue <normal>, Interactive pseudo-terminal shell mode, Command <sleep 1000>, Job Description <this is a test>
Sun Mar 23 10:08:18: Submitted from host <cmp01>, CWD <$HOME>, 2 Processors Requested, Requested Resources <rusage[mem=123]>;
Sun Mar 23 10:08:22: Started on 2 Hosts/Processors <cmp01> <cmp01>;
Sun Mar 23 10:08:36: Resource usage collected. MEM: 3 Mbytes; SWAP: 247 Mbytes; PGID: 23518; PIDs: 23518 ; PGID: 23523; PIDs: 23523 23524;

 MEMORY USAGE:
 MAX MEM: N/A MBytes;  AVG MEM: N/A MBytes
"""

LSLOAD = """HOST_NAME               status  r15s   r1m  r15m   ut    pg    ls    it   tmp    swp   mem
cmp01                 ok      0.7    0.3  0.2    5%    0.0   1     0    7391_m  1.9_g  931_m
cmp02                 ok      0.1    0.1  0.1    1%    0.0   0     0    7000_m  1.0_g  800_m
"""

LSHOSTS = """HOST_NAME type model cpuf ncpus maxmem maxswp server
cmp01 X86_64 Intel 100.0 8 16G 32G Yes
cmp02 X86_64 Intel 100.0 8 16G 32G Yes
"""

BMGROUP = """GROUP_NAME    HOSTS                     GROUP_ADMIN
pd           cmp01 cmp02                ( - )
"""

BPEEK = "hello from job 101\n"


def write_executable(path, content):
    """Write a small shell script and make it executable."""

    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content)

    os.chmod(path, 0o755)


def make_fake_bin(bin_dir, scheduler='lsf'):
    """Create fake scheduler/FlexNet commands returning deterministic fixtures.

    scheduler selects the `lsid` output: lsf (default), volclava or openlava.
    """

    os.makedirs(bin_dir, exist_ok=True)

    outputs = {
        'lsid': LSID_FIXTURES.get(scheduler, LSID),
        'busers': BUSERS,
        'lsload': LSLOAD,
        'lshosts': LSHOSTS,
        'bmgroup': BMGROUP,
        'bpeek': BPEEK,
        'bjobs': BJOBUS_W,
        'bhosts': BHOSTS_W,
        'bqueues': BQUEUES_W,
    }

    for name, text in outputs.items():
        script = '#!/bin/bash\ncat <<\'EOF\'\n' + text + 'EOF\n'
        write_executable(os.path.join(bin_dir, name), script)

    # bhosts -l / bqueues -l need different fixtures.
    write_executable(os.path.join(bin_dir, 'bhosts'), '''#!/bin/bash
if [[ "$*" == *"-l"* ]]; then
cat <<'EOF'
''' + BHOSTS_L + '''EOF
else
cat <<'EOF'
''' + BHOSTS_W + '''EOF
fi
''')

    write_executable(os.path.join(bin_dir, 'bqueues'), '''#!/bin/bash
if [[ "$*" == *"-l"* ]]; then
cat <<'EOF'
''' + BQUEUES_L + '''EOF
else
cat <<'EOF'
''' + BQUEUES_W + '''EOF
fi
''')


def make_install_tree(root, scheduler='lsf'):
    """Build a temporary install tree usable by bmonitor_cli.

    Returns (install_dir, fake_bin_dir, db_dir).
    """

    install_dir = os.path.join(root, 'install')
    config_dir = os.path.join(install_dir, 'config')
    fake_bin = os.path.join(root, 'fakebin')
    db_dir = os.path.join(install_dir, 'db', 'lsf')
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(os.path.join(config_dir, 'lsf'), exist_ok=True)
    os.makedirs(os.path.join(config_dir, 'license'), exist_ok=True)
    os.makedirs(db_dir, exist_ok=True)

    # The CLI imports common/* from the install path.
    os.symlink(os.path.join(REPO_ROOT, 'common'), os.path.join(install_dir, 'common'))

    configs = {
        'config.py': f'db_path = "{os.path.join(install_dir, "db")}"\n',
        'config_lsf.py': 'db_path = ""\ncleanup_expire_days = {}\n',
        'config_license.py': ('db_path = ""\nadministrators = "ALL"\n'
                              'lmstat_path = "lmstat"\nlmstat_bsub_command = ""\n'
                              'fresh_interval = 300\n'),
        'config_run.py': 'db_path = ""\nparallel_timeout = 20\nmax_parallel = 32\n',
        'config_ai.py': 'db_path = ""\nai_api_base_url = ""\nai_api_key = ""\n',
    }

    for name, text in configs.items():
        with open(os.path.join(config_dir, name), 'w', encoding='utf-8') as handle:
            handle.write(text)

    for name in ('exit_code.yaml', 'term_signal.yaml'):
        source = os.path.join(REPO_ROOT, 'config', 'lsf', name)
        target = os.path.join(config_dir, 'lsf', name)

        if os.path.isfile(source):
            with open(source, encoding='utf-8') as source_handle:
                with open(target, 'w', encoding='utf-8') as target_handle:
                    target_handle.write(source_handle.read())

    make_fake_bin(fake_bin, scheduler=scheduler)

    return install_dir, fake_bin, db_dir


def cli_env(install_dir, fake_bin, home_dir=None):
    """Return an environment for running bmonitor_cli against the fixtures."""

    env = dict(os.environ)
    env['LSFMONITOR_INSTALL_PATH'] = install_dir
    env['PATH'] = fake_bin + os.pathsep + env.get('PATH', '')
    env['PYTHONPATH'] = install_dir + os.pathsep + env.get('PYTHONPATH', '')

    if home_dir:
        env['HOME'] = home_dir

    env.pop('LM_LICENSE_FILE', None)

    return env


def repo_path(*parts):
    """Return a path inside the repository."""

    return os.path.join(REPO_ROOT, *parts)
