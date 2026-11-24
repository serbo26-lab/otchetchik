"""Protect the selected report and its pending edits during context changes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog

from src.uk728 import db
from src.uk728.app import MainWindow


class ReportContextSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        folder = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(folder.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO A",))
        self.other_company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO B",))
        self.house = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                                (self.company, "Synthetic house A"))
        self.other_house = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                                      (self.company, "Synthetic house B"))
        db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                   (self.other_company, "Synthetic house C"))
        self.report = db.create_report(self.company, self.house, 2025,
                                       "2025-01-01", "2025-06-30", True)
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.page = self.window.reports_tab
        self.window.navigate(6)
        self.page.company_filter.setCurrentIndex(self.page.company_filter.findData(self.company))
        self.page.house_filter.setCurrentIndex(self.page.house_filter.findData(self.house))
        self.page.year_filter.setValue(2025)
        self.assertEqual(self.page.current_id, self.report)

    def test_failed_filter_changes_keep_old_context_and_pending_values(self):
        page = self.page
        page.notes.setPlainText("Synthetic pending edit")
        with patch("src.uk728.reports_ui.connect", side_effect=OSError("synthetic disk error")):
            page.company_filter.setCurrentIndex(page.company_filter.findData(self.other_company))
            self.assertEqual(page.company_filter.currentData(), self.company)
            page.house_filter.setCurrentIndex(page.house_filter.findData(self.other_house))
            self.assertEqual(page.house_filter.currentData(), self.house)
            page.year_filter.setValue(2026)
            self.assertEqual(page.year_filter.value(), 2025)
        self.assertEqual(page.current_id, self.report)
        self.assertEqual(page.list.currentItem().data(256), self.report)
        self.assertEqual(page.notes.toPlainText(), "Synthetic pending edit")
        self.assertTrue(page._dirty_report)
        self.assertEqual(self.window.page_header.save_status.property("state"), "error")
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0], "")
        page.flush_autosave()
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0],
                         "Synthetic pending edit")

    def test_creation_same_year_saves_previous_report_before_new_id(self):
        page = self.page
        page.notes.setPlainText("Synthetic old report notes")
        page.add_content_row({"name": "Synthetic work", "unit_price": 5,
                              "plan_qty": 2, "actual_qty": 1})
        with patch("src.uk728.reports_ui.CreateReportDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.values.return_value = (2025, "2025-07-01", "2025-12-31", True)
            page.create_report()
        self.assertNotEqual(page.current_id, self.report)
        old = db.load_report_bundle(self.report)
        new = db.load_report_bundle(page.current_id)
        self.assertEqual(old["report"]["notes"], "Synthetic old report notes")
        self.assertEqual(len(old["content"]), 1)
        self.assertEqual(new["report"]["period_start"], "2025-07-01")
        self.assertEqual(new["report"]["period_end"], "2025-12-31")
        self.assertEqual(new["report"]["notes"], "")
        self.assertEqual(len(new["content"]), 0)
        self.assertFalse(page._dirty_report)

    def test_creation_is_blocked_when_previous_report_cannot_be_saved(self):
        page = self.page
        page.notes.setPlainText("Synthetic pending edit")
        with patch("src.uk728.reports_ui.CreateReportDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.values.return_value = (2025, "2025-07-01", "2025-12-31", True)
            with patch("src.uk728.reports_ui.connect", side_effect=OSError("synthetic disk error")):
                page.create_report()
        self.assertEqual(page.current_id, self.report)
        self.assertEqual(db.row("SELECT COUNT(*) FROM reports")[0], 1)
        self.assertTrue(page._dirty_report)
        self.assertEqual(page.notes.toPlainText(), "Synthetic pending edit")
        page.flush_autosave()


if __name__ == "__main__":
    unittest.main()
