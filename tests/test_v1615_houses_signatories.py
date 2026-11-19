"""House area and contract migration; two-party signatory history.

Fixtures contain only synthetic data. The real Assistant database is
never opened by CI and must never be written by this application.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.uk728 import assistant_source, db, signatories
from src.uk728.app import MainWindow


def assistant_fixture(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE management_organizations(
                id TEXT PRIMARY KEY,name TEXT NOT NULL,ogrn TEXT NOT NULL
            );
            CREATE TABLE houses(
                id TEXT PRIMARY KEY,organization_id TEXT,address TEXT,
                residential_area TEXT,non_residential_area TEXT,charge_area TEXT,
                management_start_date TEXT,management_end_date TEXT,
                management_agreement_number TEXT,management_agreement_date TEXT
            );
            CREATE TABLE act_people(
                id TEXT PRIMARY KEY,organization_id TEXT,house_id TEXT,
                data_json TEXT NOT NULL,signature BLOB
            );
        """)
        conn.execute(
            "INSERT INTO management_organizations VALUES(?,?,?)",
            ("uo-test", "УК Пример", "0000000000000"),
        )
        conn.execute(
            "INSERT INTO houses VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("house-test", "uo-test", "г. Тестовый, д. 7",
             "1200,25", "240,75", "1441", "2022-05-01", None,
             "Д-17", "2022-04-27"),
        )
        for src_id, org, home, name, role in (
            ("person-uo", "uo-test", None, "Иванов Иван Иванович", "Руководитель"),
            ("person-home", None, "house-test", "Петров Петр Петрович",
             "Председатель совета МКД"),
        ):
            details = dict(full_name=name, role=role,
                           valid_from="2022-01-01", valid_to="",
                           basis_kind="Устав" if org else "Решение ОСС",
                           basis_number="3", basis_date="2021-12-15",
                           position="Директор" if org else "")
            conn.execute(
                "INSERT INTO act_people VALUES(?,?,?,?,?)",
                (src_id, org, home, json.dumps(details, ensure_ascii=False),
                 b"private-image-not-imported"),
            )
        conn.commit()


class HouseFieldsAndSignatoriesV1615Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        p = patch("src.uk728.paths.app_dir", return_value=self.folder)
        p.start()
        self.addCleanup(p.stop)
        db.init_db()
        self.source = self.folder / "source.db"
        assistant_fixture(self.source)

    def test_source_fields_and_both_signers_read_only(self):
        expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = assistant_source.synchronize(self.source)
        self.assertEqual(result.signatories, 2)
        self.assertEqual(expected, hashlib.sha256(self.source.read_bytes()).hexdigest())
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-test'")
        self.assertAlmostEqual(house["residential_area"], 1200.25)
        self.assertAlmostEqual(house["non_residential_area"], 240.75)
        self.assertAlmostEqual(house["total_area"], 1441)
        self.assertEqual(house["management_agreement_number"], "Д-17")
        self.assertEqual(house["management_agreement_date"], "2022-04-27")
        self.assertEqual(house["management_start"], "2022-05-01")
        self.assertEqual(house["agreement_valid_from"], "")
        self.assertEqual(house["agreement_valid_to"], "")
        org = db.row("SELECT * FROM companies WHERE assistant_source_id='uo-test'")
        a = signatories.list_signatories(company_id=org["id"])
        b = signatories.list_signatories(house_id=house["id"])
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(a[0]["assistant_source_id"], "person-uo")
        self.assertEqual(b[0]["full_name"], "Петров Петр Петрович")
        with db.connect() as conn:
            value = conn.execute(
                "SELECT data_json FROM signatories WHERE assistant_source_id='person-uo'"
            ).fetchone()["data_json"]
        self.assertNotIn("private-image", value)

    def test_repeat_refresh_preserves_contract_validity_and_manual_signers(self):
        assistant_source.synchronize(self.source)
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-test'")
        org = db.row("SELECT * FROM companies WHERE assistant_source_id='uo-test'")
        db.execute(
            "UPDATE houses SET agreement_valid_from=?,agreement_valid_to=? WHERE id=?",
            ("2022-05-10", "2027-05-09", house["id"]),
        )
        rid = db.create_report(org["id"], house["id"], 2026)
        local = signatories.save_local_signatory({
            "full_name": "Сидорова Мария Ивановна",
            "role": "Уполномоченный собственник",
            "basis_kind": "Решение ОСС",
            "valid_from": "01.01.2025",
        }, house_id=house["id"])
        with sqlite3.connect(self.source) as conn:
            conn.execute("UPDATE houses SET residential_area='1300',"
                         "non_residential_area='200',charge_area='1500',"
                         "management_agreement_number='Д-18'")
            conn.execute(
                "UPDATE act_people SET data_json=? WHERE id='person-home'",
                (json.dumps(dict(full_name="Петров П. П.", role="Председатель совета МКД",
                                 valid_from="2023-01-01"), ensure_ascii=False),),
            )
            conn.commit()
        expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        assistant_source.synchronize(self.source)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), expected)
        updated = db.row("SELECT * FROM houses WHERE id=?", (house["id"],))
        self.assertEqual(updated["management_agreement_number"], "Д-18")
        self.assertEqual(updated["agreement_valid_to"], "2027-05-09")
        self.assertEqual(updated["total_area"], 1500)
        self.assertEqual(db.load_report_bundle(rid)["report"]["house_id"], house["id"])
        people = signatories.list_signatories(house_id=house["id"])
        self.assertEqual(len(people), 2)
        self.assertEqual(people[0]["full_name"], "Петров П. П.")
        self.assertIn(local, [p["id"] for p in people])
        # Source deletions never erase historical person rows needed by old reports.
        with sqlite3.connect(self.source) as conn:
            conn.execute("DELETE FROM act_people")
            conn.commit()
        assistant_source.synchronize(self.source)
        self.assertEqual(len(signatories.list_signatories(house_id=house["id"])), 2)

    def test_manual_signer_validation_and_source_protection(self):
        assistant_source.synchronize(self.source)
        org = db.row("SELECT * FROM companies WHERE assistant_source_id='uo-test'")
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-test'")
        with self.assertRaisesRegex(ValueError, "начало полномочий"):
            signatories.save_local_signatory(
                {"full_name": "Проверка", "basis_kind": "Устав"},
                company_id=org["id"],
            )
        new_id = signatories.save_local_signatory({
            "full_name": "Уполномоченный сотрудник",
            "role": "Руководитель",
            "position": "Директор",
            "basis_kind": "Устав",
            "valid_from": "01.01.2025",
        }, company_id=org["id"])
        with self.assertRaisesRegex(ValueError, "уже есть"):
            signatories.save_local_signatory({
                "full_name": "Уполномоченный сотрудник",
                "basis_kind": "Устав",
                "valid_from": "2025-01-01",
            }, company_id=org["id"])
        with self.assertRaisesRegex(ValueError, "Ассистента"):
            source_id = signatories.list_signatories(company_id=org["id"])[0]["id"]
            signatories.save_local_signatory({
                "full_name": "Заменён", "valid_from": "2025-01-01",
                "basis_kind": "Устав"
            }, company_id=org["id"], existing_id=source_id)
        self.assertNotEqual(new_id, house["id"])
        signatories.delete_local_signatory(new_id, company_id=org["id"])
        self.assertEqual(len(signatories.list_signatories(company_id=org["id"])), 1)

    def test_local_house_areas_are_summed_and_dates_are_independent(self):
        company = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Локальная",))
        house_id = db.execute(
            "INSERT INTO houses(company_id,address,total_area,management_start) "
            "VALUES(?,?,?,?)",
            (company, "г. Тестовый, д. 10", 888, "2024-01-01"),
        )
        window = MainWindow()
        window.resize(1320, 900)
        window.show()
        self.addCleanup(window.close)
        self.app.processEvents()
        house = window.houses_tab
        self.assertEqual(house.current_id, house_id)
        self.assertTrue(house.residential_area.isEnabled())
        self.assertTrue(house.non_residential_area.isEnabled())
        house.residential_area.setText("1100,50")
        house.non_residential_area.setText("200,25")
        self.assertFalse(house.area.isEnabled())
        self.assertAlmostEqual(float(house.area.text()), 1300.75)
        house.agreement_number.setText("Договор-14")
        house.agreement_date.setText("01.02.2024")
        house.agreement_valid_from.setText("05.02.2024")
        house.agreement_valid_to.setText("05.02.2029")
        house.save(refresh_after=False)
        saved = db.row("SELECT * FROM houses WHERE id=?", (house_id,))
        self.assertAlmostEqual(saved["total_area"], 1300.75)
        self.assertEqual(saved["management_agreement_number"], "Договор-14")
        self.assertEqual(saved["management_agreement_date"], "2024-02-01")
        self.assertEqual(saved["agreement_valid_from"], "2024-02-05")
        self.assertEqual(saved["agreement_valid_to"], "2029-02-05")
        self.assertEqual(saved["management_start"], "2024-01-01")

    def test_bad_contract_period_does_not_update_house(self):
        company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Тестовая УО",))
        hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                         (company, "Тест, д. 1"))
        window = MainWindow()
        window.show()
        self.addCleanup(window.close)
        self.app.processEvents()
        tab = window.houses_tab
        self.assertEqual(tab.current_id, hid)
        tab.agreement_valid_from.setText("01.03.2027")
        tab.agreement_valid_to.setText("01.03.2026")
        with patch.object(QMessageBox, "warning"):
            self.assertIs(tab.save(refresh_after=False), False)
        self.assertEqual(
            db.row("SELECT agreement_valid_from FROM houses WHERE id=?", (hid,))["agreement_valid_from"],
            "",
        )

    def test_source_fields_locked_but_missing_component_can_be_filled(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET residential_area=NULL,non_residential_area='500',"
                "charge_area=NULL"
            )
            conn.commit()
        assistant_source.synchronize(self.source)
        house = db.row("SELECT * FROM houses WHERE assistant_source_id='house-test'")
        window = MainWindow()
        window.resize(1350, 950)
        window.show()
        self.addCleanup(window.close)
        self.app.processEvents()
        tab = window.houses_tab
        self.assertEqual(tab.current_id, house["id"])
        self.assertFalse(tab.non_residential_area.isEnabled())
        self.assertTrue(tab.residential_area.isEnabled())
        self.assertFalse(tab.agreement_number.isEnabled())
        self.assertTrue(tab.agreement_valid_from.isEnabled())
        tab.residential_area.setText("1000")
        self.assertFalse(tab.area.isEnabled())
        tab.agreement_valid_from.setText("15.01.2024")
        tab.save(refresh_after=False)
        updated = db.row("SELECT * FROM houses WHERE id=?", (house["id"],))
        self.assertEqual(updated["total_area"], 1500)
        self.assertEqual(updated["residential_area"], 1000)
        self.assertEqual(updated["non_residential_area"], 500)
        self.assertEqual(updated["agreement_valid_from"], "2024-01-15")
        with sqlite3.connect(self.source) as conn:
            conn.execute(
                "UPDATE houses SET non_residential_area='550' WHERE id='house-test'"
            )
            conn.commit()
        assistant_source.synchronize(self.source)
        refreshed = db.row("SELECT * FROM houses WHERE id=?", (house["id"],))
        self.assertEqual(refreshed["residential_area"], 1000)
        self.assertEqual(refreshed["non_residential_area"], 550)
        self.assertEqual(refreshed["total_area"], 1550)
        self.assertEqual(refreshed["agreement_valid_from"], "2024-01-15")

    def test_migrate_existing_total_without_inventing_components(self):
        company = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Старая",))
        house_id = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (company, "Старый дом", 6300),
        )
        # Simulate a previous installed version before the schema migration.
        db.execute("UPDATE app_meta SET value='11' WHERE key='schema_version'")
        db.init_db()
        backups = list((self.folder / "data" / "backups").glob(
            "reporting_728_before_schema_v12_*.db"
        ))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as previous:
            self.assertEqual(
                previous.execute("SELECT value FROM app_meta "
                                 "WHERE key='schema_version'").fetchone()[0],
                "11",
            )
        house = db.row("SELECT * FROM houses WHERE id=?", (house_id,))
        self.assertEqual(house["total_area"], 6300)
        self.assertIsNone(house["residential_area"])
        self.assertIsNone(house["non_residential_area"])
        self.assertEqual(house["management_agreement_number"], "")


if __name__ == "__main__":
    unittest.main()
