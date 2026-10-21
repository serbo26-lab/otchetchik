"""V1.6.8: integrated regression checks for directory and report workflows."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.paths import make_backup


class WorkflowV168Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def seed(self):
        company = db.execute("INSERT INTO companies(name) VALUES(?)", ("УО для V168",))
        house = db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                           (company, "Дом с тестами", 1400))
        work = db.execute("INSERT INTO work_items(name,category,unit,default_price) VALUES(?,?,?,?)",
                          ("Уборка подъезда", "Содержание", "м²", 3.5))
        report = db.create_report(company, house, 2026)
        return company, house, work, report

    def open_report(self, company, house):
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(company))
        page.house_filter.setCurrentIndex(page.house_filter.findData(house))
        self.assertIsNotNone(page.current_id)
        return page

    def test_search_hides_rows_without_losing_database_records(self):
        first = db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                           ("Осмотр кровли", "Содержание"))
        second = db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                            ("Промывка системы", "Содержание"))
        page = self.window.works_tab
        page.reload()
        page.work_search.setText("кровли")
        self.assertEqual(page.work_match_count.text(), f"Показано: 1 из {page.table.rowCount()}")
        hidden = [page.table.isRowHidden(i) for i in range(page.table.rowCount())]
        self.assertIn(True, hidden)
        self.assertIn(False, hidden)
        page.save()
        self.assertEqual(db.row("SELECT is_active FROM work_items WHERE id=?", (first,))["is_active"], 1)
        self.assertEqual(db.row("SELECT is_active FROM work_items WHERE id=?", (second,))["is_active"], 1)

    def test_add_work_clears_search_and_shows_new_row(self):
        db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                   ("Кровля", "Содержание"))
        page = self.window.works_tab
        page.reload()
        page.work_search.setText("кровля")
        page.add_row()
        self.assertEqual(page.work_search.text(), "")
        self.assertFalse(page.table.isRowHidden(page.table.rowCount() - 1))
        self.assertEqual(page.table.value(page.table.rowCount() - 1, 3), "Новая работа")
        self.assertTrue(page.has_unsaved_changes())

    def test_legacy_work_without_section_does_not_block_unchanged_navigation(self):
        wid = db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                         ("Старые данные", ""))
        page = self.window.works_tab
        page.reload()
        with patch("src.uk728.app.QMessageBox.warning") as warning:
            self.assertTrue(page.save())
            self.window.navigate(3)
            self.window.navigate(0)
        self.assertFalse(warning.called)
        self.assertEqual(db.row("SELECT category FROM work_items WHERE id=?", (wid,))["category"], "")

    def test_invalid_new_work_is_not_silently_saved(self):
        page = self.window.works_tab
        page.add_row()
        with patch("src.uk728.app.QMessageBox.warning") as warning:
            self.assertIs(page.save(), False)
            self.assertTrue(warning.called)
        page.table.set_value(page.table.rowCount() - 1, 4, "Содержание")
        self.assertIsNone(page.save())
        self.assertIsNotNone(db.row(
            "SELECT id FROM work_items WHERE name=?", ("Новая работа",)
        ))

    def test_directory_header_reports_pending_changes(self):
        page = self.window.works_tab
        self.window.navigate(3)
        page.add_row()
        self.window.refresh_context()
        self.assertEqual(self.window.page_header.save_status.property("state"), "pending")
        page.table.set_value(page.table.rowCount() - 1, 4, "Содержание")
        page.save()
        self.window.refresh_context()
        self.assertEqual(self.window.page_header.save_status.property("state"), "saved")

    def test_report_context_menu_uses_safe_row_lifecycle(self):
        company, house, _work, report = self.seed()
        page = self.open_report(company, house)
        self.assertIsNotNone(page.content_table.context_add_handler)
        self.assertIsNotNone(page.content_table.context_delete_handler)
        page.content_table.context_add_handler()
        page.content_table.set_value(0, 2, "Контрольная уборка")
        page.flush_autosave()
        record = db.row("SELECT id, source FROM report_content_items WHERE report_id=?", (report,))
        self.assertIsNotNone(record)
        self.assertEqual(record["source"], "ручной ввод/редактирование")
        page.content_table.selectRow(0)
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes):
            page.content_table.context_delete_handler()
        page.flush_autosave()
        self.assertEqual(len(db.rows(
            "SELECT id FROM report_content_items WHERE report_id=?", (report,)
        )), 0)

    def test_empty_report_state_hides_previous_data(self):
        c1, h1, _work, _report = self.seed()
        page = self.open_report(c1, h1)
        self.assertFalse(page.report_tabs.isHidden())
        page.company_filter.setCurrentIndex(0)
        self.assertIsNone(page.current_id)
        self.assertTrue(page.report_tabs.isHidden())
        self.assertTrue(page.report_empty_hint.isVisibleTo(page) or page.report_empty_hint.isHidden() is False)
        self.assertFalse(page.save_button.isEnabled())

    def test_fixed_finance_rows_not_mutated_by_context_menu(self):
        company, house, _work, _report = self.seed()
        page = self.open_report(company, house)
        before = page.finance_table.rowCount()
        page.finance_table.context_add_handler()
        page.finance_table.context_delete_handler()
        self.assertEqual(page.finance_table.rowCount(), before)

    def test_category_requires_section_for_changed_record(self):
        page = self.window.categories_tab
        page.add_category()
        page.name.setText("Категория с проверкой")
        with patch("src.uk728.app.QMessageBox.warning") as warning:
            self.assertIs(page.save(refresh_after=False), False)
            self.assertTrue(warning.called)
        page.section.setCurrentText("Управление")
        page.save(refresh_after=False)
        self.assertEqual(db.row(
            "SELECT section FROM work_categories WHERE id=?", (page.current_id,)
        )["section"], "Управление")

    def test_mass_backup_exists_before_any_new_records(self):
        cid, hid, wid, _rid = self.seed()
        page = self.window.mass_tab
        page.reload()
        page.company.setCurrentIndex(page.company.findData(cid))
        page.work.setCurrentIndex(page.work.findData(wid))
        page._check_all(True)
        page.rate.setText("10")
        page.year.setValue(2026)
        page.preview()
        self.assertTrue(page.apply_button.isEnabled())
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes):
            page.apply()
        self.assertEqual(len(db.rows("SELECT id FROM report_content_items")), 1)
        paths = list((Path(self.tmp.name) / "data" / "backups").glob("*before_mass_apply*.db"))
        self.assertEqual(len(paths), 1)
        with sqlite3.connect(paths[0]) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM report_content_items").fetchone()[0], 0)

    def test_mass_cannot_modify_without_prebackup(self):
        cid, hid, wid, _rid = self.seed()
        page = self.window.mass_tab
        page.reload()
        page.company.setCurrentIndex(page.company.findData(cid))
        page.work.setCurrentIndex(page.work.findData(wid))
        page._check_all(True)
        page.rate.setText("10")
        page.preview()
        self.assertTrue(page.apply_button.isEnabled())
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("src.uk728.app.make_backup", side_effect=OSError("no disk")):
            page.apply()
        self.assertEqual(len(db.rows("SELECT id FROM report_content_items")), 0)
        self.assertIn("Запись не выполнена", page.status_label.text())

    def test_next_action_skips_optional_templates(self):
        self.seed()
        page = self.window.works_tab
        page.reload()
        self.window._update_next_action(page)
        self.assertEqual(self.window.page_header.next_button.text(), "Далее: отчеты →")


if __name__ == "__main__":
    unittest.main()
