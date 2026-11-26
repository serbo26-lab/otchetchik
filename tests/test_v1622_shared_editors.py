"""The shared editor module remains independent of the application shell."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
from pathlib import Path
import unittest

import sqlite3
from contextlib import closing
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QGuiApplication

from src.uk728 import app, ui_editors


class SharedEditorsArchitectureTests(unittest.TestCase):
    def test_old_imports_are_the_same_objects_not_wrappers_or_duplicates(self):
        names = (
            "BaseTab", "ClickComboBox", "SafeSpinBox", "EditableComboDelegate",
            "CopyPasteTable", "numeric_error", "distinct_texts", "form_scroll",
            "selectable_label", "polish_form", "empty_record_editor",
            "restore_combo_selection", "row_get", "normalize_section",
            "work_display", "fill_combo", "set_widgets_enabled", "clear_widgets",
            "COMMON_SECTIONS", "COMMON_UNITS", "COMMON_FILL_MODES",
            "COMMON_PAYER_TYPES", "COMMON_REPAIR_BASIS",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(app, name), getattr(ui_editors, name))

    def test_all_pages_continue_to_use_shared_base(self):
        for page in (
            app.CompaniesTab, app.HousesTab, app.CategoriesTab, app.WorksTab,
            app.TemplatesTab, app.ReportsTab, app.MassApplyTab, app.BackupsTab,
        ):
            self.assertTrue(issubclass(page, ui_editors.BaseTab))
        self.assertTrue(issubclass(app.MassPreviewTable, ui_editors.CopyPasteTable))

    def test_shared_module_does_not_import_application_shell(self):
        source = ast.parse(Path(ui_editors.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(source):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("app", (node.module or "").split("."))
                self.assertNotIn("app", [alias.name for alias in node.names])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("app", alias.name.split("."))


class SharedEditorBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def test_distinct_texts_retains_order_and_case_insensitive_deduplication(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE example(value TEXT)")
            conn.executemany("INSERT INTO example VALUES(?)",
                             [("alpha",), ("ALPHA",), (" beta ",), ("",), (None,)])
            with patch.object(ui_editors.db, "rows",
                              side_effect=lambda query, params=(): conn.execute(query, params).fetchall()):
                self.assertEqual(ui_editors.distinct_texts("example", "value", ["Alpha"]),
                                 ["Alpha", "beta"])

    def test_fill_combo_accepts_named_and_alternate_labels_and_preserves_selection(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE example(id INTEGER, name TEXT)")
            conn.executemany("INSERT INTO example VALUES(?,?)", [(1, "One"), (2, "Two")])
            combo = ui_editors.ClickComboBox()
            self.addCleanup(combo.close)
            with patch.object(ui_editors.db, "rows",
                              side_effect=lambda query, params=(): conn.execute(query, params).fetchall()):
                ui_editors.fill_combo(combo, "SELECT id,name FROM example ORDER BY id", empty="None")
                self.assertEqual(combo.itemText(1), "One")
                combo.setCurrentIndex(combo.findData(2))
                ui_editors.fill_combo(combo, "SELECT id,name AS label FROM example ORDER BY id DESC", empty="None")
                self.assertEqual(combo.currentData(), 2)
                self.assertEqual(combo.currentText(), "Two")
                self.assertEqual(combo.toolTip(), "Two")

    def test_copy_does_not_include_hidden_identifiers_or_filtered_rows(self):
        table = ui_editors.CopyPasteTable(2, 3)
        self.addCleanup(table.close)
        table.setColumnHidden(0, True)
        for row, values in enumerate((("77", "Visible A", "10"), ("88", "Hidden B", "20"))):
            for column, value in enumerate(values):
                table.set_value(row, column, value)
        table.selectAll()
        table.setRowHidden(1, True)
        clipboard = QGuiApplication.clipboard()
        self.addCleanup(clipboard.clear)
        table.copy_selection()
        self.assertEqual(clipboard.text(), "Visible A\t10")
        self.assertEqual(table.selected_visible_rows(), [0])
        table.delete_selected_rows(confirmed=True)
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.value(0, 0), "88")

    def test_filtered_paste_targets_visible_rows_and_rejects_overflow(self):
        table = ui_editors.CopyPasteTable(3, 2)
        self.addCleanup(table.close)
        table.allow_paste_append = True
        for row, name in enumerate(("Visible A", "Hidden", "Visible B")):
            table.set_value(row, 1, name)
        table.setRowHidden(1, True)
        table.setCurrentCell(0, 1)
        clipboard = QGuiApplication.clipboard()
        self.addCleanup(clipboard.clear)
        clipboard.setText("Changed A\nChanged B")
        table.paste_selection()
        self.assertEqual([table.value(row, 1) for row in range(3)],
                         ["Changed A", "Hidden", "Changed B"])
        clipboard.setText("New A\nNew B\nExtra")
        with patch.object(QMessageBox, "warning") as warning:
            table.paste_selection()
        warning.assert_called_once()
        self.assertEqual([table.value(row, 1) for row in range(3)],
                         ["Changed A", "Hidden", "Changed B"])

    def test_empty_table_paste_begins_at_first_visible_field(self):
        table = ui_editors.CopyPasteTable(0, 3)
        self.addCleanup(table.close)
        table.setColumnHidden(0, True)
        table.allow_paste_append = True
        clipboard = QGuiApplication.clipboard()
        self.addCleanup(clipboard.clear)
        clipboard.setText("Synthetic work\t15")
        table.paste_selection()
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.value(0, 0), "")
        self.assertEqual(table.value(0, 1), "Synthetic work")
        self.assertEqual(table.value(0, 2), "15")

    def test_paste_keeps_hidden_and_protected_columns_and_append_policy(self):
        table = ui_editors.CopyPasteTable(1, 4)
        self.addCleanup(table.close)
        table.setColumnHidden(2, True)
        table.protected_columns = {1}
        table.set_value(0, 1, "protected")
        table.set_value(0, 2, "hidden")
        table.setCurrentCell(0, 1)
        clipboard = QGuiApplication.clipboard()
        self.addCleanup(clipboard.clear)
        clipboard.setText("ignored\thidden\tfirst\nignored\thidden\tsecond")
        table.paste_selection()
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.value(0, 1), "protected")
        self.assertEqual(table.value(0, 2), "hidden")
        self.assertEqual(table.value(0, 3), "first")
        table.allow_paste_append = True
        table.context_add_handler = lambda: table.insertRow(table.rowCount())
        table.paste_selection()
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.value(1, 3), "second")
        table.setRowCount(1)
        table.context_add_handler = lambda: None
        table.paste_selection()
        self.assertEqual(table.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
