"""The shared editor module remains independent of the application shell."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
