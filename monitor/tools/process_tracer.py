#!EXPECTED_PYTHON
# -*- coding: utf-8 -*-
################################
# File Name   : process_tracer.py
# Author      : liyanqing
# Created On  : 2021-11-30 00:00:00
# Description : 
################################
import os
import re
import sys
import argparse

from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView, QAction, qApp, QMessageBox
from PyQt5.QtCore import QTimer

if 'LSFMONITOR_INSTALL_PATH' not in os.environ:
    os.environ['LSFMONITOR_INSTALL_PATH'] = 'LSFMONITOR_INSTALL_PATH_STRING'

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from common import lsf_common
from common import pyqt5_common

os.environ['PYTHONUNBUFFERED'] = '1'

def readArgs():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-j', '--job',
                        default='',
                        help='Specify the LSF jobid you want to trace on remote host.')
    parser.add_argument('-p', '--pid',
                        default='',
                        help='Specify the pid you want to trace on local host.')

    args = parser.parse_args()

    if (not args.job) and (not args.pid):
        common.printError('*Error*: "--job" or "--pid" must be specified.')
        sys.exit(1)

    return(args.job, args.pid)


class ProcessTracer(QMainWindow):
    def __init__(self, job, pid):
        super().__init__()
        self.job = job
        self.pid = pid

        self.preprocess()
        self.initUI()

    def preprocess(self):
        self.jobDic = {}
        self.pidList = []

        if self.job:
            (self.jobDic, self.pidList) = self.checkJob(self.job)
        elif self.pid:
            self.pidList = self.checkPid(self.pid)

    def checkJob(self, job):
        command = 'bjobs -UF ' + str(job)
        jobDic = lsf_common.getLsfBjobsUfInfo(command)
   
        if jobDic[job]['status'] != 'RUN':
            common.printError('*Error*: Job "' + str(job) + '" is not running, cannot get process status.')
            sys.exit(1)
        else:
            if not jobDic[job]['pids']:
                common.printError('*Error*: Not find PIDs information for job "' + str(job) + '".')
                sys.exit(1)
    
        return(jobDic, jobDic[job]['pids'])
    
    def checkPid(self, pid):
        pidList = []
        command = 'pstree -p ' + str(pid)

        (returnCode, stdout, stderr) = common.run_command(command)

        for line in str(stdout, 'utf-8').split('\n'):
            line = line.strip()

            if re.findall('\((\d+)\)', line):
                tmpPidList = re.findall('\((\d+)\)', line)

                if tmpPidList:
                    pidList.extend(tmpPidList)

        if not pidList:
            common.printError('*Error*: No valid pid was found.')
            sys.exit(1)

        return(pidList)

    def getProcessInfo(self):
        processDic = {
                      'user' : [],
                      'pid' : [],
                      'cpu' : [],
                      'mem' : [],
                      'stat' : [],
                      'started' : [],
                      'command' : [],
                     }

        command = 'ps -o ruser=userForLongName -o pid,%cpu,%mem,stat,start,command -f' + ','.join(self.pidList)

        if self.job:
            bsubCommand = self.getBsubCommand()
            command = str(bsubCommand) + " '" + str(command) + "'"

        (returnCode, stdout, stderr) = common.run_command(command)

        for line in str(stdout, 'utf-8').split('\n'):
            line = line.strip()

            if re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([a-zA-Z]{3} \d{2}|\d{2}:\d{2}:\d{2})\s(.+)$', line):
                myMatch = re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([a-zA-Z]{3} \d{2}|\d{2}:\d{2}:\d{2})\s(.+)$', line)
                user    = myMatch.group(1)
                pid     = myMatch.group(2)
                cpu     = myMatch.group(3)
                mem     = myMatch.group(4)
                stat    = myMatch.group(5)
                started = myMatch.group(6)
                command = myMatch.group(7)

                processDic['user'].append(user)
                processDic['pid'].append(pid)
                processDic['cpu'].append(cpu)
                processDic['mem'].append(mem)
                processDic['stat'].append(stat)
                processDic['started'].append(started)
                processDic['command'].append(command)
            else:
                continue

        return(processDic)

    def getBsubCommand(self):
        bsubCommand = 'bsub -Is '
        queue = self.jobDic[self.job]['queue']
        startedOn = self.jobDic[self.job]['startedOn']

        if queue:
            bsubCommand = str(bsubCommand) + ' -q ' + str(queue)

        if startedOn:
            startedOnList = startedOn.split()
            bsubCommand = str(bsubCommand) + ' -m ' + str(startedOnList[0])

        return(bsubCommand)

    def initUI(self):
        # Gen menubar
        self.genMenubar()

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
        if self.job:
            self.setWindowTitle('Process Tracer (job:' + str(self.job) + ')')
        elif self.pid:
            self.setWindowTitle('Process Tracer (pid:' + str(self.pid) + ')')

        self.resize(1200, 300)
        pyqt5_common.centerWindow(self)

    def genMenubar(self):
        menubar = self.menuBar()

        # File
        exitAction = QAction('Exit', self)
        exitAction.triggered.connect(qApp.quit)

        fileMenu = menubar.addMenu('File')
        fileMenu.addAction(exitAction)

        # Setup
        freshAction = QAction('Fresh', self)
        freshAction.triggered.connect(self.genMainTable)
        self.periodicFreshTimer = QTimer(self)
        periodicFreshAction = QAction('Periodic Fresh (1 min)', self, checkable=True)
        periodicFreshAction.triggered.connect(self.periodicFresh)

        setupMenu = menubar.addMenu('Setup')
        setupMenu.addAction(freshAction)
        setupMenu.addAction(periodicFreshAction)

        # Help
        aboutAction = QAction('About process_tracer', self)
        aboutAction.triggered.connect(self.showAbout)

        helpMenu = menubar.addMenu('Help')
        helpMenu.addAction(aboutAction)

    def periodicFresh(self, state):
        """
        Fresh the GUI every 60 seconds.
        """
        if state:
            self.periodicFreshTimer.timeout.connect(self.genMainTable)
            self.periodicFreshTimer.start(60000)
        else:
            self.periodicFreshTimer.stop()

    def showAbout(self):
        """
        Show process_tracer about information.
        """
        aboutMessage = 'process_tracer is used to get process tree and trace pid status.'
        QMessageBox.about(self, 'About process_tracer', aboutMessage)

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
        self.mainTable.setColumnCount(7)
        self.mainTable.setHorizontalHeaderLabels(['USER', 'PID', '%CPU', '%MEM', 'STAT', 'STARTED', 'COMMAND'])

        # Set column width
        self.mainTable.setColumnWidth(1, 70)
        self.mainTable.setColumnWidth(2, 60)
        self.mainTable.setColumnWidth(3, 60)
        self.mainTable.setColumnWidth(4, 60)
        self.mainTable.setColumnWidth(5, 80)
        self.mainTable.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)

        # Set click behavior
        self.mainTable.itemClicked.connect(self.mainTabCheckClick)

        # Set item
        self.processDic = self.getProcessInfo()
        self.mainTable.setRowCount(len(self.processDic['pid']))

        titleList = ['user', 'pid', 'cpu', 'mem', 'stat', 'started', 'command']

        for (row, pid) in enumerate(self.processDic['pid']):
            for (column, title) in enumerate(titleList):
                item = QTableWidgetItem()
                item.setText(self.processDic[title][row])
                self.mainTable.setItem(row, column, item)

    def mainTabCheckClick(self, item=None):
        if item != None:
            if item.column() == 1:
                currentRow = self.mainTable.currentRow() 
                pid = self.mainTable.item(currentRow, 1).text()

                command = 'xterm -e "strace -tt -p ' + str(pid) + '"'

                if self.job:
                    bsubCommand = self.getBsubCommand()
                    command = str(bsubCommand) + " '" + str(command) + "'"

                os.system(command)

################
# Main Process #
################
def main():
    (job, pid) = readArgs()
    app = QApplication(sys.argv)
    myProcessTracer = ProcessTracer(job, pid)
    myProcessTracer.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
