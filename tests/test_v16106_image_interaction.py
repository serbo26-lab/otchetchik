"""Exercise real mouse selection, file replacement and preview failure recovery."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from src.uk728.act_image_dialog import ActImageDialog, ImageRegion, PlacementPreview


def scan(size=(160, 100), color="blue"):
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).rectangle((20, 20, 80, 60), fill=color)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class ImageInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def dialog(self, kind="signature"):
        dialog = ActImageDialog(kind, scan(), "synthetic.png")
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        return dialog

    @staticmethod
    def point(region, x, y):
        bounds = region.image_rect()
        return QPoint(round(bounds.left() + x * bounds.width()),
                      round(bounds.top() + y * bounds.height()))

    def drag(self, region, start, end):
        first = self.point(region, *start)
        last = self.point(region, *end)
        QTest.mousePress(region, Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(region, last)
        QTest.mouseRelease(region, Qt.MouseButton.LeftButton, pos=last)
        self.app.processEvents()

    def test_empty_region_and_preview_paint(self):
        region = ImageRegion()
        self.addCleanup(region.close)
        self.assertTrue(region.image_rect().isEmpty())
        self.assertEqual(region.sizeHint().width(), 520)
        region.show()
        self.assertFalse(region.grab().isNull())
        QTest.mouseClick(region, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        self.assertIsNone(region.anchor)
        for kind in ("signature", "stamp"):
            preview = PlacementPreview(kind)
            self.addCleanup(preview.close)
            preview.show()
            self.assertFalse(preview.grab().isNull())

    def test_mouse_crop_reverse_drag_and_degenerate_selection(self):
        dialog = self.dialog()
        region = dialog.region
        spy = QSignalSpy(region.cropChanged)
        self.drag(region, (.65, .75), (.08, .12))
        self.assertEqual(spy.count(), 1)
        for actual, expected in zip(dialog.settings["crop"], (.08, .12, .65, .75)):
            self.assertAlmostEqual(actual, expected, delta=.01)
        self.assertTrue(dialog.save_button.isEnabled())
        old = list(region.crop)
        point = self.point(region, .5, .5)
        QTest.mouseClick(region, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(region.crop, old)
        self.assertEqual(spy.count(), 1)
        QTest.mouseClick(region, Qt.MouseButton.RightButton, pos=point)
        self.assertEqual(region.crop, old)
        self.assertFalse(region.grab().isNull())

    def test_blank_crop_disables_save_and_whole_restores(self):
        dialog = self.dialog()
        self.drag(dialog.region, (.75, .75), (.95, .95))
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIsNone(dialog.prepared_size)
        self.assertTrue(dialog.error_label.text())
        self.assertFalse(dialog.size_label.text())
        self.assertTrue(dialog.preview.picture.isNull())
        dialog._whole()
        self.assertEqual(dialog.settings["crop"], [0, 0, 1, 1])
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertFalse(dialog.error_label.text())
        self.assertIn("мм", dialog.size_label.text())

    def test_four_rotations_preserve_crop_and_original_bytes(self):
        dialog = self.dialog()
        self.drag(dialog.region, (.08, .12), (.65, .75))
        initial = list(dialog.settings["crop"])
        original = dialog.content
        for angle in (90, 180, 270, 0):
            dialog._rotate()
            self.assertEqual(dialog.settings["rotation"], angle)
            self.assertTrue(dialog.save_button.isEnabled())
        for actual, expected in zip(dialog.settings["crop"], initial):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(dialog.content, original)

    def test_file_cancel_invalid_then_valid_replacement(self):
        dialog = self.dialog("stamp")
        original = dialog.content
        dialog._rotate()
        with TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.png"
            bad.write_bytes(b"invalid")
            good = Path(tmp) / "new.png"
            good.write_bytes(scan((200, 120), "red"))
            with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")):
                dialog._choose_file()
            self.assertEqual(dialog.content, original)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(bad), "")), \
                 patch.object(QMessageBox, "warning") as warning:
                dialog._choose_file()
                warning.assert_called_once()
            self.assertEqual(dialog.content, original)
            self.assertEqual(dialog.settings["rotation"], 90)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(good), "")):
                dialog._choose_file()
            self.assertEqual(dialog.content, good.read_bytes())
            self.assertEqual(dialog.name, good.name)
            self.assertEqual(dialog.settings["rotation"], 0)
            self.assertEqual(dialog.settings["crop"], [0, 0, 1, 1])
            self.assertEqual(dialog.file_label.text(), good.name)

    def test_signature_dimensions_background_and_acceptance(self):
        dialog = self.dialog()
        before = dialog.content
        dialog.fields["width_mm"].setValue(20)
        dialog.fields["height_mm"].setValue(8)
        dialog.fields["offset_mm"].setValue(-3)
        dialog.remove_background.setChecked(False)
        self.assertEqual(dialog.settings["width_mm"], 20)
        self.assertEqual(dialog.settings["height_mm"], 8)
        self.assertEqual(dialog.preview.settings["offset_mm"], -3)
        self.assertFalse(dialog.settings["remove_background"])
        self.assertEqual(dialog.content, before)
        self.assertFalse(dialog.preview.grab().isNull())
        dialog.save_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
