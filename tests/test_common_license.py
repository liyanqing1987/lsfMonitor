"""License command-construction tests (no real lmstat execution)."""

import os
import tempfile
import unittest

from common import common_license

LMSTAT = os.path.join(tempfile.gettempdir(), 'lmstat')


class LicenseCommandTests(unittest.TestCase):
    def test_plain_command(self):
        info = common_license.GetLicenseInfo(lmstat_path='lmstat', bsub_command='')
        self.assertEqual(info.get_lmstat_command(), 'lmstat -a -i')

    def test_server_and_feature(self):
        info = common_license.GetLicenseInfo(
            lmstat_path=LMSTAT, bsub_command='', specified_feature='vcs')
        self.assertEqual(info.get_lmstat_command('5280@lic01'),
                         f'{LMSTAT} -a -i -c 5280@lic01 vcs')

    def test_bsub_wrapper(self):
        info = common_license.GetLicenseInfo(
            lmstat_path='lmstat', bsub_command='bsub -q normal -Is')
        self.assertEqual(info.get_lmstat_command(),
                         'bsub -q normal -Is "lmstat -a -i"')


if __name__ == '__main__':
    unittest.main()
