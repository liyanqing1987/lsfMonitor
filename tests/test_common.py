"""Shared helper tests: command timeout, safe expression evaluation."""

import time
import unittest

from common import common


class CommandTests(unittest.TestCase):
    def test_timeout_is_bounded(self):
        started = time.time()
        (code, _stdout, stderr) = common.run_command('sleep 5; echo done', timeout=0.2)
        elapsed = time.time() - started

        self.assertEqual(code, 1)
        self.assertIn(b'timed out', stderr)
        self.assertLess(elapsed, 3.0)

    def test_safe_eval(self):
        self.assertTrue(common.safe_eval_expr("'pd' in groups", {'groups': ['pd']}))
        self.assertEqual(common.safe_eval_expr('len(x) + 1', {'x': [1, 2]}), 3)

        with self.assertRaises(common.SafeEvalError):
            common.safe_eval_expr('__import__("os")', {})
