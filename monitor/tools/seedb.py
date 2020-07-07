#!PYTHONPATH

import os
import re
import sys
import argparse

sys.path.insert(0, 'MONITORPATH')
from conf import config
from common import sqlite3_common

def readArgs():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-d", "--database",
                        required=True,
                        help='Required argument, specify the datebase file.')
    parser.add_argument("-t", "--tables",
                        nargs='+',
                        default=[],
                        help='Specify the tables you want to review, make sure the tables exist.')
    parser.add_argument("-k", "--keys",
                        nargs='+',
                        default=[],
                        help='Specify the table keys you want to review, make sure the table keys exist.')
    parser.add_argument("-n", "--number",
                        type=int,
                        default=0,
                        help='How many lines you want to see.')

    args = parser.parse_args()

    if args.database == '':
        print('*Error*: No database file is specified.')
        sys.exit(1)
    else:
        if not re.match('^/.*$', args.database):
            if os.path.exists(args.database):
                cwd = os.getcwd()
                database = str(cwd) + '/' + str(args.database)
                if os.path.exists(database):
                    args.database = database
            else:
                database = str(config.dbPath) + '/' + str(args.database)
                if os.path.exists(database):
                    args.database = database

        if not os.path.exists(args.database):
            print('*Error*: ' + str(args.database) + ': No such database file.')
            sys.exit(1)

    return(args.database, args.tables, args.keys, args.number)

def getLength(inputList):
    """
    Get the length of the longest item on the input list.
    """
    length = 0

    for item in inputList:
        itemLength = len(item)
        if itemLength > length:
            length = itemLength

    return(length)

def seedb(dbFile, tableList, keyList, number):
    print('DB FILE : ' + str(dbFile))

    if len(tableList) == 0:
        tableList = sqlite3_common.getSqlTableList(dbFile, '')
        print('TABLES :')
        print('========')
        for table in tableList:
            print(table)
        print('========')
    else:
        for table in tableList:
            print('TABLE : ' + str(table))
            print('========')
            dataDic = sqlite3_common.getSqlTableData(dbFile, '', table, keyList, number)
            keyList = list(dataDic.keys())
            if len(keyList) == 0:
                print('*Error*: No valid keyList is specified.')
            else:
                length = getLength(keyList)
                formatString = '%-' + str(length+10) + 's'
                for key in keyList:
                    print(formatString % (key), end='')
                print('')
                for key in keyList:
                    print(formatString % ('----'), end='')
                print('')
                firstKey = keyList[0]
                firstValueList = dataDic[firstKey]
                for i in range(len(firstValueList)):
                    for j in range(len(keyList)):
                        key = keyList[j]
                        valueList = dataDic[key]
                        value = valueList[i]
                        print(formatString % (value), end='')
                    print('')
            print('========')

################
# Main Process #
################
def main():
    (dbFile, tableList, keyList, number) = readArgs()
    seedb(dbFile, tableList, keyList, number)

if __name__ == '__main__':
    main()
