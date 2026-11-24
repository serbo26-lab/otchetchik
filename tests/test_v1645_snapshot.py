"""Report reads must be consistent even if another SQLite writer commits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.uk728 import db


class ReportSnapshotTests(unittest.TestCase):
    def test_bundle_uses_one_read_transaction(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            with patch("src.uk728.paths.app_dir", return_value=Path(folder)):
                db.init_db()
                company_id = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic company",))
                house_id = db.execute(
                    "INSERT INTO houses(company_id,address) VALUES(?,?)",
                    (company_id, "Synthetic house"),
                )
                report_id = db.create_report(company_id, house_id, 2025)
                old = db.load_report_bundle(report_id)
                reader = db.connect()
                writer = db.connect()

                class InterleavedReader:
                    def execute(self, sql, params=()):
                        cursor = reader.execute(sql, params)
                        if sql.startswith("SELECT * FROM companies"):
                            writer.execute("UPDATE reports SET notes=? WHERE id=?",
                                           ("Synthetic later notes", report_id))
                            writer.execute("UPDATE companies SET name=? WHERE id=?",
                                           ("Synthetic later company", company_id))
                            writer.commit()
                            cursor = reader.execute(sql, params)
                        return cursor

                    def close(self):
                        reader.close()

                try:
                    with patch("src.uk728.db.connect", return_value=InterleavedReader()):
                        result = db.load_report_bundle(report_id)
                    self.assertEqual(result, old)
                    new = db.load_report_bundle(report_id)
                    self.assertEqual(new["report"]["notes"], "Synthetic later notes")
                    self.assertEqual(new["company"]["name"], "Synthetic later company")
                finally:
                    reader.close()
                    writer.close()


if __name__ == "__main__":
    unittest.main()
