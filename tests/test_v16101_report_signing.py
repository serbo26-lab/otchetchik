"""Selection is scoped to the report and authority dates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.uk728 import db, paths, signatories, report_signing, signing_store
from src.uk728.signing_export import signing_elements
from test_v1698_signing_storage import synthetic_image


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

    def test_saved_snapshot_is_stable_after_live_image_and_person_edits(self):
        signing_store.save_signature(self.person, synthetic_image(), "first.png", {"width_mm":25})
        signing_store.save_options(self.company, stamp=synthetic_image(), stamp_settings={"size_mm":40})
        old = report_signing.save_selection(self.report, "2025-12-31", [self.person],
                                            insert_images=True)
        before = signing_elements(db.load_report_bundle(self.report))
        signing_store.save_signature(self.person, synthetic_image(), "new.png", {"width_mm":30})
        signing_store.save_options(self.company, stamp=None)
        signatories.save_local_signatory(
            {"full_name":"Учебное новое имя", "basis_kind":"Устав", "valid_from":"2025-01-01"},
            company_id=self.company, existing_id=self.person)
        self.assertEqual(report_signing.read_snapshot(self.report), old)
        self.assertEqual(signing_elements(db.load_report_bundle(self.report)), before)
        report_signing.save_selection(self.report, "2025-12-31", [self.person], expected_snapshot=old)
        with self.assertRaises(ValueError):
            report_signing.clear_selection(self.report, expected_snapshot=old)
        db.execute("UPDATE reports SET period_start=? WHERE id=?", ("2025-02-01", self.report))
        with self.assertRaisesRegex(ValueError, "Изменился период"):
            signing_elements(db.load_report_bundle(self.report))
