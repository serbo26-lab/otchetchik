"""Synthetic marks only; no authentic signatures or seals."""
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from src.uk728 import db, paths, signatories, signing_store


def synthetic_image():
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).line((10, 10, 90, 90), fill="blue", width=3)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class SigningStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patcher = patch.object(paths, "data_dir", return_value=root)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        self.person = signatories.save_local_signatory(
            {"full_name": "Учебный подписант", "basis_kind": "Устав", "valid_from": "2025-01-01"},
            company_id=self.company)

    def test_original_image_and_physical_settings_survive_edit(self):
        data = synthetic_image()
        signing_store.save_signature(self.person, data, "synthetic.png", {"width_mm": 25})
        signing_store.save_options(self.company, stamp=data, stamp_name="synthetic.png",
                                   stamp_settings={"size_mm": 40}, insert_images=1)
        self.assertEqual(signing_store.read_signature(self.person)["content"], data)
        self.assertEqual(signing_store.read_signature(self.person)["settings"]["width_mm"], 25)
        self.assertEqual(signing_store.read_options(self.company)["stamp_settings"]["size_mm"], 40)
        with self.assertRaises(ValueError):
            signing_store.save_signature(self.person, b"not an image")
        self.assertEqual(signing_store.read_signature(self.person)["content"], data)
        signing_store.save_signature(self.person, None)
        self.assertFalse(signing_store.read_signature(self.person))
