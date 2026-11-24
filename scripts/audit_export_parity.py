"""One-off historical export comparison; no actual database is opened."""
from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader


def bundle():
    return {
        "report": {
            "id": 71, "year": 2025, "period_start": "2025-01-01",
            "period_end": "2025-12-31", "show_period_note": 0,
            "publish_date": "2026-03-01", "management_cost": 0,
            "repair_start_balance": -12.5, "repair_accrual": 200,
            "repair_work_cost": 56.78, "repair_end_balance": 130.72,
        },
        "company": {
            "name": "Учебная организация", "reception_address": "Вымышленное место приёма",
            "ogrn_inn": "0000000000000 / 0000000000",
            "report_person": "Вымышленный представитель", "report_contact": "0000000000",
        },
        "house": {"address": "Вымышленный объект", "total_area": 125.5},
        "content": [
            {"name": "Учебная услуга А", "unit": "м²", "unit_price": 1.23,
             "plan_qty": 100, "plan_cost": 123, "actual_qty": 0, "actual_cost": 0},
            {"name": "Учебная услуга Б", "unit": "шт.", "unit_price": 2.5,
             "plan_qty": 4, "plan_cost": 0, "actual_qty": 3, "actual_cost": 7.5},
        ],
        "repair": [{"name": "Учебный ремонт", "basis": "Учебное основание",
                    "cost": 56.78, "volume": "2 м²", "act_details": "Учебный акт"}],
        "management": [{"name": "Внутренний расчёт", "cost": 777}],
        "claims": {"pretensions_count": 3, "lawsuits_count": 1, "recovered_amount": 20.5},
        "financials": [
            {"payer_type": "Собственники", "debt_start": -15, "accrued": 100,
             "paid": 55, "debt_end": 30, "notes": ""},
            {"payer_type": "Наниматели", "debt_start": 2, "accrued": 30,
             "paid": 20, "debt_end": 12, "notes": ""},
        ],
    }


def cases():
    full = bundle()
    empty = copy.deepcopy(full)
    empty["report"].update(period_start="2025-07-01", show_period_note=1, publish_date="")
    empty.update(content=[], repair=[], management=[], claims=None, financials=[])
    many = copy.deepcopy(full)
    many["content"] = [
        {**full["content"][index % 2], "name": f"Учебная строка {index}: длинное наименование работы"}
        for index in range(55)
    ]
    many["financials"][1].update(debt_start=0, accrued=0, paid=0, debt_end=0, notes="")
    with_note = copy.deepcopy(empty)
    with_note["financials"] = [{"payer_type": "Наниматели", "debt_start": 0,
                               "accrued": 0, "paid": 0, "debt_end": 0,
                               "notes": "Учебное примечание"}]
    return {"full": full, "empty_partial": empty, "long": many, "zero_tenant_with_note": with_note}


def xlsx_state(path):
    wb = load_workbook(path, data_only=False)
    try:
        ws = wb.active
        return {
            "title": ws.title,
            "cells": [[(cell.value, cell.data_type, str(cell.font), str(cell.alignment),
                        str(cell.border), str(cell.fill), cell.number_format)
                       for cell in row] for row in ws.iter_rows()],
            "merged": sorted(str(value) for value in ws.merged_cells.ranges),
            "columns": {key: value.width for key, value in ws.column_dimensions.items()},
            "rows": {str(key): value.height for key, value in ws.row_dimensions.items()},
            "paper": ws.page_setup.paperSize, "orientation": ws.page_setup.orientation,
        }
    finally:
        wb.close()


def docx_state(path):
    doc = Document(path)
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return {
        "paragraphs": [paragraph.text for paragraph in doc.paragraphs],
        "tables": [[[cell.text for cell in row.cells] for row in table.rows] for table in doc.tables],
        "document_xml": xml,
    }


def pdf_state(path):
    reader = PdfReader(path)
    pages = [{"text": " ".join(page.extract_text().split()),
              "size": [float(page.mediabox.width), float(page.mediabox.height)]}
             for page in reader.pages]
    if "ОТЧЕТ" not in pages[0]["text"]:
        raise RuntimeError("PDF Cyrillic text was not extracted")
    return pages


def snapshot(repo, destination):
    sys.path.insert(0, str(repo.resolve()))
    from src.uk728 import exporters
    result = {}
    with TemporaryDirectory() as folder:
        for name, data in cases().items():
            output = Path(folder) / name
            output.mkdir()
            with patch.object(exporters, "load_report_bundle", return_value=data), \
                 patch.object(exporters, "export_dir", return_value=output):
                result[name] = {
                    "xlsx": xlsx_state(exporters.export_xlsx(71)),
                    "docx": docx_state(exporters.export_docx(71)),
                    "pdf": pdf_state(exporters.export_pdf(71)),
                }
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Export snapshots: {len(result)} scenarios, {len(result) * 3} files")


def compare(first, second):
    before = json.loads(first.read_text(encoding="utf-8"))
    after = json.loads(second.read_text(encoding="utf-8"))
    differences = []
    for case, formats in before.items():
        for fmt, value in formats.items():
            same = value == after[case][fmt]
            print(case, fmt, "MATCH" if same else "DIFFERENT")
            if not same:
                differences.append((case, fmt))
    if differences:
        raise SystemExit(f"Semantic output differences: {differences}")


def inventory(repo):
    definitions = {}
    for path in sorted((repo / "src" / "uk728").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            if isinstance(node, ast.ClassDef):
                names.extend(f"{node.name}.{child.name}" for child in node.body
                             if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
        definitions[path.name] = names
    return definitions


def compare_inventory(old, new):
    before, after = inventory(old), inventory(new)
    ui_modules = ("app.py", "ui_editors.py", "mass_apply_ui.py", "backups_ui.py",
                  "directory_ui.py", "templates_ui.py", "property_ui.py", "reports_ui.py")
    relocated = {name for module in ui_modules for name in after.get(module, [])}
    for module, names in before.items():
        actual = relocated if module == "app.py" else set(after.get(module, []))
        missing = sorted(set(names) - actual)
        print(module, "before", len(names), "missing", json.dumps(missing, ensure_ascii=False))


if __name__ == "__main__":
    mode = sys.argv[1]
    first, second = Path(sys.argv[2]), Path(sys.argv[3])
    if mode == "snapshot":
        snapshot(first, second)
    elif mode == "compare":
        compare(first, second)
    elif mode == "inventory":
        compare_inventory(first, second)
    else:
        raise SystemExit("Unknown audit command")
