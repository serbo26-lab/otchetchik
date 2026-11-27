"""Every documented guidance state has an explicit, non-mutating expectation."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from src.uk728.ui_workflow import recommend_next, workflow_help


def choice(value):
    return SimpleNamespace(currentData=lambda: value)


def button(enabled, tooltip=""):
    return SimpleNamespace(isEnabled=lambda: enabled, toolTip=lambda: tooltip)


def page(name, **fields):
    result = type(name, (), {})()
    vars(result).update(fields)
    return result


class GuidanceMatrixTests(unittest.TestCase):
    def test_directory_and_template_states(self):
        cases = (
            ("CategoriesTab", dict(current_id=None), "Добавить категорию", None),
            ("CategoriesTab", dict(current_id=1, has_unsaved_changes=lambda: True),
             "Сохранить категорию", None),
            ("CategoriesTab", dict(current_id=1, has_unsaved_changes=lambda: False),
             "справочник", 3),
            ("WorksTab", dict(has_unsaved_changes=lambda: True), "сохраните справочник", None),
            ("WorksTab", dict(has_unsaved_changes=lambda: False, table=SimpleNamespace(rowCount=lambda: 0)),
             "Добавьте работу", None),
            ("WorksTab", dict(has_unsaved_changes=lambda: False, table=SimpleNamespace(rowCount=lambda: 1)),
             "отчётам", 6),
            ("TemplatesTab", dict(current_id=None), "Добавить шаблон", None),
            ("TemplatesTab", dict(current_id=1, has_unsaved_changes=lambda: True),
             "Сохранить шаблон", None),
        )
        for name, fields, expected, destination in cases:
            with self.subTest(name=name, expected=expected):
                hint = recommend_next(page(name, **fields))
                self.assertIn(expected, hint.text)
                self.assertEqual(hint.destination, destination)
        for count, work, expected, destination in (
            (0, None, "справочник", 3), (0, 1, "Добавить работу в шаблон", None),
            (1, 1, "отчётам", 6),
        ):
            with self.subTest(count=count, work=work):
                hint = recommend_next(page("TemplatesTab", current_id=1,
                                           has_unsaved_changes=lambda: False,
                                           table=SimpleNamespace(rowCount=lambda: count),
                                           work_combo=choice(work)))
                self.assertIn(expected, hint.text)
                self.assertEqual(hint.destination, destination)

    def test_all_mass_preview_states(self):
        for preview, dirty, apply, expected in (
            (False, True, False, "Учебное ограничение"),
            (True, True, False, "Рассчитать и проверить"),
            (True, False, True, "Подтвердить и применить"),
            (True, False, False, "Нет строк для записи"),
        ):
            with self.subTest(preview=preview, dirty=dirty, apply=apply):
                target = page("MassApplyTab", preview_button=button(preview, "Учебное ограничение"),
                              _preview_dirty=dirty, apply_button=button(apply),
                              preview=Mock(), apply=Mock())
                hint = recommend_next(target)
                self.assertIn(expected, hint.text)
                target.preview.assert_not_called()
                target.apply.assert_not_called()

    def test_report_context_and_all_section_states(self):
        fields = dict(company_filter=choice(None), house_filter=choice(None),
                      current_id=None, list=SimpleNamespace(count=lambda: 0),
                      report_tabs=SimpleNamespace(currentIndex=lambda: 0),
                      content_table=SimpleNamespace(rowCount=lambda: 0),
                      _review_dirty=True, _review_snapshot=None)
        target = page("ReportsTab", **fields)
        self.assertIn("Выберите УО", recommend_next(target).text)
        target.company_filter = choice(1)
        self.assertIn("Выберите УО", recommend_next(target).text)
        target.house_filter = choice(1)
        self.assertIn("Создать отчет", recommend_next(target).text)
        target.list = SimpleNamespace(count=lambda: 1)
        self.assertIn("существующий отчёт", recommend_next(target).text)
        target.current_id = 1
        for index, expected in enumerate(("общие сведения", "Добавьте работу", "ремонте",
                                           "официальный итог", "претензионную работу",
                                           "Проверить заполнение")):
            with self.subTest(index=index):
                target.report_tabs = SimpleNamespace(currentIndex=lambda: index)
                self.assertIn(expected, recommend_next(target).text)
        target._review_dirty = False
        target._review_snapshot = (None, None, {"errors": ["Учебная ошибка"]})
        self.assertIn("Исправьте ошибки", recommend_next(target).text)
        target._review_snapshot = (None, None, {"errors": []})
        self.assertIn("формат выгрузки", recommend_next(target).text)
        target.report_tabs = SimpleNamespace(currentIndex=lambda: 99)
        self.assertEqual(recommend_next(target).text, "Заполните выбранный раздел.")

    def test_backup_guidance_precedence(self):
        target = page("BackupsTab", _restore_error=True, cancel_button=button(True),
                      _check_failed=True, _checked_path="synthetic.db", check_button=button(True))
        self.assertIn("поврежденную заявку", recommend_next(target).text)
        target._restore_error = False
        self.assertIn("перезапустите", recommend_next(target).text)
        target.cancel_button = button(False)
        self.assertIn("повторно", recommend_next(target).text)
        target._check_failed = False
        self.assertIn("Копия проверена", recommend_next(target).text)
        target._checked_path = None
        self.assertIn("Проверить выбранную", recommend_next(target).text)
        target.check_button = button(False)
        self.assertIn("Создайте копию", recommend_next(target).text)

    def test_missing_house_company_and_unknown_page(self):
        self.assertIn("Выберите управляющую организацию",
                      recommend_next(page("HousesTab", company_filter=choice(None))).text)
        target = page("UnknownTab")
        self.assertEqual(recommend_next(target).text, "Выберите запись.")
        self.assertEqual(workflow_help(target), "Действия зависят от выбранного раздела.")
        for name in ("CompaniesTab", "HousesTab", "CategoriesTab", "WorksTab",
                     "TemplatesTab", "MassApplyTab", "ReportsTab", "BackupsTab"):
            self.assertNotEqual(workflow_help(page(name)), workflow_help(target))


if __name__ == "__main__":
    unittest.main()
