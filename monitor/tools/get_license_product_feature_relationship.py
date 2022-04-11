#!EXPECTED_PYTHON
# -*- coding: utf-8 -*-
################################
# File Name   : get_license_product_feature_relationship.py
# Author      : ic_admin
# Created On  : 2021-11-30 17:25:47
# Description : 
################################
import os
import sys
import argparse

if 'LSFMONITOR_INSTALL_PATH' not in os.environ:
    os.environ['LSFMONITOR_INSTALL_PATH'] = 'LSFMONITOR_INSTALL_PATH_STRING'

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import license_common

os.environ['PYTHONUNBUFFERED'] = '1'
CWD = os.getcwd()

def readArgs():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-f', '--licenseFiles',
                        required=True,
                        nargs='+',
                        default=[],
                        help='Required argument, specify license files.')
    parser.add_argument('-v', '--vendors',
                        required=True,
                        nargs='+',
                        default=[],
                        help='Required argument, specify vendor list, must be the same order of licenseFiles.')
    parser.add_argument('-o', '--outputFile',
                        default=str(CWD) + '/product_feature_relationship.yaml',
                        help='Output file, yaml format, default is "./product_feature_relationship.yaml"')

    args = parser.parse_args()

    # Check license file exists or not.
    for licenseFile in args.licenseFiles:
        if not os.path.exists(licenseFile):
            print('*Error*: "' + str(licenseFile) + '": No such license file.')
            sys.exit(1)

    # Check vendor valid or not.
    validVendorList = ['cadence', 'synopsys', 'mentor', 'xilinx']

    for vendor in args.vendors:
        if vendor not in validVendorList:
            print('*Error*: "' + str(vendor) + '": invalid vendor name.')
            sys.exit(1) 

    # Check output directory exists or not.
    args.outputFile = os.path.abspath(args.outputFile)
    outputFileDir = os.path.dirname(args.outputFile)

    if not os.path.exists(outputFileDir):
        print('*Error*: "' + str(outputFileDir) + '": No such output file directory.')
        sys.exit(1)

    return(args.licenseFiles, args.vendors, args.outputFile)

################
# Main Process #
################
def main():
    (licenseFileList, vendorList, outputFile) = readArgs()
    myGetProductFeatureRelationship = license_common.GetProductFeatureRelationship(licenseFileList, vendorList, outputFile)
    myGetProductFeatureRelationship.run()

if __name__ == '__main__':
    main()
