import re
import math
import datetime
from typing import Optional, Callable

import screeninfo

from PyQt5.QtWidgets import QDesktopWidget, QComboBox, QLineEdit, QListWidget, QCheckBox, QListWidgetItem, QCompleter, QListView
from PyQt5.QtGui import QTextCursor, QFont
from PyQt5.Qt import QFontMetrics
from PyQt5.QtCore import Qt, QEvent, QObject, QModelIndex, QTimer
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
    monitor = screeninfo.get_monitors()[0]

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


def get_completer(item_list):
    """
    Instantiate and config QCompleter.
    """
    completer_ins = QCompleter(item_list)

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


class ComboBoxEventFilter(QObject):
    def __init__(self, comboBox, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.comboBox = comboBox
        self.droppedDown = False

    def eventFilter(self, obj, event):
        # MouseButtonPress
        if event.type() == 3:
            self.droppedDown = True
        # MouseLeave
        elif event.type() == 11:
            self.droppedDown = False

        return super().eventFilter(obj, event)


class CheckListView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QListView.SelectRows)
        self.setEditTriggers(QListView.NoEditTriggers)
        self._on_toggle_request: Optional[Callable[[QModelIndex], None]] = None

    def setToggleHandler(self, fn: Callable[[QModelIndex], None]):
        self._on_toggle_request = fn

    def mousePressEvent(self, event):
        idx = self.indexAt(event.pos())

        if idx.isValid() and self._on_toggle_request:
            self._on_toggle_request(idx)

        self.setCurrentIndex(idx)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            idx = self.currentIndex()

            if idx.isValid() and self._on_toggle_request:
                self._on_toggle_request(idx)

            return
        elif e.key() == Qt.Key_Escape:
            self.parent().hide()
            return

        super().keyPressEvent(e)


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
        self.setLineEdit(self.qLineEdit)

        self.checkBoxList = []

        self.dropDownBoxWidthPixel = self.width()

        self.eventFilter = ComboBoxEventFilter(self)
        self.view().viewport().installEventFilter(self.eventFilter)

        self.updateDropDownBoxHeight()

        if self.enableFilter:
            self._ensureFilterItem()

    def hidePopup(self):
        if getattr(self.eventFilter, "droppedDown", False):
            return
        else:
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

    def addCheckBoxItem(self, text, update_width=False):
        """
        Add QCheckBox format item into QListWidget(QComboCheckBox).
        """
        if self.enableFilter:
            self._ensureFilterItem()

        qItem = QListWidgetItem(self.qListWidget)
        qBox = MyCheckBox(text)
        qBox.stateChanged.connect(self.qBoxStateChanged)
        self.checkBoxList.append(qBox)
        self.qListWidget.setItemWidget(qItem, qBox)
        qItem.setSizeHint(qBox.sizeHint())

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
                padding: 0px;
                margin: 0px;
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
        lower = 0
        upper = len(xdata_list) - 1
        bisection_index = (upper - lower) // 2

        if xdata_list:
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

                if event_xdata > xdata_list[bisection_index]:
                    lower = bisection_index
                elif event_xdata < xdata_list[bisection_index]:
                    upper = bisection_index

                bisection_index = (upper - lower) // 2 + lower

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

                xdata_list = list(self.canvas.figure.gca().get_lines()[0].get_xdata())
                (xdata, index) = self.bisection(event_xdata, sorted(xdata_list))

                if xdata is not None:
                    info_list = []

                    for line in self.canvas.figure.gca().get_lines():
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
