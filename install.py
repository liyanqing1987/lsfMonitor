# -*- coding: utf-8 -*-
################################
# File Name   : install.py
# Author      : liyanqing.1987
# Created On  : 2026-02-01 16:57:01
# Description :
################################
import os
import sys
import shlex
import subprocess
import argparse

CWD = os.getcwd()
os.environ['PYTHONUNBUFFERED'] = '1'


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-p', '--prefix',
                        default=CWD,
                        help='Specify lsfMonitor install path on config file, default is current directory.')
    parser.add_argument('-c', '--clean',
                        action='store_true',
                        default=False,
                        help='Cleanup old installation.')
    parser.add_argument('-f', '--force',
                        action='store_true',
                        default=False,
                        help='Install by force.')
    parser.add_argument('-m', '--memPrediction',
                        action='store_true',
                        default=False,
                        help='Install memPrediction the same time.')

    args = parser.parse_args()

    return args.prefix, args.clean, args.force, args.memPrediction


class Installation():
    def __init__(self, prefix, clean_mode, force_mode, install_memPrediction):
        self.prefix = os.path.abspath(prefix)
        self.clean_mode = clean_mode
        self.force_mode = force_mode
        self.install_memPrediction = install_memPrediction
        self.tool_list = [
            'bin/bmonitor',
            'bin/bmonitor_cli',
            'bin/bsample',
            'bin/license_monitor',
            'bin/license_sample',
            'tools/akill',
            'tools/check_issue_reason',
            'tools/gen_LM_LICENSE_FILE',
            'tools/patch',
            'tools/process_tracer',
            'tools/rag_builder',
            'tools/seedb'
        ]
        # One config file per sidebar tab, loaded by common_config.load_config.
        self.lsf_config_file = os.path.join(CWD, 'config', 'config_lsf.py')
        self.license_config_file = os.path.join(CWD, 'config', 'config_license.py')
        self.run_config_file = os.path.join(CWD, 'config', 'config_run.py')
        self.ai_config_file = os.path.join(CWD, 'config', 'config_ai.py')
        self.default_config_file = os.path.join(CWD, 'config', 'config.py')

    def cleanup(self):
        """
        Cleanup shell tools and configuration file.
        """
        print('>>> Cleanup')
        remove_list = self.tool_list + [self.lsf_config_file, self.license_config_file, self.run_config_file, self.ai_config_file, self.default_config_file]
        exit_code = 0

        for remove_file in remove_list:
            try:
                remove_path = os.path.join(CWD, remove_file)

                if os.path.exists(remove_path):
                    print(f'    Remove "{remove_path}"')
                    os.remove(remove_path)
            except Exception as warning:
                exit_code += 1
                print(f'    *Warning*: Failed on removing "{remove_file}": {warning}')

        sys.exit(exit_code)

    def check_python_version(self):
        """
        python3.12.12 or newer version is required.
        """
        print('\n>>> Check python version.')
        current_python = sys.version_info[:3]
        required_python = (3, 12, 12)

        if current_python < required_python:
            print(f'    *Warning*: Unsuggested Python version, lsfMonitor requires python{required_python[0]}.{required_python[1]}.{required_python[2]} or newer version, but you\'re trying to install it with python{current_python[0]}.{current_python[1]}.{current_python[2]}.')

            if not self.force_mode:
                sys.exit(1)
        else:
            print(f'    Required python version : {required_python[0]}.{required_python[1]}.{required_python[2]}')
            print(f'    Current  python version : {current_python[0]}.{current_python[1]}.{current_python[2]}')

    def gen_shell_tools(self):
        """
        Generate shell scripts under <LSFMONITOR_INSTALL_PATH>.
        Both lsf and license tools share the single LSFMONITOR_INSTALL_PATH
        (project root); license code resolves its subsystem root as
        $LSFMONITOR_INSTALL_PATH/license internally.
        """
        print('\n>>> Generate shell tools')

        PYTHON_PATH = os.path.dirname(os.path.abspath(sys.executable))
        ld_library_path_setting = 'export LD_LIBRARY_PATH=$LSFMONITOR_INSTALL_PATH/lib'

        if 'LD_LIBRARY_PATH' in os.environ:
            ld_library_path_setting += ':' + os.environ['LD_LIBRARY_PATH']

        for tool_name in self.tool_list:
            tool_path = os.path.join(CWD, tool_name)

            print(f'    Generate "{tool_path}".')

            try:
                # Ensure directory exists
                os.makedirs(os.path.dirname(tool_path), exist_ok=True)

                with open(tool_path, 'w') as SP:
                    script_content = f"""#!/bin/bash

# Set python3 path.
export PATH={PYTHON_PATH}:$PATH

# Set install path (project root; shared by lsf + license subsystems).
export LSFMONITOR_INSTALL_PATH={shlex.quote(self.prefix)}

# Set LD_LIBRARY_PATH.
{ld_library_path_setting}

# Set input method for Qt5 (auto-detect ibus/fcitx).
if [ -z "$QT_IM_MODULE" ]; then
    if pgrep -x ibus-daemon > /dev/null 2>&1; then
        export QT_IM_MODULE=ibus
    elif pgrep -x fcitx > /dev/null 2>&1 || pgrep -x fcitx5 > /dev/null 2>&1; then
        export QT_IM_MODULE=fcitx
    fi
fi

# Capture the user's original command line (wrapper name + args) so the launch
# log records what the user typed (e.g. "bmonitor -p AI"), not the expanded
# "python3 <path>/bmonitor.py -p AI" that Python's sys.argv sees.
export LSFMONITOR_ORIG_CMDLINE="$0 $*"

# Execute {tool_name}.py
python3 $LSFMONITOR_INSTALL_PATH/{tool_name}.py "$@"
"""
                    SP.write(script_content)

                os.chmod(tool_path, 0o755)
            except Exception as error:
                print(f'    *Error*: Failed on generating script "{tool_path}": {error}')
                sys.exit(1)

    def gen_default_config_file(self):
        """Generate <prefix>/config/config.py (top-level project defaults).

        Currently only holds db_path, the root data directory shared by all
        subsystems (lsf/license/run/ai/log). Each config_<name>.py defaults its
        db_path to empty, which falls back to config.py's <db_path> + '/<name>'.
        """
        print(f'\n>>> Generate default config file "{self.default_config_file}".')

        if os.path.exists(self.default_config_file) and not self.force_mode:
            print(f'    *Warning*: config file "{self.default_config_file}" already exists, will not update it.')
            return

        db_path = os.path.join(self.prefix, 'db')

        try:
            os.makedirs(os.path.dirname(self.default_config_file), exist_ok=True)

            with open(self.default_config_file, 'w') as CF:
                CF.write(f'''# Specify the database directory.
db_path = "{db_path}"
''')

            os.chmod(self.default_config_file, 0o755)
        except Exception as error:
            print(f'    *Error*: Failed on opening config file "{self.default_config_file}" for write: {error}')
            sys.exit(1)

    def ensure_db_root(self):
        """Ensure <prefix>/db/ exists with 1777 so any user can create their
        own subsystem subdir (lsf/license/ai/run/log) at runtime. Run
        unconditionally (not gated by config file existence) so a re-install
        still fixes db/ permissions.

        Golden permission spec: docs/db_permissions.md (single source of truth).

        Also fix the mode of pre-existing shared db subdirs to 1777 if they
        exist — e.g. after a `cp -r` that stripped the sticky/write bits via
        umask. Covers:
          - db/{ai,log,run}            (top-level shared db subdirs)
          - db/ai/ai_report and its   (shared AI report output; every user
            <cluster/job/queue/user>   writes their own *.html here — resolve_
                                       report_dir only self-heals for the dir
                                       owner, so fix it at install time)
          - db/ai/rag                  (shared RAG index files written by
                                       rag_builder / users; no runtime
                                       self-heal, so must be fixed here)
        lsf/license are left alone (their <cluster>/<server> subdirs are
        created per-user at runtime under the 1777 db/ root).
        """
        db_root = os.path.join(self.prefix, 'db')

        try:
            os.makedirs(db_root, exist_ok=True)
            os.chmod(db_root, 0o1777)
        except Exception as warning:
            print(f'    *Warning*: Failed on opening write permission for "{db_root}": {warning}')

        # Shared db subdirs that must be 1777 (shared db files, multi-user write).
        # Only fix mode if the dir already exists; do NOT create them here —
        # each tool creates its own on first use.
        for name in ('ai', 'log', 'run'):
            sub = os.path.join(db_root, name)

            if os.path.isdir(sub):
                try:
                    os.chmod(sub, 0o1777)
                except Exception as warning:
                    print(f'    *Warning*: Failed on setting 1777 for "{sub}": {warning}')

        # Shared subdirs under db/ai/ that also need 1777:
        #   - ai_report/ and each <type>/ (cluster/job/queue/user)
        #   - rag/  (RAG index output, multi-user write)
        # A `cp -r` of the db tree leaves these at 0o755 via umask; without
        # this fix, non-owner users cannot write AI reports / RAG files.
        for name in ('ai_report', 'rag'):
            top = os.path.join(db_root, 'ai', name)

            if os.path.isdir(top):
                for path in (top, *self._list_subdirs(top)):
                    try:
                        os.chmod(path, 0o1777)
                    except Exception as warning:
                        print(f'    *Warning*: Failed on setting 1777 for "{path}": {warning}')

    @staticmethod
    def _list_subdirs(path):
        """Yield all regular subdirectories under path (non-recursive one level)."""
        try:
            for entry in os.listdir(path):
                sub = os.path.join(path, entry)
                if os.path.isdir(sub) and not os.path.islink(sub):
                    yield sub
        except OSError:
            return

    def gen_lsf_config_file(self):
        """Generate <prefix>/config/config_lsf.py for the LSF panel."""
        print(f'\n>>> Generate LSF config file "{self.lsf_config_file}".')

        if os.path.exists(self.lsf_config_file) and not self.force_mode:
            print(f'    *Warning*: config file "{self.lsf_config_file}" already exists, will not update it.')
            return

        try:
            os.makedirs(os.path.dirname(self.lsf_config_file), exist_ok=True)

            with open(self.lsf_config_file, 'w') as CF:
                CF.write('''# Specify the lsf database directory.
# Empty by default — falls back to config.py's <db_path> + '/lsf'.
db_path = ""

# Data retention days for cleanup (bsample --cleanup).
cleanup_expire_days = {'job': 30, 'job_data': 30, 'user': 365, 'queue': 365, 'queue_host_mapping': 365, 'group_host_mapping': 365, 'host': 365, 'load': 365, 'utilization': 365, 'utilization_day': 365}
''')

            os.chmod(self.lsf_config_file, 0o755)
        except Exception as error:
            print(f'    *Error*: Failed on opening config file "{self.lsf_config_file}" for write: {error}')
            sys.exit(1)

        # Ensure config/lsf/ exists for LSF subsystem data files
        # (exit_code.yaml, term_signal.yaml).
        lsf_data_dir = os.path.join(self.prefix, 'config', 'lsf')

        try:
            os.makedirs(lsf_data_dir, exist_ok=True)
        except Exception as warning:
            print(f'    *Warning*: Failed on creating "{lsf_data_dir}": {warning}')

    def gen_ai_config_file(self):
        """Generate <prefix>/config/config_ai.py for the AI panel."""
        print(f'\n>>> Generate AI config file "{self.ai_config_file}".')

        if os.path.exists(self.ai_config_file) and not self.force_mode:
            print(f'    *Warning*: config file "{self.ai_config_file}" already exists, will not update it.')
            return

        try:
            os.makedirs(os.path.dirname(self.ai_config_file), exist_ok=True)

            with open(self.ai_config_file, 'w') as CF:
                CF.write('''# Specify the ai database directory.
# Empty by default — falls back to config.py's <db_path> + '/ai'.
db_path = ""

# AI helpdesk settings (OpenAI-compatible API).
ai_api_base_url = ""
ai_api_key = ""
ai_model_name = ""

# AI embedding model for RAG documentation search (optional).
# If ai_embedding_api_base_url/ai_embedding_api_key are empty, falls back to ai_api_base_url/ai_api_key.
ai_embedding_api_base_url = ""
ai_embedding_api_key = ""
ai_embedding_model_name = ""

# Commands requiring user confirmation before AI executes (space-separated).
ai_dangerous_commands = "bkill badmin brestart bstop bresume bswitch bmod rm kill killall shutdown reboot mkfs dd eval source xargs"
''')

            os.chmod(self.ai_config_file, 0o755)
        except Exception as error:
            print(f'    *Error*: Failed on opening config file "{self.ai_config_file}" for write: {error}')
            sys.exit(1)

    def gen_run_config_file(self):
        """Generate <prefix>/config/config_run.py for the Run panel."""
        print(f'\n>>> Generate Run config file "{self.run_config_file}".')

        if os.path.exists(self.run_config_file) and not self.force_mode:
            print(f'    *Warning*: config file "{self.run_config_file}" already exists, will not update it.')
            return

        try:
            os.makedirs(os.path.dirname(self.run_config_file), exist_ok=True)

            with open(self.run_config_file, 'w') as CF:
                CF.write('''# Specify the run database directory.
# Empty by default — falls back to config.py's <db_path> + '/run'.
db_path = ""

# Default ssh command.
default_ssh_command = "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ConnectionAttempts=2 -o GSSAPIAuthentication=no -t -q"

# Define timeout for ssh command, unit is "second".
parallel_timeout = 20

# Upper bound on parallel ssh sessions.
max_parallel = 512
''')

            os.chmod(self.run_config_file, 0o755)
        except Exception as error:
            print(f'    *Error*: Failed on opening config file "{self.run_config_file}" for write: {error}')
            sys.exit(1)

    def gen_license_config_file(self):
        """
        Generate license subsystem config file <prefix>/config/config_license.py.
        Loaded by common_config.load_config('license'); override at
        ~/.lsfMonitor/config/config_license.py.
        """
        print(f'\n>>> Generate license config file "{self.license_config_file}".')

        if os.path.exists(self.license_config_file) and not self.force_mode:
            print(f'    *Warning*: config file "{self.license_config_file}" already exists, will not update it.')
            return

        lmstat_path = os.path.join(self.prefix, 'tools', 'lmstat')

        try:
            os.makedirs(os.path.dirname(self.license_config_file), exist_ok=True)

            with open(self.license_config_file, 'w') as CF:
                config_content = f"""# Specify the license database directory.
# Empty by default — falls back to config.py's <db_path> + '/license'.
db_path = ""

# Specify EDA license administrators.
administrators = "ALL"

# Specify lmstat path, example "/eda/synopsys/scl/2021.03/linux64/bin/lmstat".
lmstat_path = "{lmstat_path}"

# Specify lmstat bsub command, example "bsub -q normal -Is".
lmstat_bsub_command = "bsub -q normal -Is"

# The time interval to fresh license information automatically, unit is "second".
fresh_interval = 300

# Data retention days for cleanup (license_sample -c).
cleanup_expire_days = {{'usage': 365, 'utilization': 365, 'utilization_day': 365}}
"""
                CF.write(config_content)

            os.chmod(self.license_config_file, 0o755)
        except Exception as error:
            print(f'    *Error*: Failed on opening config file "{self.license_config_file}" for write: {error}')
            sys.exit(1)

    def gen_license_config_files(self):
        """
        Generate license subsystem data files (LM_LICENSE_FILE, utilization
        filter files) under <prefix>/config/.
        """
        config_dir = os.path.join(CWD, 'config')

        lm_license_file = os.path.join(config_dir, 'license', 'LM_LICENSE_FILE')

        self._gen_license_text_file(lm_license_file, '# Example:\n# 5280@lic_server1\n# 27020@lic_server2\n# 1717@lic_server3\n\n')

        # Ensure config/license/ exists for license subsystem data files.
        new_license_dir = os.path.join(config_dir, 'license')
        os.makedirs(new_license_dir, exist_ok=True)

    def _gen_license_text_file(self, file_path, content):
        """Generate a license subsystem text config file if not exists."""
        print(f'    Generate "{file_path}".')

        if os.path.exists(file_path):
            print(f'        *Warning*: file "{file_path}" already exists, will not update it.')
            return

        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, 'w') as F:
                F.write(content)

            os.chmod(file_path, 0o755)
        except Exception as error:
            print(f'    *Error*: Failed on opening file "{file_path}" for write: {error}')
            sys.exit(1)

    def install_memPrediction_tool(self):
        """
        Install memPrediction function.
        """
        mem_prediction_path = os.path.join(self.prefix, 'memPrediction')
        command = [sys.executable, 'install.py', '--prefix', mem_prediction_path]

        if self.clean_mode:
            command.append('--clean')

        if self.force_mode:
            command.append('--force')

        print('\n>>> Install tool "memPrediction" ...')
        print(f'    {" ".join(command)}')

        try:
            SP = subprocess.Popen(command, cwd='memPrediction', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            stdout, stderr = SP.communicate()
            return_code = SP.returncode

            if return_code != 0:
                print(f'    *Error*: Failed on installing tool "memPrediction" (exit code {return_code}): {stdout}')

                if stderr:
                    print(f'    *Error*: stderr: {stderr}')

                sys.exit(1)
        except Exception as error:
            print(f'    *Error*: Exception during memPrediction installation: {error}')
            sys.exit(1)

    def run(self):
        if self.clean_mode:
            self.cleanup()

        self.check_python_version()
        self.gen_shell_tools()
        self.ensure_db_root()
        self.gen_default_config_file()
        self.gen_lsf_config_file()
        self.gen_license_config_file()
        self.gen_run_config_file()
        self.gen_ai_config_file()
        self.gen_license_config_files()

        if self.install_memPrediction:
            self.install_memPrediction_tool()

        print('\nDone, Please enjoy it.')


################
# Main Process #
################
def main():
    (prefix, clean_mode, force_mode, install_memPrediction) = read_args()
    my_installation = Installation(prefix, clean_mode, force_mode, install_memPrediction)
    my_installation.run()


if __name__ == '__main__':
    main()
