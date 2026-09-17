# -*- coding: utf-8 -*-
################################
# File Name   : run_panel.py
# Author      : liyanqing.1987
# Created On  : 2026-08-24
# Description : RunPanel — lightweight batch shell-command execution across
#               cluster hosts. Embedded as an outer sidebar panel (between LICENSE
#               and AI). Two inner tabs: RUN (filter hosts by Queue/Group, run
#               commands, select/filter results by expression) and LOG (query
#               past command history).
#
# Host inventory source: LSF bhosts output (same as the LSF HOSTS tab), via
# context.get_lsf_host_info(). Assumes ssh passwordless access is configured.
################################

import os
import datetime

from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QIcon, QColor
from PyQt5.QtWidgets import (
    QAction, QDateEdit, QFileDialog, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QWidget,
)

from common import common, common_config, common_db_path, common_pyqt5, common_run_log
from gui.run_executor import RunThread
from gui.panel_base import PanelBase
from gui.theme import (
    TEXT_SECONDARY, TEXT_DISABLED,
    STATUS_RUN, STATUS_EXIT, STATUS_DONE,
)

from gui.version import USER


class RunPanel(PanelBase):
    """
    Batch shell-command execution panel. Inner tabs: RUN and LOG.
    """

    def __init__(self, context, parent=None, args=None):
        super().__init__(context, parent)

        self.config_run = common_config.load_config('run')

        # Initialize the run-history SQLite db file (shared, per-user tables).
        # resolve_db_path: config_run.db_path if set, else config.py db_path/run.
        run_db_path = common_db_path.resolve_db_path(self.config_run, 'run')
        self.run_log_db_file = common_run_log.init_run_log_db(run_db_path)

        self.run_thread = None
        self.result_dict = {}  # {host: {output, status, exit_code, duration, queue, groups}}
        self.host_inventory = {}  # {host: groups_str} from LSF bhosts
        self.ssh_target_dict = {}  # {host: ssh_connect_addr} (= hostname)
        self.lsf_host_info = {}  # {host: {status, queue, group}} same source as HOSTS tab
        self.queue_host_dic = {}  # {queue: [hosts]} derived from lsf_host_info
        self.group_host_dic = {}  # {group: [hosts]} derived from lsf_host_info

        self.init_ui()
        # Load host inventory after the widget is shown (uses LSF panel if fallback needed).
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self.refresh_hosts)

    def apply_entry_args(self, args):
        """No special entry args for the run panel."""
        return

    def register_menubar_actions(self, menubar, shared_menus=None):
        """Register export action into the shared File menu."""
        if not shared_menus:
            return

        file_menu = shared_menus['File']
        file_menu.addSeparator()

        export_action = QAction('Export RUN result table', self)
        export_action.setIcon(QIcon(str(os.environ.get('LSFMONITOR_INSTALL_PATH', ''))
                                    + '/data/pictures/save.png'))
        export_action.triggered.connect(self.export_result_table)
        file_menu.addAction(export_action)

        export_log_action = QAction('Export RUN log table', self)
        export_log_action.setIcon(QIcon(str(os.environ.get('LSFMONITOR_INSTALL_PATH', ''))
                                       + '/data/pictures/save.png'))
        export_log_action.triggered.connect(self.export_log_table)
        file_menu.addAction(export_log_action)

    # ======================================================================
    # UI construction
    # ======================================================================
    def init_ui(self):
        """Build the inner RUN / LOG tabs."""
        self.run_tab = QWidget()
        self.log_tab = QWidget()
        self.main_tab.addTab(self.run_tab, 'RUN')
        self.main_tab.addTab(self.log_tab, 'LOG')

        self.gen_run_tab()
        self.gen_log_tab()

    def gen_run_tab(self):
        """
        RUN tab layout (top to bottom):
            Timeout(s) [_30_]  Status [multi-select v]  Queue [multi-select v]  Group [multi-select v]  Command [________]  [Run]
            Select [____________expression (enter to apply)____________]
            Host | Status | Queue | Group | Output  (Host column has checkboxes)
        """
        def bold(text):
            lbl = QLabel(text)
            lbl.setStyleSheet('font-weight: bold;')
            return lbl

        layout = QGridLayout(self.run_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Row 0: Timeout(s) | Status | Queue | Group | Command | Run.
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        top_row.addWidget(bold('Timeout(s)'))
        self.timeout_input = QLineEdit()
        self.timeout_input.setFixedWidth(50)
        self.timeout_input.setText(str(getattr(self.config_run, 'parallel_timeout', 20)))
        top_row.addWidget(self.timeout_input)
        top_row.addSpacing(12)

        top_row.addWidget(bold('Status'))
        self.status_combo = common_pyqt5.QComboCheckBox(self.run_tab, enableFilter=True)
        self.status_combo.addCheckBoxItem('ALL')
        self.status_combo.setMinimumWidth(120)
        self.status_combo.currentTextChanged.connect(self._enforce_group_all)
        self.status_combo.currentTextChanged.connect(self._enqueue_apply_dimension_filter)
        top_row.addWidget(self.status_combo)
        top_row.addSpacing(12)

        top_row.addWidget(bold('Queue'))
        self.queue_combo = common_pyqt5.QComboCheckBox(self.run_tab, enableFilter=True)
        self.queue_combo.addCheckBoxItem('ALL')
        self.queue_combo.setMinimumWidth(200)
        self.queue_combo.currentTextChanged.connect(self._enforce_group_all)
        self.queue_combo.currentTextChanged.connect(self._enqueue_apply_dimension_filter)
        top_row.addWidget(self.queue_combo)
        top_row.addSpacing(12)

        top_row.addWidget(bold('Group'))
        self.group_combo = common_pyqt5.QComboCheckBox(self.run_tab, enableFilter=True)
        self.group_combo.addCheckBoxItem('ALL')
        self.group_combo.setMinimumWidth(200)
        self.group_combo.currentTextChanged.connect(self._enforce_queue_all)
        self.group_combo.currentTextChanged.connect(self._enqueue_apply_dimension_filter)
        top_row.addWidget(self.group_combo)
        top_row.addSpacing(12)

        top_row.addWidget(bold('Command'))
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText('Shell command to run on selected hosts')
        self.command_input.returnPressed.connect(self.run_command)
        top_row.addWidget(self.command_input, 1)

        self.run_button = QPushButton('Run')
        self.run_button.setFixedWidth(80)
        self.run_button.clicked.connect(self.run_command)
        top_row.addWidget(self.run_button)
        layout.addLayout(top_row, 0, 0)

        # Row 1: Select expression (run-style, matches rows by host/queue/group/status/output).
        select_row = QHBoxLayout()
        self.select_input = QLineEdit()
        self.select_input.setPlaceholderText(
            "e.g. 'error' in output  |  result == 'FAIL'  |  startswith(host, 'n01')  (Enter or Check to apply)")
        self.select_input.returnPressed.connect(self.apply_select)
        select_row.addWidget(bold('Select'))
        select_row.addWidget(self.select_input, 1)
        self.select_check_button = QPushButton('Check')
        self.select_check_button.setFixedWidth(80)
        self.select_check_button.clicked.connect(self.apply_select)
        select_row.addWidget(self.select_check_button)
        layout.addLayout(select_row, 1, 0)

        # Row 2: unified host/result table.
        # Columns: Host (with checkbox) | Status | Queue | Group | Output
        # Use Interactive (not ResizeToContents) with sensible initial widths,
        # because ResizeToContents collapses columns to header-size before the
        # first data load and users can't see them. Output gets Stretch to eat
        # the remaining width; auto_size_table_columns ensures no column drops
        # below its bold-header width.
        self.host_table = QTableWidget(0, 6)
        common_pyqt5.make_table_readonly(self.host_table)
        # Disable word wrap so long Queue/Group text truncates with an ellipsis
        # (single line) instead of wrapping to a second row — qdarkstyle's
        # dark-mode QSS widens cell padding, which made 130px-wide Queue cells
        # wrap long queue names onto two lines.
        self.host_table.setWordWrap(False)
        self.host_table.setHorizontalHeaderLabels(['Host', 'Status', 'Queue', 'Group', 'Result', 'Output'])
        # Show the row-number column (vertical header) so the user can see how
        # many hosts are listed / selected, mirroring the LSF-HOSTS tab.
        self.host_table.verticalHeader().setVisible(True)
        self.host_table.verticalHeader().setDefaultSectionSize(40)
        self.host_table.setAlternatingRowColors(True)
        self.host_table.setColumnWidth(0, 160)   # Host
        self.host_table.setColumnWidth(1, 115)   # Status (ok/closed_Busy/closed_Full/unreach)
        self.host_table.setColumnWidth(2, 130)   # Queue
        self.host_table.setColumnWidth(3, 140)   # Group
        self.host_table.setColumnWidth(4, 90)    # Result (OK/FAIL/TIMEOUT/UNREACH)
        self.host_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.host_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.host_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.host_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.host_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self.host_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        common_pyqt5.auto_size_table_columns(self.host_table)
        layout.addWidget(self.host_table, 2, 0)

        # Progress overlay: shown while a batch command is running, displaying
        # "done/total 执行中" centered over the table.
        self._progress_overlay = QLabel(self.run_tab)
        self._progress_overlay.setAlignment(Qt.AlignCenter)
        self._progress_overlay.setStyleSheet("""
            QLabel {
                background: rgba(15, 23, 42, 200);
                color: white;
                font-size: 16px;
                font-weight: bold;
                border-radius: 8px;
                padding: 20px 40px;
            }
        """)
        self._progress_overlay.hide()

        # Debounce timer so rapid checkbox toggles don't rebuild the table on each click.
        from PyQt5.QtCore import QTimer
        self._dim_filter_timer = QTimer(self)
        self._dim_filter_timer.setSingleShot(True)
        self._dim_filter_timer.timeout.connect(self._apply_dimension_filter)

    def gen_log_tab(self):
        """LOG tab: search command history (jsonl) by user/date/keyword, with
        a right-side log detail panel — mirrors the run LOG tab layout."""
        def bold(text):
            lbl = QLabel(text)
            lbl.setStyleSheet('font-weight: bold;')

            return lbl

        layout = QGridLayout(self.log_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Filter row.
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(bold('User'))
        self.log_user_input = QLineEdit()
        self.log_user_input.setText(USER)
        self.log_user_input.setPlaceholderText('Filter by user (empty = all)')
        filter_row.addWidget(self.log_user_input)
        filter_row.addSpacing(12)

        filter_row.addWidget(bold('From'))
        self.log_from_date = QDateEdit()
        self.log_from_date.setCalendarPopup(True)
        self.log_from_date.setMinimumWidth(120)
        self.log_from_date.setDate(QDate.currentDate().addDays(-7))
        self.log_from_date.setDisplayFormat('yyyy-MM-dd')
        filter_row.addWidget(self.log_from_date)
        filter_row.addSpacing(12)

        filter_row.addWidget(bold('To'))
        self.log_to_date = QDateEdit()
        self.log_to_date.setCalendarPopup(True)
        self.log_to_date.setMinimumWidth(120)
        self.log_to_date.setDate(QDate.currentDate())
        self.log_to_date.setDisplayFormat('yyyy-MM-dd')
        filter_row.addWidget(self.log_to_date)
        filter_row.addSpacing(12)

        filter_row.addWidget(bold('Keyword'))
        self.log_keyword_input = QLineEdit()
        self.log_keyword_input.setPlaceholderText('Keyword in command')
        self.log_keyword_input.returnPressed.connect(self.query_log)
        filter_row.addWidget(self.log_keyword_input, 1)

        self.log_search_button = QPushButton('Search')
        self.log_search_button.clicked.connect(self.query_log)
        filter_row.addWidget(self.log_search_button)
        layout.addLayout(filter_row, 0, 0)

        # Left: history table; Right: log detail text.
        self.log_table = QTableWidget(0, 5)
        common_pyqt5.make_table_readonly(self.log_table)
        self.log_table.setHorizontalHeaderLabels(['Date', 'User', 'Login_User', 'Command', 'Log'])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setColumnWidth(0, 155)
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.log_table.setColumnWidth(4, 40)
        self.log_table.itemClicked.connect(self._on_log_row_clicked)
        common_pyqt5.auto_size_table_columns(self.log_table)

        self.log_detail_text = QTextEdit()
        self.log_detail_text.setReadOnly(True)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.log_table)
        splitter.addWidget(self.log_detail_text)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1, 0)

        self.log_dic_list = []

        # Auto-load recent history on first show (local DB, fast).
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self.query_log)

    def _on_log_row_clicked(self, item):
        """Click a row in the log table → show the log file content on the right."""
        row = item.row()

        if row < 0 or row >= len(self.log_dic_list):
            return

        log_file = self.log_dic_list[row].get('log', '')

        if log_file and os.path.exists(log_file):
            try:
                with open(log_file, 'r') as LF:
                    self.log_detail_text.setPlainText(LF.read())
            except Exception as warning:
                self.log_detail_text.setPlainText(f'Failed to read log: {warning}')
        else:
            # No log file — show the command + output summary from the record.
            entry = self.log_dic_list[row]
            summary = f"Command: {entry.get('command', '')}\n"
            summary += f"User: {entry.get('user', '')} (login: {entry.get('login_user', '')})\n"
            summary += f"Date: {entry.get('date', '')} {entry.get('time', '')}\n"

            if entry.get('hosts'):
                summary += f"Hosts: {', '.join(entry.get('hosts', [])[:20])}\n"

            self.log_detail_text.setPlainText(summary)

    # ======================================================================
    # Host inventory + Queue/Group combo population
    # ======================================================================
    def refresh_hosts(self):
        """Reload host inventory + LSF host info, rebuild combos and table.

        Hosts come exclusively from the LSF HOSTS tab (bhosts output) via
        context.get_lsf_host_info(); no external host.list is consulted. The
        ssh target equals the hostname.
        """
        self.lsf_host_info = self.context.get_lsf_host_info() if self.context.lsf_panel is not None else {}
        self.host_inventory = {host: '' for host in self.lsf_host_info.keys()}
        self.ssh_target_dict = {host: host for host in self.host_inventory.keys()}

        self._load_lsf_host_info()
        self._populate_combos()
        self._apply_dimension_filter()

    def _load_lsf_host_info(self):
        """Derive queue/group reverse mappings from the LSF host info already
        loaded in refresh_hosts(). No external host.list is consulted."""
        self.queue_host_dic = {}
        self.group_host_dic = {}

        for host, info in self.lsf_host_info.items():
            for queue in info.get('queue', '').split():
                if queue:
                    self.queue_host_dic.setdefault(queue, [])

                    if host not in self.queue_host_dic[queue]:
                        self.queue_host_dic[queue].append(host)

            for group in info.get('group', '').split():
                if group:
                    self.group_host_dic.setdefault(group, [])

                    if host not in self.group_host_dic[group]:
                        self.group_host_dic[group].append(host)

    def _populate_combos(self):
        """(Re)populate Status/Queue/Group QComboCheckBox items, keeping current selections when possible."""
        # Status combo — gather all status values from LSF host info.
        prev_statuses = list(self.status_combo.selectedItems().values()) if hasattr(self, 'status_combo') else []
        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        self.status_combo.addCheckBoxItem('ALL')

        status_set = set()

        for info in self.lsf_host_info.values():
            status = info.get('status', '')

            if status:
                status_set.add(status)

        for s in sorted(status_set):
            self.status_combo.addCheckBoxItem(s)

        self._restore_combo_selection(self.status_combo, prev_statuses, default_all=True)
        self.status_combo.blockSignals(False)

        # Queue combo.
        prev_queues = list(self.queue_combo.selectedItems().values()) if hasattr(self, 'queue_combo') else []
        self.queue_combo.blockSignals(True)
        self.queue_combo.clear()
        self.queue_combo.addCheckBoxItem('ALL')

        for q in sorted(self.queue_host_dic.keys()):
            self.queue_combo.addCheckBoxItem(q)

        # Restore previous selection or default ALL.
        self._restore_combo_selection(self.queue_combo, prev_queues, default_all=True)
        self.queue_combo.blockSignals(False)

        # Group combo — gather groups from LSF host_group info (same source as
        # HOSTS tab); one host can be in multiple space-separated groups.
        prev_groups = list(self.group_combo.selectedItems().values()) if hasattr(self, 'group_combo') else []
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addCheckBoxItem('ALL')

        for g in sorted(self.group_host_dic.keys()):
            self.group_combo.addCheckBoxItem(g)

        self._restore_combo_selection(self.group_combo, prev_groups, default_all=True)
        self.group_combo.blockSignals(False)

    @staticmethod
    def _restore_combo_selection(combo, want_items, default_all=True):
        """Check the items in ``combo`` whose text appears in ``want_items``; fall back to ALL."""
        available = {cb.text() for cb in combo.checkBoxList}
        to_check = [t for t in want_items if t in available]

        if not to_check:
            to_check = ['ALL'] if default_all else []

        for cb in combo.checkBoxList:
            cb.setChecked(cb.text() in to_check)

    # ======================================================================
    # Queue/Group mutual exclusion (mirrors UTILIZATION tab behavior).
    # ======================================================================
    @staticmethod
    def _combo_has_non_all(combo):
        for text in combo.selectedItems().values():
            if text != 'ALL':
                return True

        return False

    @staticmethod
    def _reset_combo_to_all(combo):
        for (i, cb) in enumerate(combo.checkBoxList):
            target = (cb.text() == 'ALL')

            if cb.isChecked() != target:
                combo.checkBoxList[i].setChecked(target)

    def _enforce_group_all(self):
        if self._combo_has_non_all(self.queue_combo):
            self._reset_combo_to_all(self.group_combo)

    def _enforce_queue_all(self):
        if self._combo_has_non_all(self.group_combo):
            self._reset_combo_to_all(self.queue_combo)

    def _enqueue_apply_dimension_filter(self):
        """Debounce table rebuild while the user is ticking checkboxes."""
        self._dim_filter_timer.start(80)

    def _selected_queues(self):
        """Return the set of queues selected in the combo.

        Returns ``None`` for ALL (no filter — every host passes); an empty set
        when the user unchecked everything (no host passes); otherwise the
        selected queue names.
        """
        sel = {t for t in self.queue_combo.selectedItems().values() if t}

        if 'ALL' in sel:
            return None

        return sel

    def _selected_groups(self):
        sel = {t for t in self.group_combo.selectedItems().values() if t}

        if 'ALL' in sel:
            return None

        return sel

    def _selected_statuses(self):
        sel = {t for t in self.status_combo.selectedItems().values() if t}

        if 'ALL' in sel:
            return None

        return sel

    def _host_status(self, host):
        """Return the LSF bhosts status of this host."""
        info = self.lsf_host_info.get(host, {})

        return info.get('status', '')

    def _host_queues(self, host):
        """Return the set of queues this host belongs to (from LSF)."""
        info = self.lsf_host_info.get(host, {})
        return {q for q in info.get('queue', '').split() if q}

    def _host_groups(self, host):
        """Return the set of LSF host groups this host belongs to."""
        info = self.lsf_host_info.get(host, {})

        return {g for g in info.get('group', '').split() if g}

    def _apply_dimension_filter(self):
        """Rebuild the table rows based on current Status/Queue/Group selection.

        None means ALL (no filter); an empty set means the user unchecked every
        item, so no host matches and the table shows no rows.
        """
        statuses = self._selected_statuses()
        queues = self._selected_queues()
        groups = self._selected_groups()

        hosts = []

        for host in self.host_inventory.keys():
            if statuses is not None and self._host_status(host) not in statuses:
                continue

            host_queues = self._host_queues(host)
            host_groups = self._host_groups(host)

            if queues is not None and not (host_queues & queues):
                continue

            if groups is not None and not (host_groups & groups):
                continue

            hosts.append(host)

        self._populate_host_table(hosts)

    # ======================================================================
    # Table rendering
    # ======================================================================
    def _populate_host_table(self, hosts):
        """Fill the table with ``hosts``; Status/Queue/Group from LSF bhosts.

        Status shows the LSF bhosts host status (ok/closed/unreach) and is never
        overwritten by command results. Result (OK/FAIL/TIMEOUT/UNREACH) and
        Output are filled by on_host_finished after a command runs.
        """
        self.host_table.setRowCount(0)
        self.host_table.setRowCount(len(hosts))
        # Secondary text color (slate-600) — visible but not loud, unlike
        # TEXT_DISABLED which is too faint to read against white.
        dim_color = QColor(TEXT_SECONDARY)

        for row, host in enumerate(hosts):
            # Host column: checkbox + host text via editable item's setCheckState.
            host_item = QTableWidgetItem(host)
            host_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            host_item.setCheckState(Qt.Checked)
            self.host_table.setItem(row, 0, host_item)

            info = self.lsf_host_info.get(host, {})

            # Status: LSF bhosts status (ok/closed/unreach) — never overwritten.
            status = info.get('status', '') or '-'
            status_item = QTableWidgetItem(status)
            status_item.setForeground(dim_color)
            self.host_table.setItem(row, 1, status_item)

            # Queue: from LSF only ('-' if the host has no queue).
            queue = info.get('queue', '') or '-'
            queue_item = QTableWidgetItem(queue)

            if queue == '-':
                queue_item.setForeground(dim_color)

            self.host_table.setItem(row, 2, queue_item)

            # Group: from LSF host_group info ('-' if none).
            groups = info.get('group', '') or '-'
            group_item = QTableWidgetItem(groups)

            if groups == '-':
                group_item.setForeground(dim_color)

            self.host_table.setItem(row, 3, group_item)

            # Result: empty until a command runs (on_host_finished fills it).
            self.host_table.setItem(row, 4, QTableWidgetItem(''))

            # Output: empty until a command runs.
            self.host_table.setItem(row, 5, QTableWidgetItem(''))

        # Re-apply any active Select expression so checked/visible state tracks the filter.
        self.apply_select()

    def _row_for_host(self, host):
        for row in range(self.host_table.rowCount()):
            item = self.host_table.item(row, 0)

            if item is not None and item.text() == host:
                return row

        return -1

    # ======================================================================
    # Run / Stop
    # ======================================================================
    def run_command(self):
        """Start batch execution on the checked (visible) hosts."""
        if self.run_thread and self.run_thread.isRunning():
            return

        command = self.command_input.text().strip()

        if not command:
            QMessageBox.warning(self, 'Warning', 'Please input a command.')
            return

        hosts_list = []

        for row in range(self.host_table.rowCount()):
            if self.host_table.isRowHidden(row):
                continue

            item = self.host_table.item(row, 0)

            if item is not None and item.checkState() == Qt.Checked:
                hosts_list.append(item.text())

        if not hosts_list:
            QMessageBox.warning(self, 'Warning', 'No host selected.')
            return

        try:
            timeout = int(self.timeout_input.text().strip())
        except ValueError:
            timeout = getattr(self.config_run, 'parallel_timeout', 20)

        max_parallel = getattr(self.config_run, 'max_parallel', 512)
        default_ssh_command = getattr(self.config_run, 'default_ssh_command', None)

        # Reset results for the hosts we are about to (re)run.
        # Status (col 1) stays as the LSF bhosts status — it is never overwritten.
        self.result_dict = {}
        targets = set(hosts_list)

        for row in range(self.host_table.rowCount()):
            host_item = self.host_table.item(row, 0)

            if host_item is None:
                continue

            if host_item.text() not in targets:
                continue

            result_item = self.host_table.item(row, 4)

            if result_item is not None:
                result_item.setText('')
                result_item.setForeground(QColor(TEXT_DISABLED))

            output_item = self.host_table.item(row, 5)

            if output_item is not None:
                output_item.setText('')

        self.run_button.setEnabled(False)

        # Show progress overlay.
        self._update_progress_overlay(0, len(hosts_list))
        self._progress_overlay.show()
        self._progress_overlay.raise_()

        self.run_thread = RunThread(
            hosts_list, command, timeout, max_parallel,
            ssh_targets=self.ssh_target_dict, ssh_command_base=default_ssh_command, parent=self,
        )
        self.run_thread.host_finished.connect(self.on_host_finished)
        self.run_thread.progress.connect(self.on_progress)
        self.run_thread.all_finished.connect(self.on_all_finished)
        self.run_thread.start()

        self._record_history_start(command, hosts_list)

    def _update_progress_overlay(self, done, total):
        """Center the progress overlay over the host table and update its text."""
        self._progress_overlay.setText(f'{done}/{total} 执行中')
        self._progress_overlay.adjustSize()

        table_rect = self.host_table.rect()
        label_w = self._progress_overlay.width()
        label_h = self._progress_overlay.height()
        x = table_rect.center().x() - label_w // 2
        y = table_rect.center().y() - label_h // 2
        self._progress_overlay.setGeometry(x, y, label_w, label_h)

    def on_host_finished(self, host, result):
        """Update a finished host's Result + Output columns in-place.

        Status (col 1) is the LSF bhosts status and is never overwritten by
        command results.
        """
        info = self.lsf_host_info.get(host, {})
        result['groups'] = info.get('group', '')
        result['queue'] = info.get('queue', '')
        self.result_dict[host] = result

        row = self._row_for_host(host)

        if row < 0:
            return

        # Result column (col 4): ssh execution outcome (OK/FAIL/TIMEOUT/...).
        result_text = result.get('status', '')
        result_item = QTableWidgetItem(result_text)
        result_item.setForeground(QColor(self._status_color(result_text)))
        self.host_table.setItem(row, 4, result_item)

        # Output column (col 5): command stdout.
        output_item = QTableWidgetItem(result.get('output', ''))
        self.host_table.setItem(row, 5, output_item)

    @staticmethod
    def _status_color(status):
        return {
            'OK': STATUS_RUN,
            'TIMEOUT': STATUS_EXIT,
            'FAIL': STATUS_EXIT,
            'UNREACH': STATUS_EXIT,
            'NO_PEXPECT': STATUS_DONE,
        }.get(status, TEXT_SECONDARY)

    def on_progress(self, done, total):
        """Update progress overlay while running."""
        self._update_progress_overlay(done, total)

    def on_all_finished(self):
        """All hosts done: reset buttons, hide overlay, write history."""
        self.run_button.setEnabled(True)
        self._progress_overlay.hide()
        self._record_history_end()

        # Apply any active Select expression now that results are ready.
        if self.select_input.text().strip():
            self.apply_select(force=True)

    # ======================================================================
    # Select expression (run-style): matched rows are visible+checked,
    # mismatched rows are hidden+unchecked. Empty expression → show+check all.
    # ======================================================================
    def apply_select(self, force=False):
        # Not while a batch is running — results are incomplete, re-applying
        # mid-run would hide/check rows whose output hasn't arrived yet.
        # force=True bypasses this for the all-finished callback (run() has
        # returned but QThread.isRunning() may still be briefly True).
        if not force and self.run_thread and self.run_thread.isRunning():
            return

        expr = self.select_input.text().strip()

        if not expr:
            for row in range(self.host_table.rowCount()):
                self.host_table.setRowHidden(row, False)
                cb = self.host_table.item(row, 0)

                if cb is not None:
                    cb.setCheckState(Qt.Checked)

            return

        err = False

        for row in range(self.host_table.rowCount()):
            host_item = self.host_table.item(row, 0)
            host = host_item.text() if host_item is not None else ''
            result = self.result_dict.get(host, {})

            status_item = self.host_table.item(row, 1)
            queue_item = self.host_table.item(row, 2)
            group_item = self.host_table.item(row, 3)
            result_item = self.host_table.item(row, 4)
            output_item = self.host_table.item(row, 5)

            names = {
                'host': host,
                'host_name': host,
                'Host': host,
                'queue': queue_item.text() if queue_item is not None else '',
                'Queue': queue_item.text() if queue_item is not None else '',
                'groups': group_item.text() if group_item is not None else '',
                'Groups': group_item.text() if group_item is not None else '',
                'status': status_item.text() if status_item is not None else '',
                'Status': status_item.text() if status_item is not None else '',
                'result': result_item.text() if result_item is not None else '',
                'Result': result_item.text() if result_item is not None else '',
                'output': output_item.text() if output_item is not None else '',
                'Output': output_item.text() if output_item is not None else '',
                'output_message': output_item.text() if output_item is not None else '',
                'duration': result.get('duration', 0.0),
                'exit_code': result.get('exit_code', -1),
            }

            try:
                matched = bool(common.safe_eval_expr(expr, names))
            except Exception:
                matched = True
                err = True

            self.host_table.setRowHidden(row, not matched)

            if host_item is not None:
                host_item.setCheckState(Qt.Checked if matched else Qt.Unchecked)

        if err:
            common.bprint(
                f'Invalid Select expression "{expr}" — showing all rows.',
                level='Warning',
            )

    # ======================================================================
    # Export
    # ======================================================================
    def export_result_table(self):
        """Export the visible rows that have results to CSV."""
        current_time_string = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        default_output_file = './run_result_' + current_time_string + '.csv'
        csv_file, _ = QFileDialog.getSaveFileName(
            self, 'Export result table', default_output_file, 'CSV (*.csv)')

        if not csv_file:
            return

        title_list = ['Host', 'Status', 'Queue', 'Group', 'Result', 'Output']
        content_dic = {t: [] for t in title_list}

        for row in range(self.host_table.rowCount()):
            if self.host_table.isRowHidden(row):
                continue

            status_item = self.host_table.item(row, 1)

            if status_item is None or status_item.text() in ('', '-'):
                continue

            for col, title in enumerate(title_list):
                item = self.host_table.item(row, col)
                content_dic[title].append(item.text() if item is not None else '')

        common.write_csv(csv_file, content_dic)

    def export_log_table(self):
        """Export the RUN-LOG history table to CSV."""
        current_time_string = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        default_output_file = './run_log_' + current_time_string + '.csv'
        csv_file, _ = QFileDialog.getSaveFileName(
            self, 'Export log table', default_output_file, 'CSV (*.csv)')

        if not csv_file:
            return

        title_list = ['Date', 'User', 'Login_User', 'Command', 'Log']
        content_dic = {t: [] for t in title_list}

        for row in range(self.log_table.rowCount()):
            if self.log_table.isRowHidden(row):
                continue

            for col, title in enumerate(title_list):
                item = self.log_table.item(row, col)
                content_dic[title].append(item.text() if item is not None else '')

        common.write_csv(csv_file, content_dic)

    # ======================================================================
    # Command history
    # ======================================================================
    def _record_history_start(self, command, hosts_list):
        self._history_meta = {
            'date': datetime.datetime.now().strftime('%Y%m%d'),
            'time': datetime.datetime.now().strftime('%H%M%S'),
            'user': USER,
            'login_user': USER,
            'command': command,
            'hosts': hosts_list,
            'log': '',
        }

    def _history_log_root(self):
        """Return the run history db root: <db_path>.

        run_log.db lives at <db_path>/run_log.db; per-run detail .log files
        live at <db_path>/<user>/<date>_<time>.log. Resolved via
        common_db_path.resolve_db_path so the per-run .log lands in the SAME
        root as run_log.db (config_run.db_path > config.py db_path/run >
        <install>/db/run). Previously this used a divergent fallback that
        skipped config.py's db_path, so .log files landed under <install>/db/run
        even when config.py pointed db_path elsewhere.
        """
        from common import common_db_path

        return str(common_db_path.resolve_db_path(self.config_run, 'run'))

    def _record_history_end(self):
        meta = getattr(self, '_history_meta', None)

        if not meta:
            return

        log_dir = os.path.join(self._history_log_root(), USER)

        try:
            # NOTE: do NOT use common.create_dir here —
            # it calls sys.exit(1) on failure, which would kill the whole GUI
            # just because a per-user history log dir is not writable. Use
            # makedirs/open directly and degrade gracefully (history is a
            # convenience, not a critical function).
            os.makedirs(log_dir, exist_ok=True)

            # 0o700: 每个用户的命令明细日志目录仅本人可读写。
            os.chmod(log_dir, 0o700)

            # Write the per-run detail log (one section per host: status +
            # stdout) so the RUN-LOG table can show a magnifier icon and the
            # right pane can display full output. Named <date>_<time>.log;
            # its path is stored in run_log.db for the LOG tab to open.
            log_file = os.path.join(log_dir, f"{meta.get('date', '')}_{meta.get('time', '')}.log")

            try:
                with open(log_file, 'w') as LF:
                    LF.write(f"Command: {meta.get('command', '')}\n")
                    LF.write(f"User: {meta.get('user', '')} (login: {meta.get('login_user', '')})\n")
                    LF.write(f"Date: {meta.get('date', '')} {meta.get('time', '')}\n")
                    LF.write(f"Hosts: {len(meta.get('hosts', []))}\n")
                    LF.write('=' * 60 + '\n')

                    result_dict = getattr(self, 'result_dict', {})

                    for host in meta.get('hosts', []):
                        result = result_dict.get(host, {})
                        status = result.get('status', 'N/A')
                        output = result.get('output', '')

                        LF.write(f"\n[{host}] {status}\n")

                        if output:
                            LF.write(output.rstrip() + '\n')
                        else:
                            LF.write('(no output)\n')

                # 0o600: 明细日志含命令输出,仅本人可读写。
                os.chmod(log_file, 0o600)
                meta['log'] = log_file
            except Exception as warning:
                common.bprint(f'Failed to write run detail log: {warning}', level='Warning')

            # Write the history index to SQLite (replaces the old command.his
            # JSONL append). The detail .log path is stored so the LOG tab can
            # open it on row click.
            common_run_log.save_run_history(
                db_file=self.run_log_db_file,
                user=meta.get('user', '') or USER,
                date=meta.get('date', ''),
                time=meta.get('time', ''),
                login_user=meta.get('login_user', ''),
                command=meta.get('command', ''),
                hosts_list=meta.get('hosts', []),
                log_path=meta.get('log', ''),
            )
        except Exception as warning:
            common.bprint(f'Failed to write command history: {warning}', level='Warning')
        finally:
            self._history_meta = None

    def query_log(self):
        """Search command history (SQLite) by user/date/keyword."""
        user_filter = self.log_user_input.text().strip()
        from_date = self.log_from_date.date().toString('yyyyMMdd')
        to_date = self.log_to_date.date().toString('yyyyMMdd')
        keyword = self.log_keyword_input.text().strip()

        self.log_table.setRowCount(0)
        self.log_dic_list = []

        entry_list = common_run_log.search_run_history(
            db_file=self.run_log_db_file,
            user=user_filter,
            date_start=from_date,
            date_end=to_date,
            keyword=keyword,
        )

        for entry in entry_list:
            self.log_dic_list.append(entry)

            row = self.log_table.rowCount()
            self.log_table.insertRow(row)
            self.log_table.setItem(row, 0, QTableWidgetItem(f"{entry.get('date', '')} {entry.get('time', '')}"))
            self.log_table.setItem(row, 1, QTableWidgetItem(entry.get('user', '')))
            self.log_table.setItem(row, 2, QTableWidgetItem(entry.get('login_user', '')))
            self.log_table.setItem(row, 3, QTableWidgetItem(entry.get('command', '')))

            # Log icon column (col 4). Magnifier = a log file exists
            # (click the row to view it on the right); '-' = no log
            # file (click shows the command/output summary instead).
            log_file = entry.get('log', '')

            if log_file and os.path.exists(log_file):
                log_item = QTableWidgetItem()
                log_item.setIcon(QIcon(str(os.environ.get('LSFMONITOR_INSTALL_PATH', '')) + '/data/pictures/magnifier.png'))
                self.log_table.setItem(row, 4, log_item)
            else:
                self.log_table.setItem(row, 4, QTableWidgetItem('-'))
