# -*- coding: utf-8 -*-
################################
# File Name   : install.py
# Author      : liyanqing.1987
# Created On  : 2026-02-01 16:57:01
# Description :
################################
import os
import sys
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
            'monitor/bin/bmonitor',
            'monitor/bin/bsample',
            'monitor/tools/akill',
            'monitor/tools/check_issue_reason',
            'monitor/tools/patch',
            'monitor/tools/process_tracer',
            'monitor/tools/seedb',
            'monitor/tools/show_license_feature_usage'
        ]
        self.config_file = os.path.join(CWD, 'monitor', 'conf', 'config.py')

    def cleanup(self):
        """
        Cleanup shell tools and configuration file.
        """
        print('>>> Cleanup')
        remove_list = self.tool_list + [self.config_file]
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
        Generate shell scripts under <LSFMONITOR_INSTALL_PATH>/tools.
        """
        print('\n>>> Generate shell tools')

        for tool_name in self.tool_list:
            tool_path = os.path.join(CWD, tool_name)
            ld_library_path_setting = 'export LD_LIBRARY_PATH=$LSFMONITOR_INSTALL_PATH/lib:'

            if 'LD_LIBRARY_PATH' in os.environ:
                ld_library_path_setting += os.environ['LD_LIBRARY_PATH']

            print(f'    Generate "{tool_path}".')

            try:
                # Ensure directory exists
                os.makedirs(os.path.dirname(tool_path), exist_ok=True)

                with open(tool_path, 'w') as SP:
                    PYTHON_PATH = os.path.dirname(os.path.abspath(sys.executable))
                    script_content = f"""#!/bin/bash

# Set python3 path.
export PATH={PYTHON_PATH}:$PATH

# Set install path.
export LSFMONITOR_INSTALL_PATH={self.prefix}

# Set LD_LIBRARY_PATH.
{ld_library_path_setting}

# Execute {tool_name}.py
python3 $LSFMONITOR_INSTALL_PATH/{tool_name}.py "$@"
"""
                    SP.write(script_content)

                os.chmod(tool_path, 0o755)
            except Exception as error:
                print(f'    *Error*: Failed on generating script "{tool_path}": {error}')
                sys.exit(1)

    def gen_config_file(self):
        """
        Generate config file <LSFMONITOR_INSTALL_PATH>/monitor/conf/config.py.
        """
        print(f'\n>>> Generate config file "{self.config_file}".')

        if os.path.exists(self.config_file) and not self.force_mode:
            print(f'    *Warning*: config file "{self.config_file}" already exists, will not update it.')
            return

        db_path = os.path.join(self.prefix, 'db')
        lmstat_path = os.path.join(self.prefix, 'monitor', 'tools', 'lmstat')

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

            with open(self.config_file, 'w') as CF:
                config_content = f"""# Specify the database directory.
db_path = "{db_path}"

# Specify EDA license administrators.
license_administrators = "all"

# Specify lmstat path, example "/eda/synopsys/scl/2021.03/linux64/bin/lmstat".
lmstat_path = "{lmstat_path}"

# Specify lmstat bsub command, example "bsub -q normal -Is".
lmstat_bsub_command = ""

# Excluded license servers, format is "27020@lic_server 5280@lic_server".
excluded_license_servers = ""
"""
                CF.write(config_content)

            os.chmod(self.config_file, 0o777)
        except Exception as error:
            print(f'    *Error*: Failed on opening config file "{self.config_file}" for write: {error}')
            sys.exit(1)

        try:
            os.makedirs(db_path, exist_ok=True)
            os.chmod(db_path, 0o777)
        except Exception as warning:
            print(f'    *Warning*: Failed on opening write permission for "{db_path}": {warning}')

    def install_memPrediction_tool(self):
        """
        Install memPrediction function.
        """
        mem_prediction_path = os.path.join(self.prefix, 'memPrediction')
        command = f'cd memPrediction; {sys.executable} install.py --prefix {mem_prediction_path}'

        if self.clean_mode:
            command += ' --clean'

        if self.force_mode:
            command += ' --force'

        print('\n>>> Install tool "memPrediction" ...')
        print(f'    {command}')

        try:
            SP = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            stdout, stderr = SP.communicate()
            return_code = SP.returncode

            if return_code != 0:
                print(f'    *Error*: Failed on installing tool "memPrediction": {stdout}')

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
        self.gen_config_file()

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
