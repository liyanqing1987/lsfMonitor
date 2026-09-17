import os
import re
import math
import datetime
import subprocess
import screeninfo

from PyQt5.QtWidgets import QDesktopWidget, QComboBox, QLineEdit, QListWidget, QCheckBox, QListWidgetItem, QCompleter, QTableWidget, QTabBar
from PyQt5.QtGui import QTextCursor, QFont, QGuiApplication, QKeySequence
from PyQt5.Qt import QFontMetrics
from PyQt5.QtCore import Qt, QEvent, QObject, QTimer, QThread, QSize
from PyQt5.QtWidgets import QShortcut
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5 import NavigationToolbar2QT
from matplotlib.dates import num2date


def center_window(window):
    """
    Move the input GUI window into the center of the computer windows.
    """
    qr = window.frameGeometry()
    cp = QDesktopWidget().availableGeometry().center()
    qr.moveCenter(cp)
    window.move(qr.topLeft())


def auto_resize(window, width=0, height=0):
    """
    Scaling down the window size if screen resolution is smaller than window resolution.
    input:  Window: Original window; Width: window width; Height: window height
    output: Window: Scaled window
    """
    # Get default width/height setting.
    monitor_list = screeninfo.get_monitors()

    if not monitor_list:
        return width, height

    monitor = monitor_list[0]

    if not width:
        width = monitor.width

    if not height:
        height = monitor.height

    # If the screen size is too small, automatically obtain the appropriate length and width value.
    if (monitor.width < width) or (monitor.height < height):
        width_rate = math.floor((monitor.width / width) * 100)
        height_rate = math.floor((monitor.height / height) * 100)
        min_rate = min(width_rate, height_rate)
        width = int((width * min_rate) / 100)
        height = int((height * min_rate) / 100)

    # Resize with auto width/height value.
    window.resize(width, height)


def auto_size_table_columns(table, padding=40):
    """
    Ensure every column of ``table`` is at least wide enough to show its
    **header label** in full, while preserving any explicit ``setColumnWidth``
    initial widths and existing Stretch / ResizeToContents resize modes.

    Call this *after* ``setHorizontalHeaderLabels`` and all per-column
    ``setColumnWidth`` / ``setSectionResizeMode`` calls.  It is safe to call
    multiple times (e.g. once during widget construction, and again from a
    showEvent so that the applied QSS font is taken into account).  It:
      * measures each header label with the header's own font (forced to bold
        to match the QSS `font-weight: 600` rule for table headers),
      * sets ``setColumnWidth`` to ``max(current_width, text_width + padding)``,
      * sets ``header.setMinimumSectionSize`` so the user cannot drag a column
        narrower than its (padded) header text,
      * leaves Stretch / Fixed / Interactive / ResizeToContents modes alone so
        columns that were already set to Stretch still fill remaining space.

    ``padding`` (default 40 px) accounts for the sort-indicator arrow (~14 px,
    which only appears after the column is clicked to sort) plus the header's
    left/right padding (10 px each side per QSS) and a small safety margin so
    the bold header text + sort arrow never get clipped.
    """
    if not isinstance(table, QTableWidget):
        return

    header = table.horizontalHeader()
    # Force a bold QFontMetrics to match the QSS `QHeaderView::section { font-weight: 600 }`
    # rule — measuring with the regular font underestimates bold glyph widths.
    bold_font = QFont(header.font())
    bold_font.setBold(True)
    font_metrics = QFontMetrics(bold_font)
    global_min = 0

    for col in range(table.columnCount()):
        item = table.horizontalHeaderItem(col)
        label = item.text() if item is not None else ''

        if not label:
            continue

        text_width = font_metrics.horizontalAdvance(label)
        needed = text_width + padding
        # Do not shrink columns the caller sized wider, and never shrink below
        # the user's current drag-adjusted width after the first layout.
        current = table.columnWidth(col)

        if needed > current:
            table.setColumnWidth(col, needed)

        if needed > global_min:
            global_min = needed

    # Floor for the absolute minimum a column can be dragged to — enough for
    # the narrowest 3-char labels like "Num" / "RUN" / "Slot".
    floor = max(50, int(global_min * 0.45))

    if header.minimumSectionSize() < floor:
        header.setMinimumSectionSize(floor)


def make_table_readonly(table, copy_enabled=True):
    """
    Make a QTableWidget read-only while keeping copy (Ctrl+C) working.

    `NoEditTriggers` blocks editing (double-click / F2 / typing). Because the
    default Ctrl+C only copies when an editor is open, we register a shortcut
    that copies the selected cells' text (tab-separated for multi-cell
    selections, newline-separated for multi-row) to the clipboard so users can
    still grab cell content without being able to modify it.

    `copy_enabled=False` skips the shortcut registration (pure read-only).
    """
    if not isinstance(table, QTableWidget):
        return

    table.setEditTriggers(QTableWidget.NoEditTriggers)

    if not copy_enabled:
        return

    def _copy_selected_cells():
        selected_ranges = table.selectedRanges()

        if not selected_ranges:
            return

        # Collect a row x col grid across the union of selected ranges.
        rows = set()
        cols = set()

        for rng in selected_ranges:
            rows.update(range(rng.topRow(), rng.bottomRow() + 1))
            cols.update(range(rng.leftColumn(), rng.rightColumn() + 1))

        if not rows or not cols:
            return

        row_list = sorted(rows)
        col_list = sorted(cols)
        lines = []

        for r in row_list:
            cells = []

            for c in col_list:
                item = table.item(r, c)
                cells.append(item.text() if item is not None else '')

            lines.append('\t'.join(cells))

        QGuiApplication.clipboard().setText('\n'.join(lines))

    shortcut = QShortcut(QKeySequence.Copy, table)
    shortcut.activated.connect(_copy_selected_cells)


class BoldAwareTabBar(QTabBar):
    """Tab bar that sizes each tab to fit the **bold** (selected) label width.

    QSS gives the selected tab `font-weight: 600`, which is wider than the
    normal weight Qt uses to measure tab text — so the label gets clipped when
    the tab becomes selected. We override `tabSizeHint` to measure every label
    with a forced-bold font (plus the QSS padding) so the tab is always wide
    enough for the bold state.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)

        text = self.tabText(index)

        if not text:
            return size

        bold_font = QFont(self.font())
        bold_font.setBold(True)
        font_metrics = QFontMetrics(bold_font)
        # mainTab QSS: padding 8px 20px (left+right = 40) + margin 4px each side (8).
        # Add ~12px safety margin so the label never clips even when bold-selected.
        text_width = font_metrics.horizontalAdvance(text) + 40 + 8 + 12

        if text_width > size.width():
            size.setWidth(text_width)

        return size


def text_edit_visible_position(text_edit_item, position='End'):
    """
    For QTextEdit widget, show the 'Start' or 'End' part of the text.
    """
    cursor = text_edit_item.textCursor()

    if position == 'Start':
        cursor.movePosition(QTextCursor.Start)
    elif position == 'End':
        cursor.movePosition(QTextCursor.End)

    text_edit_item.setTextCursor(cursor)
    text_edit_item.ensureCursorVisible()


class MultiValueCompleter(QCompleter):
    """按空格分段，只对最后一段补全，支持一个输入框填多个值。"""

    def __init__(self, item_list):
        super().__init__(item_list)

        # 最后一次 splitPath 时记录的前面段（用户已输入、非最后一段）。
        # splitPath 在用户键入时被 Qt 调用，path 是当前完整输入文本，
        # 比 textEdited 更可靠（popup 选中浏览不触发 splitPath，不会污染）。
        self._prefix_segments = []
        self.activated.connect(self._on_activated)

    def setWidget(self, widget):
        super().setWidget(widget)

    def splitPath(self, path):
        segments = path.split(' ')

        # 只拿最后一段去匹配，前面段保持原样；同时记录前面段供 activated 使用。
        # 仅当文本含空格（即有前面段）时才更新记录，避免 popup 浏览选中项
        # （单个值、无空格）时把已记录的前缀清空。
        if len(segments) > 1:
            self._prefix_segments = segments[:-1]

        return [segments[-1] if segments else path]

    def _on_activated(self, text):
        widget = self.widget()

        if widget is None:
            return

        # Qt 在 activated 之后会用选中项再次替换输入框文本，所以延迟到下一个
        # 事件循环再设值，确保最终文本是「前面段 + 选中值」。
        base = ' '.join(self._prefix_segments)
        full = (base + ' ' + text) if base else text

        def _restore():
            widget.blockSignals(True)
            widget.setText(full)
            widget.setCursorPosition(len(full))
            widget.blockSignals(False)

        QTimer.singleShot(0, _restore)


def get_completer(item_list):
    """
    Instantiate and config QCompleter.
    """
    completer_ins = MultiValueCompleter(item_list)

    # Enable Qt.MatchContains mode (just like re.search()), not Qt.MatchStartsWith or Qt.MatchEndsWith.
    completer_ins.setFilterMode(Qt.MatchContains)
    # Match upper/lower case.
    completer_ins.setCaseSensitivity(Qt.CaseInsensitive)

    # Adjust the appropriate size of the item.
    if item_list:
        list_view = completer_ins.popup()
        max_length = max(len(item) for item in item_list)
        popup_width = list_view.fontMetrics().width('w' * max_length)
        list_view.setFixedWidth(popup_width)

    return completer_ins


class MyCheckBox(QCheckBox):
    """
    Re-Write eventFilter function for QCheckBox.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.installEventFilter(self)

    def eventFilter(self, watched, event):
        """
        Make sure clicking on the blank section still takes effect.
        """
        if (watched == self) and (event.type() == QEvent.MouseButtonPress):
            if self.rect().contains(event.pos()):
                self.toggle()
                return True

        return super().eventFilter(watched, event)


class _ComboLineEditClickFilter(QObject):
    """Pop up the combo's list when the (read-only) line edit is clicked.

    QComboBox shows the popup on line-edit click by default, but a read-only
    QLineEdit swallows the press; this filter forwards it so clicking the
    content area drops down the list, matching the arrow-button behavior.
    """

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress:
            combo = watched.parent()

            if combo is not None and hasattr(combo, 'showPopup'):
                combo.showPopup()

                return True

        return super().eventFilter(watched, event)


class ComboBoxEventFilter(QObject):
    def __init__(self, comboBox, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comboBox = comboBox
        self.droppedDown = False

    def eventFilter(self, obj, event):
        # MouseButtonPress == 3; the "mouse left the widget" event is QEvent.Leave
        # (value 11 in Qt5). Kept as magic numbers to stay robust across PyQt5 builds.
        if event.type() == 3:
            self.droppedDown = True
        elif event.type() == 11:
            self.droppedDown = False

        return super().eventFilter(obj, event)


class QComboCheckBox(QComboBox):
    """
    QComboCheckBox is a QComboBox with checkbox.
    """
    def __init__(self, parent=None, enableFilter=False):
        super(QComboCheckBox, self).__init__(parent)

        self.enableFilter = bool(enableFilter)
        self._hasFilterItem = False
        self._filterLineEdit = None
        self._filterTextCache = ""

        self.qListWidget = QListWidget()
        self.setModel(self.qListWidget.model())
        self.setView(self.qListWidget)

        self.qLineEdit = QLineEdit()
        self.qLineEdit.textChanged.connect(self.validQLineEditValue)
        self.qLineEdit.setReadOnly(True)
        # A read-only QLineEdit does not forward mouse clicks to the combo, so
        # clicking the content area would not drop down the list. Install a
        # filter that pops up the list on any button press inside the line edit.
        self.qLineEdit.installEventFilter(_ComboLineEditClickFilter(self))
        self.setLineEdit(self.qLineEdit)

        self.checkBoxList = []
        self.itemDataList = []

        self.dropDownBoxWidthPixel = self.width()

        self.eventFilter = ComboBoxEventFilter(self)
        self.view().viewport().installEventFilter(self.eventFilter)

        self.updateDropDownBoxHeight()

        if self.enableFilter:
            self._ensureFilterItem()

    def hidePopup(self):
        if getattr(self.eventFilter, "droppedDown", False):
            return

        return super().hidePopup()

    def validQLineEditValue(self):
        """
        Make sure value of self.qLineEdit always match selected items.
        """
        selectedItemString = ' '.join(self.selectedItems().values())

        if self.qLineEdit.text() != selectedItemString:
            self.updateLineEdit()

    def addCheckBoxItems(self, text_list):
        """
        Add multi QCheckBox format items.
        """
        for text in text_list:
            self.addCheckBoxItem(text)

    def addCheckBoxItem(self, text, data=None, update_width=False):
        """
        Add QCheckBox format item into QListWidget(QComboCheckBox).

        Args:
            text: 显示文本(可不同于内部值)。
            data: 内部值(可选)。为 None 时回退为 text,selectedData() 返回 text。
        """
        if self.enableFilter:
            self._ensureFilterItem()

        qItem = QListWidgetItem(self.qListWidget)
        qBox = MyCheckBox(text)
        qBox.stateChanged.connect(self.qBoxStateChanged)
        self.checkBoxList.append(qBox)
        self.itemDataList.append(data)
        self.qListWidget.setItemWidget(qItem, qBox)

        # Ensure a comfortable row height so text (esp. Chinese) isn't clipped.
        hint = qBox.sizeHint()
        qItem.setSizeHint(QSize(hint.width(), max(hint.height(), 28)))

        if update_width:
            self.updateDropDownBoxWidth(text, qBox)

        if self.enableFilter:
            self._applyFilter(self._filterTextCache)

    def qBoxStateChanged(self, checkState):
        """
        Post process for qBox state change.
        """
        itemText = self.sender().text()

        self.updateItemSelectedState(itemText, checkState)
        self.updateLineEdit()

    def updateItemSelectedState(self, itemText, checkState):
        """
        If "ALL" is selected, unselect other items.
        If other item is selected, unselect "ALL" item.
        """
        if checkState != 0:
            selectedItemDic = self.selectedItems()
            selectedItemList = list(selectedItemDic.values())

            if itemText == 'ALL':
                if len(selectedItemList) > 1:
                    for (i, qBox) in enumerate(self.checkBoxList):
                        if (qBox.text() in selectedItemList) and (qBox.text() != 'ALL'):
                            self.checkBoxList[i].setChecked(False)
            else:
                if 'ALL' in selectedItemList:
                    for (i, qBox) in enumerate(self.checkBoxList):
                        if qBox.text() == 'ALL':
                            self.checkBoxList[i].setChecked(False)
                            break

    def updateLineEdit(self):
        """
        Update QComboCheckBox show message with self.qLineEdit.
        """
        selectedItemString = ' '.join(self.selectedItems().values())
        self.qLineEdit.setReadOnly(False)
        self.qLineEdit.clear()
        self.qLineEdit.setText(selectedItemString)
        self.qLineEdit.setReadOnly(True)

    def updateDropDownBoxWidth(self, text, qBox):
        """
        Update self.dropDownBoxWidthPixel.
        """
        fm = QFontMetrics(QFont())

        try:
            textPixel = fm.horizontalAdvance(text)
        except Exception:
            textPixel = fm.width(text)

        indicatorPixel = int(qBox.iconSize().width() * 1.4) or 24

        if textPixel > self.dropDownBoxWidthPixel:
            self.dropDownBoxWidthPixel = textPixel
            self.view().setMinimumWidth(self.dropDownBoxWidthPixel + indicatorPixel)

    def updateDropDownBoxHeight(self):
        fm = QFontMetrics(QFont())
        fontPixel = fm.height() + 2
        self.setStyleSheet(f"""
            QComboBox QAbstractItemView::item {{
                min-height: {fontPixel}px;
                padding: 4px 8px;
                margin: 0px;
                border-radius: 3px;
            }}
        """)

    def selectedItems(self):
        """
        Get all selected items (location and value).
        """
        selectedItemDic = {}

        for (i, qBox) in enumerate(self.checkBoxList):
            if qBox.isChecked() is True:
                selectedItemDic.setdefault(i, qBox.text())

        return selectedItemDic

    def itemData(self, index):
        """Get internal data value of item at given index (falls back to text if data is None)."""
        if 0 <= index < len(self.itemDataList):
            data = self.itemDataList[index]

            return data if data is not None else self.checkBoxList[index].text()

        return None

    def selectedData(self):
        """Get all selected items' internal data values (falls back to text if data is None)."""
        selectedItemDic = {}

        for (i, qBox) in enumerate(self.checkBoxList):
            if qBox.isChecked() is True:
                data = self.itemDataList[i] if i < len(self.itemDataList) else None
                selectedItemDic.setdefault(i, data if data is not None else qBox.text())

        return selectedItemDic

    def selectAllItems(self):
        """
        Select all items.
        """
        for (i, qBox) in enumerate(self.checkBoxList):
            if qBox.isChecked() is False:
                self.checkBoxList[i].setChecked(True)

    def unselectAllItems(self):
        """
        Unselect all items.
        """
        for (i, qBox) in enumerate(self.checkBoxList):
            if qBox.isChecked() is True:
                self.checkBoxList[i].setChecked(False)

    def clear(self):
        """
        Clear all items.
        """
        super().clear()

        self.qListWidget.clear()
        self.checkBoxList.clear()
        self.itemDataList.clear()

        self._hasFilterItem = False
        self._filterLineEdit = None
        self._filterTextCache = ""

        if getattr(self, "enableFilter", False):
            self._ensureFilterItem()

        self.updateLineEdit()

    def setEnableFilter(self, enabled: bool):
        enabled = bool(enabled)

        if enabled == self.enableFilter:
            return

        self.enableFilter = enabled

        if self.enableFilter:
            self._ensureFilterItem()
            self._applyFilter(self._filterTextCache)
        else:
            if self._hasFilterItem and self.qListWidget.count() > 0:
                firstItem = self.qListWidget.item(0)
                w = self.qListWidget.itemWidget(firstItem)

                if isinstance(w, QLineEdit):
                    self.qListWidget.takeItem(0)

            self._hasFilterItem = False
            self._filterLineEdit = None
            self._filterTextCache = ""

            for row in range(self.qListWidget.count()):
                item = self.qListWidget.item(row)
                item.setHidden(False)

    def _ensureFilterItem(self):
        if self._hasFilterItem:
            return

        filterItem = QListWidgetItem(self.qListWidget)
        self.qListWidget.insertItem(0, filterItem)
        self._filterLineEdit = QLineEdit()
        self._filterLineEdit.setPlaceholderText("Filter…")
        self._filterLineEdit.installEventFilter(self.eventFilter)
        self._filterLineEdit.textChanged.connect(self._applyFilter)
        self.qListWidget.setItemWidget(filterItem, self._filterLineEdit)
        filterItem.setSizeHint(self._filterLineEdit.sizeHint())
        self._hasFilterItem = True

        if self._filterTextCache:
            self._filterLineEdit.setText(self._filterTextCache)

    def _applyFilter(self, text: str):
        self._filterTextCache = text or ""
        patt = self._filterTextCache.lower().strip()

        for i, qBox in enumerate(self.checkBoxList):
            row = i + 1 if self.enableFilter else i
            item = self.qListWidget.item(row)

            if not patt:
                item.setHidden(False)
            else:
                item.setHidden(patt not in qBox.text().lower())

    def showPopup(self):
        # Auto-widen popup so long labels aren't clipped.
        try:
            view = self.view()
            fm = view.fontMetrics()
            widest = 0

            for qBox in self.checkBoxList:
                widest = max(widest, fm.horizontalAdvance(qBox.text()))

            indicator = int(self.iconSize().width() * 1.4) or 24
            needed = widest + indicator + 24

            if needed > getattr(self, "dropDownBoxWidthPixel", 0):
                self.dropDownBoxWidthPixel = needed
                view.setMinimumWidth(needed)
        except Exception:
            pass

        if getattr(self, "enableFilter", False) and getattr(self, "_filterLineEdit", None):
            self._filterLineEdit.blockSignals(True)
            self._filterLineEdit.clear()
            self._filterLineEdit.blockSignals(False)
            self._filterTextCache = ""
            start_row = 1 if self.enableFilter and getattr(self, "_hasFilterItem", False) else 0

            for row in range(start_row, self.qListWidget.count()):
                it = self.qListWidget.item(row)

                if it is not None and it.isHidden():
                    it.setHidden(False)

            m = self.qListWidget.model()

            if hasattr(m, "layoutChanged"):
                m.layoutChanged.emit()

            self.qListWidget.updateGeometry()
            self.qListWidget.viewport().update()

        super().showPopup()

        if getattr(self, "enableFilter", False) and getattr(self, "_filterLineEdit", None):
            QTimer.singleShot(0, self._filterLineEdit.setFocus)

    def setItemsChecked(self, items, checked=True):
        if isinstance(items, str):
            items = [items]

        for qBox in self.checkBoxList:
            if qBox.text() in items:
                qBox.setChecked(bool(checked))

        self.updateLineEdit()


_chart_style_applied = False


def apply_chart_style():
    """Set matplotlib rcParams once so all figures share the theme's look.

    Idempotent: a module-level flag guards re-entry. Call at GUI startup
    (after QApplication exists) so font rcParams take effect. Pulls colors /
    font from gui.theme so charts match the AntD-ish UI instead of matplotlib
    defaults.
    """
    global _chart_style_applied

    if _chart_style_applied:
        return

    import matplotlib as mpl
    try:
        from gui import theme
    except Exception:
        theme = None

    if theme is None:
        _chart_style_applied = True
        return

    rc = {
        # matplotlib has its own font resolver (not Qt's). theme.FONT_FAMILY
        # targets Qt QSS and starts with "PingFang SC" (macOS), which is absent
        # on Linux and triggers "findfont: ... not found". Chart titles/labels
        # are English-only here, so use the generic 'sans-serif' family —
        # matplotlib falls back to DejaVu Sans (bundled, always available) with
        # no warning. Add CJK names only if a chart ever needs Chinese text.
        'font.family':         'sans-serif',
        'font.size':           9,
        'axes.titlesize':      10,
        'axes.labelsize':      9,
        'xtick.labelsize':     8,
        'ytick.labelsize':     8,
        'legend.fontsize':     9,
        'figure.dpi':          100,
        'savefig.dpi':         150,
        'axes.unicode_minus': False,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'axes.grid': True,
        'grid.color':         theme.CHART_COLORS[False]['grid'],
        'grid.linestyle':     '--',
        'grid.linewidth':     0.6,
        'grid.alpha':         0.6,
        'axes.edgecolor':     theme.BORDER_STRONG,
        'axes.linewidth':     0.8,
        'lines.linewidth':    theme.CHART_LINEWIDTH,
        'lines.markersize':   3,
    }

    try:
        mpl.rcParams.update(rc)
    except Exception:
        pass

    _chart_style_applied = True


def style_axes(axes, dark_mode=False, title=None, xlabel=None, ylabel=None):
    """Apply the theme palette + de-clutter to one axes.

    Replaces the per-function dark/light boilerplate (facecolor, 4-spine
    set_color, title/label color, grid) that was duplicated across every
    draw_*_curve in lsf_panel / license_panel. Call after plotting, before
    draw().

    dark_mode: if True, uses the dark palette (facecolor #19232D, light text).
    title/xlabel/ylabel: optional text; colored to match the palette.
    """
    try:
        from gui import theme
    except Exception:
        return

    pal = theme.CHART_COLORS[bool(dark_mode)]

    try:
        axes.set_facecolor(pal['facecolor'])
    except Exception:
        pass

    # Grid: y-axis only, subtle. rcParams sets axes.grid=True for both axes;
    # narrow it to y here (callers that want x-grid can re-enable per-axes).
    axes.grid(True, axis='y', color=pal['grid'], linestyle='--', linewidth=0.6, alpha=0.6)
    axes.grid(False, axis='x')

    # Spines: hide top/right (rcParams already does), tint left/bottom.
    for side in ('top', 'right'):
        axes.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        axes.spines[side].set_color(pal['spine'])
        axes.spines[side].set_linewidth(0.8)

    # Ticks.
    axes.tick_params(colors=pal['tick'], length=3, width=0.6)

    if title is not None:
        axes.set_title(title, color=pal['text'], fontsize=10, pad=8)
    if xlabel is not None:
        axes.set_xlabel(xlabel, color=pal['label'])
    if ylabel is not None:
        axes.set_ylabel(ylabel, color=pal['label'])

    try:
        axes.figure.tight_layout()
    except Exception:
        pass


class FigureCanvasQTAgg(FigureCanvasQTAgg):
    """
    Generate a new figure canvas.
    """
    def __init__(self):
        self.figure = Figure()
        self.axes = None
        super().__init__(self.figure)


class NavigationToolbar2QT(NavigationToolbar2QT):
    """
    Enhancement for NavigationToolbar2QT, can get and show label value.
    """
    def __init__(self, canvas, parent, coordinates=True, x_is_date=True):
        super().__init__(canvas, parent, coordinates)
        self.x_is_date = x_is_date

    @staticmethod
    def bisection(event_xdata, xdata_list):
        xdata = None
        index = None

        if not xdata_list:
            return xdata, index

        lower = 0
        upper = len(xdata_list) - 1
        bisection_index = (upper + lower) // 2

        if event_xdata > xdata_list[upper]:
            xdata = xdata_list[upper]
            index = upper
        elif (event_xdata < xdata_list[lower]) or (len(xdata_list) <= 2):
            xdata = xdata_list[lower]
            index = lower
        elif event_xdata in xdata_list:
            xdata = event_xdata
            index = xdata_list.index(event_xdata)

        while xdata is None:
            if upper - lower == 1:
                if event_xdata - xdata_list[lower] <= xdata_list[upper] - event_xdata:
                    xdata = xdata_list[lower]
                    index = lower
                else:
                    xdata = xdata_list[upper]
                    index = upper

                break

            if event_xdata >= xdata_list[bisection_index]:
                lower = bisection_index
            else:
                upper = bisection_index

            bisection_index = (upper + lower) // 2

        return xdata, index

    def _mouse_event_to_message(self, event):
        if event.inaxes and event.inaxes.get_navigate():
            try:
                if self.x_is_date:
                    event_xdata = num2date(event.xdata).strftime('%Y,%m,%d,%H,%M,%S')
                else:
                    event_xdata = event.xdata
            except (ValueError, OverflowError):
                pass
            else:
                if self.x_is_date and (len(event_xdata.split(',')) == 6):
                    (year, month, day, hour, minute, second) = event_xdata.split(',')
                    event_xdata = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))

                lines = self.canvas.figure.gca().get_lines()

                # Empty figure (e.g. a hint-only canvas with just text and no
                # plotted lines): get_lines() is empty, [0] would IndexError.
                if not lines:
                    return ''

                xdata_list = list(lines[0].get_xdata())
                (xdata, index) = self.bisection(event_xdata, sorted(xdata_list))

                if xdata is not None:
                    info_list = []

                    for line in lines:
                        label = line.get_label()
                        ydata_string = line.get_ydata()
                        ydata_list = list(ydata_string)
                        ydata = ydata_list[index]

                        info_list.append('%s=%s' % (label, ydata))

                    info_string = '  '.join(info_list)

                    if self.x_is_date:
                        xdata_string = xdata.strftime('%Y-%m-%d %H:%M:%S')
                        xdata_string = re.sub(r' 00:00:00', '', xdata_string)
                        info_string = '[%s]\n%s' % (xdata_string, info_string)

                    return info_string

        return ''


class ShowMessage(QThread):
    """Non-blocking loading prompt (spawns tools/message.py as a subprocess).

    Shared by all panels. ONLY for "loading..." progress prompts. For warnings
    / errors that need user confirmation, use QMessageBox instead.

    Uses subprocess.Popen (tracked) + an overridden terminate() that kills the
    child and waits for the QThread to finish. This matters because call sites
    often do ``msg.terminate(); msg = ShowMessage(...)`` (rebinding the local) —
    with the default QThread.terminate() (asynchronous, and with os.system()
    in run() that does not even kill the child) the old QThread can be GC'd
    while still blocked in waitpid(), aborting with "QThread: Destroyed while
    thread is still running". The overridden terminate() guarantees the thread
    has actually stopped before the caller rebinds or the local goes out of
    scope.
    """
    def __init__(self, title, message, persistent=False):
        super(ShowMessage, self).__init__()
        self.title = title
        self.message = message
        self.persistent = persistent
        self._proc = None
        # Signaled once run() has assigned self._proc (or is about to exit).
        # terminate() waits on this so it never observes _proc=None mid-startup
        # and fails to kill a persistent (no-autoclose) child — which would
        # otherwise block self.wait() forever.
        import threading
        self._proc_ready = threading.Event()

    def run(self):
        command = [
            'python3',
            str(os.environ.get('LSFMONITOR_INSTALL_PATH', '')) + '/tools/message.py',
            '--title', str(self.title),
            '--message', str(self.message),
        ]

        if self.persistent:
            command.append('--no-autoclose')

        # Tracked subprocess so terminate() can kill the message window;
        # otherwise a persistent (--no-autoclose) window would be orphaned.
        self._proc = subprocess.Popen(command)
        self._proc_ready.set()
        self._proc.wait()

    def terminate(self):
        """Close the message window and reap the QThread, bounded in time.

        Steps (each with a timeout so the GUI thread is never blocked forever):
          1. Wait briefly for run() to publish self._proc (startup race guard).
          2. SIGTERM the child (graceful), escalate to SIGKILL if needed.
          3. proc.wait(timeout) so the subprocess is reaped.
          4. self.wait(timeout) for run() to return.

        Why bounded: callers do ``msg.terminate()`` on the GUI thread. With a
        persistent (--no-autoclose) child, an unbounded self.wait() here would
        freeze the event loop (table repaints queued behind it never run). The
        old comment worried that wait(timeout) could leave the QThread running
        when the local goes out of scope → "QThread: Destroyed while thread is
        still running". We mitigate by: (a) guaranteeing the child is killed so
        run() unblocks quickly in the normal case, and (b) keeping a generous
        3s self.wait — every call site also does msg.wait(3000) afterward, so
        there is a second chance before any rebind/scope-exit.
        """
        # Step 1: if terminate() races ahead of run(), wait for _proc.
        if self._proc is None:
            self._proc_ready.wait(timeout=2.0)

        # Step 2 + 3: kill and reap the subprocess.
        if self._proc is not None:
            try:
                self._proc.terminate()  # SIGTERM — graceful for PyQt app
            except Exception:
                pass

            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()  # SIGKILL fallback
                except Exception:
                    pass

                try:
                    self._proc.wait(timeout=2.0)
                except Exception:
                    pass
            except Exception:
                pass

        # Step 4: let run() return. Bounded so a wedged child can't freeze the
        # GUI thread indefinitely; caller's wait(3000) is the backstop.
        self.wait(3000)
