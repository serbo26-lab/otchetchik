"""Storage safety regressions; no real user data."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.backup_restore import verify_backup, stage_restore, pending_restore
from src.uk728.paths import db_path, data_dir, make_backup, _logical_digest
from src.uk728.template_storage import save_template


class StorageFixture(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        p = patch("src.uk728.paths.app_dir", return_value=Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        db.init_db()



class StorageSafetyTests(StorageFixture):
    def test_future_or_invalid_schema_not_overwritten_on_startup(self):
        for version in (str(db.SCHEMA_VERSION + 1), "invalid", "-1"):
            with self.subTest(version=version):
                db.execute("UPDATE app_meta SET value=? WHERE key='schema_version'", (version,))
                before = _logical_digest(db_path())
                copies = list((data_dir() / "backups").glob("*.db"))
                with self.assertRaises(ValueError):
                    db.init_db()
                self.assertEqual(_logical_digest(db_path()), before)
                self.assertEqual(list((data_dir() / "backups").glob("*.db")), copies)
                self.assertEqual(db.row("SELECT value FROM app_meta WHERE key='schema_version'")[0], version)

    def test_current_backup_missing_required_table_or_column_is_rejected(self):
        for ddl in ("DROP TABLE template_items", "ALTER TABLE reports RENAME COLUMN year TO wrong_year",
                    "DROP TABLE signatories"):
            with self.subTest(ddl=ddl):
                copy = make_backup("manual")
                with closing(sqlite3.connect(copy)) as conn:
                    conn.execute(ddl)
                    conn.commit()
                with self.assertRaises(ValueError):
                    verify_backup(copy)
                with self.assertRaises(ValueError):
                    stage_restore(copy)
                self.assertIsNone(pending_restore())

    def test_backup_with_orphaned_report_row_is_rejected(self):
        copy = make_backup("manual")
        with closing(sqlite3.connect(copy)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("INSERT INTO report_content_items(report_id,name) VALUES(?,?)",
                         (999999, "Учебная строка"))
            conn.commit()
        with self.assertRaisesRegex(ValueError, "нарушены связи"):
            verify_backup(copy)

    def test_legacy_copy_can_lack_migratable_tables_and_columns(self):
        copy = make_backup("manual")
        with closing(sqlite3.connect(copy)) as conn:
            conn.execute("UPDATE app_meta SET value='11' WHERE key='schema_version'")
            conn.execute("DROP TABLE signatories")
            conn.execute("ALTER TABLE houses DROP COLUMN agreement_valid_to")
            conn.commit()
        self.assertEqual(verify_backup(copy)["schema_version"], 11)

    def test_template_save_only_changes_edited_rows_and_preserves_identity(self):
        tid = db.execute("INSERT INTO templates(name) VALUES(?)", ("Учебный шаблон",))
        fields = dict(work_item_id=None, name="Учебная работа", unit="шт.",
                      unit_price=2.0, plan_qty=3.0)
        with closing(db.connect()) as conn:
            with conn:
                inserted = save_template(conn, tid, "Учебный шаблон", "Содержание", "", [fields])
            rid = inserted[0][1]
            payload = {**fields, "id": rid}
            baseline = conn.total_changes
            with conn:
                self.assertEqual(save_template(conn, tid, "Учебный шаблон", "Содержание", "", [payload]), [])
            self.assertEqual(conn.total_changes, baseline)
            with conn:
                save_template(conn, tid, "Учебный шаблон", "Содержание", "", [{**payload, "plan_qty": 7.0}])
            self.assertEqual(conn.execute("SELECT id,plan_qty FROM template_items WHERE template_id=?", (tid,)).fetchone()[0], rid)
            self.assertEqual(conn.execute("SELECT plan_qty FROM template_items WHERE id=?", (rid,)).fetchone()[0], 7)
            with self.assertRaisesRegex(ValueError, "наименование"):
                with conn:
                    save_template(conn, tid, "Учебный шаблон", "Содержание", "", [{**payload, "name": ""}])
            self.assertEqual(conn.execute("SELECT plan_qty FROM template_items WHERE id=?", (rid,)).fetchone()[0], 7)
            with self.assertRaises(ValueError):
                with conn:
                    save_template(conn, tid, "Учебный шаблон", "Содержание", "", [payload, payload])
            with conn:
                save_template(conn, tid, "Учебный шаблон", "Содержание", "", [])
            self.assertIsNone(conn.execute("SELECT id FROM template_items WHERE id=?", (rid,)).fetchone())


class TemplateEditorSafetyTests(StorageFixture):
    # Keep UI coverage separate from the storage-level regression cases.
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def test_repeated_timer_saves_and_navigation_do_not_create_backups(self):
        window = MainWindow()
        try:
            window.navigate(4)
            tab = window.templates_tab
            tab.add_template()
            work = db.execute(
                "INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
                ("Учебная работа", "Содержание", "шт."),
            )
            tab.reload_work_combo()
            tab.work_combo.setCurrentIndex(tab.work_combo.findData(work))
            tab.add_work_to_template()
            tab.table.set_value(0, 5, "4")
            tab.save(refresh_after=False)
            rid = int(tab.table.value(0, 0))
            self.assertGreater(rid, 0)
            window.auto_backup()
            before = _logical_digest(db_path())
            backups = set((data_dir() / "backups").glob("*.db"))
            for _ in range(3):
                tab.save(refresh_after=False)
                window.auto_backup()
            self.assertEqual(_logical_digest(db_path()), before)
            self.assertEqual(set((data_dir() / "backups").glob("*.db")), backups)
            self.assertEqual(int(tab.table.value(0, 0)), rid)
            tab.table.set_value(0, 2, "")
            with patch.object(QMessageBox, "warning"):
                self.assertIs(tab.save(refresh_after=False), False)
            self.assertEqual(db.row("SELECT name FROM template_items WHERE id=?", (rid,))[0],
                             "Учебная работа")
            tab.table.set_value(0, 2, "Учебная работа")
            tab.table.set_value(0, 5, "8")
            tab.save(refresh_after=False)
            self.assertEqual(db.row("SELECT plan_qty FROM template_items WHERE id=?", (rid,))[0], 8)
            window.navigate(3)
            window.navigate(4)
            self.assertEqual(int(tab.table.value(0, 0)), rid)
        finally:
            window.timer.stop()
            window.context_timer.stop()
            window.close()


if __name__ == "__main__":
    unittest.main()
