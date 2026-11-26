"""Stage E: guided reports, review gating, row safety and unchanged exporters."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.reports_ui import AddFromReferenceDialog
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

    def test_export_controls_follow_review_and_clear_stale_results(self):
        page = self.page
        self.assertTrue(all(not button.isEnabled() for button in page.export_buttons.values()))
        page.run_review()
        self.assertTrue(all(button.isEnabled() for button in page.export_buttons.values()))
        self.assertTrue(page.review_text.toPlainText())
        page.notes.setPlainText("Synthetic pending review change")
        self.assertEqual(page.review_text.toPlainText(), "")
        self.assertTrue(all(not button.isEnabled() for button in page.export_buttons.values()))
        page.period_start.setText("2025-12-31")
        page.period_end.setText("2025-01-01")
        result = page.run_review()
        self.assertTrue(result["errors"])
        self.assertTrue(all(not button.isEnabled() for button in page.export_buttons.values()))
        page.period_start.setText("2025-01-01")
        page.period_end.setText("2025-12-31")
        self.assertFalse(page.run_review()["errors"])
        self.assertTrue(all(button.isEnabled() for button in page.export_buttons.values()))

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
        with patch("src.uk728.reports_ui.export_docx") as exporter:
            self.page.export("docx")
        exporter.assert_not_called()
        self.assertIn("заблокирован", self.page.review_status.text())

    def test_cannot_export_without_review_or_after_new_edit(self):
        with patch("src.uk728.reports_ui.export_xlsx") as exporter:
            self.page.export("xlsx")
        exporter.assert_not_called()
        self.assertIn("Сначала", self.page.review_status.text())
        self.page.run_review()
        self.page.notes.setPlainText("Новое примечание после проверки")
        self.assertTrue(self.page._review_dirty)
        with patch("src.uk728.reports_ui.export_xlsx") as exporter:
            self.page.export("xlsx")
        exporter.assert_not_called()
        self.assertTrue(self.page._review_dirty)

    def test_external_database_change_invalidates_export(self):
        self.page.run_review()
        db.execute("UPDATE reports SET notes=? WHERE id=?", ("Изменено извне", self.report_id))
        with patch("src.uk728.reports_ui.export_xlsx") as exporter:
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
            patch("src.uk728.reports_ui.make_backup", return_value=Path(self.tmp.name) / "backup.db"),
            patch("src.uk728.reports_ui.export_xlsx", return_value=expected_path) as exporter,
        ):
            self.page.export("xlsx")
        confirm.assert_called_once()
        exporter.assert_called_once_with(self.report_id, bundle=db.load_report_bundle(self.report_id))
        self.assertFalse(self.page._review_dirty)

    def test_change_during_warning_confirmation_blocks_export(self):
        self.page.run_review()

        def change_and_confirm(*args):
            db.execute("UPDATE reports SET notes=? WHERE id=?", ("Synthetic edit", self.report_id))
            return QMessageBox.Yes

        with (
            patch.object(QMessageBox, "question", side_effect=change_and_confirm),
            patch("src.uk728.reports_ui.export_xlsx") as exporter,
        ):
            self.page.export("xlsx")
        exporter.assert_not_called()
        self.assertTrue(self.page._review_dirty)

    def test_all_formats_use_same_reviewed_snapshot(self):
        self.page.run_review()
        reviewed = db.load_report_bundle(self.report_id)
        target = Path(self.tmp.name) / "synthetic.xlsx"

        def first_export(report_id, *, bundle):
            self.assertEqual(report_id, self.report_id)
            self.assertEqual(bundle, reviewed)
            db.execute("UPDATE reports SET notes=? WHERE id=?", ("Synthetic later edit", report_id))
            return target

        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            patch.object(QMessageBox, "exec", return_value=0),
            patch("src.uk728.reports_ui.export_xlsx", side_effect=first_export) as xlsx,
            patch("src.uk728.reports_ui.export_docx", return_value=target) as docx,
            patch("src.uk728.reports_ui.export_pdf", return_value=target) as pdf,
        ):
            self.page.export("all")
        for exporter in (xlsx, docx, pdf):
            exporter.assert_called_once_with(self.report_id, bundle=reviewed)
        self.assertNotEqual(db.load_report_bundle(self.report_id), reviewed)

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

    def test_financial_validation_rolls_back_earlier_section_writes(self):
        before = db.load_report_bundle(self.report_id)
        self.page.notes.setPlainText("Synthetic pending change")
        self.page.add_content_row({
            "name": "Synthetic work", "unit": "шт.", "unit_price": 3,
            "plan_qty": 2, "actual_qty": 1,
        })
        table = self.page.finance_table
        original_payer = table.value(1, 1)
        table.set_value(1, 1, table.value(0, 1))
        try:
            with self.assertRaisesRegex(ValueError, "Повторяется вид плательщика"):
                self.page.save(silent=True, refresh_after=False)
            self.assertEqual(db.load_report_bundle(self.report_id), before)
            self.assertEqual(self.page.content_table.value(0, 0), "")
            self.assertTrue(self.page._dirty_report)
        finally:
            table.set_value(1, 1, original_payer)
            self.page.flush_autosave()
        saved = db.load_report_bundle(self.report_id)
        self.assertEqual(saved["report"]["notes"], "Synthetic pending change")
        self.assertEqual(len(saved["content"]), 1)
        self.assertEqual(int(self.page.content_table.value(0, 0)), saved["content"][0]["id"])

    def test_reference_search_and_empty_selection(self):
        first = db.execute("INSERT INTO work_items(name,category,group_name) VALUES(?,?,?)",
                           ("Synthetic cleaning", "Содержание", "Synthetic group"))
        db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                   ("Synthetic repair", "Текущий ремонт"))
        dialog = AddFromReferenceDialog("Содержание")
        self.addCleanup(dialog.close)
        self.assertGreaterEqual(dialog.combo.findData(first), 0)
        self.assertNotIn("Synthetic repair", [dialog.combo.itemText(i) for i in range(dialog.combo.count())])
        dialog.search.setText("not found")
        self.assertFalse(dialog.add_button.isEnabled())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        dialog.search.setText("synthetic group")
        self.assertEqual(dialog.combo.count(), 1)
        self.assertTrue(dialog.add_button.isEnabled())
        dialog.add_button.click()
        self.assertEqual(dialog.selected_id, first)

    def test_reference_archived_during_selection_is_not_added(self):
        wid = db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                         ("Synthetic work", "Содержание"))

        def archive_and_accept(dialog):
            dialog.selected_id = wid
            db.execute("UPDATE work_items SET is_active=0 WHERE id=?", (wid,))
            return QDialog.Accepted

        with patch.object(AddFromReferenceDialog, "exec", new=archive_and_accept), \
             patch.object(QMessageBox, "warning") as warning:
            self.page.add_from_reference("Содержание")
        warning.assert_called_once()
        self.assertEqual(self.page.content_table.rowCount(), 0)

    def test_template_action_requires_available_template_and_rechecks_section(self):
        db.execute("DELETE FROM template_items")
        db.execute("DELETE FROM templates")
        self.page.reload_template_combo()
        combo = self.page.content_template_combo
        button = self.page._template_apply_buttons[combo]
        self.assertFalse(button.isEnabled())
        tid = db.execute("INSERT INTO templates(name,category) VALUES(?,?)",
                         ("Synthetic template", "Содержание"))
        self.page.reload_template_combo()
        combo.setCurrentIndex(combo.findData(tid))
        self.assertTrue(button.isEnabled())

        def change_section(*args):
            db.execute("UPDATE templates SET category=? WHERE id=?", ("Текущий ремонт", tid))
            return QMessageBox.Yes

        with patch.object(QMessageBox, "question", side_effect=change_section), \
             patch.object(QMessageBox, "warning") as warning:
            button.click()
        warning.assert_called_once()
        self.assertFalse(button.isEnabled())
        self.assertEqual(self.page.content_table.rowCount(), 0)

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

    def test_clearing_saved_name_preserves_row_and_pending_edits(self):
        self.page.add_content_row({
            "name": "Учебная услуга", "unit_price": 10, "plan_qty": 2,
            "actual_qty": 1,
        })
        self.page.flush_autosave()
        table = self.page.content_table
        record_id = int(table.value(0, 0))
        table.set_value(0, 2, "")
        table.set_value(0, 5, "3")
        try:
            with self.assertRaisesRegex(ValueError, "наименование"):
                self.page.flush_autosave()
            stored = db.row("SELECT * FROM report_content_items WHERE id=?", (record_id,))
            self.assertEqual(stored["name"], "Учебная услуга")
            self.assertEqual(stored["plan_qty"], 2)
            self.assertTrue(self.page._dirty_report)
            self.assertEqual(table.value(0, 2), "")
            self.assertEqual(float(table.value(0, 5).replace(",", ".")), 3)
        finally:
            # Keep the test independent of modal close handling.
            table.set_value(0, 2, "Учебная услуга")
            self.page.flush_autosave()
        stored = db.row("SELECT * FROM report_content_items WHERE id=?", (record_id,))
        self.assertEqual(stored["plan_qty"], 3)
        self.assertEqual(stored["name"], "Учебная услуга")

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
