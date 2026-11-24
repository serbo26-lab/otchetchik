"""Compatibility checks for the extracted work-directory pages."""
import ast
import unittest
from pathlib import Path

from src.uk728 import app, directory_ui, ui_editors, ui_labels


class DirectoryPageModuleTests(unittest.TestCase):
    def test_public_page_classes_remain_compatible(self):
        for name in ("CategoriesTab", "WorksTab"):
            with self.subTest(name=name):
                page = getattr(directory_ui, name)
                self.assertIs(getattr(app, name), page)
                self.assertTrue(issubclass(page, ui_editors.BaseTab))

    def test_shared_labels_and_query_are_unchanged(self):
        expected = {
            "NOTES_LABEL": "Примечание",
            "UNNAMED_LABEL": "Без названия",
            "DELETE_QUESTION": "Удалить?",
            "DELETE_CANCELLED": "Удаление отменено",
            "NOT_SELECTED": "Не выбрано",
            "SECTION_LABEL": "Раздел формы",
            "UNIT_LABEL": "Ед. изм.",
            "CATEGORY_BY_ID_SQL": "SELECT * FROM work_categories WHERE id=?",
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(ui_labels, name), value)
                self.assertEqual(getattr(directory_ui, name), value)
                self.assertEqual(getattr(app, name), value)

    def test_directory_module_does_not_import_application_shell(self):
        source = Path(directory_ui.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app", "src.uk728.app"))
                self.assertFalse(any(alias.name == "app" for alias in node.names))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.endswith(".app") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
