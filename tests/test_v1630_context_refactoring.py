"""Regression checks for context hints on synthetic tab objects."""
import unittest
from types import SimpleNamespace

from src.uk728.ui_components import describe_context


def tab(name, **fields):
    page = type(name, (), {})()
    vars(page).update(fields)
    return page


def combo(text, data):
    return SimpleNamespace(currentText=lambda: text, currentData=lambda: data)


def listing(text=None):
    item = SimpleNamespace(text=lambda: text) if text is not None else None
    return SimpleNamespace(currentItem=lambda: item)


class ContextRefactoringTests(unittest.TestCase):
    def test_directory_contexts_preserve_selected_and_empty_states(self):
        cases = (
            ("CompaniesTab", "Управляющая организация: Synthetic company",
             "Управляющая организация не выбрана", "Synthetic company"),
            ("CategoriesTab", "Категория: Synthetic category",
             "Категория не выбрана", "Synthetic category"),
            ("TemplatesTab", "Шаблон: Synthetic template",
             "Шаблон не выбран", "Synthetic template"),
        )
        for name, selected, empty, item in cases:
            with self.subTest(name=name):
                page = tab(name, current_id=1, list=listing(item))
                self.assertEqual(describe_context(page)[0], selected)
                page.current_id = None
                self.assertEqual(describe_context(page)[0], empty)

        houses = tab("HousesTab", current_id=1, list=listing("Synthetic house"),
                     company_filter=combo("Synthetic company", 1))
        self.assertEqual(describe_context(houses)[0],
                         "УО: Synthetic company   •   Дом: Synthetic house")
        houses.company_filter = combo("Выберите УО", None)
        self.assertEqual(describe_context(houses)[0], "Управляющая организация не выбрана")

        works = tab("WorksTab", category_combo=combo("Synthetic category", 1),
                    subcategory_combo=combo("Synthetic subgroup", 2))
        self.assertEqual(describe_context(works)[0],
                         "Справочник работ   •   Категория: Synthetic category"
                         "   •   Подкатегория: Synthetic subgroup")
        works.subcategory_combo = combo("Не выбрано", None)
        self.assertEqual(describe_context(works)[0],
                         "Справочник работ   •   Категория: Synthetic category")

    def test_mass_context_preserves_step_precedence(self):
        page = tab("MassApplyTab", company=combo("Выберите УО", None),
                   work=combo("Synthetic work", 1),
                   year=SimpleNamespace(value=lambda: 2025),
                   selected_house_ids=lambda: [],
                   _preview_dirty=True, _preview_plan=None)
        self.assertTrue(describe_context(page)[1].startswith("Шаг 1:"))
        page.company = combo("Synthetic company", 1)
        self.assertTrue(describe_context(page)[1].startswith("Шаг 3:"))
        page.selected_house_ids = lambda: [1]
        self.assertTrue(describe_context(page)[1].startswith("Шаг 4:"))
        page._preview_dirty = False
        page._preview_plan = {"summary": {"insert": 0, "update": 0}}
        self.assertTrue(describe_context(page)[1].startswith("Нет строк для записи."))
        page._preview_plan["summary"]["insert"] = 1
        self.assertTrue(describe_context(page)[1].startswith("Шаг 5:"))

    def test_report_and_backup_context(self):
        page = tab("ReportsTab", current_id=1,
                   company_filter=combo("Synthetic company", 1),
                   house_filter=combo("Synthetic house", 2),
                   year_filter=SimpleNamespace(value=lambda: 2025),
                   list=listing("Synthetic report"))
        context, guidance = describe_context(page)
        self.assertEqual(context, "УО: Synthetic company   •   Дом: Synthetic house"
                                  "   •   Отчетный год: 2025")
        self.assertTrue(guidance.startswith("Проверьте введенные значения"))
        page.current_id = None
        self.assertTrue(describe_context(page)[1].startswith("Выберите дом и создайте"))
        self.assertEqual(describe_context(tab("BackupsTab"))[0], "Локальные данные программы")
        self.assertEqual(describe_context(tab("SyntheticUnknownTab"))[0], "Рабочий контекст")


if __name__ == "__main__":
    unittest.main()
