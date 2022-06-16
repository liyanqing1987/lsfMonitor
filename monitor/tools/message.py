#!EXPECTED_PYTHON
# -*- coding: utf-8 -*-
################################
# File Name   : process_tracer.py
# Author      : liyanqing
# Created On  : 2021-11-30 17:25:47
# Description :
################################
import os
import sys
import argparse

from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QFrame, QGridLayout, QLabel
from PyQt5.QtCore import Qt

if 'LSFMONITOR_INSTALL_PATH' not in os.environ:
    os.environ['LSFMONITOR_INSTALL_PATH'] = 'LSFMONITOR_INSTALL_PATH_STRING'

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import pyqt5_common

os.environ['PYTHONUNBUFFERED'] = '1'


def readArgs():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-t', '--title',
                        default='Message',
                        help='Specify message title, default is "Message".')
    parser.add_argument('-m', '--message',
                        required=True,
                        default='',
                        help='Required argument, specified message (text).')

    args = parser.parse_args()

    return(args.title, args.message)


class ShowMessage(QMainWindow):
    def __init__(self, title, message):
        super().__init__()
        self.title = title
        self.message = message

        self.initUI()

    def initUI(self):
        # Add mainTab
        self.mainTab = QTabWidget(self)
        self.setCentralWidget(self.mainTab)

        self.mainFrame = QFrame(self.mainTab)

        # Grid
        mainGrid = QGridLayout()
        mainGrid.addWidget(self.mainFrame, 0, 0)
        self.mainTab.setLayout(mainGrid)

        # Generate mainTable
        self.genMainFrame()

        # Show main window
        self.setWindowTitle(self.title)
        self.resize(400, 50)
        pyqt5_common.centerWindow(self)

    def genMainFrame(self):
        self.messageLabel = QLabel(self.mainFrame)
        self.messageLabel.setText(self.message)
        self.messageLabel.setAlignment(Qt.AlignCenter)

        # Grid
        mainFrameGrid = QGridLayout()
        mainFrameGrid.addWidget(self.messageLabel, 0, 0)
        self.mainFrame.setLayout(mainFrameGrid)


################
# Main Process #
################
def main():
    (title, message) = readArgs()
    app = QApplication(sys.argv)
    myShowMessage = ShowMessage(title, message)
    myShowMessage.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
