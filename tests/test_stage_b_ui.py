"""Stage B: semantic layout, contextual guidance and shared UI tests."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton

from src.uk728 import db
from src.uk728.app import MainWindow, CopyPasteTable
from src.uk728.ui_components import ActionPanel, ContentCard
from src.uk728.ui_shell import PAGES


class CommonUxTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_every_page_has_semantic_groups(self):
        for page in [self.window.tabs.widget(i) for i in range(8)]:
            groups = page.findChildren(ActionPanel)
            self.assertGreater(len(groups), 0, type(page).__name__)
            self.assertTrue(all(g.objectName() == "ActionPanel" for g in groups))
            self.assertTrue(all(g.findChildren(QPushButton) for g in groups))
            for group in groups:
                if group.property("purpose") == "danger":
                    self.assertTrue(all(
                        b.objectName() == "DangerAction" for b in group.findChildren(QPushButton)
                    ))
                if any(b.objectName() == "DangerAction" for b in group.findChildren(QPushButton)):
                    self.assertFalse(any(
                        b.objectName() == "PrimaryAction" for b in group.findChildren(QPushButton)
                    ))

    def test_actual_groups_separate_creation_and_deletion(self):
        for page in (
            self.window.company_tab, self.window.houses_tab,
            self.window.categories_tab, self.window.templates_tab,
            self.window.reports_tab,
        ):
            purposes = [p.property("purpose") for p in page.findChildren(ActionPanel)]
            self.assertIn("danger", purposes)
            self.assertIn("normal", purposes)
        self.assertGreaterEqual(len(self.window.reports_tab.findChildren(ContentCard)), 4)

    def test_context_describes_current_page_not_unrelated_reports(self):
        # A default report selector should not contaminate the company context.
        self.window.refresh_context()
        self.assertIn("Управляющая организация", self.window.page_header.context.text())
        self.assertNotIn("Год отчета", self.window.page_header.context.text())
        self.window.company_tab.add_company()
        self.window.company_tab.name.setText("УК для контекста")
        self.window.navigate(1)
        self.window.refresh_context()
        self.assertIn("УО:", self.window.page_header.context.text())
        self.assertNotIn("Отчетный год", self.window.page_header.context.text())
        self.window.navigate(5)
        self.window.refresh_context()
        self.assertIn("Массовое заполнение", self.window.page_header.context.text())
        self.assertIn("предпросмотр", self.window.page_header.guide.text().lower())
        self.window.navigate(6)
        self.window.refresh_context()
        self.assertIn("Отчетный год", self.window.page_header.context.text())

    def test_long_table_values_have_tooltips_after_edits(self):
        table = CopyPasteTable(1, 2)
        self.addCleanup(table.deleteLater)
        long_name = "Карачаево-Черкесская Республика, Зеленчукский район, ст. Сторожевая-2, дом 14А"
        table.set_value(0, 0, long_name)
        self.assertEqual(table.item(0, 0).toolTip(), long_name)
        table.item(0, 0).setText(long_name + " — уточнение")
        self.assertEqual(table.item(0, 0).toolTip(), long_name + " — уточнение")
        self.assertTrue(table.horizontalScrollMode() == table.ScrollPerPixel)

    def test_themes_consistently_style_cards_and_actions(self):
        self.window.switch_theme()
        dark = self.app.styleSheet()
        self.assertIn("QFrame#ActionPanel", dark)
        self.assertIn("QFrame#ContentCard", dark)
        self.assertIn("QPushButton#DangerAction", dark)
        self.window.switch_theme()
        light = self.app.styleSheet()
        self.assertNotEqual(dark, light)
        self.assertEqual(self.window._scale, 100)
        self.assertTrue(self.window.sidebar.scale_combo.isHidden())


if __name__ == "__main__":
    import unittest
    unittest.main()
