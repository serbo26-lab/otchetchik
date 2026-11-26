"""State-based guidance on real widgets, using only fictional local records."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.uk728 import db, signatories
from src.uk728.app import MainWindow
from src.uk728.ui_workflow import recommend_next


class AssistantGuidanceTests(unittest.TestCase):
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
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_company_and_house_guidance_follows_record_state(self):
        company = self.window.company_tab
        self.assertIn("Добавить организацию", recommend_next(company).text)
        company.add_company()
        self.assertEqual(recommend_next(company).destination, 1)
        company.notes.setPlainText("Synthetic change")
        self.assertIsNone(recommend_next(company).destination)
        self.assertIn("Сохранить организацию", recommend_next(company).text)
        company.save()
        self.window.navigate(1)
        houses = self.window.houses_tab
        self.assertIn("Добавить дом", recommend_next(houses).text)
        houses.add_house()
        self.assertEqual(recommend_next(houses).destination, 6)
        houses.notes.setPlainText("Synthetic house change")
        self.window.refresh_context()
        self.assertTrue(self.window.page_header.next_button.isHidden())
        self.assertIn("Сохранить дом", self.window.page_header.recommendation.text())

    def test_property_delete_reasons_follow_dependencies(self):
        company = self.window.company_tab
        company.add_company()
        cid = company.current_id
        self.assertTrue(company.delete_button.isEnabled())
        self.window.navigate(1)
        house = self.window.houses_tab
        house.add_house()
        hid = house.current_id
        self.assertTrue(house.delete_button.isEnabled())
        self.window.navigate(0)
        self.assertFalse(company.delete_button.isEnabled())
        self.assertIn("дома или отчёты", company.delete_hint.text())
        db.create_report(cid, hid, 2026)
        self.window.navigate(1)
        self.assertFalse(house.delete_button.isEnabled())
        self.assertIn("есть отчёты", house.delete_hint.text())
        db.execute("DELETE FROM reports WHERE house_id=?", (hid,))
        house.refresh()
        self.assertTrue(house.delete_button.isEnabled())
        self.assertTrue(house.delete_hint.isHidden())

    def test_signatory_save_failure_keeps_dialog_and_values(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO",))
        calls = []
        def persist(values):
            calls.append(values)
            if len(calls) == 1:
                raise ValueError("Synthetic duplicate")
            return signatories.save_local_signatory(values, company_id=cid)
        editor = signatories.PersonEditor(organization=True, persist=persist)
        self.addCleanup(editor.close)
        editor.fields["full_name"].setText("Synthetic person")
        editor.fields["valid_from"].setText("01.01.2026")
        editor.basis.setCurrentText("Устав")
        editor.show()
        with patch.object(QMessageBox, "warning") as warning:
            editor._save()
        warning.assert_called_once()
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.fields["full_name"].text(), "Synthetic person")
        self.assertIsNone(editor.saved_id)
        editor._save()
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(len(signatories.list_signatories(company_id=cid)), 1)

    def test_signatory_commands_distinguish_read_only_and_local(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO",))
        local = signatories.save_local_signatory(
            {"full_name": "Synthetic local", "valid_from": "2026-01-01", "basis_kind": "Устав"},
            company_id=cid,
        )
        db.execute("INSERT INTO signatories(company_id,assistant_source_id,data_json) VALUES(?,?,?)",
                   (cid, "synthetic-source", '{"full_name":"Synthetic imported"}'))
        dialog = signatories.SignatoryListDialog(company_id=cid)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.list.item(0).data(256), local)
        self.assertEqual(dialog.edit_button.text(), "Изменить")
        self.assertTrue(dialog.delete_button.isEnabled())
        dialog.list.setCurrentRow(1)
        self.assertEqual(dialog.edit_button.text(), "Просмотреть")
        self.assertFalse(dialog.delete_button.isEnabled())

    def test_report_guidance_follows_section_and_review(self):
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO",))
        hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                         (cid, "Synthetic house"))
        self.window.navigate(6)
        page = self.window.reports_tab
        page.reload_filters()
        self.assertIn("Создать отчет", recommend_next(page).text)
        db.create_report(cid, hid, 2026)
        page.refresh()
        page.report_tabs.setCurrentIndex(1)
        self.assertIn("Добавьте работу", recommend_next(page).text)
        page.add_blank_report_row("Содержание")
        self.assertIn("текущему ремонту", recommend_next(page).text)
        page.content_table.set_value(0, 2, "Synthetic work")
        page.report_tabs.setCurrentIndex(5)
        self.assertIn("Проверить заполнение", recommend_next(page).text)
        page.run_review()
        hint = recommend_next(page).text
        self.assertTrue("формат выгрузки" in hint or "Исправьте ошибки" in hint)

    def test_mass_and_backup_guidance_reflect_blocking_state(self):
        mass = self.window.mass_tab
        self.assertEqual(recommend_next(mass).text, mass.preview_button.toolTip())
        backups = self.window.backups_tab
        self.assertIn("Создайте копию", recommend_next(backups).text)
        backups.backup_now()
        backups.archive_list.setCurrentRow(0)
        self.assertIn("Проверить выбранную", recommend_next(backups).text)


if __name__ == "__main__":
    unittest.main()
