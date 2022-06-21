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


def read_args():
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

        self.license_feature_usage_dic_list = self.get_license_feature_usage()
        self.init_ui()

    def parse_feature_usage_line(self, line):
        usage_dic = {
                     'user': '',
                     'execute_host': '',
                     'submit_host': '',
                     'version': '',
                     'license_server': '',
                     'start_time': '',
                     'license_num': '1',
                    }

        if re.match('^\s*(\S+)\s+(\S+)\s+(\S+)?\s*(.+)?\s*\((\S+)\)\s+\((\S+)\s+(\d+)\), start (.+?)(,\s+(\d+)\s+licenses)?\s*$', line):
            my_match = re.match('^\s*(\S+)\s+(\S+)\s+(\S+)?\s*(.+)?\s*\((\S+)\)\s+\((\S+)\s+(\d+)\), start (.+?)(,\s+(\d+)\s+licenses)?\s*$', line)
            usage_dic['user'] = my_match.group(1)
            usage_dic['execute_host'] = my_match.group(2)
            display_setting = my_match.group(3)

            if display_setting:
                if re.match('^(.+):.+$', display_setting):
                    display_match = re.match('^(.+):.+$', display_setting)
                    usage_dic['submit_host'] = display_match.group(1)

            usage_dic['version'] = my_match.group(5)
            usage_dic['license_server'] = my_match.group(6)
            usage_dic['start_time'] = my_match.group(8)

            license_num_setting = my_match.group(9)

            if license_num_setting:
                usage_dic['license_num'] = my_match.group(10)

        return(usage_dic)

    def get_license_feature_usage(self):
        # Get license information.
        license_dic = license_common.get_license_info(specified_feature=self.feature)
        license_feature_usage_dic_list = []

        if self.server in license_dic:
            if 'feature' in license_dic[self.server]:
                if self.feature in license_dic[self.server]['feature']:
                    if 'in_use_info' in license_dic[self.server]['feature'][self.feature]:
                        for feature_usage_line in license_dic[self.server]['feature'][self.feature]['in_use_info']:
                            usage_dic = self.parse_feature_usage_line(feature_usage_line)
                            license_feature_usage_dic_list.append(usage_dic)

        return(license_feature_usage_dic_list)

    def init_ui(self):
        # Add main_tab
        self.main_tab = QTabWidget(self)
        self.setCentralWidget(self.main_tab)

        self.main_frame = QFrame(self.main_tab)

        # Grid
        main_grid = QGridLayout()
        main_grid.addWidget(self.main_frame, 0, 0)
        self.main_tab.setLayout(main_grid)

        # Generate main_table
        self.gen_main_frame()

        # Show main window
        self.setWindowTitle('"' + str(self.feature) + '" usage on ' + str(self.server))

        self.resize(900, 400)
        pyqt5_common.center_window(self)

    def gen_main_frame(self):
        self.main_table = QTableWidget(self.main_frame)

        # Grid
        main_frame_grid = QGridLayout()
        main_frame_grid.addWidget(self.main_table, 0, 0)
        self.main_frame.setLayout(main_frame_grid)

        self.gen_main_table()

    def gen_main_table(self):
        self.main_table.setShowGrid(True)
        self.main_table.setColumnCount(0)
        self.main_table.setColumnCount(6)
        self.main_table.setHorizontalHeaderLabels(['USER', 'SUBMIT_HOST', 'EXECUTE_HOST', 'LICENSE_NUM', 'LICENSE_VERSION', 'START_TIME'])

        # Set column width
        self.main_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.main_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.main_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.main_table.setColumnWidth(3, 120)
        self.main_table.setColumnWidth(4, 140)
        self.main_table.setColumnWidth(5, 140)

        # Set item
        self.main_table.setRowCount(len(self.license_feature_usage_dic_list))

        title_list = ['user', 'submit_host', 'execute_host', 'license_num', 'version', 'start_time']

        for (row, license_feature_usage_dic) in enumerate(self.license_feature_usage_dic_list):
            for (column, title) in enumerate(title_list):
                item = QTableWidgetItem()
                item.setText(license_feature_usage_dic[title])

                if (column == 5) and license_common.check_long_runtime(license_feature_usage_dic[title]):
                    item.setForeground(QBrush(Qt.red))

                self.main_table.setItem(row, column, item)


################
# Main Process #
################
def main():
    (server, feature) = read_args()
    app = QApplication(sys.argv)
    my_show = ShowLicenseFreatureUsage(server, feature)
    my_show.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
