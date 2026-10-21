"""Regression tests for the V1.6.7 data-safety and workflow improvements."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import db
from src.uk728.app import CopyPasteTable, MainWindow
from src.uk728.paths import db_path, make_backup


class SafetyUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        fake = patch("src.uk728.paths.app_dir", return_value=Path(self.temp.name))
        fake.start()
        self.addCleanup(fake.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def create_house_and_report(self, *, year=2026):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Для проверки",))
        hid = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (cid, "ул. Проверочная, д. 1", 1200),
        )
        rid = db.create_report(cid, hid, year)
        return cid, hid, rid

    def test_consistent_backup_includes_committed_wal_transactions(self):
        with db.connect() as conn:
            conn.execute("INSERT INTO companies(name) VALUES(?)", ("Из журнала WAL",))
            conn.commit()
            snapshot = make_backup("wal_test")
            self.assertIsNotNone(snapshot)
            with sqlite3.connect(snapshot) as copied:
                names = [r[0] for r in copied.execute("SELECT name FROM companies")]
                self.assertIn("Из журнала WAL", names)
                self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertFalse(snapshot.with_suffix(".tmp").exists())

    def test_report_is_backed_up_before_deletion(self):
        cid, hid, rid = self.create_house_and_report()
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        self.assertEqual(page.current_id, rid)
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes):
            page.delete_report()
        self.assertIsNone(db.row("SELECT id FROM reports WHERE id=?", (rid,)))
        copies = list((Path(self.temp.name) / "data" / "backups").glob("*before_delete_report*.db"))
        self.assertEqual(len(copies), 1)
        with sqlite3.connect(copies[0]) as copied:
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM reports WHERE id=?", (rid,)).fetchone()[0], 1)

    def test_failed_backup_prevents_report_deletion(self):
        cid, hid, rid = self.create_house_and_report()
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("src.uk728.app.make_backup", side_effect=OSError("disk full")), \
             patch("src.uk728.app.QMessageBox.warning"):
            page.delete_report()
        self.assertIsNotNone(db.row("SELECT id FROM reports WHERE id=?", (rid,)))

    def test_company_and_house_deletion_block_orphaned_reports(self):
        cid, hid, rid = self.create_house_and_report()
        self.window.company_tab.refresh()
        house = self.window.houses_tab
        house.reload()
        house.company_filter.setCurrentIndex(house.company_filter.findData(cid))
        self.assertEqual(house.current_id, hid)
        with patch("src.uk728.app.QMessageBox.warning") as warning:
            house.delete_house()
            self.assertTrue(warning.called)
            self.assertIsNotNone(db.row("SELECT id FROM houses WHERE id=?", (hid,)))
            self.window.company_tab.delete_company()
            self.assertGreaterEqual(warning.call_count, 2)
        self.assertIsNotNone(db.row("SELECT id FROM reports WHERE id=?", (rid,)))

    def test_table_deletion_always_needs_confirmation(self):
        table = CopyPasteTable(2, 2)
        self.addCleanup(table.deleteLater)
        table.set_value(0, 0, "Первая")
        table.set_value(1, 0, "Вторая")
        table.selectRow(0)
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.No):
            self.assertFalse(table.delete_selected_rows())
        self.assertEqual(table.rowCount(), 2)
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes):
            self.assertTrue(table.delete_selected_rows())
        self.assertEqual(table.rowCount(), 1)

    def test_reports_do_not_show_mixed_organizations_without_selection(self):
        first = db.execute("INSERT INTO companies(name) VALUES('УО А')")
        second = db.execute("INSERT INTO companies(name) VALUES('УО Б')")
        ha = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (first, "Дом А"))
        hb = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (second, "Дом Б"))
        rida = db.create_report(first, ha, 2026)
        db.create_report(second, hb, 2026)
        page = self.window.reports_tab
        page.reload_filters()
        self.assertIsNone(page.company_filter.currentData())
        self.assertEqual(page.list.count(), 0)
        self.assertFalse(page.create_report_btn.isEnabled())
        page.company_filter.setCurrentIndex(page.company_filter.findData(first))
        page.house_filter.setCurrentIndex(page.house_filter.findData(ha))
        self.assertEqual(page.list.count(), 1)
        self.assertEqual(page.current_id, rida)

    def test_work_section_is_required(self):
        page = self.window.works_tab
        previous = len(db.rows("SELECT id FROM work_items WHERE is_active=1"))
        page.add_row()
        with patch("src.uk728.app.QMessageBox.warning") as warning:
            self.assertIs(page.save(), False)
            self.assertTrue(warning.called)
        self.assertEqual(len(db.rows("SELECT id FROM work_items WHERE is_active=1")), previous)

    def test_template_cannot_be_reclassified_with_foreign_section(self):
        wid = db.execute(
            "INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
            ("Очистка кровли", "Содержание", "м²"),
        )
        page = self.window.templates_tab
        page.add_template()
        tid = page.current_id
        page.reload_work_combo()
        page.work_combo.setCurrentIndex(page.work_combo.findData(wid))
        page.add_work_to_template()
        self.assertEqual(page.table.rowCount(), 1)
        page.category.setCurrentText("Текущий ремонт")
        with patch("src.uk728.app.QMessageBox.warning") as warning:
            self.assertIs(page.save(refresh_after=False), False)
            self.assertTrue(warning.called)
        self.assertEqual(db.row("SELECT category FROM templates WHERE id=?", (tid,))["category"], "Содержание")

    def test_selected_organization_follows_navigation(self):
        c1 = db.execute("INSERT INTO companies(name) VALUES('УО Первая')")
        c2 = db.execute("INSERT INTO companies(name) VALUES('УО Вторая')")
        self.window.company_tab.refresh()
        select = next(i for i in range(self.window.company_tab.list.count())
                      if self.window.company_tab.list.item(i).data(0x0100) == c2)
        self.window.company_tab.list.setCurrentRow(select)
        self.window.navigate(1)
        self.assertEqual(self.window.houses_tab.company_filter.currentData(), c2)
        self.window.navigate(6)
        self.assertEqual(self.window.reports_tab.company_filter.currentData(), c2)
        self.window.navigate(5)
        self.assertEqual(self.window.mass_tab.company.currentData(), c2)
        self.window.refresh_context()
        self.assertIn("УО Вторая", self.window.sidebar.organization_caption.text())
        self.assertNotEqual(c1, c2)

    def test_next_button_moves_to_relevant_page(self):
        self.window.company_tab.add_company()
        self.window.refresh_context()
        self.assertEqual(self.window.page_header.next_button.text(), "Далее: дома →")
        self.window.page_header.next_button.click()
        self.assertEqual(self.window.tabs.currentIndex(), 1)


if __name__ == "__main__":
    unittest.main()
