# -*- coding: utf-8 -*-
"""
Unified loading prompt for lsfMonitor.

Used by ShowMessage (a QThread that spawns this as a subprocess) for all
non-blocking "loading..." prompts: LSF data loading (bhosts/bjobs/queues),
license loading, etc. Runs as an independent process so the window stays
responsive even when the caller's GUI thread is blocked.

This is ONLY for progress / loading prompts (always green "Info"). For
warnings/errors that need user confirmation, use QMessageBox instead — it
blocks until the user clicks OK and shows the appropriate icon.

Options:
  --no-autoclose : stay until killed by caller (long operations)
"""
import os
import sys
import argparse

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QTimer

os.environ['PYTHONUNBUFFERED'] = '1'

# Design tokens in sync with gui/theme.py.
BG_WHITE = '#FFFFFF'
TEXT_PRIMARY = '#0F172A'
BORDER = '#DCE3ED'
FONT_FAMILY = '"PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Helvetica Neue", Arial, sans-serif'


def read_args():
    """Read in arguments."""
    parser = argparse.ArgumentParser(
        description="""
message — 加载提示弹窗工具

显示非阻塞的加载提示窗口（绿色 Info），5 秒后自动关闭，也可由调用方手动关闭。
仅供进度/加载提示使用；需要用户确认的警告/错误请使用 QMessageBox。

选项：
  --no-autoclose : 保持显示，直到调用方关闭（用于耗时操作）
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('-t', '--title',
                        nargs='+',
                        default=['Info', ],
                        help='指定消息标题，默认为 "Info"')
    parser.add_argument('-m', '--message',
                        required=True,
                        nargs='+',
                        help='必选参数，指定消息内容（文本）')
    parser.add_argument('--no-autoclose',
                        action='store_true',
                        default=False,
                        help='不自动关闭（5 秒后），保持显示直到调用方关闭')

    args = parser.parse_args()
    title_string = ' '.join(args.title)
    message_string = ' '.join(args.message)

    return title_string, message_string, args.no_autoclose


class MessageDialog(QWidget):
    """Card-style loading prompt with a green title bar."""

    def __init__(self, title, message, no_autoclose=False):
        super().__init__()
        self.no_autoclose = no_autoclose

        # Title shown only in the window title bar (system bar), not repeated
        # in the body — avoids showing "Info" twice.
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setFixedSize(380, 90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        # Message body — the only content in the dialog.
        message_label = QLabel(message, self)
        message_label.setAlignment(Qt.AlignCenter)
        message_label.setWordWrap(True)
        message_label.setStyleSheet(f"""
            QLabel {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
                font-family: {FONT_FAMILY};
                background: transparent;
            }}
        """)
        layout.addWidget(message_label)

        self._center()

        if not self.no_autoclose:
            QTimer.singleShot(5000, self.close)

    def _center(self):
        primary_screen = QApplication.primaryScreen()

        if primary_screen is None:
            return

        screen = primary_screen.geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)


################
# Main Process #
################
def main():
    (title, message, no_autoclose) = read_args()
    app = QApplication(sys.argv)
    dialog = MessageDialog(title, message, no_autoclose=no_autoclose)
    dialog.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
