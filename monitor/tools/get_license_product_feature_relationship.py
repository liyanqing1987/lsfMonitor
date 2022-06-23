#!EXPECTED_PYTHON
# -*- coding: utf-8 -*-
################################
# File Name   : get_license_product_feature_relationship.py
# Author      : liyanqing
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


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-f', '--license_files',
                        required=True,
                        nargs='+',
                        default=[],
                        help='Required argument, specify license files.')
    parser.add_argument('-v', '--vendors',
                        required=True,
                        nargs='+',
                        default=[],
                        help='Required argument, specify vendor list, must be the same order of license_files.')
    parser.add_argument('-o', '--output_file',
                        default=str(CWD) + '/product_feature_relationship.yaml',
                        help='Output file, yaml format, default is "./product_feature_relationship.yaml"')

    args = parser.parse_args()

    # Check license file exists or not.
    for license_file in args.license_files:
        if not os.path.exists(license_file):
            print('*Error*: "' + str(license_file) + '": No such license file.')
            sys.exit(1)

    # Check vendor valid or not.
    valid_vendor_list = ['cadence', 'synopsys', 'mentor', 'xilinx']

    for vendor in args.vendors:
        if vendor not in valid_vendor_list:
            print('*Error*: "' + str(vendor) + '": invalid vendor name.')
            sys.exit(1)

    # Check output directory exists or not.
    args.output_file = os.path.abspath(args.output_file)
    output_file_dir = os.path.dirname(args.output_file)

    if not os.path.exists(output_file_dir):
        print('*Error*: "' + str(output_file_dir) + '": No such output file directory.')
        sys.exit(1)

    return(args.license_files, args.vendors, args.output_file)


################
# Main Process #
################
def main():
    (license_file_list, vendor_list, output_file) = read_args()
    my_get_product_feature_relationship = license_common.GetProductFeatureRelationship(license_file_list, vendor_list, output_file)
    my_get_product_feature_relationship.run()


if __name__ == '__main__':
    main()
