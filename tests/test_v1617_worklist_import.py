"""Work-list transfer tests: synthetic data only, never production SQLite."""
from __future__ import annotations

import hashlib
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import assistant_source, db
from src.uk728.assistant_work_list import (
    CONTENT, REPAIR, SKIP, WorkListImportError, apply_selected, read_preview,
)
from src.uk728.assistant_work_list_ui import AssistantWorkListDialog
from src.uk728.report_review import inspect_report
from src.uk728.paths import make_backup


def synthetic_source(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE management_organizations(
                id TEXT PRIMARY KEY, name TEXT, ogrn TEXT
            );
            CREATE TABLE houses(
                id TEXT PRIMARY KEY, organization_id TEXT, address TEXT,
                residential_area TEXT, non_residential_area TEXT,
                charge_area TEXT, management_start_date TEXT
            );
            CREATE TABLE work_lists(
                id TEXT PRIMARY KEY, house_id TEXT, base_year INTEGER,
                start_year INTEGER, start_month INTEGER, end_year INTEGER,
                end_month INTEGER, status TEXT
            );
            CREATE TABLE work_list_items(
                id TEXT PRIMARY KEY, work_list_id TEXT, position INTEGER,
                work_code TEXT, work_name TEXT, unit_name TEXT,
                price TEXT, volume TEXT, quantity TEXT, cost TEXT
            );
        """)
        conn.execute(
            "INSERT INTO management_organizations VALUES(?,?,?)",
            ("uo-example", "Учебная УК", "0000000000000")
        )
        conn.execute(
            "INSERT INTO houses VALUES(?,?,?,?,?,?,?)",
            ("house-example", "uo-example", "Учебный город, дом 1",
             None, None, "750,00", "2020-01-01")
        )
        conn.execute(
            "INSERT INTO work_lists VALUES(?,?,?,?,?,?,?,?)",
            ("list-2025", "house-example", 2025, 2025, 1, 2025, 12, "placed")
        )
        rows = [
            ("item-content", "C-1", "Учебная работа по содержанию", "м²",
             "5,00", "50", "12", "3000,00"),
            ("item-repair", "R-1", "Учебный текущий ремонт", "шт.",
             "100", "2", "1", "200"),
            ("item-unknown-unit", "C-2", "Учебная работа без единицы", "",
             "25", "1", "4", "100"),
            ("item-invalid", "C-3", "Учебный числовой конфликт", "шт.",
             "1", "2", "3", "100"),
        ]
        for pos, item in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO work_list_items VALUES(?,?,?,?,?,?,?,?,?,?)",
                (item[0], "list-2025", pos, *item[1:]),
            )


class WorkListTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        ctx = patch("src.uk728.paths.app_dir", return_value=self.folder)
        ctx.start()
        self.addCleanup(ctx.stop)
        db.init_db()
        self.source = self.folder / "synthetic_assistant.db"
        synthetic_source(self.source)
        assistant_source.synchronize(self.source, save_path=True)
        org = db.row("SELECT id FROM companies WHERE assistant_source_id=?", ("uo-example",))
        house = db.row("SELECT id FROM houses WHERE assistant_source_id=?", ("house-example",))
        self.report_id = db.create_report(org["id"], house["id"], 2025)

    def _backups(self):
        return list(
            (self.folder / "data" / "backups")
            .glob("reporting_728_before_work_list_import_*.db")
        )

    def test_preview_is_readonly_and_uses_both_source_quantity_factors(self):
        before = hashlib.sha256(self.source.read_bytes()).digest()
        preview = read_preview(self.report_id)
        self.assertEqual(len(preview.items), 4)
        self.assertEqual(preview.period, "01.2025–12.2025")
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), before)
        first = preview.items[0]
        self.assertEqual(str(first.unit_price), "5.00")
        self.assertEqual(str(first.plan_qty), "600")
        self.assertEqual(str(first.plan_cost), "3000.00")
        self.assertEqual(preview.items[2].unit, "")
        self.assertTrue(preview.items[3].problem)
        self.assertFalse(self._backups())

    def test_explicit_sections_never_invent_performed_work(self):
        preview = read_preview(self.report_id)
        result = apply_selected(preview, {
            "item-content": (CONTENT, "м²"),
            "item-repair": (REPAIR, "шт."),
        })
        self.assertEqual((result.added_content, result.added_repair), (1, 1))
        bundle = db.load_report_bundle(self.report_id)
        item = bundle["content"][0]
        self.assertEqual(item["name"], "Учебная работа по содержанию")
        self.assertEqual(item["unit_price"], 5)
        self.assertEqual(item["plan_qty"], 600)
        self.assertEqual(item["plan_cost"], 3000)
        self.assertEqual(item["actual_qty"], 0)
        self.assertEqual(item["actual_cost"], 0)
        self.assertIsNone(item["work_item_id"])
        repair = bundle["repair"][0]
        self.assertEqual(repair["cost"], 0)
        self.assertEqual(repair["basis"], "")
        self.assertEqual(repair["volume"], "")
        self.assertEqual(repair["act_details"], "")
        self.assertTrue(any(
            "черновик" in x for x in inspect_report(bundle)["errors"]
        ))
        self.assertEqual(len(self._backups()), 1)

    def test_repeat_import_does_not_duplicate_or_overwrite_manual_edits(self):
        preview = read_preview(self.report_id)
        apply_selected(preview, {"item-content": (CONTENT, "м²")})
        entry = db.row(
            "SELECT * FROM report_content_items WHERE report_id=?", (self.report_id,)
        )
        db.execute(
            "UPDATE report_content_items SET plan_qty=?,plan_cost=?,is_manual=1 "
            "WHERE id=?", (777, 3885, entry["id"])
        )
        retry = read_preview(self.report_id)
        self.assertTrue(retry.items[0].already_imported)
        done = apply_selected(retry, {"item-content": (CONTENT, "м²")})
        self.assertEqual(done.added_content, 0)
        self.assertEqual(done.skipped, 1)
        self.assertEqual(len(self._backups()), 1)
        stored = db.row("SELECT * FROM report_content_items WHERE id=?", (entry["id"],))
        self.assertEqual(stored["plan_qty"], 777)
        self.assertEqual(stored["is_manual"], 1)

    def test_reject_source_changes_after_preview(self):
        preview = read_preview(self.report_id)
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE work_list_items SET cost='3500' WHERE id='item-content'"
            )
        with self.assertRaisesRegex(WorkListImportError, "изменён"):
            apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertFalse(db.rows(
            "SELECT id FROM report_content_items WHERE report_id=?", (self.report_id,)
        ))
        self.assertFalse(self._backups())

    def test_reject_report_changes_after_preview(self):
        preview = read_preview(self.report_id)
        db.execute(
            "INSERT INTO report_content_items(report_id,name,unit,plan_qty) "
            "VALUES(?,?,?,?)",
            (self.report_id, "Локальная работа", "шт.", 2),
        )
        with self.assertRaisesRegex(WorkListImportError, "изменился"):
            apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertEqual(len(db.rows(
            "SELECT id FROM report_content_items WHERE report_id=?", (self.report_id,)
        )), 1)
        self.assertFalse(self._backups())

    def test_missing_units_are_user_supplied_not_guessed(self):
        preview = read_preview(self.report_id)
        with self.assertRaisesRegex(WorkListImportError, "единицу"):
            apply_selected(preview, {"item-unknown-unit": (CONTENT, "")})
        result = apply_selected(preview, {"item-unknown-unit": (CONTENT, "раз")})
        self.assertEqual(result.added_content, 1)
        saved = db.row(
            "SELECT unit,plan_qty FROM report_content_items WHERE report_id=?",
            (self.report_id,),
        )
        self.assertEqual(saved["unit"], "раз")
        self.assertEqual(saved["plan_qty"], 4)

    def test_repair_draft_needs_no_source_unit(self):
        preview = read_preview(self.report_id)
        result = apply_selected(preview, {
            "item-unknown-unit": (REPAIR, ""),
        })
        self.assertEqual(result.added_repair, 1)
        repair = db.row(
            "SELECT name,basis,cost,volume FROM report_repair_items WHERE report_id=?",
            (self.report_id,),
        )
        self.assertEqual(repair["name"], "Учебная работа без единицы")
        self.assertEqual(repair["basis"], "")
        self.assertEqual(repair["cost"], 0)
        self.assertEqual(repair["volume"], "")

    def test_invalid_numeric_row_cannot_be_imported(self):
        preview = read_preview(self.report_id)
        with self.assertRaisesRegex(WorkListImportError, "числовые"):
            apply_selected(preview, {"item-invalid": (CONTENT, "шт.")})
        self.assertFalse(self._backups())

    def test_unrelated_year_and_unlinked_house_are_rejected(self):
        org = db.row("SELECT id FROM companies WHERE assistant_source_id='uo-example'")
        home = db.row("SELECT id FROM houses WHERE assistant_source_id='house-example'")
        older = db.create_report(org["id"], home["id"], 2024)
        with self.assertRaisesRegex(WorkListImportError, "нет перечня"):
            read_preview(older)
        local = db.execute(
            "INSERT INTO houses(company_id,address) VALUES(?,?)",
            (org["id"], "Другой учебный дом"),
        )
        another = db.create_report(org["id"], local, 2025)
        with self.assertRaisesRegex(WorkListImportError, "не связан"):
            read_preview(another)

    def test_partial_report_period_blocks_annual_import(self):
        db.execute(
            "UPDATE reports SET period_end=? WHERE id=?",
            ("2025-11-30", self.report_id),
        )
        with self.assertRaisesRegex(WorkListImportError, "неполный период"):
            read_preview(self.report_id)

    def test_period_change_after_preview_cancels_transfer(self):
        preview = read_preview(self.report_id)
        db.execute(
            "UPDATE reports SET period_end=? WHERE id=?",
            ("2025-11-30", self.report_id),
        )
        with self.assertRaisesRegex(WorkListImportError, "изменился"):
            apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertFalse(self._backups())

    def test_unplaced_and_cancelled_lists_do_not_feed_official_report(self):
        for status in ("local", "gis_draft", "cancelled"):
            with sqlite3.connect(self.source) as conn:
                conn.execute(
                    "UPDATE work_lists SET status=? WHERE id='list-2025'",
                    (status,),
                )
            with self.assertRaisesRegex(WorkListImportError, "Размещён"):
                read_preview(self.report_id)
        self.assertFalse(self._backups())

    def test_partial_source_list_does_not_pretend_to_be_annual(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE work_lists SET end_month=? WHERE id=?",
                (11, "list-2025"),
            )
        with self.assertRaisesRegex(WorkListImportError, "неполный год"):
            read_preview(self.report_id)

    def test_actual_data_are_not_pretended_performed_and_trigger_warning(self):
        original_hash = hashlib.sha256(self.source.read_bytes()).digest()
        preview = read_preview(self.report_id)
        apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), original_hash)
        warnings = inspect_report(db.load_report_bundle(self.report_id))["warnings"]
        self.assertTrue(any(
            "Перечень не подтверждает" in text for text in warnings
        ))

    def test_existing_manual_row_is_preserved_without_name_duplicate(self):
        db.execute(
            "INSERT INTO report_content_items(report_id,name,unit,unit_price,plan_qty,"
            "plan_cost,actual_qty,actual_cost,is_manual) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.report_id, "Учебная работа по содержанию", "м²", 8, 10,
             80, 10, 80, 1),
        )
        preview = read_preview(self.report_id)
        result = apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.added_content, 0)
        row = db.row(
            "SELECT unit_price,plan_qty,actual_qty,is_manual "
            "FROM report_content_items WHERE report_id=?", (self.report_id,),
        )
        self.assertEqual((row["unit_price"], row["plan_qty"], row["actual_qty"]),
                         (8, 10, 10))
        self.assertEqual(row["is_manual"], 1)
        self.assertFalse(self._backups())

    def test_published_or_archived_reports_are_immutable_to_import(self):
        for status in ("Размещен", "Архив"):
            db.execute("UPDATE reports SET status=? WHERE id=?",
                       (status, self.report_id))
            with self.assertRaisesRegex(
                WorkListImportError, "размещённый или архивный"
            ):
                read_preview(self.report_id)
        self.assertFalse(self._backups())

    def test_import_invalidates_previously_verified_status(self):
        db.execute("UPDATE reports SET status='Проверен' WHERE id=?",
                   (self.report_id,))
        preview = read_preview(self.report_id)
        apply_selected(preview, {"item-content": (CONTENT, "м²")})
        status = db.row(
            "SELECT status FROM reports WHERE id=?", (self.report_id,)
        )["status"]
        self.assertEqual(status, "Черновик")

    def test_full_year_in_local_date_format_is_accepted(self):
        db.execute(
            "UPDATE reports SET period_start=?,period_end=? WHERE id=?",
            ("01.01.2025", "31.12.2025", self.report_id),
        )
        preview = read_preview(self.report_id)
        self.assertEqual(preview.year, 2025)

    def test_distinct_source_ids_with_same_name_are_not_duplicates(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE work_list_items SET work_name=(SELECT work_name "
                "FROM work_list_items WHERE id='item-content') "
                "WHERE id='item-unknown-unit'"
            )
        first = read_preview(self.report_id)
        result = apply_selected(first, {"item-content": (CONTENT, "м²")})
        self.assertEqual(result.added_content, 1)
        second = read_preview(self.report_id)
        result = apply_selected(second, {"item-unknown-unit": (CONTENT, "шт.")})
        self.assertEqual(result.added_content, 1)
        retry = read_preview(self.report_id)
        result = apply_selected(retry, {
            "item-content": (CONTENT, "м²"),
            "item-unknown-unit": (CONTENT, "шт."),
        })
        self.assertEqual(result.skipped, 2)
        self.assertEqual(len(db.load_report_bundle(self.report_id)["content"]), 2)

    def test_connected_source_switch_cancels_import_before_backup(self):
        preview = read_preview(self.report_id)
        replacement = self.folder / "synthetic_other_assistant.db"
        synthetic_source(replacement)
        db.execute("UPDATE app_meta SET value=? WHERE key=?",
                   (str(replacement), "assistant_database_path"))
        with self.assertRaisesRegex(WorkListImportError, "Подключённая база"):
            apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertFalse(db.load_report_bundle(self.report_id)["content"])
        self.assertFalse(self._backups())
        fresh = read_preview(self.report_id)
        self.assertEqual(fresh.source_path, replacement.resolve())
        self.assertEqual(apply_selected(fresh, {"item-content": (CONTENT, "м²")}).added_content, 1)

    def test_connected_source_switch_during_backup_cancels_import(self):
        preview = read_preview(self.report_id)
        replacement = self.folder / "synthetic_other_assistant.db"
        synthetic_source(replacement)

        def backup_and_switch(reason):
            result = make_backup(reason)
            db.execute("UPDATE app_meta SET value=? WHERE key=?",
                       (str(replacement), "assistant_database_path"))
            return result

        with patch("src.uk728.assistant_work_list.make_backup", side_effect=backup_and_switch):
            with self.assertRaisesRegex(WorkListImportError, "Подключённая база"):
                apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertFalse(db.load_report_bundle(self.report_id)["content"])

    def test_house_link_changed_during_backup_cancels_import(self):
        preview = read_preview(self.report_id)

        def backup_and_change_link(reason):
            result = make_backup(reason)
            db.execute("UPDATE houses SET assistant_source_id=? WHERE assistant_source_id=?",
                       ("different-example", "house-example"))
            return result

        with patch("src.uk728.assistant_work_list.make_backup", side_effect=backup_and_change_link):
            with self.assertRaisesRegex(WorkListImportError, "изменился"):
                apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertFalse(db.load_report_bundle(self.report_id)["content"])

    def test_source_changed_during_backup_cancels_import(self):
        preview = read_preview(self.report_id)

        def backup_and_change_source(reason):
            result = make_backup(reason)
            with sqlite3.connect(self.source) as conn:
                conn.execute("UPDATE work_list_items SET price='6', cost='3600' WHERE id='item-content'")
            return result

        with patch("src.uk728.assistant_work_list.make_backup", side_effect=backup_and_change_source):
            with self.assertRaisesRegex(WorkListImportError, "изменён"):
                apply_selected(preview, {"item-content": (CONTENT, "м²")})
        self.assertFalse(db.load_report_bundle(self.report_id)["content"])

    def test_second_section_failure_rolls_back_first_and_keeps_verified_status(self):
        db.execute("UPDATE reports SET status='Проверен' WHERE id=?", (self.report_id,))
        db.execute("""CREATE TRIGGER synthetic_import_failure
            BEFORE INSERT ON report_repair_items
            BEGIN SELECT RAISE(ABORT, 'synthetic import rollback'); END;""")
        original = hashlib.sha256(self.source.read_bytes()).digest()
        preview = read_preview(self.report_id)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic import rollback"):
            apply_selected(preview, {
                "item-content": (CONTENT, "м²"),
                "item-repair": (REPAIR, "шт."),
            })
        bundle = db.load_report_bundle(self.report_id)
        self.assertFalse(bundle["content"])
        self.assertFalse(bundle["repair"])
        self.assertEqual(bundle["report"]["status"], "Проверен")
        self.assertEqual(len(self._backups()), 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), original)

    def test_dialog_preserves_imported_and_invalid_row_tooltips(self):
        apply_selected(read_preview(self.report_id), {"item-content": (CONTENT, "м²")})
        dialog = AssistantWorkListDialog(self.report_id)
        try:
            self.assertFalse(dialog.choices[0].isEnabled())
            self.assertFalse(dialog.choices[3].isEnabled())
            self.assertIn("уже была перенесена", dialog.table.item(0, 1).toolTip())
            self.assertIn("Стоимость не совпадает", dialog.table.item(3, 1).toolTip())
            self.assertEqual(dialog.table.item(2, 2).text(), "")
            self.assertFalse(dialog.apply_button.isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.application.processEvents()

    def test_dialog_keeps_selection_on_storage_failure(self):
        dialog = AssistantWorkListDialog(self.report_id)
        try:
            dialog._set_all(CONTENT)
            selected = [choice.currentData() for choice in dialog.choices]
            for error in (sqlite3.OperationalError("synthetic locked database"),
                          RuntimeError("synthetic backup failure")):
                with self.subTest(error=error):
                    with patch("src.uk728.assistant_work_list_ui.apply_selected", side_effect=error):
                        with patch.object(QMessageBox, "warning") as warning:
                            with patch.object(QMessageBox, "information") as success:
                                dialog._apply()
                                warning.assert_called_once()
                                success.assert_not_called()
                    self.assertIsNone(dialog.import_result)
                    self.assertEqual([choice.currentData() for choice in dialog.choices], selected)
            self.assertEqual(db.row("SELECT COUNT(*) FROM report_content_items WHERE report_id=?",
                                    (self.report_id,))[0], 0)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.application.processEvents()

    def test_dialog_requires_explicit_section_choice(self):
        dialog = AssistantWorkListDialog(self.report_id)
        try:
            self.assertEqual(dialog.choices[0].currentData(), SKIP)
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertFalse(dialog.choices[3].isEnabled())
            dialog._set_all(CONTENT)
            self.assertTrue(dialog.apply_button.isEnabled())
            self.assertEqual(dialog.choices[0].currentData(), CONTENT)
            self.assertEqual(dialog.choices[1].currentData(), CONTENT)
            self.assertEqual(dialog.choices[2].currentData(), SKIP)
            self.assertEqual(dialog.choices[3].currentData(), SKIP)
            dialog._set_all(SKIP)
            self.assertEqual(dialog.choices[0].currentData(), SKIP)
            self.assertFalse(dialog.apply_button.isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
