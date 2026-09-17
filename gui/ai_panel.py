# -*- coding: utf-8 -*-
################################
# File Name   : ai_panel.py
# Author      : liyanqing.1987
# Created On  : 2026-08-24
# Description : AI helpdesk panel. Owns an inner QTabWidget with an 'AI' chat
#               tab, and contributes an AI menu to the host menubar.
################################

import os
import sys
import re
import shlex
import time
import getpass
import datetime
import html

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QPainter, QPainterPath, QPen, QPixmap, QTextCharFormat, QTextBlockFormat, QTextImageFormat, QTextLength, QTextTableFormat, QColor, QFont
from PyQt5.QtWidgets import QWidget, QGridLayout, QTextEdit, QPushButton, QMessageBox, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSplitter, QTabWidget, QLineEdit, QFrame, QMenu, QAction, QApplication, QDialog

# QWebEngineView renders HTML reports inline (optional; falls back to browser if
# absent). Disable GPU compositing to avoid "Xlib: sequence lost" on some X setups.
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

# root 下 Chromium sandbox 会崩,故 root 时不加载 QWebEngineView,走浏览器降级。
IS_ROOT = os.geteuid() == 0

try:
    if IS_ROOT:
        raise ImportError('root: QWebEngineView disabled (Chromium sandbox)')

    from PyQt5.QtWebEngineWidgets import QWebEngineView

    HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    HAS_WEBENGINE = False

LSFMONITOR_INSTALL_PATH = os.environ.get('LSFMONITOR_INSTALL_PATH', '.')

if LSFMONITOR_INSTALL_PATH not in sys.path:
    sys.path.append(LSFMONITOR_INSTALL_PATH)

from common import common
from common import common_ai
from common import common_config
from common import common_db_path
from common import common_pyqt5

from gui.panel_base import PanelBase
from gui.theme import PRIMARY, PRIMARY_BG, STATUS_RUN

# lsf config_lsf (db_path, lmstat_*) and ai config_lsf (ai_*). Loaded by panel name so
# each panel config_lsf lives in its own sys.modules slot.
config_lsf = common_config.load_config('lsf')
config_ai = common_config.load_config('ai')
config_license = common_config.load_config('license')

from gui.version import USER


# ANALYZE 子页配置：每个 report kind 的差异收敛到这份 dict，供子 tab 构建、
# 报告列表加载、analyze 触发/完成文案等统一复用。
_ANALYZE_TAB_CONFIG_DICT = {
    'job': {
        'title': 'JOB',
        'button': 'Analyze Specified Job',
        'input_label': 'Job(s):',
        'input_placeholder': '输入 jobid（多个用空格或逗号分隔，回车生成）',
        'no_target': '请输入要分析的 jobid。',
        'placeholder': '点击 Analyze Specified Job 生成单作业分析报告',
        'empty': '暂无作业报告。点击 Analyze Specified Job 生成。',
        'dialog': 'Job Analyze',
        'working': '正在分析作业 {target}，请稍候 ...',
        'done_prefix': '作业分析完成',
    },
    'user': {
        'title': 'USER',
        'button': 'Analyze User Jobs',
        'input_label': 'User(s):',
        'input_placeholder': '用户名（默认当前用户，多个用空格或逗号分隔，回车生成）',
        'input_default': getpass.getuser(),
        'no_target': '请输入要分析的用户名。',
        'placeholder': '点击 Analyze User Jobs 生成用户作业分析报告',
        'empty': '暂无用户报告。点击 Analyze User Jobs 生成。',
        'dialog': 'User Jobs Analyze',
        'working': '正在分析用户 {target} 的作业，请稍候 ...',
        'done_prefix': '用户作业分析完成',
    },
    'queue': {
        'title': 'QUEUE',
        'button': 'Analyze Queue Load',
        'input_label': 'Queue(s):',
        'input_placeholder': '输入队列名（多个用空格或逗号分隔，回车生成）',
        'no_target': '请输入要分析的队列名。',
        'placeholder': '点击 Analyze Queue Load 生成队列负载分析报告',
        'empty': '暂无队列报告。点击 Analyze Queue Load 生成。',
        'dialog': 'Queue Analyze',
        'working': '正在分析队列 {target}，请稍候 ...',
        'done_prefix': '队列分析完成',
    },
    'cluster': {
        'title': 'CLUSTER',
        'button': 'Analyze Cluster Status',
        'placeholder': '点击 Analyze Cluster Status 生成集群分析报告',
        'empty': '暂无集群报告。点击 Analyze Cluster Status 生成。',
        'dialog': 'Cluster Analyze',
        'working': '正在分析集群状况，请稍候 ...',
        'done_prefix': '集群分析完成',
    },
}


class AiPanel(PanelBase):
    """
    AI helpdesk panel. Provides an AI chat tab and an AI menu with
    record search, problem analyze, cluster analyze and record cleanup.
    """

    def __init__(self, context, parent=None, args=None):
        super().__init__(context, parent)

        # AI state variables.
        self.ai_thread = None
        self.ai_messages = []
        self.ai_doc_chunks = {"chunks": [], "embeddings": None}
        self.ai_skills = []
        self.ai_configured = False
        self.dark_mode = getattr(args, 'dark_mode', False) if args else False

        # Build the AI chat tab UI and load AI resources.
        self.build_ai_tab()

    def apply_entry_args(self, args):
        """AI panel has no special entry args."""
        pass

    def cleanup(self):
        """Stop AI thread if running (called on application exit)."""
        if self.ai_thread and self.ai_thread.isRunning():
            self.ai_thread.stop()
            self.ai_thread.wait(3000)

    # For AI TAB (begin) #
    def build_ai_tab(self):
        """
        Generate the AI helpdesk tab.
        """
        global config_ai

        # Reload config_ai with a cluster-specific override if one exists, so a
        # single install can serve multiple clusters with different AI settings
        # (api_base_url / api_key / model). Cluster is populated by LsfPanel
        # during its init; AI panel is lazy-built when first shown, so context
        # already has it. Falls back to the base config when no override exists.
        cluster = getattr(self.context, 'cluster', None)

        if cluster:
            try:
                config_ai = common_config.reload_config_for_cluster(cluster, 'ai')
            except Exception as error:
                common.bprint(f'Failed to reload cluster AI config for "{cluster}": {error}', level='Warning')

        # Check if AI is configured.
        self.ai_configured = False

        if hasattr(config_ai, 'ai_api_base_url') and config_ai.ai_api_base_url and hasattr(config_ai, 'ai_api_key') and config_ai.ai_api_key and hasattr(config_ai, 'ai_model_name') and config_ai.ai_model_name:
            self.ai_configured = True

        # Container widget for the AI tab.
        self.ai_tab = QWidget()

        # Chat display area.
        self.ai_tab_chat_text = QTextEdit(self.ai_tab)
        self.ai_tab_chat_text.setReadOnly(True)
        # Custom context menu: Copy explicitly writes the selection to the
        # system clipboard (QClipboard.Clipboard mode = Ctrl+V target). The
        # default QTextEdit Copy can land in the X11 PRIMARY selection on some
        # desktops (ETX/X11), so Ctrl+V into a terminal/editor pastes nothing.
        self.ai_tab_chat_text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ai_tab_chat_text.customContextMenuRequested.connect(self._chat_context_menu)
        self._create_chat_avatars()

        if not self.ai_configured:
            self.ai_tab_chat_text.setHtml('<p>AI helpdesk is not configured.</p><p>Please set <b>ai_api_base_url</b>, <b>ai_api_key</b>, and <b>ai_model_name</b> in config_lsf.py.</p>')
        else:
            self.ai_tab_chat_text.setHtml(self._welcome_html())

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

        # Layout.
        ai_tab_grid = QGridLayout()
        ai_tab_grid.setContentsMargins(8, 8, 8, 8)
        ai_tab_grid.setSpacing(6)
        ai_tab_grid.addWidget(self.ai_tab_chat_text, 0, 0, 1, 2)
        ai_tab_grid.addWidget(self.ai_tab_input, 1, 0)
        ai_tab_grid.addLayout(button_layout, 1, 1)
        ai_tab_grid.setColumnStretch(0, 10)
        ai_tab_grid.setColumnStretch(1, 1)
        self.ai_tab.setLayout(ai_tab_grid)

        # Chat display + input box: light/dark canvas to match the active theme.
        # ai_panel previously hardcoded #FFFFFF so the whole panel stayed white in
        # dark mode while the rest of the window went dark.
        if self.dark_mode:
            chat_bg = '#19232D'
            chat_border = '#3A4452'
            input_bg = '#1E2A35'
            input_border = '#3A4452'
            input_text = '#E6EAF0'
            # AI message bubble + text colours for dark mode (black text on a white
            # bubble is invisible in dark mode).
            self._ai_bubble_bg = '#2A3744'
            self._ai_text_color = '#E6EAF0'
            # User message bubble: a dark blue-grey that sits on the dark canvas
            # instead of the light-blue PRIMARY_BG, which read as a white block.
            self._user_bubble_bg = '#2A3D55'
            self._user_text_color = '#E6EAF0'
        else:
            chat_bg = '#FFFFFF'
            chat_border = '#E5E9F0'
            input_bg = '#FFFFFF'
            input_border = '#CBD5E1'
            input_text = '#0F172A'
            self._ai_bubble_bg = '#F8FAFC'
            self._ai_text_color = '#000000'
            self._user_bubble_bg = PRIMARY_BG
            self._user_text_color = '#0F172A'

        self.ai_tab_chat_text.setStyleSheet(f"""
            QTextEdit {{
                background: {chat_bg};
                border: 1px solid {chat_border};
                border-radius: 6px;
                padding: 6px;
            }}
        """)

        # Input box: rounded, soft border, focus accent.
        self.ai_tab_input.setStyleSheet(f"""
            QTextEdit {{
                background: {input_bg};
                border: 1px solid {input_border};
                border-radius: 6px;
                padding: 8px 10px;
                color: {input_text};
            }}
            QTextEdit:focus {{
                border: 1px solid #2563EB;
            }}
        """)

        # Add to main_tab.
        self.main_tab.addTab(self.ai_tab, 'AI')

        # Build the ANALYZE tab (Cluster Analyze button + history report list).
        self.build_analyze_tab()

        # Init conversation history.
        self.ai_messages = [{"role": "system", "content": common_ai.SYSTEM_PROMPT + f"\n\nCurrent user: {USER}"}]

        # AI data root: resolve via config_ai.db_path or fallback to config.py/ai.
        # RAG vectors live under this root; rag_builder writes into <root>/rag.
        install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '.')

        ai_db_root = common_db_path.resolve_db_path(config_ai, 'ai')

        # Ensure the root exists (sticky, 1777) so rag_builder can write into it.
        common.create_dir(ai_db_root, 0o1777)

        # Load AI documents (RAG vectors or keyword chunks) in background.
        common.bprint('Loading AI documents ...', date_format='%Y-%m-%d %H:%M:%S')
        self.ai_doc_chunks = {"chunks": [], "embeddings": None}
        docs_dir = os.path.join(ai_db_root, 'rag')
        self._doc_loader = common_ai.DocLoaderThread(docs_dir)
        self._doc_loader.finished_signal.connect(lambda doc_data: setattr(self, 'ai_doc_chunks', doc_data))
        self._doc_loader.start()

        # Load skills.
        common.bprint('Loading AI skills ...', date_format='%Y-%m-%d %H:%M:%S')
        skills_dir = os.path.join(install_path, 'config', 'ai')
        self.ai_skills = common_ai.load_skills(skills_dir)

    def build_analyze_tab(self):
        """Build the ANALYZE tab: JOB/USER/QUEUE/CLUSTER sub-tabs, each with button + list + web."""
        self.analyze_tab = QWidget()
        layout = QVBoxLayout(self.analyze_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._analyze_views = {}

        # Analyze thread tracking: keyed by (kind, target) so multiple jobs
        # can run concurrently (each jobid is its own key). Other kinds
        # (user/queue/cluster) naturally have a single target.
        self._analyze_threads = {}    # (kind, target) -> AnalyzeReportThread
        self._analyze_msgboxes = {}   # (kind, target) -> QMessageBox
        self._analyze_canceled = {}   # (kind, target) -> bool
        self._analyze_starts = {}     # (kind, target) -> float (start time)

        # Wrap the QTabWidget in a QFrame (Box+Raised) that paints the tab-
        # strip background. QTabWidget under Fusion+QSS does NOT paint the
        # area to the right of the last tab — it shows through to the parent.
        # This is the EXACT same pattern used by lsf_panel.job_tab_frame3 /
        # chartFrame (JOB page MEMORY/IDLE_FACTOR sub-tabs), which is proven
        # to work correctly: the frame (chartFrame/analyzeFrame) fills the
        # right-side gap with a tuned background color, and the QTabWidget's
        # QSS paints its own QTabBar/QSS area on top.
        self.analyze_subtabs_frame = QFrame(self.analyze_tab)
        self.analyze_subtabs_frame.setFrameShadow(QFrame.Raised)
        self.analyze_subtabs_frame.setFrameShape(QFrame.Box)
        self.analyze_subtabs_frame.setObjectName('analyzeFrame')
        _frame_layout = QVBoxLayout(self.analyze_subtabs_frame)
        _frame_layout.setContentsMargins(0, 0, 0, 0)

        self.analyze_subtabs = QTabWidget(self.analyze_subtabs_frame)
        self.analyze_subtabs.setObjectName('analyzeSubTab')
        # BoldAwareTabBar prevents label clipping when font-weight:600 kicks in
        # on the selected tab (same reason as chartTab / mainTab).
        self.analyze_subtabs.setTabBar(common_pyqt5.BoldAwareTabBar())
        # Do NOT set autoFillBackground / QPalette on the QTabWidget — that
        # creates two overlapping paint layers (frame + widget) and causes a
        # visible colour seam. chartTab also skips these and relies solely on
        # the wrapping frame to fill the gap.

        for kind in ('job', 'user', 'queue', 'cluster'):
            tab = self._build_report_subtab(kind, _ANALYZE_TAB_CONFIG_DICT[kind])
            self.analyze_subtabs.addTab(tab, _ANALYZE_TAB_CONFIG_DICT[kind]['title'])

        self.analyze_subtabs.setCurrentIndex(0)
        _frame_layout.addWidget(self.analyze_subtabs)
        layout.addWidget(self.analyze_subtabs_frame, 1)

        # Lazy-load name/queue completers on first edit of the input.
        for kind in ('user', 'queue'):
            input_widget = self._analyze_views[kind]['input']

            if input_widget is not None:
                input_widget.textChanged.connect(lambda _text, k=kind: self._ensure_input_completer(k))

        self.main_tab.addTab(self.analyze_tab, 'ANALYZE')

        # Populate with existing reports (deferred so it doesn't block startup).
        QTimer.singleShot(0, self.load_analyze_reports)

    def _build_report_subtab(self, kind, cfg):
        """Build one ANALYZE sub-tab (action bar + splitter[list + web]) from its config."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(6)

        # Action bar (QFrame matches lsf_panel frame0 style).
        action_frame = QFrame(tab)
        action_frame.setFrameShadow(QFrame.Raised)
        action_frame.setFrameShape(QFrame.Box)
        bar = QHBoxLayout(action_frame)

        button = QPushButton(cfg['button'], action_frame)
        button.setFixedHeight(28)
        # QPushButton.clicked 会发射 checked(bool) 参数，须用 _checked 占位接收，
        # 否则会覆盖默认参数 k，导致 _start_analyze 收到 False。
        button.clicked.connect(lambda _checked, k=kind: self._start_analyze(k))
        bar.addWidget(button)

        input_widget = None

        if cfg.get('input_label'):
            bar.addSpacing(12)
            bar.addWidget(QLabel(cfg['input_label']))
            input_widget = QLineEdit(action_frame)

            if cfg.get('input_default'):
                input_widget.setText(cfg['input_default'])

            input_widget.setPlaceholderText(cfg['input_placeholder'])
            input_widget.returnPressed.connect(lambda k=kind: self._start_analyze(k))
            bar.addWidget(input_widget, 1)
        else:
            bar.addStretch(1)

        tab_layout.addWidget(action_frame)

        splitter = QSplitter(Qt.Horizontal, tab)
        list_widget = QListWidget(tab)
        list_widget.setFixedWidth(220)
        list_widget.currentRowChanged.connect(lambda row, k=kind: self._show_selected_report(k, row))
        list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        list_widget.customContextMenuRequested.connect(lambda pos, k=kind, lw=list_widget: self._report_context_menu(k, lw, pos))
        splitter.addWidget(list_widget)
        web_view = self._build_report_view(tab, cfg, list_widget)
        splitter.addWidget(web_view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        tab_layout.addWidget(splitter, 1)

        self._analyze_views[kind] = {
            'tab': tab,
            'list': list_widget,
            'web': web_view,
            'input': input_widget,
            'button': button,
        }

        return tab

    def _build_report_view(self, parent, cfg, list_widget):
        """Build the right-side report view: QWebEngineView (normal user) or a
        path+open-button fallback (root, where Chromium sandbox is unavailable).

        The fallback stores its QLabel on ._path_label and the QPushButton on
        ._open_button, so _show_selected_report can branch by HAS_WEBENGINE.
        """
        if HAS_WEBENGINE:
            web_view = QWebEngineView(parent)
            web_view.setHtml(self._placeholder_html(cfg['placeholder']))

            return web_view

        fallback = QWidget(parent)
        layout = QVBoxLayout(fallback)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # 单行:按钮在前、路径在后,路径自适应折行。
        row = QHBoxLayout()

        open_button = QPushButton('用浏览器打开')
        open_button.setEnabled(False)
        open_button.setFixedHeight(28)
        open_button.clicked.connect(lambda _checked, lw=list_widget: self._open_selected_report_browser(lw))

        path_label = QLabel(cfg['placeholder'])
        path_label.setWordWrap(True)
        path_label.setStyleSheet('color:#64748B;')

        row.addWidget(open_button)
        row.addWidget(path_label, 1)
        layout.addLayout(row)
        layout.addStretch(1)

        fallback._path_label = path_label
        fallback._open_button = open_button

        return fallback

    def _open_selected_report_browser(self, list_widget):
        """Fallback open handler: open the currently-selected report in the
        system browser via _open_in_browser (reused for its LD_LIBRARY_PATH fix).
        """
        row = list_widget.currentRow()
        item = list_widget.item(row) if row >= 0 else None

        if item is None:
            return

        report_path = item.data(Qt.UserRole)

        if report_path and os.path.isfile(report_path):
            self._open_in_browser(report_path)

    @staticmethod
    def _list_report_files(report_dir):
        """Return *.html report filenames sorted by mtime desc (newest first).

        Sort by file mtime rather than name: legacy reports used a timestamp-less
        name (e.g. <jobid>.html) that sorts before the newer <jobid>_<timestamp>.html
        names and would be auto-selected, re-rendering stale reports whose inline
        JS had a syntax bug.
        """
        if not os.path.isdir(report_dir):
            return []

        files = [f for f in os.listdir(report_dir) if f.endswith('.html')]

        return sorted(files, key=lambda f: os.path.getmtime(os.path.join(report_dir, f)), reverse=True)

    def load_analyze_reports(self):
        """Scan <ai_db_path>/ai_report/<kind>/ for *.html and fill each sub-tab list."""
        for kind, view in self._analyze_views.items():
            report_dir = common_ai.resolve_report_dir(kind)
            reports = self._list_report_files(report_dir)

            list_widget = view['list']
            list_widget.clear()

            for report_name in reports:
                label = report_name.replace('.html', '')
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, os.path.join(report_dir, report_name))
                list_widget.addItem(item)

            if list_widget.count() > 0:
                list_widget.setCurrentRow(0)
            else:
                # QWebEngineView shows a placeholder HTML; the root fallback
                # widget shows the message in its path label + disabled button.
                if HAS_WEBENGINE:
                    view['web'].setHtml(self._placeholder_html(_ANALYZE_TAB_CONFIG_DICT[kind]['empty']))
                else:
                    view['web']._path_label.setText(_ANALYZE_TAB_CONFIG_DICT[kind]['empty'])
                    view['web']._open_button.setEnabled(False)

    @staticmethod
    def _placeholder_html(msg):
        return ('<html><body style="font-family:sans-serif;color:#64748B;padding:24px;">'
                f'<p>{msg}</p></body></html>')

    def _show_selected_report(self, kind, row):
        """Render the selected report of the given kind inline (QWebEngineView)
        or update the fallback path label + open button (root)."""
        if row < 0:
            return

        view = self._analyze_views.get(kind)

        if view is None:
            return

        item = view['list'].item(row)

        if item is None:
            return

        report_path = item.data(Qt.UserRole)

        if not (report_path and os.path.isfile(report_path)):
            return

        if HAS_WEBENGINE:
            view['web'].load(QUrl.fromLocalFile(report_path))
        else:
            view['web']._path_label.setText(report_path)
            view['web']._open_button.setEnabled(True)

    def _report_context_menu(self, kind, list_widget, pos):
        """Right-click menu on a report list item: Delete / Rename."""
        item = list_widget.itemAt(pos)

        if item is None:
            return

        menu = QMenu(list_widget)

        rename_action = QAction('Rename', menu)
        rename_action.triggered.connect(lambda _, it=item, k=kind, lw=list_widget: self._rename_report(k, lw, it))
        menu.addAction(rename_action)

        delete_action = QAction('Delete', menu)
        delete_action.triggered.connect(lambda _, it=item, k=kind, lw=list_widget: self._delete_report(k, lw, it))
        menu.addAction(delete_action)

        menu.exec_(list_widget.viewport().mapToGlobal(pos))

    def _delete_report(self, kind, list_widget, item):
        """Delete a report file and remove it from the list."""
        report_path = item.data(Qt.UserRole)

        if not report_path or not os.path.isfile(report_path):
            return

        reply = QMessageBox.question(self, 'Delete Report',
                                     f'Delete report?\n\n{os.path.basename(report_path)}',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply != QMessageBox.Yes:
            return

        try:
            os.remove(report_path)
        except PermissionError:
            QMessageBox.warning(self, 'Permission Denied', f'No permission to delete:\n{report_path}')

            return
        except OSError as e:
            QMessageBox.warning(self, 'Error', f'Failed to delete report:\n{e}')

            return

        # Remove from list and refresh.
        row = list_widget.row(item)
        list_widget.takeItem(row)
        self.load_analyze_reports()

    def _rename_report(self, kind, list_widget, item):
        """Rename a report file and update the list item display + path."""
        report_path = item.data(Qt.UserRole)

        if not report_path or not os.path.isfile(report_path):
            return

        old_name = os.path.basename(report_path)
        old_base = old_name.replace('.html', '')

        from PyQt5.QtWidgets import QInputDialog

        new_base, ok = QInputDialog.getText(self, 'Rename Report', 'New name (without .html):', text=old_base)

        if not ok or not new_base.strip():
            return

        new_base = new_base.strip()
        new_name = new_base + '.html' if not new_base.endswith('.html') else new_base
        new_path = os.path.join(os.path.dirname(report_path), new_name)

        if new_path == report_path:
            return

        try:
            os.rename(report_path, new_path)
        except PermissionError:
            QMessageBox.warning(self, 'Permission Denied', f'No permission to rename:\n{report_path}')

            return
        except OSError as e:
            QMessageBox.warning(self, 'Error', f'Failed to rename report:\n{e}')

            return

        # Update item display + stored path, then refresh web.
        item.setText(new_name.replace('.html', ''))
        item.setData(Qt.UserRole, new_path)
        self.load_analyze_reports()

    def _ensure_input_completer(self, kind):
        """Lazy-load the name/queue completer the first time an input is edited.

        Source: user → busers (USER/GROUP), queue → bqueues (QUEUE_NAME). Loaded
        once and cached; failures are silent so typing still works.
        """
        attr = f'_{kind}_completer_set'

        if getattr(self, attr, False):
            return

        setattr(self, attr, True)

        view = self._analyze_views.get(kind)

        if view is None or view['input'] is None:
            return

        try:
            from common import common_lsf

            if kind == 'user':
                busers_dict = common_lsf.get_busers_info()
                values_list = busers_dict.get('USER/GROUP', [])
            elif kind == 'queue':
                bqueues_dict = common_lsf.get_bqueues_info()
                values_list = bqueues_dict.get('QUEUE_NAME', [])
            else:
                return

            if values_list:
                view['input'].setCompleter(common_pyqt5.get_completer(values_list))
        except Exception:
            pass

    def _welcome_html(self):
        """Welcome/intro shown in the chat area on first show and after Clear.

        Introduces the lsfMonitor sub-pages and the typical problems the AI
        can help diagnose and resolve (job PEND/SLOW/FAIL, cluster analyze,
        license usage, etc.).
        """
        # Welcome text colours follow the active theme: the chat canvas is dark
        # (#19232D) in dark mode, so the light-theme dark text (#0F172A/#334155)
        # would be nearly invisible — switch to light text then.
        if self.dark_mode:
            text_main = '#CBD5E1'
            text_head = '#FFFFFF'
            text_muted = '#94A3B8'
            text_accent = '#60A5FA'
        else:
            text_main = '#334155'
            text_head = '#0F172A'
            text_muted = '#64748B'
            text_accent = '#2563EB'

        return (
            f'<div style="color:{text_main}; font-size:13px; line-height:1.6;">'
            f'<p style="font-size:15px; color:{text_head};"><b>👋 你好，我是 lsfMonitor AI 助手</b></p>'
            '<p>lsfMonitor 是统一的 IC 基础设施监控与运维平台，包含以下面板：</p>'
            '<table cellspacing="0" cellpadding="4" style="font-size:13px;">'
            f'<tr><td valign="top" style="color:{text_accent};"><b>LSF</b></td>'
            '<td>集群作业监控：JOB（单作业详情）/ JOBS（作业列表）/ HOSTS（主机）/ LOAD（负载曲线）/ USERS（用户统计）/ QUEUES（队列）/ UTILIZATION（利用率）</td></tr>'
            f'<tr><td valign="top" style="color:{text_accent};"><b>LICENSE</b></td>'
            '<td>EDA License 监控：SERVER / FEATURE / EXPIRES（到期）/ USAGE（占用）/ CURVE（曲线）/ UTILIZATION</td></tr>'
            f'<tr><td valign="top" style="color:{text_accent};"><b>RUN</b></td>'
            '<td>批量运维：在多台主机上并行执行 shell 命令（按 Queue/Group 筛选主机）</td></tr>'
            f'<tr><td valign="top" style="color:{text_accent};"><b>AI</b></td>'
            '<td>即本页，AI 诊断与问答助手</td></tr>'
            '</table>'
            f'<p style="margin-top:10px; color:{text_head};"><b>我能帮你解决这些典型问题：</b></p>'
            '<ul style="margin-top:2px;">'
            '<li><b>作业 PEND（排队不动）</b>：分析 pending reason，定位是资源不足、队列限制、预约占用还是 license 缺失，给出处置建议。</li>'
            '<li><b>作业 SLOW（运行缓慢）</b>：结合主机负载、CPU/内存、IO 与历史 rusage，判断是主机争抢、内存超用还是 IO 瓶颈。</li>'
            '<li><b>作业 FAIL/EXIT（异常退出）</b>：解读退出码与终止信号，区分 OOM、超时被杀、license 检出失败或命令错误。</li>'
            '<li><b>集群健康分析</b>：一键生成集群现状报告，发现队列拥塞、主机不可达、负载失衡等问题并给出可执行操作。</li>'
            '<li><b>License 排障</b>：查询 feature 占用、剩余、谁在持有、服务器健康与到期情况。</li>'
            '<li><b>历史作业查询</b>：按 job id / 用户 / 队列 / 状态 / 日期查已完成作业记录。</li>'
            '<li><b>批量操作</b>：跨主机批量执行命令排查问题。</li>'
            '</ul>'
            f'<p style="margin-top:10px; color:{text_muted};">直接输入你的问题即可，例如「<b>job 12345 为什么一直 pend</b>」「<b>哪些主机负载最高</b>」「<b>calibre 的 license 还剩多少</b>」。</p>'
            '</div>'
        )

    def _chat_context_menu(self, pos):
        """Right-click menu for the chat area.

        Copy explicitly writes the selected text to the system clipboard
        (QClipboard.Clipboard mode = the Ctrl+V target). QTextEdit's default
        Copy can land in the X11 PRIMARY selection on some desktops, where
        Ctrl+V into a terminal/editor pastes nothing — this guarantees the
        selection reaches the clipboard.
        """
        cursor = self.ai_tab_chat_text.textCursor()

        menu = QMenu(self.ai_tab_chat_text)

        copy_action = QAction('Copy', menu)
        copy_action.setEnabled(cursor.hasSelection())
        copy_action.triggered.connect(self._copy_chat_selection)
        menu.addAction(copy_action)

        select_all_action = QAction('Select All', menu)
        select_all_action.triggered.connect(self.ai_tab_chat_text.selectAll)
        menu.addAction(select_all_action)

        menu.exec_(self.ai_tab_chat_text.viewport().mapToGlobal(pos))

    def _copy_chat_selection(self):
        """Copy the chat selection to the system clipboard (Ctrl+V target)."""
        cursor = self.ai_tab_chat_text.textCursor()

        if not cursor.hasSelection():
            return

        text = cursor.selectedText()

        # QTextCursor.selectedText uses U+2029 (paragraph separator) for
        # newlines; convert to '\n' so pasted text has real line breaks.
        text = text.replace(' ', '\n')

        # setText() with no mode arg defaults to QClipboard.Clipboard (the
        # Ctrl+V target). Don't pass mode=QApplication.clipboard().Clipboard —
        # accessing the enum via the instance is unreliable in PyQt5 and can
        # make setText silently fail.
        QApplication.clipboard().setText(text)

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
        p.fillRect(0, 0, size, size, QColor(PRIMARY))
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
        p.fillRect(0, 0, size, size, QColor(STATUS_RUN))
        p.setBrush(QColor('#FFFFFF'))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(6, 7, 20, 14, 3, 3)
        p.setBrush(QColor(STATUS_RUN))
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

    def _check_lsf_ready(self):
        """Return True if LSF context is available; show warning and return False otherwise."""
        if not self.context.cluster_db_path:
            QMessageBox.warning(
                self, 'AI',
                'AI features require an active LSF environment. '
                'Please switch to the LSF panel first.'
            )
            return False

        return True

    def ai_tab_send_message(self):
        """
        Send user message to AI and start streaming response.
        """
        if not self.ai_configured:
            return

        if not self._check_lsf_ready():
            return

        user_text = self.ai_tab_input.toPlainText().strip()

        if not user_text:
            return

        # Don't send while AI is still responding.
        if self.ai_thread and self.ai_thread.isRunning():
            return

        # Display user message (right-aligned light blue bubble with avatar).
        # The bubble background is PRIMARY_BG (light blue) in both themes, so the
        # text must be explicitly dark — in dark mode the QTextEdit palette is
        # light, which would render light text on the light bubble unreadable.
        user_html = html.escape(user_text).replace('\n', '<br>')
        self.ai_tab_chat_text.append(
            f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td width="15%"></td>'
            f'<td style="background-color:{self._user_bubble_bg}; color:{self._user_text_color}; padding:10px 14px; -qt-block-indent:0;">'
            f'{user_html}</td>'
            f'<td width="42" valign="top" style="padding:2px 0 0 6px;">'
            f'<img src="user_avatar" width="32" height="32"></td>'
            f'</tr></table>'
        )
        self.ai_tab_input.clear()

        # Log the question.

        # Track timing and tool calls for the current question.
        self._ai_send_time = time.time()

        self._current_ai_question = user_text
        self._current_ai_tool_calls = []

        # Add to messages.
        self.ai_messages.append({"role": "user", "content": user_text})

        # Trim context if too long (keep system prompt + last 30 messages).
        if len(self.ai_messages) > 40:
            self.ai_messages = [self.ai_messages[0]] + self.ai_messages[-30:]

        # Get config_lsf.
        dangerous_commands = common_ai.DEFAULT_DANGEROUS_COMMANDS

        if hasattr(config_ai, 'ai_dangerous_commands') and config_ai.ai_dangerous_commands:
            dangerous_commands = config_ai.ai_dangerous_commands.split()

        lmstat_path = config_license.lmstat_path if hasattr(config_license, 'lmstat_path') else 'lmstat'
        lmstat_bsub_command = config_license.lmstat_bsub_command if hasattr(config_license, 'lmstat_bsub_command') else ''

        # Insert "AI" label and prepare block format for streaming text.
        self._ai_tab_start_ai_block()

        # Start AI thread.
        embedding_model = config_ai.ai_embedding_model_name if hasattr(config_ai, 'ai_embedding_model_name') else ''
        embedding_api_base_url = config_ai.ai_embedding_api_base_url if hasattr(config_ai, 'ai_embedding_api_base_url') else ''
        embedding_api_key = config_ai.ai_embedding_api_key if hasattr(config_ai, 'ai_embedding_api_key') else ''
        self.ai_thread = common_ai.AiChatThread(
            api_base_url=config_ai.ai_api_base_url,
            api_key=config_ai.ai_api_key,
            model_name=config_ai.ai_model_name,
            messages=self.ai_messages,
            db_path=self.context.cluster_db_path,
            license_dic=self.context.license_dic,
            lmstat_path=lmstat_path,
            lmstat_bsub_command=lmstat_bsub_command,
            dangerous_commands=dangerous_commands,
            doc_chunks=self.ai_doc_chunks,
            skills=self.ai_skills,
            embedding_model=embedding_model,
            embedding_api_base_url=embedding_api_base_url,
            embedding_api_key=embedding_api_key,
            debug=False
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

        # Right cell: soft gray-blue message bubble (matches theme BG_TABLE_ALT).
        self._ai_msg_cell = table.cellAt(0, 1)
        cell_fmt = self._ai_msg_cell.format()
        cell_fmt.setBackground(QColor(self._ai_bubble_bg))
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
        self._ai_text_fmt.setForeground(QColor(self._ai_text_color))

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

        # Track tool call for AI log.
        self._current_ai_tool_calls.append({'name': tool_name, 'args': description, 'result': ''})

    def ai_tab_on_tool_result(self, tool_name, result):
        """Tool call finished - start a new AI block for the response."""

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
        cursor.insertText('─' * 40, sep_fmt)

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
            text = f'  · {source}'

            if page:
                text += f' (p.{page})'

            cursor.insertText(text, item_fmt)

        # Skill sources.
        for skill_name in skills:
            cursor.insertBlock()
            cursor.insertText(f'  · Skill: {skill_name}', item_fmt)

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

    def ai_tab_clear_chat(self):
        """Clear chat history."""
        # Stop any running AI thread first. stop() only sets a flag; the thread
        # may be blocked inside an LLM API call for many seconds, so wait() can
        # time out. We must NEVER drop the last Python reference to a still-
        # running QThread (that raises "QThread: Destroyed while thread is
        # still running" and crashes the app), and its queued signals must not
        # fire into the cleared QTextEdit afterward.
        self._release_ai_thread(clear_ui_state=True)

        self.ai_tab_chat_text.clear()
        self.ai_messages = [{"role": "system", "content": common_ai.SYSTEM_PROMPT + f"\n\nCurrent user: {USER}"}]

        if self.ai_configured:
            self.ai_tab_chat_text.setHtml(self._welcome_html())

    def _release_ai_thread(self, clear_ui_state=False):
        """Safely stop and release the running AI thread.

        If the thread stops within a short wait, drop the reference. If it is
        still running (blocked in an LLM API call), disconnect its signals so
        late emissions can't touch the UI, and park the reference on a pending
        list until the thread actually exits — only then is the QThread safe to
        destroy. This is used by Clear (clear_ui_state=True to also reset the
        streaming-cell state the animation writes into).
        """
        thread = getattr(self, 'ai_thread', None)

        if thread is None:
            if clear_ui_state:
                self._reset_ai_streaming_state()
            return

        if not thread.isRunning():
            self.ai_thread = None
            if clear_ui_state:
                self._reset_ai_streaming_state()
            return

        thread.stop()

        # Gave it a moment to notice the flag and exit between tool calls /
        # stream chunks. A full LLM call can take far longer, so don't block
        # the UI — handle the still-running case below.
        if thread.wait(500):
            self.ai_thread = None
            if clear_ui_state:
                self._reset_ai_streaming_state()
            return

        # Still running (blocked in IO). Disconnect all signals so its queued
        # emissions can't reach slots that would write into the cleared chat
        # widget. (Queued events already posted before disconnect still run, so
        # the slots must also be defensive — see _reset_ai_streaming_state.)
        for sig in (thread.token_received, thread.tool_call_start,
                    thread.tool_call_result, thread.finished_signal,
                    thread.error_signal, thread.confirm_requested,
                    thread.status_signal, thread.sources_signal):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass

        # Park the reference until the thread truly exits; dropping it now
        # would destroy a running QThread and crash. Reclaim on finished.
        if not hasattr(self, '_ai_pending_threads'):
            self._ai_pending_threads = []
        self._ai_pending_threads.append(thread)

        try:
            thread.finished_signal.connect(self._ai_reclaim_pending_thread)
        except (TypeError, RuntimeError):
            pass

        self.ai_thread = None

        if clear_ui_state:
            self._reset_ai_streaming_state()

    def _ai_reclaim_pending_thread(self):
        """Drop references to AI threads that have now finished (parked by
        _release_ai_thread when they wouldn't stop in time)."""
        pending = getattr(self, '_ai_pending_threads', None)

        if not pending:
            return

        self._ai_pending_threads = [t for t in pending if t.isRunning()]

    def _reset_ai_streaming_state(self):
        """Reset the per-message streaming state used by the token/thinking
        animation, so late callbacks don't touch a cell we just cleared."""
        if hasattr(self, '_ai_thinking_timer'):
            self._ai_thinking_timer.stop()

        # Invalidate the old message-cell reference — the QTextTable it points
        # into was destroyed by chat_text.clear(), and reusing its cursor would
        # segfault. ai_tab_on_token falls back to the document cursor when this
        # is None.
        self._ai_msg_cell = None
        self._ai_first_token = False

    def ai_handle_confirm_request(self, command):
        """Show a themed dialog to confirm dangerous command execution.

        Uses a custom QDialog styled with the app QSS (theme.py palette) instead
        of QMessageBox.question, which renders as an unstyled native system
        dialog that clashes with the AntD-ish UI.
        """
        from gui import theme

        dialog = QDialog(self)
        dialog.setWindowTitle('AI 危险命令确认')
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        # Title row: warning icon (red dot) + title text.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        dot = QLabel('●')
        dot.setStyleSheet(f'color: {theme.STATUS_EXIT}; font-size: 16px;')
        title_row.addWidget(dot)

        title = QLabel('AI 请求执行以下命令')
        title.setStyleSheet(f'color: {theme.TEXT_PRIMARY}; font-size: {theme.FONT_SIZE + 1}px; font-weight: 600;')
        title_row.addWidget(title)

        title_row.addStretch()
        layout.addLayout(title_row)

        # Hint line.
        hint = QLabel('该命令会改变集群状态，请确认是否允许执行：')
        hint.setStyleSheet(f'color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE}px;')
        layout.addWidget(hint)

        # Command display (monospace, in a bordered box).
        cmd_label = QLabel(command)
        cmd_label.setWordWrap(True)
        cmd_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cmd_label.setStyleSheet(
            f'background: {theme.BG_TABLE_ALT}; '
            f'border: 1px solid {theme.BORDER_STRONG}; '
            f'border-radius: 6px; '
            f'padding: 10px 12px; '
            f'color: {theme.TEXT_PRIMARY}; '
            f'font-family: "JetBrains Mono", "Consolas", "Courier New", monospace; '
            f'font-size: {theme.FONT_SIZE}px;'
        )
        layout.addWidget(cmd_label)

        # Button row: Reject (default) + Allow.
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reject_btn = QPushButton('拒绝')
        reject_btn.setCursor(Qt.PointingHandCursor)
        reject_btn.setStyleSheet(
            f'QPushButton {{ background: {theme.BG_WHITE}; color: {theme.TEXT_REGULAR}; '
            f'border: 1px solid {theme.BORDER_STRONG}; border-radius: 5px; padding: 6px 18px; }}'
            f'QPushButton:hover {{ background: {theme.BG_TABLE_HOVER}; }}'
        )

        allow_btn = QPushButton('允许执行')
        allow_btn.setCursor(Qt.PointingHandCursor)
        allow_btn.setStyleSheet(
            f'QPushButton {{ background: {theme.STATUS_EXIT}; color: {theme.TEXT_INVERSE}; '
            f'border: none; border-radius: 5px; padding: 6px 18px; font-weight: 600; }}'
            f'QPushButton:hover {{ background: #B91C1C; }}'
        )

        btn_row.addWidget(reject_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(allow_btn)
        layout.addLayout(btn_row)

        def _reject():
            dialog.done(0)

        def _allow():
            dialog.done(1)

        reject_btn.clicked.connect(_reject)
        allow_btn.clicked.connect(_allow)

        self.ai_thread.set_confirm_result(dialog.exec_() == 1)

    # For AI TAB (end) #

    def _start_analyze(self, kind):
        """Trigger an analyze report of the given kind (job/user/queue/cluster).

        For kind in (job/user/queue), the input may contain multiple targets
        (space/comma separated); each target launches its own analyze thread
        concurrently.
        """
        if not self._check_lsf_ready():
            return

        if not self.ai_configured:
            QMessageBox.warning(self, 'Warning', 'AI is not configured. Cannot generate analyze report.')
            return

        cfg = _ANALYZE_TAB_CONFIG_DICT[kind]

        # Resolve the target (cluster from context, others from the sub-tab input).
        view = self._analyze_views.get(kind)

        if kind == 'cluster':
            targets = [self.context.cluster or 'unknown']
        else:
            raw = view['input'].text().strip() if view and view['input'] else ''

            # job/user/queue all support multiple targets (space/comma/newline
            # separated); each target launches its own analyze thread.
            targets = [t.strip() for t in re.split(r'[\s,]+', raw) if t.strip()]

        if not any(targets):
            QMessageBox.warning(self, 'Warning', cfg.get('no_target', '请输入要分析的目标。'))
            return

        # Launch each target as its own analyze (concurrent for multiple jobs).
        for target in targets:
            if not target:
                continue

            key = (kind, target)
            thread = self._analyze_threads.get(key)

            # Guard against re-entry of the SAME target.
            if thread is not None and thread.isRunning():
                QMessageBox.information(self, cfg['dialog'], f'目标 "{target}" 的分析报告正在生成中，请稍候 ...')
                continue

            self._launch_single_analyze(kind, target, cfg, view)

    def _job_status_suffix(self, target):
        """Quick bjobs query to get a job's status, return '_<STAT>' for the filename.

        For an array job "5822397[5]", bjobs prints JOBID as "5822397" (the
        parent id), NOT "5822397[5]" — so a naive fields[0]==target check fails.
        Match by the parent jobid, and if an index [N] is present prefer the
        row whose JOB_NAME ends with [N].
        """
        try:
            rc, out, err = common.run_command(f'bjobs {shlex.quote(str(target))} 2>/dev/null')
            lines = out.decode('utf-8', 'ignore').strip().split('\n') if out else []

            parent_id = re.sub(r'\[.*$', '', str(target))
            index_match = re.search(r'\[(\d+)\]', str(target))
            wanted_index = index_match.group(1) if index_match else None

            if len(lines) >= 2:
                chosen_fields = None

                for line in lines[1:]:
                    fields = line.split()

                    if len(fields) < 3 or fields[0] != parent_id:
                        continue

                    if wanted_index:
                        job_name = fields[6] if len(fields) >= 7 else ''

                        if job_name.endswith(f'[{wanted_index}]'):
                            chosen_fields = fields

                            break
                    elif chosen_fields is None:
                        chosen_fields = fields

                if chosen_fields is not None:
                    return f'_{chosen_fields[2]}'
        except Exception:
            pass

        return ''

    def _launch_single_analyze(self, kind, target, cfg, view):
        """Launch ONE analyze thread for a single target (job/user/queue/cluster).

        Thread/msgbox/canceled/start are tracked by (kind, target) so multiple
        jobs can run concurrently without clobbering each other's state.
        """
        # Output path: <ai_db_path>/ai_report/<kind>/<target>[_<status>]_<timestamp>.html.
        report_dir = common_ai.resolve_report_dir(kind)
        safe_target = re.sub(r'\[(\d+)\]', r'_\1', str(target))
        safe_target = re.sub(r'[^A-Za-z0-9._-]', '_', safe_target)
        safe_target = re.sub(r'_+', '_', safe_target).strip('_')

        name_suffix = self._job_status_suffix(target) if kind == 'job' else ''

        output_file = os.path.join(report_dir, f'{safe_target}{name_suffix}_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.html')

        lmstat_path = config_license.lmstat_path if hasattr(config_license, 'lmstat_path') else 'lmstat'
        lmstat_bsub_command = config_license.lmstat_bsub_command if hasattr(config_license, 'lmstat_bsub_command') else ''
        embedding_model = config_ai.ai_embedding_model_name if hasattr(config_ai, 'ai_embedding_model_name') else ''
        embedding_api_base_url = config_ai.ai_embedding_api_base_url if hasattr(config_ai, 'ai_embedding_api_base_url') else ''
        embedding_api_key = config_ai.ai_embedding_api_key if hasattr(config_ai, 'ai_embedding_api_key') else ''

        thread = common_ai.AnalyzeReportThread(
            report_type=kind,
            target=target,
            output_file=output_file,
            api_base_url=config_ai.ai_api_base_url,
            api_key=config_ai.ai_api_key,
            model_name=config_ai.ai_model_name,
            tool=self.context.tool,
            db_path=self.context.cluster_db_path,
            lmstat_path=lmstat_path,
            lmstat_bsub_command=lmstat_bsub_command,
            doc_chunks=self.ai_doc_chunks,
            embedding_model=embedding_model,
            embedding_api_base_url=embedding_api_base_url,
            embedding_api_key=embedding_api_key,
            cluster=self.context.cluster,
            debug=False,
        )

        key = (kind, target)
        self._analyze_threads[key] = thread
        self._analyze_canceled[key] = False
        self._analyze_starts[key] = time.time()
        thread.finished_signal.connect(lambda out, k=kind, t=target: self._analyze_finished(k, t, out))
        thread.error_signal.connect(lambda msg, k=kind, t=target: self._analyze_error(k, t, msg))
        thread.start()

        # Switch to the sub-tab so the input/spinner is visible while running.
        if HAS_WEBENGINE:
            self.main_tab.setCurrentWidget(self.analyze_tab)
            self.analyze_subtabs.setCurrentWidget(view['tab'])

        # Non-modal progress dialog with Cancel (one per target).
        msgbox = QMessageBox(QMessageBox.Information, cfg['dialog'],
                             cfg['working'].format(target=target) + '\n'
                             '（耗时通常数十秒到数分钟，完成后会在 ANALYZE 页面展示报告）\n\n'
                             '可点击 Cancel 中止本次分析。',
                             QMessageBox.Cancel, self)
        msgbox.setModal(False)
        msgbox.buttonClicked.connect(lambda _b, k=kind, t=target: self._analyze_cancel(k, t))
        self._analyze_msgboxes[key] = msgbox
        msgbox.show()

    def _analyze_close_msgbox(self, kind, target):
        """Close the analyze progress dialog for the given (kind, target) if any."""
        key = (kind, target)
        msgbox = self._analyze_msgboxes.get(key)

        if msgbox is not None:
            msgbox.close()
            self._analyze_msgboxes.pop(key, None)

    def _analyze_cancel(self, kind, target):
        """User canceled: stop the thread and suppress the pending result."""
        key = (kind, target)
        self._analyze_canceled[key] = True

        thread = self._analyze_threads.get(key)

        if thread is not None:
            thread.stop()

        self._analyze_close_msgbox(kind, target)

    def _analyze_finished(self, kind, target, output_file):
        """Called when an analyze report generation is complete."""
        key = (kind, target)
        self._analyze_close_msgbox(kind, target)

        if self._analyze_canceled.get(key, False):
            # Cleanup tracking state; don't show a result.
            self._analyze_threads.pop(key, None)
            self._analyze_canceled.pop(key, None)
            self._analyze_starts.pop(key, None)

            return

        cfg = _ANALYZE_TAB_CONFIG_DICT[kind]

        # Refresh the ANALYZE tab history list so the new report appears; inline
        # mode auto-selects row 0 and renders it in the QWebEngineView.
        self.load_analyze_reports()

        if HAS_WEBENGINE:
            self.main_tab.setCurrentWidget(self.analyze_tab)
            self.analyze_subtabs.setCurrentWidget(self._analyze_views[kind]['tab'])
            elapsed = time.time() - self._analyze_starts.get(key, time.time())
            text = cfg['done_prefix'] + f'，报告已在 ANALYZE 页面展示。\n\n耗时：{elapsed:.1f} 秒\n\n报告路径：\n{output_file}'
            QMessageBox.information(self, cfg['dialog'], text)
        else:
            # Fallback: open in external browser.
            opened = self._open_in_browser(output_file)

            if opened:
                text = cfg['done_prefix'] + f'，报告已在浏览器中打开。\n\n报告路径：\n{output_file}'
            else:
                text = cfg['done_prefix'] + f'，但无法自动打开浏览器，请手动打开：\n\n{output_file}'

            QMessageBox.information(self, cfg['dialog'], text)

        # Cleanup tracking state for this (kind, target).
        self._analyze_threads.pop(key, None)
        self._analyze_canceled.pop(key, None)
        self._analyze_starts.pop(key, None)

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

    def _analyze_error(self, kind, target, error_msg):
        """Called when an analyze report generation fails."""
        key = (kind, target)
        self._analyze_close_msgbox(kind, target)

        if self._analyze_canceled.get(key, False):
            self._analyze_threads.pop(key, None)
            self._analyze_canceled.pop(key, None)
            self._analyze_starts.pop(key, None)

            return

        QMessageBox.warning(self, 'Error', f'Failed to generate {kind} analyze report for "{target}":\n{error_msg}')

        # Cleanup tracking state for this (kind, target).
        self._analyze_threads.pop(key, None)
        self._analyze_canceled.pop(key, None)
        self._analyze_starts.pop(key, None)


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
