# -*- coding: utf-8 -*-
"""Tests for the unified cluster-specific config override rule.

Naming rule: config.py <-> config_<cluster>.py (top-level),
config_<name>.py <-> config_<name>_<cluster>.py (panels).
Merge order (last wins): base < install cluster < user < user cluster.
"""

import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALL_ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, INSTALL_ROOT)

from common import common_config  # noqa: E402


class ConfigOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.install_config = os.path.join(self.tmp_dir, 'install', 'config')
        self.user_config = os.path.join(self.tmp_dir, 'user', '.lsfMonitor', 'config')
        os.makedirs(self.install_config)
        os.makedirs(self.user_config)
        os.environ['LSFMONITOR_INSTALL_PATH'] = os.path.join(self.tmp_dir, 'install')
        self.home_backup = os.environ.get('HOME')
        os.environ['HOME'] = os.path.join(self.tmp_dir, 'user')

    def tearDown(self):
        if self.home_backup is not None:
            os.environ['HOME'] = self.home_backup

    def write(self, directory, name, content):
        with open(os.path.join(directory, name), 'w', encoding='utf-8') as handle:
            handle.write(content + '\n')

    def test_panel_cluster_override_naming(self):
        # LSF reads config_lsf_<cluster>.py now (no legacy special case).
        self.write(self.install_config, 'config_lsf.py', 'db_path = "/base"')
        self.write(self.install_config, 'config_lsf_MYCLUSTER.py', 'db_path = "/lsf-cluster"')
        config = common_config.load_config('lsf', 'MYCLUSTER')
        self.assertEqual(config.db_path, '/lsf-cluster')

    def test_top_level_cluster_config(self):
        self.write(self.install_config, 'config.py', 'db_path = "/top"')
        self.write(self.install_config, 'config_MYCLUSTER.py', 'db_path = "/top-cluster"')
        top = common_config.load_default_config('MYCLUSTER')
        self.assertEqual(top.db_path, '/top-cluster')

    def test_cluster_config_not_used_by_lsf(self):
        # The legacy form must not leak into the lsf panel anymore.
        self.write(self.install_config, 'config_lsf.py', 'db_path = "/lsf-base"')
        self.write(self.install_config, 'config_MYCLUSTER.py', 'db_path = "/top-cluster"')
        config = common_config.load_config('lsf', 'MYCLUSTER')
        self.assertEqual(config.db_path, '/lsf-base')

    def test_user_files_override_install_files(self):
        self.write(self.install_config, 'config_lsf.py', 'db_path = "/base"')
        self.write(self.install_config, 'config_lsf_MYCLUSTER.py', 'db_path = "/install-cluster"')
        self.write(self.user_config, 'config_lsf.py', 'db_path = "/user"')
        self.write(self.user_config, 'config_lsf_MYCLUSTER.py', 'db_path = "/user-cluster"')
        config = common_config.load_config('lsf', 'MYCLUSTER')
        self.assertEqual(config.db_path, '/user-cluster')

    def test_merge_keeps_unset_variables(self):
        # A cluster file setting only one variable must not wipe the others.
        self.write(self.install_config, 'config_lsf.py', 'db_path = "/base"\nfresh_interval = 60')
        self.write(self.install_config, 'config_lsf_MYCLUSTER.py', 'db_path = "/lsf-cluster"')
        config = common_config.load_config('lsf', 'MYCLUSTER')
        self.assertEqual(config.db_path, '/lsf-cluster')
        self.assertEqual(config.fresh_interval, 60)

    def test_no_cluster_config_falls_back_to_base(self):
        self.write(self.install_config, 'config_lsf.py', 'db_path = "/base"')
        config = common_config.load_config('lsf', 'MYCLUSTER')
        self.assertEqual(config.db_path, '/base')

    def test_missing_base_returns_none_for_top_level(self):
        self.assertIsNone(common_config.load_default_config('MYCLUSTER'))

    def test_missing_base_raises_for_panel(self):
        with self.assertRaises(FileNotFoundError):
            common_config.load_config('lsf', 'MYCLUSTER')

    def test_legacy_reload_api_unchanged(self):
        self.write(self.install_config, 'config_lsf.py', 'db_path = "/base"')
        self.write(self.install_config, 'config_lsf_LEGACY.py', 'db_path = "/legacy"')
        config = common_config.reload_config_for_cluster('LEGACY')
        self.assertEqual(config.db_path, '/legacy')

    def test_resolve_db_path_uses_cluster_top_level(self):
        from common import common_db_path
        import types

        self.write(self.install_config, 'config_MYCLUSTER.py', 'db_path = "/top-cluster"')
        subsystem = types.SimpleNamespace(db_path='')
        resolved = common_db_path.resolve_db_path(subsystem, 'lsf', 'MYCLUSTER')
        self.assertEqual(resolved, '/top-cluster/lsf')


if __name__ == '__main__':
    unittest.main()
