"""Mass report refactoring regressions using fictional records only."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from src.uk728 import db
from src.uk728.mass_apply_logic import TABLES, apply_plan, build_plan


class MassRefactoringTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(temporary.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic company",))
        self.houses = [
            db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                       (self.company, f"Synthetic object {i}", area))
            for i, area in enumerate((100, 300))
        ]

    def inputs(self, section):
        work = db.execute("INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
                          (f"Synthetic service {section}", section, "м²"))
        return dict(company_id=self.company, work_id=work, year=2025,
                    house_ids=self.houses, mode="fixed", total="12",
                    create_missing=True, update_existing=True)

    def test_all_five_modes_preserve_preview_values(self):
        inputs = self.inputs("Содержание")
        cases = (
            ("area", {"rate": "2"}, [(2, 100, 200), (2, 300, 600)]),
            ("monthly", {"rate": "2", "months": "3"}, [(6, 100, 600), (6, 300, 1800)]),
            ("fixed", {"total": "12"}, [(12, 1, 12), (12, 1, 12)]),
            ("split", {"total": "400"}, [(1, 100, 100), (1, 300, 300)]),
            ("quantity", {"rate": "5", "quantity": "3"}, [(5, 3, 15), (5, 3, 15)]),
        )
        with closing(db.connect()) as conn:
            for mode, extra, expected in cases:
                with self.subTest(mode=mode):
                    plan = build_plan(conn, {**inputs, **extra, "mode": mode})
                    self.assertEqual([row["action"] for row in plan["rows"]], ["create"] * 2)
                    self.assertEqual([(row["price"], row["quantity"], row["cost"])
                                      for row in plan["rows"]], expected)
                    self.assertEqual(plan["summary"]["cost"], sum(item[2] for item in expected))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 0)

    def test_all_sections_insert_update_and_preserve_identity(self):
        for section, table in TABLES.items():
            with self.subTest(section=section), closing(db.connect()) as conn:
                inputs = self.inputs(section)
                plan = build_plan(conn, inputs)
                apply_plan(conn, plan)
                initial = [dict(row) for row in conn.execute(
                    f"SELECT * FROM {table} WHERE work_item_id=? ORDER BY id", (inputs["work_id"],)
                )]
                self.assertEqual(len(initial), 2)
                if section == "Текущий ремонт":
                    conn.execute(f"UPDATE {table} SET basis=?,act_details=? WHERE work_item_id=?",
                                 ("Synthetic basis", "Synthetic act", inputs["work_id"]))
                conn.execute("UPDATE reports SET status='Проверен' WHERE company_id=?", (self.company,))
                conn.commit()
                update = build_plan(conn, {**inputs, "total": "20"})
                self.assertEqual([row["action"] for row in update["rows"]], ["update"] * 2)
                apply_plan(conn, update)
                after = [dict(row) for row in conn.execute(
                    f"SELECT * FROM {table} WHERE work_item_id=? ORDER BY id", (inputs["work_id"],)
                )]
                self.assertEqual([row["id"] for row in after], [row["id"] for row in initial])
                for row in after:
                    self.assertEqual(row["source"], "массовое применение")
                    self.assertEqual(row["is_manual"], 0)
                    if section == "Содержание":
                        self.assertEqual((row["plan_cost"], row["actual_cost"]), (20, 20))
                    else:
                        self.assertEqual(row["cost"], 20)
                    if section == "Текущий ремонт":
                        self.assertEqual((row["basis"], row["act_details"]),
                                         ("Synthetic basis", "Synthetic act"))
                self.assertEqual([row[0] for row in conn.execute(
                    "SELECT status FROM reports WHERE company_id=?", (self.company,)
                )], ["Черновик", "Черновик"])

    def test_rollback_when_management_insert_fails_for_second_house(self):
        inputs = self.inputs("Управление")
        with closing(db.connect()) as conn:
            plan = build_plan(conn, inputs)
            conn.execute(f"""CREATE TRIGGER synthetic_failure BEFORE INSERT ON report_management_items
                WHEN (SELECT house_id FROM reports WHERE id=NEW.report_id)={self.houses[1]}
                BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END;""")
            conn.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic rollback"):
                apply_plan(conn, plan)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_management_items").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
