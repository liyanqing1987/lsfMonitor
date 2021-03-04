# -*- coding: utf-8 -*-

import os
import sys
import argparse
import datetime
import time
from multiprocessing import Process

sys.path.append(str(os.environ['LSFMONITOR_PATH']) + '/monitor')
from conf import config
from common import common
from common import lsf_common
from common import sqlite3_common

os.environ["PYTHONUNBUFFERED"]="1"

def readArgs():
    """
    Read arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-j", "--job",
                        action="store_true", default=False,
                        help='Sample running job info with command "bjobs -u all -r -UF".')
    parser.add_argument("-q", "--queue",
                        action="store_true", default=False,
                        help='Sample queue info with command "bqueues".')
    parser.add_argument("-H", "--host",
                        action="store_true", default=False,
                        help='Sample host info with command "bhosts".')
    parser.add_argument("-l", "--load",
                        action="store_true", default=False,
                        help='Sample host load info with command "lsload".')
    parser.add_argument("-u", "--user",
                        action="store_true", default=False,
                        help='Sample user info with command "busers".')
    parser.add_argument("-i", "--interval",
                        type=int,
                        default=0,
                        help='Specify the sampling interval, unit is second. Sampling only once by default".')

    args = parser.parse_args()

    if args.interval < 0:
        common.printError('*Error*: interval "' + str(args.interval) + '": Cannot be less than "0".')
        sys.exit(1)

    return(args.job, args.queue, args.host, args.load, args.user, args.interval)


class sampling:
    """
    Sample LSF basic information with LSF bjobs/bqueues/bhosts/lshosts/lsload/busers commands.
    Save the infomation into sqlite3 DB.
    """
    def __init__(self, jobSampling, queueSampling, hostSampling, loadSampling, userSampling, interval):
        self.jobSampling = jobSampling
        self.queueSampling = queueSampling
        self.hostSampling = hostSampling
        self.loadSampling = loadSampling
        self.userSampling = userSampling

        self.interval = interval
        self.dbPath = str(config.dbPath) + '/monitor'
        jobDbPath = str(self.dbPath) + '/job'
    
        if not os.path.exists(jobDbPath):
            try:
                os.system('mkdir -p ' + str(jobDbPath))
            except:
                print('*Error*: Failed on creating sqlite job db directory "' + str(jobDbPath) + '".')
                sys.exit(1)

    def getDateInfo(self):
        self.sampleTime = datetime.datetime.today().strftime('%Y%m%d_%H%M%S')
        self.currentSeconds = int(time.time())

    def sampleJobInfo(self):
        """
        Sample job info, especially the memory usage info.
        """
        self.getDateInfo()

        print('>>> Sampling job info ...')

        command = 'bjobs -u all -r -UF'
        bjobsDic = lsf_common.getBjobsUfInfo(command)
        jobList = list(bjobsDic.keys())
        jobRangeDic = common.getJobRangeDic(jobList)
        jobSqlDic = {}

        keyList = ['sampleTime', 'mem']

        for jobRange in jobRangeDic.keys():
            jobDbFile = str(self.dbPath) + '/job/' + str(jobRange) + '.db'
            (result, jobDbConn) = sqlite3_common.connectDbFile(jobDbFile, mode='read')

            if result == 'passed':
                jobTableList = sqlite3_common.getSqlTableList(jobDbFile, jobDbConn)
            else:
                jobTableList = []

            for job in jobRangeDic[jobRange]:
                jobTableName='job_' + str(job)

                print('    Sampling for job "' + str(job) + '" ...')

                jobSqlDic[job] = {
                                  'drop': False,
                                  'keyString': '',
                                  'valueString': '',
                                 }

                # If job table (with old data) has been on the jobDbFile, drop it.
                if jobTableName in jobTableList:
                    dataDic = sqlite3_common.getSqlTableData(jobDbFile, jobDbConn, jobTableName, ['sampleTime'])

                    if dataDic:
                        if len(dataDic['sampleTime']) > 0:
                            lastSampleTime = dataDic['sampleTime'][-1]
                            lastSeconds = int(time.mktime(datetime.datetime.strptime(str(lastSampleTime), "%Y%m%d_%H%M%S").timetuple()))

                            if self.currentSeconds-lastSeconds > 3600:
                                common.printWarning('    *Warning*: table "' + str(jobTableName) + '" already existed even one hour ago, will drop it.')
                                jobSqlDic[job]['drop'] = True
                                jobTableList.remove(jobTableName)

                # If job table is not on the jobDbFile, create it.
                if jobTableName not in jobTableList:
                    keyString = sqlite3_common.genSqlTableKeyString(keyList)
                    jobSqlDic[job]['keyString'] = keyString

                # Insert sql table value.
                valueList = [self.sampleTime, bjobsDic[job]['mem']]
                valueString = sqlite3_common.genSqlTableValueString(valueList)
                jobSqlDic[job]['valueString'] = valueString

            if result == 'passed':
                jobDbConn.commit()
                jobDbConn.close()

        for jobRange in jobRangeDic.keys():
            jobDbFile = str(self.dbPath) + '/job/' + str(jobRange) + '.db'
            (result, jobDbConn) = sqlite3_common.connectDbFile(jobDbFile, mode='write')

            if result != 'passed':
                return

            for job in jobRangeDic[jobRange]:
                jobTableName='job_' + str(job)

                if jobSqlDic[job]['drop']:
                    sqlite3_common.dropSqlTable(jobDbFile, jobDbConn, jobTableName, commit=False)

                if jobSqlDic[job]['keyString'] != '':
                    sqlite3_common.createSqlTable(jobDbFile, jobDbConn, jobTableName, jobSqlDic[job]['keyString'], commit=False)

                if jobSqlDic[job]['valueString'] != '':
                    sqlite3_common.insertIntoSqlTable(jobDbFile, jobDbConn, jobTableName, jobSqlDic[job]['valueString'], commit=False)

            jobDbConn.commit()
            jobDbConn.close()

        print('    Committing the update to sqlite3 ...')
        print('    Done (' + str(len(jobList)) + ' jobs).')

    def sampleQueueInfo(self):
        """
        Sample queue info and save it into sqlite db.
        """
        self.getDateInfo()
        queueDbFile = str(self.dbPath) + '/queue.db'
        (result, queueDbConn) = sqlite3_common.connectDbFile(queueDbFile, mode='write')

        if result != 'passed':
            return

        print('>>> Sampling queue info into ' + str(queueDbFile) + ' ...')

        queueTableList = sqlite3_common.getSqlTableList(queueDbFile, queueDbConn)
        bqueuesDic = lsf_common.getBqueuesInfo()
        queueList = bqueuesDic['QUEUE_NAME']
        queueList.append('ALL')
        queueSqlDic = {}

        keyList = ['sampleTime', 'NJOBS', 'PEND', 'RUN', 'SUSP']

        for i in range(len(queueList)):
            queue = queueList[i]
            queueSqlDic[queue] = {
                                  'keyString': '',
                                  'valueString': '',
                                 }
            queueTableName = 'queue_' + str(queue)

            print('    Sampling for queue "' + str(queue) + '" ...')

            # Generate sql table.
            if queueTableName not in queueTableList:
                keyString = sqlite3_common.genSqlTableKeyString(keyList)
                queueSqlDic[queue]['keyString'] = keyString

            # Insert sql table value.
            if queue == 'ALL':
                valueList = [self.sampleTime, sum([int(i) for i in bqueuesDic['NJOBS']]), sum([int(i) for i in bqueuesDic['PEND']]), sum([int(i) for i in bqueuesDic['RUN']]), sum([int(i) for i in bqueuesDic['SUSP']])]
            else:
                valueList = [self.sampleTime, bqueuesDic['NJOBS'][i], bqueuesDic['PEND'][i], bqueuesDic['RUN'][i], bqueuesDic['SUSP'][i]]

            valueString = sqlite3_common.genSqlTableValueString(valueList)
            queueSqlDic[queue]['valueString'] = valueString

        for queue in queueList:
            queueTableName = 'queue_' + str(queue)

            if queueSqlDic[queue]['keyString'] != '':
                sqlite3_common.createSqlTable(queueDbFile, queueDbConn, queueTableName, queueSqlDic[queue]['keyString'], commit=False)

            if queueSqlDic[queue]['valueString'] != '':
                sqlite3_common.insertIntoSqlTable(queueDbFile, queueDbConn, queueTableName, queueSqlDic[queue]['valueString'], commit=False)

        print('    Committing the update to sqlite3 ...')

        # Clean up queue database, only keep 10000 items.
        for queue in queueList:
            queueTableName = 'queue_' + str(queue)
            queueTableCount = int(sqlite3_common.getSqlTableCount(queueDbFile, queueDbConn, queueTableName))

            if queueTableCount != 'N/A':
                if int(queueTableCount) > 10000:
                    rowId = 'sampleTime'
                    beginLine = 0
                    endLine = int(queueTableCount) - 10000

                    print('    Deleting database "' + str(queueDbFile) + '" table "' + str(queueTableName) + '" ' + str(beginLine) + '-' + str(endLine) + ' lines to only keep 10000 items.')

                    sqlite3_common.deleteSqlTableRows(queueDbFile, queueDbConn, queueTableName, rowId, beginLine, endLine)

        queueDbConn.commit()
        queueDbConn.close()

    def sampleHostInfo(self):
        """
        Sample host info and save it into sqlite db.
        """
        self.getDateInfo()
        hostDbFile = str(self.dbPath) + '/host.db'
        (result, hostDbConn) = sqlite3_common.connectDbFile(hostDbFile, mode='write')

        if result != 'passed':
            return

        print('>>> Sampling host info into ' + str(hostDbFile) + ' ...')

        hostTableList = sqlite3_common.getSqlTableList(hostDbFile, hostDbConn)
        bhostsDic = lsf_common.getBhostsInfo()
        hostList = bhostsDic['HOST_NAME']
        hostSqlDic = {}

        keyList = ['sampleTime', 'NJOBS', 'RUN', 'SSUSP', 'USUSP']

        for i in range(len(hostList)):
            host = hostList[i]
            hostSqlDic[host] = {
                                'keyString': '',
                                'valueString': '',
                               }
            hostTableName = 'host_' + str(host)

            print('    Sampling for host "' + str(host) + '" ...')

            # Generate sql table.
            if hostTableName not in hostTableList:
                keyString = sqlite3_common.genSqlTableKeyString(keyList)
                hostSqlDic[host]['keyString'] = keyString

            # Insert sql table value.
            valueList = [self.sampleTime, bhostsDic['NJOBS'][i], bhostsDic['RUN'][i], bhostsDic['SSUSP'][i], bhostsDic['USUSP'][i]]
            valueString = sqlite3_common.genSqlTableValueString(valueList)
            hostSqlDic[host]['valueString'] = valueString

        for host in hostList:
            hostTableName = 'host_' + str(host)

            if hostSqlDic[host]['keyString'] != '':
                sqlite3_common.createSqlTable(hostDbFile, hostDbConn, hostTableName, hostSqlDic[host]['keyString'], commit=False)

            if hostSqlDic[host]['valueString'] != '':
                sqlite3_common.insertIntoSqlTable(hostDbFile, hostDbConn, hostTableName, hostSqlDic[host]['valueString'], commit=False)

        print('    Committing the update to sqlite3 ...')

        # Clean up host database, only keep 10000 items.
        for host in hostList:
            hostTableName = 'host_' + str(host)
            hostTableCount = int(sqlite3_common.getSqlTableCount(hostDbFile, hostDbConn, hostTableName))

            if hostTableCount != 'N/A':
                if int(hostTableCount) > 10000:
                    rowId = 'sampleTime'
                    beginLine = 0
                    endLine = int(hostTableCount) - 10000

                    print('    Deleting database "' + str(hostDbFile) + '" table "' + str(hostTableName) + '" ' + str(beginLine) + '-' + str(endLine) + ' lines to only keep 10000 items.')

                    sqlite3_common.deleteSqlTableRows(hostDbFile, hostDbConn, hostTableName, rowId, beginLine, endLine)

        hostDbConn.commit()
        hostDbConn.close()

    def sampleLoadInfo(self):
        """
        Sample host load info and save it into sqlite db.
        """
        self.getDateInfo()
        loadDbFile = str(self.dbPath) + '/load.db'
        (result, loadDbConn) = sqlite3_common.connectDbFile(loadDbFile, mode='write')

        if result != 'passed':
            return

        print('>>> Sampling host load info into ' + str(loadDbFile) + ' ...')

        loadTableList = sqlite3_common.getSqlTableList(loadDbFile, loadDbConn)
        lsloadDic = lsf_common.getLsloadInfo()
        hostList = lsloadDic['HOST_NAME']
        loadSqlDic = {}

        keyList = ['sampleTime', 'ut', 'tmp', 'swp', 'mem']

        for i in range(len(hostList)):
            host = hostList[i]
            loadSqlDic[host] = {
                                'keyString': '',
                                'valueString': '',
                               }
            loadTableName = 'load_' + str(host)

            print('    Sampling for host "' + str(host) + '" ...')

            # Generate sql table.
            if loadTableName not in loadTableList:
                keyString = sqlite3_common.genSqlTableKeyString(keyList)
                loadSqlDic[host]['keyString'] = keyString

            # Insert sql table value.
            valueList = [self.sampleTime, lsloadDic['ut'][i], lsloadDic['tmp'][i], lsloadDic['swp'][i], lsloadDic['mem'][i]]
            valueString = sqlite3_common.genSqlTableValueString(valueList)
            loadSqlDic[host]['valueString'] = valueString

        for host in hostList:
            loadTableName = 'load_' + str(host)

            if loadSqlDic[host]['keyString'] != '':
                sqlite3_common.createSqlTable(loadDbFile, loadDbConn, loadTableName, loadSqlDic[host]['keyString'], commit=False)

            if loadSqlDic[host]['valueString'] != '':
                sqlite3_common.insertIntoSqlTable(loadDbFile, loadDbConn, loadTableName, loadSqlDic[host]['valueString'], commit=False)

        print('    Committing the update to sqlite3 ...')

        # Clean up load database, only keep 10000 items.
        for host in hostList:
            loadTableName = 'load_' + str(host)
            loadTableCount = int(sqlite3_common.getSqlTableCount(loadDbFile, loadDbConn, loadTableName))

            if loadTableCount != 'N/A':
                if int(loadTableCount) > 10000:
                    rowId = 'sampleTime'
                    beginLine = 0
                    endLine = int(loadTableCount) - 10000

                    print('    Deleting database "' + str(loadDbFile) + '" table "' + str(loadTableName) + '" ' + str(beginLine) + '-' + str(endLine) + ' lines to only keep 10000 items.')

                    sqlite3_common.deleteSqlTableRows(loadDbFile, loadDbConn, loadTableName, rowId, beginLine, endLine)

        loadDbConn.commit()
        loadDbConn.close()

    def sampleUserInfo(self):
        """
        Sample user info and save it into sqlite db.
        """
        self.getDateInfo()
        userDbFile = str(self.dbPath) + '/user.db'
        (result, userDbConn) = sqlite3_common.connectDbFile(userDbFile, mode='write')

        if result != 'passed':
            return

        print('>>> Sampling user info into ' + str(userDbFile) + ' ...')

        userTableList = sqlite3_common.getSqlTableList(userDbFile, userDbConn)
        busersDic = lsf_common.getBusersInfo()
        userList = busersDic['USER/GROUP']
        userSqlDic = {}

        keyList = ['sampleTime', 'NJOBS', 'PEND', 'RUN', 'SSUSP', 'USUSP']

        for i in range(len(userList)):
            user = userList[i]
            userSqlDic[user] = {
                                'keyString': '',
                                'valueString': '',
                               }
            userTableName = 'user_' + str(user)

            print('    Sampling for user "' + str(user) + '" ...')

            # Generate sql table.
            if userTableName not in userTableList:
                keyString = sqlite3_common.genSqlTableKeyString(keyList)
                userSqlDic[user]['keyString'] = keyString

            # Insert sql table value.
            valueList = [self.sampleTime, busersDic['NJOBS'][i], busersDic['PEND'][i], busersDic['RUN'][i], busersDic['SSUSP'][i], busersDic['USUSP'][i]]
            valueString = sqlite3_common.genSqlTableValueString(valueList)
            userSqlDic[user]['valueString'] = valueString

        for user in userList:
            userTableName = 'user_' + str(user)

            if userSqlDic[user]['keyString'] != '':
                sqlite3_common.createSqlTable(userDbFile, userDbConn, userTableName, userSqlDic[user]['keyString'], commit=False)

            if userSqlDic[user]['valueString'] != '':
                sqlite3_common.insertIntoSqlTable(userDbFile, userDbConn, userTableName, userSqlDic[user]['valueString'], commit=False)

        print('    Committing the update to sqlite3 ...')

        # Clean up user database, only keep 10000 items.
        for user in userList:
            userTableName = 'user_' + str(user)
            userTableCount = int(sqlite3_common.getSqlTableCount(userDbFile, userDbConn, userTableName))

            if userTableCount != 'N/A':
                if int(userTableCount) > 10000:
                    rowId = 'sampleTime'
                    beginLine = 0
                    endLine = int(userTableCount) - 10000

                    print('    Deleting database "' + str(userDbFile) + '" table "' + str(userTableName) + '" ' + str(beginLine) + '-' + str(endLine) + ' lines to only keep 10000 items.')

                    sqlite3_common.deleteSqlTableRows(userDbFile, userDbConn, userTableName, rowId, beginLine, endLine)

        userDbConn.commit()
        userDbConn.close()

    def sampling(self):
        while True:
            if self.jobSampling:
                p = Process(target=self.sampleJobInfo)
                p.start()

            if self.queueSampling:
                p = Process(target=self.sampleQueueInfo)
                p.start()

            if self.hostSampling:
                p = Process(target=self.sampleHostInfo)
                p.start()

            if self.loadSampling:
                p = Process(target=self.sampleLoadInfo)
                p.start()

            if self.userSampling:
                p = Process(target=self.sampleUserInfo)
                p.start()

            p.join()

            if self.interval == 0:
                 break
            elif self.interval > 0:
                 time.sleep(self.interval)


#################
# Main Function #
#################
def main():
    (job, queue, host, load, user, interval) = readArgs()
    mySampling = sampling(job, queue, host, load, user, interval)
    mySampling.sampling()

if __name__ == '__main__':
    main()
