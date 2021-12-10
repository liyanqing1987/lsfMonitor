# -*- coding: utf-8 -*-

import os
import re
import sys
import stat
import copy
import time
import getpass
import datetime
import argparse

from PyQt5.QtWidgets import QApplication, QWidget, QMainWindow, QAction, qApp, QTextEdit, QTabWidget, QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QMessageBox, QLineEdit, QComboBox, QHeaderView
from PyQt5.QtGui import QBrush, QFont
from PyQt5.QtCore import Qt, QTimer, QThread

from matplotlib.backends.backend_qt5 import NavigationToolbar2QT
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from common import lsf_common
from common import license_common
from common import pyqt5_common
from common import sqlite3_common
from conf import config

os.environ['PYTHONUNBUFFERED'] = '1'

# Solve some unexpected warning message.
if 'XDG_RUNTIME_DIR' not in os.environ:
    user = getpass.getuser()
    os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-' + str(user)

    if not os.path.exists(os.environ['XDG_RUNTIME_DIR']):
        os.makedirs(os.environ['XDG_RUNTIME_DIR'])

    os.chmod(os.environ['XDG_RUNTIME_DIR'], stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)

def check_tool():
    """
    Make sure LSF or Openlava environment exists.
    """
    tool = lsf_common.getToolName()

    if tool == '':
        print('*Error*: Not find any LSF or Openlava environment!')
        sys.exit(1)

def readArgs():
    """
    Read arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-j", "--jobid",
                        type=int,
                        help='Specify the jobid which show it\'s information on "JOB" tab.')
    parser.add_argument("-u", "--user",
                        default='',
                        help='Specify the user show how\'s job information on "JOBS" tab.')
    parser.add_argument("-f", "--feature",
                        default='',
                        help='Specify license feature which you want to see on "LICENSE" tab.')
    parser.add_argument("-t", "--tab",
                        default='',
                        choices=['JOB', 'JOBS', 'HOSTS', 'QUEUES', 'LOAD', 'LICENSE'],
                        help='Specify the current tab, default is "JOB" tab.')

    args = parser.parse_args()

    # Make sure specified job exists.
    if args.jobid:
        if not args.tab:
            args.tab = 'JOB'

        command = 'bjobs -w ' + str(args.jobid)
        jobDic = lsf_common.getBjobsInfo(command)

        if not jobDic:
            args.jobid = None

    # Set default tab for args.user.
    if args.user:
        if not args.tab:
            args.tab = 'JOBS'

    # Set default tab for args.feature.
    if args.feature:
        if not args.tab:
            args.tab = 'LICENSE'

    # Set default tab.
    if not args.tab:
        args.tab = 'JOB'

    return(args.jobid, args.user, args.feature, args.tab)


class FigureCanvas(FigureCanvasQTAgg):
    """
    Generate a new figure canvas.
    """
    def __init__(self):
        self.figure = Figure()
        super().__init__(self.figure)


class MainWindow(QMainWindow):
    """
    Main window of lsfMonitor.
    """
    def __init__(self, specifiedJob, specifiedUser, specifiedFeature, specifiedTab):
        super().__init__()
        self.specifiedJob = specifiedJob
        self.specifiedUser = specifiedUser
        self.specifiedFeature = specifiedFeature
        self.initUI()
        self.switchTab(specifiedTab)

    def initUI(self):
        """
        Main process, draw the main graphic frame.
        """
        # Add menubar.
        self.genMenubar()

        # Define main Tab widget
        self.mainTab = QTabWidget(self)
        self.setCentralWidget(self.mainTab)

        # Define four sub-tabs (JOB/JOBS/HOSTS/QUEUES)
        self.jobTab     = QWidget()
        self.jobsTab    = QWidget()
        self.hostsTab   = QWidget()
        self.queuesTab  = QWidget()
        self.loadTab    = QWidget()
        self.licenseTab = QWidget()

        # Add the sub-tabs into main Tab widget
        self.mainTab.addTab(self.jobTab, 'JOB')
        self.mainTab.addTab(self.jobsTab, 'JOBS')
        self.mainTab.addTab(self.hostsTab, 'HOSTS')
        self.mainTab.addTab(self.queuesTab, 'QUEUES')
        self.mainTab.addTab(self.loadTab, 'LOAD')
        self.mainTab.addTab(self.licenseTab, 'LICENSE')

        # Get LSF queue/host information.
        print('* Loading LSF basic information, please wait a moment ...')
        self.queueList = lsf_common.getQueueList()
        self.hostList = lsf_common.getHostList()

        # Get license information.
        print('* Loading license basic information, please wait a moment ...')
        self.licenseDic = license_common.getLicenseInfo()

        # Generate the sub-tabs
        self.genJobTab()
        self.genJobsTab()
        self.genHostsTab()
        self.genQueuesTab()
        self.genLoadTab()
        self.genLicenseTab()

        # Show main window
        self.setWindowTitle('lsfMonitor')
        self.resize(1111, 620)
        pyqt5_common.centerWindow(self)

    def switchTab(self, specifiedTab):
        """
        Switch to the specified Tab.
        """
        tabDic = {
                  'JOB'     : self.jobTab,
                  'JOBS'    : self.jobsTab,
                  'HOSTS'   : self.hostsTab,
                  'QUEUES'  : self.queuesTab,
                  'LOAD'    : self.loadTab,
                  'LICENSE' : self.licenseTab,
                 }

        self.mainTab.setCurrentWidget(tabDic[specifiedTab])

    def genMenubar(self):
        """
        Generate menubar.
        """
        menubar = self.menuBar()

        # File
        exitAction = QAction('Exit', self)
        exitAction.triggered.connect(qApp.quit)

        fileMenu = menubar.addMenu('File')
        fileMenu.addAction(exitAction)

        # Setup
        freshAction = QAction('Fresh', self)
        freshAction.triggered.connect(self.fresh)
        self.periodicFreshTimer = QTimer(self)
        periodicFreshAction = QAction('Periodic Fresh (1 min)', self, checkable=True)
        periodicFreshAction.triggered.connect(self.periodicFresh)

        setupMenu = menubar.addMenu('Setup')
        setupMenu.addAction(freshAction)
        setupMenu.addAction(periodicFreshAction)

        # Help
        aboutAction = QAction('About lsfMonitor', self)
        aboutAction.triggered.connect(self.showAbout)

        helpMenu = menubar.addMenu('Help')
        helpMenu.addAction(aboutAction)

    def fresh(self):
        """
        Re-build the GUI with latest LSF status.
        """
        currentTime = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        print('* [' + str(currentTime) + '] Re-Loading LSF and License status, please wait a moment ...')

        self.genJobsTabTable()
        self.genHostsTabTable()
        self.genQueuesTabTable()
        self.genLicenseTabFeatureTable(update=True)
        self.genLicenseTabExpiresTable()

    def periodicFresh(self, state):
        """
        Fresh the GUI every 60 seconds.
        """
        if state:
            self.periodicFreshTimer.timeout.connect(self.fresh)
            self.periodicFreshTimer.start(60000)
        else:
            self.periodicFreshTimer.stop()

    def showAbout(self):
        """
        Show lsfMonitor about information.
        """
        readmeFile = str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/README'
        aboutMessage = ''

        with open(readmeFile, 'r') as RF:
            for line in RF.readlines():
                aboutMessage = str(aboutMessage) + str(line)

        QMessageBox.about(self, 'About lsfMonitor', aboutMessage)


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
        jobTabGrid.setRowStretch(1, 14)
        jobTabGrid.setRowStretch(2, 6)
        jobTabGrid.setColumnStretch(0, 1)
        jobTabGrid.setColumnStretch(1, 10)

        jobTabGrid.setRowMinimumHeight(0, 60)
        jobTabGrid.setRowMinimumHeight(1, 320)
        jobTabGrid.setRowMinimumHeight(2, 120)
        jobTabGrid.setColumnMinimumWidth(0, 250)
        jobTabGrid.setColumnMinimumWidth(1, 500)

        self.jobTab.setLayout(jobTabGrid)

        # Generate sub-frames
        self.genJobTabFrame0()
        self.genJobTabFrame1()
        self.genJobTabFrame2()
        self.genJobTabFrame3()

        if self.specifiedJob:
            self.jobTabJobLine.setText(str(self.specifiedJob))
            self.checkJob()

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

        jobTabRusageMemLabel = QLabel('Rusage', self.jobTabFrame1)
        self.jobTabRusageMemLine = QLineEdit()

        jobTabMemLabel = QLabel('Mem', self.jobTabFrame1)
        self.jobTabMemLine = QLineEdit()

        jobTabAvgMemLabel = QLabel('AvgMem', self.jobTabFrame1)
        self.jobTabAvgMemLine = QLineEdit()

        jobTabMaxMemLabel = QLabel('MaxMem', self.jobTabFrame1)
        self.jobTabMaxMemLine = QLineEdit()

        processTracerButton = QPushButton('Process  Tracer', self.jobTabFrame1)
        processTracerButton.clicked.connect(self.processTracer)

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
        jobTabFrame1Grid.addWidget(jobTabRusageMemLabel, 6, 0)
        jobTabFrame1Grid.addWidget(self.jobTabRusageMemLine, 6, 1)
        jobTabFrame1Grid.addWidget(jobTabMemLabel, 7, 0)
        jobTabFrame1Grid.addWidget(self.jobTabMemLine, 7, 1)
        jobTabFrame1Grid.addWidget(jobTabAvgMemLabel, 8, 0)
        jobTabFrame1Grid.addWidget(self.jobTabAvgMemLine, 8, 1)
        jobTabFrame1Grid.addWidget(jobTabMaxMemLabel, 9, 0)
        jobTabFrame1Grid.addWidget(self.jobTabMaxMemLine, 9, 1)
        jobTabFrame1Grid.addWidget(processTracerButton, 10, 0, 1, 2)

        self.jobTabFrame1.setLayout(jobTabFrame1Grid)

    def processTracer(self):
        # Call script process_tracer.py to get job process information.
        self.currentJob = self.jobTabJobLine.text().strip()

        if self.currentJob:
            self.myProcessTracer = ProcessTracer(self.currentJob)
            self.myProcessTracer.start()

    def genJobTabFrame2(self):
        # self.jobTabFrame2
        self.jobTabJobInfoText = QTextEdit(self.jobTabFrame2)

        # self.jobTabFrame2 - Grid
        jobTabFrame2Grid = QGridLayout()
        jobTabFrame2Grid.addWidget(self.jobTabJobInfoText, 0, 0)
        self.jobTabFrame2.setLayout(jobTabFrame2Grid)

    def genJobTabFrame3(self):
        # self.jobTabFrame3
        self.jobMemFigureCanvas = FigureCanvas()
        self.jobMemNavigationToolbar = NavigationToolbar2QT(self.jobMemFigureCanvas, self)

        # self.jobTabFrame3 - Grid
        jobTabFrame3Grid = QGridLayout()
        jobTabFrame3Grid.addWidget(self.jobMemNavigationToolbar, 0, 0)
        jobTabFrame3Grid.addWidget(self.jobMemFigureCanvas, 1, 0)
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
        print('* Getting LSF job information for "' + str(currentJob) + '", please wait a moment ...')
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
                rusageMemValue = round(int(self.jobInfoDic[self.currentJob]['rusageMem'])/1024, 1)
                self.jobTabRusageMemLine.setText(str(rusageMemValue) + ' G')
                self.jobTabRusageMemLine.setCursorPosition(0)

        # For "Mem" item.
        if init:
            self.jobTabMemLine.setText('')
        else:
            if self.jobInfoDic[self.currentJob]['mem'] != '':
                memValue = round(float(self.jobInfoDic[self.currentJob]['mem'])/1024, 1)
                self.jobTabMemLine.setText(str(memValue) + ' G')
                self.jobTabMemLine.setCursorPosition(0)

        # For "AvgMem" item.
        if init:
            self.jobTabAvgMemLine.setText('')
        else:
            if self.jobInfoDic[self.currentJob]['avgMem'] != '':
                avgMemValue = round(float(self.jobInfoDic[self.currentJob]['avgMem'])/1024, 1)
                self.jobTabAvgMemLine.setText(str(avgMemValue) + ' G')
                self.jobTabAvgMemLine.setCursorPosition(0)

        # For "MaxMem" item.
        if init:
            self.jobTabMaxMemLine.setText('')
        else:
            if self.jobInfoDic[self.currentJob]['maxMem'] != '':
                maxMemValue = round(float(self.jobInfoDic[self.currentJob]['maxMem'])/1024, 1)
                self.jobTabMaxMemLine.setText(str(maxMemValue) + ' G')
                self.jobTabMaxMemLine.setCursorPosition(0)

    def updateJobTabFrame2(self, init=False):
        """
        Show job detailed description info on self.jobTabFrame2/self.jobTabJobInfoText.
        """
        self.jobTabJobInfoText.clear()

        if not init:
            self.jobTabJobInfoText.insertPlainText(self.jobInfoDic[self.currentJob]['jobInfo'])
            pyqt5_common.textEditVisiblePosition(self.jobTabJobInfoText, 'Start')

    def getJobMemList(self):
        """
        Get job sample-time mem list for self.currentJob.
        """
        runtimeList = []
        realMemList = []

        jobRangeDic = common.getJobRangeDic([self.currentJob,])
        jobRangeList = list(jobRangeDic.keys())
        jobRange = jobRangeList[0]
        jobDbFile= str(config.dbPath) + '/monitor/job/' + str(jobRange) + '.db'

        if not os.path.exists(jobDbFile):
            common.printWarning('*Warning*: Job memory usage information is missing for "' + str(self.currentJob) + '".')
        else:
            (jobDbFileConnectResult, jobDbConn) = sqlite3_common.connectDbFile(jobDbFile)

            if jobDbFileConnectResult == 'failed':
                common.printWarning('*Warning*: Failed on connecting job database file "' + str(jobDbFile) + '".')
            else:
                print('Getting history of job memory usage for job "' + str(self.currentJob) + '".')

                tableName = 'job_' + str(self.currentJob)
                dataDic = sqlite3_common.getSqlTableData(jobDbFile, jobDbConn, tableName, ['sampleTime', 'mem'])

                if not dataDic:
                    common.printWarning('*Warning*: job memory usage information is empty for "' + str(self.currentJob) + '".')
                else:
                    sampleTimeList = dataDic['sampleTime']
                    memList = dataDic['mem']
                    firstSampleTime = datetime.datetime.strptime(str(sampleTimeList[0]), '%Y%m%d_%H%M%S').timestamp()

                    for i in range(len(sampleTimeList)):
                        sampleTime = sampleTimeList[i]
                        currentTime = datetime.datetime.strptime(str(sampleTime), '%Y%m%d_%H%M%S').timestamp()
                        runtime = int((currentTime-firstSampleTime)/60)
                        runtimeList.append(runtime)
                        mem = memList[i]

                        if mem == '':
                            mem = '0'

                        realMem = round(float(mem)/1024, 1)
                        realMemList.append(realMem)

                jobDbConn.close()

        return(runtimeList, realMemList)

    def updateJobTabFrame3(self, init=False):
        """
        Draw memory curve for current job on self.jobTabFrame3.
        """
        fig = self.jobMemFigureCanvas.figure
        fig.clear()
        self.jobMemFigureCanvas.draw()

        if not init:
            if self.jobInfoDic[self.currentJob]['status'] != 'PEND':
                (runtimeList, memList) = self.getJobMemList()

                if runtimeList and memList:
                    self.drawJobMemCurve(fig, runtimeList, memList)

    def drawJobMemCurve(self, fig, runtimeList, memList):
        """
        Draw memory curve for specified job.
        """
        fig.subplots_adjust(bottom=0.2)
        axes = fig.add_subplot(111)
        axes.set_title('job "' + str(self.currentJob) + '" memory curve')
        axes.set_xlabel('Runtime (Minutes)')
        axes.set_ylabel('Memory Usage (G)')
        axes.plot(runtimeList, memList, 'ro-', color='red')
        axes.grid()
        self.jobMemFigureCanvas.draw()
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
        jobsTabGrid.setRowStretch(1, 20)

        self.jobsTab.setLayout(jobsTabGrid)

        # Generate sub-frame
        self.genJobsTabFrame0()

        if self.specifiedUser:
            self.jobsTabUserLine.setText(str(self.specifiedUser))

        self.genJobsTabTable()

    def genJobsTabFrame0(self):
        # self.jobsTabFrame0
        jobsTabStatusLabel = QLabel('Status', self.jobsTabFrame0)
        jobsTabStatusLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabStatusCombo = QComboBox(self.jobsTabFrame0)
        self.setJobsTabStatusCombo(['RUN', 'PEND', 'ALL'])

        jobsTabQueueLabel = QLabel('       Queue', self.jobsTabFrame0)
        jobsTabQueueLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabQueueCombo = QComboBox(self.jobsTabFrame0)
        self.setJobsTabQueueCombo()

        jobsTabStartedOnLabel = QLabel('       Host', self.jobsTabFrame0)
        jobsTabStartedOnLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabStartedOnCombo = QComboBox(self.jobsTabFrame0)
        self.setJobsTabStartedOnCombo()

        jobsTabUserLabel = QLabel('       User', self.jobsTabFrame0)
        jobsTabUserLabel.setStyleSheet("font-weight: bold;")
        self.jobsTabUserLine = QLineEdit()

        self.jobsTabStatusCombo.currentIndexChanged.connect(self.genJobsTabTable)
        self.jobsTabQueueCombo.currentIndexChanged.connect(self.genJobsTabTable)
        self.jobsTabStartedOnCombo.currentIndexChanged.connect(self.genJobsTabTable)

        jobsTabCheckButton = QPushButton('Check', self.jobsTabFrame0)
        jobsTabCheckButton.clicked.connect(self.genJobsTabTable)

        # self.jobsTabFrame0 - Grid
        jobsTabFrame0Grid = QGridLayout()

        jobsTabFrame0Grid.addWidget(jobsTabStatusLabel, 0, 0)
        jobsTabFrame0Grid.addWidget(self.jobsTabStatusCombo, 0, 1)
        jobsTabFrame0Grid.addWidget(jobsTabQueueLabel, 0, 2)
        jobsTabFrame0Grid.addWidget(self.jobsTabQueueCombo, 0, 3)
        jobsTabFrame0Grid.addWidget(jobsTabStartedOnLabel, 0, 4)
        jobsTabFrame0Grid.addWidget(self.jobsTabStartedOnCombo, 0, 5)
        jobsTabFrame0Grid.addWidget(jobsTabUserLabel, 0, 6)
        jobsTabFrame0Grid.addWidget(self.jobsTabUserLine, 0, 7)
        jobsTabFrame0Grid.addWidget(jobsTabCheckButton, 0, 8)

        jobsTabFrame0Grid.setColumnStretch(1, 1)
        jobsTabFrame0Grid.setColumnStretch(3, 1)
        jobsTabFrame0Grid.setColumnStretch(5, 1)
        jobsTabFrame0Grid.setColumnStretch(7, 1)

        self.jobsTabFrame0.setLayout(jobsTabFrame0Grid)

    def genJobsTabTable(self):
        # self.jobsTabTable
        self.jobsTabTable.setShowGrid(True)
        self.jobsTabTable.setSortingEnabled(True)
        self.jobsTabTable.setColumnCount(0)
        self.jobsTabTable.setColumnCount(11)
        self.jobsTabTable.setHorizontalHeaderLabels(['Job', 'User', 'Status', 'Queue', 'Host', 'Started', 'Project', 'Processers', 'Rusage (G)', 'Mem (G)', 'Command'])

        self.jobsTabTable.setColumnWidth(0, 70)
        self.jobsTabTable.setColumnWidth(2, 70)
        self.jobsTabTable.setColumnWidth(7, 80)
        self.jobsTabTable.setColumnWidth(8, 80)
        self.jobsTabTable.setColumnWidth(9, 80)
        self.jobsTabTable.horizontalHeader().setSectionResizeMode(10, QHeaderView.Stretch)

        # Get specified user related jobs.
        command = 'bjobs -UF '
        specifiedUser = self.jobsTabUserLine.text().strip()

        if re.match('^\s*$', specifiedUser):
            command = str(command) + ' -u all'
        else:
            command = str(command) + ' -u ' + str(specifiedUser)

        # Get specified queue related jobs.
        specifiedQueue = self.jobsTabQueueCombo.currentText().strip()

        if specifiedQueue != 'ALL':
            command = str(command) + ' -q ' + str(specifiedQueue)

        # Get specified status (RUN/PEND/ALL) related jobs.
        specifiedStatus = self.jobsTabStatusCombo.currentText().strip()

        if specifiedStatus == 'RUN':
            command = str(command) + ' -r'
        elif specifiedStatus == 'PEND':
            command = str(command) + ' -p'
        elif specifiedStatus == 'ALL':
            command = str(command) + ' -a'

        # Get specified host related jobs.
        specifiedHost = self.jobsTabStartedOnCombo.currentText().strip()

        if specifiedHost != 'ALL':
            command = str(command) + ' -m ' + str(specifiedHost)

        # Run command to get expected jobs information.
        print('* Loading LSF jobs information, please wait a moment ...')
        jobDic = lsf_common.getBjobsUfInfo(command)

        # Fill self.jobsTabTable items.
        self.jobsTabTable.setRowCount(0)
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

            if jobDic[job]['status'] == 'PEND':
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))

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

            if str(jobDic[job]['rusageMem']) != '':
                item = QTableWidgetItem()
                rusageMemValue = round(int(jobDic[job]['rusageMem'])/1024, 1)
                item.setData(Qt.DisplayRole, rusageMemValue)
                self.jobsTabTable.setItem(i, j, item)

            j = j+1

            if str(jobDic[job]['mem']) != '':
                item = QTableWidgetItem()
                memValue = round(float(jobDic[job]['mem'])/1024, 1)
                item.setData(Qt.DisplayRole, memValue)
                self.jobsTabTable.setItem(i, j, item)

                if ((not jobDic[job]['rusageMem']) and (memValue > 0)) or (jobDic[job]['rusageMem'] and (memValue > rusageMemValue)):
                    item.setFont(QFont('song', 10, QFont.Bold))
                    item.setForeground(QBrush(Qt.red))

            j = j+1
            item = QTableWidgetItem()
            item.setText(jobDic[job]['command'])
            self.jobsTabTable.setItem(i, j, item)

    def jobsTabCheckClick(self, item=None):
        """
        If click the Job id, jump to the JOB tab and show the job information.
        If click the "PEND" Status, show the job pend reasons on a QMessageBox.information().
        """
        if item is not None:
            currentRow = self.jobsTabTable.currentRow()
            job = self.jobsTabTable.item(currentRow, 0).text().strip()

            if item.column() == 0:
                if job != '':
                    self.jobTabJobLine.setText(job)
                    self.checkJob()
                    self.mainTab.setCurrentWidget(self.jobTab)
            elif item.column() == 2:
                jobStatus = self.jobsTabTable.item(currentRow, 2).text().strip()

                if jobStatus == 'PEND':
                    command = 'bjobs -UF ' + str(job)

                    print('* Getting LSF job information for "' + str(job) + '", please wait a moment ...')
                    jobDic = lsf_common.getBjobsUfInfo(command)
                    jobPendingReasons = ''

                    for line in jobDic[job]['pendingReasons']:
                        jobPendingReasons = str(jobPendingReasons) + '\n' + str(line)
                        QMessageBox.information(self, 'Pending reason for ' + str(job), jobPendingReasons)

    def setJobsTabStatusCombo(self, statusList):
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

        if not queueList:
            queueList = copy.deepcopy(self.queueList)
            queueList.insert(0, 'ALL')

        for queue in queueList:
            self.jobsTabQueueCombo.addItem(queue)

    def setJobsTabStartedOnCombo(self, hostList=[]):
        """
        Set (initialize) self.jobsTabStartedOnCombo.
        """
        self.jobsTabStartedOnCombo.clear()

        if not hostList:
            hostList = copy.deepcopy(self.hostList)
            hostList.insert(0, 'ALL')

        for host in hostList:
            self.jobsTabStartedOnCombo.addItem(host)
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
        hostsTabGrid.setRowStretch(1, 20)

        self.hostsTab.setLayout(hostsTabGrid)

        # Generate sub-fram
        self.genHostsTabFrame0()
        self.genHostsTabTable()

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

        hostsTabFrame0Grid.addWidget(hostsTabQueueLabel, 0, 0)
        hostsTabFrame0Grid.addWidget(self.hostsTabQueueCombo, 0, 1)
        hostsTabFrame0Grid.addWidget(hostsTabEmptyLabel, 0, 2)

        hostsTabFrame0Grid.setColumnStretch(0, 1)
        hostsTabFrame0Grid.setColumnStretch(1, 1)
        hostsTabFrame0Grid.setColumnStretch(2, 12)

        self.hostsTabFrame0.setLayout(hostsTabFrame0Grid)

    def genHostsTabTable(self):
        # self.hostsTabTable
        self.hostsTabTable.setShowGrid(True)
        self.hostsTabTable.setSortingEnabled(True)
        self.hostsTabTable.setColumnCount(0)
        self.hostsTabTable.setColumnCount(12)
        self.hostsTabTable.setHorizontalHeaderLabels(['Host', 'Status', 'Queue', 'Ncpus', 'MAX', 'Njobs', 'Ut (%)', 'Maxmem (G)', 'Mem (G)', 'Maxswp (G)', 'Swp (G)', 'Tmp (G)'])

        self.hostsTabTable.setColumnWidth(1, 80)
        self.hostsTabTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.hostsTabTable.setColumnWidth(3, 80)
        self.hostsTabTable.setColumnWidth(4, 80)
        self.hostsTabTable.setColumnWidth(5, 80)
        self.hostsTabTable.setColumnWidth(6, 80)
        self.hostsTabTable.setColumnWidth(7, 100)
        self.hostsTabTable.setColumnWidth(8, 80)
        self.hostsTabTable.setColumnWidth(9, 80)
        self.hostsTabTable.setColumnWidth(10, 80)
        self.hostsTabTable.setColumnWidth(11, 80)

        print('* Loading LSF hosts information, please wait a moment ...')

        bhostsDic  = lsf_common.getBhostsInfo()
        lshostsDic = lsf_common.getLshostsInfo()
        lsloadDic  = lsf_common.getLsloadInfo()
        hostQueueDic = lsf_common.getHostQueueInfo()

        # Get expected host list
        self.queueHostList = []
        specifiedQueue = self.hostsTabQueueCombo.currentText().strip()

        if specifiedQueue == 'ALL':
            self.queueHostList = self.hostList
        else:
            for host in self.hostList:
                if host in hostQueueDic:
                    if specifiedQueue in hostQueueDic[host]:
                        self.queueHostList.append(host)

        # Fill self.hostsTabTable items.
        self.hostsTabTable.setRowCount(0)
        self.hostsTabTable.setRowCount(len(self.queueHostList))

        for i in range(len(self.queueHostList)):
            host = self.queueHostList[i]

            # For "Host" item.
            j = 0
            self.hostsTabTable.setItem(i, j, QTableWidgetItem(host))

            # For "Status" item.
            j = j+1
            index = bhostsDic['HOST_NAME'].index(host)
            status = bhostsDic['STATUS'][index]
            item = QTableWidgetItem(status)

            if (str(status) == 'unavail') or (str(status) == 'unreach') or (str(status) == 'closed_LIM'):
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))

            self.hostsTabTable.setItem(i, j, item)

            # For "Queue" item.
            j = j+1

            if host in hostQueueDic.keys():
                queues = ' '.join(hostQueueDic[host])
                item = QTableWidgetItem(queues)
                self.hostsTabTable.setItem(i, j, item)

            # For "Ncpus" item.
            j = j+1
            index = lshostsDic['HOST_NAME'].index(host)
            ncpus = lshostsDic['ncpus'][index]

            if not re.match('^[0-9]+$', ncpus):
                common.printWarning('*Warning*: host(' + str(host) + ') ncpus info "' + str(ncpus) + '": invalid value, reset it to "0".')
                ncpus = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ncpus))
            self.hostsTabTable.setItem(i, j, item)

            # For "MAX" item.
            j = j+1
            index = bhostsDic['HOST_NAME'].index(host)
            max = bhostsDic['MAX'][index]

            if not re.match('^[0-9]+$', max):
                common.printWarning('*Warning*: host(' + str(host) + ') MAX info "' + str(max) + '": invalid value, reset it to "0".')
                max = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(max))
            self.hostsTabTable.setItem(i, j, item)

            # For "Njobs" item.
            j = j+1
            index = bhostsDic['HOST_NAME'].index(host)
            njobs = bhostsDic['NJOBS'][index]

            if not re.match('^[0-9]+$', njobs):
                common.printWarning('*Warning*: host(' + str(host) + ') NJOBS info "' + str(njobs) + '": invalid value, reset it to "0".')
                njobs = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(njobs))
            self.hostsTabTable.setItem(i, j, item)

            # For "Ut" item.
            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            ut = lsloadDic['ut'][index]
            ut = re.sub('%', '', ut)

            if not re.match('^[0-9]+$', ut):
                common.printWarning('*Warning*: host(' + str(host) + ') ut info "' + str(ut) + '": invalid value, reset it to "0".')
                ut = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(ut))

            if int(ut) > 90:
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))

            self.hostsTabTable.setItem(i, j, item)

            # For "Maxmem" item.
            j = j+1
            index = lshostsDic['HOST_NAME'].index(host)
            maxmem = lshostsDic['maxmem'][index]

            if re.search('M', maxmem):
                maxmem = re.sub('M', '', maxmem)
                maxmem = float(maxmem)/1024
            elif re.search('G', maxmem):
                maxmem = re.sub('G', '', maxmem)
            elif re.search('T', maxmem):
                maxmem = re.sub('T', '', maxmem)
                maxmem = float(maxmem)*1024
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') maxmem info "' + str(maxmem) + '": unrecognized unit, reset it to "0".')
                maxmem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(maxmem)))
            self.hostsTabTable.setItem(i, j, item)

            # For "Mem" item.
            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            mem = lsloadDic['mem'][index]

            if re.search('M', mem):
                mem = re.sub('M', '', mem)
                mem = float(mem)/1024
            elif re.search('G', mem):
                mem = re.sub('G', '', mem)
            elif re.search('T', mem):
                mem = re.sub('T', '', mem)
                mem = float(mem)*1024
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') mem info "' + str(mem) + '": unrecognized unit, reset it to "0".')
                mem = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(mem)))

            if (maxmem and (float(mem)/float(maxmem) < 0.1)):
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))

            self.hostsTabTable.setItem(i, j, item)

            # For "MaxSwp" item.
            j = j+1
            index = lshostsDic['HOST_NAME'].index(host)
            maxswp = lshostsDic['maxswp'][index]

            if re.search('M', maxswp):
                maxswp = re.sub('M', '', maxswp)
                maxswp = float(maxswp)/1024
            elif re.search('G', maxswp):
                maxswp = re.sub('G', '', maxswp)
            elif re.search('T', maxswp):
                maxswp = re.sub('T', '', maxswp)
                maxswp = float(maxswp)*1024
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') maxswp info "' + str(maxswp) + '": unrecognized unit, reset it to "0".')
                maxswp = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(maxswp)))
            self.hostsTabTable.setItem(i, j, item)

            # For "Swp" item.
            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            swp = lsloadDic['swp'][index]

            if re.search('M', swp):
                swp = re.sub('M', '', swp)
                swp = float(swp)/1024
            elif re.search('G', swp):
                swp = re.sub('G', '', swp)
            elif re.search('T', swp):
                swp = re.sub('T', '', swp)
                swp = float(swp)*1024
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') swp info "' + str(swp) + '": unrecognized unit, reset it to "0".')
                swp = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(swp)))
            self.hostsTabTable.setItem(i, j, item)

            # For "Tmp" item.
            j = j+1
            index = lsloadDic['HOST_NAME'].index(host)
            tmp = lsloadDic['tmp'][index]

            if re.search('M', tmp):
                tmp = re.sub('M', '', tmp)
                tmp = float(tmp)/1024
            elif re.search('G', tmp):
                tmp = re.sub('G', '', tmp)
            elif re.search('T', tmp):
                tmp = re.sub('T', '', tmp)
                tmp = float(tmp)*1024
            else:
                common.printWarning('*Warning*: host(' + str(host) + ') tmp info "' + str(tmp) + '": unrecognized unit, reset it to "0".')
                tmp = 0

            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, int(float(tmp)))

            if int(float(tmp)) == 0:
                item.setFont(QFont('song', 10, QFont.Bold))
                item.setForeground(QBrush(Qt.red))

            self.hostsTabTable.setItem(i, j, item)

    def hostsTabCheckClick(self, item=None):
        """
        If click the Host name, jump to the LOAD Tab and show the host load inforamtion.
        If click the non-zero Njobs number, jump to the JOBS tab and show the host related jobs information.
        """
        if item is not None:
            currentRow = self.hostsTabTable.currentRow()
            host = self.hostsTabTable.item(currentRow, 0).text().strip()
            njobsNum = self.hostsTabTable.item(currentRow, 5).text().strip()

            if item.column() == 0:
                hostList = copy.deepcopy(self.hostList)
                hostList.remove(host)
                hostList.insert(0, host)
                self.setLoadTabHostCombo(hostList)
                self.mainTab.setCurrentWidget(self.loadTab)
            elif item.column() == 5:
                if int(njobsNum) > 0:
                    self.jobsTabUserLine.setText('')
                    self.setJobsTabStatusCombo(['RUN', 'PEND', 'ALL'])
                    self.setJobsTabQueueCombo()

                    hostList = copy.deepcopy(self.queueHostList)
                    hostList.remove(host)
                    hostList.insert(0, host)
                    hostList.insert(1, 'ALL')
                    self.setJobsTabStartedOnCombo(hostList)

                    self.genJobsTabTable()
                    self.mainTab.setCurrentWidget(self.jobsTab)

                self.mainTab.setCurrentWidget(self.jobsTab)

    def setHostsTabQueueCombo(self):
        """
        Set (initialize) self.hostsTabQueueCombo.
        """
        self.hostsTabQueueCombo.clear()

        queueList = copy.deepcopy(self.queueList)
        queueList.insert(0, 'ALL')

        for queue in queueList:
            self.hostsTabQueueCombo.addItem(queue)
## For hosts TAB (end) ## 


## For queues TAB (start) ## 
    def genQueuesTab(self):
        """
        Generate the queues tab on lsfMonitor GUI, show queues informations.
        """
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

        queuesTabGrid.setRowStretch(0, 2)
        queuesTabGrid.setRowStretch(1, 1)
        queuesTabGrid.setColumnStretch(0, 1)
        queuesTabGrid.setColumnStretch(1, 10)

        queuesTabGrid.setRowMinimumHeight(0, 380)
        queuesTabGrid.setRowMinimumHeight(1, 120)
        queuesTabGrid.setColumnMinimumWidth(0, 328)
        queuesTabGrid.setColumnMinimumWidth(1, 500)

        self.queuesTab.setLayout(queuesTabGrid)

        # Generate sub-frame
        self.genQueuesTabTable()
        self.genQueuesTabFrame0()
        self.genQueuesTabFrame1()

    def genQueuesTabTable(self):
        self.queuesTabTable.setShowGrid(True)
        self.queuesTabTable.setColumnCount(0)
        self.queuesTabTable.setColumnCount(3)
        self.queuesTabTable.setHorizontalHeaderLabels(['QUEUE', 'PEND', 'RUN'])

        self.queuesTabTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queuesTabTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queuesTabTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        # Hide the vertical header
        self.queuesTabTable.verticalHeader().setVisible(False)

        # File self.queuesTabTable items.
        self.queuesTabTable.setRowCount(0)
        self.queuesTabTable.setRowCount(len(self.queueList)+1)

        print('* Loading LSF queue information, please wait a moment ...')
        queuesDic = lsf_common.getBqueuesInfo()
        queueList = copy.deepcopy(self.queueList)

        queueList.append('ALL')

        pendSum = 0
        runSum = 0

        for i in range(len(queueList)):
            queue = queueList[i]
            index = 0

            if i < len(queueList)-1:
                index = queuesDic['QUEUE_NAME'].index(queue)

            # For "QUEUE" item.
            j = 0
            item = QTableWidgetItem(queue)
            self.queuesTabTable.setItem(i, j, item)

            # For "PEND" item.
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

            # For "RUN" item.
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
        self.queueJobNumFigureCanvas = FigureCanvas()
        self.queueJobNumNavigationToolbar = NavigationToolbar2QT(self.queueJobNumFigureCanvas, self)

        # self.queuesTabFrame0 - Grid
        queuesTabFrame0Grid = QGridLayout()
        queuesTabFrame0Grid.addWidget(self.queueJobNumNavigationToolbar, 0, 0)
        queuesTabFrame0Grid.addWidget(self.queueJobNumFigureCanvas, 1, 0)
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
        If click the QUEUE name, show queue information on QUEUE tab.
        If click the PEND number, jump to the JOBS Tab and show the queue PEND jobs.
        If click the RUN number, jump to the JOB Tab and show the queue RUN jobs.
        """
        if item is not None:
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
                    self.setJobsTabStatusCombo(['PEND', 'RUN', 'ALL'])

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
                    self.setJobsTabStatusCombo(['RUN', 'PEND', 'ALL'])

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
        Draw queue (PEND/RUN) job number current job on self.queuesTabFrame0.
        """
        fig = self.queueJobNumFigureCanvas.figure
        fig.clear()
        self.queueJobNumFigureCanvas.draw()

        (dateList, pendList, runList) = self.getQueueJobNumList(queue)

        if dateList and pendList and runList:
            for i in range(len(dateList)):
                dateList[i] = datetime.datetime.strptime(dateList[i], '%Y%m%d')

            self.drawQueueJobNumCurve(fig, queue, dateList, pendList, runList)

    def updateQueueTabFrame1(self, queue):
        """
        Show queue detailed informations on self.queuesTabText.
        """
        self.queuesTabText.clear()

        command = 'bqueues -l ' + str(queue)
        (returnCode, stdout, stderr) = common.run_command(command)

        for line in str(stdout, 'utf-8').split('\n'):
            line = line.strip()
            self.queuesTabText.insertPlainText(line)

        pyqt5_common.textEditVisiblePosition(self.queuesTabText, 'Start')

    def getQueueJobNumList(self, queue):
        """
        Draw (PEND/RUN) job number curve for specified queueu.
        """
        dateList = []
        pendList = []
        runList = []
        tmpPendList = []
        tmpRunList = []

        queueDbFile = str(config.dbPath) + '/monitor/queue.db'

        if not os.path.exists(queueDbFile):
            common.printWarning('*Warning*: queue pend/run job number information is missing for "' + str(queue) + '".')
        else:
            (queueDbFileConnectResult, queueDbConn) = sqlite3_common.connectDbFile(queueDbFile)

            if queueDbFileConnectResult == 'failed':
                common.printWarning('*Warning*: Failed on connecting queue database file "' + str(self.queueDbFile) + '".')
            else:
                print('Getting history of queue PEND/RUN job number for queue "' + str(queue) + '".')

                tableName = 'queue_' + str(queue)
                dataDic = sqlite3_common.getSqlTableData(queueDbFile, queueDbConn, tableName, ['sampleTime', 'PEND', 'RUN'])

                if not dataDic:
                    common.printWarning('*Warning*: queue pend/run job number information is empty for "' + str(queue) + '".')
                else:
                    origSampleTimeList = dataDic['sampleTime']
                    origPendList = dataDic['PEND']
                    origRunList = dataDic['RUN']

                    for i in range(len(origSampleTimeList)):
                        sampleTime = origSampleTimeList[i]
                        date = re.sub('_.*', '', sampleTime)
                        pendNum = origPendList[i]
                        runNum = origRunList[i]

                        if (i != 0) and ((i == len(origSampleTimeList)-1) or (date not in dateList)):
                            pendAvg = int(sum(tmpPendList)/len(tmpPendList))
                            pendList.append(pendAvg)
                            runAvg = int(sum(tmpRunList)/len(tmpRunList))
                            runList.append(runAvg)

                        if date not in dateList:
                            dateList.append(date)
                            tmpPendList = []
                            tmpRunList = []

                        tmpPendList.append(int(pendNum))
                        tmpRunList.append(int(runNum))

                    # Cut dateList/pendList/runList, only save recent 30 days result.
                    if len(dateList) > 30:
                        dateList = dateList[-30:]
                        pendList = pendList[-30:]
                        runList = runList[-30:]

                    if len(dateList) == 0:
                        common.printWarning('*Warning*: queue pend/run job number information is empty for "' + str(queue) + '".')

                    queueDbConn.close()

        return(dateList, pendList, runList)

    def drawQueueJobNumCurve(self, fig, queue, dateList, pendList, runList):
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)
        axes.set_title('queue "' + str(queue) + '" PEND/RUN slots number curve')
        axes.set_xlabel('Sample Date')
        axes.set_ylabel('Slots Num')
        axes.plot(dateList, pendList, 'ro-', label='PEND', color='red')
        axes.plot(dateList, runList, 'ro-', label='RUN', color='green')
        axes.legend(loc='upper right')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.queueJobNumFigureCanvas.draw()
## For queues TAB (end) ## 

## For load TAB (start) ## 
    def genLoadTab(self):
        """
        Generate the load tab on lsfMonitor GUI, show host load (ut/mem) information.
        """
        # self.loadTab
        self.loadTabFrame0 = QFrame(self.loadTab)
        self.loadTabFrame1 = QFrame(self.loadTab)
        self.loadTabFrame2 = QFrame(self.loadTab)

        self.loadTabFrame0.setFrameShadow(QFrame.Raised)
        self.loadTabFrame0.setFrameShape(QFrame.Box)
        self.loadTabFrame1.setFrameShadow(QFrame.Raised)
        self.loadTabFrame1.setFrameShape(QFrame.Box)
        self.loadTabFrame2.setFrameShadow(QFrame.Raised)
        self.loadTabFrame2.setFrameShape(QFrame.Box)

        # self.loadTab - Grid
        loadTabGrid = QGridLayout()

        loadTabGrid.addWidget(self.loadTabFrame0, 0, 0)
        loadTabGrid.addWidget(self.loadTabFrame1, 1, 0)
        loadTabGrid.addWidget(self.loadTabFrame2, 2, 0)

        loadTabGrid.setRowStretch(0, 1)
        loadTabGrid.setRowStretch(1, 10)
        loadTabGrid.setRowStretch(2, 10)

        self.loadTab.setLayout(loadTabGrid)

        # Generate sub-frame
        self.genLoadTabFrame0()
        self.genLoadTabFrame1()
        self.genLoadTabFrame2()

    def genLoadTabFrame0(self):
        # self.loadTabFrame0
        loadTabHostLabel = QLabel('          Host', self.loadTabFrame0)
        loadTabHostLabel.setStyleSheet("font-weight: bold;")
        self.loadTabHostCombo = QComboBox(self.loadTabFrame0)
        self.setLoadTabHostCombo()
        self.loadTabHostCombo.currentIndexChanged.connect(self.updateLoadTabLoadInfo)

        loadTabDateLabel = QLabel('          Date', self.loadTabFrame0)
        loadTabDateLabel.setStyleSheet("font-weight: bold;")
        self.loadTabDateCombo = QComboBox(self.loadTabFrame0)
        self.setLoadTabDateCombo()
        self.loadTabDateCombo.currentIndexChanged.connect(self.updateLoadTabLoadInfo)

        loadTabEmptyLabel = QLabel('')

        # self.loadTabFrame0 - Grid
        loadTabFrame0Grid = QGridLayout()

        loadTabFrame0Grid.addWidget(loadTabHostLabel, 0, 1)
        loadTabFrame0Grid.addWidget(self.loadTabHostCombo, 0, 2)
        loadTabFrame0Grid.addWidget(loadTabDateLabel, 0, 3)
        loadTabFrame0Grid.addWidget(self.loadTabDateCombo, 0, 4)
        loadTabFrame0Grid.addWidget(loadTabEmptyLabel, 0, 5)

        loadTabFrame0Grid.setColumnStretch(1, 1)
        loadTabFrame0Grid.setColumnStretch(2, 1)
        loadTabFrame0Grid.setColumnStretch(3, 1)
        loadTabFrame0Grid.setColumnStretch(4, 1)
        loadTabFrame0Grid.setColumnStretch(5, 10)

        self.loadTabFrame0.setLayout(loadTabFrame0Grid)

    def genLoadTabFrame1(self):
        # self.loadTabFrame1
        self.hostUtFigureCanvas = FigureCanvas()
        self.hostUtNavigationToolbar = NavigationToolbar2QT(self.hostUtFigureCanvas, self)

        # self.loadTabFrame1 - Grid
        loadTabFrame1Grid = QGridLayout()
        loadTabFrame1Grid.addWidget(self.hostUtNavigationToolbar, 0, 0)
        loadTabFrame1Grid.addWidget(self.hostUtFigureCanvas, 1, 0)
        self.loadTabFrame1.setLayout(loadTabFrame1Grid)

    def genLoadTabFrame2(self):
        # self.loadTabFrame2
        self.hostMemFigureCanvas = FigureCanvas()
        self.hostMemNavigationToolbar = NavigationToolbar2QT(self.hostMemFigureCanvas, self)

        # self.loadTabFrame2 - Grid
        loadTabFrame2Grid = QGridLayout()
        loadTabFrame2Grid.addWidget(self.hostMemNavigationToolbar, 0, 0)
        loadTabFrame2Grid.addWidget(self.hostMemFigureCanvas, 1, 0)
        self.loadTabFrame2.setLayout(loadTabFrame2Grid)

    def setLoadTabHostCombo(self, hostList=[]):
        """
        Set (initialize) self.loadTabHostCombo.
        """
        self.loadTabHostCombo.clear()

        if not hostList:
            hostList = copy.deepcopy(self.hostList)
            hostList.insert(0, '')

        for host in hostList:
            self.loadTabHostCombo.addItem(host)

    def setLoadTabDateCombo(self):
        """
        Set (initialize) self.loadTabDateCombo.
        """
        self.loadTabDateCombo.clear()

        dateList = [
                    'Last Day',
                    'Last Week',
                    'Last Month',
                    'Last Year',
                   ]

        for date in dateList:
            self.loadTabDateCombo.addItem(date)

    def updateLoadTabLoadInfo(self):
        """
        Update self.loadTabFrame1 (ut information) and self.loadTabFrame2 (memory information).
        """
        self.specifiedHost = self.loadTabHostCombo.currentText().strip()
        self.specifiedDate = self.loadTabDateCombo.currentText().strip()

        self.updateLoadTabFrame1([], [])
        self.updateLoadTabFrame2([], [])

        (sampleTimeList, utList, memList) = self.getLoadInfo()

        if sampleTimeList:
            self.updateLoadTabFrame1(sampleTimeList, utList)
            self.updateLoadTabFrame2(sampleTimeList, memList)

    def getLoadInfo(self):
        """
        Get sampleTime/ut/mem list for specified host.
        """
        sampleTimeList = []
        utList = []
        memList = []

        loadDbFile = str(config.dbPath) + '/monitor/load.db'

        if not os.path.exists(loadDbFile):
            common.printWarning('*Warning*: load database "' + str(loadDbFile) + '" is missing.')
        else:
            (loadDbFileConnectResult, loadDbConn) = sqlite3_common.connectDbFile(loadDbFile)
          
            if loadDbFileConnectResult == 'failed':
                common.printWarning('*Warning*: Failed on connecting load database file "' + str(loadDbFile) + '".')
            else:
                if self.specifiedHost:
                    print('Getting history of load information for host "' + str(self.specifiedHost) + '".')

                    tableName = 'load_' + str(self.specifiedHost)
                    dataDic = sqlite3_common.getSqlTableData(loadDbFile, loadDbConn, tableName, ['sampleTime', 'ut', 'mem'])

                    if not dataDic:
                        common.printWarning('*Warning*: load information is empty for "' + str(self.specifiedHost) + '".')
                    else:
                        specifiedDateSecond = 0

                        if self.specifiedDate == 'Last Day':
                            specifiedDateSecond = time.mktime((datetime.datetime.now()-datetime.timedelta(days=1)).timetuple())
                        elif self.specifiedDate == 'Last Week':
                            specifiedDateSecond = time.mktime((datetime.datetime.now()-datetime.timedelta(days=7)).timetuple())
                        elif self.specifiedDate == 'Last Month':
                            specifiedDateSecond = time.mktime((datetime.datetime.now()-datetime.timedelta(days=30)).timetuple())
                        elif self.specifiedDate == 'Last Year':
                            specifiedDateSecond = time.mktime((datetime.datetime.now()-datetime.timedelta(days=365)).timetuple())

                        for i in range(len(dataDic['sampleTime'])-1, -1, -1):
                            sampleTimeSecond = time.mktime(datetime.datetime.strptime(dataDic['sampleTime'][i], '%Y%m%d_%H%M%S').timetuple())

                            if sampleTimeSecond > specifiedDateSecond:
                                # For sampleTime
                                sampleTime = datetime.datetime.strptime(dataDic['sampleTime'][i], '%Y%m%d_%H%M%S')
                                sampleTimeList.append(sampleTime)

                                # For ut
                                ut = dataDic['ut'][i]

                                if ut:
                                    ut = int(re.sub('%', '', ut))
                                else:
                                    ut = 0

                                utList.append(ut)

                                # For mem
                                mem = dataDic['mem'][i]

                                if mem:
                                    if re.match('.*M', mem):
                                        mem = round(float(re.sub('M', '', mem))/1024, 1)
                                    elif re.match('.*G', mem):
                                        mem = round(float(re.sub('G', '', mem)), 1)
                                    elif re.match('.*T', mem):
                                        mem = round(float(re.sub('T', '', mem))*1024, 1)
                                else:
                                    mem = 0

                                memList.append(mem)

                    loadDbConn.close()

        return(sampleTimeList, utList, memList)

    def updateLoadTabFrame1(self, sampleTimeList, utList):
        """
        Draw Ut curve for specified host on self.loadTabFrame1.
        """
        fig = self.hostUtFigureCanvas.figure
        fig.clear()
        self.hostUtFigureCanvas.draw()

        if sampleTimeList and utList:
            self.drawHostUtCurve(fig, sampleTimeList, utList)

    def drawHostUtCurve(self, fig, sampleTimeList, utList):
        # Fil self.hostUtFigureCanvas.
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)
        axes.set_title('host "' + str(self.specifiedHost) + '" ut curve')
        axes.set_xlabel('Sample Time')
        axes.set_ylabel('Cpu Utilization (%)')
        axes.plot(sampleTimeList, utList, 'ro-', color='red')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.hostUtFigureCanvas.draw()

    def updateLoadTabFrame2(self, sampleTimeList, memList):
        """
        Draw mem curve for specified host on self.loadTabFrame2.
        """
        fig = self.hostMemFigureCanvas.figure
        fig.clear()
        self.hostMemFigureCanvas.draw()

        if sampleTimeList and memList:
            self.drawHostMemCurve(fig, sampleTimeList, memList)

    def drawHostMemCurve(self, fig, sampleTimeList, memList):
        # File self.hostMemFigureCanvas.
        fig.subplots_adjust(bottom=0.25)
        axes = fig.add_subplot(111)
        axes.set_title('host "' + str(self.specifiedHost) + '" available mem curve')
        axes.set_xlabel('Sample Time')
        axes.set_ylabel('Available RAM (G)')
        axes.plot(sampleTimeList, memList, 'ro-', color='green')
        axes.tick_params(axis='x', rotation=15)
        axes.grid()
        self.hostMemFigureCanvas.draw()
## For load TAB (end) ## 


## For license TAB (start) ## 
    def genLicenseTab(self):
        """
        Generate the license tab on lsfMonitor GUI, show host license usage information.
        """
        # self.licenseTab
        self.licenseTabFrame0 = QFrame(self.licenseTab)
        self.licenseTabFrame0.setFrameShadow(QFrame.Raised)
        self.licenseTabFrame0.setFrameShape(QFrame.Box)

        self.licenseTabFeatureLabel = QLabel('Feature Information', self.licenseTab)
        self.licenseTabFeatureLabel.setStyleSheet("font-weight: bold;") 
        self.licenseTabFeatureLabel.setAlignment(Qt.AlignCenter)
        self.licenseTabExpiresLabel = QLabel('Expires Information', self.licenseTab)
        self.licenseTabExpiresLabel.setStyleSheet("font-weight: bold;") 
        self.licenseTabExpiresLabel.setAlignment(Qt.AlignCenter)

        self.licenseTabFeatureTable = QTableWidget(self.licenseTab)
        self.licenseTabExpiresTable = QTableWidget(self.licenseTab)

        # self.licenseTab - Grid
        licenseTabGrid = QGridLayout()

        licenseTabGrid.addWidget(self.licenseTabFrame0, 0, 0, 1, 2)
        licenseTabGrid.addWidget(self.licenseTabFeatureLabel, 1, 0)
        licenseTabGrid.addWidget(self.licenseTabExpiresLabel, 1, 1)
        licenseTabGrid.addWidget(self.licenseTabFeatureTable, 2, 0)
        licenseTabGrid.addWidget(self.licenseTabExpiresTable, 2, 1)

        licenseTabGrid.setRowStretch(0, 2)
        licenseTabGrid.setRowStretch(1, 1)
        licenseTabGrid.setRowStretch(2, 20)

        self.licenseTab.setLayout(licenseTabGrid)

        # Generate sub-frame
        self.genLicenseTabFrame0()
        self.genLicenseTabFeatureTable()
        self.genLicenseTabExpiresTable()

        if self.specifiedFeature:
            self.licenseTabLicenseFeatureLine.setText(str(self.specifiedFeature))
            self.filterLicenseFeature()

    def genLicenseTabFrame0(self):
        # self.licenseTabFrame0
        licenseTabLicenseServerLabel = QLabel('License Server', self.licenseTabFrame0)
        licenseTabLicenseServerLabel.setStyleSheet("font-weight: bold;")
        self.licenseTabLicenseServerCombo = QComboBox(self.licenseTabFrame0)
        self.setLicenseTabLicenseServerCombo()

        self.licenseTabLicenseServerCombo.currentIndexChanged.connect(self.filterLicenseFeature)

        licenseTabEmpty1Label = QLabel('')

        licenseTabShowLabel = QLabel('   Show', self.licenseTabFrame0)
        licenseTabShowLabel.setStyleSheet("font-weight: bold;")
        self.licenseTabShowCombo = QComboBox(self.licenseTabFrame0)
        self.licenseTabShowCombo.addItem('ALL')
        self.licenseTabShowCombo.addItem('in_use')

        self.licenseTabShowCombo.currentIndexChanged.connect(self.filterLicenseFeature)

        licenseTabEmpty2Label = QLabel('')

        licenseTabLicenseFeatureLabel = QLabel('License Feature', self.licenseTabFrame0)
        licenseTabLicenseFeatureLabel.setStyleSheet("font-weight: bold;")
        self.licenseTabLicenseFeatureLine = QLineEdit()

        licenseTabFilterButton = QPushButton('Filter', self.licenseTabFrame0)
        licenseTabFilterButton.clicked.connect(self.filterLicenseFeature)

        # self.licenseTabFrame0 - Grid
        licenseTabFrame0Grid = QGridLayout()

        licenseTabFrame0Grid.addWidget(licenseTabLicenseServerLabel, 0, 0)
        licenseTabFrame0Grid.addWidget(self.licenseTabLicenseServerCombo, 0, 1)
        licenseTabFrame0Grid.addWidget(licenseTabEmpty1Label, 0, 2)
        licenseTabFrame0Grid.addWidget(licenseTabShowLabel, 0, 3)
        licenseTabFrame0Grid.addWidget(self.licenseTabShowCombo, 0, 4)
        licenseTabFrame0Grid.addWidget(licenseTabEmpty2Label, 0, 5)
        licenseTabFrame0Grid.addWidget(licenseTabLicenseFeatureLabel, 0, 6)
        licenseTabFrame0Grid.addWidget(self.licenseTabLicenseFeatureLine, 0, 7)
        licenseTabFrame0Grid.addWidget(licenseTabFilterButton, 0, 8)

        licenseTabFrame0Grid.setColumnStretch(0, 1)
        licenseTabFrame0Grid.setColumnStretch(1, 2)
        licenseTabFrame0Grid.setColumnStretch(2, 1)
        licenseTabFrame0Grid.setColumnStretch(3, 1)
        licenseTabFrame0Grid.setColumnStretch(4, 2)
        licenseTabFrame0Grid.setColumnStretch(5, 1)
        licenseTabFrame0Grid.setColumnStretch(6, 1)
        licenseTabFrame0Grid.setColumnStretch(7, 3)
        licenseTabFrame0Grid.setColumnStretch(8, 1)

        self.licenseTabFrame0.setLayout(licenseTabFrame0Grid)

    def setLicenseTabLicenseServerCombo(self):
        self.licenseTabLicenseServerCombo.clear()

        licenseServerList = list(self.licenseDic.keys())
        licenseServerList.insert(0, 'ALL')

        for licenseServer in licenseServerList:
            self.licenseTabLicenseServerCombo.addItem(licenseServer)

    def filterLicenseFeature(self):
        # Get license information.
        print('* Loading license information, please wait a moment ...')
        self.licenseDic = license_common.getLicenseInfo()

        # Get all license feature list from self.licenseDic.
        allLicenseFeatureList = []
        
        for (licenseServer, licenseServerDic) in self.licenseDic.items():
            if 'feature' in licenseServerDic:
                for licenseFeature in licenseServerDic['feature']:
                    if licenseFeature not in allLicenseFeatureList:
                        allLicenseFeatureList.append(licenseFeature)

        expectedLicenseFeatureList = []
        specifiedLicenseFeatureList = self.licenseTabLicenseFeatureLine.text().strip().split()
 
        if not specifiedLicenseFeatureList:
            expectedLicenseFeatureList = allLicenseFeatureList
        else:    
            # Get real expected license feature list.
            expectedLicenseFeatureAbsoluteList = []
            expectedLicenseFeatureRelativeList = []

            for expectedLicenseFeature in specifiedLicenseFeatureList:
                if expectedLicenseFeature in allLicenseFeatureList:
                    expectedLicenseFeatureAbsoluteList.append(expectedLicenseFeature)
                else:
                    for licenseFeature in allLicenseFeatureList:
                        if re.search(expectedLicenseFeature, licenseFeature):
                            expectedLicenseFeatureRelativeList.append(licenseFeature)

            expectedLicenseFeatureList = expectedLicenseFeatureAbsoluteList + expectedLicenseFeatureRelativeList

        if expectedLicenseFeatureList:
            if len(expectedLicenseFeatureList) > 5:
                print('* Filter license features "' + str(' '.join(expectedLicenseFeatureList[0:4])) + '" ...')
            else:
                print('* Filter license features "' + str(' '.join(expectedLicenseFeatureList)) + '" ...')
            specifiedLicenseServer = self.licenseTabLicenseServerCombo.currentText().strip()
            specifiedShow = self.licenseTabShowCombo.currentText().strip()
            self.licenseDic = license_common.filterLicenseFeature(self.licenseDic, features=expectedLicenseFeatureList, servers=[specifiedLicenseServer,], mode=specifiedShow)

        # Update self.licenseTabFeatureTable and self.licenseTabExpiresTable.
        self.genLicenseTabFeatureTable()
        self.genLicenseTabExpiresTable()

    def genLicenseTabFeatureTable(self, update=False):
        # Get license information.
        if update:
            print('* Loading license information, please wait a moment ...')
            self.licenseDic = license_common.getLicenseInfo()

        self.licenseTabFeatureTable.setShowGrid(True)
        self.licenseTabFeatureTable.setColumnCount(0)
        self.licenseTabFeatureTable.setColumnCount(4)
        self.licenseTabFeatureTable.setHorizontalHeaderLabels(['License Server', 'Feature', 'Issued', 'In_use'])

        self.licenseTabFeatureTable.setColumnWidth(0, 160)
        self.licenseTabFeatureTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.licenseTabFeatureTable.setColumnWidth(2, 50)
        self.licenseTabFeatureTable.setColumnWidth(3, 50)

        # Get license feature information length.
        licenseFeatureInfoLength = 0

        for (licenseServer, licenseServerDic) in self.licenseDic.items():
            if 'feature' in licenseServerDic:
                for feature in licenseServerDic['feature']:
                    licenseFeatureInfoLength += 1
                    licenseFeatureInfoLength += len(licenseServerDic['feature'][feature]['in_use_info'])

        # Fill self.licenseTabFeatureTable items.
        self.licenseTabFeatureTable.setRowCount(0)
        self.licenseTabFeatureTable.setRowCount(licenseFeatureInfoLength)

        row = -1

        for (licenseServer, licenseServerDic) in self.licenseDic.items():
            if 'feature' in licenseServerDic:
                for feature in licenseServerDic['feature']:
                    row += 1
                    self.licenseTabFeatureTable.setItem(row, 0, QTableWidgetItem(licenseServer))

                    item = QTableWidgetItem(feature)
                    item.setFont(QFont('song', 10, QFont.Bold))
                    item.setForeground(QBrush(Qt.blue))
                    self.licenseTabFeatureTable.setItem(row, 1, item)

                    issued = licenseServerDic['feature'][feature]['issued']
                    self.licenseTabFeatureTable.setItem(row, 2, QTableWidgetItem(issued))

                    in_use = licenseServerDic['feature'][feature]['in_use']
                    self.licenseTabFeatureTable.setItem(row, 3, QTableWidgetItem(in_use))

                    if licenseServerDic['feature'][feature]['in_use_info']:
                        for in_use_info in licenseServerDic['feature'][feature]['in_use_info']:
                            row += 1
                            item = QTableWidgetItem(in_use_info)

                            if license_common.checkLongRuntime(in_use_info):
                                item.setForeground(QBrush(Qt.red))

                            self.licenseTabFeatureTable.setSpan(row, 1, 1, 4)
                            self.licenseTabFeatureTable.setItem(row, 1, item)

    def genLicenseTabExpiresTable(self):
        self.licenseTabExpiresTable.setShowGrid(True)
        self.licenseTabExpiresTable.setColumnCount(0)
        self.licenseTabExpiresTable.setColumnCount(4)
        self.licenseTabExpiresTable.setHorizontalHeaderLabels(['License Server', 'Feature', 'Num', 'Expires'])

        self.licenseTabExpiresTable.setColumnWidth(0, 160)
        self.licenseTabExpiresTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.licenseTabExpiresTable.setColumnWidth(2, 50)
        self.licenseTabExpiresTable.setColumnWidth(3, 100)

        # Get license feature information length.
        licenseExpiresInfoLength = 0

        for (licenseServer, licenseServerDic) in self.licenseDic.items():
            if 'expires' in licenseServerDic:
                for feature in licenseServerDic['expires']:
                    licenseExpiresInfoLength += len(licenseServerDic['expires'][feature])

        # File self.licenseTabExpiresTable items.
        self.licenseTabExpiresTable.setRowCount(0)
        self.licenseTabExpiresTable.setRowCount(licenseExpiresInfoLength)

        row = -1

        for (licenseServer, licenseServerDic) in self.licenseDic.items():
            if 'expires' in licenseServerDic:
                for feature in licenseServerDic['expires']:
                    for expiresDic in licenseServerDic['expires'][feature]:
                        row += 1
                        self.licenseTabExpiresTable.setItem(row, 0, QTableWidgetItem(licenseServer))

                        item = QTableWidgetItem(feature)
                        item.setFont(QFont('song', 10, QFont.Bold))
                        item.setForeground(QBrush(Qt.blue))
                        self.licenseTabExpiresTable.setItem(row, 1, item)

                        license = expiresDic['license']
                        self.licenseTabExpiresTable.setItem(row, 2, QTableWidgetItem(license))

                        expires = expiresDic['expires']
                        item = QTableWidgetItem(expires)
                        expiresMark = license_common.checkExpireDate(expires)
                        if expiresMark == 0:
                            pass
                        elif expiresMark == -1:
                            item.setForeground(QBrush(Qt.gray))
                        else:
                            item.setForeground(QBrush(Qt.red))
                        self.licenseTabExpiresTable.setItem(row, 3, item)
## For license TAB (end) ## 


    def closeEvent(self, QCloseEvent):
        """
        When window close, post-process.
        """
        print('Bye')


class ProcessTracer(QThread):
    """
    Start tool process_tracer.py to trace job process.
    """
    def __init__(self, job):
        super(ProcessTracer, self).__init__()
        self.job = job

    def run(self):
        command = str(str(os.environ['LSFMONITOR_INSTALL_PATH'])) + '/monitor/tools/process_tracer.py -j ' + str(self.job)
        os.system(command)


#################
# Main Function #
#################
def main():
    check_tool()
    (specifiedJob, specifiedUser, specifiedFeature, specifiedTab) = readArgs()
    app = QApplication(sys.argv)
    mw = MainWindow(specifiedJob, specifiedUser, specifiedFeature, specifiedTab)
    mw.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
