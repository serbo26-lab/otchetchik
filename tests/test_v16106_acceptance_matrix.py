"""Cross-section acceptance matrix; all records and images are synthetic."""
from contextlib import closing
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from src.uk728 import db, paths, report_signing, signatories, signing_store
from src.uk728.act_images import (
    decode_act_image, fitted_size, image_png, normalize_image_settings,
    prepare_act_image, signature_placement,
)
from src.uk728.mass_apply_logic import (
    OutdatedPreview, amount, apply_plan, build_plan, validate_inputs,
)


class BulkMatrixTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(paths, "app_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        self.houses = [
            db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                       (self.company, f"Учебный объект {i}", area))
            for i, area in enumerate((100, 300))
        ]

    def new_work(self, section, suffix):
        return db.execute("INSERT INTO work_items(name,category,unit) VALUES(?,?,?)",
                          (f"Учебная работа {section} {suffix}", section, "шт."))

    def inputs(self, work, **changes):
        return dict(company_id=self.company, work_id=work, year=2025,
                    house_ids=self.houses, mode="fixed", total="10",
                    create_missing=True) | changes

    def check_write(self, section, table, cost_column, mode, values, expected):
        work = self.new_work(section, mode)
        raw = self.inputs(work, mode=mode, **values)
        with closing(db.connect()) as conn:
            preview = build_plan(conn, raw)
            self.assertEqual([r["cost"] for r in preview["rows"]], expected)
            self.assertEqual(preview["summary"]["cost"], sum(expected))
            self.assertEqual(apply_plan(conn, preview), preview["summary"])
            records = conn.execute(
                f"SELECT * FROM {table} WHERE work_item_id=? ORDER BY report_id", (work,)
            ).fetchall()
            self.assertEqual(len(records), 2)
            self.assertEqual([row[cost_column] for row in records], expected)
            self.assertTrue(all(row["is_manual"] == 0 for row in records))
            old_ids = [row["id"] for row in records]
            raw["update_existing"] = True
            repeat = build_plan(conn, raw)
            self.assertEqual(repeat["summary"]["update"], 2)
            apply_plan(conn, repeat)
            ids = conn.execute(
                f"SELECT id FROM {table} WHERE work_item_id=? ORDER BY report_id", (work,)
            ).fetchall()
            self.assertEqual([row["id"] for row in ids], old_ids)

    def test_all_calculation_modes_in_all_three_sections(self):
        sections = (
            ("Содержание", "report_content_items", "plan_cost"),
            ("Текущий ремонт", "report_repair_items", "cost"),
            ("Управление", "report_management_items", "cost"),
        )
        modes = (
            ("area", {"rate": "2,5"}, [250, 750]),
            ("monthly", {"rate": "2", "months": "3"}, [600, 1800]),
            ("fixed", {"total": "11,25"}, [11.25, 11.25]),
            ("split", {"total": "101"}, [25.25, 75.75]),
            ("quantity", {"rate": "2,5", "quantity": "4"}, [10, 10]),
        )
        for section, table, column in sections:
            for mode, values, expected in modes:
                with self.subTest(section=section, mode=mode):
                    self.check_write(section, table, column, mode, values, expected)

    def test_protected_reports_stay_unchanged_in_every_section(self):
        protected = db.create_report(self.company, self.houses[0], 2025)
        db.execute("UPDATE reports SET status=? WHERE id=?", ("Архив", protected))
        for section in ("Содержание", "Текущий ремонт", "Управление"):
            with self.subTest(section=section):
                work = self.new_work(section, "protected")
                with closing(db.connect()) as conn:
                    plan = build_plan(conn, self.inputs(work, update_existing=True, override_manual=True))
                    self.assertEqual(plan["rows"][0]["action"], "skip_protected_report")
                    apply_plan(conn, plan)
                    self.assertEqual(conn.execute(
                        f"SELECT COUNT(*) FROM {plan['table']} WHERE report_id=?", (protected,)
                    ).fetchone()[0], 0)

    def test_no_area_skips_only_area_dependent_modes(self):
        db.execute("UPDATE houses SET total_area=0 WHERE id=?", (self.houses[0],))
        work = self.new_work("Содержание", "zero")
        cases = (("area", {"rate": "2"}), ("monthly", {"rate": "2", "months": 12}),
                 ("split", {"total": "100"}), ("fixed", {"total": "100"}),
                 ("quantity", {"rate": "2", "quantity": "3"}))
        with closing(db.connect()) as conn:
            for mode, values in cases:
                with self.subTest(mode=mode):
                    result = build_plan(conn, self.inputs(work, mode=mode, **values))
                    expected = "skip_area" if mode in {"area", "monthly", "split"} else "create"
                    self.assertEqual(result["rows"][0]["action"], expected)

    def test_changed_work_and_tampered_preview_are_rejected(self):
        work = self.new_work("Управление", "stale")
        with closing(db.connect()) as conn:
            raw = self.inputs(work)
            plan = build_plan(conn, raw)
            plan["rows"][0]["cost"] = 99999
            with self.assertRaises(OutdatedPreview):
                apply_plan(conn, plan)
            plan = build_plan(conn, raw)
            db.execute("UPDATE work_items SET name=? WHERE id=?", ("Учебная новая работа", work))
            with self.assertRaises(OutdatedPreview):
                apply_plan(conn, plan)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 0)

    def test_number_and_month_boundaries(self):
        for value in ("", "-1", "NaN", "Infinity", "1e13", "text", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                amount(value, "Учебное поле")
        self.assertEqual(amount("1 234,50", "Учебное поле"), Decimal("1234.50"))
        self.assertEqual(amount("0", "Учебное поле"), Decimal(0))
        with self.assertRaises(ValueError):
            amount("0", "Учебное поле", strictly_positive=True)
        work = self.new_work("Содержание", "months")
        for months in ("0", "13", "1.5", "-1", ""):
            with self.subTest(months=months), self.assertRaises(ValueError):
                validate_inputs(self.inputs(work, mode="monthly", rate="1", months=months))
        for months in (1, 12):
            self.assertEqual(validate_inputs(self.inputs(
                work, mode="monthly", rate="1", months=months))["months"], months)


def sample_png(background="white"):
    image = Image.new("RGB", (40, 20), background)
    ImageDraw.Draw(image).line((5, 5, 30, 15), fill="blue", width=2)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageBoundaryTests(unittest.TestCase):
    def test_all_dimensions_and_rotation_boundaries(self):
        for key, low, high in (("width_mm", 5, 38), ("height_mm", 3, 13),
                               ("offset_mm", -5, 5)):
            for value in (low, high):
                with self.subTest(key=key, value=value):
                    self.assertEqual(normalize_image_settings({key: value}, kind="signature")[key], value)
            for value in (low - .01, high + .01, float("nan"), float("inf"), "bad"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalize_image_settings({key: value}, kind="signature")
        for value in (10, 50):
            self.assertEqual(normalize_image_settings({"size_mm": value}, kind="stamp")["size_mm"], value)
        for value in (9, 51):
            with self.assertRaises(ValueError):
                normalize_image_settings({"size_mm": value}, kind="stamp")
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                result = prepare_act_image(sample_png(), {"rotation": rotation}, kind="signature")
                self.assertEqual(result.mode, "RGBA")
                self.assertGreater(result.width, 0)
        with self.assertRaises(ValueError):
            normalize_image_settings({"rotation": 45}, kind="signature")

    def test_invalid_and_empty_crops_rejected(self):
        for crop in ([0, 0, 0, 1], [-.1, 0, 1, 1], [0, 0, 1.1, 1], [0, 1], None):
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                normalize_image_settings({"crop": crop}, kind="signature")
        with self.assertRaises(ValueError):
            prepare_act_image(sample_png(), {"crop": [0, 0, .05, .05]}, kind="signature")

    def test_corrupt_and_oversize_image_bytes_rejected(self):
        for value in (b"", b"fake", b"\x89PNG\r\n\x1a\n", b"x" * (5 * 1024 * 1024 + 1)):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                decode_act_image(value)

    def test_png_roundtrip_and_physical_fit(self):
        original = sample_png()
        result = prepare_act_image(original, kind="signature")
        self.assertEqual(decode_act_image(image_png(result)).size, result.size)
        self.assertEqual(decode_act_image(original).size, (40, 20))
        for size in ((100, 20), (20, 100), (100, 100)):
            with self.subTest(size=size):
                width, height = fitted_size(size, 38, 13)
                self.assertLessEqual(width, 38)
                self.assertLessEqual(height, 13)
                self.assertAlmostEqual(width / height, size[0] / size[1])
                x, y, w, h = signature_placement(size)
                self.assertAlmostEqual(x + w / 2, 19)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y + h, 26)

    def test_non_mapping_settings_rejected_even_if_empty(self):
        for value in ([], "", 0, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_image_settings(value, kind="signature")


class SigningBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(paths, "app_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.company = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
        self.house = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)",
                                (self.company, "Учебный объект"))
        self.report = db.create_report(self.company, self.house, 2025)
        self.person = signatories.save_local_signatory(
            {"full_name": "Учебный представитель", "basis_kind": "Устав", "valid_from": "2025-01-01"},
            company_id=self.company)
        self.resident = signatories.save_local_signatory(
            {"full_name": "Учебный собственник"}, house_id=self.house)
        signing_store.save_signature(self.person, sample_png())
        signing_store.save_signature(self.resident, sample_png())
        signing_store.save_options(self.company, stamp=sample_png())

    def test_all_privacy_and_consent_combinations_in_saved_snapshots(self):
        for mode in report_signing.PRIVACY_MODES:
            for consent in (False, True):
                with self.subTest(mode=mode, consent=consent):
                    saved = report_signing.save_selection(
                        self.report, "2025-12-31", [self.person, self.resident],
                        privacy=mode, insert_images=consent)
                    self.assertEqual(report_signing.read_snapshot(self.report), saved)
                    public = report_signing.public_block(saved)
                    company, house = public["people"]
                    self.assertEqual(company["name"] == report_signing.HIDDEN, mode == "all_identity")
                    self.assertEqual(house["name"] == report_signing.HIDDEN,
                                     mode in {"all_identity", "house_identity"})
                    self.assertEqual(bool(company["image"]), consent and mode != "all_identity")
                    self.assertEqual(bool(house["image"]), consent and mode == "full")
                    self.assertEqual(bool(public["stamp"]), consent and mode != "all_identity")
                    self.assertEqual(bool(saved["people"][1].get("image")), consent and mode == "full")

    def test_invalid_selection_does_not_replace_saved_snapshot(self):
        before = report_signing.save_selection(self.report, "2025-12-31", [self.person])
        other = db.execute("INSERT INTO companies(name) VALUES(?)", ("Другая учебная УО",))
        stranger = signatories.save_local_signatory({"full_name": "Другой представитель", "basis_kind": "Устав", "valid_from": "2025-01-01"},
            company_id=other)
        for day, people, privacy in (("invalid", [self.person], "full"),
                                     ("2025-12-31", [], "full"),
                                     ("2025-12-31", [stranger], "full"),
                                     ("2025-12-31", [self.person], "invalid")):
            with self.subTest(day=day, people=people, privacy=privacy), self.assertRaises(ValueError):
                report_signing.save_selection(self.report, day, people, privacy=privacy)
            self.assertEqual(report_signing.read_snapshot(self.report), before)
        with self.assertRaises(ValueError):
            report_signing.save_selection(self.report, "2025-12-31", [self.person],
                                          expected_snapshot={})
        self.assertEqual(report_signing.read_snapshot(self.report), before)

    def test_duplicate_selection_and_stamp_without_company_signatory(self):
        snapshot = report_signing.save_selection(
            self.report, "2025-12-31", [self.resident, self.resident], insert_images=True)
        self.assertEqual(len(snapshot["people"]), 1)
        self.assertIsNone(report_signing.public_block(snapshot)["stamp"])


if __name__ == "__main__":
    unittest.main()
