"""Read-only source integration using only artificial image data."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.uk728 import assistant_source, db, paths, signing_store
from test_v1698_signing_storage import synthetic_image


class SigningImportTests(unittest.TestCase):
    def test_import_and_refresh_preserve_source_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "assistant.db"
            data = synthetic_image()
            with sqlite3.connect(source) as conn:
                conn.executescript("""
                    CREATE TABLE management_organizations(id TEXT PRIMARY KEY,name TEXT,ogrn TEXT);
                    CREATE TABLE houses(id TEXT PRIMARY KEY,organization_id TEXT,address TEXT);
                    CREATE TABLE act_people(id TEXT PRIMARY KEY,organization_id TEXT,house_id TEXT,
                        data_json TEXT,signature BLOB,signature_name TEXT,signature_settings TEXT);
                    CREATE TABLE act_organization_options(organization_id TEXT PRIMARY KEY,
                        insert_images INTEGER,customer_signature_placeholder INTEGER,
                        stamp BLOB,stamp_name TEXT,stamp_settings TEXT);
                    INSERT INTO management_organizations VALUES('org','Учебная УО','');
                    INSERT INTO houses VALUES('house','org','Учебный дом');
                """)
                conn.execute("INSERT INTO act_people VALUES(?,?,?,?,?,?,?)", (
                    "person", "org", None, json.dumps({"full_name":"Учебный подписант"}),
                    data, "synthetic.png", '{"width_mm":25}'))
                conn.execute("INSERT INTO act_organization_options VALUES(?,?,?,?,?,?)",
                             ("org", 1, 1, data, "synthetic.png", '{"size_mm":40}'))
            digest = hashlib.sha256(source.read_bytes()).digest()
            with patch.object(paths, "data_dir", return_value=root / "local"):
                db.init_db()
                assistant_source.synchronize(source, save_path=True)
                person = db.row("SELECT id FROM signatories")[0]
                company = db.row("SELECT id FROM companies")[0]
                self.assertEqual(signing_store.read_signature(person)["content"], data)
                self.assertEqual(signing_store.read_signature(person)["settings"]["width_mm"], 25)
                self.assertEqual(signing_store.read_options(company)["stamp_settings"]["size_mm"], 40)
                self.assertTrue(assistant_source.synchronize().unchanged)
                with self.assertRaises(ValueError):
                    signing_store.save_signature(person, None)
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
