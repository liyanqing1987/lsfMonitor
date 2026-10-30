"""End-to-end JSON contract tests for bmonitor_cli.

The CLI runs as a subprocess against a temporary install tree and fake
scheduler commands, so stdout purity, exit behavior and real parsing are
covered without a real cluster. Each scheduler family (LSF, volclava,
OpenLava) gets its own tree so the `lsid` detection path is exercised too.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from support import cli_env, make_install_tree, repo_path


class CliTestBase(unittest.TestCase):
    scheduler = 'lsf'

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = cls.temp_dir.name
        (cls.install_dir, cls.fake_bin, cls.db_dir) = make_install_tree(
            cls.root, scheduler=cls.scheduler)
        cls.env = cli_env(cls.install_dir, cls.fake_bin, os.path.join(cls.root, 'home'))
        os.makedirs(os.path.join(cls.root, 'home'), exist_ok=True)

        cluster_dir = os.path.join(cls.db_dir, 'DEMO_CLUSTER')
        os.makedirs(os.path.join(cluster_dir, 'job'), exist_ok=True)
        os.makedirs(os.path.join(cluster_dir, 'user'), exist_ok=True)

        db_file = os.path.join(cluster_dir, 'job', '20260101.db')
        connection = sqlite3.connect(db_file)
        connection.execute(
            'CREATE TABLE job (job TEXT, user TEXT, project TEXT, status TEXT, queue TEXT,'
            ' command TEXT, cwd TEXT, submitted_time TEXT, started_time TEXT,'
            ' finished_time TEXT, exit_code TEXT, term_signal TEXT, rusage_mem TEXT,'
            ' max_mem TEXT, avg_mem TEXT, cpu_time TEXT, run_limit TEXT, pending_reasons TEXT)')
        connection.execute(
            "INSERT INTO job VALUES ('123','alice','demo','EXIT','normal','make run',"
            "'/tmp/work','Mon Oct 26 10:00:00','Mon Oct 26 10:01:00',"
            "'Mon Oct 26 10:02:00','2','','1000','500','300','60','','')")
        connection.commit()
        connection.close()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def run_cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, repo_path('bin', 'bmonitor_cli.py')] + list(arguments),
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        payload = json.loads(result.stdout)

        return result, payload


class CliContractTests(CliTestBase):
    scheduler = 'lsf'

    def test_cluster_info(self):
        _result, payload = self.run_cli('cluster-info')
        self.assertEqual(payload['cluster'], 'DEMO_CLUSTER')
        self.assertEqual(payload['tool'], 'LSF')

    def test_jobs(self):
        _result, payload = self.run_cli('jobs')
        rows = payload

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['JOBID'], '101')

    def test_hosts(self):
        _result, payload = self.run_cli('hosts')
        rows = payload

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['host'], 'cmp01')

    def test_queues(self):
        _result, payload = self.run_cli('queues')
        rows = payload

        self.assertEqual(rows[0]['QUEUE_NAME'], 'normal')

    def test_users(self):
        _result, payload = self.run_cli('users')
        rows = payload['users']

        self.assertEqual(rows[0]['USER/GROUP'], 'alice')

    def test_host_groups(self):
        _result, payload = self.run_cli('host-groups')
        self.assertEqual(payload[0]['group'], 'pd')
        self.assertEqual(payload[0]['hosts'], ['cmp01', 'cmp02'])

    def test_host_detail(self):
        _result, payload = self.run_cli('host-detail', 'cmp01')
        self.assertEqual(payload['host'], 'cmp01')
        self.assertIn('Total', payload)

    def test_job_output(self):
        _result, payload = self.run_cli('job-output', '101')
        self.assertEqual(payload['jobid'], '101')
        self.assertIn('hello from job 101', payload['output'])

    def test_invalid_jobid_is_json_error(self):
        _result, payload = self.run_cli('job', '123; rm -rf /')
        self.assertIn('error', payload)

    def test_invalid_status_is_json_error(self):
        _result, payload = self.run_cli('jobs', '--status', 'BAD')
        self.assertIn('error', payload)

    def test_db_clusters(self):
        _result, payload = self.run_cli('db', 'clusters')
        self.assertEqual(payload[0]['cluster'], 'DEMO_CLUSTER')

    def test_db_job(self):
        _result, payload = self.run_cli('db', 'job', '123')
        self.assertEqual(payload['jobid'], '123')
        self.assertEqual(payload['job']['user'], 'alice')

    def test_db_jobs(self):
        _result, payload = self.run_cli('db', 'jobs', '--date', '20260101')
        rows = payload.get('jobs', payload)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['job'], '123')


class VolclavaCliTests(CliTestBase):
    scheduler = 'volclava'

    def test_cluster_info_reports_volclava(self):
        _result, payload = self.run_cli('cluster-info')
        self.assertEqual(payload['tool'], 'volclava')
        self.assertEqual(payload['cluster'], 'DEMO_CLUSTER')

    def test_jobs_use_lsf_family_parser(self):
        _result, payload = self.run_cli('jobs')
        self.assertEqual(len(payload), 2)


class OpenLavaCliTests(CliTestBase):
    scheduler = 'openlava'

    def test_cluster_info_reports_openlava(self):
        _result, payload = self.run_cli('cluster-info')
        self.assertEqual(payload['tool'], 'openlava')
        self.assertEqual(payload['cluster'], 'DEMO_CLUSTER')


if __name__ == '__main__':
    unittest.main()
