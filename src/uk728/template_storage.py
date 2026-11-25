"""Persist a template and its composition without replacing unchanged rows."""
from __future__ import annotations

ITEM_FIELDS = ("work_item_id", "name", "unit", "unit_price", "plan_qty", "position")


def read_template_snapshot(conn, template_id):
    """Read the header and ordered composition in the caller's transaction."""
    header = conn.execute(
        "SELECT * FROM templates WHERE id=?", (template_id,)
    ).fetchone()
    if header is None:
        return None
    items = [
        dict(row) for row in conn.execute(
            "SELECT * FROM template_items WHERE template_id=? ORDER BY position,id",
            (template_id,),
        )
    ]
    return dict(header), items


def _prepare_rows(rows, existing):
    kept = set()
    prepared = []
    for index, payload in enumerate(rows):
        row_id = int(payload.get("id") or 0)
        if not str(payload.get("name") or "").strip():
            raise ValueError(
                f"Шаблон, строка {index + 1}: заполните наименование "
                "или явно удалите строку."
            )
        if row_id:
            if row_id not in existing or row_id in kept:
                raise ValueError("Состав шаблона изменился. Откройте шаблон заново.")
            kept.add(row_id)
        fields = {key: payload[key] for key in ITEM_FIELDS if key != "position"}
        fields["position"] = index + 1
        prepared.append((index, row_id, fields))
    return kept, prepared


def _persist_rows(conn, template_id, existing, kept, prepared):
    changed = False
    inserted = []
    for index, row_id, fields in prepared:
        values = tuple(fields[key] for key in ITEM_FIELDS)
        if row_id:
            old = existing[row_id]
            if any(old[key] != fields[key] for key in ITEM_FIELDS):
                conn.execute(
                    "UPDATE template_items SET work_item_id=?,name=?,unit=?,"
                    "unit_price=?,plan_qty=?,position=? WHERE id=? AND template_id=?",
                    (*values, row_id, template_id),
                )
                changed = True
        else:
            cur = conn.execute(
                "INSERT INTO template_items(template_id,work_item_id,name,unit,"
                "unit_price,plan_qty,position) VALUES(?,?,?,?,?,?,?)",
                (template_id, *values),
            )
            inserted.append((index, int(cur.lastrowid)))
            changed = True
    for row_id in existing.keys() - kept:
        conn.execute("DELETE FROM template_items WHERE id=? AND template_id=?",
                     (row_id, template_id))
        changed = True
    return changed, inserted


def save_template(conn, template_id, name, category, notes, rows):
    """Use the caller's transaction; return newly allocated row IDs.

    Existing rows retain their IDs. A missing row means explicit removal from
    the editor, not a cleared title. Never accept foreign or repeated row IDs.
    """
    rows = list(rows)
    current = conn.execute(
        "SELECT name,category,notes FROM templates WHERE id=?", (template_id,)
    ).fetchone()
    if current is None:
        raise ValueError("Шаблон больше не существует.")
    existing = {
        int(row["id"]): row for row in conn.execute(
            "SELECT * FROM template_items WHERE template_id=?", (template_id,)
        )
    }
    kept, prepared = _prepare_rows(rows, existing)

    changed, inserted = _persist_rows(conn, template_id, existing, kept, prepared)
    if changed or tuple(current) != (name, category, notes):
        conn.execute(
            "UPDATE templates SET name=?,category=?,notes=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, category, notes, template_id),
        )
    return inserted
