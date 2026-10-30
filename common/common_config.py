# -*- coding: utf-8 -*-
################################
# File Name   : common_config.py
# Author      : liyanqing.1987
# Created On : 2026-08-24
# Description : Unified config loader for the top-level default and the four
#               sidebar panels (lsf / license / run / ai). Every config has a
#               uniform naming rule and an optional cluster-specific override:
#
#                 config.py            <-> config_<cluster>.py
#                 config_<name>.py     <-> config_<name>_<cluster>.py
#
#               Each is overlaid by the user's file of the same name under
#               ~/.lsfMonitor/config/. Loaded into sys.modules via importlib
#               so panel configs never collide with each other.
################################

import os
import sys
import importlib.util


def _config_paths(name, cluster):
    """Return the four config file paths (base, cluster base, user, user cluster).

    name '' is the top-level config: config.py / config_<cluster>.py.
    A panel config: config_<name>.py / config_<name>_<cluster>.py.
    """
    install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
    local_config_dir = os.path.join(os.path.expanduser('~'), '.lsfMonitor', 'config')

    if name:
        base_name = f'config_{name}.py'
        cluster_name = f'config_{name}_{cluster}.py'
    else:
        base_name = 'config.py'
        cluster_name = f'config_{cluster}.py'

    return (
        os.path.join(install_path, 'config', base_name),
        os.path.join(install_path, 'config', cluster_name),
        os.path.join(local_config_dir, base_name),
        os.path.join(local_config_dir, cluster_name),
    )


def load_config(name='', cluster=''):
    """Load the top-level (name='') or one panel's config, with an optional
    cluster-specific override.

    Merge order (last wins):
      1. <install>/config/config[_<name>].py
      2. <install>/config/config[_<name>]_<cluster>.py
      3. ~/.lsfMonitor/config/config[_<name>].py
      4. ~/.lsfMonitor/config/config[_<name>]_<cluster>.py

    A missing base raises for panels (name != '') and returns None for the
    top-level config (e.g. not yet installed).
    """
    base_file, cluster_base_file, local_file, cluster_local_file = _config_paths(name, cluster)
    module_name = f'config_{name}' if name else 'config'

    if os.path.isfile(base_file):
        config = _load_module_from_file(module_name, base_file)
        overlay_files = (cluster_base_file, local_file, cluster_local_file)
    elif not name and os.path.isfile(cluster_base_file):
        # Top-level cluster file can stand alone (e.g. config.py not installed
        # yet but the site ships a config_<cluster>.py).
        config = _load_module_from_file(module_name, cluster_base_file)
        overlay_files = (local_file, cluster_local_file)
    elif name:
        raise FileNotFoundError(f'Base config file not found: {base_file}')
    else:
        return None

    for tag, path in enumerate(overlay_files, start=1):
        if os.path.exists(path):
            overlay = _load_module_from_file(f'_{tag}_{module_name}', path)
            _merge_config(config, overlay)

    sys.modules[module_name] = config

    return config


def reload_config_for_cluster(cluster, name='lsf'):
    """Backward-compatible wrapper of load_config(name, cluster)."""
    return load_config(name, cluster)


def load_default_config(cluster=''):
    """Load the top-level config.py (project defaults), with an optional
    cluster-specific override (config_<cluster>.py).
    """
    return load_config('', cluster)


def _load_module_from_file(module_name, file_path):
    """Load a Python file as a module."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)

    if spec is None or spec.loader is None:
        raise FileNotFoundError(f'Cannot load module from "{file_path}": spec or loader is None.')

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def _merge_config(base, overlay):
    """Merge overlay module attributes into base, skipping private/dunder names."""
    for attr in vars(overlay):
        if not attr.startswith('_'):
            setattr(base, attr, getattr(overlay, attr))
