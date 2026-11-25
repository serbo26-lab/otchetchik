"""Mass application must preserve report ownership and confirmed opening balances."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.uk728 import db
from src.uk728.mass_apply_logic import OutdatedPreview, apply_plan, build_plan


class MassContextTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(temporary.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()
        self.old_company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic old company",))
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic new company",))
        self.house = db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                                (self.company, "Synthetic house", 100))
        self.second = db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                                 (self.company, "Synthetic second house", 200))
        self.work = db.execute("INSERT INTO work_items(name,category) VALUES(?,?)",
                               ("Synthetic service", "Содержание"))
        self.inputs = dict(company_id=self.company, work_id=self.work, year=2025,
                           house_ids=[self.house, self.second], mode="fixed", total="12",
                           create_missing=True, update_existing=True, override_manual=True)

    def test_other_company_report_is_not_updated(self):
        db.execute("UPDATE houses SET company_id=? WHERE id=?", (self.old_company, self.house))
        foreign_report = db.create_report(self.old_company, self.house, 2025)
        db.execute("INSERT INTO report_content_items(report_id,work_item_id,name,plan_cost,is_manual) VALUES(?,?,?,?,?)",
                   (foreign_report, self.work, "Synthetic service", 99, 1))
        db.execute("UPDATE houses SET company_id=? WHERE id=?", (self.company, self.house))
        with closing(db.connect()) as conn:
            plan = build_plan(conn, self.inputs)
            actions = {row["house_id"]: row["action"] for row in plan["rows"]}
            self.assertEqual(actions, {self.house: "skip_other_company", self.second: "create"})
            apply_plan(conn, plan)
            rows = conn.execute("SELECT plan_cost,is_manual FROM report_content_items WHERE report_id=?",
                                (foreign_report,)).fetchall()
            self.assertEqual([tuple(row) for row in rows], [(99, 1)])
            self.assertEqual(conn.execute("SELECT company_id FROM reports WHERE id=?",
                                          (foreign_report,)).fetchone()[0], self.old_company)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports WHERE house_id=?",
                                          (self.second,)).fetchone()[0], 1)

    def test_ownership_change_after_preview_rejects_whole_plan(self):
        report = db.create_report(self.company, self.house, 2025)
        with closing(db.connect()) as conn:
            plan = build_plan(conn, self.inputs)
            db.execute("UPDATE reports SET company_id=? WHERE id=?", (self.old_company, report))
            with self.assertRaises(OutdatedPreview):
                apply_plan(conn, plan)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_content_items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)

    def test_changed_previous_balances_require_new_preview(self):
        previous = db.create_report(self.company, self.house, 2024)
        with closing(db.connect()) as conn:
            plan = build_plan(conn, self.inputs)
            db.execute("UPDATE reports SET repair_end_balance=? WHERE id=?", (32, previous))
            with self.assertRaises(OutdatedPreview):
                apply_plan(conn, plan)
            plan = build_plan(conn, self.inputs)
            db.execute("UPDATE report_financials SET debt_end=? WHERE report_id=? AND payer_type=?",
                       (15, previous, "Собственники"))
            with self.assertRaises(OutdatedPreview):
                apply_plan(conn, plan)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports WHERE year=2025").fetchone()[0], 0)
            apply_plan(conn, build_plan(conn, self.inputs))
            report = conn.execute("SELECT id,repair_start_balance FROM reports WHERE house_id=? AND year=2025",
                                  (self.house,)).fetchone()
            self.assertEqual(report["repair_start_balance"], 32)
            self.assertEqual(conn.execute("SELECT debt_start FROM report_financials WHERE report_id=? AND payer_type=?",
                                          (report["id"], "Собственники")).fetchone()[0], 15)


if __name__ == "__main__":
    unittest.main()
