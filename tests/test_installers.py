"""Installer tests: install writes into the current directory; --clean is file-only."""

import importlib.util
import os
import stat
import tempfile
import unittest

from support import repo_path


def load_installer():
    """Load the top-level install.py without colliding with sub-installers."""

    path = repo_path('install.py')
    spec = importlib.util.spec_from_file_location('lsfmonitor_installer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cwd = os.path.join(self.temp_dir.name, 'installed')
        os.makedirs(self.cwd)
        self.old_cwd = os.getcwd()
        os.chdir(self.cwd)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.temp_dir.cleanup()

    def test_install_writes_into_cwd(self):
        module = load_installer()
        installer = module.Installation(False, False)

        installer.gen_shell_tools()
        installer.ensure_db_root()
        installer.gen_default_config_file()
        installer.gen_lsf_config_file()
        installer.gen_license_config_file()
        installer.gen_run_config_file()
        installer.gen_ai_config_file()
        installer.gen_license_config_files()

        self.assertTrue(os.path.isfile(os.path.join(self.cwd, 'bin', 'bmonitor')))
        self.assertTrue(os.path.isfile(os.path.join(self.cwd, 'config', 'config.py')))
        self.assertTrue(os.path.isfile(os.path.join(self.cwd, 'config', 'config_lsf.py')))

        with open(os.path.join(self.cwd, 'bin', 'bmonitor'), encoding='utf-8') as handle:
            self.assertIn(self.cwd, handle.read())

        ai_mode = stat.S_IMODE(os.stat(os.path.join(self.cwd, 'config', 'config_ai.py')).st_mode)
        self.assertEqual(ai_mode, 0o755)

    def test_clean_removes_tools_and_keeps_directories(self):
        module = load_installer()
        installer = module.Installation(True, False)
        os.makedirs(os.path.join(self.cwd, 'bin'))
        os.makedirs(os.path.join(self.cwd, 'keepdir'))

        source_probe = os.path.join(self.cwd, 'source_probe.py')
        wrapper = os.path.join(self.cwd, 'bin', 'bmonitor')
        keepdir = os.path.join(self.cwd, 'keepdir')

        for path in (source_probe, wrapper):
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('probe\n')

        installer.tool_list = ['bin/bmonitor', 'keepdir']

        with self.assertRaises(SystemExit):
            installer.cleanup()

        self.assertTrue(os.path.isfile(source_probe))
        self.assertTrue(os.path.isdir(keepdir))
        self.assertFalse(os.path.isfile(wrapper))


if __name__ == '__main__':
    unittest.main()
