#!EXPECTED_PYTHON
# -*- coding: utf-8 -*-
################################
# File Name   : show_license_feature_usage.py
# Author      : liyanqing
# Created On  : 2022-06-15 00:00:00
# Description :
################################
import os
import re
import sys
import argparse

from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView
from PyQt5.QtGui import QBrush
from PyQt5.QtCore import Qt

if 'LSFMONITOR_INSTALL_PATH' not in os.environ:
    os.environ['LSFMONITOR_INSTALL_PATH'] = 'LSFMONITOR_INSTALL_PATH_STRING'

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import pyqt5_common
from common import license_common

os.environ['PYTHONUNBUFFERED'] = '1'


def readArgs():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-s', '--server',
                        required=True,
                        default='',
                        help='Specify license server.')
    parser.add_argument('-f', '--feature',
                        required=True,
                        default='',
                        help='Specify license feature.')

    args = parser.parse_args()

    return(args.server, args.feature)


class ShowLicenseFreatureUsage(QMainWindow):
    def __init__(self, server, feature):
        super().__init__()
        self.server = server
        self.feature = feature

        self.licenseFeatureUsageDicList = self.getLicenseFeatureUsage()
        self.initUI()

    def parseFeatureUsageLine(self, line):
        usageDic = {
                    'user': '',
                    'execute_host': '',
                    'submit_host': '',
                    'version': '',
                    'license_server': '',
                    'start_time': '',
                    'license_num': '1',
                   }

        if re.match('^\s*(\S+)\s+(\S+)\s+(\S+)?\s*(.+)?\s*\((\S+)\)\s+\((\S+)\s+(\d+)\), start (.+?)(,\s+(\d+)\s+licenses)?\s*$', line):
            myMatch = re.match('^\s*(\S+)\s+(\S+)\s+(\S+)?\s*(.+)?\s*\((\S+)\)\s+\((\S+)\s+(\d+)\), start (.+?)(,\s+(\d+)\s+licenses)?\s*$', line)
            usageDic['user'] = myMatch.group(1)
            usageDic['execute_host'] = myMatch.group(2)
            displaySetting = myMatch.group(3)

            if displaySetting:
                if re.match('^(.+):.+$', displaySetting):
                    displayMatch = re.match('^(.+):.+$', displaySetting)
                    usageDic['submit_host'] = displayMatch.group(1)

            usageDic['version'] = myMatch.group(5)
            usageDic['license_server'] = myMatch.group(6)
            usageDic['start_time'] = myMatch.group(8)

            licenseNumSetting = myMatch.group(9)

            if licenseNumSetting:
                usageDic['license_num'] = myMatch.group(10)

        return(usageDic)

    def getLicenseFeatureUsage(self):
        # Get license information.
        licenseDic = license_common.getLicenseInfo(specifiedFeature=self.feature)
        licenseFeatureUsageDicList = []

        if self.server in licenseDic:
            if 'feature' in licenseDic[self.server]:
                if self.feature in licenseDic[self.server]['feature']:
                    if 'in_use_info' in licenseDic[self.server]['feature'][self.feature]:
                        for featureUsageLine in licenseDic[self.server]['feature'][self.feature]['in_use_info']:
                            usageDic = self.parseFeatureUsageLine(featureUsageLine)
                            licenseFeatureUsageDicList.append(usageDic)

        return(licenseFeatureUsageDicList)

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
        self.setWindowTitle('"' + str(self.feature) + '" usage on ' + str(self.server))

        self.resize(900, 400)
        pyqt5_common.centerWindow(self)

    def genMainFrame(self):
        self.mainTable = QTableWidget(self.mainFrame)

        # Grid
        mainFrameGrid = QGridLayout()
        mainFrameGrid.addWidget(self.mainTable, 0, 0)
        self.mainFrame.setLayout(mainFrameGrid)

        self.genMainTable()

    def genMainTable(self):
        self.mainTable.setShowGrid(True)
        self.mainTable.setColumnCount(0)
        self.mainTable.setColumnCount(6)
        self.mainTable.setHorizontalHeaderLabels(['USER', 'SUBMIT_HOST', 'EXECUTE_HOST', 'LICENSE_NUM', 'LICENSE_VERSION', 'START_TIME'])

        # Set column width
        self.mainTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.mainTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.mainTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.mainTable.setColumnWidth(3, 120)
        self.mainTable.setColumnWidth(4, 140)
        self.mainTable.setColumnWidth(5, 140)

        # Set item
        self.mainTable.setRowCount(len(self.licenseFeatureUsageDicList))

        titleList = ['user', 'submit_host', 'execute_host', 'license_num', 'version', 'start_time']

        for (row, licenseFeatureUsageDic) in enumerate(self.licenseFeatureUsageDicList):
            for (column, title) in enumerate(titleList):
                item = QTableWidgetItem()
                item.setText(licenseFeatureUsageDic[title])

                if (column == 5) and license_common.checkLongRuntime(licenseFeatureUsageDic[title]):
                    item.setForeground(QBrush(Qt.red))

                self.mainTable.setItem(row, column, item)


################
# Main Process #
################
def main():
    (server, feature) = readArgs()
    app = QApplication(sys.argv)
    myShow = ShowLicenseFreatureUsage(server, feature)
    myShow.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
