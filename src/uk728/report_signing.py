"""Explicit selection of report signatories, with a local historical snapshot."""
from __future__ import annotations

import base64
import json
from contextlib import closing

from . import db, signing_store
from .utils import iso_date, parse_date

PRIVACY_MODES = {
    "full": "Полные реквизиты",
    "house_mark": "Скрыть подписи представителей МКД, сохранить ФИО",
    "house_identity": "Скрыть ФИО, сведения и подписи представителей МКД",
    "all_identity": "Скрыть персональные реквизиты и изображения всех подписантов",
}
HIDDEN = "Данные скрыты в целях безопасности персональных данных"
SIGNATURE_LINE = "________________"


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


def _encoded_image(record):
    if not record.get("content"):
        return None
    return {"content": base64.b64encode(record["content"]).decode("ascii"),
            "settings": record["settings"]}


def _hidden_identity(side, privacy):
    return privacy == "all_identity" or (side == "house" and privacy == "house_identity")


def _hidden_mark(side, privacy):
    return _hidden_identity(side, privacy) or (side == "house" and privacy == "house_mark")


def _capture_images(conn, snapshot, company_id):
    if not snapshot["insert_images"]:
        return
    privacy = snapshot["privacy"]
    for person in snapshot["people"]:
        if not _hidden_mark(person["side"], privacy):
            person["image"] = _encoded_image(signing_store.read_signature(person["id"], conn))
    if privacy != "all_identity" and any(p["side"] == "company" for p in snapshot["people"]):
        options = signing_store.read_options(company_id, conn)
        snapshot["stamp"] = _encoded_image(dict(content=options["stamp"], settings=options["stamp_settings"]))


def public_block(snapshot):
    """Allowlist output fields; hidden personal data and images never enter renderers."""
    if not snapshot:
        return {}
    privacy = snapshot.get("privacy", "full")
    if privacy not in PRIVACY_MODES:
        raise ValueError("Неизвестный режим скрытия реквизитов.")
    people = []
    for person in snapshot["people"]:
        side = person["side"]
        identity_hidden = _hidden_identity(side, privacy)
        mark_hidden = _hidden_mark(side, privacy)
        label = "Представитель УО" if side == "company" else "Представитель собственников МКД"
        people.append(dict(label=label,
                           name=HIDDEN if identity_hidden else person["name"],
                           position="" if identity_hidden else person["position"] or person["role"],
                           mark=HIDDEN if mark_hidden else SIGNATURE_LINE,
                           image=person.get("image") if snapshot.get("insert_images") and not mark_hidden else None))
    return dict(date=snapshot["date"], people=people,
                stamp=snapshot.get("stamp") if snapshot.get("insert_images") and privacy != "all_identity" else None)


def verify_snapshot(bundle):
    snapshot = bundle.get("signing")
    if not snapshot:
        return
    if snapshot.get("context") != report_context(bundle["report"]):
        raise ValueError("Изменился период или объект отчета. Повторно выберите подписантов либо уберите блок.")
    if not parse_date(snapshot.get("date")) or not snapshot.get("people"):
        raise ValueError("Некорректный снимок подписантов. Повторно выберите подписантов.")
    if snapshot.get("privacy", "full") not in PRIVACY_MODES:
        raise ValueError("Неизвестный режим скрытия реквизитов.")


def _require_previous(conn, report_id, expected_snapshot):
    if expected_snapshot is not None and read_snapshot(report_id, conn) != expected_snapshot:
        raise ValueError("Реквизиты подписантов изменились в другом окне. Откройте их повторно.")


def save_selection(report_id, signing_date, selected_ids, *, expected_context=None,
                   expected_snapshot=None, privacy="full", insert_images=False):
    if privacy not in PRIVACY_MODES:
        raise ValueError("Неизвестный режим скрытия реквизитов.")
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
        _require_previous(conn, report_id, expected_snapshot)
        context = report_context(report)
        if expected_context is not None and context != expected_context:
            raise ValueError("Отчет изменился. Откройте выбор подписантов повторно.")
        available = {person["id"]: person for person in available_people(conn, report, day)}
        if any(ident not in available for ident in ids):
            raise ValueError("Подписант не относится к отчету или не уполномочен на указанную дату.")
        snapshot = dict(date=day, context=context, people=[available[ident] for ident in ids],
                        privacy=privacy, insert_images=bool(insert_images))
        _capture_images(conn, snapshot, report["company_id"])
        conn.execute("""INSERT INTO report_signing(report_id,snapshot_json) VALUES(?,?)
            ON CONFLICT(report_id) DO UPDATE SET snapshot_json=excluded.snapshot_json
            WHERE snapshot_json<>excluded.snapshot_json""",
            (report_id, json.dumps(snapshot, ensure_ascii=False, sort_keys=True)))
        return snapshot


def clear_selection(report_id, *, expected_snapshot=None):
    with closing(db.connect()) as conn, conn:
        conn.execute(db.BEGIN_IMMEDIATE_SQL)
        _require_previous(conn, report_id, expected_snapshot)
        conn.execute("DELETE FROM report_signing WHERE report_id=?", (report_id,))
