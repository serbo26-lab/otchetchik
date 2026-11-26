"""State-based guidance on real widgets, using only fictional local records."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.ui_workflow import recommend_next


class AssistantGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        folder = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(folder.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_company_and_house_guidance_follows_record_state(self):
        company = self.window.company_tab
        self.assertIn("Добавить организацию", recommend_next(company).text)
        company.add_company()
        self.assertEqual(recommend_next(company).destination, 1)
        company.notes.setPlainText("Synthetic change")
        self.assertIsNone(recommend_next(company).destination)
        self.assertIn("Сохранить организацию", recommend_next(company).text)
        company.save()
        self.window.navigate(1)
        houses = self.window.houses_tab
        self.assertIn("Добавить дом", recommend_next(houses).text)
        houses.add_house()
        self.assertEqual(recommend_next(houses).destination, 6)
        houses.notes.setPlainText("Synthetic house change")
        self.window.refresh_context()
        self.assertTrue(self.window.page_header.next_button.isHidden())
        self.assertIn("Сохранить дом", self.window.page_header.recommendation.text())

    def test_report_guidance_follows_section_and_review(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO",))
        hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                         (cid, "Synthetic house"))
        self.window.navigate(6)
        page = self.window.reports_tab
        page.reload_filters()
        self.assertIn("Создать отчет", recommend_next(page).text)
        db.create_report(cid, hid, 2026)
        page.refresh()
        page.report_tabs.setCurrentIndex(1)
        self.assertIn("Добавьте работу", recommend_next(page).text)
        page.add_blank_report_row("Содержание")
        self.assertIn("текущему ремонту", recommend_next(page).text)
        page.content_table.set_value(0, 2, "Synthetic work")
        page.report_tabs.setCurrentIndex(5)
        self.assertIn("Проверить заполнение", recommend_next(page).text)
        page.run_review()
        hint = recommend_next(page).text
        self.assertTrue("формат выгрузки" in hint or "Исправьте ошибки" in hint)

    def test_mass_and_backup_guidance_reflect_blocking_state(self):
        mass = self.window.mass_tab
        self.assertEqual(recommend_next(mass).text, mass.preview_button.toolTip())
        backups = self.window.backups_tab
        self.assertIn("Создайте копию", recommend_next(backups).text)
        backups.backup_now()
        backups.archive_list.setCurrentRow(0)
        self.assertIn("Проверить выбранную", recommend_next(backups).text)


if __name__ == "__main__":
    unittest.main()
