#!EXPECTED_PYTHON
# -*- coding: utf-8 -*-
################################
# File Name   : check_issue_reason.py
# Author      : liyanqing.1987
# Created On  : 2022-04-13 00:00:00
# Description : 
################################
import os
import re
import sys
import argparse

from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QFrame, QGridLayout, QLabel, QLineEdit, QComboBox, QPushButton, QTextEdit
from PyQt5.QtCore import QThread

if 'LSFMONITOR_INSTALL_PATH' not in os.environ:
    os.environ['LSFMONITOR_INSTALL_PATH'] = 'LSFMONITOR_INSTALL_PATH_STRING'

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import lsf_common
from common import pyqt5_common

os.environ['PYTHONUNBUFFERED'] = '1'

def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-j', '--job',
                        default='',
                        help='Specify jobid.')
    parser.add_argument('-i', '--issue',
                        default='PEND',
                        choices=['PEND', 'SLOW', 'FAIL'],
                        help='Specify issue type, default is "PEND".')

    args = parser.parse_args()

    return(args.job, args.issue)


class MainWindow(QMainWindow):
    """
    Main window of check_issue_reason.
    """
    def __init__(self, job, issue):
        super().__init__()
        self.job = job
        self.issue = issue

        self.initUI()
        self.processArgs()

    def initUI(self):
        """
        Main process, draw the main graphic frame.
        """
        # Define main Tab widget
        self.mainTab = QTabWidget(self)
        self.setCentralWidget(self.mainTab)

        # Defaint sub-frames
        self.selectFrame = QFrame(self.mainTab)
        self.infoFrame = QFrame(self.mainTab)

        self.selectFrame.setFrameShadow(QFrame.Raised)
        self.selectFrame.setFrameShape(QFrame.Box)
        self.infoFrame.setFrameShadow(QFrame.Raised)
        self.infoFrame.setFrameShape(QFrame.Box)

        # Grid
        mainGrid = QGridLayout()

        mainGrid.addWidget(self.selectFrame, 0, 0)
        mainGrid.addWidget(self.infoFrame, 1, 0)

        mainGrid.setRowStretch(0, 1)
        mainGrid.setRowStretch(1, 20)

        self.mainTab.setLayout(mainGrid)

        # Generate mainTable
        self.genSelectFrame()
        self.genInfoFrame()

        # Show main window
        self.setWindowTitle('Check Issue Reason')
        self.resize(600, 300)
        pyqt5_common.centerWindow(self)

    def processArgs(self):
        """
        Process argument if user specified jobid.
        """
        if self.job:
            self.jobLine.setText(self.job)
            self.checkIssue()    

    def genSelectFrame(self):
        # self.selectFrame
        jobLabel = QLabel(self.selectFrame)
        jobLabel.setText('Job')

        self.jobLine = QLineEdit()

        issueLabel = QLabel(self.selectFrame)
        issueLabel.setText('Issue')

        self.issueCombo = QComboBox(self.selectFrame)
        self.setIssueCombo()

        checkButton = QPushButton('Check', self.selectFrame)
        checkButton.clicked.connect(self.checkIssue)

        emptyLabel = QLabel(self.selectFrame)

        # self.selectFrame - Grid
        selectFrameGrid = QGridLayout()

        selectFrameGrid.addWidget(jobLabel, 0, 0)
        selectFrameGrid.addWidget(self.jobLine, 0, 1)
        selectFrameGrid.addWidget(emptyLabel, 0, 2)
        selectFrameGrid.addWidget(issueLabel, 0, 3)
        selectFrameGrid.addWidget(self.issueCombo, 0, 4)
        selectFrameGrid.addWidget(emptyLabel, 0, 5)
        selectFrameGrid.addWidget(checkButton, 0, 6)

        selectFrameGrid.setColumnStretch(0, 1)
        selectFrameGrid.setColumnStretch(1, 3)
        selectFrameGrid.setColumnStretch(2, 3)
        selectFrameGrid.setColumnStretch(3, 1)
        selectFrameGrid.setColumnStretch(4, 3)
        selectFrameGrid.setColumnStretch(5, 3)
        selectFrameGrid.setColumnStretch(6, 1)

        self.selectFrame.setLayout(selectFrameGrid)

    def genInfoFrame(self):
        # self.infoFrame
        self.infoText = QTextEdit(self.infoFrame)

        # self.infoFrame - Grid
        infoFrameGrid = QGridLayout()
        infoFrameGrid.addWidget(self.infoText, 0, 0)
        self.infoFrame.setLayout(infoFrameGrid)

    def setIssueCombo(self):
        self.issueCombo.addItem(self.issue)

        issueList = ['PEND', 'SLOW', 'FAIL']

        for issue in issueList:
            if issue != self.issue:
                self.issueCombo.addItem(issue)

    def checkIssue(self):
        self.infoText.clear()
        job = self.jobLine.text().strip()

        if not job:
            self.infoText.append('<font color="#FF0000">*Error*: Please specify "Job" first.</font>')
        else:
            command = 'bjobs -UF ' + str(job)
            jobDic = lsf_common.getBjobsUfInfo(command)

            if job not in jobDic:
                self.infoText.append('<font color="#FF0000">*Error*: "' + str(job) + '": No such job.</font>')
            else:
                if self.issueCombo.currentText().strip() == 'PEND':
                    self.checkPendIssue(job, jobDic)
                elif self.issueCombo.currentText().strip() == 'SLOW':
                    self.checkSlowIssue(job, jobDic)
                elif self.issueCombo.currentText().strip() == 'FAIL':
                    self.checkFailIssue(job, jobDic)

    def checkPendIssue(self, job, jobDic):
        self.infoText.clear()

        if jobDic[job]['status'] != 'PEND':
            self.infoText.append('<font color="#FF0000">*Error*: Job status is "' + str(jobDic[job]['status']) + '"!</font>')
        else:
            for (i, line) in enumerate(jobDic[job]['pendingReasons']):
                self.infoText.append('[Reason ' + str(i) + '] : ' + str(line))
 
                if re.search('New job is waiting for scheduling',  line):
                    self.infoText.append('                    任务分发中, 请耐心等待')
                elif re.search('Not enough job slot',  line):
                    self.infoText.append('                    cpu需求不能满足, 请耐心等待队列资源.')

                    if jobDic[job]['processorsRequested']:
                        self.infoText.append('                    cpu : ' + str(jobDic[job]['processorsRequested']) + ' slot(s)')
                elif re.search('Job slot limit reached',  line):
                    self.infoText.append('                    cpu需求不能满足, 请耐心等待队列资源.')

                    if jobDic[job]['processorsRequested']:
                        self.infoText.append('                    cpu : ' + str(jobDic[job]['processorsRequested']) + ' slot(s)')
                elif re.search('Not enough processors to meet the job\'s spanning requirement',  line):
                    self.infoText.append('                    cpu需求不能满足, 请耐心等待队列资源.')

                    if jobDic[job]['processorsRequested']:
                        self.infoText.append('                    cpu : ' + str(jobDic[job]['processorsRequested']) + ' slot(s)')
                elif re.search('Job requirements for reserving resource \(mem\) not satisfied',  line):
                    self.infoText.append('                    mem需求不能满足, 请耐心等待队列资源, 如有必要申请专有队列.')

                    if jobDic[job]['requestedResources']:
                        self.infoText.append('                    mem : ' + str(jobDic[job]['requestedResources']))
                elif re.search('Job\'s requirements for resource reservation not satisfied \(Resource: mem\)',  line):
                    self.infoText.append('                    mem需求不能满足, 请耐心等待队列资源, 如有必要申请专有队列.')

                    if jobDic[job]['requestedResources']:
                        self.infoText.append('                    mem : ' + str(jobDic[job]['requestedResources']))
                elif re.search('There are no suitable hosts for the job',  line):
                    self.infoText.append('                    资源申请不能满足, 请检查资源申请条件是否过于苛刻.')

                    if jobDic[job]['processorsRequested']:
                        self.infoText.append('                    cpu : ' + str(jobDic[job]['processorsRequested']) + ' slot(s)')

                    if jobDic[job]['requestedResources']:
                        self.infoText.append('                    mem : ' + str(jobDic[job]['requestedResources']))
                elif re.search('User has reached the per-user job slot limit of the queue',  line):
                    self.infoText.append('                    queue限制, 请耐心等待队列资源.')

            self.infoText.append('')
            self.infoText.append('备注 : job PEND原因浮动变化, 仅了解PEND的核心瓶颈所在即可.')

    def checkSlowIssue(self, job, jobDic):
        if jobDic[job]['status'] != 'RUN':
            self.infoText.append('<font color="#FF0000">*Error*: Job status is "' + str(jobDic[job]['status']) + '"!</font>')
        else:
            self.infoText.clear()
            self.infoText.append('Step 1: Check "STAT" on Process Tracer.')
            self.infoText.append('            STAT "R" means "RUN".')
            self.infoText.append('            STAT "S" means "SLEEP".')
            self.infoText.append('Step 2: If there is "R" STAT on any process.')
            self.infoText.append('            Process status is ok, Please check EDA tool setting.')
            self.infoText.append('Step 3: If all STAT are "S".')
            self.infoText.append('            Find key command, click command pid on Process Tracer.')
            self.infoText.append('            Check what EDA tool is doing with strace terminal.')

            self.myProcessTracer = ProcessTracer(job)
            self.myProcessTracer.start()

    def checkFailIssue(self, job, jobDic):
        self.infoText.clear()
        self.infoText.append('Status : ' + str(jobDic[job]['status']))

        if jobDic[job]['status'] == 'DONE':
            self.infoText.append('Job done sucessfully.')
            self.infoText.append('The issue should be from your command, please check your command log.')
        elif jobDic[job]['status'] == 'EXIT':
            if jobDic[job]['exitCode']:
                self.infoText.append('Job exit code is "' + str(jobDic[job]['exitCode']) + '".')

                if int(jobDic[job]['exitCode']) <= 127:
                    self.infoText.append('The issue should be from your command, please check your command log.')
                else:
                    self.infoText.append('The job should be kill by system or LSF, please contact LSF administrator for further debug.')
            if jobDic[job]['termOwner']:
                self.infoText.append('Job TERM_OWNER info just like below:')
                self.infoText.append('    "' + str(jobDic[job]['termOwner']) + '"')
                self.infoText.append('Please contact LSF administrator for further debug.')
        else:
            self.infoText.append('<font color="#FF0000">*Error*: Job is not finished!</font>')


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


################
# Main Process #
################
def main():
    (job, issue) = read_args()
    app = QApplication(sys.argv)
    mw = MainWindow(job, issue)
    mw.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
