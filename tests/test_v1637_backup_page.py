"""Compatibility and dependency checks for the extracted backup page."""
import ast
import unittest
from pathlib import Path

from src.uk728 import app, backups_ui, ui_editors


class BackupPageModuleTests(unittest.TestCase):
    def test_public_page_class_is_compatible_alias(self):
        self.assertIs(app.BackupsTab, backups_ui.BackupsTab)
        self.assertTrue(issubclass(app.BackupsTab, ui_editors.BaseTab))

    def test_extracted_module_does_not_import_application_shell(self):
        source = Path(backups_ui.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app", "src.uk728.app"))
                self.assertFalse(any(alias.name == "app" for alias in node.names))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.endswith(".app") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
