# -*- coding: utf-8 -*-
################################
# File Name   : version.py
# Author      : liyanqing.1987
# Created On : 2026-08-25
# Description : Single source of truth for the application version, release
#               date and the current user. Imported by panels instead of each
#               defining its own VERSION/RELEASE_DATE/USER constant.
################################

import getpass

VERSION = 'V3.0'
RELEASE_DATE = '2026.09.15'
USER = getpass.getuser()
