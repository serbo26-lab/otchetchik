"""Render only the permitted signing block; never pass hidden original data to a file."""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageDraw

from .act_images import (SIGNATURE_HEIGHT_MM, SIGNATURE_LINE_MM, SIGNATURE_WIDTH_MM,
                         fitted_size, image_png, prepare_act_image, signature_placement)
from .report_signing import public_block, verify_snapshot
from .utils import format_date

BLOCK_TITLE = "Дополнительные реквизиты подписантов"
DPI = 300
MM_PER_INCH = 25.4


def _pixels(mm):
    return max(1, round(mm * DPI / MM_PER_INCH))


def _signature_field(record):
    image = prepare_act_image(base64.b64decode(record["content"], validate=True),
                              record["settings"], kind="signature")
    x, y, width, height = signature_placement(image.size, record["settings"])
    canvas = Image.new("RGBA", (_pixels(SIGNATURE_WIDTH_MM), _pixels(SIGNATURE_HEIGHT_MM)), "white")
    image = image.resize((_pixels(width), _pixels(height)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(image, (round(x * DPI / MM_PER_INCH),
                                   round((SIGNATURE_HEIGHT_MM - y - height) * DPI / MM_PER_INCH)))
    line_y = round((SIGNATURE_HEIGHT_MM - SIGNATURE_LINE_MM) * DPI / MM_PER_INCH)
    ImageDraw.Draw(canvas).line((0, line_y, canvas.width - 1, line_y), fill="black", width=2)
    return image_png(canvas), SIGNATURE_WIDTH_MM, SIGNATURE_HEIGHT_MM


def _stamp_field(record):
    settings = record["settings"]
    image = prepare_act_image(base64.b64decode(record["content"], validate=True), settings, kind="stamp")
    width, height = fitted_size(image.size, settings["size_mm"], settings["size_mm"])
    image.thumbnail((_pixels(width), _pixels(height)), Image.Resampling.LANCZOS)
    return image_png(image), width, height


def signing_elements(bundle):
    verify_snapshot(bundle)
    block = public_block(bundle.get("signing"))
    if not block:
        return []
    elements = [(BLOCK_TITLE, None), ("Дата: " + format_date(block["date"]), None)]
    for person in block["people"]:
        text = person["label"] + ": " + person["name"]
        if person["position"]:
            text += " — " + person["position"]
        image = person["image"]
        elements.append((text, _signature_field(image) if image else None))
        if not image:
            elements.append((person["mark"], None))
    if block["stamp"]:
        elements.append(("Печать УО", _stamp_field(block["stamp"])))
    return elements


def append_word(doc, bundle):
    from docx.shared import Mm

    for text, image in signing_elements(bundle):
        para = doc.add_paragraph(text)
        if image:
            content, width, height = image
            para.paragraph_format.keep_with_next = True
            mark = doc.add_paragraph()
            mark.add_run().add_picture(BytesIO(content), width=Mm(width), height=Mm(height))


def append_excel(ws, row, bundle):
    from openpyxl.drawing.image import Image as ExcelImage
    from openpyxl.styles import Alignment

    for text, image in signing_elements(bundle):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        cell = ws.cell(row, 1, text)
        cell.data_type = "s"
        cell.alignment = Alignment(wrap_text=True)
        ws.row_dimensions[row].height = 30
        row += 1
        if image:
            content, width, height = image
            picture = ExcelImage(BytesIO(content))
            picture.width = width * 96 / MM_PER_INCH
            picture.height = height * 96 / MM_PER_INCH
            ws.add_image(picture, f"B{row}")
            ws.row_dimensions[row].height = height * 72 / MM_PER_INCH + 5
            row += 1


def append_pdf(elements, bundle, paragraph):
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as PdfImage, KeepTogether, Spacer

    for text, image in signing_elements(bundle):
        items = [paragraph(text)]
        if image:
            content, width, height = image
            mark = PdfImage(BytesIO(content), width=width * mm, height=height * mm)
            mark.hAlign = "LEFT"
            items.append(mark)
        items.append(Spacer(1, 4))
        elements.append(KeepTogether(items))
