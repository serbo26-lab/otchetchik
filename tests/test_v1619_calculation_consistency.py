"""Calculation and review regressions; all fixtures are fictional."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.uk728 import db
from src.uk728.assistant_work_list import _candidate
from src.uk728.mass_apply_logic import _split_cents, build_plan, apply_plan, validate_inputs
from src.uk728.report_review import inspect_report


class CalculationConsistencyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(temp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.org = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        self.houses = [
            db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                       (self.org, f"Учебный объект {i}", 100))
            for i in range(5)
        ]
        self.work = db.execute("INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
                               ("Учебная услуга", "Содержание", "шт."))
        self.inputs = dict(company_id=self.org, work_id=self.work, year=2025,
                           house_ids=self.houses, mode="split", total="0,03",
                           create_missing=True)

    def test_three_kopecks_split_five_ways_without_negative_amount(self):
        with db.connect() as conn:
            plan = build_plan(conn, self.inputs)
            self.assertEqual([r["cost"] for r in plan["rows"]], [.01,.01,.01,0,0])
            self.assertEqual(plan["summary"]["cost"], .03)
            apply_plan(conn, plan)
        amounts = db.rows("SELECT plan_cost FROM report_content_items WHERE work_item_id=?", (self.work,))
        self.assertEqual(sum(Decimal(str(r["plan_cost"])) for r in amounts), Decimal(".03"))
        self.assertTrue(all(r["plan_cost"] >= 0 for r in amounts))

    def test_split_exact_proportions_and_stable_ties(self):
        for cents in (1, 3, 7, 100, 9999):
            total = Decimal(cents) / 100
            areas = list(map(Decimal, ("1.1", "4.2", "8", "3", "1.1")))
            allocation = _split_cents(total, areas)
            self.assertEqual(sum(allocation), total)
            self.assertTrue(all(x >= 0 and x == x.quantize(Decimal(".01")) for x in allocation))
            self.assertEqual(allocation, _split_cents(total, areas))
        self.assertEqual(_split_cents(Decimal("1000"), [Decimal(100), Decimal(300)]),
                         [Decimal("250"), Decimal("750")])

    def test_fractional_kopeck_input_rejected_in_split_mode(self):
        with self.assertRaisesRegex(ValueError, "копейки"):
            validate_inputs({**self.inputs, "total": "1.001"})

    def test_date_boundaries_must_belong_to_report_year(self):
        rid = db.create_report(self.org, self.houses[0], 2025)
        db.execute("UPDATE reports SET period_start=? WHERE id=?", ("2024-12-31", rid))
        result = inspect_report(db.load_report_bundle(rid))
        self.assertTrue(any("отчетному году" in x for x in result["errors"]))
        db.execute("UPDATE reports SET period_start=? WHERE id=?", ("2025-03-01", rid))
        self.assertFalse(inspect_report(db.load_report_bundle(rid))["errors"])

    def test_carry_forward_warns_for_gap_or_unverified_previous_report(self):
        previous = db.create_report(self.org, self.houses[0], 2024)
        db.execute("UPDATE reports SET repair_end_balance=23 WHERE id=?", (previous,))
        current = db.create_report(self.org, self.houses[0], 2025)
        bundle = db.load_report_bundle(current)
        self.assertEqual(bundle["report"]["repair_start_balance"], 23)
        self.assertTrue(any("Сверьте начальные остатки" in x for x in inspect_report(bundle)["warnings"]))
        db.execute("UPDATE reports SET status='Проверен' WHERE id=?", (previous,))
        self.assertFalse(any("Сверьте начальные остатки" in x for x in
                             inspect_report(db.load_report_bundle(current))["warnings"]))
        db.execute("UPDATE reports SET period_end='2024-11-30' WHERE id=?", (previous,))
        self.assertTrue(any("Сверьте начальные остатки" in x for x in
                            inspect_report(db.load_report_bundle(current))["warnings"]))

    def test_persisted_nonfinite_values_block_review(self):
        rid = db.create_report(self.org, self.houses[0], 2025)
        db.execute("UPDATE reports SET management_cost=? WHERE id=?", (float("inf"), rid))
        result = inspect_report(db.load_report_bundle(rid))
        self.assertTrue(any("некорректное число" in x for x in result["errors"]))

    def test_import_rejects_float_overflow_and_decimal_product_overflow(self):
        base = dict(id="fictional", work_name="Учебная услуга", unit_name="шт.",
                    price="1", volume="1", quantity="1", cost="1")
        for extra in (dict(price="1e309"), dict(volume="1e308", quantity="100")):
            self.assertTrue(_candidate({**base, **extra}, False).problem)


if __name__ == "__main__":
    unittest.main()
