#!/ic/software/tools/python3/3.8.8/bin/python3
# -*- coding: utf-8 -*-
################################
# File Name   : report_gui.py
# Author      : zhangjingwen.silvia
# Created On  : 2025-01-20 16:51:36
# Description :
################################
import os
import argparse

os.environ['PYTHONUNBUFFERED'] = '1'


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-e', '--example',
                        default='',
                        help='This is an example argument.')

    args = parser.parse_args()

    return args.example


################
# Main Process #
################
def main():
    (example) = read_args()
    print(example)


if __name__ == '__main__':
    main()
