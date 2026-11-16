"""Regression: empty report states stay compact and live editor opens."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSizePolicy
from src.uk728 import db
from src.uk728.app import MainWindow


class ReportEmptyStateV1613Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(temp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.window = MainWindow()
        self.window.resize(1400, 900)
        self.window.show()
        self.addCleanup(self.window.close)
        self.window.navigate(6)
        self.app.processEvents()

    def test_empty_card_does_not_expand_with_report_editor_height(self):
        page = self.window.reports_tab
        self.assertIsNone(page.current_id)
        self.assertTrue(page.report_empty_card.isVisibleTo(self.window))
        self.assertTrue(page.report_empty_fill.isVisibleTo(self.window))
        self.assertFalse(page.report_tabs.isVisibleTo(self.window))
        self.assertEqual(
            page.report_empty_card.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Maximum,
        )
        self.assertLess(page.report_empty_card.height(), 175)
        self.assertGreater(page.report_empty_fill.height(), 140)
        self.assertIn("Сначала выберите", page.report_empty_hint.text())

    def test_empty_card_stays_compact_after_resize(self):
        self.window.resize(1100, 700)
        self.app.processEvents()
        page = self.window.reports_tab
        self.assertLess(page.report_empty_card.height(), 175)
        self.assertTrue(page.report_empty_hint.isVisibleTo(self.window))
        self.window.resize(860, 520)
        self.app.processEvents()
        self.assertLess(page.report_empty_card.height(), 175)

    def test_selected_report_replaces_empty_hint_with_editor(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Тестовая",))
        hid = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (cid, "г. Образцовый, дом 9", 1200),
        )
        rid = db.create_report(cid, hid, 2026)
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        self.app.processEvents()
        self.assertEqual(page.current_id, rid)
        self.assertFalse(page.report_empty_fill.isVisibleTo(self.window))
        self.assertFalse(page.report_empty_card.isVisibleTo(self.window))
        self.assertTrue(page.report_tabs.isVisibleTo(self.window))
        self.assertTrue(page.save_button.isEnabled())

    def test_empty_card_text_updates_to_missing_house(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Пустая",))
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        self.app.processEvents()
        self.assertIn("Выберите дом", page.report_empty_hint.text())
        self.assertLess(page.report_empty_card.height(), 175)


if __name__ == "__main__":
    unittest.main()
