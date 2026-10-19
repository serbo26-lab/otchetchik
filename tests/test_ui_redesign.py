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
        self.window.set_scale(125)
        self.assertEqual(self.window._scale, 125)
        self.assertEqual(self.window.sidebar.width(), 306)
        self.window.switch_theme()
        self.window.set_scale(100)

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


if __name__ == "__main__":
    unittest.main()
