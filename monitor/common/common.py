import re
import subprocess

def printError(message):
    """
    Print error message with red color.
    """
    print('\033[1;31m' + str(message) + '\033[0m')

def printWarning(message):
    """
    Print warning message with yellow color.
    """
    print('\033[1;33m' + str(message) + '\033[0m')

def run_command(command, mystdin=subprocess.PIPE, mystdout=subprocess.PIPE, mystderr=subprocess.PIPE):
    """
    Run system command with subprocess.Popen, get returncode/stdout/stderr.
    """
    SP = subprocess.Popen(command, shell=True, stdin=mystdin, stdout=mystdout, stderr=mystderr)
    (stdout, stderr) = SP.communicate()

    return(SP.returncode, stdout, stderr)

def getJobRangeDic(jobList):
    jobRangeDic = {}

    for job in jobList:
        jobOrg = job
        job = re.sub('\[.*', '', job)
        jobHead = (int(int(job)/10000))*10000
        jobTail = jobHead + 9999
        jobRange = str(jobHead)  + '_' + str(jobTail)
        jobRangeDic.setdefault(jobRange, [])
        jobRangeDic[jobRange].append(jobOrg)

    return(jobRangeDic)
