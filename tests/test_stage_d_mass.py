"""Stage D: preview integrity, selection, calculations, manual edits and atomicity."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.mass_apply_logic import (
    OutdatedPreview, apply_plan, build_plan, validate_inputs,
)


class MassWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.p = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        self.p.start()
        self.addCleanup(self.p.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.company_id = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Буревестник",))
        self.work_id = db.execute(
            "INSERT INTO work_items(name,category,unit,default_price) VALUES(?,?,?,?)",
            ("Контрольная уборка", "Содержание", "м²", 5.0),
        )
        self.house_1 = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.company_id, "Тестовый дом А", 100.0),
        )
        self.house_2 = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.company_id, "Тестовый дом Б", 300.0),
        )
        self.house_archive = db.execute(
            "INSERT INTO houses(company_id,address,total_area,status) VALUES(?,?,?,?)",
            (self.company_id, "Архивный дом", 500.0, "Архив"),
        )
        self.ui = self.window.mass_tab
        self.ui.reload()
        self.ui.company.setCurrentIndex(self.ui.company.findData(self.company_id))
        self.ui.work.setCurrentIndex(self.ui.work.findData(self.work_id))
        self.ui.year.setValue(2025)
        self.ui.create_missing.setChecked(True)

    def _set_mode(self, key: str):
        self.ui.mode.setCurrentIndex(self.ui.mode.findData(key))

    def _preview_area(self):
        self._set_mode("area")
        self.ui.rate.setText("6,76")
        self.ui.preview()
        self.assertFalse(self.ui._preview_dirty)
        self.assertIsNotNone(self.ui._preview_plan)
        return self.ui._preview_plan

    def _insert_existing(self, house_id=None, *, manual=1, price=6.0):
        hid = house_id or self.house_1
        rid = db.create_report(self.company_id, hid, 2025)
        row_id = db.execute(
            """INSERT INTO report_content_items
            (report_id, work_item_id, name, unit, unit_price, plan_qty,
             plan_cost, actual_qty, actual_cost, source, is_manual)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, self.work_id, "Контрольная уборка", "м²", price, 100,
             price * 100, 100, price * 100, "ручной ввод", manual),
        )
        return rid, row_id

    def test_explicit_house_selection_and_archived_excluded(self):
        self.assertEqual(self.ui.house_list.count(), 2)
        self.assertEqual(set(self.ui.selected_house_ids()), {self.house_1, self.house_2})
        self.assertNotIn(self.house_archive, self.ui.selected_house_ids())
        self.ui._check_all(False)
        self.ui.rate.setText("3")
        self.ui.preview()
        self.assertTrue(self.ui._preview_dirty)
        self.assertFalse(self.ui.apply_button.isEnabled())
        self.assertIn("дом", self.ui.status_label.text().lower())
        self.ui.house_list.item(0).setCheckState(Qt.Checked)
        self.ui.preview()
        self.assertEqual(len(self.ui._preview_plan["rows"]), 1)
        self.assertTrue(self.ui.apply_button.isEnabled())

    def test_monthly_price_and_quantity_validation(self):
        self._set_mode("monthly")
        self.ui.rate.setText("6,76")
        self.ui.months.setText("0")
        self.ui.preview()
        self.assertTrue(self.ui._preview_dirty)
        self.assertIn("12", self.ui.status_label.text())
        self.ui.months.setText("12")
        self.ui.preview()
        plan = self.ui._preview_plan
        self.assertEqual(plan["summary"]["insert"], 2)
        self.assertAlmostEqual(plan["rows"][0]["price"], 81.12)
        self.assertAlmostEqual(plan["rows"][0]["cost"], 8112.0)
        self._set_mode("quantity")
        self.ui.rate.setText("20")
        self.ui.quantity.setText("")
        self.ui.preview()
        self.assertFalse(self.ui.apply_button.isEnabled())
        self.ui.quantity.setText("0")
        self.ui.preview()
        self.assertTrue(self.ui._preview_dirty)
        self.ui.quantity.setText("3")
        self.ui.preview()
        self.assertEqual(self.ui._preview_plan["summary"]["cost"], 120)

    def test_split_total_uses_only_actionable_houses(self):
        self._set_mode("split")
        self.ui.total.setText("1000")
        self.ui.preview()
        rows = self.ui._preview_plan["rows"]
        self.assertEqual([r["cost"] for r in rows], [250.0, 750.0])
        self.assertEqual(self.ui._preview_plan["summary"]["cost"], 1000.0)
        _, old = self._insert_existing(self.house_1, manual=1)
        self.ui.preview()
        actions = {r["house_id"]: r for r in self.ui._preview_plan["rows"]}
        self.assertEqual(actions[self.house_1]["action"], "skip_manual")
        self.assertEqual(actions[self.house_2]["cost"], 1000.0)
        self.assertEqual(self.ui._preview_plan["summary"]["cost"], 1000.0)

    def test_preview_is_read_only_and_never_drives_database(self):
        plan = self._preview_area()
        self.assertEqual(self.ui.table.editTriggers(), QTableWidget.NoEditTriggers)
        n = self.ui.table.rowCount()
        QGuiApplication.clipboard().setText("999999\t999999")
        self.ui.table.setCurrentCell(0, 4)
        self.ui.table.paste_selection()
        self.ui.table.delete_selected_rows()
        self.assertEqual(self.ui.table.rowCount(), n)
        self.assertNotIn("999999", self.ui.table.value(0, 4))
        # Even a programmatic modification to a preview cell cannot affect the plan.
        self.ui.table.set_value(0, 4, "9999999")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.ui.apply()
        rid = db.row("SELECT id FROM reports WHERE house_id=? AND year=2025", (self.house_1,))["id"]
        item = db.row("SELECT plan_cost FROM report_content_items WHERE report_id=?", (rid,))
        self.assertEqual(item["plan_cost"], 676.0)

    def test_update_manual_line_protected_until_separate_permission(self):
        rid, item = self._insert_existing(manual=1)
        self.ui.update_existing.setChecked(True)
        self._preview_area()
        actions = {r["house_id"]: r["action"] for r in self.ui._preview_plan["rows"]}
        self.assertEqual(actions[self.house_1], "skip_manual")
        self.assertEqual(actions[self.house_2], "create")
        self.assertFalse(self.ui.override_manual.isChecked())
        self.ui.override_manual.setChecked(True)
        self.assertTrue(self.ui._preview_dirty)
        self.assertFalse(self.ui.apply_button.isEnabled())
        self.ui.preview()
        self.assertEqual(self.ui._preview_plan["rows"][0]["action"], "update")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes) as confirm:
            self.ui.apply()
        self.assertIn("ручную", confirm.call_args[0][2].lower())
        record = db.row("SELECT * FROM report_content_items WHERE id=?", (item,))
        self.assertEqual(record["is_manual"], 0)
        self.assertEqual(record["source"], "массовое применение")
        self.assertEqual(record["plan_cost"], 676.0)
        self.assertTrue(self.ui._preview_dirty)

    def test_changing_parameters_disables_apply_and_does_not_repreview_automatically(self):
        self._preview_area()
        self.ui.rate.setText("10")
        self.assertTrue(self.ui._preview_dirty)
        self.assertFalse(self.ui.apply_button.isEnabled())
        with patch.object(QMessageBox, "question") as confirm:
            self.ui.apply()
        confirm.assert_not_called()
        self.assertFalse(db.rows("SELECT * FROM reports WHERE house_id IN (?,?)", (self.house_1, self.house_2)))

    def test_data_drift_rejected_without_partial_write(self):
        self._preview_area()
        db.execute("UPDATE houses SET total_area=200 WHERE id=?", (self.house_1,))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.ui.apply()
        self.assertTrue(self.ui._preview_dirty)
        self.assertIn("изменились", self.ui.status_label.text())
        self.assertEqual(len(db.rows("SELECT * FROM reports WHERE year=2025 AND company_id=?", (self.company_id,))), 0)

    def test_manual_flag_changed_after_preview_rejected(self):
        rid, item = self._insert_existing(manual=0)
        self.ui.update_existing.setChecked(True)
        self._preview_area()
        db.execute("UPDATE report_content_items SET is_manual=1 WHERE id=?", (item,))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.ui.apply()
        self.assertTrue(self.ui._preview_dirty)
        self.assertEqual(db.row("SELECT is_manual FROM report_content_items WHERE id=?", (item,))["is_manual"], 1)

    def test_atomic_rollback_for_failed_write(self):
        plan = self._preview_area()
        with db.connect() as conn:
            conn.execute(f"""CREATE TRIGGER prevent_second_house BEFORE INSERT ON report_content_items
                WHEN (SELECT house_id FROM reports WHERE id=NEW.report_id)={self.house_2}
                BEGIN SELECT RAISE(ABORT, 'forced rollback'); END;""")
            conn.commit()
        with db.connect() as conn:
            with self.assertRaises(Exception):
                apply_plan(conn, plan)
        self.assertEqual(len(db.rows("SELECT id FROM reports WHERE year=2025 AND company_id=?", (self.company_id,))), 0)
        self.assertEqual(len(db.rows("SELECT id FROM report_content_items WHERE work_item_id=?", (self.work_id,))), 0)

    def test_multiple_reports_in_year_are_skipped_safely(self):
        with db.connect() as conn:
            db.create_report_in_conn(conn, self.company_id, self.house_1, 2025,
                                     "2025-01-01", "2025-06-30")
            db.create_report_in_conn(conn, self.company_id, self.house_1, 2025,
                                     "2025-07-01", "2025-12-31")
            conn.commit()
        plan = self._preview_area()
        result = {r["house_id"]: r["action"] for r in plan["rows"]}
        self.assertEqual(result[self.house_1], "skip_many_reports")
        self.assertEqual(result[self.house_2], "create")
        self.assertEqual(plan["summary"]["skip"], 1)

    def test_form_requires_explicit_selected_inputs(self):
        bad = {
            "company_id": self.company_id, "work_id": self.work_id, "year": 2025,
            "house_ids": [self.house_1], "mode": "monthly", "rate": "3",
            "months": "13", "create_missing": True,
        }
        with self.assertRaises(ValueError):
            validate_inputs(bad)
        bad["months"] = "12"
        with db.connect() as conn:
            p = build_plan(conn, bad)
        self.assertEqual(p["rows"][0]["cost"], 3600.0)


if __name__ == "__main__":
    import unittest
    unittest.main()
