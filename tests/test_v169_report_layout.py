"""V1.6.9: report editor readability and row focus acceptance tests."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.ui_components import ActionPanel


class ReportLayoutV169Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Приемка",))
        self.hid = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.cid, "Дом на длинной улице", 1700),
        )
        self.rid = db.create_report(self.cid, self.hid, 2026)
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.page = self.window.reports_tab
        self.page.reload_filters()
        self.page.company_filter.setCurrentIndex(
            self.page.company_filter.findData(self.cid)
        )
        self.page.house_filter.setCurrentIndex(
            self.page.house_filter.findData(self.hid)
        )
        self.assertEqual(self.page.current_id, self.rid)

    def selected_caption(self, tab_index):
        matches = self.page.report_tabs.widget(tab_index).findChildren(
            QLabel, "SelectedWorkLabel"
        )
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_compact_tab_bar_supports_horizontal_scrolling(self):
        self.assertTrue(self.page.report_tabs.usesScrollButtons())

    def test_each_business_section_has_one_wide_wrapping_action_group(self):
        for index in (1, 2, 3):
            screen = self.page.report_tabs.widget(index)
            panels = [item for item in screen.findChildren(ActionPanel)
                      if item.heading.text() == "Наполнение раздела"]
            self.assertEqual(len(panels), 1, index)
            self.assertEqual(len(panels[0].findChildren(QPushButton)), 3)

    def test_blank_row_receives_keyboard_focus_and_context_header(self):
        table = self.page.content_table
        self.page.add_blank_report_row("Содержание")
        self.assertEqual(table.currentRow(), 0)
        self.assertEqual(table.currentColumn(), 2)
        table.set_value(0, 2, "Содержание инженерных коммуникаций")
        label = self.selected_caption(1)
        self.assertIn("Содержание инженерных коммуникаций", label.text())
        self.assertFalse(label.isHidden())
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        self.assertIn("Содержание инженерных коммуникаций", label.text())

    def test_repair_and_management_selected_names_stay_visible(self):
        for name, index, table in (
            ("Текущий ремонт", 2, self.page.repair_table),
            ("Управление", 3, self.page.management_table),
        ):
            self.page.add_blank_report_row(name)
            self.assertEqual(table.currentColumn(), 2)
            table.set_value(0, 2, "Длинное наименование тестовой услуги")
            self.assertIn("Длинное наименование", self.selected_caption(index).text())

    def test_finance_caption_identifies_payer(self):
        table = self.page.finance_table
        table.setCurrentCell(0, 2)
        label = self.selected_caption(4)
        self.assertIn("Собственники", label.text())

    def test_template_long_name_is_available_in_tooltip(self):
        long_name = "Подробный перечень содержания многоквартирного дома номер семь на 2026 год"
        tid = db.execute(
            "INSERT INTO templates(name,category) VALUES(?,?)",
            (long_name, "Содержание"),
        )
        self.page.reload_template_combo()
        combo = self.page.content_template_combo
        index = combo.findData(tid)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)
        self.assertIn(long_name, combo.currentText())
        self.assertEqual(combo.toolTip(), combo.currentText())
        self.assertEqual(combo.itemData(index, Qt.ToolTipRole), combo.currentText())

    def test_context_add_uses_focused_row_action(self):
        self.page.repair_table.context_add_handler()
        self.assertEqual(self.page.repair_table.currentRow(), 0)
        self.assertEqual(self.page.repair_table.currentColumn(), 2)
        self.page.management_table.context_add_handler()
        self.assertEqual(self.page.management_table.currentRow(), 0)
        self.assertEqual(self.page.management_table.currentColumn(), 2)


if __name__ == "__main__":
    unittest.main()
