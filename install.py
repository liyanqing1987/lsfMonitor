import os
import re
import sys
import stat


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


## Generate .env.bash file for bmonitor and bsample.
print('>>> Generate env file ".env.bash".')

defaultPython = sys.executable
defaultPythonPath = os.path.dirname(defaultPython)
lsfmonitorPath = os.getcwd()
envBash = str(lsfmonitorPath) + '/monitor/bin/.env.bash'

print('    Env file : ' + str(envBash))

if os.path.exists(envBash):
    print('*Warning*: env file "' + str(envBash) + '" already exists, will not update it.')
else:
    try:
        with open(envBash, 'w') as CF:
            print('        DEFAULT_PYTHON_PATH = ' + str(defaultPythonPath) + '')
            CF.write('export PATH=' + str(defaultPythonPath) + ':$PATH\n')
            print('        LSFMONITOR_PATH     = ' + str(lsfmonitorPath) + '')
            CF.write('export LSFMONITOR_PATH=' + str(lsfmonitorPath) + '\n')

        os.chmod(envBash, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
    except Exception as error:
        print('*Error*: Failed on opening env file "' + str(envBash) + '" for write: ' + str(error))
        sys.exit(1)


## Generate config file.
print('>>> Generate config file.')

dbPath = str(lsfmonitorPath) + '/db'
configFile = str(lsfmonitorPath) + '/monitor/conf/config.py'

print('    Config file : ' + str(configFile))

if os.path.exists(configFile):
    print('*Warning*: config file "' + str(configFile) + '" already exists, will not update it.')
else:
    try:
        with open(configFile, 'w') as CF:
            print('        dbPath = "' + str(dbPath) + '"')
            CF.write('# Specify the database directory.\n')
            CF.write('dbPath = "' + str(dbPath) + '"\n')

        os.chmod(configFile, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
        os.chmod(dbPath, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
    except Exception as error:
        print('*Error*: Failed on opening config file "' + str(configFile) + '" for write: ' + str(error))
        sys.exit(1)


## Replace strings "PYTHON_PATH" and "LSFMONITOR_PATH_SETTING" into the real monitor directory path on all of the python files.
print('>>> Updating python path and lsfMonitor install path on tools.')

pythonFiles = ['monitor/tools/seedb.py',]
pythonPathEscaping = re.sub('/', '\/', defaultPython)
lsfmonitorPathEscaping = re.sub('/', '\/', lsfmonitorPath)

for pythonFile in pythonFiles:
    print('    ' + str(pythonFile))
    print('        PYTHON_PATH  >>> ' + str(defaultPython))
    command = "sed -i 's/PYTHON_PATH/" + str(pythonPathEscaping) + "/g' " + str(pythonFile)
    os.system(command)
    print('        LSFMONITOR_PATH_SETTING>>> ' + str(lsfmonitorPath))
    command = "sed -i 's/LSFMONITOR_PATH_SETTING/" + str(lsfmonitorPathEscaping) + "/g' " + str(pythonFile)
    os.system(command)


print('\nDone, Please enjoy it.')
