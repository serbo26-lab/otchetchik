"""Stage C: practical guided workflows for UO, houses, categories, works, templates."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSplitter
from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.ui_components import ActionPanel, ContentCard


class DirectoryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def titles(self, page):
        return [label.text() for card in page.findChildren(ContentCard)
                for label in card.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly)
                if label.objectName() == "CardTitle"]

    def test_first_five_sections_have_guided_sequence(self):
        expectations = [
            ("company_tab", ["1. Выбор организации", "2. Основные сведения", "3. Сведения для разъяснений по отчету"]),
            ("houses_tab", ["1. Управляющая организация", "2. Перечень домов", "3. Сведения о доме", "4. Период управления"]),
            ("categories_tab", ["1. Выберите категорию", "2. Параметры категории", "3. Подкатегории"]),
            ("works_tab", ["1. Выберите источник работы", "2. Справочник работ и услуг"]),
            ("templates_tab", ["1. Выберите шаблон", "2. Основные сведения шаблона", "3. Наполнение шаблона"]),
        ]
        for attr, headings in expectations:
            page = getattr(self.window, attr)
            found = self.titles(page)
            for heading in headings:
                self.assertIn(heading, found, attr)
            # Two-column screens are navigated from left to right. Within each
            # column the numbered steps must follow their actual visual order.
            splits = page.findChildren(QSplitter)
            areas = ([splits[0].widget(0), splits[0].widget(1)] if splits else [page])
            for area in areas:
                local_titles = self.titles(area)
                steps = [int(t.split(".", 1)[0]) for t in local_titles
                         if t[:1].isdigit() and "." in t]
                self.assertEqual(steps, sorted(steps), (attr, local_titles))

    def test_empty_forms_disable_edit_and_destructive_operations(self):
        company = self.window.company_tab
        house = self.window.houses_tab
        category = self.window.categories_tab
        template = self.window.templates_tab
        self.assertFalse(company.name.isEnabled())
        self.assertFalse(company.delete_button.isEnabled())
        self.assertFalse(house.add_button.isEnabled())
        self.assertFalse(house.delete_button.isEnabled())
        # The application ships with sample categories. Explicitly clear the
        # selection to verify disabled-state behavior regardless of seed data.
        category.list.setCurrentRow(-1)
        self.assertIsNone(category.current_id)
        self.assertFalse(category.add_sub_button.isEnabled())
        self.assertFalse(category.delete_sub_button.isEnabled())
        self.assertFalse(category.save_button.isEnabled())
        self.assertFalse(category.delete_button.isEnabled())
        self.assertFalse(template.add_item_button.isEnabled())
        self.assertFalse(template.remove_item_button.isEnabled())
        self.assertFalse(template.delete_button.isEnabled())
        self.assertFalse(template.save_button.isEnabled())

    def test_organization_edit_saved_when_creating_another(self):
        tab = self.window.company_tab
        tab.add_company()
        first = tab.current_id
        tab.name.setText("Первая управляющая организация")
        tab.ogrn.setText("1000000000000")
        tab.add_company()
        self.assertNotEqual(first, tab.current_id)
        original = db.row("SELECT * FROM companies WHERE id=?", (first,))
        self.assertEqual(original["name"], "Первая управляющая организация")
        self.assertEqual(original["ogrn_inn"], "1000000000000")

    def test_house_selection_requires_organization_and_preserves_existing_edits(self):
        c1 = db.execute("INSERT INTO companies(name) VALUES(?)", ("УО 1",))
        c2 = db.execute("INSERT INTO companies(name) VALUES(?)", ("УО 2",))
        tab = self.window.houses_tab
        tab.reload()
        self.assertIsNone(tab.company_filter.currentData())
        self.assertFalse(tab.add_button.isEnabled())
        self.assertEqual(tab.list.count(), 0)
        tab.company_filter.setCurrentIndex(tab.company_filter.findData(c1))
        self.assertTrue(tab.add_button.isEnabled())
        tab.add_house()
        house_id = tab.current_id
        tab.address.setPlainText("КЧР, дом 17")
        tab.area.setText("1400")
        tab.company_filter.setCurrentIndex(tab.company_filter.findData(c2))
        self.assertIsNone(tab.current_id)
        row = db.row("SELECT address,total_area FROM houses WHERE id=?", (house_id,))
        self.assertEqual(row["address"], "КЧР, дом 17")
        self.assertEqual(row["total_area"], 1400.0)
        self.assertFalse(tab.delete_button.isEnabled())

    def test_category_subcategories_created_only_after_selection(self):
        page = self.window.categories_tab
        page.add_category()
        page.name.setText("Управление домом")
        page.section.setCurrentText("Управление")
        page.add_subcategory()
        page.sub_table.set_value(0, 1, "Сбор показаний")
        page.save(refresh_after=False)
        category = db.row("SELECT * FROM work_categories WHERE id=?", (page.current_id,))
        self.assertEqual(category["section"], "Управление")
        records = db.rows("SELECT * FROM work_categories WHERE parent_id=? AND is_active=1", (page.current_id,))
        self.assertEqual(len(records), 1)
        page.save(refresh_after=False)
        self.assertEqual(len(db.rows("SELECT * FROM work_categories WHERE parent_id=? AND is_active=1", (page.current_id,))), 1)

    def test_dictionary_add_from_category_and_manual(self):
        cid = db.execute("INSERT INTO work_categories(name,section,unit,default_price) VALUES(?,?,?,?)",
                         ("Уборка", "Содержание", "м²", 4.4))
        work = self.window.works_tab
        work.reload()
        self.assertFalse(work.add_from_category_button.isEnabled())
        work.category_combo.setCurrentIndex(work.category_combo.findData(cid))
        self.assertTrue(work.add_from_category_button.isEnabled())
        work.add_from_category()
        created_index = work.table.rowCount() - 1
        self.assertEqual(work.table.value(created_index, 3), "Уборка")
        self.assertEqual(work.table.value(created_index, 4), "Содержание")
        work.add_row()
        manual_index = work.table.rowCount() - 1
        work.table.set_value(manual_index, 3, "Новая услуга")
        work.table.set_value(manual_index, 4, "Управление")
        previous_count = len(db.rows("SELECT id FROM work_items WHERE is_active=1"))
        work.save()
        self.assertEqual(len(db.rows("SELECT id FROM work_items WHERE is_active=1")), previous_count + 2)

    def test_template_filters_by_section_and_prevents_duplicates(self):
        w1 = db.execute("INSERT INTO work_items(name,category,unit,default_price) VALUES(?,?,?,?)",
                        ("Уборка", "Содержание", "м²", 3))
        w2 = db.execute("INSERT INTO work_items(name,category,unit,default_price) VALUES(?,?,?,?)",
                        ("Ремонт", "Текущий ремонт", "шт.", 400))
        tab = self.window.templates_tab
        tab.reload()
        tab.add_template()
        self.assertIsNotNone(tab.current_id)
        self.assertGreaterEqual(tab.work_combo.findData(w1), 0)
        self.assertEqual(tab.work_combo.findData(w2), -1)
        self.assertTrue(tab.add_item_button.isEnabled())
        tab.work_combo.setCurrentIndex(tab.work_combo.findData(w1))
        tab.add_work_to_template()
        self.assertEqual(tab.table.rowCount(), 1)
        tab.add_work_to_template()
        self.assertEqual(tab.table.rowCount(), 1)
        tab.table.set_value(0, 5, "12")
        tab.save(refresh_after=False)
        self.assertEqual(len(db.rows("SELECT * FROM template_items WHERE template_id=?", (tab.current_id,))), 1)
        tab.category.setCurrentText("Текущий ремонт")
        self.assertEqual(tab.work_combo.findData(w1), -1)
        self.assertGreaterEqual(tab.work_combo.findData(w2), 0)

    def test_destructive_buttons_are_separate_from_creation(self):
        for page in (self.window.company_tab, self.window.houses_tab,
                     self.window.categories_tab, self.window.templates_tab):
            panels = page.findChildren(ActionPanel)
            self.assertTrue(any(p.property("purpose") == "danger" for p in panels))
            self.assertTrue(any(any(b.objectName() == "PrimaryAction"
                                    for b in p.findChildren(QPushButton))
                                for p in panels if p.property("purpose") != "danger"))
            for p in panels:
                if p.property("purpose") == "danger":
                    self.assertFalse(any(b.objectName() == "PrimaryAction"
                                         for b in p.findChildren(QPushButton)))


if __name__ == "__main__":
    unittest.main()
