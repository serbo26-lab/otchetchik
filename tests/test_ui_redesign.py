"""Lightweight real-Qt smoke tests for the eight-page reporting workspace."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.ui_shell import PAGES


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.patch_dir = patch("src.uk728.paths.app_dir", return_value=Path(self.temp.name))
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_all_sections_have_navigation(self):
        self.assertEqual(self.window.tabs.count(), 8)
        self.assertEqual(self.window.sidebar.navigation.count(), len(PAGES))
        self.assertEqual(self.window.tabs.currentIndex(), 0)

    def test_switching_pages_preserves_selection(self):
        for index in (1, 3, 5, 6, 2, 7, 0):
            self.window.sidebar.navigation.setCurrentRow(index)
            self.app.processEvents()
            self.assertEqual(self.window.tabs.currentIndex(), index)
            self.assertEqual(self.window._last_tab, index)
        self.assertEqual(self.window.sidebar.navigation.currentRow(), 0)

    def test_change_theme_and_scale(self):
        old_theme = self.window._theme
        self.window.switch_theme()
        self.assertNotEqual(self.window._theme, old_theme)
        self.assertTrue(self.window.sidebar.scale_combo.isHidden())
        self.assertEqual(self.window.sidebar.scale_combo.count(), 1)
        self.window.set_scale(125)
        self.assertEqual(self.window._scale, 100)
        self.assertEqual(self.window.sidebar.width(), 245)
        self.window.switch_theme()

    def test_data_folder_is_external_and_survives_page_changes(self):
        database = Path(self.temp.name) / "data" / "reporting_728.db"
        self.assertTrue(database.exists())
        self.window.navigate(5)
        self.window.navigate(6)
        self.assertTrue(database.exists())

    def test_organization_edit_saved_on_navigation(self):
        self.window.company_tab.add_company()
        company_id = self.window.company_tab.current_id
        self.assertIsNotNone(company_id)
        self.window.company_tab.name.setText("Тестовая УО после переключения")
        self.window.navigate(1)
        row = db.row("SELECT name FROM companies WHERE id=?", (company_id,))
        self.assertEqual(row["name"], "Тестовая УО после переключения")

    def test_failed_page_save_stops_navigation(self):
        self.window.company_tab.current_id = 12345
        with patch.object(self.window.company_tab, "save", side_effect=RuntimeError("write failed")):
            self.window.navigate(5)
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertEqual(self.window.sidebar.navigation.currentRow(), 0)
        self.assertEqual(self.window.page_header.save_status.property("state"), "error")
        self.window.company_tab.current_id = None


    def test_bulk_line_identity_and_explicit_zero(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("УО",))
        hid = db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                         (cid, "Дом 1", 100))
        wid = db.execute(
            "INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
            ("Уборка", "Содержание", "м²"))
        rid = db.create_report(cid, hid, 2025)
        with db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO report_content_items
                   (report_id,work_item_id,name,unit,unit_price,plan_qty,
                   plan_cost,actual_qty,actual_cost,source,is_manual,position)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid,wid,"Уборка","м²",6.76,100,676,100,676,"массовое применение",0,1))
            line_id = int(cur.lastrowid)
            conn.commit()
        self.window.navigate(6)
        page = self.window.reports_tab
        page.year_filter.setValue(2025)
        self.assertEqual(page.current_id, rid)
        page.save(silent=True, refresh_after=False)
        original = db.row("SELECT * FROM report_content_items WHERE id=?", (line_id,))
        self.assertEqual(original["is_manual"], 0)
        self.assertEqual(original["source"], "массовое применение")
        self.assertEqual(page.content_table.value(0,0), str(line_id))

        page.content_table.set_value(0,7,"0")
        page.flush_autosave()
        changed = db.row("SELECT * FROM report_content_items WHERE id=?", (line_id,))
        self.assertEqual(changed["id"], line_id)
        self.assertEqual(changed["is_manual"], 1)
        self.assertEqual(changed["source"], "массовое применение")
        self.assertEqual(changed["actual_qty"], 0)
        self.assertEqual(changed["actual_cost"], 0)
        self.assertEqual(changed["plan_cost"], 676)
        self.assertEqual(self.window.page_header.save_status.property("state"), "saved")

    def test_failed_report_save_blocks_navigation(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("УО",))
        hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (cid,"Дом 2"))
        rid = db.create_report(cid,hid,2025)
        self.window.navigate(6)
        page = self.window.reports_tab
        page.year_filter.setValue(2025)
        self.assertEqual(page.current_id, rid)
        page.period_start.setText("2025-02-01")
        self.assertTrue(page._dirty_report)
        with patch("src.uk728.app.connect", side_effect=OSError("readonly database")):
            self.window.navigate(0)
        self.assertEqual(self.window.tabs.currentIndex(), 6)
        self.assertEqual(self.window.sidebar.navigation.currentRow(), 6)
        self.assertTrue(page._dirty_report)
        self.assertEqual(page.period_start.text(), "2025-02-01")
        self.assertEqual(self.window.page_header.save_status.property("state"), "error")
        # Allow tear-down without writing the deliberately unsaved mock value.
        page._dirty_report = False
        page._autosave_timer.stop()


    def test_switching_company_record_saves_previous_values(self):
        page = self.window.company_tab
        page.add_company()
        first_id = page.current_id
        other = db.execute("INSERT INTO companies(name) VALUES(?)", ("Другая УО",))
        page.refresh()
        page.name.setText("Первая УО — изменена")
        for i in range(page.list.count()):
            if page.list.item(i).data(256) == other:
                page.list.setCurrentRow(i)
                break
        self.assertEqual(page.current_id, other)
        self.assertEqual(
            db.row("SELECT name FROM companies WHERE id=?", (first_id,))["name"],
            "Первая УО — изменена"
        )

    def test_failed_company_save_rolls_back_selection(self):
        page = self.window.company_tab
        page.add_company()
        old_id = page.current_id
        other = db.execute("INSERT INTO companies(name) VALUES(?)", ("Другая УО",))
        page.refresh()
        page.name.setText("Не сохранено")
        with patch.object(page, "save", side_effect=OSError("readonly database")):
            for i in range(page.list.count()):
                if page.list.item(i).data(256) == other:
                    page.list.setCurrentRow(i)
                    break
        self.assertEqual(page.current_id, old_id)
        self.assertEqual(page.list.currentItem().data(256), old_id)
        self.assertEqual(page.name.text(), "Не сохранено")
        self.assertEqual(self.window.page_header.save_status.property("state"), "error")

    def test_repeated_category_save_does_not_duplicate_subcategory(self):
        page = self.window.categories_tab
        page.add_category()
        cid = page.current_id
        page.name.setText("Содержание")
        page.section.setCurrentText("Содержание")
        page.add_subcategory()
        page.save(refresh_after=False)
        page.save(refresh_after=False)
        self.assertEqual(
            db.row("SELECT COUNT(*) AS n FROM work_categories WHERE parent_id=?", (cid,))["n"], 1
        )


if __name__ == "__main__":
    unittest.main()
