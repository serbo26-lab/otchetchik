"""Compatibility checks for the extracted template editor."""
import ast
import unittest
from pathlib import Path

from src.uk728 import app, templates_ui, ui_editors


class TemplatePageModuleTests(unittest.TestCase):
    def test_template_class_remains_compatible(self):
        self.assertIs(app.TemplatesTab, templates_ui.TemplatesTab)
        self.assertTrue(issubclass(app.TemplatesTab, ui_editors.BaseTab))

    def test_template_module_does_not_import_application_shell(self):
        source = Path(templates_ui.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app", "src.uk728.app"))
                self.assertFalse(any(alias.name == "app" for alias in node.names))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.endswith(".app") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
