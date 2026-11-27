"""Selection is scoped to the report and authority dates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.uk728 import db, paths, signatories, report_signing


class ReportSigningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(paths, "data_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        self.house = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                                (self.company, "Учебный дом"))
        self.report = db.create_report(self.company, self.house, 2025)
        self.person = signatories.save_local_signatory(
            {"full_name": "Учебный подписант", "basis_kind": "Устав",
             "valid_from": "2025-01-01", "valid_to": "2025-12-31"}, company_id=self.company)
        self.other = signatories.save_local_signatory({"full_name":"Учебный собственник"},
                                                       house_id=self.house)

    def test_selection_dates_and_multiple_people(self):
        with self.assertRaises(ValueError):
            report_signing.save_selection(self.report, "2026-01-01", [self.person])
        snapshot = report_signing.save_selection(self.report, "2025-12-31", [self.person, self.other])
        self.assertEqual(len(snapshot["people"]), 2)
        self.assertEqual(report_signing.read_snapshot(self.report), snapshot)
        report_signing.clear_selection(self.report)
        self.assertEqual(report_signing.read_snapshot(self.report), {})
