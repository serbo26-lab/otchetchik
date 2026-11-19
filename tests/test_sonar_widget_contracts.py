"""Regression checks for the real widget-contract failures reported by Sonar.

All data below are artificial; no production database or addresses are used.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem

from src.uk728 import db
from src.uk728.app import BaseTab, MainWindow, MassPreviewTable


class SonarWidgetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        directory_patch = patch(
            "src.uk728.paths.app_dir", return_value=Path(temp.name)
        )
        directory_patch.start()
        self.addCleanup(directory_patch.stop)
        db.init_db()

    def test_work_directory_saves_before_assistant_refresh(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.navigate(3)
        self.assertEqual(window.tabs.currentIndex(), 3)
        directory = window.works_tab
        directory.add_row()
        index = directory.table.rowCount() - 1
        directory.table.set_value(index, 3, "Учебная услуга")
        directory.table.set_value(index, 4, "Содержание")

        # MainWindow used to call WorksTab.save(refresh_after=False), which
        # raised TypeError before this fix, blocking Assistant refresh.
        self.assertTrue(window.save_before_assistant_sync())
        row = db.row("SELECT name,category FROM work_items WHERE name=?",
                     ("Учебная услуга",))
        self.assertIsNotNone(row)
        self.assertEqual(row["category"], "Содержание")
        self.assertTrue(window.save_before_assistant_sync())
        rows = db.rows("SELECT id FROM work_items WHERE name=?", ("Учебная услуга",))
        self.assertEqual(len(rows), 1)

    def test_invalid_work_entry_stops_assistant_refresh_without_data_loss(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.navigate(3)
        directory = window.works_tab
        directory.add_row()
        index = directory.table.rowCount() - 1
        directory.table.set_value(index, 3, "Учебная работа без раздела")
        directory.table.set_value(index, 4, "")
        with patch.object(QMessageBox, "warning") as warning:
            self.assertFalse(window.save_before_assistant_sync())
        self.assertTrue(warning.called)
        self.assertIsNone(db.row(
            "SELECT id FROM work_items WHERE name=?",
            ("Учебная работа без раздела",)
        ))

    def test_preview_rejects_delete_even_when_called_as_parent_method(self):
        table = MassPreviewTable(1, 2)
        self.addCleanup(table.close)
        table.setItem(0, 0, QTableWidgetItem("Вымышленная строка"))
        table.selectRow(0)
        self.assertIs(table.delete_selected_rows(confirmed=True), False)
        self.assertIs(table.delete_selected_rows(), False)
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "Вымышленная строка")

    def test_base_tab_keeps_intentional_noop_hook_signature(self):
        tab = BaseTab()
        self.addCleanup(tab.close)
        self.assertIsNone(tab.save(refresh_after=False))
        self.assertIsNone(tab.refresh())


if __name__ == "__main__":
    unittest.main()
