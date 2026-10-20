"""Validated preview + atomic bulk report updates (stage D).

The preview is an immutable plan, never a writable source of amounts.
The entire plan is rebuilt inside BEGIN IMMEDIATE before any changes are made.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from . import db

CENT = Decimal("0.01")
MODES = {"area", "monthly", "fixed", "split", "quantity"}
TABLES = {
    "Содержание": "report_content_items",
    "Текущий ремонт": "report_repair_items",
    "Управление": "report_management_items",
}

ACTION_TEXT = {
    "insert": "Будет добавлена",
    "update": "Будет обновлена",
    "create": "Создать отчет и добавить",
    "skip_exists": "Уже есть — пропуск",
    "skip_manual": "Ручные правки защищены — пропуск",
    "skip_report": "Нет отчета — пропуск",
    "skip_many_reports": "Несколько отчетов за год — пропуск",
    "skip_ambiguous": "Неоднозначная строка — пропуск",
    "skip_area": "Площадь не указана — пропуск",
}

class OutdatedPreview(ValueError):
    """The database no longer matches the explicitly confirmed preview."""


def amount(value: Any, label: str, *, strictly_positive: bool = False) -> Decimal:
    text = str(value if value is not None else "").strip().replace(" ", "").replace(",", ".")
    if not text:
        raise ValueError(f"Введите значение поля «{label}».")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Поле «{label}» должно содержать число.") from exc
    if not result.is_finite() or result < 0 or (strictly_positive and result == 0):
        raise ValueError(f"Поле «{label}» должно быть положительным числом." if strictly_positive
                         else f"Поле «{label}» не может быть отрицательным.")
    if result > Decimal("1000000000000"):
        raise ValueError(f"Слишком большое значение поля «{label}».")
    return result


def validate_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    mode = str(inputs.get("mode") or "")
    if mode not in MODES:
        raise ValueError("Выберите способ расчета.")
    cid = int(inputs.get("company_id") or 0)
    wid = int(inputs.get("work_id") or 0)
    year = int(inputs.get("year") or 0)
    if not cid or not wid:
        raise ValueError("Выберите управляющую организацию и работу.")
    if year < 2000 or year > 2100:
        raise ValueError("Некорректный отчетный год.")
    targets = sorted({int(x) for x in inputs.get("house_ids", [])})
    if not targets:
        raise ValueError("Отметьте хотя бы один дом для заполнения.")
    result = {
        "company_id": cid, "work_id": wid, "year": year, "house_ids": targets,
        "mode": mode, "create_missing": bool(inputs.get("create_missing")),
        "update_existing": bool(inputs.get("update_existing")),
        "override_manual": bool(inputs.get("override_manual")) and bool(inputs.get("update_existing")),
        "rate": "", "months": 12, "quantity": "", "total": "",
    }
    if mode in {"area", "monthly", "quantity"}:
        result["rate"] = str(amount(inputs.get("rate"), "Ставка / цена", strictly_positive=True))
    if mode == "monthly":
        months_text = str(inputs.get("months") or "").strip()
        if not months_text.isdecimal() or not (1 <= int(months_text) <= 12):
            raise ValueError("Количество месяцев должно быть целым числом от 1 до 12.")
        result["months"] = int(months_text)
    if mode == "quantity":
        result["quantity"] = str(amount(inputs.get("quantity"), "Количество", strictly_positive=True))
    if mode in {"fixed", "split"}:
        result["total"] = str(amount(inputs.get("total"), "Сумма", strictly_positive=True))
    return result


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _fingerprint(row):
    if row is None:
        return None
    # Also detects changes in actual quantities, source and manual flags.
    return [(key, row[key]) for key in row.keys()]


def _existing_rows(conn, table: str, rid: int, wid: int, name: str):
    matches = conn.execute(
        f"""SELECT * FROM {table}
            WHERE report_id=? AND
                 (work_item_id=? OR (work_item_id IS NULL AND TRIM(name)=TRIM(?)))
            ORDER BY id""", (rid, wid, name)
    ).fetchall()
    return matches


def build_plan(conn, raw_inputs: dict[str, Any]) -> dict[str, Any]:
    inputs = validate_inputs(raw_inputs)
    company = conn.execute("SELECT id FROM companies WHERE id=?", (inputs["company_id"],)).fetchone()
    work = conn.execute("SELECT * FROM work_items WHERE id=? AND is_active=1", (inputs["work_id"],)).fetchone()
    if not company or not work:
        raise ValueError("Управляющая организация или работа больше не существует.")
    section = str(work["category"] or "").strip()
    if section not in TABLES:
        raise ValueError("У выбранной работы должен быть указан раздел формы: Содержание, Текущий ремонт или Управление.")
    table = TABLES[section]
    selected_ids = inputs["house_ids"]
    placeholders = ",".join("?" for _ in selected_ids)
    houses = conn.execute(
        f"""SELECT * FROM houses
            WHERE company_id=? AND status!='Архив' AND id IN ({placeholders})
            ORDER BY address, id""",
        (inputs["company_id"], *selected_ids),
    ).fetchall()
    if {int(h["id"]) for h in houses} != set(selected_ids):
        raise ValueError("Список домов изменился. Обновите выбор домов.")
    rows = []
    for h in houses:
        hid = int(h["id"])
        area = _decimal(h["total_area"])
        reports = conn.execute(
            "SELECT id FROM reports WHERE house_id=? AND year=? ORDER BY id",
            (hid, inputs["year"]),
        ).fetchall()
        report_id = int(reports[0]["id"]) if len(reports) == 1 else None
        matches = _existing_rows(conn, table, report_id, inputs["work_id"], work["name"]) if report_id else []
        existing = matches[0] if len(matches) == 1 else None
        if len(reports) > 1:
            action = "skip_many_reports"
        elif len(matches) > 1:
            action = "skip_ambiguous"
        elif existing and int(existing["is_manual"] or 0) and not inputs["override_manual"]:
            action = "skip_manual"
        elif existing and not inputs["update_existing"]:
            action = "skip_exists"
        elif existing:
            action = "update"
        elif report_id is None and not inputs["create_missing"]:
            action = "skip_report"
        elif report_id is None:
            action = "create"
        else:
            action = "insert"
        if inputs["mode"] in {"area", "monthly", "split"} and area <= 0:
            action = "skip_area" if action in {"insert", "update", "create"} else action
        rows.append({
            "house_id": hid, "address": str(h["address"] or ""), "area": str(area),
            "house_state": _fingerprint(h),
            "report_id": report_id, "existing_id": int(existing["id"]) if existing else None,
            "existing_state": _fingerprint(existing),
            "match_ids": [int(r["id"]) for r in matches],
            "report_ids": [int(r["id"]) for r in reports],
            "action": action,
            "unit": "", "price": 0.0, "quantity": 0.0, "cost": 0.0,
        })
    actionable = [r for r in rows if r["action"] in ("insert", "update", "create")]
    mode = inputs["mode"]
    rate = _decimal(inputs["rate"] or 0)
    total = _decimal(inputs["total"] or 0)
    qty = _decimal(inputs["quantity"] or 0)
    months = int(inputs["months"])
    total_area = sum((_decimal(r["area"]) for r in actionable), Decimal(0))
    if mode == "split" and actionable and total_area <= 0:
        raise ValueError("Для распределения суммы необходима положительная площадь выбранных домов.")
    accumulated = Decimal(0)
    for idx, row in enumerate(actionable):
        area = _decimal(row["area"])
        if mode == "monthly":
            line_qty = area
            price = rate * months
            cost = price * line_qty
            unit = "м²"
        elif mode == "area":
            line_qty = area
            price = rate
            cost = price * line_qty
            unit = "м²"
        elif mode == "fixed":
            line_qty = Decimal(1)
            price = total
            cost = total
            unit = "усл."
        elif mode == "split":
            line_qty = area
            cost = (total - accumulated if idx == len(actionable) - 1
                    else (total * area / total_area).quantize(CENT, rounding=ROUND_HALF_UP))
            accumulated += cost
            price = cost / line_qty
            unit = "м²"
        else:
            line_qty = qty
            price = rate
            cost = price * line_qty
            unit = "усл."
        row.update(cost=float(cost), price=float(price), quantity=float(line_qty), unit=unit)
    summary = {
        "insert": sum(r["action"] in ("insert", "create") for r in rows),
        "update": sum(r["action"] == "update" for r in rows),
        "skip": sum(r["action"].startswith("skip") for r in rows),
        "create_reports": sum(r["action"] == "create" for r in rows),
        "cost": float(sum((_decimal(r["cost"]) for r in actionable), Decimal(0))),
    }
    return {
        "inputs": inputs,
        "work_state": _fingerprint(work),
        "work_name": str(work["name"]),
        "section": section, "table": table,
        "rows": rows, "summary": summary,
    }


def apply_plan(conn, preview: dict[str, Any]) -> dict[str, Any]:
    """Write only what the user saw. Refuse stale data, and roll back atomically."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        try:
            current = build_plan(conn, preview["inputs"])
        except ValueError as exc:
            raise OutdatedPreview("Данные изменились с момента предпросмотра. Обновите его.") from exc
        if current != preview:
            raise OutdatedPreview("Данные изменились с момента предпросмотра. Обновите его.")
        if not current["summary"]["insert"] and not current["summary"]["update"]:
            raise ValueError("Нет изменений для применения.")
        work_name = preview["work_name"]
        table = preview["table"]
        wid = preview["inputs"]["work_id"]
        cid = preview["inputs"]["company_id"]
        year = preview["inputs"]["year"]
        for row in preview["rows"]:
            action = row["action"]
            if action not in ("insert", "update", "create"):
                continue
            rid = row["report_id"]
            if action == "create":
                rid = db.create_report_in_conn(conn, cid, row["house_id"], year)
            cost, price, qty, unit = (row["cost"], row["price"], row["quantity"], row["unit"])
            existing_id = row["existing_id"]
            if table == "report_content_items":
                if action == "update":
                    conn.execute("""UPDATE report_content_items SET unit=?, unit_price=?, plan_qty=?,
                        plan_cost=?, actual_qty=?, actual_cost=?, source='массовое применение',
                        is_manual=0 WHERE id=? AND report_id=?""",
                        (unit, price, qty, cost, qty, cost, existing_id, rid))
                else:
                    pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM report_content_items WHERE report_id=?", (rid,)).fetchone()[0]
                    conn.execute("""INSERT INTO report_content_items
                        (report_id,work_item_id,name,unit,unit_price,plan_qty,plan_cost,actual_qty,
                         actual_cost,source,is_manual,position)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, wid, work_name, unit, price, qty, cost, qty, cost, "массовое применение", 0, pos))
            elif table == "report_repair_items":
                volume = f"{qty:g} {unit}" if qty else unit
                if action == "update":
                    conn.execute("""UPDATE report_repair_items SET cost=?,volume=?,source='массовое применение',
                        is_manual=0 WHERE id=? AND report_id=?""", (cost, volume, existing_id, rid))
                else:
                    pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM report_repair_items WHERE report_id=?", (rid,)).fetchone()[0]
                    conn.execute("""INSERT INTO report_repair_items
                        (report_id,work_item_id,name,basis,cost,volume,act_details,source,is_manual,position)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (rid, wid, work_name, "", cost, volume, "", "массовое применение", 0, pos))
            else:
                if action == "update":
                    conn.execute("""UPDATE report_management_items SET unit=?,unit_price=?,qty=?,cost=?,
                        source='массовое применение',is_manual=0 WHERE id=? AND report_id=?""",
                        (unit, price, qty, cost, existing_id, rid))
                else:
                    pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM report_management_items WHERE report_id=?", (rid,)).fetchone()[0]
                    conn.execute("""INSERT INTO report_management_items
                        (report_id,work_item_id,name,unit,unit_price,qty,cost,source,is_manual,position)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (rid, wid, work_name, unit, price, qty, cost, "массовое применение", 0, pos))
        conn.commit()
        return preview["summary"]
    except Exception:
        conn.rollback()
        raise
