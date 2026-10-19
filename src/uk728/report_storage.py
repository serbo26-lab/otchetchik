"""Preserve report-row identities and provenance without database migration."""
from __future__ import annotations

COLUMNS = {
    "report_content_items": ("work_item_id", "name", "unit", "unit_price", "plan_qty", "plan_cost", "actual_qty", "actual_cost", "position"),
    "report_repair_items": ("work_item_id", "name", "basis", "cost", "volume", "act_details", "position"),
    "report_management_items": ("work_item_id", "name", "unit", "unit_price", "qty", "cost", "position"),
}

def same_business_data(old, fields, keys):
    for key in keys:
        if key == "position":
            continue
        a, b = old[key], fields[key]
        if isinstance(a, (int, float)):
            try:
                if abs(float(a or 0) - float(b or 0)) > 0.000001:
                    return False
                continue
            except (TypeError, ValueError):
                return False
        if (a if a is not None else "") != (b if b is not None else ""):
            return False
    return True

def sync_report_rows(conn, table, report_id, rows):
    """Update existing rows by ID, mark only truly changed rows as manual."""
    if table not in COLUMNS:
        raise ValueError("Unknown report section")
    keys = COLUMNS[table]
    existing = {int(r["id"]): r for r in conn.execute(
        f"SELECT * FROM {table} WHERE report_id=?", (report_id,)).fetchall()}
    kept, inserted = set(), []
    for row_index, payload in enumerate(rows):
        if not str(payload.get("name") or "").strip():
            continue
        values = {key: payload.get(key) for key in keys}
        values["position"] = row_index + 1
        row_id = int(payload.get("id") or 0)
        if row_id:
            if row_id not in existing:
                raise ValueError(f"Row {row_id} does not belong to report {report_id}")
            old = existing[row_id]
            changed = not same_business_data(old, values, keys)
            if changed or old["position"] != values["position"]:
                clause = ", ".join(f"{key}=?" for key in keys)
                conn.execute(
                    f"UPDATE {table} SET {clause}, is_manual=? WHERE id=? AND report_id=?",
                    (*[values[k] for k in keys], 1 if changed else int(old["is_manual"] or 0), row_id, report_id)
                )
            kept.add(row_id)
        else:
            source = str(payload.get("source") or "ручной ввод/редактирование")
            manual = int(payload.get("is_manual", 1))
            fields = ("report_id", *keys, "source", "is_manual")
            cur = conn.execute(
                f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                (report_id, *[values[k] for k in keys], source, manual)
            )
            inserted.append((row_index, int(cur.lastrowid)))
    for removed in existing.keys() - kept:
        conn.execute(f"DELETE FROM {table} WHERE id=? AND report_id=?", (removed, report_id))
    return inserted
