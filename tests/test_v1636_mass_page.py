"""Compatibility checks for the extracted mass-application page."""
import ast
import unittest
from pathlib import Path

from src.uk728 import app, mass_apply_ui, ui_editors, ui_labels


class MassPageModuleTests(unittest.TestCase):
    def test_public_page_classes_are_compatible_aliases(self):
        self.assertIs(app.MassApplyTab, mass_apply_ui.MassApplyTab)
        self.assertIs(app.MassPreviewTable, mass_apply_ui.MassPreviewTable)
        self.assertTrue(issubclass(app.MassApplyTab, ui_editors.BaseTab))
        self.assertTrue(issubclass(app.MassPreviewTable, ui_editors.CopyPasteTable))

    def test_shared_labels_keep_their_values(self):
        self.assertEqual(ui_labels.COST_LABEL, "Стоимость")
        self.assertEqual(ui_labels.QUANTITY_LABEL, "Количество")
        self.assertEqual(ui_labels.REPORT_YEAR_LABEL, "Отчетный год")
        self.assertEqual(app.MassApplyTab.PREVIEW_COLUMNS[4], ui_labels.COST_LABEL)
        self.assertEqual(app.MassApplyTab.PREVIEW_COLUMNS[6], ui_labels.QUANTITY_LABEL)

    def test_extracted_module_does_not_import_shell(self):
        source = Path(mass_apply_ui.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app", "src.uk728.app"))
                self.assertFalse(any(alias.name == "app" for alias in node.names))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.endswith(".app") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
