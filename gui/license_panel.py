# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import copy
import shlex
import datetime

from PyQt5.QtWidgets import QWidget, QAction, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit, QHeaderView, QDateEdit, QFileDialog, QApplication
from PyQt5.QtGui import QIcon, QBrush, QColor, QFont
from PyQt5.QtCore import Qt, QThread, QDate, pyqtSignal

# Project root. License subsystem data lives under config/ (shared with other
# panels); tools live under tools/. No separate license/ subsystem root anymore.
_LSFMONITOR_INSTALL_PATH = os.environ['LSFMONITOR_INSTALL_PATH']

if _LSFMONITOR_INSTALL_PATH not in sys.path:
    sys.path.append(_LSFMONITOR_INSTALL_PATH)

from common import common
from common import common_config
from common import common_db_path
from common import common_pyqt5
from common import common_license
from common import common_sqlite3
from gui.panel_base import PanelBase
from gui.theme import STATUS_EXIT, STATUS_DONE
from gui import theme

# Shared loading-prompt QThread (tracked subprocess + safe terminate()).
ShowMessage = common_pyqt5.ShowMessage

# License config loaded via the unified loader into sys.modules['config_license'];
# bound to the local name `config` used throughout this file.
config = common_config.load_config('license')

from gui.version import USER

os.environ['PYTHONUNBUFFERED'] = '1'

# Solve some unexpected warning message.
if 'XDG_RUNTIME_DIR' not in os.environ:
    os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-' + str(USER)

    if not os.path.exists(os.environ['XDG_RUNTIME_DIR']):
        os.makedirs(os.environ['XDG_RUNTIME_DIR'])
        os.chmod(os.environ['XDG_RUNTIME_DIR'], 0o1777)


class LicensePanel(PanelBase):
    """
    License monitoring panel. Embedded as an outer tab in the unified MainWindow.
    Owns an inner QTabWidget (SERVER/FEATURE/EXPIRES/USAGE/CURVE/UTILIZATION).
    """

    def __init__(self, context, parent=None, args=None):
        super().__init__(context, parent)

        self.args = args
        self.specified_user = getattr(args, 'user', '') if args else ''
        self.specified_tab = getattr(args, 'tab', 'FEATURE') if args else 'FEATURE'
        self.dark_mode = getattr(args, 'dark_mode', False) if args else False

        # Get administrator list, check admin permission.
        common.bprint('Check admin permission', date_format='%Y-%m-%d %H:%M:%S')

        if hasattr(config, 'administrators') and config.administrators:
            self.administrator_list = config.administrators.split()

            if ('all' not in self.administrator_list) and ('ALL' not in self.administrator_list) and (USER not in self.administrator_list):
                common.bprint('You are not administrator, certain functions is prohibited!', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
        else:
            common.bprint('You are not administrator, certain functions is prohibited!', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            self.administrator_list = []

        # Initialization for class variables.
        self.license_dic = {}
        self.license_dic_second = 0
        self.feature_list = []
        self.user_list = []
        self.db_dic = {}
        self.enable_utilization_detail = False

        # Background license-data loader. Empty license_dic until the first
        # load completes; inner tabs are built against {} (empty tables) and
        # repopulated once data arrives via _on_license_loaded().
        self._license_load_thread = None

        # Generate GUI (builds inner tabs; menubar handled by host). Does NOT
        # fetch license data here -- the fetch runs in on_first_show() via a
        # background thread so the panel renders immediately.
        self.init_ui()

    def on_first_show(self):
        """Called once when the panel first becomes visible.

        Kicks off the (slow) lmstat fetch in a background thread so the panel
        has already painted its empty tables + loading overlay instead of
        blocking the previous panel on screen.
        """
        self._start_license_load()

    def _start_license_load(self):
        """Launch the background license-data fetch (lmstat).

        Shows the loading overlay and runs GetLicenseInfo in a QThread. The
        thread emits ``loaded`` (with the resulting license dict) when done,
        which is marshalled back to the GUI thread via a signal so table
        population happens on the GUI thread.
        """
        # Avoid overlapping loads if the user navigates away and back quickly.
        if self._license_load_thread is not None:
            return

        # Refresh control: skip if already fresh within fresh_interval, but only
        # when we already have data. First load always runs.
        if self.license_dic and (not self._license_data_stale()):
            self._on_license_loaded(self.license_dic)

            return

        common.bprint('Load license info (background) ...', date_format='%Y-%m-%d %H:%M:%S')

        self._show_loading_overlay()

        thread = LicenseLoadThread(self)
        # Keep a reference so the QThread is not GC'd while running (which would
        # abort with "QThread: Destroyed while thread is still running"), and
        # clear it only after the thread has fully finished.
        self._license_load_thread = thread
        thread.loaded.connect(self._on_license_loaded)
        thread.finished.connect(self._on_license_thread_finished)
        thread.start()

    def _on_license_thread_finished(self):
        """Clear the thread reference once QThread has fully finished.

        Called via the finished signal (delivered on the GUI thread) after run()
        returns. Clearing ``_license_load_thread`` here — rather than inside
        _on_license_loaded — guarantees the QThread object stays alive until it
        is truly done, preventing premature destruction during quick
        LSF<->LICENSE panel switching.
        """
        self._license_load_thread = None

    def _license_data_stale(self):
        """Return True if the cached license_dic is older than fresh_interval."""
        if hasattr(config, 'fresh_interval') and config.fresh_interval:
            return (int(time.time()) - self.license_dic_second) > int(config.fresh_interval)

        return True

    def _show_loading_overlay(self):
        """Size and reveal the loading overlay across the panel."""
        self._resize_loading_overlay()
        self._license_loading_label.raise_()
        self._license_loading_label.show()

    def _resize_loading_overlay(self):
        self._license_loading_label.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, event):
        """Keep the loading overlay centered when the panel is resized."""
        super().resizeEvent(event)

        if hasattr(self, '_license_loading_label') and self._license_loading_label is not None:
            self._resize_loading_overlay()

    def _on_license_loaded(self, license_dic):
        """GUI-thread slot: store fetched data, populate tables, apply filters.

        Connected to LicenseLoadThread.loaded. Runs on the GUI thread (Qt
        marshals cross-thread signal delivery) so it is safe to touch widgets.
        The thread reference is cleared separately in _on_license_thread_finished
        (on the finished signal) to keep the QThread alive until it is done.
        """
        self.license_dic = license_dic or {}
        self.license_dic_second = int(time.time())

        if not self.license_dic:
            common.bprint('Not find any valid license information.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

        # Derive feature/user/product lists now that data is available.
        (self.feature_list, self.user_list) = self.get_feature_and_user_list()

        # Refresh every inner table with the freshly loaded data.
        self.gen_server_tab_table()
        self.gen_feature_tab_table(self.license_dic)
        self.gen_expires_tab_table(self.license_dic)
        self.gen_usage_tab_table(self.license_dic)

        # Re-populate the server/vendor combos that were built against the
        # still-empty license_dic during init_ui().
        self.set_feature_tab_server_combo()
        self.set_feature_tab_vendor_combo()
        self.set_expires_tab_server_combo()
        self.set_expires_tab_vendor_combo()
        self.set_usage_tab_server_combo()
        self.set_usage_tab_vendor_combo()
        self.set_usage_tab_submit_host_combo()
        self.set_usage_tab_execute_host_combo()

        # Admin-only tabs (CURVE/UTILIZATION) combos were also built with an
        # empty license_dic; refresh them now if they exist.
        if hasattr(self, 'curve_tab_server_combo'):
            self.set_curve_tab_server_combo()
            self.set_curve_tab_vendor_combo()

        if hasattr(self, 'utilization_tab_server_combo'):
            self.set_utilization_tab_server_combo()
            self.set_utilization_tab_vendor_combo()

        # Refresh feature/user line completers with the now-available lists.
        self.feature_tab_feature_line.setCompleter(common_pyqt5.get_completer(self.feature_list))
        self.expires_tab_feature_line.setCompleter(common_pyqt5.get_completer(self.feature_list))
        self.usage_tab_feature_line.setCompleter(common_pyqt5.get_completer(self.feature_list))
        self.usage_tab_user_line.setCompleter(common_pyqt5.get_completer(self.user_list))

        # Admin-only tabs (CURVE/UTILIZATION) feature lines were built
        # with an empty feature_list; refresh their completers.
        if hasattr(self, 'curve_tab_feature_line'):
            self.curve_tab_feature_line.setCompleter(common_pyqt5.get_completer(self.feature_list))

        if hasattr(self, 'utilization_tab_feature_line'):
            self.utilization_tab_feature_line.setCompleter(common_pyqt5.get_completer(self.feature_list))

        # Hide the loading overlay.
        self._license_loading_label.hide()

        # Apply entry args (-f/-u) now that license_dic is populated.
        self.apply_entry_args(self.args)

    def cleanup(self):
        """Stop the background loader if still running on application exit."""
        thread = self._license_load_thread

        if thread is not None:
            thread.wait(3000)
            self._license_load_thread = None

    def apply_entry_args(self, args):
        """Apply entry -f/-u/-t args after the panel is built and shown.

        Requires license data to be loaded (filters read self.license_dic).
        When called before the background load completes (e.g. from MainWindow
        at startup for the license_monitor entry), this is a no-op and the real
        application is performed by _on_license_loaded() once data arrives.
        """
        if not self.license_dic:
            return

        # -u/-f are nargs='+' lists now; join to a space-separated string so
        # the filter inputs (which .split() on whitespace) keep working.
        specified_feature = ' '.join(getattr(args, 'feature', []) or [])
        specified_user = ' '.join(getattr(args, 'user', []) or [])
        specified_tab = getattr(args, 'tab', '')

        # Default inner tab: FEATURE for -f, USAGE for -u, FEATURE otherwise.
        if not specified_tab:
            if specified_feature:
                specified_tab = 'FEATURE'
            elif specified_user:
                specified_tab = 'USAGE'
            else:
                specified_tab = 'FEATURE'

        is_admin = (
            ('all' in self.administrator_list) or
            ('ALL' in self.administrator_list) or
            (USER in self.administrator_list)
        )

        # Pre-set feature.
        if specified_feature:
            self.feature_tab_feature_line.setText(specified_feature)
            self.expires_tab_feature_line.setText(specified_feature)
            self.usage_tab_feature_line.setText(specified_feature)

            if is_admin:
                if hasattr(self, 'curve_tab_feature_line'):
                    self.curve_tab_feature_line.setText(specified_feature)
                self.utilization_tab_feature_line.setText(specified_feature)

        # Pre-set user.
        if specified_user:
            self.usage_tab_user_line.setText(specified_user)

        # For pre-set feature or pre-set user, apply filters.
        # License data is already loaded (by _on_license_loaded before this is
        # called), so we do NOT force a second fetch here.
        if specified_feature or specified_user:
            if specified_feature:
                self.filter_feature_tab_license_feature(get_license_info=False)
                self.filter_expires_tab_license_feature(get_license_info=False)
                self.filter_usage_tab_license_feature(get_license_info=False)

                if is_admin:
                    if hasattr(self, 'curve_tab_feature_line'):
                        self.filter_curve_tab()
                    self.filter_utilization_tab()

            if specified_user and (not specified_feature):
                self.filter_usage_tab_license_feature(get_license_info=False)

        # For pre-set tab.
        self.switch_tab(specified_tab)

    def get_license_dic(self, force=False, show_message=True):
        """
        Get license_dic based on config/license/LM_LICENSE_FILE.

        When show_message is False the caller owns the loading prompt (e.g. a
        filter_*_tab method that spans data fetch + table render with a single
        prompt); get_license_dic then stays silent so the two prompts don't
        flash in sequence with a gap of unresponsive UI in between.
        """
        # Not update license_dic repeatedly in config.fresh_interval seconds.
        current_second = int(time.time())

        if not force:
            if hasattr(config, 'fresh_interval') and config.fresh_interval:
                if current_second - self.license_dic_second <= int(config.fresh_interval):
                    return

        self.license_dic_second = current_second

        common.bprint('Load license info ...', date_format='%Y-%m-%d %H:%M:%S')

        # Print loading license information message with GUI.
        my_show_message = None

        if show_message:
            my_show_message = ShowMessage('Info', 'Loading license info, please wait a moment ...', persistent=True)
            my_show_message.start()

        # Get self.license_dic.
        LM_LICENSE_FILE_file = str(_LSFMONITOR_INSTALL_PATH) + '/config/license/LM_LICENSE_FILE'

        if os.path.exists(LM_LICENSE_FILE_file) and (('all' in self.administrator_list) or ('ALL' in self.administrator_list) or (USER in self.administrator_list)):
            # Collect non-empty/non-comment lines first; only override the
            # LM_LICENSE_FILE environment variable when the platform file
            # actually yields servers. An empty (comment-only) file must fall
            # back to the shell's LM_LICENSE_FILE instead of clobbering it.
            parsed_servers = []

            with open(LM_LICENSE_FILE_file, 'r') as LLF:
                for line in LLF.readlines():
                    line = line.strip()

                    if (not re.match(r'^\s*$', line)) and (not re.match(r'^\s*#.*$', line)):
                        parsed_servers.append(line)

            if parsed_servers:
                os.environ['LM_LICENSE_FILE'] = ':'.join(parsed_servers)

        if not hasattr(config, 'lmstat_path'):
            config.lmstat_path = ''
        elif config.lmstat_path and not os.path.exists(config.lmstat_path):
            common.bprint('"' + str(config.lmstat_path) + '": no such lmstat file!', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            config.lmstat_path = ''

        if not hasattr(config, 'lmstat_bsub_command'):
            config.lmstat_bsub_command = ''

        my_get_license_info = common_license.GetLicenseInfo(lmstat_path=config.lmstat_path, bsub_command=config.lmstat_bsub_command)
        self.license_dic = my_get_license_info.get_license_info()

        # Print loading license information message with GUI. (END)
        if my_show_message is not None:
            my_show_message.terminate()

        if not self.license_dic:
            common.bprint('Not find any valid license information.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

    def get_license_server_list(self, license_dic={}):
        """
        Get all license_server on specified license_dic.
        """
        license_server_list = []

        if not license_dic:
            license_dic = self.license_dic

        for license_server in license_dic.keys():
            if license_server not in license_server_list:
                license_server_list.append(license_server)

        license_server_list.sort()

        return license_server_list

    def get_vendor_daemon_list(self, license_dic={}, specified_license_server_list=['ALL', ]):
        """
        Get vendor_daemon on specified license_dic with specified license_server_list.
        """
        vendor_daemon_list = []

        if not license_dic:
            license_dic = self.license_dic

        for license_server in license_dic.keys():
            if ('ALL' in specified_license_server_list) or (license_server in specified_license_server_list):
                for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                    if vendor_daemon not in vendor_daemon_list:
                        vendor_daemon_list.append(vendor_daemon)

        vendor_daemon_list.sort()

        return vendor_daemon_list

    def get_feature_and_user_list(self):
        """
        Get all features/users from self.license_dic.
        """
        feature_list = []
        user_list = []

        for license_server in self.license_dic.keys():
            for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                for feature in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                    feature_list.append(feature)

                    for usage_dic in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['in_use_info']:
                        user_list.append(usage_dic['user'])

        feature_list = list(set(feature_list))
        user_list = list(set(user_list))

        return feature_list, user_list

    def init_ui(self):
        """Build the inner-tab structure with empty tables and a loading overlay.

        License data is NOT fetched here. The inner tabs are built against an
        empty license_dic (yielding header-only tables), and a centered
        "loading" overlay is shown on top of the tab area. The actual lmstat
        fetch runs in on_first_show() via a background thread; when it finishes
        _on_license_loaded() repopulates every table.
        """
        # main_tab is provided by PanelBase; menubar is contributed by
        # register_menubar_actions() onto the host MainWindow.

        # Define sub-tab placeholders.
        self.server_tab = QWidget()
        self.feature_tab = QWidget()
        self.expires_tab = QWidget()
        self.usage_tab = QWidget()

        if ('all' in self.administrator_list) or ('ALL' in self.administrator_list) or (USER in self.administrator_list):
            self.curve_tab = QWidget()
            self.utilization_tab = QWidget()

        # Add the sub-tabs into main Tab widget
        self.main_tab.addTab(self.server_tab, 'SERVER')
        self.main_tab.addTab(self.feature_tab, 'FEATURE')
        self.main_tab.addTab(self.expires_tab, 'EXPIRES')
        self.main_tab.addTab(self.usage_tab, 'USAGE')

        if ('all' in self.administrator_list) or ('ALL' in self.administrator_list) or (USER in self.administrator_list):
            self.main_tab.addTab(self.curve_tab, 'CURVE')
            self.main_tab.addTab(self.utilization_tab, 'UTILIZATION')

        # Build inner tabs against the (still-empty) license_dic so the tab UI
        # exists immediately; tables stay header-only until data arrives.
        self.gen_server_tab()
        self.gen_feature_tab()
        self.gen_expires_tab()
        self.gen_usage_tab()

        if ('all' in self.administrator_list) or ('ALL' in self.administrator_list) or (USER in self.administrator_list):
            self.gen_curve_tab()
            self.gen_utilization_tab()

        # Switch to the default inner tab:
        #   --tab > --feature+--user(USAGE) > --feature(FEATURE) > --user(USAGE) > FEATURE.
        specified_feature = getattr(self.args, 'feature', '') if self.args else ''

        initial_tab = 'FEATURE'

        if self.specified_tab:
            initial_tab = self.specified_tab
        elif specified_feature and self.specified_user:
            initial_tab = 'USAGE'
        elif self.specified_user:
            initial_tab = 'USAGE'

        self.switch_tab(initial_tab)

        # Loading overlay shown across the whole panel while the background
        # lmstat fetch is running. Hidden in _on_license_loaded().
        self._license_loading_label = QLabel('Loading license info, please wait ...', self)
        self._license_loading_label.setAlignment(Qt.AlignCenter)
        self._license_loading_label.setStyleSheet(
            'QLabel { background-color: rgba(245, 245, 245, 220); color: #555; font-size: 15px; }'
        )
        self._license_loading_label.hide()

    def switch_tab(self, specified_tab):
        """
        Switch to the specified Tab.
        """
        tab_dic = {
                   'SERVER': self.server_tab,
                   'FEATURE': self.feature_tab,
                   'EXPIRES': self.expires_tab,
                   'USAGE': self.usage_tab,
                  }

        if ('all' in self.administrator_list) or ('ALL' in self.administrator_list) or (USER in self.administrator_list):
            tab_dic['CURVE'] = self.curve_tab
            tab_dic['UTILIZATION'] = self.utilization_tab

        # Tab name is case-insensitive (user may type feature/Feature/FEATURE).
        # Guard against unknown names (would otherwise raise KeyError).
        specified_tab = specified_tab.upper() if specified_tab else ''

        if specified_tab not in tab_dic:
            return

        self.main_tab.setCurrentWidget(tab_dic[specified_tab])

    def register_menubar_actions(self, menubar, shared_menus=None):
        """
        Register this panel's actions into the host's shared top-level menus.
        Exports go into File with a "License - " prefix; setup toggles into Setup.
        Exit/Help are owned by the LSF panel so this panel does not add them.
        """
        file_menu = shared_menus['File']
        setup_menu = shared_menus['Setup']

        # ----- File (License exports), added after LSF items via a separator -----
        file_menu.addSeparator()

        export_server_table_action = QAction('Export LICENSE server table', self)
        export_server_table_action.setIcon(QIcon(str(_LSFMONITOR_INSTALL_PATH) + '/data/pictures/save.png'))
        export_server_table_action.triggered.connect(self.export_server_table)

        export_feature_table_action = QAction('Export LICENSE feature table', self)
        export_feature_table_action.setIcon(QIcon(str(_LSFMONITOR_INSTALL_PATH) + '/data/pictures/save.png'))
        export_feature_table_action.triggered.connect(self.export_feature_table)

        export_expires_table_action = QAction('Export LICENSE expires table', self)
        export_expires_table_action.setIcon(QIcon(str(_LSFMONITOR_INSTALL_PATH) + '/data/pictures/save.png'))
        export_expires_table_action.triggered.connect(self.export_expires_table)

        export_usage_table_action = QAction('Export LICENSE usage table', self)
        export_usage_table_action.setIcon(QIcon(str(_LSFMONITOR_INSTALL_PATH) + '/data/pictures/save.png'))
        export_usage_table_action.triggered.connect(self.export_usage_table)

        file_menu.addAction(export_server_table_action)
        file_menu.addAction(export_feature_table_action)
        file_menu.addAction(export_expires_table_action)
        file_menu.addAction(export_usage_table_action)

        if ('all' in self.administrator_list) or ('ALL' in self.administrator_list) or (USER in self.administrator_list):
            export_curve_table_action = QAction('Export LICENSE curve table', self)
            export_curve_table_action.setIcon(QIcon(str(_LSFMONITOR_INSTALL_PATH) + '/data/pictures/save.png'))
            export_curve_table_action.triggered.connect(self.export_curve_table)

            export_utilization_table_action = QAction('Export LICENSE utilization table', self)
            export_utilization_table_action.setIcon(QIcon(str(_LSFMONITOR_INSTALL_PATH) + '/data/pictures/save.png'))
            export_utilization_table_action.triggered.connect(self.export_utilization_table)

            file_menu.addAction(export_curve_table_action)
            file_menu.addAction(export_utilization_table_action)

            # ----- Setup (License) -----
            self.enable_utilization_detail_action = QAction('Enable License utilization detail', self, checkable=True)
            self.enable_utilization_detail_action.setChecked(False)
            self.enable_utilization_detail_action.toggled.connect(self.func_enable_utilization_detail)

            setup_menu.addSeparator()
            setup_menu.addAction(self.enable_utilization_detail_action)

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

    def set_common_checkbox_combo(self, checkbox_combo, item_list):
        """
        Set (initialize) checkbox combo item.
        """
        checkbox_combo.clear()

        # Fill checkbox_combo.
        for item in item_list:
            checkbox_combo.addCheckBoxItem(item, update_width=True)

        # Set "ALL" as checked status.
        for (i, qBox) in enumerate(checkbox_combo.checkBoxList):
            if (qBox.text() == 'ALL') and (qBox.isChecked() is False):
                checkbox_combo.checkBoxList[i].setChecked(True)
                break

    def set_checkbox_combo_item_state(self, checkbox_combo, item, state=True):
        """
        Set checkbox combo items state to "True"(checked) or "False"(unchecked), default is "True"
        """
        for (i, qBox) in enumerate(checkbox_combo.checkBoxList):
            if qBox.text() == item:
                checkbox_combo.checkBoxList[i].setChecked(state)
                break

# For SERVER TAB (start) #
    def gen_server_tab(self):
        """
        Generate SERVER tab, show license server/vendor information.
        """
        self.server_tab_table = QTableWidget(self.server_tab)
        common_pyqt5.make_table_readonly(self.server_tab_table)

        # Grid
        server_tab_grid = QGridLayout()
        server_tab_grid.addWidget(self.server_tab_table, 0, 0)
        self.server_tab.setLayout(server_tab_grid)

        # Generate self.server_tab_table
        self.gen_server_tab_table()

    def gen_server_tab_table(self):
        self.server_tab_table.setShowGrid(True)
        self.server_tab_table.setSortingEnabled(True)
        self.server_tab_table.setColumnCount(0)
        self.server_tab_table_title_list = ['Server', 'Server_Status', 'Server_Version', 'License_Files', 'Vendor', 'Vendor_Status', 'Vendor_Version']
        self.server_tab_table.setColumnCount(len(self.server_tab_table_title_list))
        self.server_tab_table.setHorizontalHeaderLabels(self.server_tab_table_title_list)

        # Set column width
        self.server_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.server_tab_table.setColumnWidth(1, 110)
        self.server_tab_table.setColumnWidth(2, 110)
        self.server_tab_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.server_tab_table.setColumnWidth(4, 90)
        self.server_tab_table.setColumnWidth(5, 120)
        self.server_tab_table.setColumnWidth(6, 120)
        common_pyqt5.auto_size_table_columns(self.server_tab_table)

        # Get and update license_dic
        license_dic = copy.deepcopy(self.license_dic)

        for license_server in license_dic.keys():
            if not license_dic[license_server]['vendor_daemon']:
                license_dic[license_server]['vendor_daemon'].setdefault('', {'vendor_daemon_status': '', 'vendor_daemon_version': ''})

        # Get license_server_list.
        license_server_list = self.get_license_server_list()

        # Set self.server_tab_table.setRowCount
        row = 0

        for license_server in license_server_list:
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                row += 1

        self.server_tab_table.setRowCount(row)

        # Set item
        row = -1

        for license_server in license_server_list:
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                column = 0
                row += 1

                # For Server
                item = QTableWidgetItem()
                item.setText(license_server)

                if license_dic[license_server]['license_server_status'] != 'UP':
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)

                # For Server_Status
                column += 1
                item = QTableWidgetItem()
                item.setText(license_dic[license_server]['license_server_status'])

                if license_dic[license_server]['license_server_status'] != 'UP':
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)

                # For Server_Version
                column += 1
                item = QTableWidgetItem()
                item.setText(license_dic[license_server]['license_server_version'])

                if license_dic[license_server]['license_server_status'] != 'UP':
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)

                # For license_files
                column += 1
                item = QTableWidgetItem()
                item.setText(license_dic[license_server]['license_files'])

                if license_dic[license_server]['license_server_status'] != 'UP':
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)

                # For Vendor
                column += 1
                item = QTableWidgetItem()
                item.setText(vendor_daemon)

                if (license_dic[license_server]['license_server_status'] != 'UP') or (license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_status'] != 'UP'):
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)

                # For Vendor_Status
                column += 1
                item = QTableWidgetItem()
                item.setText(license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_status'])

                if (license_dic[license_server]['license_server_status'] != 'UP') or (license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_status'] != 'UP'):
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)

                # For Vendor_Version
                column += 1
                item = QTableWidgetItem()
                item.setText(license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_version'])

                if (license_dic[license_server]['license_server_status'] != 'UP') or (license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_status'] != 'UP'):
                    item.setBackground(QBrush(QColor(STATUS_EXIT)))

                self.server_tab_table.setItem(row, column, item)
# For SERVER TAB (end) #

# For FEATURE TAB (start) #
    def gen_feature_tab(self):
        """
        Generate FEATURE tab, show license feature usage information.
        """
        self.feature_tab_frame = QFrame(self.feature_tab)
        self.feature_tab_frame.setFrameShadow(QFrame.Raised)
        self.feature_tab_frame.setFrameShape(QFrame.Box)

        self.feature_tab_table = QTableWidget(self.feature_tab)
        common_pyqt5.make_table_readonly(self.feature_tab_table)
        self.feature_tab_table.setContextMenuPolicy(Qt.CustomContextMenu)

        # Grid
        feature_tab_grid = QGridLayout()

        feature_tab_grid.addWidget(self.feature_tab_frame, 0, 0)
        feature_tab_grid.addWidget(self.feature_tab_table, 1, 0)

        feature_tab_grid.setRowStretch(0, 1)
        feature_tab_grid.setRowStretch(1, 10)

        self.feature_tab.setLayout(feature_tab_grid)

        # Generate self.feature_tab_frame and self.feature_tab_table
        self.gen_feature_tab_frame()
        self.gen_feature_tab_table(self.license_dic)
        self.feature_tab_table.itemClicked.connect(self.feature_tab_table_check_click)

    def gen_feature_tab_frame(self):
        # Show
        feature_tab_show_label = QLabel('Show', self.feature_tab_frame)
        feature_tab_show_label.setStyleSheet('font-weight: bold;')
        feature_tab_show_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.feature_tab_show_combo = common_pyqt5.QComboCheckBox(self.feature_tab_frame)
        self.set_feature_tab_show_combo()

        # License Server
        feature_tab_server_label = QLabel('Server', self.feature_tab_frame)
        feature_tab_server_label.setStyleSheet('font-weight: bold;')
        feature_tab_server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.feature_tab_server_combo = common_pyqt5.QComboCheckBox(self.feature_tab_frame)
        self.set_feature_tab_server_combo()

        # Vendor Daemon
        feature_tab_vendor_label = QLabel('Vendor', self.feature_tab_frame)
        feature_tab_vendor_label.setStyleSheet('font-weight: bold;')
        feature_tab_vendor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.feature_tab_vendor_combo = common_pyqt5.QComboCheckBox(self.feature_tab_frame)
        self.set_feature_tab_vendor_combo()

        # License Feature
        feature_tab_feature_label = QLabel('Feature', self.feature_tab_frame)
        feature_tab_feature_label.setStyleSheet('font-weight: bold;')
        feature_tab_feature_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.feature_tab_feature_line = QLineEdit()
        self.feature_tab_feature_line.returnPressed.connect(lambda: self.filter_feature_tab_license_feature())

        feature_tab_feature_line_completer = common_pyqt5.get_completer(self.feature_list)
        self.feature_tab_feature_line.setCompleter(feature_tab_feature_line_completer)

        # Filter Button
        feature_tab_check_button = QPushButton('Check', self.feature_tab_frame)
        feature_tab_check_button.clicked.connect(lambda: self.filter_feature_tab_license_feature())

        # Grid
        feature_tab_frame_grid = QGridLayout()

        feature_tab_frame_grid.addWidget(feature_tab_show_label, 0, 0)
        feature_tab_frame_grid.addWidget(self.feature_tab_show_combo, 0, 1)
        feature_tab_frame_grid.addWidget(feature_tab_server_label, 0, 2)
        feature_tab_frame_grid.addWidget(self.feature_tab_server_combo, 0, 3)
        feature_tab_frame_grid.addWidget(feature_tab_vendor_label, 0, 4)
        feature_tab_frame_grid.addWidget(self.feature_tab_vendor_combo, 0, 5)
        feature_tab_frame_grid.addWidget(feature_tab_feature_label, 0, 6)
        feature_tab_frame_grid.addWidget(self.feature_tab_feature_line, 0, 7)
        feature_tab_frame_grid.addWidget(feature_tab_check_button, 0, 8)

        feature_tab_frame_grid.setColumnStretch(1, 1)
        feature_tab_frame_grid.setColumnStretch(2, 1)
        feature_tab_frame_grid.setColumnStretch(3, 1)
        feature_tab_frame_grid.setColumnStretch(4, 1)
        feature_tab_frame_grid.setColumnStretch(5, 1)
        feature_tab_frame_grid.setColumnStretch(6, 1)
        feature_tab_frame_grid.setColumnStretch(7, 2)
        feature_tab_frame_grid.setColumnStretch(8, 1)

        self.feature_tab_frame.setLayout(feature_tab_frame_grid)

    def set_feature_tab_show_combo(self):
        """
        Set (initialize) self.feature_tab_show_combo.
        """
        self.feature_tab_show_combo.clear()
        show_list = ['ALL', 'IN_USE', 'NOT_USED']

        for item in show_list:
            self.feature_tab_show_combo.addCheckBoxItem(item)

        # Default: ALL checked.
        for (i, qBox) in enumerate(self.feature_tab_show_combo.checkBoxList):
            if (qBox.text() == 'ALL') and (qBox.isChecked() is False):
                self.feature_tab_show_combo.checkBoxList[i].setChecked(True)

                break

    def set_feature_tab_server_combo(self):
        """
        Set (initialize) self.feature_tab_server_combo.
        """
        # Get license_server list.
        license_server_list = self.get_license_server_list()
        license_server_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.feature_tab_server_combo, license_server_list)

    def set_feature_tab_vendor_combo(self):
        """
        Set (initialize) self.feature_tab_vendor_combo.
        """
        # Get vendor_daemon list.
        vendor_daemon_list = self.get_vendor_daemon_list()
        vendor_daemon_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.feature_tab_vendor_combo, vendor_daemon_list)

    def filter_feature_tab_license_feature(self, get_license_info=True):
        """
        Get license feature information based on self.feature_tab_show_combo/self.feature_tab_server_combo/self.feature_tab_vendor_combo/self.feature_tab_feature_line.
        Generate self.feature_tab_table with filetered license feature information.
        """
        # One loading prompt spans license fetch, filter, and table render.
        # persistent=True so the message window stays visible until terminate().
        my_show_message = ShowMessage('Info', 'Loading feature info, please wait a moment ...', persistent=True)
        my_show_message.start()

        # Let the message window render before blocking the GUI thread.
        QApplication.processEvents()
        time.sleep(0.2)
        QApplication.processEvents()

        # Re-generate self.feature_tab_table.
        if get_license_info:
            self.get_license_dic(show_message=False)

        if self.license_dic:
            # show_combo is now a QComboCheckBox; selectedItems() returns a dict
            # {text: text}. If ALL is selected (or nothing selected), show all.
            show_mode_items = list(self.feature_tab_show_combo.selectedItems().values())
            show_mode = 'ALL' if ('ALL' in show_mode_items or not show_mode_items) else show_mode_items[0]
            selected_license_server_dic = self.feature_tab_server_combo.selectedItems()
            selected_license_server_list = list(selected_license_server_dic.values())
            selected_vendor_daemon_dic = self.feature_tab_vendor_combo.selectedItems()
            selected_vendor_daemon_list = list(selected_vendor_daemon_dic.values())
            specified_license_feature_list = self.feature_tab_feature_line.text().strip().split()
            my_filter_license = common_license.FilterLicenseDic()
            filtered_license_dic = my_filter_license.run(license_dic=self.license_dic, server_list=selected_license_server_list, vendor_list=selected_vendor_daemon_list, feature_list=specified_license_feature_list, show_mode=show_mode)

            # Update self.feature_tab_table
            self.gen_feature_tab_table(filtered_license_dic)

        my_show_message.terminate()

    def feature_tab_table_check_click(self, item=None):
        if item is not None:
            if item.column() == 4:
                in_use_num = self.feature_tab_table.item(item.row(), item.column()).text().strip()

                if in_use_num != '0':
                    # Reset self.usage_tab_server_combo on USAGE tab.
                    current_license_server = self.feature_tab_table.item(item.row(), 0).text().strip()
                    self.set_usage_tab_server_combo()
                    self.set_checkbox_combo_item_state(self.usage_tab_server_combo, current_license_server, state=True)

                    # Reset self.usage_tab_vendor_combo on USAGE tab.
                    current_vendor_daemon = self.feature_tab_table.item(item.row(), 1).text().strip()
                    self.set_usage_tab_vendor_combo()
                    self.set_checkbox_combo_item_state(self.usage_tab_vendor_combo, current_vendor_daemon, state=True)

                    # Reset self.usage_tab_feature_line on USAGE tab.
                    current_feature = self.feature_tab_table.item(item.row(), 2).text().strip()
                    self.usage_tab_feature_line.setText(current_feature)

                    # Clear self.usage_tab_user_line on USAGE tab.
                    self.usage_tab_user_line.setText('')

                    # Switch to USGAE tab, filter USAGE tab license feature.
                    self.main_tab.setCurrentWidget(self.usage_tab)
                    self.filter_usage_tab_license_feature()

    def gen_feature_tab_table(self, license_dic):
        # Get license feature num.
        license_feature_num = 0

        if license_dic:
            for license_server in license_dic.keys():
                for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                    for license_feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                        license_feature_num += 1

        # Fill self.feature_tab_table column.
        self.feature_tab_table.setShowGrid(True)
        self.feature_tab_table.setSortingEnabled(True)
        self.feature_tab_table.setColumnCount(0)
        self.feature_tab_table.setColumnCount(5)
        self.feature_tab_table_title_list = ['Server', 'Vendor', 'Feature', 'Total_License', 'In_Use_License']
        self.feature_tab_table.setHorizontalHeaderLabels(self.feature_tab_table_title_list)

        self.feature_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.feature_tab_table.setColumnWidth(1, 120)
        self.feature_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.feature_tab_table.setColumnWidth(3, 160)
        self.feature_tab_table.setColumnWidth(4, 160)
        common_pyqt5.auto_size_table_columns(self.feature_tab_table)

        # Fill self.feature_tab_table row.
        self.feature_tab_table.setRowCount(0)
        self.feature_tab_table.setRowCount(license_feature_num)

        row = -1

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for license_feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                    row += 1

                    # For Server.
                    item = QTableWidgetItem()
                    item.setText(license_server)
                    self.feature_tab_table.setItem(row, 0, item)

                    # For Vendor.
                    item = QTableWidgetItem()
                    item.setText(vendor_daemon)
                    self.feature_tab_table.setItem(row, 1, item)

                    # For Feature.
                    item = QTableWidgetItem()
                    item.setText(license_feature)
                    self.feature_tab_table.setItem(row, 2, item)

                    # For Total_License.
                    item = QTableWidgetItem()

                    if self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][license_feature]['issued'] == 'Uncounted':
                        item.setData(Qt.DisplayRole, self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][license_feature]['issued'])
                    else:
                        item.setData(Qt.DisplayRole, int(self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][license_feature]['issued']))

                    self.feature_tab_table.setItem(row, 3, item)

                    # For In_Use_License.
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, int(self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][license_feature]['in_use']))

                    if item.text() != '0':
                        item.setFont(QFont('song', 9, QFont.Bold))

                    self.feature_tab_table.setItem(row, 4, item)
# For FEATURE TAB (end) #

# For EXPIRES TAB (start) #
    def gen_expires_tab(self):
        """
        Generate EXPIRES tab, show license feature expires information.
        """
        self.expires_tab_frame = QFrame(self.expires_tab)
        self.expires_tab_frame.setFrameShadow(QFrame.Raised)
        self.expires_tab_frame.setFrameShape(QFrame.Box)

        self.expires_tab_table = QTableWidget(self.expires_tab)
        common_pyqt5.make_table_readonly(self.expires_tab_table)

        # Grid
        expires_tab_grid = QGridLayout()

        expires_tab_grid.addWidget(self.expires_tab_frame, 0, 0)
        expires_tab_grid.addWidget(self.expires_tab_table, 1, 0)

        expires_tab_grid.setRowStretch(0, 1)
        expires_tab_grid.setRowStretch(1, 10)

        self.expires_tab.setLayout(expires_tab_grid)

        # Generate self.expires_tab_frame and self.expires_tab_table.
        self.gen_expires_tab_frame()
        self.gen_expires_tab_table(self.license_dic)

    def gen_expires_tab_frame(self):
        # Show
        expires_tab_show_label = QLabel('Show', self.expires_tab_frame)
        expires_tab_show_label.setStyleSheet('font-weight: bold;')
        expires_tab_show_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.expires_tab_show_combo = common_pyqt5.QComboCheckBox(self.expires_tab_frame)
        self.set_expires_tab_show_combo()

        # License Server
        expires_tab_server_label = QLabel('Server', self.expires_tab_frame)
        expires_tab_server_label.setStyleSheet('font-weight: bold;')
        expires_tab_server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.expires_tab_server_combo = common_pyqt5.QComboCheckBox(self.expires_tab_frame)
        self.set_expires_tab_server_combo()

        # License vendor daemon
        expires_tab_vendor_label = QLabel('Vendor', self.expires_tab_frame)
        expires_tab_vendor_label.setStyleSheet('font-weight: bold;')
        expires_tab_vendor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.expires_tab_vendor_combo = common_pyqt5.QComboCheckBox(self.expires_tab_frame)
        self.set_expires_tab_vendor_combo()

        # License Feature
        expires_tab_feature_label = QLabel('Feature', self.expires_tab_frame)
        expires_tab_feature_label.setStyleSheet('font-weight: bold;')
        expires_tab_feature_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.expires_tab_feature_line = QLineEdit()
        self.expires_tab_feature_line.returnPressed.connect(lambda: self.filter_expires_tab_license_feature())

        expires_tab_feature_line_completer = common_pyqt5.get_completer(self.feature_list)
        self.expires_tab_feature_line.setCompleter(expires_tab_feature_line_completer)

        # Filter Button
        expires_tab_check_button = QPushButton('Check', self.expires_tab_frame)
        expires_tab_check_button.clicked.connect(lambda: self.filter_expires_tab_license_feature())

        # Grid
        expires_tab_frame_grid = QGridLayout()

        expires_tab_frame_grid.addWidget(expires_tab_show_label, 0, 0)
        expires_tab_frame_grid.addWidget(self.expires_tab_show_combo, 0, 1)
        expires_tab_frame_grid.addWidget(expires_tab_server_label, 0, 2)
        expires_tab_frame_grid.addWidget(self.expires_tab_server_combo, 0, 3)
        expires_tab_frame_grid.addWidget(expires_tab_vendor_label, 0, 4)
        expires_tab_frame_grid.addWidget(self.expires_tab_vendor_combo, 0, 5)
        expires_tab_frame_grid.addWidget(expires_tab_feature_label, 0, 6)
        expires_tab_frame_grid.addWidget(self.expires_tab_feature_line, 0, 7)
        expires_tab_frame_grid.addWidget(expires_tab_check_button, 0, 8)

        expires_tab_frame_grid.setColumnStretch(1, 1)
        expires_tab_frame_grid.setColumnStretch(2, 1)
        expires_tab_frame_grid.setColumnStretch(3, 1)
        expires_tab_frame_grid.setColumnStretch(4, 1)
        expires_tab_frame_grid.setColumnStretch(5, 1)
        expires_tab_frame_grid.setColumnStretch(6, 1)
        expires_tab_frame_grid.setColumnStretch(7, 2)
        expires_tab_frame_grid.setColumnStretch(8, 1)

        self.expires_tab_frame.setLayout(expires_tab_frame_grid)

    def set_expires_tab_show_combo(self):
        """
        Set (initialize) self.expires_tab_show_combo.
        """
        self.expires_tab_show_combo.clear()
        show_list = ['ALL', 'Expired', 'Nearly_Expired', 'Unexpired']

        for item in show_list:
            self.expires_tab_show_combo.addCheckBoxItem(item)

        # Default: ALL checked.
        for (i, qBox) in enumerate(self.expires_tab_show_combo.checkBoxList):
            if (qBox.text() == 'ALL') and (qBox.isChecked() is False):
                self.expires_tab_show_combo.checkBoxList[i].setChecked(True)

                break

    def set_expires_tab_server_combo(self):
        """
        Set (initialize) self.expires_tab_server_combo.
        """
        # Get license_server list.
        license_server_list = self.get_license_server_list()
        license_server_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.expires_tab_server_combo, license_server_list)

    def set_expires_tab_vendor_combo(self):
        """
        Set (initialize) self.expires_tab_vendor_combo.
        """
        # Get vendor_daemon list.
        vendor_daemon_list = self.get_vendor_daemon_list()
        vendor_daemon_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.expires_tab_vendor_combo, vendor_daemon_list)

    def filter_expires_tab_license_feature(self, get_license_info=True):
        """
        Get license feature expires information based on self.expires_tab_show_combo/self.expires_tab_server_combo/self.expires_tab_vendor_combo/self.expires_tab_feature_line.
        Generate self.expires_tab_table with filetered license feature information.
        """
        # One loading prompt spans the whole pipeline — license fetch, filter,
        # (optional license-file grep), and table render — so it stays up until
        # the result is on screen. get_license_dic stays silent (show_message
        # =False) to avoid a second prompt flashing with a gap of frozen UI.
        my_show_message = ShowMessage('Info', 'Loading expires info, please wait a moment ...', persistent=True)
        my_show_message.start()

        # Let the message window render before blocking the GUI thread.
        QApplication.processEvents()
        time.sleep(0.2)
        QApplication.processEvents()

        # Re-generate self.expires_tab_table.
        if get_license_info:
            self.get_license_dic(show_message=False)

        if self.license_dic:
            selected_show_items = list(self.expires_tab_show_combo.selectedItems().values())
            # Multi-select: pass the full list down so e.g. Expired + Nearly_Expired
            # both apply (OR). 'ALL' / empty means no filtering.
            selected_show_mode = ['ALL'] if (('ALL' in selected_show_items) or (not selected_show_items)) else selected_show_items
            selected_license_server_dic = self.expires_tab_server_combo.selectedItems()
            selected_license_server_list = list(selected_license_server_dic.values())
            selected_vendor_daemon_dic = self.expires_tab_vendor_combo.selectedItems()
            selected_vendor_daemon_list = list(selected_vendor_daemon_dic.values())
            specified_license_feature_list = self.expires_tab_feature_line.text().strip().split()
            my_filter_license = common_license.FilterLicenseDic()
            filtered_license_dic = my_filter_license.run(license_dic=self.license_dic, server_list=selected_license_server_list, vendor_list=selected_vendor_daemon_list, feature_list=specified_license_feature_list, show_mode=selected_show_mode)

            if (not filtered_license_dic) and specified_license_feature_list:
                common.bprint('Searching expires info from license file ...', date_format='%Y-%m-%d %H:%M:%S')
                filtered_license_dic = self.search_expire_info_from_license_file(selected_show_mode, selected_license_server_list, selected_vendor_daemon_list, specified_license_feature_list)

            # Update self.expires_tab_table
            self.gen_expires_tab_table(filtered_license_dic)

        my_show_message.terminate()

    def search_expire_info_from_license_file(self, selected_show_mode, selected_license_server_list, selected_vendor_daemon_list, specified_license_feature_list):
        """
        Search license feature expires information from license file.
        """
        filtered_license_dic = {}
        license_dic = {}

        for license_server in self.license_dic.keys():
            if ('ALL' in selected_license_server_list) or (license_server in selected_license_server_list):
                for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                    if ('ALL' in selected_vendor_daemon_list) or (vendor_daemon in selected_vendor_daemon_list):
                        license_files = self.license_dic[license_server]['license_files']

                        for license_file in license_files.split():
                            for specified_feature in specified_license_feature_list:
                                grep_command = 'grep ' + shlex.quote(' ' + str(specified_feature) + ' ') + ' ' + shlex.quote(str(license_file))

                                if os.path.exists(license_file):
                                    (return_code, stdout, stderr) = common.run_command(grep_command)
                                    stdout_list = str(stdout, 'utf-8').split('\n')
                                else:
                                    host_name = license_server.split('@')[1] if '@' in license_server else license_server
                                    stdout_list = common.ssh_client(host_name=host_name, command=grep_command, timeout=1)

                                for line in stdout_list:
                                    if re.match(r'^\s*(FEATURE|INCREMENT)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+.*$', line):
                                        my_match = re.match(r'^\s*(FEATURE|INCREMENT)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+.*$', line)
                                        feature = my_match.group(2)
                                        vendor = my_match.group(3)
                                        version = my_match.group(4)
                                        expire_info = my_match.group(5)
                                        license_num = my_match.group(6)

                                        if feature == specified_feature:
                                            license_dic.setdefault(license_server, {'license_files': license_files, 'license_server_status': self.license_dic[license_server]['license_server_status'], 'license_server_version': self.license_dic[license_server]['license_server_version'], 'vendor_daemon': {}})
                                            license_dic[license_server]['vendor_daemon'].setdefault(vendor_daemon, {'vendor_daemon_status': self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_status'], 'vendor_daemon_version': self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['vendor_daemon_version'], 'feature': {}, 'expires': {}})
                                            license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].setdefault(feature, {})
                                            license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'].setdefault(feature, [])
                                            license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'][feature].append({'version': version, 'license': license_num, 'vendor': vendor, 'expires': expire_info})

        if license_dic:
            # selected_show_mode is now a list (multi-select). Filter only when
            # there's at least one non-ALL mode.
            if isinstance(selected_show_mode, str):
                do_filter = selected_show_mode and selected_show_mode != 'ALL'
            else:
                do_filter = any(m and m != 'ALL' for m in selected_show_mode)

            if do_filter:
                my_filter_license = common_license.FilterLicenseDic()
                filtered_license_dic = my_filter_license.filter_show_mode_feature(license_dic, selected_show_mode)
            else:
                filtered_license_dic = license_dic

        return filtered_license_dic

    def gen_expires_tab_table(self, license_dic):
        # Get license expires num.
        license_feature_num = 0

        if license_dic:
            for license_server in license_dic.keys():
                for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                    for license_feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'].keys():
                        for expires_dic in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'][license_feature]:
                            license_feature_num += 1

        # Fill self.expires_tab_table column.
        self.expires_tab_table.setShowGrid(True)
        self.expires_tab_table.setSortingEnabled(True)
        self.expires_tab_table.setColumnCount(0)
        self.expires_tab_table.setColumnCount(6)
        self.expires_tab_table_title_list = ['Server', 'Vendor', 'Feature', 'Version', 'License_Num', 'Expires']
        self.expires_tab_table.setHorizontalHeaderLabels(self.expires_tab_table_title_list)

        self.expires_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.expires_tab_table.setColumnWidth(1, 100)
        self.expires_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.expires_tab_table.setColumnWidth(3, 100)
        self.expires_tab_table.setColumnWidth(4, 120)
        self.expires_tab_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        common_pyqt5.auto_size_table_columns(self.expires_tab_table)

        # Fill self.expires_tab_table row.
        self.expires_tab_table.setRowCount(0)
        self.expires_tab_table.setRowCount(license_feature_num)

        row = -1

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for license_feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'].keys():
                    for expires_dic in license_dic[license_server]['vendor_daemon'][vendor_daemon]['expires'][license_feature]:
                        row += 1

                        # For Server.
                        item = QTableWidgetItem()
                        item.setText(license_server)
                        self.expires_tab_table.setItem(row, 0, item)

                        # For Vendor.
                        item = QTableWidgetItem()
                        item.setText(expires_dic['vendor'])
                        self.expires_tab_table.setItem(row, 1, item)

                        # For Feature.
                        item = QTableWidgetItem()
                        item.setText(license_feature)
                        self.expires_tab_table.setItem(row, 2, item)

                        # For Version.
                        item = QTableWidgetItem()
                        item.setText(expires_dic['version'])
                        self.expires_tab_table.setItem(row, 3, item)

                        # For Feature Number.
                        item = QTableWidgetItem()
                        item.setData(Qt.DisplayRole, int(expires_dic['license']))
                        self.expires_tab_table.setItem(row, 4, item)

                        # For Expires Date.
                        item = QTableWidgetItem()
                        expires_date = common_license.switch_expires_date(expires_dic['expires'])
                        item.setText(expires_date)

                        expires_mark = common_license.check_expire_date(expires_dic['expires'])

                        if expires_mark == 0:
                            pass
                        elif expires_mark == -1:
                            item.setForeground(QBrush(QColor(STATUS_DONE)))
                        else:
                            item.setForeground(QBrush(QColor(STATUS_EXIT)))

                        self.expires_tab_table.setItem(row, 5, item)

# For EXPIRES TAB (end) #

# For USAGE TAB (start) #
    def gen_usage_tab(self):
        """
        Generate USAGE tab, show license feature usage information for running tasks.
        """
        self.usage_tab_frame = QFrame(self.usage_tab)
        self.usage_tab_frame.setFrameShadow(QFrame.Raised)
        self.usage_tab_frame.setFrameShape(QFrame.Box)

        self.usage_tab_table = QTableWidget(self.usage_tab)
        common_pyqt5.make_table_readonly(self.usage_tab_table)

        # Grid
        usage_tab_grid = QGridLayout()

        usage_tab_grid.addWidget(self.usage_tab_frame, 0, 0)
        usage_tab_grid.addWidget(self.usage_tab_table, 1, 0)

        usage_tab_grid.setRowStretch(0, 1)
        usage_tab_grid.setRowStretch(1, 10)

        self.usage_tab.setLayout(usage_tab_grid)

        # Generate self.usage_tab_frame and self.usage_tab_table.
        self.gen_usage_tab_frame()
        self.gen_usage_tab_table(self.license_dic)

    def gen_usage_tab_frame(self):
        # License Server
        usage_tab_server_label = QLabel('Server', self.usage_tab_frame)
        usage_tab_server_label.setStyleSheet('font-weight: bold;')
        usage_tab_server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.usage_tab_server_combo = common_pyqt5.QComboCheckBox(self.usage_tab_frame)
        self.set_usage_tab_server_combo()

        # License vendor daemon
        usage_tab_vendor_label = QLabel('Vendor', self.usage_tab_frame)
        usage_tab_vendor_label.setStyleSheet('font-weight: bold;')
        usage_tab_vendor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.usage_tab_vendor_combo = common_pyqt5.QComboCheckBox(self.usage_tab_frame)
        self.set_usage_tab_vendor_combo()

        # License Feature
        usage_tab_feature_label = QLabel('Feature', self.usage_tab_frame)
        usage_tab_feature_label.setStyleSheet('font-weight: bold;')
        usage_tab_feature_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.usage_tab_feature_line = QLineEdit()
        self.usage_tab_feature_line.returnPressed.connect(lambda: self.filter_usage_tab_license_feature())

        usage_tab_feature_line_completer = common_pyqt5.get_completer(self.feature_list)
        self.usage_tab_feature_line.setCompleter(usage_tab_feature_line_completer)

        # Submit Host
        usage_tab_submit_host_label = QLabel('Submit_Host', self.usage_tab_frame)
        usage_tab_submit_host_label.setStyleSheet('font-weight: bold;')
        usage_tab_submit_host_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.usage_tab_submit_host_combo = common_pyqt5.QComboCheckBox(self.usage_tab_frame, enableFilter=True)
        self.set_usage_tab_submit_host_combo()

        # Execute Host
        usage_tab_execute_host_label = QLabel('Execute_Host', self.usage_tab_frame)
        usage_tab_execute_host_label.setStyleSheet('font-weight: bold;')
        usage_tab_execute_host_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.usage_tab_execute_host_combo = common_pyqt5.QComboCheckBox(self.usage_tab_frame, enableFilter=True)
        self.set_usage_tab_execute_host_combo()

        # User
        usage_tab_user_label = QLabel('User', self.usage_tab_frame)
        usage_tab_user_label.setStyleSheet('font-weight: bold;')
        usage_tab_user_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.usage_tab_user_line = QLineEdit()
        self.usage_tab_user_line.returnPressed.connect(lambda: self.filter_usage_tab_license_feature())

        usage_tab_user_line_completer = common_pyqt5.get_completer(self.user_list)
        self.usage_tab_user_line.setCompleter(usage_tab_user_line_completer)

        # Fileter
        usage_tab_check_button = QPushButton('Check', self.usage_tab_frame)
        usage_tab_check_button.clicked.connect(lambda: self.filter_usage_tab_license_feature())

        # Grid
        usage_tab_frame_grid = QGridLayout()

        usage_tab_frame_grid.addWidget(usage_tab_server_label, 0, 0)
        usage_tab_frame_grid.addWidget(self.usage_tab_server_combo, 0, 1)
        usage_tab_frame_grid.addWidget(usage_tab_vendor_label, 0, 2)
        usage_tab_frame_grid.addWidget(self.usage_tab_vendor_combo, 0, 3)
        usage_tab_frame_grid.addWidget(usage_tab_submit_host_label, 0, 4)
        usage_tab_frame_grid.addWidget(self.usage_tab_submit_host_combo, 0, 5)
        usage_tab_frame_grid.addWidget(usage_tab_execute_host_label, 0, 6)
        usage_tab_frame_grid.addWidget(self.usage_tab_execute_host_combo, 0, 7)
        usage_tab_frame_grid.addWidget(usage_tab_feature_label, 0, 8)
        usage_tab_frame_grid.addWidget(self.usage_tab_feature_line, 0, 9)
        usage_tab_frame_grid.addWidget(usage_tab_user_label, 0, 10)
        usage_tab_frame_grid.addWidget(self.usage_tab_user_line, 0, 11)
        usage_tab_frame_grid.addWidget(usage_tab_check_button, 0, 12)

        for col in range(13):
            usage_tab_frame_grid.setColumnStretch(col, 1)

        usage_tab_frame_grid.setColumnStretch(9, 2)
        usage_tab_frame_grid.setColumnStretch(11, 2)

        self.usage_tab_frame.setLayout(usage_tab_frame_grid)

    def set_usage_tab_server_combo(self):
        """Set (initialize) self.usage_tab_server_combo."""
        license_server_list = self.get_license_server_list()
        license_server_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.usage_tab_server_combo, license_server_list)

    def set_usage_tab_vendor_combo(self):
        """Set (initialize) self.usage_tab_vendor_combo."""
        vendor_daemon_list = self.get_vendor_daemon_list()
        vendor_daemon_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.usage_tab_vendor_combo, vendor_daemon_list)

    def set_usage_tab_submit_host_combo(self):
        self.usage_tab_submit_host_combo.clear()
        submit_host_list = ['ALL', ]
        selected_license_server_list = list(self.usage_tab_server_combo.selectedItems().values()) if hasattr(self.usage_tab_server_combo, 'selectedItems') else ['ALL']
        selected_vendor_daemon_list = list(self.usage_tab_vendor_combo.selectedItems().values()) if hasattr(self.usage_tab_vendor_combo, 'selectedItems') else ['ALL']

        for license_server in self.license_dic.keys():
            if ('ALL' in selected_license_server_list) or (license_server in selected_license_server_list):
                for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                    if ('ALL' in selected_vendor_daemon_list) or (vendor_daemon in selected_vendor_daemon_list):
                        for feature in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                            for usage_dic in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['in_use_info']:
                                submit_host = usage_dic['submit_host']

                                if submit_host not in submit_host_list:
                                    submit_host_list.append(submit_host)

        for submit_host in submit_host_list:
            self.usage_tab_submit_host_combo.addCheckBoxItem(submit_host)

        # Default: ALL checked.
        for (i, qBox) in enumerate(self.usage_tab_submit_host_combo.checkBoxList):
            if (qBox.text() == 'ALL') and (qBox.isChecked() is False):
                self.usage_tab_submit_host_combo.checkBoxList[i].setChecked(True)

                break

    def set_usage_tab_execute_host_combo(self):
        self.usage_tab_execute_host_combo.clear()
        execute_host_list = ['ALL', ]
        selected_license_server_list = list(self.usage_tab_server_combo.selectedItems().values()) if hasattr(self.usage_tab_server_combo, 'selectedItems') else ['ALL']
        selected_vendor_daemon_list = list(self.usage_tab_vendor_combo.selectedItems().values()) if hasattr(self.usage_tab_vendor_combo, 'selectedItems') else ['ALL']

        for license_server in self.license_dic.keys():
            if ('ALL' in selected_license_server_list) or (license_server in selected_license_server_list):
                for vendor_daemon in self.license_dic[license_server]['vendor_daemon'].keys():
                    if ('ALL' in selected_vendor_daemon_list) or (vendor_daemon in selected_vendor_daemon_list):
                        for feature in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                            for usage_dic in self.license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][feature]['in_use_info']:
                                execute_host = usage_dic['execute_host']

                                if execute_host not in execute_host_list:
                                    execute_host_list.append(execute_host)

        for execute_host in execute_host_list:
            self.usage_tab_execute_host_combo.addCheckBoxItem(execute_host)

        # Default: ALL checked.
        for (i, qBox) in enumerate(self.usage_tab_execute_host_combo.checkBoxList):
            if (qBox.text() == 'ALL') and (qBox.isChecked() is False):
                self.usage_tab_execute_host_combo.checkBoxList[i].setChecked(True)

                break

    def filter_usage_tab_license_feature(self, get_license_info=True):
        """
        Get license feature information based on self.usage_tab_server_combo/self.usage_tab_vendor_combo/self.usage_tab_feature_line/self.usage_tab_user_line.
        Generate self.usage_tab_table with filetered license feature information.
        """
        # One loading prompt spans license fetch, filter, and table render.
        # persistent=True so the message window stays visible (no auto-close)
        # until terminate() is called after the table is fully rendered.
        my_show_message = ShowMessage('Info', 'Loading usage info, please wait a moment ...', persistent=True)
        my_show_message.start()

        # Let the ShowMessage subprocess (tools/message.py) start and render
        # before we block the GUI thread with lmstat. Without this, the GUI
        # thread blocks immediately on get_license_dic and the message window
        # never appears ("稍纵即逝").
        QApplication.processEvents()
        time.sleep(0.2)
        QApplication.processEvents()

        # Re-generate self.usage_tab_table.
        if get_license_info:
            self.get_license_dic(show_message=False)

        if self.license_dic:
            show_mode = 'IN_USE'
            selected_license_server_dic = self.usage_tab_server_combo.selectedItems()
            selected_license_server_list = list(selected_license_server_dic.values())
            selected_vendor_daemon_dic = self.usage_tab_vendor_combo.selectedItems()
            selected_vendor_daemon_list = list(selected_vendor_daemon_dic.values())
            specified_license_feature_list = self.usage_tab_feature_line.text().strip().split()
            selected_submit_host_list = list(self.usage_tab_submit_host_combo.selectedItems().values())
            selected_execute_host_list = list(self.usage_tab_execute_host_combo.selectedItems().values())
            specified_user_list = self.usage_tab_user_line.text().strip().split()
            filter_license_dic = common_license.FilterLicenseDic()
            filtered_license_dic = filter_license_dic.run(license_dic=self.license_dic, server_list=selected_license_server_list, vendor_list=selected_vendor_daemon_list, feature_list=specified_license_feature_list, submit_host_list=selected_submit_host_list, execute_host_list=selected_execute_host_list, user_list=specified_user_list, show_mode=show_mode)

            # Update self.usage_tab_table
            self.gen_usage_tab_table(license_dic=filtered_license_dic)

        my_show_message.terminate()

    def gen_usage_tab_table(self, license_dic):
        # Get license usage num.
        license_usage_num = 0

        if license_dic:
            for license_server in license_dic.keys():
                for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                    for license_feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                        for usage_dic in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][license_feature]['in_use_info']:
                            license_usage_num += 1

        # Fill self.usage_tab_table column.
        self.usage_tab_table.setShowGrid(True)
        self.usage_tab_table.setSortingEnabled(True)
        self.usage_tab_table.setColumnCount(0)
        self.usage_tab_table.setColumnCount(9)
        self.usage_tab_table_title_list = ['Server', 'Vendor', 'Feature', 'User', 'Submit_Host', 'Execute_Host', 'Num', 'Version', 'Start_Time']
        self.usage_tab_table.setHorizontalHeaderLabels(self.usage_tab_table_title_list)

        self.usage_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.usage_tab_table.setColumnWidth(1, 95)    # Vendor (e.g. "Synopsys", "CDNS")
        self.usage_tab_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.usage_tab_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.usage_tab_table.setColumnWidth(4, 110)   # Submit_Host
        self.usage_tab_table.setColumnWidth(5, 110)   # Execute_Host
        self.usage_tab_table.setColumnWidth(6, 50)    # Num
        # Version: "v2023.1025" (10 chars) ≈ 80px + 36 padding ≈ 115px
        self.usage_tab_table.setColumnWidth(7, 115)
        # Start_Time: "2026-08-27 10:19" (16 chars) ≈ 115px + 36 padding ≈ 150px
        self.usage_tab_table.setColumnWidth(8, 155)
        common_pyqt5.auto_size_table_columns(self.usage_tab_table)

        # Fill self.usage_tab_table row.
        self.usage_tab_table.setRowCount(0)
        self.usage_tab_table.setRowCount(license_usage_num)

        row = -1

        for license_server in license_dic.keys():
            for vendor_daemon in license_dic[license_server]['vendor_daemon'].keys():
                for license_feature in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'].keys():
                    for usage_dic in license_dic[license_server]['vendor_daemon'][vendor_daemon]['feature'][license_feature]['in_use_info']:
                        row += 1

                        # For Server.
                        item = QTableWidgetItem()
                        item.setText(license_server)
                        self.usage_tab_table.setItem(row, 0, item)

                        # For Vendor.
                        item = QTableWidgetItem()
                        item.setText(vendor_daemon)
                        self.usage_tab_table.setItem(row, 1, item)

                        # For Feature.
                        item = QTableWidgetItem()
                        item.setText(license_feature)
                        self.usage_tab_table.setItem(row, 2, item)

                        # For User.
                        item = QTableWidgetItem()
                        item.setText(usage_dic['user'])
                        self.usage_tab_table.setItem(row, 3, item)

                        # For Submit_Host.
                        item = QTableWidgetItem()
                        item.setText(usage_dic['submit_host'])
                        self.usage_tab_table.setItem(row, 4, item)

                        # For Execute_Host.
                        item = QTableWidgetItem()
                        item.setText(usage_dic['execute_host'])
                        self.usage_tab_table.setItem(row, 5, item)

                        # For License_Num.
                        item = QTableWidgetItem()
                        item.setData(Qt.DisplayRole, int(usage_dic['license_num']))
                        self.usage_tab_table.setItem(row, 6, item)

                        # For License_Version.
                        item = QTableWidgetItem()
                        item.setText(usage_dic['version'])
                        self.usage_tab_table.setItem(row, 7, item)

                        # For Start_Time.
                        item = QTableWidgetItem()
                        start_time = common_license.switch_start_time(usage_dic['start_time'], format='%Y-%m-%d %H:%M')
                        item.setText(start_time)

                        if common_license.check_long_runtime(usage_dic['start_time']):
                            item.setForeground(QBrush(QColor(STATUS_EXIT)))

                        self.usage_tab_table.setItem(row, 8, item)
# For USAGE TAB (end) #

# For CURVE TAB (start) #
    def gen_curve_tab(self):
        """
        Generate CURVE tab, show license feature curve information.
        """
        self.curve_tab_frame0 = QFrame(self.curve_tab)
        self.curve_tab_frame0.setFrameShadow(QFrame.Raised)
        self.curve_tab_frame0.setFrameShape(QFrame.Box)

        self.curve_tab_table = QTableWidget(self.curve_tab)
        common_pyqt5.make_table_readonly(self.curve_tab_table)
        self.curve_tab_table.itemClicked.connect(self.curve_tab_table_click)

        self.curve_tab_frame1 = QFrame(self.curve_tab)
        self.curve_tab_frame1.setFrameShadow(QFrame.Raised)
        self.curve_tab_frame1.setFrameShape(QFrame.Box)

        # Grid
        curve_tab_grid = QGridLayout()

        curve_tab_grid.addWidget(self.curve_tab_frame0, 0, 0, 1, 2)
        curve_tab_grid.addWidget(self.curve_tab_table, 1, 0)
        curve_tab_grid.addWidget(self.curve_tab_frame1, 1, 1)

        curve_tab_grid.setRowStretch(0, 1)
        curve_tab_grid.setRowStretch(1, 10)

        curve_tab_grid.setColumnStretch(0, 2)
        curve_tab_grid.setColumnStretch(1, 3)

        self.curve_tab.setLayout(curve_tab_grid)

        # Generate self.curve_tab_frame0, self.curve_tab_table and self.curve_tab_frame1.
        self.gen_curve_tab_frame0()
        self.gen_curve_tab_table()
        self.gen_curve_tab_frame1()

        # Empty-state hint: the CURVE table needs a "Check" to load history data.
        # Shown over the chart frame (the tab's graphics area) so the filter
        # row and table stay interactive; hidden once a load is triggered.
        self.curve_tab_hint_label = self._make_empty_hint(
            curve_tab_grid, 1, 1, 1, 1, 'Click "Check" button to load license feature curve information.'
        )

    def gen_curve_tab_frame0(self):
        # License Server
        curve_tab_server_label = QLabel('Server', self.curve_tab_frame0)
        curve_tab_server_label.setStyleSheet('font-weight: bold;')
        curve_tab_server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.curve_tab_server_combo = common_pyqt5.QComboCheckBox(self.curve_tab_frame0)
        self.set_curve_tab_server_combo()

        # License vendor daemon
        curve_tab_vendor_label = QLabel('Vendor', self.curve_tab_frame0)
        curve_tab_vendor_label.setStyleSheet('font-weight: bold;')
        curve_tab_vendor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.curve_tab_vendor_combo = common_pyqt5.QComboCheckBox(self.curve_tab_frame0)
        self.set_curve_tab_vendor_combo()

        # License Feature
        curve_tab_feature_label = QLabel('Feature', self.curve_tab_frame0)
        curve_tab_feature_label.setStyleSheet('font-weight: bold;')
        curve_tab_feature_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.curve_tab_feature_line = QLineEdit()
        self.curve_tab_feature_line.returnPressed.connect(self.filter_curve_tab)

        curve_tab_feature_line_completer = common_pyqt5.get_completer(self.feature_list)
        self.curve_tab_feature_line.setCompleter(curve_tab_feature_line_completer)

        # Check button
        curve_tab_check_button = QPushButton('Check', self.curve_tab_frame0)
        curve_tab_check_button.clicked.connect(self.filter_curve_tab)

        # Begin_Date
        curve_tab_begin_date_label = QLabel('Begin_Date', self.curve_tab_frame0)
        curve_tab_begin_date_label.setStyleSheet("font-weight: bold;")
        curve_tab_begin_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.curve_tab_begin_date_edit = QDateEdit(self.curve_tab_frame0)
        self.curve_tab_begin_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.curve_tab_begin_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.curve_tab_begin_date_edit.setCalendarPopup(True)
        self.curve_tab_begin_date_edit.setDate(QDate.currentDate().addDays(-7))

        # End_Date
        curve_tab_end_date_label = QLabel('End_Date', self.curve_tab_frame0)
        curve_tab_end_date_label.setStyleSheet("font-weight: bold;")
        curve_tab_end_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.curve_tab_end_date_edit = QDateEdit(self.curve_tab_frame0)
        self.curve_tab_end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.curve_tab_end_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.curve_tab_end_date_edit.setCalendarPopup(True)
        self.curve_tab_end_date_edit.setDate(QDate.currentDate())

        # Export is provided via the File menu (Export License curve table);
        # no in-tab Export button to avoid duplication.

        # self.curve_tab_frame0 - Grid
        curve_tab_frame0_grid = QGridLayout()

        curve_tab_frame0_grid.addWidget(curve_tab_begin_date_label, 0, 0)
        curve_tab_frame0_grid.addWidget(self.curve_tab_begin_date_edit, 0, 1)
        curve_tab_frame0_grid.addWidget(curve_tab_end_date_label, 0, 2)
        curve_tab_frame0_grid.addWidget(self.curve_tab_end_date_edit, 0, 3)
        curve_tab_frame0_grid.addWidget(curve_tab_server_label, 0, 4)
        curve_tab_frame0_grid.addWidget(self.curve_tab_server_combo, 0, 5)
        curve_tab_frame0_grid.addWidget(curve_tab_vendor_label, 0, 6)
        curve_tab_frame0_grid.addWidget(self.curve_tab_vendor_combo, 0, 7)
        curve_tab_frame0_grid.addWidget(curve_tab_feature_label, 0, 8)
        curve_tab_frame0_grid.addWidget(self.curve_tab_feature_line, 0, 9)
        curve_tab_frame0_grid.addWidget(curve_tab_check_button, 0, 10)

        for col in range(11):
            curve_tab_frame0_grid.setColumnStretch(col, 1)

        curve_tab_frame0_grid.setColumnStretch(9, 3)

        self.curve_tab_frame0.setLayout(curve_tab_frame0_grid)

    def set_curve_tab_server_combo(self):
        """
        Set (initialize) self.curve_tab_server_combo.
        """
        # Get license_server list.
        license_server_list = self.get_license_server_list()
        license_server_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.curve_tab_server_combo, license_server_list)

    def set_curve_tab_vendor_combo(self):
        """
        Set (initialize) self.curve_tab_vendor_combo.
        """
        # Get vendor_daemon list.
        vendor_daemon_list = self.get_vendor_daemon_list()
        vendor_daemon_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.curve_tab_vendor_combo, vendor_daemon_list)

    def filter_curve_tab(self):
        """
        Update self.curve_tab_table and self.curve_tab_frame1.
        """
        self.hide_empty_hint(getattr(self, 'curve_tab_hint_label', None))

        # One loading prompt spans the whole pipeline — data fetch, table
        # render, and curve drawing — so the user sees it stay up until the
        # result is actually on screen. The inner methods no longer raise
        # their own (premature) prompts.
        my_show_message = ShowMessage('Info', 'Loading curve info, please wait a moment ...', persistent=True)
        my_show_message.start()

        # Let the message window render before blocking the GUI thread.
        QApplication.processEvents()
        time.sleep(0.2)
        QApplication.processEvents()

        curve_dic = self.get_curve_info()

        self.gen_curve_tab_table(curve_dic)
        self.update_curve_tab_frame1(curve_dic)

        my_show_message.terminate()

    def update_db_info(self):
        """
        Get curve/utilization/usage database information.
        """
        common.bprint('Parse license db_path', date_format='%Y-%m-%d %H:%M:%S')
        self.db_dic = {}

        # resolve_db_path: config_license.db_path if set, else config.py db_path/license.
        resolved_db_path = common_db_path.resolve_db_path(config, 'license')

        if os.path.exists(resolved_db_path):
            license_server_db_path = str(resolved_db_path) + '/license_server'

            if not os.path.exists(license_server_db_path):
                common.bprint('"' + str(license_server_db_path) + '": No such directory.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            else:
                for license_server in os.listdir(license_server_db_path):
                    license_server_path = str(license_server_db_path) + '/' + str(license_server)

                    if re.match(r'^\d+@\S+$', license_server) and os.path.isdir(license_server_path):
                        self.db_dic.setdefault(license_server, {})

                        for vendor_daemon in os.listdir(license_server_path):
                            vendor_daemon_path = str(license_server_path) + '/' + str(vendor_daemon)
                            curve_db_path = str(vendor_daemon_path) + '/utilization.db'
                            usage_db_path = str(vendor_daemon_path) + '/usage.db'

                            if self.enable_utilization_detail:
                                utilization_db_path = str(vendor_daemon_path) + '/utilization.db'
                            else:
                                utilization_db_path = str(vendor_daemon_path) + '/utilization_day.db'

                            if os.path.isdir(vendor_daemon_path):
                                self.db_dic[license_server].setdefault(vendor_daemon, {})

                                if os.path.exists(curve_db_path):
                                    self.db_dic[license_server][vendor_daemon].setdefault('curve', curve_db_path)

                                if os.path.exists(usage_db_path):
                                    self.db_dic[license_server][vendor_daemon].setdefault('usage', usage_db_path)

                                if os.path.exists(utilization_db_path):
                                    self.db_dic[license_server][vendor_daemon].setdefault('utilization', utilization_db_path)

    def get_curve_info(self):
        """
        Get curve information from <license db_path>/license_server/<license_server>/<vendor_deamon>/usage.db.
        """
        # Print loading curve information message.
        common.bprint('Load curve info ...', date_format='%Y-%m-%d %H:%M:%S')

        curve_dic = {}

        key_list = ['sample_time', 'issued', 'in_use']
        begin_date = self.curve_tab_begin_date_edit.date().toString(Qt.ISODate)
        begin_time = str(begin_date) + ' 00:00:00'
        begin_second = time.mktime(time.strptime(begin_time, '%Y-%m-%d %H:%M:%S'))
        end_date = self.curve_tab_end_date_edit.date().toString(Qt.ISODate)
        end_time = str(end_date) + ' 23:59:59'
        end_second = time.mktime(time.strptime(end_time, '%Y-%m-%d %H:%M:%S'))
        select_condition = 'WHERE sample_second>=' + str(begin_second) + ' AND sample_second<=' + str(end_second)

        selected_license_server_dic = self.curve_tab_server_combo.selectedItems()
        selected_license_server_list = list(selected_license_server_dic.values())
        selected_vendor_daemon_dic = self.curve_tab_vendor_combo.selectedItems()
        selected_vendor_daemon_list = list(selected_vendor_daemon_dic.values())
        specified_license_feature_list_input = self.curve_tab_feature_line.text().strip().split()

        self.update_db_info()

        # Filter with license_server/vendor_daemon/feature.
        for license_server in self.db_dic.keys():
            if ('ALL' in selected_license_server_list) or (license_server in selected_license_server_list):
                for vendor_daemon in self.db_dic[license_server].keys():
                    if ('ALL' in selected_vendor_daemon_list) or (vendor_daemon in selected_vendor_daemon_list):
                        if 'curve' in self.db_dic[license_server][vendor_daemon].keys():
                            curve_db_file = self.db_dic[license_server][vendor_daemon]['curve']
                            (curve_db_file_connect_result, curve_db_conn) = common_sqlite3.connect_db_file(curve_db_file)

                            if curve_db_file_connect_result == 'failed':
                                common.bprint('Failed on connecting curve database file "' + str(curve_db_file) + '".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                            else:
                                # Get specified_license_feature_list (space-separated keywords, fuzzy OR match).
                                curve_db_table_list = common_sqlite3.get_sql_table_list(curve_db_file, curve_db_conn)

                                if 'sqlite_sequence' in curve_db_table_list:
                                    curve_db_table_list.remove('sqlite_sequence')

                                specified_license_feature_list = []

                                if not specified_license_feature_list_input:
                                    specified_license_feature_list = curve_db_table_list
                                else:
                                    specified_license_feature_list = common_license.match_exact_or_fuzzy(specified_license_feature_list_input, curve_db_table_list)

                                # Get specified feature data.
                                for feature in specified_license_feature_list:
                                    data_dic = common_sqlite3.get_sql_table_data(curve_db_file, curve_db_conn, feature, key_list, select_condition)

                                    if data_dic:
                                        curve_dic.setdefault(feature, {})
                                        curve_dic[feature].setdefault(vendor_daemon, {'sample_data': {}, 'summary': {}})
                                        issued_list = []
                                        in_use_list = []

                                        # Get sample data.
                                        for (i, sample_time) in enumerate(data_dic['sample_time']):
                                            curve_dic[feature][vendor_daemon]['sample_data'].setdefault(sample_time, {'issued': 0.0, 'in_use': 0.0})
                                            issued_num = data_dic['issued'][i]
                                            in_use_num = data_dic['in_use'][i]

                                            if issued_num == 'Uncounted':
                                                curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['issued'] = 'Uncounted'
                                            else:
                                                if curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['issued'] != 'Uncounted':
                                                    curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['issued'] += float(issued_num)

                                            curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['in_use'] += float(in_use_num)

                                            # Collect summary information.
                                            issued_list.append(curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['issued'])
                                            in_use_list.append(curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['in_use'])

                                        # Get summary data.
                                        if 'Uncounted' in issued_list:
                                            avg_issued = 'Uncounted'
                                        else:
                                            avg_issued = round(sum(issued_list) / len(issued_list), 1)

                                        avg_in_use = round(sum(in_use_list) / len(in_use_list), 1)
                                        peak_in_use = max(in_use_list)
                                        curve_dic[feature][vendor_daemon]['summary'] = {'avg_issued': avg_issued, 'avg_in_use': avg_in_use, 'peak_in_use': peak_in_use}

                            curve_db_conn.close()

        if not curve_dic:
            common.bprint('No curve data is find.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

        return curve_dic

    def gen_curve_tab_table(self, curve_dic={}):
        """
        Generate self.curve_tab_table.
        """
        self.curve_tab_table.setShowGrid(True)
        self.curve_tab_table.setSortingEnabled(True)
        self.curve_tab_table.setColumnCount(0)
        self.curve_tab_table.setColumnCount(5)
        self.curve_tab_table_title_list = ['Feature', 'Vendor', 'Total', 'In_Use', 'Peak']
        self.curve_tab_table.setHorizontalHeaderLabels(self.curve_tab_table_title_list)

        self.curve_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.curve_tab_table.setColumnWidth(1, 80)
        self.curve_tab_table.setColumnWidth(2, 90)
        self.curve_tab_table.setColumnWidth(3, 60)
        self.curve_tab_table.setColumnWidth(4, 60)
        common_pyqt5.auto_size_table_columns(self.curve_tab_table)

        # Set self.curve_tab_table row length.
        row_length = 0

        for feature in curve_dic.keys():
            for vendor_daemon in curve_dic[feature].keys():
                row_length += 1

        self.curve_tab_table.setRowCount(0)
        self.curve_tab_table.setRowCount(row_length)

        # Fill self.curve_tab_table items.
        if curve_dic:
            i = -1

            for feature in curve_dic.keys():
                for vendor_daemon in curve_dic[feature].keys():
                    avg_issued = curve_dic[feature][vendor_daemon]['summary']['avg_issued']
                    avg_in_use = curve_dic[feature][vendor_daemon]['summary']['avg_in_use']
                    peak_in_use = curve_dic[feature][vendor_daemon]['summary']['peak_in_use']

                    i += 1

                    # Fill "Feature" item.
                    item = QTableWidgetItem(feature)
                    self.curve_tab_table.setItem(i, 0, item)

                    # Fill "Vendor" item.
                    item = QTableWidgetItem(vendor_daemon)
                    self.curve_tab_table.setItem(i, 1, item)

                    # Fill "Total" item.
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, avg_issued)
                    self.curve_tab_table.setItem(i, 2, item)

                    # Fill "In_Use" item.
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, avg_in_use)
                    self.curve_tab_table.setItem(i, 3, item)

                    # Fill "Peak" item.
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, peak_in_use)
                    self.curve_tab_table.setItem(i, 4, item)

    def curve_tab_table_click(self, item=None):
        """
        Click handler for self.curve_tab_table. When the user clicks the
        "Feature" column (column 0), copy that feature name into the Feature
        input line and trigger a Check so the curve is loaded immediately —
        equivalent to typing the feature name and pressing Check.
        Clicks on other columns (Vendor / numeric summary columns) are ignored.
        """
        if item is None or item.column() != 0:
            return

        current_row = self.curve_tab_table.currentRow()
        feature_item = self.curve_tab_table.item(current_row, 0)
        if feature_item is None:
            return

        feature = feature_item.text().strip()
        if not feature:
            return

        self.curve_tab_feature_line.setText(feature)
        self.filter_curve_tab()

    def _draw_curve_hint(self, hint_text):
        """Draw a centered hint message on the (empty) curve canvas.

        Used when no feature is selected or the selected feature has no data,
        so the user knows they must pick a feature and click Check to draw.
        All axis spines are hidden so the hint text is shown without a black
        border box (the empty-state QLabel hint overlays this area on init,
        and a visible axes frame there looks like an unwanted "black box").
        """
        fig = self.curve_tab_canvas.figure
        fig.clear()
        axes = fig.add_subplot(111)

        if self.dark_mode:
            axes.set_facecolor(theme.CHART_COLORS[True]['facecolor'])
            text_color = 'white'
        else:
            axes.set_facecolor(theme.CHART_COLORS[False]['facecolor'])
            text_color = '#666666'

        # Hide ticks and all spines so the hint renders as plain centered text
        # without a surrounding rectangle.
        axes.set_xticks([])
        axes.set_yticks([])
        for spine in axes.spines.values():
            spine.set_visible(False)

        axes.text(0.5, 0.5, hint_text, transform=axes.transAxes, ha='center', va='center', fontsize=12, color=text_color, wrap=True)
        self.curve_tab_canvas.draw()

    def gen_curve_tab_frame1(self):
        """
        Generate self.curve_tab_frame1.
        """
        # self.curve_tab_frame1
        self.curve_tab_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.curve_tab_toolbar = common_pyqt5.NavigationToolbar2QT(self.curve_tab_canvas, self)

        if self.dark_mode:
            fig = self.curve_tab_canvas.figure
            fig.set_facecolor(theme.CHART_COLORS[True]['figure_face'])

        # self.curve_tab_frame1 - Grid
        curve_tab_frame1_grid = QGridLayout()
        curve_tab_frame1_grid.addWidget(self.curve_tab_toolbar, 0, 0)
        curve_tab_frame1_grid.addWidget(self.curve_tab_canvas, 1, 0)
        self.curve_tab_frame1.setLayout(curve_tab_frame1_grid)

        # Canvas starts blank; the empty-state QLabel hint
        # (curve_tab_hint_label) overlays this area until the user clicks
        # Check. _draw_curve_hint() will be invoked from update_curve_tab_frame1
        # only if needed after the first Check attempt.

    def update_curve_tab_frame1(self, curve_dic={}):
        """
        Generate self.curve_tab_frame1.
        """
        # Generate fig.
        fig = self.curve_tab_canvas.figure
        fig.clear()
        self.curve_tab_canvas.draw()

        specified_license_feature = self.curve_tab_feature_line.text().strip()

        if not specified_license_feature:
            self._draw_curve_hint('Select a Feature and click Check to generate usage curve.')
            return
        elif specified_license_feature not in curve_dic:
            common.bprint('No valid feature is specified, will not generate curve.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            self._draw_curve_hint('Feature "' + str(specified_license_feature) + '" has no data. Please verify the feature name and click Check again.')
            return

        # Print loading curve information message.
        common.bprint('Processing curve info ...', date_format='%Y-%m-%d %H:%M:%S')

        # Get sample_time_list.
        sample_time_list = []

        for feature in curve_dic.keys():
            for vendor_daemon in curve_dic[feature].keys():
                sample_time_list.extend(list(curve_dic[feature][vendor_daemon]['sample_data'].keys()))

        sample_time_list = list(set(sample_time_list))
        sample_time_list.sort()

        # Get issued/in_use list.
        issued_list = []
        in_use_list = []

        for sample_time in sample_time_list:
            for feature in curve_dic.keys():
                for vendor_daemon in curve_dic[feature].keys():
                    if sample_time in curve_dic[feature][vendor_daemon]['sample_data'].keys():
                        issued_num = curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['issued']
                        in_use_num = curve_dic[feature][vendor_daemon]['sample_data'][sample_time]['in_use']

                        if issued_num == 'Uncounted':
                            issued_num = 0

                        issued_list.append(issued_num)
                        in_use_list.append(in_use_num)

        if sample_time_list and issued_list and in_use_list:
            # Update sample_time format.
            for (i, sample_time) in enumerate(sample_time_list):
                sample_time_list[i] = datetime.datetime.strptime(sample_time, '%Y%m%d_%H%M%S')

            # Draw curve.
            self.draw_curve_tab_curve(fig, sample_time_list, issued_list, in_use_list)

    def draw_curve_tab_curve(self, fig, sample_time_list, issued_list, in_use_list):
        """
        Draw average issued/in_use curve for specified feature(s).
        """
        axes = fig.add_subplot(111)
        avg_in_use = round((sum(in_use_list) / len(in_use_list)), 1)

        axes.plot(sample_time_list, issued_list, color=theme.CHART_SERIES[0], linewidth=theme.CHART_LINEWIDTH, label='TOTAL')
        axes.plot(sample_time_list, in_use_list, color=theme.CHART_RUN_COLOR, linewidth=theme.CHART_LINEWIDTH, label='IN_USE')
        axes.fill_between(sample_time_list, 0, in_use_list, color=theme.CHART_RUN_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        axes.tick_params(axis='x', rotation=15)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title='Average Used : ' + str(avg_in_use),
                                 xlabel='Sample Time',
                                 ylabel='Num')
        self.curve_tab_canvas.draw()
# For CURVE TAB (end) #

# For UTILIZATION TAB (start) #
    def gen_utilization_tab(self):
        """
        Generate UTILIZATION tab, show license feature utilization information.
        """
        self.utilization_tab_frame0 = QFrame(self.utilization_tab)
        self.utilization_tab_frame0.setFrameShadow(QFrame.Raised)
        self.utilization_tab_frame0.setFrameShape(QFrame.Box)

        self.utilization_tab_table = QTableWidget(self.utilization_tab)
        common_pyqt5.make_table_readonly(self.utilization_tab_table)
        self.utilization_tab_table.itemClicked.connect(self.utilization_tab_table_click)

        self.utilization_tab_frame1 = QFrame(self.utilization_tab)
        self.utilization_tab_frame1.setFrameShadow(QFrame.Raised)
        self.utilization_tab_frame1.setFrameShape(QFrame.Box)

        # Grid
        utilization_tab_grid = QGridLayout()

        utilization_tab_grid.addWidget(self.utilization_tab_frame0, 0, 0, 1, 2)
        utilization_tab_grid.addWidget(self.utilization_tab_table, 1, 0)
        utilization_tab_grid.addWidget(self.utilization_tab_frame1, 1, 1)

        utilization_tab_grid.setRowStretch(0, 1)
        utilization_tab_grid.setRowStretch(1, 10)

        utilization_tab_grid.setColumnStretch(0, 3)
        utilization_tab_grid.setColumnStretch(1, 7)

        self.utilization_tab.setLayout(utilization_tab_grid)

        # Generate self.utilization_tab_frame0, self.utilization_tab_table and self.utilization_tab_frame1.
        self.gen_utilization_tab_frame0()
        self.gen_utilization_tab_table()
        self.gen_utilization_tab_frame1()

        # NOTE: update_utilization_tab_frame1() is NOT called here — it runs
        # ShowMessage and data processing, which should only happen when the
        # user clicks "Check", not during init_ui construction.

        # Empty-state hint: the UTILIZATION table needs a "Check" to load data.
        # Shown over the chart frame (the tab's graphics area) so the filter
        # row and table stay interactive; hidden once a load is triggered.
        self.utilization_tab_hint_label = self._make_empty_hint(
            utilization_tab_grid, 1, 1, 1, 1, 'Click "Check" button to load license utilization information.'
        )

    def gen_utilization_tab_frame0(self):
        # License Server
        utilization_tab_server_label = QLabel('Server', self.utilization_tab_frame0)
        utilization_tab_server_label.setStyleSheet('font-weight: bold;')
        utilization_tab_server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_server_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0)
        self.set_utilization_tab_server_combo()

        # License vendor daemon
        utilization_tab_vendor_label = QLabel('Vendor', self.utilization_tab_frame0)
        utilization_tab_vendor_label.setStyleSheet('font-weight: bold;')
        utilization_tab_vendor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_vendor_combo = common_pyqt5.QComboCheckBox(self.utilization_tab_frame0)
        self.set_utilization_tab_vendor_combo()

        # License Feature
        utilization_tab_feature_label = QLabel('Feature', self.utilization_tab_frame0)
        utilization_tab_feature_label.setStyleSheet('font-weight: bold;')
        utilization_tab_feature_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_feature_line = QLineEdit()
        self.utilization_tab_feature_line.returnPressed.connect(self.filter_utilization_tab)

        utilization_tab_feature_line_completer = common_pyqt5.get_completer(self.feature_list)
        self.utilization_tab_feature_line.setCompleter(utilization_tab_feature_line_completer)

        # Check button
        utilization_tab_check_button = QPushButton('Check', self.utilization_tab_frame0)
        utilization_tab_check_button.clicked.connect(self.filter_utilization_tab)

        # Begin_Date
        utilization_tab_begin_date_label = QLabel('Begin_Date', self.utilization_tab_frame0)
        utilization_tab_begin_date_label.setStyleSheet("font-weight: bold;")
        utilization_tab_begin_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_begin_date_edit = QDateEdit(self.utilization_tab_frame0)
        self.utilization_tab_begin_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.utilization_tab_begin_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.utilization_tab_begin_date_edit.setCalendarPopup(True)
        self.utilization_tab_begin_date_edit.setDate(QDate.currentDate().addMonths(-1))

        # End_Date
        utilization_tab_end_date_label = QLabel('End_Date', self.utilization_tab_frame0)
        utilization_tab_end_date_label.setStyleSheet("font-weight: bold;")
        utilization_tab_end_date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.utilization_tab_end_date_edit = QDateEdit(self.utilization_tab_frame0)
        self.utilization_tab_end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.utilization_tab_end_date_edit.setMinimumDate(QDate.currentDate().addDays(-3652))
        self.utilization_tab_end_date_edit.setCalendarPopup(True)
        self.utilization_tab_end_date_edit.setDate(QDate.currentDate())

        # License Product
        # Export is provided via the File menu (Export License utilization
        # table); no in-tab Export button to avoid duplication.

        # self.utilization_tab_frame0 - Grid
        utilization_tab_frame0_grid = QGridLayout()

        utilization_tab_frame0_grid.addWidget(utilization_tab_begin_date_label, 0, 0)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_begin_date_edit, 0, 1)
        utilization_tab_frame0_grid.addWidget(utilization_tab_end_date_label, 0, 2)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_end_date_edit, 0, 3)
        utilization_tab_frame0_grid.addWidget(utilization_tab_server_label, 0, 4)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_server_combo, 0, 5)
        utilization_tab_frame0_grid.addWidget(utilization_tab_vendor_label, 0, 6)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_vendor_combo, 0, 7)
        utilization_tab_frame0_grid.addWidget(utilization_tab_feature_label, 0, 8)
        utilization_tab_frame0_grid.addWidget(self.utilization_tab_feature_line, 0, 9)
        utilization_tab_frame0_grid.addWidget(utilization_tab_check_button, 0, 10)

        for col in range(11):
            utilization_tab_frame0_grid.setColumnStretch(col, 1)

        utilization_tab_frame0_grid.setColumnStretch(9, 3)

        self.utilization_tab_frame0.setLayout(utilization_tab_frame0_grid)

    def set_utilization_tab_server_combo(self):
        """
        Set (initialize) self.utilization_tab_server_combo.
        """
        # Get license_server list.
        license_server_list = self.get_license_server_list()
        license_server_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.utilization_tab_server_combo, license_server_list)

    def set_utilization_tab_vendor_combo(self):
        """
        Set (initialize) self.utilization_tab_vendor_combo.
        """
        # Get vendor_daemon list.
        vendor_daemon_list = self.get_vendor_daemon_list()
        vendor_daemon_list.insert(0, 'ALL')

        self.set_common_checkbox_combo(self.utilization_tab_vendor_combo, vendor_daemon_list)

    def filter_utilization_tab(self):
        """
        Update self.utilization_tab_table and self.utilization_tab_frame1.
        """
        self.hide_empty_hint(getattr(self, 'utilization_tab_hint_label', None))

        # One loading prompt spans data fetch, table render, and curve drawing
        # so it stays up until the result is on screen. Inner methods no
        # longer raise their own (premature) prompts.
        my_show_message = ShowMessage('Info', 'Loading utilization info, please wait a moment ...', persistent=True)
        my_show_message.start()

        # Let the message window render before blocking the GUI thread.
        QApplication.processEvents()
        time.sleep(0.2)
        QApplication.processEvents()

        utilization_dic = self.get_utilization_info()

        if utilization_dic:
            self.gen_utilization_tab_table(utilization_dic)
            self.update_utilization_tab_frame1(utilization_dic)

        my_show_message.terminate()

    def get_utilization_info(self):
        """
        Get utilization information from <license db_path>/license_server/<license_server>/<vendor_deamon>/utilization(_day).db.
        """
        # Print loading utilization information message.
        common.bprint('Load utilization info ...', date_format='%Y-%m-%d %H:%M:%S')

        utilization_dic = {}

        if self.enable_utilization_detail:
            key_list = ['sample_time', 'issued', 'in_use']
            begin_date = self.utilization_tab_begin_date_edit.date().toString(Qt.ISODate)
            begin_time = str(begin_date) + ' 00:00:00'
            begin_second = time.mktime(time.strptime(begin_time, '%Y-%m-%d %H:%M:%S'))
            end_date = self.utilization_tab_end_date_edit.date().toString(Qt.ISODate)
            end_time = str(end_date) + ' 23:59:59'
            end_second = time.mktime(time.strptime(end_time, '%Y-%m-%d %H:%M:%S'))
            select_condition = 'WHERE sample_second>=' + str(begin_second) + ' AND sample_second<=' + str(end_second)
        else:
            key_list = ['sample_date', 'issued', 'in_use']
            begin_date = self.utilization_tab_begin_date_edit.date().toString(Qt.ISODate)
            begin_date = re.sub('-', '', begin_date)
            end_date = self.utilization_tab_end_date_edit.date().toString(Qt.ISODate)
            end_date = re.sub('-', '', end_date)
            select_condition = 'WHERE sample_date>=' + str(begin_date) + ' AND sample_date<=' + str(end_date)

        selected_license_server_dic = self.utilization_tab_server_combo.selectedItems()
        selected_license_server_list = list(selected_license_server_dic.values())
        selected_vendor_daemon_dic = self.utilization_tab_vendor_combo.selectedItems()
        selected_vendor_daemon_list = list(selected_vendor_daemon_dic.values())
        selected_license_feature_list = self.utilization_tab_feature_line.text().strip().split()

        self.update_db_info()

        # Filter with license_server/vendor_daemon/feature.
        for license_server in self.db_dic.keys():
            if ('ALL' in selected_license_server_list) or (license_server in selected_license_server_list):
                for vendor_daemon in self.db_dic[license_server].keys():
                    if ('ALL' in selected_vendor_daemon_list) or (vendor_daemon in selected_vendor_daemon_list):
                        if 'utilization' in self.db_dic[license_server][vendor_daemon].keys():
                            utilization_db_file = self.db_dic[license_server][vendor_daemon]['utilization']
                            (utilization_db_file_connect_result, utilization_db_conn) = common_sqlite3.connect_db_file(utilization_db_file)

                            if utilization_db_file_connect_result == 'failed':
                                common.bprint('Failed on connecting utilization database file "' + str(utilization_db_file) + '".', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
                            else:
                                # Get specified_license_feature_list.
                                utilization_db_table_list = common_sqlite3.get_sql_table_list(utilization_db_file, utilization_db_conn)
                                specified_license_feature_list = self.count_specified_license_feature_list(utilization_db_table_list, vendor_daemon, selected_license_feature_list)

                                # Get specified feature data.
                                for feature in specified_license_feature_list:
                                    data_dic = common_sqlite3.get_sql_table_data(utilization_db_file, utilization_db_conn, feature, key_list, select_condition)

                                    if data_dic:
                                        # Save sample data.
                                        utilization_dic.setdefault(feature, {})
                                        utilization_dic[feature].setdefault(vendor_daemon, {'sample_data': {}, 'summary': {}})

                                        if self.enable_utilization_detail:
                                            key = 'sample_time'
                                        else:
                                            key = 'sample_date'

                                        for (i, sample_date) in enumerate(data_dic[key]):
                                            utilization_dic[feature][vendor_daemon]['sample_data'].setdefault(sample_date, {'issued': 0.0, 'in_use': 0.0})
                                            issued_num = data_dic['issued'][i]
                                            in_use_num = data_dic['in_use'][i]

                                            if issued_num == 'Uncounted':
                                                utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['issued'] = 'Uncounted'
                                            else:
                                                if utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['issued'] != 'Uncounted':
                                                    utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['issued'] += float(issued_num)

                                            utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['in_use'] += float(in_use_num)

                            utilization_db_conn.close()

        # Count utilizaton/avg_utilization information. (Aggregated data from different license servers)
        for feature in utilization_dic.keys():
            for vendor_daemon in utilization_dic[feature].keys():
                issued_list = []
                in_use_list = []

                for sample_date in utilization_dic[feature][vendor_daemon]['sample_data'].keys():
                    # Get feature sample_date 'utilization' info.
                    issued_num = utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['issued']
                    in_use_num = utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['in_use']
                    utilization = 0.0

                    if issued_num == 'Uncounted':
                        if in_use_num > 0:
                            utilization = 100.0
                        else:
                            utilization = 0.0
                    else:
                        utilization = round(100 * in_use_num / issued_num, 1) if issued_num else 0.0

                    utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['utilization'] = utilization

                    issued_list.append(issued_num)
                    in_use_list.append(in_use_num)

                # Get feature 'avg_utilization' info.
                if 'Uncounted' in issued_list:
                    if sum(in_use_list) > 0:
                        avg_utilization = 100.0
                    else:
                        avg_utilization = 0.0
                else:
                    avg_utilization = round(100 * sum(in_use_list) / sum(issued_list), 1) if sum(issued_list) else 0.0

                utilization_dic[feature][vendor_daemon]['summary'] = {'avg_utilization': avg_utilization}

        if not utilization_dic:
            common.bprint('No utilization data is find.', date_format='%Y-%m-%d %H:%M:%S', level='Warning')

        return utilization_dic

    def count_specified_license_feature_list(self, feature_list, vendor_daemon, selected_license_feature_list=None):
        """
        Based on selected_license_feature_list, count specified_license_feature_list.
        """
        specified_license_feature_list = []

        if 'sqlite_sequence' in feature_list:
            feature_list.remove('sqlite_sequence')

        if not selected_license_feature_list:
            specified_license_feature_list = feature_list
        else:
            # Exact-preferred match: exact match wins; fuzzy only when no exact match.
            specified_license_feature_list = common_license.match_exact_or_fuzzy(selected_license_feature_list, feature_list)

        return specified_license_feature_list

    def gen_utilization_tab_table(self, utilization_dic=None):
        """
        Generate self.utilization_tab_table.
        """
        if utilization_dic is None:
            utilization_dic = {}

        self.utilization_tab_table_title_list = ['Feature', 'Vendor', 'Ut (%)']

        self.utilization_tab_table.setShowGrid(True)
        self.utilization_tab_table.setSortingEnabled(True)
        self.utilization_tab_table.setColumnCount(0)
        self.utilization_tab_table.setColumnCount(3)
        self.utilization_tab_table.setHorizontalHeaderLabels(self.utilization_tab_table_title_list)

        self.utilization_tab_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.utilization_tab_table.setColumnWidth(1, 80)
        self.utilization_tab_table.setColumnWidth(2, 60)
        common_pyqt5.auto_size_table_columns(self.utilization_tab_table)

        # Set self.utilization_tab_table row length.
        row_length = 0

        for feature in utilization_dic.keys():
            for vendor_daemon in utilization_dic[feature].keys():
                row_length += 1

        self.utilization_tab_table.setRowCount(0)
        self.utilization_tab_table.setRowCount(row_length)

        # Fill self.utilization_tab_table items.
        if utilization_dic:
            i = -1

            for feature in utilization_dic.keys():
                for vendor_daemon in utilization_dic[feature].keys():
                    avg_utilization = utilization_dic[feature][vendor_daemon]['summary']['avg_utilization']
                    i += 1

                    # Fill "feature" item.
                    item = QTableWidgetItem(feature)
                    self.utilization_tab_table.setItem(i, 0, item)

                    # Fill "vendor" item.
                    item = QTableWidgetItem(vendor_daemon)
                    self.utilization_tab_table.setItem(i, 1, item)

                    # Fill "utilization" item.
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, avg_utilization)

                    if avg_utilization >= 80:
                        item.setForeground(QBrush(QColor(STATUS_EXIT)))
                    elif int(avg_utilization) == 0:
                        item.setForeground(QBrush(QColor(STATUS_DONE)))

                    self.utilization_tab_table.setItem(i, 2, item)

    def utilization_tab_table_click(self, item=None):
        """
        Click handler for self.utilization_tab_table. When the user clicks the
        first column ("Feature", or "Product" when product view is enabled),
        copy that text into the corresponding input line and trigger a Check
        so the utilization detail is loaded immediately — equivalent to typing
        the name and pressing Check.
        Clicks on other columns (Vendor / Ut (%)) are ignored.
        """
        if item is None or item.column() != 0:
            return

        current_row = self.utilization_tab_table.currentRow()
        name_item = self.utilization_tab_table.item(current_row, 0)
        if name_item is None:
            return

        name = name_item.text().strip()
        if not name:
            return

        self.utilization_tab_feature_line.setText(name)

        self.filter_utilization_tab()

    def gen_utilization_tab_frame1(self):
        """
        Generate self.utilization_tab_frame1.
        """
        # self.utilization_tab_frame1
        self.utilization_tab_canvas = common_pyqt5.FigureCanvasQTAgg()
        self.utilization_tab_toolbar = common_pyqt5.NavigationToolbar2QT(self.utilization_tab_canvas, self)

        if self.dark_mode:
            fig = self.utilization_tab_canvas.figure
            fig.set_facecolor(theme.CHART_COLORS[True]['figure_face'])

        # self.utilization_tab_frame1 - Grid
        utilization_tab_frame1_grid = QGridLayout()
        utilization_tab_frame1_grid.addWidget(self.utilization_tab_toolbar, 0, 0)
        utilization_tab_frame1_grid.addWidget(self.utilization_tab_canvas, 1, 0)
        self.utilization_tab_frame1.setLayout(utilization_tab_frame1_grid)

    def update_utilization_tab_frame1(self, utilization_dic={}):
        """
        Generate self.utilization_tab_frame1.
        """
        # Generate fig.
        fig = self.utilization_tab_canvas.figure
        fig.clear()
        self.utilization_tab_canvas.draw()

        # Print loading utilization information message.
        common.bprint('Process utilization info ...', date_format='%Y-%m-%d %H:%M:%S')

        # Get sample_date_list.
        sample_date_list = []

        for feature in utilization_dic.keys():
            for vendor_daemon in utilization_dic[feature].keys():
                sample_date_list.extend(list(utilization_dic[feature][vendor_daemon]['sample_data'].keys()))

        sample_date_list = list(set(sample_date_list))
        sample_date_list.sort()

        # Get utilization_list.
        utilization_list = []

        for sample_date in sample_date_list:
            sample_date_utilization_list = []

            for feature in utilization_dic.keys():
                for vendor_daemon in utilization_dic[feature].keys():
                    if sample_date in utilization_dic[feature][vendor_daemon]['sample_data'].keys():
                        sample_date_utilization_list.append(utilization_dic[feature][vendor_daemon]['sample_data'][sample_date]['utilization'])

            avg_sample_date_utilzation = round(sum(sample_date_utilization_list) / len(sample_date_utilization_list), 1)
            utilization_list.append(avg_sample_date_utilzation)

        if sample_date_list and utilization_list:
            # Update sample_date format.
            for (i, sample_date) in enumerate(sample_date_list):
                if self.enable_utilization_detail:
                    sample_date_list[i] = datetime.datetime.strptime(sample_date, '%Y%m%d_%H%M%S')
                else:
                    sample_date_list[i] = datetime.datetime.strptime(sample_date, '%Y%m%d')

            # Get avg_utilization.
            avg_utilization = round(sum(utilization_list) / len(utilization_list), 1)

            # Draw utilization curve.
            self.draw_utilization_tab_curve(fig, avg_utilization, sample_date_list, utilization_list)

    def draw_utilization_tab_curve(self, fig, avg_utilization, sample_date_list, utilization_list):
        """
        Draw average utilization curve for specified feature(s).
        """
        axes = fig.add_subplot(111)

        axes.plot(sample_date_list, utilization_list, color=theme.CHART_UT_COLOR, linewidth=theme.CHART_LINEWIDTH, label='UT')
        axes.fill_between(sample_date_list, 0, utilization_list, color=theme.CHART_UT_COLOR, alpha=0.15)
        axes.legend(loc='upper right', frameon=False)
        axes.tick_params(axis='x', rotation=15)
        common_pyqt5.style_axes(axes, dark_mode=self.dark_mode,
                                 title='Average Utilization : ' + str(avg_utilization) + '%',
                                 xlabel='Sample Time' if self.enable_utilization_detail else 'Sample Date',
                                 ylabel='Utilization (%)')
        self.utilization_tab_canvas.draw()
# For UTILIZATION TAB (end) #


# Export table (start) #
    def export_server_table(self):
        self.export_table('server', self.server_tab_table, self.server_tab_table_title_list)

    def export_feature_table(self):
        self.export_table('feature', self.feature_tab_table, self.feature_tab_table_title_list)

    def export_expires_table(self):
        self.export_table('expires', self.expires_tab_table, self.expires_tab_table_title_list)

    def export_usage_table(self):
        self.export_table('usage', self.usage_tab_table, self.usage_tab_table_title_list)

    def export_curve_table(self):
        self.export_table('curve', self.curve_tab_table, self.curve_tab_table_title_list)

    def export_utilization_table(self):
        self.export_table('utilization', self.utilization_tab_table, self.utilization_tab_table_title_list)

    def export_table(self, table_type, table_item, title_list):
        """
        Export specified table info into an Excel.
        """
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_time_string = re.sub('-', '', current_time)
        current_time_string = re.sub(':', '', current_time_string)
        current_time_string = re.sub(' ', '_', current_time_string)
        default_output_file = './license_' + str(table_type) + '_' + str(current_time_string) + '.csv'
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
            common.bprint('Writing ' + str(table_type) + ' table into "' + str(output_file) + '" ...', date_format='%Y-%m-%d %H:%M:%S')

            common.write_csv(csv_file=output_file, content_dic=content_dic)

# Export table (end) #

    def closeEvent(self, event):
        """
        When window close, post-process.
        """
        common.bprint('Bye', date_format='%Y-%m-%d %H:%M:%S')


#################
# Main Function #
#################
class LicenseLoadThread(QThread):
    """Background loader that runs lmstat via common_license.GetLicenseInfo.

    The lmstat call can take several seconds; running it off the GUI thread
    keeps the panel responsive (showing its loading overlay) while it runs.
    The resulting license dict is emitted via ``loaded`` and consumed on the
    GUI thread.
    """

    loaded = pyqtSignal(dict)

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def run(self):
        license_dic = self._fetch_license_info()
        self.loaded.emit(license_dic or {})

    def _fetch_license_info(self):
        """Mirror of LicensePanel.get_license_dic's data-fetching body.

        Runs entirely in this thread: configures LM_LICENSE_FILE from the
        platform file (admin only), resolves lmstat, and returns the parsed
        license dict. Does NOT touch any QWidget.
        """
        panel = self._panel

        LM_LICENSE_FILE_file = str(_LSFMONITOR_INSTALL_PATH) + '/config/license/LM_LICENSE_FILE'

        is_admin = (
            ('all' in panel.administrator_list) or
            ('ALL' in panel.administrator_list) or
            (USER in panel.administrator_list)
        )

        if os.path.exists(LM_LICENSE_FILE_file) and is_admin:
            # Collect non-empty/non-comment lines first; only override the
            # LM_LICENSE_FILE environment variable when the platform file
            # actually yields servers. An empty (comment-only) file must fall
            # back to the shell's LM_LICENSE_FILE instead of clobbering it.
            parsed_servers = []

            with open(LM_LICENSE_FILE_file, 'r') as LLF:
                for line in LLF.readlines():
                    line = line.strip()

                    if (not re.match(r'^\s*$', line)) and (not re.match(r'^\s*#.*$', line)):
                        parsed_servers.append(line)

            if parsed_servers:
                os.environ['LM_LICENSE_FILE'] = ':'.join(parsed_servers)

        if not hasattr(config, 'lmstat_path'):
            config.lmstat_path = ''
        elif config.lmstat_path and not os.path.exists(config.lmstat_path):
            common.bprint('"' + str(config.lmstat_path) + '": no such lmstat file!', date_format='%Y-%m-%d %H:%M:%S', level='Warning')
            config.lmstat_path = ''

        if not hasattr(config, 'lmstat_bsub_command'):
            config.lmstat_bsub_command = ''

        my_get_license_info = common_license.GetLicenseInfo(lmstat_path=config.lmstat_path, bsub_command=config.lmstat_bsub_command)

        return my_get_license_info.get_license_info()


def main():
    """Entry point: forward to the unified MainWindow focused on the License panel.

    license_monitor keeps its own -f/-u/-t/-d args (same names as the unified
    entry), so we inject --panel=license and delegate to gui.main_window.main().
    """
    sys.argv = [sys.argv[0]] + ['--panel', 'license'] + sys.argv[1:]

    from gui import main_window

    main_window.main()


if __name__ == "__main__":
    main()
