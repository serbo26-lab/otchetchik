"""Local signature assets; records do not attest that a report was signed."""
from __future__ import annotations

import json
from contextlib import closing

from . import db
from .act_images import normalize_image_settings, prepare_act_image


def image_record(content, name, settings, *, kind):
    """Validate a real image and its selected region before storing original bytes."""
    normalized = normalize_image_settings(settings, kind=kind)
    prepare_act_image(content, normalized, kind=kind)
    return bytes(content), str(name or "Изображение"), json.dumps(normalized, sort_keys=True)


def read_signature(person_id, conn=None):
    if conn is None:
        with closing(db.connect()) as connection:
            return read_signature(person_id, connection)
    row = conn.execute("SELECT content,name,settings FROM signatory_images WHERE person_id=?",
                       (person_id,)).fetchone()
    if not row:
        return {}
    return {"content": row["content"], "name": row["name"], "settings": json.loads(row["settings"])}


def read_options(company_id, conn=None):
    if conn is None:
        with closing(db.connect()) as connection:
            return read_options(company_id, connection)
    row = conn.execute("SELECT * FROM organization_signing WHERE company_id=?", (company_id,)).fetchone()
    if row is None:
        return {"stamp": None, "stamp_name": "", "stamp_settings": {}, "insert_images": 0,
                "customer_signature_placeholder": 0}
    result = dict(row)
    result["stamp_settings"] = json.loads(result["stamp_settings"])
    return result


def write_signature(conn, person_id, record):
    if record is None:
        conn.execute("DELETE FROM signatory_images WHERE person_id=?", (person_id,))
        return
    conn.execute("""INSERT INTO signatory_images(person_id,content,name,settings) VALUES(?,?,?,?)
        ON CONFLICT(person_id) DO UPDATE SET content=excluded.content,name=excluded.name,
        settings=excluded.settings WHERE content<>excluded.content OR name<>excluded.name
        OR settings<>excluded.settings""", (person_id, *record))


def save_signature(person_id, content, name="", settings=None):
    record = image_record(content, name, settings, kind="signature") if content is not None else None
    with closing(db.connect()) as conn, conn:
        owner = conn.execute("SELECT assistant_source_id FROM signatories WHERE id=?", (person_id,)).fetchone()
        if owner is None or owner["assistant_source_id"] is not None:
            raise ValueError("Подпись из Ассистента изменяется только в Ассистенте.")
        write_signature(conn, person_id, record)


def write_options(conn, company_id, options):
    conn.execute("""INSERT INTO organization_signing(company_id,stamp,stamp_name,stamp_settings,
        insert_images,customer_signature_placeholder) VALUES(?,?,?,?,?,?)
        ON CONFLICT(company_id) DO UPDATE SET stamp=excluded.stamp,stamp_name=excluded.stamp_name,
        stamp_settings=excluded.stamp_settings,insert_images=excluded.insert_images,
        customer_signature_placeholder=excluded.customer_signature_placeholder
        WHERE stamp IS NOT excluded.stamp OR stamp_name<>excluded.stamp_name
        OR stamp_settings<>excluded.stamp_settings OR insert_images<>excluded.insert_images
        OR customer_signature_placeholder<>excluded.customer_signature_placeholder""",
        (company_id, options["stamp"], options["stamp_name"],
         json.dumps(options["stamp_settings"], sort_keys=True), int(options["insert_images"]),
         int(options["customer_signature_placeholder"])))


def save_options(company_id, **changes):
    allowed = {"stamp", "stamp_name", "stamp_settings", "insert_images", "customer_signature_placeholder"}
    if set(changes) - allowed:
        raise ValueError("Неизвестная настройка подписания.")
    with closing(db.connect()) as conn, conn:
        owner = conn.execute("SELECT assistant_source_id FROM companies WHERE id=?", (company_id,)).fetchone()
        if owner is None or owner["assistant_source_id"] is not None:
            raise ValueError("Параметры связанной УО изменяются в Ассистенте.")
        options = read_options(company_id, conn)
        options.update(changes)
        if options["stamp"] is not None:
            content, name, settings = image_record(options["stamp"], options["stamp_name"],
                                                     options["stamp_settings"], kind="stamp")
            options.update(stamp=content, stamp_name=name, stamp_settings=json.loads(settings))
        else:
            options.update(stamp_name="", stamp_settings={})
        write_options(conn, company_id, options)
