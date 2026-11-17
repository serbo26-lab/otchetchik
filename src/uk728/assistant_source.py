"""Read-only access to the independent GIS JKH Assistant SQLite database.

The Assistant database is opened via SQLite URI mode=ro and query_only.
No Repository instance, Assistant migrations, CREATE, ATTACH, VACUUM or
writes are executed against the source. Linked UUIDs and report-specific
details are stored exclusively in our own reporting_728.db.

The source can use WAL while Assistant runs. Never use immutable=1, which
can silently miss committed WAL transactions.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3

from . import db
from .paths import db_path

_CONFIG_KEY = "assistant_database_path"
PERSON_FIELDS = (
    "full_name", "name_genitive", "position", "position_genitive", "role",
    "premises", "gender", "basis_kind", "basis_number", "basis_date",
    "basis_text", "valid_from", "valid_to",
)


@dataclass(frozen=True)
class AssistantSnapshot:
    organizations: tuple[dict, ...]
    houses: tuple[dict, ...]
    missing_area: int
    people: tuple[dict, ...] = ()


@dataclass(frozen=True)
class SyncSummary:
    organizations: int
    houses: int
    new_organizations: int
    new_houses: int
    missing_area: int
    path: Path
    signatories: int = 0


class AssistantSourceError(ValueError):
    """A missing, incompatible, or inaccessible read-only source."""


def default_source_path() -> Path:
    """Do not import Assistant.paths: its user_data_dir creates/moves folders."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or
                    (Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or
                    (Path.home() / ".local" / "share"))
    regular = root / "GIS JKH Assistant" / "data.db"
    if regular.is_file():
        return regular
    legacy = root / "GIS-JKH-Assistant" / "data.db"
    return legacy if legacy.is_file() else regular


def configured_source_path() -> Path:
    record = db.row("SELECT value FROM app_meta WHERE key=?", (_CONFIG_KEY,))
    value = str(record["value"]).strip() if record else ""
    return Path(value) if value else default_source_path()


def _required_columns(conn: sqlite3.Connection, table: str, fields: set[str]) -> set[str]:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not table_exists:
        raise AssistantSourceError(
            f"В выбранной базе нет таблицы «{table}». Это не база ГИС ЖКХ Ассистента."
        )
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    missing = fields - columns
    if missing:
        raise AssistantSourceError(
            f"В таблице «{table}» не хватает полей: {', '.join(sorted(missing))}."
        )
    return columns


def _decimal(value: object, *, house_id: str) -> float | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise AssistantSourceError(
            f"В базе Ассистента у дома {house_id} некорректная площадь: {value!r}."
        ) from None
    if not number.is_finite() or number < 0:
        raise AssistantSourceError(
            f"В базе Ассистента у дома {house_id} некорректная площадь: {value!r}."
        )
    return float(number)


def read_source(source: Path | str) -> AssistantSnapshot:
    """All source queries complete before any local data is modified."""
    path = Path(source).expanduser().absolute()
    if not path.is_file():
        raise AssistantSourceError(f"База Ассистента не найдена: {path}")
    if path.resolve() == db_path().resolve():
        raise AssistantSourceError(
            "Выбрана собственная база Отчётчика. Нужен файл data.db Ассистента."
        )
    try:
        # .as_uri() escapes spaces and special characters, including # and ?.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro",
                                     uri=True, timeout=8.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA busy_timeout = 8000")
            # A single read transaction gives organizations/houses one
            # consistent WAL snapshot while Assistant is editing records.
            conn.execute("BEGIN")
            _required_columns(conn, "management_organizations",
                              {"id", "name", "ogrn"})
            cols = _required_columns(conn, "houses",
                                     {"id", "organization_id", "address"})
            organizations = tuple(
                dict(row) for row in conn.execute(
                    "SELECT * FROM management_organizations ORDER BY name COLLATE NOCASE"
                )
            )
            house_rows = tuple(
                dict(row) for row in conn.execute(
                    "SELECT * FROM houses ORDER BY address COLLATE NOCASE"
                )
            )
            has_people = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='act_people'"
            ).fetchone()
            if has_people:
                _required_columns(conn, "act_people", {
                    "id", "organization_id", "house_id", "data_json"
                })
                # Explicit projection excludes signature image BLOBs.
                people_rows = tuple(dict(row) for row in conn.execute(
                    "SELECT id,organization_id,house_id,data_json "
                    "FROM act_people ORDER BY rowid"
                ))
            else:
                people_rows = ()
    except sqlite3.DatabaseError as exc:
        raise AssistantSourceError(
            f"Не удалось прочитать SQLite Ассистента: {exc}"
        ) from exc

    org_ids: set[str] = set()
    for org in organizations:
        key = str(org["id"] or "").strip()
        if not key or key in org_ids:
            raise AssistantSourceError("В базе Ассистента обнаружен повторный или пустой ID УО.")
        org_ids.add(key)
    house_ids: set[str] = set()
    houses: list[dict] = []
    missing_area = 0
    for house in house_rows:
        key = str(house["id"] or "").strip()
        owner = str(house["organization_id"] or "").strip()
        if not key or key in house_ids or owner not in org_ids:
            raise AssistantSourceError(
                "У дома в базе Ассистента отсутствует корректный ID или связь с УО."
            )
        house_ids.add(key)
        # The 728/pr form requires residential+non-residential premises area.
        # charge_area is a tariff denominator and must NOT substitute for it.
        a = _decimal(house.get("residential_area"), house_id=key)
        b = _decimal(house.get("non_residential_area"), house_id=key)
        if a is None or b is None:
            area = None
            missing_area += 1
        else:
            area = float(Decimal(str(a)) + Decimal(str(b)))
        houses.append({
            "id": key,
            "organization_id": owner,
            "address": str(house["address"] or "").strip(),
            "total_area": area,
            "residential_area": a,
            "non_residential_area": b,
            "management_start": str(house.get("management_start_date") or ""),
            "management_end": str(house.get("management_end_date") or ""),
            "management_agreement_number": str(house.get("management_agreement_number") or ""),
            "management_agreement_date": str(house.get("management_agreement_date") or ""),
        })
    people: list[dict] = []
    seen: set[str] = set()
    for person in people_rows:
        key = str(person["id"] or "").strip()
        owner_org = str(person["organization_id"] or "").strip()
        owner_house = str(person["house_id"] or "").strip()
        if not key or key in seen or (bool(owner_org) == bool(owner_house)):
            raise AssistantSourceError("Подписант с повторным ID или без корректного владельца.")
        if (owner_org and owner_org not in org_ids) or (owner_house and owner_house not in house_ids):
            raise AssistantSourceError("Подписант ссылается на отсутствующую УО или дом.")
        seen.add(key)
        try:
            value = json.loads(person["data_json"])
        except (TypeError, ValueError) as exc:
            raise AssistantSourceError(f"Ошибочные реквизиты подписанта {key}: {exc}") from exc
        if not isinstance(value, dict) or not str(value.get("full_name") or "").strip():
            raise AssistantSourceError(f"Подписант {key} не содержит ФИО.")
        people.append({
            "id": key, "organization_id": owner_org, "house_id": owner_house,
            "data": {field: str(value.get(field) or "").strip() for field in PERSON_FIELDS},
        })
    return AssistantSnapshot(organizations, tuple(houses), missing_area, tuple(people))


def synchronize(source: Path | str | None = None, *, save_path: bool = False) -> SyncSummary:
    """Read source; atomically upsert only into reporting_728.db.

    Keep source-linked rows even if they disappear from the Assistant later:
    historical reports must never be orphaned by a refresh.
    """
    path = Path(source) if source is not None else configured_source_path()
    snapshot = read_source(path)
    path = path.expanduser().absolute()
    new_companies = 0
    new_houses = 0
    with db.connect() as conn:
        # No write to the reporting DB until the entire Assistant source
        # snapshot has been read and its identifiers validated.
        conn.execute("BEGIN IMMEDIATE")
        mapping = {}
        for org in snapshot.organizations:
            src_id = str(org["id"])
            current = conn.execute(
                "SELECT id FROM companies WHERE assistant_source_id=?", (src_id,)
            ).fetchone()
            if current is None:
                cursor = conn.execute(
                    "INSERT INTO companies(assistant_source_id,name,ogrn_inn) VALUES(?,?,?)",
                    (src_id, str(org["name"]), str(org["ogrn"] or "")),
                )
                cid = int(cursor.lastrowid)
                new_companies += 1
            else:
                cid = int(current["id"])
                conn.execute(
                    "UPDATE companies SET name=?,ogrn_inn=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (str(org["name"]), str(org["ogrn"] or ""), cid),
                )
            mapping[src_id] = cid

        for house in snapshot.houses:
            src_id = str(house["id"])
            owner = mapping[str(house["organization_id"])]
            existing = conn.execute(
                "SELECT id,company_id FROM houses WHERE assistant_source_id=?",
                (src_id,),
            ).fetchone()
            if existing is None:
                fields = (
                    owner, src_id, house["address"], house["total_area"] or 0,
                    house["management_start"], house["management_end"],
                    int(house["total_area"] is None),
                    house["residential_area"], house["non_residential_area"],
                    int(house["residential_area"] is None),
                    int(house["non_residential_area"] is None),
                    house["management_agreement_number"],
                    house["management_agreement_date"],
                )
                conn.execute(
                    "INSERT INTO houses(company_id,assistant_source_id,address,total_area,"
                    "management_start,management_end,assistant_area_missing,"
                    "residential_area,non_residential_area,"
                    "assistant_residential_missing,assistant_non_residential_missing,"
                    "management_agreement_number,management_agreement_date) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", fields,
                )
                new_houses += 1
            else:
                hid = int(existing["id"])
                if existing["company_id"] != owner:
                    used = conn.execute(
                        "SELECT 1 FROM reports WHERE house_id=? LIMIT 1", (hid,)
                    ).fetchone()
                    if used:
                        raise AssistantSourceError(
                            f"Дом «{house['address']}» в Ассистенте перенесён к другой УО. "
                            "В Отчётчике уже есть отчёты по прежней УО; автоматическая "
                            "перепривязка запрещена. Данные не изменены."
                        )
                conn.execute(
                    "UPDATE houses SET company_id=?,address=?,"
                    "residential_area=COALESCE(?,residential_area),"
                    "non_residential_area=COALESCE(?,non_residential_area),"
                    "assistant_residential_missing=?,assistant_non_residential_missing=?,"
                    "total_area=COALESCE(?,total_area),assistant_area_missing=?,"
                    "management_start=?,management_end=?,"
                    "management_agreement_number=?,management_agreement_date=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (
                        owner, house["address"], house["residential_area"],
                        house["non_residential_area"],
                        int(house["residential_area"] is None),
                        int(house["non_residential_area"] is None),
                        house["total_area"], int(house["total_area"] is None),
                        house["management_start"], house["management_end"],
                        house["management_agreement_number"],
                        house["management_agreement_date"], hid,
                    ),
                )
        # People are stored by the source UUID, never by matching their names.
        for person in snapshot.people:
            src_id = person["id"]
            cid = mapping.get(person["organization_id"])
            owner = conn.execute(
                "SELECT id FROM houses WHERE assistant_source_id=?",
                (person["house_id"],),
            ).fetchone() if person["house_id"] else None
            hid = int(owner["id"]) if owner else None
            if (cid is None) == (hid is None):
                raise AssistantSourceError("Не удалось сопоставить подписанта с владельцем.")
            old = conn.execute(
                "SELECT id,company_id,house_id FROM signatories "
                "WHERE assistant_source_id=?", (src_id,),
            ).fetchone()
            if old and (old["company_id"] != cid or old["house_id"] != hid):
                raise AssistantSourceError(
                    "В Ассистенте изменена принадлежность подписанта. "
                    "Автоматический перенос истории запрещён."
                )
            data = json.dumps(person["data"], ensure_ascii=False)
            if old:
                conn.execute(
                    "UPDATE signatories SET data_json=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (data, old["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO signatories(company_id,house_id,"
                    "assistant_source_id,data_json) VALUES(?,?,?,?)",
                    (cid, hid, src_id, data),
                )
        if save_path:
            conn.execute(
                "INSERT INTO app_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_CONFIG_KEY, str(path)),
            )
        conn.commit()
    return SyncSummary(
        len(snapshot.organizations), len(snapshot.houses),
        new_companies, new_houses, snapshot.missing_area, path,
        len(snapshot.people),
    )
