import os
import re
import sys
import time
import datetime

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from conf import config

os.environ['PYTHONUNBUFFERED'] = '1'


def getLicenseInfo():
    """
    Get EDA liecnse feature usage and expires information.
    Save it into a dict.
    """
    licenseDic = {}
    licenseServer = ''
    licenseFiles = ''
    lmgrdStatus = ''
    feature = ''
    expiresMark = False

    if config.lmstatPath:
        lmstat = str(config.lmstatPath) + '/lmstat'

        if os.path.exists(lmstat):
            command = str(lmstat) + ' -a -i'
    else:
        command = 'lmstat -a -i'

    if config.lmstatBsubCommand:
        command = str(config.lmstatBsubCommand) + ' "' + str(command) + '"'

    (returnCode, stdout, stderr) = common.run_command(command)

    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if re.match('^License server status: (\S+)\s*$', line):
            myMatch = re.match('^License server status: (\S+)\s*$', line)
            licenseServer = myMatch.group(1)
            licenseDic.setdefault(licenseServer, {})
            expiresMark = False
        elif re.match('^\s*License file\(s\) on (\S+): (\S+):\s*$', line):
            myMatch = re.match('^\s*License file\(s\) on (\S+): (\S+):\s*$', line)
            licenseHost = re.sub('.*@', '', licenseServer)

            if myMatch.group(1) != licenseHost:
                print('*Error*: Not find "License file(s) ..." information for license server "' + str(licenseServer) + '".')
                sys.exit(1)
        
            licenseFiles = myMatch.group(2) 
            licenseFileList = licenseFiles.split(':')
            licenseDic[licenseServer].setdefault('licenseFiles', licenseFileList)
            licenseDic[licenseServer].setdefault('status', 'down')
        elif re.search(': UP ', line):
            lmgrdStatus = 'up'
            licenseDic[licenseServer].setdefault('status', lmgrdStatus)
        elif re.match('^Users of (\S+):  \(Total of ([0-9]+) license(s?) issued;  Total of ([0-9]+) license(s?) in use\)\s*$', line):
            myMatch = re.match('^Users of (\S+):  \(Total of ([0-9]+) license(s?) issued;  Total of ([0-9]+) license(s?) in use\)\s*$', line)
            feature = myMatch.group(1)
            issuedNum = myMatch.group(2)
            inUseNum = myMatch.group(4)

            licenseDic[licenseServer].setdefault('feature', {})
            licenseDic[licenseServer]['feature'].setdefault(feature, {})
            licenseDic[licenseServer]['feature'][feature].setdefault('issued', issuedNum)
            licenseDic[licenseServer]['feature'][feature].setdefault('in_use', inUseNum)
            licenseDic[licenseServer]['feature'][feature].setdefault('in_use_info', [])
        elif re.search(', start ', line):
            licenseDic[licenseServer]['feature'][feature]['in_use_info'].append(line)
        elif re.match('^Feature .* Expires\s*$', line):
            expiresMark = True
            licenseDic[licenseServer].setdefault('expires', {})
        elif expiresMark and re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})\s*$', line):
            myMatch = re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})\s*$', line)
            feature = myMatch.group(1)
            version = myMatch.group(2)
            license = myMatch.group(3)
            vendor  = myMatch.group(4)
            expires = myMatch.group(5)
            licenseDic[licenseServer]['expires'].setdefault(feature, [])
            licenseDic[licenseServer]['expires'][feature].append({'version': version, 'license': license, 'vendor': vendor, 'expires': expires})
   
    return(licenseDic)

def filterLicenseFeature(licenseDic, features=[], servers=[], mode='ALL'):
    """
    Only keep specified features on licenseDic.
    """
    newLicenseDic = {}

    for (licenseServer, licenseServerDic) in licenseDic.items():
        if (not servers) or ('ALL' in servers) or (licenseServer in servers):
            licenseFiles = ' '.join(licenseServerDic['licenseFiles'])
            licenseStatus = licenseServerDic['status']

            # Get filtered feature information. (filtered by feature and in_use mode)
            newFeatureList = []
            newFeatureDic = {}
            maxFeatureLength = 0

            if 'feature' in licenseServerDic.keys():
                for (feature, featureDic) in licenseServerDic['feature'].items():
                    if (not features) or (feature in features):
                        issuedNum = featureDic['issued']
                        inUseNum = featureDic['in_use']
                        inUseInfo = featureDic['in_use_info']

                        if (mode == 'ALL') or ((mode == 'in_use') and (inUseNum != '0')):
                            newFeatureList.append(feature)
                            newFeatureDic.setdefault(feature, {'issued': issuedNum, 'in_use': inUseNum, 'in_use_info': inUseInfo})

                            if len(feature) > maxFeatureLength:
                                maxFeatureLength = len(feature)

            # Save newLicenseDic.
            if newFeatureDic:
                newLicenseDic.setdefault(licenseServer, {})
                newLicenseDic[licenseServer].setdefault('licenseFiles', licenseFiles)
                newLicenseDic[licenseServer].setdefault('status', licenseStatus)

                if newFeatureDic:
                    newLicenseDic[licenseServer].setdefault('feature', newFeatureDic)
                    newLicenseDic[licenseServer].setdefault('maxFeatureLength', maxFeatureLength)

                # Save expires information.
                if 'expires' in licenseServerDic.keys():
                    for (feature, featureDicList) in licenseServerDic['expires'].items():
                        if feature in newFeatureList:
                            for featureDic in featureDicList:
                                version = featureDic['version']
                                license = featureDic['license']
                                vendor  = featureDic['vendor']
                                expires = featureDic['expires']
                           
                                newLicenseDic[licenseServer].setdefault('expires', {})
                                newLicenseDic[licenseServer]['expires'].setdefault(feature, [])
                                newLicenseDic[licenseServer]['expires'][feature].append({'version': version, 'license': license, 'vendor': vendor, 'expires': expires})
              
    return(newLicenseDic)

def checkLongRuntime(line):
    """
    Runtime is more than 3 days, return True.
    Runtime is less than 3 days, return False.
    """
    if re.match('^.* start (.+?)\s*$', line):
        myMatch = re.match('^.* start (.+?)(,.+)?$', line)
        startTime = myMatch.group(1)
        currentYear = datetime.date.today().year
        startTime = str(currentYear) + ' ' + str(startTime)
        startSeconds = int(time.mktime(time.strptime(startTime, '%Y %a %m/%d %H:%M')))
        currentSeconds = int(time.time())

        if startSeconds > currentSeconds:
            currentYear = int(datetime.date.today().year) - 1
            startSeconds = int(time.mktime(time.strptime(startTime, '%Y %a %m/%d %H:%M')))

        if currentSeconds - startSeconds >= 259200:
            return(True)

    return(False)

def checkExpireDate(expireDate):
    """
    Expired, return -1.
    Expire in 14 days, return day number.
    Expire later than 14 days, return 0.
    """
    expireSeconds = int(time.mktime(time.strptime(expireDate, '%d-%b-%Y')))
    expireSeconds = expireSeconds + 86400
    currentSeconds = int(time.time())

    if expireSeconds < currentSeconds:
        return(-1)
    elif expireSeconds - currentSeconds <= 1209600:
        return((expireSeconds - currentSeconds)//86400 + 1)
    else:
        return(0)
