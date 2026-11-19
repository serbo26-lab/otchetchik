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

from PySide6.QtWidgets import QApplication

from src.uk728 import assistant_source, db
from src.uk728.assistant_work_list import (
    CONTENT, REPAIR, SKIP, WorkListImportError, apply_selected, read_preview,
)
from src.uk728.assistant_work_list_ui import AssistantWorkListDialog
from src.uk728.report_review import inspect_report


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

    def test_dialog_requires_explicit_section_choice(self):
        dialog = AssistantWorkListDialog(self.report_id)
        try:
            self.assertEqual(dialog.choices[0].currentData(), SKIP)
            self.assertFalse(dialog.choices[3].isEnabled())
            dialog._set_all(CONTENT)
            self.assertEqual(dialog.choices[0].currentData(), CONTENT)
            self.assertEqual(dialog.choices[1].currentData(), CONTENT)
            self.assertEqual(dialog.choices[2].currentData(), SKIP)
            self.assertEqual(dialog.choices[3].currentData(), SKIP)
            dialog._set_all(SKIP)
            self.assertEqual(dialog.choices[0].currentData(), SKIP)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
