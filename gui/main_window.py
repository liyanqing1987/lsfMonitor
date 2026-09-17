# -*- coding: utf-8 -*-
################################
# File Name   : main_window.py
# Author      : liyanqing.1987
# Created On  : 2026-08-24
# Description : Unified MainWindow. Hosts an outer sidebar navigation with
#               LSF/LICENSE/RUN/AI panels and a single merged menubar. Both
#               `bmonitor` and `license_monitor` launchers start this program;
#               the entry panel is chosen via --panel (lsf by default, license
#               for license_monitor). Non-entry panels are instantiated lazily
#               the first time the user clicks them in the sidebar.
################################

import os
import sys
import argparse
import getpass
import socket

from PyQt5.QtCore import QRectF, QSize, Qt, QPointF
from PyQt5.QtGui import QIcon, QPainter, QColor, QPen, QPixmap, QBrush, QFont, QFontMetrics
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QWidget

from common import common
from common import common_pyqt5

from gui.app_context import AppContext
from gui.ai_panel import AiPanel
from gui.run_panel import RunPanel
from gui.theme import (
    LIGHT_THEME_QSS, SIDEBAR_LIGHT_QSS, SIDEBAR_DARK_QSS, PRIMARY,
)


def _menu_action(text, icon_path, slot, parent=None):
    """Small helper to build a QAction with optional icon and slot."""
    from PyQt5.QtWidgets import QAction
    act = QAction(text, parent)

    if icon_path and os.path.exists(icon_path):
        act.setIcon(QIcon(icon_path))

    if slot is not None:
        act.triggered.connect(slot)

    return act


# ------------------------------------------------------------------
# Sidebar icons are drawn programmatically (simple, clean, match theme)
# instead of using the old bitmap assets.
# ------------------------------------------------------------------
def _make_icon(draw_fn, size=20, color=PRIMARY):
    """Build a QIcon by calling draw_fn(QPainter, size, color) on a transparent pm."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    draw_fn(p, size, QColor(color))
    p.end()
    return QIcon(pm)


def _draw_server_icon(p, s, c):
    """Stacked server/racks symbol for LSF (2 clean units)."""
    pen = QPen(c)
    pen.setWidthF(max(1.2, s * 0.085))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # Two rack units (top & bottom), evenly placed, with breathing room.
    pad_x = s * 0.13
    h = s * 0.28
    top_y = s * 0.17
    bot_y = s * 0.55

    for y in (top_y, bot_y):
        r = QRectF(pad_x, y, s - 2 * pad_x, h)
        p.drawRoundedRect(r, s * 0.06, s * 0.06)
        # Status dots on the left side
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        dot_r = s * 0.035
        dot_x = pad_x + s * 0.10

        for i in range(3):
            dx = dot_x + i * (dot_r * 2 + s * 0.04)
            p.drawEllipse(QPointF(dx, y + h * 0.5), dot_r, dot_r)

        p.setBrush(Qt.NoBrush)
        p.setPen(pen)


def _draw_key_icon(p, s, c):
    """Simple modern key shape for License."""
    pen = QPen(c)
    pen.setWidthF(max(1.2, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # Key bow (ring) — top-left
    cx, cy, cr = s * 0.32, s * 0.50, s * 0.20
    p.drawEllipse(QPointF(cx, cy), cr, cr)
    # Keyhole dot (filled center for visual weight)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(cx, cy), s * 0.05, s * 0.05)
    p.setBrush(Qt.NoBrush)
    p.setPen(pen)
    # Shaft extending to the right
    shaft_x0 = cx + cr * 0.8
    shaft_x1 = s * 0.92
    p.drawLine(QPointF(shaft_x0, cy), QPointF(shaft_x1, cy))
    # One single tooth at the tip (down)
    p.drawLine(QPointF(shaft_x1, cy), QPointF(shaft_x1, cy + s * 0.16))


def _draw_sparkle_icon(p, s, c):
    """Sparkle (4-pointed star) for AI — filled, clean, no stray marks."""
    from PyQt5.QtGui import QPolygonF
    cx, cy = s * 0.50, s * 0.52
    # Main star: filled 4-pointed. Vertical arm slightly taller than horizontal
    # to look natural. Use a polygon with "notch" points near the center to
    # create the classic sparkle shape instead of a plain cross.
    arm_v = s * 0.36   # vertical half-length
    arm_h = s * 0.30   # horizontal half-length
    notch = s * 0.06   # indent on the diagonals (0 = sharp diamond)
    star = QPolygonF([
        QPointF(cx, cy - arm_v),
        QPointF(cx + notch, cy - notch),
        QPointF(cx + arm_h, cy),
        QPointF(cx + notch, cy + notch),
        QPointF(cx, cy + arm_v),
        QPointF(cx - notch, cy + notch),
        QPointF(cx - arm_h, cy),
        QPointF(cx - notch, cy - notch),
    ])
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(star)
    # Two small sparkle satellites (top-right and bottom-left) for "AI" feel
    sat = s * 0.05

    for sx, sy in [(s * 0.80, s * 0.22), (s * 0.22, s * 0.80)]:
        p.drawEllipse(QPointF(sx, sy), sat, sat)


def _draw_terminal_icon(p, s, c):
    """Terminal window symbol for the Run panel (rounded screen + prompt chevron)."""
    pen = QPen(c)
    pen.setWidthF(max(1.2, s * 0.085))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # Screen body.
    body = QRectF(s * 0.14, s * 0.20, s * 0.72, s * 0.60)
    p.drawRoundedRect(body, s * 0.08, s * 0.08)
    # Title bar line.
    p.drawLine(QPointF(body.left(), s * 0.30), QPointF(body.right(), s * 0.30))
    # Prompt ">" chevron + cursor.
    prompt_pen = QPen(c)
    prompt_pen.setWidthF(max(1.4, s * 0.11))
    prompt_pen.setCapStyle(Qt.RoundCap)
    p.setPen(prompt_pen)
    bx = body.left() + s * 0.13
    by = body.bottom() - s * 0.16
    p.drawLine(QPointF(bx, by), QPointF(bx + s * 0.10, by - s * 0.07))
    p.drawLine(QPointF(bx, by), QPointF(bx + s * 0.10, by + s * 0.07))
    p.drawLine(QPointF(bx + s * 0.15, by), QPointF(bx + s * 0.28, by))


_NAV_ICONS_CACHE = {}


def _nav_icon(kind):
    if kind not in _NAV_ICONS_CACHE:
        fn = {'lsf': _draw_server_icon, 'license': _draw_key_icon, 'run': _draw_terminal_icon, 'ai': _draw_sparkle_icon}[kind]
        _NAV_ICONS_CACHE[kind] = _make_icon(fn, size=22)

    return _NAV_ICONS_CACHE[kind]


# LsfPanel lives in gui/lsf_panel.py and LicensePanel in gui/license_panel.py
# (panel classes live alongside the GUI framework, not under bin/).
_INSTALL_PATH = os.environ.get('LSFMONITOR_INSTALL_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if _INSTALL_PATH not in sys.path:
    sys.path.insert(0, _INSTALL_PATH)

from gui.lsf_panel import LsfPanel
from gui.license_panel import LicensePanel


def read_args():
    """
    Read unified entry arguments.

    --panel selects the outer panel to focus at startup (LSF default). Inner-tab
    and filter args are forwarded to the focused panel when it is first shown.
    """
    parser = argparse.ArgumentParser(
        description="""
lsfMonitor — 统一 GUI 监控工具（LSF/License/批量运维/AI 助手）

启动后自动打开指定面板和子页，预填筛选条件并加载数据。
不带任何参数时默认打开 LSF 面板的 JOBS 页。
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
参数自动推断规则（--panel / --tab 未指定时）：

  --jobid          → LSF 面板，JOB 页，自动填入 jobid 并查询
  --user           → LSF 面板，JOBS 页，自动填入用户并加载
                     同时填充 LSF-USERS 和 LICENSE-USAGE 页
  --host           → LSF 面板，LOAD 页，自动填入主机并加载
                     同时填充 LSF-HOSTS 页
  --feature        → LICENSE 面板，FEATURE 页，自动填入并过滤
                     同时填充 EXPIRES/USAGE/CURVE 页
  --feature --user → LICENSE 面板，USAGE 页
  无参数           → LSF 面板，JOBS 页

--panel 和 --tab 可手动指定，覆盖自动推断。

使用示例：

  # 查作业详情
  bmonitor --jobid 12345

  # 查某用户作业
  bmonitor --user liyanqing

  # 查主机负载
  bmonitor --host n019-123-001

  # 查 license feature
  bmonitor --feature calibre

  # feature + user 组合
  bmonitor --feature calibre --user someone

  # 手动指定面板和子页
  bmonitor --panel lsf --tab HOSTS
  bmonitor --panel license --tab CURVE --feature calibre

  # 多值输入（-u/-H/-f 支持空格分隔多个值）
  bmonitor --user alice bob          # 多用户
  bmonitor --feature VCS Verdi       # 多 feature
  bmonitor --host h1 h2 h3           # HOSTS 页全部填入；LOAD 页仅取第一个
  bmonitor --panel lsf --tab HOSTS --host h1 h2 h3


  # 暗黑模式
  bmonitor -d
  bmonitor --feature calibre -d

相关命令：
  bmonitor_cli     命令行查询工具（输出 JSON，供脚本/AI 使用）
  bsample          LSF 数据采样
  license_monitor  直接启动 LICENSE 面板
  license_sample   License 数据采样
""",
    )
    parser.add_argument('-p', '--panel', default='', help='指定启动面板：lsf/license/run/ai。不指定时自动推断。')
    parser.add_argument('-t', '--tab', default='', help='指定启动子页名称（如 JOB/JOBS/HOSTS/LOAD/FEATURE/USAGE 等）。不指定时自动推断。')
    parser.add_argument('-j', '--jobid', type=int, default=0, help='LSF 作业 ID。自动切到 LSF JOB 页并查询。')
    parser.add_argument('-u', '--user', nargs='+', default=[], help='用户名，空格分隔可多个。自动切到 LSF JOBS 页并加载，同时填充 USERS 和 LICENSE-USAGE 页。')
    parser.add_argument('-H', '--host', nargs='+', default=[], help='主机名，空格分隔可多个。HOSTS 页全部填入；LOAD 页仅取第一个（LOAD 只支持单 host）。')
    parser.add_argument('-f', '--feature', nargs='+', default=[], help='License feature 名称，空格分隔可多个。自动切到 LICENSE FEATURE 页并过滤，同时填充 EXPIRES/USAGE/CURVE 页。')
    parser.add_argument('-d', '--dark_mode', action='store_true', default=False, help='暗黑模式。')

    args = parser.parse_args()

    return args


class MainWindow(QMainWindow):
    """
    Unified host window. Owns the outer sidebar navigation and the merged
    menubar. Only the entry panel (selected by --panel) is instantiated at
    startup; the other three panels are created on first navigation.
    """

    def __init__(self, args):
        super().__init__()

        self.args = args
        self._panel_meta = []
        self.panels = {}
        install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
        self.context = AppContext(install_path, dark_mode=args.dark_mode)

        # QListWidget for outer nav (West QTabWidget rotates text 90°); each
        # panel keeps its own inner QTabWidget.
        central = QWidget(self)
        central.setObjectName('centralWidget')
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav_list = QListWidget(central)
        self.nav_list.setObjectName('navList')
        # Size sidebar to the longest nav label (LICENSE).
        _nav_font = QFont(self.nav_list.font())
        _nav_font.setBold(True)
        _fm = QFontMetrics(_nav_font)
        _longest_label = max(('LSF', 'LICENSE', 'RUN', 'AI'), key=len)
        _nav_width = _fm.horizontalAdvance(_longest_label) + 22 + 20 + 16 + 3 + 8
        self.nav_list.setFixedWidth(_nav_width)
        self.nav_list.setSpacing(0)
        self.nav_list.setCurrentRow(0)
        self.nav_list.setMovement(QListWidget.Static)
        self.nav_list.setFocusPolicy(Qt.NoFocus)

        self.stack = QStackedWidget(central)
        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack)
        self.setCentralWidget(central)

        # Panel registry: (name, label, icon_key, factory). Non-entry panels are
        # instantiated lazily on first navigation.
        self._panel_meta = [
            ('lsf', 'LSF', 'lsf', LsfPanel),
            ('license', 'LICENSE', 'license', LicensePanel),
            ('run', 'RUN', 'run', RunPanel),
            ('ai', 'AI', 'ai', AiPanel),
        ]

        # Populate sidebar + placeholder widgets (entry panel replaces its
        # placeholder below).
        for _name, label, icon_key, _factory in self._panel_meta:
            item = QListWidgetItem(label)
            item.setIcon(_nav_icon(icon_key))
            item.setSizeHint(QSize(0, 44))
            self.nav_list.addItem(item)
            placeholder = QWidget()
            self.stack.addWidget(placeholder)

        # Shared menubar skeleton (File/Setup/Function/Help) + Exit anchor.
        self._shared_menus = None
        self._exit_action = None
        self.gen_menubar_skeleton()

        # Instantiate all panels so their menu actions are available from
        # startup; UIs are still built lazily via on_first_show.
        for _name, _label, _icon_key, _factory in self._panel_meta:
            self._ensure_panel_created(_name)

        # Entry panel: --panel (case-insensitive) or auto-inferred from args
        # (--feature → license, else lsf).
        entry_name = args.panel.lower() if args.panel else ''

        if not entry_name:
            if args.feature:
                entry_name = 'license'
            else:
                entry_name = 'lsf'

        if entry_name not in [n for (n, _l, _ic, _f) in self._panel_meta]:
            print(f'*Warning*: Unknown panel "{args.panel}", falling back to "lsf".')
            entry_name = 'lsf'

        entry_idx = next(i for i, (n, _l, _ic, _f) in enumerate(self._panel_meta) if n == entry_name)

        # Connect navigation after creating all panels so setCurrentRow triggers
        # activation exactly once.
        self.nav_list.currentRowChanged.connect(self._on_outer_panel_changed)
        self.nav_list.setCurrentRow(entry_idx)

        # setCurrentRow(0) doesn't emit currentChanged if row is already 0.
        if self.nav_list.currentRow() == entry_idx:
            self._activate_panel(entry_name)

        # Forward entry args (-j/-u/-f/-t) to the entry panel.
        entry_panel = self.panels[entry_name]
        entry_panel.apply_entry_args(self.args)

        common_pyqt5.auto_resize(self, 1400, 760)
        self.setWindowTitle('lsfMonitor')
        self.setWindowIcon(QIcon(os.path.join(install_path, 'data', 'pictures', 'monitor.ico')))
        common_pyqt5.center_window(self)

        self._apply_theme()

    def _apply_theme(self):
        """Apply global QSS + sidebar styling based on dark_mode flag."""
        app = QApplication.instance()

        if self.args.dark_mode:
            import qdarkstyle
            app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
            self.nav_list.setStyleSheet(SIDEBAR_DARK_QSS)
        else:
            app.setStyleSheet(LIGHT_THEME_QSS)
            self.nav_list.setStyleSheet(SIDEBAR_LIGHT_QSS)

    def closeEvent(self, event):
        """Stop panel background threads (notably the AI chat thread) on exit."""
        for panel in self.panels.values():
            cleanup = getattr(panel, 'cleanup', None)

            if cleanup is not None:
                cleanup()

        common.bprint('Bye', date_format='%Y-%m-%d %H:%M:%S')
        super().closeEvent(event)

    def gen_menubar_skeleton(self):
        """Create the shared top-level menus and the Exit anchor action.

        Panel actions are appended lazily via `_register_panel_menus()` when
        each panel is instantiated. The Exit action is inserted once here and
        moved to the bottom whenever new actions are registered, so it always
        stays at the end of the File menu.
        """
        install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
        menubar = self.menuBar()
        file_menu = menubar.addMenu('File')
        setup_menu = menubar.addMenu('Setup')
        function_menu = menubar.addMenu('Function')
        help_menu = menubar.addMenu('Help')
        self._shared_menus = {
            'File': file_menu,
            'Setup': setup_menu,
            'Function': function_menu,
            'Help': help_menu,
        }

        # Exit + separator: re-added to the tail of File whenever a panel
        # registers actions, so Exit stays at the bottom (addAction moves an
        # existing action to the tail).
        from PyQt5.QtWidgets import qApp
        self._exit_action = _menu_action('Exit',
                                         os.path.join(install_path, 'data/pictures/exit.png'),
                                         qApp.quit,
                                         parent=file_menu)
        self._exit_separator = file_menu.addSeparator()
        file_menu.addAction(self._exit_action)

    def _register_panel_menus(self, panel):
        """Ask a panel to register its menubar actions, then re-anchor Exit."""
        panel.register_menubar_actions(self.menuBar(), self._shared_menus)
        file_menu = self._shared_menus['File']
        file_menu.addAction(self._exit_separator)
        file_menu.addAction(self._exit_action)

    def _ensure_panel_created(self, name):
        """Instantiate a panel on demand and swap its stack placeholder."""
        if name in self.panels:
            return self.panels[name]

        idx = next(i for i, (n, _l, _ic, _f) in enumerate(self._panel_meta) if n == name)
        _pname, label, _icon_key, factory = self._panel_meta[idx]
        common.bprint('Building {} panel ...'.format(label), date_format='%Y-%m-%d %H:%M:%S')

        panel = factory(self.context, parent=self, args=self.args)
        placeholder = self.stack.widget(idx)
        self.stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.stack.insertWidget(idx, panel)

        self.panels[name] = panel
        setattr(self, '{}_panel'.format(name), panel)
        setattr(self.context, '{}_panel'.format(name), panel)
        panel._panel_activated = False

        # Register menu actions. This is a no-op for panels that haven't
        # built their inner UI yet, but the actions themselves (QAction
        # objects) are cheap to create and only do work when triggered.
        self._register_panel_menus(panel)

        return panel

    def _activate_panel(self, name):
        """Call on_first_show() on a panel once, the first time it is entered."""
        panel = self.panels.get(name)

        if panel is None:
            return

        if not getattr(panel, '_panel_activated', False):
            panel.on_first_show()
            panel._panel_activated = True

    def _on_outer_panel_changed(self, row):
        """Sidebar nav slot: lazily create + activate the target panel. Stack is
        switched before on_first_show so a panel launching a background load can
        render its own loading placeholder first."""
        if row < 0 or row >= len(self._panel_meta):
            return

        name = self._panel_meta[row][0]
        self._ensure_panel_created(name)
        self.stack.setCurrentIndex(row)
        self._activate_panel(name)


def main():
    args = read_args()

    # Ensure db_path root is 0o1777 before any subsystem writes (earliest write
    # point is save_app_start → init_app_log_db → os.makedirs).
    try:
        from common import common_config, common_db_path

        default_config = common_config.load_default_config()
        db_path = getattr(default_config, 'db_path', '') if default_config else ''
        if not db_path:
            db_path = os.path.join(os.environ.get('LSFMONITOR_INSTALL_PATH', '.'), 'db')

        common_db_path.ensure_db_root(db_path)
    except Exception:
        pass

    # Record this launch (best-effort). Prefer LSFMONITOR_ORIG_CMDLINE so the
    # logged command matches what the user typed, not Python's expanded argv.
    try:
        from common import common_app_log

        orig_cmdline = os.environ.get('LSFMONITOR_ORIG_CMDLINE', '').strip()
        command = orig_cmdline if orig_cmdline else ' '.join(sys.argv)

        common_app_log.save_app_start(
            user=getpass.getuser(),
            cwd=os.getcwd(),
            command=command,
            host=socket.gethostname(),
        )
    except Exception:
        pass

    app = QApplication(sys.argv)

    # Apply matplotlib chart style after QApplication exists.
    try:
        common_pyqt5.apply_chart_style()
    except Exception:
        pass

    mw = MainWindow(args)
    mw.showMaximized()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
