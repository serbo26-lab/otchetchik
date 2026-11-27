"""Explicit selection of report signatories, with a local historical snapshot."""
from __future__ import annotations

import json
from contextlib import closing

from . import db
from .utils import iso_date, parse_date

OWNER_FIELDS = ("company_id", "house_id")


def available_people(conn, report, signing_date):
    day = parse_date(signing_date)
    if day is None:
        raise ValueError("Укажите дату подписания в формате ДД.ММ.ГГГГ.")
    result = []
    for row in conn.execute("""SELECT * FROM signatories WHERE company_id=? OR house_id=?
                                ORDER BY company_id IS NULL,id""",
                            (report["company_id"], report["house_id"])):
        data = json.loads(row["data_json"])
        start, end = data.get("valid_from"), data.get("valid_to")
        if (start and (parse_date(start) is None or parse_date(start) > day)
                or end and (parse_date(end) is None or parse_date(end) < day)):
            continue
        result.append(dict(id=row["id"], side="company" if row["company_id"] else "house",
                           name=str(data.get("full_name") or ""), role=str(data.get("role") or ""),
                           position=str(data.get("position") or ""), details=data))
    return result


def read_snapshot(report_id, conn=None):
    if conn is None:
        with closing(db.connect()) as connection:
            return read_snapshot(report_id, connection)
    row = conn.execute("SELECT snapshot_json FROM report_signing WHERE report_id=?", (report_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def report_context(report):
    return {key: report[key] for key in ("company_id", "house_id", "year", "period_start", "period_end")}


def save_selection(report_id, signing_date, selected_ids, *, expected_context=None):
    day = iso_date(signing_date)
    if not day:
        raise ValueError("Укажите корректную дату подписания.")
    ids = list(dict.fromkeys(selected_ids))
    if not ids:
        raise ValueError("Выберите хотя бы одного подписанта.")
    with closing(db.connect()) as conn, conn:
        conn.execute(db.BEGIN_IMMEDIATE_SQL)
        report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            raise ValueError("Отчет не найден.")
        context = report_context(report)
        if expected_context is not None and context != expected_context:
            raise ValueError("Отчет изменился. Откройте выбор подписантов повторно.")
        available = {person["id"]: person for person in available_people(conn, report, day)}
        if any(ident not in available for ident in ids):
            raise ValueError("Подписант не относится к отчету или не уполномочен на указанную дату.")
        snapshot = dict(date=day, context=context, people=[available[ident] for ident in ids])
        conn.execute("""INSERT INTO report_signing(report_id,snapshot_json) VALUES(?,?)
            ON CONFLICT(report_id) DO UPDATE SET snapshot_json=excluded.snapshot_json
            WHERE snapshot_json<>excluded.snapshot_json""",
            (report_id, json.dumps(snapshot, ensure_ascii=False, sort_keys=True)))
        return snapshot


def clear_selection(report_id):
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM report_signing WHERE report_id=?", (report_id,))
