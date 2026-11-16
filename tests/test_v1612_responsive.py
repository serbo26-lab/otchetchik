"""Real Qt layout regression checks for truncated dropdowns and small windows.

All fixtures are synthetic; report values and database operations are unchanged.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout, QScrollArea

from src.uk728 import db
from src.uk728.app import MainWindow


class ResponsiveWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.window = MainWindow()
        self.window.resize(1500, 930)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(self.window.close)

    def test_combobox_values_are_not_truncated_when_space_is_available(self):
        cat = self.window.categories_tab
        self.window.navigate(2)
        cat.section.setCurrentText("Текущий ремонт")
        cat.unit.setCurrentText("комплект")
        self.qt.processEvents()
        for field in (cat.section, cat.unit):
            self.assertEqual(
                field.sizePolicy().horizontalPolicy(),
                field.sizePolicy().Policy.Expanding,
            )
            self.assertGreater(field.width(), 240, field.currentText())
            self.assertGreater(
                field.width() - 40,
                field.fontMetrics().horizontalAdvance(field.currentText()),
                field.currentText(),
            )
            self.assertEqual(field.toolTip(), field.currentText())

        self.window.navigate(5)
        self.qt.processEvents()
        bulk = self.window.mass_tab
        for field in (bulk.company, bulk.work, bulk.mode):
            self.assertIsInstance(field, QComboBox)
            self.assertGreater(field.width(), 240, field.currentText())
            self.assertEqual(field.toolTip(), field.currentText())

    def test_minimum_window_is_resizable_and_vertical_scrolling_keeps_controls_reachable(self):
        w = self.window
        w.resize(860, 520)
        self.qt.processEvents()
        self.assertLessEqual(w.width(), 880)
        self.assertLessEqual(w.height(), 540)
        self.assertIsInstance(w.workspace_scroll, QScrollArea)
        self.assertTrue(w.workspace_scroll.widgetResizable())
        w.navigate(5)
        self.qt.processEvents()
        self.assertGreater(w.workspace_scroll.verticalScrollBar().maximum(), 0)
        w.workspace_scroll.ensureWidgetVisible(w.mass_tab.apply_button, 0, 0)
        self.qt.processEvents()
        self.assertTrue(w.mass_tab.apply_button.isVisible())
        self.assertGreater(w.mass_tab.apply_button.width(), 100)

    def test_compact_mode_reflows_every_splitter_and_bulk_card(self):
        w = self.window
        w.resize(860, 520)
        self.qt.processEvents()
        self.assertTrue(w._layout_compact)
        for page in (w.company_tab, w.houses_tab, w.categories_tab,
                     w.templates_tab, w.reports_tab):
            self.assertEqual(page.splitter.orientation(), Qt.Orientation.Vertical)
        bulk = w.mass_tab
        row, column, _rs, _cs = bulk.top_layout.getItemPosition(
            bulk.top_layout.indexOf(bulk.calculation_card)
        )
        self.assertEqual((row, column), (1, 0))
        row, column, _rs, _cs = bulk.finish_layout.getItemPosition(
            bulk.finish_layout.indexOf(bulk.apply_button)
        )
        self.assertEqual((row, column), (1, 0))
        w.resize(1500, 930)
        self.qt.processEvents()
        self.assertFalse(w._layout_compact)
        for page in (w.company_tab, w.houses_tab, w.categories_tab,
                     w.templates_tab, w.reports_tab):
            self.assertEqual(page.splitter.orientation(), Qt.Orientation.Horizontal)
        row, column, _rs, _cs = bulk.top_layout.getItemPosition(
            bulk.top_layout.indexOf(bulk.calculation_card)
        )
        self.assertEqual((row, column), (0, 1))

    def test_form_rows_expand_combos_and_wrap_when_necessary(self):
        w = self.window
        for page in (w.categories_tab, w.houses_tab, w.mass_tab):
            form_widgets = [
                item for item in page.findChildren(QComboBox) if item.isEnabled()
            ]
            self.assertTrue(form_widgets)
            for field in form_widgets:
                if field.__class__.__name__ == "ClickComboBox":
                    self.assertGreaterEqual(field.minimumWidth(), 150)
        form = w.categories_tab.section.parentWidget().layout()
        # QFormLayout has explicit wrap rules so short screens do not clip labels.
        candidates = [
            obj for obj in (form, w.categories_tab.findChildren(QFormLayout))
            if isinstance(obj, QFormLayout)
        ] if form else w.categories_tab.findChildren(QFormLayout)
        self.assertTrue(any(
            item.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows
            for item in candidates
        ))

    def test_navigating_resized_windows_does_not_lose_edits(self):
        w = self.window
        cat = w.categories_tab
        cid = cat.current_id
        self.assertIsNotNone(cid)
        w.resize(860, 520)
        self.qt.processEvents()
        w.navigate(2)
        cat.name.setText("Тестовая категория для узкого окна")
        cat.section.setCurrentText("Содержание")
        self.qt.processEvents()
        w.navigate(3)
        self.assertEqual(w.tabs.currentIndex(), 3)
        row = db.row("SELECT name FROM work_categories WHERE id=?", (cid,))
        self.assertEqual(row["name"], "Тестовая категория для узкого окна")


if __name__ == "__main__":
    unittest.main()
