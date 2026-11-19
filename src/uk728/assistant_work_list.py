"""Safe, selective import of Assistant work-list data into a single 728/pr report.

Only work_lists and work_list_items are read from Assistant. No source writes,
copying of Assistant tables, automatic legal section classification, or
automatic statements about actually performed work.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3

from . import assistant_source, db
from .paths import db_path, make_backup
from .utils import iso_date

SKIP = "skip"
CONTENT = "content"
REPAIR = "repair"
SECTIONS = (SKIP, CONTENT, REPAIR)
SOURCE_PREFIX = "assistant-work-list:"


class WorkListImportError(ValueError):
    """Source, selected report, or preview changed; do not modify local data."""


@dataclass(frozen=True)
class Candidate:
    item_id: str
    code: str
    name: str
    unit: str
    unit_price: Decimal | None
    plan_qty: Decimal | None
    plan_cost: Decimal | None
    problem: str = ""
    already_imported: bool = False


@dataclass(frozen=True)
class Preview:
    report_id: int
    year: int
    house_source_id: str
    source_path: Path
    work_list_id: str
    period: str
    source_digest: str
    report_digest: str
    items: tuple[Candidate, ...]


@dataclass(frozen=True)
class ImportResult:
    added_content: int = 0
    added_repair: int = 0
    skipped: int = 0


def _parse_amount(raw: object, label: str) -> Decimal:
    value = str(raw if raw is not None else "").strip()
    if not value:
        raise WorkListImportError(f"Отсутствует показатель «{label}».")
    value = value.replace("\N{NO-BREAK SPACE}", "").replace(" ", "").replace(",", ".")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise WorkListImportError(f"Некорректное число в графе «{label}».") from None
    if not result.is_finite() or result < 0:
        raise WorkListImportError(f"Некорректное число в графе «{label}».")
    return result


def _report_digest(conn: sqlite3.Connection, report_id: int) -> tuple[str, sqlite3.Row]:
    row = conn.execute(
        "SELECT * FROM reports WHERE id=?", (report_id,)
    ).fetchone()
    if row is None:
        raise WorkListImportError("Выбранный отчёт больше не существует.")
    # Report dates are part of the preview identity: changing the covered
    # period must invalidate the import, even if row contents are unchanged.
    payload = {"report": dict(row)}
    for table in ("report_content_items", "report_repair_items"):
        payload[table] = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM {table} WHERE report_id=? ORDER BY id", (report_id,)
            )
        ]
    signature = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return signature, row


def _source_snapshot(path: Path, house_source_id: str, year: int):
    path = path.expanduser().absolute()
    if not path.is_file():
        raise WorkListImportError("Файл базы Ассистента недоступен.")
    if path.resolve() == db_path().resolve():
        raise WorkListImportError("Источником не может быть база Отчётчика.")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro",
                                     uri=True, timeout=8.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=8000")
            conn.execute("BEGIN")
            for table, required in (
                ("work_lists", {"id", "house_id", "base_year", "start_year",
                                "start_month", "end_year", "end_month", "status"}),
                ("work_list_items", {"id", "work_list_id", "position",
                                     "work_name", "work_code", "price", "volume",
                                     "quantity", "cost", "unit_name"}),
            ):
                found = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not found:
                    raise WorkListImportError("В базе Ассистента нет перечней работ нужного формата.")
                columns = {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")}
                if required - columns:
                    raise WorkListImportError("Формат перечней Ассистента несовместим с Отчётчиком.")
            lists = conn.execute(
                "SELECT id,base_year,start_year,start_month,end_year,end_month,status "
                "FROM work_lists WHERE house_id=? AND base_year=?",
                (house_source_id, year),
            ).fetchall()
            if not lists:
                raise WorkListImportError("Для этого дома нет перечня работ за выбранный год.")
            if len(lists) != 1:
                raise WorkListImportError(
                    "Для дома найдено несколько перечней за год. "
                    "Автоматический выбор небезопасен."
                )
            work_list = dict(lists[0])
            if (
                work_list["start_year"] != year
                or work_list["end_year"] != year
                or work_list["start_month"] != 1
                or work_list["end_month"] != 12
            ):
                raise WorkListImportError(
                    "Перечень Ассистента охватывает неполный год. "
                    "Нельзя автоматически переносить его в годовой отчёт."
                )
            records = [
                dict(x) for x in conn.execute(
                    "SELECT id,work_code,work_name,unit_name,price,volume,"
                    "quantity,cost,position FROM work_list_items "
                    "WHERE work_list_id=? ORDER BY position,id", (work_list["id"],)
                )
            ]
    except sqlite3.DatabaseError as exc:
        raise WorkListImportError(
            "Не удалось прочитать перечень из базы Ассистента."
        ) from exc
    if not records:
        raise WorkListImportError("Перечень работ за выбранный год пуст.")
    raw = json.dumps(
        {"list": work_list, "items": records}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    period = (
        f'{work_list["start_month"]:02d}.{work_list["start_year"]}'
        f'–{work_list["end_month"]:02d}.{work_list["end_year"]}'
    )
    return work_list["id"], period, digest, records


def _candidate(item: dict, already: bool) -> Candidate:
    name = str(item.get("work_name") or "").strip()
    unit = str(item.get("unit_name") or "").strip()
    problem = ""
    price = qty = cost = None
    try:
        if not name:
            raise WorkListImportError("Не указано наименование работы.")
        price = _parse_amount(item.get("price"), "Цена")
        volume = _parse_amount(item.get("volume"), "Объём")
        count = _parse_amount(item.get("quantity"), "Количество")
        cost = _parse_amount(item.get("cost"), "Стоимость")
        qty = volume * count
        # In Assistant cost = price * volume * quantity. The official
        # content form has just price * amount, so multiply its two factors.
        if abs(price * qty - cost) > Decimal("0.02"):
            raise WorkListImportError("Стоимость не совпадает с ценой × объёмом × количеством.")
    except WorkListImportError as exc:
        problem = str(exc)
    return Candidate(
        item_id=str(item["id"]), code=str(item.get("work_code") or ""),
        name=name, unit=unit, unit_price=price,
        plan_qty=qty, plan_cost=cost, problem=problem, already_imported=already,
    )


def read_preview(report_id: int) -> Preview:
    with closing(db.connect()) as conn:
        fingerprint, report = _report_digest(conn, report_id)
        house = conn.execute(
            "SELECT assistant_source_id FROM houses WHERE id=?", (report["house_id"],)
        ).fetchone()
        linked_id = str(house["assistant_source_id"] or "") if house else ""
        if not linked_id:
            raise WorkListImportError(
                "Дом не связан с ГИС ЖКХ Ассистентом. Выберите связанный дом."
            )
        year = int(report["year"])
        if str(report["status"] or "") in ("Размещен", "Архив"):
            raise WorkListImportError(
                "Перечень нельзя импортировать в размещённый или архивный отчёт. "
                "Используйте черновик для изменения данных."
            )
        if (
            iso_date(report["period_start"]) != f"{year}-01-01"
            or iso_date(report["period_end"]) != f"{year}-12-31"
        ):
            raise WorkListImportError(
                "Годовой перечень нельзя применять к отчёту за неполный период. "
                "Выберите отчёт за полный календарный год."
            )
        existing = set()
        for table in ("report_content_items", "report_repair_items"):
            existing.update(
                str(r[0]) for r in conn.execute(
                    f"SELECT source FROM {table} WHERE report_id=? "
                    "AND source LIKE 'assistant-work-list:%'", (report_id,),
                )
            )
    path = assistant_source.configured_source_path()
    list_id, period, digest, items = _source_snapshot(path, linked_id, int(report["year"]))
    candidates = tuple(
        _candidate(item, SOURCE_PREFIX + str(item["id"]) in existing)
        for item in items
    )
    return Preview(
        report_id=report_id, year=int(report["year"]),
        house_source_id=linked_id, source_path=path,
        work_list_id=str(list_id), period=period,
        source_digest=digest, report_digest=fingerprint, items=candidates,
    )


def apply_selected(preview: Preview, selections: dict[str, tuple[str, str]]) -> ImportResult:
    """Atomically add explicitly selected rows; never overwrite a report row.

    Selections map Assistant item UUID -> (skip/content/repair, unit name).
    A repair selection creates an incomplete DRAFT: no performed work figures
    or signed act are fabricated. The report review rejects unfinished drafts.
    """
    candidates = {c.item_id: c for c in preview.items}
    if set(selections) - set(candidates):
        raise WorkListImportError("В выборе обнаружены посторонние позиции.")
    requested = []
    for item_id, pair in selections.items():
        section, unit = pair
        if section not in SECTIONS:
            raise WorkListImportError("Неверный раздел отчёта.")
        if section == SKIP:
            continue
        candidate = candidates[item_id]
        if candidate.problem:
            raise WorkListImportError(
                "В выбранной строке перечня ошибочные числовые показатели."
            )
        if section == CONTENT and not str(unit or "").strip():
            raise WorkListImportError(
                "Укажите единицу измерения для каждой выбранной работы содержания."
            )
        requested.append((candidate, section, str(unit).strip()))
    if not requested:
        return ImportResult()

    # A source may change while the user is reviewing the preview.
    list_id, _, digest, _ = _source_snapshot(
        preview.source_path, preview.house_source_id, preview.year
    )
    if str(list_id) != preview.work_list_id or digest != preview.source_digest:
        raise WorkListImportError(
            "Перечень в Ассистенте изменён после предварительного просмотра. "
            "Откройте импорт заново."
        )
    with closing(db.connect()) as conn:
        before, report = _report_digest(conn, preview.report_id)
        house = conn.execute(
            "SELECT assistant_source_id FROM houses WHERE id=?", (report["house_id"],)
        ).fetchone()
        if (before != preview.report_digest or int(report["year"]) != preview.year
                or str(report["status"] or "") in ("Размещен", "Архив")
                or not house or house["assistant_source_id"] != preview.house_source_id):
            raise WorkListImportError(
                "Отчёт изменился после предварительного просмотра. "
                "Откройте импорт заново."
            )
        current = {}
        for section, table in (
            (CONTENT, "report_content_items"), (REPAIR, "report_repair_items")
        ):
            rows = conn.execute(
                f"SELECT source,name FROM {table} WHERE report_id=?", (preview.report_id,)
            ).fetchall()
            current[section] = (
                {str(r["source"]) for r in rows},
                {str(r["name"] or "").strip().casefold() for r in rows},
            )
        existing_sources = current[CONTENT][0] | current[REPAIR][0]
        selected = []
        skipped = 0
        for candidate, section, unit in requested:
            marker = SOURCE_PREFIX + candidate.item_id
            names = current[section][1]
            if (marker in existing_sources or candidate.name.casefold() in names):
                skipped += 1
                continue
            selected.append((candidate, section, unit, marker))
            existing_sources.add(marker)
            names.add(candidate.name.casefold())
    if not selected:
        return ImportResult(skipped=skipped)

    # A protective SQLite-online snapshot of the Reporter only, taken before
    # the first actual write. Never copy or back up the Assistant source.
    if make_backup("before_work_list_import") is None:
        raise WorkListImportError("Невозможно создать защитную копию базы Отчётчика.")

    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        fresh, report = _report_digest(conn, preview.report_id)
        if fresh != preview.report_digest:
            raise WorkListImportError(
                "Отчёт изменился перед сохранением. Импорт отменён."
            )
        positions = {}
        for section, table in (
            (CONTENT, "report_content_items"), (REPAIR, "report_repair_items")
        ):
            positions[section] = int(conn.execute(
                f"SELECT COALESCE(MAX(position),0) FROM {table} WHERE report_id=?",
                (preview.report_id,),
            ).fetchone()[0])
        added_content = added_repair = 0
        for candidate, section, unit, marker in selected:
            positions[section] += 1
            if section == CONTENT:
                conn.execute(
                    "INSERT INTO report_content_items("
                    "report_id,name,unit,unit_price,plan_qty,plan_cost,"
                    "actual_qty,actual_cost,source,is_manual,position)"
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        preview.report_id, candidate.name, unit,
                        float(candidate.unit_price), float(candidate.plan_qty),
                        float(candidate.plan_cost), 0, 0, marker, 0,
                        positions[section],
                    ),
                )
                added_content += 1
            else:
                conn.execute(
                    "INSERT INTO report_repair_items("
                    "report_id,name,basis,cost,volume,act_details,source,is_manual,position)"
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        preview.report_id, candidate.name, "", 0, "", "",
                        marker, 0, positions[section],
                    ),
                )
                added_repair += 1
        # Any newly imported information invalidates a previously checked
        # report. Never retain "Проверен" for data not yet reviewed.
        conn.execute(
            "UPDATE reports SET status='Черновик',updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='Проверен'", (preview.report_id,)
        )
        conn.commit()
    return ImportResult(added_content, added_repair, skipped)
