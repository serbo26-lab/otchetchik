"""Real Qt layout regression checks for truncated dropdowns and small windows.

All fixtures are synthetic; report values and database operations are unchanged.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout, QScrollArea, QWidget, QPushButton

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.ui_shell import ActionFlowLayout
from src.uk728.ui_editors import ClickComboBox, CopyPasteTable, SafeSpinBox
from src.uk728.reports_ui import AddFromReferenceDialog
from src.uk728.ui_style import apply_theme


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

    def test_sidebar_scroll_is_discoverable_and_last_page_reachable(self):
        w = self.window
        w.resize(860, 520)
        self.qt.processEvents()
        nav = w.sidebar.navigation
        self.assertEqual(nav.verticalScrollBarPolicy(), Qt.ScrollBarAsNeeded)
        self.assertGreater(nav.verticalScrollBar().maximum(), 0)
        nav.scrollToItem(nav.item(nav.count() - 1))
        self.qt.processEvents()
        self.assertTrue(nav.viewport().rect().contains(nav.visualItemRect(nav.item(nav.count() - 1))))
        nav.setCurrentRow(nav.count() - 1)
        self.assertIs(w.tabs.currentWidget(), w.backups_tab)

    def test_flow_layout_queries_without_python_items_are_safe(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        flow = ActionFlowLayout(parent)
        del flow._items
        try:
            self.assertEqual(flow.count(), 0)
            self.assertIsNone(flow.itemAt(0))
            self.assertIsNone(flow.takeAt(0))
            self.assertGreaterEqual(flow.heightForWidth(200), 0)
            self.assertGreaterEqual(flow.minimumSize().width(), 0)
        finally:
            flow._items = []
        flow.addWidget(QPushButton("Synthetic action"))
        self.assertEqual(flow.count(), 1)

    def test_theme_switch_after_repeated_dialog_disposal(self):
        for theme in ("light", "dark", "light"):
            dialog = AddFromReferenceDialog("Содержание", self.window)
            dialog.show()
            self.qt.processEvents()
            dialog.reject()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            apply_theme(self.qt, theme)
            self.qt.processEvents()
        self.assertTrue(self.window.isVisible())

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
        for form in (
            w.categories_tab.editor_form,
            w.mass_tab.source_form,
            w.mass_tab.calculation_form,
        ):
            self.assertEqual(
                form.rowWrapPolicy(), QFormLayout.RowWrapPolicy.WrapLongRows
            )

    def test_report_forms_wrap_long_labels_and_preserve_fields(self):
        page = self.window.reports_tab
        forms = page.report_tabs.findChildren(QFormLayout)
        self.assertGreaterEqual(len(forms), 4)
        for form in forms:
            self.assertEqual(form.rowWrapPolicy(), QFormLayout.RowWrapPolicy.WrapLongRows)
            self.assertEqual(form.fieldGrowthPolicy(), QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.assertIn("период управления", page.show_period.text())
        self.assertIn("полный год", page.show_period.toolTip())

    def test_delete_action_requires_visible_selection_and_enabled_table(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        table = CopyPasteTable(2, 2, parent)
        button = QPushButton("Удалить", parent)
        table.set_value(0, 0, "Учебная строка A")
        table.set_value(1, 0, "Учебная строка B")
        table.bind_delete_action(button)
        self.assertFalse(button.isEnabled())
        table.setCurrentCell(0, 0)
        self.assertTrue(button.isEnabled())
        table.setEnabled(False)
        self.assertFalse(button.isEnabled())
        table.setEnabled(True)
        self.assertTrue(button.isEnabled())
        table.setRowHidden(0, True)
        table.refresh_delete_action()
        self.assertFalse(button.isEnabled())
        table.setRowHidden(0, False)
        table.setCurrentCell(1, 0)
        self.assertTrue(button.isEnabled())
        table.clearSelection()
        self.assertFalse(button.isEnabled())
        table.setCurrentCell(0, 0)
        table.setRowCount(0)
        self.assertFalse(button.isEnabled())

    def test_row_delete_actions_are_bound_in_all_editable_sections(self):
        w = self.window
        tables = (w.categories_tab.sub_table, w.works_tab.table,
                  w.templates_tab.table, w.reports_tab.content_table,
                  w.reports_tab.repair_table, w.reports_tab.management_table)
        for table in tables:
            with self.subTest(table=table):
                button = table._delete_action_button
                self.assertIsNotNone(button)
                table.clearSelection()
                self.assertFalse(button.isEnabled())
                self.assertIn("выделите", button.toolTip())
        self.assertIsNone(w.reports_tab.finance_table._delete_action_button)

    def test_form_fields_ignore_wheel_even_after_keyboard_focus(self):
        parent = QWidget(self.window)
        self.addCleanup(parent.deleteLater)
        combo = ClickComboBox(parent)
        combo.addItems(["Первый", "Второй", "Третий"])
        combo.setCurrentIndex(1)
        year = SafeSpinBox(parent)
        year.setRange(2000, 2100)
        year.setValue(2026)
        for field, before, read in (
            (combo, 1, combo.currentIndex),
            (year, 2026, year.value),
        ):
            with self.subTest(field=type(field).__name__):
                field.show()
                parent.show()
                field.setFocus()
                self.qt.processEvents()
                for delta in (120, -120):
                    event = QWheelEvent(
                        QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False,
                    )
                    field.wheelEvent(event)
                    self.assertFalse(event.isAccepted())
                    self.assertEqual(read(), before)
        parent.hide()

    def test_form_fields_still_accept_explicit_keyboard_changes(self):
        combo = ClickComboBox(self.window)
        year = SafeSpinBox(self.window)
        self.addCleanup(combo.deleteLater)
        self.addCleanup(year.deleteLater)
        combo.addItems(["Первый", "Второй", "Третий"])
        combo.setCurrentIndex(1)
        year.setRange(2000, 2100)
        year.setValue(2026)
        QTest.keyClick(combo, Qt.Key_Down)
        self.assertEqual(combo.currentIndex(), 2)
        QTest.keyClick(year, Qt.Key_Up)
        self.assertEqual(year.value(), 2027)

    def test_report_next_section_moves_focus_to_first_relevant_control(self):
        w = self.window
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (cid, "Учебный дом"))
        db.create_report(cid, hid, 2026)
        w.navigate(6)
        page = w.reports_tab
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        w.activateWindow()
        self.qt.processEvents()
        for index, target in ((1, page.content_template_combo), (2, page.repair_start),
                              (3, page.management_cost), (4, page.claim_pret),
                              (5, page.review_button)):
            page._go_to_report_section(index)
            self.qt.processEvents()
            self.assertEqual(page.report_tabs.currentIndex(), index)
            self.assertTrue(target.hasFocus(), type(target).__name__)

    def test_report_form_labels_are_associated_with_fields(self):
        for form in self.window.reports_tab.report_tabs.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.LabelRole)
                field_item = form.itemAt(row, QFormLayout.FieldRole)
                if label_item and field_item and field_item.widget():
                    self.assertIs(label_item.widget().buddy(), field_item.widget())

    def _assert_action_reachable(self, button):
        self.qt.processEvents()
        parent = button.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(button, 0, 0)
                self.qt.processEvents()
            parent = parent.parentWidget()
        self.assertTrue(button.isVisible(), button.text())
        self.assertTrue(button.visibleRegion().contains(button.rect().center()),
                        f"Центр команды недоступен: {button.text()}")

    def test_previously_clipped_primary_actions_are_reachable(self):
        w = self.window
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (cid, "Учебный дом"))
        db.create_report(cid, hid, 2026)
        for theme in ("light", "dark"):
            apply_theme(self.qt, theme)
            for width, height in ((1366, 900), (1100, 700), (860, 520)):
                w.resize(width, height)
                self.qt.processEvents()
                w.navigate(0)
                self._assert_action_reachable(w.company_tab.add_button)
                w.navigate(1)
                self._assert_action_reachable(w.houses_tab.add_button)
                w.navigate(2)
                self._assert_action_reachable(w.categories_tab.add_sub_button)
                w.navigate(6)
                page = w.reports_tab
                page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
                page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
                page.report_tabs.setCurrentIndex(0)
                self._assert_action_reachable(page.show_period)
                page.report_tabs.setCurrentIndex(5)
                self._assert_action_reachable(page.review_button)
                w.navigate(7)
                make = next(b for b in w.backups_tab.findChildren(QPushButton)
                            if b.text() == "Создать копию")
                self._assert_action_reachable(make)

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
