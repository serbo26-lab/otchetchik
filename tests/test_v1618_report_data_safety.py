"""Regression tests for the report-data safety audit (fictional data only)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.uk728 import db
from src.uk728.exporters import _new_export_path, export_docx, export_pdf, export_xlsx
from src.uk728.mass_apply_logic import OutdatedPreview, apply_plan, build_plan
from src.uk728.report_storage import sync_report_rows


class ReportDataSafetyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = patch("src.uk728.paths.app_dir", return_value=self.root)
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.org = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная организация",))
        self.home = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.org, "Учебный объект А", 100),
        )
        self.work = db.execute(
            "INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
            ("Учебная услуга", "Содержание", "шт."),
        )
        self.rid = db.create_report(self.org, self.home, 2025)
        self.inputs = {
            "company_id": self.org, "work_id": self.work, "year": 2025,
            "house_ids": [self.home], "mode": "fixed", "total": "100",
            "create_missing": True, "update_existing": True,
            "override_manual": True,
        }

    def plan(self):
        with db.connect() as conn:
            return build_plan(conn, self.inputs)

    def apply(self, plan):
        with db.connect() as conn:
            return apply_plan(conn, plan)

    def set_status(self, status):
        db.execute("UPDATE reports SET status=? WHERE id=?", (status, self.rid))

    def test_protected_reports_skip_even_with_manual_override(self):
        for status in ("Размещен", "Размещён", "Архив"):
            with self.subTest(status=status):
                self.set_status(status)
                plan = self.plan()
                self.assertEqual(plan["rows"][0]["action"], "skip_protected_report")
                self.assertEqual(plan["summary"]["insert"], 0)
                with self.assertRaises(ValueError):
                    self.apply(plan)
                self.assertFalse(db.rows(
                    "SELECT id FROM report_content_items WHERE report_id=?", (self.rid,)
                ))
                self.assertEqual(db.row("SELECT status FROM reports WHERE id=?", (self.rid,))["status"], status)

    def test_status_changed_after_preview_rejects_the_entire_plan(self):
        plan = self.plan()
        self.set_status("Размещен")
        with self.assertRaises(OutdatedPreview):
            self.apply(plan)
        self.assertFalse(db.rows("SELECT id FROM report_content_items WHERE report_id=?", (self.rid,)))

    def test_verified_report_becomes_draft_on_insert_and_update(self):
        self.set_status("Проверен")
        self.apply(self.plan())
        self.assertEqual(db.row("SELECT status FROM reports WHERE id=?", (self.rid,))["status"], "Черновик")
        self.set_status("Проверен")
        self.inputs["total"] = "200"
        self.apply(self.plan())
        self.assertEqual(db.row("SELECT status FROM reports WHERE id=?", (self.rid,))["status"], "Черновик")
        self.assertEqual(db.row("SELECT plan_cost FROM report_content_items WHERE report_id=?", (self.rid,))["plan_cost"], 200)

    def test_cleared_saved_name_is_not_an_implicit_delete_in_any_section(self):
        for table in ("report_content_items", "report_repair_items", "report_management_items"):
            with self.subTest(table=table):
                row_id = db.execute(
                    f"INSERT INTO {table}(report_id,name) VALUES(?,?)",
                    (self.rid, "Учебная работа"),
                )
                old = dict(db.row(f"SELECT * FROM {table} WHERE id=?", (row_id,)))
                with self.assertRaisesRegex(ValueError, "наименование"):
                    with db.connect() as conn:
                        sync_report_rows(conn, table, self.rid, [{**old, "name": " "}])
                self.assertEqual(dict(db.row(f"SELECT * FROM {table} WHERE id=?", (row_id,))), old)
                # Explicit removal of the row from the table remains possible.
                with db.connect() as conn:
                    sync_report_rows(conn, table, self.rid, [])
                    conn.commit()
                self.assertIsNone(db.row(f"SELECT * FROM {table} WHERE id=?", (row_id,)))

    def test_unnamed_new_populated_row_is_not_silently_discarded(self):
        with db.connect() as conn:
            with self.assertRaisesRegex(ValueError, "наименование"):
                sync_report_rows(conn, "report_content_items", self.rid, [
                    {"name": "", "unit": "усл.", "unit_price": 10, "plan_qty": 2},
                ])
            result = sync_report_rows(conn, "report_content_items", self.rid, [
                {"name": "", "unit": "усл.", "unit_price": 0, "plan_qty": 0},
            ])
            self.assertEqual(result, [])

    def test_failed_export_reservation_does_not_touch_previous_file(self):
        base = self.root / "example.xlsx"
        base.write_bytes(b"existing export")
        with self.assertRaisesRegex(OSError, "synthetic failure"):
            with _new_export_path(base) as target:
                self.assertNotEqual(base, target)
                target.write_bytes(b"partial")
                raise OSError("synthetic failure")
        self.assertEqual(base.read_bytes(), b"existing export")
        self.assertEqual(list(self.root.glob("example*.xlsx")), [base])

    def test_export_same_house_year_and_repeated_exports_keep_all_files(self):
        db.execute("UPDATE reports SET period_end=? WHERE id=?", ("2025-06-30", self.rid))
        other = db.create_report(self.org, self.home, 2025, "2025-07-01", "2025-12-31")
        for export in (export_xlsx, export_docx, export_pdf):
            with self.subTest(format=export.__name__):
                first = export(self.rid)
                original = first.read_bytes()
                second = export(other)
                repeated = export(self.rid)
                self.assertEqual(len({first, second, repeated}), 3)
                self.assertEqual(first.read_bytes(), original)
                self.assertTrue(second.stat().st_size)
                self.assertTrue(repeated.stat().st_size)


if __name__ == "__main__":
    unittest.main()
