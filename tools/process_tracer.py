# -*- coding: utf-8 -*-
import os
import re
import sys
import argparse
import shlex
import subprocess

from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView, QAction, qApp, QMessageBox
from PyQt5.QtCore import Qt, QTimer

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']))
from common import common
from common import common_lsf
from common import common_pyqt5

os.environ['PYTHONUNBUFFERED'] = '1'


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser(
        description='process_tracer — LSF 进程追踪工具\n\n通过 LSF job 或本地 pid 获取进程树，并实时追踪进程状态（CPU/内存/STAT 等）。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('-j', '--job',
                        default='',
                        help='指定要追踪的 LSF job ID（在远端执行主机上查看进程）')
    parser.add_argument('-p', '--pid',
                        default='',
                        help='指定要追踪的本地 pid（在本地查看进程树）')

    args = parser.parse_args()

    if (not args.job) and (not args.pid):
        common.bprint('"--job" or "--pid" must be specified.', level='Error')
        sys.exit(1)

    return args.job, args.pid


class ProcessTracer(QMainWindow):
    def __init__(self, job, pid):
        super().__init__()
        self.job = job
        self.pid = pid
        # Populated by get_process_info when bsub/ps fails so gen_main_table can
        # show a warning dialog instead of a silently empty table.
        self.trace_error = ''

        self.preprocess()
        self.init_ui()

    def preprocess(self):
        self.job_dic = {}
        self.pid_list = []

        if self.job:
            (self.job_dic, self.pid_list) = self.check_job(self.job)
        elif self.pid:
            self.pid_list = self.check_pid(self.pid)

    def check_job(self, job):
        command = 'bjobs -UF ' + str(job)
        job_dic = common_lsf.get_lsf_bjobs_uf_info(command)

        if job not in job_dic:
            common.bprint(f'Job "{job}" is not found.', level='Error')
            sys.exit(1)

        if job_dic[job]['status'] != 'RUN':
            common.bprint(f'Job "{job}" is not running, cannot get process status.', level='Error')
            sys.exit(1)
        else:
            if not job_dic[job]['pids']:
                common.bprint(f'Not find PIDs information for job "{job}".', level='Error')
                sys.exit(1)

        return job_dic, job_dic[job]['pids']

    def check_pid(self, pid):
        pid_list = []
        command = 'pstree -p ' + str(pid)

        (return_code, stdout, stderr) = common.run_command(command)

        for line in str(stdout, 'utf-8').split('\n'):
            line = line.strip()

            tmp_pid_list = re.findall(r'\((\d+)\)', line)

            if tmp_pid_list:
                pid_list.extend(tmp_pid_list)

        if not pid_list:
            common.bprint('No valid pid was found.', level='Error')
            sys.exit(1)

        return pid_list

    def get_process_info(self):
        process_dic = {
                       'user': [],
                       'pid': [],
                       'cpu': [],
                       'mem': [],
                       'stat': [],
                       'started': [],
                       'command': [],
                      }

        command = 'ps -o ruser=userForLongName -o pid,%cpu,%mem,stat,start,command -p ' + ','.join(self.pid_list)

        if self.job:
            bsub_command = self.get_bsub_command()

            if bsub_command:
                command = str(bsub_command) + " '" + str(command) + "'"
            else:
                # get_bsub_command already printed why (host not ok / no queue).
                # Fail loudly instead of silently returning an empty table so
                # the user sees why Trace produced nothing.
                self.trace_error = 'Cannot build bsub command to reach the execution host (host not ok or no queue). Trace aborted.'
                common.bprint(self.trace_error, level='Error')

                return process_dic

        (return_code, stdout, stderr) = common.run_command(command)

        # ps -p returns non-zero (and "PID ... not found" on stderr) when none
        # of the PIDs exist on the host the interactive bsub landed on — e.g. a
        # multi-host job whose PIDs live on a different execution host than the
        # first one we submitted to. Surface that instead of an empty table.
        if return_code != 0 and stderr:
            stderr_text = str(stderr, 'utf-8').strip()

            if stderr_text:
                self.trace_error = f'ps on execution host failed: {stderr_text}'
                common.bprint(self.trace_error, level='Warning')
        elif not str(stdout, 'utf-8').strip():
            self.trace_error = 'No process found for the given PIDs on the execution host (PIDs may be on a different host for multi-host jobs, or bsub to the host was rejected).'
            common.bprint(self.trace_error, level='Warning')

        for line in str(stdout, 'utf-8').split('\n'):
            line = line.strip()

            my_match = re.match(r'^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([a-zA-Z]{3}\s+\d{1,2}|\d{2}:\d{2}:\d{2})\s(.+)$', line)

            if my_match:
                user = my_match.group(1)
                pid = my_match.group(2)
                cpu = my_match.group(3)
                mem = my_match.group(4)
                stat = my_match.group(5)
                started = my_match.group(6)
                command = my_match.group(7)

                process_dic['user'].append(user)
                process_dic['pid'].append(pid)
                process_dic['cpu'].append(cpu)
                process_dic['mem'].append(mem)
                process_dic['stat'].append(stat)
                process_dic['started'].append(started)
                process_dic['command'].append(command)

        return process_dic

    def get_bsub_command(self):
        bsub_command = 'bsub -Is '
        queue = self.job_dic[self.job]['queue']
        started_on = self.job_dic[self.job]['started_on']

        if queue:
            bsub_command = str(bsub_command) + ' -q ' + str(queue)

        if started_on:
            started_on_list = started_on.split()
            first_started_on_host = started_on_list[0]
            bhosts_dic = common_lsf.get_bhosts_info('bhosts ' + str(first_started_on_host))

            if ('HOST_NAME' in bhosts_dic) and (first_started_on_host in bhosts_dic['HOST_NAME']):
                host_status = bhosts_dic['STATUS'][0]

                if host_status != 'ok':
                    common.bprint(f'Host "{first_started_on_host}" is {host_status} status, cannot submit job on it.', level='Warning')
                    return ''

            bsub_command = str(bsub_command) + ' -m ' + str(first_started_on_host)

        return bsub_command

    def init_ui(self):
        # Gen menubar
        self.gen_menubar()

        # Add main_tab
        self.main_tab = QTabWidget(self)
        self.setCentralWidget(self.main_tab)

        self.main_frame = QFrame(self.main_tab)

        # Grid
        main_grid = QGridLayout()
        main_grid.addWidget(self.main_frame, 0, 0)
        self.main_tab.setLayout(main_grid)

        # Generate main_table
        self.gen_main_frame()

        # Show main window
        if self.job:
            self.setWindowTitle('Process Tracer (job:' + str(self.job) + ')')
        elif self.pid:
            self.setWindowTitle('Process Tracer (pid:' + str(self.pid) + ')')

        common_pyqt5.auto_resize(self, 1200, 300)
        common_pyqt5.center_window(self)

    def gen_menubar(self):
        menubar = self.menuBar()

        # File
        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(qApp.quit)

        file_menu = menubar.addMenu('File')
        file_menu.addAction(exit_action)

        # Setup
        fresh_action = QAction('Refresh', self)
        fresh_action.triggered.connect(self.gen_main_table)
        self.periodic_fresh_timer = QTimer(self)
        periodic_fresh_action = QAction('Periodic Fresh (1 min)', self, checkable=True)
        periodic_fresh_action.triggered.connect(self.periodic_fresh)

        setup_menu = menubar.addMenu('Setup')
        setup_menu.addAction(fresh_action)
        setup_menu.addAction(periodic_fresh_action)

        # Help
        about_action = QAction('About process_tracer', self)
        about_action.triggered.connect(self.show_about)

        help_menu = menubar.addMenu('Help')
        help_menu.addAction(about_action)

    def periodic_fresh(self, state):
        """
        Fresh the GUI every 60 seconds.
        """
        if state:
            self.periodic_fresh_timer.timeout.connect(self.gen_main_table, Qt.UniqueConnection)
            self.periodic_fresh_timer.start(60000)
        else:
            self.periodic_fresh_timer.stop()

    def show_about(self):
        """
        Show process_tracer about information.
        """
        about_message = 'process_tracer is used to get process tree and trace pid status.'
        QMessageBox.about(self, 'About process_tracer', about_message)

    def gen_main_frame(self):
        self.main_table = QTableWidget(self.main_frame)

        # Grid
        main_frame_grid = QGridLayout()
        main_frame_grid.addWidget(self.main_table, 0, 0)
        self.main_frame.setLayout(main_frame_grid)

        self.gen_main_table()

    def gen_main_table(self):
        self.main_table.setShowGrid(True)
        self.main_table.setColumnCount(0)
        self.main_table.setColumnCount(7)
        self.main_table.setHorizontalHeaderLabels(['USER', 'PID', '%CPU', '%MEM', 'STAT', 'STARTED', 'COMMAND'])

        # Set column width
        self.main_table.setColumnWidth(1, 70)
        self.main_table.setColumnWidth(2, 60)
        self.main_table.setColumnWidth(3, 60)
        self.main_table.setColumnWidth(4, 60)
        self.main_table.setColumnWidth(5, 80)
        self.main_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        common_pyqt5.auto_size_table_columns(self.main_table)

        # Set click behavior
        self.main_table.itemClicked.connect(self.main_tab_check_click)

        # Set item
        self.process_dic = self.get_process_info()
        self.main_table.setRowCount(len(self.process_dic['pid']))

        # Pop a warning dialog instead of a silently black window with empty pid info.
        if not self.process_dic['pid'] and self.trace_error:
            error_msg = self.trace_error
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, 'Process Tracer', error_msg))

        title_list = ['user', 'pid', 'cpu', 'mem', 'stat', 'started', 'command']

        for (row, pid) in enumerate(self.process_dic['pid']):
            for (column, title) in enumerate(title_list):
                item = QTableWidgetItem()
                item.setText(self.process_dic[title][row])
                self.main_table.setItem(row, column, item)

    def main_tab_check_click(self, item=None):
        if item is not None:
            if item.column() == 1:
                current_row = self.main_table.currentRow()
                pid = self.main_table.item(current_row, 1).text()

                if not re.match(r'^\d+$', str(pid)):
                    return

                command = 'xterm -e "strace -tt -p ' + str(pid) + '"'

                if self.job:
                    bsub_command = self.get_bsub_command()

                    if bsub_command:
                        command = str(bsub_command) + ' ' + shlex.quote(command)
                    else:
                        return

                subprocess.run(command, shell=True)


################
# Main Process #
################
def main():
    (job, pid) = read_args()
    app = QApplication(sys.argv)
    my_process_tracer = ProcessTracer(job, pid)
    my_process_tracer.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
