"""Regression coverage for non-destructive initialization and ownership rules."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.uk728 import db


class ExistingDataSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()

    def test_deleted_default_template_does_not_reappear_after_restart(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM templates")
            conn.commit()
        db.init_db()
        self.assertEqual(db.row("SELECT COUNT(*) AS n FROM templates")["n"], 0)

    def test_legacy_custom_work_section_is_not_erased_at_start(self):
        wid = db.execute(
            "INSERT INTO work_items(name, category, unit) VALUES(?,?,?)",
            ("Старая особая услуга", "Дополнительная услуга УК", "усл."),
        )
        db.init_db()
        self.assertEqual(
            db.row("SELECT category FROM work_items WHERE id=?", (wid,))["category"],
            "Дополнительная услуга УК",
        )

    def test_report_cannot_be_assigned_to_house_of_other_organization(self):
        first = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Альфа",))
        second = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Бета",))
        house = db.execute(
            "INSERT INTO houses(company_id,address) VALUES(?,?)", (first, "г. Примерск, д. 1")
        )
        with self.assertRaisesRegex(ValueError, "не принадлежит"):
            db.create_report(second, house, 2026)
        self.assertEqual(
            db.row("SELECT COUNT(*) AS n FROM reports")["n"], 0
        )
        self.assertGreater(db.create_report(first, house, 2026), 0)

    def test_reinitialize_existing_database_does_not_seed_samples(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM templates")
            conn.execute("DELETE FROM work_items")
            conn.execute("DELETE FROM work_categories")
            conn.commit()
        db.init_db()
        for table in ("templates", "work_items", "work_categories"):
            self.assertEqual(
                db.row(f"SELECT COUNT(*) AS n FROM {table}")["n"], 0, table
            )


if __name__ == "__main__":
    unittest.main()
