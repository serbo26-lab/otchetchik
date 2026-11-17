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
             "123456", None if no_start else "2021-07-01", None),
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
                "UPDATE houses SET management_start_date=?,residential_area=? "
                "WHERE id='house-example'", ("2022-04-01", "1100"),
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
                "management_start_date=NULL WHERE id='house-example'"
            )
        result = assistant_source.synchronize(self.source)
        self.assertEqual(result.missing_area, 1)
        self.assertEqual(result.missing_management_start, 1)
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-example'")
        self.assertIsNone(house["residential_area"])
        self.assertIsNone(house["non_residential_area"])
        self.assertEqual(house["management_start"], "")
        self.assertEqual(house["total_area"], 0)

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
