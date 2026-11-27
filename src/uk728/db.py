from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .paths import db_path, make_backup
from .utils import full_year_period

SCHEMA_VERSION = 13
BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"

TEXT_DEFAULT_EMPTY = "TEXT NOT NULL DEFAULT ''"
INTEGER_DEFAULT_ZERO = "INTEGER NOT NULL DEFAULT 0"
REPAIR_CATEGORY = "Текущий ремонт"
ROOF_REPAIR_NAME = "Ремонт кровельного покрытия"
SANITARY_GROUP = "Санитарное содержание"
CONTENT_CATEGORY = "Содержание"
DERATIZATION_NAME = "Дератизация"
DISINSECTION_NAME = "Дезинсекция"
MANAGEMENT_CATEGORY = "Управление"
MANAGEMENT_WORK_NAME = "Услуги управления МКД"


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def read_schema_version(conn: sqlite3.Connection) -> int | None:
    """Read the version without changing a database, including legacy copies."""
    meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_meta'"
    ).fetchone()
    if meta is None:
        return None
    row = conn.execute(
        "SELECT value FROM app_meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        return None
    try:
        version = int(row[0])
    except (TypeError, ValueError):
        raise ValueError("Неверный номер версии структуры базы.") from None
    if version < 0:
        raise ValueError("Неверный номер версии структуры базы.")
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"Версия структуры базы ({version}) новее поддерживаемой "
            f"программой ({SCHEMA_VERSION}). Установите более новую версию."
        )
    return version


def init_db() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Back up the existing Reporter database BEFORE any versioned ALTER/DDL.
    # The Assistant source is never involved. This also works with Reporter WAL.
    if path.is_file() and path.stat().st_size > 0:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=15.0)) as old_conn:
            old_conn.execute("PRAGMA query_only=ON")
            previous_version = read_schema_version(old_conn)
        if previous_version != SCHEMA_VERSION:
            if make_backup(f"before_schema_v{SCHEMA_VERSION}") is None:
                raise RuntimeError("Не удалось создать копию перед обновлением структуры базы.")
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                ogrn_inn TEXT NOT NULL DEFAULT '',
                reception_address TEXT NOT NULL DEFAULT '',
                report_person TEXT NOT NULL DEFAULT '',
                report_contact TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS houses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
                address TEXT NOT NULL DEFAULT '',
                total_area REAL NOT NULL DEFAULT 0,
                management_start TEXT NOT NULL DEFAULT '',
                management_end TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'В управлении',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS work_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER REFERENCES work_categories(id) ON DELETE CASCADE,
                name TEXT NOT NULL DEFAULT '',
                section TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT 'усл.',
                default_price REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS work_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL DEFAULT '',
                subgroup_name TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT 'усл.',
                default_price REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'Содержание',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS template_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
                work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
                name TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT 'усл.',
                unit_price REAL NOT NULL DEFAULT 0,
                plan_qty REAL NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
                house_id INTEGER REFERENCES houses(id) ON DELETE SET NULL,
                year INTEGER NOT NULL,
                period_start TEXT NOT NULL DEFAULT '',
                period_end TEXT NOT NULL DEFAULT '',
                show_period_note INTEGER NOT NULL DEFAULT 0,
                publish_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Черновик',
                management_cost REAL NOT NULL DEFAULT 0,
                repair_start_balance REAL NOT NULL DEFAULT 0,
                repair_accrual REAL NOT NULL DEFAULT 0,
                repair_work_cost REAL NOT NULL DEFAULT 0,
                repair_end_balance REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(house_id, year, period_start, period_end)
            );

            CREATE TABLE IF NOT EXISTS report_content_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
                name TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT 'усл.',
                unit_price REAL NOT NULL DEFAULT 0,
                plan_qty REAL NOT NULL DEFAULT 0,
                plan_cost REAL NOT NULL DEFAULT 0,
                actual_qty REAL NOT NULL DEFAULT 0,
                actual_cost REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'ручной ввод',
                is_manual INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS report_repair_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
                name TEXT NOT NULL DEFAULT '',
                basis TEXT NOT NULL DEFAULT '',
                cost REAL NOT NULL DEFAULT 0,
                volume TEXT NOT NULL DEFAULT '',
                act_details TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'ручной ввод',
                is_manual INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS report_management_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
                name TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT 'усл.',
                unit_price REAL NOT NULL DEFAULT 0,
                qty REAL NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'ручной ввод',
                is_manual INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS report_financials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                payer_type TEXT NOT NULL DEFAULT 'Собственники',
                debt_start REAL NOT NULL DEFAULT 0,
                accrued REAL NOT NULL DEFAULT 0,
                paid REAL NOT NULL DEFAULT 0,
                debt_end REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE(report_id, payer_type)
            );

            CREATE TABLE IF NOT EXISTS report_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE UNIQUE,
                pretensions_count INTEGER NOT NULL DEFAULT 0,
                lawsuits_count INTEGER NOT NULL DEFAULT 0,
                recovered_amount REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT ''
            );
            """
        )
        # Миграции для существующих тестовых баз: старые лишние колонки не используются,
        # но оставляются в базе, чтобы не рисковать пользовательскими данными.
        _ensure_column(conn, "work_items", "group_name", TEXT_DEFAULT_EMPTY)
        _ensure_column(conn, "work_items", "subgroup_name", TEXT_DEFAULT_EMPTY)
        # Stable source UUIDs from GIS JKH Assistant. The original SQLite
        # remains entirely separate and is never attached as a writable DB.
        _ensure_column(conn, "companies", "assistant_source_id", "TEXT")
        _ensure_column(conn, "houses", "assistant_source_id", "TEXT")
        _ensure_column(conn, "houses", "assistant_area_missing", INTEGER_DEFAULT_ZERO)
        # Nullable components distinguish unknown area from a genuine zero.
        # Preserve total_area in old user databases; never infer component
        # areas by splitting or copying the old total.
        for column, declaration in (
            ("residential_area", "REAL"),
            ("non_residential_area", "REAL"),
            ("assistant_residential_missing", INTEGER_DEFAULT_ZERO),
            ("assistant_non_residential_missing", INTEGER_DEFAULT_ZERO),
            ("management_agreement_number", TEXT_DEFAULT_EMPTY),
            ("management_agreement_date", TEXT_DEFAULT_EMPTY),
            ("agreement_valid_from", TEXT_DEFAULT_EMPTY),
            ("agreement_valid_to", TEXT_DEFAULT_EMPTY),
        ):
            _ensure_column(conn, "houses", column, declaration)
        # Signatories belong either to a UO or a house, never both. Imported
        # people have stable source UUIDs; manually entered people have NULL.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signatories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
                house_id INTEGER REFERENCES houses(id) ON DELETE CASCADE,
                assistant_source_id TEXT,
                data_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK ((company_id IS NOT NULL) != (house_id IS NOT NULL))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_signatories_assistant
                ON signatories(assistant_source_id)
                WHERE assistant_source_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS ix_signatories_company ON signatories(company_id);
            CREATE INDEX IF NOT EXISTS ix_signatories_house ON signatories(house_id);
            CREATE TABLE IF NOT EXISTS signatory_images (
                person_id INTEGER PRIMARY KEY REFERENCES signatories(id) ON DELETE CASCADE,
                content BLOB NOT NULL, name TEXT NOT NULL, settings TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS organization_signing (
                company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
                stamp BLOB, stamp_name TEXT NOT NULL DEFAULT '', stamp_settings TEXT NOT NULL DEFAULT '{}',
                insert_images INTEGER NOT NULL DEFAULT 0,
                customer_signature_placeholder INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS report_signing (
                report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
                snapshot_json TEXT NOT NULL
            );
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_assistant_source "
            "ON companies(assistant_source_id) "
            "WHERE assistant_source_id IS NOT NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_houses_assistant_source "
            "ON houses(assistant_source_id) "
            "WHERE assistant_source_id IS NOT NULL"
        )
        for table in ("work_categories", "work_items", "template_items", "report_content_items", "report_management_items"):
            cols = _table_columns(conn, table)
            if "unit" in cols:
                conn.execute(f"UPDATE {table} SET unit='м²' WHERE unit='м2'")
                conn.execute(f"UPDATE {table} SET unit='м³' WHERE unit='м3'")
        # Reopening an existing database must never erase legacy category
        # labels or regenerate sample templates deleted by the user.
        # The UI requires official sections only when the corresponding work
        # is edited or selected for inclusion in the 728/pr report.
        installed = conn.execute(
            "SELECT value FROM app_meta WHERE key='schema_version'"
        ).fetchone()
        existing_records = any(
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
            for table in ("companies", "houses", "work_categories",
                          "work_items", "templates", "reports")
        )
        if installed is None and not existing_records:
            seed_defaults(conn)
        conn.execute(
            "INSERT INTO app_meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
            "WHERE app_meta.value<>excluded.value", (str(SCHEMA_VERSION),)
        )
        conn.commit()


def _insert_category(conn: sqlite3.Connection, name: str, section: str, unit: str, price: float = 0.0, notes: str = "", parent_id: int | None = None) -> int:
    existing = conn.execute(
        "SELECT id FROM work_categories WHERE COALESCE(parent_id,0)=COALESCE(?,0) AND LOWER(TRIM(name))=LOWER(TRIM(?)) LIMIT 1",
        (parent_id, name),
    ).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        "INSERT INTO work_categories(parent_id, name, section, unit, default_price, notes) VALUES(?,?,?,?,?,?)",
        (parent_id, name, section, unit, price, notes),
    )
    return int(cur.lastrowid)


def seed_defaults(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM work_categories").fetchone()[0] == 0:
        roof = _insert_category(conn, "Ремонт крыши", REPAIR_CATEGORY, "работа", 0, "Категория для быстрого выбора работ по кровле")
        _insert_category(conn, ROOF_REPAIR_NAME, REPAIR_CATEGORY, "м²", 0, parent_id=roof)
        _insert_category(conn, "Ремонт козырька балкона", REPAIR_CATEGORY, "работа", 0, parent_id=roof)
        sanit = _insert_category(conn, SANITARY_GROUP, CONTENT_CATEGORY, "усл.", 0)
        _insert_category(conn, DERATIZATION_NAME, CONTENT_CATEGORY, "усл.", 0, parent_id=sanit)
        _insert_category(conn, DISINSECTION_NAME, CONTENT_CATEGORY, "усл.", 0, parent_id=sanit)
        adm = _insert_category(conn, "Управление МКД", MANAGEMENT_CATEGORY, "м²", 0)
        _insert_category(conn, MANAGEMENT_WORK_NAME, MANAGEMENT_CATEGORY, "м²", 0, "", parent_id=adm)

    if conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == 0:
        rows = [
            (SANITARY_GROUP, DERATIZATION_NAME, DERATIZATION_NAME, CONTENT_CATEGORY, "усл.", 0, ""),
            (SANITARY_GROUP, DISINSECTION_NAME, DISINSECTION_NAME, CONTENT_CATEGORY, "усл.", 0, ""),
            ("", "", "Уборка подъездов", CONTENT_CATEGORY, "мес.", 0, ""),
            ("", "", "Аварийно-диспетчерское обслуживание", CONTENT_CATEGORY, "м²", 0, ""),
            ("Управление МКД", MANAGEMENT_WORK_NAME, MANAGEMENT_WORK_NAME, MANAGEMENT_CATEGORY, "м²", 0, ""),
            ("Ремонт крыши", ROOF_REPAIR_NAME, ROOF_REPAIR_NAME, REPAIR_CATEGORY, "м²", 0, ""),
            ("", "", "Ремонт подъезда", REPAIR_CATEGORY, "м²", 0, ""),
        ]
        conn.executemany(
            "INSERT INTO work_items(group_name, subgroup_name, name, category, unit, default_price, notes) VALUES(?,?,?,?,?,?,?)",
            rows,
        )
    if conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0] == 0:
        conn.execute("INSERT INTO templates(name, category, notes) VALUES(?,?,?)", ("Базовый шаблон содержания", CONTENT_CATEGORY, "Можно изменить под вашу организацию"))
        template_id = conn.execute("SELECT id FROM templates WHERE name=?", ("Базовый шаблон содержания",)).fetchone()[0]
        items = conn.execute("SELECT id, name, unit, default_price FROM work_items WHERE category='Содержание' ORDER BY id").fetchall()
        for pos, item in enumerate(items, 1):
            conn.execute(
                "INSERT INTO template_items(template_id, work_item_id, name, unit, unit_price, plan_qty, position) VALUES(?,?,?,?,?,?,?)",
                (template_id, item["id"], item["name"], item["unit"], item["default_price"], 0, pos),
            )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def named_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Return explicit name-keyed records for UI consumers.

    Keep rows() and its positional SQLite Row access unchanged for other callers.
    """
    return [dict(record) for record in rows(query, params)]


def row(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(query, params).fetchone()


def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    with connect() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return int(cur.lastrowid or 0)


def _previous_balances(conn, previous):
    repair_start = float(previous["repair_end_balance"]) if previous else 0.0
    owners_debt_start = 0.0
    tenants_debt_start = 0.0
    if previous:
        prev_fin = conn.execute("SELECT * FROM report_financials WHERE report_id=?", (previous["id"],)).fetchall()
        for fin in prev_fin:
            if fin["payer_type"] == "Собственники":
                owners_debt_start = float(fin["debt_end"] or 0)
            if fin["payer_type"] == "Наниматели":
                tenants_debt_start = float(fin["debt_end"] or 0)
    return repair_start, owners_debt_start, tenants_debt_start


def report_opening_state(conn, house_id: int, year: int, period_start: str | None = None):
    """Return the predecessor identity and balances used when creating a report."""
    p_start = period_start or full_year_period(year)[0]
    previous = conn.execute(
        """
        SELECT * FROM reports
        WHERE house_id=? AND (year < ? OR (year=? AND period_end < ?))
        ORDER BY year DESC, period_end DESC, id DESC LIMIT 1
        """,
        (house_id, year, year, p_start),
    ).fetchone()
    return (previous["id"] if previous else None, *_previous_balances(conn, previous))


def create_report_in_conn(conn: sqlite3.Connection, company_id: int, house_id: int, year: int, period_start: str | None = None, period_end: str | None = None, show_period_note: bool = False) -> int:
    """Создает отчет, используя уже открытую SQLite-сессию.

    Это нужно для массового заполнения: нельзя открывать второе соединение и
    создавать отчет, пока первая транзакция уже выполняет пакетную запись.
    Иначе SQLite на Windows может вернуть `database is locked`.
    """
    owner = conn.execute(
        "SELECT company_id FROM houses WHERE id=?", (house_id,)
    ).fetchone()
    if owner is None or owner["company_id"] != company_id:
        raise ValueError("Дом не принадлежит выбранной управляющей организации.")
    default_start, default_end = full_year_period(year)
    p_start = period_start or default_start
    p_end = period_end or default_end
    _, repair_start, owners_debt_start, tenants_debt_start = report_opening_state(
        conn, house_id, year, p_start
    )
    cur = conn.execute(
        """
        INSERT INTO reports(company_id, house_id, year, period_start, period_end, show_period_note, repair_start_balance)
        VALUES(?,?,?,?,?,?,?)
        """,
        (company_id, house_id, year, p_start, p_end, 1 if show_period_note else 0, repair_start),
    )
    rid = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO report_financials(report_id, payer_type, debt_start) VALUES(?,?,?)",
        (rid, "Собственники", owners_debt_start),
    )
    conn.execute(
        "INSERT INTO report_financials(report_id, payer_type, debt_start) VALUES(?,?,?)",
        (rid, "Наниматели", tenants_debt_start),
    )
    conn.execute("INSERT INTO report_claims(report_id) VALUES(?)", (rid,))
    return rid


def create_report(company_id: int, house_id: int, year: int, period_start: str | None = None, period_end: str | None = None, show_period_note: bool = False) -> int:
    with connect() as conn:
        rid = create_report_in_conn(conn, company_id, house_id, year, period_start, period_end, show_period_note)
        conn.commit()
        return rid


REPORT_EDITOR_KEYS = ("report", "content", "repair", "management", "financials", "claims")


def read_report_editor_snapshot(conn, report_id: int) -> dict[str, Any]:
    """Read only the rows owned by this report in the caller's transaction."""
    report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if report is None:
        raise ValueError("Отчет не найден")
    content = conn.execute("SELECT * FROM report_content_items WHERE report_id=? ORDER BY position, id", (report_id,)).fetchall()
    repair = conn.execute("SELECT * FROM report_repair_items WHERE report_id=? ORDER BY position, id", (report_id,)).fetchall()
    management = conn.execute("SELECT * FROM report_management_items WHERE report_id=? ORDER BY position, id", (report_id,)).fetchall()
    financials = conn.execute("SELECT * FROM report_financials WHERE report_id=? ORDER BY CASE payer_type WHEN 'Собственники' THEN 1 ELSE 2 END", (report_id,)).fetchall()
    claims = conn.execute("SELECT * FROM report_claims WHERE report_id=?", (report_id,)).fetchone()
    return {
        "report": dict(report),
        "content": [dict(x) for x in content],
        "repair": [dict(x) for x in repair],
        "management": [dict(x) for x in management],
        "financials": [dict(x) for x in financials],
        "claims": dict(claims) if claims else {},
    }


def load_report_bundle(report_id: int) -> dict[str, Any]:
    """Read all report sections from one SQLite snapshot."""
    with closing(connect()) as conn:
        conn.execute("BEGIN")
        snapshot = read_report_editor_snapshot(conn, report_id)
        report = snapshot["report"]
        company = conn.execute("SELECT * FROM companies WHERE id=?", (report["company_id"],)).fetchone()
        house = conn.execute("SELECT * FROM houses WHERE id=?", (report["house_id"],)).fetchone()
        # The previous report is contextual evidence, not a claim that its
        # balances were actually copied. Review can flag uncertain continuity.
        previous = conn.execute(
            """SELECT id,company_id,year,period_end,status FROM reports
               WHERE house_id=? AND id<>?
                 AND (year<? OR (year=? AND period_end<?))
               ORDER BY year DESC,period_end DESC,id DESC LIMIT 1""",
            (report["house_id"], report_id, report["year"],
             report["year"], report["period_start"]),
        ).fetchone()
        signing = conn.execute("SELECT snapshot_json FROM report_signing WHERE report_id=?", (report_id,)).fetchone()
        if signing:
            snapshot["signing"] = json.loads(signing[0])
        return {
            **snapshot,
            "company": dict(company) if company else {},
            "house": dict(house) if house else {},
            "previous_report": dict(previous) if previous else {},
        }
