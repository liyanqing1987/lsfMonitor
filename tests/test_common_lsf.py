"""Parser tests for common_lsf, driven by deterministic command fixtures.

Fixtures cover all three supported schedulers (LSF, volclava, OpenLava); no
real cluster or client command is required.
"""

import os
import unittest
from unittest.mock import patch

from common import common, common_lsf
from support import (BHOSTS_L, BHOSTS_W, BJOBUS_UF_LSF, BJOBUS_UF_OPENLAVA,
                     BJOBUS_W, BMGROUP, BQUEUES_W, BUSERS, LSID,
                     LSID_OPENLAVA, LSID_VOLCLAVA, LSHOSTS, LSLOAD)


class LsfParserTests(unittest.TestCase):
    def parse(self, function, text, *args, **kwargs):
        with patch.object(common, 'run_command', return_value=(0, text.encode(), b'')):
            return function(*args, **kwargs)

    def test_bqueues(self):
        result = self.parse(common_lsf.get_bqueues_info, BQUEUES_W)
        self.assertIn('QUEUE_NAME', result)
        self.assertEqual(result['QUEUE_NAME'], ['normal'])

    def test_bhosts(self):
        result = self.parse(common_lsf.get_bhosts_info, BHOSTS_W)
        self.assertEqual(result['HOST_NAME'], ['cmp01', 'cmp02'])
        self.assertEqual(result['STATUS'], ['ok', 'closed_Full'])

    def test_bjobs(self):
        result = self.parse(common_lsf.get_bjobs_info, BJOBUS_W)
        self.assertEqual(result['JOBID'], ['101', '102'])
        self.assertEqual(result['STAT'][0], 'RUN')
        self.assertEqual(result['EXEC_HOST'][0], '2*cmp01')

    def test_busers(self):
        result = self.parse(common_lsf.get_busers_info, BUSERS)
        self.assertEqual(result['USER/GROUP'], ['alice', 'bob'])

    def test_lsid(self):
        result = self.parse(common_lsf.get_lsid_info, LSID)
        self.assertEqual(result[0], 'LSF')
        self.assertEqual(result[2], 'DEMO_CLUSTER')
        self.assertEqual(result[3], 'demo-master')

    def test_lsid_volclava(self):
        result = self.parse(common_lsf.get_lsid_info, LSID_VOLCLAVA)
        self.assertEqual(result[0], 'volclava')
        self.assertEqual(result[1], '1.0.0')

    def test_lsid_openlava(self):
        result = self.parse(common_lsf.get_lsid_info, LSID_OPENLAVA)
        self.assertEqual(result[0], 'openlava')
        self.assertEqual(result[1], '3.3.1')

    def test_unit_from_badmin(self):
        result = self.parse(common_lsf.get_lsf_unit_for_limits,
                            'LSF_UNIT_FOR_LIMITS = GB\n')
        self.assertEqual(result, 'GB')

    def test_unit_default_lsf_is_mb(self):
        with patch.object(common, 'run_command', return_value=(0, b'', b'')), \
             patch.dict(os.environ, {'LSF_ENVDIR': '/nonexistent',
                                     'LSF_CONFDIR': '/nonexistent'}), \
             patch.object(common_lsf, 'get_lsid_info',
                          return_value=('LSF', '', '', '')):
            self.assertEqual(common_lsf.get_lsf_unit_for_limits(), 'MB')

    def test_unit_default_openlava_is_kb(self):
        with patch.object(common, 'run_command', return_value=(0, b'', b'')), \
             patch.dict(os.environ, {'LSF_ENVDIR': '/nonexistent',
                                     'LSF_CONFDIR': '/nonexistent'}), \
             patch.object(common_lsf, 'get_lsid_info',
                          return_value=('openlava', '', '', '')):
            self.assertEqual(common_lsf.get_lsf_unit_for_limits(), 'KB')

    def test_uf_dispatch_lsf_family(self):
        for tool in ('LSF', 'volclava'):
            with patch.object(common_lsf, 'get_lsid_info',
                              return_value=(tool, '', '', '')), \
                 patch.object(common_lsf, 'get_lsf_bjobs_uf_info',
                              return_value={'tool': tool}) as lsf_fn, \
                 patch.object(common_lsf, 'get_openlava_bjobs_uf_info') as open_fn:
                self.assertEqual(common_lsf.get_bjobs_uf_info('bjobs -UF'),
                                 {'tool': tool})
                lsf_fn.assert_called_once_with('bjobs -UF')
                open_fn.assert_not_called()

    def test_uf_dispatch_openlava(self):
        with patch.object(common_lsf, 'get_lsid_info',
                          return_value=('openlava', '', '', '')), \
             patch.object(common_lsf, 'get_lsf_bjobs_uf_info') as lsf_fn, \
             patch.object(common_lsf, 'get_openlava_bjobs_uf_info',
                          return_value={'tool': 'openlava'}) as open_fn:
            self.assertEqual(common_lsf.get_bjobs_uf_info('bjobs -UF'),
                             {'tool': 'openlava'})
            open_fn.assert_called_once_with('bjobs -UF')
            lsf_fn.assert_not_called()

    def test_lsf_bjobs_uf(self):
        with patch.object(common, 'run_command',
                          return_value=(0, BJOBUS_UF_LSF.encode(), b'')), \
             patch.object(common_lsf, 'get_lsf_unit_for_limits',
                          return_value='MB'):
            result = common_lsf.get_lsf_bjobs_uf_info('bjobs -UF')

        self.assertIn('101', result)
        self.assertEqual(result['101']['user'], 'alice')
        self.assertEqual(result['101']['queue'], 'normal')
        self.assertEqual(result['101']['rusage_mem'], 123.0)

    def test_openlava_bjobs_uf(self):
        with patch.object(common, 'run_command',
                          return_value=(0, BJOBUS_UF_OPENLAVA.encode(), b'')), \
             patch.object(common_lsf, 'get_lsf_unit_for_limits',
                          return_value='MB'):
            result = common_lsf.get_openlava_bjobs_uf_info('bjobs -UF')

        self.assertIn('305', result)
        self.assertEqual(result['305']['user'], 'alice')
        self.assertEqual(result['305']['queue'], 'normal')
        self.assertEqual(result['305']['rusage_mem'], 123.0)

    def test_bmgroup(self):
        result = self.parse(common_lsf.get_bmgroup_info, BMGROUP)
        self.assertEqual(result['pd'], ['cmp01', 'cmp02'])

    def test_lsload(self):
        result = self.parse(common_lsf.get_lsload_info, LSLOAD)
        self.assertEqual(result['HOST_NAME'], ['cmp01', 'cmp02'])

    def test_bhosts_load(self):
        result = self.parse(common_lsf.get_bhosts_load_info, BHOSTS_L)
        self.assertIn('cmp01', result)
        self.assertEqual(result['cmp01']['Total']['ut'], '2%')

    def test_lshosts(self):
        result = self.parse(common_lsf.get_lshosts_info, LSHOSTS)
        self.assertEqual(result['HOST_NAME'], ['cmp01', 'cmp02'])

    def test_exec_host_list(self):
        self.assertEqual(common_lsf.get_exec_host_list('2*cmp01:cmp02'),
                         ['cmp01', 'cmp02'])
        self.assertEqual(common_lsf.get_exec_host_list(''), [])

    def test_host_list(self):
        self.assertEqual(self.parse(common_lsf.get_host_list, BHOSTS_W), ['cmp01', 'cmp02'])


if __name__ == '__main__':
    unittest.main()
