import os
import re
import sys
import stat
from setuptools import find_packages, setup


## Python 3.5 or greater version is required.
print('>>> Check python version.')

CURRENT_PYTHON = sys.version_info[:2]
REQUIRED_PYTHON = (3, 5)

if CURRENT_PYTHON < REQUIRED_PYTHON:
    sys.stderr.write("""
==========================
Unsupported Python version
==========================
This version of lsfMonitor requires Python {}.{} (or greater version), 
but you're trying to install it on Python {}.{}.
""".format(*(REQUIRED_PYTHON + CURRENT_PYTHON)))
    sys.exit(1)
else:
    print('    Required python version : ' + str(REQUIRED_PYTHON))
    print('    Current  python version : ' + str(CURRENT_PYTHON))


## Generate config file.
print('>>> Generate config file.')

installPath = os.getcwd()
dbPath = str(installPath) + '/db'
tmpPath = str(installPath) + '/tmp'
configFile = str(installPath) + '/monitor/conf/config.py'
print('    Config file : ' + str(configFile))

if os.path.exists(configFile):
    print('*Warning*: config file "' + str(configFile) + '" already exists, will not update it.')
else:
    try:
        with open(configFile, 'w') as CF:
            print('        installPath = "' + str(installPath) + '"')
            CF.write('installPath = "' + str(installPath) + '"\n')
            print('        dbPath      = "' + str(dbPath) + '"')
            CF.write('dbPath      = "' + str(dbPath) + '"\n')
            print('        tmpPath     = "' + str(tmpPath) + '"')
            CF.write('tmpPath     = "' + str(tmpPath) + '"\n')
        os.chmod(configFile, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
        os.chmod(dbPath, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
        os.chmod(tmpPath, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
    except Exception as error:
        print('*Error*: Failed on opening config file "' + str(configFile) + '" for write: ' + str(error))
        sys.exit(1)


## Replace string "PYTHONPATH" into the real python path on all of the python files.
print('>>> Update python path for main executable programs.')

pythonFiles = ['monitor/bin/bmonitor.py', 'monitor/bin/bmonitorGUI.py', 'monitor/bin/bsample.py', 'monitor/tools/seedb.py']
currentPython = sys.executable
currentPythonEscaping = re.sub('/', '\/', currentPython)

for pythonFile in pythonFiles:
    try:
        command = "sed -i 's/PYTHONPATH/" + str(currentPythonEscaping) + "/g' " + str(pythonFile)
        os.system(command)
    except Exception as error:
        print('*Error*: Failed on replacing real python path on file "' + str(pythonFile) + '": ' + str(error))
        sys.exit(1)


## Replace string "MONITORPATH" into the real monitor directory path on all of the python files.
print('>>> Update monitor directory path for main executable programs.')

pythonFiles = ['monitor/bin/bmonitor.py', 'monitor/bin/bmonitorGUI.py', 'monitor/bin/bsample.py', 'monitor/tools/seedb.py', 'monitor/common/lsf_common.py', 'monitor/common/sqlite3_common.py']
monitorPath = str(installPath) + '/monitor'
monitorPathEscaping = re.sub('/', '\/', monitorPath)

for pythonFile in pythonFiles:
    try:
        command = "sed -i 's/MONITORPATH/" + str(monitorPathEscaping) + "/g' " + str(pythonFile)
        os.system(command)
    except Exception as error:
        print('*Error*: Failed on replacing real monitor directory path on file "' + str(pythonFile) + '": ' + str(error))
        sys.exit(1)


print('\nDone, Please enjoy it.')
