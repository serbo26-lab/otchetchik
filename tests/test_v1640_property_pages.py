"""Compatibility checks for the extracted property editors."""
import ast
import unittest
from pathlib import Path

from src.uk728 import app, property_ui, ui_editors


class PropertyPageModuleTests(unittest.TestCase):
    def test_property_classes_remain_compatible(self):
        for name in ("CompaniesTab", "HousesTab"):
            with self.subTest(name=name):
                page = getattr(property_ui, name)
                self.assertIs(getattr(app, name), page)
                self.assertTrue(issubclass(page, ui_editors.BaseTab))

    def test_property_module_does_not_import_application_shell(self):
        source = Path(property_ui.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app", "src.uk728.app"))
                self.assertFalse(any(alias.name == "app" for alias in node.names))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.endswith(".app") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
