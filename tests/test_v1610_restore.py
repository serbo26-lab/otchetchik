"""V1.6.10 backup recovery must never silently lose existing report data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import db
from src.uk728.backup_restore import (
    verify_backup, stage_restore, pending_restore,
    cancel_restore, apply_pending_restore,
)
from src.uk728.paths import db_path, data_dir, make_backup, cleanup_backups
from src.uk728.app import MainWindow


class BackupRestorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()

    def create_company(self, name):
        return db.execute("INSERT INTO companies(name) VALUES(?)", (name,))

    def count_companies(self):
        return db.row("SELECT COUNT(*) AS n FROM companies")["n"]

    def test_verified_restore_is_only_applied_on_next_start_and_has_guard(self):
        self.create_company("В резервной копии")
        snapshot = make_backup("manual")
        result = verify_backup(snapshot)
        self.assertEqual(result["companies"], 1)
        self.create_company("После резервной копии")
        self.assertEqual(self.count_companies(), 2)
        request = stage_restore(snapshot)
        self.assertTrue(request["sha256"])
        self.assertEqual(self.count_companies(), 2)
        self.assertIsNotNone(pending_restore())
        # This simulates the next program start, before any widgets open.
        guard = apply_pending_restore()
        self.assertIsNotNone(guard)
        self.assertTrue(guard.exists())
        self.assertEqual(self.count_companies(), 1)
        self.assertEqual(pending_restore(), None)
        self.assertEqual(verify_backup(guard)["companies"], 2)
        with sqlite3.connect(guard) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM companies WHERE name='После резервной копии'").fetchone()[0], 1
            )

    def test_cancel_does_not_touch_current_database(self):
        self.create_company("Исходная")
        snapshot = make_backup("manual")
        record = stage_restore(snapshot)
        staged = data_dir() / "backups" / record["snapshot"]
        self.assertTrue(staged.is_file())
        self.create_company("Новая")
        self.assertTrue(cancel_restore())
        self.assertFalse(staged.exists())
        self.assertIsNone(pending_restore())
        self.assertIsNone(apply_pending_restore())
        self.assertEqual(self.count_companies(), 2)

    def test_invalid_or_unrelated_sqlite_backup_is_rejected(self):
        path = data_dir() / "backups" / "invalid.db"
        path.write_bytes(b"not sqlite at all")
        with self.assertRaises(ValueError):
            verify_backup(path)
        with self.assertRaises(ValueError):
            stage_restore(path)
        self.assertIsNone(pending_restore())
        unrelated = data_dir() / "backups" / "unrelated.db"
        with sqlite3.connect(unrelated) as conn:
            conn.execute("CREATE TABLE other(id INTEGER)")
        with self.assertRaisesRegex(ValueError, "полной базой"):
            verify_backup(unrelated)
        self.assertIsNone(pending_restore())

    def test_changed_staged_file_does_not_modify_live_database(self):
        self.create_company("Актуальная")
        snapshot = make_backup("manual")
        stage_restore(snapshot)
        self.create_company("Позже")
        record = pending_restore()
        staged = data_dir() / "backups" / record["snapshot"]
        staged.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "была изменена"):
            apply_pending_restore()
        self.assertEqual(self.count_companies(), 2)
        self.assertIsNotNone(pending_restore())
        cancel_restore()

    def test_cannot_replace_an_unprocessed_request(self):
        self.create_company("Проверка")
        original = make_backup("manual")
        req = stage_restore(original)
        with self.assertRaises(RuntimeError):
            stage_restore(original)
        self.assertEqual(pending_restore()["sha256"], req["sha256"])

    def test_reject_future_schema_version_without_creating_request(self):
        future = data_dir() / "backups" / "future.db"
        with sqlite3.connect(db_path()) as current, sqlite3.connect(future) as dst:
            current.backup(dst)
        with sqlite3.connect(future) as conn:
            conn.execute("UPDATE app_meta SET value='999' WHERE key='schema_version'")
            conn.commit()
        with self.assertRaisesRegex(ValueError, "новее поддерживаемой"):
            verify_backup(future)
        with self.assertRaises(ValueError):
            stage_restore(future)
        self.assertIsNone(pending_restore())

    def test_no_backup_prevents_any_destructive_restore(self):
        self.create_company("Текущая")
        previous = make_backup("manual")
        stage_restore(previous)
        self.create_company("Не удалять")
        with patch("src.uk728.backup_restore.make_backup", side_effect=OSError("no space")):
            with self.assertRaisesRegex(OSError, "no space"):
                apply_pending_restore()
        self.assertEqual(self.count_companies(), 2)
        self.assertIsNotNone(pending_restore())

    def test_cleanup_only_rotates_auto_backups_not_safety_or_manual(self):
        self.create_company("Хранение")
        manual = make_backup("manual")
        safety = make_backup("before_restore")
        old_auto = make_backup("auto")
        newer_auto = make_backup("auto")
        cleanup_backups(max_files=1)
        self.assertTrue(manual.exists())
        self.assertTrue(safety.exists())
        self.assertFalse(old_auto.exists())
        self.assertTrue(newer_auto.exists())

    def test_archive_ui_can_check_and_stage_copy(self):
        window = MainWindow()
        self.addCleanup(window.close)
        self.create_company("Архив")
        copy = make_backup("manual")
        page = window.backups_tab
        page.refresh()
        index = next(
            i for i in range(page.archive_list.count())
            if page.archive_list.item(i).data(0x0100) == str(copy)
        )
        page.archive_list.setCurrentRow(index)
        self.assertTrue(page.check_button.isEnabled())
        self.assertTrue(page.restore_button.isEnabled())
        page.check_backup()
        self.assertIn("Копия проверена", page.info.toPlainText())
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("src.uk728.app.QMessageBox.information"):
            page.restore_selected()
        self.assertIsNotNone(pending_restore())
        self.assertFalse(page.restore_button.isEnabled())
        self.assertTrue(page.cancel_button.isEnabled())
        with patch("src.uk728.app.QMessageBox.question", return_value=QMessageBox.Yes):
            page.cancel_pending()
        self.assertIsNone(pending_restore())

    def test_pending_record_cannot_point_outside_backups_directory(self):
        marker = data_dir() / ".restore_request.json"
        marker.write_text('{"snapshot":"../../other.db"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Неверный путь"):
            pending_restore()
        marker.unlink()


if __name__ == "__main__":
    unittest.main()
