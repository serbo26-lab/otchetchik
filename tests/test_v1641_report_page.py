"""Compatibility checks for the extracted report editor."""
import ast
import unittest
from pathlib import Path

from src.uk728 import app, reports_ui, ui_editors


class ReportPageModuleTests(unittest.TestCase):
    def test_report_class_and_dialogs_remain_compatible(self):
        for name in ("ReportsTab", "AddFromReferenceDialog", "CreateReportDialog"):
            with self.subTest(name=name):
                self.assertIs(getattr(app, name), getattr(reports_ui, name))
        self.assertTrue(issubclass(app.ReportsTab, ui_editors.BaseTab))

    def test_report_module_does_not_import_application_shell(self):
        source = Path(reports_ui.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app", "src.uk728.app"))
                self.assertFalse(any(alias.name == "app" for alias in node.names))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.endswith(".app") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
