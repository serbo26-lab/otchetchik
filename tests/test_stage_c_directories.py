"""Stage C: practical guided workflows for UO, houses, categories, works, templates."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QSplitter
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
        # A sole default template is now selected for immediate editing.
        # Clear it explicitly to verify genuinely empty form behavior.
        template.list.setCurrentRow(-1)
        self.assertIsNone(template.current_id)
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

    def test_template_deletion_preserves_remaining_template_items(self):
        tab = self.window.templates_tab
        db.execute("DELETE FROM templates")
        deleted_id = db.execute("INSERT INTO templates(name,category) VALUES(?,?)",
                                ("Synthetic deleted", "Содержание"))
        kept_id = db.execute("INSERT INTO templates(name,category) VALUES(?,?)",
                             ("Synthetic retained", "Содержание"))
        item_id = db.execute(
            "INSERT INTO template_items(template_id,name,unit,unit_price,plan_qty) VALUES(?,?,?,?,?)",
            (kept_id, "Synthetic retained item", "шт.", 4, 2),
        )
        tab.refresh()
        tab.list.setCurrentRow(next(i for i in range(tab.list.count())
                                    if tab.list.item(i).data(Qt.UserRole) == deleted_id))
        self.assertEqual(tab.current_id, deleted_id)
        tab.notes.setPlainText("Synthetic unsaved edit to deleted template")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(QMessageBox, "warning") as warning:
            tab.delete_template()
            warning.assert_not_called()
        self.assertIsNone(db.row("SELECT id FROM templates WHERE id=?", (deleted_id,)))
        self.assertEqual(tab.current_id, kept_id)
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertEqual(tab.table.value(0, 0), str(item_id))
        self.assertFalse(tab.has_unsaved_changes())
        tab.notes.setPlainText("Synthetic retained edit")
        tab.save(refresh_after=False)
        self.assertIsNotNone(db.row("SELECT id FROM template_items WHERE id=?", (item_id,)))

    def test_category_deletion_preserves_next_category_subcategories(self):
        tab = self.window.categories_tab
        deleted_id = db.execute("INSERT INTO work_categories(name,section) VALUES(?,?)",
                                ("001 Synthetic deleted", "Содержание"))
        kept_id = db.execute("INSERT INTO work_categories(name,section) VALUES(?,?)",
                             ("000 Synthetic retained", "Содержание"))
        sub_id = db.execute(
            "INSERT INTO work_categories(parent_id,name,section) VALUES(?,?,?)",
            (kept_id, "Synthetic retained subcategory", "Содержание"),
        )
        tab.refresh()
        tab.list.setCurrentRow(next(i for i in range(tab.list.count())
                                    if tab.list.item(i).data(Qt.UserRole) == deleted_id))
        self.assertEqual(tab.current_id, deleted_id)
        tab.notes.setPlainText("Synthetic unsaved edit to deleted category")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(QMessageBox, "warning") as warning:
            tab.delete_category()
            warning.assert_not_called()
        self.assertEqual(db.row("SELECT is_active FROM work_categories WHERE id=?", (deleted_id,))[0], 0)
        self.assertEqual(tab.current_id, kept_id)
        self.assertEqual(tab.sub_table.rowCount(), 1)
        self.assertEqual(tab.sub_table.value(0, 0), str(sub_id))
        self.assertFalse(tab.has_unsaved_changes())
        tab.notes.setPlainText("Synthetic retained edit")
        tab.save(refresh_after=False)
        self.assertEqual(db.row("SELECT is_active FROM work_categories WHERE id=?", (sub_id,))[0], 1)

    def test_template_sqlite_failure_keeps_edits_for_retry(self):
        tab = self.window.templates_tab
        tab.reload()
        tab.add_template()
        template_id = tab.current_id
        original = db.row("SELECT name FROM templates WHERE id=?", (template_id,))[0]
        tab.name.setText("Synthetic pending template")
        with patch("src.uk728.templates_ui.connect", side_effect=sqlite3.OperationalError("synthetic locked database")):
            with patch.object(QMessageBox, "warning") as warning:
                self.assertIs(tab.save(refresh_after=False), False)
                warning.assert_called_once()
        self.assertEqual(tab.current_id, template_id)
        self.assertTrue(tab.has_unsaved_changes())
        self.assertEqual(tab.name.text(), "Synthetic pending template")
        self.assertEqual(db.row("SELECT name FROM templates WHERE id=?", (template_id,))[0], original)
        tab.save(refresh_after=False)
        self.assertEqual(db.row("SELECT name FROM templates WHERE id=?", (template_id,))[0],
                         "Synthetic pending template")

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

    def test_subcategory_save_rejects_invalid_row_identity_and_empty_name(self):
        page = self.window.categories_tab
        parent = db.execute("INSERT INTO work_categories(name,section) VALUES(?,?)",
                            ("Synthetic parent", "Содержание"))
        other = db.execute("INSERT INTO work_categories(name,section) VALUES(?,?)",
                           ("Synthetic other", "Содержание"))
        child = db.execute("INSERT INTO work_categories(parent_id,name,section) VALUES(?,?,?)",
                           (parent, "Synthetic child", "Содержание"))
        foreign = db.execute("INSERT INTO work_categories(parent_id,name,section) VALUES(?,?,?)",
                             (other, "Synthetic foreign", "Содержание"))
        page.refresh()
        page.list.setCurrentRow(next(i for i in range(page.list.count())
                                     if page.list.item(i).data(Qt.UserRole) == parent))
        page.notes.setPlainText("Synthetic pending edit")
        for row_id, name in ((foreign, "Synthetic child"), (child, "   ")):
            with self.subTest(row_id=row_id, name=name):
                page.sub_table.set_value(0, 0, row_id)
                page.sub_table.set_value(0, 1, name)
                with patch.object(QMessageBox, "warning") as warning:
                    self.assertIs(page.save(refresh_after=False), False)
                    warning.assert_called_once()
                self.assertEqual(db.row("SELECT notes FROM work_categories WHERE id=?", (parent,))[0], "")
                self.assertEqual(db.row("SELECT parent_id FROM work_categories WHERE id=?", (foreign,))[0], other)
                self.assertEqual(db.row("SELECT is_active FROM work_categories WHERE id=?", (child,))[0], 1)
        page.sub_table.set_value(0, 0, child)
        page.sub_table.set_value(0, 1, "Synthetic child")
        page.add_subcategory()
        page.sub_table.set_value(1, 0, child)
        with patch.object(QMessageBox, "warning"):
            self.assertIs(page.save(refresh_after=False), False)
        page.sub_table.removeRow(1)
        page.save(refresh_after=False)
        self.assertEqual(db.row("SELECT notes FROM work_categories WHERE id=?", (parent,))[0],
                         "Synthetic pending edit")

    def test_subcategory_save_rejects_changed_composition_after_open(self):
        page = self.window.categories_tab
        parent = db.execute("INSERT INTO work_categories(name,section) VALUES(?,?)",
                            ("Synthetic parent", "Содержание"))
        page.refresh()
        page.list.setCurrentRow(next(i for i in range(page.list.count())
                                     if page.list.item(i).data(Qt.UserRole) == parent))
        new_id = db.execute("INSERT INTO work_categories(parent_id,name,section) VALUES(?,?,?)",
                            (parent, "Synthetic added externally", "Содержание"))
        page.notes.setPlainText("Synthetic pending edit")
        with patch.object(QMessageBox, "warning") as warning:
            self.assertIs(page.save(refresh_after=False), False)
            warning.assert_called_once()
        self.assertEqual(db.row("SELECT is_active FROM work_categories WHERE id=?", (new_id,))[0], 1)
        self.assertEqual(db.row("SELECT notes FROM work_categories WHERE id=?", (parent,))[0], "")
        self.assertTrue(page.has_unsaved_changes())
        page.notes.clear()
        page.refresh()
        self.assertEqual(page.sub_table.value(0, 0), str(new_id))
        page.notes.setPlainText("Synthetic retried edit")
        page.save(refresh_after=False)
        self.assertEqual(db.row("SELECT notes FROM work_categories WHERE id=?", (parent,))[0],
                         "Synthetic retried edit")

    def test_directory_validation_rejects_changes_before_saving(self):
        category = self.window.categories_tab
        category.add_category()
        cid = category.current_id
        category.name.setText("Synthetic category")
        category.section.setCurrentText("Содержание")
        category.add_subcategory()
        category.sub_table.set_value(0, 1, "Synthetic subcategory")
        category.sub_table.set_value(0, 4, "invalid")
        original = dict(db.row("SELECT * FROM work_categories WHERE id=?", (cid,)))
        with patch("src.uk728.app.QMessageBox.warning"):
            self.assertIs(category.save(refresh_after=False), False)
        self.assertEqual(dict(db.row("SELECT * FROM work_categories WHERE id=?", (cid,))), original)
        self.assertFalse(db.rows("SELECT id FROM work_categories WHERE parent_id=?", (cid,)))
        category.sub_table.set_value(0, 4, "10")
        category.save(refresh_after=False)
        row_id = int(category.sub_table.value(0, 0))
        self.assertEqual(db.row("SELECT default_price FROM work_categories WHERE id=?", (row_id,))[0], 10)

        work = self.window.works_tab
        work.reload()
        work.add_row()
        index = work.table.rowCount() - 1
        work.table.set_value(index, 3, "Synthetic invalid work")
        work.table.set_value(index, 4, "Содержание")
        work.table.set_value(index, 6, "invalid")
        before = len(db.rows("SELECT id FROM work_items"))
        with patch("src.uk728.app.QMessageBox.warning"):
            self.assertIs(work.save(), False)
        self.assertEqual(len(db.rows("SELECT id FROM work_items")), before)
        work.table.set_value(index, 6, "7")
        work.save()
        self.assertEqual(len(db.rows("SELECT id FROM work_items")), before + 1)

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
