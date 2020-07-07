#!PYTHONPATH
# -*- coding: utf-8 -*-

import os
import re
import sys
import stat
import copy
import getpass
from PyQt5.QtWidgets import QApplication, QWidget, QMainWindow, QAction, qApp, QTextEdit, QTabWidget, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QMessageBox, QLineEdit, QComboBox
from PyQt5.QtGui import QPixmap, QBrush, QFont
from PyQt5.QtCore import Qt

sys.path.append('MONITORPATH')
from common import common
from common import lsf_common
from common import pyqt5_common
from conf import config
from bin import bmonitor

os.environ['PYTHONUNBUFFERED'] = '1'

user = getpass.getuser()

# Solve some unexpected warning message.
if 'XDG_RUNTIME_DIR' not in os.environ:
    os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-' + str(user)
    if not os.path.exists(os.environ['XDG_RUNTIME_DIR']):
        os.makedirs(os.environ['XDG_RUNTIME_DIR'])
    os.chmod(os.environ['XDG_RUNTIME_DIR'], stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)

class mainWindow(QMainWindow):
    """
    Main window of lsfMonitor.
    """
    def __init__(self):
        super().__init__()

        self.myDrawCurve = bmonitor.drawCurve()
        self.freshMark = False

        self.initUI()

    def initUI(self):
        """
        Main process, draw the main graphic frame.
        """
        self.queueList = lsf_common.getQueueList()
        self.hostList = lsf_common.getHostList()

        # Add menubar.
        if not self.freshMark:
            self.genMenubar()

        # Define main Tab widget
        self.mainTab = QTabWidget(self)
        self.setCentralWidget(self.mainTab)

        # Define four sub-tabs (JOB/JOBS/HOSTS/QUEUES)
        self.jobTab    = QWidget()
        self.jobsTab   = QWidget()
        self.hostsTab  = QWidget()
        self.queuesTab = QWidget()

        # Add the sub-tabs into main Tab widget
        self.mainTab.addTab(self.jobTab, 'JOB')
        self.mainTab.addTab(self.jobsTab, 'JOBS')
        self.mainTab.addTab(self.hostsTab, 'HOSTS')
        self.mainTab.addTab(self.queuesTab, 'QUEUES')

        # Generate the sub-tabs
        self.genJobTab()
        self.genJobsTab()
        self.genHostsTab()
        self.genQueuesTab()

        # Show main window
        self.resize(1111, 620)
        pyqt5_common.centerWindow(self)
        self.setWindowTitle('lsfMonitor')

    def genMenubar(self):
        """
        Generate menubar.
        """
        menubar = self.menuBar()

        # File
        exitAction = QAction('Quit', self)
        exitAction.triggered.connect(qApp.quit)

        fileMenu = menubar.addMenu('File')
        fileMenu.addAction(exitAction)

        # Setup
        freshAction = QAction('Fresh', self)
        freshAction.triggered.connect(self.fresh)

        setupMenu = menubar.addMenu('Setup')
        setupMenu.addAction(freshAction)

    def fresh(self):
        print('* Re-Loading LSF status, please wait a moment ...')
        self.freshMark = True
        self.initUI()

## Common sub-functions (begin) ##
    def guiWarning(self, warningMessage):
        """
        Show the specified warning message on both of command line and GUI window.
        """
        common.printWarning(warningMessage)
        QMessageBox.warning(self, 'lsfMonitor Warning', warningMessage)
## Common sub-functions (end) ##


## For job TAB (begin) ## 
    def genJobTab(self):
        """
        Generate the job tab on lsfMonitor GUI, show job informations.
        """
        # Init var
        self.currentJob = ''
        self.jobInfoDic = {}

        # self.jobTab
        self.jobTabFrame0 = QFrame(self.jobTab)
        self.jobTabFrame1 = QFrame(self.jobTab)
        self.jobTabFrame2 = QFrame(self.jobTab)
        self.jobTabFrame3 = QFrame(self.jobTab)

        self.jobTabFrame0.setFrameShadow(QFrame.Raised)
        self.jobTabFrame0.setFrameShape(QFrame.Box)
        self.jobTabFrame1.setFrameShadow(QFrame.Raised)
        self.jobTabFrame1.setFrameShape(QFrame.Box)
        self.jobTabFrame2.setFrameShadow(QFrame.Raised)
        self.jobTabFrame2.setFrameShape(QFrame.Box)
        self.jobTabFrame3.setFrameShadow(QFrame.Raised)
        self.jobTabFrame3.setFrameShape(QFrame.Box)

        # self.jobTab - Grid
        jobTabGrid = QGridLayout()

        jobTabGrid.addWidget(self.jobTabFrame0, 0, 0)
        jobTabGrid.addWidget(self.jobTabFrame1, 1, 0)
        jobTabGrid.addWidget(self.jobTabFrame2, 2, 0, 1, 2)
        jobTabGrid.addWidget(self.jobTabFrame3, 0, 1, 2, 1)

        jobTabGrid.setRowStretch(0, 1)
        jobTabGrid.setRowStretch(1, 1)
        jobTabGrid.setRowStretch(2, 10)
        jobTabGrid.setColumnStretch(0, 1)
        jobTabGrid.setColumnStretch(1, 10)

        jobTabGrid.setColumnMinimumWidth(0, 250)
        jobTabGrid.setColumnMinimumWidth(1, 500)

        self.jobTab.setLayout(jobTabGrid)

        # Generate sub-frames
        self.genJobTabFrame0()
        self.genJobTabFrame1()
        self.genJobTabFrame2()
        self.genJobTabFrame3()

    def genJobTabFrame0(self):
        # self.jobTabFrame0
        jobTabJobLabel = QLabel(self.jobTabFrame0)
        jobTabJobLabel.setText('Job')

        self.jobTabJobLine = QLineEdit()

        jobTabCheckButton = QPushButton('Check', self.jobTabFrame0)
        jobTabCheckButton.clicked.connect(self.checkJob)

        # self.jobTabFrame0 - Grid
        jobTabFrame0Grid = QGridLayout()

        jobTabFrame0Grid.addWidget(jobTabJobLabel, 0, 0)
        jobTabFrame0Grid.addWidget(self.jobTabJobLine, 0, 1)
        jobTabFrame0Grid.addWidget(jobTabCheckButton, 0, 2)

        self.jobTabFrame0.setLayout(jobTabFrame0Grid)

    def genJobTabFrame1(self):
        # self.jobTabFrame1
        jobTabUserLabel = QLabel('User', self.jobTabFrame1)
        self.jobTabUserLine = QLineEdit()

        jobTabStatusLabel = QLabel('Status', self.jobTabFrame1)
        self.jobTabStatusLine = QLineEdit()

        jobTabQueueLabel = QLabel('Queue', self.jobTabFrame1)
        self.jobTabQueueLine = QLineEdit()

        jobTabStartedOnLabel = QLabel('Host', self.jobTabFrame1)
        self.jobTabStartedOnLine = QLineEdit()

        jobTabProjectLabel = QLabel('Project', self.jobTabFrame1)
        self.jobTabProjectLine = QLineEdit()

        jobTabProcessorsRequestedLabel = QLabel('Processors', self.jobTabFrame1)
        self.jobTabProcessorsRequestedLine = QLineEdit()

        jobTabCpuTimeLabel = QLabel('Cpu Time', self.jobTabFrame1)
        self.jobTabCpuTimeLine = QLineEdit()

        jobTabRusageMemLabel = QLabel('Rusage', self.jobTabFrame1)
        self.jobTabRusageMemLine = QLineEdit()

        jobTabMemLabel = QLabel('Mem', self.jobTabFrame1)
        self.jobTabMemLine = QLineEdit()

        # self.jobTabFrame1 - Grid
        jobTabFrame1Grid = QGridLayout()

        jobTabFrame1Grid.addWidget(jobTabUserLabel, 0, 0)
        jobTabFrame1Grid.addWidget(self.jobTabUserLine, 0, 1)
        jobTabFrame1Grid.addWidget(jobTabStatusLabel, 1, 0)
        jobTabFrame1Grid.addWidget(self.jobTabStatusLine, 1, 1)
        jobTabFrame1Grid.addWidget(jobTabQueueLabel, 2, 0)
        jobTabFrame1Grid.addWidget(self.jobTabQueueLine, 2, 1)
        jobTabFrame1Grid.addWidget(jobTabStartedOnLabel, 3, 0)
        jobTabFrame1Grid.addWidget(self.jobTabStartedOnLine, 3, 1)
        jobTabFrame1Grid.addWidget(jobTabProjectLabel, 4, 0)
        jobTabFrame1Grid.addWidget(self.jobTabProjectLine, 4, 1)
        jobTabFrame1Grid.addWidget(jobTabProcessorsRequestedLabel, 5, 0)
        jobTabFrame1Grid.addWidget(self.jobTabProcessorsRequestedLine, 5, 1)
        jobTabFrame1Grid.addWidget(jobTabCpuTimeLabel, 6, 0)
        jobTabFrame1Grid.addWidget(self.jobTabCpuTimeLine, 6, 1)
        jobTabFrame1Grid.addWidget(jobTabRusageMemLabel, 7, 0)
        jobTabFrame1Grid.addWidget(self.jobTabRusageMemLine, 7, 1)
        jobTabFrame1Grid.addWidget(jobTabMemLabel, 8, 0)
        jobTabFrame1Grid.addWidget(self.jobTabMemLine, 8, 1)

        self.jobTabFrame1.setLayout(jobTabFrame1Grid)

    def genJobTabFrame2(self):
        # self.jobTabFrame2
        self.jobTabJobInfoText = QTextEdit(self.jobTabFrame2)

        # self.jobTabFrame2 - Grid
        jobTabFrame2Grid = QGridLayout()
        jobTabFrame2Grid.addWidget(self.jobTabJobInfoText, 0, 0)
        self.jobTabFrame2.setLayout(jobTabFrame2Grid)

    def genJobTabFrame3(self):
        # self.jobTabFram3
        self.jobTabMemCurveLabel = QLabel('Job memory curve', self.jobTabFrame3)
        self.jobTabMemCurveLabel.setAlignment(Qt.AlignCenter)

        # self.jobTabFram3 - Grid
        jobTabFrame3Grid = QGridLayout()
        jobTabFrame3Grid.addWidget(self.jobTabMemCurveLabel, 0, 0)
        self.jobTabFrame3.setLayout(jobTabFrame3Grid)

    def checkJob(self):
        """
        Get job information with "bjobs -UF <jobId>", save the infomation into dict self.jobInfoDic.
        Update self.jobTabFrame1 and self.jobTabFrame3.
        """
        self.currentJob = self.jobTabJobLine.text().strip()
        print('* Checking job "' + str(self.currentJob) + '".')

        # Initicalization
        self.updateJobTabFrame1(init=True)
        self.updateJobTabFrame2(init=True)
        self.updateJobTabFrame3(init=True)

        # Job name must be a string of numbers.
        currentJob = self.currentJob

        if re.match('^(\d+)(\[\d+\])?$', self.currentJob):
            my_match = re.match('^(\d+)(\[\d+\])?$', self.currentJob)
            currentJob = my_match.group(1)
        else:
            warningMessage = '*Warning*: No valid job is specified!'
            self.guiWarning(warningMessage)
            return

        # Get job info
        print('Getting job information for job "' + str(self.currentJob) + '".')
        self.jobInfoDic = lsf_common.getBjobsUfInfo(command='bjobs -UF ' + str(currentJob))

        # Update the related frames with the job info.
        self.updateJobTabFrame1()
        self.updateJobTabFrame2()
        self.updateJobTabFrame3()

    def updateJobTabFrame1(self, init=False):
        """
        Update self.jobTabFrame1 with job infos.
        """
        # For "User" item.
        if init:
            self.jobTabUserLine.setText('')
        else:
            self.jobTabUserLine.setText(self.jobInfoDic[self.currentJob]['user'])
            self.jobTabUserLine.setCursorPosition(0)

        # For "Status" item.
        if init:
            self.jobTabStatusLine.setText('')
        else:
            self.jobTabStatusLine.setText(self.jobInfoDic[self.currentJob]['status'])
            self.jobTabStatusLine.setCursorPosition(0)

        # For "Queue" item.
        if init:
            self.jobTabQueueLine.setText('')
        else:
            self.jobTabQueueLine.setText(self.jobInfoDic[self.currentJob]['queue'])
            self.jobTabQueueLine.setCursorPosition(0)

        # For "Host" item.
        if init:
            self.jobTabStartedOnLine.setText('')
        else:
            self.jobTabStartedOnLine.setText(self.jobInfoDic[self.currentJob]['startedOn'])
            self.jobTabStartedOnLine.setCursorPosition(0)

        # For "Processors" item.
        if init:
            self.jobTabProcessorsRequestedLine.setText('')
        else:
            self.jobTabProcessorsRequestedLine.setText(self.jobInfoDic[self.currentJob]['processorsRequested'])
            self.jobTabProcessorsRequestedLine.setCursorPosition(0)

        # For "Cpu Time" item.
        if init:
            self.jobTabCpuTimeLine.setText('')
        else:
            if self.jobInfoDic[self.currentJob]['cpuTime'] != '':
                self.jobTabCpuTimeLine.setText(self.jobInfoDic[self.currentJob]['cpuTime'] + ' s')
                self.jobTabCpuTimeLine.setCursorPosition(0)

        # For "Project" item.
        if init:
            self.jobTabProjectLine.setText('')
        else:
            self.jobTabProjectLine.setText(self.jobInfoDic[self.currentJob]['project'])
            self.jobTabProjectLine.setCursorPosition(0)

        # For "Rusage" item.
        if init:
            self.jobTabRusageMemLine.setText('')
        else:
            if self.jobInfoDic[self.currentJob]['rusageMem'] != '':
                self.jobTabRusageMemLine.setText(self.jobInfoDic[self.currentJob]['rusageMem'] + ' M')
                self.jobTabRusageMemLine.setCursorPosition(0)

        # For "Mem" item.
        if init:
            self.jobTabMemLine.setText('')
        else:
            if self.jobInfoDic[self.currentJob]['mem'] != '':
                self.jobTabMemLine.setText(self.jobInfoDic[self.currentJob]['mem'] + ' M')
                self.jobTabMemLine.setCursorPosition(0)

    def updateJobTabFrame2(self, init=False):
        """
        Show job detailed description info on self.jobTabFrame2/self.jobTabJobInfoText.
        """
        self.jobTabJobInfoText.clear()

        if not init:
            self.jobTabJobInfoText.insertPlainText(self.jobInfoDic[self.currentJob]['jobInfo'])
            pyqt5_common.textEditVisiblePosition(self.jobTabJobInfoText, 'Start')

    def updateJobTabFrame3(self, init=False):
        """
        Draw memory curve for current job, save the png picture and show it on self.jobTabFrame3.
        """
        self.jobTabMemCurveLabel.setText('Job memory curve')

        if not init:
            if self.jobInfoDic[self.currentJob]['status'] != 'PEND':
                # Generate memory curve with the specified job id
                self.myDrawCurve.drawJobMemCurve(self.currentJob)
                memCurveFig = str(config.tmpPath) + '/' + str(user) + '_' + str(self.currentJob) + '.png'

                if os.path.exists(memCurveFig):
                    pixMap = QPixmap(memCurveFig).scaled(self.jobTabMemCurveLabel.width(), self.jobTabMemCurveLabel.height())
                    self.jobTabMemCurveLabel.setPixmap(pixMap)
                else:
                    warningMessage = '*Warning*: Not find memory curve fig "' + str(memCurveFig) + '".'
                    self.guiWarning(warningMessage)
## For job TAB (end) ## 


## For jobs TAB (start) ## 
    def genJobsTab(self):
        """
        Generate the jobs tab on lsfMonitor GUI, show jobs informations.
        """
        # self.jobsTab
        self.jobsTabFrame0 = QFrame(self.jobsTab)
        self.jobsTabFrame0.setFrameShadow(QFrame.Raised)
        self.jobsTabFrame0.setFrameShape(QFrame.Box)

        self.jobsTabTable = QTableWidget(self.jobsTab)
        self.jobsTabTable.itemClicked.connect(self.jobsTabCheckClick)

        # self.jobsTab - Grid
        jobsTabGrid = QGridLayout()

        jobsTabGrid.addWidget(self.jobsTabFrame0, 0, 0)
        jobsTabGrid.addWidget(self.jobsTabTable, 1, 0)

        jobsTabGrid.setRowStretch(0, 1)
        jobsTabGrid.setRowStretch(1, 10)

        self.jobsTab.setLayout(jobsTabGrid)

        # Generate sub-frame
        self.genJobsTabFrame0()
        self.genJobsTabTable()

    def setJobsTabStatusCombo(self, statusList=['RUN', 'PEND', 'ALL']):
        """
        Set (initialize) self.jobsTabStatusCombo.
        """
        self.jobsTabStatusCombo.clear()
        for status in statusList:
            self.jobsTabStatusCombo.addItem(status)

    def setJobsTabQueueCombo(self, queueList=[]):
        """
        Set (initialize) self.jobsTabQueueCombo.
        """
        self.jobsTabQueueCombo.clear()
        if len(queueList) == 0:
            queueList = copy.deepcopy(self.queueList)
            queueList.insert(0, 'ALL')
        for queue in queueList:
            self.jobsTabQueueCombo.addItem(queue)

    def setJobsTabStartedOnCombo(self, hostList=[]):
        """
        Set (initialize) self.jobsTabStartedOnCombo.
        """
        self.jobsTabStartedOnCombo.clear()
        if len(hostList) == 0:
            hostList = copy.deepcopy(self.hostList)
            hostList.insert(0, 'ALL')
        for host in hostList:
            self.jobsTabStartedOnCombo.addItem(host)

    def genJobsTabFrame0(self):
        # self.jobsTabFrame0
        jobsTabUserLabel = QLabel('User', self.jobsTabFrame0)
        jobsTabUserLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabUserLine = QLineEdit()

        jobsTabStatusLabel = QLabel('       Status', self.jobsTabFrame0)
        jobsTabStatusLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabStatusCombo = QComboBox(self.jobsTabFrame0)
        self.setJobsTabStatusCombo()

        jobsTabQueueLabel = QLabel('       Queue', self.jobsTabFrame0)
        jobsTabQueueLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabQueueCombo = QComboBox(self.jobsTabFrame0)
        self.setJobsTabQueueCombo()

        jobsTabStartedOnLabel = QLabel('       Host', self.jobsTabFrame0)
        jobsTabStartedOnLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabStartedOnCombo = QComboBox(self.jobsTabFrame0)
        self.setJobsTabStartedOnCombo()

        self.jobsTabStatusCombo.currentIndexChanged.connect(self.genJobsTabTable)
        self.jobsTabQueueCombo.currentIndexChanged.connect(self.genJobsTabTable)
        self.jobsTabStartedOnCombo.currentIndexChanged.connect(self.genJobsTabTable)

        jobsTabCheckButton = QPushButton('Check', self.jobsTabFrame0)
        jobsTabCheckButton.clicked.connect(self.genJobsTabTable)

        # self.jobsTabFrame0 - Grid
        jobsTabFrame0Grid = QGridLayout()

        jobsTabFrame0Grid.addWidget(jobsTabUserLabel, 0, 0)
        jobsTabFrame0Grid.addWidget(self.jobsTabUserLine, 0, 1)
        jobsTabFrame0Grid.addWidget(jobsTabStatusLabel, 0, 2)
        jobsTabFrame0Grid.addWidget(self.jobsTabStatusCombo, 0, 3)
        jobsTabFrame0Grid.addWidget(jobsTabQueueLabel, 0, 4)
        jobsTabFrame0Grid.addWidget(self.jobsTabQueueCombo, 0, 5)
        jobsTabFrame0Grid.addWidget(jobsTabStartedOnLabel, 0, 6)
        jobsTabFrame0Grid.addWidget(self.jobsTabStartedOnCombo, 0, 7)
        jobsTabFrame0Grid.addWidget(jobsTabCheckButton, 0, 8)

        jobsTabFrame0Grid.setColumnStretch(1, 1)
        jobsTabFrame0Grid.setColumnStretch(3, 1)
        jobsTabFrame0Grid.setColumnStretch(5, 1)
        jobsTabFrame0Grid.setColumnStretch(7, 1)

        self.jobsTabFrame0.setLayout(jobsTabFrame0Grid)

    def genJobsTabTable(self):
        self.jobsTabTable.setShowGrid(True)
        self.jobsTabTable.setSortingEnabled(True)
        self.jobsTabTable.setColumnCount(12)
        self.jobsTabTable.setHorizontalHeaderLabels(['Job', 'User', 'Status', 'Queue', 'Host', 'Started', 'Project', 'Processers', 'cpuTime', 'Rusage (G)', 'Mem (G)', 'Command'])

        command = 'bjobs -UF '
        user = self.jobsTabUserLine.text().strip()

        if re.match('^\s*$', user):
            command = str(command) + ' -u all'
        else:
            command = str(command) + ' -u ' + str(user)

        queue = self.jobsTabQueueCombo.currentText().strip()

        if queue != 'ALL':
            command = str(command) + ' -q ' + str(queue)

        status = self.jobsTabStatusCombo.currentText().strip()

        if status == 'RUN':
            command = str(command) + ' -r'
        elif status == 'PEND':
            command = str(command) + ' -p'
        elif status == 'ALL':
            command = str(command) + ' -a'

        startedOn = self.jobsTabStartedOnCombo.currentText().strip()

        if startedOn != 'ALL':
            command = str(command) + ' -m ' + str(startedOn)

        jobDic = lsf_common.getBjobsUfInfo(command)

        self.jobsTabTable.setRowCount(len(jobDic.keys()))
        jobs = list(jobDic.keys())

        for i in range(len(jobs)):
            job = jobs[i]
            j = 0
            self.jobsTabTable.setItem(i, j, QTableWidgetItem(job))

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['user'])
            self.jobsTabTable.setItem(i, j, item)

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['status'])
            self.jobsTabTable.setItem(i, j, item)

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['queue'])
            self.jobsTabTable.setItem(i, j, item)

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['startedOn'])
            self.jobsTabTable.setItem(i, j, item)

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['startedTime'])
            self.jobsTabTable.setItem(i, j, item)

            j = j+1
            if str(jobDic[job]['project']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, jobDic[job]['project'])
                self.jobsTabTable.setItem(i, j, item)

            j = j+1
            if str(jobDic[job]['processorsRequested']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, int(jobDic[job]['processorsRequested']))
                self.jobsTabTable.setItem(i, j, item)

            j = j+1
            if str(jobDic[job]['cpuTime']) != '':
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, int(jobDic[job]['cpuTime']))
                self.jobsTabTable.setItem(i, j, item)

            j = j+1
            if str(jobDic[job]['rusageMem']) != '':
                item = QTableWidgetItem()
                rusageMemValue = round(int(jobDic[job]['rusageMem'])/1024, 1)
                item.setData(Qt.DisplayRole, rusageMemValue)
                self.jobsTabTable.setItem(i, j, item)

            j = j+1
            if str(jobDic[job]['mem']) != '':
                item = QTableWidgetItem()
                memValue = round(int(jobDic[job]['mem'])/1024, 1)
                item.setData(Qt.DisplayRole, memValue)
                self.jobsTabTable.setItem(i, j, item)

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['command'])
            self.jobsTabTable.setItem(i, j, item)

    def jobsTabCheckClick(self, item=None):
        """
        With the clicked job, jump the the job Tab, show the job related infos.
        """
        if item != None:
            if item.column() == 0:
                currentRow = self.jobsTabTable.currentRow()
                job = self.jobsTabTable.item(currentRow, 0).text().strip()
                if job != '':
                    self.jobTabJobLine.setText(job)
                    self.checkJob()
                    self.mainTab.setCurrentWidget(self.jobTab)
## For jobs TAB (end) ## 


## For hosts TAB (start) ## 
    def genHostsTab(self):
        """
        Generate the hosts tab on lsfMonitor GUI, show hosts informations.
        """
        # self.hostsTabTable
        self.hostsTabFrame0 = QFrame(self.hostsTab)
        self.hostsTabFrame0.setFrameShadow(QFrame.Raised)
        self.hostsTabFrame0.setFrameShape(QFrame.Box)

        self.hostsTabTable = QTableWidget(self.hostsTab)
        self.hostsTabTable.itemClicked.connect(self.hostsTabCheckClick)

        # self.hostsTabTable - Grid
        hostsTabGrid = QGridLayout()

        hostsTabGrid.addWidget(self.hostsTabFrame0, 0, 0)
        hostsTabGrid.addWidget(self.hostsTabTable, 1, 0)

        hostsTabGrid.setRowStretch(0, 1)
        hostsTabGrid.setRowStretch(1, 10)

        self.hostsTab.setLayout(hostsTabGrid)

        # Generate sub-fram
        self.genHostsTabFrame0()
        self.genHostsTabTable()

    def setHostsTabQueueCombo(self, queueList=[]):
        """
        Set (initialize) self.hostsTabQueueCombo.
        """
        self.hostsTabQueueCombo.clear()
        if len(queueList) == 0:
            queueList = copy.deepcopy(self.queueList)
            queueList.insert(0, 'ALL')
        for queue in queueList:
            self.hostsTabQueueCombo.addItem(queue)

    def genHostsTabFrame0(self):
        # self.hostsTabFrame0
        hostsTabQueueLabel = QLabel('       Queue', self.hostsTabFrame0)
        hostsTabQueueLabel.setStyleSheet("font-weight: bold;")
        self.hostsTabQueueCombo = QComboBox(self.hostsTabFrame0)
        self.setHostsTabQueueCombo()
        self.hostsTabQueueCombo.currentIndexChanged.connect(self.genHostsTabTable)
        hostsTabEmptyLabel = QLabel('')

        # self.hostsTabFrame0 - Grid
        hostsTabFrame0Grid = QGridLayout()

        hostsTabFrame0Grid.addWidget(hostsTabQueueLabel, 0, 1)
        hostsTabFrame0Grid.addWidget(self.hostsTabQueueCombo, 0, 2)
        hostsTabFrame0Grid.addWidget(hostsTabEmptyLabel, 0, 3)

        hostsTabFrame0Grid.setColumnStretch(1, 1)
        hostsTabFrame0Grid.setColumnStretch(2, 1)
        hostsTabFrame0Grid.setColumnStretch(3, 8)

        self.hostsTabFrame0.setLayout(hostsTabFrame0Grid)

    def genHostsTabTable(self):
        print('* Updating hosts information, please wait a moment ...')

        self.hostsTabTable.setShowGrid(True)
        self.hostsTabTable.setSortingEnabled(True)
        self.hostsTabTable.setColumnCount(10)
        self.hostsTabTable.setHorizontalHeaderLabels(['Host', 'Status', 'Queue', 'Njobs', 'Ncpus', 'Ut (%)', 'Mem (G)', 'Maxmem (G)', 'swp (G)', 'maxswp (G)'])

        queue = self.hostsTabQueueCombo.currentText().strip()

        bhostsDic  = lsf_common.getBhostsInfo()
        lshostsDic = lsf_common.getLshostsInfo()
        lsloadDic  = lsf_common.getLsloadInfo()
        hostQueueDic = lsf_common.getHostQueueInfo()

        # Get expected host list
        self.queueHostList = []

        if queue == 'ALL':
            self.queueHostList = self.hostList
        else:
            for host in self.hostList:
                if host in hostQueueDic:
                    if queue in hostQueueDic[host]:
                        self.queueHostList.append(host)

        self.hostsTabTable.setRowCount(len(self.queueHostList))

        for i in range(len(self.queueHostList)):
            host = self.queueHostList[i]

            j = 0
            self.hostsTabTable.setItem(i, j, QTableWidgetItem(host))

            j = j+1
            index = bhostsDic['HOST_NAME'].index(host)
            status = bhostsDic['STATUS'][index]
            item = QTableWidgetItem(status)
            if str(status) == 'closed':
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            if host in hostQueueDic.keys():
                queues = ' '.join(hostQueueDic[host])
                item = QTableWidgetItem(queues)
                self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = bhostsDic['HOST_NAME'].index(host)
            njobs = bhostsDic['NJOBS'][index]
            if not re.match('^[0-9]+$', njobs):
                common.printWarning('*Warning*: host(' + str(host) + ') NJOBS info "' + str(njobs) + '": invalid value, reset it to "0".')
                njobs = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(njobs))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = lshostsDic['HOST_NAME'].index(host)
            ncpus = lshostsDic['ncpus'][index]
            if not re.match('^[0-9]+$', ncpus):
                common.printWarning('*Warning*: host(' + str(host) + ') ncpus info "' + str(ncpus) + '": invalid value, reset it to "0".')
                ncpus = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ncpus))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            ut = lsloadDic['ut'][index]
            ut = re.sub('%', '', ut)
            if not re.match('^[0-9]+$', ut):
                common.printWarning('*Warning*: host(' + str(host) + ') ut info "' + str(ut) + '": invalid value, reset it to "0".')
                ut = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ut))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            mem = lsloadDic['mem'][index]
            if re.search('M', mem):
                mem = re.sub('M', '', mem)
                mem = int(mem)/1024
            elif re.search('G', mem):
                mem = re.sub('G', '', mem)
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') mem info "' + str(mem) + '": unrecognized unit, reset it to "0".')
                mem = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(mem))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = lshostsDic['HOST_NAME'].index(host)
            maxmem = lshostsDic['maxmem'][index]
            if re.search('M', maxmem):
                maxmem = re.sub('M', '', maxmem)
                maxmem = int(maxmem)/1024
            elif re.search('G', maxmem):
                maxmem = re.sub('G', '', maxmem)
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') maxmem info "' + str(maxmem) + '": unrecognized unit, reset it to "0".')
                maxmem = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(maxmem))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            swp = lsloadDic['swp'][index]
            if re.search('M', swp):
                swp = re.sub('M', '', swp)
                swp = int(swp)/1024
            elif re.search('G', swp):
                swp = re.sub('G', '', swp)
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') swp info "' + str(swp) + '": unrecognized unit, reset it to "0".')
                swp = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(swp))
            self.hostsTabTable.setItem(i, j, item)

            j = j+1
            index = lshostsDic['HOST_NAME'].index(host)
            maxswp = lshostsDic['maxswp'][index]
            if re.search('M', maxswp):
                maxswp = re.sub('M', '', maxswp)
                maxswp = int(maxswp)/1024
            elif re.search('G', maxswp):
                maxswp = re.sub('G', '', maxswp)
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') maxswp info "' + str(maxswp) + '": unrecognized unit, reset it to "0".')
                maxswp = 0
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(maxswp))
            self.hostsTabTable.setItem(i, j, item)

    def hostsTabCheckClick(self, item=None):
        """
        If click the host name (or Njobs number), jump to the jobs Tab and show the host related jobs.
        """
        if item != None:
            currentRow = self.hostsTabTable.currentRow()
            host = self.hostsTabTable.item(currentRow, 0).text().strip()
            njobsNum = self.hostsTabTable.item(currentRow, 3).text().strip()

            if (item.column() == 0) or (item.column() == 3):
                if int(njobsNum) > 0:
                    self.jobsTabUserLine.setText('')
                    self.setJobsTabStatusCombo()
                    self.setJobsTabQueueCombo()

                    hostList = copy.deepcopy(self.queueHostList)
                    hostList.remove(host)
                    hostList.insert(0, host)
                    hostList.insert(1, 'ALL')
                    self.setJobsTabStartedOnCombo(hostList)

                    self.genJobsTabTable()
                    self.mainTab.setCurrentWidget(self.jobsTab)

                self.mainTab.setCurrentWidget(self.jobsTab)
## For hosts TAB (end) ## 


## For queues TAB (start) ## 
    def genQueuesTab(self):
        """
        Generate the queues tab on lsfMonitor GUI, show queues informations.
        """
        # Init var
        self.bqueuesFilesDic = {}

        # self.queuesTab
        self.queuesTabTable = QTableWidget(self.queuesTab)
        self.queuesTabTable.itemClicked.connect(self.queuesTabCheckClick)

        self.queuesTabFrame0 = QFrame(self.queuesTab)
        self.queuesTabFrame0.setFrameShadow(QFrame.Raised)
        self.queuesTabFrame0.setFrameShape(QFrame.Box)

        self.queuesTabFrame1 = QFrame(self.queuesTab)
        self.queuesTabFrame1.setFrameShadow(QFrame.Raised)
        self.queuesTabFrame1.setFrameShape(QFrame.Box)

        # self.queuesTab - Grid
        queuesTabGrid = QGridLayout()

        queuesTabGrid.addWidget(self.queuesTabTable, 0, 0)
        queuesTabGrid.addWidget(self.queuesTabFrame0, 0, 1)
        queuesTabGrid.addWidget(self.queuesTabFrame1, 1, 0, 1, 2)

        queuesTabGrid.setRowStretch(0, 1)
        queuesTabGrid.setRowStretch(1, 10)
        queuesTabGrid.setColumnStretch(0, 1)
        queuesTabGrid.setColumnStretch(1, 10)

        queuesTabGrid.setRowMinimumHeight(0, 350)
        queuesTabGrid.setRowMinimumHeight(1, 150)
        queuesTabGrid.setColumnMinimumWidth(0, 328)
        queuesTabGrid.setColumnMinimumWidth(1, 500)

        self.queuesTab.setLayout(queuesTabGrid)

        # Generate sub-frame
        self.genQueuesTabTable()
        self.genQueuesTabFrame0()
        self.genQueuesTabFrame1()

    def genQueuesTabTable(self):
        self.queuesTabTable.setShowGrid(True)
        self.queuesTabTable.setColumnCount(3)
        self.queuesTabTable.setHorizontalHeaderLabels(['QUEUE', 'PEND', 'RUN'])

        # Hide the vertical header
        self.queuesTabTable.verticalHeader().setVisible(False)

        queuesDic = lsf_common.getBqueuesInfo()
        self.queuesTabTable.setRowCount(len(self.queueList)+1)
        queueList = copy.deepcopy(self.queueList)
        queueList.append('ALL')
        pendSum = 0
        runSum = 0

        for i in range(len(queueList)):
            queue = queueList[i]
            if i < len(queueList)-1:
                index = queuesDic['QUEUE_NAME'].index(queue)

            j = 0
            item = QTableWidgetItem(queue)
            self.queuesTabTable.setItem(i, j, item)

            j = j+1
            if i == len(queueList)-1:
                pend = str(pendSum)
            else:
                pend = queuesDic['PEND'][index]
                pendSum += int(pend)
            item = QTableWidgetItem(pend)
            if int(pend) > 0:
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))
            self.queuesTabTable.setItem(i, j, item)

            j = j+1
            if i == len(queueList)-1:
                run = str(runSum)
            else:
                run = queuesDic['RUN'][index]
                runSum += int(run)
            item = QTableWidgetItem(run)
            self.queuesTabTable.setItem(i, j, item)

    def genQueuesTabFrame0(self):
        # self.queuesTabFrame0
        self.queuesTabJobNumCurveLabel = QLabel('queue (PEND/RUN) job number curve', self.queuesTabFrame0)
        self.queuesTabJobNumCurveLabel.setAlignment(Qt.AlignCenter)

        # self.queuesTabFrame0 - Grid
        queuesTabFrame0Grid = QGridLayout()
        queuesTabFrame0Grid.addWidget(self.queuesTabJobNumCurveLabel, 0, 0)
        self.queuesTabFrame0.setLayout(queuesTabFrame0Grid)

    def genQueuesTabFrame1(self):
        # self.queuesTabFrame1
        self.queuesTabText = QTextEdit(self.queuesTabFrame1)

        # self.queuesTabFrame1 - Grid
        queuesTabFrame1Grid = QGridLayout()
        queuesTabFrame1Grid.addWidget(self.queuesTabText, 0, 0)
        self.queuesTabFrame1.setLayout(queuesTabFrame1Grid)

    def queuesTabCheckClick(self, item=None):
        """
        If click the queue name, jump to the jobs Tab and show the queue related jobs.
        If click the PEND number, jump the jobs Tab and show the queue PEND related jobs.
        If click the RUN number, jump the jobs Tab and show the queue RUN related jobs.
        """
        if item != None:
            currentRow = self.queuesTabTable.currentRow()
            queue      = self.queuesTabTable.item(currentRow, 0).text().strip()
            pendNum    = self.queuesTabTable.item(currentRow, 1).text().strip()
            runNum     = self.queuesTabTable.item(currentRow, 2).text().strip()

            if item.column() == 0:
                print('* Checking queue "' + str(queue) + '".')
                self.updateQueueTabFrame0(queue)
                self.updateQueueTabFrame1(queue)
            elif item.column() == 1:
                if (pendNum != '') and (int(pendNum) > 0):
                    self.jobsTabUserLine.setText('')

                    statusList = ['PEND', 'RUN', 'ALL']
                    self.setJobsTabStatusCombo(statusList)

                    if queue == 'ALL':
                        self.setJobsTabQueueCombo()
                    else:
                        queueList = copy.deepcopy(self.queueList)
                        queueList.remove(queue)
                        queueList.insert(0, queue)
                        queueList.insert(1, 'ALL')
                        self.setJobsTabQueueCombo(queueList)

                    self.setJobsTabStartedOnCombo()
                    self.genJobsTabTable()
                    self.mainTab.setCurrentWidget(self.jobsTab)
            elif item.column() == 2:
                if (runNum != '') and (int(runNum) > 0):
                    self.jobsTabUserLine.setText('')

                    statusList = ['RUN', 'PEND', 'ALL']
                    self.setJobsTabStatusCombo(statusList)

                    if queue == 'ALL':
                        self.setJobsTabQueueCombo()
                    else:
                        queueList = copy.deepcopy(self.queueList)
                        queueList.remove(queue)
                        queueList.insert(0, queue)
                        queueList.insert(1, 'ALL')
                        self.setJobsTabQueueCombo(queueList)

                    self.setJobsTabStartedOnCombo()
                    self.genJobsTabTable()
                    self.mainTab.setCurrentWidget(self.jobsTab)

    def updateQueueTabFrame0(self, queue):
        """
        Draw queue (PEND/RUN) job number current job, save the png picture and show it on self.queuesTabFrame0.
        """
        self.queuesTabJobNumCurveLabel.setText('queue (PEND/RUN) job number curve')

        # Generate queue job number curve with the specified job id
        self.myDrawCurve.drawQueueJobNumCurve(queue)
        queueJobNumCurveFig = str(config.tmpPath) + '/' + str(user) + '_' + str(queue) + '_jobNum.png'

        if os.path.exists(queueJobNumCurveFig):
            pixMap = QPixmap(queueJobNumCurveFig).scaled(self.queuesTabJobNumCurveLabel.width(), self.queuesTabJobNumCurveLabel.height())
            self.queuesTabJobNumCurveLabel.setPixmap(pixMap)
        else:
            warningMessage = '*Warning*: Not find queue job number curve fig "' + str(queueJobNumCurveFig) + '".'
            self.guiWarning(warningMessage)

    def updateQueueTabFrame1(self, queue):
        """
        Show queue detailed informations on self.queuesTabText.
        """
        self.queuesTabText.clear()

        command = 'bqueues -l ' + str(queue)
        lines = os.popen(command).readlines()

        for line in lines:
            self.queuesTabText.insertPlainText(line)

        pyqt5_common.textEditVisiblePosition(self.queuesTabText, 'Start')
## For queues TAB (end) ## 

    def closeEvent(self, QCloseEvent):
        """
        When window close, post-process.
        """
        print('Bye')

#################
# Main Function #
#################
def main():
    print('* Loading LSF status, please wait a moment ...')
    app = QApplication(sys.argv)
    mw = mainWindow()
    mw.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
