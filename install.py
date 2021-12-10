import os
import sys
import stat

CWD = os.getcwd()

def checkPythonVersion():
    """
    Check python version.
    python3 is required, anaconda3 is better.
    """
    print('>>> Check python version.')
    
    currentPython = sys.version_info[:2]
    requiredPython = (3, 5)
    
    if currentPython < requiredPython:
        sys.stderr.write("""
==========================
Unsupported Python version
==========================
This version of lsfMonitor requires Python {}.{} (or greater version), 
but you're trying to install it on Python {}.{}.
""".format(*(requiredPython + currentPython)))
        sys.exit(1)
    else:
        print('    Required python version : ' + str(requiredPython))
        print('    Current  python version : ' + str(currentPython))

def genBmonitor():
    """
    Generate script <LSFMONITOR_INSTALL_PATH>/monitor/bin/bmonitor.
    """
    bmonitor = str(CWD) + '/monitor/bin/bmonitor'

    print('')
    print('>>> Generate script "' + str(bmonitor) + '".')

    try:
        with open(bmonitor, 'w') as BM:
             pythonPath = os.path.dirname(os.path.abspath(sys.executable))

             BM.write("""#!/bin/bash

# Set python3 path.
export PATH=""" + str(pythonPath) + """:$PATH

# Set lsfMonitor install path.
export LSFMONITOR_INSTALL_PATH=""" + str(CWD) + """

# Execute bmonitor.py.
python3 $LSFMONITOR_INSTALL_PATH/monitor/bin/bmonitor.py $@
""")

        os.chmod(bmonitor, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
    except Exception as err:
        print('*Error*: Failed on generating script "' + str(bmonitor) + '": ' + str(err))
        sys.exit(1)

def genBsample():
    """
    Generate script <LSFMONITOR_INSTALL_PATH>/monitor/bin/bsample.
    """
    bsample = str(CWD) + '/monitor/bin/bsample'

    print('')
    print('>>> Generate script "' + str(bsample) + '".')

    try:
        with open(bsample, 'w') as BS:
             pythonPath = os.path.dirname(os.path.abspath(sys.executable))

             BS.write("""#!/bin/bash

# Set python3 path.
export PATH=""" + str(pythonPath) + """:$PATH

# Set lsfMonitor install path.
export LSFMONITOR_INSTALL_PATH=""" + str(CWD) + """

# Execute bsample.py.
python3 $LSFMONITOR_INSTALL_PATH/monitor/bin/bsample.py $@
""")

        os.chmod(bsample, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
    except Exception as err:
        print('*Error*: Failed on generating script "' + str(bsample) + '": ' + str(err))
        sys.exit(1)

def genConfigFile():
    """
    Generate config file <LSFMONITOR_INSTALL_PATH>/monitor/conf/config.py.
    """
    configFile = str(CWD) + '/monitor/conf/config.py'

    print('')
    print('>>> Generate config file "' + str(configFile) + '".')
    
    if os.path.exists(configFile):
        print('*Warning*: config file "' + str(configFile) + '" already exists, will not update it.')
    else:
        try:
            dbPath = str(CWD) + '/db'

            with open(configFile, 'w') as CF:
                CF.write('''# Specify the database directory.
dbPath = "''' + str(dbPath) + '''"

# Specify lmstat path, example "/*/*/bin".
lmstatPath = ""

# Specify lmstat bsub command, example "bsub -q normal -Is".
lmstatBsubCommand = ""''')

            os.chmod(configFile, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
            os.chmod(dbPath, stat.S_IRWXU+stat.S_IRWXG+stat.S_IRWXO)
        except Exception as error:
            print('*Error*: Failed on opening config file "' + str(configFile) + '" for write: ' + str(error))
            sys.exit(1)

def updateTools():
    """
    Update string "LSFMONITOR_INSTALL_PATH_STRING" into environment variable LSFMONITOR_INSTALL_PATH.
    """
    expectedPython = os.path.abspath(sys.executable)
    toolList = [str(CWD) + '/monitor/tools/seedb.py', str(CWD) + '/monitor/tools/process_tracer.py']

    for tool in toolList:
        with open(tool, 'r+') as TOOL:
            lines = TOOL.read()
            TOOL.seek(0)
            lines = lines.replace('EXPECTED_PYTHON', expectedPython)
            lines = lines.replace('LSFMONITOR_INSTALL_PATH_STRING', CWD)
            TOOL.write(lines)

################
# Main Process #
################
def main():
    checkPythonVersion()
    genBmonitor()
    genBsample()
    genConfigFile()
    updateTools()

    print('')
    print('Done, Please enjoy it.')

if __name__ == '__main__':
    main()
