"""GUI workflow with synthetic images and no real personal data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from src.uk728 import db, paths, report_signing, signatories, signing_store
from src.uk728.act_image_dialog import ActImageDialog
from src.uk728.report_signing_dialog import ReportSigningDialog
from test_v1698_signing_storage import synthetic_image


class SigningWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.object(paths, "data_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        self.house = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                                (self.company, "Учебный дом"))
        self.report = db.create_report(self.company, self.house, 2025)
        self.director = signatories.save_local_signatory(
            {"full_name":"Учебный руководитель", "basis_kind":"Устав", "valid_from":"2025-01-01"},
            company_id=self.company)
        self.resident = signatories.save_local_signatory(
            {"full_name":"Учебный представитель"}, house_id=self.house)

    def test_card_image_workflow_and_report_dialog(self):
        source = self.root / "synthetic.png"
        source.write_bytes(synthetic_image())
        card = signatories.SignatoryListDialog(company_id=self.company)
        self.addCleanup(card.close)
        card.show()
        self.app.processEvents()
        self.assertTrue(card.signature_button.isEnabled())

        def edit(dialog):
            field = "width_mm" if dialog.kind == "signature" else "size_mm"
            dialog.fields[field].setValue(30 if dialog.kind == "signature" else 35)
            return QDialog.Accepted

        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "")), \
             patch.object(ActImageDialog, "exec", new=edit):
            card.signature_button.click()
            card.stamp_button.click()
        self.assertEqual(signing_store.read_signature(self.director)["settings"]["width_mm"], 30)
        self.assertEqual(signing_store.read_options(self.company)["stamp_settings"]["size_mm"], 35)
        card.insert_images.setChecked(True)
        original = signing_store.read_signature(self.director)
        with patch.object(ActImageDialog, "exec", return_value=QDialog.Rejected):
            card.signature_button.click()
        self.assertEqual(signing_store.read_signature(self.director), original)
        card.close()

        dialog = ReportSigningDialog(self.report)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.images.isChecked())
        self.assertIn("Подтвердите", dialog.images.toolTip())
        self.assertEqual(dialog.list.count(), 2)
        for index in range(dialog.list.count()):
            dialog.list.item(index).setCheckState(Qt.Checked)
        dialog.images.setChecked(True)
        dialog.privacy.setCurrentIndex(dialog.privacy.findData("house_identity"))
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.save_button.click()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        snapshot = report_signing.read_snapshot(self.report)
        block = report_signing.public_block(snapshot)
        self.assertEqual(len(block["people"]), 2)
        self.assertIsNotNone(block["people"][0]["image"])
        self.assertEqual(block["people"][1]["name"], report_signing.HIDDEN)
        self.assertIsNone(block["people"][1]["image"])
        self.assertEqual(block["stamp"]["settings"]["size_mm"], 35)
