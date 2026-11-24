"""Regression tests for explicit name-keyed SQLite query results."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.uk728 import db


class NamedRowsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(temporary.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()

    def test_named_rows_preserve_aliases_values_order_and_bound_parameters(self):
        query = """
            SELECT 2 AS id, ? AS name, NULL AS absent, 1.25 AS amount
            UNION ALL SELECT 1, ?, NULL, 2.5
            ORDER BY id
        """
        params = ("Synthetic B", "Synthetic A")
        original = db.rows(query, params)
        records = db.named_rows(query, params)
        self.assertEqual(records, [dict(record) for record in original])
        self.assertEqual(records, [
            {"id": 1, "name": "Synthetic A", "absent": None, "amount": 2.5},
            {"id": 2, "name": "Synthetic B", "absent": None, "amount": 1.25},
        ])
        self.assertTrue(all(isinstance(record, dict) for record in records))
        self.assertTrue(all(isinstance(record, sqlite3.Row) for record in original))
        self.assertEqual(original[0][0], original[0]["id"])

    def test_empty_query_result_and_single_row_contract(self):
        self.assertEqual(db.named_rows("SELECT id FROM companies WHERE id=?", (-1,)), [])
        result = db.row("SELECT 7 AS id")
        self.assertIsInstance(result, sqlite3.Row)
        self.assertEqual(result[0], 7)
        self.assertEqual(result["id"], 7)


if __name__ == "__main__":
    unittest.main()
