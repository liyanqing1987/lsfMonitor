# -*- coding: utf-8 -*-
################################
# File Name   : bmonitor.py
# Author      : liyanqing.1987
# Created On : 2026-08-25
# Description : lsfMonitor unified GUI launcher (defaults to the LSF panel).
#               Thin wrapper: sets up sys.path and delegates to
#               gui.main_window.main() which focuses the LSF outer tab.
################################

import os
import sys

_INSTALL_PATH = os.environ.get('LSFMONITOR_INSTALL_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if _INSTALL_PATH not in sys.path:
    sys.path.insert(0, _INSTALL_PATH)

from gui import main_window


def main():
    """Entry point: forward to the unified MainWindow (defaults to LSF panel)."""
    main_window.main()


if __name__ == '__main__':
    main()
