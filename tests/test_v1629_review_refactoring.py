"""Regression tests for the read-only report review with fictional data."""
from copy import deepcopy
import unittest

from src.uk728.report_review import inspect_report


class ReviewRefactoringTests(unittest.TestCase):
    def test_multiple_diagnostics_keep_order_and_do_not_mutate_bundle(self):
        bundle = {
            "report": {"id": 1, "year": 2025, "company_id": 1,
                       "period_start": "2026-01-01", "period_end": "2025-12-31",
                       "publish_date": "invalid", "management_cost": 10},
            "company": {},
            "house": {"total_area": 0},
            "previous_report": {"company_id": 2, "period_end": "2025-12-31",
                                "status": "Черновик"},
            "content": [{"name": "", "unit_price": "invalid",
                         "source": "assistant-work-list:synthetic",
                         "actual_qty": 0, "actual_cost": 0}],
            "repair": [{"name": "Synthetic repair", "cost": 0,
                        "source": "assistant-work-list:synthetic"}],
            "management": [{"name": "Synthetic service", "cost": 20}],
            "financials": [{"payer_type": "Synthetic unknown payer"}],
        }
        original = deepcopy(bundle)
        result = inspect_report(bundle)
        self.assertEqual(bundle, original)
        self.assertEqual(result["report_id"], 1)
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["counts"], {"content": 1, "repair": 1,
                                            "management": 1, "financials": 1})
        self.assertEqual(result["errors"], [
            "Не указано наименование управляющей организации.",
            "Не указан адрес многоквартирного дома.",
            "Период управления: дата начала позже даты окончания.",
            "Даты периода управления в отчете должны относиться к отчетному году.",
            "Неверный формат даты размещения отчета.",
            "Содержание: строка 1 содержит некорректное число.",
            "Содержание: строка 1 не имеет наименования.",
            "Текущий ремонт: строка 1 перенесена из перечня "
            "как черновик. Укажите подтверждённые основание, объём и "
            "стоимость выполненной работы либо удалите строку.",
            "Начисления: строка 1 имеет неизвестный вид плательщика.",
        ])
        warnings = result["warnings"]
        self.assertEqual(len(warnings), 8)
        self.assertIn("общая площадь", warnings[0])
        self.assertIn("Предыдущий отчет", warnings[5])
        self.assertIn("нулевым выполнением", warnings[6])
        self.assertIn("внутренних строк", warnings[7])

    def test_valid_report_and_explicit_zero_management_without_internal_rows(self):
        bundle = {
            "report": {"id": 1, "year": 2025, "company_id": 1,
                       "period_start": "2025-01-01", "period_end": "2025-12-31",
                       "publish_date": "2026-01-01", "management_cost": 0},
            "company": {"name": "Synthetic company", "ogrn_inn": "synthetic",
                        "reception_address": "Synthetic office",
                        "report_person": "Synthetic representative",
                        "report_contact": "Synthetic contact"},
            "house": {"address": "Synthetic property", "total_area": 100},
            "previous_report": {"company_id": 1, "period_end": "2024-12-31",
                                "status": "Проверен"},
            "content": [{"name": "Synthetic service", "actual_qty": 1, "actual_cost": 2}],
            "repair": [{"name": "Synthetic repair", "cost": 1, "basis": "Synthetic basis",
                        "volume": "1", "source": "assistant-work-list:synthetic"}],
            "financials": [{"payer_type": "Собственники"}],
        }
        result = inspect_report(bundle)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [
            "В разделе 3 «Управление» итоговая сумма равна нулю.",
        ])
        self.assertEqual(result["counts"]["management"], 0)


if __name__ == "__main__":
    unittest.main()
