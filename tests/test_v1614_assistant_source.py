"""Strictly read-only Assistant data source and stable report-local mirror."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.uk728 import assistant_source, db
from src.uk728.app import MainWindow


def source_fixture(path: Path, *, areas=True):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE management_organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            ogrn TEXT NOT NULL
        );
        CREATE TABLE houses (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            address TEXT NOT NULL,
            residential_area TEXT,
            non_residential_area TEXT,
            charge_area TEXT,
            management_start_date TEXT,
            management_end_date TEXT
        );
        """)
        conn.execute(
            "INSERT INTO management_organizations(id,name,ogrn) VALUES(?,?,?)",
            ("org-001", "УК Образец", "0000000000000"),
        )
        conn.execute(
            "INSERT INTO houses VALUES(?,?,?,?,?,?,?,?)",
            ("house-001", "org-001", "г. Примерск, ул. Тестовая, д. 7",
             "1000,50" if areas else None, "200,25" if areas else None,
             "1200,75" if areas else None, "2024-01-01", None),
        )
        conn.commit()


class AssistantReadOnlySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        patcher = patch("src.uk728.paths.app_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.source = self.folder / "assistant.db"
        source_fixture(self.source)

    def local_count(self, table):
        return db.row(f"SELECT COUNT(*) AS n FROM {table}")["n"]

    def test_read_never_writes_source_and_preserves_uuid_links(self):
        old = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = assistant_source.synchronize(self.source, save_path=True)
        self.assertEqual(result.organizations, 1)
        self.assertEqual(result.houses, 1)
        self.assertEqual(result.new_houses, 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), old)
        self.assertEqual(
            assistant_source.configured_source_path().resolve(), self.source.resolve()
        )
        org = db.row("SELECT * FROM companies WHERE assistant_source_id='org-001'")
        home = db.row("SELECT * FROM houses WHERE assistant_source_id='house-001'")
        self.assertEqual(home["company_id"], org["id"])
        self.assertEqual(home["total_area"], 1200.75)
        self.assertEqual(home["management_start"], "2024-01-01")
        self.assertEqual(result.missing_area, 0)
        self.assertEqual(self.local_count("reports"), 0)
        self.assertEqual(self.local_count("work_items"), 7)

    def test_refresh_keeps_local_ids_reports_contacts_and_stale_houses(self):
        assistant_source.synchronize(self.source, save_path=True)
        org = db.row("SELECT * FROM companies WHERE assistant_source_id='org-001'")
        home = db.row("SELECT * FROM houses WHERE assistant_source_id='house-001'")
        db.execute(
            "UPDATE companies SET reception_address=?,report_person=? WHERE id=?",
            ("Адрес для приёма", "Проверяющий сотрудник", org["id"]),
        )
        db.execute("UPDATE houses SET notes=?,status=? WHERE id=?",
                   ("Локальное пояснение", "Архив", home["id"]))
        report_id = db.create_report(org["id"], home["id"], 2026)
        with sqlite3.connect(self.source) as conn:
            conn.execute("UPDATE management_organizations SET name='УК Новое название'")
            conn.execute(
                "UPDATE houses SET address='г. Примерск, д. 9', residential_area='1400',"
                "non_residential_area='100',charge_area='1500' WHERE id='house-001'"
            )
            conn.commit()
        summary = assistant_source.synchronize()
        self.assertEqual(summary.new_houses, 0)
        self.assertEqual(summary.new_organizations, 0)
        org2 = db.row("SELECT * FROM companies WHERE assistant_source_id='org-001'")
        home2 = db.row("SELECT * FROM houses WHERE assistant_source_id='house-001'")
        self.assertEqual(org2["id"], org["id"])
        self.assertEqual(home2["id"], home["id"])
        self.assertEqual(org2["name"], "УК Новое название")
        self.assertEqual(org2["reception_address"], "Адрес для приёма")
        self.assertEqual(home2["address"], "г. Примерск, д. 9")
        self.assertEqual(home2["total_area"], 1500)
        self.assertEqual(home2["status"], "Архив")
        self.assertEqual(home2["notes"], "Локальное пояснение")
        self.assertEqual(db.load_report_bundle(report_id)["report"]["house_id"], home["id"])
        with sqlite3.connect(self.source) as conn:
            conn.execute("DELETE FROM houses")
            conn.commit()
        assistant_source.synchronize()
        self.assertEqual(self.local_count("houses"), 1)
        self.assertIsNotNone(db.row("SELECT id FROM reports WHERE id=?", (report_id,)))

    def test_charge_area_imports_total_even_without_breakdown(self):
        # Source fields are synthetic; no identifiable data is involved.
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET residential_area=NULL,non_residential_area=NULL,"
                "charge_area='1400,75'"
            )
        result = assistant_source.synchronize(self.source)
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-001'")
        self.assertEqual(result.missing_area, 0)
        self.assertEqual(house["total_area"], 1400.75)
        self.assertEqual(house["assistant_area_missing"], 0)
        self.assertIsNone(house["residential_area"])
        self.assertIsNone(house["non_residential_area"])
        # The total is authoritative and updates on the next source refresh.
        with sqlite3.connect(self.source) as conn:
            conn.execute("UPDATE houses SET charge_area='1450' ")
        assistant_source.synchronize(self.source)
        house = db.row("SELECT * FROM houses WHERE id=?", (house["id"],))
        self.assertEqual(house["total_area"], 1450)

    def test_missing_all_source_areas_keeps_existing_local_total(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET residential_area=NULL,non_residential_area=NULL,"
                "charge_area=NULL"
            )
        result = assistant_source.synchronize(self.source)
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-001'")
        self.assertEqual(result.missing_area, 1)
        self.assertEqual(house["total_area"], 0)
        self.assertEqual(house["assistant_area_missing"], 1)
        db.execute("UPDATE houses SET total_area=? WHERE id=?", (1200, house["id"]))
        assistant_source.synchronize(self.source)
        house = db.row("SELECT * FROM houses WHERE id=?", (house["id"],))
        self.assertEqual(house["total_area"], 1200)

    def test_wrong_or_absent_source_never_creates_source_file(self):
        nonexistent = self.folder / "no-data.db"
        with self.assertRaises(assistant_source.AssistantSourceError):
            assistant_source.synchronize(nonexistent, save_path=True)
        self.assertFalse(nonexistent.exists())
        with self.assertRaises(assistant_source.AssistantSourceError):
            assistant_source.synchronize(self.folder / "data" / "reporting_728.db")
        self.assertEqual(self.local_count("companies"), 0)
        self.assertNotEqual(assistant_source.configured_source_path(), nonexistent)

    def test_incompatible_source_fails_without_local_changes(self):
        other = self.folder / "other.db"
        with sqlite3.connect(other) as conn:
            conn.execute("CREATE TABLE unrelated(v INTEGER)")
        with self.assertRaisesRegex(assistant_source.AssistantSourceError, "management_organizations"):
            assistant_source.synchronize(other, save_path=True)
        self.assertEqual(self.local_count("companies"), 0)

    def test_org_reassignment_with_reports_rolls_back_complete_sync(self):
        assistant_source.synchronize(self.source)
        org = db.row("SELECT * FROM companies WHERE assistant_source_id='org-001'")
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-001'")
        rid = db.create_report(org["id"], house["id"], 2026)
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "INSERT INTO management_organizations VALUES(?,?,?)",
                ("org-002", "УК Вторая", "0000000000001"),
            )
            conn.execute(
                "UPDATE houses SET organization_id='org-002' WHERE id='house-001'"
            )
            conn.commit()
        with self.assertRaisesRegex(assistant_source.AssistantSourceError, "перенесён"):
            assistant_source.synchronize(self.source)
        self.assertEqual(self.local_count("companies"), 1)
        self.assertEqual(db.row("SELECT company_id FROM houses WHERE id=?",
                                (house["id"],))["company_id"], org["id"])
        self.assertIsNotNone(db.row("SELECT id FROM reports WHERE id=?", (rid,)))

    def test_gui_linked_identity_is_readonly_but_reporting_data_is_local(self):
        assistant_source.synchronize(self.source, save_path=True)
        window = MainWindow()
        window.resize(1350, 900)
        window.show()
        self.addCleanup(window.close)
        self.app.processEvents()
        co = window.company_tab
        self.assertIsNotNone(co.current_id)
        self.assertFalse(co.name.isEnabled())
        self.assertFalse(co.ogrn.isEnabled())
        self.assertFalse(co.delete_button.isEnabled())
        self.assertTrue(co.person.isEnabled())
        co.person.setText("Ответственный специалист")
        co.save(refresh_after=False)
        self.assertEqual(db.row("SELECT report_person FROM companies WHERE id=?",
                               (co.current_id,))["report_person"],
                         "Ответственный специалист")
        home = window.houses_tab
        home.reload_companies()
        home.refresh()
        self.assertIsNotNone(home.current_id)
        self.assertFalse(home.address.isEnabled())
        self.assertFalse(home.area.isEnabled())
        self.assertFalse(home.start.isEnabled())
        self.assertFalse(home.delete_button.isEnabled())
        self.assertTrue(home.status.isEnabled())
        self.assertTrue(home.notes.isEnabled())

    def test_extracted_page_aborts_sync_when_pending_edits_cannot_be_saved(self):
        window = MainWindow()
        self.addCleanup(window.close)
        with patch.object(window, "save_before_assistant_sync", return_value=False) as save, \
             patch("src.uk728.backups_ui.assistant_source.synchronize") as sync, \
             patch.object(window, "reload_after_assistant_sync") as reload:
            window.backups_tab._apply_assistant_sync(self.source, remember=True)
        save.assert_called_once_with()
        sync.assert_not_called()
        reload.assert_not_called()

    def test_data_page_connect_updates_all_related_selectors(self):
        window = MainWindow()
        window.resize(1350, 900)
        window.show()
        self.addCleanup(window.close)
        self.app.processEvents()
        window.navigate(7)
        self.app.processEvents()
        with patch.object(window, "save_before_assistant_sync",
                          wraps=window.save_before_assistant_sync) as save, \
             patch.object(window, "reload_after_assistant_sync",
                          wraps=window.reload_after_assistant_sync) as reload:
            window.backups_tab._apply_assistant_sync(self.source, remember=True)
        save.assert_called_once_with()
        reload.assert_called_once_with()
        self.app.processEvents()
        self.assertEqual(assistant_source.configured_source_path(), self.source)
        self.assertEqual(window.company_tab.list.count(), 1)
        cid = db.row("SELECT id FROM companies WHERE assistant_source_id='org-001'")["id"]
        hid = db.row("SELECT id FROM houses WHERE assistant_source_id='house-001'")["id"]
        self.assertEqual(window.houses_tab.company_filter.findData(cid), 0)
        self.assertGreaterEqual(window.reports_tab.company_filter.findData(cid), 0)
        self.assertGreaterEqual(window.mass_tab.company.findData(cid), 0)
        self.assertIn("Связанных локальных записей",
                      window.backups_tab.assistant_summary.text())
        window.navigate(6)
        self.app.processEvents()
        reports = window.reports_tab
        reports.company_filter.setCurrentIndex(reports.company_filter.findData(cid))
        self.assertGreaterEqual(reports.house_filter.findData(hid), 0)

    def test_read_source_with_live_wal_commits(self):
        writer = sqlite3.connect(self.source)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE management_organizations SET name='УК WAL'")
        writer.commit()
        result = assistant_source.synchronize(self.source)
        self.assertEqual(result.organizations, 1)
        self.assertEqual(db.row("SELECT name FROM companies WHERE assistant_source_id='org-001'")["name"],
                         "УК WAL")


if __name__ == "__main__":
    unittest.main()
