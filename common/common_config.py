# -*- coding: utf-8 -*-
################################
# File Name   : common_config.py
# Author      : liyanqing.1987
# Created On : 2026-08-24
# Description : Unified config loader for the four sidebar panels. Each panel
#               reads its own config_<name>.py from config/ (lsf / license /
#               run / ai), overlaid by ~/.lsfMonitor/config/config_<name>.py.
#               Loaded into sys.modules['config_<name>'] via importlib so panel
#               configs never collide with each other.
################################

import os
import sys
import importlib.util


def load_config(name):
    """
    Load a panel config by name (lsf / license / run / ai).

    Merge behavior:
      1. Load base config from <install>/config/config_<name>.py.
      2. If ~/.lsfMonitor/config/config_<name>.py exists, overlay user settings.
    The resulting module is registered as sys.modules['config_<name>'] and
    returned.
    """
    install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
    base_config_file = os.path.join(install_path, 'config', f'config_{name}.py')
    local_config_dir = os.path.join(os.path.expanduser('~'), '.lsfMonitor', 'config')
    local_config_file = os.path.join(local_config_dir, f'config_{name}.py')

    if not os.path.isfile(base_config_file):
        raise FileNotFoundError(f'Base config file not found: {base_config_file}')

    config = _load_module_from_file(f'config_{name}', base_config_file)

    if os.path.exists(local_config_file):
        local_config = _load_module_from_file(f'_local_config_{name}', local_config_file)
        _merge_config(config, local_config)

    sys.modules[f'config_{name}'] = config

    return config


def reload_config_for_cluster(cluster, name='lsf'):
    """Reload a panel config with a cluster-specific override. Merge order
    (last wins): base config_<name>.py < cluster override (install) < user
    config_<name>.py < user cluster override. If cluster is falsy, reloads
    just base + user. For name='lsf' the legacy config_<cluster>.py form
    (no panel prefix) is honored for backward compat."""
    if not cluster:
        return load_config(name)

    install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
    base_config_file = os.path.join(install_path, 'config', f'config_{name}.py')
    local_config_dir = os.path.join(os.path.expanduser('~'), '.lsfMonitor', 'config')
    local_config_file = os.path.join(local_config_dir, f'config_{name}.py')

    if name == 'lsf':
        cluster_config_base = os.path.join(install_path, 'config', f'config_{cluster}.py')
        cluster_config_local = os.path.join(local_config_dir, f'config_{cluster}.py')
    else:
        cluster_config_base = os.path.join(install_path, 'config', f'config_{name}_{cluster}.py')
        cluster_config_local = os.path.join(local_config_dir, f'config_{name}_{cluster}.py')

    config = _load_module_from_file(f'config_{name}', base_config_file)

    if os.path.exists(cluster_config_base):
        cluster_base = _load_module_from_file(f'_cluster_base_{name}', cluster_config_base)
        _merge_config(config, cluster_base)

    if os.path.exists(local_config_file):
        local_config = _load_module_from_file(f'_local_config_{name}', local_config_file)
        _merge_config(config, local_config)

    if os.path.exists(cluster_config_local):
        cluster_local = _load_module_from_file(f'_cluster_local_{name}', cluster_config_local)
        _merge_config(config, cluster_local)

    sys.modules[f'config_{name}'] = config

    return config


def _load_module_from_file(module_name, file_path):
    """Load a Python file as a module."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)

    if spec is None or spec.loader is None:
        raise FileNotFoundError(f'Cannot load module from "{file_path}": spec or loader is None.')

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def load_default_config():
    """Load the top-level config.py (project defaults).

    Merge behavior:
      1. Load <install>/config/config.py.
      2. If ~/.lsfMonitor/config/config.py exists, overlay user settings.
    Registered as sys.modules['config'] and returned. Returns None if the
    base config.py does not exist (e.g. not yet installed).
    """
    install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
    base_config_file = os.path.join(install_path, 'config', 'config.py')

    if not os.path.isfile(base_config_file):
        return None

    config = _load_module_from_file('config', base_config_file)

    local_config_dir = os.path.join(os.path.expanduser('~'), '.lsfMonitor', 'config')
    local_config_file = os.path.join(local_config_dir, 'config.py')

    if os.path.exists(local_config_file):
        local_config = _load_module_from_file('_local_config', local_config_file)
        _merge_config(config, local_config)

    sys.modules['config'] = config

    return config


def _merge_config(base, overlay):
    """Merge overlay module attributes into base, skipping private/dunder names."""
    for attr in vars(overlay):
        if not attr.startswith('_'):
            setattr(base, attr, getattr(overlay, attr))
