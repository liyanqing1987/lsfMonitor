# -*- coding: utf-8 -*-

# Standard library imports
import os
import re
import sys
import json
import time
import copy
import socket
import getpass
import argparse
import datetime
from pathlib import Path
import numpy as np

# Third-party imports
import qdarkstyle
from PyQt5.QtCore import QDate, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextBlockFormat, QTextCharFormat, QTextImageFormat, QTextLength, QTextTableFormat
from PyQt5.QtWidgets import QAction, QApplication, QComboBox, QDateEdit, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, qApp, QInputDialog

# Local imports
LSFMONITOR_INSTALL_PATH = Path(os.environ['LSFMONITOR_INSTALL_PATH'])
sys.path.append(str(LSFMONITOR_INSTALL_PATH / 'monitor'))
from common import common
from common import common_lsf
from common import common_license
from common import common_pyqt5
from common import common_sqlite3
from common import common_ai
from common import common_ai_log

from common import common_config

config = common_config.load_config()


# Constants
VERSION = 'V2.3'
VERSION_DATE = '2026.06.06'
USER = getpass.getuser()
DEFAULT_RUNTIME_DIR = Path('/tmp') / f'runtime-{USER}'

# Environment configuration
os.environ.update({
    'LSB_NTRIES': '3',
    'PYTHONUNBUFFERED': '1',
    'XDG_RUNTIME_DIR': os.environ.get('XDG_RUNTIME_DIR', str(DEFAULT_RUNTIME_DIR))
})

# Ensure runtime directory exists
DEFAULT_RUNTIME_DIR.mkdir(exist_ok=True)
DEFAULT_RUNTIME_DIR.chmod(0o700)


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
                        default='',
                        choices=['JOB', 'JOBS', 'HOSTS', 'LOAD', 'USERS', 'QUEUES', 'UTILIZATION', 'LICENSE', 'AI'],
                        help='Specify current tab, default is "JOBS" tab.')
    parser.add_argument("--disable_license",
                        action='store_true',
                        default=False,
                        help='Disable license check function.')
    parser.add_argument("-d", "--dark_mode",
                        action='store_true',
                        default=False,
                        help='Enable dark mode on the main interface.')

    args = parser.parse_args()

    # Make sure specified job exists, only set tab to JOB if user didn't specify tab
    if args.jobid and not args.tab:
        args.tab = 'JOB'

    # Determine tab: user specified tab has highest priority
    if not args.tab:
        tab_priority = [
            (args.jobid, 'JOB'),
            (args.user, 'JOBS'),
            (args.feature, 'LICENSE')
        ]

        args.tab = next((tab for condition, tab in tab_priority if condition), 'JOBS')

    return args.jobid, args.user, args.feature, args.tab, args.disable_license, args.dark_mode


class MainWindow(QMainWindow):
    """
    Main window of lsfMonitor.
    """
    def __init__(self, specified_job, specified_user, specified_feature, specified_tab, disable_license, dark_mode):
        super().__init__()

        # Show version information.
        common.bprint(f'lsfMonitor Version: {VERSION} ({VERSION_DATE})', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint('', date_format='%Y-%m-%d %H:%M:%S')

        # Check cluster info.
        common.bprint('Checking cluster information ...', date_format='%Y-%m-%d %H:%M:%S')
        (self.tool, self.cluster) = self.check_cluster_info()

        # Reload cluster-specific config if exists.
        common_config.reload_config_for_cluster(self.cluster)

        # Set db_path.
        self.cluster_db_path = str(config.db_path) + '/lsfMonitor'

        if self.cluster:
            self.cluster_db_path = str(config.db_path) + '/' + str(self.cluster)

        common.create_dir(config.db_path, 0o1777)
        common.create_dir(self.cluster_db_path, 0o1777)

        # Save start action.
        log_dir = str(config.db_path) + '/log'
        self.my_save_log = common.SaveLog(log_dir, self.cluster)
        self.my_save_log.save_log('Start lsfMonitor')

        # Get license administrators list.
        self.license_administrator_list = []

        if config.license_administrators:
            self.license_administrator_list = config.license_administrators.split()

        # Init variables.
        self.specified_job = specified_job
        self.specified_user = specified_user
        self.specified_feature = specified_feature
        self.disable_license = disable_license
        self.dark_mode = dark_mode

        # Enable detail information on QUEUE/UTILIZATION tab.
        self.enable_queue_detail = False
        self.enable_utilization_detail = False

        # Utilization query cache (60 minutes cache timeout)
        self.utilization_cache = {}
        self.utilization_cache_timeout = 3600

        # Init LSF information related variables.
        self.bhosts_dic = {}
        self.busers_dic = {}
        self.lsload_dic = {}
        self.bqueues_dic = {}
        self.lshosts_dic = {}
        self.queue_host_dic = {}
        self.host_queue_dic = {}
        self.bhosts_load_dic = {}

        # Set self.lsf_info_dic for how to get LSF information.
        self.lsf_info_dic = {'bhosts': {'exec_cmd': 'self.bhosts_dic = common_lsf.get_bhosts_info()', 'update_second': 0},
                             'lsload': {'exec_cmd': 'self.lsload_dic = common_lsf.get_lsload_info()', 'update_second': 0},
                             'bqueues': {'exec_cmd': 'self.bqueues_dic = common_lsf.get_bqueues_info()', 'update_second': 0},
                             'busers': {'exec_cmd': 'self.busers_dic = common_lsf.get_busers_info()', 'update_second': 0},
                             'lshosts': {'exec_cmd': 'self.lshosts_dic = common_lsf.get_lshosts_info()', 'update_second': 0},
                             'queue_host': {'exec_cmd': 'self.queue_host_dic = common_lsf.get_queue_host_info()', 'update_second': 0},
                             'host_queue': {'exec_cmd': 'self.host_queue_dic = common_lsf.get_host_queue_info()', 'update_second': 0},
                             'bhosts_load': {'exec_cmd': 'self.bhosts_load_dic = common_lsf.get_bhosts_load_info()', 'update_second': 0}}

        # Just update specified_job info if specified_job argument is specified.
        if self.specified_job:
            self.disable_license = True
            current_second = int(time.time())

            for item in self.lsf_info_dic.keys():
                self.lsf_info_dic[item]['update_second'] = current_second

        # Init license information.
        self.license_dic = {}
        self.license_dic_second = 0

        # USERS/UTILIZATION/LICENSE页面懒加载标记
        self.users_loaded = False
        self.utilization_loaded = False
        self.license_loaded = False

        # AI helpdesk thread.
        self.ai_thread = None

        # Generate GUI.
        self.init_ui()

        # Switch tab.
        self.switch_tab(specified_tab)

        common.bprint('lsfMonitor is ready.', date_format='%Y-%m-%d %H:%M:%S')
        print('')

    def check_cluster_info(self):
        """
        Make sure LSF/Volclava/Openlava environment exists.
        """
        if ('LSFMONITOR_FAKE_RUN' in os.environ) and (os.environ['LSFMONITOR_FAKE_RUN'] == 'True'):
            (tool, tool_version, cluster, master) = ('LSF', '10.1.0.12', 'FAKE_CLUSTER', 'fake-lsf-main-m1')
        else:
            (tool, tool_version, cluster, master) = common_lsf.get_lsid_info()

        if tool == '':
            common.bprint('Not find any LSF/Volclava/Openlava environment!', date_format='%Y-%m-%d %H:%M:%S', level='Error')
            sys.exit(1)

        common.bprint(f'{tool} ({tool_version})', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint(f'My cluster name is "{cluster}"', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint(f'My master  name is "{master}"', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint('', date_format='%Y-%m-%d %H:%M:%S')

        return tool, cluster

    def fresh_lsf_info(self, lsf_info):
        """
        Get LSF information with functions on common_lsf.
        If the information is updated in 30 seconds, will not update it again.
        """
        if lsf_info in self.lsf_info_dic:
            current_second = int(time.time())

            if current_second - self.lsf_info_dic[lsf_info]['update_second'] > 30:
                common.bprint(f'Loading LSF {lsf_info} information ...', date_format='%Y-%m-%d %H:%M:%S')
                my_show_message = ShowMessage('Info', f'Loading LSF {lsf_info} information ...')
                my_show_message.start()

                exec(self.lsf_info_dic[lsf_info]['exec_cmd'])
                self.lsf_info_dic[lsf_info]['update_second'] = current_second

                time.sleep(0.01)
                my_show_message.terminate()

    def get_license_dic(self):
        if self.disable_license:
            return

        if ('all' not in self.license_administrator_list) and ('ALL' not in self.license_administrator_list) and (USER not in self.license_administrator_list):
            return

        # Setup default license update waiting time.
        license_update_waiting_time = 300

        if ('LICENSE_UPDATE_WAITING_TIME' in os.environ) and re.match(r'^\d+$', os.environ['LICENSE_UPDATE_WAITING_TIME']):
            license_update_waiting_time = int(os.environ['LICENSE_UPDATE_WAITING_TIME'])

        # Not update license_dic repeatedly in license_update_waiting_time seconds.
        current_second = int(time.time())

        if current_second - self.license_dic_second <= license_update_waiting_time:
            common.bprint(f'Will not get license information repeatedly in {license_update_waiting_time} seconds.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            return

        self.license_dic_second = current_second

        # Print loading license message.
        common.bprint('Loading License information ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading license information ...')
        my_show_message.start()

        # Get self.license_dic.
        if ('LM_LICENSE_FILE' in os.environ) and os.environ['LM_LICENSE_FILE']:
            excluded_license_server_list = []

            if config.excluded_license_servers:
                excluded_license_server_list = config.excluded_license_servers.split()

            if config.lmstat_path:
                my_get_license_info = common_license.GetLicenseInfo(excluded_servers=excluded_license_server_list, lmstat_path=config.lmstat_path, bsub_command=config.lmstat_bsub_command)
            else:
                my_get_license_info = common_license.GetLicenseInfo(excluded_servers=excluded_license_server_list, bsub_command=config.lmstat_bsub_command)

            self.license_dic = my_get_license_info.get_license_info()

        time.sleep(0.01)
        my_show_message.terminate()

        if not self.license_dic:
            common.bprint('Not find any valid license information.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

    def init_ui(self):
        """
        Main process, draw the main graphic frame.
        """
        # Add menubar.
        self.gen_menubar()

        # Define main Tab widget
        self.main_tab = QTabWidget(self)
        self.setCentralWidget(self.main_tab)

        # Define sub-tabs
        self.job_tab = QWidget()
        self.jobs_tab = QWidget()
        self.hosts_tab = QWidget()
        self.load_tab = QWidget()
        self.users_tab = QWidget()
        self.queues_tab = QWidget()
        self.utilization_tab = QWidget()
        self.license_tab = QWidget()
        self.ai_tab = QWidget()

        # Add the sub-tabs into main Tab widget
        self.main_tab.addTab(self.job_tab, 'JOB')
        self.main_tab.addTab(self.jobs_tab, 'JOBS')
        self.main_tab.addTab(self.hosts_tab, 'HOSTS')
        self.main_tab.addTab(self.load_tab, 'LOAD')
        self.main_tab.addTab(self.users_tab, 'USERS')
        self.main_tab.addTab(self.queues_tab, 'QUEUES')
        self.main_tab.addTab(self.utilization_tab, 'UTILIZATION')

        if ('all' in self.license_administrator_list) or ('ALL' in self.license_administrator_list) or (USER in self.license_administrator_list):
            self.main_tab.addTab(self.license_tab, 'LICENSE')

        self.main_tab.addTab(self.ai_tab, 'AI')

        # Generate the sub-tabs
        common.bprint('Generating JOB tab ...', date_format='%Y-%m-%d %H:%M:%S')
        self.gen_job_tab()

        if not self.specified_job:
            common.bprint('Generating JOBS tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_jobs_tab()
            common.bprint('Generating HOSTS tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_hosts_tab()
            common.bprint('Generating LOAD tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_load_tab()
            common.bprint('Generating USERS tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_users_tab()
            common.bprint('Generating QUEUES tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_queues_tab()
            common.bprint('Generating UTILIZATION tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_utilization_tab()
            common.bprint('Generating LICENSE tab ...', date_format='%Y-%m-%d %H:%M:%S')
            self.gen_license_tab()

        common.bprint('Generating AI tab ...', date_format='%Y-%m-%d %H:%M:%S')
        self.gen_ai_tab()

        # Show main window
        common.bprint('Initializing main window ...', date_format='%Y-%m-%d %H:%M:%S')
        common_pyqt5.auto_resize(self, 1300, 610)
        self.setWindowTitle('lsfMonitor ' + str(VERSION) + '    (' + str(self.tool) + ' - ' + str(self.cluster) + ')')
        self.setWindowIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/monitor.ico'))
        common_pyqt5.center_window(self)

        if self.dark_mode:
            self.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())

    def switch_tab(self, specified_tab):
        """
        Switch to the specified Tab.
        """
        tab_dic = {'JOB': self.job_tab,
                   'JOBS': self.jobs_tab,
                   'HOSTS': self.hosts_tab,
                   'LOAD': self.load_tab,
                   'USERS': self.users_tab,
                   'QUEUES': self.queues_tab,
                   'UTILIZATION': self.utilization_tab,
                   'LICENSE': self.license_tab,
                   'AI': self.ai_tab}

        # 切换到USERS页面时，如果还没加载过用户信息，就加载
        if specified_tab == 'USERS' and not self.users_loaded:
            self.gen_users_tab_table()
            self.users_loaded = True

        # 切换到UTILIZATION页面时，如果还没加载过利用率信息，就加载
        if specified_tab == 'UTILIZATION' and not self.utilization_loaded:
            self.update_utilization_tab_info()
            self.utilization_loaded = True

        # 切换到LICENSE页面时，如果还没加载过License信息，就加载
        if specified_tab == 'LICENSE':
            is_license_admin = ('all' in self.license_administrator_list) or ('ALL' in self.license_administrator_list) or (USER in self.license_administrator_list)

            if not is_license_admin:
                common.bprint('Current user is not a license administrator, cannot switch to LICENSE tab.', level='Warning')
                return

            if not self.license_loaded:
                self.get_license_dic()
                self.license_loaded = True
                self.update_license_tab_feature_completer()

        self.main_tab.setCurrentWidget(tab_dic[specified_tab])

    def gen_menubar(self):
        """
        Generate menubar.
        """
        menubar = self.menuBar()

        # File
        export_jobs_table_action = QAction('Export jobs table', self)
        export_jobs_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_jobs_table_action.triggered.connect(self.export_jobs_table)

        export_hosts_table_action = QAction('Export hosts table', self)
        export_hosts_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_hosts_table_action.triggered.connect(self.export_hosts_table)

        export_users_table_action = QAction('Export users table', self)
        export_users_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_users_table_action.triggered.connect(self.export_users_table)

        export_queues_table_action = QAction('Export queues table', self)
        export_queues_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_queues_table_action.triggered.connect(self.export_queues_table)

        export_utilization_table_action = QAction('Export utilization table', self)
        export_utilization_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_utilization_table_action.triggered.connect(self.export_utilization_table)

        export_license_feature_table_action = QAction('Export license feature table', self)
        export_license_feature_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_license_feature_table_action.triggered.connect(self.export_license_feature_table)

        export_license_expires_table_action = QAction('Export license expires table', self)
        export_license_expires_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_license_expires_table_action.triggered.connect(self.export_license_expires_table)

        exit_action = QAction('Exit', self)
        exit_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/exit.png'))
        exit_action.triggered.connect(qApp.quit)

        file_menu = menubar.addMenu('File')
        file_menu.addAction(export_jobs_table_action)
        file_menu.addAction(export_hosts_table_action)
        file_menu.addAction(export_users_table_action)
        file_menu.addAction(export_queues_table_action)
        file_menu.addAction(export_utilization_table_action)
        file_menu.addAction(export_license_feature_table_action)
        file_menu.addAction(export_license_expires_table_action)
        file_menu.addAction(exit_action)

        # Setup
        enable_queue_detail_action = QAction('Enable queue detail', self, checkable=True)
        enable_queue_detail_action.triggered.connect(self.func_enable_queue_detail)

        enable_utilization_detail_action = QAction('Enable utilization detail', self, checkable=True)
        enable_utilization_detail_action.triggered.connect(self.func_enable_utilization_detail)

        setup_menu = menubar.addMenu('Setup')
        setup_menu.addAction(enable_queue_detail_action)
        setup_menu.addAction(enable_utilization_detail_action)

        # Function
        check_pend_reason_action = QAction('Check Pend reason', self)
        check_pend_reason_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/pend.png'))
        check_pend_reason_action.triggered.connect(self.check_pend_reason)
        check_slow_reason_action = QAction('Check Slow reason', self)
        check_slow_reason_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/slow.png'))
        check_slow_reason_action.triggered.connect(self.check_slow_reason)
        check_fail_reason_action = QAction('Check Fail reason', self)
        check_fail_reason_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/fail.png'))
        check_fail_reason_action.triggered.connect(self.check_fail_reason)

        function_menu = menubar.addMenu('Function')
        function_menu.addAction(check_pend_reason_action)
        function_menu.addAction(check_slow_reason_action)
        function_menu.addAction(check_fail_reason_action)

        # AI
        ai_record_search_action = QAction('Record Search', self)
        ai_record_search_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/magnifier.png'))
        ai_record_search_action.triggered.connect(self.ai_record_search)

        ai_problem_analysis_action = QAction('Problem Analysis', self)
        ai_problem_analysis_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/trace.png'))
        ai_problem_analysis_action.triggered.connect(self.ai_problem_analysis)

        ai_cluster_analysis_action = QAction('Cluster Analysis', self)
        ai_cluster_analysis_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/trace.png'))
        ai_cluster_analysis_action.triggered.connect(self.ai_cluster_analysis)

        ai_record_cleanup_action = QAction('Record Cleanup', self)
        ai_record_cleanup_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/close.png'))
        ai_record_cleanup_action.triggered.connect(self.ai_record_cleanup)

        self.ai_debug_action = QAction('Debug', self)
        self.ai_debug_action.setCheckable(True)
        self.ai_debug_action.setChecked(False)

        ai_menu = menubar.addMenu('AI')
        ai_menu.addAction(ai_record_search_action)
        ai_menu.addAction(ai_problem_analysis_action)
        ai_menu.addAction(ai_record_cleanup_action)
        ai_menu.addSeparator()
        ai_menu.addAction(ai_cluster_analysis_action)
        ai_menu.addSeparator()
        ai_menu.addAction(self.ai_debug_action)

        # Help
        version_action = QAction('Version', self)
        version_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/version.png'))
        version_action.triggered.connect(self.show_version)

        about_action = QAction('About lsfMonitor', self)
        about_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/about.png'))
        about_action.triggered.connect(self.show_about)

        help_menu = menubar.addMenu('Help')
        help_menu.addAction(version_action)
        help_menu.addAction(about_action)

    def func_enable_queue_detail(self, state):
        """
        Show detail information for RUN/PEND curve on QUEUE tab.
        """
        if state:
            self.enable_queue_detail = True
            self.queues_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-7))
        else:
            self.enable_queue_detail = False
            self.queues_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

    def func_enable_utilization_detail(self, state):
        """
        Show detail information for utilization curve on UTILIZATION tab.
        """
        if state:
            self.enable_utilization_detail = True
            self.utilization_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-7))
        else:
            self.enable_utilization_detail = False
            self.utilization_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

    def check_pend_reason(self, job=''):
        """
        Call a separate script to check job pend reason.
        """
        self.my_check_issue_reason = CheckIssueReason(job=job, issue='PEND')
        self.my_check_issue_reason.start()

    def check_slow_reason(self, job=''):
        """
        Call a separate script to check job slow reason.
        """
        self.my_check_issue_reason = CheckIssueReason(job=job, issue='SLOW')
        self.my_check_issue_reason.start()

    def check_fail_reason(self, job=''):
        """
        Call a separate script to check job fail reason.
        """
        self.my_check_issue_reason = CheckIssueReason(job=job, issue='FAIL')
        self.my_check_issue_reason.start()

    def show_version(self):
        """
        Show lsfMonitor version information.
        """
        QMessageBox.about(self, 'lsfMonitor', 'Version: ' + str(VERSION) + ' (' + str(VERSION_DATE) + ')')

    def show_about(self):
        """
        Show lsfMonitor about information.
        """
        about_message = """
Thanks for downloading lsfMonitor.

lsfMonitor is an open source software for LSF information data-collection, data-analysis and data-display.

Please contact with liyanqing1987@163.com with any question."""

        QMessageBox.about(self, 'lsfMonitor', about_message)

# Common sub-functions (begin) #
    def gui_warning(self, warning_message):
        """
        Show the specified warning message on both of command line and GUI window.
        """
        common.bprint(warning_message, date_format='%Y-%m-%d %H:%M:%S', level='Warning')
        QMessageBox.warning(self, 'lsfMonitor Warning', warning_message)
# Common sub-functions (end) #

# For job TAB (begin) #
    def gen_job_tab(self):
        """
        Generate the job tab on lsfMonitor GUI, show job informations.
        """
        # Init var
        self.job_tab_current_job = ''
        self.job_tab_current_job_dic = {}

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

        job_tab_grid.setColumnMinimumWidth(0, 250)

        self.job_tab.setLayout(job_tab_grid)

        # Generate sub-frames
        self.gen_job_tab_frame0()
        self.gen_job_tab_frame1()
        self.gen_job_tab_frame2()
        self.gen_job_tab_frame3()

        if self.specified_job:
            self.job_tab_job_line.setText(str(self.specified_job))
            self.check_job_on_job_tab()

    def gen_job_tab_frame0(self):
        # self.job_tab_frame0
        # "Job" item.
        job_tab_job_label = QLabel(self.job_tab_frame0)
        job_tab_job_label.setStyleSheet("font-weight: bold;")
        job_tab_job_label.setText('Job')

        self.job_tab_job_line = QLineEdit()
        self.job_tab_job_line.returnPressed.connect(self.check_job_on_job_tab)

        # "Check" button.
        job_tab_check_button = QPushButton('Check', self.job_tab_frame0)
        job_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        job_tab_check_button.clicked.connect(self.check_job_on_job_tab)

        # "Kill" button.
        job_tab_kill_button = QPushButton('Kill', self.job_tab_frame0)
        job_tab_kill_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        job_tab_kill_button.clicked.connect(self.kill_job_on_job_tab)

        # "Trace" button.
        job_tab_trace_button = QPushButton('Trace', self.job_tab_frame0)
        job_tab_trace_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        job_tab_trace_button.clicked.connect(lambda: self.trace_job())

        # self.job_tab_frame0 - Grid
        job_tab_frame0_grid = QGridLayout()

        job_tab_frame0_grid.addWidget(job_tab_job_label, 0, 0)
        job_tab_frame0_grid.addWidget(self.job_tab_job_line, 0, 1, 1, 2)
        job_tab_frame0_grid.addWidget(job_tab_check_button, 1, 0)
        job_tab_frame0_grid.addWidget(job_tab_kill_button, 1, 1)
        job_tab_frame0_grid.addWidget(job_tab_trace_button, 1, 2)

        self.job_tab_frame0.setLayout(job_tab_frame0_grid)

    def gen_job_tab_frame1(self):
        # self.job_tab_frame1
        # "Status" item.
        job_tab_status_label = QLabel('Status', self.job_tab_frame1)
        job_tab_status_label.setStyleSheet("font-weight: bold;")

        self.job_tab_status_line = QLineEdit()

        # "User" item.
        job_tab_user_label = QLabel('User', self.job_tab_frame1)
        job_tab_user_label.setStyleSheet("font-weight: bold;")

        self.job_tab_user_line = QLineEdit()

        # "Project" item.
        job_tab_project_label = QLabel('Project', self.job_tab_frame1)
        job_tab_project_label.setStyleSheet("font-weight: bold;")

        self.job_tab_project_line = QLineEdit()

        # "Queue" item.
        job_tab_queue_label = QLabel('Queue', self.job_tab_frame1)
        job_tab_queue_label.setStyleSheet("font-weight: bold;")

        self.job_tab_queue_line = QLineEdit()

        # "Host" item.
        job_tab_started_on_label = QLabel('Host', self.job_tab_frame1)
        job_tab_started_on_label.setStyleSheet("font-weight: bold;")

        self.job_tab_started_on_line = QLineEdit()
        self.job_tab_started_on_line.returnPressed.connect(self.job_tab_host_click)

        # "Start Time" item.
        job_tab_started_time_label = QLabel('Start Time', self.job_tab_frame1)
        job_tab_started_time_label.setStyleSheet("font-weight: bold;")

        self.job_tab_started_time_line = QLineEdit()

        # "Finish Time" item.
        job_tab_finished_time_label = QLabel('Finish Time', self.job_tab_frame1)
        job_tab_finished_time_label.setStyleSheet("font-weight: bold;")

        self.job_tab_finished_time_line = QLineEdit()

        # "Processors" item.
        job_tab_processors_requested_label = QLabel('Processors', self.job_tab_frame1)
        job_tab_processors_requested_label.setStyleSheet("font-weight: bold;")

        self.job_tab_processors_requested_line = QLineEdit()

        # "IDLE_FACTOR" item.
        job_tab_idle_factor_label = QLabel('IDLE_FACTOR', self.job_tab_frame1)
        job_tab_idle_factor_label.setStyleSheet("font-weight: bold;")

        self.job_tab_idle_factor_line = QLineEdit()

        # "Rusage" item.
        job_tab_rusage_mem_label = QLabel('Rusage', self.job_tab_frame1)
        job_tab_rusage_mem_label.setStyleSheet("font-weight: bold;")

        self.job_tab_rusage_mem_line = QLineEdit()

        # "Mem (now)" item.
        job_tab_mem_label = QLabel('Mem (now)', self.job_tab_frame1)
        job_tab_mem_label.setStyleSheet("font-weight: bold;")

        self.job_tab_mem_line = QLineEdit()

        # "Mem (max)" item.
        job_tab_max_mem_label = QLabel('Mem (max)', self.job_tab_frame1)
        job_tab_max_mem_label.setStyleSheet("font-weight: bold;")

        self.job_tab_max_mem_line = QLineEdit()

        # self.job_tab_frame1 - Grid
        job_tab_frame1_grid = QGridLayout()

        job_tab_frame1_grid.addWidget(job_tab_status_label, 0, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_status_line, 0, 1)
        job_tab_frame1_grid.addWidget(job_tab_user_label, 1, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_user_line, 1, 1)
        job_tab_frame1_grid.addWidget(job_tab_project_label, 2, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_project_line, 2, 1)
        job_tab_frame1_grid.addWidget(job_tab_queue_label, 3, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_queue_line, 3, 1)
        job_tab_frame1_grid.addWidget(job_tab_started_on_label, 4, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_started_on_line, 4, 1)
        job_tab_frame1_grid.addWidget(job_tab_started_time_label, 5, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_started_time_line, 5, 1)
        job_tab_frame1_grid.addWidget(job_tab_finished_time_label, 6, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_finished_time_line, 6, 1)
        job_tab_frame1_grid.addWidget(job_tab_processors_requested_label, 7, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_processors_requested_line, 7, 1)
        job_tab_frame1_grid.addWidget(job_tab_idle_factor_label, 8, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_idle_factor_line, 8, 1)
        job_tab_frame1_grid.addWidget(job_tab_rusage_mem_label, 9, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_rusage_mem_line, 9, 1)
        job_tab_frame1_grid.addWidget(job_tab_mem_label, 10, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_mem_line, 10, 1)
        job_tab_frame1_grid.addWidget(job_tab_max_mem_label, 11, 0)
        job_tab_frame1_grid.addWidget(self.job_tab_max_mem_line, 11, 1)

        self.job_tab_frame1.setLayout(job_tab_frame1_grid)

    def job_tab_host_click(self):
        """
        Jump to LOAD tab when clicking Host line on self.job_tab.
        """
        job_started_on = self.job_tab_started_on_line.text().strip()

        if job_started_on:
            # Re-set self.load_tab_host_line.
            self.load_tab_host_line.setText(job_started_on)

            # Re-set self.load_tab_begin_date_edit.
            job_start_time = self.job_tab_started_time_line.text().strip()

            if job_start_time:
                job_start_time = common_lsf.switch_bjobs_uf_time(job_start_time, '%Y%m%d')
                self.load_tab_begin_date_edit.setDate(QDate.fromString(job_start_time, 'yyyyMMdd'))

            # Re-set self.load_tab_end_date_edit.
            job_finish_time = self.job_tab_finished_time_line.text().strip()

            if job_finish_time:
                job_finish_time = common_lsf.switch_bjobs_uf_time(job_finish_time, '%Y%m%d')
                self.load_tab_end_date_edit.setDate(QDate.fromString(job_finish_time, 'yyyyMMdd'))
            else:
                self.load_tab_end_date_edit.setDate(QDate.currentDate())

            # Switch to LOAD tab.
            self.update_load_tab_load_info()
            self.main_tab.setCurrentWidget(self.load_tab)

    def gen_job_tab_frame2(self):
        # self.job_tab_frame2
        self.job_tab_job_info_text = QTextEdit(self.job_tab_frame2)

        # self.job_tab_frame2 - Grid
        job_tab_frame2_grid = QGridLayout()
        job_tab_frame2_grid.addWidget(self.job_tab_job_info_text, 0, 0)
        self.job_tab_frame2.setLayout(job_tab_frame2_grid)

    def gen_job_tab_frame3(self):
        # self.job_tab_frame3
        self.job_tab_chart_tab = QTabWidget(self.job_tab_frame3)

        # MEMORY tab
        job_tab_mem_widget = QWidget()
        self.job_tab_mem_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.job_tab_mem_toolbar = common_pyqt5.NavigationToolbar2QT(self.job_tab_mem_canvas, self, x_is_date=False)

        if self.dark_mode:
            self.job_tab_mem_canvas.figure.set_facecolor('#19232d')

        job_tab_mem_layout = QVBoxLayout()
        job_tab_mem_layout.addWidget(self.job_tab_mem_toolbar)
        job_tab_mem_layout.addWidget(self.job_tab_mem_canvas)
        job_tab_mem_widget.setLayout(job_tab_mem_layout)

        # IDLE_FACTOR tab
        job_tab_idle_factor_widget = QWidget()
        self.job_tab_idle_factor_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.job_tab_idle_factor_toolbar = common_pyqt5.NavigationToolbar2QT(self.job_tab_idle_factor_canvas, self, x_is_date=False)

        if self.dark_mode:
            self.job_tab_idle_factor_canvas.figure.set_facecolor('#19232d')

        job_tab_idle_factor_layout = QVBoxLayout()
        job_tab_idle_factor_layout.addWidget(self.job_tab_idle_factor_toolbar)
        job_tab_idle_factor_layout.addWidget(self.job_tab_idle_factor_canvas)
        job_tab_idle_factor_widget.setLayout(job_tab_idle_factor_layout)

        # Add tabs
        self.job_tab_chart_tab.addTab(job_tab_mem_widget, 'MEMORY')
        self.job_tab_chart_tab.addTab(job_tab_idle_factor_widget, 'IDLE_FACTOR')

        # self.job_tab_frame3 - Grid
        job_tab_frame3_grid = QGridLayout()
        job_tab_frame3_grid.addWidget(self.job_tab_chart_tab, 0, 0)
        self.job_tab_frame3.setLayout(job_tab_frame3_grid)

    def check_job_on_job_tab(self):
        """
        Get job information with "bjobs -UF <job_id>", save the infomation into dict self.job_tab_current_job_dic.
        Update self.job_tab_frame1 and self.job_tab_frame3.
        """
        # Initicalization JOB tab.
        self.update_job_tab_frame1(init=True)
        self.update_job_tab_frame2(init=True)
        self.update_job_tab_frame3(init=True)

        # Get real jobid and check it.
        self.job_tab_current_job = self.job_tab_job_line.text().strip()
        my_match = re.match(r'^(\d+)(\[\d+\])?$', self.job_tab_current_job)

        if not my_match:
            warning_message = 'No valid job is specified on JOB tab.'
            self.gui_warning(warning_message)
            return

        current_job = my_match.group(1)

        common.bprint(f'Checking job "{current_job}".', date_format='%Y-%m-%d %H:%M:%S')

        # Get job info
        common.bprint(f'Getting LSF job information for "{current_job}" ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', f'Getting LSF job information for "{current_job}" ...')
        my_show_message.start()

        self.job_tab_current_job_dic = common_lsf.get_bjobs_uf_info(command='bjobs -UF ' + str(current_job))

        if not self.job_tab_current_job_dic:
            job_db_path = str(self.cluster_db_path) + '/job'

            if os.path.exists(job_db_path):
                select_condition = 'WHERE job="' + str(current_job) + '"'
                job_finished_date_db_list = list(os.listdir(job_db_path))

                for job_finished_date_db in job_finished_date_db_list[::-1]:
                    job_finished_date_db = str(job_db_path) + '/' + str(job_finished_date_db)

                    if os.path.exists(job_finished_date_db):
                        common.bprint(f'Searching for "{job_finished_date_db}" ...', indent=4, date_format='%Y-%m-%d %H:%M:%S')
                        (job_finished_date_db_connect_result, job_finished_date_db_conn) = common_sqlite3.connect_db_file(job_finished_date_db)

                        if job_finished_date_db_connect_result == 'failed':
                            common.bprint(f'Failed on connecting job database file "{job_finished_date_db}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                        else:
                            finished_job_list = common_sqlite3.get_sql_table_key_list(job_finished_date_db, job_finished_date_db_conn, 'job', 'job')

                            if current_job in finished_job_list:
                                job_tab_current_job_dic = common_sqlite3.get_sql_table_data(job_finished_date_db, job_finished_date_db_conn, 'job', ['job', 'job_name', 'job_description', 'user', 'project', 'status', 'interactive_mode', 'queue', 'command', 'submitted_from', 'submitted_time', 'cwd', 'processors_requested', 'requested_resources', 'span_hosts', 'rusage_mem', 'specified_hosts', 'started_on', 'started_time', 'finished_time', 'exit_code', 'term_signal', 'cpu_time', 'idle_factor', 'mem', 'swap', 'run_limit', 'pids', 'max_mem', 'avg_mem', 'pending_reasons', 'job_info'], select_condition)

                                if job_tab_current_job_dic:
                                    self.job_tab_current_job_dic[current_job] = {}

                                    for key in job_tab_current_job_dic:
                                        self.job_tab_current_job_dic[current_job][key] = job_tab_current_job_dic[key][0]

                                job_finished_date_db_conn.close()
                                break

                            job_finished_date_db_conn.close()

        if not self.job_tab_current_job_dic:
            warning_message = 'Not find job information for job "' + str(current_job) + '" on JOB tab.'
            self.gui_warning(warning_message)
            return

        # Update JOB tab with latest job info.
        self.update_job_tab_frame1()
        self.update_job_tab_frame2()
        self.update_job_tab_frame3()

        time.sleep(0.01)
        my_show_message.terminate()

    def kill_job_on_job_tab(self):
        """
        Kill job, update self.job_tab.
        """
        if self.job_tab_current_job:
            return_code = self.kill_job(self.job_tab_current_job)

            if return_code == 0:
                self.check_job_on_job_tab()

    def kill_job(self, jobid=None):
        """
        Kill job with "bkill".
        """
        if jobid:
            common.bprint(f'Kill job "{jobid}".', date_format='%Y-%m-%d %H:%M:%S')

            command = 'bkill ' + str(jobid)
            (return_code, stdout, stderr) = common.run_command(command)

            if return_code == 0:
                common.bprint(f'Kill {jobid} successfully!', date_format='%Y-%m-%d %H:%M:%S')
                my_show_message = ShowMessage('Info', f'Kill {jobid} successfully!')
                my_show_message.start()
                time.sleep(5)
                my_show_message.terminate()
            else:
                common.bprint(f'Failed on killing {jobid}.', date_format='%Y-%m-%d %H:%M:%S')
                common.bprint(str(stderr, 'utf-8').strip(), date_format='%Y-%m-%d %H:%M:%S')
                my_show_message = ShowMessage(f'Kill {jobid} fail', str(str(stderr, 'utf-8')).strip())
                my_show_message.run()

            return return_code

        return -1

    def trace_job(self, jobid='', job_status=''):
        """
        Trace job pend/slow/fail reason.
        """
        if not jobid:
            jobid = self.job_tab_current_job

        if not jobid:
            common.bprint('No job is selected, cannot trace job.', level='Warning')
            return

        if not job_status:
            job_status = self.job_tab_current_job_dic.get(jobid, {}).get('status', '')

        if jobid:
            if job_status == 'PEND':
                self.check_pend_reason(job=jobid)
            elif job_status == 'RUN':
                self.check_slow_reason(job=jobid)
            elif (job_status == 'DONE') or (job_status == 'EXIT'):
                self.check_fail_reason(job=jobid)

    def _fill_line_edit(self, line_edit, value, init):
        if init:
            line_edit.setText('')
        else:
            line_edit.setText(str(value))
            line_edit.setCursorPosition(0)

    def _fill_mem_line_edit(self, line_edit, mem_mb, init):
        if init:
            line_edit.setText('')
        elif mem_mb != '':
            line_edit.setText(str(round(float(mem_mb) / 1024, 1)) + ' G')
            line_edit.setCursorPosition(0)

    def update_job_tab_frame1(self, init=False):
        """
        Update self.job_tab_frame1 with job infos.
        """
        job_dic = self.job_tab_current_job_dic.get(self.job_tab_current_job, {}) if not init else {}

        self._fill_line_edit(self.job_tab_status_line, job_dic.get('status', ''), init)
        self._fill_line_edit(self.job_tab_user_line, job_dic.get('user', ''), init)
        self._fill_line_edit(self.job_tab_project_line, job_dic.get('project', ''), init)
        self._fill_line_edit(self.job_tab_queue_line, job_dic.get('queue', ''), init)
        self._fill_line_edit(self.job_tab_started_on_line, job_dic.get('started_on', ''), init)
        self._fill_line_edit(self.job_tab_started_time_line, job_dic.get('started_time', ''), init)
        self._fill_line_edit(self.job_tab_finished_time_line, job_dic.get('finished_time', ''), init)
        self._fill_line_edit(self.job_tab_processors_requested_line, job_dic.get('processors_requested', ''), init)

        if init:
            self.job_tab_idle_factor_line.setText('')
        else:
            idle_value = ''
            idle_factor = job_dic.get('idle_factor', '')
            if idle_factor != '':
                idle_value = str(round(float(idle_factor), 2))
            self.job_tab_idle_factor_line.setText(idle_value)
            self.job_tab_idle_factor_line.setCursorPosition(0)

        self._fill_mem_line_edit(self.job_tab_rusage_mem_line, job_dic.get('rusage_mem', ''), init)
        self._fill_mem_line_edit(self.job_tab_mem_line, job_dic.get('mem', ''), init)
        self._fill_mem_line_edit(self.job_tab_max_mem_line, job_dic.get('max_mem', ''), init)

    def update_job_tab_frame2(self, init=False):
        """
        Show job detailed description info on self.job_tab_frame2/self.job_tab_job_info_text.
        """
        self.job_tab_job_info_text.clear()

        if not init:
            self.job_tab_job_info_text.insertPlainText(self.job_tab_current_job_dic[self.job_tab_current_job]['job_info'])
            common_pyqt5.text_edit_visible_position(self.job_tab_job_info_text, 'Start')

    def get_job_mem_list(self):
        """
        Get job sample-time mem list for self.job_tab_current_job.
        Try new job_data/ format first, fall back to old job_mem/ format.
        """
        runtime_list = []
        real_mem_list = []

        # Try new format (job_data/ single-table schema, 1M range) first.
        job_range_dic = common.get_job_range_dic([self.job_tab_current_job, ], range_size=1000000)
        job_range = list(job_range_dic.keys())[0]
        job_data_db_file = str(self.cluster_db_path) + '/job_data/' + str(job_range) + '.db'

        if os.path.exists(job_data_db_file):
            (connect_result, db_conn) = common_sqlite3.connect_db_file(job_data_db_file)

            if connect_result == 'passed':
                try:
                    curs = db_conn.cursor()
                    curs.execute("SELECT sample_time, mem FROM job_data WHERE job_id=? ORDER BY sample_second", (str(self.job_tab_current_job),))
                    rows = curs.fetchall()
                    curs.close()
                    db_conn.close()

                    if rows:
                        first_sample_time = datetime.datetime.strptime(str(rows[0][0]), '%Y%m%d_%H%M%S').timestamp()

                        for (sample_time, mem) in rows:
                            current_time = datetime.datetime.strptime(str(sample_time), '%Y%m%d_%H%M%S').timestamp()
                            runtime = int((current_time - first_sample_time) / 60)
                            runtime_list.append(runtime)

                            if mem == '' or mem is None:
                                mem = '0'

                            real_mem = round(float(mem) / 1024, 1)
                            real_mem_list.append(real_mem)

                        return runtime_list, real_mem_list
                except Exception:
                    db_conn.close()

        # Fall back to old format (job_mem/ per-job tables, 100K range).
        job_range_dic_old = common.get_job_range_dic([self.job_tab_current_job, ])
        job_range_old = list(job_range_dic_old.keys())[0]
        job_mem_db_file = str(self.cluster_db_path) + '/job_mem/' + str(job_range_old) + '.db'

        if not os.path.exists(job_mem_db_file):
            common.bprint(f'Job memory usage information is missing for "{self.job_tab_current_job}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
        else:
            (job_mem_db_file_connect_result, job_mem_db_conn) = common_sqlite3.connect_db_file(job_mem_db_file)

            if job_mem_db_file_connect_result == 'failed':
                common.bprint(f'Failed on connecting job database file "{job_mem_db_file}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            else:
                table_name = 'job_' + str(self.job_tab_current_job)
                data_dic = common_sqlite3.get_sql_table_data(job_mem_db_file, job_mem_db_conn, table_name, ['sample_time', 'mem'])

                if not data_dic:
                    common.bprint(f'Job memory usage information is empty for "{self.job_tab_current_job}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
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

                job_mem_db_conn.close()

        return runtime_list, real_mem_list

    def get_job_idle_factor_list(self):
        """
        Get job sample-time idle_factor list for self.job_tab_current_job.
        Try new job_data/ format first, fall back to old job_idle_factor/ format.
        """
        runtime_list = []
        idle_factor_list = []

        # Try new format (job_data/ single-table schema, 1M range) first.
        job_range_dic = common.get_job_range_dic([self.job_tab_current_job, ], range_size=1000000)
        job_range = list(job_range_dic.keys())[0]
        job_data_db_file = str(self.cluster_db_path) + '/job_data/' + str(job_range) + '.db'

        if os.path.exists(job_data_db_file):
            (connect_result, db_conn) = common_sqlite3.connect_db_file(job_data_db_file)

            if connect_result == 'passed':
                try:
                    curs = db_conn.cursor()
                    curs.execute("SELECT sample_time, idle_factor FROM job_data WHERE job_id=? ORDER BY sample_second", (str(self.job_tab_current_job),))
                    rows = curs.fetchall()
                    curs.close()
                    db_conn.close()

                    if rows:
                        first_sample_time = datetime.datetime.strptime(str(rows[0][0]), '%Y%m%d_%H%M%S').timestamp()

                        for (sample_time, idle_factor) in rows:
                            if idle_factor == '' or idle_factor is None:
                                continue

                            current_time = datetime.datetime.strptime(str(sample_time), '%Y%m%d_%H%M%S').timestamp()
                            runtime = int((current_time - first_sample_time) / 60)
                            runtime_list.append(runtime)
                            idle_factor_list.append(round(float(idle_factor), 2))

                        return runtime_list, idle_factor_list
                except Exception:
                    db_conn.close()

        # Fall back to old format (job_idle_factor/ per-job tables, 100K range).
        job_range_dic_old = common.get_job_range_dic([self.job_tab_current_job, ])
        job_range_old = list(job_range_dic_old.keys())[0]
        job_idle_factor_db_file = str(self.cluster_db_path) + '/job_idle_factor/' + str(job_range_old) + '.db'

        if not os.path.exists(job_idle_factor_db_file):
            return runtime_list, idle_factor_list

        (connect_result, db_conn) = common_sqlite3.connect_db_file(job_idle_factor_db_file)

        if connect_result == 'passed':
            table_name = 'job_' + str(self.job_tab_current_job)
            data_dic = common_sqlite3.get_sql_table_data(job_idle_factor_db_file, db_conn, table_name, ['sample_time', 'idle_factor'])

            if data_dic and 'idle_factor' in data_dic and 'sample_time' in data_dic:
                sample_time_list = data_dic['sample_time']
                raw_idle_factor_list = data_dic['idle_factor']
                first_sample_time = datetime.datetime.strptime(str(sample_time_list[0]), '%Y%m%d_%H%M%S').timestamp()

                for i in range(len(sample_time_list)):
                    idle_factor = raw_idle_factor_list[i]

                    if idle_factor == '' or idle_factor is None:
                        continue

                    sample_time = sample_time_list[i]
                    current_time = datetime.datetime.strptime(str(sample_time), '%Y%m%d_%H%M%S').timestamp()
                    runtime = int((current_time - first_sample_time) / 60)
                    runtime_list.append(runtime)
                    idle_factor_list.append(round(float(idle_factor), 2))

            db_conn.close()

        return runtime_list, idle_factor_list

    def update_job_tab_frame3(self, init=False):
        """
        Draw memory and idle_factor curves for current job on self.job_tab_frame3.
        """
        mem_fig = self.job_tab_mem_canvas.figure
        mem_fig.clear()
        self.job_tab_mem_canvas.draw()

        idle_factor_fig = self.job_tab_idle_factor_canvas.figure
        idle_factor_fig.clear()
        self.job_tab_idle_factor_canvas.draw()

        if not init:
            if self.job_tab_current_job_dic[self.job_tab_current_job]['status'] != 'PEND':
                (runtime_list, mem_list) = self.get_job_mem_list()

                if runtime_list and mem_list:
                    self.draw_job_tab_mem_curve(mem_fig, runtime_list, mem_list)

                (idle_runtime_list, idle_factor_list) = self.get_job_idle_factor_list()

                if idle_runtime_list and idle_factor_list:
                    self.draw_job_tab_idle_factor_curve(idle_factor_fig, idle_runtime_list, idle_factor_list)

    def draw_job_tab_mem_curve(self, fig, runtime_list, mem_list):
        """
        Draw memory curve for specified job.
        """
        fig.subplots_adjust(bottom=0.2)
        axes = fig.add_subplot(111)

        if self.dark_mode:
            axes.set_facecolor('#19232d')

            for spine in axes.spines.values():
                spine.set_color('white')

            axes.tick_params(axis='both', colors='white')
            axes.set_title('memory usage for job "' + str(self.job_tab_current_job) + '"', color='white')
            axes.set_xlabel('Runtime (Minutes)', color='white')
            axes.set_ylabel('Memory Usage (G)', color='white')
        else:
            axes.set_title('memory usage for job "' + str(self.job_tab_current_job) + '"')
            axes.set_xlabel('Runtime (Minutes)')
            axes.set_ylabel('Memory Usage (G)')

        axes.plot(runtime_list, mem_list, 'go-', label='MEM', linewidth=0.1, markersize=0.1)
        axes.fill_between(runtime_list, mem_list, color='green', alpha=0.5)
        axes.legend(loc='upper right')
        axes.grid()
        self.job_tab_mem_canvas.draw()

    def draw_job_tab_idle_factor_curve(self, fig, runtime_list, idle_factor_list):
        """
        Draw idle_factor curve for specified job.
        """
        fig.subplots_adjust(bottom=0.2)
        axes = fig.add_subplot(111)

        if self.dark_mode:
            axes.set_facecolor('#19232d')

            for spine in axes.spines.values():
                spine.set_color('white')

            axes.tick_params(axis='both', colors='white')
            axes.set_title('IDLE_FACTOR(cputime/runtime) for job "' + str(self.job_tab_current_job) + '"', color='white')
            axes.set_xlabel('Runtime (Minutes)', color='white')
            axes.set_ylabel('IDLE_FACTOR', color='white')
        else:
            axes.set_title('IDLE_FACTOR(cputime/runtime) for job "' + str(self.job_tab_current_job) + '"')
            axes.set_xlabel('Runtime (Minutes)')
            axes.set_ylabel('IDLE_FACTOR')

        axes.plot(runtime_list, idle_factor_list, 'bo-', label='IDLE_FACTOR', linewidth=0.1, markersize=0.1)
        axes.fill_between(runtime_list, idle_factor_list, color='blue', alpha=0.3)
        axes.legend(loc='upper right')
        axes.grid()
        self.job_tab_idle_factor_canvas.draw()
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
        self.jobs_tab_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.jobs_tab_table.customContextMenuRequested.connect(self.gen_jobs_tab_menu)

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
        # "Status" item.
        jobs_tab_status_label = QLabel('Status', self.jobs_tab_frame0)
        jobs_tab_status_label.setStyleSheet("font-weight: bold;")
        jobs_tab_status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.jobs_tab_status_combo = common_pyqt5.QComboCheckBox(self.jobs_tab_frame0)
        self.set_jobs_tab_status_combo()

        # "Queue" item.
        jobs_tab_queue_label = QLabel('Queue', self.jobs_tab_frame0)
        jobs_tab_queue_label.setStyleSheet("font-weight: bold;")
        jobs_tab_queue_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.jobs_tab_queue_combo = common_pyqt5.QComboCheckBox(self.jobs_tab_frame0, enableFilter=True)
        self.set_jobs_tab_queue_combo()

        # "Host" item.
        jobs_tab_started_on_label = QLabel('Host', self.jobs_tab_frame0)
        jobs_tab_started_on_label.setStyleSheet("font-weight: bold;")
        jobs_tab_started_on_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.jobs_tab_host_combo = common_pyqt5.QComboCheckBox(self.jobs_tab_frame0, enableFilter=True)
        self.set_jobs_tab_host_combo()

        # "User" item.
        jobs_tab_user_label = QLabel('User', self.jobs_tab_frame0)
        jobs_tab_user_label.setStyleSheet("font-weight: bold;")
        jobs_tab_user_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.jobs_tab_user_line = QLineEdit()
        self.jobs_tab_user_line.returnPressed.connect(self.gen_jobs_tab_table)

        self.fresh_lsf_info('busers')

        if 'USER/GROUP' in self.busers_dic:
            jobs_tab_user_line_completer = common_pyqt5.get_completer(self.busers_dic['USER/GROUP'])
        else:
            jobs_tab_user_line_completer = common_pyqt5.get_completer([])

        self.jobs_tab_user_line.setCompleter(jobs_tab_user_line_completer)

        # "Check" button.
        jobs_tab_check_button = QPushButton('Check', self.jobs_tab_frame0)
        jobs_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        jobs_tab_check_button.clicked.connect(self.gen_jobs_tab_table)

        # self.jobs_tab_frame0 - Grid
        jobs_tab_frame0_grid = QGridLayout()

        jobs_tab_frame0_grid.addWidget(jobs_tab_status_label, 0, 0)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_status_combo, 0, 1)
        jobs_tab_frame0_grid.addWidget(jobs_tab_queue_label, 0, 2)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_queue_combo, 0, 3)
        jobs_tab_frame0_grid.addWidget(jobs_tab_started_on_label, 0, 4)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_host_combo, 0, 5)
        jobs_tab_frame0_grid.addWidget(jobs_tab_user_label, 0, 6)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_user_line, 0, 7)
        jobs_tab_frame0_grid.addWidget(jobs_tab_check_button, 0, 8)

        jobs_tab_frame0_grid.setColumnStretch(0, 1)
        jobs_tab_frame0_grid.setColumnStretch(1, 1)
        jobs_tab_frame0_grid.setColumnStretch(2, 1)
        jobs_tab_frame0_grid.setColumnStretch(3, 1)
        jobs_tab_frame0_grid.setColumnStretch(4, 1)
        jobs_tab_frame0_grid.setColumnStretch(5, 1)
        jobs_tab_frame0_grid.setColumnStretch(6, 1)
        jobs_tab_frame0_grid.setColumnStretch(7, 1)
        jobs_tab_frame0_grid.setColumnStretch(8, 1)

        self.jobs_tab_frame0.setLayout(jobs_tab_frame0_grid)

    def gen_jobs_tab_table(self):
        # self.jobs_tab_table
        self.jobs_tab_table.setShowGrid(True)
        self.jobs_tab_table.setSortingEnabled(False)
        self.jobs_tab_table.setColumnCount(0)
        self.jobs_tab_table.setColumnCount(13)
        self.jobs_tab_table_title_list = ['Job', 'User', 'Status', 'Queue', 'Host', 'Started', 'Project', 'Slot', 'IDLE', 'Rusage (G)', 'Mem (G)', 'MaxMem (G)', 'Command']
        self.jobs_tab_table.setHorizontalHeaderLabels(self.jobs_tab_table_title_list)

        self.jobs_tab_table.setColumnWidth(0, 80)
        self.jobs_tab_table.setColumnWidth(1, 120)
        self.jobs_tab_table.setColumnWidth(2, 65)
        self.jobs_tab_table.setColumnWidth(3, 125)
        self.jobs_tab_table.setColumnWidth(4, 120)
        self.jobs_tab_table.setColumnWidth(5, 150)
        self.jobs_tab_table.setColumnWidth(6, 100)
        self.jobs_tab_table.setColumnWidth(7, 40)
        self.jobs_tab_table.setColumnWidth(8, 50)
        self.jobs_tab_table.setColumnWidth(9, 80)
        self.jobs_tab_table.setColumnWidth(10, 70)
        self.jobs_tab_table.setColumnWidth(11, 100)
        self.jobs_tab_table.horizontalHeader().setSectionResizeMode(12, QHeaderView.Stretch)

        # Get specified user related jobs.
        command = 'bjobs -UF '
        specified_user = self.jobs_tab_user_line.text().strip()

        if re.match(r'^\s*$', specified_user):
            command = str(command) + ' -u all'
        else:
            command = str(command) + ' -u ' + str(specified_user)

        # Get specified queue related jobs.
        specified_queue_list = self.jobs_tab_queue_combo.currentText().strip().split()

        if (len(specified_queue_list) == 1) and (specified_queue_list[0] != 'ALL'):
            command = str(command) + ' -q ' + str(specified_queue_list[0])

        # Get specified status (RUN/PEND/ALL) related jobs.
        specified_status_list = self.jobs_tab_status_combo.currentText().strip().split()

        if (len(specified_status_list) == 1) and (specified_status_list[0] == 'RUN'):
            command = str(command) + ' -r'
        elif (len(specified_status_list) == 1) and (specified_status_list[0] == 'PEND'):
            command = str(command) + ' -p'
        elif (len(specified_status_list) == 1) and (specified_status_list[0] == 'DONE'):
            command = str(command) + ' -d'
        elif (len(specified_status_list) == 1) and (specified_status_list[0] == 'EXIT'):
            command = str(command) + ' -d'
        elif (len(specified_status_list) == 2) and ('DONE' in specified_status_list) and ('EXIT' in specified_status_list):
            command = str(command) + ' -d'
        elif (len(specified_status_list) == 1) and (specified_status_list[0] in ['PSUSP', 'USUSP', 'SSUSP']):
            command = str(command) + ' -s'
        else:
            command = str(command) + ' -a'

        # Get specified host related jobs.
        specified_host_list = self.jobs_tab_host_combo.currentText().strip().split()

        if (len(specified_host_list) == 1) and (specified_host_list[0] != 'ALL'):
            command = str(command) + ' -m ' + str(specified_host_list[0])

        # Run command to get expected jobs information.
        common.bprint('Loading LSF jobs information ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading LSF jobs information ...')
        my_show_message.start()

        job_dic = common_lsf.get_bjobs_uf_info(command)

        time.sleep(0.01)
        my_show_message.terminate()

        # Filter job_dic.
        job_list = list(job_dic.keys())

        for job in job_list:
            if ('ALL' not in specified_status_list) and (job_dic[job]['status'] not in specified_status_list):
                del job_dic[job]
                continue

            if ('ALL' not in specified_queue_list) and (len(specified_queue_list) > 1) and (job_dic[job]['queue'] not in specified_queue_list):
                del job_dic[job]
                continue

            if ('ALL' not in specified_host_list) and (len(specified_host_list) > 1):
                find_host = False
                started_on_list = job_dic[job]['started_on'].strip().split()

                for specified_host in specified_host_list:
                    if specified_host in started_on_list:
                        find_host = True
                        break

                if not find_host:
                    del job_dic[job]
                    continue

        # Fill self.jobs_tab_table items.
        self.jobs_tab_table.setRowCount(0)
        self.jobs_tab_table.setRowCount(len(job_dic.keys()))

        # Don't remove below setting!!!
        job_list = list(job_dic.keys())

        for i in range(len(job_list)):
            # Fill "Job" item.
            job = job_list[i]
            j = 0
            item = QTableWidgetItem(job)
            item.setFont(QFont('song', 9, QFont.Bold))
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "User" item.
            j = j+1
            item = QTableWidgetItem(job_dic[job]['user'])
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Status" item.
            j = j+1
            item = QTableWidgetItem(job_dic[job]['status'])
            item.setFont(QFont('song', 9, QFont.Bold))

            if (job_dic[job]['status'] == 'RUN'):
                item.setForeground(QBrush(Qt.darkGreen))
            elif (job_dic[job]['status'] in ['PEND', 'PSUSP', 'USUSP', 'SSUSP', 'WAIT', 'PROV']):
                item.setForeground(QBrush(Qt.blue))
            elif (job_dic[job]['status'] == 'DONE'):
                item.setForeground(QBrush(Qt.gray))
            elif (job_dic[job]['status'] in ['EXIT', 'UNKWN', 'ZOMBI']):
                item.setForeground(QBrush(Qt.red))

            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Queue" item.
            j = j+1
            item = QTableWidgetItem(job_dic[job]['queue'])
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Host" item.
            j = j+1
            item = QTableWidgetItem(job_dic[job]['started_on'])
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Started" item.
            j = j+1
            start_time = common_lsf.switch_bjobs_uf_time(job_dic[job]['started_time'], '%Y-%m-%d %H:%M:%S')
            item = QTableWidgetItem(start_time)
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Project" item.
            j = j+1

            if str(job_dic[job]['project']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, job_dic[job]['project'])
                self.jobs_tab_table.setItem(i, j, item)

            # Fill "Slot" item.
            j = j+1

            if str(job_dic[job]['processors_requested']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, int(job_dic[job]['processors_requested']))
                self.jobs_tab_table.setItem(i, j, item)

            # Fill "IDLE" item.
            j = j+1
            idle_value = ''

            if str(job_dic[job]['idle_factor']) != '':
                idle_value = round(float(job_dic[job]['idle_factor']), 2)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, idle_value)
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Rusage" item.
            j = j+1
            rusage_mem_value = 0

            if str(job_dic[job]['rusage_mem']) != '':
                item = QTableWidgetItem()
                rusage_mem_value = round(float(job_dic[job]['rusage_mem'])/1024, 1)
                item.setData(Qt.DisplayRole, rusage_mem_value)
                self.jobs_tab_table.setItem(i, j, item)

            # Fill "Mem" item.
            j = j+1
            mem_value = ''

            if (job_dic[job]['status'] != 'DONE') and (job_dic[job]['status'] != 'EXIT'):
                if str(job_dic[job]['mem']) != '':
                    mem_value = round(float(job_dic[job]['mem'])/1024, 1)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, mem_value)
            self.jobs_tab_table.setItem(i, j, item)

            if mem_value and (((not job_dic[job]['rusage_mem']) and (mem_value > 0)) or (job_dic[job]['rusage_mem'] and (mem_value > rusage_mem_value))):
                item.setBackground(QBrush(Qt.red))

            # Fill "MaxMem" item.
            j = j+1
            max_mem_value = ''

            if str(job_dic[job]['max_mem']) != '':
                max_mem_value = round(float(job_dic[job]['max_mem'])/1024, 1)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, max_mem_value)
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Command" item.
            j = j+1
            item = QTableWidgetItem(job_dic[job]['command'])
            self.jobs_tab_table.setItem(i, j, item)

        self.jobs_tab_table.setSortingEnabled(True)

    def jobs_tab_check_click(self, item=None):
        """
        If click the Job id, jump to the JOB tab and show the job information.
        If click the "PEND" Status, show the job pend reasons on a QMessageBox.information().
        """
        if item is not None:
            current_row = self.jobs_tab_table.currentRow()
            job = self.jobs_tab_table.item(current_row, 0).text().strip()

            if item.column() == 0:
                if job != '':
                    self.job_tab_job_line.setText(job)
                    self.check_job_on_job_tab()
                    self.main_tab.setCurrentWidget(self.job_tab)
            elif item.column() == 2:
                job_status = self.jobs_tab_table.item(current_row, 2).text().strip()

                if job_status == 'PEND':
                    self.check_pend_reason(job=job)
                elif job_status == 'RUN':
                    self.check_slow_reason(job=job)
                elif (job_status == 'DONE') or (job_status == 'EXIT'):
                    self.check_fail_reason(job=job)

    def gen_jobs_tab_menu(self, pos):
        """
        Generate right click menu on self.jobs_tab_table.
        """
        item = self.jobs_tab_table.itemAt(pos)

        if item and (item.column() == 9):  # Rusage (G) column
            current_row = self.jobs_tab_table.currentRow()
            job = self.jobs_tab_table.item(current_row, 0).text().strip()
            job_user = self.jobs_tab_table.item(current_row, 1).text().strip()

            # Only show menu for current user's jobs
            if job_user == USER:
                menu = QMenu(self.jobs_tab_table)

                modify_rusage_action = QAction('Modify Rusage Mem', self)
                modify_rusage_action.triggered.connect(lambda: self.modify_job_rusage(job))
                menu.addAction(modify_rusage_action)

                menu.exec_(self.jobs_tab_table.mapToGlobal(pos))

    def modify_job_rusage(self, job):
        """
        Open dialog to modify job's rusage memory.
        """
        # Get current Rusage value from table
        current_row = -1

        for row in range(self.jobs_tab_table.rowCount()):
            if self.jobs_tab_table.item(row, 0).text().strip() == job:
                current_row = row
                break

        if current_row == -1:
            return

        rusage_item = self.jobs_tab_table.item(current_row, 9)
        current_rusage_gb = rusage_item.text().strip() if rusage_item else ''
        current_rusage_mb = 0

        if current_rusage_gb and re.match(r'^\d+\.?\d*$', current_rusage_gb):
            current_rusage_mb = int(float(current_rusage_gb) * 1024)

        # Get host information to determine max rusage limit
        host_item = self.jobs_tab_table.item(current_row, 4)
        host = host_item.text().strip() if host_item else ''

        # Default max value (int max value = 2^31 - 1 = 2147483647 MB ≈ 2048 TB)
        max_rusage_mb = 2147483647
        host_info = ''

        if host:
            # Fresh bhosts_load info to get latest data
            self.fresh_lsf_info('bhosts_load')

            # Get saMem (scheduling available memory) from host
            # saMem is the memory that can be scheduled, i.e., not yet used by jobs
            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('mem' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['mem'] != '-'):
                sa_mem = self.bhosts_load_dic[host]['Total']['mem']
                # mem_unit_switch returns GB, convert to MB
                sa_mem_mb = int(self.mem_unit_switch(sa_mem) * 1024)

                # Max rusage for this job = saMem + current_rusage_mb
                # This allows the job to:
                # 1. Reduce its rusage (free up memory)
                # 2. Increase its rusage (but only up to saMem)
                max_rusage_mb = sa_mem_mb + current_rusage_mb
                host_info = f'\nHost: {host}\nSchedulable Memory (saMem): {sa_mem_mb} MB\nMax Rusage for this job: {max_rusage_mb} MB'

        # Use QInputDialog to get new value
        new_rusage_mb, ok = QInputDialog.getInt(
            self,
            f'Modify Rusage Mem - Job {job}',
            f'Current Rusage: {current_rusage_mb} MB\nEnter new Rusage (MB):{host_info}',
            current_rusage_mb,
            0,
            max_rusage_mb,
            1
        )

        if ok:
            self.apply_rusage_modification(job, new_rusage_mb)

    def apply_rusage_modification(self, job, new_rusage_mb):
        """
        Apply rusage modification using bmod command.
        """
        # Get job info using common_lsf.get_bjobs_uf_info()
        command = f'bjobs -UF {job}'
        common.bprint(f'Getting job info: {command}', date_format='%Y-%m-%d %H:%M:%S')

        job_dic = common_lsf.get_bjobs_uf_info(command)

        if job not in job_dic:
            QMessageBox.warning(self, 'Error', f'Job {job} not found.')
            return

        # Debug: print job_dic keys
        common.bprint(f'Job info keys: {list(job_dic[job].keys())}', date_format='%Y-%m-%d %H:%M:%S')

        requested_resource = job_dic[job].get('requested_resources', '')

        if not requested_resource:
            common.bprint(f'ERROR: requested_resources is empty. Full job info: {job_dic[job]}', date_format='%Y-%m-%d %H:%M:%S', level='Error')
            QMessageBox.warning(self, 'Error', 'Could not find requested resources in job info.')
            return

        common.bprint(f'Original requested_resources: {requested_resource}', date_format='%Y-%m-%d %H:%M:%S')

        # Modify rusage[mem=...] value
        # Use more precise regex to match rusage[mem=...]
        new_requested_resource = re.sub(
            r'rusage\s*\[\s*mem\s*=\s*\d+(\.\d+)?\s*\]',
            f'rusage[mem={new_rusage_mb}]',
            requested_resource
        )

        common.bprint(f'Modified requested_resources: {new_requested_resource}', date_format='%Y-%m-%d %H:%M:%S')

        # If rusage[mem=...] not found, add it
        if new_requested_resource == requested_resource:
            # Check if rusage exists but without mem
            if 'rusage[' in requested_resource:
                QMessageBox.warning(self, 'Error', 'Found rusage but could not find mem parameter. Please check job resources.')
                return
            else:
                # Add rusage[mem=...] to the end
                new_requested_resource = f'{requested_resource} rusage[mem={new_rusage_mb}]'

        # Apply modification with bmod
        bmod_command = f'bmod -R "{new_requested_resource}" {job}'
        common.bprint(f'Executing: {bmod_command}', date_format='%Y-%m-%d %H:%M:%S')

        (return_code, stdout, stderr) = common.run_command(bmod_command)

        if return_code == 0:
            common.bprint(f'Successfully modified rusage for job {job}', date_format='%Y-%m-%d %H:%M:%S')
            QMessageBox.information(self, 'Success', f'Successfully modified rusage for job {job}')

            # Wait a moment for LSF to update job info
            time.sleep(2)

            # Refresh the jobs table
            self.gen_jobs_tab_table()

            # Also try to update the specific cell directly for immediate feedback
            # Find the job row and update the Rusage value
            for row in range(self.jobs_tab_table.rowCount()):
                if self.jobs_tab_table.item(row, 0).text().strip() == job:
                    rusage_gb = round(new_rusage_mb / 1024, 1)
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, rusage_gb)
                    self.jobs_tab_table.setItem(row, 9, item)
                    break
        else:
            error_msg = stderr.decode('utf-8').strip()
            common.bprint(f'Failed to modify rusage: {error_msg}', date_format='%Y-%m-%d %H:%M:%S', level='Error')
            QMessageBox.warning(self, 'Error', f'Failed to modify rusage: {error_msg}')

    def set_jobs_tab_status_combo(self, checked_status_list=['RUN', ]):
        """
        Set (initialize) self.jobs_tab_status_combo.
        """
        self.jobs_tab_status_combo.clear()

        status_list = ['ALL', 'RUN', 'PEND', 'DONE', 'EXIT', 'PSUSP', 'USUSP', 'SSUSP', 'UNKWN', 'WAIT', 'ZOMBI', 'PROV']

        for status in status_list:
            self.jobs_tab_status_combo.addCheckBoxItem(status)

        # Set to checked status for checked_status_list.
        for (i, qBox) in enumerate(self.jobs_tab_status_combo.checkBoxList):
            if (qBox.text() in checked_status_list) and (qBox.isChecked() is False):
                self.jobs_tab_status_combo.checkBoxList[i].setChecked(True)

    def set_jobs_tab_queue_combo(self, checked_queue_list=['ALL', ]):
        """
        Set (initialize) self.jobs_tab_queue_combo.
        """
        self.jobs_tab_queue_combo.clear()
        self.fresh_lsf_info('bqueues')

        if 'QUEUE_NAME' in self.bqueues_dic:
            queue_list = copy.deepcopy(self.bqueues_dic['QUEUE_NAME'])
            queue_list.sort()
        else:
            queue_list = []

        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.jobs_tab_queue_combo.addCheckBoxItem(queue)

        # Set to checked status for checked_queue_list.
        for (i, qBox) in enumerate(self.jobs_tab_queue_combo.checkBoxList):
            if (qBox.text() in checked_queue_list) and (qBox.isChecked() is False):
                self.jobs_tab_queue_combo.checkBoxList[i].setChecked(True)

    def set_jobs_tab_host_combo(self, checked_host_list=['ALL', ]):
        """
        Set (initialize) self.jobs_tab_host_combo.
        """
        self.jobs_tab_host_combo.clear()
        self.fresh_lsf_info('bhosts')

        if 'HOST_NAME' in self.bhosts_dic:
            host_list = copy.deepcopy(self.bhosts_dic['HOST_NAME'])
        else:
            host_list = []

        host_list.insert(0, 'ALL')

        for host in host_list:
            self.jobs_tab_host_combo.addCheckBoxItem(host)

        # Set to checked status for checked_host_list.
        for (i, qBox) in enumerate(self.jobs_tab_host_combo.checkBoxList):
            if (qBox.text() in checked_host_list) and (qBox.isChecked() is False):
                self.jobs_tab_host_combo.checkBoxList[i].setChecked(True)
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
        self.hosts_tab_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.hosts_tab_table.customContextMenuRequested.connect(self.gen_hosts_tab_menu)

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
        # "Status" item.
        hosts_tab_status_label = QLabel('Status', self.hosts_tab_frame0)
        hosts_tab_status_label.setStyleSheet("font-weight: bold;")
        hosts_tab_status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.hosts_tab_status_combo = common_pyqt5.QComboCheckBox(self.hosts_tab_frame0)
        self.set_hosts_tab_status_combo()

        # "Queue" item.
        hosts_tab_queue_label = QLabel('Queue', self.hosts_tab_frame0)
        hosts_tab_queue_label.setStyleSheet("font-weight: bold;")
        hosts_tab_queue_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.hosts_tab_queue_combo = common_pyqt5.QComboCheckBox(self.hosts_tab_frame0, enableFilter=True)
        self.set_hosts_tab_queue_combo()

        # "MAX" item.
        hosts_tab_max_label = QLabel('MAX', self.hosts_tab_frame0)
        hosts_tab_max_label.setStyleSheet("font-weight: bold;")
        hosts_tab_max_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.hosts_tab_max_combo = common_pyqt5.QComboCheckBox(self.hosts_tab_frame0)
        self.set_hosts_tab_max_combo()

        # "MaxMem" item.
        hosts_tab_maxmem_label = QLabel('MaxMem', self.hosts_tab_frame0)
        hosts_tab_maxmem_label.setStyleSheet("font-weight: bold;")
        hosts_tab_maxmem_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.hosts_tab_maxmem_combo = common_pyqt5.QComboCheckBox(self.hosts_tab_frame0)
        self.set_hosts_tab_maxmem_combo()

        # "Host" item.
        hosts_tab_host_label = QLabel('Host', self.hosts_tab_frame0)
        hosts_tab_host_label.setStyleSheet("font-weight: bold;")
        hosts_tab_host_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.hosts_tab_host_line = QLineEdit()
        self.hosts_tab_host_line.returnPressed.connect(self.gen_hosts_tab_table)

        if 'HOST_NAME' in self.bhosts_dic:
            hosts_tab_host_line_completer = common_pyqt5.get_completer(self.bhosts_dic['HOST_NAME'])
        else:
            hosts_tab_host_line_completer = common_pyqt5.get_completer([])

        self.hosts_tab_host_line.setCompleter(hosts_tab_host_line_completer)

        # "Check" button.
        hosts_tab_check_button = QPushButton('Check', self.hosts_tab_frame0)
        hosts_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        hosts_tab_check_button.clicked.connect(self.gen_hosts_tab_table)

        # self.hosts_tab_frame0 - Grid
        hosts_tab_frame0_grid = QGridLayout()

        hosts_tab_frame0_grid.addWidget(hosts_tab_status_label, 0, 0)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_status_combo, 0, 1)
        hosts_tab_frame0_grid.addWidget(hosts_tab_queue_label, 0, 2)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_queue_combo, 0, 3)
        hosts_tab_frame0_grid.addWidget(hosts_tab_max_label, 0, 4)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_max_combo, 0, 5)
        hosts_tab_frame0_grid.addWidget(hosts_tab_maxmem_label, 0, 6)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_maxmem_combo, 0, 7)
        hosts_tab_frame0_grid.addWidget(hosts_tab_host_label, 0, 8)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_host_line, 0, 9)
        hosts_tab_frame0_grid.addWidget(hosts_tab_check_button, 0, 10)

        hosts_tab_frame0_grid.setColumnStretch(0, 1)
        hosts_tab_frame0_grid.setColumnStretch(1, 1)
        hosts_tab_frame0_grid.setColumnStretch(2, 1)
        hosts_tab_frame0_grid.setColumnStretch(3, 1)
        hosts_tab_frame0_grid.setColumnStretch(4, 1)
        hosts_tab_frame0_grid.setColumnStretch(5, 1)
        hosts_tab_frame0_grid.setColumnStretch(6, 1)
        hosts_tab_frame0_grid.setColumnStretch(7, 1)
        hosts_tab_frame0_grid.setColumnStretch(8, 1)
        hosts_tab_frame0_grid.setColumnStretch(9, 1)
        hosts_tab_frame0_grid.setColumnStretch(10, 1)

        self.hosts_tab_frame0.setLayout(hosts_tab_frame0_grid)

    def gen_hosts_tab_table(self):
        # self.hosts_tab_table
        self.hosts_tab_table.setShowGrid(True)
        self.hosts_tab_table.setSortingEnabled(False)
        self.hosts_tab_table.setColumnCount(0)
        self.hosts_tab_table.setColumnCount(12)
        self.hosts_tab_table_title_list = ['Host', 'Status', 'Queue', 'MAX', 'Njobs', 'Ut (%)', 'MaxMem (G)', 'aMem (G)', 'saMem (G)', 'MaxSwp (G)', 'Swp (G)', 'Tmp (G)']
        self.hosts_tab_table.setHorizontalHeaderLabels(self.hosts_tab_table_title_list)

        self.hosts_tab_table.setColumnWidth(0, 150)
        self.hosts_tab_table.setColumnWidth(1, 90)
        self.hosts_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.hosts_tab_table.setColumnWidth(3, 60)
        self.hosts_tab_table.setColumnWidth(4, 60)
        self.hosts_tab_table.setColumnWidth(5, 60)
        self.hosts_tab_table.setColumnWidth(6, 100)
        self.hosts_tab_table.setColumnWidth(7, 85)
        self.hosts_tab_table.setColumnWidth(8, 90)
        self.hosts_tab_table.setColumnWidth(9, 100)
        self.hosts_tab_table.setColumnWidth(10, 75)
        self.hosts_tab_table.setColumnWidth(11, 75)

        # Fill self.hosts_tab_table items.
        hosts_tab_specified_host_list = self.get_hosts_tab_specified_host_list()
        self.hosts_tab_table.setRowCount(0)
        self.hosts_tab_table.setRowCount(len(hosts_tab_specified_host_list))

        # Fresh LSF bhosts/lsload/lshosts/host_queue/bhosts_load information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('lsload')
        self.fresh_lsf_info('lshosts')
        self.fresh_lsf_info('host_queue')
        self.fresh_lsf_info('bhosts_load')

        for (i, host) in enumerate(hosts_tab_specified_host_list):
            fatal_error = False

            # Fill "Host" item.
            j = 0
            item = QTableWidgetItem(host)
            item.setFont(QFont('song', 9, QFont.Bold))

            if host == 'lost_and_found':
                fatal_error = True

            if fatal_error:
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Status" item.
            j = j+1
            index = self.bhosts_dic['HOST_NAME'].index(host)
            status = self.bhosts_dic['STATUS'][index]
            item = QTableWidgetItem(status)

            if str(status) == 'ok':
                item.setForeground(QBrush(Qt.darkGreen))
            else:
                if (str(status) == 'unavail') or (str(status) == 'unreach') or (str(status) == 'closed_LIM'):
                    fatal_error = True
                    item.setForeground(QBrush(Qt.red))
                else:
                    item.setForeground(QBrush(Qt.magenta))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Queue" item.
            j = j+1
            queues = ''

            if host in self.host_queue_dic.keys():
                queues = ' '.join(self.host_queue_dic[host])

            item = QTableWidgetItem(queues)

            if fatal_error:
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "MAX" item.
            j = j+1
            index = self.bhosts_dic['HOST_NAME'].index(host)
            max = self.bhosts_dic['MAX'][index]

            if not re.match(r'^[0-9]+$', max):
                common.bprint(f'Host({host}) MAX info "{max}": invalid value, reset it to "0".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                max = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(max))

            if fatal_error:
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Njobs" item.
            j = j+1
            index = self.bhosts_dic['HOST_NAME'].index(host)
            njobs = self.bhosts_dic['NJOBS'][index]

            if not re.match(r'^[0-9]+$', njobs):
                common.bprint(f'Host({host}) NJOBS info "{njobs}": invalid value, reset it to "0".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                njobs = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(njobs))
            item.setFont(QFont('song', 9, QFont.Bold))

            if fatal_error:
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Ut" item.
            j = j+1
            ut = '0'

            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('ut' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['ut'] != '-'):
                ut = self.bhosts_load_dic[host]['Total']['ut']
            elif ('HOST_NAME' in self.lsload_dic) and (host in self.lsload_dic['HOST_NAME']):
                index = self.lsload_dic['HOST_NAME'].index(host)
                ut = self.lsload_dic['ut'][index]

            ut = re.sub(r'%', '', ut)

            if not re.match(r'^[0-9]+$', ut):
                common.bprint(f'Host({host}) ut info "{ut}": invalid value, reset it to "0".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                ut = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ut))

            if fatal_error or (int(ut) > 90):
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "MaxMem" item with unit "GB".
            j = j+1
            maxmem = '0'

            if host in self.lshosts_dic['HOST_NAME']:
                index = self.lshosts_dic['HOST_NAME'].index(host)
                maxmem = self.lshosts_dic['maxmem'][index]

            maxmem = int(self.mem_unit_switch(maxmem))
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, maxmem)

            if fatal_error or (maxmem == 0):
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "aMem" item with unit "GB".
            # "aMem" means avaliable mem, it is from "lsload -l" command, same with "free -g" result.
            j = j+1

            if ('HOST_NAME' in self.lsload_dic) and (host in self.lsload_dic['HOST_NAME']):
                index = self.lsload_dic['HOST_NAME'].index(host)
                mem = self.lsload_dic['mem'][index]
                mem = int(self.mem_unit_switch(mem))
            else:
                mem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, mem)

            if fatal_error or (maxmem and (float(mem)/float(maxmem) < 0.1)):
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "saMem" item with unit "GB".
            # "saMem" means scheduling avaliable mem, it is from "bhosts -l" command.
            j = j+1

            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('mem' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['mem'] != '-'):
                mem = self.bhosts_load_dic[host]['Total']['mem']
                mem = int(self.mem_unit_switch(mem))
            else:
                mem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, mem)

            if fatal_error or (maxmem and (float(mem)/float(maxmem) < 0.1)):
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "MaxSwp" item with unit "GB".
            j = j+1
            maxswp = '0'

            if host in self.lshosts_dic['HOST_NAME']:
                index = self.lshosts_dic['HOST_NAME'].index(host)
                maxswp = self.lshosts_dic['maxswp'][index]

            maxswp = int(self.mem_unit_switch(maxswp))
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, maxswp)

            if fatal_error:
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Swp" item with unit "GB".
            j = j+1
            swp = '0'

            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('swp' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['swp'] != '-'):
                swp = self.bhosts_load_dic[host]['Total']['swp']
            elif ('HOST_NAME' in self.lsload_dic) and (host in self.lsload_dic['HOST_NAME']):
                index = self.lsload_dic['HOST_NAME'].index(host)
                swp = self.lsload_dic['swp'][index]

            swp = int(self.mem_unit_switch(swp))
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, swp)

            if fatal_error:
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Tmp" item with unit "GB".
            j = j+1
            tmp = '0'

            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('tmp' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['tmp'] != '-'):
                tmp = self.bhosts_load_dic[host]['Total']['tmp']
            elif ('HOST_NAME' in self.lsload_dic) and (host in self.lsload_dic['HOST_NAME']):
                index = self.lsload_dic['HOST_NAME'].index(host)
                tmp = self.lsload_dic['tmp'][index]

            tmp = int(self.mem_unit_switch(tmp))
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, tmp)

            if fatal_error or (int(float(tmp)) == 0):
                item.setBackground(QBrush(Qt.red))

            self.hosts_tab_table.setItem(i, j, item)

        self.hosts_tab_table.setSortingEnabled(True)

    def mem_unit_switch(self, mem_string):
        """
        Switch mem unit M/G/T into G, then remove the unit string.
        """
        mem_match = re.match(r'^([\d.]+)([MGT])$', str(mem_string))

        if mem_match:
            value = float(mem_match.group(1))
            unit = mem_match.group(2)

            if unit == 'M':
                return value / 1024
            elif unit == 'G':
                return value
            elif unit == 'T':
                return value * 1024

        return 0.0

    def gen_hosts_tab_menu(self, pos):
        """
        Generate right click menu on self.hosts_tab_table.
        """
        item = self.hosts_tab_table.itemAt(pos)

        if item and (item.column() == 0):
            menu = QMenu(self.hosts_tab_table)

            open_host_action = QAction('Open', self)
            open_host_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/open.png'))
            open_host_action.triggered.connect(lambda: self.manage_host_on_hosts_tab(item.text(), 'open'))
            menu.addAction(open_host_action)

            close_host_action = QAction('Close', self)
            close_host_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/close.png'))
            close_host_action.triggered.connect(lambda: self.manage_host_on_hosts_tab(item.text(), 'close'))
            menu.addAction(close_host_action)

            menu.exec_(self.hosts_tab_table.mapToGlobal(pos))

    def manage_host_on_hosts_tab(self, host_name, behavior):
        """
        Manage specified host with specified behavior(open/close).
        """
        if host_name:
            command = ''

            if behavior == 'open':
                command = 'badmin hopen ' + str(host_name)
            elif behavior == 'close':
                command = 'badmin hclose ' + str(host_name)

            if command:
                common.bprint(command, date_format='%Y-%m-%d %H:%M:%S')
                (return_code, stdout, stderr) = common.run_command(command)

                if return_code == 0:
                    common.bprint(f'{behavior} {host_name} successfully!', date_format='%Y-%m-%d %H:%M:%S')
                    my_show_message = ShowMessage('Info', f'{behavior} {host_name} successfully!')
                    my_show_message.start()
                    time.sleep(5)
                    my_show_message.terminate()
                    self.gen_hosts_tab_table()
                else:
                    common.bprint(f'Failed on {behavior}ing host "{host_name}".', date_format='%Y-%m-%d %H:%M:%S')
                    common.bprint(str(stderr, 'utf-8').strip(), date_format='%Y-%m-%d %H:%M:%S')
                    my_show_message = ShowMessage(f'{behavior} {host_name} fail', str(str(stderr, 'utf-8')).strip())
                    my_show_message.run()

    def get_hosts_tab_specified_host_list(self):
        """
        Filter host list with specified queue/status/max/maxmem/host.
        """
        specified_status_list = self.hosts_tab_status_combo.currentText().strip().split()
        specified_queue_list = self.hosts_tab_queue_combo.currentText().strip().split()
        specified_max_list = self.hosts_tab_max_combo.currentText().strip().split()
        specified_maxmem_list = self.hosts_tab_maxmem_combo.currentText().strip().split()
        specified_host = self.hosts_tab_host_line.text().strip()
        hosts_tab_specified_host_list = []

        # Fresh LSF bhosts/lshosts/host_queue information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('lshosts')
        self.fresh_lsf_info('host_queue')

        if 'HOST_NAME' in self.bhosts_dic:
            for host in self.bhosts_dic['HOST_NAME']:
                # Filter with specified_status_list.
                index = self.bhosts_dic['HOST_NAME'].index(host)
                status = self.bhosts_dic['STATUS'][index]

                if 'ALL' not in specified_status_list:
                    continue_mark = True

                    for specified_status in specified_status_list:
                        if specified_status == status:
                            continue_mark = False
                            break

                    if continue_mark:
                        continue

                # Filter with specified_queue_list.
                if 'ALL' not in specified_queue_list:
                    continue_mark = True

                    for specified_queue in specified_queue_list:
                        if (host in self.host_queue_dic) and (specified_queue in self.host_queue_dic[host]):
                            continue_mark = False
                            break

                    if continue_mark:
                        continue

                # Filter with specified_max_list.
                index = self.bhosts_dic['HOST_NAME'].index(host)
                max = self.bhosts_dic['MAX'][index]

                if not re.match(r'^[0-9]+$', max):
                    max = 0

                if 'ALL' not in specified_max_list:
                    continue_mark = True

                    for specified_max in specified_max_list:
                        if specified_max == str(max):
                            continue_mark = False
                            break

                    if continue_mark:
                        continue

                # Filter with specified_maxmem_list.
                if host not in self.lshosts_dic['HOST_NAME']:
                    maxmem = 0
                else:
                    index = self.lshosts_dic['HOST_NAME'].index(host)
                    maxmem = int(self.mem_unit_switch(self.lshosts_dic['maxmem'][index]))

                if 'ALL' not in specified_maxmem_list:
                    continue_mark = True

                    for specified_maxmem in specified_maxmem_list:
                        specified_maxmem = re.sub(r'G', '', specified_maxmem)

                        if specified_maxmem == str(maxmem):
                            continue_mark = False
                            break

                    if continue_mark:
                        continue

                # Filter with specified_host.
                if specified_host and (not re.search(specified_host, host)):
                    continue

                hosts_tab_specified_host_list.append(host)

        return hosts_tab_specified_host_list

    def hosts_tab_check_click(self, item=None):
        """
        If click the Host name, jump to the LOAD Tab and show the host load inforamtion.
        If click the non-zero Njobs number, jump to the JOBS tab and show the host related jobs information.
        """
        if item is not None:
            current_row = self.hosts_tab_table.currentRow()
            host = self.hosts_tab_table.item(current_row, 0).text().strip()
            njobs_num = self.hosts_tab_table.item(current_row, 4).text().strip()

            if item.column() == 0:
                self.load_tab_host_line.setText(host)
                self.update_load_tab_load_info()
                self.main_tab.setCurrentWidget(self.load_tab)
            elif item.column() == 4:
                if int(njobs_num) > 0:
                    self.set_jobs_tab_status_combo()
                    self.set_jobs_tab_queue_combo()
                    self.set_jobs_tab_host_combo(checked_host_list=[host, ])
                    self.jobs_tab_user_line.setText('')
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)

    def set_hosts_tab_status_combo(self, checked_status_list=['ALL', ]):
        """
        Set (initialize) self.hosts_tab_status_combo.
        """
        self.hosts_tab_status_combo.clear()
        self.fresh_lsf_info('bhosts')

        status_list = ['ALL', ]

        if 'HOST_NAME' in self.bhosts_dic:
            for host in self.bhosts_dic['HOST_NAME']:
                index = self.bhosts_dic['HOST_NAME'].index(host)
                status = self.bhosts_dic['STATUS'][index]

                if status not in status_list:
                    status_list.append(status)

        for status in status_list:
            self.hosts_tab_status_combo.addCheckBoxItem(status)

        # Set to checked status for checked_status_list.
        for (i, qBox) in enumerate(self.hosts_tab_status_combo.checkBoxList):
            if (qBox.text() in checked_status_list) and (qBox.isChecked() is False):
                self.hosts_tab_status_combo.checkBoxList[i].setChecked(True)

    def set_hosts_tab_queue_combo(self, checked_queue_list=['ALL', ]):
        """
        Set (initialize) self.hosts_tab_queue_combo.
        """
        self.hosts_tab_queue_combo.clear()
        self.fresh_lsf_info('bqueues')

        if 'QUEUE_NAME' in self.bqueues_dic:
            queue_list = copy.deepcopy(self.bqueues_dic['QUEUE_NAME'])
            queue_list.sort()
        else:
            queue_list = []

        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.hosts_tab_queue_combo.addCheckBoxItem(queue)

        # Set to checked status for checked_queue_list.
        for (i, qBox) in enumerate(self.hosts_tab_queue_combo.checkBoxList):
            if (qBox.text() in checked_queue_list) and (qBox.isChecked() is False):
                self.hosts_tab_queue_combo.checkBoxList[i].setChecked(True)

    def set_hosts_tab_max_combo(self, checked_max_list=['ALL', ]):
        """
        Set (initialize) self.hosts_tab_max_combo.
        """
        self.hosts_tab_max_combo.clear()
        self.fresh_lsf_info('bhosts')

        max_list = []

        if 'HOST_NAME' in self.bhosts_dic:
            for host in self.bhosts_dic['HOST_NAME']:
                index = self.bhosts_dic['HOST_NAME'].index(host)
                max = self.bhosts_dic['MAX'][index]

                if not re.match(r'^[0-9]+$', max):
                    max = 0

                if int(max) not in max_list:
                    max_list.append(int(max))

        max_list.sort()
        max_list.insert(0, 'ALL')

        for max in max_list:
            self.hosts_tab_max_combo.addCheckBoxItem(str(max))

        # Set to checked status for checked_max_list.
        for (i, qBox) in enumerate(self.hosts_tab_max_combo.checkBoxList):
            if (qBox.text() in checked_max_list) and (qBox.isChecked() is False):
                self.hosts_tab_max_combo.checkBoxList[i].setChecked(True)

    def set_hosts_tab_maxmem_combo(self, checked_maxmem_list=['ALL', ]):
        """
        Set (initialize) self.hosts_tab_maxmem_combo.
        """
        self.hosts_tab_maxmem_combo.clear()
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('lshosts')

        maxmem_list = []

        if 'HOST_NAME' in self.bhosts_dic:
            for host in self.bhosts_dic['HOST_NAME']:
                if host not in self.lshosts_dic['HOST_NAME']:
                    maxmem = 0
                else:
                    index = self.lshosts_dic['HOST_NAME'].index(host)
                    maxmem = int(self.mem_unit_switch(self.lshosts_dic['maxmem'][index]))

                if maxmem not in maxmem_list:
                    maxmem_list.append(maxmem)

        maxmem_list.sort()

        for (i, maxmem) in enumerate(maxmem_list):
            if maxmem == '0':
                maxmem_list[i] = '-'
            else:
                maxmem_list[i] = str(maxmem) + 'G'

        maxmem_list.insert(0, 'ALL')

        for maxmem in maxmem_list:
            self.hosts_tab_maxmem_combo.addCheckBoxItem(maxmem)

        # Set to checked status for checked_maxmem_list.
        for (i, qBox) in enumerate(self.hosts_tab_maxmem_combo.checkBoxList):
            if (qBox.text() in checked_maxmem_list) and (qBox.isChecked() is False):
                self.hosts_tab_maxmem_combo.checkBoxList[i].setChecked(True)
# For hosts TAB (end) #

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
        # "Host" item.
        load_tab_host_label = QLabel('Host', self.load_tab_frame0)
        load_tab_host_label.setStyleSheet("font-weight: bold;")
        load_tab_host_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.load_tab_host_line = QLineEdit()
        self.load_tab_host_line.returnPressed.connect(self.update_load_tab_load_info)

        if 'HOST_NAME' in self.bhosts_dic:
            load_tab_host_line_completer = common_pyqt5.get_completer(self.bhosts_dic['HOST_NAME'])
        else:
            load_tab_host_line_completer = common_pyqt5.get_completer([])

        self.load_tab_host_line.setCompleter(load_tab_host_line_completer)

        # "Begin_Date" item.
        load_tab_begin_date_label = QLabel('Begin_Date', self.load_tab_frame0)
        load_tab_begin_date_label.setStyleSheet("font-weight: bold;")
        load_tab_begin_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.load_tab_begin_date_edit = QDateEdit(self.load_tab_frame0)
        self.load_tab_begin_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.load_tab_begin_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.load_tab_begin_date_edit.setCalendarPopup(True)
        self.load_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-7))

        # "End_Date" item.
        load_tab_end_date_label = QLabel('End_Date', self.load_tab_frame0)
        load_tab_end_date_label.setStyleSheet("font-weight: bold;")
        load_tab_end_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.load_tab_end_date_edit = QDateEdit(self.load_tab_frame0)
        self.load_tab_end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.load_tab_end_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.load_tab_end_date_edit.setCalendarPopup(True)
        self.load_tab_end_date_edit.setDate(QDate.currentDate())

        # "Check" button.
        load_tab_check_button = QPushButton('Check', self.load_tab_frame0)
        load_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        load_tab_check_button.clicked.connect(self.update_load_tab_load_info)

        # self.load_tab_frame0 - Grid
        load_tab_frame0_grid = QGridLayout()

        load_tab_frame0_grid.addWidget(load_tab_host_label, 0, 0)
        load_tab_frame0_grid.addWidget(self.load_tab_host_line, 0, 1)
        load_tab_frame0_grid.addWidget(load_tab_begin_date_label, 0, 2)
        load_tab_frame0_grid.addWidget(self.load_tab_begin_date_edit, 0, 3)
        load_tab_frame0_grid.addWidget(load_tab_end_date_label, 0, 4)
        load_tab_frame0_grid.addWidget(self.load_tab_end_date_edit, 0, 5)
        load_tab_frame0_grid.addWidget(load_tab_check_button, 0, 6)

        load_tab_frame0_grid.setColumnStretch(0, 1)
        load_tab_frame0_grid.setColumnStretch(1, 1)
        load_tab_frame0_grid.setColumnStretch(2, 1)
        load_tab_frame0_grid.setColumnStretch(3, 1)
        load_tab_frame0_grid.setColumnStretch(4, 1)
        load_tab_frame0_grid.setColumnStretch(5, 1)
        load_tab_frame0_grid.setColumnStretch(6, 1)

        self.load_tab_frame0.setLayout(load_tab_frame0_grid)

    def gen_load_tab_frame1(self):
        # self.load_tab_frame1
        self.load_tab_ut_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.load_tab_ut_toolbar = common_pyqt5.NavigationToolbar2QT(self.load_tab_ut_canvas, self)

        if self.dark_mode:
            fig = self.load_tab_ut_canvas.figure
            fig.set_facecolor('#19232d')

        # self.load_tab_frame1 - Grid
        load_tab_frame1_grid = QGridLayout()
        load_tab_frame1_grid.addWidget(self.load_tab_ut_toolbar, 0, 0)
        load_tab_frame1_grid.addWidget(self.load_tab_ut_canvas, 1, 0)
        self.load_tab_frame1.setLayout(load_tab_frame1_grid)

    def gen_load_tab_frame2(self):
        # self.load_tab_frame2
        self.load_tab_mem_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.load_tab_mem_toolbar = common_pyqt5.NavigationToolbar2QT(self.load_tab_mem_canvas, self)

        if self.dark_mode:
            fig = self.load_tab_mem_canvas.figure
            fig.set_facecolor('#19232d')

        # self.load_tab_frame2 - Grid
        load_tab_frame2_grid = QGridLayout()
        load_tab_frame2_grid.addWidget(self.load_tab_mem_toolbar, 0, 0)
        load_tab_frame2_grid.addWidget(self.load_tab_mem_canvas, 1, 0)
        self.load_tab_frame2.setLayout(load_tab_frame2_grid)

    def update_load_tab_load_info(self):
        """
        Update self.load_tab_frame1 (ut information) and self.load_tab_frame2 (memory information).
        """
        specified_host = self.load_tab_host_line.text().strip()

        if not specified_host:
            warning_message = 'No host is specified on LOAD tab.'
            self.gui_warning(warning_message)
            return

        self.update_load_tab_frame1(specified_host, [], [])
        self.update_load_tab_frame2(specified_host, [], [])

        common.bprint('Loading ut/mem load information ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading ut/mem load information ...')
        my_show_message.start()

        (sample_time_list, ut_list, mem_list) = self.get_load_info(specified_host)

        if sample_time_list:
            self.update_load_tab_frame1(specified_host, sample_time_list, ut_list)
            self.update_load_tab_frame2(specified_host, sample_time_list, mem_list)

        time.sleep(0.01)
        my_show_message.terminate()

    def get_load_info(self, specified_host):
        """
        Get sample_time/ut/mem list for specified host.
        """
        sample_time_list = []
        ut_list = []
        mem_list = []

        load_db_file = str(self.cluster_db_path) + '/load.db'

        if not os.path.exists(load_db_file):
            common.bprint(f'Load database "{load_db_file}" is missing.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
        else:
            (load_db_file_connect_result, load_db_conn) = common_sqlite3.connect_db_file(load_db_file)

            if load_db_file_connect_result == 'failed':
                common.bprint(f'Failed on connecting load database file "{load_db_file}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            else:
                if specified_host:
                    table_name = 'load_' + str(specified_host)
                    begin_date = self.load_tab_begin_date_edit.date().toString(Qt.ISODate)
                    begin_time = str(begin_date) + ' 00:00:00'
                    begin_second = time.mktime(time.strptime(begin_time, '%Y-%m-%d %H:%M:%S'))
                    end_date = self.load_tab_end_date_edit.date().toString(Qt.ISODate)
                    end_time = str(end_date) + ' 23:59:59'
                    end_second = time.mktime(time.strptime(end_time, '%Y-%m-%d %H:%M:%S'))
                    select_condition = "WHERE sample_second BETWEEN '" + str(begin_second) + "' AND '" + str(end_second) + "'"
                    data_dic = common_sqlite3.get_sql_table_data(load_db_file, load_db_conn, table_name, ['sample_time', 'ut', 'mem'], select_condition)

                    if not data_dic:
                        common.bprint(f'Load information is empty for "{specified_host}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                    else:
                        for (i, sample_time) in enumerate(data_dic['sample_time']):
                            # For sample_time
                            sample_time = datetime.datetime.strptime(data_dic['sample_time'][i], '%Y%m%d_%H%M%S')
                            sample_time_list.append(sample_time)

                            # For ut
                            ut = data_dic['ut'][i]

                            if ut:
                                ut = int(re.sub(r'%', '', ut))
                            else:
                                ut = 0

                            ut_list.append(ut)

                            # For mem
                            mem = round(self.mem_unit_switch(data_dic['mem'][i]), 1)
                            mem_list.append(mem)

                    load_db_conn.close()

        return sample_time_list, ut_list, mem_list

    def update_load_tab_frame1(self, specified_host, sample_time_list, ut_list):
        """
        Draw Ut curve for specified host on self.load_tab_frame1.
        """
        fig = self.load_tab_ut_canvas.figure
        fig.clear()
        self.load_tab_ut_canvas.draw()

        if sample_time_list and ut_list:
            self.draw_load_tab_ut_curve(fig, specified_host, sample_time_list, ut_list)

    def draw_load_tab_ut_curve(self, fig, specified_host, sample_time_list, ut_list):
        """
        Draw ut curve for specified host.
        """
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)

        if self.dark_mode:
            axes.set_facecolor('#19232d')

            for spine in axes.spines.values():
                spine.set_color('white')

            axes.tick_params(axis='both', colors='white')
            axes.set_title('ut curve for host "' + str(specified_host) + '"', color='white')
            axes.set_xlabel('Sample Time', color='white')
            axes.set_ylabel('Cpu Utilization (%)', color='white')
        else:
            axes.set_title('ut curve for host "' + str(specified_host) + '"')
            axes.set_xlabel('Sample Time')
            axes.set_ylabel('Cpu Utilization (%)')

        axes.plot(sample_time_list, ut_list, 'ro-', label='CPU', linewidth=0.1, markersize=0.1)
        axes.fill_between(sample_time_list, ut_list, color='red', alpha=0.5)
        axes.legend(loc='upper right')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.load_tab_ut_canvas.draw()

    def update_load_tab_frame2(self, specified_host, sample_time_list, mem_list):
        """
        Draw mem curve for specified host on self.load_tab_frame2.
        """
        fig = self.load_tab_mem_canvas.figure
        fig.clear()
        self.load_tab_mem_canvas.draw()

        if sample_time_list and mem_list:
            self.draw_load_tab_mem_curve(fig, specified_host, sample_time_list, mem_list)

    def draw_load_tab_mem_curve(self, fig, specified_host, sample_time_list, mem_list):
        """
        Draw mem curve for specified host.
        """
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)

        if self.dark_mode:
            axes.set_facecolor('#19232d')

            for spine in axes.spines.values():
                spine.set_color('white')

            axes.tick_params(axis='both', colors='white')
            axes.set_title('available mem curve for host "' + str(specified_host) + '"', color='white')
            axes.set_xlabel('Sample Time', color='white')
            axes.set_ylabel('Available Mem (G)', color='white')
        else:
            axes.set_title('available mem curve for host "' + str(specified_host) + '"')
            axes.set_xlabel('Sample Time')
            axes.set_ylabel('Available Mem (G)')

        axes.plot(sample_time_list, mem_list, 'go-', label='MEM', linewidth=0.1, markersize=0.1)
        axes.fill_between(sample_time_list, mem_list, color='green', alpha=0.5)
        axes.legend(loc='upper right')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.load_tab_mem_canvas.draw()
# For load TAB (end) #

# For users TAB (start) #
    def gen_users_tab(self):
        """
        Generate the users tab on lsfMonitor GUI, show users informations.
        """
        # self.users_tab_table
        self.users_tab_frame0 = QFrame(self.users_tab)
        self.users_tab_frame0.setFrameShadow(QFrame.Raised)
        self.users_tab_frame0.setFrameShape(QFrame.Box)

        self.users_tab_table = QTableWidget(self.users_tab)

        # self.users_tab_table - Grid
        users_tab_grid = QGridLayout()

        users_tab_grid.addWidget(self.users_tab_frame0, 0, 0)
        users_tab_grid.addWidget(self.users_tab_table, 1, 0)

        users_tab_grid.setRowStretch(0, 1)
        users_tab_grid.setRowStretch(1, 20)

        self.users_tab.setLayout(users_tab_grid)

        # Generate sub-fram
        self.gen_users_tab_frame0()

    def gen_users_tab_frame0(self):
        # self.users_tab_frame0
        # "Status" item.
        users_tab_status_label = QLabel('Status', self.users_tab_frame0)
        users_tab_status_label.setStyleSheet("font-weight: bold;")
        users_tab_status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.users_tab_status_combo = common_pyqt5.QComboCheckBox(self.users_tab_frame0)
        self.set_users_tab_status_combo()

        # "Queue" item.
        users_tab_queue_label = QLabel('Queue', self.users_tab_frame0)
        users_tab_queue_label.setStyleSheet("font-weight: bold;")
        users_tab_queue_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.users_tab_queue_line = QLineEdit()
        self.users_tab_queue_line.returnPressed.connect(self.gen_users_tab_table)

        if 'QUEUE_NAME' in self.bqueues_dic:
            users_tab_queue_line_completer = common_pyqt5.get_completer(self.bqueues_dic['QUEUE_NAME'])
        else:
            users_tab_queue_line_completer = common_pyqt5.get_completer([])

        self.users_tab_queue_line.setCompleter(users_tab_queue_line_completer)

        # "Project" item.
        users_tab_project_label = QLabel('Project', self.users_tab_frame0)
        users_tab_project_label.setStyleSheet("font-weight: bold;")
        users_tab_project_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.users_tab_project_line = QLineEdit()
        self.users_tab_project_line.returnPressed.connect(self.gen_users_tab_table)

        # "User" item.
        users_tab_user_label = QLabel('User', self.users_tab_frame0)
        users_tab_user_label.setStyleSheet("font-weight: bold;")
        users_tab_user_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.users_tab_user_line = QLineEdit()
        self.users_tab_user_line.returnPressed.connect(self.gen_users_tab_table)

        if 'USER/GROUP' in self.busers_dic:
            users_tab_user_line_completer = common_pyqt5.get_completer(self.busers_dic['USER/GROUP'])
        else:
            users_tab_user_line_completer = common_pyqt5.get_completer([])

        self.users_tab_user_line.setCompleter(users_tab_user_line_completer)

        # "Begin_Date" item.
        users_tab_begin_date_label = QLabel('Begin_Date', self.users_tab_frame0)
        users_tab_begin_date_label.setStyleSheet("font-weight: bold;")
        users_tab_begin_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.users_tab_begin_date_edit = QDateEdit(self.users_tab_frame0)
        self.users_tab_begin_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.users_tab_begin_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.users_tab_begin_date_edit.setCalendarPopup(True)
        self.users_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-1))

        # "End_Date" item.
        users_tab_end_date_label = QLabel('End_Date', self.users_tab_frame0)
        users_tab_end_date_label.setStyleSheet("font-weight: bold;")
        users_tab_end_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.users_tab_end_date_edit = QDateEdit(self.users_tab_frame0)
        self.users_tab_end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.users_tab_end_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.users_tab_end_date_edit.setCalendarPopup(True)
        self.users_tab_end_date_edit.setDate(QDate.currentDate())

        # "Check" button.
        users_tab_check_button = QPushButton('Check', self.users_tab_frame0)
        users_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        users_tab_check_button.clicked.connect(self.gen_users_tab_table)

        # empty item.
        users_tab_empty_label = QLabel('', self.users_tab_frame0)

        # self.users_tab_frame0 - Grid
        users_tab_frame0_grid = QGridLayout()

        users_tab_frame0_grid.addWidget(users_tab_status_label, 0, 0)
        users_tab_frame0_grid.addWidget(self.users_tab_status_combo, 0, 1)
        users_tab_frame0_grid.addWidget(users_tab_queue_label, 0, 2)
        users_tab_frame0_grid.addWidget(self.users_tab_queue_line, 0, 3)
        users_tab_frame0_grid.addWidget(users_tab_project_label, 0, 4)
        users_tab_frame0_grid.addWidget(self.users_tab_project_line, 0, 5)
        users_tab_frame0_grid.addWidget(users_tab_user_label, 0, 6)
        users_tab_frame0_grid.addWidget(self.users_tab_user_line, 0, 7)
        users_tab_frame0_grid.addWidget(users_tab_check_button, 0, 8)
        users_tab_frame0_grid.addWidget(users_tab_begin_date_label, 1, 0)
        users_tab_frame0_grid.addWidget(self.users_tab_begin_date_edit, 1, 1)
        users_tab_frame0_grid.addWidget(users_tab_end_date_label, 1, 2)
        users_tab_frame0_grid.addWidget(self.users_tab_end_date_edit, 1, 3)
        users_tab_frame0_grid.addWidget(users_tab_empty_label, 1, 4, 1, 5)

        users_tab_frame0_grid.setColumnStretch(0, 1)
        users_tab_frame0_grid.setColumnStretch(1, 1)
        users_tab_frame0_grid.setColumnStretch(2, 1)
        users_tab_frame0_grid.setColumnStretch(3, 1)
        users_tab_frame0_grid.setColumnStretch(4, 1)
        users_tab_frame0_grid.setColumnStretch(5, 1)
        users_tab_frame0_grid.setColumnStretch(6, 1)

        self.users_tab_frame0.setLayout(users_tab_frame0_grid)

    def set_users_tab_status_combo(self, checked_status_list=['ALL', ]):
        """
        Set (initialize) self.users_tab_status_combo.
        """
        self.users_tab_status_combo.clear()
        status_list = ['ALL', 'DONE', 'EXIT']

        for status in status_list:
            self.users_tab_status_combo.addCheckBoxItem(status)

        # Set to checked status for checked_status_list.
        for (i, qBox) in enumerate(self.users_tab_status_combo.checkBoxList):
            if (qBox.text() in checked_status_list) and (qBox.isChecked() is False):
                self.users_tab_status_combo.checkBoxList[i].setChecked(True)

    def gen_users_tab_table(self):
        # self.users_tab_table
        self.users_tab_table.setShowGrid(True)
        self.users_tab_table.setSortingEnabled(False)
        self.users_tab_table.setColumnCount(0)
        self.users_tab_table.setColumnCount(9)
        self.users_tab_table_title_list = ['User', 'Job_Num', 'Pass_Rate (%)', 'Total_Rusage_Mem (G)', 'Avg_Rusage_Mem (G)', 'Total_Max_Mem (G)', 'Avg_Max_Mem (G)', 'Total_Mem_Waste (G)', 'Avg_Mem_Waste (G)']
        self.users_tab_table.setHorizontalHeaderLabels(self.users_tab_table_title_list)

        self.users_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.users_tab_table.setColumnWidth(1, 80)
        self.users_tab_table.setColumnWidth(2, 100)
        self.users_tab_table.setColumnWidth(3, 155)
        self.users_tab_table.setColumnWidth(4, 150)
        self.users_tab_table.setColumnWidth(5, 135)
        self.users_tab_table.setColumnWidth(6, 130)
        self.users_tab_table.setColumnWidth(7, 150)
        self.users_tab_table.setColumnWidth(8, 145)

        # Fill self.users_tab_table items.
        user_dic = self.get_user_info()
        self.users_tab_table.setRowCount(0)
        self.users_tab_table.setRowCount(len(user_dic.keys()))

        i = -1

        for user in user_dic.keys():
            i += 1

            # Fill "User" item.
            j = 0
            item = QTableWidgetItem(user)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Job_Num" item.
            j = j+1
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(user_dic[user]['job_num']))
            self.users_tab_table.setItem(i, j, item)

            # Fill "Pass_Rate" item.
            j = j+1
            pass_rate = 0

            if user_dic[user]['job_num']:
                pass_rate = round((100*float(user_dic[user]['done_num'])/float(user_dic[user]['job_num'])), 1)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, pass_rate)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Total_Rusage_Mem" item.
            j = j+1
            item = QTableWidgetItem()
            total_rusage_mem = round(float(user_dic[user]['rusage_mem'])/1024, 1)
            item.setData(Qt.DisplayRole, total_rusage_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Avg_Rusage_Mem" item.
            j = j+1
            item = QTableWidgetItem()
            avg_rusage_mem = 0

            if user_dic[user]['job_num']:
                avg_rusage_mem = round(float(user_dic[user]['rusage_mem'])/1024/float(user_dic[user]['job_num']), 1)

            item.setData(Qt.DisplayRole, avg_rusage_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Total_Max_Mem" item.
            j = j+1
            item = QTableWidgetItem()
            total_max_mem = round(float(user_dic[user]['max_mem'])/1024, 1)
            item.setData(Qt.DisplayRole, total_max_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Avg_Max_Mem" item.
            j = j+1
            item = QTableWidgetItem()
            avg_max_mem = 0

            if user_dic[user]['job_num']:
                avg_max_mem = round(float(user_dic[user]['max_mem'])/1024/float(user_dic[user]['job_num']), 1)

            item.setData(Qt.DisplayRole, avg_max_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Total_Mem_Waste" item.
            j = j+1
            item = QTableWidgetItem()
            total_mem_waste = round((float(user_dic[user]['rusage_mem'])-float(user_dic[user]['max_mem']))/1024, 1)
            item.setData(Qt.DisplayRole, total_mem_waste)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Avg_Mem_Waste" item.
            j = j+1
            item = QTableWidgetItem()
            avg_mem_waste = 0

            if user_dic[user]['job_num']:
                avg_mem_waste = round((float(user_dic[user]['rusage_mem'])-float(user_dic[user]['max_mem']))/1024/float(user_dic[user]['job_num']), 1)

            item.setData(Qt.DisplayRole, avg_mem_waste)
            self.users_tab_table.setItem(i, j, item)

        self.users_tab_table.setSortingEnabled(True)

    def get_user_info(self):
        """
        Get user history information from database.
        """
        common.bprint('Loading user history info ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading user history info ...')
        my_show_message.start()

        user_dic = {'ALL': {'job_num': 0, 'done_num': 0, 'exit_num': 0, 'rusage_mem': 0, 'max_mem': 0}}
        specified_status_list = self.users_tab_status_combo.currentText().strip().split()
        specified_queue_list = self.users_tab_queue_line.text().strip().split()
        specified_project_list = self.users_tab_project_line.text().strip().split()
        specified_user_list = self.users_tab_user_line.text().strip().split()
        begin_date = self.users_tab_begin_date_edit.date()
        end_date = self.users_tab_end_date_edit.date()
        current_date = begin_date

        # Get select WHERE condition.
        select_condition = ''

        if specified_status_list and ('ALL' not in specified_status_list):
            if len(specified_status_list) == 1:
                select_condition = 'WHERE status = "' + str(specified_status_list[0]) + '"'
            else:
                select_condition = 'WHERE status IN ' + str(tuple(specified_status_list))

        if specified_queue_list:
            if select_condition:
                if len(specified_queue_list) == 1:
                    select_condition = str(select_condition) + ' AND queue = "' + str(specified_queue_list[0]) + '"'
                else:
                    select_condition = str(select_condition) + ' AND queue IN ' + str(tuple(specified_queue_list))
            else:
                if len(specified_queue_list) == 1:
                    select_condition = 'WHERE queue = "' + str(specified_queue_list[0]) + '"'
                else:
                    select_condition = 'WHERE queue IN ' + str(tuple(specified_queue_list))

        if specified_project_list:
            if select_condition:
                if len(specified_project_list) == 1:
                    select_condition = str(select_condition) + ' AND project = "' + str(specified_project_list[0]) + '"'
                else:
                    select_condition = str(select_condition) + ' AND project IN ' + str(tuple(specified_project_list))
            else:
                if len(specified_project_list) == 1:
                    select_condition = 'WHERE project = "' + str(specified_project_list[0]) + '"'
                else:
                    select_condition = 'WHERE project IN ' + str(tuple(specified_project_list))

        while current_date <= end_date:
            # Get all user/date history data.
            current_date_string = current_date.toString('yyyyMMdd')
            current_date = current_date.addDays(1)
            user_db_file = str(self.cluster_db_path) + '/user/' + str(current_date_string) + '.db'

            if os.path.exists(user_db_file):
                (user_db_file_connect_result, user_db_conn) = common_sqlite3.connect_db_file(user_db_file)

                if user_db_file_connect_result == 'failed':
                    common.bprint(f'Failed on connecting user database file "{user_db_file}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                else:
                    user_table_list = common_sqlite3.get_sql_table_list(user_db_file, user_db_conn)

                    for user_table_name in user_table_list:
                        user = re.sub(r'^user_', '', user_table_name)

                        if (not specified_user_list) or (user in specified_user_list):
                            user_dic.setdefault(user, {'job_num': 0, 'done_num': 0, 'exit_num': 0, 'rusage_mem': 0, 'max_mem': 0})
                            data_dic = common_sqlite3.get_sql_table_data(user_db_file, user_db_conn, user_table_name, ['status', 'rusage_mem', 'max_mem'], select_condition)

                            if data_dic:
                                for i, status in enumerate(data_dic['status']):
                                    user_dic[user]['job_num'] += 1
                                    user_dic['ALL']['job_num'] += 1

                                    if status == 'DONE':
                                        user_dic[user]['done_num'] += 1
                                        user_dic['ALL']['done_num'] += 1
                                    elif status == 'EXIT':
                                        user_dic[user]['exit_num'] += 1
                                        user_dic['ALL']['exit_num'] += 1

                                    if data_dic['rusage_mem'][i]:
                                        rusage_mem = float(data_dic['rusage_mem'][i])
                                    else:
                                        rusage_mem = 0

                                    if data_dic['max_mem'][i]:
                                        max_mem = float(data_dic['max_mem'][i])
                                    else:
                                        max_mem = 0

                                    user_dic[user]['rusage_mem'] += rusage_mem
                                    user_dic['ALL']['rusage_mem'] += rusage_mem
                                    user_dic[user]['max_mem'] += max_mem
                                    user_dic['ALL']['max_mem'] += max_mem

                user_db_conn.close()

        time.sleep(0.01)
        my_show_message.terminate()

        return user_dic
# For users TAB (end) #

# For queues TAB (start) #
    def gen_queues_tab(self):
        """
        Generate the queues tab on lsfMonitor GUI, show queues informations.
        """
        # self.queues_tab
        self.queues_tab_table = QTableWidget(self.queues_tab)
        self.queues_tab_table.itemClicked.connect(self.queues_tab_check_click)
        self.queues_tab_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queues_tab_table.customContextMenuRequested.connect(self.gen_queues_tab_menu)

        self.queues_tab_frame0 = QFrame(self.queues_tab)
        self.queues_tab_frame0.setFrameShadow(QFrame.Raised)
        self.queues_tab_frame0.setFrameShape(QFrame.Box)

        self.queues_tab_frame1 = QFrame(self.queues_tab)
        self.queues_tab_frame1.setFrameShadow(QFrame.Raised)
        self.queues_tab_frame1.setFrameShape(QFrame.Box)

        self.queues_tab_frame2 = QFrame(self.queues_tab)
        self.queues_tab_frame2.setFrameShadow(QFrame.Raised)
        self.queues_tab_frame2.setFrameShape(QFrame.Box)

        # self.queues_tab - Grid
        queues_tab_grid = QGridLayout()

        queues_tab_grid.addWidget(self.queues_tab_table, 0, 0, 2, 1)
        queues_tab_grid.addWidget(self.queues_tab_frame0, 0, 1)
        queues_tab_grid.addWidget(self.queues_tab_frame1, 1, 1)
        queues_tab_grid.addWidget(self.queues_tab_frame2, 2, 0, 1, 2)

        queues_tab_grid.setRowStretch(0, 1)
        queues_tab_grid.setRowStretch(1, 14)
        queues_tab_grid.setRowStretch(2, 6)

        queues_tab_grid.setColumnStretch(0, 37)
        queues_tab_grid.setColumnStretch(1, 63)

        queues_tab_grid.setColumnMinimumWidth(0, 330)

        self.queues_tab.setLayout(queues_tab_grid)

        # Generate sub-frame
        self.gen_queues_tab_table()
        self.gen_queues_tab_frame0()
        self.gen_queues_tab_frame1()
        self.gen_queues_tab_frame2()

    def gen_queues_tab_table(self):
        self.queues_tab_table.setShowGrid(True)
        self.queues_tab_table.setSortingEnabled(False)
        self.queues_tab_table.setColumnCount(0)
        self.queues_tab_table.setColumnCount(4)
        self.queues_tab_table_title_list = ['QUEUE', 'SLOTS', 'PEND', 'RUN']
        self.queues_tab_table.setHorizontalHeaderLabels(self.queues_tab_table_title_list)

        self.queues_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queues_tab_table.setColumnWidth(1, 70)
        self.queues_tab_table.setColumnWidth(2, 80)
        self.queues_tab_table.setColumnWidth(3, 80)

        # Fresh LSF bhosts/queues/queue_host information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('bqueues')
        self.fresh_lsf_info('queue_host')

        # Fill self.queues_tab_table items.
        self.queues_tab_table.setRowCount(0)

        if 'QUEUE_NAME' in self.bqueues_dic:
            self.queues_tab_table.setRowCount(len(self.bqueues_dic['QUEUE_NAME'])+1)
            queue_list = copy.deepcopy(self.bqueues_dic['QUEUE_NAME'])
        else:
            self.queues_tab_table.setRowCount(1)
            queue_list = []

        queue_list.sort()
        queue_list.append('ALL')

        pend_sum = 0
        run_sum = 0

        for i in range(len(queue_list)):
            queue = queue_list[i]
            index = 0

            if i < len(queue_list)-1:
                index = self.bqueues_dic['QUEUE_NAME'].index(queue)

            # Fill "QUEUE" item.
            j = 0
            item = QTableWidgetItem(queue)
            item.setFont(QFont('song', 9, QFont.Bold))
            self.queues_tab_table.setItem(i, j, item)

            # Fill "SLOTS" item.
            j = j+1
            total = 0

            if queue == 'ALL':
                if 'MAX' in self.bhosts_dic:
                    for max in self.bhosts_dic['MAX']:
                        if re.match(r'^\d+$', max):
                            total += int(max)
            elif queue == 'lost_and_found':
                total = 'N/A'
            else:
                for queue_host in self.queue_host_dic[queue]:
                    if queue_host in self.bhosts_dic['HOST_NAME']:
                        host_index = self.bhosts_dic['HOST_NAME'].index(queue_host)
                        host_max = self.bhosts_dic['MAX'][host_index]

                        if re.match(r'^\d+$', host_max):
                            total += int(host_max)

            item = QTableWidgetItem()

            if queue == 'lost_and_found':
                item.setForeground(QBrush(Qt.red))

            if total == 'N/A':
                item.setData(Qt.DisplayRole, str(total))
            else:
                item.setData(Qt.DisplayRole, int(total))

            self.queues_tab_table.setItem(i, j, item)

            # Fill "PEND" item.
            j = j+1

            if i == len(queue_list)-1:
                pend = str(pend_sum)
            else:
                pend = self.bqueues_dic['PEND'][index]
                pend_sum += int(pend)

            item = QTableWidgetItem()
            item.setFont(QFont('song', 9, QFont.Bold))

            if int(pend) > 0:
                item.setForeground(QBrush(Qt.blue))

            item.setData(Qt.DisplayRole, int(pend))
            self.queues_tab_table.setItem(i, j, item)

            # Fill "RUN" item.
            j = j+1

            if i == len(queue_list)-1:
                run = str(run_sum)
            else:
                run = self.bqueues_dic['RUN'][index]
                run_sum += int(run)

            item = QTableWidgetItem()
            item.setFont(QFont('song', 9, QFont.Bold))
            item.setData(Qt.DisplayRole, int(run))
            self.queues_tab_table.setItem(i, j, item)

        self.queues_tab_table.setSortingEnabled(True)

    def gen_queues_tab_menu(self, pos):
        menu = QMenu(self.queues_tab_table)
        refresh_action = QAction('Refresh', self)
        refresh_action.triggered.connect(self.refresh_queues_tab_table)
        menu.addAction(refresh_action)
        menu.exec_(self.queues_tab_table.mapToGlobal(pos))

    def refresh_queues_tab_table(self):
        my_show_message = ShowMessage('Info', 'Loading queue information, please wait ...')
        my_show_message.start()
        self.gen_queues_tab_table()
        time.sleep(0.01)
        my_show_message.terminate()

    def gen_queues_tab_frame0(self):
        # "Queue" item.
        queues_tab_queue_label = QLabel('Queue', self.queues_tab_frame0)
        queues_tab_queue_label.setStyleSheet("font-weight: bold;")
        queues_tab_queue_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.queues_tab_queue_combo = common_pyqt5.QComboCheckBox(self.queues_tab_frame0, enableFilter=True)
        self.set_queues_tab_queue_combo()

        # "Begin_Date" item.
        queues_tab_begin_date_label = QLabel('Begin_Date', self.queues_tab_frame0)
        queues_tab_begin_date_label.setStyleSheet("font-weight: bold;")
        queues_tab_begin_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.queues_tab_begin_date_edit = QDateEdit(self.queues_tab_frame0)
        self.queues_tab_begin_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.queues_tab_begin_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.queues_tab_begin_date_edit.setCalendarPopup(True)
        self.queues_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

        # "End_Date" item.
        queues_tab_end_date_label = QLabel('End_Date', self.queues_tab_frame0)
        queues_tab_end_date_label.setStyleSheet("font-weight: bold;")
        queues_tab_end_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.queues_tab_end_date_edit = QDateEdit(self.queues_tab_frame0)
        self.queues_tab_end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.queues_tab_end_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.queues_tab_end_date_edit.setCalendarPopup(True)
        self.queues_tab_end_date_edit.setDate(QDate.currentDate())

        # "Check" button.
        queues_tab_check_button = QPushButton('Check', self.queues_tab_frame0)
        queues_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        queues_tab_check_button.clicked.connect(self.update_queues_tab_info)

        # self.queues_tab_frame0 - Grid
        queues_tab_frame0_grid = QGridLayout()

        queues_tab_frame0_grid.addWidget(queues_tab_queue_label, 0, 0)
        queues_tab_frame0_grid.addWidget(self.queues_tab_queue_combo, 0, 1)
        queues_tab_frame0_grid.addWidget(queues_tab_begin_date_label, 0, 2)
        queues_tab_frame0_grid.addWidget(self.queues_tab_begin_date_edit, 0, 3)
        queues_tab_frame0_grid.addWidget(queues_tab_end_date_label, 0, 4)
        queues_tab_frame0_grid.addWidget(self.queues_tab_end_date_edit, 0, 5)
        queues_tab_frame0_grid.addWidget(queues_tab_check_button, 0, 6)

        queues_tab_frame0_grid.setColumnStretch(0, 1)
        queues_tab_frame0_grid.setColumnStretch(1, 1)
        queues_tab_frame0_grid.setColumnStretch(2, 1)
        queues_tab_frame0_grid.setColumnStretch(3, 1)
        queues_tab_frame0_grid.setColumnStretch(4, 1)
        queues_tab_frame0_grid.setColumnStretch(5, 1)
        queues_tab_frame0_grid.setColumnStretch(6, 1)

        self.queues_tab_frame0.setLayout(queues_tab_frame0_grid)

    def set_queues_tab_queue_combo(self, checked_queue_list=['ALL', ]):
        """
        Set (initialize) self.queues_tab_queue_combo.
        """
        self.queues_tab_queue_combo.clear()
        self.fresh_lsf_info('bqueues')

        if 'QUEUE_NAME' in self.bqueues_dic:
            queue_list = copy.deepcopy(self.bqueues_dic['QUEUE_NAME'])
            queue_list.sort()
        else:
            queue_list = []

        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.queues_tab_queue_combo.addCheckBoxItem(queue)

        # Set to checked status for checked_queue_list.
        for (i, qBox) in enumerate(self.queues_tab_queue_combo.checkBoxList):
            if (qBox.text() in checked_queue_list) and (qBox.isChecked() is False):
                self.queues_tab_queue_combo.checkBoxList[i].setChecked(True)

    def queues_tab_check_click(self, item=None):
        """
        If click the QUEUE name, show queue information on QUEUE tab.
        If click the PEND number, jump to the JOBS Tab and show the queue PEND jobs.
        If click the RUN number, jump to the JOB Tab and show the queue RUN jobs.
        """
        if item is not None:
            current_row = self.queues_tab_table.currentRow()
            queue = self.queues_tab_table.item(current_row, 0).text().strip()
            pend_num = self.queues_tab_table.item(current_row, 2).text().strip()
            run_num = self.queues_tab_table.item(current_row, 3).text().strip()

            if item.column() == 0:
                common.bprint(f'Checking queue "{queue}".', date_format='%Y-%m-%d %H:%M:%S')

                self.set_queues_tab_queue_combo(checked_queue_list=[queue])
                self.update_queues_tab_info()
            elif item.column() == 2:
                if (pend_num != '') and (int(pend_num) > 0):
                    self.set_jobs_tab_status_combo(checked_status_list=['PEND', ])
                    self.set_jobs_tab_queue_combo(checked_queue_list=[queue, ])
                    self.set_jobs_tab_host_combo()
                    self.jobs_tab_user_line.setText('')
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)
            elif item.column() == 3:
                if (run_num != '') and (int(run_num) > 0):
                    self.set_jobs_tab_status_combo(checked_status_list=['RUN', ])
                    self.set_jobs_tab_queue_combo(checked_queue_list=[queue, ])
                    self.set_jobs_tab_host_combo()
                    self.jobs_tab_user_line.setText('')
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)

            # Update queue information first.
            self.gen_queues_tab_table()

    def gen_queues_tab_frame1(self):
        # self.queues_tab_frame1
        self.queues_tab_num_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.queues_tab_num_toolbar = common_pyqt5.NavigationToolbar2QT(self.queues_tab_num_canvas, self)

        if self.dark_mode:
            fig = self.queues_tab_num_canvas.figure
            fig.set_facecolor('#19232d')

        # self.queues_tab_frame1 - Grid
        queues_tab_frame1_grid = QGridLayout()
        queues_tab_frame1_grid.addWidget(self.queues_tab_num_toolbar, 0, 0)
        queues_tab_frame1_grid.addWidget(self.queues_tab_num_canvas, 1, 0)
        self.queues_tab_frame1.setLayout(queues_tab_frame1_grid)

    def update_queues_tab_frame1(self):
        """
        Draw queue (PEND/RUN) job number current job on self.queues_tab_frame1.
        """
        fig = self.queues_tab_num_canvas.figure
        fig.clear()
        self.queues_tab_num_canvas.draw()

        queue_list = list(self.queues_tab_queue_combo.selectedItems().values())
        queue_date_dic = self.get_queue_job_num_list(queue_list)

        # Get sorted date list.
        date_list = list(queue_date_dic.keys())
        date_list.sort()

        # Get total_list, pend_list, run_list.
        total_list = []
        pend_list = []
        run_list = []

        for i, date in enumerate(date_list):
            # Switch date format.
            if self.enable_queue_detail:
                date_list[i] = datetime.datetime.strptime(date_list[i], '%Y%m%d_%H%M%S')
            else:
                date_list[i] = datetime.datetime.strptime(date_list[i], '%Y%m%d')

            total_num = sum(queue_date_dic[date][queue]['total'] for queue in queue_date_dic[date])
            total_list.append(total_num)
            pend_num = sum(queue_date_dic[date][queue]['pend'] for queue in queue_date_dic[date])
            pend_list.append(pend_num)
            run_num = sum(queue_date_dic[date][queue]['run'] for queue in queue_date_dic[date])
            run_list.append(run_num)

        self.draw_queues_tab_num_curve(fig, queue_list, date_list, total_list, pend_list, run_list)

    def gen_queues_tab_frame2(self):
        # self.queues_tab_frame2
        self.queues_tab_text = QTextEdit(self.queues_tab_frame2)

        # self.queues_tab_frame2 - Grid
        queues_tab_frame2_grid = QGridLayout()
        queues_tab_frame2_grid.addWidget(self.queues_tab_text, 0, 0)
        self.queues_tab_frame2.setLayout(queues_tab_frame2_grid)

    def update_queues_tab_frame2(self):
        """
        Show queue detailed informations on self.queues_tab_text.
        """
        self.queues_tab_text.clear()
        selected_queue_dic = self.queues_tab_queue_combo.selectedItems()

        if selected_queue_dic:
            selected_queues = ' '.join(selected_queue_dic.values())
            command = 'bqueues -l ' + str(selected_queues)
            (return_code, stdout, stderr) = common.run_command(command)

            for (i, line) in enumerate(str(stdout, 'utf-8').split('\n')):
                line = line.strip()

                if (not line) and (i == 0):
                    continue

                self.queues_tab_text.insertPlainText(str(line) + '\n')

            common_pyqt5.text_edit_visible_position(self.queues_tab_text, 'Start')

    def update_queues_tab_info(self):
        """
        Update self.queues_tab_frame1 and self.queues_tab_frame2.
        """
        self.update_queues_tab_frame1()
        self.update_queues_tab_frame2()

    def get_queue_job_num_list(self, queue_list):
        """
        Draw (PEND/RUN) job number curve for specified queueu.
        """
        queue_date_dic = {}
        queue_db_file = str(self.cluster_db_path) + '/queue.db'

        if not os.path.exists(queue_db_file):
            common.bprint(f'Queue database file "{queue_db_file}" is missing.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
        else:
            (queue_db_file_connect_result, queue_db_conn) = common_sqlite3.connect_db_file(queue_db_file)

            if queue_db_file_connect_result == 'failed':
                common.bprint(f'Failed on connecting queue database file "{queue_db_file}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            else:
                for queue in queue_list:
                    table_name = 'queue_' + str(queue)
                    begin_date = self.queues_tab_begin_date_edit.date().toString(Qt.ISODate)
                    begin_time = str(begin_date) + ' 00:00:00'
                    begin_second = time.mktime(time.strptime(begin_time, '%Y-%m-%d %H:%M:%S'))
                    end_date = self.queues_tab_end_date_edit.date().toString(Qt.ISODate)
                    end_time = str(end_date) + ' 23:59:59'
                    end_second = time.mktime(time.strptime(end_time, '%Y-%m-%d %H:%M:%S'))
                    select_condition = 'WHERE sample_second>=' + str(begin_second) + ' AND sample_second<=' + str(end_second)
                    data_dic = common_sqlite3.get_sql_table_data(queue_db_file, queue_db_conn, table_name, ['sample_time', 'TOTAL', 'PEND', 'RUN'], select_condition)

                    if not data_dic:
                        common.bprint(f'Queue pend/run job number information is empty for "{queue}".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                    else:
                        tmp_date_dic = {}

                        for i, sample_time in enumerate(data_dic['sample_time']):
                            if self.enable_queue_detail:
                                date = sample_time
                            else:
                                date = re.sub(r'_.*', '', sample_time)

                            tmp_date_dic.setdefault(date, {'total': [], 'pend': [], 'run': []})
                            tmp_date_dic[date]['total'].append(int(data_dic['TOTAL'][i]))
                            tmp_date_dic[date]['pend'].append(int(data_dic['PEND'][i]))
                            tmp_date_dic[date]['run'].append(int(data_dic['RUN'][i]))

                        for date in tmp_date_dic.keys():
                            queue_date_dic.setdefault(date, {})
                            queue_date_dic[date].setdefault(queue, {'total': [], 'pend': [], 'run': []})
                            queue_date_dic[date][queue]['total'] = int(sum(tmp_date_dic[date]['total'])/len(tmp_date_dic[date]['total']))
                            queue_date_dic[date][queue]['pend'] = int(sum(tmp_date_dic[date]['pend'])/len(tmp_date_dic[date]['pend']))
                            queue_date_dic[date][queue]['run'] = int(sum(tmp_date_dic[date]['run'])/len(tmp_date_dic[date]['run']))

                queue_db_conn.close()

        return queue_date_dic

    def draw_queues_tab_num_curve(self, fig, queue_list, date_list, total_list, pend_list, run_list):
        """
        Draw RUN/PEND job num curve for specified queue(s).
        """
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)

        # Get queue string.
        if len(queue_list) == 0:
            queue_string = ''
        elif len(queue_list) == 1:
            queue_string = queue_list[0]
        else:
            queue_string = str(queue_list[0]) + '...'

        if self.dark_mode:
            axes.set_facecolor('#19232d')

            for spine in axes.spines.values():
                spine.set_color('white')

            axes.tick_params(axis='both', colors='white')
            axes.set_title('Trends of RUN/PEND number for queues "' + str(queue_string) + '"', color='white')

            if self.enable_queue_detail:
                axes.set_xlabel('Sample Time', color='white')
            else:
                axes.set_xlabel('Sample Date', color='white')

            axes.set_ylabel('Num', color='white')
        else:
            axes.set_title('Trends of RUN/PEND number for queues "' + str(queue_string) + '"')

            if self.enable_queue_detail:
                axes.set_xlabel('Sample Time')
            else:
                axes.set_xlabel('Sample Date')

            axes.set_ylabel('Num')

        if self.enable_queue_detail:
            expected_linewidth = 0.1
            expected_markersize = 0.1
        else:
            expected_linewidth = 1
            expected_markersize = 1

        axes.plot(date_list, total_list, 'bo-', label='SLOTS', linewidth=expected_linewidth, markersize=expected_markersize)
        axes.fill_between(date_list, total_list, color='lightblue', alpha=0.3)
        axes.plot(date_list, run_list, 'go-', label='RUN', linewidth=expected_linewidth, markersize=expected_markersize)
        axes.fill_between(date_list, run_list, color='green', alpha=0.3)
        axes.plot(date_list, pend_list, 'ro-', label='PEND', linewidth=expected_linewidth, markersize=expected_markersize)
        axes.fill_between(date_list, pend_list, color='red', alpha=0.5)
        axes.legend(loc='upper right')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.queues_tab_num_canvas.draw()
# For queues TAB (end) #

# For utilization TAB (start) #
    def gen_utilization_tab(self):
        """
        Generate the utilization tab on lsfMonitor GUI, show host utilization (slot/cpu/mem) information.
        """
        self.utilization_tab_resource_list = ['slot', 'cpu', 'mem']

        # self.utilization_tab
        self.utilization_tab_frame0 = QFrame(self.utilization_tab)
        self.utilization_tab_frame0.setFrameShadow(QFrame.Raised)
        self.utilization_tab_frame0.setFrameShape(QFrame.Box)

        self.utilization_tab_table = QTableWidget(self.utilization_tab)
        self.utilization_tab_table.itemClicked.connect(self.utilization_tab_check_click)

        self.utilization_tab_frame1 = QFrame(self.utilization_tab)
        self.utilization_tab_frame1.setFrameShadow(QFrame.Raised)
        self.utilization_tab_frame1.setFrameShape(QFrame.Box)

        # self.utilization_tab - Grid
        utilization_tab_grid = QGridLayout()

        utilization_tab_grid.addWidget(self.utilization_tab_frame0, 0, 0, 1, 2)
        utilization_tab_grid.addWidget(self.utilization_tab_table, 1, 0)
        utilization_tab_grid.addWidget(self.utilization_tab_frame1, 1, 1)

        utilization_tab_grid.setRowStretch(0, 1)
        utilization_tab_grid.setRowStretch(1, 10)

        utilization_tab_grid.setColumnStretch(0, 38)
        utilization_tab_grid.setColumnStretch(1, 62)

        self.utilization_tab.setLayout(utilization_tab_grid)

        # Generate sub-frame
        self.gen_utilization_tab_frame0()
        self.gen_utilization_tab_table()
        self.gen_utilization_tab_frame1()

    def gen_utilization_tab_frame0(self):
        # self.utilization_tab_frame0
        # "Cluster" item.
        utilization_tab_cluster_label = QLabel('Cluster', self.utilization_tab_frame0)
        utilization_tab_cluster_label.setStyleSheet("font-weight: bold;")
        utilization_tab_cluster_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_cluster_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0, enableFilter=True)
        self.set_utilization_tab_cluster_combo()
        self.utilization_tab_cluster_combo.currentTextChanged.connect(self.update_utilization_tab_queue_combo_by_cluster)

        # "Queue" item.
        utilization_tab_queue_label = QLabel('Queue', self.utilization_tab_frame0)
        utilization_tab_queue_label.setStyleSheet("font-weight: bold;")
        utilization_tab_queue_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_queue_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0, enableFilter=True)
        self.set_utilization_tab_queue_combo()

        # "Resource" item.
        utilization_tab_resource_label = QLabel('Resource', self.utilization_tab_frame0)
        utilization_tab_resource_label.setStyleSheet("font-weight: bold;")
        utilization_tab_resource_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_resource_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0)
        self.set_utilization_tab_resource_combo()

        # "Check" button.
        utilization_tab_check_button = QPushButton('Check', self.utilization_tab_frame0)
        utilization_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        utilization_tab_check_button.clicked.connect(self.update_utilization_tab_info)

        # "Begin_Date" item.
        utilization_tab_begin_date_label = QLabel('Begin_Date', self.utilization_tab_frame0)
        utilization_tab_begin_date_label.setStyleSheet("font-weight: bold;")
        utilization_tab_begin_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_begin_date_edit = QDateEdit(self.utilization_tab_frame0)
        self.utilization_tab_begin_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.utilization_tab_begin_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.utilization_tab_begin_date_edit.setCalendarPopup(True)
        self.utilization_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

        # "End_Date" item.
        utilization_tab_end_date_label = QLabel('End_Date', self.utilization_tab_frame0)
        utilization_tab_end_date_label.setStyleSheet("font-weight: bold;")
        utilization_tab_end_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_end_date_edit = QDateEdit(self.utilization_tab_frame0)
        self.utilization_tab_end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.utilization_tab_end_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.utilization_tab_end_date_edit.setCalendarPopup(True)
        self.utilization_tab_end_date_edit.setDate(QDate.currentDate())

        # empty item.
        utilization_tab_empty_label = QLabel('', self.utilization_tab_frame0)

        # self.utilization_tab_frame0 - Grid
        utilization_tab_frame0_grid = QGridLayout()

        utilization_tab_frame0_grid.addWidget(utilization_tab_cluster_label, 0, 0)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_cluster_combo, 0, 1)
        utilization_tab_frame0_grid.addWidget(utilization_tab_queue_label, 0, 2)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_queue_combo, 0, 3)
        utilization_tab_frame0_grid.addWidget(utilization_tab_resource_label, 0, 4)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_resource_combo, 0, 5)
        utilization_tab_frame0_grid.addWidget(utilization_tab_check_button, 0, 6)
        utilization_tab_frame0_grid.addWidget(utilization_tab_begin_date_label, 1, 0)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_begin_date_edit, 1, 1)
        utilization_tab_frame0_grid.addWidget(utilization_tab_end_date_label, 1, 2)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_end_date_edit, 1, 3)
        utilization_tab_frame0_grid.addWidget(utilization_tab_empty_label, 1, 4, 1, 3)

        utilization_tab_frame0_grid.setColumnStretch(0, 1)
        utilization_tab_frame0_grid.setColumnStretch(1, 2)
        utilization_tab_frame0_grid.setColumnStretch(2, 1)
        utilization_tab_frame0_grid.setColumnStretch(3, 2)
        utilization_tab_frame0_grid.setColumnStretch(4, 1)
        utilization_tab_frame0_grid.setColumnStretch(5, 1)
        utilization_tab_frame0_grid.setColumnStretch(6, 1)

        self.utilization_tab_frame0.setLayout(utilization_tab_frame0_grid)

    def set_utilization_tab_cluster_combo(self, checked_cluster_list=None):
        """
        Set (initialize) self.utilization_tab_cluster_combo.
        """
        self.utilization_tab_cluster_combo.clear()
        db_root_path = Path(config.db_path)
        cluster_list = []

        if db_root_path.exists() and db_root_path.is_dir():
            for entry in os.scandir(db_root_path):
                if entry.is_dir() and entry.name != 'log':
                    cluster_list.append(entry.name)

        cluster_list.sort()
        cluster_list.insert(0, 'ALL')

        for cluster in cluster_list:
            self.utilization_tab_cluster_combo.addCheckBoxItem(cluster)

        # 默认选中当前集群
        if checked_cluster_list is None:
            checked_cluster_list = [self.cluster]

        # Set to checked status for checked_cluster_list.
        for (i, qBox) in enumerate(self.utilization_tab_cluster_combo.checkBoxList):
            if (qBox.text() in checked_cluster_list) and (qBox.isChecked() is False):
                self.utilization_tab_cluster_combo.checkBoxList[i].setChecked(True)

    def set_utilization_tab_queue_combo(self, checked_queue_list=None):
        """
        Set (initialize) self.utilization_tab_queue_combo.
        """
        self.utilization_tab_queue_combo.clear()
        db_root_path = Path(config.db_path)
        queue_list = []

        # 获取所有集群的所有队列
        if db_root_path.exists() and db_root_path.is_dir():
            for entry in os.scandir(db_root_path):
                if entry.is_dir() and entry.name != 'log':
                    cluster = entry.name
                    cluster_db_path = db_root_path / cluster
                    queue_host_mapping_db = cluster_db_path / 'queue_host_mapping.db'

                    if queue_host_mapping_db.exists():
                        # 读取当前集群的所有队列
                        (result, conn) = common_sqlite3.connect_db_file(str(queue_host_mapping_db))

                        if result == 'passed':
                            table_list = common_sqlite3.get_sql_table_list(str(queue_host_mapping_db), conn)
                            cluster_queues = [re.sub(r'^queue_', '', table) for table in table_list if table.startswith('queue_')]

                            for queue in cluster_queues:
                                queue_list.append(f"{cluster}-{queue}")

                            conn.close()

        queue_list.sort()
        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.utilization_tab_queue_combo.addCheckBoxItem(queue)

        # 处理默认选中逻辑
        if checked_queue_list is None:
            # 默认选中当前集群的所有队列
            checked_queue_list = []

            for queue in queue_list:
                if queue == 'ALL':
                    continue

                if '-' in queue:
                    q_cluster = queue.split('-', 1)[0]

                    if q_cluster == self.cluster:
                        checked_queue_list.append(queue)

        # Set to checked status for checked_queue_list.
        for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
            if (qBox.text() in checked_queue_list) and (qBox.isChecked() is False):
                self.utilization_tab_queue_combo.checkBoxList[i].setChecked(True)
            elif (qBox.text() not in checked_queue_list) and (qBox.isChecked() is True):
                self.utilization_tab_queue_combo.checkBoxList[i].setChecked(False)

    def update_utilization_tab_queue_combo_by_cluster(self):
        """
        Update queue checked state when cluster selection changes.
        仅调整Queue选中状态，不修改队列列表
        """
        # 获取选中的集群
        selected_cluster_dic = self.utilization_tab_cluster_combo.selectedItems()
        selected_clusters = sorted(list(selected_cluster_dic.values())) if selected_cluster_dic else []
        select_all = False

        if 'ALL' in selected_clusters:
            select_all = True
            # 选中ALL时获取所有集群
            selected_clusters = []
            db_root_path = Path(config.db_path)

            if db_root_path.exists() and db_root_path.is_dir():
                for entry in os.scandir(db_root_path):
                    if entry.is_dir() and entry.name != 'log':
                        selected_clusters.append(entry.name)

        # 调整队列选中状态
        if not selected_clusters:
            # 没有选中任何集群时，所有队列都不选中
            for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
                qBox.setChecked(False)
        elif select_all:
            # 选中Cluster的ALL时，Queue只需要选中ALL即可，不需要选中所有队列
            for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
                if qBox.text() == 'ALL':
                    qBox.setChecked(True)
                else:
                    qBox.setChecked(False)
        else:
            for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
                queue_name = qBox.text()

                if queue_name == 'ALL':
                    # 选中部分集群时ALL保持选中
                    qBox.setChecked(True)
                    continue

                # 获取队列所属集群
                if '-' in queue_name:
                    queue_cluster = queue_name.split('-', 1)[0]

                    if queue_cluster in selected_clusters:
                        qBox.setChecked(True)
                    else:
                        qBox.setChecked(False)
                else:
                    # 特殊队列（如lost_and_found）默认不选中
                    qBox.setChecked(False)

    def set_utilization_tab_resource_combo(self):
        """
        Set (initialize) self.utilization_tab_date_combo.
        """
        self.utilization_tab_resource_combo.clear()

        resource_list = copy.deepcopy(self.utilization_tab_resource_list)

        for resource in resource_list:
            self.utilization_tab_resource_combo.addCheckBoxItem(resource)

        # Set all resources as checked.
        for (i, qBox) in enumerate(self.utilization_tab_resource_combo.checkBoxList):
            self.utilization_tab_resource_combo.checkBoxList[i].setChecked(True)

    def update_utilization_tab_info(self):
        """
        Update self.utilization_tab_table and self.utilization_tab_frame1.
        """
        # 先清空所有旧数据，避免切换Cluster时残留
        self.utilization_full_time_data = None
        self.utilization_time_range = None
        # 清空表格
        self.utilization_tab_table.setRowCount(0)

        return_data = self.get_queue_utilization_info()

        if not return_data:
            self.update_utilization_tab_frame1()
            return

        queue_utilization_dic, full_time_util, begin_second, end_second = return_data

        # 保存到实例变量供图表使用
        self.utilization_full_time_data = full_time_util
        self.utilization_time_range = (begin_second, end_second)

        if queue_utilization_dic:
            self.gen_utilization_tab_table(queue_utilization_dic)

        self.update_utilization_tab_frame1()

    def get_historical_queue_host_mapping(self, db_path, begin_second, end_second):
        """
        Get all queue-host mappings in the specified time range.
        Returns:
            - queue_list: all queues existed in the time range
            - mapping_matrix: list of (start_second, end_second, queue_host_dic)
            - current_queue_list: current existing queues (to mark deleted queues)
        """
        queue_host_mapping_db_file = str(db_path) + '/queue_host_mapping.db'
        mapping_matrix = []
        all_queues = set()
        queue_change_points = []

        if not os.path.exists(queue_host_mapping_db_file):
            common.bprint(f'Queue-host mapping database "{queue_host_mapping_db_file}" is missing.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            # Fallback to current mapping if no historical data
            self.fresh_lsf_info('queue_host')
            return list(self.queue_host_dic.keys()), [(begin_second, end_second, self.queue_host_dic)], list(self.queue_host_dic.keys())

        (result, conn) = common_sqlite3.connect_db_file(queue_host_mapping_db_file)

        if result == 'failed':
            common.bprint('Failed to connect queue-host mapping database.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            self.fresh_lsf_info('queue_host')
            return list(self.queue_host_dic.keys()), [(begin_second, end_second, self.queue_host_dic)], list(self.queue_host_dic.keys())

        # Get all queue tables
        table_list = common_sqlite3.get_sql_table_list(queue_host_mapping_db_file, conn)
        queue_list = [re.sub(r'^queue_', '', table) for table in table_list if table.startswith('queue_')]

        # Collect all change points
        for queue in queue_list:
            table_name = f'queue_{queue}'
            # Get all mapping records in time range
            data = common_sqlite3.get_sql_table_data(
                queue_host_mapping_db_file, conn, table_name,
                ['sample_second', 'hosts'],
                f"WHERE sample_second BETWEEN {begin_second - 86400} AND {end_second + 86400} ORDER BY sample_second"
            )

            if data and data['sample_second']:
                for i, sample_second in enumerate(data['sample_second']):
                    hosts = data['hosts'][i].split()
                    queue_change_points.append((int(sample_second), queue, hosts))

                all_queues.add(queue)

        # Get current existing queues: 仅能获取当前集群的队列列表，其他集群无法通过LSF命令查询
        current_cluster_db_path = str(self.cluster_db_path)

        if db_path == current_cluster_db_path:
            self.fresh_lsf_info('bqueues')
            current_queue_list = self.bqueues_dic.get('QUEUE_NAME', []) if hasattr(self, 'bqueues_dic') else []
        else:
            # 其他集群的队列默认视为存在，不标记deleted状态
            current_queue_list = list(all_queues)

        # Sort change points by time
        queue_change_points.sort(key=lambda x: x[0])

        # Generate time slices
        if not queue_change_points:
            # No changes in time range, use current mapping
            self.fresh_lsf_info('queue_host')
            return list(all_queues) if all_queues else list(self.queue_host_dic.keys()), [(begin_second, end_second, self.queue_host_dic)], current_queue_list

        # Build mapping matrix
        current_mapping = {}

        # Initialize mapping: if no records before begin_second, use queue's earliest record as default for pre-begin time
        for queue in queue_list:
            table_name = f'queue_{queue}'
            # First try to get the latest record before begin_second
            data = common_sqlite3.get_sql_table_data(
                queue_host_mapping_db_file, conn, table_name,
                ['sample_second', 'hosts'],
                f"WHERE sample_second <= {begin_second} ORDER BY sample_second DESC LIMIT 1"
            )

            if data and data['hosts']:
                current_mapping[queue] = data['hosts'][0].split()
            else:
                # No records before begin_second, get queue's earliest record to use for pre-begin period
                earliest_data = common_sqlite3.get_sql_table_data(
                    queue_host_mapping_db_file, conn, table_name,
                    ['sample_second', 'hosts'],
                    "ORDER BY sample_second ASC LIMIT 1"
                )

                if earliest_data and earliest_data['hosts']:
                    current_mapping[queue] = earliest_data['hosts'][0].split()

        # Process change points to build time slices
        all_change_times = sorted(list(set([cp[0] for cp in queue_change_points] + [begin_second, end_second])))

        for i in range(len(all_change_times) - 1):
            slice_start = max(all_change_times[i], begin_second)
            slice_end = min(all_change_times[i+1], end_second)

            if slice_start >= slice_end:
                continue

            # Update mapping for this slice
            for cp_time, queue, hosts in queue_change_points:
                if cp_time == all_change_times[i]:
                    current_mapping[queue] = hosts

            # Add to matrix
            mapping_matrix.append((slice_start, slice_end, copy.deepcopy(current_mapping)))

        conn.close()

        return sorted(list(all_queues)), mapping_matrix, current_queue_list

    def get_queue_utilization_info(self):
        """
        Get queue utilization info from sqlite database using historical queue-host mapping, support multi-cluster.
        """
        import pandas as pd

        common.bprint('Loading queue utilization info ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading queue utilization info ...')
        my_show_message.start()

        # Get time range
        begin_date = self.utilization_tab_begin_date_edit.date().toString(Qt.ISODate)
        original_begin_second = int(time.mktime(time.strptime(f"{begin_date} 00:00:00", '%Y-%m-%d %H:%M:%S')))
        end_date = self.utilization_tab_end_date_edit.date().toString(Qt.ISODate)
        original_end_second = int(time.mktime(time.strptime(f"{end_date} 23:59:59", '%Y-%m-%d %H:%M:%S')))
        # 缓存查询用的时间范围
        begin_second = original_begin_second
        end_second = original_end_second

        # 获取选中的集群
        db_root_path = Path(config.db_path)
        selected_cluster_dic = self.utilization_tab_cluster_combo.selectedItems()
        selected_clusters = sorted(list(selected_cluster_dic.values())) if selected_cluster_dic else []

        if 'ALL' in selected_clusters:
            selected_clusters = []

            if db_root_path.exists() and db_root_path.is_dir():
                for entry in os.scandir(db_root_path):
                    if entry.is_dir() and entry.name != 'log':
                        selected_clusters.append(entry.name)

        # 获取选中的队列
        selected_queue_dic = self.utilization_tab_queue_combo.selectedItems()
        selected_queues = list(selected_queue_dic.values()) if selected_queue_dic else []

        # Generate cache key (增量查询优化：key包含集群和队列，同条件下复用缓存)
        cache_base_key = (tuple(selected_clusters), tuple(selected_queues), self.enable_utilization_detail)

        # Check cache
        current_time = time.time()
        cached_full_util = None
        cached_begin = 0
        cached_end = 0

        if cache_base_key in self.utilization_cache:
            cached_full_util, cache_time, cached_begin, cached_end = self.utilization_cache[cache_base_key]

            if current_time - cache_time < self.utilization_cache_timeout:
                # 检查新查询的时间范围是否完全在缓存范围内
                if begin_second >= cached_begin and end_second <= cached_end:
                    common.bprint('Using cached utilization data (full hit).', date_format='%Y-%m-%d %H:%M:%S')
                    # 从缓存中截取需要的时间段
                    queue_utilization_dic = {}

                    for queue in cached_full_util:
                        queue_utilization_dic[queue] = {'is_deleted': cached_full_util[queue]['is_deleted']}

                        for res in ['slot', 'cpu', 'mem']:
                            vals = []

                            for ts_key, val in cached_full_util[queue][res].items():
                                if self.enable_utilization_detail:
                                    ts = int(time.mktime(time.strptime(ts_key, '%Y%m%d_%H%M%S')))
                                else:
                                    ts = int(time.mktime(time.strptime(f"{ts_key[:4]}-{ts_key[4:6]}-{ts_key[6:8]} 12:00:00", '%Y-%m-%d %H:%M:%S')))

                                if begin_second <= ts <= end_second:
                                    vals.append(val)
                            if vals:
                                queue_utilization_dic[queue][res] = round(sum(vals) / len(vals), 1)
                            else:
                                queue_utilization_dic[queue][res] = 0.0

                    time.sleep(0.01)
                    my_show_message.terminate()
                    # 缓存命中时也返回完整数据
                    return queue_utilization_dic, cached_full_util, original_begin_second, original_end_second
                else:
                    common.bprint('Using cached utilization data (partial hit), only querying new time range.', date_format='%Y-%m-%d %H:%M:%S')
                    # 部分命中，扩展时间范围查询
                    new_begin = min(begin_second, cached_begin)
                    new_end = max(end_second, cached_end)

                    if new_begin == begin_second and new_end == end_second:
                        # 完全不重叠，重新查询
                        cached_full_util = None
                    else:
                        # 调整查询范围为新增部分
                        if begin_second < cached_begin:
                            query_begin = begin_second
                            query_end = cached_begin
                        else:
                            query_begin = cached_end
                            query_end = end_second

                        begin_second, end_second = query_begin, query_end

        # Clean up expired cache
        keys_to_delete = [k for k, (_, t, _, _) in self.utilization_cache.items() if current_time - t > self.utilization_cache_timeout]

        for k in keys_to_delete:
            del self.utilization_cache[k]

        # Limit cache size to 20 entries
        if len(self.utilization_cache) > 20:
            oldest_key = sorted(self.utilization_cache.keys(), key=lambda k: self.utilization_cache[k][1])[0]
            del self.utilization_cache[oldest_key]

        # Update message: loading historical mapping
        # 解析队列和集群的映射
        queue_cluster_map = {}
        queue_full_name_map = {}
        only_all_selected = len(selected_queues) == 1 and 'ALL' in selected_queues

        for q in selected_queues:
            if q == 'ALL':
                continue

            if '-' in q:
                cluster, queue_name = q.split('-', 1)

                if cluster in selected_clusters:
                    if cluster not in queue_cluster_map:
                        queue_cluster_map[cluster] = []

                    queue_cluster_map[cluster].append(queue_name)
                    queue_full_name_map[(cluster, queue_name)] = q

        # 遍历每个集群收集数据
        all_time_based_util = {}
        all_queue_avg = {}

        for cluster in selected_clusters:
            cluster_db_path = db_root_path / cluster

            if not cluster_db_path.exists():
                continue

            # Update message: loading historical mapping for cluster
            time.sleep(0.01)
            my_show_message.terminate()
            my_show_message = ShowMessage('Info', f'Loading historical mapping for cluster {cluster} ...')
            my_show_message.start()
            QApplication.processEvents()

            # Get historical mapping for current cluster
            historical_queue_list, mapping_matrix, current_queue_list = self.get_historical_queue_host_mapping(str(cluster_db_path), original_begin_second, original_end_second)

            # 确定当前集群要处理的队列
            if only_all_selected or 'ALL' in selected_queues:
                cluster_process_queues = historical_queue_list
            else:
                cluster_process_queues = queue_cluster_map.get(cluster, [])

            if not cluster_process_queues:
                continue

            # Collect all hosts involved for current cluster
            all_hosts = set()

            for _, _, mapping in mapping_matrix:
                if only_all_selected or 'ALL' in selected_queues:
                    for queue in mapping:
                        all_hosts.update(mapping[queue])
                else:
                    for queue in cluster_process_queues:
                        if queue in mapping:
                            all_hosts.update(mapping[queue])

            all_hosts = list(all_hosts)

            if not all_hosts:
                continue

            # Update message: loading host utilization data for cluster
            time.sleep(0.01)
            my_show_message.terminate()
            my_show_message = ShowMessage('Info', f'Loading {len(all_hosts)} hosts data for cluster {cluster} ...')
            my_show_message.start()
            QApplication.processEvents()

            # Get utilization data for current cluster
            df_cluster = pd.DataFrame()

            if self.enable_utilization_detail:
                db_file = str(cluster_db_path) + '/utilization.db'
            else:
                db_file = str(cluster_db_path) + '/utilization_day.db'

            if os.path.exists(db_file):
                (result, conn) = common_sqlite3.connect_db_file(db_file)

                if result == 'passed':
                    host_dfs = []

                    for host in all_hosts:
                        table_name = f'utilization_{host}'

                        if table_name not in common_sqlite3.get_sql_table_list(db_file, conn):
                            continue

                        if self.enable_utilization_detail:
                            key_list = ['sample_second', 'slot', 'cpu', 'mem']
                            select_condition = f"WHERE sample_second BETWEEN {original_begin_second} AND {original_end_second}"
                        else:
                            key_list = ['sample_date', 'slot', 'cpu', 'mem']
                            begin_date_str = re.sub('-', '', begin_date)
                            end_date_str = re.sub('-', '', end_date)
                            select_condition = f"WHERE sample_date BETWEEN '{begin_date_str}' AND '{end_date_str}'"

                        data = common_sqlite3.get_sql_table_data(db_file, conn, table_name, key_list, select_condition)

                        if data:
                            df_host = pd.DataFrame(data)
                            df_host['host'] = host

                            if self.enable_utilization_detail:
                                df_host['sample_second'] = df_host['sample_second'].astype(np.int64)

                            host_dfs.append(df_host)

                    if host_dfs:
                        df_cluster = pd.concat(host_dfs, ignore_index=True)

                    conn.close()

            # 转换数值类型
            for res in ['slot', 'cpu', 'mem']:
                if res in df_cluster.columns:
                    df_cluster[res] = pd.to_numeric(df_cluster[res], errors='coerce').fillna(0).clip(upper=100)

            if df_cluster.empty:
                continue

            # Update message: calculating utilization for cluster
            time.sleep(0.01)
            my_show_message.terminate()
            my_show_message = ShowMessage('Info', f'Calculating utilization for cluster {cluster} ...')
            my_show_message.start()
            QApplication.processEvents()

            # 生成时间key
            if self.enable_utilization_detail:
                df_cluster['time_key'] = df_cluster['sample_second'].apply(lambda x: time.strftime('%Y%m%d_%H%M%S', time.localtime(x)))
                df_cluster['ts'] = df_cluster['sample_second']
            else:
                df_cluster['ts'] = pd.to_datetime(df_cluster['sample_date'], format='%Y%m%d').astype(np.int64) // 10**9 + 12*3600
                df_cluster['time_key'] = df_cluster['sample_date']

            # 处理当前集群的每个时间切片
            for slice_idx, (slice_start, slice_end, mapping) in enumerate(mapping_matrix):
                if slice_end - slice_start <= 0:
                    continue

                mask = (df_cluster['ts'] >= slice_start) & (df_cluster['ts'] <= slice_end)
                df_slice = df_cluster[mask].copy()

                if df_slice.empty:
                    continue

                # 处理单个队列
                for queue in cluster_process_queues:
                    if queue not in mapping:
                        continue

                    full_queue_name = queue_full_name_map.get((cluster, queue), f"{cluster}-{queue}")

                    if full_queue_name not in all_time_based_util:
                        all_time_based_util[full_queue_name] = {'slot': {}, 'cpu': {}, 'mem': {}}
                        all_queue_avg[full_queue_name] = {'is_deleted': queue not in current_queue_list}

                    queue_hosts = mapping[queue]
                    df_queue = df_slice[df_slice['host'].isin(queue_hosts)]

                    if df_queue.empty:
                        continue

                    for res in ['slot', 'cpu', 'mem']:
                        grouped = df_queue.groupby('time_key')[res].mean().round(1)

                        for time_key, avg_val in grouped.items():
                            if not self.enable_utilization_detail:
                                all_time_based_util[full_queue_name][res][time_key] = avg_val
                            else:
                                if time_key not in all_time_based_util[full_queue_name][res]:
                                    all_time_based_util[full_queue_name][res][time_key] = []

                                all_time_based_util[full_queue_name][res][time_key].append(avg_val)

                # 处理当前集群的ALL队列数据，汇总到全局ALL
                if 'ALL' not in all_time_based_util:
                    all_time_based_util['ALL'] = {'slot': {}, 'cpu': {}, 'mem': {}}
                    all_queue_avg['ALL'] = {'is_deleted': False}

                all_slice_hosts = set()

                for queue in cluster_process_queues:
                    if queue in mapping:
                        all_slice_hosts.update(mapping[queue])

                df_all_queue = df_slice[df_slice['host'].isin(all_slice_hosts)]

                if not df_all_queue.empty:
                    for res in ['slot', 'cpu', 'mem']:
                        grouped = df_all_queue.groupby('time_key')[res].mean().round(1)

                        for time_key, avg_val in grouped.items():
                            if time_key not in all_time_based_util['ALL'][res]:
                                all_time_based_util['ALL'][res][time_key] = []

                            all_time_based_util['ALL'][res][time_key].append(avg_val)

        # 合并所有集群的数据，计算平均值
        queue_utilization_dic = copy.deepcopy(all_queue_avg)
        full_time_util = {}

        # 处理detail模式下的多值平均
        for queue in all_time_based_util:
            full_time_util[queue] = {'is_deleted': queue_utilization_dic[queue]['is_deleted']}

            for res in ['slot', 'cpu', 'mem']:
                res_data = all_time_based_util[queue][res]
                avg_vals = {}
                all_vals = []

                for time_key, vals in res_data.items():
                    if isinstance(vals, list):
                        avg_val = round(sum(vals) / len(vals), 1)
                    else:
                        avg_val = vals

                    avg_vals[time_key] = avg_val
                    all_vals.append(avg_val)

                full_time_util[queue][res] = avg_vals

                if all_vals:
                    queue_utilization_dic[queue][res] = round(sum(all_vals) / len(all_vals), 1)
                else:
                    queue_utilization_dic[queue][res] = 0.0

        # 合并缓存数据（如果有部分命中）
        if cached_full_util is not None:
            # 合并新旧时间序列数据
            for queue in full_time_util:
                if queue not in cached_full_util:
                    cached_full_util[queue] = {'is_deleted': full_time_util[queue]['is_deleted']}

                    for res in ['slot', 'cpu', 'mem']:
                        cached_full_util[queue][res] = {}

                # 合并每个时间点的数据
                for res in ['slot', 'cpu', 'mem']:
                    cached_full_util[queue][res].update(full_time_util[queue][res])

            # 使用合并后的完整数据
            full_time_util = cached_full_util

        # 保存到缓存
        cache_begin = min(original_begin_second, cached_begin) if cached_full_util is not None else original_begin_second
        cache_end = max(original_end_second, cached_end) if cached_full_util is not None else original_end_second
        self.utilization_cache[cache_base_key] = (full_time_util, current_time, cache_begin, cache_end)

        time.sleep(0.01)
        my_show_message.terminate()

        if not queue_utilization_dic:
            common.bprint('No utilization data found for selected clusters and queues.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            return None

        return queue_utilization_dic, full_time_util, original_begin_second, original_end_second

    def gen_utilization_tab_table(self, queue_utilization_dic={}):
        """
        Generte self.utilization_tab_table.
        """
        self.utilization_tab_table.setShowGrid(True)
        self.utilization_tab_table.setSortingEnabled(False)
        self.utilization_tab_table.setColumnCount(0)
        self.utilization_tab_table.setColumnCount(5)
        self.utilization_tab_table.setRowCount(0)
        self.utilization_tab_table.setRowCount(len(queue_utilization_dic))
        self.utilization_tab_table_title_list = ['Queue', 'slots', 'slot(%)', 'cpu(%)', 'mem(%)']
        self.utilization_tab_table.setHorizontalHeaderLabels(self.utilization_tab_table_title_list)

        self.utilization_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.utilization_tab_table.setColumnWidth(1, 70)
        self.utilization_tab_table.setColumnWidth(2, 60)
        self.utilization_tab_table.setColumnWidth(3, 60)
        self.utilization_tab_table.setColumnWidth(4, 60)

        # Fresh LSF bhosts/queues/queue_host information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('queue_host')

        # Fill self.utilization_tab_table items.
        if queue_utilization_dic:
            row = -1

            for queue in queue_utilization_dic.keys():
                row += 1
                queue_data = queue_utilization_dic[queue]
                is_deleted = queue_data.get('is_deleted', False) if isinstance(queue_data, dict) else False

                # Fill "Queue" item.
                queue_display = queue

                if is_deleted:
                    queue_display = f"{queue} (deleted)"

                item = QTableWidgetItem(queue_display)
                item.setFont(QFont('song', 9, QFont.Bold))

                if is_deleted:
                    item.setForeground(QBrush(Qt.gray))

                self.utilization_tab_table.setItem(row, 0, item)

                # Fill "slots" item.
                total = 0

                if queue == 'ALL':
                    # Get all selected queues (exclude ALL itself)
                    selected_queues = [q for q in queue_utilization_dic.keys() if q != 'ALL']
                    has_na = False
                    all_hosts = set()

                    for q in selected_queues:
                        # 检查是否有跨集群队列、已删除队列或不存在的队列
                        queue_name = q

                        if '-' in q:
                            q_cluster, queue_name = q.split('-', 1)

                            if q_cluster != self.cluster:
                                has_na = True
                                break

                        if queue_name not in self.queue_host_dic:
                            has_na = True
                            break

                        all_hosts.update(self.queue_host_dic[queue_name])

                    if has_na:
                        total = 'N/A'
                    else:
                        # Calculate sum of slots for unique hosts
                        total = 0

                        if 'HOST_NAME' in self.bhosts_dic and 'MAX' in self.bhosts_dic:
                            for host in all_hosts:
                                if host in self.bhosts_dic['HOST_NAME']:
                                    host_index = self.bhosts_dic['HOST_NAME'].index(host)
                                    host_max = self.bhosts_dic['MAX'][host_index]

                                    if re.match(r'^\d+$', host_max):
                                        total += int(host_max)
                elif queue == 'lost_and_found' or is_deleted:
                    total = 'N/A'
                else:
                    # 检查是否是跨集群队列
                    if '-' in queue:
                        q_cluster = queue.split('-', 1)[0]

                        if q_cluster != self.cluster:
                            total = 'N/A'
                        else:
                            # 同集群的带前缀队列，提取队列名查询
                            queue_name = queue.split('-', 1)[1]

                            if queue_name in self.queue_host_dic:
                                for queue_host in self.queue_host_dic[queue_name]:
                                    if 'HOST_NAME' in self.bhosts_dic:
                                        if queue_host in self.bhosts_dic['HOST_NAME']:
                                            host_index = self.bhosts_dic['HOST_NAME'].index(queue_host)
                                            host_max = self.bhosts_dic['MAX'][host_index]

                                            if re.match(r'^\d+$', host_max):
                                                total += int(host_max)
                            else:
                                total = 'N/A'
                    else:
                        # 当前集群普通队列
                        if queue in self.queue_host_dic:
                            for queue_host in self.queue_host_dic[queue]:
                                if 'HOST_NAME' in self.bhosts_dic:
                                    if queue_host in self.bhosts_dic['HOST_NAME']:
                                        host_index = self.bhosts_dic['HOST_NAME'].index(queue_host)
                                        host_max = self.bhosts_dic['MAX'][host_index]

                                        if re.match(r'^\d+$', host_max):
                                            total += int(host_max)
                        else:
                            total = 'N/A'

                item = QTableWidgetItem()

                if queue == 'lost_and_found' or is_deleted:
                    item.setForeground(QBrush(Qt.gray))

                if total == 'N/A':
                    item.setData(Qt.DisplayRole, str(total))
                else:
                    item.setData(Qt.DisplayRole, int(total))

                self.utilization_tab_table.setItem(row, 1, item)

                for (i, resource) in enumerate(self.utilization_tab_resource_list):
                    # Fill <resource> item.
                    item = QTableWidgetItem()

                    if isinstance(queue_data, dict) and resource in queue_data:
                        item.setData(Qt.DisplayRole, queue_data[resource])
                    else:
                        item.setData(Qt.DisplayRole, queue_data)

                    if is_deleted:
                        item.setForeground(QBrush(Qt.gray))

                    self.utilization_tab_table.setItem(row, i+2, item)

        self.utilization_tab_table.setSortingEnabled(True)

    def utilization_tab_check_click(self, item=None):
        """
        If click QUEUE name, show queue slot/cpu/mem utilization information on UTILIZATION tab.
        """
        if item is not None:
            current_row = self.utilization_tab_table.currentRow()
            queue = self.utilization_tab_table.item(current_row, 0).text().strip()
            # Remove (deleted) suffix if exists
            queue = re.sub(r'\s*\(deleted\)$', '', queue)

            if item.column() == 0:
                common.bprint(f'Checking utilization for queue "{queue}".', date_format='%Y-%m-%d %H:%M:%S')

                self.set_utilization_tab_queue_combo(checked_queue_list=[queue, ])
                self.update_utilization_tab_info()

    def gen_utilization_tab_frame1(self):
        """
        Generte self.utilization_tab_frame1.
        """
        # self.utilization_tab_frame1
        self.utilization_tab_utilization_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.utilization_tab_utilization_toolbar = common_pyqt5.NavigationToolbar2QT(self.utilization_tab_utilization_canvas, self)

        if self.dark_mode:
            fig = self.utilization_tab_utilization_canvas.figure
            fig.set_facecolor('#19232d')

        # self.utilization_tab_frame1 - Grid
        utilization_tab_frame1_grid = QGridLayout()
        utilization_tab_frame1_grid.addWidget(self.utilization_tab_utilization_toolbar, 0, 0)
        utilization_tab_frame1_grid.addWidget(self.utilization_tab_utilization_canvas, 1, 0)
        self.utilization_tab_frame1.setLayout(utilization_tab_frame1_grid)

    def update_utilization_tab_frame1(self):
        """
        Draw Ut curve for specified queue on self.utilization_tab_frame1, 使用和左侧表格完全相同的计算结果，保证数据一致
        """
        # Generate figure.
        fig = self.utilization_tab_utilization_canvas.figure
        fig.clear()
        self.utilization_tab_utilization_canvas.draw()

        # 检查是否有已计算好的利用率数据
        if not hasattr(self, 'utilization_full_time_data') or not self.utilization_full_time_data:
            warning_message = 'No utilization data available, please click "Check" button first.'
            self.gui_warning(warning_message)
            return

        # Get selected queues
        selected_queue_dic = self.utilization_tab_queue_combo.selectedItems()
        selected_queues = list(selected_queue_dic.values()) if selected_queue_dic else []

        if not selected_queues:
            warning_message = 'No queue is specified on UTILIZATION tab.'
            self.gui_warning(warning_message)
            return

        selected_resource_dic = self.utilization_tab_resource_combo.selectedItems()
        selected_resource_list = list(selected_resource_dic.values())

        if not selected_resource_list:
            warning_message = 'No resource is specified on UTILIZATION tab.'
            self.gui_warning(warning_message)
            return

        # 获取时间范围
        begin_second, end_second = self.utilization_time_range

        # 无论选择多少队列，只显示汇聚的ALL曲线
        display_queues = ['ALL'] if 'ALL' in self.utilization_full_time_data else []

        if not display_queues:
            warning_message = 'No valid queue data available for selected queues.'
            self.gui_warning(warning_message)
            return

        # 为每个资源每个队列准备数据
        plot_data = {}
        title_lines = []

        # 资源颜色配置，和原版本保持完全一致
        resource_colors = {
            'slot': {'line': 'bo-', 'fill': 'lightblue', 'alpha': 0.3},
            'cpu': {'line': 'ro-', 'fill': 'red', 'alpha': 0.5},
            'mem': {'line': 'go-', 'fill': 'green', 'alpha': 0.3}
        }

        for queue in display_queues:
            queue_data = self.utilization_full_time_data[queue]

            # 计算队列的总平均，和左侧表格一致
            for res in selected_resource_list:
                res_vals = []

                for ts_key, val in queue_data[res].items():
                    # 只统计当前时间范围内的数据
                    if self.enable_utilization_detail:
                        ts = int(time.mktime(time.strptime(ts_key, '%Y%m%d_%H%M%S')))
                    else:
                        ts = int(time.mktime(time.strptime(f"{ts_key[:4]}-{ts_key[4:6]}-{ts_key[6:8]} 12:00:00", '%Y-%m-%d %H:%M:%S')))

                    if begin_second <= ts <= end_second:
                        res_vals.append(val)

                if res_vals:
                    avg_val = round(sum(res_vals) / len(res_vals), 1)
                    title_lines.append(f"{queue} {res}: {avg_val}%")

                    # 整理时间序列数据
                    time_series = []

                    for ts_key, val in queue_data[res].items():
                        if self.enable_utilization_detail:
                            ts = int(time.mktime(time.strptime(ts_key, '%Y%m%d_%H%M%S')))
                            dt = datetime.datetime.strptime(ts_key, '%Y%m%d_%H%M%S')
                        else:
                            ts = int(time.mktime(time.strptime(f"{ts_key[:4]}-{ts_key[4:6]}-{ts_key[6:8]} 12:00:00", '%Y-%m-%d %H:%M:%S')))
                            dt = datetime.datetime.strptime(ts_key, '%Y%m%d')

                        if begin_second <= ts <= end_second:
                            time_series.append((dt, val))

                    # 按时间排序
                    time_series.sort(key=lambda x: x[0])
                    date_list = [x[0] for x in time_series]
                    util_list = [min(x[1], 100.0) for x in time_series]

                    plot_key = f"{queue}_{res}"
                    plot_data[plot_key] = {
                        'queue': queue,
                        'resource': res,
                        'date_list': date_list,
                        'util_list': util_list
                    }

        if not plot_data:
            warning_message = 'No valid data to plot for the selected criteria.'
            self.gui_warning(warning_message)
            return

        # 绘制曲线
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)

        # 设置标题
        title = ';    '.join(title_lines)

        # 设置样式
        if self.dark_mode:
            axes.set_facecolor('#19232d')

            for spine in axes.spines.values():
                spine.set_color('white')

            axes.tick_params(axis='both', colors='white')

            if self.enable_utilization_detail:
                axes.set_xlabel('Sample Time', color='white')
            else:
                axes.set_xlabel('Sample Date', color='white')

            axes.set_ylabel('Utilization (%)', color='white')
            axes.set_title(title, color='white')
        else:
            if self.enable_utilization_detail:
                axes.set_xlabel('Sample Time')
            else:
                axes.set_xlabel('Sample Date')

            axes.set_ylabel('Utilization (%)')
            axes.set_title(title)

        # 绘制曲线
        for plot_key, data in plot_data.items():
            queue = data['queue']
            res = data['resource']
            date_list = data['date_list']
            util_list = data['util_list']

            # 选择颜色，和原版本保持完全一致
            line_color = resource_colors[res]['line']
            fill_color = resource_colors[res]['fill']
            fill_alpha = resource_colors[res]['alpha']

            # 设置线宽和标记大小
            if self.enable_utilization_detail:
                linewidth = 0.1
                markersize = 0.1
            else:
                linewidth = 1
                markersize = 1

            # 绘制曲线
            label = f"{queue}_{res.upper()}" if queue != 'ALL' else res.upper()
            axes.plot(date_list, util_list, line_color, label=label, linewidth=linewidth, markersize=markersize)

            # 只有ALL队列做填充，避免多队列时颜色叠加变色，和原版本效果保持一致
            if queue == 'ALL':
                axes.fill_between(date_list, util_list, color=fill_color, alpha=fill_alpha)

        axes.legend(loc='upper right')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.utilization_tab_utilization_canvas.draw()

# For utilization TAB (end) #

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
        self.license_tab_feature_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)

        self.license_tab_expires_label = QLabel('Expires Information', self.license_tab)
        self.license_tab_expires_label.setStyleSheet("font-weight: bold;")
        self.license_tab_expires_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)

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
        self.gen_license_tab_feature_table(self.license_dic)
        self.gen_license_tab_expires_table(self.license_dic)

        if self.specified_feature:
            self.license_tab_feature_line.setText(str(self.specified_feature))
            self.license_tab_user_line.setText(str(self.specified_user))
            self.update_license_info()

    def gen_license_tab_frame0(self):
        # self.license_tab_frame0
        # "Show" item.
        license_tab_show_label = QLabel('Show', self.license_tab_frame0)
        license_tab_show_label.setStyleSheet("font-weight: bold;")
        license_tab_show_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.license_tab_show_combo = common_pyqt5.QComboCheckBox(self.license_tab_frame0)
        self.set_license_tab_show_combo()

        # "Server" item.
        license_tab_server_label = QLabel('Server', self.license_tab_frame0)
        license_tab_server_label.setStyleSheet("font-weight: bold;")
        license_tab_server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.license_tab_server_combo = common_pyqt5.QComboCheckBox(self.license_tab_frame0)
        self.set_license_tab_server_combo()
        self.license_tab_server_combo.currentTextChanged.connect(lambda: self.set_license_tab_vendor_combo())

        # "Vendor" item.
        license_tab_vendor_label = QLabel('Vendor', self.license_tab_frame0)
        license_tab_vendor_label.setStyleSheet("font-weight: bold;")
        license_tab_vendor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.license_tab_vendor_combo = common_pyqt5.QComboCheckBox(self.license_tab_frame0)
        self.set_license_tab_vendor_combo()

        # "Feature" item.
        license_tab_feature_label = QLabel('Feature', self.license_tab_frame0)
        license_tab_feature_label.setStyleSheet("font-weight: bold;")
        license_tab_feature_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.license_tab_feature_line = QLineEdit()
        self.license_tab_feature_line.returnPressed.connect(self.update_license_info)

        feature_list = self.get_license_feature_list()
        license_tab_feature_line_completer = common_pyqt5.get_completer(feature_list)
        self.license_tab_feature_line.setCompleter(license_tab_feature_line_completer)

        # "User" item.
        license_tab_user_label = QLabel('User', self.license_tab_frame0)
        license_tab_user_label.setStyleSheet("font-weight: bold;")
        license_tab_user_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.license_tab_user_line = QLineEdit()
        self.license_tab_user_line.returnPressed.connect(self.update_license_info)

        if 'USER/GROUP' in self.busers_dic:
            license_tab_user_line_completer = common_pyqt5.get_completer(self.busers_dic['USER/GROUP'])
        else:
            license_tab_user_line_completer = common_pyqt5.get_completer([])

        self.license_tab_user_line.setCompleter(license_tab_user_line_completer)

        # "Filter" button.
        license_tab_check_button = QPushButton('Check', self.license_tab_frame0)
        license_tab_check_button.setStyleSheet('''QPushButton:hover{background:rgb(0, 85, 255);}''')
        license_tab_check_button.clicked.connect(self.update_license_info)

        # self.license_tab_frame0 - Grid
        license_tab_frame0_grid = QGridLayout()

        license_tab_frame0_grid.addWidget(license_tab_show_label, 0, 0)
        license_tab_frame0_grid.addWidget(self.license_tab_show_combo, 0, 1)
        license_tab_frame0_grid.addWidget(license_tab_server_label, 0, 2)
        license_tab_frame0_grid.addWidget(self.license_tab_server_combo, 0, 3)
        license_tab_frame0_grid.addWidget(license_tab_vendor_label, 0, 4)
        license_tab_frame0_grid.addWidget(self.license_tab_vendor_combo, 0, 5)
        license_tab_frame0_grid.addWidget(license_tab_feature_label, 0, 6)
        license_tab_frame0_grid.addWidget(self.license_tab_feature_line, 0, 7)
        license_tab_frame0_grid.addWidget(license_tab_user_label, 0, 8)
        license_tab_frame0_grid.addWidget(self.license_tab_user_line, 0, 9)
        license_tab_frame0_grid.addWidget(license_tab_check_button, 0, 10)

        license_tab_frame0_grid.setColumnStretch(0, 1)
        license_tab_frame0_grid.setColumnStretch(1, 1)
        license_tab_frame0_grid.setColumnStretch(2, 1)
        license_tab_frame0_grid.setColumnStretch(3, 1)
        license_tab_frame0_grid.setColumnStretch(4, 1)
        license_tab_frame0_grid.setColumnStretch(5, 1)
        license_tab_frame0_grid.setColumnStretch(6, 1)
        license_tab_frame0_grid.setColumnStretch(7, 1)
        license_tab_frame0_grid.setColumnStretch(8, 1)
        license_tab_frame0_grid.setColumnStretch(9, 1)
        license_tab_frame0_grid.setColumnStretch(10, 1)

        self.license_tab_frame0.setLayout(license_tab_frame0_grid)

    def get_license_feature_list(self):
        """
        Get all features from self.license_dic.
        """
        feature_list = []

        for license_server in self.license_dic.keys():
            for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                for feature in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                    feature_list.append(feature)

        feature_list = list(set(feature_list))

        return feature_list

    def update_license_tab_feature_completer(self):
        feature_list = self.get_license_feature_list()
        completer = common_pyqt5.get_completer(feature_list)
        self.license_tab_feature_line.setCompleter(completer)

    def set_license_tab_show_combo(self, checked_status_list=['ALL', ]):
        self.license_tab_show_combo.clear()

        license_status_list = ['ALL', 'IN_USE', 'NOT_USED']

        for license_status in license_status_list:
            self.license_tab_show_combo.addCheckBoxItem(license_status)

        # Set to checked status for checked_status_list.
        for (i, qBox) in enumerate(self.license_tab_show_combo.checkBoxList):
            if (qBox.text() in checked_status_list) and (qBox.isChecked() is False):
                self.license_tab_show_combo.checkBoxList[i].setChecked(True)

    def set_license_tab_server_combo(self, checked_server_list=['ALL', ]):
        self.license_tab_server_combo.clear()

        license_server_list = list(self.license_dic.keys())
        license_server_list.insert(0, 'ALL')

        for license_server in license_server_list:
            self.license_tab_server_combo.addCheckBoxItem(license_server, update_width=True)

        # Set to checked status for checked_server_list.
        for (i, qBox) in enumerate(self.license_tab_server_combo.checkBoxList):
            if (qBox.text() in checked_server_list) and (qBox.isChecked() is False):
                self.license_tab_server_combo.checkBoxList[i].setChecked(True)

    def set_license_tab_vendor_combo(self, checked_vendor_list=['ALL', ]):
        self.license_tab_vendor_combo.clear()

        # Get vendor_daemon list.
        vendor_daemon_list = ['ALL', ]
        selected_license_server_list = self.license_tab_server_combo.currentText().strip().split()

        for license_server in self.license_dic.keys():
            for selected_license_server in selected_license_server_list:
                if (selected_license_server == license_server) or (selected_license_server == 'ALL'):
                    for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                        if vendor_daemon not in vendor_daemon_list:
                            vendor_daemon_list.append(vendor_daemon)

        for vendor_daemon in vendor_daemon_list:
            self.license_tab_vendor_combo.addCheckBoxItem(vendor_daemon)

        # Set to checked status for checked_vendor_list.
        for (i, qBox) in enumerate(self.license_tab_vendor_combo.checkBoxList):
            if (qBox.text() in checked_vendor_list) and (qBox.isChecked() is False):
                self.license_tab_vendor_combo.checkBoxList[i].setChecked(True)

    def update_license_info(self):
        # Get license information.
        self.get_license_dic()

        if not self.license_dic:
            warning_message = 'Not find any license information.'
            self.gui_warning(warning_message)
            return

        self.update_license_tab_feature_completer()

        selected_license_server_list = self.license_tab_server_combo.currentText().strip().split()
        selected_vendor_daemon_list = self.license_tab_vendor_combo.currentText().strip().split()
        specified_license_feature_list = self.license_tab_feature_line.text().strip().split()
        specified_license_user_list = self.license_tab_user_line.text().strip().split()
        show_mode_list = self.license_tab_show_combo.currentText().strip().split()

        if show_mode_list:
            if ('ALL' in show_mode_list) or (('IN_USE' in show_mode_list) and ('NOT_USED' in show_mode_list)):
                show_mode = 'ALL'
            else:
                show_mode = show_mode_list[0]
        else:
            show_mode = 'ALL'

        filter_license_dic_item = common_license.FilterLicenseDic()
        filtered_license_dic = filter_license_dic_item.run(license_dic=self.license_dic, server_list=selected_license_server_list, vendor_list=selected_vendor_daemon_list, feature_list=specified_license_feature_list, user_list=specified_license_user_list, show_mode=show_mode)

        # Update self.license_tab_feature_table and self.license_tab_expires_table.
        self.gen_license_tab_feature_table(filtered_license_dic)
        self.gen_license_tab_expires_table(filtered_license_dic)

    def gen_license_tab_feature_table(self, license_dic):
        self.license_tab_feature_table.setShowGrid(True)
        self.license_tab_feature_table.setSortingEnabled(False)
        self.license_tab_feature_table.setColumnCount(0)
        self.license_tab_feature_table.setColumnCount(5)
        self.license_tab_feature_table_title_list = ['Server', 'Vendor', 'Feature', 'Issued', 'In_Use']
        self.license_tab_feature_table.setHorizontalHeaderLabels(self.license_tab_feature_table_title_list)

        self.license_tab_feature_table.setColumnWidth(0, 160)
        self.license_tab_feature_table.setColumnWidth(1, 80)
        self.license_tab_feature_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.license_tab_feature_table.setColumnWidth(3, 50)
        self.license_tab_feature_table.setColumnWidth(4, 50)

        # Get license feature information length.
        license_feature_info_length = 0

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                    license_feature_info_length += 1

        # Fill self.license_tab_feature_table items.
        self.license_tab_feature_table.setRowCount(0)
        self.license_tab_feature_table.setRowCount(license_feature_info_length)

        row = -1

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                    row += 1

                    # Fill "Server" item.
                    self.license_tab_feature_table.setItem(row, 0, QTableWidgetItem(license_server))

                    # Fill "Vendor" item.
                    self.license_tab_feature_table.setItem(row, 1, QTableWidgetItem(vendor_daemon))

                    # Fill "Feature" item.
                    item = QTableWidgetItem(feature)
                    item.setForeground(QBrush(Qt.blue))
                    self.license_tab_feature_table.setItem(row, 2, item)

                    # Fill "Issued" item.
                    issued = license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['issued']
                    item = QTableWidgetItem()

                    if re.match(r'^\d+$', issued):
                        item.setData(Qt.DisplayRole, int(issued))
                    else:
                        item.setText(issued)

                    self.license_tab_feature_table.setItem(row, 3, item)

                    # Fill "In_Use" item.
                    in_use = license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['in_use']
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, int(in_use))

                    if in_use == '0':
                        item.setFont(QFont('song', 9))
                    else:
                        item.setFont(QFont('song', 9, QFont.Bold))

                    self.license_tab_feature_table.setItem(row, 4, item)

        self.license_tab_feature_table.setSortingEnabled(True)

    def license_tab_check_click(self, item=None):
        """
        If click the Job id, jump to the JOB tab and show the job information.
        If click the "PEND" Status, show the job pend reasons on a QMessageBox.information().
        """
        if item is not None:
            if item.column() == 4:
                current_row = self.license_tab_feature_table.currentRow()
                in_use_num = int(self.license_tab_feature_table.item(current_row, 4).text().strip())

                if in_use_num > 0:
                    license_server = self.license_tab_feature_table.item(current_row, 0).text().strip()
                    vendor_daemon = self.license_tab_feature_table.item(current_row, 1).text().strip()
                    license_feature = self.license_tab_feature_table.item(current_row, 2).text().strip()

                    common.bprint(f'Getting license feature "{license_feature}" usage on license server ' + str(license_server) + ' ...', date_format='%Y-%m-%d %H:%M:%S')

                    self.my_show_license_feature_usage = ShowLicenseFeatureUsage(server=license_server, vendor=vendor_daemon, feature=license_feature)
                    self.my_show_license_feature_usage.start()

    def gen_license_tab_expires_table(self, license_dic):
        self.license_tab_expires_table.setShowGrid(True)
        self.license_tab_expires_table.setSortingEnabled(False)
        self.license_tab_expires_table.setColumnCount(0)
        self.license_tab_expires_table.setColumnCount(4)
        self.license_tab_expires_table_title_list = ['License Server', 'Feature', 'Num', 'Expires']
        self.license_tab_expires_table.setHorizontalHeaderLabels(self.license_tab_expires_table_title_list)

        self.license_tab_expires_table.setColumnWidth(0, 160)
        self.license_tab_expires_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.license_tab_expires_table.setColumnWidth(2, 50)
        self.license_tab_expires_table.setColumnWidth(3, 100)

        # Get license feature information length.
        license_expires_info_length = 0

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'].keys():
                    license_expires_info_length += len(license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'][feature])

        # Fill self.license_tab_expires_table items.
        self.license_tab_expires_table.setRowCount(0)
        self.license_tab_expires_table.setRowCount(license_expires_info_length)

        row = -1

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'].keys():
                    for expires_dic in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'][feature]:
                        row += 1

                        # Fill "Server" item.
                        self.license_tab_expires_table.setItem(row, 0, QTableWidgetItem(license_server))

                        # Fill "Feature" item.
                        item = QTableWidgetItem(feature)
                        item.setForeground(QBrush(Qt.blue))
                        self.license_tab_expires_table.setItem(row, 1, item)

                        # Fill "Num" item.
                        item = QTableWidgetItem()
                        item.setData(Qt.DisplayRole, int(expires_dic['license']))
                        self.license_tab_expires_table.setItem(row, 2, item)

                        # Fill "Expires" item.
                        expires = expires_dic['expires']
                        item = QTableWidgetItem(expires)
                        expires_mark = common_license.check_expire_date(expires)

                        if expires_mark == 0:
                            pass
                        elif expires_mark == -1:
                            item.setForeground(QBrush(Qt.gray))
                        else:
                            item.setForeground(QBrush(Qt.red))
                        self.license_tab_expires_table.setItem(row, 3, item)

        self.license_tab_expires_table.setSortingEnabled(True)
# For license TAB (end) #

# Export table (start) #
    def export_jobs_table(self):
        self.export_table('jobs', self.jobs_tab_table, self.jobs_tab_table_title_list)

    def export_hosts_table(self):
        self.export_table('hosts', self.hosts_tab_table, self.hosts_tab_table_title_list)

    def export_users_table(self):
        self.export_table('users', self.users_tab_table, self.users_tab_table_title_list)

    def export_queues_table(self):
        self.export_table('queues', self.queues_tab_table, self.queues_tab_table_title_list)

    def export_utilization_table(self):
        self.export_table('utilization', self.utilization_tab_table, self.utilization_tab_table_title_list)

    def export_license_feature_table(self):
        self.export_table('license_feature', self.license_tab_feature_table, self.license_tab_feature_table_title_list)

    def export_license_expires_table(self):
        self.export_table('license_expires', self.license_tab_expires_table, self.license_tab_expires_table_title_list)

    def export_table(self, table_type, table_item, title_list):
        """
        Export specified table info into an csv file.
        """
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_time_string = re.sub('-', '', current_time)
        current_time_string = re.sub(':', '', current_time_string)
        current_time_string = re.sub(' ', '_', current_time_string)
        default_output_file = './lsfMonitor_' + str(table_type) + '_' + str(current_time_string) + '.csv'
        (output_file, output_file_type) = QFileDialog.getSaveFileName(self, 'Export ' + str(table_type) + ' table', default_output_file, 'CSV Files (*.csv)')

        if output_file:
            # Get table content.
            content_dic = {}
            row_num = table_item.rowCount()
            column_num = table_item.columnCount()

            for column in range(column_num):
                column_list = []

                for row in range(row_num):
                    if table_item.item(row, column):
                        column_list.append(table_item.item(row, column).text())
                    else:
                        column_list.append('')

                content_dic.setdefault(title_list[column], column_list)

            # Write csv
            common.bprint(f'Writing {table_type} table into "{output_file}" ...', date_format='%Y-%m-%d %H:%M:%S')
            common.write_csv(csv_file=output_file, content_dic=content_dic)
# Export table (end) #

# For AI TAB (begin) #
    def gen_ai_tab(self):
        """
        Generate the AI helpdesk tab.
        """
        # Check if AI is configured.
        self.ai_configured = False

        if hasattr(config, 'ai_api_base_url') and config.ai_api_base_url and hasattr(config, 'ai_api_key') and config.ai_api_key and hasattr(config, 'ai_model_name') and config.ai_model_name:
            self.ai_configured = True

        # Chat display area.
        self.ai_tab_chat_text = QTextEdit(self.ai_tab)
        self.ai_tab_chat_text.setReadOnly(True)
        self._create_chat_avatars()

        if not self.ai_configured:
            self.ai_tab_chat_text.setHtml('<p>AI helpdesk is not configured.</p><p>Please set <b>ai_api_base_url</b>, <b>ai_api_key</b>, and <b>ai_model_name</b> in config.py.</p>')

        # Input area (multi-line, Enter sends, Shift+Enter for newline).
        self.ai_tab_input = AiInputBox(self.ai_tab)
        self.ai_tab_input.setPlaceholderText('Ask AI about LSF jobs, cluster status, licenses ... (Enter to send, Shift+Enter for newline)')
        self.ai_tab_input.setFixedHeight(60)
        self.ai_tab_input.send_requested.connect(self.ai_tab_send_message)

        if not self.ai_configured:
            self.ai_tab_input.setEnabled(False)

        # Buttons (stacked vertically, matching input height).
        ai_tab_send_button = QPushButton('Send', self.ai_tab)
        ai_tab_send_button.setFixedHeight(28)
        ai_tab_send_button.clicked.connect(self.ai_tab_send_message)

        ai_tab_clear_button = QPushButton('Clear', self.ai_tab)
        ai_tab_clear_button.setFixedHeight(28)
        ai_tab_clear_button.clicked.connect(self.ai_tab_clear_chat)

        button_layout = QVBoxLayout()
        button_layout.addWidget(ai_tab_send_button)
        button_layout.addWidget(ai_tab_clear_button)
        button_layout.setSpacing(4)

        # Feedback bar (hidden by default, shown after AI response).
        self.ai_feedback_widget = QWidget(self.ai_tab)
        feedback_layout = QHBoxLayout(self.ai_feedback_widget)
        feedback_layout.setContentsMargins(0, 2, 0, 2)
        feedback_layout.setSpacing(6)

        feedback_label = QLabel('Was this helpful?')
        feedback_label.setStyleSheet('color: #888; font-size: 11px;')
        feedback_layout.addWidget(feedback_label)

        self.ai_feedback_solved_btn = QPushButton('Solved')
        self.ai_feedback_solved_btn.setFixedSize(60, 22)
        self.ai_feedback_solved_btn.setStyleSheet('font-size: 11px;')
        self.ai_feedback_solved_btn.clicked.connect(lambda: self._ai_tab_user_feedback('solved'))
        feedback_layout.addWidget(self.ai_feedback_solved_btn)

        self.ai_feedback_unsolved_btn = QPushButton('Unsolved')
        self.ai_feedback_unsolved_btn.setFixedSize(70, 22)
        self.ai_feedback_unsolved_btn.setStyleSheet('font-size: 11px;')
        self.ai_feedback_unsolved_btn.clicked.connect(lambda: self._ai_tab_user_feedback('unsolved'))
        feedback_layout.addWidget(self.ai_feedback_unsolved_btn)

        feedback_layout.addStretch()
        self.ai_feedback_widget.hide()

        # Layout.
        ai_tab_grid = QGridLayout()
        ai_tab_grid.addWidget(self.ai_tab_chat_text, 0, 0, 1, 2)
        ai_tab_grid.addWidget(self.ai_feedback_widget, 1, 0, 1, 2)
        ai_tab_grid.addWidget(self.ai_tab_input, 2, 0)
        ai_tab_grid.addLayout(button_layout, 2, 1)
        ai_tab_grid.setColumnStretch(0, 10)
        ai_tab_grid.setColumnStretch(1, 1)
        self.ai_tab.setLayout(ai_tab_grid)

        # Init conversation history.
        self.ai_messages = [{"role": "system", "content": common_ai.SYSTEM_PROMPT + f"\n\nCurrent user: {USER}"}]

        # Load AI documents (RAG vectors or keyword chunks) in background.
        common.bprint('Loading AI documents ...', date_format='%Y-%m-%d %H:%M:%S')
        self.ai_doc_chunks = {"chunks": [], "embeddings": None}
        docs_dir = os.path.join(os.environ.get('LSFMONITOR_INSTALL_PATH', '.'), 'db', 'ai')
        self._doc_loader = common_ai.DocLoaderThread(docs_dir)
        self._doc_loader.finished_signal.connect(lambda doc_data: setattr(self, 'ai_doc_chunks', doc_data))
        self._doc_loader.start()

        # Load skills.
        common.bprint('Loading AI skills ...', date_format='%Y-%m-%d %H:%M:%S')
        skills_dir = os.path.join(os.environ.get('LSFMONITOR_INSTALL_PATH', '.'), 'monitor', 'conf', 'skills')
        self.ai_skills = common_ai.load_skills(skills_dir)

        # Init AI log database.
        self.ai_log_db_file = common_ai_log.init_ai_log_db(config.db_path)

    def _create_chat_avatars(self):
        """Create user (person) and AI (robot) avatar icons with QPainter."""
        size = 32
        doc = self.ai_tab_chat_text.document()

        # Circular clip path.
        clip = QPainterPath()
        clip.addEllipse(0, 0, size, size)

        # --- User avatar: blue circle, white person silhouette ---
        user_pm = QPixmap(size, size)
        user_pm.fill(Qt.transparent)
        p = QPainter(user_pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipPath(clip)
        p.fillRect(0, 0, size, size, QColor('#5DADE2'))
        p.setBrush(QColor('#FFFFFF'))
        p.setPen(Qt.NoPen)
        p.drawEllipse(10, 3, 12, 12)
        p.drawEllipse(4, 17, 24, 22)
        p.end()

        # --- AI avatar: green circle, white robot face ---
        ai_pm = QPixmap(size, size)
        ai_pm.fill(Qt.transparent)
        p = QPainter(ai_pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipPath(clip)
        p.fillRect(0, 0, size, size, QColor('#27AE60'))
        p.setBrush(QColor('#FFFFFF'))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(6, 7, 20, 14, 3, 3)
        p.setBrush(QColor('#27AE60'))
        p.drawRoundedRect(9, 10, 5, 5, 1, 1)
        p.drawRoundedRect(18, 10, 5, 5, 1, 1)
        p.drawRect(11, 17, 10, 2)
        p.setPen(QPen(QColor('#FFFFFF'), 2))
        p.drawLine(16, 7, 16, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(14, 0, 4, 4)
        p.drawRoundedRect(9, 23, 14, 7, 2, 2)
        p.end()

        doc.addResource(2, QUrl("user_avatar"), user_pm)
        doc.addResource(2, QUrl("ai_avatar"), ai_pm)

    def ai_tab_send_message(self):
        """
        Send user message to AI and start streaming response.
        """
        if not self.ai_configured:
            return

        user_text = self.ai_tab_input.toPlainText().strip()

        if not user_text:
            return

        # Don't send while AI is still responding.
        if self.ai_thread and self.ai_thread.isRunning():
            return

        # Hide feedback bar from previous response.
        self.ai_feedback_widget.hide()

        # Display user message (right-aligned light blue bubble with avatar).
        user_html = user_text.replace('\n', '<br>')
        self.ai_tab_chat_text.append(
            f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td width="15%"></td>'
            f'<td style="background-color:#D6EAF8; padding:10px 14px; -qt-block-indent:0;">'
            f'{user_html}</td>'
            f'<td width="42" valign="top" style="padding:2px 0 0 6px;">'
            f'<img src="user_avatar" width="32" height="32"></td>'
            f'</tr></table>'
        )
        self.ai_tab_input.clear()

        # Log the question.
        self.my_save_log.save_log(f'AI question: {user_text}')

        # Track session for AI log database.
        self._ai_send_time = time.time()

        if self.ai_debug_action.isChecked():
            common.bprint('[AI Debug] User sent message', date_format='%Y-%m-%d %H:%M:%S')

        self._current_ai_session_id = common_ai_log.gen_session_id()
        self._current_ai_question = user_text
        self._current_ai_tool_calls = []

        # Add to messages.
        self.ai_messages.append({"role": "user", "content": user_text})

        # Trim context if too long (keep system prompt + last 30 messages).
        if len(self.ai_messages) > 40:
            self.ai_messages = [self.ai_messages[0]] + self.ai_messages[-30:]

        # Get config.
        dangerous_commands = common_ai.DEFAULT_DANGEROUS_COMMANDS

        if hasattr(config, 'ai_dangerous_commands') and config.ai_dangerous_commands:
            dangerous_commands = config.ai_dangerous_commands.split()

        lmstat_path = config.lmstat_path if hasattr(config, 'lmstat_path') else 'lmstat'
        lmstat_bsub_command = config.lmstat_bsub_command if hasattr(config, 'lmstat_bsub_command') else ''

        # Insert "AI" label and prepare block format for streaming text.
        self._ai_tab_start_ai_block()

        # Start AI thread.
        embedding_model = config.ai_embedding_model_name if hasattr(config, 'ai_embedding_model_name') else ''
        embedding_api_base_url = config.ai_embedding_api_base_url if hasattr(config, 'ai_embedding_api_base_url') else ''
        embedding_api_key = config.ai_embedding_api_key if hasattr(config, 'ai_embedding_api_key') else ''
        self.ai_thread = common_ai.AiChatThread(
            api_base_url=config.ai_api_base_url,
            api_key=config.ai_api_key,
            model_name=config.ai_model_name,
            messages=self.ai_messages,
            db_path=self.cluster_db_path,
            license_dic=self.license_dic,
            lmstat_path=lmstat_path,
            lmstat_bsub_command=lmstat_bsub_command,
            dangerous_commands=dangerous_commands,
            doc_chunks=self.ai_doc_chunks,
            skills=self.ai_skills,
            embedding_model=embedding_model,
            embedding_api_base_url=embedding_api_base_url,
            embedding_api_key=embedding_api_key,
            debug=self.ai_debug_action.isChecked()
        )
        self.ai_thread.token_received.connect(self.ai_tab_on_token)
        self.ai_thread.tool_call_start.connect(self.ai_tab_on_tool_start)
        self.ai_thread.tool_call_result.connect(self.ai_tab_on_tool_result)
        self.ai_thread.finished_signal.connect(self.ai_tab_on_finished)
        self.ai_thread.error_signal.connect(self.ai_tab_on_error)
        self.ai_thread.confirm_requested.connect(self.ai_handle_confirm_request)
        self.ai_thread.status_signal.connect(self.ai_tab_on_status)
        self.ai_thread.sources_signal.connect(self.ai_tab_on_sources)
        self._ai_sources = {}
        self.ai_thread.start()

    def _ai_tab_start_ai_block(self):
        """Insert robot avatar with animated 'Thinking...' placeholder, set block format for streaming."""
        cursor = self.ai_tab_chat_text.textCursor()
        cursor.movePosition(cursor.End)

        # Two-column table: left=avatar, right=gray message area (mirrors user HTML table).
        table_fmt = QTextTableFormat()
        table_fmt.setBorder(0)
        table_fmt.setCellPadding(6)
        table_fmt.setCellSpacing(0)
        table_fmt.setTopMargin(6)
        table_fmt.setBottomMargin(4)
        table_fmt.setRightMargin(80)
        table_fmt.setColumnWidthConstraints([
            QTextLength(QTextLength.FixedLength, 42),
            QTextLength(QTextLength.PercentageLength, 100),
        ])
        table = cursor.insertTable(1, 2, table_fmt)

        # Left cell: avatar.
        avatar_cursor = table.cellAt(0, 0).firstCursorPosition()
        img_fmt = QTextImageFormat()
        img_fmt.setName("ai_avatar")
        img_fmt.setWidth(32)
        img_fmt.setHeight(32)
        avatar_cursor.insertImage(img_fmt)

        # Right cell: gray background message area.
        self._ai_msg_cell = table.cellAt(0, 1)
        cell_fmt = self._ai_msg_cell.format()
        cell_fmt.setBackground(QColor('#F2F3F4'))
        self._ai_msg_cell.setFormat(cell_fmt)

        cursor = self._ai_msg_cell.firstCursorPosition()

        # Animated status placeholder in gray italic.
        self._ai_thinking_pos = cursor.position()
        self._ai_thinking_fmt = QTextCharFormat()
        self._ai_thinking_fmt.setForeground(QColor('#999999'))
        self._ai_thinking_fmt.setFontItalic(True)
        self._ai_status_base = 'Thinking'
        cursor.insertText('Thinking.', self._ai_thinking_fmt)
        self._ai_first_token = True
        self._ai_thinking_dots = 1

        # Start dot animation timer.
        if not hasattr(self, '_ai_thinking_timer'):
            self._ai_thinking_timer = QTimer(self)
            self._ai_thinking_timer.timeout.connect(self._ai_tab_animate_thinking)

        self._ai_thinking_timer.start(500)

        # Normal text format for streaming content.
        self._ai_text_fmt = QTextCharFormat()
        self._ai_text_fmt.setForeground(QColor('#000000'))

        self.ai_tab_chat_text.setTextCursor(cursor)
        self.ai_tab_chat_text.ensureCursorVisible()

    def _ai_frame_end_position(self):
        """Return the last valid cursor position inside the current AI message cell."""
        if hasattr(self, '_ai_msg_cell') and self._ai_msg_cell:
            return self._ai_msg_cell.lastCursorPosition().position()

        # Fallback: document end.
        return self.ai_tab_chat_text.document().characterCount() - 1

    def _ai_tab_animate_thinking(self):
        """Cycle dots on status text: Thinking. -> Thinking.. -> Thinking..."""
        if not self._ai_first_token:
            self._ai_thinking_timer.stop()
            return

        self._ai_thinking_dots = (self._ai_thinking_dots % 3) + 1
        text = self._ai_status_base + '.' * self._ai_thinking_dots

        doc_length = self.ai_tab_chat_text.document().characterCount()
        end_pos = self._ai_frame_end_position()

        if self._ai_thinking_pos >= doc_length or end_pos >= doc_length:
            self._ai_thinking_timer.stop()
            return

        cursor = self.ai_tab_chat_text.textCursor()
        cursor.setPosition(self._ai_thinking_pos)
        cursor.setPosition(end_pos, cursor.KeepAnchor)
        cursor.insertText(text, self._ai_thinking_fmt)
        self.ai_tab_chat_text.setTextCursor(cursor)
        self.ai_tab_chat_text.ensureCursorVisible()

    def _ai_tab_remove_thinking(self):
        """Remove 'Thinking...' placeholder and stop animation."""
        if not self._ai_first_token:
            return

        self._ai_first_token = False

        if hasattr(self, '_ai_thinking_timer'):
            self._ai_thinking_timer.stop()

        doc_length = self.ai_tab_chat_text.document().characterCount()
        end_pos = self._ai_frame_end_position()

        if self._ai_thinking_pos >= doc_length or end_pos >= doc_length:
            return

        cursor = self.ai_tab_chat_text.textCursor()
        cursor.setPosition(self._ai_thinking_pos)
        cursor.setPosition(end_pos, cursor.KeepAnchor)
        cursor.removeSelectedText()
        self.ai_tab_chat_text.setTextCursor(cursor)

    def ai_tab_on_token(self, token):
        """Append a single token to the chat display (streaming)."""
        self._ai_tab_remove_thinking()
        cursor = self._ai_msg_cell.lastCursorPosition() if hasattr(self, '_ai_msg_cell') and self._ai_msg_cell else self.ai_tab_chat_text.textCursor()
        cursor.insertText(token, self._ai_text_fmt)
        self.ai_tab_chat_text.setTextCursor(cursor)
        self.ai_tab_chat_text.ensureCursorVisible()

    def ai_tab_on_status(self, status):
        """Update the animated status text with a new phase description."""
        self._ai_status_base = status
        self._ai_thinking_dots = 0
        self._ai_tab_animate_thinking()

    def ai_tab_on_tool_start(self, tool_name, description):
        """Tool call started - update status text to show what's being executed."""
        self._ai_status_base = description
        self._ai_thinking_dots = 0
        self._ai_tab_animate_thinking()
        self.my_save_log.save_log(f'AI tool: {description}')

        # Track tool call for AI log.
        self._current_ai_tool_calls.append({'name': tool_name, 'args': description, 'result': ''})

    def ai_tab_on_tool_result(self, tool_name, result):
        """Tool call finished - start a new AI block for the response."""
        self.my_save_log.save_log(f'AI tool result: {tool_name}, {len(result)} chars')

        # Update the latest tool call with its result.
        if self._current_ai_tool_calls:
            self._current_ai_tool_calls[-1]['result'] = result[:1000]

        # Start new AI block (shows Thinking... while API processes tool results).
        self._ai_tab_start_ai_block()

    def ai_tab_on_sources(self, sources):
        """Store sources dict emitted by AiChatThread."""
        self._ai_sources = sources

    def _ai_tab_render_sources(self, rag_sources, skills):
        """Append a sources block at the bottom of the current AI message cell."""
        if not hasattr(self, '_ai_msg_cell') or not self._ai_msg_cell:
            return

        cursor = self._ai_msg_cell.lastCursorPosition()

        # Separator line.
        cursor.insertBlock()
        sep_fmt = QTextCharFormat()
        sep_fmt.setForeground(QColor('#AAAAAA'))
        sep_fmt.setFontPointSize(8)
        cursor.insertText('\u2500' * 40, sep_fmt)

        # "Sources:" label.
        cursor.insertBlock()
        label_fmt = QTextCharFormat()
        label_fmt.setForeground(QColor('#666666'))
        label_fmt.setFontPointSize(9)
        label_fmt.setFontItalic(True)
        cursor.insertText('Sources:', label_fmt)

        # Item format.
        item_fmt = QTextCharFormat()
        item_fmt.setForeground(QColor('#888888'))
        item_fmt.setFontPointSize(8)
        item_fmt.setFontItalic(True)

        # RAG sources (deduplicated by source+page).
        seen = set()

        for meta in rag_sources:
            source = meta.get('source', '')
            page = meta.get('page', '')
            key = (source, str(page))

            if key in seen or not source:
                continue

            seen.add(key)
            cursor.insertBlock()
            text = f'  \u00b7 {source}'

            if page:
                text += f' (p.{page})'

            cursor.insertText(text, item_fmt)

        # Skill sources.
        for skill_name in skills:
            cursor.insertBlock()
            cursor.insertText(f'  \u00b7 Skill: {skill_name}', item_fmt)

        self.ai_tab_chat_text.setTextCursor(cursor)

    def ai_tab_on_finished(self):
        """Called when AI response is complete."""
        # Render sources block if any sources were collected.
        rag_sources = self._ai_sources.get('rag_sources', []) if self._ai_sources else []
        skills = self._ai_sources.get('skills', []) if self._ai_sources else []

        if rag_sources or skills:
            self._ai_tab_render_sources(rag_sources, skills)

        # Append total elapsed time.
        if hasattr(self, '_ai_send_time') and self._ai_send_time:
            elapsed = time.time() - self._ai_send_time
            time_text = f'⏱ Total time: {elapsed:.1f}s'

            # Always append LLM performance metrics.
            if self.ai_thread and hasattr(self.ai_thread, '_timing_stats'):
                stats = self.ai_thread._timing_stats
                first_token_max = stats.get('llm_first_token_max', 0)
                output_tokens = stats.get('output_tokens', 0)

                first_token_slow = first_token_max > 10

                if first_token_slow:
                    first_token_html = f'<span style="color: #CC0000;">最慢首token {first_token_max:.1f}s [慢]</span>'
                else:
                    first_token_html = f'最慢首token {first_token_max:.1f}s'

                tpm_html = ''

                if output_tokens > 0:
                    generation_time = stats.get('llm_generation_total', 0)
                    tpm = (generation_time / output_tokens) * 1000 if generation_time > 0 else 0

                    if tpm > 100:
                        tpm_html = f'<span style="color: #CC0000;">平均生成 {tpm:.0f}ms/token [慢]</span>'
                    else:
                        tpm_html = f'平均生成 {tpm:.0f}ms/token'

                if tpm_html:
                    time_text += f'（{first_token_html}，{tpm_html}）'
                else:
                    time_text += f'（{first_token_html}）'

            cursor = self.ai_tab_chat_text.textCursor()
            cursor.movePosition(cursor.End)
            cursor.insertBlock(QTextBlockFormat())
            cursor.insertHtml(f'<span style="color: #888888; font-size: 11px;">{time_text}</span>')
            self.ai_tab_chat_text.setTextCursor(cursor)

        # Add a blank separator line.
        cursor = self.ai_tab_chat_text.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertBlock(QTextBlockFormat())
        self.ai_tab_chat_text.setTextCursor(cursor)
        self.ai_tab_chat_text.ensureCursorVisible()

        # Extract the full AI answer.
        full_answer = ''

        for msg in reversed(self.ai_messages):
            if msg.get('role') == 'assistant' and msg.get('content'):
                full_answer = msg['content']
                break

        # Log the AI answer (truncated for text log).
        if full_answer:
            self.my_save_log.save_log(f'AI answer: {full_answer[:200]}')

        # Save complete conversation to AI log database.
        if hasattr(self, 'ai_log_db_file') and self.ai_log_db_file and hasattr(self, '_current_ai_session_id'):
            try:
                resolution = common_ai_log.auto_judge_resolution(
                    self._current_ai_question,
                    full_answer,
                    self._current_ai_tool_calls,
                )

                common_ai_log.save_conversation(
                    db_file=self.ai_log_db_file,
                    session_id=self._current_ai_session_id,
                    user=USER,
                    cluster=self.cluster or '',
                    host=socket.gethostname(),
                    question=self._current_ai_question,
                    answer=full_answer,
                    tool_calls=self._current_ai_tool_calls,
                    resolution=resolution,
                )

                # Trigger background insight generation for solved conversations.
                if resolution == 'solved':
                    self._ai_generate_insight(self._current_ai_session_id, self._current_ai_question, full_answer, self._current_ai_tool_calls)
            except Exception as e:
                common.bprint(f'Failed to save AI conversation log: {e}', level='Warning')

        # Show feedback bar for user override.
        self.ai_feedback_widget.show()

    def _ai_tab_user_feedback(self, resolution):
        """User clicked Solved/Unsolved button to override auto-judgment."""
        self.ai_feedback_widget.hide()

        if hasattr(self, 'ai_log_db_file') and self.ai_log_db_file and hasattr(self, '_current_ai_session_id'):
            try:
                common_ai_log.update_resolution(self.ai_log_db_file, self._current_ai_session_id, resolution, user=USER)

                # User confirmed solved — generate insight if not already done.
                if resolution == 'solved' and hasattr(self, '_current_ai_question'):
                    full_answer = ''

                    for msg in reversed(self.ai_messages):
                        if msg.get('role') == 'assistant' and msg.get('content'):
                            full_answer = msg['content']
                            break

                    if full_answer:
                        self._ai_generate_insight(self._current_ai_session_id, self._current_ai_question, full_answer, self._current_ai_tool_calls)
            except Exception as e:
                common.bprint(f'Failed to update AI resolution: {e}', level='Warning')

    def _ai_generate_insight(self, session_id, question, answer, tool_calls):
        """Launch background thread to generate and save a distilled insight."""
        tool_calls_json = json.dumps(tool_calls or [], ensure_ascii=False)

        self._insight_thread = common_ai_log.InsightGeneratorThread(
            api_base_url=config.ai_api_base_url,
            api_key=config.ai_api_key,
            model_name=config.ai_model_name,
            session_id=session_id,
            question=question,
            answer=answer,
            tool_calls_json=tool_calls_json,
        )
        self._insight_thread.finished_signal.connect(self._ai_on_insight_generated)
        self._insight_thread.start()

    def _ai_on_insight_generated(self, session_id, insight, keywords):
        """Callback when background insight generation completes."""
        if hasattr(self, 'ai_log_db_file') and self.ai_log_db_file:
            try:
                common_ai_log.save_insight(
                    db_file=self.ai_log_db_file,
                    session_id=session_id,
                    insight=insight,
                    keywords=keywords,
                    source_question=self._current_ai_question[:200] if hasattr(self, '_current_ai_question') else '',
                )

                if self.ai_debug_action.isChecked():
                    common.bprint(f'[AI Debug] Insight saved: {insight[:80]}', date_format='%Y-%m-%d %H:%M:%S')
            except Exception as e:
                common.bprint(f'Failed to save AI insight: {e}', level='Warning')

    def ai_tab_on_error(self, error_msg):
        """Show error in chat."""
        self._ai_tab_remove_thinking()
        cursor = self.ai_tab_chat_text.textCursor()
        cursor.movePosition(cursor.End)

        block_fmt = QTextBlockFormat()
        block_fmt.setBackground(QColor('#F8D7DA'))
        block_fmt.setLeftMargin(4)
        block_fmt.setRightMargin(4)
        block_fmt.setTopMargin(4)
        block_fmt.setBottomMargin(4)
        cursor.insertBlock(block_fmt)

        char_fmt = QTextCharFormat()
        char_fmt.setForeground(QColor('#721C24'))
        char_fmt.setFontWeight(QFont.Bold)
        cursor.insertText('Error: ', char_fmt)

        char_fmt.setFontWeight(QFont.Normal)
        cursor.insertText(error_msg, char_fmt)

        self.ai_tab_chat_text.setTextCursor(cursor)

        self.my_save_log.save_log(f'AI error: {error_msg}')

    def ai_tab_clear_chat(self):
        """Clear chat history."""
        self.ai_tab_chat_text.clear()
        self.ai_messages = [{"role": "system", "content": common_ai.SYSTEM_PROMPT + f"\n\nCurrent user: {USER}"}]
        self.ai_feedback_widget.hide()

    def ai_handle_confirm_request(self, command):
        """Show QMessageBox to confirm dangerous command execution."""
        result = QMessageBox.question(
            self,
            'Confirm Command',
            f'AI wants to execute:\n\n{command}\n\nAllow?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        self.ai_thread.set_confirm_result(result == QMessageBox.Yes)

        if result == QMessageBox.Yes:
            self.my_save_log.save_log(f'AI dangerous command approved: {command}')
        else:
            self.my_save_log.save_log(f'AI dangerous command rejected: {command}')
# For AI TAB (end) #

# For AI Menu (start) #
    def ai_record_search(self):
        """Open the AI conversation record search window."""
        if not hasattr(self, 'ai_log_db_file') or not self.ai_log_db_file:
            QMessageBox.warning(self, 'Warning', 'AI log database is not initialized.')
            return

        self.ai_search_window = AiRecordSearchWindow(self.ai_log_db_file)
        self.ai_search_window.show()

    def ai_problem_analysis(self):
        """Generate an AI problem analysis HTML report using LLM."""
        if not hasattr(self, 'ai_log_db_file') or not self.ai_log_db_file:
            QMessageBox.warning(self, 'Warning', 'AI log database is not initialized.')
            return

        if not self.ai_configured:
            QMessageBox.warning(self, 'Warning', 'AI is not configured. Cannot generate analysis report.')
            return

        # Get all conversations.
        data = common_ai_log.get_all_conversations(self.ai_log_db_file)

        if not data or 'question' not in data or len(data['question']) == 0:
            QMessageBox.information(self, 'Info', 'No conversation records found. Please use the AI helpdesk first.')
            return

        # Choose output file path.
        default_name = f'ai_report_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
        output_file, _ = QFileDialog.getSaveFileName(self, 'Save Analysis Report', default_name, 'HTML Files (*.html)')

        if not output_file:
            return

        # Start report generation thread.
        self._ai_report_thread = common_ai_log.AiReportThread(
            api_base_url=config.ai_api_base_url,
            api_key=config.ai_api_key,
            model_name=config.ai_model_name,
            conversations_data=data,
            output_file=output_file,
        )
        self._ai_report_thread.finished_signal.connect(self._ai_report_finished)
        self._ai_report_thread.error_signal.connect(self._ai_report_error)
        self._ai_report_thread.start()

        # Estimate time: ~1.5min per batch of 50 records.
        total = len(data['question'])
        batch_count = (total + 49) // 50
        est_minutes = max(1, round(batch_count * 1.5))

        # Show non-modal info dialog (user can dismiss it anytime).
        self._ai_report_msgbox = QMessageBox(QMessageBox.Information, 'Problem Analysis',
                                             f'Generating analysis report ({total} conversations) ...\n'
                                             f'Approximately {est_minutes} min, will auto-open when done.\n\n'
                                             f'You can close this dialog and continue working.',
                                             QMessageBox.Close, self)
        self._ai_report_msgbox.setModal(False)
        self._ai_report_msgbox.show()

    def _ai_report_close_msgbox(self):
        """Close the report progress dialog if it exists."""
        if hasattr(self, '_ai_report_msgbox') and self._ai_report_msgbox:
            self._ai_report_msgbox.close()
            self._ai_report_msgbox = None

    def _ai_report_finished(self, output_file):
        """Called when AI report generation is complete."""
        self._ai_report_close_msgbox()

        if not self._open_in_browser(output_file):
            QMessageBox.information(self, 'Problem Analysis', f'报告已生成，但无法自动打开浏览器。\n报告路径：\n{output_file}')

    def _ai_report_error(self, error_msg):
        """Called when AI report generation fails."""
        self._ai_report_close_msgbox()

        QMessageBox.warning(self, 'Error', f'Failed to generate report:\n{error_msg}')

    def ai_cluster_analysis(self):
        """Generate an AI cluster analysis HTML report and open it in the browser."""
        if not self.ai_configured:
            QMessageBox.warning(self, 'Warning', 'AI is not configured. Cannot generate cluster analysis report.')
            return

        # Guard against re-entry: starting a second run would drop the Python
        # reference to the still-running QThread and crash the app with
        # "QThread: Destroyed while thread is still running".
        if getattr(self, '_cluster_analysis_thread', None) is not None and self._cluster_analysis_thread.isRunning():
            QMessageBox.information(self, 'Cluster Analysis', '集群分析报告正在生成中，请稍候 ...')
            return

        # Output path: <cluster_db_path>/ai_report/cluster_analysis_<timestamp>.html.
        # Falls back to a per-user temp dir when cluster_db_path is read-only.
        report_dir = common_ai.resolve_report_dir(self.cluster_db_path)
        output_file = report_dir + '/cluster_analysis_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.html'

        lmstat_path = config.lmstat_path if hasattr(config, 'lmstat_path') else 'lmstat'
        lmstat_bsub_command = config.lmstat_bsub_command if hasattr(config, 'lmstat_bsub_command') else ''
        embedding_model = config.ai_embedding_model_name if hasattr(config, 'ai_embedding_model_name') else ''
        embedding_api_base_url = config.ai_embedding_api_base_url if hasattr(config, 'ai_embedding_api_base_url') else ''
        embedding_api_key = config.ai_embedding_api_key if hasattr(config, 'ai_embedding_api_key') else ''

        self._cluster_analysis_thread = common_ai.ClusterAnalysisThread(
            api_base_url=config.ai_api_base_url,
            api_key=config.ai_api_key,
            model_name=config.ai_model_name,
            output_file=output_file,
            tool=self.tool,
            db_path=self.cluster_db_path,
            lmstat_path=lmstat_path,
            lmstat_bsub_command=lmstat_bsub_command,
            doc_chunks=self.ai_doc_chunks,
            embedding_model=embedding_model,
            embedding_api_base_url=embedding_api_base_url,
            embedding_api_key=embedding_api_key,
            cluster=self.cluster,
            debug=self.ai_debug_action.isChecked(),
        )
        self._cluster_analysis_canceled = False
        self._cluster_analysis_thread.finished_signal.connect(self._cluster_analysis_finished)
        self._cluster_analysis_thread.error_signal.connect(self._cluster_analysis_error)
        self._cluster_analysis_thread.start()

        # Independent, non-modal info dialog (same style as other bmonitor
        # dialogs). The Cancel button aborts the analysis.
        self._cluster_analysis_msgbox = QMessageBox(QMessageBox.Information, 'Cluster Analysis',
                                                    '正在分析集群状况，请稍候 ...\n'
                                                    '（耗时通常数十秒到数分钟，完成后会弹出提示并自动打开报告）\n\n'
                                                    '可点击 Cancel 中止本次分析。',
                                                    QMessageBox.Cancel, self)
        self._cluster_analysis_msgbox.setModal(False)
        self._cluster_analysis_msgbox.buttonClicked.connect(self._cluster_analysis_cancel)
        self._cluster_analysis_msgbox.show()

    def _cluster_analysis_close_msgbox(self):
        """Close the cluster analysis progress dialog if it exists."""
        if hasattr(self, '_cluster_analysis_msgbox') and self._cluster_analysis_msgbox:
            self._cluster_analysis_msgbox.close()
            self._cluster_analysis_msgbox = None

    def _cluster_analysis_cancel(self, button=None):
        """User canceled: ask the thread to stop and suppress the pending result."""
        self._cluster_analysis_canceled = True

        if getattr(self, '_cluster_analysis_thread', None) is not None:
            self._cluster_analysis_thread.stop()

        self._cluster_analysis_close_msgbox()

    def _cluster_analysis_finished(self, output_file):
        """Called when cluster analysis report generation is complete."""
        self._cluster_analysis_close_msgbox()

        if getattr(self, '_cluster_analysis_canceled', False):
            return

        opened = self._open_in_browser(output_file)

        if opened:
            text = f'集群分析完成，报告已在浏览器中打开。\n\n报告路径：\n{output_file}'
        else:
            text = f'集群分析完成，但无法自动打开浏览器，请手动打开：\n\n{output_file}'

        # Always announce completion so the result is unmistakable even if the
        # browser fails to open.
        QMessageBox.information(self, 'Cluster Analysis', text)

    @staticmethod
    def _open_in_browser(output_file):
        """Open a local HTML report in the system browser. Returns True on success.

        The bmonitor launcher prepends $LSFMONITOR_INSTALL_PATH/lib (which ships
        a custom libsqlite3.so.0) to LD_LIBRARY_PATH. A browser spawned by this
        process inherits that and loads the wrong libsqlite3, dying silently with
        no window and no error. So spawn the browser with that lib dir stripped
        from LD_LIBRARY_PATH, via subprocess (QDesktopServices/webbrowser can't
        take a custom env).
        """
        import shutil
        import subprocess

        url = QUrl.fromLocalFile(output_file).toString()

        env = os.environ.copy()
        install_lib = os.path.join(os.environ.get('LSFMONITOR_INSTALL_PATH', ''), 'lib')
        ld_path = env.get('LD_LIBRARY_PATH', '')

        if ld_path and install_lib:
            kept = [p for p in ld_path.split(':') if p and os.path.normpath(p) != os.path.normpath(install_lib)]

            if kept:
                env['LD_LIBRARY_PATH'] = ':'.join(kept)
            else:
                env.pop('LD_LIBRARY_PATH', None)

        candidates = []
        xdg_open = shutil.which('xdg-open')

        if xdg_open:
            candidates.append([xdg_open, url])

        for browser in (os.environ.get('BROWSER'), 'firefox', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
            if browser:
                browser_path = shutil.which(browser)

                if browser_path:
                    candidates.append([browser_path, url])

        for command in candidates:
            try:
                subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except Exception:
                continue

        return False

    def _cluster_analysis_error(self, error_msg):
        """Called when cluster analysis report generation fails."""
        self._cluster_analysis_close_msgbox()

        if getattr(self, '_cluster_analysis_canceled', False):
            return

        QMessageBox.warning(self, 'Error', f'Failed to generate cluster analysis report:\n{error_msg}')

    def ai_record_cleanup(self):
        """Clean up AI conversation records by entries limit."""
        if not hasattr(self, 'ai_log_db_file') or not self.ai_log_db_file:
            QMessageBox.warning(self, 'Warning', 'AI log database is not initialized.')
            return

        user_list = common_ai_log.get_user_list(self.ai_log_db_file)

        if not user_list:
            QMessageBox.information(self, 'Info', 'No conversation records found.')
            return

        # Ask for entries limit.
        entries_limit, ok = QInputDialog.getInt(self, 'Record Cleanup', 'Max records to keep per user:', 100, 0, 100000, 10)

        if not ok:
            return

        total_deleted = 0

        for user in user_list:
            deleted = common_ai_log.cleanup_conversations(self.ai_log_db_file, user, entries_limit)
            total_deleted += deleted

        QMessageBox.information(self, 'Cleanup Done', f'Cleaned up {total_deleted} records across {len(user_list)} users.\nEach user keeps at most {entries_limit} records.')

# For AI Menu (end) #

    def closeEvent(self, QCloseEvent):
        """
        When window close, post-process.
        """
        # Stop AI thread if running.
        if self.ai_thread and self.ai_thread.isRunning():
            self.ai_thread.stop()
            self.ai_thread.wait(3000)

        common.bprint('Bye', date_format='%Y-%m-%d %H:%M:%S')
        self.my_save_log.save_log('Exit lsfMonitor')


class AiInputBox(QTextEdit):
    """
    Multi-line input box for AI tab.
    Enter sends message, Shift+Enter inserts newline.
    """
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.send_requested.emit()
        else:
            super().keyPressEvent(event)


class AiRecordSearchWindow(QWidget):
    """Window for searching and browsing AI conversation records."""

    def __init__(self, db_file):
        super().__init__()
        self.db_file = db_file
        self._search_results = {}
        self.setWindowTitle('AI Record Search')
        self.resize(1000, 700)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout()

        # Top filter area.
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel('Keyword:'))
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText('Search question/answer/keywords')
        filter_layout.addWidget(self.keyword_edit)

        filter_layout.addWidget(QLabel('User:'))
        self.user_edit = QLineEdit()
        self.user_edit.setFixedWidth(100)
        filter_layout.addWidget(self.user_edit)

        filter_layout.addWidget(QLabel('From:'))
        self.date_start_edit = QLineEdit()
        self.date_start_edit.setPlaceholderText('YYYY-MM-DD')
        self.date_start_edit.setFixedWidth(110)
        filter_layout.addWidget(self.date_start_edit)

        filter_layout.addWidget(QLabel('To:'))
        self.date_end_edit = QLineEdit()
        self.date_end_edit.setPlaceholderText('YYYY-MM-DD')
        self.date_end_edit.setFixedWidth(110)
        filter_layout.addWidget(self.date_end_edit)

        filter_layout.addWidget(QLabel('Status:'))
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(['all', 'solved', 'unsolved', 'unknown'])
        self.resolution_combo.setFixedWidth(100)
        filter_layout.addWidget(self.resolution_combo)

        search_button = QPushButton('Search')
        search_button.clicked.connect(self._do_search)
        filter_layout.addWidget(search_button)

        main_layout.addLayout(filter_layout)

        # Results table.
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(['Time', 'User', 'Question', 'Status'])
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.result_table.clicked.connect(self._on_row_clicked)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self._show_context_menu)
        main_layout.addWidget(self.result_table)

        # Detail area.
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(250)
        main_layout.addWidget(self.detail_text)

        self.setLayout(main_layout)

        # Load all records initially.
        self._do_search()

    def _do_search(self):
        """Execute search with current filter values."""
        self._search_results = common_ai_log.search_conversations(
            db_file=self.db_file,
            keyword=self.keyword_edit.text().strip(),
            user=self.user_edit.text().strip(),
            date_start=self.date_start_edit.text().strip(),
            date_end=self.date_end_edit.text().strip(),
            resolution=self.resolution_combo.currentText(),
        )

        self._populate_table()

    def _populate_table(self):
        """Fill table with search results."""
        data = self._search_results

        if not data or 'timestamp' not in data:
            self.result_table.setRowCount(0)
            return

        count = len(data['timestamp'])
        self.result_table.setRowCount(count)

        for i in range(count):
            self.result_table.setItem(i, 0, QTableWidgetItem(data['timestamp'][i] or ''))
            self.result_table.setItem(i, 1, QTableWidgetItem(data.get('user', [''])[i] or ''))

            question = data.get('question', [''])[i] or ''
            self.result_table.setItem(i, 2, QTableWidgetItem(question[:100]))

            self.result_table.setItem(i, 3, QTableWidgetItem(data.get('resolution', [''])[i] or ''))

    def _on_row_clicked(self, index):
        """Show full detail for clicked row."""
        row = index.row()
        data = self._search_results

        if not data or 'question' not in data or row >= len(data['question']):
            return

        question = data['question'][row] or ''
        answer = data.get('answer', [''])[row] or ''
        tool_calls = data.get('tool_calls', ['[]'])[row] or '[]'
        session_id = data.get('session_id', [''])[row] or ''
        resolution = data.get('resolution', [''])[row] or ''
        cluster = data.get('cluster', [''])[row] or ''
        host = data.get('host', [''])[row] or ''

        # Format tool calls.
        tool_text = ''

        try:
            tools = json.loads(tool_calls)

            if tools:
                tool_lines = []

                for t in tools:
                    tool_lines.append(f"  - {t.get('name', 'unknown')}: {t.get('args', '')}")

                tool_text = '\n'.join(tool_lines)
        except (json.JSONDecodeError, TypeError):
            tool_text = tool_calls

        detail = (
            f'<b>Session ID:</b> {session_id}<br>'
            f'<b>Status:</b> {resolution} | <b>Cluster:</b> {cluster} | <b>Host:</b> {host}<br><br>'
            f'<b>Question:</b><br>{question.replace(chr(10), "<br>")}<br><br>'
            f'<b>Answer:</b><br>{answer.replace(chr(10), "<br>")}<br><br>'
        )

        if tool_text:
            detail += f'<b>Tool Calls:</b><br><pre>{tool_text}</pre>'

        self.detail_text.setHtml(detail)

    def _show_context_menu(self, pos):
        """Right-click context menu to update resolution."""
        row = self.result_table.rowAt(pos.y())

        if row < 0:
            return

        data = self._search_results

        if not data or 'session_id' not in data or row >= len(data['session_id']):
            return

        session_id = data['session_id'][row]
        row_user = data.get('user', [''])[row] or ''

        menu = QMenu(self)
        mark_solved = menu.addAction('Mark as Solved')
        mark_unsolved = menu.addAction('Mark as Unsolved')

        action = menu.exec_(self.result_table.mapToGlobal(pos))

        if action == mark_solved:
            common_ai_log.update_resolution(self.db_file, session_id, 'solved', user=row_user)
            data['resolution'][row] = 'solved'
            self.result_table.setItem(row, 3, QTableWidgetItem('solved'))
        elif action == mark_unsolved:
            common_ai_log.update_resolution(self.db_file, session_id, 'unsolved', user=row_user)
            data['resolution'][row] = 'unsolved'
            self.result_table.setItem(row, 3, QTableWidgetItem('unsolved'))


class CheckIssueReason(QThread):
    """
    Start tool check_issue_reason to debug issue job.
    """
    def __init__(self, job='', issue='PEND'):
        super(CheckIssueReason, self).__init__()
        self.job = job
        self.issue = issue

    def run(self):
        command = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor/tools/check_issue_reason -i ' + str(self.issue)

        if self.job:
            common.bprint(f'Getting job {self.issue.lower()} reason for "{self.job}" ...', date_format='%Y-%m-%d %H:%M:%S')
            command = str(command) + ' -j ' + str(self.job)

        os.system(command)


class ShowLicenseFeatureUsage(QThread):
    """
    Start tool show_license_feature_usage to show license feature usage information.
    """
    def __init__(self, server, vendor, feature):
        super(ShowLicenseFeatureUsage, self).__init__()
        self.server = server
        self.vendor = vendor
        self.feature = feature

    def run(self):
        command = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor/tools/show_license_feature_usage -s ' + str(self.server) + ' -v ' + str(self.vendor) + ' -f ' + str(self.feature)
        os.system(command)


class ShowMessage(QThread):
    """
    Show message with tool message.
    """
    def __init__(self, title, message):
        super(ShowMessage, self).__init__()
        self.title = title
        self.message = message

    def run(self):
        command = 'python3 ' + str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor/tools/message.py --title "' + str(self.title) + '" --message "' + str(self.message) + '"'
        os.system(command)


#################
# Main Function #
#################
def main():
    (specified_job, specified_user, specified_feature, specified_tab, disable_license, dark_mode) = read_args()
    app = QApplication(sys.argv)
    mw = MainWindow(specified_job, specified_user, specified_feature, specified_tab, disable_license, dark_mode)
    mw.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
