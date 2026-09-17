# -*- coding: utf-8 -*-
################################
# File Name   : panel_base.py
# Author      : liyanqing.1987
# Created On : 2026-08-24
# Description : Panel abstract base. Each tool panel owns an inner QTabWidget
#               and registers menubar actions onto MainWindow. Supports lazy
#               construction of inner tabs and a one-time on_first_show.
################################

from PyQt5.QtWidgets import QWidget, QTabWidget, QGridLayout, QLabel
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette

from common import common_pyqt5
from gui.theme import PRIMARY_LIGHT


class PanelBase(QWidget):
    """Base class for tool panels embedded in the unified MainWindow.

    Subclasses register heavy tabs via _register_lazy_tab so gen_*_tab() runs
    only when the user first clicks that tab."""

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._panel_activated = False
        self._lazy_inner_tabs = {}
        self._inner_tabs_built = set()

        self.main_tab = QTabWidget(self)
        # BoldAwareTabBar sizes tabs to their bold (selected) width so the
        # font-weight:600 selected label isn't clipped.
        _main_tab_bar = common_pyqt5.BoldAwareTabBar()
        _main_tab_bar.setExpanding(True)
        self.main_tab.setTabBar(_main_tab_bar)
        # objectName so QSS targets panel-level styling without leaking into
        # nested QTabWidgets; fill the tab-strip area with PRIMARY_LIGHT.
        self.main_tab.setObjectName('mainTab')
        self.main_tab.setAutoFillBackground(True)
        _main_palette = self.main_tab.palette()
        _main_palette.setColor(QPalette.Window, QColor(PRIMARY_LIGHT))
        self.main_tab.setPalette(_main_palette)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.main_tab)
        self.main_tab.currentChanged.connect(self._on_inner_tab_changed)

    def _make_empty_hint(self, layout, row, col, row_span=1, col_span=1, message=''):
        """Create a mouse-transparent empty-state hint label in a grid cell,
        overlaying the data widget. Caller hides it via hide_empty_hint."""
        hint = QLabel(message)
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet('QLabel { color: #888; font-size: 14px; }')
        hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(hint, row, col, row_span, col_span)

        return hint

    def hide_empty_hint(self, hint):
        """Hide an empty-state hint created by _make_empty_hint."""
        if hint is not None:
            hint.hide()

    def _register_lazy_tab(self, tab_widget, build_callable):
        """Register an inner tab to be built lazily on first activation."""
        self._lazy_inner_tabs[tab_widget] = build_callable

    def _ensure_inner_tab_built(self, tab_widget):
        """Force-build a lazy tab if not built yet. Call before accessing its
        widgets from outside the currentChanged slot."""
        if tab_widget in self._lazy_inner_tabs and tab_widget not in self._inner_tabs_built:
            self._inner_tabs_built.add(tab_widget)
            self._lazy_inner_tabs[tab_widget]()

    def _on_inner_tab_changed(self, index):
        """Slot connected to main_tab.currentChanged; triggers lazy build."""
        if index < 0 or index >= self.main_tab.count():
            return

        w = self.main_tab.widget(index)
        self._ensure_inner_tab_built(w)

    def on_first_show(self):
        """Called once by MainWindow when the panel is first shown."""
        return

    def register_menubar_actions(self, menubar, shared_menus=None):
        """Register this panel's actions onto the host menubar. Override if needed."""
        return

    def switch_tab(self, tab_name):
        """Switch to an inner tab by name. Override in subclass if needed."""
        return

    def cleanup(self):
        """Stop background threads on exit. Override if the panel owns threads."""
        return
