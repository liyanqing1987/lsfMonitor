# -*- coding: utf-8 -*-
#
# common_db_path.py
#
# Author: liyanqing.1987
# Created: 2026-09-03
# Description: Resolve a subsystem's db_path with fallback to the top-level
#               config.py.
#
#   Priority: config_<name>.db_path (if non-empty) > config.db_path/<name>
#   > $LSFMONITOR_INSTALL_PATH/db/<name> (last resort when neither is set).

import os


def resolve_db_path(subsystem_config, subsystem_name):
    """Return the db_path for a subsystem.

    Priority:
      1. subsystem_config.db_path (if non-empty) — per-subsystem override wins.
      2. top-level config.py db_path + '/' + subsystem_name.
      3. $LSFMONITOR_INSTALL_PATH/db/<subsystem> (fallback when config.py absent).
    """
    sub_db = getattr(subsystem_config, 'db_path', '') or ''

    if sub_db:
        return sub_db

    from common import common_config

    default_config = common_config.load_default_config()
    base = getattr(default_config, 'db_path', '') if default_config else ''

    if not base:
        base = os.path.join(os.environ.get('LSFMONITOR_INSTALL_PATH', '.'), 'db')

    return os.path.join(base, subsystem_name)


def ensure_db_root(db_path):
    """Ensure db_path root exists and is 0o1777 (shared write + sticky).

    os.makedirs applies mode only to the leaf dir; intermediate dirs follow
    umask and end up 0o755, locking out other sampler accounts. This chmod-s
    the root so any sampler can create its subsystem subdir. Re-chmods
    existing dirs too (repairs 0o755 from cp -r / umask). Never raises.
    """
    try:
        os.makedirs(db_path, exist_ok=True)
        os.chmod(db_path, 0o1777)
    except PermissionError:
        pass
    except Exception as warning:
        print(f'*Warning* ensure_db_root("{db_path}"): {warning}')
