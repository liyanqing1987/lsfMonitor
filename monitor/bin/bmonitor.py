# -*- coding: utf-8 -*-

import os
import re
import sys
import stat
import copy
import time
import yaml
import getpass
import datetime
import argparse

from PyQt5.QtWidgets import QApplication, QWidget, QMainWindow, QAction, qApp, QTextEdit, QTabWidget, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QMessageBox, QLineEdit, QComboBox, QHeaderView
from PyQt5.QtGui import QBrush, QFont
from PyQt5.QtCore import Qt, QTimer, QThread

from matplotlib.backends.backend_qt5 import NavigationToolbar2QT
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from common import lsf_common
from common import license_common
from common import pyqt5_common
from common import sqlite3_common
from conf import config

os.environ['PYTHONUNBUFFERED'] = '1'

# Solve some unexpected warning message.
if 'XDG_RUNTIME_DIR' not in os.environ:
    user = getpass.getuser()
    os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-' + str(user)

    if not os.path.exists(os.environ['XDG_RUNTIME_DIR']):
        os.makedirs(os.environ['XDG_RUNTIME_DIR'])

    os.chmod(os.environ['XDG_RUNTIME_DIR'], stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)


def check_tool():
    """
    Make sure LSF or Openlava environment exists.
    """
    tool = lsf_common.get_tool_name()

    if tool == '':
        print('*Error*: Not find any LSF or Openlava environment!')
        sys.exit(1)


def read_args():
    """
    Read arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-j", "--jobid",
                        type=int,
                        help='Specify the jobid which show it\'s information on "JOB" tab.')
    parser.add_argument("-u", "--user",
                        default='',
                        help='Specify the user show how\'s job information on "JOBS" tab.')
    parser.add_argument("-f", "--feature",
                        default='',
                        help='Specify license feature which you want to see on "LICENSE" tab.')
    parser.add_argument("-t", "--tab",
                        default='JOBS',
                        choices=['JOB', 'JOBS', 'HOSTS', 'QUEUES', 'LOAD', 'LICENSE'],
                        help='Specify current tab, default is "JOB" tab.')

    args = parser.parse_args()

    # Make sure specified job exists.
    if args.jobid:
        args.tab = 'JOB'

        command = 'bjobs -w ' + str(args.jobid)
        job_dic = lsf_common.get_bjobs_info(command)

        if not job_dic:
            args.jobid = None

    # Set default tab for args.user.
    if args.user:
        if not args.tab:
            args.tab = 'JOBS'

    # Set default tab for args.feature.
    if args.feature:
        if not args.tab:
            args.tab = 'LICENSE'

    # Set default tab.
    if not args.tab:
        args.tab = 'JOB'

    return(args.jobid, args.user, args.feature, args.tab)


class FigureCanvas(FigureCanvasQTAgg):
    """
    Generate a new figure canvas.
    """
    def __init__(self):
        self.figure = Figure()
        super().__init__(self.figure)


class MainWindow(QMainWindow):
    """
    Main window of lsfMonitor.
    """
    def __init__(self, specified_job, specified_user, specified_feature, specified_tab):
        super().__init__()
        self.specified_job = specified_job
        self.specified_user = specified_user
        self.specified_feature = specified_feature
        self.init_ui()
        self.switch_tab(specified_tab)

    def init_ui(self):
        """
        Main process, draw the main graphic frame.
        """
        # Add menubar.
        self.gen_menubar()

        # Define main Tab widget
        self.main_tab = QTabWidget(self)
        self.setCentralWidget(self.main_tab)

        # Define four sub-tabs (JOB/JOBS/HOSTS/QUEUES)
        self.job_tab = QWidget()
        self.jobs_tab = QWidget()
        self.hosts_tab = QWidget()
        self.queues_tab = QWidget()
        self.load_tab = QWidget()
        self.license_tab = QWidget()

        # Add the sub-tabs into main Tab widget
        self.main_tab.addTab(self.job_tab, 'JOB')
        self.main_tab.addTab(self.jobs_tab, 'JOBS')
        self.main_tab.addTab(self.hosts_tab, 'HOSTS')
        self.main_tab.addTab(self.queues_tab, 'QUEUES')
        self.main_tab.addTab(self.load_tab, 'LOAD')
        self.main_tab.addTab(self.license_tab, 'LICENSE')

        # Get LSF queue/host information.
        print('* Loading LSF information, please wait a moment ...')
        self.queue_list = lsf_common.get_queue_list()
        self.host_list = lsf_common.get_host_list()

        # Get license information.
        print('* Loading license information, please wait a moment ...')
        self.license_dic = license_common.get_license_info()

        if not self.license_dic:
            print('*Warning*: Not find any valid license information.')

        # Get liense product-feature relationshi directory.
        print('* Loading license product-feature relationship, please wait a moment ...')
        self.product_feature_relationship_dic = {}

        if os.path.exists(config.product_feature_relationship_file):
            self.product_feature_relationship_dic = yaml.load(open(config.product_feature_relationship_file), Loader=yaml.FullLoader)
        else:
            print('*Warning*: product feature relationshi file "' + str(config.product_feature_relationship_file) + '" is missing.')

        # Generate the sub-tabs
        self.gen_job_tab()
        self.gen_jobs_tab()
        self.gen_hosts_tab()
        self.gen_queues_tab()
        self.gen_load_tab()
        self.gen_license_tab()

        # Show main window
        self.setWindowTitle('lsfMonitor')
        self.resize(1111, 620)
        pyqt5_common.center_window(self)

    def switch_tab(self, specified_tab):
        """
        Switch to the specified Tab.
        """
        tab_dic = {
                   'JOB': self.job_tab,
                   'JOBS': self.jobs_tab,
                   'HOSTS': self.hosts_tab,
                   'QUEUES': self.queues_tab,
                   'LOAD': self.load_tab,
                   'LICENSE': self.license_tab,
                  }

        self.main_tab.setCurrentWidget(tab_dic[specified_tab])

    def gen_menubar(self):
        """
        Generate menubar.
        """
        menubar = self.menuBar()

        # File
        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(qApp.quit)

        file_menu = menubar.addMenu('File')
        file_menu.addAction(exit_action)

        # Setup
        fresh_action = QAction('Fresh', self)
        fresh_action.triggered.connect(self.fresh)
        self.periodic_fresh_timer = QTimer(self)
        periodic_fresh_action = QAction('Periodic Fresh (1 min)', self, checkable=True)
        periodic_fresh_action.triggered.connect(self.periodic_fresh)

        setup_menu = menubar.addMenu('Setup')
        setup_menu.addAction(fresh_action)
        setup_menu.addAction(periodic_fresh_action)

        # Function
        check_pend_reason_action = QAction('Check Pend reason', self)
        check_pend_reason_action.triggered.connect(self.check_pend_reason)
        check_slow_reason_action = QAction('Check Slow reason', self)
        check_slow_reason_action.triggered.connect(self.check_slow_reason)
        check_fail_reason_action = QAction('Check Fail reason', self)
        check_fail_reason_action.triggered.connect(self.check_fail_reason)

        function_menu = menubar.addMenu('Function')
        function_menu.addAction(check_pend_reason_action)
        function_menu.addAction(check_slow_reason_action)
        function_menu.addAction(check_fail_reason_action)

        # Help
        version_action = QAction('Version', self)
        version_action.triggered.connect(self.show_version)

        about_action = QAction('About lsfMonitor', self)
        about_action.triggered.connect(self.show_about)

        help_menu = menubar.addMenu('Help')
        help_menu.addAction(version_action)
        help_menu.addAction(about_action)

    def fresh(self):
        """
        Re-build the GUI with latest LSF status.
        """
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        print('* [' + str(current_time) + '] Re-Loading LSF and License status, please wait a moment ...')

        self.gen_jobs_tab_table()
        self.gen_hosts_tab_table()
        self.gen_queues_tab_table()
        self.gen_license_tab_feature_table(update=True)
        self.gen_license_tab_expires_table()

    def periodic_fresh(self, state):
        """
        Fresh the GUI every 60 seconds.
        """
        if state:
            self.periodic_fresh_timer.timeout.connect(self.fresh)
            self.periodic_fresh_timer.start(60000)
        else:
            self.periodic_fresh_timer.stop()

    def check_pend_reason(self):
        """
        Call a separate script to check job pend reason.
        """
        self.my_check_issue_reason = CheckIssueReason(issue='PEND')
        self.my_check_issue_reason.start()

    def check_slow_reason(self):
        """
        Call a separate script to check job slow reason.
        """
        self.my_check_issue_reason = CheckIssueReason(issue='SLOW')
        self.my_check_issue_reason.start()

    def check_fail_reason(self):
        """
        Call a separate script to check job fail reason.
        """
        self.my_check_issue_reason = CheckIssueReason(issue='FAIL')
        self.my_check_issue_reason.start()

    def show_version(self):
        """
        Show lsfMonitor version information.
        """
        version = ''
        version_mark = False
        readme_file = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/README'

        with open(readme_file, 'r') as RF:
            for line in RF.readlines():
                if version_mark:
                    version = line.strip()
                    break
                else:
                    if re.match('^\s*VERSION:\s*$', line):
                        version_mark = True

        QMessageBox.about(self, 'lsfMonitor', 'Version: ' + str(version) + '        ')

    def show_about(self):
        """
        Show lsfMonitor about information.
        """
        readme_file = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/README'
        about_message = ''

        with open(readme_file, 'r') as RF:
            for line in RF.readlines():
                about_message = str(about_message) + str(line)

        QMessageBox.about(self, 'lsfMonitor', about_message)

# Common sub-functions (begin) #
    def gui_warning(self, warning_message):
        """
        Show the specified warning message on both of command line and GUI window.
        """
        common.print_warning(warning_message)
        QMessageBox.warning(self, 'lsfMonitor Warning', warning_message)
# Common sub-functions (end) #

# For job TAB (begin) #
    def gen_job_tab(self):
        """
        Generate the job tab on lsfMonitor GUI, show job informations.
        """
        # Init var
        self.current_job = ''
        self.job_info_dic = {}

        # self.job_tab
        self.job_tab_frame0 = QFrame(self.job_tab)
        self.job_tab_frame1 = QFrame(self.job_tab)
        self.job_tab_frame2 = QFrame(self.job_tab)
        self.job_tab_frame3 = QFrame(self.job_tab)

        self.job_tab_frame0.setFrameShadow(QFrame.Raised)
        self.job_tab_frame0.setFrameShape(QFrame.Box)
        self.job_tab_frame1.setFrameShadow(QFrame.Raised)
        self.job_tab_frame1.setFrameShape(QFrame.Box)
        self.job_tab_frame2.setFrameShadow(QFrame.Raised)
        self.job_tab_frame2.setFrameShape(QFrame.Box)
        self.job_tab_frame3.setFrameShadow(QFrame.Raised)
        self.job_tab_frame3.setFrameShape(QFrame.Box)

        # self.job_tab - Grid
        job_tab_grid = QGridLayout()

        job_tab_grid.addWidget(self.job_tab_frame0, 0, 0)
        job_tab_grid.addWidget(self.job_tab_frame1, 1, 0)
        job_tab_grid.addWidget(self.job_tab_frame2, 2, 0, 1, 2)
        job_tab_grid.addWidget(self.job_tab_frame3, 0, 1, 2, 1)

        job_tab_grid.setRowStretch(0, 1)
        job_tab_grid.setRowStretch(1, 14)
        job_tab_grid.setRowStretch(2, 6)
        job_tab_grid.setColumnStretch(0, 1)
        job_tab_grid.setColumnStretch(1, 10)

        job_tab_grid.setRowMinimumHeight(0, 60)
        job_tab_grid.setRowMinimumHeight(1, 320)
        job_tab_grid.setRowMinimumHeight(2, 120)
        job_tab_grid.setColumnMinimumWidth(0, 250)
        job_tab_grid.setColumnMinimumWidth(1, 500)

        self.job_tab.setLayout(job_tab_grid)

        # Generate sub-frames
        self.gen_job_tab_frame0()
        self.gen_job_tab_frame1()
        self.gen_job_tab_frame2()
        self.gen_job_tab_frame3()

        if self.specified_job:
            self.job_tab_job_line.setText(str(self.specified_job))
            self.check_job()

    def gen_job_tab_frame0(self):
        # self.job_tab_frame0
        job_tab_job_label = QLabel(self.job_tab_frame0)
        job_tab_job_label.setText('Job')

        self.job_tab_job_line = QLineEdit()

        job_tab_check_button = QPushButton('Check', self.job_tab_frame0)
        job_tab_check_button.clicked.connect(self.check_job)

        # self.job_tab_frame0 - Grid
        job_tab_frame0_grid = QGridLayout()

        job_tab_frame0_grid.addWidget(job_tab_job_label, 0, 0)
        job_tab_frame0_grid.addWidget(self.job_tab_job_line, 0, 1)
        job_tab_frame0_grid.addWidget(job_tab_check_button, 0, 2)

        self.job_tab_frame0.setLayout(job_tab_frame0_grid)

    def gen_job_tab_frame1(self):
        # self.job_tab_frame1
        job_tab_user_label = QLabel('User', self.job_tab_frame1)
        self.job_tab_user_line = QLineEdit()

        job_tab_status_label = QLabel('Status', self.job_tab_frame1)
        self.job_tab_status_line = QLineEdit()

        job_tab_queue_label = QLabel('Queue', self.job_tab_frame1)
        self.job_tab_queue_line = QLineEdit()

        job_tab_started_on_label = QLabel('Host', self.job_tab_frame1)
        self.job_tab_started_on_line = QLineEdit()

        job_tab_project_label = QLabel('Project', self.job_tab_frame1)
        self.job_tab_project_line = QLineEdit()

        job_tab_processors_requested_label = QLabel('Processors', self.job_tab_frame1)
        self.job_tab_processors_requested_line = QLineEdit()

        job_tab_rusage_mem_label = QLabel('Rusage', self.job_tab_frame1)
        self.job_tab_rusage_mem_line = QLineEdit()

        job_tab_mem_label = QLabel('Mem', self.job_tab_frame1)
        self.job_tab_mem_line = QLineEdit()

        job_tab_avg_mem_label = QLabel('avg_mem', self.job_tab_frame1)
        self.job_tab_avg_mem_line = QLineEdit()

        job_tab_max_mem_label = QLabel('max_mem', self.job_tab_frame1)
        self.job_tab_max_mem_line = QLineEdit()

        process_tracer_button = QPushButton('Process  Tracer', self.job_tab_frame1)
        process_tracer_button.clicked.connect(self.process_tracer)

        # self.job_tab_frame1 - Grid
        job_tab_frame1_grid = QGridLayout()

        job_tab_frame1_grid.addWidget(job_tab_user_label, 0, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_user_line, 0, 1)
        job_tab_frame1_grid.addWidget(job_tab_status_label, 1, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_status_line, 1, 1)
        job_tab_frame1_grid.addWidget(job_tab_queue_label, 2, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_queue_line, 2, 1)
        job_tab_frame1_grid.addWidget(job_tab_started_on_label, 3, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_started_on_line, 3, 1)
        job_tab_frame1_grid.addWidget(job_tab_project_label, 4, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_project_line, 4, 1)
        job_tab_frame1_grid.addWidget(job_tab_processors_requested_label, 5, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_processors_requested_line, 5, 1)
        job_tab_frame1_grid.addWidget(job_tab_rusage_mem_label, 6, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_rusage_mem_line, 6, 1)
        job_tab_frame1_grid.addWidget(job_tab_mem_label, 7, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_mem_line, 7, 1)
        job_tab_frame1_grid.addWidget(job_tab_avg_mem_label, 8, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_avg_mem_line, 8, 1)
        job_tab_frame1_grid.addWidget(job_tab_max_mem_label, 9, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_max_mem_line, 9, 1)
        job_tab_frame1_grid.addWidget(process_tracer_button, 10, 0, 1, 2)

        self.job_tab_frame1.setLayout(job_tab_frame1_grid)

    def process_tracer(self):
        # Call script process_tracer.py to get job process information.
        self.current_job = self.job_tab_job_line.text().strip()

        if self.current_job:
            self.my_process_tracer = ProcessTracer(self.current_job)
            self.my_process_tracer.start()

    def gen_job_tab_frame2(self):
        # self.job_tab_frame2
        self.job_tab_job_info_text = QTextEdit(self.job_tab_frame2)

        # self.job_tab_frame2 - Grid
        job_tab_frame2_grid = QGridLayout()
        job_tab_frame2_grid.addWidget(self.job_tab_job_info_text, 0, 0)
        self.job_tab_frame2.setLayout(job_tab_frame2_grid)

    def gen_job_tab_frame3(self):
        # self.job_tab_frame3
        self.job_mem_figure_canvas = FigureCanvas()
        self.job_mem_navigation_toolbar = NavigationToolbar2QT(self.job_mem_figure_canvas, self)

        # self.job_tab_frame3 - Grid
        job_tab_frame3_grid = QGridLayout()
        job_tab_frame3_grid.addWidget(self.job_mem_navigation_toolbar, 0, 0)
        job_tab_frame3_grid.addWidget(self.job_mem_figure_canvas, 1, 0)
        self.job_tab_frame3.setLayout(job_tab_frame3_grid)

    def check_job(self):
        """
        Get job information with "bjobs -UF <job_id>", save the infomation into dict self.job_info_dic.
        Update self.job_tab_frame1 and self.job_tab_frame3.
        """
        self.current_job = self.job_tab_job_line.text().strip()

        print('* Checking job "' + str(self.current_job) + '".')

        # Initicalization
        self.update_job_tab_frame1(init=True)
        self.update_job_tab_frame2(init=True)
        self.update_job_tab_frame3(init=True)

        # Job name must be a string of numbers.
        current_job = self.current_job

        if re.match('^(\d+)(\[\d+\])?$', self.current_job):
            my_match = re.match('^(\d+)(\[\d+\])?$', self.current_job)
            current_job = my_match.group(1)
        else:
            warning_message = '*Warning*: No valid job is specified!'
            self.gui_warning(warning_message)
            return

        # Get job info
        my_show_message = ShowMessage('Info', '* Getting LSF job information for "' + str(current_job) + '", please wait a moment ...')
        my_show_message.start()
        self.job_info_dic = lsf_common.get_bjobs_uf_info(command='bjobs -UF ' + str(current_job))
        my_show_message.terminate()

        if self.job_info_dic:
            # Update the related frames with the job info.
            self.update_job_tab_frame1()
            self.update_job_tab_frame2()
            self.update_job_tab_frame3()
        else:
            print('*Warning*: Not find job information for job "' + str(current_job) + '".')

    def update_job_tab_frame1(self, init=False):
        """
        Update self.job_tab_frame1 with job infos.
        """
        # For "User" item.
        if init:
            self.job_tab_user_line.setText('')
        else:
            self.job_tab_user_line.setText(self.job_info_dic[self.current_job]['user'])
            self.job_tab_user_line.setCursorPosition(0)

        # For "Status" item.
        if init:
            self.job_tab_status_line.setText('')
        else:
            self.job_tab_status_line.setText(self.job_info_dic[self.current_job]['status'])
            self.job_tab_status_line.setCursorPosition(0)

        # For "Queue" item.
        if init:
            self.job_tab_queue_line.setText('')
        else:
            self.job_tab_queue_line.setText(self.job_info_dic[self.current_job]['queue'])
            self.job_tab_queue_line.setCursorPosition(0)

        # For "Host" item.
        if init:
            self.job_tab_started_on_line.setText('')
        else:
            self.job_tab_started_on_line.setText(self.job_info_dic[self.current_job]['started_on'])
            self.job_tab_started_on_line.setCursorPosition(0)

        # For "Processors" item.
        if init:
            self.job_tab_processors_requested_line.setText('')
        else:
            self.job_tab_processors_requested_line.setText(self.job_info_dic[self.current_job]['processors_requested'])
            self.job_tab_processors_requested_line.setCursorPosition(0)

        # For "Project" item.
        if init:
            self.job_tab_project_line.setText('')
        else:
            self.job_tab_project_line.setText(self.job_info_dic[self.current_job]['project'])
            self.job_tab_project_line.setCursorPosition(0)

        # For "Rusage" item.
        if init:
            self.job_tab_rusage_mem_line.setText('')
        else:
            if self.job_info_dic[self.current_job]['rusage_mem'] != '':
                rusage_mem_value = round(int(self.job_info_dic[self.current_job]['rusage_mem'])/1024, 1)
                self.job_tab_rusage_mem_line.setText(str(rusage_mem_value) + ' G')
                self.job_tab_rusage_mem_line.setCursorPosition(0)

        # For "Mem" item.
        if init:
            self.job_tab_mem_line.setText('')
        else:
            if self.job_info_dic[self.current_job]['mem'] != '':
                mem_value = round(float(self.job_info_dic[self.current_job]['mem'])/1024, 1)
                self.job_tab_mem_line.setText(str(mem_value) + ' G')
                self.job_tab_mem_line.setCursorPosition(0)

        # For "avg_mem" item.
        if init:
            self.job_tab_avg_mem_line.setText('')
        else:
            if self.job_info_dic[self.current_job]['avg_mem'] != '':
                avg_mem_value = round(float(self.job_info_dic[self.current_job]['avg_mem'])/1024, 1)
                self.job_tab_avg_mem_line.setText(str(avg_mem_value) + ' G')
                self.job_tab_avg_mem_line.setCursorPosition(0)

        # For "max_mem" item.
        if init:
            self.job_tab_max_mem_line.setText('')
        else:
            if self.job_info_dic[self.current_job]['max_mem'] != '':
                max_mem_value = round(float(self.job_info_dic[self.current_job]['max_mem'])/1024, 1)
                self.job_tab_max_mem_line.setText(str(max_mem_value) + ' G')
                self.job_tab_max_mem_line.setCursorPosition(0)

    def update_job_tab_frame2(self, init=False):
        """
        Show job detailed description info on self.job_tab_frame2/self.job_tab_job_info_text.
        """
        self.job_tab_job_info_text.clear()

        if not init:
            self.job_tab_job_info_text.insertPlainText(self.job_info_dic[self.current_job]['job_info'])
            pyqt5_common.text_edit_visible_position(self.job_tab_job_info_text, 'Start')

    def get_job_mem_list(self):
        """
        Get job sample-time mem list for self.current_job.
        """
        runtime_list = []
        real_mem_list = []

        job_range_dic = common.get_job_range_dic([self.current_job, ])
        job_range_list = list(job_range_dic.keys())
        job_range = job_range_list[0]
        job_db_file = str(config.db_path) + '/monitor/job/' + str(job_range) + '.db'

        if not os.path.exists(job_db_file):
            common.print_warning('*Warning*: Job memory usage information is missing for "' + str(self.current_job) + '".')
        else:
            (job_db_file_connect_result, job_db_conn) = sqlite3_common.connect_db_file(job_db_file)

            if job_db_file_connect_result == 'failed':
                common.print_warning('*Warning*: Failed on connecting job database file "' + str(job_db_file) + '".')
            else:
                print('Getting history of job memory usage for job "' + str(self.current_job) + '".')

                table_name = 'job_' + str(self.current_job)
                data_dic = sqlite3_common.get_sql_table_data(job_db_file, job_db_conn, table_name, ['sample_time', 'mem'])

                if not data_dic:
                    common.print_warning('*Warning*: job memory usage information is empty for "' + str(self.current_job) + '".')
                else:
                    sample_time_list = data_dic['sample_time']
                    mem_list = data_dic['mem']
                    first_sample_time = datetime.datetime.strptime(str(sample_time_list[0]), '%Y%m%d_%H%M%S').timestamp()

                    for i in range(len(sample_time_list)):
                        sample_time = sample_time_list[i]
                        current_time = datetime.datetime.strptime(str(sample_time), '%Y%m%d_%H%M%S').timestamp()
                        runtime = int((current_time-first_sample_time)/60)
                        runtime_list.append(runtime)
                        mem = mem_list[i]

                        if mem == '':
                            mem = '0'

                        real_mem = round(float(mem)/1024, 1)
                        real_mem_list.append(real_mem)

                job_db_conn.close()

        return(runtime_list, real_mem_list)

    def update_job_tab_frame3(self, init=False):
        """
        Draw memory curve for current job on self.job_tab_frame3.
        """
        fig = self.job_mem_figure_canvas.figure
        fig.clear()
        self.job_mem_figure_canvas.draw()

        if not init:
            if self.job_info_dic[self.current_job]['status'] != 'PEND':
                (runtime_list, mem_list) = self.get_job_mem_list()

                if runtime_list and mem_list:
                    self.draw_job_mem_curve(fig, runtime_list, mem_list)

    def draw_job_mem_curve(self, fig, runtime_list, mem_list):
        """
        Draw memory curve for specified job.
        """
        fig.subplots_adjust(bottom=0.2)
        axes = fig.add_subplot(111)
        axes.set_title('job "' + str(self.current_job) + '" memory curve')
        axes.set_xlabel('Runtime (Minutes)')
        axes.set_ylabel('Memory Usage (G)')
        axes.plot(runtime_list, mem_list, 'ro-', color='red')
        axes.grid()
        self.job_mem_figure_canvas.draw()
# For job TAB (end) #

# For jobs TAB (start) #
    def gen_jobs_tab(self):
        """
        Generate the jobs tab on lsfMonitor GUI, show jobs informations.
        """
        # self.jobs_tab
        self.jobs_tab_frame0 = QFrame(self.jobs_tab)
        self.jobs_tab_frame0.setFrameShadow(QFrame.Raised)
        self.jobs_tab_frame0.setFrameShape(QFrame.Box)

        self.jobs_tab_table = QTableWidget(self.jobs_tab)
        self.jobs_tab_table.itemClicked.connect(self.jobs_tab_check_click)

        # self.jobs_tab - Grid
        jobs_tab_grid = QGridLayout()

        jobs_tab_grid.addWidget(self.jobs_tab_frame0, 0, 0)
        jobs_tab_grid.addWidget(self.jobs_tab_table, 1, 0)

        jobs_tab_grid.setRowStretch(0, 1)
        jobs_tab_grid.setRowStretch(1, 20)

        self.jobs_tab.setLayout(jobs_tab_grid)

        # Generate sub-frame
        self.gen_jobs_tab_frame0()

        if self.specified_user:
            self.jobs_tab_user_line.setText(str(self.specified_user))

        self.gen_jobs_tab_table()

    def gen_jobs_tab_frame0(self):
        # self.jobs_tab_frame0
        jobs_tab_status_label = QLabel('Status', self.jobs_tab_frame0)
        jobs_tab_status_label.setStyleSheet("font-weight: bold;")
        self.jobs_tab_status_combo = QComboBox(self.jobs_tab_frame0)
        self.set_jobs_tab_status_combo(['RUN', 'PEND', 'DONE', 'EXIT', 'ALL'])

        jobs_tab_queue_label = QLabel('       Queue', self.jobs_tab_frame0)
        jobs_tab_queue_label.setStyleSheet("font-weight: bold;")
        self.jobs_tab_queue_combo = QComboBox(self.jobs_tab_frame0)
        self.set_jobs_tab_queue_combo()

        jobs_tab_started_on_label = QLabel('       Host', self.jobs_tab_frame0)
        jobs_tab_started_on_label.setStyleSheet("font-weight: bold;")
        self.jobs_tab_started_on_combo = QComboBox(self.jobs_tab_frame0)
        self.set_jobs_tab_started_on_combo()

        jobs_tab_user_label = QLabel('       User', self.jobs_tab_frame0)
        jobs_tab_user_label.setStyleSheet("font-weight: bold;")
        self.jobs_tab_user_line = QLineEdit()

        self.jobs_tab_status_combo.currentIndexChanged.connect(self.gen_jobs_tab_table)
        self.jobs_tab_queue_combo.currentIndexChanged.connect(self.gen_jobs_tab_table)
        self.jobs_tab_started_on_combo.currentIndexChanged.connect(self.gen_jobs_tab_table)

        jobs_tab_check_button = QPushButton('Check', self.jobs_tab_frame0)
        jobs_tab_check_button.clicked.connect(self.gen_jobs_tab_table)

        # self.jobs_tab_frame0 - Grid
        jobs_tab_frame0_grid = QGridLayout()

        jobs_tab_frame0_grid.addWidget(jobs_tab_status_label, 0, 0)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_status_combo, 0, 1)
        jobs_tab_frame0_grid.addWidget(jobs_tab_queue_label, 0, 2)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_queue_combo, 0, 3)
        jobs_tab_frame0_grid.addWidget(jobs_tab_started_on_label, 0, 4)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_started_on_combo, 0, 5)
        jobs_tab_frame0_grid.addWidget(jobs_tab_user_label, 0, 6)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_user_line, 0, 7)
        jobs_tab_frame0_grid.addWidget(jobs_tab_check_button, 0, 8)

        jobs_tab_frame0_grid.setColumnStretch(1, 1)
        jobs_tab_frame0_grid.setColumnStretch(3, 1)
        jobs_tab_frame0_grid.setColumnStretch(5, 1)
        jobs_tab_frame0_grid.setColumnStretch(7, 1)

        self.jobs_tab_frame0.setLayout(jobs_tab_frame0_grid)

    def gen_jobs_tab_table(self):
        # self.jobs_tab_table
        self.jobs_tab_table.setShowGrid(True)
        self.jobs_tab_table.setSortingEnabled(True)
        self.jobs_tab_table.setColumnCount(0)
        self.jobs_tab_table.setColumnCount(11)
        self.jobs_tab_table.setHorizontalHeaderLabels(['Job', 'User', 'Status', 'Queue', 'Host', 'Started', 'Project', 'Slot', 'Rusage (G)', 'Mem (G)', 'Command'])

        self.jobs_tab_table.setColumnWidth(0, 70)
        self.jobs_tab_table.setColumnWidth(2, 70)
        self.jobs_tab_table.setColumnWidth(7, 80)
        self.jobs_tab_table.setColumnWidth(8, 80)
        self.jobs_tab_table.setColumnWidth(9, 80)
        self.jobs_tab_table.horizontalHeader().setSectionResizeMode(10, QHeaderView.Stretch)

        # Get specified user related jobs.
        command = 'bjobs -UF '
        specified_user = self.jobs_tab_user_line.text().strip()

        if re.match('^\s*$', specified_user):
            command = str(command) + ' -u all'
        else:
            command = str(command) + ' -u ' + str(specified_user)

        # Get specified queue related jobs.
        specified_queue = self.jobs_tab_queue_combo.currentText().strip()

        if specified_queue != 'ALL':
            command = str(command) + ' -q ' + str(specified_queue)

        # Get specified status (RUN/PEND/ALL) related jobs.
        specified_status = self.jobs_tab_status_combo.currentText().strip()

        if specified_status == 'RUN':
            command = str(command) + ' -r'
        elif specified_status == 'PEND':
            command = str(command) + ' -p'
        else:
            command = str(command) + ' -a'

        # Get specified host related jobs.
        specified_host = self.jobs_tab_started_on_combo.currentText().strip()

        if specified_host != 'ALL':
            command = str(command) + ' -m ' + str(specified_host)

        # Run command to get expected jobs information.
        my_show_message = ShowMessage('Info', '* Loading LSF jobs information, please wait a moment ...')
        my_show_message.start()
        orig_job_dic = lsf_common.get_bjobs_uf_info(command)
        my_show_message.terminate()

        # Filter job_dic.
        job_dic = {}

        if (specified_status == 'DONE') or (specified_status == 'EXIT'):
            for job in orig_job_dic.keys():
                if orig_job_dic[job]['status'] == specified_status:
                    job_dic.setdefault(job, orig_job_dic[job])
        else:
            job_dic = orig_job_dic

        # Fill self.jobs_tab_table items.
        self.jobs_tab_table.setRowCount(0)
        self.jobs_tab_table.setRowCount(len(job_dic.keys()))
        jobs = list(job_dic.keys())

        for i in range(len(jobs)):
            # File "Job"
            job = jobs[i]
            j = 0
            item = QTableWidgetItem(job)
            item.setFont(QFont('song', 9, QFont.Bold))
            self.jobs_tab_table.setItem(i, j, item)

            # File "User"
            j = j+1
            item = QTableWidgetItem(job_dic[job]['user'])
            self.jobs_tab_table.setItem(i, j, item)

            # File "Status"
            j = j+1
            item = QTableWidgetItem(job_dic[job]['status'])
            item.setFont(QFont('song', 9, QFont.Bold))

            if job_dic[job]['status'] == 'PEND':
                item.setForeground(QBrush(Qt.red))

            self.jobs_tab_table.setItem(i, j, item)

            # File "Queue"
            j = j+1
            item = QTableWidgetItem(job_dic[job]['queue'])
            self.jobs_tab_table.setItem(i, j, item)

            # File "Host"
            j = j+1
            item = QTableWidgetItem(job_dic[job]['started_on'])
            self.jobs_tab_table.setItem(i, j, item)

            # File "Started"
            j = j+1
            item = QTableWidgetItem(job_dic[job]['started_time'])
            self.jobs_tab_table.setItem(i, j, item)

            # File "Project"
            j = j+1

            if str(job_dic[job]['project']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, job_dic[job]['project'])
                self.jobs_tab_table.setItem(i, j, item)

            # File "Slot"
            j = j+1

            if str(job_dic[job]['processors_requested']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, int(job_dic[job]['processors_requested']))
                self.jobs_tab_table.setItem(i, j, item)

            # File "Rusage"
            j = j+1

            if str(job_dic[job]['rusage_mem']) != '':
                item = QTableWidgetItem()
                rusage_mem_value = round(int(job_dic[job]['rusage_mem'])/1024, 1)
                item.setData(Qt.DisplayRole, rusage_mem_value)
                self.jobs_tab_table.setItem(i, j, item)

            # File "Mem"
            j = j+1

            if str(job_dic[job]['mem']) != '':
                item = QTableWidgetItem()
                mem_value = round(float(job_dic[job]['mem'])/1024, 1)
                item.setData(Qt.DisplayRole, mem_value)
                self.jobs_tab_table.setItem(i, j, item)

                if ((not job_dic[job]['rusage_mem']) and (mem_value > 0)) or (job_dic[job]['rusage_mem'] and (mem_value > rusage_mem_value)):
                    item.setForeground(QBrush(Qt.red))

            # File "Command"
            j = j+1
            item = QTableWidgetItem(job_dic[job]['command'])
            self.jobs_tab_table.setItem(i, j, item)

    def jobs_tab_check_click(self, item=None):
        """
        If click the Job id, jump to the JOB tab and show the job information.
        If click the "PEND" Status, show the job pend reasons on a QMessageBox.information().
        """
        if item is not None:
            currentRow = self.jobs_tab_table.currentRow()
            job = self.jobs_tab_table.item(currentRow, 0).text().strip()

            if item.column() == 0:
                if job != '':
                    self.job_tab_job_line.setText(job)
                    self.check_job()
                    self.main_tab.setCurrentWidget(self.job_tab)
            elif item.column() == 2:
                job_status = self.jobs_tab_table.item(currentRow, 2).text().strip()

                if job_status == 'PEND':
                    print('* Getting job pend reason for "' + str(job) + '", please wait a moment ...')
                    self.my_check_issue_reason = CheckIssueReason(job=job, issue='PEND')
                    self.my_check_issue_reason.start()
                elif job_status == 'RUN':
                    print('* Getting job process information for "' + str(job) + '", please wait a moment ...')
                    self.my_check_issue_reason = CheckIssueReason(job=job, issue='SLOW')
                    self.my_check_issue_reason.start()
                elif (job_status == 'DONE') or (job_status == 'EXIT'):
                    print('* Getting job fail reason for "' + str(job) + '", please wait a moment ...')
                    self.my_check_issue_reason = CheckIssueReason(job=job, issue='FAIL')
                    self.my_check_issue_reason.start()

    def set_jobs_tab_status_combo(self, status_list):
        """
        Set (initialize) self.jobs_tab_status_combo.
        """
        self.jobs_tab_status_combo.clear()

        for status in status_list:
            self.jobs_tab_status_combo.addItem(status)

    def set_jobs_tab_queue_combo(self, queue_list=[]):
        """
        Set (initialize) self.jobs_tab_queue_combo.
        """
        self.jobs_tab_queue_combo.clear()

        if not queue_list:
            queue_list = copy.deepcopy(self.queue_list)
            queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.jobs_tab_queue_combo.addItem(queue)

    def set_jobs_tab_started_on_combo(self, host_list=[]):
        """
        Set (initialize) self.jobs_tab_started_on_combo.
        """
        self.jobs_tab_started_on_combo.clear()

        if not host_list:
            host_list = copy.deepcopy(self.host_list)
            host_list.insert(0, 'ALL')

        for host in host_list:
            self.jobs_tab_started_on_combo.addItem(host)
# For jobs TAB (end) #

# For hosts TAB (start) #
    def gen_hosts_tab(self):
        """
        Generate the hosts tab on lsfMonitor GUI, show hosts informations.
        """
        # self.hosts_tab_table
        self.hosts_tab_frame0 = QFrame(self.hosts_tab)
        self.hosts_tab_frame0.setFrameShadow(QFrame.Raised)
        self.hosts_tab_frame0.setFrameShape(QFrame.Box)

        self.hosts_tab_table = QTableWidget(self.hosts_tab)
        self.hosts_tab_table.itemClicked.connect(self.hosts_tab_check_click)

        # self.hosts_tab_table - Grid
        hosts_tab_grid = QGridLayout()

        hosts_tab_grid.addWidget(self.hosts_tab_frame0, 0, 0)
        hosts_tab_grid.addWidget(self.hosts_tab_table, 1, 0)

        hosts_tab_grid.setRowStretch(0, 1)
        hosts_tab_grid.setRowStretch(1, 20)

        self.hosts_tab.setLayout(hosts_tab_grid)

        # Generate sub-fram
        self.gen_hosts_tab_frame0()
        self.gen_hosts_tab_table()

    def gen_hosts_tab_frame0(self):
        # self.hosts_tab_frame0
        hosts_tab_queue_label = QLabel('       Queue', self.hosts_tab_frame0)
        hosts_tab_queue_label.setStyleSheet("font-weight: bold;")
        self.hosts_tab_queue_combo = QComboBox(self.hosts_tab_frame0)
        self.set_hosts_tab_queue_combo()
        self.hosts_tab_queue_combo.currentIndexChanged.connect(self.gen_hosts_tab_table)
        hosts_tab_empty_label = QLabel('')

        # self.hosts_tab_frame0 - Grid
        hosts_tab_frame0_grid = QGridLayout()

        hosts_tab_frame0_grid.addWidget(hosts_tab_queue_label, 0, 0)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_queue_combo, 0, 1)
        hosts_tab_frame0_grid.addWidget(hosts_tab_empty_label, 0, 2)

        hosts_tab_frame0_grid.setColumnStretch(0, 1)
        hosts_tab_frame0_grid.setColumnStretch(1, 1)
        hosts_tab_frame0_grid.setColumnStretch(2, 12)

        self.hosts_tab_frame0.setLayout(hosts_tab_frame0_grid)

    def gen_hosts_tab_table(self):
        # self.hosts_tab_table
        self.hosts_tab_table.setShowGrid(True)
        self.hosts_tab_table.setSortingEnabled(True)
        self.hosts_tab_table.setColumnCount(0)
        self.hosts_tab_table.setColumnCount(12)
        self.hosts_tab_table.setHorizontalHeaderLabels(['Host', 'Status', 'Queue', 'Ncpus', 'MAX', 'Njobs', 'Ut (%)', 'MaxMem (G)', 'Mem (G)', 'MaxSwp (G)', 'Swp (G)', 'Tmp (G)'])

        self.hosts_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.hosts_tab_table.setColumnWidth(1, 90)
        self.hosts_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.hosts_tab_table.setColumnWidth(3, 60)
        self.hosts_tab_table.setColumnWidth(4, 60)
        self.hosts_tab_table.setColumnWidth(5, 60)
        self.hosts_tab_table.setColumnWidth(6, 60)
        self.hosts_tab_table.setColumnWidth(7, 100)
        self.hosts_tab_table.setColumnWidth(8, 75)
        self.hosts_tab_table.setColumnWidth(9, 100)
        self.hosts_tab_table.setColumnWidth(10, 75)
        self.hosts_tab_table.setColumnWidth(11, 75)

        print('* Loading LSF hosts information, please wait a moment ...')

        bhosts_dic = lsf_common.get_bhosts_info()
        bhosts_load_dic = lsf_common.get_bhosts_load_info()
        lshosts_dic = lsf_common.get_lshosts_info()
        lsload_dic = lsf_common.get_lsload_info()
        host_queue_dic = lsf_common.get_host_queue_info()

        # Get expected host list
        self.queue_host_list = []
        specified_queue = self.hosts_tab_queue_combo.currentText().strip()

        if specified_queue == 'ALL':
            self.queue_host_list = self.host_list
        else:
            for host in self.host_list:
                if host in host_queue_dic:
                    if specified_queue in host_queue_dic[host]:
                        self.queue_host_list.append(host)

        # Fill self.hosts_tab_table items.
        self.hosts_tab_table.setRowCount(0)
        self.hosts_tab_table.setRowCount(len(self.queue_host_list))

        for i in range(len(self.queue_host_list)):
            host = self.queue_host_list[i]

            # For "Host" item.
            j = 0
            item = QTableWidgetItem(host)
            item.setFont(QFont('song', 9, QFont.Bold))
            self.hosts_tab_table.setItem(i, j, item)

            # For "Status" item.
            j = j+1
            index = bhosts_dic['HOST_NAME'].index(host)
            status = bhosts_dic['STATUS'][index]
            item = QTableWidgetItem(status)

            if (str(status) == 'unavail') or (str(status) == 'unreach') or (str(status) == 'closed_LIM'):
                item.setForeground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # For "Queue" item.
            j = j+1

            if host in host_queue_dic.keys():
                queues = ' '.join(host_queue_dic[host])
                item = QTableWidgetItem(queues)
                self.hosts_tab_table.setItem(i, j, item)

            # For "Ncpus" item.
            j = j+1
            index = lshosts_dic['HOST_NAME'].index(host)
            ncpus = lshosts_dic['ncpus'][index]

            if not re.match('^[0-9]+$', ncpus):
                common.print_warning('*Warning*: host(' + str(host) + ') ncpus info "' + str(ncpus) + '": invalid value, reset it to "0".')
                ncpus = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ncpus))
            self.hosts_tab_table.setItem(i, j, item)

            # For "MAX" item.
            j = j+1
            index = bhosts_dic['HOST_NAME'].index(host)
            max = bhosts_dic['MAX'][index]

            if not re.match('^[0-9]+$', max):
                common.print_warning('*Warning*: host(' + str(host) + ') MAX info "' + str(max) + '": invalid value, reset it to "0".')
                max = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(max))
            self.hosts_tab_table.setItem(i, j, item)

            # For "Njobs" item.
            j = j+1
            index = bhosts_dic['HOST_NAME'].index(host)
            njobs = bhosts_dic['NJOBS'][index]

            if not re.match('^[0-9]+$', njobs):
                common.print_warning('*Warning*: host(' + str(host) + ') NJOBS info "' + str(njobs) + '": invalid value, reset it to "0".')
                njobs = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(njobs))
            item.setFont(QFont('song', 9, QFont.Bold))
            self.hosts_tab_table.setItem(i, j, item)

            # For "Ut" item.
            j = j+1

            if (host in bhosts_load_dic) and ('Total' in bhosts_load_dic[host]) and ('ut' in bhosts_load_dic[host]['Total']) and (bhosts_load_dic[host]['Total']['ut'] != '-'):
                ut = bhosts_load_dic[host]['Total']['ut']
            else:
                index = lsload_dic['HOST_NAME'].index(host)
                ut = lsload_dic['ut'][index]

            ut = re.sub('%', '', ut)

            if not re.match('^[0-9]+$', ut):
                common.print_warning('*Warning*: host(' + str(host) + ') ut info "' + str(ut) + '": invalid value, reset it to "0".')
                ut = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ut))

            if int(ut) > 90:
                item.setForeground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # For "MaxMem" item.
            j = j+1
            index = lshosts_dic['HOST_NAME'].index(host)
            maxmem = lshosts_dic['maxmem'][index]

            if re.search('M', maxmem):
                maxmem = re.sub('M', '', maxmem)
                maxmem = float(maxmem)/1024
            elif re.search('G', maxmem):
                maxmem = re.sub('G', '', maxmem)
            elif re.search('T', maxmem):
                maxmem = re.sub('T', '', maxmem)
                maxmem = float(maxmem)*1024
            else:
                common.print_warning('*Warning*: host(' + str(host) + ') maxmem info "' + str(maxmem) + '": unrecognized unit, reset it to "0".')
                maxmem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(maxmem)))
            self.hosts_tab_table.setItem(i, j, item)

            # For "Mem" item.
            j = j+1

            if (host in bhosts_load_dic) and ('Total' in bhosts_load_dic[host]) and ('mem' in bhosts_load_dic[host]['Total']) and (bhosts_load_dic[host]['Total']['mem'] != '-'):
                mem = bhosts_load_dic[host]['Total']['mem']
            else:
                index = lsload_dic['HOST_NAME'].index(host)
                mem = lsload_dic['mem'][index]

            if re.search('M', mem):
                mem = re.sub('M', '', mem)
                mem = float(mem)/1024
            elif re.search('G', mem):
                mem = re.sub('G', '', mem)
            elif re.search('T', mem):
                mem = re.sub('T', '', mem)
                mem = float(mem)*1024
            else:
                common.print_warning('*Warning*: host(' + str(host) + ') mem info "' + str(mem) + '": unrecognized unit, reset it to "0".')
                mem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(mem)))

            if (maxmem and (float(mem)/float(maxmem) < 0.1)):
                item.setForeground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # For "MaxSwp" item.
            j = j+1
            index = lshosts_dic['HOST_NAME'].index(host)
            maxswp = lshosts_dic['maxswp'][index]

            if re.search('M', maxswp):
                maxswp = re.sub('M', '', maxswp)
                maxswp = float(maxswp)/1024
            elif re.search('G', maxswp):
                maxswp = re.sub('G', '', maxswp)
            elif re.search('T', maxswp):
                maxswp = re.sub('T', '', maxswp)
                maxswp = float(maxswp)*1024
            else:
                common.print_warning('*Warning*: host(' + str(host) + ') maxswp info "' + str(maxswp) + '": unrecognized unit, reset it to "0".')
                maxswp = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(maxswp)))
            self.hosts_tab_table.setItem(i, j, item)

            # For "Swp" item.
            j = j+1

            if (host in bhosts_load_dic) and ('Total' in bhosts_load_dic[host]) and ('swp' in bhosts_load_dic[host]['Total']) and (bhosts_load_dic[host]['Total']['swp'] != '-'):
                swp = bhosts_load_dic[host]['Total']['swp']
            else:
                index = lsload_dic['HOST_NAME'].index(host)
                swp = lsload_dic['swp'][index]

            if re.search('M', swp):
                swp = re.sub('M', '', swp)
                swp = float(swp)/1024
            elif re.search('G', swp):
                swp = re.sub('G', '', swp)
            elif re.search('T', swp):
                swp = re.sub('T', '', swp)
                swp = float(swp)*1024
            else:
                common.print_warning('*Warning*: host(' + str(host) + ') swp info "' + str(swp) + '": unrecognized unit, reset it to "0".')
                swp = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(swp)))
            self.hosts_tab_table.setItem(i, j, item)

            # For "Tmp" item.
            j = j+1

            if (host in bhosts_load_dic) and ('Total' in bhosts_load_dic[host]) and ('tmp' in bhosts_load_dic[host]['Total']) and (bhosts_load_dic[host]['Total']['tmp'] != '-'):
                tmp = bhosts_load_dic[host]['Total']['tmp']
            else:
                index = lsload_dic['HOST_NAME'].index(host)
                tmp = lsload_dic['tmp'][index]

            if re.search('M', tmp):
                tmp = re.sub('M', '', tmp)
                tmp = float(tmp)/1024
            elif re.search('G', tmp):
                tmp = re.sub('G', '', tmp)
            elif re.search('T', tmp):
                tmp = re.sub('T', '', tmp)
                tmp = float(tmp)*1024
            else:
                common.print_warning('*Warning*: host(' + str(host) + ') tmp info "' + str(tmp) + '": unrecognized unit, reset it to "0".')
                tmp = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(tmp)))

            if int(float(tmp)) == 0:
                item.setForeground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

    def hosts_tab_check_click(self, item=None):
        """
        If click the Host name, jump to the LOAD Tab and show the host load inforamtion.
        If click the non-zero Njobs number, jump to the JOBS tab and show the host related jobs information.
        """
        if item is not None:
            currentRow = self.hosts_tab_table.currentRow()
            host = self.hosts_tab_table.item(currentRow, 0).text().strip()
            njobs_num = self.hosts_tab_table.item(currentRow, 5).text().strip()

            if item.column() == 0:
                host_list = copy.deepcopy(self.host_list)
                host_list.remove(host)
                host_list.insert(0, host)
                self.set_load_tab_host_combo(host_list)
                self.main_tab.setCurrentWidget(self.load_tab)
            elif item.column() == 5:
                if int(njobs_num) > 0:
                    self.jobs_tab_user_line.setText('')
                    self.set_jobs_tab_status_combo(['RUN', 'PEND', 'DONE', 'EXIT', 'ALL'])
                    self.set_jobs_tab_queue_combo()

                    host_list = copy.deepcopy(self.queue_host_list)
                    host_list.remove(host)
                    host_list.insert(0, host)
                    host_list.insert(1, 'ALL')
                    self.set_jobs_tab_started_on_combo(host_list)

                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)

                    self.main_tab.setCurrentWidget(self.jobs_tab)

    def set_hosts_tab_queue_combo(self):
        """
        Set (initialize) self.hosts_tab_queue_combo.
        """
        self.hosts_tab_queue_combo.clear()

        queue_list = copy.deepcopy(self.queue_list)
        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.hosts_tab_queue_combo.addItem(queue)
# For hosts TAB (end) #

# For queues TAB (start) #
    def gen_queues_tab(self):
        """
        Generate the queues tab on lsfMonitor GUI, show queues informations.
        """
        self.bqueues_files_dic = {}

        # self.queues_tab
        self.queues_tab_table = QTableWidget(self.queues_tab)
        self.queues_tab_table.itemClicked.connect(self.queues_tab_check_click)

        self.queues_tab_frame0 = QFrame(self.queues_tab)
        self.queues_tab_frame0.setFrameShadow(QFrame.Raised)
        self.queues_tab_frame0.setFrameShape(QFrame.Box)

        self.queues_tab_frame1 = QFrame(self.queues_tab)
        self.queues_tab_frame1.setFrameShadow(QFrame.Raised)
        self.queues_tab_frame1.setFrameShape(QFrame.Box)

        # self.queues_tab - Grid
        queues_tab_grid = QGridLayout()

        queues_tab_grid.addWidget(self.queues_tab_table, 0, 0)
        queues_tab_grid.addWidget(self.queues_tab_frame0, 0, 1)
        queues_tab_grid.addWidget(self.queues_tab_frame1, 1, 0, 1, 2)

        queues_tab_grid.setRowStretch(0, 2)
        queues_tab_grid.setRowStretch(1, 1)
        queues_tab_grid.setColumnStretch(0, 1)
        queues_tab_grid.setColumnStretch(1, 10)

        queues_tab_grid.setRowMinimumHeight(0, 380)
        queues_tab_grid.setRowMinimumHeight(1, 120)
        queues_tab_grid.setColumnMinimumWidth(0, 328)
        queues_tab_grid.setColumnMinimumWidth(1, 500)

        self.queues_tab.setLayout(queues_tab_grid)

        # Generate sub-frame
        self.gen_queues_tab_table()
        self.gen_queues_tab_frame0()
        self.gen_queues_tab_frame1()

    def gen_queues_tab_table(self):
        self.queues_tab_table.setShowGrid(True)
        self.queues_tab_table.setColumnCount(0)
        self.queues_tab_table.setColumnCount(3)
        self.queues_tab_table.setHorizontalHeaderLabels(['QUEUE', 'PEND', 'RUN'])

        self.queues_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queues_tab_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queues_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        # Hide the vertical header
        self.queues_tab_table.verticalHeader().setVisible(False)

        # File self.queues_tab_table items.
        self.queues_tab_table.setRowCount(0)
        self.queues_tab_table.setRowCount(len(self.queue_list)+1)

        print('* Loading LSF queue information, please wait a moment ...')
        queues_dic = lsf_common.get_bqueues_info()
        queue_list = copy.deepcopy(self.queue_list)

        queue_list.append('ALL')

        pend_sum = 0
        run_sum = 0

        for i in range(len(queue_list)):
            queue = queue_list[i]
            index = 0

            if i < len(queue_list)-1:
                index = queues_dic['QUEUE_NAME'].index(queue)

            # For "QUEUE" item.
            j = 0
            item = QTableWidgetItem(queue)
            self.queues_tab_table.setItem(i, j, item)

            # For "PEND" item.
            j = j+1

            if i == len(queue_list)-1:
                pend = str(pend_sum)
            else:
                pend = queues_dic['PEND'][index]
                pend_sum += int(pend)

            item = QTableWidgetItem(pend)
            item.setFont(QFont('song', 9, QFont.Bold))

            if int(pend) > 0:
                item.setForeground(QBrush(Qt.red))

            self.queues_tab_table.setItem(i, j, item)

            # For "RUN" item.
            j = j+1

            if i == len(queue_list)-1:
                run = str(run_sum)
            else:
                run = queues_dic['RUN'][index]
                run_sum += int(run)

            item = QTableWidgetItem(run)
            item.setFont(QFont('song', 9, QFont.Bold))
            self.queues_tab_table.setItem(i, j, item)

    def gen_queues_tab_frame0(self):
        # self.queues_tab_frame0
        self.queue_job_num_figure_canvas = FigureCanvas()
        self.queue_job_num_navigation_toolbar = NavigationToolbar2QT(self.queue_job_num_figure_canvas, self)

        # self.queues_tab_frame0 - Grid
        queues_tab_frame0_grid = QGridLayout()
        queues_tab_frame0_grid.addWidget(self.queue_job_num_navigation_toolbar, 0, 0)
        queues_tab_frame0_grid.addWidget(self.queue_job_num_figure_canvas, 1, 0)
        self.queues_tab_frame0.setLayout(queues_tab_frame0_grid)

    def gen_queues_tab_frame1(self):
        # self.queues_tab_frame1
        self.queues_tab_text = QTextEdit(self.queues_tab_frame1)

        # self.queues_tab_frame1 - Grid
        queues_tab_frame1_grid = QGridLayout()
        queues_tab_frame1_grid.addWidget(self.queues_tab_text, 0, 0)
        self.queues_tab_frame1.setLayout(queues_tab_frame1_grid)

    def queues_tab_check_click(self, item=None):
        """
        If click the QUEUE name, show queue information on QUEUE tab.
        If click the PEND number, jump to the JOBS Tab and show the queue PEND jobs.
        If click the RUN number, jump to the JOB Tab and show the queue RUN jobs.
        """
        if item is not None:
            currentRow = self.queues_tab_table.currentRow()
            queue = self.queues_tab_table.item(currentRow, 0).text().strip()
            pend_num = self.queues_tab_table.item(currentRow, 1).text().strip()
            run_num = self.queues_tab_table.item(currentRow, 2).text().strip()

            if item.column() == 0:
                print('* Checking queue "' + str(queue) + '".')

                self.update_queue_tab_frame0(queue)
                self.update_queue_tab_frame1(queue)
            elif item.column() == 1:
                if (pend_num != '') and (int(pend_num) > 0):
                    self.jobs_tab_user_line.setText('')
                    self.set_jobs_tab_status_combo(['PEND', 'RUN', 'DONE', 'EXIT', 'ALL'])

                    if queue == 'ALL':
                        self.set_jobs_tab_queue_combo()
                    else:
                        queue_list = copy.deepcopy(self.queue_list)
                        queue_list.remove(queue)
                        queue_list.insert(0, queue)
                        queue_list.insert(1, 'ALL')
                        self.set_jobs_tab_queue_combo(queue_list)

                    self.set_jobs_tab_started_on_combo()
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)
            elif item.column() == 2:
                if (run_num != '') and (int(run_num) > 0):
                    self.jobs_tab_user_line.setText('')
                    self.set_jobs_tab_status_combo(['RUN', 'PEND', 'DONE', 'EXIT', 'ALL'])

                    if queue == 'ALL':
                        self.set_jobs_tab_queue_combo()
                    else:
                        queue_list = copy.deepcopy(self.queue_list)
                        queue_list.remove(queue)
                        queue_list.insert(0, queue)
                        queue_list.insert(1, 'ALL')
                        self.set_jobs_tab_queue_combo(queue_list)

                    self.set_jobs_tab_started_on_combo()
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)

    def update_queue_tab_frame0(self, queue):
        """
        Draw queue (PEND/RUN) job number current job on self.queues_tab_frame0.
        """
        fig = self.queue_job_num_figure_canvas.figure
        fig.clear()
        self.queue_job_num_figure_canvas.draw()

        (date_list, pend_list, run_list) = self.get_queue_job_num_list(queue)

        if date_list and pend_list and run_list:
            for i in range(len(date_list)):
                date_list[i] = datetime.datetime.strptime(date_list[i], '%Y%m%d')

            self.draw_queue_job_num_curve(fig, queue, date_list, pend_list, run_list)

    def update_queue_tab_frame1(self, queue):
        """
        Show queue detailed informations on self.queues_tab_text.
        """
        self.queues_tab_text.clear()

        command = 'bqueues -l ' + str(queue)
        (return_code, stdout, stderr) = common.run_command(command)

        for line in str(stdout, 'utf-8').split('\n'):
            line = line.strip()
            self.queues_tab_text.insertPlainText(str(line) + '\n')

        pyqt5_common.text_edit_visible_position(self.queues_tab_text, 'Start')

    def get_queue_job_num_list(self, queue):
        """
        Draw (PEND/RUN) job number curve for specified queueu.
        """
        date_list = []
        pend_list = []
        run_list = []
        tmp_pend_list = []
        tmp_run_list = []

        queue_db_file = str(config.db_path) + '/monitor/queue.db'

        if not os.path.exists(queue_db_file):
            common.print_warning('*Warning*: queue pend/run job number information is missing for "' + str(queue) + '".')
        else:
            (queue_db_file_connect_result, queue_db_conn) = sqlite3_common.connect_db_file(queue_db_file)

            if queue_db_file_connect_result == 'failed':
                common.print_warning('*Warning*: Failed on connecting queue database file "' + str(self.queue_db_file) + '".')
            else:
                print('Getting history of queue PEND/RUN job number for queue "' + str(queue) + '".')

                table_name = 'queue_' + str(queue)
                data_dic = sqlite3_common.get_sql_table_data(queue_db_file, queue_db_conn, table_name, ['sample_time', 'PEND', 'RUN'])

                if not data_dic:
                    common.print_warning('*Warning*: queue pend/run job number information is empty for "' + str(queue) + '".')
                else:
                    orig_sample_time_list = data_dic['sample_time']
                    orig_pend_list = data_dic['PEND']
                    orig_run_list = data_dic['RUN']

                    for i in range(len(orig_sample_time_list)):
                        sample_time = orig_sample_time_list[i]
                        date = re.sub('_.*', '', sample_time)
                        pend_num = orig_pend_list[i]
                        run_num = orig_run_list[i]

                        if (i != 0) and ((i == len(orig_sample_time_list)-1) or (date not in date_list)):
                            pend_avg = int(sum(tmp_pend_list)/len(tmp_pend_list))
                            pend_list.append(pend_avg)
                            run_avg = int(sum(tmp_run_list)/len(tmp_run_list))
                            run_list.append(run_avg)

                        if date not in date_list:
                            date_list.append(date)
                            tmp_pend_list = []
                            tmp_run_list = []

                        tmp_pend_list.append(int(pend_num))
                        tmp_run_list.append(int(run_num))

                    # Cut date_list/pend_list/run_list, only save recent 30 days result.
                    if len(date_list) > 30:
                        date_list = date_list[-30:]
                        pend_list = pend_list[-30:]
                        run_list = run_list[-30:]

                    if len(date_list) == 0:
                        common.print_warning('*Warning*: queue pend/run job number information is empty for "' + str(queue) + '".')

                    queue_db_conn.close()

        return(date_list, pend_list, run_list)

    def draw_queue_job_num_curve(self, fig, queue, date_list, pend_list, run_list):
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)
        axes.set_title('queue "' + str(queue) + '" PEND/RUN slots number curve')
        axes.set_xlabel('Sample Date')
        axes.set_ylabel('Slots Num')
        axes.plot(date_list, pend_list, 'ro-', label='PEND', color='red')
        axes.plot(date_list, run_list, 'ro-', label='RUN', color='green')
        axes.legend(loc='upper right')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.queue_job_num_figure_canvas.draw()
# For queues TAB (end) #

# For load TAB (start) #
    def gen_load_tab(self):
        """
        Generate the load tab on lsfMonitor GUI, show host load (ut/mem) information.
        """
        # self.load_tab
        self.load_tab_frame0 = QFrame(self.load_tab)
        self.load_tab_frame1 = QFrame(self.load_tab)
        self.load_tab_frame2 = QFrame(self.load_tab)

        self.load_tab_frame0.setFrameShadow(QFrame.Raised)
        self.load_tab_frame0.setFrameShape(QFrame.Box)
        self.load_tab_frame1.setFrameShadow(QFrame.Raised)
        self.load_tab_frame1.setFrameShape(QFrame.Box)
        self.load_tab_frame2.setFrameShadow(QFrame.Raised)
        self.load_tab_frame2.setFrameShape(QFrame.Box)

        # self.load_tab - Grid
        load_tab_grid = QGridLayout()

        load_tab_grid.addWidget(self.load_tab_frame0, 0, 0)
        load_tab_grid.addWidget(self.load_tab_frame1, 1, 0)
        load_tab_grid.addWidget(self.load_tab_frame2, 2, 0)

        load_tab_grid.setRowStretch(0, 1)
        load_tab_grid.setRowStretch(1, 10)
        load_tab_grid.setRowStretch(2, 10)

        self.load_tab.setLayout(load_tab_grid)

        # Generate sub-frame
        self.gen_load_tab_frame0()
        self.gen_load_tab_frame1()
        self.gen_load_tab_frame2()

    def gen_load_tab_frame0(self):
        # self.load_tab_frame0
        load_tab_host_label = QLabel('          Host', self.load_tab_frame0)
        load_tab_host_label.setStyleSheet("font-weight: bold;")
        self.load_tab_host_combo = QComboBox(self.load_tab_frame0)
        self.set_load_tab_host_combo()
        self.load_tab_host_combo.currentIndexChanged.connect(self.update_load_tab_load_info)

        load_tab_date_label = QLabel('          Date', self.load_tab_frame0)
        load_tab_date_label.setStyleSheet("font-weight: bold;")
        self.load_tab_date_combo = QComboBox(self.load_tab_frame0)
        self.set_load_tab_date_combo()
        self.load_tab_date_combo.currentIndexChanged.connect(self.update_load_tab_load_info)

        load_tab_empty_label = QLabel('')

        # self.load_tab_frame0 - Grid
        load_tab_frame0_grid = QGridLayout()

        load_tab_frame0_grid.addWidget(load_tab_host_label, 0, 1)
        load_tab_frame0_grid.addWidget(self.load_tab_host_combo, 0, 2)
        load_tab_frame0_grid.addWidget(load_tab_date_label, 0, 3)
        load_tab_frame0_grid.addWidget(self.load_tab_date_combo, 0, 4)
        load_tab_frame0_grid.addWidget(load_tab_empty_label, 0, 5)

        load_tab_frame0_grid.setColumnStretch(1, 1)
        load_tab_frame0_grid.setColumnStretch(2, 1)
        load_tab_frame0_grid.setColumnStretch(3, 1)
        load_tab_frame0_grid.setColumnStretch(4, 1)
        load_tab_frame0_grid.setColumnStretch(5, 10)

        self.load_tab_frame0.setLayout(load_tab_frame0_grid)

    def gen_load_tab_frame1(self):
        # self.load_tab_frame1
        self.host_ut_figure_canvas = FigureCanvas()
        self.host_ut_navigation_toolbar = NavigationToolbar2QT(self.host_ut_figure_canvas, self)

        # self.load_tab_frame1 - Grid
        load_tab_frame1_grid = QGridLayout()
        load_tab_frame1_grid.addWidget(self.host_ut_navigation_toolbar, 0, 0)
        load_tab_frame1_grid.addWidget(self.host_ut_figure_canvas, 1, 0)
        self.load_tab_frame1.setLayout(load_tab_frame1_grid)

    def gen_load_tab_frame2(self):
        # self.load_tab_frame2
        self.host_mem_figure_canvas = FigureCanvas()
        self.host_mem_navigation_toolbar = NavigationToolbar2QT(self.host_mem_figure_canvas, self)

        # self.load_tab_frame2 - Grid
        load_tab_frame2_grid = QGridLayout()
        load_tab_frame2_grid.addWidget(self.host_mem_navigation_toolbar, 0, 0)
        load_tab_frame2_grid.addWidget(self.host_mem_figure_canvas, 1, 0)
        self.load_tab_frame2.setLayout(load_tab_frame2_grid)

    def set_load_tab_host_combo(self, host_list=[]):
        """
        Set (initialize) self.load_tab_host_combo.
        """
        self.load_tab_host_combo.clear()

        if not host_list:
            host_list = copy.deepcopy(self.host_list)
            host_list.insert(0, '')

        for host in host_list:
            self.load_tab_host_combo.addItem(host)

    def set_load_tab_date_combo(self):
        """
        Set (initialize) self.load_tab_date_combo.
        """
        self.load_tab_date_combo.clear()

        date_list = ['Last Day', 'Last Week', 'Last Month', 'Last Year']

        for date in date_list:
            self.load_tab_date_combo.addItem(date)

    def update_load_tab_load_info(self):
        """
        Update self.load_tab_frame1 (ut information) and self.load_tab_frame2 (memory information).
        """
        self.specified_host = self.load_tab_host_combo.currentText().strip()
        self.specified_date = self.load_tab_date_combo.currentText().strip()

        self.update_load_tab_frame1([], [])
        self.update_load_tab_frame2([], [])

        (sample_time_list, ut_list, mem_list) = self.get_load_info()

        if sample_time_list:
            self.update_load_tab_frame1(sample_time_list, ut_list)
            self.update_load_tab_frame2(sample_time_list, mem_list)

    def get_load_info(self):
        """
        Get sample_time/ut/mem list for specified host.
        """
        sample_time_list = []
        ut_list = []
        mem_list = []

        load_db_file = str(config.db_path) + '/monitor/load.db'

        if not os.path.exists(load_db_file):
            common.print_warning('*Warning*: load database "' + str(load_db_file) + '" is missing.')
        else:
            (load_db_file_connect_result, load_db_conn) = sqlite3_common.connect_db_file(load_db_file)

            if load_db_file_connect_result == 'failed':
                common.print_warning('*Warning*: Failed on connecting load database file "' + str(load_db_file) + '".')
            else:
                if self.specified_host:
                    print('Getting history of load information for host "' + str(self.specified_host) + '".')

                    table_name = 'load_' + str(self.specified_host)
                    data_dic = sqlite3_common.get_sql_table_data(load_db_file, load_db_conn, table_name, ['sample_time', 'ut', 'mem'])

                    if not data_dic:
                        common.print_warning('*Warning*: load information is empty for "' + str(self.specified_host) + '".')
                    else:
                        specified_date_second = 0

                        if self.specified_date == 'Last Day':
                            specified_date_second = time.mktime((datetime.datetime.now()-datetime.timedelta(days=1)).timetuple())
                        elif self.specified_date == 'Last Week':
                            specified_date_second = time.mktime((datetime.datetime.now()-datetime.timedelta(days=7)).timetuple())
                        elif self.specified_date == 'Last Month':
                            specified_date_second = time.mktime((datetime.datetime.now()-datetime.timedelta(days=30)).timetuple())
                        elif self.specified_date == 'Last Year':
                            specified_date_second = time.mktime((datetime.datetime.now()-datetime.timedelta(days=365)).timetuple())

                        for i in range(len(data_dic['sample_time'])-1, -1, -1):
                            sample_time_second = time.mktime(datetime.datetime.strptime(data_dic['sample_time'][i], '%Y%m%d_%H%M%S').timetuple())

                            if sample_time_second > specified_date_second:
                                # For sample_time
                                sample_time = datetime.datetime.strptime(data_dic['sample_time'][i], '%Y%m%d_%H%M%S')
                                sample_time_list.append(sample_time)

                                # For ut
                                ut = data_dic['ut'][i]

                                if ut:
                                    ut = int(re.sub('%', '', ut))
                                else:
                                    ut = 0

                                ut_list.append(ut)

                                # For mem
                                mem = data_dic['mem'][i]

                                if mem:
                                    if re.match('.*M', mem):
                                        mem = round(float(re.sub('M', '', mem))/1024, 1)
                                    elif re.match('.*G', mem):
                                        mem = round(float(re.sub('G', '', mem)), 1)
                                    elif re.match('.*T', mem):
                                        mem = round(float(re.sub('T', '', mem))*1024, 1)
                                else:
                                    mem = 0

                                mem_list.append(mem)

                    load_db_conn.close()

        return(sample_time_list, ut_list, mem_list)

    def update_load_tab_frame1(self, sample_time_list, ut_list):
        """
        Draw Ut curve for specified host on self.load_tab_frame1.
        """
        fig = self.host_ut_figure_canvas.figure
        fig.clear()
        self.host_ut_figure_canvas.draw()

        if sample_time_list and ut_list:
            self.draw_host_ut_curve(fig, sample_time_list, ut_list)

    def draw_host_ut_curve(self, fig, sample_time_list, ut_list):
        # Fil self.host_ut_figure_canvas.
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)
        axes.set_title('host "' + str(self.specified_host) + '" ut curve')
        axes.set_xlabel('Sample Time')
        axes.set_ylabel('Cpu Utilization (%)')
        axes.plot(sample_time_list, ut_list, 'ro-', color='red')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.host_ut_figure_canvas.draw()

    def update_load_tab_frame2(self, sample_time_list, mem_list):
        """
        Draw mem curve for specified host on self.load_tab_frame2.
        """
        fig = self.host_mem_figure_canvas.figure
        fig.clear()
        self.host_mem_figure_canvas.draw()

        if sample_time_list and mem_list:
            self.draw_host_mem_curve(fig, sample_time_list, mem_list)

    def draw_host_mem_curve(self, fig, sample_time_list, mem_list):
        # File self.host_mem_figure_canvas.
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)
        axes.set_title('host "' + str(self.specified_host) + '" available mem curve')
        axes.set_xlabel('Sample Time')
        axes.set_ylabel('Available RAM (G)')
        axes.plot(sample_time_list, mem_list, 'ro-', color='green')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.host_mem_figure_canvas.draw()
# For load TAB (end) #

# For license TAB (start) #
    def gen_license_tab(self):
        """
        Generate the license tab on lsfMonitor GUI, show host license usage information.
        """
        # self.license_tab
        self.license_tab_frame0 = QFrame(self.license_tab)
        self.license_tab_frame0.setFrameShadow(QFrame.Raised)
        self.license_tab_frame0.setFrameShape(QFrame.Box)

        self.license_tab_feature_label = QLabel('Feature Information', self.license_tab)
        self.license_tab_feature_label.setStyleSheet("font-weight: bold;")
        self.license_tab_feature_label.setAlignment(Qt.AlignCenter)
        self.license_tab_expires_label = QLabel('Expires Information', self.license_tab)
        self.license_tab_expires_label.setStyleSheet("font-weight: bold;")
        self.license_tab_expires_label.setAlignment(Qt.AlignCenter)

        self.license_tab_feature_table = QTableWidget(self.license_tab)
        self.license_tab_feature_table.itemClicked.connect(self.license_tab_check_click)
        self.license_tab_expires_table = QTableWidget(self.license_tab)

        # self.license_tab - Grid
        license_tab_grid = QGridLayout()

        license_tab_grid.addWidget(self.license_tab_frame0, 0, 0, 1, 2)
        license_tab_grid.addWidget(self.license_tab_feature_label, 1, 0)
        license_tab_grid.addWidget(self.license_tab_expires_label, 1, 1)
        license_tab_grid.addWidget(self.license_tab_feature_table, 2, 0)
        license_tab_grid.addWidget(self.license_tab_expires_table, 2, 1)

        license_tab_grid.setRowStretch(0, 2)
        license_tab_grid.setRowStretch(1, 1)
        license_tab_grid.setRowStretch(2, 20)

        self.license_tab.setLayout(license_tab_grid)

        # Generate sub-frame
        self.gen_license_tab_frame0()
        self.gen_license_tab_feature_table()
        self.gen_license_tab_expires_table()

        if self.specified_feature:
            self.license_tab_license_feature_line.setText(str(self.specified_feature))
            self.filter_license_feature()

    def gen_license_tab_frame0(self):
        # self.license_tab_frame0
        # Show
        license_tab_show_label = QLabel('       Show', self.license_tab_frame0)
        license_tab_show_label.setStyleSheet("font-weight: bold;")
        self.license_tab_show_combo = QComboBox(self.license_tab_frame0)
        self.set_license_tab_show_combo()

        self.license_tab_show_combo.currentIndexChanged.connect(self.filter_license_feature)

        # License Server
        license_tab_license_server_label = QLabel('     Server', self.license_tab_frame0)
        license_tab_license_server_label.setStyleSheet("font-weight: bold;")
        self.license_tab_license_server_combo = QComboBox(self.license_tab_frame0)
        self.license_tab_license_server_combo.setMaximumWidth(100)
        self.set_license_tab_license_server_combo()

        self.license_tab_license_server_combo.currentIndexChanged.connect(self.filter_license_feature)

        # License Vendor
        license_tab_license_vendor_label = QLabel('     Vendor', self.license_tab_frame0)
        license_tab_license_vendor_label.setStyleSheet("font-weight: bold;")
        self.license_tab_license_vendor_combo = QComboBox(self.license_tab_frame0)
        self.license_tab_license_vendor_combo.setMaximumWidth(100)
        self.set_license_tab_license_vendor_combo()

        self.license_tab_license_vendor_combo.activated.connect(self.update_license_tab_license_product_combo)

        # License Product
        license_tab_license_product_label = QLabel('    Product', self.license_tab_frame0)
        license_tab_license_product_label.setStyleSheet("font-weight: bold;")
        self.license_tab_license_product_combo = QComboBox(self.license_tab_frame0)
        self.license_tab_license_product_combo.setMaximumWidth(100)
        self.set_license_tab_license_product_combo()

        self.license_tab_license_product_combo.activated.connect(self.filter_license_feature)

        # License Feature
        license_tab_license_feature_label = QLabel('    Feature', self.license_tab_frame0)
        license_tab_license_feature_label.setStyleSheet("font-weight: bold;")
        self.license_tab_license_feature_line = QLineEdit()

        license_tab_filter_button = QPushButton('Filter', self.license_tab_frame0)
        license_tab_filter_button.clicked.connect(self.filter_license_feature)

        # self.license_tab_frame0 - Grid
        license_tab_frame0_grid = QGridLayout()

        license_tab_frame0_grid.addWidget(license_tab_show_label, 0, 0)
        license_tab_frame0_grid.addWidget(self.license_tab_show_combo, 0, 1)
        license_tab_frame0_grid.addWidget(license_tab_license_server_label, 0, 2)
        license_tab_frame0_grid.addWidget(self.license_tab_license_server_combo, 0, 3)
        license_tab_frame0_grid.addWidget(license_tab_license_vendor_label, 0, 4)
        license_tab_frame0_grid.addWidget(self.license_tab_license_vendor_combo, 0, 5)
        license_tab_frame0_grid.addWidget(license_tab_license_product_label, 0, 6)
        license_tab_frame0_grid.addWidget(self.license_tab_license_product_combo, 0, 7)
        license_tab_frame0_grid.addWidget(license_tab_license_feature_label, 0, 8)
        license_tab_frame0_grid.addWidget(self.license_tab_license_feature_line, 0, 9)
        license_tab_frame0_grid.addWidget(license_tab_filter_button, 0, 10)

        license_tab_frame0_grid.setColumnStretch(0, 1)
        license_tab_frame0_grid.setColumnStretch(1, 1)
        license_tab_frame0_grid.setColumnStretch(2, 1)
        license_tab_frame0_grid.setColumnStretch(3, 1)
        license_tab_frame0_grid.setColumnStretch(4, 1)
        license_tab_frame0_grid.setColumnStretch(5, 1)
        license_tab_frame0_grid.setColumnStretch(6, 1)
        license_tab_frame0_grid.setColumnStretch(7, 1)
        license_tab_frame0_grid.setColumnStretch(8, 1)
        license_tab_frame0_grid.setColumnStretch(9, 3)
        license_tab_frame0_grid.setColumnStretch(10, 1)

        self.license_tab_frame0.setLayout(license_tab_frame0_grid)

    def set_license_tab_show_combo(self):
        self.license_tab_show_combo.clear()
        self.license_tab_show_combo.addItem('ALL')
        self.license_tab_show_combo.addItem('in_use')

    def set_license_tab_license_server_combo(self):
        self.license_tab_license_server_combo.clear()

        license_server_list = list(self.license_dic.keys())
        license_server_list.insert(0, 'ALL')

        for license_server in license_server_list:
            self.license_tab_license_server_combo.addItem(license_server)

    def set_license_tab_license_vendor_combo(self):
        self.license_tab_license_vendor_combo.clear()

        license_vendor_list = list(self.product_feature_relationship_dic.keys())
        license_vendor_list.insert(0, 'ALL')

        for license_vendor in license_vendor_list:
            self.license_tab_license_vendor_combo.addItem(license_vendor)

    def set_license_tab_license_product_combo(self):
        self.license_tab_license_product_combo.clear()

        license_product_list = []
        current_vendor = self.license_tab_license_vendor_combo.currentText().strip()

        for vendor in self.product_feature_relationship_dic.keys():
            if (current_vendor == 'ALL') or (vendor == current_vendor):
                for product in self.product_feature_relationship_dic[vendor].keys():
                    if product not in license_product_list:
                        license_product_list.append(product)

        license_product_list.insert(0, 'ALL')

        for license_product in license_product_list:
            self.license_tab_license_product_combo.addItem(license_product)

    def update_license_tab_license_product_combo(self):
        self.set_license_tab_license_product_combo()
        self.filter_license_feature()

    def filter_license_feature(self):
        # Get license information.
        print('* Loading license information, please wait a moment ...')
        my_show_message = ShowMessage('Info', 'Please wait, getting license information ...')
        my_show_message.start()
        self.license_dic = license_common.get_license_info()
        my_show_message.terminate()

        if not self.license_dic:
            print('*Warning*: Not find any valid license information.')

        if self.license_dic:
            # Get specified Vendor-Product license features.
            vendor_product_feature_list = []
            current_vendor = self.license_tab_license_vendor_combo.currentText().strip()
            current_product = self.license_tab_license_product_combo.currentText().strip()

            if not ((current_vendor == 'ALL') and (current_product == 'ALL')):
                for vendor in self.product_feature_relationship_dic.keys():
                    if (current_vendor == 'ALL') or (vendor == current_vendor):
                        for product in self.product_feature_relationship_dic[vendor].keys():
                            if (current_product == 'ALL') or (product == current_product):
                                for feature in self.product_feature_relationship_dic[vendor][product]:
                                    if feature not in vendor_product_feature_list:
                                        vendor_product_feature_list.append(feature)

            # Fileter license feature with Server/Vendor/Product.
            filtered_license_feature_list = []
            current_server = self.license_tab_license_server_combo.currentText().strip()

            for (license_server, license_server_dic) in self.license_dic.items():
                if (current_server == 'ALL') or (license_server == current_server):
                    if 'feature' in license_server_dic:
                        for feature in license_server_dic['feature']:
                            if feature not in filtered_license_feature_list:
                                if vendor_product_feature_list:
                                    if feature in vendor_product_feature_list:
                                        filtered_license_feature_list.append(feature)
                                else:
                                    if (current_vendor == 'ALL') and (current_product == 'ALL'):
                                        filtered_license_feature_list.append(feature)

            # Filter license feature with Feature line.
            expected_license_feature_list = []
            specified_license_feature_list = self.license_tab_license_feature_line.text().strip().split()

            if not specified_license_feature_list:
                expected_license_feature_list = filtered_license_feature_list
            else:
                expected_license_feature_absolute_list = []
                expected_license_feature_relative_list = []

                for expected_license_feature in specified_license_feature_list:
                    if expected_license_feature in filtered_license_feature_list:
                        expected_license_feature_absolute_list.append(expected_license_feature)
                    else:
                        for license_feature in filtered_license_feature_list:
                            if re.search(expected_license_feature.lower(), license_feature.lower()):
                                expected_license_feature_relative_list.append(license_feature)

                expected_license_feature_list = expected_license_feature_absolute_list + expected_license_feature_relative_list

            if len(expected_license_feature_list) > 5:
                print('* Filter license features "' + str(' '.join(expected_license_feature_list[0:4])) + '" ...')
            elif len(expected_license_feature_list) > 0:
                print('* Filter license features "' + str(' '.join(expected_license_feature_list)) + '".')

            current_server = self.license_tab_license_server_combo.currentText().strip()
            specified_show = self.license_tab_show_combo.currentText().strip()
            self.license_dic = license_common.filter_license_feature(self.license_dic, features=expected_license_feature_list, servers=[current_server, ], mode=specified_show)

            # Update self.license_tab_feature_table and self.license_tab_expires_table.
            self.gen_license_tab_feature_table()
            self.gen_license_tab_expires_table()

    def gen_license_tab_feature_table(self, update=False):
        # Get license information.
        if update:
            print('* Loading license information, please wait a moment ...')
            self.license_dic = license_common.get_license_info()

            if not self.license_dic:
                print('*Warning*: Not find any valid license information.')

        self.license_tab_feature_table.setShowGrid(True)
        self.license_tab_feature_table.setColumnCount(0)
        self.license_tab_feature_table.setColumnCount(4)
        self.license_tab_feature_table.setHorizontalHeaderLabels(['License Server', 'Feature', 'Issued', 'In_use'])

        self.license_tab_feature_table.setColumnWidth(0, 160)
        self.license_tab_feature_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.license_tab_feature_table.setColumnWidth(2, 50)
        self.license_tab_feature_table.setColumnWidth(3, 50)

        # Get license feature information length.
        license_feature_info_length = 0

        for (license_server, license_server_dic) in self.license_dic.items():
            if 'feature' in license_server_dic:
                for feature in license_server_dic['feature']:
                    license_feature_info_length += 1

        # Fill self.license_tab_feature_table items.
        self.license_tab_feature_table.setRowCount(0)
        self.license_tab_feature_table.setRowCount(license_feature_info_length)

        row = -1

        for (license_server, license_server_dic) in self.license_dic.items():
            if 'feature' in license_server_dic:
                for feature in license_server_dic['feature']:
                    row += 1
                    self.license_tab_feature_table.setItem(row, 0, QTableWidgetItem(license_server))

                    item = QTableWidgetItem(feature)
                    item.setForeground(QBrush(Qt.blue))
                    self.license_tab_feature_table.setItem(row, 1, item)

                    issued = license_server_dic['feature'][feature]['issued']
                    item = QTableWidgetItem(issued)
                    self.license_tab_feature_table.setItem(row, 2, item)

                    in_use = license_server_dic['feature'][feature]['in_use']
                    item = QTableWidgetItem(in_use)
                    item.setFont(QFont('song', 9, QFont.Bold))
                    self.license_tab_feature_table.setItem(row, 3, item)

    def license_tab_check_click(self, item=None):
        """
        If click the Job id, jump to the JOB tab and show the job information.
        If click the "PEND" Status, show the job pend reasons on a QMessageBox.information().
        """
        if item is not None:
            if item.column() == 3:
                currentRow = self.license_tab_feature_table.currentRow()
                in_use_num = int(self.license_tab_feature_table.item(currentRow, 3).text().strip())

                if in_use_num > 0:
                    license_server = self.license_tab_feature_table.item(currentRow, 0).text().strip()
                    license_feature = self.license_tab_feature_table.item(currentRow, 1).text().strip()

                    print('* Getting license feature "' + str(license_feature) + '" usage on license server ' + str(license_server) + ' ...')
                    self.my_show_license_feature_usage = ShowLicenseFeatureUsage(server=license_server, feature=license_feature)
                    self.my_show_license_feature_usage.start()

    def gen_license_tab_expires_table(self):
        self.license_tab_expires_table.setShowGrid(True)
        self.license_tab_expires_table.setColumnCount(0)
        self.license_tab_expires_table.setColumnCount(4)
        self.license_tab_expires_table.setHorizontalHeaderLabels(['License Server', 'Feature', 'Num', 'Expires'])

        self.license_tab_expires_table.setColumnWidth(0, 160)
        self.license_tab_expires_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.license_tab_expires_table.setColumnWidth(2, 40)
        self.license_tab_expires_table.setColumnWidth(3, 100)

        # Get license feature information length.
        license_expires_info_length = 0

        for (license_server, license_server_dic) in self.license_dic.items():
            if 'expires' in license_server_dic:
                for feature in license_server_dic['expires']:
                    license_expires_info_length += len(license_server_dic['expires'][feature])

        # File self.license_tab_expires_table items.
        self.license_tab_expires_table.setRowCount(0)
        self.license_tab_expires_table.setRowCount(license_expires_info_length)

        row = -1

        for (license_server, license_server_dic) in self.license_dic.items():
            if 'expires' in license_server_dic:
                for feature in license_server_dic['expires']:
                    for expires_dic in license_server_dic['expires'][feature]:
                        row += 1
                        self.license_tab_expires_table.setItem(row, 0, QTableWidgetItem(license_server))

                        item = QTableWidgetItem(feature)
                        item.setForeground(QBrush(Qt.blue))
                        self.license_tab_expires_table.setItem(row, 1, item)

                        license = expires_dic['license']
                        self.license_tab_expires_table.setItem(row, 2, QTableWidgetItem(license))

                        expires = expires_dic['expires']
                        item = QTableWidgetItem(expires)
                        expires_mark = license_common.check_expire_date(expires)

                        if expires_mark == 0:
                            pass
                        elif expires_mark == -1:
                            item.setForeground(QBrush(Qt.gray))
                        else:
                            item.setForeground(QBrush(Qt.red))
                        self.license_tab_expires_table.setItem(row, 3, item)
# For license TAB (end) #

    def close_event(self, QCloseEvent):
        """
        When window close, post-process.
        """
        print('Bye')


class CheckIssueReason(QThread):
    """
    Start tool check_issue_reason.py to debug issue job.
    """
    def __init__(self, job='', issue='PEND'):
        super(CheckIssueReason, self).__init__()
        self.job = job
        self.issue = issue

    def run(self):
        command = str(str(os.environ['LSFMONITOR_INSTALL_PATH'])) + '/monitor/tools/check_issue_reason.py -i ' + str(self.issue)

        if self.job:
            command = str(command) + ' -j ' + str(self.job)

        os.system(command)


class ProcessTracer(QThread):
    """
    Start tool process_tracer.py to trace job process.
    """
    def __init__(self, job):
        super(ProcessTracer, self).__init__()
        self.job = job

    def run(self):
        command = str(str(os.environ['LSFMONITOR_INSTALL_PATH'])) + '/monitor/tools/process_tracer.py -j ' + str(self.job)
        os.system(command)


class ShowLicenseFeatureUsage(QThread):
    """
    Start tool process_tracer.py to trace job process.
    """
    def __init__(self, server, feature):
        super(ShowLicenseFeatureUsage, self).__init__()
        self.server = server
        self.feature = feature

    def run(self):
        command = str(str(os.environ['LSFMONITOR_INSTALL_PATH'])) + '/monitor/tools/show_license_feature_usage.py -s ' + str(self.server) + ' -f ' + str(self.feature)
        os.system(command)


class ShowMessage(QThread):
    """
    Show message with tool message.py.
    """
    def __init__(self, title, message):
        super(ShowMessage, self).__init__()
        self.title = title
        self.message = message

    def run(self):
        command = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor/tools/message.py --title "' + str(self.title) + '" --message "' + str(self.message) + '"'
        os.system(command)


#################
# Main Function #
#################
def main():
    check_tool()
    (specified_job, specified_user, specified_feature, specified_tab) = read_args()
    app = QApplication(sys.argv)
    mw = MainWindow(specified_job, specified_user, specified_feature, specified_tab)
    mw.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
