"""SQLite helper tests (create/insert/query/update and read-only handling)."""

import os
import tempfile
import unittest

from common import common_sqlite3


class SqliteHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.temp_dir.name, 'test.db')
        common_sqlite3.create_sql_table(
            self.db_file, '', 'sample', '(id INTEGER PRIMARY KEY, name TEXT)')

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_table_list_and_crud(self):
        common_sqlite3.insert_into_sql_table(self.db_file, '', 'sample', '(1, "alice")')
        common_sqlite3.insert_into_sql_table(self.db_file, '', 'sample', '(2, "bob")')

        self.assertIn('sample', common_sqlite3.get_sql_table_list(self.db_file, ''))

        data = common_sqlite3.get_sql_table_data(self.db_file, '', 'sample')
        self.assertEqual(data['id'], [1, 2])
        self.assertEqual(data['name'], ['alice', 'bob'])

        common_sqlite3.update_sql_table_data(
            self.db_file, '', 'sample', 'SET name="carol"', 'WHERE id=2')
        data = common_sqlite3.get_sql_table_data(
            self.db_file, '', 'sample', select_condition='WHERE id=2')
        self.assertEqual(data['name'], ['carol'])

    def test_read_only_missing_file(self):
        missing = os.path.join(self.temp_dir.name, 'missing.db')
        (result, connection) = common_sqlite3.connect_db_file(missing, mode='read')
        self.assertEqual(result, 'failed')
        self.assertEqual(connection, '')

    def test_write_connection(self):
        (result, connection) = common_sqlite3.connect_db_file(self.db_file, mode='write')
        self.assertEqual(result, 'passed')
        self.assertIsNotNone(connection)
        connection.close()


if __name__ == '__main__':
    unittest.main()
