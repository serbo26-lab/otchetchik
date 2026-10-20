"""Stage E: guided reports, review gating, row safety and unchanged exporters."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.report_review import inspect_report, bundle_fingerprint


class ReportWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.filesystem = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        self.filesystem.start()
        self.addCleanup(self.filesystem.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.company_id = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Для Отчетности",))
        self.house_id = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.company_id, "Многоквартирный дом № 4", 1200),
        )
        self.report_id = db.create_report(self.company_id, self.house_id, 2025)
        self.page = self.window.reports_tab
        self.page.reload_filters()
        self.page.company_filter.setCurrentIndex(
            self.page.company_filter.findData(self.company_id)
        )
        self.page.house_filter.setCurrentIndex(self.page.house_filter.findData(self.house_id))
        self.page.year_filter.setValue(2025)
        self.page.refresh()
        self.assertEqual(self.page.current_id, self.report_id)

    def test_report_context_and_six_stages(self):
        self.assertEqual(self.page.report_tabs.count(), 6)
        self.assertEqual(self.page.report_tabs.tabText(5), "Проверка и выгрузка")
        self.assertTrue(self.page.save_button.isEnabled())
        self.assertTrue(self.page.delete_report_btn.isEnabled())
        self.assertTrue(self.page.create_report_btn.isEnabled())

    def test_saved_review_only_and_warnings(self):
        result = self.page.run_review()
        self.assertIsNotNone(result)
        self.assertFalse(result["errors"])
        self.assertTrue(result["warnings"])
        self.assertFalse(self.page._review_dirty)
        self.assertIn("не является юридической экспертизой", self.page.review_text.toPlainText())
        self.assertIn("Содержание", self.page.review_text.toPlainText())

    def test_review_detects_inverted_period_and_blocks_export(self):
        self.page.period_start.setText("2025-12-31")
        self.page.period_end.setText("2025-01-01")
        result = self.page.run_review()
        self.assertTrue(any("дата начала позже" in x for x in result["errors"]))
        with patch("src.uk728.app.export_docx") as exporter:
            self.page.export("docx")
        exporter.assert_not_called()
        self.assertIn("заблокирован", self.page.review_status.text())

    def test_cannot_export_without_review_or_after_new_edit(self):
        with patch("src.uk728.app.export_xlsx") as exporter:
            self.page.export("xlsx")
        exporter.assert_not_called()
        self.assertIn("Сначала", self.page.review_status.text())
        self.page.run_review()
        self.page.notes.setPlainText("Новое примечание после проверки")
        self.assertTrue(self.page._review_dirty)
        with patch("src.uk728.app.export_xlsx") as exporter:
            self.page.export("xlsx")
        exporter.assert_not_called()
        self.assertTrue(self.page._review_dirty)

    def test_external_database_change_invalidates_export(self):
        self.page.run_review()
        db.execute("UPDATE reports SET notes=? WHERE id=?", ("Изменено извне", self.report_id))
        with patch("src.uk728.app.export_xlsx") as exporter:
            self.page.export("xlsx")
        exporter.assert_not_called()
        self.assertTrue(self.page._review_dirty)
        self.assertIn("повторите", self.page.review_status.text().lower())

    def test_export_uses_existing_generator_after_review_and_confirmation(self):
        self.page.run_review()
        expected_path = Path(self.tmp.name) / "official_728.xlsx"
        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.Yes) as confirm,
            patch.object(QMessageBox, "exec", return_value=0),
            patch("src.uk728.app.make_backup", return_value=Path(self.tmp.name) / "backup.db"),
            patch("src.uk728.app.export_xlsx", return_value=expected_path) as exporter,
        ):
            self.page.export("xlsx")
        confirm.assert_called_once()
        exporter.assert_called_once_with(self.report_id)
        self.assertFalse(self.page._review_dirty)

    def test_financial_and_claim_row_ids_survive_repeated_autosave(self):
        before = db.load_report_bundle(self.report_id)
        financial_ids = {row["payer_type"]: row["id"] for row in before["financials"]}
        claim_id = before["claims"]["id"]
        self.page.finance_table.set_value(0, 3, "875")
        self.page.flush_autosave()
        self.page.save(silent=True, refresh_after=False)
        after = db.load_report_bundle(self.report_id)
        self.assertEqual(financial_ids, {row["payer_type"]: row["id"] for row in after["financials"]})
        self.assertEqual(claim_id, after["claims"]["id"])
        self.assertEqual(after["financials"][0]["accrued"], 875)

    def test_duplicate_template_application_skips_existing_work(self):
        wid = db.execute(
            "INSERT INTO work_items(name,category,unit,default_price) VALUES(?,?,?,?)",
            ("Уборка территории", "Содержание", "м²", 5),
        )
        tid = db.execute("INSERT INTO templates(name,category) VALUES(?,?)",
                         ("Тестовый шаблон", "Содержание"))
        db.execute(
            """INSERT INTO template_items
               (template_id,work_item_id,name,unit,unit_price,plan_qty)
               VALUES(?,?,?,?,?,?)""",
            (tid, wid, "Уборка территории", "м²", 5, 1200),
        )
        self.page.reload_template_combo()
        combo = self.page.content_template_combo
        combo.setCurrentIndex(combo.findData(tid))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.page.apply_template("Содержание", combo)
            first = self.page.content_table.rowCount()
            self.page.apply_template("Содержание", combo)
        self.assertEqual(first, 1)
        self.assertEqual(self.page.content_table.rowCount(), 1)
        self.page.flush_autosave()
        self.assertEqual(len(db.load_report_bundle(self.report_id)["content"]), 1)

    def test_select_and_remove_report_rows_with_confirmation(self):
        self.page.add_content_row({
            "name": "Ручная работа", "unit_price": 10, "plan_qty": 2,
            "actual_qty": 2,
        })
        self.assertEqual(self.page.content_table.rowCount(), 1)
        self.page.content_table.selectRow(0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.page._delete_selected_report_rows(self.page.content_table)
        self.assertEqual(self.page.content_table.rowCount(), 0)
        self.assertTrue(self.page._dirty_report)

    def test_tab_navigation_undoes_failed_save(self):
        self.page.report_tabs.setCurrentIndex(0)
        self.page.period_start.setText("2025-02-01")
        self.assertTrue(self.page._dirty_report)
        with patch.object(self.page, "flush_autosave", side_effect=OSError("read only")):
            self.page.report_tabs.setCurrentIndex(1)
        self.assertEqual(self.page.report_tabs.currentIndex(), 0)
        self.assertEqual(self.page.period_start.text(), "2025-02-01")
        # Avoid teardown trying to commit while a test simulates a failed write.
        self.page._dirty_report = False
        self.page._autosave_timer.stop()


class ReportReviewUnitTests(TestCase):
    def test_report_bundle_fingerprint_and_structural_validation(self):
        bundle = {
            "report": {"id": 4, "year": 2025, "period_start": "2025-01-01",
                       "period_end": "2025-12-31", "show_period_note": 1,
                       "management_cost": 100},
            "company": {"name": "УК"},
            "house": {"address": "Дом № 1"},
            "content": [{"name": "Содержание"}],
            "repair": [],
            "management": [{"name": "Управление", "cost": 120}],
            "financials": [{"payer_type": "Собственники"}],
        }
        result = inspect_report(bundle)
        self.assertFalse(result["errors"])
        self.assertTrue(any("Итоговая сумма" in x for x in result["warnings"]))
        first_hash = bundle_fingerprint(bundle)
        bundle["report"]["management_cost"] = 120
        self.assertNotEqual(first_hash, bundle_fingerprint(bundle))
        self.assertFalse(any("Итоговая сумма" in x for x in inspect_report(bundle)["warnings"]))


if __name__ == "__main__":
    import unittest
    unittest.main()
