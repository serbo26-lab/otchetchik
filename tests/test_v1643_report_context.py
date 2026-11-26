"""Protect the selected report and its pending edits during context changes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

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

    def test_report_save_rejects_external_rows_and_preserves_pending_edits(self):
        page = self.page
        page.notes.setPlainText("Synthetic pending edit")
        added = db.execute(
            "INSERT INTO report_content_items(report_id,name) VALUES(?,?)",
            (self.report, "Synthetic external work"),
        )
        with self.assertRaisesRegex(ValueError, "изменился"):
            page.flush_autosave()
        self.assertTrue(page._dirty_report)
        self.assertEqual(page.notes.toPlainText(), "Synthetic pending edit")
        self.assertIsNotNone(db.row("SELECT id FROM report_content_items WHERE id=?", (added,)))
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0], "")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            page.save_manually()
        self.assertTrue(page._dirty_report)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            page.save_manually()
        self.assertFalse(page._dirty_report)
        self.assertEqual(page.content_table.rowCount(), 1)
        page.notes.setPlainText("Synthetic retry")
        page.flush_autosave()
        page.notes.setPlainText("Synthetic second save")
        page.flush_autosave()
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0],
                         "Synthetic second save")
        self.assertIsNotNone(db.row("SELECT id FROM report_content_items WHERE id=?", (added,)))

    def test_report_save_rejects_external_financial_edit_but_not_house_edit(self):
        page = self.page
        db.execute("UPDATE houses SET address=? WHERE id=?", ("Synthetic renamed house", self.house))
        page.notes.setPlainText("Synthetic first edit")
        page.flush_autosave()
        db.execute("UPDATE report_financials SET accrued=45 WHERE report_id=? AND payer_type=?",
                   (self.report, "Собственники"))
        page.notes.setPlainText("Synthetic pending edit")
        with self.assertRaisesRegex(ValueError, "изменился"):
            page.flush_autosave()
        self.assertEqual(db.row("SELECT accrued FROM report_financials WHERE report_id=? AND payer_type=?",
                                (self.report, "Собственники"))[0], 45)
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0],
                         "Synthetic first edit")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            page.save_manually()
        self.assertFalse(page._dirty_report)

    def test_manual_sqlite_failure_keeps_pending_report(self):
        page = self.page
        page.notes.setPlainText("Synthetic pending edit")
        with patch("src.uk728.reports_ui.connect", side_effect=sqlite3.OperationalError("synthetic locked database")):
            with patch.object(QMessageBox, "warning") as warning:
                page.save_manually()
                warning.assert_called_once()
        self.assertEqual(page.current_id, self.report)
        self.assertTrue(page._dirty_report)
        self.assertEqual(page.notes.toPlainText(), "Synthetic pending edit")
        self.assertEqual(self.window.page_header.save_status.property("state"), "error")
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0], "")
        page.flush_autosave()
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0],
                         "Synthetic pending edit")

    def test_failed_report_read_disables_stale_editor_until_retry(self):
        page = self.page
        second = db.create_report(self.company, self.house, 2025,
                                  "2025-07-01", "2025-12-31", True)
        page.refresh()
        page.notes.setPlainText("Synthetic first report edit")
        target = next(i for i in range(page.list.count())
                      if page.list.item(i).data(256) == second)
        with patch("src.uk728.reports_ui.db.load_report_bundle",
                   side_effect=sqlite3.OperationalError("synthetic read failure")):
            page.list.setCurrentRow(target)
        self.assertIsNone(page.current_id)
        self.assertEqual(page.list.currentRow(), -1)
        self.assertFalse(page.save_button.isEnabled())
        self.assertFalse(page.report_tabs.isEnabled())
        self.assertFalse(page._autosave_timer.isActive())
        self.assertFalse(page._dirty_report)
        self.assertEqual(self.window.page_header.save_status.property("state"), "error")
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (self.report,))[0],
                         "Synthetic first report edit")
        page.save(silent=True)
        self.assertEqual(db.row("SELECT notes FROM reports WHERE id=?", (second,))[0], "")
        page.list.setCurrentRow(target)
        self.assertEqual(page.current_id, second)
        self.assertTrue(page.save_button.isEnabled())
        self.assertEqual(page.notes.toPlainText(), "")

    def test_failed_report_population_disables_partially_loaded_editor(self):
        page = self.page
        with patch.object(page, "_populate_loaded_report", side_effect=ValueError("synthetic form failure")):
            page._load_current()
        self.assertIsNone(page.current_id)
        self.assertFalse(page.save_button.isEnabled())
        self.assertFalse(page.delete_report_btn.isEnabled())
        self.assertFalse(page._loading_report)
        self.assertEqual(page.list.currentRow(), -1)
        page.list.setCurrentRow(0)
        self.assertEqual(page.current_id, self.report)
        self.assertTrue(page.save_button.isEnabled())

    def test_negative_save_notifications_do_not_announce_success(self):
        for message in ("Переход отменен: отчет не сохранен",
                        "Отчёт не сохранён", "Не удалось сохранить данные"):
            with self.subTest(message=message):
                self.window.set_save_status("saved")
                self.window.notification_received(message)
                self.assertEqual(self.window.page_header.save_status.property("state"), "error")
        self.window.notification_received("Отчет сохранен")
        self.assertEqual(self.window.page_header.save_status.property("state"), "saved")

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
