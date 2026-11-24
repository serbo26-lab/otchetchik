"""Storage refactoring regressions using only synthetic records."""
from __future__ import annotations

import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.uk728 import db
from src.uk728.report_storage import COLUMNS, same_business_data, sync_report_rows
from src.uk728.template_storage import save_template


class StorageRefactoringTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(temporary.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic company",))
        self.house = db.execute(
            "INSERT INTO houses(company_id,address) VALUES(?,?)",
            (self.company, "Synthetic building"),
        )
        self.report = db.create_report(self.company, self.house, 2025)

    def test_business_comparison_retains_tolerance_and_null_semantics(self):
        keys = ("name", "cost", "position")
        old = {"name": None, "cost": 2.0, "position": 1}
        self.assertTrue(same_business_data(old, {"name": "", "cost": "2.0000001", "position": 8}, keys))
        self.assertFalse(same_business_data(old, {"name": "", "cost": "2.00001", "position": 1}, keys))
        self.assertFalse(same_business_data(old, {"name": "", "cost": "invalid", "position": 1}, keys))
        self.assertFalse(same_business_data(old, {"name": "changed", "cost": 2, "position": 1}, keys))

    def test_report_reorder_does_not_mark_imported_rows_as_manual(self):
        for table in COLUMNS:
            with self.subTest(table=table), closing(db.connect()) as conn:
                with conn:
                    first = conn.execute(
                        f"INSERT INTO {table}(report_id,name,source,is_manual,position) VALUES(?,?,?,?,?)",
                        (self.report, "Synthetic A", "synthetic-import", 0, 1),
                    ).lastrowid
                    second = conn.execute(
                        f"INSERT INTO {table}(report_id,name,source,is_manual,position) VALUES(?,?,?,?,?)",
                        (self.report, "Synthetic B", "synthetic-import", 0, 2),
                    ).lastrowid
                before = [
                    dict(row) for row in conn.execute(
                        f"SELECT * FROM {table} WHERE report_id=? ORDER BY position", (self.report,)
                    )
                ]
                with conn:
                    self.assertEqual(sync_report_rows(conn, table, self.report, reversed(before)), [])
                reordered = [
                    dict(row) for row in conn.execute(
                        f"SELECT * FROM {table} WHERE report_id=? ORDER BY position", (self.report,)
                    )
                ]
                self.assertEqual([row["id"] for row in reordered], [second, first])
                self.assertEqual([row["is_manual"] for row in reordered], [0, 0])
                self.assertEqual([row["source"] for row in reordered], ["synthetic-import"] * 2)
                reordered[0]["name"] = "Synthetic edit"
                new_row = {**reordered[1], "id": None, "name": "Synthetic new", "source": "synthetic-new"}
                with conn:
                    inserted = sync_report_rows(conn, table, self.report, [reordered[0], new_row])
                after = [
                    dict(row) for row in conn.execute(
                        f"SELECT * FROM {table} WHERE report_id=? ORDER BY position", (self.report,)
                    )
                ]
                self.assertEqual(inserted, [(1, after[1]["id"])])
                self.assertEqual(after[0]["id"], second)
                self.assertEqual(after[0]["is_manual"], 1)
                self.assertEqual(after[0]["source"], "synthetic-import")
                self.assertEqual(after[1]["source"], "synthetic-new")
                self.assertNotIn(first, [row["id"] for row in after])

    def test_invalid_template_rows_are_rejected_before_any_write(self):
        template = db.execute("INSERT INTO templates(name) VALUES(?)", ("Synthetic template",))
        payload = {"work_item_id": None, "name": "Synthetic item", "unit": "шт.",
                   "unit_price": 10, "plan_qty": 2}
        with closing(db.connect()) as conn:
            with conn:
                inserted = save_template(conn, template, "Synthetic template", "Содержание", "", [payload])
            first = inserted[0][1]
            baseline = conn.total_changes
            with self.assertRaisesRegex(ValueError, "наименование"):
                save_template(conn, template, "Changed title", "Содержание", "",
                              [{**payload, "id": first, "plan_qty": 8}, {**payload, "name": ""}])
            self.assertEqual(conn.total_changes, baseline)
            with self.assertRaises(ValueError):
                save_template(conn, template, "Changed title", "Содержание", "",
                              [{**payload, "id": first}, {**payload, "id": first}])
            self.assertEqual(conn.total_changes, baseline)
            with conn:
                new = save_template(conn, template, "Synthetic template", "Содержание", "",
                                    [{**payload, "name": "Synthetic new"}, {**payload, "id": first}])
            records = list(conn.execute(
                "SELECT id,position FROM template_items WHERE template_id=? ORDER BY position", (template,)
            ))
            self.assertEqual([tuple(row) for row in records], [(new[0][1], 1), (first, 2)])


if __name__ == "__main__":
    unittest.main()
