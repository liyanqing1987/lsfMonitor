from PyQt5.QtWidgets import QDesktopWidget
from PyQt5.QtGui import QTextCursor

def centerWindow(window):
    """
    Move the input GUI window into the center of the computer windows.
    """
    qr = window.frameGeometry()
    cp = QDesktopWidget().availableGeometry().center()
    qr.moveCenter(cp)
    window.move(qr.topLeft())

def textEditVisiblePosition(textEditItem, position='End'):
    """
    For QTextEdit widget, show the 'Start' or 'End' part of the text.
    """
    cursor = textEditItem.textCursor()

    if position == 'Start':
        cursor.movePosition(QTextCursor.Start)
    elif position == 'End':
        cursor.movePosition(QTextCursor.End)

    textEditItem.setTextCursor(cursor)
    textEditItem.ensureCursorVisible()
