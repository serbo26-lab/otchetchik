"""Test the ported image editor with fictitious marks."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtWidgets import QApplication
from src.uk728.act_image_dialog import ActImageDialog
from src.uk728.act_images import prepare_act_image, fitted_size
from test_v1698_signing_storage import synthetic_image


class ImageEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_resize_rotate_preview_keeps_original(self):
        data = synthetic_image()
        dialog = ActImageDialog("stamp", data, "synthetic.png")
        self.addCleanup(dialog.close)
        dialog.fields["size_mm"].setValue(35)
        dialog._rotate()
        self.assertEqual(dialog.settings["rotation"], 90)
        self.assertEqual(dialog.content, data)
        image = prepare_act_image(data, dialog.settings, kind="stamp")
        self.assertAlmostEqual(max(fitted_size(image.size, 35, 35)), 35)
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.reject()
