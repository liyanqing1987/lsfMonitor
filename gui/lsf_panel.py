# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import copy
import shlex
import datetime
import subprocess
from pathlib import Path
import numpy as np

from PyQt5.QtCore import QDate, Qt, QThread, QTimer
from PyQt5.QtGui import QBrush, QColor, QFont, QIcon
from PyQt5.QtWidgets import QAction, QApplication, QDateEdit, QFileDialog, QFrame, QGridLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QInputDialog

LSFMONITOR_INSTALL_PATH = Path(os.environ['LSFMONITOR_INSTALL_PATH'])
sys.path.append(str(LSFMONITOR_INSTALL_PATH))
from common import common
from common import common_db_path
from common import common_lsf
from common import common_pyqt5
from common import common_sqlite3

# Shared loading-prompt QThread (tracked subprocess + safe terminate()).
# lsf_panel previously had its own unsafe ShowMessage (os.system + no
# terminate() override); rebinding its local while still running aborted with
# "QThread: Destroyed while thread is still running".
ShowMessage = common_pyqt5.ShowMessage

from common import common_config

from gui.panel_base import PanelBase
from gui.theme import STATUS_RUN, STATUS_PEND, STATUS_DONE, STATUS_EXIT
from gui import theme

config_lsf = common_config.load_config('lsf')

from gui.version import VERSION, RELEASE_DATE, USER

# Constants
DEFAULT_RUNTIME_DIR = Path('/tmp') / f'runtime-{USER}'

# Inner tab names (avoid magic strings scattered through tab_dic / addTab).
TAB_JOB = 'JOB'
TAB_JOBS = 'JOBS'
TAB_HOSTS = 'HOSTS'
TAB_LOAD = 'LOAD'
TAB_USERS = 'USERS'
TAB_QUEUES = 'QUEUES'
TAB_UTILIZATION = 'UTILIZATION'

# Environment configuration
os.environ.update({
    'LSB_NTRIES': '3',
    'PYTHONUNBUFFERED': '1',
    'XDG_RUNTIME_DIR': os.environ.get('XDG_RUNTIME_DIR', str(DEFAULT_RUNTIME_DIR))
})

# Ensure runtime directory exists
DEFAULT_RUNTIME_DIR.mkdir(exist_ok=True)
DEFAULT_RUNTIME_DIR.chmod(0o700)


class LsfPanel(PanelBase):
    """
    LSF monitoring panel. Embedded as an outer tab in the unified MainWindow.
    Owns an inner QTabWidget (JOB/JOBS/HOSTS/LOAD/USERS/QUEUES/UTILIZATION/LICENSE).
    AI tab is provided by the separate AiPanel.
    """

    def __init__(self, context, parent=None, args=None):
        super().__init__(context, parent)

        # Show version information.
        common.bprint(f'lsfMonitor Version: {VERSION} ({RELEASE_DATE})', date_format='%Y-%m-%d %H:%M:%S')
        common.bprint('', date_format='%Y-%m-%d %H:%M:%S')

        # Entry args. -u/-H/-f are now nargs='+' lists; join back to a
        # space-separated string so the downstream GUI inputs (which .split()
        # on whitespace) keep working unchanged.
        self.specified_job = getattr(args, 'jobid', 0) if args else 0
        self.specified_user = ' '.join(getattr(args, 'user', []) or []) if args else ''
        self.specified_host = ' '.join(getattr(args, 'host', []) or []) if args else ''
        self.specified_tab = getattr(args, 'tab', '') if args else ''
        self.dark_mode = getattr(args, 'dark_mode', False) if args else False

        # Check cluster info.
        common.bprint('Checking cluster information ...', date_format='%Y-%m-%d %H:%M:%S')
        (self.tool, self.cluster) = self.check_cluster_info()

        # Reload cluster-specific config_lsf if exists.
        common_config.reload_config_for_cluster(self.cluster)

        # Set db_path. Defaults to config_lsf.db_path (e.g. <prefix>/db/lsf); when a
        # cluster is detected, scope to <db_path>/<cluster>. This matches bsample's
        # db_path layout so GUI reads exactly where the sampler writes.
        # resolve_db_path: config_lsf.db_path if set, else config.py db_path/lsf.
        self.lsf_db_root = common_db_path.resolve_db_path(config_lsf, 'lsf')
        self.cluster_db_path = self.lsf_db_root

        if self.cluster:
            self.cluster_db_path = self.lsf_db_root + '/' + str(self.cluster)

        # Sampling dirs are written by bsample (owner); the GUI only reads, so
        # these are 0o755. create_dir() is a no-op when the dir already exists,
        # so it never alters a sampler-created dir's owner/mode.
        common.create_dir(self.lsf_db_root, 0o755)
        common.create_dir(self.cluster_db_path, 0o755)

        # Publish cluster context onto AppContext for sibling panels (e.g. AiPanel).
        self.context.tool = self.tool
        self.context.cluster = self.cluster
        self.context.cluster_db_path = self.cluster_db_path

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
        self.host_group_dic = {}
        self.bhosts_load_dic = {}

        # Set self.lsf_info_dic for how to get LSF information.
        self.lsf_info_dic = {'bhosts': {'exec_cmd': 'self.bhosts_dic = common_lsf.get_bhosts_info()', 'update_second': 0},
                             'lsload': {'exec_cmd': 'self.lsload_dic = common_lsf.get_lsload_info()', 'update_second': 0},
                             'bqueues': {'exec_cmd': 'self.bqueues_dic = common_lsf.get_bqueues_info()', 'update_second': 0},
                             'busers': {'exec_cmd': 'self.busers_dic = common_lsf.get_busers_info()', 'update_second': 0},
                             'lshosts': {'exec_cmd': 'self.lshosts_dic = common_lsf.get_lshosts_info()', 'update_second': 0},
                             'queue_host': {'exec_cmd': 'self.queue_host_dic = common_lsf.get_queue_host_info()', 'update_second': 0},
                             'host_queue': {'exec_cmd': 'self.host_queue_dic = common_lsf.get_host_queue_info()', 'update_second': 0},
                             'host_group': {'exec_cmd': 'self.host_group_dic = common_lsf.get_host_group_info()', 'update_second': 0},
                             'bhosts_load': {'exec_cmd': 'self.bhosts_load_dic = common_lsf.get_bhosts_load_info()', 'update_second': 0}}

        # Just update specified_job info if specified_job argument is specified.
        if self.specified_job:
            current_second = int(time.time())

            for item in self.lsf_info_dic.keys():
                self.lsf_info_dic[item]['update_second'] = current_second

        # Generate GUI (creates tab placeholders; the initial visible tab is
        # built eagerly in init_ui, the rest lazily on first click).
        self.init_ui()

        common.bprint('lsfMonitor is ready.', date_format='%Y-%m-%d %H:%M:%S')
        print('')

    def on_first_show(self):
        """Called once when the user first sees this panel.

        Determines the initial inner tab from entry args, builds it (triggering
        lazy construction), and pre-fills filter fields + auto-clicks Check.
        """
        # Infer initial tab: explicit --tab > --jobid(JOB) > --host(LOAD) >
        # --user(JOBS) > JOBS(default).
        if not self.specified_tab:
            if self.specified_job:
                self.specified_tab = TAB_JOB
            elif self.specified_host:
                self.specified_tab = TAB_LOAD
            else:
                self.specified_tab = TAB_JOBS

        self.switch_tab(self.specified_tab)

        # Pre-fill all relevant tabs with the specified values and auto-check.
        self.apply_entry_args(None)

    def apply_entry_args(self, args):
        """Pre-fill filter fields in all relevant LSF tabs and auto-click Check.

        Called from on_first_show after the initial tab is built. Uses the
        values stored in self (specified_job/specified_user/specified_host)
        rather than re-reading args.
        """
        # JOBS tab: fill user + auto-check.
        if self.specified_user:
            self._ensure_inner_tab_built(self.jobs_tab)

            if hasattr(self, 'jobs_tab_user_line'):
                self.jobs_tab_user_line.setText(str(self.specified_user))
                self.gen_jobs_tab_table()

        # JOB tab: fill jobid + auto-check (on_first_show already built it if
        # specified_job was set; gen_job_tab handles the Check via
        # check_job_on_job_tab).
        # (handled during gen_job_tab build, no extra action needed here)

        # HOSTS tab: fill host line (space-separated multi-host, the line's
        # consumer splits on whitespace so multiple hosts all apply).
        if self.specified_host:
            self._ensure_inner_tab_built(self.hosts_tab)

            if hasattr(self, 'hosts_tab_host_line'):
                self.hosts_tab_host_line.setText(str(self.specified_host))
                self.gen_hosts_tab_table()

        # LOAD tab: only accepts a single host. When -H passed multiple, fill
        # the first one and warn about the rest so the user knows why.
        if self.specified_host:
            host_list = str(self.specified_host).split()
            first_host = host_list[0] if host_list else ''

            if len(host_list) > 1:
                common.bprint(f'LOAD tab accepts a single host only; using "{first_host}", '
                              f'remaining {len(host_list) - 1} host(s) ignored.', level='Warning')

            self._ensure_inner_tab_built(self.load_tab)

            if first_host and hasattr(self, 'load_tab_host_line'):
                self.load_tab_host_line.setText(first_host)
                self.update_load_tab_load_info()

        # USERS tab: fill user/queue + auto-check.
        if self.specified_user:
            self._ensure_inner_tab_built(self.users_tab)

            if hasattr(self, 'users_tab_user_line'):
                self.users_tab_user_line.setText(str(self.specified_user))
                self.gen_users_tab_table()

    def check_cluster_info(self):
        """
        Make sure LSF/Volclava/Openlava environment exists.

        In the unified GUI (lsf + license + ai panels in one process), a missing
        LSF environment must NOT terminate the whole app — the user may only need
        the License panel. So we warn and return empty cluster; LSF tabs will show
        empty data downstream instead of exiting.
        """
        if ('LSFMONITOR_FAKE_RUN' in os.environ) and (os.environ['LSFMONITOR_FAKE_RUN'] == 'True'):
            (tool, tool_version, cluster, master) = ('LSF', '10.1.0.12', 'FAKE_CLUSTER', 'fake-lsf-main-m1')
        else:
            (tool, tool_version, cluster, master) = common_lsf.get_lsid_info()

        if tool == '':
            common.bprint('Not find any LSF/Volclava/Openlava environment! LSF panel will show empty data.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            common.bprint('', date_format='%Y-%m-%d %H:%M:%S')

            return '', ''

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
                my_show_message = ShowMessage('Info', f'Loading LSF {lsf_info} information ...', persistent=True)
                my_show_message.start()

                exec(self.lsf_info_dic[lsf_info]['exec_cmd'])
                self.lsf_info_dic[lsf_info]['update_second'] = current_second

                time.sleep(0.01)
                my_show_message.terminate()
                my_show_message.wait(3000)

    def _parse_queue_full_name(self, full_name):
        """
        正确拆分 '{cluster}-{queue}' 格式，处理集群名含 '-' 的情况。
        返回 (cluster, queue_name)，无法识别则返回 (None, full_name)。
        """
        # 优先匹配当前集群
        prefix = self.cluster + '-'

        if full_name.startswith(prefix):
            return (self.cluster, full_name[len(prefix):])

        # 遍历 DB 中所有已知集群
        db_root_path = Path(self.lsf_db_root)

        if db_root_path.exists() and db_root_path.is_dir():
            for entry in os.scandir(db_root_path):
                if entry.is_dir() and entry.name != self.cluster:
                    prefix = entry.name + '-'

                    if full_name.startswith(prefix):
                        return (entry.name, full_name[len(prefix):])

        return (None, full_name)

    def _parse_group_full_name(self, full_name):
        """
        正确拆分 '{cluster}-{group}' 格式,语义与 _parse_queue_full_name 相同,
        仅为 host group 维度的可读性而存在。
        """
        return self._parse_queue_full_name(full_name)

    def init_ui(self):
        """
        Create placeholder QWidgets for every inner tab, add them to main_tab
        (so the tab bar is fully visible), then build ONLY the tab that will
        be shown first. Heavy tabs (HOSTS/USERS/QUEUES/UTILIZATION/INNER-LICENSE)
        are registered for lazy construction via ``_register_lazy_tab`` so that
        their LSF/license command invocations do not run at startup.
        """
        # main_tab is provided by PanelBase; menubar is contributed by
        # register_menubar_actions() onto the host MainWindow. AI tab is owned
        # by the separate AiPanel, not added here.

        # Define sub-tab placeholders.
        self.job_tab = QWidget()
        self.jobs_tab = QWidget()
        self.hosts_tab = QWidget()
        self.load_tab = QWidget()
        self.users_tab = QWidget()
        self.queues_tab = QWidget()
        self.utilization_tab = QWidget()

        # Add the sub-tabs into main Tab widget (labels visible immediately).
        # The LSF inner LICENSE tab is removed; license monitoring lives in the
        # outer LICENSE panel instead.
        self.main_tab.addTab(self.job_tab, TAB_JOB)
        self.main_tab.addTab(self.jobs_tab, TAB_JOBS)
        self.main_tab.addTab(self.hosts_tab, TAB_HOSTS)
        self.main_tab.addTab(self.load_tab, TAB_LOAD)
        self.main_tab.addTab(self.users_tab, TAB_USERS)
        self.main_tab.addTab(self.queues_tab, TAB_QUEUES)
        self.main_tab.addTab(self.utilization_tab, TAB_UTILIZATION)

        # Decide which tab to build eagerly (the initial visible tab).
        # All other tabs are registered for lazy build on first click.
        if self.specified_job:
            initial_tab = self.job_tab
            initial_builder = self.gen_job_tab
        elif self.specified_user:
            initial_tab = self.jobs_tab
            initial_builder = self.gen_jobs_tab
        elif self.specified_tab:
            tab_map = {
                TAB_JOB: (self.job_tab, self.gen_job_tab),
                TAB_JOBS: (self.jobs_tab, self.gen_jobs_tab),
                TAB_HOSTS: (self.hosts_tab, self.gen_hosts_tab),
                TAB_LOAD: (self.load_tab, self.gen_load_tab),
                TAB_USERS: (self.users_tab, self.gen_users_tab),
                TAB_QUEUES: (self.queues_tab, self.gen_queues_tab),
                TAB_UTILIZATION: (self.utilization_tab, self.gen_utilization_tab),
            }

            if self.specified_tab in tab_map:
                initial_tab, initial_builder = tab_map[self.specified_tab]
            else:
                initial_tab = self.jobs_tab
                initial_builder = self.gen_jobs_tab
        else:
            initial_tab = self.jobs_tab
            initial_builder = self.gen_jobs_tab

        # Build the initial tab eagerly.
        common.bprint('Generating {} tab ...'.format(
            self.main_tab.tabText(self.main_tab.indexOf(initial_tab))
        ), date_format='%Y-%m-%d %H:%M:%S')
        initial_builder()

        # Register every OTHER tab for lazy construction. We detect whether a
        # tab has already been built by checking if its layout is set (every
        # gen_*_tab method calls tab.setLayout before returning).
        lazy_specs = [
            (self.job_tab, self.gen_job_tab, TAB_JOB),
            (self.jobs_tab, self.gen_jobs_tab, TAB_JOBS),
            (self.hosts_tab, self.gen_hosts_tab, TAB_HOSTS),
            (self.load_tab, self.gen_load_tab, TAB_LOAD),
            (self.users_tab, self.gen_users_tab, TAB_USERS),
            (self.queues_tab, self.gen_queues_tab, TAB_QUEUES),
            (self.utilization_tab, self.gen_utilization_tab, TAB_UTILIZATION),
        ]

        for tab_widget, gen_method, tab_name in lazy_specs:
            if tab_widget is initial_tab:
                continue

            if tab_widget.layout() is None:
                self._register_lazy_tab(tab_widget, self._make_lazy_builder(gen_method, tab_name))

        # Window chrome (title/icon/resize/center/dark stylesheet) is handled by
        # the host MainWindow; this panel is an embedded QWidget.

    def _make_lazy_builder(self, gen_method, tab_name):
        """Return a zero-arg callable that logs and invokes gen_method."""
        def _build():
            common.bprint('Generating {} tab ...'.format(tab_name), date_format='%Y-%m-%d %H:%M:%S')
            gen_method()

        return _build

    def switch_tab(self, specified_tab):
        """
        Switch to the specified inner tab.

        Lazy construction of non-initial tabs is handled by the base-class
        `_on_inner_tab_changed` slot connected to `main_tab.currentChanged`,
        so switching here is simply a setCurrentWidget call; the first time a
        placeholder tab becomes current its gen_*_tab() builder runs.
        """
        tab_dic = {TAB_JOB: self.job_tab,
                   TAB_JOBS: self.jobs_tab,
                   TAB_HOSTS: self.hosts_tab,
                   TAB_LOAD: self.load_tab,
                   TAB_USERS: self.users_tab,
                   TAB_QUEUES: self.queues_tab,
                   TAB_UTILIZATION: self.utilization_tab}

        # Tab name is case-insensitive (user may type job/Jobs/JOBS).
        specified_tab = specified_tab.upper() if specified_tab else ''

        if specified_tab not in tab_dic:
            return

        self.main_tab.setCurrentWidget(tab_dic[specified_tab])

    def register_menubar_actions(self, menubar, shared_menus=None):
        """
        Register this panel's menubar actions onto the shared top-level menus.
        The LSF panel also owns the application-wide Exit and About entries.
        """
        file_menu = shared_menus['File']
        setup_menu = shared_menus['Setup']
        function_menu = shared_menus['Function']
        help_menu = shared_menus['Help']

        # ----- File (LSF exports) -----
        export_jobs_table_action = QAction('Export LSF jobs table', self)
        export_jobs_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_jobs_table_action.triggered.connect(self.export_jobs_table)

        export_hosts_table_action = QAction('Export LSF hosts table', self)
        export_hosts_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_hosts_table_action.triggered.connect(self.export_hosts_table)

        export_users_table_action = QAction('Export LSF users table', self)
        export_users_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_users_table_action.triggered.connect(self.export_users_table)

        export_queues_table_action = QAction('Export LSF queues table', self)
        export_queues_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_queues_table_action.triggered.connect(self.export_queues_table)

        export_utilization_table_action = QAction('Export LSF utilization table', self)
        export_utilization_table_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/save.png'))
        export_utilization_table_action.triggered.connect(self.export_utilization_table)

        for act in (export_jobs_table_action, export_hosts_table_action,
                    export_users_table_action, export_queues_table_action,
                    export_utilization_table_action):
            file_menu.addAction(act)

        # ----- Setup (LSF) -----
        self.enable_queue_detail_action = QAction('Enable LSF queue detail', self, checkable=True)
        self.enable_queue_detail_action.setChecked(False)
        self.enable_queue_detail_action.toggled.connect(self.func_enable_queue_detail)

        self.enable_utilization_detail_action = QAction('Enable LSF utilization detail', self, checkable=True)
        self.enable_utilization_detail_action.setChecked(False)
        self.enable_utilization_detail_action.toggled.connect(self.func_enable_utilization_detail)

        setup_menu.addAction(self.enable_queue_detail_action)
        setup_menu.addAction(self.enable_utilization_detail_action)

        # ----- Function (LSF) -----
        check_pend_reason_action = QAction('Check LSF Pend reason', self)
        check_pend_reason_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/pend.png'))
        check_pend_reason_action.triggered.connect(self.check_pend_reason)
        check_slow_reason_action = QAction('Check LSF Slow reason', self)
        check_slow_reason_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/slow.png'))
        check_slow_reason_action.triggered.connect(self.check_slow_reason)
        check_fail_reason_action = QAction('Check LSF Fail reason', self)
        check_fail_reason_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/fail.png'))
        check_fail_reason_action.triggered.connect(self.check_fail_reason)

        function_menu.addAction(check_pend_reason_action)
        function_menu.addAction(check_slow_reason_action)
        function_menu.addAction(check_fail_reason_action)

        # ----- Help (application-level, owned by LSF panel) -----
        version_action = QAction('Version', self)
        version_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/version.png'))
        version_action.triggered.connect(self.show_version)

        about_action = QAction('About lsfMonitor', self)
        about_action.setIcon(QIcon(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/data/pictures/about.png'))
        about_action.triggered.connect(self.show_about)

        help_menu.addAction(version_action)
        help_menu.addAction(about_action)

    def func_enable_queue_detail(self, state):
        """
        Show detail information for RUN/PEND curve on QUEUE tab.
        """
        self.enable_queue_detail = state

        if hasattr(self, 'queues_tab_begin_date_edit'):
            if state:
                self.queues_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-7))
            else:
                self.queues_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

    def func_enable_utilization_detail(self, state):
        """
        Show detail information for utilization curve on UTILIZATION tab.
        """
        self.enable_utilization_detail = state

        if hasattr(self, 'utilization_tab_begin_date_edit'):
            if state:
                self.utilization_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-7))
            else:
                self.utilization_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

    def _stop_check_issue_reason_thread(self):
        """Stop any running check_issue_reason thread before starting a new one."""
        old_thread = getattr(self, 'my_check_issue_reason', None)

        if old_thread is not None and old_thread.isRunning():
            old_thread.terminate()
            old_thread.wait(3000)

    def check_pend_reason(self, job=''):
        """
        Call a separate script to check job pend reason.
        """
        self._stop_check_issue_reason_thread()
        self.my_check_issue_reason = CheckIssueReason(job=job, issue='PEND')
        self.my_check_issue_reason.start()

    def check_slow_reason(self, job=''):
        """
        Call a separate script to check job slow reason.
        """
        self._stop_check_issue_reason_thread()
        self.my_check_issue_reason = CheckIssueReason(job=job, issue='SLOW')
        self.my_check_issue_reason.start()

    def check_fail_reason(self, job=''):
        """
        Call a separate script to check job fail reason.
        """
        self._stop_check_issue_reason_thread()
        self.my_check_issue_reason = CheckIssueReason(job=job, issue='FAIL')
        self.my_check_issue_reason.start()

    def show_version(self):
        """
        Show lsfMonitor version information.
        """
        QMessageBox.about(self, 'lsfMonitor', 'Version: ' + str(VERSION) + ' (' + str(RELEASE_DATE) + ')')

    def show_about(self):
        """
        Show lsfMonitor about information.
        """
        about_message = """
🚀 lsfMonitor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

让 IC 团队的作业、License、机器状态，一屏搞定。

🎯 功能模块
   LSF 集群     作业 · 主机 · 队列 · 负载 · 利用率
   EDA License  feature 占用 · 到期预警 · 利用率曲线
   批量运维     跨主机并行 ssh 执行命令
   AI 助手      自然语言提问，自动查来回答你

🛠️ 命令行工具
   bmonitor        GUI 监控界面
   bmonitor_cli     命令行查询（JSON 输出）
   bsample          LSF 数据采样
   license_monitor   License GUI
   license_sample    License 数据采样

Made with ❤️  by liyanqing.1987
反馈：liyanqing1987@163.com"""

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

        # 360px gives the label+value columns in frame1 enough room to show
        # full timestamps like "Thu Aug 27 11:43:26" (≈20 chars) without clipping.
        job_tab_grid.setColumnMinimumWidth(0, 360)

        self.job_tab.setLayout(job_tab_grid)

        # Generate sub-frames
        self.gen_job_tab_frame0()
        self.gen_job_tab_frame1()
        self.gen_job_tab_frame2()
        self.gen_job_tab_frame3()

        # Empty-state hint: the JOB tab does not load anything until the user
        # enters a job id and clicks "Check". Shown over the chart frame (the
        # tab's graphics area) so the filter row stays interactive; hidden in
        # check_job_on_job_tab().
        self.job_tab_hint_label = self._make_empty_hint(
            job_tab_grid, 0, 1, 2, 1, 'Enter a job id and click "Check" to load job information.'
        )

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
        job_tab_check_button.clicked.connect(self.check_job_on_job_tab)

        # "Kill" button.
        job_tab_kill_button = QPushButton('Kill', self.job_tab_frame0)
        job_tab_kill_button.clicked.connect(self.kill_job_on_job_tab)

        # "Trace" button.
        job_tab_trace_button = QPushButton('Trace', self.job_tab_frame0)
        job_tab_trace_button.clicked.connect(lambda: self.trace_job())

        # self.job_tab_frame0 - Grid
        job_tab_frame0_grid = QGridLayout()

        # Row 0: "Job" label + input (input stretches to fill remaining width).
        job_tab_frame0_grid.addWidget(job_tab_job_label, 0, 0)
        job_tab_frame0_grid.addWidget(self.job_tab_job_line, 0, 1)
        # Row 1: Check / Kill / Trace — three buttons equally share the width
        # (no trailing spacer, so they stretch uniformly across the row).
        from PyQt5.QtWidgets import QHBoxLayout
        job_tab_btn_row = QHBoxLayout()
        job_tab_btn_row.setSpacing(10)
        job_tab_btn_row.addWidget(job_tab_check_button, 1)
        job_tab_btn_row.addWidget(job_tab_kill_button, 1)
        job_tab_btn_row.addWidget(job_tab_trace_button, 1)
        job_tab_frame0_grid.addLayout(job_tab_btn_row, 1, 0, 1, 2)

        job_tab_frame0_grid.setColumnStretch(0, 0)
        job_tab_frame0_grid.setColumnStretch(1, 1)
        job_tab_frame0_grid.setHorizontalSpacing(10)
        job_tab_frame0_grid.setContentsMargins(8, 8, 8, 8)

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

        # Column 0 (labels) hugs content; column 1 (value fields) takes the rest,
        # so timestamps / long values are never clipped.
        job_tab_frame1_grid.setColumnStretch(0, 0)
        job_tab_frame1_grid.setColumnStretch(1, 1)
        job_tab_frame1_grid.setHorizontalSpacing(10)
        job_tab_frame1_grid.setContentsMargins(8, 8, 8, 8)

        self.job_tab_frame1.setLayout(job_tab_frame1_grid)

    def job_tab_host_click(self):
        """
        Jump to LOAD tab when clicking Host line on self.job_tab.
        """
        job_started_on = self.job_tab_started_on_line.text().strip()

        if job_started_on:
            # Multi-host job (e.g. "n019-028-054 n249-073-069"): LOAD tab is
            # single-host, so keep only the first host to avoid empty result.
            exec_host_list = common_lsf.get_exec_host_list(job_started_on)
            if exec_host_list:
                job_started_on = exec_host_list[0]

            # Build the LOAD tab lazily before touching its widgets.
            self._ensure_inner_tab_built(self.load_tab)

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
        # Name the wrapping card frame chartFrame so QSS can paint it
        # PRIMARY_LIGHT: the QTabWidget does not paint the tab-bar strip to
        # the right of the last tab, so that gap shows the frame behind it.
        # The chartTab QSS (light-blue widget background + white ::pane) then
        # gives a light-blue tab strip (matching the main JOB/JOBS bar) with a
        # white content card below — same as every other panel tab.
        self.job_tab_frame3.setObjectName('chartFrame')
        self.job_tab_chart_tab = QTabWidget(self.job_tab_frame3)
        self.job_tab_chart_tab.setObjectName('chartTab')
        # BoldAwareTabBar: size tabs to their bold width so MEMORY/IDLE_FACTOR
        # labels never clip when selected (QSS :selected had font-weight:600).
        self.job_tab_chart_tab.setTabBar(common_pyqt5.BoldAwareTabBar())

        # MEMORY tab
        job_tab_mem_widget = QWidget()
        self.job_tab_mem_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.job_tab_mem_toolbar = common_pyqt5.NavigationToolbar2QT(self.job_tab_mem_canvas, self, x_is_date=False)

        self._style_job_canvas(self.job_tab_mem_canvas)

        job_tab_mem_layout = QVBoxLayout()
        job_tab_mem_layout.setContentsMargins(0, 0, 0, 0)
        job_tab_mem_layout.addWidget(self.job_tab_mem_toolbar)
        job_tab_mem_layout.addWidget(self.job_tab_mem_canvas, 1)
        job_tab_mem_widget.setLayout(job_tab_mem_layout)

        # IDLE_FACTOR tab
        job_tab_idle_factor_widget = QWidget()
        self.job_tab_idle_factor_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.job_tab_idle_factor_toolbar = common_pyqt5.NavigationToolbar2QT(self.job_tab_idle_factor_canvas, self, x_is_date=False)

        self._style_job_canvas(self.job_tab_idle_factor_canvas)

        job_tab_idle_factor_layout = QVBoxLayout()
        job_tab_idle_factor_layout.setContentsMargins(0, 0, 0, 0)
        job_tab_idle_factor_layout.addWidget(self.job_tab_idle_factor_toolbar)
        job_tab_idle_factor_layout.addWidget(self.job_tab_idle_factor_canvas, 1)
        job_tab_idle_factor_widget.setLayout(job_tab_idle_factor_layout)

        # Add tabs
        self.job_tab_chart_tab.addTab(job_tab_mem_widget, 'MEMORY')
        self.job_tab_chart_tab.addTab(job_tab_idle_factor_widget, 'IDLE_FACTOR')

        # self.job_tab_frame3 - Grid
        job_tab_frame3_grid = QGridLayout()
        job_tab_frame3_grid.setContentsMargins(0, 0, 0, 0)
        job_tab_frame3_grid.addWidget(self.job_tab_chart_tab, 0, 0)
        self.job_tab_frame3.setLayout(job_tab_frame3_grid)

    def _style_job_canvas(self, canvas):
        """Apply figure background + size policy so MEMORY/IDLE_FACTOR charts
        are on a white background and fill the tab area."""
        from PyQt5.QtWidgets import QSizePolicy

        canvas.figure.set_facecolor(theme.CHART_COLORS[bool(self.dark_mode)]['figure_face'])

        canvas.setMinimumHeight(220)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def check_job_on_job_tab(self):
        """
        Get job information with "bjobs -UF <job_id>", save the infomation into dict self.job_tab_current_job_dic.
        Update self.job_tab_frame1 and self.job_tab_frame3.
        """
        # Hide the empty-state hint once the user triggers a load.
        if getattr(self, 'job_tab_hint_label', None) is not None:
            self.job_tab_hint_label.hide()

        # Initialization JOB tab.
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

        my_show_message = ShowMessage('Info', f'Getting LSF job information for "{current_job}" ...', persistent=True)
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
        my_show_message.terminate()
        my_show_message.wait(3000)
        my_show_message = ShowMessage('Info', 'Rendering job information ...', persistent=True)
        my_show_message.start()
        self.update_job_tab_frame1()
        self.update_job_tab_frame2()
        self.update_job_tab_frame3()

        time.sleep(0.01)
        my_show_message.terminate()
        my_show_message.wait(3000)

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
                my_show_message = ShowMessage('Info', f'Kill {jobid} successfully!', persistent=True)
                my_show_message.start()
                # Non-blocking auto-close: a singleShot timer terminates the
                # prompt after a short delay so the UI stays responsive (the old
                # time.sleep(5) blocked the main thread for the whole window).
                QTimer.singleShot(2500, my_show_message.terminate)
            else:
                common.bprint(f'Failed on killing {jobid}.', date_format='%Y-%m-%d %H:%M:%S')
                common.bprint(str(stderr, 'utf-8').strip(), date_format='%Y-%m-%d %H:%M:%S')
                my_show_message = ShowMessage(f'Kill {jobid} fail', str(str(stderr, 'utf-8')).strip(), persistent=True)
                my_show_message.start()
                # Persistent prompts never auto-close on their own — schedule
                # the terminate so a failure prompt can't become an orphan.
                QTimer.singleShot(5000, my_show_message.terminate)

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

        # Try new format (job_data/ single-table schema) first. range_size
        # changed from 1M to 100K; resolve_job_data_db tries 100K-shard then
        # falls back to legacy 1M-shard so old sampled data stays readable.
        job_data_db_file = common.resolve_job_data_db(str(self.cluster_db_path) + '/job_data', self.job_tab_current_job)

        if job_data_db_file:
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
                        runtime = int((current_time - first_sample_time) / 60)
                        runtime_list.append(runtime)
                        mem = mem_list[i]

                        if mem == '':
                            mem = '0'

                        real_mem = round(float(mem) / 1024, 1)
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

        # Try new format (job_data/ single-table schema) first. range_size
        # changed from 1M to 100K; resolve_job_data_db tries 100K-shard then
        # falls back to legacy 1M-shard so old sampled data stays readable.
        job_data_db_file = common.resolve_job_data_db(str(self.cluster_db_path) + '/job_data', self.job_tab_current_job)

        if job_data_db_file:
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
        axes = fig.add_subplot(111)

        axes.plot(runtime_list, mem_list, color=theme.CHART_MEM_COLOR, linewidth=theme.CHART_LINEWIDTH, label='MEM')
        axes.fill_between(runtime_list, mem_list, color=theme.CHART_MEM_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                title='memory usage for job "' + str(self.job_tab_current_job) + '"',
                                xlabel='Runtime (Minutes)',
                                ylabel='Memory Usage (G)')
        self.job_tab_mem_canvas.draw()

    def draw_job_tab_idle_factor_curve(self, fig, runtime_list, idle_factor_list):
        """
        Draw idle_factor curve for specified job.
        """
        axes = fig.add_subplot(111)

        axes.plot(runtime_list, idle_factor_list, color=theme.CHART_IDLE_COLOR, linewidth=theme.CHART_LINEWIDTH, label='IDLE_FACTOR')
        axes.fill_between(runtime_list, idle_factor_list, color=theme.CHART_IDLE_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title='IDLE_FACTOR(cputime/runtime) for job "' + str(self.job_tab_current_job) + '"',
                                 xlabel='Runtime (Minutes)',
                                 ylabel='IDLE_FACTOR')
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
        common_pyqt5.make_table_readonly(self.jobs_tab_table)
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

        # "Group" item (host group, same source as HOSTS tab).
        jobs_tab_group_label = QLabel('Group', self.jobs_tab_frame0)
        jobs_tab_group_label.setStyleSheet("font-weight: bold;")
        jobs_tab_group_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.jobs_tab_group_combo = common_pyqt5.QComboCheckBox(self.jobs_tab_frame0, enableFilter=True)
        self.set_jobs_tab_group_combo()

        # "Host" item.
        jobs_tab_started_on_label = QLabel('Host', self.jobs_tab_frame0)
        jobs_tab_started_on_label.setStyleSheet("font-weight: bold;")
        jobs_tab_started_on_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.jobs_tab_host_line = QLineEdit()
        self.jobs_tab_host_line.returnPressed.connect(self.gen_jobs_tab_table)
        self.set_jobs_tab_host_line_completer()

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
        jobs_tab_check_button.clicked.connect(self.gen_jobs_tab_table)

        # self.jobs_tab_frame0 - Grid
        jobs_tab_frame0_grid = QGridLayout()

        jobs_tab_frame0_grid.addWidget(jobs_tab_status_label, 0, 0)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_status_combo, 0, 1)
        jobs_tab_frame0_grid.addWidget(jobs_tab_queue_label, 0, 2)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_queue_combo, 0, 3)
        jobs_tab_frame0_grid.addWidget(jobs_tab_group_label, 0, 4)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_group_combo, 0, 5)
        jobs_tab_frame0_grid.addWidget(jobs_tab_started_on_label, 0, 6)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_host_line, 0, 7)
        jobs_tab_frame0_grid.addWidget(jobs_tab_user_label, 0, 8)
        jobs_tab_frame0_grid.addWidget(self.jobs_tab_user_line, 0, 9)
        jobs_tab_frame0_grid.addWidget(jobs_tab_check_button, 0, 10)

        jobs_tab_frame0_grid.setColumnStretch(0, 1)
        jobs_tab_frame0_grid.setColumnStretch(1, 1)
        jobs_tab_frame0_grid.setColumnStretch(2, 1)
        jobs_tab_frame0_grid.setColumnStretch(3, 1)
        jobs_tab_frame0_grid.setColumnStretch(4, 1)
        jobs_tab_frame0_grid.setColumnStretch(5, 1)
        jobs_tab_frame0_grid.setColumnStretch(6, 1)
        jobs_tab_frame0_grid.setColumnStretch(7, 2)
        jobs_tab_frame0_grid.setColumnStretch(8, 1)
        jobs_tab_frame0_grid.setColumnStretch(9, 2)
        jobs_tab_frame0_grid.setColumnStretch(10, 1)

        self.jobs_tab_frame0.setLayout(jobs_tab_frame0_grid)

    def gen_jobs_tab_table(self):
        # self.jobs_tab_table
        self.jobs_tab_table.setShowGrid(True)
        self.jobs_tab_table.setSortingEnabled(False)
        self.jobs_tab_table.setColumnCount(0)
        self.jobs_tab_table.setColumnCount(12)
        self.jobs_tab_table_title_list = ['Job', 'User', 'Status', 'Queue', 'Host', 'Started', 'Slot', 'IDLE', 'Rusage (G)', 'Mem (G)', 'MaxMem (G)', 'Command']
        self.jobs_tab_table.setHorizontalHeaderLabels(self.jobs_tab_table_title_list)

        # Column widths (px). Sized to show typical content without clipping:
        #   Job      — 8 digits max ≈ 85px; kept Interactive so users can resize.
        #   User     — account names like "liyanqing.1987" (up to ~15 chars) ≈ 125px.
        #   Started  — "YYYY-MM-DD HH:MM:SS" (19 chars) ≈ 185px.
        #   Command  — stretch, takes remaining width.
        self.jobs_tab_table.setColumnWidth(0, 100)
        self.jobs_tab_table.setColumnWidth(1, 125)
        self.jobs_tab_table.setColumnWidth(2, 65)
        self.jobs_tab_table.setColumnWidth(3, 110)
        self.jobs_tab_table.setColumnWidth(4, 130)
        self.jobs_tab_table.setColumnWidth(5, 165)
        self.jobs_tab_table.setColumnWidth(6, 30)    # Slot
        self.jobs_tab_table.setColumnWidth(7, 30)    # IDLE
        self.jobs_tab_table.setColumnWidth(8, 30)    # Rusage (G)
        self.jobs_tab_table.setColumnWidth(9, 30)    # Mem (G)
        self.jobs_tab_table.setColumnWidth(10, 30)   # MaxMem (G)
        # Default Interactive so setColumnWidth() is honoured (ResizeToContents
        # would re-grow Job to fit the widest JOBID and ignore our 85px cap).
        self.jobs_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.jobs_tab_table.horizontalHeader().setSectionResizeMode(11, QHeaderView.Stretch)
        # auto_size: ensure every column is at least wide enough for its header label.
        common_pyqt5.auto_size_table_columns(self.jobs_tab_table)

        # Get specified user related jobs.
        # bjobs -u does not support multiple users, so resolve keywords against
        # the known user list (busers): exact usernames query bjobs -u <user>
        # directly (fast); a fuzzy keyword that matches too many users falls back
        # to bjobs -u all + Python filter (slow but correct).
        specified_user_list = self.jobs_tab_user_line.text().strip().split()
        self.fresh_lsf_info('busers')

        all_user_list = self.busers_dic.get('USER/GROUP', []) if 'USER/GROUP' in self.busers_dic else []
        resolved_user_list = []
        use_fallback_user_filter = False

        for specified_user in specified_user_list:
            if specified_user in all_user_list:
                # Exact username — query it directly.
                if specified_user not in resolved_user_list:
                    resolved_user_list.append(specified_user)
            else:
                # Fuzzy keyword — expand to matching usernames.
                matched_user_list = [user for user in all_user_list if specified_user.lower() in user.lower()]

                if len(matched_user_list) > 50:
                    # Too broad (e.g. "li" matches hundreds) — fall back to -u all.
                    use_fallback_user_filter = True
                    break

                for user in matched_user_list:
                    if user not in resolved_user_list:
                        resolved_user_list.append(user)

        # Build the base command (without -u; added per-path below).
        # need_python_filter covers BOTH slow cases: a fuzzy keyword that
        # matched too many users (>50, -u all + filter), AND a keyword that
        # matched nobody (resolved_user_list empty but specified_user_list
        # non-empty — must filter so the empty match yields an empty result
        # instead of -u all returning every job in the cluster).
        need_python_filter = use_fallback_user_filter or (bool(specified_user_list) and not resolved_user_list)

        if need_python_filter:
            # Slow path: fetch all users, filter in Python below.
            command = 'bjobs -UF -u all'
        elif resolved_user_list:
            # Fast path: fetch each resolved user (no -u in base command; queried per user).
            command = 'bjobs -UF'
        else:
            # No user filter.
            command = 'bjobs -UF -u all'

        # Get specified queue related jobs.
        specified_queue_list = self.jobs_tab_queue_combo.currentText().strip().split()

        if (len(specified_queue_list) == 1) and (specified_queue_list[0] != 'ALL'):
            command = str(command) + ' -q ' + str(specified_queue_list[0])

        # Get specified host group related jobs (filtered client-side below).
        specified_group_list = self.jobs_tab_group_combo.currentText().strip().split()

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
        specified_host_list = self.jobs_tab_host_line.text().strip().split()

        if (len(specified_host_list) == 1) and (specified_host_list[0] != 'ALL'):
            self.fresh_lsf_info('bhosts')
            all_host_list = self.bhosts_dic.get('HOST_NAME', []) if 'HOST_NAME' in self.bhosts_dic else []

            if specified_host_list[0] in all_host_list:
                command = str(command) + ' -m ' + str(specified_host_list[0])

        # Run command to get expected jobs information.
        common.bprint('Loading LSF jobs information ...', date_format='%Y-%m-%d %H:%M:%S')

        # persistent=True (--no-autoclose): the message window must stay up
        # until terminate() at the end of this method, not auto-close after 5s.
        # The full pipeline (bjobs fetch + filter + fill table + resize) often
        # exceeds the default 5s autoclose — without this the prompt disappears
        # mid-work and the UI looks frozen while still busy.
        my_show_message = ShowMessage('Info', 'Loading LSF jobs information ...', persistent=True)
        my_show_message.start()

        if resolved_user_list and not use_fallback_user_filter:
            # Fast path: query each resolved user, merge into one job_dic.
            job_dic = {}

            for resolved_user in resolved_user_list:
                per_user_command = str(command) + ' -u ' + str(resolved_user)
                per_user_job_dic = common_lsf.get_bjobs_uf_info(per_user_command)
                job_dic.update(per_user_job_dic)
        else:
            job_dic = common_lsf.get_bjobs_uf_info(command)

        time.sleep(0.01)
        my_show_message.terminate()
        my_show_message.wait(3000)
        # Same persistent flag: rendering a large jobs table can take a while,
        # keep the prompt visible until the final terminate() at method end.
        my_show_message = ShowMessage('Info', 'Rendering jobs table ...', persistent=True)
        my_show_message.start()

        # Filter job_dic.
        job_list = list(job_dic.keys())

        # On the slow -u all path, resolve specified_user_list once:
        # exact-preferred (exact match wins; fuzzy only when no exact match).
        # matched_user_list is [] when no keyword matched any job user, which
        # (combined with the filter below) correctly yields an empty result.
        matched_user_list = []

        if need_python_filter and specified_user_list:
            all_job_user_list = list({job_dic[job]['user'] for job in job_dic.keys()})
            matched_user_list = common.match_exact_or_fuzzy(specified_user_list, all_job_user_list)

        matched_host_list = []

        if specified_host_list and ('ALL' not in specified_host_list):
            self.fresh_lsf_info('bhosts')
            all_host_list = self.bhosts_dic.get('HOST_NAME', []) if 'HOST_NAME' in self.bhosts_dic else []
            matched_host_list = common.match_exact_or_fuzzy(specified_host_list, all_host_list)

        # Ensure host_group info is fresh for the Group filter below.
        self.fresh_lsf_info('host_group')

        for job in job_list:
            # Filter with specified_user_list on the slow -u all path (the fast
            # path already queried each user's jobs directly). matched_user_list
            # is empty when nothing matched → every job is dropped → empty result.
            if need_python_filter and specified_user_list and (job_dic[job]['user'] not in matched_user_list):
                del job_dic[job]
                continue

            if ('ALL' not in specified_status_list) and (job_dic[job]['status'] not in specified_status_list):
                del job_dic[job]
                continue

            if ('ALL' not in specified_queue_list) and (len(specified_queue_list) >= 1) and (job_dic[job]['queue'] not in specified_queue_list):
                del job_dic[job]
                continue

            if specified_host_list and ('ALL' not in specified_host_list):
                started_on_list = common_lsf.get_exec_host_list(job_dic[job]['started_on'])
                find_host = any(host in matched_host_list for host in started_on_list)

                if not find_host:
                    del job_dic[job]
                    continue

            # Filter with specified_group_list: keep the job if any of its
            # execution hosts belongs to a selected host group.
            if ('ALL' not in specified_group_list) and (len(specified_group_list) >= 1):
                find_group = False
                started_on_list = job_dic[job]['started_on'].strip().split()

                for started_host in started_on_list:
                    if started_host in self.host_group_dic:
                        for specified_group in specified_group_list:
                            if specified_group in self.host_group_dic[started_host]:
                                find_group = True
                                break

                    if find_group:
                        break

                if not find_group:
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
            j = j + 1
            item = QTableWidgetItem(job_dic[job]['user'])
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Status" item.
            j = j + 1
            item = QTableWidgetItem(job_dic[job]['status'])
            item.setFont(QFont('song', 9, QFont.Bold))

            status = job_dic[job]['status']

            if status == 'RUN':
                item.setForeground(QBrush(QColor(STATUS_RUN)))
            elif status in ('PEND', 'PSUSP', 'USUSP', 'SSUSP', 'WAIT', 'PROV'):
                item.setForeground(QBrush(QColor(STATUS_PEND)))
            elif status == 'DONE':
                item.setForeground(QBrush(QColor(STATUS_DONE)))
            elif status in ('EXIT', 'UNKWN', 'ZOMBI'):
                item.setForeground(QBrush(QColor(STATUS_EXIT)))

            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Queue" item.
            j = j + 1
            item = QTableWidgetItem(job_dic[job]['queue'])
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Host" item.
            j = j + 1
            item = QTableWidgetItem(job_dic[job]['started_on'])
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Started" item.
            j = j + 1
            start_time = common_lsf.switch_bjobs_uf_time(job_dic[job]['started_time'], '%Y-%m-%d %H:%M:%S')
            item = QTableWidgetItem(start_time)
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Slot" item.
            j = j + 1

            if str(job_dic[job]['processors_requested']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, int(job_dic[job]['processors_requested']))
                self.jobs_tab_table.setItem(i, j, item)

            # Fill "IDLE" item.
            j = j + 1
            idle_value = ''

            if str(job_dic[job]['idle_factor']) != '':
                idle_value = round(float(job_dic[job]['idle_factor']), 2)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, idle_value)
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Rusage" item.
            j = j + 1
            rusage_mem_value = 0

            if str(job_dic[job]['rusage_mem']) != '':
                item = QTableWidgetItem()
                rusage_mem_value = round(float(job_dic[job]['rusage_mem']) / 1024, 1)
                item.setData(Qt.DisplayRole, rusage_mem_value)
                self.jobs_tab_table.setItem(i, j, item)

            # Fill "Mem" item.
            j = j + 1
            mem_value = ''

            if (job_dic[job]['status'] != 'DONE') and (job_dic[job]['status'] != 'EXIT'):
                if str(job_dic[job]['mem']) != '':
                    mem_value = round(float(job_dic[job]['mem']) / 1024, 1)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, mem_value)
            self.jobs_tab_table.setItem(i, j, item)

            if mem_value and (((not job_dic[job]['rusage_mem']) and (mem_value > 0)) or (job_dic[job]['rusage_mem'] and (mem_value > rusage_mem_value))):
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            # Fill "MaxMem" item.
            j = j + 1
            max_mem_value = ''

            if str(job_dic[job]['max_mem']) != '':
                max_mem_value = round(float(job_dic[job]['max_mem']) / 1024, 1)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, max_mem_value)
            self.jobs_tab_table.setItem(i, j, item)

            # Fill "Command" item.
            j = j + 1
            item = QTableWidgetItem(job_dic[job]['command'])
            self.jobs_tab_table.setItem(i, j, item)

        self.jobs_tab_table.setSortingEnabled(True)
        # Job 列按内容自适应(resizeColumnToContents 保证最长 jobid 完整显示、
        # 不出现省略号),但短 jobid(如 6 位)算出的内容宽度会小于构造时设的
        # 100px,把列缩得过窄;取内容宽度与 100px 的较大值作为下限兜底。
        self.jobs_tab_table.resizeColumnToContents(0)

        if self.jobs_tab_table.columnWidth(0) < 100:
            self.jobs_tab_table.setColumnWidth(0, 100)

        my_show_message.terminate()
        my_show_message.wait(3000)

    def jobs_tab_check_click(self, item=None):
        """
        If click the Job id, jump to the JOB tab and show the job information.
        If click the "PEND" Status, show the job pend reasons on a QMessageBox.information().
        If click the Host, jump to the HOSTS tab and filter by the first host.
        """
        if item is not None:
            current_row = self.jobs_tab_table.currentRow()
            job = self.jobs_tab_table.item(current_row, 0).text().strip()

            if item.column() == 0:
                if job != '':
                    self._ensure_inner_tab_built(self.job_tab)
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
            elif item.column() == 4:
                started_on = self.jobs_tab_table.item(current_row, 4).text().strip()

                if started_on:
                    exec_host_list = common_lsf.get_exec_host_list(started_on)
                    if exec_host_list:
                        # Pass all exec hosts (space-separated) so HOSTS filters to all of them.
                        self._ensure_inner_tab_built(self.hosts_tab)
                        self.hosts_tab_host_line.setText(' '.join(exec_host_list))
                        self.gen_hosts_tab_table()
                        self.main_tab.setCurrentWidget(self.hosts_tab)

    def gen_jobs_tab_menu(self, pos):
        """
        Generate right click menu on self.jobs_tab_table.
        """
        item = self.jobs_tab_table.itemAt(pos)

        # Rusage (G) 列号按标题名取,避免列顺序调整后索引漂移导致菜单错位。
        rusage_column = self.jobs_tab_table_title_list.index('Rusage (G)')

        if item and (item.column() == rusage_column):
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

        rusage_column = self.jobs_tab_table_title_list.index('Rusage (G)')
        rusage_item = self.jobs_tab_table.item(current_row, rusage_column)
        current_rusage_gb = rusage_item.text().strip() if rusage_item else ''
        current_rusage_mb = 0

        if current_rusage_gb and re.match(r'^\d+\.?\d*$', current_rusage_gb):
            current_rusage_mb = int(float(current_rusage_gb) * 1024)

        # Get host information to determine max rusage limit
        host_column = self.jobs_tab_table_title_list.index('Host')
        host_item = self.jobs_tab_table.item(current_row, host_column)
        host = host_item.text().strip() if host_item else ''

        # Multi-host jobs: take the first exec host for the memory ceiling check.
        if host:
            exec_host_list = common_lsf.get_exec_host_list(host)

            if exec_host_list:
                host = exec_host_list[0]

        max_mem_mb = 0
        sa_mem_mb = 0
        sa_mem_available = False
        host_info = ''

        if host:
            self.fresh_lsf_info('lshosts')
            self.fresh_lsf_info('bhosts_load')

            # Max Mem (physical) from lshosts — hard ceiling.
            if ('HOST_NAME' in self.lshosts_dic) and (host in self.lshosts_dic['HOST_NAME']):
                host_index = self.lshosts_dic['HOST_NAME'].index(host)
                maxmem = self.lshosts_dic['maxmem'][host_index]

                if maxmem not in ('', '-'):
                    max_mem_mb = int(self.mem_unit_switch(maxmem) * 1024)

            # saMem (available) from bhosts_load — for the over-reservation warning.
            # sa_mem_available marks "data fetched" so a real 0 still triggers the warning.
            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('mem' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['mem'] != '-'):
                sa_mem_mb = int(self.mem_unit_switch(self.bhosts_load_dic[host]['Total']['mem']) * 1024)
                sa_mem_available = True

            if max_mem_mb:
                host_info = f'\nHost: {host}\nMax Mem: {max_mem_mb} MB\nAvailable Memory: {sa_mem_mb} MB'
            else:
                host_info = f'\nHost: {host}\nAvailable Memory: {sa_mem_mb} MB\n*Warning*: Cannot get host Max Mem, hard ceiling not enforced. Set rusage cautiously.'

        new_rusage_mb, ok = QInputDialog.getInt(
            self,
            f'Modify Rusage Mem - Job {job}',
            f'Current Rusage: {current_rusage_mb} MB\nEnter new Rusage (MB):{host_info}',
            current_rusage_mb,
            0,
            2147483647,
            1
        )

        if not ok:
            return

        # Hard limit: reject values above the host's physical Max Mem.
        if max_mem_mb and (new_rusage_mb > max_mem_mb):
            QMessageBox.warning(
                self,
                'Exceeds Max Mem',
                f'New rusage ({new_rusage_mb} MB) exceeds host Max Mem ({max_mem_mb} MB). Cannot set.'
            )

            return

        # Warn if over-reserving: accepted, but takes full effect after host frees memory.
        if sa_mem_available and (new_rusage_mb > current_rusage_mb + sa_mem_mb):
            reply = QMessageBox.warning(
                self,
                'Over-reservation',
                f'New rusage ({new_rusage_mb} MB) exceeds host available memory '
                f'({current_rusage_mb + sa_mem_mb} MB = current {current_rusage_mb} + available {sa_mem_mb}).\n\n'
                f'The reservation will be accepted, but exceeds current free resources '
                f'and takes full effect only after the host frees up memory.\n'
                f'Proceed anyway?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

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

        requested_resource = job_dic[job].get('requested_resources', '')

        if not requested_resource:
            common.bprint(f'requested_resources is empty for job {job}.', date_format='%Y-%m-%d %H:%M:%S', level='Error')
            QMessageBox.warning(self, 'Error', 'Could not find requested resources in job info.')
            return

        # Modify rusage[mem=...] value
        # Use more precise regex to match rusage[mem=...]
        new_requested_resource = re.sub(
            r'rusage\s*\[\s*mem\s*=\s*\d+(\.\d+)?\s*\]',
            f'rusage[mem={new_rusage_mb}]',
            requested_resource
        )

        # If rusage[mem=...] not found, add it
        if new_requested_resource == requested_resource:
            # Check if rusage exists but without mem
            if 'rusage[' in requested_resource:
                QMessageBox.warning(self, 'Error', 'Found rusage but could not find mem parameter. Please check job resources.')
                return
            else:
                # Add rusage[mem=...] to the end
                new_requested_resource = f'{requested_resource} rusage[mem={new_rusage_mb}]'

        # Apply modification with bmod.
        # shlex.quote the resource string and job id so a resource string
        # containing shell metacharacters (sourced from LSF bjobs -UF output)
        # cannot inject shell commands under run_command's shell=True.
        bmod_command = f'bmod -R {shlex.quote(new_requested_resource)} {shlex.quote(str(job))}'
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
                    self.jobs_tab_table.setItem(row, 8, item)
                    break
        else:
            error_msg = stderr.decode('utf-8').strip()
            common.bprint(f'Failed to modify rusage: {error_msg}', date_format='%Y-%m-%d %H:%M:%S', level='Error')
            QMessageBox.warning(self, 'Error', f'Failed to modify rusage: {error_msg}')

    def set_jobs_tab_status_combo(self, checked_status_list=None):
        """
        Set (initialize) self.jobs_tab_status_combo.
        """
        if checked_status_list is None:
            checked_status_list = ['RUN']

        self.jobs_tab_status_combo.clear()

        status_list = ['ALL', 'RUN', 'PEND', 'DONE', 'EXIT', 'PSUSP', 'USUSP', 'SSUSP', 'UNKWN', 'WAIT', 'ZOMBI', 'PROV']

        for status in status_list:
            self.jobs_tab_status_combo.addCheckBoxItem(status)

        # Set to checked status for checked_status_list.
        for (i, qBox) in enumerate(self.jobs_tab_status_combo.checkBoxList):
            if (qBox.text() in checked_status_list) and (qBox.isChecked() is False):
                self.jobs_tab_status_combo.checkBoxList[i].setChecked(True)

    def set_jobs_tab_queue_combo(self, checked_queue_list=None):
        """
        Set (initialize) self.jobs_tab_queue_combo.
        """
        if checked_queue_list is None:
            checked_queue_list = ['ALL']
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

    def set_jobs_tab_group_combo(self, checked_group_list=None):
        """Set (initialize) self.jobs_tab_group_combo from LSF host groups.

        Mirrors set_hosts_tab_group_combo: collects group names from
        host_group_dic (host -> [groups]).
        """
        if checked_group_list is None:
            checked_group_list = ['ALL']
        self.jobs_tab_group_combo.clear()
        self.fresh_lsf_info('host_group')

        group_set = set()

        for groups in self.host_group_dic.values():
            for group in groups:
                group_set.add(group)

        group_list = sorted(group_set)
        group_list.insert(0, 'ALL')

        for group in group_list:
            self.jobs_tab_group_combo.addCheckBoxItem(group)

        # Set to checked status for checked_group_list.
        for (i, qBox) in enumerate(self.jobs_tab_group_combo.checkBoxList):
            if (qBox.text() in checked_group_list) and (qBox.isChecked() is False):
                self.jobs_tab_group_combo.checkBoxList[i].setChecked(True)

    def set_jobs_tab_host_line_completer(self):
        """Refresh the Host line's completer candidates from bhosts."""
        self.fresh_lsf_info('bhosts')

        if 'HOST_NAME' in self.bhosts_dic:
            host_list = copy.deepcopy(self.bhosts_dic['HOST_NAME'])
        else:
            host_list = []

        self.jobs_tab_host_line.setCompleter(common_pyqt5.get_completer(host_list))

    def set_jobs_tab_host_line(self, host_list=None):
        """Pre-fill the Host line (space-separated). None/[] → empty (no filter)."""
        if host_list and host_list != ['ALL']:
            self.jobs_tab_host_line.setText(' '.join(host_list))
        else:
            self.jobs_tab_host_line.setText('')

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
        common_pyqt5.make_table_readonly(self.hosts_tab_table)
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

        # "Group" item.
        hosts_tab_group_label = QLabel('Group', self.hosts_tab_frame0)
        hosts_tab_group_label.setStyleSheet("font-weight: bold;")
        hosts_tab_group_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.hosts_tab_group_combo = common_pyqt5.QComboCheckBox(self.hosts_tab_frame0, enableFilter=True)
        self.set_hosts_tab_group_combo()

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
        hosts_tab_check_button.clicked.connect(self.gen_hosts_tab_table)

        # self.hosts_tab_frame0 - Grid
        hosts_tab_frame0_grid = QGridLayout()

        hosts_tab_frame0_grid.addWidget(hosts_tab_status_label, 0, 0)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_status_combo, 0, 1)
        hosts_tab_frame0_grid.addWidget(hosts_tab_queue_label, 0, 2)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_queue_combo, 0, 3)
        hosts_tab_frame0_grid.addWidget(hosts_tab_group_label, 0, 4)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_group_combo, 0, 5)
        hosts_tab_frame0_grid.addWidget(hosts_tab_max_label, 0, 6)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_max_combo, 0, 7)
        hosts_tab_frame0_grid.addWidget(hosts_tab_maxmem_label, 0, 8)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_maxmem_combo, 0, 9)
        hosts_tab_frame0_grid.addWidget(hosts_tab_host_label, 0, 10)
        hosts_tab_frame0_grid.addWidget(self.hosts_tab_host_line, 0, 11)
        hosts_tab_frame0_grid.addWidget(hosts_tab_check_button, 0, 12)

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
        hosts_tab_frame0_grid.setColumnStretch(11, 2)
        hosts_tab_frame0_grid.setColumnStretch(12, 1)

        self.hosts_tab_frame0.setLayout(hosts_tab_frame0_grid)

    def gen_hosts_tab_table(self):
        # self.hosts_tab_table
        self.hosts_tab_table.setShowGrid(True)
        self.hosts_tab_table.setSortingEnabled(False)
        self.hosts_tab_table.setColumnCount(0)
        self.hosts_tab_table.setColumnCount(13)
        self.hosts_tab_table_title_list = ['Host', 'Status', 'Queue', 'Group', 'MAX', 'Njobs', 'Ut (%)', 'MaxMem (G)', 'aMem (G)', 'saMem (G)', 'MaxSwp (G)', 'Swp (G)', 'Tmp (G)']
        self.hosts_tab_table.setHorizontalHeaderLabels(self.hosts_tab_table_title_list)

        self.hosts_tab_table.setColumnWidth(0, 150)
        # "closed_Busy" (11 chars) + bold header "Status" + padding ≈ 110px.
        self.hosts_tab_table.setColumnWidth(1, 110)
        # Queue 与 Group 均分剩余空间(Queue 内容通常更多,但 Group 列也需自适应)。
        self.hosts_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.hosts_tab_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.hosts_tab_table.setColumnWidth(4, 60)
        self.hosts_tab_table.setColumnWidth(5, 60)
        self.hosts_tab_table.setColumnWidth(6, 60)
        self.hosts_tab_table.setColumnWidth(7, 100)
        self.hosts_tab_table.setColumnWidth(8, 85)
        self.hosts_tab_table.setColumnWidth(9, 90)
        self.hosts_tab_table.setColumnWidth(10, 100)
        self.hosts_tab_table.setColumnWidth(11, 75)
        self.hosts_tab_table.setColumnWidth(12, 75)
        common_pyqt5.auto_size_table_columns(self.hosts_tab_table)

        # Fill self.hosts_tab_table items.
        hosts_tab_specified_host_list = self.get_hosts_tab_specified_host_list()
        self.hosts_tab_table.setRowCount(0)
        self.hosts_tab_table.setRowCount(len(hosts_tab_specified_host_list))

        # Fresh LSF bhosts/lsload/lshosts/host_queue/host_group/bhosts_load information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('lsload')
        self.fresh_lsf_info('lshosts')
        self.fresh_lsf_info('host_queue')
        self.fresh_lsf_info('host_group')
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
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Status" item.
            j = j + 1
            index = self.bhosts_dic['HOST_NAME'].index(host)
            status = self.bhosts_dic['STATUS'][index]
            item = QTableWidgetItem(status)

            if str(status) == 'ok':
                item.setForeground(QBrush(QColor(STATUS_RUN)))
            else:
                if (str(status) == 'unavail') or (str(status) == 'unreach') or (str(status) == 'closed_LIM'):
                    fatal_error = True
                    item.setForeground(QBrush(QColor(STATUS_EXIT)))
                else:
                    item.setForeground(QBrush(Qt.magenta))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Queue" item.
            j = j + 1
            queues = ''

            if host in self.host_queue_dic.keys():
                queues = ' '.join(self.host_queue_dic[host])

            item = QTableWidgetItem(queues)

            if fatal_error:
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Group" item.
            j = j + 1
            groups = ''

            if host in self.host_group_dic.keys():
                groups = ' '.join(self.host_group_dic[host])

            item = QTableWidgetItem(groups)

            if fatal_error:
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "MAX" item.
            j = j + 1
            index = self.bhosts_dic['HOST_NAME'].index(host)
            max = self.bhosts_dic['MAX'][index]

            if not re.match(r'^[0-9]+$', max):
                common.bprint(f'Host({host}) MAX info "{max}": invalid value, reset it to "0".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                max = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(max))

            if fatal_error:
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Njobs" item.
            j = j + 1
            index = self.bhosts_dic['HOST_NAME'].index(host)
            njobs = self.bhosts_dic['NJOBS'][index]

            if not re.match(r'^[0-9]+$', njobs):
                common.bprint(f'Host({host}) NJOBS info "{njobs}": invalid value, reset it to "0".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                njobs = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(njobs))
            item.setFont(QFont('song', 9, QFont.Bold))

            if fatal_error:
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Ut" item.
            j = j + 1
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
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "MaxMem" item with unit "GB".
            j = j + 1
            maxmem = '0'

            if host in self.lshosts_dic['HOST_NAME']:
                index = self.lshosts_dic['HOST_NAME'].index(host)
                maxmem = self.lshosts_dic['maxmem'][index]

            maxmem = int(self.mem_unit_switch(maxmem))
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, maxmem)

            if fatal_error or (maxmem == 0):
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "aMem" item with unit "GB".
            # "aMem" means avaliable mem, it is from "lsload -l" command, same with "free -g" result.
            j = j + 1

            if ('HOST_NAME' in self.lsload_dic) and (host in self.lsload_dic['HOST_NAME']):
                index = self.lsload_dic['HOST_NAME'].index(host)
                mem = self.lsload_dic['mem'][index]
                mem = int(self.mem_unit_switch(mem))
            else:
                mem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, mem)

            if fatal_error or (maxmem and (float(mem) / float(maxmem) < 0.1)):
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "saMem" item with unit "GB".
            # "saMem" means scheduling avaliable mem, it is from "bhosts -l" command.
            j = j + 1

            if (host in self.bhosts_load_dic) and ('Total' in self.bhosts_load_dic[host]) and ('mem' in self.bhosts_load_dic[host]['Total']) and (self.bhosts_load_dic[host]['Total']['mem'] != '-'):
                mem = self.bhosts_load_dic[host]['Total']['mem']
                mem = int(self.mem_unit_switch(mem))
            else:
                mem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, mem)

            if fatal_error or (maxmem and (float(mem) / float(maxmem) < 0.1)):
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "MaxSwp" item with unit "GB".
            j = j + 1
            maxswp = '0'

            if host in self.lshosts_dic['HOST_NAME']:
                index = self.lshosts_dic['HOST_NAME'].index(host)
                maxswp = self.lshosts_dic['maxswp'][index]

            maxswp = int(self.mem_unit_switch(maxswp))
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, maxswp)

            if fatal_error:
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Swp" item with unit "GB".
            j = j + 1
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
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

            self.hosts_tab_table.setItem(i, j, item)

            # Fill "Tmp" item with unit "GB".
            j = j + 1
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
                item.setBackground(QBrush(QColor(STATUS_EXIT)))

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
                    my_show_message = ShowMessage('Info', f'{behavior} {host_name} successfully!', persistent=True)
                    my_show_message.start()
                    # Refresh the hosts table while the success prompt stays up;
                    # the persistent prompt is closed by a timer afterward so the
                    # UI never blocks (the old time.sleep(5) froze the window).
                    self.gen_hosts_tab_table()
                    QTimer.singleShot(2500, my_show_message.terminate)
                else:
                    common.bprint(f'Failed on {behavior}ing host "{host_name}".', date_format='%Y-%m-%d %H:%M:%S')
                    common.bprint(str(stderr, 'utf-8').strip(), date_format='%Y-%m-%d %H:%M:%S')
                    my_show_message = ShowMessage(f'{behavior} {host_name} fail', str(str(stderr, 'utf-8')).strip(), persistent=True)
                    my_show_message.start()
                    # Persistent prompts never auto-close on their own — schedule
                    # the terminate so a failure prompt can't become an orphan.
                    QTimer.singleShot(5000, my_show_message.terminate)

    def get_hosts_tab_specified_host_list(self):
        """
        Filter host list with specified queue/status/max/maxmem/host.
        """
        specified_status_list = self.hosts_tab_status_combo.currentText().strip().split()
        specified_queue_list = self.hosts_tab_queue_combo.currentText().strip().split()
        specified_group_list = self.hosts_tab_group_combo.currentText().strip().split()
        specified_max_list = self.hosts_tab_max_combo.currentText().strip().split()
        specified_maxmem_list = self.hosts_tab_maxmem_combo.currentText().strip().split()
        specified_host_list = self.hosts_tab_host_line.text().strip().split()
        hosts_tab_specified_host_list = []

        # Fresh LSF bhosts/lshosts/host_queue/host_group information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('lshosts')
        self.fresh_lsf_info('host_queue')
        self.fresh_lsf_info('host_group')

        if 'HOST_NAME' in self.bhosts_dic:
            # Resolve specified_host_list once: exact-preferred (exact match wins; fuzzy only when no exact match).
            matched_host_list = common.match_exact_or_fuzzy(specified_host_list, self.bhosts_dic['HOST_NAME']) if specified_host_list else []

            for index, host in enumerate(self.bhosts_dic['HOST_NAME']):
                # Filter with specified_status_list.
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

                # Filter with specified_group_list(与 Queue 取交集)。
                if 'ALL' not in specified_group_list:
                    continue_mark = True

                    for specified_group in specified_group_list:
                        if (host in self.host_group_dic) and (specified_group in self.host_group_dic[host]):
                            continue_mark = False
                            break

                    if continue_mark:
                        continue

                # Filter with specified_max_list.
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

                # Filter with specified_host (exact-preferred: exact match wins; fuzzy only when no exact match).
                if specified_host_list and host not in matched_host_list:
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
            njobs_num = self.hosts_tab_table.item(current_row, 5).text().strip()

            if item.column() == 0:
                self._ensure_inner_tab_built(self.load_tab)
                self.load_tab_host_line.setText(host)
                self.update_load_tab_load_info()
                self.main_tab.setCurrentWidget(self.load_tab)
            elif item.column() == 5:
                if int(njobs_num) > 0:
                    self._ensure_inner_tab_built(self.jobs_tab)
                    self.set_jobs_tab_status_combo()
                    self.set_jobs_tab_queue_combo()
                    self.set_jobs_tab_host_line(host_list=[host, ])
                    self.jobs_tab_user_line.setText('')
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)

    def set_hosts_tab_status_combo(self, checked_status_list=None):
        """
        Set (initialize) self.hosts_tab_status_combo.
        """
        if checked_status_list is None:
            checked_status_list = ['ALL']
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

    def set_hosts_tab_queue_combo(self, checked_queue_list=None):
        """
        Set (initialize) self.hosts_tab_queue_combo.
        """
        if checked_queue_list is None:
            checked_queue_list = ['ALL']
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

    def set_hosts_tab_group_combo(self, checked_group_list=None):
        """Set (initialize) self.hosts_tab_group_combo."""
        if checked_group_list is None:
            checked_group_list = ['ALL']
        self.hosts_tab_group_combo.clear()
        self.fresh_lsf_info('host_group')

        # 从 host_group_dic(host->[groups])收集所有 group 名。
        group_set = set()

        for groups in self.host_group_dic.values():
            for group in groups:
                group_set.add(group)

        group_list = sorted(group_set)
        group_list.insert(0, 'ALL')

        for group in group_list:
            self.hosts_tab_group_combo.addCheckBoxItem(group)

        # Set to checked status for checked_group_list.
        for (i, qBox) in enumerate(self.hosts_tab_group_combo.checkBoxList):
            if (qBox.text() in checked_group_list) and (qBox.isChecked() is False):
                self.hosts_tab_group_combo.checkBoxList[i].setChecked(True)

    def set_hosts_tab_max_combo(self, checked_max_list=None):
        """
        Set (initialize) self.hosts_tab_max_combo.
        """
        if checked_max_list is None:
            checked_max_list = ['ALL']
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

    def set_hosts_tab_maxmem_combo(self, checked_maxmem_list=None):
        """
        Set (initialize) self.hosts_tab_maxmem_combo.
        """
        if checked_maxmem_list is None:
            checked_maxmem_list = ['ALL']
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
            if maxmem == 0:
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

        # Empty-state hint: the LOAD tab needs a host + "Check" before drawing.
        # Shown over the ut canvas frame (the tab's graphics area) so the filter
        # row stays interactive; hidden once a load is triggered.
        self.load_tab_hint_label = self._make_empty_hint(
            load_tab_grid, 1, 0, 1, 1, 'Enter a host and click "Check" to load ut/mem load charts.'
        )

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
        load_tab_check_button.clicked.connect(self.update_load_tab_load_info)

        # self.load_tab_frame0 - Grid
        load_tab_frame0_grid = QGridLayout()

        load_tab_frame0_grid.addWidget(load_tab_begin_date_label, 0, 0)
        load_tab_frame0_grid.addWidget(self.load_tab_begin_date_edit, 0, 1)
        load_tab_frame0_grid.addWidget(load_tab_end_date_label, 0, 2)
        load_tab_frame0_grid.addWidget(self.load_tab_end_date_edit, 0, 3)
        load_tab_frame0_grid.addWidget(load_tab_host_label, 0, 4)
        load_tab_frame0_grid.addWidget(self.load_tab_host_line, 0, 5)
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
            fig.set_facecolor(theme.CHART_COLORS[True]['figure_face'])

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
            fig.set_facecolor(theme.CHART_COLORS[True]['figure_face'])

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

        # Hide the empty-state hint once a real load is triggered.
        self.hide_empty_hint(getattr(self, 'load_tab_hint_label', None))

        self.update_load_tab_frame1(specified_host, [], [])
        self.update_load_tab_frame2(specified_host, [], [])

        common.bprint('Loading ut/mem load information ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading ut/mem load information ...', persistent=True)
        my_show_message.start()

        (sample_time_list, ut_list, mem_list) = self.get_load_info(specified_host)

        if sample_time_list:
            self.update_load_tab_frame1(specified_host, sample_time_list, ut_list)
            self.update_load_tab_frame2(specified_host, sample_time_list, mem_list)

        time.sleep(0.01)
        my_show_message.terminate()
        my_show_message.wait(3000)

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
        axes = fig.add_subplot(111)

        axes.plot(sample_time_list, ut_list, color=theme.CHART_UT_COLOR, linewidth=theme.CHART_LINEWIDTH, label='CPU')
        axes.fill_between(sample_time_list, ut_list, color=theme.CHART_UT_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        axes.tick_params(axis='x', rotation=15)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title='ut curve for host "' + str(specified_host) + '"',
                                 xlabel='Sample Time',
                                 ylabel='Cpu Utilization (%)')
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
        axes = fig.add_subplot(111)

        axes.plot(sample_time_list, mem_list, color=theme.CHART_MEM_COLOR, linewidth=theme.CHART_LINEWIDTH, label='MEM')
        axes.fill_between(sample_time_list, mem_list, color=theme.CHART_MEM_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        axes.tick_params(axis='x', rotation=15)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title='available mem curve for host "' + str(specified_host) + '"',
                                 xlabel='Sample Time',
                                 ylabel='Available Mem (G)')
        self.load_tab_mem_canvas.draw()
# For load TAB (end) #

# For users TAB (start) #
    def gen_users_tab(self):
        """
        Generate the users tab on lsfMonitor GUI, show users informations.

        The user history query (get_user_info) scans daily DB files and is
        slow, so on first build we only lay out the filter frame and an empty
        header-only table plus a placeholder prompting the user to click
        "Check". The query runs when "Check" (or Enter on a filter field) is
        triggered, via gen_users_tab_table().
        """
        # self.users_tab_frame0
        self.users_tab_frame0 = QFrame(self.users_tab)
        self.users_tab_frame0.setFrameShadow(QFrame.Raised)
        self.users_tab_frame0.setFrameShape(QFrame.Box)

        self.users_tab_table = QTableWidget(self.users_tab)
        common_pyqt5.make_table_readonly(self.users_tab_table)

        # Placeholder overlay prompting the user to click "Check"; hidden once
        # gen_users_tab_table() populates the table. Mouse-transparent so it
        # never blocks interaction with the empty table underneath.
        self.users_tab_placeholder_label = QLabel('Click "Check" button to load user statistics.', self.users_tab)
        self.users_tab_placeholder_label.setAlignment(Qt.AlignCenter)
        self.users_tab_placeholder_label.setWordWrap(True)
        self.users_tab_placeholder_label.setStyleSheet('QLabel { color: #888; font-size: 14px; }')
        self.users_tab_placeholder_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        # self.users_tab_table - Grid
        users_tab_grid = QGridLayout()

        users_tab_grid.addWidget(self.users_tab_frame0, 0, 0)
        users_tab_grid.addWidget(self.users_tab_table, 1, 0)
        users_tab_grid.addWidget(self.users_tab_placeholder_label, 1, 0)

        users_tab_grid.setRowStretch(0, 1)
        users_tab_grid.setRowStretch(1, 20)

        self.users_tab.setLayout(users_tab_grid)

        # Generate sub-frame and an empty header-only table; defer data loading
        # until the user clicks "Check".
        self.gen_users_tab_frame0()
        self.gen_users_tab_table_header()

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

        self.users_tab_queue_combo = common_pyqt5.QComboCheckBox(self.users_tab_frame0, enableFilter=True)
        self.set_users_tab_queue_combo()
        # NOTE: do NOT connect currentTextChanged -> gen_users_tab_table here.
        # USERS queries historical user/<date>.db across the date range, which
        # is slow; auto-refreshing on every queue check toggle would also pop
        # loading dialogs repeatedly. Click the "Check" button to run it,
        # matching how the JOBS tab queue combo behaves.

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
        users_tab_check_button.clicked.connect(self.gen_users_tab_table)

        # self.users_tab_frame0 - Grid
        users_tab_frame0_grid = QGridLayout()

        users_tab_frame0_grid.addWidget(users_tab_begin_date_label, 0, 0)
        users_tab_frame0_grid.addWidget(self.users_tab_begin_date_edit, 0, 1)
        users_tab_frame0_grid.addWidget(users_tab_end_date_label, 0, 2)
        users_tab_frame0_grid.addWidget(self.users_tab_end_date_edit, 0, 3)
        users_tab_frame0_grid.addWidget(users_tab_status_label, 0, 4)
        users_tab_frame0_grid.addWidget(self.users_tab_status_combo, 0, 5)
        users_tab_frame0_grid.addWidget(users_tab_queue_label, 0, 6)
        users_tab_frame0_grid.addWidget(self.users_tab_queue_combo, 0, 7)
        users_tab_frame0_grid.addWidget(users_tab_user_label, 0, 8)
        users_tab_frame0_grid.addWidget(self.users_tab_user_line, 0, 9)
        users_tab_frame0_grid.addWidget(users_tab_check_button, 0, 10)

        for col in range(11):
            users_tab_frame0_grid.setColumnStretch(col, 1)

        users_tab_frame0_grid.setColumnStretch(9, 2)

        self.users_tab_frame0.setLayout(users_tab_frame0_grid)

    def set_users_tab_status_combo(self, checked_status_list=None):
        """
        Set (initialize) self.users_tab_status_combo.
        """
        if checked_status_list is None:
            checked_status_list = ['ALL']
        self.users_tab_status_combo.clear()
        status_list = ['ALL', 'DONE', 'EXIT']

        for status in status_list:
            self.users_tab_status_combo.addCheckBoxItem(status)

        # Set to checked status for checked_status_list.
        for (i, qBox) in enumerate(self.users_tab_status_combo.checkBoxList):
            if (qBox.text() in checked_status_list) and (qBox.isChecked() is False):
                self.users_tab_status_combo.checkBoxList[i].setChecked(True)

    def set_users_tab_queue_combo(self, checked_queue_list=None):
        """
        Set (initialize) self.users_tab_queue_combo.
        """
        if checked_queue_list is None:
            checked_queue_list = ['ALL']
        self.users_tab_queue_combo.clear()
        self.fresh_lsf_info('bqueues')

        if 'QUEUE_NAME' in self.bqueues_dic:
            queue_list = copy.deepcopy(self.bqueues_dic['QUEUE_NAME'])
            queue_list.sort()
        else:
            queue_list = []

        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            self.users_tab_queue_combo.addCheckBoxItem(queue)

        # Set to checked status for checked_queue_list.
        for (i, qBox) in enumerate(self.users_tab_queue_combo.checkBoxList):
            if (qBox.text() in checked_queue_list) and (qBox.isChecked() is False):
                self.users_tab_queue_combo.checkBoxList[i].setChecked(True)

    def gen_users_tab_table_header(self):
        """Initialize the users table header and column widths.

        No data is fetched here; this only prepares an empty, header-only
        table so the USERS tab can render instantly on first visit and defer
        the (slow) history query to the "Check" button.
        """
        self.users_tab_table.setShowGrid(True)
        self.users_tab_table.setSortingEnabled(False)
        self.users_tab_table.setColumnCount(0)
        self.users_tab_table.setColumnCount(9)
        self.users_tab_table_title_list = ['User', 'Job_Num', 'Pass_Rate (%)', 'Total_Rusage_Mem (G)', 'Avg_Rusage_Mem (G)', 'Total_Max_Mem (G)', 'Avg_Max_Mem (G)', 'Total_Mem_Waste (G)', 'Avg_Mem_Waste (G)']
        self.users_tab_table.setHorizontalHeaderLabels(self.users_tab_table_title_list)

        # User column stretches to fill remaining space (handles long account
        # names like "liyanqing.1987"). Numeric columns are fixed-width and sized
        # to their header + 1–2 digit values; tightened from the old generous
        # widths which left the Stretch User column with zero room.
        self.users_tab_table.setColumnWidth(0, 160)
        self.users_tab_table.setColumnWidth(1, 75)
        self.users_tab_table.setColumnWidth(2, 100)
        self.users_tab_table.setColumnWidth(3, 155)
        self.users_tab_table.setColumnWidth(4, 150)
        self.users_tab_table.setColumnWidth(5, 135)
        self.users_tab_table.setColumnWidth(6, 130)
        self.users_tab_table.setColumnWidth(7, 155)
        self.users_tab_table.setColumnWidth(8, 150)
        self.users_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        common_pyqt5.auto_size_table_columns(self.users_tab_table)
        self.users_tab_table.setRowCount(0)

    def gen_users_tab_table(self):
        """Fetch user history info and populate the users table."""
        # Hide the first-visit placeholder once data is loaded.
        if getattr(self, 'users_tab_placeholder_label', None) is not None:
            self.users_tab_placeholder_label.hide()

        self.gen_users_tab_table_header()

        # Fill self.users_tab_table items.
        user_dic = self.get_user_info()
        my_show_message = ShowMessage('Info', 'Rendering users table ...', persistent=True)
        my_show_message.start()
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
            j = j + 1
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(user_dic[user]['job_num']))
            self.users_tab_table.setItem(i, j, item)

            # Fill "Pass_Rate" item.
            j = j + 1
            pass_rate = 0

            if user_dic[user]['job_num']:
                pass_rate = round((100 * float(user_dic[user]['done_num']) / float(user_dic[user]['job_num'])), 1)

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, pass_rate)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Total_Rusage_Mem" item.
            j = j + 1
            item = QTableWidgetItem()
            total_rusage_mem = round(float(user_dic[user]['rusage_mem']) / 1024, 1)
            item.setData(Qt.DisplayRole, total_rusage_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Avg_Rusage_Mem" item.
            j = j + 1
            item = QTableWidgetItem()
            avg_rusage_mem = 0

            if user_dic[user]['job_num']:
                avg_rusage_mem = round(float(user_dic[user]['rusage_mem']) / 1024 / float(user_dic[user]['job_num']), 1)

            item.setData(Qt.DisplayRole, avg_rusage_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Total_Max_Mem" item.
            j = j + 1
            item = QTableWidgetItem()
            total_max_mem = round(float(user_dic[user]['max_mem']) / 1024, 1)
            item.setData(Qt.DisplayRole, total_max_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Avg_Max_Mem" item.
            j = j + 1
            item = QTableWidgetItem()
            avg_max_mem = 0

            if user_dic[user]['job_num']:
                avg_max_mem = round(float(user_dic[user]['max_mem']) / 1024 / float(user_dic[user]['job_num']), 1)

            item.setData(Qt.DisplayRole, avg_max_mem)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Total_Mem_Waste" item.
            j = j + 1
            item = QTableWidgetItem()
            total_mem_waste = round((float(user_dic[user]['rusage_mem']) - float(user_dic[user]['max_mem'])) / 1024, 1)
            item.setData(Qt.DisplayRole, total_mem_waste)
            self.users_tab_table.setItem(i, j, item)

            # Fill "Avg_Mem_Waste" item.
            j = j + 1
            item = QTableWidgetItem()
            avg_mem_waste = 0

            if user_dic[user]['job_num']:
                avg_mem_waste = round((float(user_dic[user]['rusage_mem']) - float(user_dic[user]['max_mem'])) / 1024 / float(user_dic[user]['job_num']), 1)

            item.setData(Qt.DisplayRole, avg_mem_waste)
            self.users_tab_table.setItem(i, j, item)

        self.users_tab_table.setSortingEnabled(True)
        my_show_message.terminate()
        my_show_message.wait(3000)

    def get_user_info(self):
        """
        Get user history information from database.
        """
        common.bprint('Loading user history info ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading user history info ...', persistent=True)
        my_show_message.start()

        user_dic = {'ALL': {'job_num': 0, 'done_num': 0, 'exit_num': 0, 'rusage_mem': 0, 'max_mem': 0}}
        specified_status_list = self.users_tab_status_combo.currentText().strip().split()
        specified_queue_list = self.users_tab_queue_combo.currentText().strip().split()
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

        if specified_queue_list and ('ALL' not in specified_queue_list):
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
                    # Resolve specified_user_list once per db: exact-preferred (exact match wins; fuzzy only when no exact match).
                    all_user_list = [re.sub(r'^user_', '', t) for t in user_table_list if t.startswith('user_')]
                    matched_user_list = common.match_exact_or_fuzzy(specified_user_list, all_user_list) if specified_user_list else []

                    for user_table_name in user_table_list:
                        user = re.sub(r'^user_', '', user_table_name)

                        if (not specified_user_list) or (user in matched_user_list):
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
        my_show_message.wait(3000)

        return user_dic
# For users TAB (end) #

# For queues TAB (start) #
    def gen_queues_tab(self):
        """
        Generate the queues tab on lsfMonitor GUI, show queues informations.
        """
        # self.queues_tab
        self.queues_tab_table = QTableWidget(self.queues_tab)
        common_pyqt5.make_table_readonly(self.queues_tab_table)
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

        # Empty-state hint: the PEND/RUN curve (frame1) stays empty until the
        # user clicks "Check". Shown over the chart frame (graphics area) so the
        # queue table and filter row stay interactive; hidden once a load is
        # triggered.
        self.queues_tab_hint_label = self._make_empty_hint(
            queues_tab_grid, 1, 1, 1, 1, 'Click "Check" button to load queue PEND/RUN curve information.'
        )

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
        common_pyqt5.auto_size_table_columns(self.queues_tab_table)

        # Fresh LSF bhosts/queues/queue_host information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('bqueues')
        self.fresh_lsf_info('queue_host')

        # Fill self.queues_tab_table items.
        self.queues_tab_table.setRowCount(0)

        if 'QUEUE_NAME' in self.bqueues_dic:
            self.queues_tab_table.setRowCount(len(self.bqueues_dic['QUEUE_NAME']) + 1)
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

            if i < len(queue_list) - 1:
                index = self.bqueues_dic['QUEUE_NAME'].index(queue)

            # Fill "QUEUE" item.
            j = 0
            item = QTableWidgetItem(queue)
            item.setFont(QFont('song', 9, QFont.Bold))
            self.queues_tab_table.setItem(i, j, item)

            # Fill "SLOTS" item.
            j = j + 1
            total = 0

            if queue == 'ALL':
                if 'MAX' in self.bhosts_dic:
                    for max in self.bhosts_dic['MAX']:
                        if re.match(r'^\d+$', max):
                            total += int(max)
            elif queue == 'lost_and_found':
                total = 'N/A'
            else:
                for queue_host in self.queue_host_dic.get(queue, []):
                    if queue_host in self.bhosts_dic['HOST_NAME']:
                        host_index = self.bhosts_dic['HOST_NAME'].index(queue_host)
                        host_max = self.bhosts_dic['MAX'][host_index]

                        if re.match(r'^\d+$', host_max):
                            total += int(host_max)

            item = QTableWidgetItem()

            if queue == 'lost_and_found':
                item.setForeground(QBrush(QColor(STATUS_EXIT)))

            if total == 'N/A':
                item.setData(Qt.DisplayRole, str(total))
            else:
                item.setData(Qt.DisplayRole, int(total))

            self.queues_tab_table.setItem(i, j, item)

            # Fill "PEND" item.
            j = j + 1

            if i == len(queue_list) - 1:
                pend = str(pend_sum)
            else:
                pend = self.bqueues_dic['PEND'][index]
                pend_sum += int(pend)

            item = QTableWidgetItem()
            item.setFont(QFont('song', 9, QFont.Bold))

            if int(pend) > 0:
                item.setForeground(QBrush(QColor(STATUS_PEND)))

            item.setData(Qt.DisplayRole, int(pend))
            self.queues_tab_table.setItem(i, j, item)

            # Fill "RUN" item.
            j = j + 1

            if i == len(queue_list) - 1:
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
        my_show_message = ShowMessage('Info', 'Loading queue information, please wait ...', persistent=True)
        my_show_message.start()
        self.gen_queues_tab_table()
        time.sleep(0.01)
        my_show_message.terminate()
        my_show_message.wait(3000)

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
        queues_tab_check_button.clicked.connect(self.update_queues_tab_info)

        # self.queues_tab_frame0 - Grid
        queues_tab_frame0_grid = QGridLayout()

        queues_tab_frame0_grid.addWidget(queues_tab_begin_date_label, 0, 0)
        queues_tab_frame0_grid.addWidget(self.queues_tab_begin_date_edit, 0, 1)
        queues_tab_frame0_grid.addWidget(queues_tab_end_date_label, 0, 2)
        queues_tab_frame0_grid.addWidget(self.queues_tab_end_date_edit, 0, 3)
        queues_tab_frame0_grid.addWidget(queues_tab_queue_label, 0, 4)
        queues_tab_frame0_grid.addWidget(self.queues_tab_queue_combo, 0, 5)
        queues_tab_frame0_grid.addWidget(queues_tab_check_button, 0, 6)

        queues_tab_frame0_grid.setColumnStretch(0, 1)
        queues_tab_frame0_grid.setColumnStretch(1, 1)
        queues_tab_frame0_grid.setColumnStretch(2, 1)
        queues_tab_frame0_grid.setColumnStretch(3, 1)
        queues_tab_frame0_grid.setColumnStretch(4, 1)
        queues_tab_frame0_grid.setColumnStretch(5, 1)
        queues_tab_frame0_grid.setColumnStretch(6, 1)

        self.queues_tab_frame0.setLayout(queues_tab_frame0_grid)

    def set_queues_tab_queue_combo(self, checked_queue_list=None):
        """
        Set (initialize) self.queues_tab_queue_combo.
        """
        if checked_queue_list is None:
            checked_queue_list = ['ALL']
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
                    self._ensure_inner_tab_built(self.jobs_tab)
                    self.set_jobs_tab_status_combo(checked_status_list=['PEND', ])
                    self.set_jobs_tab_queue_combo(checked_queue_list=[queue, ])
                    self.set_jobs_tab_host_line()
                    self.jobs_tab_user_line.setText('')
                    self.gen_jobs_tab_table()
                    self.main_tab.setCurrentWidget(self.jobs_tab)
            elif item.column() == 3:
                if (run_num != '') and (int(run_num) > 0):
                    self._ensure_inner_tab_built(self.jobs_tab)
                    self.set_jobs_tab_status_combo(checked_status_list=['RUN', ])
                    self.set_jobs_tab_queue_combo(checked_queue_list=[queue, ])
                    self.set_jobs_tab_host_line()
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
            fig.set_facecolor(theme.CHART_COLORS[True]['figure_face'])

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
        # Hide the empty-state hint once the user triggers a load.
        self.hide_empty_hint(getattr(self, 'queues_tab_hint_label', None))

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
                            queue_date_dic[date][queue]['total'] = int(sum(tmp_date_dic[date]['total']) / len(tmp_date_dic[date]['total']))
                            queue_date_dic[date][queue]['pend'] = int(sum(tmp_date_dic[date]['pend']) / len(tmp_date_dic[date]['pend']))
                            queue_date_dic[date][queue]['run'] = int(sum(tmp_date_dic[date]['run']) / len(tmp_date_dic[date]['run']))

                queue_db_conn.close()

        return queue_date_dic

    def draw_queues_tab_num_curve(self, fig, queue_list, date_list, total_list, pend_list, run_list):
        """
        Draw RUN/PEND job num curve for specified queue(s).
        """
        axes = fig.add_subplot(111)

        # Get queue string.
        if len(queue_list) == 0:
            queue_string = ''
        elif len(queue_list) == 1:
            queue_string = queue_list[0]
        else:
            queue_string = str(queue_list[0]) + '...'

        # In detail mode there are many dense sample points; use a thin line
        # so the curve stays readable (matches the old expected_linewidth logic).
        if self.enable_queue_detail:
            lw = theme.CHART_LINEWIDTH_DETAIL
        else:
            lw = theme.CHART_LINEWIDTH

        axes.plot(date_list, total_list, color=theme.CHART_SERIES[0], linewidth=lw, label='SLOTS')
        axes.fill_between(date_list, total_list, color=theme.CHART_SERIES[0], alpha=0.12)
        axes.plot(date_list, run_list, color=theme.CHART_RUN_COLOR, linewidth=lw, label='RUN')
        axes.fill_between(date_list, run_list, color=theme.CHART_RUN_COLOR, alpha=0.12)
        axes.plot(date_list, pend_list, color=theme.CHART_PEND_COLOR, linewidth=lw, label='PEND')
        axes.fill_between(date_list, pend_list, color=theme.CHART_PEND_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        axes.tick_params(axis='x', rotation=15)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title='Trends of RUN/PEND number for queues "' + str(queue_string) + '"',
                                 xlabel='Sample Time' if self.enable_queue_detail else 'Sample Date',
                                 ylabel='Num')
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
        common_pyqt5.make_table_readonly(self.utilization_tab_table)
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

        # Empty-state hint: the UTILIZATION table stays empty until the user
        # clicks "Check". Shown over the chart frame (the tab's graphics area)
        # so the filter row and table stay interactive; hidden once a load is
        # triggered.
        self.utilization_tab_hint_label = self._make_empty_hint(
            utilization_tab_grid, 1, 1, 1, 1, 'Click "Check" button to load queue utilization information.'
        )

    def gen_utilization_tab_frame0(self):
        # self.utilization_tab_frame0
        # "Cluster" item.
        utilization_tab_cluster_label = QLabel('Cluster', self.utilization_tab_frame0)
        utilization_tab_cluster_label.setStyleSheet("font-weight: bold;")
        utilization_tab_cluster_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_cluster_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0, enableFilter=True)
        self.set_utilization_tab_cluster_combo()
        self.utilization_tab_cluster_combo.currentTextChanged.connect(self.update_utilization_tab_queue_combo_by_cluster)
        self.utilization_tab_cluster_combo.currentTextChanged.connect(self.update_utilization_tab_group_combo_by_cluster)

        # "Queue" item.
        utilization_tab_queue_label = QLabel('Queue', self.utilization_tab_frame0)
        utilization_tab_queue_label.setStyleSheet("font-weight: bold;")
        utilization_tab_queue_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_queue_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0, enableFilter=True)
        self.set_utilization_tab_queue_combo()
        # Queue 与 Group 互斥:选中 Queue 非 ALL 项时,Group 强制回到 ALL。
        self.utilization_tab_queue_combo.currentTextChanged.connect(self.enforce_utilization_tab_group_all)

        # "Group" item.
        utilization_tab_group_label = QLabel('Group', self.utilization_tab_frame0)
        utilization_tab_group_label.setStyleSheet("font-weight: bold;")
        utilization_tab_group_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_group_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0, enableFilter=True)
        self.set_utilization_tab_group_combo()
        # Group 与 Queue 互斥:选中 Group 非 ALL 项时,Queue 强制回到 ALL。
        self.utilization_tab_group_combo.currentTextChanged.connect(self.enforce_utilization_tab_queue_all)

        # "Check" button.
        utilization_tab_check_button = QPushButton('Check', self.utilization_tab_frame0)
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

        # self.utilization_tab_frame0 - Grid
        utilization_tab_frame0_grid = QGridLayout()

        utilization_tab_frame0_grid.addWidget(utilization_tab_begin_date_label, 0, 0)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_begin_date_edit, 0, 1)
        utilization_tab_frame0_grid.addWidget(utilization_tab_end_date_label, 0, 2)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_end_date_edit, 0, 3)
        utilization_tab_frame0_grid.addWidget(utilization_tab_cluster_label, 0, 4)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_cluster_combo, 0, 5)
        utilization_tab_frame0_grid.addWidget(utilization_tab_queue_label, 0, 6)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_queue_combo, 0, 7)
        utilization_tab_frame0_grid.addWidget(utilization_tab_group_label, 0, 8)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_group_combo, 0, 9)
        utilization_tab_frame0_grid.addWidget(utilization_tab_check_button, 0, 10)

        for col in range(11):
            utilization_tab_frame0_grid.setColumnStretch(col, 1)

        utilization_tab_frame0_grid.setColumnStretch(5, 2)
        utilization_tab_frame0_grid.setColumnStretch(7, 2)
        utilization_tab_frame0_grid.setColumnStretch(9, 2)

        self.utilization_tab_frame0.setLayout(utilization_tab_frame0_grid)

    def set_utilization_tab_cluster_combo(self, checked_cluster_list=None):
        """
        Set (initialize) self.utilization_tab_cluster_combo.
        """
        self.utilization_tab_cluster_combo.clear()
        db_root_path = Path(self.lsf_db_root)
        cluster_list = []

        if db_root_path.exists() and db_root_path.is_dir():
            for entry in os.scandir(db_root_path):
                if entry.is_dir():
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
        db_root_path = Path(self.lsf_db_root)
        queue_list = []

        # 获取所有集群的所有队列
        if db_root_path.exists() and db_root_path.is_dir():
            for entry in os.scandir(db_root_path):
                if entry.is_dir():
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

        # 兜底:若 db 中没有当前集群的 queue 数据(如全新环境未采样过),
        # 实时用 bqueues 获取当前集群的 queue 列表填充,避免 Queue combo 为空。
        if self.cluster:
            current_cluster_has_queue = any(
                (q != 'ALL') and (self._parse_queue_full_name(q)[0] == self.cluster)
                for q in queue_list
            )

            if not current_cluster_has_queue:
                try:
                    bqueues_dic = common_lsf.get_bqueues_info()
                    queue_names = bqueues_dic.get('QUEUE_NAME', [])

                    for queue_name in queue_names:
                        if queue_name:
                            queue_list.append(f"{self.cluster}-{queue_name}")
                except Exception as warning:
                    common.bprint(f'Failed on getting queue list from bqueues: {warning}', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

        # 按 (cluster, queue_name) 二级排序,使同一 cluster 的 queue 连续显示。
        def _queue_sort_key(q):
            c, n = self._parse_queue_full_name(q)

            return (c or '', n or '')

        queue_list.sort(key=_queue_sort_key)
        queue_list.insert(0, 'ALL')

        for queue in queue_list:
            if queue == 'ALL':
                display_text = 'ALL'
            else:
                q_cluster, q_name = self._parse_queue_full_name(queue)
                display_text = f"{q_name}  ({q_cluster})" if q_cluster else queue

            self.utilization_tab_queue_combo.addCheckBoxItem(display_text, data=queue, update_width=True)

        # 处理默认选中逻辑:默认选中当前 Cluster 的所有 Queue(不选 ALL)。
        if checked_queue_list is None:
            checked_queue_list = []

            for queue in queue_list:
                if queue == 'ALL':
                    continue

                if '-' in queue:
                    q_cluster, _ = self._parse_queue_full_name(queue)

                    if q_cluster == self.cluster:
                        checked_queue_list.append(queue)

        # Set to checked status for checked_queue_list(按内部值 data 匹配)。
        for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
            data = self.utilization_tab_queue_combo.itemData(i)

            if (data in checked_queue_list) and (qBox.isChecked() is False):
                self.utilization_tab_queue_combo.checkBoxList[i].setChecked(True)
            elif (data not in checked_queue_list) and (qBox.isChecked() is True):
                self.utilization_tab_queue_combo.checkBoxList[i].setChecked(False)

    def update_utilization_tab_queue_combo_by_cluster(self):
        """
        Update queue checked state when cluster selection changes.
        仅调整Queue选中状态，不修改队列列表。

        blockSignals 防止 setChecked 触发 currentTextChanged 回调
        enforce_utilization_tab_group_all,与 Group 侧互斥形成递归(切 Cluster
        批量 set 时 RecursionError 崩溃,或第一个 group 漏选)。
        """
        self.utilization_tab_queue_combo.blockSignals(True)

        try:
            # 获取选中的集群
            selected_cluster_dic = self.utilization_tab_cluster_combo.selectedItems()
            selected_clusters = sorted(list(selected_cluster_dic.values())) if selected_cluster_dic else []
            select_all = False

            if 'ALL' in selected_clusters:
                select_all = True
                # 选中ALL时获取所有集群
                selected_clusters = []
                db_root_path = Path(self.lsf_db_root)

                if db_root_path.exists() and db_root_path.is_dir():
                    for entry in os.scandir(db_root_path):
                        if entry.is_dir():
                            selected_clusters.append(entry.name)

            # 调整队列选中状态(按内部值 data 匹配)。
            if not selected_clusters:
                # 没有选中任何集群时，所有队列都不选中
                for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
                    qBox.setChecked(False)
            elif select_all:
                # 选中Cluster的ALL时，Queue只需要选中ALL即可，不需要选中所有队列
                for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
                    if self.utilization_tab_queue_combo.itemData(i) == 'ALL':
                        qBox.setChecked(True)
                    else:
                        qBox.setChecked(False)
            else:
                for (i, qBox) in enumerate(self.utilization_tab_queue_combo.checkBoxList):
                    queue_name = self.utilization_tab_queue_combo.itemData(i)

                    if queue_name == 'ALL':
                        # 选中部分集群时ALL保持选中
                        qBox.setChecked(True)
                        continue

                    # 获取队列所属集群
                    if '-' in queue_name:
                        queue_cluster, _ = self._parse_queue_full_name(queue_name)

                        if queue_cluster in selected_clusters:
                            qBox.setChecked(True)
                        else:
                            qBox.setChecked(False)
                    else:
                        # 特殊队列（如lost_and_found）默认不选中
                        qBox.setChecked(False)

            self.utilization_tab_queue_combo.updateLineEdit()
        finally:
            self.utilization_tab_queue_combo.blockSignals(False)

    def set_utilization_tab_group_combo(self, checked_group_list=None):
        """
        Set (initialize) self.utilization_tab_group_combo.
        优先从 group_host_mapping.db 读取历史 group 列表;库缺失时 fallback 实时 bmgroup。
        """
        self.utilization_tab_group_combo.clear()
        db_root_path = Path(self.lsf_db_root)
        group_list = []

        # 获取所有集群的所有 host group。
        if db_root_path.exists() and db_root_path.is_dir():
            for entry in os.scandir(db_root_path):
                if entry.is_dir():
                    cluster = entry.name
                    cluster_db_path = db_root_path / cluster
                    host_group_mapping_db = cluster_db_path / 'group_host_mapping.db'

                    if host_group_mapping_db.exists():
                        (result, conn) = common_sqlite3.connect_db_file(str(host_group_mapping_db))

                        if result == 'passed':
                            table_list = common_sqlite3.get_sql_table_list(str(host_group_mapping_db), conn)
                            cluster_groups = [re.sub(r'^group_', '', table) for table in table_list if table.startswith('group_')]

                            for group in cluster_groups:
                                group_list.append(f"{cluster}-{group}")

                            conn.close()

        # 库为空时 fallback 实时 bmgroup(当前集群)。
        if not group_list:
            self.fresh_lsf_info('host_group')

            for host, groups in self.host_group_dic.items():
                for group in groups:
                    group_list.append(f"{self.cluster}-{group}")

        # 按 (cluster, group_name) 二级排序,使同一 cluster 的 group 连续显示。
        def _group_sort_key(g):
            c, n = self._parse_group_full_name(g)

            return (c or '', n or '')

        group_list = sorted(set(group_list), key=_group_sort_key)
        group_list.insert(0, 'ALL')

        for group in group_list:
            if group == 'ALL':
                display_text = 'ALL'
            else:
                g_cluster, g_name = self._parse_group_full_name(group)
                display_text = f"{g_name}  ({g_cluster})" if g_cluster else group

            self.utilization_tab_group_combo.addCheckBoxItem(display_text, data=group, update_width=True)

        # 处理默认选中逻辑:默认只选 ALL。
        if checked_group_list is None:
            checked_group_list = ['ALL']

        # Set to checked status for checked_group_list(按内部值 data 匹配)。
        for (i, qBox) in enumerate(self.utilization_tab_group_combo.checkBoxList):
            data = self.utilization_tab_group_combo.itemData(i)

            if (data in checked_group_list) and (qBox.isChecked() is False):
                self.utilization_tab_group_combo.checkBoxList[i].setChecked(True)
            elif (data not in checked_group_list) and (qBox.isChecked() is True):
                self.utilization_tab_group_combo.checkBoxList[i].setChecked(False)

    def _utilization_tab_has_non_all(self, combo):
        """检查指定 QComboCheckBox 是否选中了非 ALL 的项。"""
        selected_dic = combo.selectedItems()

        for text in selected_dic.values():
            if text != 'ALL':
                return True

        return False

    def _utilization_tab_combo_is_empty(self, combo):
        """检查指定 QComboCheckBox 是否完全未选(连 ALL 也没勾)。"""
        return not combo.selectedItems()

    def _utilization_tab_group_active(self):
        """
        判断是否按 Group 维度展示,依据 Queue/Group 两侧选择状态:
        1. 都 ALL 或都空选 → 默认 Queue 维度。
        2. 一侧空选,另一侧有选(ALL 或具体项) → 走有选的那侧;另一侧为 Group 时即 Group 维度。
        3. 任一侧选了非 ALL 项 → 该侧生效(互斥已由 enforce_* 保证另一侧回 ALL)。
        即:Group 选了非 ALL,或 Queue 空选而 Group 有选 → Group 维度。
        """
        queue_empty = self._utilization_tab_combo_is_empty(self.utilization_tab_queue_combo)
        group_has_selection = not self._utilization_tab_combo_is_empty(self.utilization_tab_group_combo)

        if self._utilization_tab_has_non_all(self.utilization_tab_group_combo):
            return True

        # Queue 空选且 Group 有选(ALL 或具体项) → 交给 Group 维度。
        if queue_empty and group_has_selection:
            return True

        return False

    def _utilization_tab_reset_to_all(self, combo):
        """将指定 QComboCheckBox 强制回到仅 ALL 选中状态。

        blockSignals 避免 setChecked 触发 currentTextChanged,该信号会回调
        enforce_utilization_tab_*_all 又 reset 另一侧,queue↔group 互斥形成
        递归(RecursionError 崩溃)。
        """
        combo.blockSignals(True)

        try:
            for (i, qBox) in enumerate(combo.checkBoxList):
                if qBox.text() == 'ALL':
                    if not qBox.isChecked():
                        combo.checkBoxList[i].setChecked(True)
                else:
                    if qBox.isChecked():
                        combo.checkBoxList[i].setChecked(False)

            # blockSignals 挡了 qBoxStateChanged→updateLineEdit,这里手动同步显示文本。
            combo.updateLineEdit()
        finally:
            combo.blockSignals(False)

    def enforce_utilization_tab_group_all(self):
        """Queue 选中非 ALL 项时,Group 强制回到 ALL(互斥)。"""
        if self._utilization_tab_has_non_all(self.utilization_tab_queue_combo):
            self._utilization_tab_reset_to_all(self.utilization_tab_group_combo)

    def enforce_utilization_tab_queue_all(self):
        """Group 选中非 ALL 项时,Queue 强制回到 ALL(互斥)。"""
        if self._utilization_tab_has_non_all(self.utilization_tab_group_combo):
            self._utilization_tab_reset_to_all(self.utilization_tab_queue_combo)

    def update_utilization_tab_group_combo_by_cluster(self):
        """Reset Group to ALL when cluster selection changes.

        Group 是给用户保留的可选维度,默认 ALL;切换 Cluster 时不联动勾选
        group 项(留给用户手动选),而是重置回 ALL。blockSignals 防止 setChecked
        触发 currentTextChanged 回调 enforce_utilization_tab_queue_all,与
        Queue 侧互斥形成递归(切 Cluster 批量 set 时 RecursionError 崩溃,
        或第一个 group adamd 漏选)。
        """
        self.utilization_tab_group_combo.blockSignals(True)

        try:
            for (i, qBox) in enumerate(self.utilization_tab_group_combo.checkBoxList):
                if self.utilization_tab_group_combo.itemData(i) == 'ALL':
                    if not qBox.isChecked():
                        self.utilization_tab_group_combo.checkBoxList[i].setChecked(True)
                else:
                    if qBox.isChecked():
                        self.utilization_tab_group_combo.checkBoxList[i].setChecked(False)

            self.utilization_tab_group_combo.updateLineEdit()
        finally:
            self.utilization_tab_group_combo.blockSignals(False)

    def update_utilization_tab_info(self):
        """
        Update self.utilization_tab_table and self.utilization_tab_frame1.
        """
        # Hide the empty-state hint once the user triggers a load.
        self.hide_empty_hint(getattr(self, 'utilization_tab_hint_label', None))

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
            my_show_message = ShowMessage('Info', 'Rendering utilization table ...', persistent=True)
            my_show_message.start()
            self.gen_utilization_tab_table(queue_utilization_dic)
            self.update_utilization_tab_frame1()
            my_show_message.terminate()
            my_show_message.wait(3000)
        else:
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
            slice_end = min(all_change_times[i + 1], end_second)

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

    def get_historical_host_group_mapping(self, db_path, begin_second, end_second):
        """
        Get all host group-host mappings in the specified time range.
        与 get_historical_queue_host_mapping 对称,数据源为 group_host_mapping.db(表 group_<name>)。
        Returns:
            - group_list: all groups existed in the time range
            - mapping_matrix: list of (start_second, end_second, {group: [hosts]})
            - current_group_list: current existing groups (to mark deleted groups)
        """
        host_group_mapping_db_file = str(db_path) + '/group_host_mapping.db'
        mapping_matrix = []
        all_groups = set()
        group_change_points = []

        # 库缺失时 fallback 实时 host_group_dic(当前集群快照)。
        if not os.path.exists(host_group_mapping_db_file):
            common.bprint(f'Host group-host mapping database "{host_group_mapping_db_file}" is missing.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            self.fresh_lsf_info('host_group')

            # host_group_dic 是 {host: [groups]},反转为 {group: [hosts]}。
            current_group_host_dic = {}

            for host, groups in self.host_group_dic.items():
                for group in groups:
                    current_group_host_dic.setdefault(group, []).append(host)

            return list(current_group_host_dic.keys()), [(begin_second, end_second, current_group_host_dic)], list(current_group_host_dic.keys())

        (result, conn) = common_sqlite3.connect_db_file(host_group_mapping_db_file)

        if result == 'failed':
            common.bprint('Failed to connect host group-host mapping database.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            self.fresh_lsf_info('host_group')

            current_group_host_dic = {}

            for host, groups in self.host_group_dic.items():
                for group in groups:
                    current_group_host_dic.setdefault(group, []).append(host)

            return list(current_group_host_dic.keys()), [(begin_second, end_second, current_group_host_dic)], list(current_group_host_dic.keys())

        # Get all group tables.
        table_list = common_sqlite3.get_sql_table_list(host_group_mapping_db_file, conn)
        group_list = [re.sub(r'^group_', '', table) for table in table_list if table.startswith('group_')]

        # Collect all change points.
        for group in group_list:
            table_name = f'group_{group}'
            data = common_sqlite3.get_sql_table_data(
                host_group_mapping_db_file, conn, table_name,
                ['sample_second', 'hosts'],
                f"WHERE sample_second BETWEEN {begin_second - 86400} AND {end_second + 86400} ORDER BY sample_second"
            )

            if data and data['sample_second']:
                for i, sample_second in enumerate(data['sample_second']):
                    hosts = data['hosts'][i].split()
                    group_change_points.append((int(sample_second), group, hosts))

                all_groups.add(group)

        # 当前集群才标记 deleted(用实时 bmgroup 判断 group 是否仍存在);其他集群默认视为存在。
        current_cluster_db_path = str(self.cluster_db_path)

        if db_path == current_cluster_db_path:
            self.fresh_lsf_info('host_group')
            current_group_set = set()

            for groups in self.host_group_dic.values():
                for group in groups:
                    current_group_set.add(group)

            current_group_list = list(current_group_set)
        else:
            current_group_list = list(all_groups)

        # Sort change points by time.
        group_change_points.sort(key=lambda x: x[0])

        # Generate time slices.
        if not group_change_points:
            current_group_host_dic = {}

            for host, groups in self.host_group_dic.items():
                for group in groups:
                    current_group_host_dic.setdefault(group, []).append(host)

            return list(all_groups) if all_groups else list(current_group_host_dic.keys()), [(begin_second, end_second, current_group_host_dic)], current_group_list

        # Build mapping matrix.
        current_mapping = {}

        # Initialize mapping: use the latest record before begin_second for each group.
        for group in group_list:
            table_name = f'group_{group}'
            data = common_sqlite3.get_sql_table_data(
                host_group_mapping_db_file, conn, table_name,
                ['sample_second', 'hosts'],
                f"WHERE sample_second <= {begin_second} ORDER BY sample_second DESC LIMIT 1"
            )

            if data and data['hosts']:
                current_mapping[group] = data['hosts'][0].split()
            else:
                earliest_data = common_sqlite3.get_sql_table_data(
                    host_group_mapping_db_file, conn, table_name,
                    ['sample_second', 'hosts'],
                    "ORDER BY sample_second ASC LIMIT 1"
                )

                if earliest_data and earliest_data['hosts']:
                    current_mapping[group] = earliest_data['hosts'][0].split()

        # Process change points to build time slices.
        all_change_times = sorted(list(set([cp[0] for cp in group_change_points] + [begin_second, end_second])))

        for i in range(len(all_change_times) - 1):
            slice_start = max(all_change_times[i], begin_second)
            slice_end = min(all_change_times[i + 1], end_second)

            if slice_start >= slice_end:
                continue

            # Update mapping for this slice.
            for cp_time, group, hosts in group_change_points:
                if cp_time == all_change_times[i]:
                    current_mapping[group] = hosts

            # Add to matrix.
            mapping_matrix.append((slice_start, slice_end, copy.deepcopy(current_mapping)))

        conn.close()

        return sorted(list(all_groups)), mapping_matrix, current_group_list

    def get_queue_utilization_info(self):
        """
        Get queue utilization info from sqlite database using historical queue-host mapping, support multi-cluster.
        """
        common.bprint('Loading queue utilization info ...', date_format='%Y-%m-%d %H:%M:%S')

        my_show_message = ShowMessage('Info', 'Loading queue utilization info ...', persistent=True)
        my_show_message.start()

        # Import pandas AFTER the loading prompt is shown — pandas' first import
        # takes ~2s, so showing the prompt first keeps the UI responsive
        # instead of freezing for 2s before anything appears.
        import pandas as pd

        # Get time range
        begin_date = self.utilization_tab_begin_date_edit.date().toString(Qt.ISODate)
        original_begin_second = int(time.mktime(time.strptime(f"{begin_date} 00:00:00", '%Y-%m-%d %H:%M:%S')))
        end_date = self.utilization_tab_end_date_edit.date().toString(Qt.ISODate)
        original_end_second = int(time.mktime(time.strptime(f"{end_date} 23:59:59", '%Y-%m-%d %H:%M:%S')))
        # 缓存查询用的时间范围
        begin_second = original_begin_second
        end_second = original_end_second

        # 获取选中的集群
        db_root_path = Path(self.lsf_db_root)
        selected_cluster_dic = self.utilization_tab_cluster_combo.selectedItems()
        selected_clusters = sorted(list(selected_cluster_dic.values())) if selected_cluster_dic else []

        if 'ALL' in selected_clusters:
            selected_clusters = []

            if db_root_path.exists() and db_root_path.is_dir():
                for entry in os.scandir(db_root_path):
                    if entry.is_dir():
                        selected_clusters.append(entry.name)

        # 确定当前生效维度:Group 选中了非 ALL 项时按 group 维度统计,否则按 queue 维度。
        group_active = self._utilization_tab_group_active()

        # 获取选中的队列/Group(二者互斥,生效维度决定数据来源)。取内部值(完整名 {cluster}-{name})。
        if group_active:
            selected_entity_dic = self.utilization_tab_group_combo.selectedData()
        else:
            selected_entity_dic = self.utilization_tab_queue_combo.selectedData()

        selected_queues = list(selected_entity_dic.values()) if selected_entity_dic else []

        # 生效维度 combo 完全空选时(如双侧都空,或 Group 维度但 Group 也空),视为 ALL 取全部。
        if not selected_queues:
            selected_queues = ['ALL']

        # Generate cache key (增量查询优化：key包含集群、维度和队列/group，同条件下复用缓存)
        cache_base_key = (tuple(selected_clusters), 'group' if group_active else 'queue', tuple(selected_queues), self.enable_utilization_detail)

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
                    my_show_message.wait(3000)
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
        # 解析 queue/group 与集群的映射(group_active 时维度为 group)。
        parse_full_name = self._parse_group_full_name if group_active else self._parse_queue_full_name
        queue_cluster_map = {}
        queue_full_name_map = {}
        only_all_selected = len(selected_queues) == 1 and 'ALL' in selected_queues

        for q in selected_queues:
            if q == 'ALL':
                continue

            if '-' in q:
                cluster, queue_name = parse_full_name(q)

                if cluster and cluster in selected_clusters:
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
            my_show_message.wait(3000)
            my_show_message = ShowMessage('Info', f'Loading historical mapping for cluster {cluster} ...', persistent=True)
            my_show_message.start()
            QApplication.processEvents()

            # Get historical mapping for current cluster (queue or group dimension).
            if group_active:
                historical_queue_list, mapping_matrix, current_queue_list = self.get_historical_host_group_mapping(str(cluster_db_path), original_begin_second, original_end_second)
            else:
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
            my_show_message.wait(3000)
            my_show_message = ShowMessage('Info', f'Loading {len(all_hosts)} hosts data for cluster {cluster} ...', persistent=True)
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
            my_show_message.wait(3000)
            my_show_message = ShowMessage('Info', f'Calculating utilization for cluster {cluster} ...', persistent=True)
            my_show_message.start()
            QApplication.processEvents()

            # 生成时间key
            if self.enable_utilization_detail:
                df_cluster['time_key'] = df_cluster['sample_second'].apply(lambda x: time.strftime('%Y%m%d_%H%M%S', time.localtime(x)))
                df_cluster['ts'] = df_cluster['sample_second']
            else:
                df_cluster['ts'] = pd.to_datetime(df_cluster['sample_date'], format='%Y%m%d').astype(np.int64) // 10**9 + 12 * 3600
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
        my_show_message.wait(3000)

        if not queue_utilization_dic:
            common.bprint('No utilization data found for selected clusters and queues.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            return None

        return queue_utilization_dic, full_time_util, original_begin_second, original_end_second

    def gen_utilization_tab_table(self, queue_utilization_dic=None):
        """
        Generte self.utilization_tab_table.
        """
        if queue_utilization_dic is None:
            queue_utilization_dic = {}

        self.utilization_tab_table.setShowGrid(True)
        self.utilization_tab_table.setSortingEnabled(False)
        self.utilization_tab_table.setColumnCount(0)
        self.utilization_tab_table.setColumnCount(5)
        self.utilization_tab_table.setRowCount(0)
        self.utilization_tab_table.setRowCount(len(queue_utilization_dic))

        # 首列标题随生效维度切换:Group 维度显示 Group,否则显示 Queue。
        group_active = self._utilization_tab_group_active()
        self.utilization_tab_table_title_list = ['Group' if group_active else 'Queue', 'slots', 'slot(%)', 'cpu(%)', 'mem(%)']
        self.utilization_tab_table.setHorizontalHeaderLabels(self.utilization_tab_table_title_list)

        self.utilization_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.utilization_tab_table.setColumnWidth(1, 70)
        self.utilization_tab_table.setColumnWidth(2, 60)
        self.utilization_tab_table.setColumnWidth(3, 60)
        self.utilization_tab_table.setColumnWidth(4, 60)
        common_pyqt5.auto_size_table_columns(self.utilization_tab_table)

        # Fresh LSF bhosts/queues/queue_host/host_group information.
        self.fresh_lsf_info('bhosts')
        self.fresh_lsf_info('queue_host')
        self.fresh_lsf_info('host_group')

        # 按生效维度选择 entity->hosts 映射:group 维度用 host group,否则用 queue。
        if group_active:
            # host_group_dic 是 {host: [groups]},反转为 {group: [hosts]}。
            entity_host_dic = {}

            for host, groups in self.host_group_dic.items():
                for group in groups:
                    entity_host_dic.setdefault(group, []).append(host)
        else:
            entity_host_dic = self.queue_host_dic

        # 按生效维度选择 full name 解析函数(queue 或 group),整个表格生成期间不变。
        parse_full_name = self._parse_group_full_name if group_active else self._parse_queue_full_name

        # Fill self.utilization_tab_table items.
        if queue_utilization_dic:
            row = -1

            for queue in queue_utilization_dic.keys():
                row += 1
                queue_data = queue_utilization_dic[queue]
                is_deleted = queue_data.get('is_deleted', False) if isinstance(queue_data, dict) else False

                # Fill "Queue/Group" item(显示名 <name>  (<cluster>),内部完整名存 UserRole 供下钻使用)。
                if queue == 'ALL':
                    queue_display = 'ALL'
                else:
                    e_cluster, e_name = parse_full_name(queue)
                    queue_display = f"{e_name}  ({e_cluster})" if e_cluster else queue

                if is_deleted:
                    queue_display = f"{queue_display} (deleted)"

                item = QTableWidgetItem(queue_display)
                item.setData(Qt.UserRole, queue)
                item.setFont(QFont('song', 9, QFont.Bold))

                if is_deleted:
                    item.setForeground(QBrush(QColor(STATUS_DONE)))

                self.utilization_tab_table.setItem(row, 0, item)

                # Fill "slots" item.
                total = 0

                if queue == 'ALL':
                    # Get all selected entities (exclude ALL itself)
                    selected_entities = [q for q in queue_utilization_dic.keys() if q != 'ALL']
                    has_na = False
                    all_hosts = set()

                    for q in selected_entities:
                        # 检查是否有跨集群、已删除或不存在的 entity
                        entity_name = q

                        if '-' in q:
                            q_cluster, entity_name = parse_full_name(q)

                            if q_cluster != self.cluster:
                                has_na = True
                                break

                        if entity_name not in entity_host_dic:
                            has_na = True
                            break

                        all_hosts.update(entity_host_dic[entity_name])

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
                elif (not group_active and queue == 'lost_and_found') or is_deleted:
                    total = 'N/A'
                else:
                    # 检查是否是跨集群 entity
                    if '-' in queue:
                        q_cluster, _ = parse_full_name(queue)

                        if q_cluster != self.cluster:
                            total = 'N/A'
                        else:
                            # 同集群的带前缀 entity,提取名称查询
                            _, entity_name = parse_full_name(queue)

                            if entity_name in entity_host_dic:
                                for entity_host in entity_host_dic[entity_name]:
                                    if 'HOST_NAME' in self.bhosts_dic:
                                        if entity_host in self.bhosts_dic['HOST_NAME']:
                                            host_index = self.bhosts_dic['HOST_NAME'].index(entity_host)
                                            host_max = self.bhosts_dic['MAX'][host_index]

                                            if re.match(r'^\d+$', host_max):
                                                total += int(host_max)
                            else:
                                total = 'N/A'
                    else:
                        # 当前集群普通 entity
                        if queue in entity_host_dic:
                            for entity_host in entity_host_dic[queue]:
                                if 'HOST_NAME' in self.bhosts_dic:
                                    if entity_host in self.bhosts_dic['HOST_NAME']:
                                        host_index = self.bhosts_dic['HOST_NAME'].index(entity_host)
                                        host_max = self.bhosts_dic['MAX'][host_index]

                                        if re.match(r'^\d+$', host_max):
                                            total += int(host_max)
                        else:
                            total = 'N/A'

                item = QTableWidgetItem()

                if (not group_active and queue == 'lost_and_found') or is_deleted:
                    item.setForeground(QBrush(QColor(STATUS_DONE)))

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
                        item.setForeground(QBrush(QColor(STATUS_DONE)))

                    self.utilization_tab_table.setItem(row, i + 2, item)

        self.utilization_tab_table.setSortingEnabled(True)

    def utilization_tab_check_click(self, item=None):
        """
        If click QUEUE/GROUP name, show its slot/cpu/mem utilization information on UTILIZATION tab.
        """
        if item is not None:
            current_row = self.utilization_tab_table.currentRow()
            # 优先取 UserRole 存的完整名(单元格显示名为 <name>  (<cluster>)),回退到 text。
            cell_item = self.utilization_tab_table.item(current_row, 0)
            queue = cell_item.data(Qt.UserRole) if cell_item.data(Qt.UserRole) else cell_item.text().strip()
            # Remove (deleted) suffix if exists
            queue = re.sub(r'\s*\(deleted\)$', '', queue)

            if item.column() == 0:
                # 当前生效维度决定下钻到 queue 还是 group。
                group_active = self._utilization_tab_group_active()

                if group_active:
                    common.bprint(f'Checking utilization for group "{queue}".', date_format='%Y-%m-%d %H:%M:%S')
                    self.set_utilization_tab_group_combo(checked_group_list=[queue, ])
                    # set_utilization_tab_group_combo 内部不会自动互斥 queue,显式触发一次。
                    self.enforce_utilization_tab_queue_all()
                else:
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
            fig.set_facecolor(theme.CHART_COLORS[True]['figure_face'])

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

        # Get selected queues(取内部值)。Group 维度时取 Group combo,Queue 维度时取 Queue combo。
        if self._utilization_tab_group_active():
            selected_queue_dic = self.utilization_tab_group_combo.selectedData()
        else:
            selected_queue_dic = self.utilization_tab_queue_combo.selectedData()

        selected_queues = list(selected_queue_dic.values()) if selected_queue_dic else []

        # 生效维度 combo 空选时视为 ALL(与取数逻辑一致),否则会误报 "No queue"。
        if not selected_queues:
            selected_queues = ['ALL']

        selected_resource_list = list(self.utilization_tab_resource_list)

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

        # 资源颜色配置：与 theme 色板对齐（slot=蓝, cpu=橙, mem=绿）。
        resource_colors = {
            'slot': {'line': theme.CHART_SERIES[0], 'fill': theme.CHART_SERIES[0], 'alpha': 0.12},
            'cpu': {'line': '#F59E0B', 'fill': '#F59E0B', 'alpha': 0.15},
            'mem': {'line': theme.CHART_MEM_COLOR, 'fill': theme.CHART_MEM_COLOR, 'alpha': 0.12},
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
        axes = fig.add_subplot(111)

        # 设置标题
        title = ';    '.join(title_lines)

        # 设置线宽和标记大小
        if self.enable_utilization_detail:
            lw = theme.CHART_LINEWIDTH_DETAIL
        else:
            lw = theme.CHART_LINEWIDTH

        # 绘制曲线
        for plot_key, data in plot_data.items():
            queue = data['queue']
            res = data['resource']
            date_list = data['date_list']
            util_list = data['util_list']

            # 选择颜色
            line_color = resource_colors[res]['line']
            fill_color = resource_colors[res]['fill']
            fill_alpha = resource_colors[res]['alpha']

            # 绘制曲线
            label = f"{queue}_{res.upper()}" if queue != 'ALL' else res.upper()
            axes.plot(date_list, util_list, color=line_color, linewidth=lw, label=label)

            # 只有ALL队列做填充，避免多队列时颜色叠加变色
            if queue == 'ALL':
                axes.fill_between(date_list, util_list, color=fill_color, alpha=fill_alpha)

        axes.legend(loc='upper right', frameon=False)
        axes.tick_params(axis='x', rotation=15)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title=title,
                                 xlabel='Sample Time' if self.enable_utilization_detail else 'Sample Date',
                                 ylabel='Utilization (%)')
        self.utilization_tab_utilization_canvas.draw()

# For utilization TAB (end) #


# Export table (start) #
    def export_jobs_table(self):
        self._ensure_inner_tab_built(self.jobs_tab)
        self.export_table('jobs', self.jobs_tab_table, self.jobs_tab_table_title_list)

    def export_hosts_table(self):
        self._ensure_inner_tab_built(self.hosts_tab)
        self.export_table('hosts', self.hosts_tab_table, self.hosts_tab_table_title_list)

    def export_users_table(self):
        self._ensure_inner_tab_built(self.users_tab)
        self.export_table('users', self.users_tab_table, self.users_tab_table_title_list)

    def export_queues_table(self):
        self._ensure_inner_tab_built(self.queues_tab)
        self.export_table('queues', self.queues_tab_table, self.queues_tab_table_title_list)

    def export_utilization_table(self):
        self._ensure_inner_tab_built(self.utilization_tab)
        self.export_table('utilization', self.utilization_tab_table, self.utilization_tab_table_title_list)

    def export_table(self, table_type, table_item, title_list):
        """
        Export specified table info into an csv file.
        """
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_time_string = re.sub('-', '', current_time)
        current_time_string = re.sub(':', '', current_time_string)
        current_time_string = re.sub(' ', '_', current_time_string)
        default_output_file = './' + str(self.tool.lower()) + '_' + str(table_type) + '_' + str(current_time_string) + '.csv'
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


class CheckIssueReason(QThread):
    """
    Start tool check_issue_reason to debug issue job.
    """
    def __init__(self, job='', issue='PEND'):
        super(CheckIssueReason, self).__init__()
        self.job = job
        self.issue = issue

    def run(self):
        command = [str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/tools/check_issue_reason', '-i', str(self.issue)]

        if self.job:
            common.bprint(f'Getting job {self.issue.lower()} reason for "{self.job}" ...', date_format='%Y-%m-%d %H:%M:%S')
            command += ['-j', str(self.job)]

        subprocess.run(command)


#################
# Main Function #
#################
def main():
    """Entry point: forward to the unified MainWindow (defaults to LSF panel).

    bmonitor keeps its own -j/-u/-f/-t/-d args (same names as the unified
    entry), so we delegate directly to gui.main_window.main() which focuses the
    LSF outer tab by default.
    """
    from gui import main_window

    main_window.main()


if __name__ == "__main__":
    main()
