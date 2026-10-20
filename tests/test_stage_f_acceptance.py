"""Acceptance-stage regression suite: real DB -> reports -> DOCX/XLSX/PDF.

The official form reference is Appendix 2, Minstroy Order 728/pr dated 20.11.2025.
These tests verify section structure and numeric/data preservation, not legal validity.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader
from PySide6.QtWidgets import QApplication

from src.uk728 import db
from src.uk728.app import MainWindow
from src.uk728.exporters import (
    _management_cost, _period_line, export_docx, export_pdf, export_xlsx,
)
from src.uk728.paths import db_path


class AcceptanceStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.path_patch = patch("src.uk728.paths.app_dir",
                                return_value=Path(self.temp.name))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        db.init_db()
        self.org = db.execute(
            """INSERT INTO companies
               (name,ogrn_inn,reception_address,report_person,report_contact)
               VALUES(?,?,?,?,?)""",
            ("УК Приёмка", "1234567890123", "ул. Центральная, 10",
             "Специалист Иванов", "+7 900 111 00 11")
        )
        self.house = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.org, "ул. Кленовая, д. 5", 1200.25)
        )
        self.other_house = db.execute(
            "INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
            (self.org, "ул. Кленовая, д. 7", 700.0)
        )
        self.rid = db.create_report(self.org, self.house, 2025)
        db.execute(
            """INSERT INTO report_content_items
               (report_id,name,unit,unit_price,plan_qty,plan_cost,
                actual_qty,actual_cost,source,is_manual,position)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (self.rid, "Содержание лестничных клеток", "м²",
             6.76, 100, 676, 0, 0, "массовое применение", 1, 1)
        )
        db.execute(
            """INSERT INTO report_repair_items
               (report_id,name,basis,cost,volume,act_details,position)
               VALUES(?,?,?,?,?,?,?)""",
            (self.rid, "Ремонт дверей", "Решение ОСС", 1000,
             "4 шт.", "Акт 15", 1)
        )
        db.execute(
            """INSERT INTO report_management_items
               (report_id,name,unit,unit_price,qty,cost,position)
               VALUES(?,?,?,?,?,?,?)""",
            (self.rid, "Внутренний расчет управления", "усл.",
             700, 1, 700, 1)
        )
        db.execute(
            """UPDATE reports SET management_cost=?,repair_end_balance=?,
               repair_start_balance=?,repair_accrual=?,repair_work_cost=?,
               publish_date=? WHERE id=?""",
            (0, 2500, -50, 6000, 3500, "2026-02-26", self.rid)
        )
        db.execute(
            "UPDATE report_financials SET debt_start=?,accrued=?,paid=?,debt_end=? "
            "WHERE report_id=? AND payer_type='Собственники'",
            (70, 350, 240, 180, self.rid)
        )
        db.execute(
            "UPDATE report_financials SET debt_start=?,accrued=?,paid=?,debt_end=? "
            "WHERE report_id=? AND payer_type='Наниматели'",
            (15, 20, 5, 30, self.rid)
        )
        db.execute(
            """UPDATE report_claims SET pretensions_count=?,lawsuits_count=?,
               recovered_amount=? WHERE report_id=?""",
            (4, 2, 115, self.rid)
        )

    def test_2026_is_default_and_previous_year_is_readable(self):
        win = MainWindow()
        self.addCleanup(win.close)
        self.assertEqual(win.reports_tab.year_filter.value(), 2026)
        self.assertEqual(win.mass_tab.year.value(), 2026)
        self.assertEqual(db.load_report_bundle(self.rid)["report"]["year"], 2025)

    def test_balances_are_carried_forward_to_next_year(self):
        next_rid = db.create_report(self.org, self.house, 2026)
        data = db.load_report_bundle(next_rid)
        self.assertEqual(data["report"]["repair_start_balance"], 2500)
        finance = {row["payer_type"]: row for row in data["financials"]}
        self.assertEqual(finance["Собственники"]["debt_start"], 180)
        self.assertEqual(finance["Наниматели"]["debt_start"], 30)
        self.assertEqual(data["report"]["period_start"], "2026-01-01")
        self.assertEqual(data["report"]["period_end"], "2026-12-31")

    def test_other_house_and_year_have_separate_report_data(self):
        other = db.create_report(self.org, self.other_house, 2025)
        future = db.create_report(self.org, self.house, 2026)
        a = db.load_report_bundle(self.rid)
        b = db.load_report_bundle(other)
        c = db.load_report_bundle(future)
        self.assertEqual(len(a["content"]), 1)
        self.assertEqual(len(b["content"]), 0)
        self.assertEqual(len(c["content"]), 0)
        self.assertEqual(len(a["repair"]), 1)
        self.assertEqual(len(c["repair"]), 0)

    def test_official_total_zero_is_not_replaced_by_internal_lines(self):
        bundle = db.load_report_bundle(self.rid)
        self.assertEqual(bundle["report"]["management_cost"], 0)
        self.assertEqual(bundle["management"][0]["cost"], 700)
        self.assertEqual(_management_cost(bundle), 0)

    def test_xlsx_preserves_zero_actual_and_official_section_structure(self):
        path = export_xlsx(self.rid)
        self.assertTrue(path.is_file())
        wb = load_workbook(path, read_only=True, data_only=True)
        self.addCleanup(wb.close)
        self.assertEqual(len(wb.worksheets), 1)
        rows = [list(row) for row in wb.active.iter_rows(values_only=True)]
        flattened = "\n".join(str(cell) for row in rows for cell in row if cell is not None)
        self.assertIn("УК Приёмка", flattened)
        self.assertIn("ул. Кленовая, д. 5", flattened)
        for number in range(1, 6):
            self.assertIn(f"{number}.", flattened)
        item = next(row for row in rows if len(row) >= 8 and row[1] == "Содержание лестничных клеток")
        self.assertEqual(item[5], "676")
        self.assertEqual(item[6], "0")
        self.assertEqual(item[7], "0")
        self.assertNotIn("Внутренний расчет управления", flattened)
        key_row = next(i for i, row in enumerate(rows)
                       if row[0] and str(row[0]).startswith("3. Стоимость услуг"))
        self.assertEqual(rows[key_row + 1][0], "0 руб.")

    def test_explicit_zero_plan_cost_is_not_recalculated_in_exports(self):
        db.execute(
            "UPDATE report_content_items SET plan_cost=0, actual_qty=0, actual_cost=0 "
            "WHERE report_id=?", (self.rid,)
        )
        xlsx = export_xlsx(self.rid)
        wb = load_workbook(xlsx, read_only=True, data_only=True)
        self.addCleanup(wb.close)
        matches = [row for row in wb.active.iter_rows(values_only=True)
                   if len(row) > 7 and row[1] == "Содержание лестничных клеток"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][5], "0")
        self.assertEqual(matches[0][7], "0")
        doc = Document(export_docx(self.rid))
        item = next(row for row in doc.tables[0].rows
                    if row.cells[1].text == "Содержание лестничных клеток")
        self.assertEqual(item.cells[5].text, "0")
        self.assertEqual(item.cells[7].text, "0")

    def test_docx_has_appendix_two_structure_without_internal_breakdown(self):
        path = export_docx(self.rid)
        self.assertTrue(path.is_file())
        document = Document(path)
        self.assertEqual([len(table.columns) for table in document.tables],
                         [8, 6, 4, 6])
        text = "\n".join(p.text for p in document.paragraphs)
        self.assertIn("ОТЧЕТ", text)
        self.assertIn("УК Приёмка", text)
        self.assertIn("ул. Кленовая, д. 5", text)
        self.assertIn("1 200,25 м²", text)
        self.assertIn("26.02.2026", text)
        for number in range(1, 6):
            self.assertIn(f"{number}.", text)
        self.assertNotIn("Внутренний расчет управления", text)
        mgmt_section = next(i for i, p in enumerate(document.paragraphs)
                            if p.text.startswith("3. Стоимость услуг"))
        self.assertEqual(document.paragraphs[mgmt_section + 1].text, "0 руб.")

    def test_pdf_is_valid_and_contains_all_five_sections(self):
        path = export_pdf(self.rid)
        self.assertTrue(path.is_file())
        with path.open("rb") as fh:
            self.assertEqual(fh.read(5), b"%PDF-")
        reader = PdfReader(str(path))
        self.assertGreaterEqual(len(reader.pages), 1)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        self.assertIn("содержанию", text.lower())
        self.assertIn("текущему ремонту", text.lower())
        self.assertIn("управлению", text.lower())
        self.assertNotIn("Внутренний расчет управления", text)

    def test_exports_are_separated_by_house_and_year(self):
        current = export_xlsx(self.rid)
        next_rid = db.create_report(self.org, self.other_house, 2026)
        other = export_xlsx(next_rid)
        self.assertNotEqual(current, other)
        self.assertTrue(current.exists())
        self.assertTrue(other.exists())
        self.assertIn("2025", str(current))
        self.assertIn("2026", str(other))

    def test_period_note_for_short_period_only(self):
        partial = {"year": 2026, "period_start": "2026-03-15",
                   "period_end": "2026-12-31", "show_period_note": 0}
        self.assertIn("15.03.2026", _period_line(partial))
        annual = {"year": 2026, "period_start": "2026-01-01",
                  "period_end": "2026-12-31", "show_period_note": 0}
        self.assertEqual(_period_line(annual), "")
        annual["show_period_note"] = 1
        self.assertIn("01.01.2026", _period_line(annual))

    def test_financial_totals_in_all_exported_sections(self):
        wb = load_workbook(export_xlsx(self.rid), data_only=True)
        self.addCleanup(wb.close)
        rows = [list(row) for row in wb.active.iter_rows(values_only=True)]
        totals = [row for row in rows if row[0] == "ИТОГО"]
        self.assertGreaterEqual(len(totals), 3)
        finance_total = totals[-1]
        self.assertEqual(finance_total[2:6], ["85", "370", "245", "210"])
        doc = Document(export_docx(self.rid))
        last = doc.tables[-1].rows[-1]
        self.assertEqual([cell.text for cell in last.cells][2:6],
                         ["85", "370", "245", "210"])

    def test_saved_data_directory_stays_beside_application(self):
        actual = db_path()
        self.assertTrue(actual.exists())
        self.assertEqual(actual, Path(self.temp.name) / "data" / "reporting_728.db")
        self.assertTrue((actual.parent / "backups").exists())
        self.assertTrue((actual.parent / "exports").exists())


if __name__ == "__main__":
    unittest.main()
