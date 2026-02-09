# -*- coding: utf-8 -*-
################################
# File Name   : install.py
# Author      : zhangjingwen.silvia
# Created On  : 2026-02-03 10:16:02
# Description :
################################
import os
import shutil
import sys
import socket
import argparse

CWD = os.getcwd()
PYTHON_PATH = os.path.dirname(os.path.abspath(sys.executable))
os.environ['PYTHONUNBUFFERED'] = '1'


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-p', '--prefix',
                        default=CWD,
                        help='Specify memPrediction install path on config file, default is current directory.')
    parser.add_argument('-c', '--clean',
                        action='store_true',
                        default=False,
                        help='Cleanup old installation.')
    parser.add_argument('-f', '--force',
                        action='store_true',
                        default=False,
                        help='Install by force.')

    args = parser.parse_args()

    return args.prefix, args.clean, args.force


class Installation:
    def __init__(self, prefix, clean_mode, force_mode):
        self.prefix = prefix
        self.clean_mode = clean_mode
        self.force_mode = force_mode
        self.tool_list = ['bin/sample', 'bin/report', 'bin/train', 'bin/predict', 'tools/update', 'web_app/setup', 'web_app/backend/dataCollector',
                          'tools/.env', 'tools/predict_web.service', 'config/config.py', 'tools/stopservice.sh', 'tools/predict_gconf.py',
                          'tools/esub.mem_predict', 'tools/train.sh', 'tools/update', 'tools/web_startup.sh', 'tools/predict_web.service'
                          'db/model_db/latest',
                          ]

        self.config_file = str(CWD) + '/config/config.py'

    def check_python_version(self):
        """
        python3.12.12 or newer version is required.
        """
        print('\n>>> Check python version.')
        current_python = sys.version_info[:3]
        required_python = (3, 12, 12)

        if current_python < required_python:
            print('    *Warning*: Unsuggested Python version, lsfMonitor requires python{}.{}.{} or newer version, but you\'re trying to install it with python{}.{}.{}.'.format(*(required_python + current_python)))

            if not self.force_mode:
                sys.exit(1)
        else:
            print('    Required python version : ' + str('.'.join(str(x) for x in required_python)))
            print('    Current  python version : ' + str('.'.join(str(y) for y in current_python)))

    def gen_shell_tools(self):
        """
        Generate shell scripts under <MEM_PREDICTION_INSTALL_PATH>/tools.
        """
        print('\n>>> Generate shell tools')

        tool_list = ['bin/sample', 'bin/report', 'bin/train', 'bin/predict', 'tools/update', 'web_app/setup', 'web_app/backend/dataCollector']

        for tool_name in tool_list:
            tool = str(CWD) + '/' + str(tool_name)
            ld_library_path_setting = 'export LD_LIBRARY_PATH=$MEM_PREDICTION_INSTALL_PATH/lib:'

            if 'LD_LIBRARY_PATH' in os.environ:
                ld_library_path_setting = str(ld_library_path_setting) + str(os.environ['LD_LIBRARY_PATH'])

            print('    Generate script "' + str(tool) + '".')

            try:
                with open(tool, 'w') as SP:
                    SP.write("""#!/bin/bash

# Set python3 path.
export PATH=""" + str(PYTHON_PATH) + """:$PATH

# Set install path.
export MEM_PREDICTION_INSTALL_PATH=""" + str(self.prefix) + """

# Set LD_LIBRARY_PATH.
""" + str(ld_library_path_setting) + """

# Execute """ + str(tool_name) + """.py.
python3 $MEM_PREDICTION_INSTALL_PATH/""" + str(tool_name) + '.py "$@"')

                os.chmod(tool, 0o755)
            except Exception as error:
                print('    *Error*: Failed on generating script "' + str(tool) + '": ' + str(error))
                sys.exit(1)

    def gen_predict_env_tools(self):
        """
        Generate web env scripts under <MEM_PREDICTION_INSTALL_PATH>/tools/*env.
        """
        tool = str(CWD) + '/' + 'tools/.env'
        ld_library_path_setting = 'LD_LIBRARY_PATH=$MEM_PREDICTION_INSTALL_PATH/lib:'

        if 'LD_LIBRARY_PATH' in os.environ:
            ld_library_path_setting = str(ld_library_path_setting) + str(os.environ['LD_LIBRARY_PATH'])

        print('\n>>> Generate script "' + str(tool) + '".')

        try:
            with open(tool, 'w') as SP:
                SP.write("""
# Set python3 path.
PATH=""" + str(PYTHON_PATH) + """:$PATH

# Set install path.
MEM_PREDICTION_INSTALL_PATH=""" + str(self.prefix) + """

# Set LD_LIBRARY_PATH.
""" + str(ld_library_path_setting))

            os.chmod(tool, 0o755)
        except Exception as error:
            print('    *Error*: Failed on generating script "' + str(tool) + '": ' + str(error))
            sys.exit(1)

    def gen_web_service_tools(self):
        """
        Generate web service scripts under <MEM_PREDICTION_INSTALL_PATH>/tools/web_service.
        """
        # gen web service scripts
        web_service_tool = str(CWD) + '/' + 'tools/predict_web.service'

        print('\n>>> Generate script "' + str(web_service_tool) + '".')

        try:
            with open(web_service_tool, 'w') as SP:
                SP.write("""
[Unit]
Description=LSF memory prediction web service
After=syslog.target network.target

[Service]
Type=simple
WorkingDirectory=""" + str(self.prefix) + '/tools' + """
EnvironmentFile=""" + str(self.prefix) + '/tools/.env' + ''"""
ExecStart=""" + str(PYTHON_PATH) + '/gunicorn' + """ -c predict_gconf.py predict_web:app
ExecStop=""" + str(self.prefix) + '/stopservice.sh' + """

Restart=on-failure

[Install]
WantedBy=multi-user.target""")

            os.chmod(web_service_tool, 0o755)
        except Exception as error:
            print('    *Error*: Failed on generating script "' + str(web_service_tool) + '": ' + str(error))
            sys.exit(1)

        # generate stop service scripts
        stop_service_tool = str(CWD) + '/' + 'tools/stopservice.sh'

        print('\n>>> Generate script "' + str(stop_service_tool) + '".')

        try:
            with open(stop_service_tool, 'w') as SP:
                SP.write("""#!/bin/bash

ps -elf|grep '""" + str(PYTHON_PATH) + '/gunicorn' + """ -c predict_gconf.py predict_web:app'|grep -v grep|awk '{print $4}'|xargs kill""")

            os.chmod(stop_service_tool, 0o755)
        except Exception as error:
            print('    *Error*: Failed on generating script "' + str(stop_service_tool) + '": ' + str(error))
            sys.exit(1)

    def gen_config_file(self):
        """
        Generate config file <MEM_PREDICTION_INSTALL_PATH>/config/config.py.
        """
        config_file = self.config_file

        print('\n>>> Generate config file "' + str(config_file) + '".')

        if os.path.exists(self.config_file) and (not self.force_mode):
            print('    *Warning*: config file "' + str(config_file) + '" already exists, will not update it.')
        else:
            try:
                job_db_path = str(CWD) + '/db/job_db'
                report_db_path = str(CWD) + '/db/report_db'
                model_db_path = str(CWD) + '/db/model_db'
                report_template = str(self.prefix) + '/config/rusage_report_template.md'
                training_config = str(self.prefix) + '/config/training.config.yaml'
                default_predict_model = str(self.prefix) + '/db/model_db/latest'

                with open(config_file, 'w') as CF:
                    CF.write('''# job infomation database save directory, format: csv/sqlite.
db_path = "''' + str(self.prefix) + '/db/job_db' + '''"

# Specify job database format
job_format = 'csv'

# job rusage analysis report template
report_template = "''' + report_template + '''"

# job rusage analysis report db path
report_path = "''' + str(self.prefix) + '/db/report_db' + '''"

# training job memory model config yaml file
training_config_yaml = "''' + training_config + '''"

# train and save model this directory
model_db_path = "''' + str(self.prefix) + '/db/model_db' + '''"

# prediction model config yaml
predict_model = "''' + default_predict_model + '''"

# model training max lines, default 10,000,000. if set to '0' or '', means infinity.
max_training_lines = 10000000
''')
                os.makedirs(job_db_path, exist_ok=True)
                os.makedirs(report_db_path, exist_ok=True)
                os.makedirs(model_db_path, exist_ok=True)
                os.chmod(config_file, 0o777)
                os.chmod(job_db_path, 0o777)
                os.chmod(report_db_path, 0o777)
                os.chmod(model_db_path, 0o777)
            except Exception as error:
                print('    *Error*: Failed on opening config file "' + str(config_file) + '" for write: ' + str(error))
                sys.exit(1)

    def get_host_ip(self):
        """
        Get current host ip
        :return: ip
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()

        if not ip:
            print("    *Error*: Could not find valid ip.")
            sys.exit(1)

        return ip

    def get_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            _, port = s.getsockname()

        if not port:
            print("    *Error*: Could not find free port.")
            sys.exit(1)

        return port

    def replace_key_word(self, ip=None, port=None):
        file_list = [
            os.path.join(CWD, 'tools/predict_gconf.py.template'),
            os.path.join(CWD, 'tools/esub.mem_predict.template')
        ]

        for file_path in file_list:
            new_file = file_path.replace('.template', '')
            print('\n>>> Generate config file "' + str(new_file) + '".')

            if os.path.exists(new_file) and (not self.force_mode):
                print('    *Warning*: config file "' + str(new_file) + '" already exists, will not update it.')
            else:
                line_list = []

                with open(file_path, 'r') as ff:
                    for line in ff:
                        line = line.replace('$IP', str(ip)).replace('$PORT', str(port))
                        line_list.append(line)

                with open(new_file, 'w') as ff:
                    ff.write(''.join(line_list))

    def gen_training_scripts(self):
        """
        Generate training scripts under <MEM_PREDICTION_INSTALL_PATH>/tools/train.sh.
        """
        tool = str(CWD) + '/' + 'tools/train.sh'
        training_tool = os.path.join(str(self.prefix), 'bin/train')
        update_tool = os.path.join(str(self.prefix), 'tools/update')

        print('\n>>> Generate script "' + str(tool) + '".')

        try:
            with open(tool, 'w') as SP:
                SP.write("""#!/bin/bash

# Training a new model
""" + str(training_tool) + """

exit_code=$?

if [ $exit_code -ne 0 ]; then
    echo "training failed."
fi

# Update config predict model after training
""" + str(update_tool) + """

exit_code=$?

if [ $exit_code -ne 0 ]; then
    echo "update failed."
fi
""")

            os.chmod(tool, 0o755)
        except Exception as error:
            print('    *Error*: Failed on generating script "' + str(tool) + '": ' + str(error))
            sys.exit(1)

    def gen_web_service_startup(self):
        """
        Generate training scripts under <MEM_PREDICTION_INSTALL_PATH>/tools/train.sh.
        """
        tool = str(CWD) + '/' + 'tools/web_startup.sh'
        service_tool = os.path.join(str(self.prefix), 'tools/predict_web.service')

        print('\n>>> Generate script "' + str(tool) + '".')

        try:
            with open(tool, 'w') as SP:
                SP.write("""#!/bin/bash
echo "Start predict web service ..."

cp -rf """ + str(service_tool) + """ /lib/systemd/system/

echo "systemctl enable predict_web"
systemctl enable predict_web.service

echo "systemctl start predict_web"
systemctl start predict_web.service

echo "check predict web status..."
systemctl status predict_web.service

""")

            os.chmod(tool, 0o755)
        except Exception as error:
            print('    *Error*: Failed on generating script "' + str(tool) + '": ' + str(error))
            sys.exit(1)

    def cleanup(self):
        """
        Cleanup shell tools and configuration file.
        """
        print('\n>>> Cleanup')
        remove_list = self.tool_list + [self.config_file]
        exit_code = 0

        for remove_file in remove_list:
            remove_file = os.path.join(CWD, remove_file)

            try:
                if os.path.exists(remove_file):
                    print('    Remove "' + str(remove_file) + '"')

                    if os.path.isfile(remove_file):
                        os.remove(remove_file)
                    else:
                        shutil.rmtree(remove_file)
            except Exception as warning:
                exit_code += 1
                print('    *Warning*: Failed on removing "' + str(remove_file) + '": ' + str(warning))

        sys.exit(exit_code)

    def run(self):
        if self.clean_mode:
            self.cleanup()
        else:
            self.check_python_version()
            ip = self.get_host_ip()
            port = self.get_free_port()

            self.replace_key_word(ip=ip, port=port)
            self.gen_shell_tools()
            self.gen_predict_env_tools()
            self.gen_config_file()
            self.gen_web_service_tools()
            self.gen_training_scripts()
            self.gen_web_service_startup()

            print('\nDone, Please enjoy it.')


################
# Main Process #
################
def main():
    (prefix, clean_mode, force_mode) = read_args()
    my_installation = Installation(prefix, clean_mode, force_mode)
    my_installation.run()


if __name__ == '__main__':
    main()
