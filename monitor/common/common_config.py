# -*- coding: utf-8 -*-

import os
import sys
import importlib.util


def load_config():
    """
    Load config with merge behavior:
    1. Always load base config from monitor/conf/config.py
    2. If ~/.lsfMonitor/conf/config.py exists, overlay user settings on top
    3. User values take priority; base values serve as defaults
    """
    install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
    base_config_file = os.path.join(install_path, 'monitor', 'conf', 'config.py')
    local_config_dir = os.path.join(os.environ['HOME'], '.lsfMonitor', 'conf')
    local_config_file = os.path.join(local_config_dir, 'config.py')

    config = _load_module_from_file('config', base_config_file)

    if os.path.exists(local_config_file):
        local_config = _load_module_from_file('_local_config', local_config_file)
        _merge_config(config, local_config)

    sys.modules['config'] = config
    return config


def reload_config_for_cluster(cluster):
    """
    Reload config with cluster-specific config file.
    Merge order: base config.py < base config_{cluster}.py < user config.py < user config_{cluster}.py
    """
    if not cluster:
        return sys.modules.get('config')

    install_path = os.environ.get('LSFMONITOR_INSTALL_PATH', '')
    base_config_file = os.path.join(install_path, 'monitor', 'conf', 'config.py')
    local_config_dir = os.path.join(os.environ['HOME'], '.lsfMonitor', 'conf')
    local_config_file = os.path.join(local_config_dir, 'config.py')

    cluster_config_name = f'config_{cluster}'
    cluster_config_base = os.path.join(install_path, 'monitor', 'conf', f'{cluster_config_name}.py')
    cluster_config_local = os.path.join(local_config_dir, f'{cluster_config_name}.py')

    config = _load_module_from_file('config', base_config_file)

    if os.path.exists(cluster_config_base):
        cluster_base = _load_module_from_file('_cluster_base', cluster_config_base)
        _merge_config(config, cluster_base)

    if os.path.exists(local_config_file):
        local_config = _load_module_from_file('_local_config', local_config_file)
        _merge_config(config, local_config)

    if os.path.exists(cluster_config_local):
        cluster_local = _load_module_from_file('_cluster_local', cluster_config_local)
        _merge_config(config, cluster_local)

    sys.modules['config'] = config
    return config


def _load_module_from_file(module_name, file_path):
    """Load a Python file as a module."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _merge_config(base, overlay):
    """Merge overlay module attributes into base, skipping private/dunder names."""
    for attr in dir(overlay):
        if not attr.startswith('_'):
            setattr(base, attr, getattr(overlay, attr))
