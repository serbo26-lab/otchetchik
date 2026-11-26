"""Backup cadence and read-only Assistant refresh (synthetic values only).

Do not use real houses, addresses, personal details, or user SQLite files in CI.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.uk728 import assistant_source, db
from src.uk728.app import MainWindow
from src.uk728.paths import make_backup
from src.uk728.report_review import inspect_report


def source_fixture(path: Path, *, no_areas: bool = False, no_start: bool = False) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE management_organizations(
                id TEXT PRIMARY KEY, name TEXT, ogrn TEXT
            );
            CREATE TABLE houses(
                id TEXT PRIMARY KEY, organization_id TEXT, address TEXT,
                residential_area TEXT, non_residential_area TEXT,
                charge_area TEXT, management_start_date TEXT, management_end_date TEXT
            );
        """)
        conn.execute(
            "INSERT INTO management_organizations VALUES(?,?,?)",
            ("uo-example", "УК Тест", "0000000000000"),
        )
        conn.execute(
            "INSERT INTO houses VALUES(?,?,?,?,?,?,?,?)",
            ("house-example", "uo-example", "г. Тестовый, дом 1",
             None if no_areas else "1000,5",
             None if no_areas else "200,25",
             "1200,75" if not no_areas else None,
             None if no_start else "2021-07-01", None),
        )
        conn.commit()


class BackupPolicyV1616Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        directory_patch = patch("src.uk728.paths.app_dir", return_value=self.folder)
        directory_patch.start()
        self.addCleanup(directory_patch.stop)
        db.init_db()
        self.source = self.folder / "synthetic_assistant.db"
        source_fixture(self.source)

    def backups(self, reason=None):
        pattern = f"reporting_728_{reason}_*.db" if reason else "reporting_728_*.db"
        return list((self.folder / "data" / "backups").glob(pattern))

    def test_sync_requires_guard_but_can_reuse_identical_backup(self):
        existing = make_backup("manual")
        with patch("src.uk728.assistant_source.make_backup", return_value=None):
            with self.assertRaisesRegex(assistant_source.AssistantSourceError, "защитить"):
                assistant_source.synchronize(self.source, save_path=True)
        self.assertEqual(db.row("SELECT COUNT(*) FROM companies")[0], 0)
        self.assertEqual(make_backup("before_assistant_sync", reuse_existing=True), existing)
        result = assistant_source.synchronize(self.source, save_path=True)
        self.assertFalse(result.unchanged)
        self.assertEqual(len(self.backups("before_assistant_sync")), 0)

    def test_sync_rejects_changed_configuration_during_backup(self):
        assistant_source.synchronize(self.source, save_path=True)
        before = dict(db.row("SELECT * FROM companies WHERE assistant_source_id='uo-example'"))
        with sqlite3.connect(self.source) as conn:
            conn.execute("UPDATE management_organizations SET name='Synthetic external change'")
        alternative = self.folder / "synthetic_other.db"

        def backup_and_switch(reason, **kwargs):
            guard = make_backup(reason, **kwargs)
            db.execute("UPDATE app_meta SET value=? WHERE key=?",
                       (str(alternative), "assistant_database_path"))
            return guard

        with patch("src.uk728.assistant_source.make_backup", side_effect=backup_and_switch):
            with self.assertRaisesRegex(assistant_source.AssistantSourceError, "изменились"):
                assistant_source.synchronize()
        self.assertEqual(dict(db.row("SELECT * FROM companies WHERE id=?", (before["id"],))), before)
        self.assertEqual(assistant_source.configured_source_path(), alternative)

    def test_ten_unchanged_launch_refreshes_make_no_new_backups(self):
        result = assistant_source.synchronize(self.source, save_path=True)
        self.assertFalse(result.unchanged)
        self.assertEqual(len(self.backups("before_assistant_sync")), 1)
        local = db.row("SELECT * FROM houses WHERE assistant_source_id='house-example'")
        self.assertEqual(local["management_start"], "2021-07-01")
        self.assertAlmostEqual(local["total_area"], 1200.75)
        for _ in range(10):
            db.init_db()  # Simulate reopening the application.
            result = assistant_source.synchronize()
            self.assertTrue(result.unchanged)
            self.assertEqual(result.new_houses, 0)
        self.assertEqual(len(self.backups("before_assistant_sync")), 1)
        self.assertIsNotNone(make_backup("auto"))
        for _ in range(10):
            # SQLite may move app_meta rows internally during init_db(), even
            # when their values stay identical. That is not a data change.
            db.init_db()
            self.assertIsNone(make_backup("auto"))
        self.assertEqual(len(self.backups("auto")), 1)

    def test_auto_backups_follow_real_changes_and_preserve_wal(self):
        assistant_source.synchronize(self.source)
        initial_auto = make_backup("auto")
        self.assertIsNotNone(initial_auto)
        self.assertIsNone(make_backup("auto"))
        db.execute("UPDATE houses SET notes=? WHERE assistant_source_id=?",
                   ("Локальное примечание", "house-example"))
        changed_auto = make_backup("auto")
        self.assertIsNotNone(changed_auto)
        self.assertNotEqual(changed_auto, initial_auto)
        self.assertIsNone(make_backup("auto"))
        with sqlite3.connect(changed_auto) as conn:
            value = conn.execute(
                "SELECT notes FROM houses WHERE assistant_source_id='house-example'"
            ).fetchone()[0]
        self.assertEqual(value, "Локальное примечание")

    def test_source_date_change_creates_one_guard_and_updates_linked_house(self):
        assistant_source.synchronize(self.source)
        backups_before = len(self.backups("before_assistant_sync"))
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET management_start_date=?,residential_area=?,"
                "charge_area=? WHERE id='house-example'",
                ("2022-04-01", "1100", "1300,25"),
            )
        result = assistant_source.synchronize(self.source)
        self.assertFalse(result.unchanged)
        self.assertEqual(len(self.backups("before_assistant_sync")), backups_before + 1)
        changed = db.row("SELECT * FROM houses WHERE assistant_source_id='house-example'")
        self.assertEqual(changed["management_start"], "2022-04-01")
        self.assertAlmostEqual(changed["total_area"], 1300.25)
        assistant_source.synchronize(self.source)
        self.assertEqual(len(self.backups("before_assistant_sync")), backups_before + 1)

    def test_missing_source_fields_are_not_invented(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET residential_area=NULL,non_residential_area=NULL,"
                "charge_area=NULL,management_start_date=NULL WHERE id='house-example'"
            )
        result = assistant_source.synchronize(self.source)
        self.assertEqual(result.missing_area, 1)
        self.assertEqual(result.missing_management_start, 1)
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-example'")
        self.assertIsNone(house["residential_area"])
        self.assertIsNone(house["non_residential_area"])
        self.assertEqual(house["management_start"], "")
        self.assertEqual(house["total_area"], 0)

    def test_russian_date_format_is_normalized_without_losing_the_date(self):
        # All values in this test are invented.
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET management_start_date=?,management_end_date=? "
                "WHERE id='house-example'",
                ("05.07.2021", "30.11.2025"),
            )
        result = assistant_source.synchronize(self.source)
        self.assertEqual(result.missing_management_start, 0)
        row = db.row("SELECT * FROM houses WHERE assistant_source_id='house-example'")
        self.assertEqual(row["management_start"], "2021-07-05")
        self.assertEqual(row["management_end"], "2025-11-30")
        window = MainWindow()
        try:
            window.navigate(1)
            tab = window.houses_tab
            self.assertEqual(tab.start.text(), "05.07.2021")
            self.assertEqual(tab.end.text(), "30.11.2025")
            # The source did not provide separate agreement validity dates.
            self.assertEqual(tab.agreement_valid_from.text(), "")
        finally:
            window.timer.stop()
            window.context_timer.stop()
            window.close()

    def test_charge_only_source_area_is_shown_without_required_breakdown(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET residential_area=NULL,non_residential_area=NULL "
                "WHERE id='house-example'"
            )
        assistant_source.synchronize(self.source)
        window = MainWindow()
        try:
            window.navigate(1)
            tab = window.houses_tab
            message = tab.source_note.text()
            self.assertIn("площади к начислению", message)
            self.assertIn("указана не полностью", message)
            self.assertEqual(tab.residential_area.text(), "")
            self.assertEqual(tab.non_residential_area.text(), "")
            self.assertAlmostEqual(float(tab.area.text()), 1200.75)
            self.assertFalse(tab.area.isEnabled())
            tab.residential_area.setText("1000")
            tab.non_residential_area.setText("250")
            self.assertAlmostEqual(float(tab.area.text()), 1200.75)
            tab.save(refresh_after=False)
            self.assertAlmostEqual(
                db.row("SELECT total_area FROM houses WHERE assistant_source_id='house-example'")
                  ["total_area"],
                1200.75,
            )
        finally:
            window.timer.stop()
            window.context_timer.stop()
            window.close()

    def test_invalid_source_date_is_rejected_without_overwriting_data(self):
        assistant_source.synchronize(self.source)
        original = db.row(
            "SELECT management_start FROM houses WHERE assistant_source_id='house-example'"
        )["management_start"]
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET management_start_date=? "
                "WHERE id='house-example'", ("32.12.2022",)
            )
        with self.assertRaises(assistant_source.AssistantSourceError):
            assistant_source.synchronize(self.source)
        current = db.row(
            "SELECT management_start FROM houses WHERE assistant_source_id='house-example'"
        )["management_start"]
        self.assertEqual(current, original)

    def test_review_accepts_charge_total_without_optional_breakdown(self):
        result = inspect_report({
            "report": {"id": 1, "year": 2026},
            "company": {"name": "УК Тест"},
            "house": {
                "address": "Учебный объект",
                "assistant_source_id": "synthetic-house",
                "assistant_area_missing": 0,
                "residential_area": None,
                "non_residential_area": None,
                "total_area": 1200.75,
            },
        })
        self.assertFalse(any(
            "площадь" in message.lower() for message in result["warnings"]
        ))

    def test_residential_only_fallback_without_charge_total(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET residential_area='700',"
                "non_residential_area=NULL,charge_area=NULL "
                "WHERE id='house-example'"
            )
        result = assistant_source.synchronize(self.source)
        self.assertEqual(result.missing_area, 0)
        house = db.row(
            "SELECT * FROM houses WHERE assistant_source_id='house-example'"
        )
        self.assertEqual(house["total_area"], 700)
        self.assertEqual(house["residential_area"], 700)
        self.assertIsNone(house["non_residential_area"])


    def test_ui_timer_does_not_write_untouched_card_or_duplicate_backup(self):
        assistant_source.synchronize(self.source)
        window = MainWindow()
        try:
            window.auto_backup()
            initial_count = len(self.backups("auto"))
            window.auto_backup()
            window.navigate(1)  # House tab: another untouched form.
            window.auto_backup()
            self.assertEqual(len(self.backups("auto")), initial_count)
        finally:
            window.timer.stop()
            window.context_timer.stop()
            window.close()


if __name__ == "__main__":
    unittest.main()
