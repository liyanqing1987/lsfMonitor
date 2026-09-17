# -*- coding: utf-8 -*-
################################
# File Name   : license_monitor.py
# Author      : liyanqing.1987
# Created On : 2026-08-25
# Description : lsfMonitor unified GUI launcher focused on the License panel.
#               Injects --panel=license and delegates to gui.main_window.main().
################################

import os
import sys

_INSTALL_PATH = os.environ.get('LSFMONITOR_INSTALL_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if _INSTALL_PATH not in sys.path:
    sys.path.insert(0, _INSTALL_PATH)


def main():
    """Entry point: focus the License outer tab in the unified MainWindow."""
    sys.argv = [sys.argv[0]] + ['--panel', 'license'] + sys.argv[1:]

    from gui import main_window

    main_window.main()


if __name__ == '__main__':
    main()
