"""Audit of field activation, table geometry and persistence at desktop scale.

The app is never tested against a real address or personally identifiable data.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from src.uk728 import db
from src.uk728.app import MainWindow, SafeSpinBox


class FieldAndLayoutAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.window = MainWindow()
        self.window.resize(1440, 900)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(self.window.close)

    def set_up_company_and_house(self):
        company = self.window.company_tab
        if company.current_id is None:
            company.add_company()
        company.name.setText("УК Макетная")
        company.ogrn.setText("0000000000000")
        company.addr.setPlainText("ул. Тестовая, д. 1")
        company.person.setText("Специалист по отчётности")
        company.contact.setText("example@example.test")
        company.notes.setPlainText("Только тестовые данные")
        company.save(refresh_after=False)
        cid = company.current_id
        house = self.window.houses_tab
        house.reload_companies()
        house.add_house()
        house.address.setPlainText("г. Примерск, ул. Образцовая, д. 7")
        house.area.setText("1234,50")
        house.start.setText("01.01.2026")
        house.status.setCurrentText("В управлении")
        house.notes.setPlainText("Выдуманный объект")
        house.save(refresh_after=False)
        return cid, house.current_id

    def test_unselected_company_has_explicit_action_not_a_dead_form(self):
        page = self.window.company_tab
        self.assertEqual(page.editor_stack.currentIndex(), 0)
        self.assertFalse(page.name.isEnabled())
        self.assertTrue(page.editor_stack.empty_add_button.isEnabled())
        page.editor_stack.empty_add_button.click()
        self.assertEqual(page.editor_stack.currentIndex(), 1)
        for field in (page.name, page.ogrn, page.addr, page.person, page.contact, page.notes):
            self.assertTrue(field.isEnabled(), field.objectName())
        page.name.setText("УК Первый дом")
        page.person.setText("Ответственный сотрудник, должность")
        page.save(refresh_after=False)
        saved = db.row("SELECT * FROM companies WHERE id=?", (page.current_id,))
        self.assertEqual(saved["name"], "УК Первый дом")
        self.assertEqual(saved["report_person"], "Ответственный сотрудник, должность")

    def test_house_fields_only_unlock_with_selected_company_and_house(self):
        page = self.window.houses_tab
        self.assertEqual(page.editor_stack.currentIndex(), 0)
        self.assertFalse(page.editor_stack.empty_add_button.isEnabled())
        self.assertFalse(page.address.isEnabled())
        cid, hid = self.set_up_company_and_house()
        self.assertEqual(page.editor_stack.currentIndex(), 1)
        self.assertFalse(page.company.isEnabled())  # Ownership is read-only.
        self.assertEqual(page.company.currentData(), cid)
        for field in (page.address, page.area, page.start, page.end, page.status, page.notes):
            self.assertTrue(field.isEnabled())
        house = db.row("SELECT * FROM houses WHERE id=?", (hid,))
        self.assertEqual(house["total_area"], 1234.5)
        self.assertIn("Образцовая", house["address"])
        self.assertEqual(house["status"], "В управлении")

    def test_categories_all_inputs_and_subcategories_are_saved(self):
        page = self.window.categories_tab
        self.assertIsNotNone(page.current_id)
        for field in (page.name, page.section, page.unit, page.price, page.notes, page.sub_table):
            self.assertTrue(field.isEnabled())
        page.name.setText("Инженерные системы")
        page.section.setCurrentText("Содержание")
        page.unit.setCurrentText("м²")
        page.price.setText("9,90")
        start_rows = page.sub_table.rowCount()
        page.add_subcategory()
        self.assertEqual(page.sub_table.rowCount(), start_rows + 1)
        page.sub_table.set_value(start_rows, 1, "Проверка оборудования")
        page.save(refresh_after=False)
        saved = db.row("SELECT * FROM work_categories WHERE id=?", (page.current_id,))
        self.assertEqual(saved["name"], "Инженерные системы")
        self.assertEqual(saved["default_price"], 9.9)
        self.assertIsNotNone(db.row(
            "SELECT id FROM work_categories WHERE parent_id=? AND name=?",
            (page.current_id, "Проверка оборудования"),
        ))

    def test_default_single_template_is_selected_and_fully_editable(self):
        page = self.window.templates_tab
        self.assertIsNotNone(page.current_id)
        self.assertEqual(page.editor_stack.currentIndex(), 1)
        for field in (page.name, page.category, page.notes, page.table, page.save_button):
            self.assertTrue(field.isEnabled())
        tid = page.current_id
        page.name.setText("Исправленный базовый шаблон")
        page.notes.setPlainText("Нормальные поля")
        page.save(refresh_after=False)
        saved = db.row("SELECT * FROM templates WHERE id=?", (tid,))
        self.assertEqual(saved["name"], "Исправленный базовый шаблон")
        self.assertEqual(saved["notes"], "Нормальные поля")

    def test_work_directory_columns_are_editable_and_persist(self):
        page = self.window.works_tab
        self.assertTrue(page.table.isEnabled())
        row = page.table.rowCount()
        page.add_row()
        self.assertEqual(page.table.rowCount(), row + 1)
        page.table.set_value(row, 1, "Содержание")
        page.table.set_value(row, 2, "Уборка")
        page.table.set_value(row, 3, "Влажная уборка лестниц")
        page.table.set_value(row, 4, "Содержание")
        page.table.set_value(row, 5, "м²")
        page.table.set_value(row, 6, "8,25")
        page.table.set_value(row, 7, "Раз в месяц")
        page.save()
        record = db.row("SELECT * FROM work_items WHERE name=?", ("Влажная уборка лестниц",))
        self.assertEqual(record["default_price"], 8.25)
        self.assertEqual(record["category"], "Содержание")
        self.assertEqual(record["notes"], "Раз в месяц")

    def test_clipboard_append_creates_complete_report_rows(self):
        cid, hid = self.set_up_company_and_house()
        rid = db.create_report(cid, hid, 2026)
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        self.assertEqual(page.current_id, rid)
        page.add_blank_report_row("Содержание")
        page.content_table.setCurrentCell(0, 2)
        QGuiApplication.clipboard().setText(
            "Содержание входной группы\tм²\t5\t10\t50\t8\t40\n"
            "Уборка лестниц\tм²\t2\t20\t40\t15\t30"
        )
        page.content_table.paste_selection()
        self.assertEqual(page.content_table.rowCount(), 2)
        self.assertIsNotNone(page.content_table.item(1, 0))
        page.flush_autosave()
        saved = db.rows("SELECT * FROM report_content_items WHERE report_id=? ORDER BY position", (rid,))
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["name"], "Содержание входной группы")
        self.assertEqual(saved[1]["name"], "Уборка лестниц")
        self.assertEqual(saved[1]["actual_cost"], 30)

    def test_finance_has_fixed_payer_names_even_after_reload_and_paste(self):
        cid, hid = self.set_up_company_and_house()
        rid = db.create_report(cid, hid, 2026)
        page = self.window.reports_tab
        page.reload_filters()
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        self.assertEqual(page.current_id, rid)
        payer = page.finance_table.item(0, 1)
        self.assertFalse(bool(payer.flags() & Qt.ItemFlag.ItemIsEditable))
        page.finance_table.setCurrentCell(0, 1)
        QGuiApplication.clipboard().setText("Случайный плательщик\t250\nПлательщик 2\t10\nТретий\t1")
        with patch.object(QMessageBox, "warning") as warning:
            page.finance_table.paste_selection()
        warning.assert_called_once()
        self.assertEqual(page.finance_table.rowCount(), 2)
        self.assertEqual(page.finance_table.value(0, 1), "Собственники")
        self.assertEqual(page.finance_table.value(1, 1), "Наниматели")
        page.flush_autosave()
        self.assertEqual(db.row(
            "SELECT COUNT(*) AS n FROM report_financials WHERE report_id=?", (rid,)
        )["n"], 2)

    def test_report_controls_and_all_five_official_sections_are_active(self):
        cid, hid = self.set_up_company_and_house()
        page = self.window.reports_tab
        page.reload_filters()
        self.assertFalse(page.save_button.isEnabled())
        self.assertFalse(page.report_tabs.isVisibleTo(page))
        page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
        page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
        rid = db.create_report(cid, hid, 2026)
        page.refresh()
        self.assertEqual(page.current_id, rid)
        self.assertTrue(page.save_button.isEnabled())
        self.assertTrue(page.report_tabs.isEnabled())
        self.assertTrue(page.save_panel.isVisibleTo(page))
        for field in (
            page.year, page.period_start, page.period_end, page.show_period,
            page.publish, page.status, page.notes,
            page.content_table, page.repair_start, page.repair_accrual,
            page.repair_work_cost, page.repair_end, page.repair_table,
            page.management_cost, page.management_table, page.claim_pret,
            page.claim_lawsuits, page.claim_amount, page.finance_table,
        ):
            self.assertTrue(field.isEnabled(), field.__class__.__name__)
        page.management_cost.setText("150,75")
        page.claim_pret.setText("2")
        page.claim_lawsuits.setText("1")
        page.claim_amount.setText("55,25")
        page.finance_table.set_value(0, 3, "10,5")
        page.save(silent=True, refresh_after=False)
        bundle = db.load_report_bundle(rid)
        self.assertEqual(bundle["report"]["management_cost"], 150.75)
        self.assertEqual(bundle["claims"]["pretensions_count"], 2)
        self.assertEqual(bundle["claims"]["lawsuits_count"], 1)
        self.assertEqual(bundle["claims"]["recovered_amount"], 55.25)
        self.assertEqual(next(f for f in bundle["financials"]
                         if f["payer_type"] == "Собственники")["accrued"], 10.5)

    def test_mass_mode_shows_only_the_relevant_numeric_fields(self):
        self.set_up_company_and_house()
        page = self.window.mass_tab
        page.reload()
        self.assertTrue(page.year.isEnabled())
        self.assertIsInstance(page.year, SafeSpinBox)
        for mode in ("area", "monthly", "fixed", "split", "quantity"):
            index = page.mode.findData(mode)
            self.assertGreaterEqual(index, 0)
            page.mode.setCurrentIndex(index)
            self.assertEqual(not page.rate.isHidden(), mode in ("area", "monthly", "quantity"))
            self.assertEqual(not page.months.isHidden(), mode == "monthly")
            self.assertEqual(not page.quantity.isHidden(), mode == "quantity")
            self.assertEqual(not page.total.isHidden(), mode in ("fixed", "split"))

    def test_backup_archive_has_sensible_empty_and_populated_states(self):
        page = self.window.backups_tab
        page.refresh()
        self.assertFalse(page.archive_list.isVisibleTo(page))
        self.assertTrue(page.empty_archive_hint.isVisibleTo(page))
        page.backup_now()
        page.refresh()
        self.assertEqual(page.archive_list.count(), 1)
        self.assertFalse(page.empty_archive_hint.isVisibleTo(page))
        self.assertTrue(page.archive_list.isVisibleTo(page))

    def test_full_width_tables_and_legible_form_labels(self):
        self.set_up_company_and_house()
        tabs = [
            (2, self.window.categories_tab.sub_table),
            (3, self.window.works_tab.table),
            (4, self.window.templates_tab.table),
        ]
        for index, table in tabs:
            self.window.navigate(index)
            self.qt.processEvents()
            width = sum(table.columnWidth(col) for col in range(table.columnCount())
                        if not table.isColumnHidden(col))
            viewport = table.viewport().width()
            self.assertGreater(viewport, 450, (index, viewport))
            self.assertGreaterEqual(width, viewport - 15,
                                    (index, width, viewport))
        self.window.navigate(0)
        self.qt.processEvents()
        labels = [label for label in self.window.company_tab.findChildren(QLabel)
                  if label.text() in ("Полное наименование", "Адрес приема населения")]
        self.assertEqual(len(labels), 2)
        for label in labels:
            self.assertGreaterEqual(label.minimumWidth(), 165)
            self.assertFalse(label.wordWrap())

    def test_sidebar_titles_have_full_text_tooltips(self):
        sidebar = self.window.sidebar
        self.assertGreaterEqual(sidebar.width(), 278)
        for index in (0, 7):
            item = sidebar.navigation.item(index)
            self.assertIn(item.text(), item.toolTip())


if __name__ == "__main__":
    unittest.main()
