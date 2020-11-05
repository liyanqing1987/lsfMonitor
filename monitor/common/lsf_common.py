import os
import re
import sys
import collections
import subprocess

sys.path.append('MONITORPATH')
from common import common

def getCommandDict(command):
    """
    Collect (common) LSF command info into a dict.
    It only works with the Title-Item type informations.
    """
    myDic = collections.OrderedDict()
    keyList = []
    lines = os.popen(command).readlines()

    for i in range(len(lines)):
        line = lines[i].strip()

        # Some speciall preprocess.
        if re.search('lsload', command):
            line = re.sub('\*', ' ', line)

        if i == 0:
            keyList = line.split()
            for key in keyList:
                myDic[key] = []
        else:
            commandInfo = line.split()
            if len(commandInfo) < len(keyList):
                common.printWarning('*Warning* (getCommandDict) : For command "' + str(command) + '", below info line is incomplate/unexpected.')
                common.printWarning('           ' + str(line))

            for j in range(len(keyList)):
                key = keyList[j]
                if j < len(commandInfo):
                    value = commandInfo[j]
                else:
                    value = ''
                myDic[key].append(value)

    return(myDic)

def getBjobsInfo(command='bjobs -u all -r -w'):
    """
    Get bjobs info with command 'bjobs'.
    ====
    JOBID   USER      STAT  QUEUE      FROM_HOST   EXEC_HOST   JOB_NAME            SUBMIT_TIME
    101     liyanqing RUN   normal     cmp01       2*cmp01     Tesf for lsfMonitor Oct 26 17:43
    ====
    """
    bjobsDic = getCommandDict(command)
    return(bjobsDic)

def getBqueuesInfo(command='bqueues -w'):
    """
    Get bqueues info with command 'bqueues'.
    ====
    QUEUE_NAME      PRIO STATUS          MAX JL/U JL/P JL/H NJOBS  PEND   RUN  SUSP  RSV PJOBS 
    normal           30  Open:Active       -    -    -    -     2     0     2     0    0     0
    ====
    """
    bqueuesDic = getCommandDict(command)
    return(bqueuesDic)

def getBhostsInfo(command='bhosts -w'):
    """
    Get bhosts info with command 'bhosts'.
    ====
    HOST_NAME          STATUS          JL/U    MAX  NJOBS    RUN  SSUSP  USUSP    RSV 
    cmp01              ok              -       4    2        2    0      0        0
    ====
    """
    bhostsDic = getCommandDict(command)
    return(bhostsDic)

def getLshostsInfo(command='lshosts -w'):
    """
    Get lshosts info with command 'lshosts'.
    ====
    HOST_NAME                     type       model           cpuf     ncpus maxmem maxswp server RESOURCES
    cmp01                         X86_64     Intel_Platinum  15.0     4     1.7G   1.9G   Yes    (mg)
    ====
    """
    lshostsDic = getCommandDict(command)
    return(lshostsDic)

def getLsloadInfo(command='lsload -w'):
    """
    Get lsload info with command 'lsload'.
    ====
    ST_NAME               status  r15s   r1m  r15m   ut    pg    ls    it   tmp    swp   mem
    cmp01                 ok      0.7    0.3  0.2    5%    0.0   1     0    7391M  1.9G  931M
    ====
    """
    lsloadDic = getCommandDict(command)

    return(lsloadDic)

def getBusersInfo(command='busers all'):
    """
    Get lsload info with command 'busers'.
    ====
    USER/GROUP          JL/P    MAX  NJOBS   PEND    RUN  SSUSP  USUSP    RSV 
    liyanqing           -       -    2       0       2    0      0        0
    ====
    """
    busersDic = getCommandDict(command)
    return(busersDic)

def getBjobsUfInfo(command='bjobs -u all -r -UF'):
    """
    Parse job info which are from command 'bjobs -u all -r -UF'.
    ====
    Job <101>, Job Name <Tesf for lsfMonitor>, User <liyanqing>, Project <lsf_test>, Status <RUN>, Queue <normal>, Command <sleep 12345>, Share group charged </liyanqing>
    Mon Oct 26 17:43:07: Submitted from host <cmp01>, CWD <$HOME>, 2 Task(s), Requested Resources <span[hosts=1] rusage[mem=123]>;
    Mon Oct 26 17:43:07: Started 2 Task(s) on Host(s) <2*cmp01>, Allocated 2 Slot(s) on Host(s) <2*cmp01>, Execution Home </home/liyanqing>, Execution CWD </home/liyanqing>;
    Mon Oct 26 17:46:17: Resource usage collected. MEM: 2 Mbytes; SWAP: 238 Mbytes; NTHREAD: 4; PGID: 10643; PIDs: 10643 10644 10646;
    
    
     MEMORY USAGE:
     MAX MEM: 2 Mbytes;  AVG MEM: 2 Mbytes
    
     SCHEDULING PARAMETERS:
               r15s   r1m  r15m   ut      pg    io   ls    it    tmp    swp    mem
     loadSched   -     -     -     -       -     -    -     -     -      -      -  
     loadStop    -     -     -     -       -     -    -     -     -      -      -  
    
     RESOURCE REQUIREMENT DETAILS:
     Combined: select[type == local] order[r15s:pg] rusage[mem=123.00] span[hosts=1]
     Effective: select[type == local] order[r15s:pg] rusage[mem=123.00] span[hosts=1]
    ====
    """
    jobCompileDic = {
                     'jobCompile'                 : re.compile('.*Job <([0-9]+(\[[0-9]+\])?)>.*'),
                     'jobNameCompile'             : re.compile('.*Job Name <([^>]+)>.*'),
                     'userCompile'                : re.compile('.*User <([^>]+)>.*'),
                     'projectCompile'             : re.compile('.*Project <([^>]+)>.*'),
                     'statusCompile'              : re.compile('.*Status <([A-Z]+)>*'),
                     'queueCompile'               : re.compile('.*Queue <([^>]+)>.*'),
                     'commandCompile'             : re.compile('.*Command <(.+)>\s*$'),
                     'submittedFromCompile'       : re.compile('.*Submitted from host <([^>]+)>.*'),
                     'submittedTimeCompile'       : re.compile('(.*): Submitted from host.*'),
                     'cwdCompile'                 : re.compile('.*CWD <([^>]+)>.*'),
                     'requestedResourcesCompile'  : re.compile('.*Requested Resources <(.+)>;.*'),
                     'spanHostsCompile'           : re.compile('.*Requested Resources <.*span\[hosts=([1-9][0-9]*).*>.*'),
                     'rusageMemCompile'           : re.compile('.*Requested Resources <.*rusage\[mem=([1-9][0-9]*).*>.*'),
                     'startedOnCompile'           : re.compile('(.*): (\[\d+\] )?[sS]tarted \d+ Task\(s\) on Host\(s\) (.+?), Allocated (\d+) Slot\(s\) on Host\(s\).*'),
                     'finishedTimeCompile'        : re.compile('(.*): (Done successfully|Completed <exit>).*'),
                     'cpuTimeCompile'             : re.compile('.*The CPU time used is ([1-9][0-9]*) seconds.*'),
                     'memCompile'                 : re.compile('.* MEM: (\d+(\.\d+)?) ([KMGT]bytes).*'),
                    }

    myDic = collections.OrderedDict()
    job = ''

    p = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = p.stdout.readlines()

    for line in lines:
        line = str(line.strip(), 'utf-8')

        if re.match('Job <' + str(job) + '> is not found', line):
            continue
        else:
            if jobCompileDic['jobCompile'].match(line):
                myMatch = jobCompileDic['jobCompile'].match(line)
                job = myMatch.group(1)

                # Initialization for myDic[job].
                myDic[job] = collections.OrderedDict()
                myDic[job]['jobId'] = job
                myDic[job]['jobName'] = ''
                myDic[job]['user'] = ''
                myDic[job]['project'] = ''
                myDic[job]['status'] = ''
                myDic[job]['queue'] = ''
                myDic[job]['command'] = ''
                myDic[job]['submittedFrom'] = ''
                myDic[job]['submittedTime'] = ''
                myDic[job]['cwd'] = ''
                myDic[job]['processorsRequested'] = ''
                myDic[job]['requestedResources'] = ''
                myDic[job]['spanHosts'] = ''
                myDic[job]['rusageMem'] = ''
                myDic[job]['startedOn'] = ''
                myDic[job]['startedTime'] = ''
                myDic[job]['finishedTime'] = ''
                myDic[job]['cpuTime'] = ''
                myDic[job]['mem'] = ''

            if job != '':
                if 'jobInfo' in myDic[job].keys():
                    myDic[job]['jobInfo'] = str(myDic[job]['jobInfo']) + '\n' + str(line)
                else:
                    myDic[job]['jobInfo'] = line

                if jobCompileDic['jobNameCompile'].match(line):
                    myMatch = jobCompileDic['jobNameCompile'].match(line)
                    myDic[job]['jobName'] = myMatch.group(1)
                if jobCompileDic['userCompile'].match(line):
                    myMatch = jobCompileDic['userCompile'].match(line)
                    myDic[job]['user'] = myMatch.group(1)
                if jobCompileDic['projectCompile'].match(line):
                    myMatch = jobCompileDic['projectCompile'].match(line)
                    myDic[job]['project'] = myMatch.group(1)
                if jobCompileDic['statusCompile'].match(line):
                    myMatch = jobCompileDic['statusCompile'].match(line)
                    myDic[job]['status'] = myMatch.group(1)
                if jobCompileDic['queueCompile'].match(line):
                    myMatch = jobCompileDic['queueCompile'].match(line)
                    myDic[job]['queue'] = myMatch.group(1)
                if jobCompileDic['commandCompile'].match(line):
                    myMatch = jobCompileDic['commandCompile'].match(line)
                    myDic[job]['command'] = myMatch.group(1)
                if jobCompileDic['submittedFromCompile'].match(line):
                    myMatch = jobCompileDic['submittedFromCompile'].match(line)
                    myDic[job]['submittedFrom'] = myMatch.group(1)
                if jobCompileDic['submittedTimeCompile'].match(line):
                    myMatch = jobCompileDic['submittedTimeCompile'].match(line)
                    myDic[job]['submittedTime'] = myMatch.group(1)
                if jobCompileDic['cwdCompile'].match(line):
                    myMatch = jobCompileDic['cwdCompile'].match(line)
                    myDic[job]['cwd'] = myMatch.group(1)
                if jobCompileDic['requestedResourcesCompile'].match(line):
                    myMatch = jobCompileDic['requestedResourcesCompile'].match(line)
                    myDic[job]['requestedResources'] = myMatch.group(1)
                if jobCompileDic['spanHostsCompile'].match(line):
                    myMatch = jobCompileDic['spanHostsCompile'].match(line)
                    myDic[job]['spanHosts'] = myMatch.group(1)
                if jobCompileDic['rusageMemCompile'].match(line):
                    myMatch = jobCompileDic['rusageMemCompile'].match(line)
                    myDic[job]['rusageMem'] = myMatch.group(1)
                if jobCompileDic['startedOnCompile'].match(line):
                    myMatch = jobCompileDic['startedOnCompile'].match(line)
                    myDic[job]['startedTime'] = myMatch.group(1)
                    startedHost = myMatch.group(3)
                    startedHost = re.sub('<', '', startedHost)
                    startedHost = re.sub('>', '', startedHost)
                    startedHost = re.sub('\d+\*', '', startedHost)
                    myDic[job]['startedOn'] = startedHost
                    myDic[job]['processorsRequested'] = myMatch.group(4)
                if jobCompileDic['finishedTimeCompile'].match(line):
                    myMatch = jobCompileDic['finishedTimeCompile'].match(line)
                    myDic[job]['finishedTime'] = myMatch.group(1)
                if jobCompileDic['cpuTimeCompile'].match(line):
                    myMatch = jobCompileDic['cpuTimeCompile'].match(line)
                    myDic[job]['cpuTime'] = myMatch.group(1)
                if jobCompileDic['memCompile'].match(line):
                    myMatch = jobCompileDic['memCompile'].match(line)
                    myDic[job]['mem'] = myMatch.group(1)
                    unit = myMatch.group(3)
                    if unit == 'Kbytes':
                        myDic[job]['mem'] = float(myDic[job]['mem'])/1024
                    elif unit == 'Gbytes':
                        myDic[job]['mem'] = float(myDic[job]['mem'])*1024
                    elif unit == 'Tbytes':
                        myDic[job]['mem'] = float(myDic[job]['mem'])*1024*1024

    return(myDic)
 
def getHostList():
    """
    Get all of the hosts.
    """
    bhostsDic = getBhostsInfo()
    hostList = bhostsDic['HOST_NAME']
    return(hostList)

def getQueueList():
    """
    Get all of the queues.
    """
    bqueuesDic = getBqueuesInfo()
    queueList = bqueuesDic['QUEUE_NAME']
    return(queueList)

def getHostGroupMembers(hostGroupName):
    """
    Get host group members with bmgroup.
    ====
    [yanqing.li@nxnode03 lsfMonitor]$ bmgroup pd
    GROUP_NAME    HOSTS
    pd           dm006 dm007 dm010 dm009 dm002 dm003 dm005 
    ====
    """
    hostList = []
    lines = os.popen('bmgroup -r ' + str(hostGroupName)).readlines()

    for line in lines:
        if re.search('No such user/host group', line):
            break
        elif re.match('^' + str(hostGroupName) + ' .*$', line):
            myList = line.split()
            hostList = myList[1:]

    return(hostList)

def getUserGroupMembers(userGroupName):
    """
    Get user group members with bugroup.
    ====
    [yanqing.li@nxnode03 lsfMonitor]$ bugroup pd
    GROUP_NAME    USERS
    pd           yanqing.li san.zhang si.li
    ====
    """
    userList = []
    lines = os.popen('bugroup -r ' + str(userGroupName)).readlines()

    for line in lines:
        if re.match('^' + str(userGroupName) + ' .*$', line):
            myList = line.split()
            userList = myList[1:]

    return(userList)

def getQueueHostInfo():
    """
    Get hosts on (specified) queues.
    """
    queueHostDic = {}
    queueCompile = re.compile('^QUEUE:\s*(\S+)\s*$')
    hostsCompile= re.compile('^HOSTS:\s*(.*?)\s*$')
    queue = ''

    lines = os.popen('bqueues -l').readlines()
    for line in lines:
        line = line.strip()
        if queueCompile.match(line):
            myMatch = queueCompile.match(line)
            queue = myMatch.group(1)
            queueHostDic[queue] = []
        if hostsCompile.match(line):
            myMatch = hostsCompile.match(line)
            hostsString = myMatch.group(1)
            if re.search('all hosts used by the OpenLava system', hostsString):
                common.printWarning('*Warning* (getQueueHostInfo) : queue "' + str(queue) + '" is not well configured, all of the hosts are on the same queue.')
                queueHostDic[queue] = getHostList()
            else:
                queueHostDic.setdefault(queue, [])
                hostsList = hostsString.split()
                for hosts in hostsList:
                    if re.match('.+/', hosts):
                        hostGroupName = re.sub('/$', '', hosts)
                        hostList = getHostGroupMembers(hostGroupName)
                        if len(hostList) > 0:
                            queueHostDic[queue].extend(hostList)
                    elif re.match('^(.+)\+\d+$', hosts):
                        myMatch = re.match('^(.+)\+\d+$', hosts)
                        hostGroupName = myMatch.group(1)
                        hostList = getHostGroupMembers(hostGroupName)
                        if len(hostList) == 0:
                            queueHostDic[queue].append(hosts)
                        else:
                            queueHostDic[queue].extend(hostList)
                    else:
                        queueHostDic[queue].append(hosts)

    return(queueHostDic)

def getHostQueueInfo():
    """
    Get queues which (specified) host belongs to.
    """
    hostQueueDic = {}

    queueHostDic = getQueueHostInfo()
    queueList = list(queueHostDic.keys())

    for queue in queueList:
        hostList = queueHostDic[queue]
        for host in hostList:
            if host in hostQueueDic.keys():
               hostQueueDic[host].append(queue)
            else:
                hostQueueDic[host] = [queue, ]

    return(hostQueueDic)
