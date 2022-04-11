import os
import re
import sys
import time
import datetime
import yaml

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
        elif expiresMark and re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(permanent\(no expiration date\)|[0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})\s*$', line):
            myMatch = re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(permanent\(no expiration date\)|[0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})\s*$', line)
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
                    if feature in features:
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

def checkLongRuntime(line, secondThreshold=259200):
    """
    Runtime is more than secondThreshold (default is 3 days), return True.
    Runtime is less than secondThreshold (default is 3 days), return False.
    """
    if re.match('^.* start ([A-Z][a-z]+ \d+/\d+ \d+:\d+)\s*(,.+)?$', line):
        myMatch = re.match('^.* start ([A-Z][a-z]+ \d+/\d+ \d+:\d+)\s*(,.+)?$', line)
        startTime = myMatch.group(1)
        currentYear = datetime.date.today().year
        startTime = str(currentYear) + ' ' + str(startTime)
        startSeconds = int(time.mktime(time.strptime(startTime, '%Y %a %m/%d %H:%M')))
        currentSeconds = int(time.time())

        if startSeconds > currentSeconds:
            currentYear = int(datetime.date.today().year) - 1
            startSeconds = int(time.mktime(time.strptime(startTime, '%Y %a %m/%d %H:%M')))

        if currentSeconds - startSeconds >= secondThreshold:
            return(True)

    return(False)

def checkExpireDate(expireDate, secondThreshold=1209600):
    """
    Expired, return -1.
    Expire in secondThreshold (default is 14 days), return day number.
    Expire later than secondThreshold (default is 14 days), return 0.
    """
    if re.search('permanent', expireDate):
        return(0)
    else:
        expireSeconds = int(time.mktime(time.strptime(expireDate, '%d-%b-%Y')))
        expireSeconds = expireSeconds + 86400
        currentSeconds = int(time.time())

        if expireSeconds < currentSeconds:
            return(-1)
        elif expireSeconds - currentSeconds <= secondThreshold:
            return((expireSeconds - currentSeconds)//86400 + 1)
        else:
            return(0)


class GetProductFeatureRelationship():
    def __init__(self, licenseFileList, vendorList, outputFile='./product_feature_relationship.yaml'):
        """
        licenseFileList : license files.
        vendorList : same order with licenseFileList, vendor must be "cadence/synopsys/mentor".
        outputFile : must be yaml format.
        """
        self.licenseFileList = licenseFileList
        self.vendorList = vendorList
        self.outputFile = outputFile
        self.licenseDic = {}

        # Check licenseFile exist or not.
        for (i, licenseFile) in enumerate(self.licenseFileList):
            if os.path.exists(licenseFile):
                self.licenseFileList[i] = os.path.realpath(licenseFile)
            else:
                print('*Error*: "' + str(licenseFile) + '": No such license file.')
                sys.exit(1)

        # Check vendor setting.
        validVendorList = ['cadence', 'synopsys', 'mentor', 'xilinx']

        for vendor in self.vendorList:
            if vendor not in validVendorList:
                print('*Error*: "' + str(vendor) + '": Invalid vendor.')
                sys.exit(1)

        # Check self.licenseFileList and self.vendorList length.
        if len(self.licenseFileList) != len(self.vendorList):
            print('*Error*: length of licenseFileList is different with vendorList.')
            sys.exit(1)

        # Check output file.
        outputDir = os.path.dirname(self.outputFile)

        if os.path.exists(outputDir):
            self.outputFile = os.path.abspath(self.outputFile)
        else:
            print('*Error*: "' + str(self.outputFile) + '": No such output directory.')
            sys.exit(1)

    def parseCadenceLicenseFile(self, licenseFile):
        """
        Parse cadence license file, and save product-feature relationship into self.licenseDic.
        """
        self.licenseDic.setdefault('cadence', {})
        productName = ''
        feature = ''

        with open(licenseFile, 'r') as LF:
            for line in LF.readlines():
                if re.match('^\s*#\s*Product\s+Name\s*:\s*(.+?)\s*$', line):
                    myMatch = re.match('^\s*#\s*Product\s+Name\s*:\s*(.+?)\s*$', line)
                    productName = myMatch.group(1)
                    self.licenseDic['cadence'].setdefault(productName, [])
                elif re.match('^\s*#\s*Feature\s*:\s*(.+?)\s+.*$', line):
                    myMatch = re.match('^\s*#\s*Feature\s*:\s*(.+?)\s+.*$', line)
                    feature = myMatch.group(1)

                    if productName:
                        if feature not in self.licenseDic['cadence'][productName]:
                            self.licenseDic['cadence'][productName].append(feature)
                    else:
                        print('*Warning*: Not find product name for feature "' + str(feature) + '".')

    def parseSynopsysLicenseFile(self, licenseFile):
        """
        Parse synopsys license file, and save product-feature relationship into self.licenseDic.
        """
        self.licenseDic.setdefault('synopsys', {})
        productDic = {}
        productId = ''
        productName = ''
        feature = ''
        productMark = 0

        with open(licenseFile, 'r') as LF:
            for line in LF.readlines():
                if (productMark == 0) and re.match('^\s*#\s*Product\s*:.*$', line):
                    productMark = 1
                elif (productMark == 1) and re.match('^\s*#\s*----.*$', line):
                    productMark = 2
                elif (productMark == 2) and re.match('^\s*#\s*(.+?):\S+\s+(.+?)\s+0000.*$', line):
                    myMatch = re.match('^\s*#\s*(.+?):\S+\s+(.+?)\s+0000.*$', line)
                    productId = myMatch.group(1)
                    productName = myMatch.group(2)
                    self.licenseDic['synopsys'].setdefault(productName, [])
                    productDic.setdefault(productId, productName)
                elif (productMark == 2) and re.match('^\s*#\s*----.*$', line):
                    productMark = 0
                elif (productMark == 0) and re.match('^\s*(INCREMENT|FEATURE)\s+(\S+)\s+.*$', line):
                    myMatch = re.match('^\s*(INCREMENT|FEATURE)\s+(\S+)\s+.*$', line)
                    feature = myMatch.group(2)
                elif (productMark == 0) and re.match('^.*SN=RK:(.+?):.*$', line):
                    myMatch = re.match('^.*SN=RK:(.+?):.*$', line)
                    currentProductId = myMatch.group(1)

                    if feature:
                        if currentProductId in productDic:
                            if feature not in self.licenseDic['synopsys'][productDic[currentProductId]]:
                                self.licenseDic['synopsys'][productDic[currentProductId]].append(feature)
                        else:
                            print('*Warning*: Not find product name for feature "' + str(feature) + '".')

    def parseMentorLicenseFile(self, licenseFile):
        """
        Parse mentor license file, and save product-feature relationship into self.licenseDic.
        """
        self.licenseDic.setdefault('mentor', {})
        productName = ''
        feature = ''

        with open(licenseFile, 'r', encoding='ISO-8859-1') as LF:
            for line in LF.readlines():
                if re.match(r'^\s*#\s*(\d+)\s*(.+?)\s+\d+\s*$', line):
                    myMatch = re.match(r'^\s*#\s*(\d+)\s*(.+?)\s+\d+\s*$', line)
                    productName = myMatch.group(2)
                    self.licenseDic['mentor'].setdefault(productName, [])
                elif re.match(r'^\s*#\s*(\S+)\s*(20\S+)\s*(\S+)\s*(\S+)\s*\d+\s*$', line):
                    myMatch = re.match(r'^\s*#\s*(\S+)\s*(20\S+)\s*(\S+)\s*(\S+)\s*\d+\s*$', line)
                    feature = myMatch.group(1)

                    if productName:
                        if feature not in self.licenseDic['mentor'][productName]:
                            self.licenseDic['mentor'][productName].append(feature)
                    else:
                        print('*Warning*: Not find product name for feature "' + str(feature) + '".')

    def parseXilinxLicenseFile(self, licenseFile):
        """
        Parse xilinx license file, and save product-feature relationship into self.licenseDic.
        """
        self.licenseDic.setdefault('xilinx', {})
        lineString = ''
        productName = ''
        feature = ''
        featureList = []
        packageFeatureList = []

        with open(licenseFile, 'r') as LF:
            for line in LF.readlines():
                line = line.strip()
                line = re.sub('\\\s*$', '', line)

                if re.match('^\s*#.*$', line) or re.match('^\s*$', line):
                    if lineString:
                        if re.match('^\s*PACKAGE\s+(.+?)\s+.*COMPONENTS="(.+?)".*$', lineString):
                            myMatch = re.match('^\s*PACKAGE\s+(.+?)\s+.*COMPONENTS="(.+?)".*$', lineString)
                            productName = myMatch.group(1)
                            featureString = myMatch.group(2)
                            featureList = featureString.split()
                            packageFeatureList.extend(featureList)
                            self.licenseDic['xilinx'].setdefault(productName, featureList)
                        elif re.match('^\s*(FEATURE|INCREMENT)\s+(.+?)\s+.*$', lineString):
                            myMatch = re.match('^\s*(FEATURE|INCREMENT)\s+(.+?)\s+.*$', lineString)
                            feature = myMatch.group(2)
                            featureList.append(feature) 

                    lineString = ''
                elif re.match('^FEATURE\s+.*$', line) or re.match('^INCREMENT\s+.*$', line) or re.match('^PACKAGE\s+.*$', line):
                    lineString = line
                else:
                    if lineString:
                        lineString = str(lineString) + str(line)

            for feature in featureList:
                if feature not in packageFeatureList:
                    if feature in self.licenseDic['xilinx'].keys():
                        self.licenseDic['xilinx'][feature].append(feature)
                    else:
                        print('*Warning*: Not find product name for feature "' + str(feature) + '".')

    def parseLicenseFile(self):
        """
        Parse license file to get product-feature relationship.
        """
        for (i, licenseFile) in enumerate(self.licenseFileList):
            vendor = self.vendorList[i] 

            print('>>> Parse ' + str(vendor) + ' license file "' + str(licenseFile) + '".')

            if vendor == 'cadence':
                self.parseCadenceLicenseFile(licenseFile)
            elif vendor == 'synopsys':
                self.parseSynopsysLicenseFile(licenseFile)
            elif vendor == 'mentor':
                self.parseMentorLicenseFile(licenseFile)
            elif vendor == 'xilinx':
                self.parseXilinxLicenseFile(licenseFile)

        return(self.licenseDic)

    def writeOutputFile(self):
        """
        Write self.outputFile with yaml format.
        """
        print('')
        print('* Write output file "' + str(self.outputFile) + '".')

        with open(self.outputFile, 'w', encoding='utf-8') as OF:
            yaml.dump(self.licenseDic, OF)

    def run(self):
        """
        Main function of class GetProductFeatureRelationship.
        """
        self.parseLicenseFile()
        self.writeOutputFile()
