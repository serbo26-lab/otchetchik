"""Historical migration and storage failure regressions, fictional records only."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import runpy
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import db, paths, signatories
from src.uk728.app import MainWindow
from src.uk728.backup_restore import verify_backup


class AuditFixture(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = patch("src.uk728.paths.app_dir", return_value=Path(folder.name))
        override.start()
        self.addCleanup(override.stop)


class StorageAuditTests(AuditFixture):
    def test_v10_database_preserves_all_original_tables_and_records(self):
        fixture = Path(__file__).with_name("fixtures") / "legacy_v10_schema.py"
        with closing(sqlite3.connect(paths.db_path())) as conn:
            conn.executescript(runpy.run_path(str(fixture))["SCHEMA_SQL"])
            conn.executescript("""
                INSERT INTO app_meta VALUES('schema_version','10');
                INSERT INTO app_meta VALUES('synthetic_marker','retained');
                INSERT INTO companies(id,name,notes) VALUES(11,'Synthetic UO','local');
                INSERT INTO houses(id,company_id,address,total_area) VALUES(21,11,'Synthetic house',125.5);
                INSERT INTO work_categories(id,name,section) VALUES(31,'Synthetic category','Содержание');
                INSERT INTO work_items(id,name,category,unit,default_price) VALUES(41,'Synthetic work','Содержание','м²',3);
                INSERT INTO templates(id,name,category) VALUES(51,'Synthetic template','Содержание');
                INSERT INTO template_items(id,template_id,work_item_id,name,unit,unit_price,plan_qty)
                    VALUES(61,51,41,'Synthetic work','м²',3,5);
                INSERT INTO reports(id,company_id,house_id,year,period_start,period_end,management_cost,notes)
                    VALUES(71,11,21,2025,'2025-01-01','2025-12-31',0,'Synthetic report');
                INSERT INTO report_content_items(id,report_id,work_item_id,name,unit,unit_price,plan_qty,plan_cost,actual_qty,actual_cost,source,is_manual)
                    VALUES(81,71,41,'Synthetic work','м²',3,5,15,0,0,'synthetic source',1);
                INSERT INTO report_repair_items(id,report_id,name,cost,volume)
                    VALUES(91,71,'Synthetic repair',6,'2 м²');
                INSERT INTO report_management_items(id,report_id,name,unit_price,qty,cost)
                    VALUES(101,71,'Synthetic management',2,4,8);
                INSERT INTO report_financials(id,report_id,payer_type,debt_start,accrued,paid,debt_end)
                    VALUES(111,71,'Собственники',2,15,10,7);
                INSERT INTO report_claims(id,report_id,pretensions_count,lawsuits_count,recovered_amount)
                    VALUES(121,71,2,1,4);
            """)
            conn.commit()
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='app_meta'"
            )]
            snapshots = {}
            for table in tables:
                columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                snapshots[table] = (columns, conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall())
        self.assertEqual(verify_backup(paths.db_path())["schema_version"], 10)
        db.init_db()
        with closing(sqlite3.connect(paths.db_path())) as conn:
            self.assertEqual(db.read_schema_version(conn), db.SCHEMA_VERSION)
            for table, (columns, expected) in snapshots.items():
                with self.subTest(table=table):
                    projection = ",".join('"' + name + '"' for name in columns)
                    actual = conn.execute(f'SELECT {projection} FROM "{table}" ORDER BY id').fetchall()
                    self.assertEqual(actual, expected)
            self.assertEqual(conn.execute("SELECT value FROM app_meta WHERE key='synthetic_marker'").fetchone()[0], "retained")
            self.assertEqual(conn.execute("SELECT residential_area,non_residential_area FROM houses WHERE id=21").fetchone(), (None, None))
            self.assertIsNone(conn.execute("PRAGMA foreign_key_check").fetchone())
        guards = list((paths.data_dir() / "backups").glob("reporting_728_before_schema_v*.db"))
        self.assertEqual(len(guards), 1)
        self.assertEqual(verify_backup(guards[0])["schema_version"], 10)

    def test_backup_does_not_recreate_source_removed_before_open(self):
        source = paths.data_dir() / "synthetic-source.db"
        connect = sqlite3.connect
        with closing(connect(source)) as conn:
            conn.execute("CREATE TABLE synthetic(id INTEGER PRIMARY KEY)")
            conn.commit()

        def remove_before_connect(database, *args, **kwargs):
            source.unlink()
            return connect(database, *args, **kwargs)

        with patch.object(paths, "db_path", return_value=source), \
             patch.object(paths.sqlite3, "connect", side_effect=remove_before_connect):
            with self.assertRaises(sqlite3.OperationalError):
                paths.make_backup("manual")
        self.assertFalse(source.exists())
        self.assertFalse(list((paths.data_dir() / "backups").glob("*.db")))

    def test_entrypoint_propagates_startup_failure(self):
        with patch("src.uk728.app.main", return_value=1):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_module("main", run_name="__main__")
        self.assertEqual(raised.exception.code, 1)

    def test_signatory_deletion_requires_backup(self):
        db.init_db()
        company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Synthetic UO",))
        house = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (company, "Synthetic house"))
        record = signatories.save_local_signatory({"full_name": "Synthetic person"}, house_id=house)
        with patch("src.uk728.signatories.make_backup", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "резервную копию"):
                signatories.delete_local_signatory(record, house_id=house)
        self.assertEqual(signatories.list_signatories(house_id=house)[0]["id"], record)


class TemplateGuardAuditTests(AuditFixture):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def test_template_deletion_requires_backup(self):
        db.init_db()
        window = MainWindow()
        self.addCleanup(window.close)
        window.navigate(4)
        page = window.templates_tab
        page.add_template()
        template = page.current_id
        self.assertIsNotNone(template)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(QMessageBox, "warning") as warning, \
             patch("src.uk728.templates_ui.make_backup", return_value=None):
            page.delete_template()
        warning.assert_called_once()
        self.assertEqual(page.current_id, template)
        self.assertIsNotNone(db.row("SELECT id FROM templates WHERE id=?", (template,)))


if __name__ == "__main__":
    unittest.main()
