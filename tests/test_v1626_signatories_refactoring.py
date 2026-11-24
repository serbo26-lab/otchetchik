"""Regression tests for signatory refactoring; fictional records only."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFormLayout

from src.uk728 import db, signatories


class SignatoryRefactoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        folder = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(folder.name))
        override.start()
        self.addCleanup(override.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic organization",))
        self.house = db.execute(
            "INSERT INTO houses(company_id,address) VALUES(?,?)",
            (self.company, "Synthetic property"),
        )

    def test_owner_and_case_insensitive_duplicate_checks_preserve_existing_id(self):
        values = {"full_name": "Synthetic representative", "basis_kind": "Устав",
                  "valid_from": "01.01.2025"}
        ident = signatories.save_local_signatory(values, company_id=self.company)
        duplicate = {**values, "full_name": values["full_name"].upper()}
        with self.assertRaisesRegex(ValueError, "уже есть"):
            signatories.save_local_signatory(duplicate, company_id=self.company)
        with self.assertRaisesRegex(ValueError, "другому"):
            signatories.save_local_signatory(values, house_id=self.house, existing_id=ident)
        updated = signatories.save_local_signatory(
            {**values, "position": "Synthetic position"}, company_id=self.company, existing_id=ident,
        )
        self.assertEqual(updated, ident)
        records = signatories.list_signatories(company_id=self.company)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["position"], "Synthetic position")
        self.assertEqual(records[0]["valid_from"], "2025-01-01")
        second = signatories.save_local_signatory(
            {**values, "valid_from": "2026-01-01"}, company_id=self.company,
        )
        self.assertNotEqual(second, ident)
        signatories.save_local_signatory(values, house_id=self.house)
        self.assertEqual(len(signatories.list_signatories(house_id=self.house)), 1)

    def test_editor_keeps_custom_values_and_owner_specific_fields(self):
        original = {"full_name": "Synthetic representative", "role": "Synthetic role",
                    "basis_kind": "Synthetic basis", "gender": "female",
                    "valid_from": "2025-01-02", "position": "Synthetic position",
                    "premises": "Test unit"}
        for organization in (True, False):
            with self.subTest(organization=organization):
                editor = signatories.PersonEditor(original, organization=organization)
                self.addCleanup(editor.close)
                form = editor.findChild(QFormLayout)
                self.assertIsNotNone(form)
                self.assertEqual(form.getWidgetPosition(editor.fields["position"])[0] >= 0, organization)
                self.assertEqual(form.getWidgetPosition(editor.fields["premises"])[0] >= 0, not organization)
                self.assertEqual(editor.role.currentText(), original["role"])
                self.assertEqual(editor.basis.currentText(), original["basis_kind"])
                self.assertEqual(editor.gender.currentIndex(), 2)
                self.assertEqual(editor.fields["valid_from"].text(), "02.01.2025")
                buttons = editor.findChild(QDialogButtonBox)
                buttons.button(QDialogButtonBox.StandardButton.Save).click()
                self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(editor.result_data["valid_from"], original["valid_from"])
                self.assertEqual(editor.result_data["role"], original["role"])
                self.assertEqual(editor.result_data["position"], original["position"])
                self.assertEqual(editor.result_data["premises"], original["premises"])

    def test_readonly_editor_disables_all_fields_and_has_no_save_button(self):
        editor = signatories.PersonEditor({"full_name": "Synthetic source"}, readonly=True)
        self.addCleanup(editor.close)
        for widget in [*editor.fields.values(), editor.role, editor.gender, editor.basis]:
            self.assertFalse(widget.isEnabled())
        buttons = editor.findChild(QDialogButtonBox)
        self.assertIsNone(buttons.button(QDialogButtonBox.StandardButton.Save))
        self.assertIsNotNone(buttons.button(QDialogButtonBox.StandardButton.Close))
        self.assertIsNone(editor.result_data)


if __name__ == "__main__":
    unittest.main()
