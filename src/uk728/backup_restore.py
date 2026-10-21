"""Safe, restart-time restoration of the local SQLite reporting database.

No GUI may overwrite a live database: the UI prepares an immutable, checked
snapshot and the next process restores it before db.init_db() opens the file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .db import SCHEMA_VERSION
from .paths import data_dir, db_path, make_backup


_REQUIRED_TABLES = {
    "app_meta", "companies", "houses", "work_items", "templates",
    "reports", "report_content_items", "report_repair_items",
    "report_management_items", "report_financials", "report_claims",
}
_PENDING_NAME = re.compile(r"^\.restore_pending_[0-9a-f]{32}\.db$")


def _read_only(path: Path) -> sqlite3.Connection:
    # pathlib's as_uri handles Windows drive letters and non-ASCII filenames.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup(path: Path) -> dict:
    """Reject corruption, unrelated DBs and future unsupported schema versions."""
    source = Path(path)
    if not source.is_file():
        raise ValueError("Файл резервной копии не найден.")
    try:
        with closing(_read_only(source)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite сообщает о нарушенной целостности файла.")
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = _REQUIRED_TABLES - tables
            if missing:
                raise ValueError(
                    "Файл не является полной базой «Отчетности УК» "
                    f"(нет таблиц: {', '.join(sorted(missing))})."
                )
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key='schema_version'"
            ).fetchone()
            if row is not None:
                try:
                    version = int(row[0])
                except (TypeError, ValueError):
                    raise ValueError("Неверный номер версии структуры базы.") from None
                if version > SCHEMA_VERSION:
                    raise ValueError(
                        f"Версия структуры копии ({version}) новее поддерживаемой "
                        f"программой ({SCHEMA_VERSION}). Установите более новую версию."
                    )
            else:
                version = None
            companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            houses = conn.execute("SELECT COUNT(*) FROM houses").fetchone()[0]
            reports = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Файл SQLite повреждён или не читается: {exc}") from exc
    return {
        "schema_version": version, "companies": companies,
        "houses": houses, "reports": reports, "size": source.stat().st_size,
    }


def _marker_path() -> Path:
    return data_dir() / ".restore_request.json"


def _pending_file(record: dict) -> Path:
    name = record.get("snapshot", "")
    if not isinstance(name, str) or not _PENDING_NAME.fullmatch(name):
        raise ValueError("Неверный путь подготовленной копии.")
    root = (data_dir() / "backups").resolve()
    path = root / name
    if path.resolve().parent != root or path.is_symlink():
        raise ValueError("Подготовленная копия находится вне разрешённой папки.")
    return path


def pending_restore() -> dict | None:
    marker = _marker_path()
    if not marker.exists():
        return None
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Не удалось прочитать заявку на восстановление.") from exc
    if not isinstance(record, dict):
        raise ValueError("Неверный формат заявки на восстановление.")
    _pending_file(record)
    return record


def stage_restore(selected: Path) -> dict:
    """Snapshot the selected backup and make a durable restore request."""
    if pending_restore() is not None:
        raise RuntimeError("Восстановление уже подготовлено. Сначала отмените его.")
    source = Path(selected)
    if source.resolve() == db_path().resolve():
        raise ValueError("Нельзя выбирать открытую рабочую базу. Выберите резервную копию.")
    details = verify_backup(source)
    name = ".restore_pending_" + uuid.uuid4().hex + ".db"
    target = data_dir() / "backups" / name
    temporary = target.with_suffix(".tmp")
    try:
        with closing(_read_only(source)) as original:
            with closing(sqlite3.connect(str(temporary), timeout=30)) as destination:
                original.backup(destination)
        os.replace(temporary, target)
        verify_backup(target)
        request = {
            "snapshot": name,
            "sha256": _sha256(target),
            "selected_name": source.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "summary": details,
        }
        marker = _marker_path()
        temporary_marker = marker.with_suffix(".tmp")
        temporary_marker.write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary_marker, marker)
        return request
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        _marker_path().with_suffix(".tmp").unlink(missing_ok=True)
        raise


def cancel_restore() -> bool:
    record = pending_restore()
    if record is None:
        return False
    snapshot = _pending_file(record)
    _marker_path().unlink()
    snapshot.unlink(missing_ok=True)
    return True


def apply_pending_restore() -> Path | None:
    """Call once before init_db; never replace a database opened by the GUI."""
    record = pending_restore()
    if record is None:
        return None
    snapshot = _pending_file(record)
    if not snapshot.is_file() or _sha256(snapshot) != record.get("sha256"):
        raise ValueError(
            "Подготовленная копия была изменена или пропала. "
            "Текущая база не затронута."
        )
    verify_backup(snapshot)
    # A consistent BEFORE-restore copy is mandatory even if there is an old WAL
    # containing committed data. It must be preserved by retention clean-up.
    live = db_path()
    guard = None
    if live.exists():
        guard = make_backup("before_restore")
        if guard is None:
            raise RuntimeError("Не удалось защитить текущую базу перед восстановлением.")
        verify_backup(guard)
    try:
        # SQLite's online backup API manages existing WAL files correctly.
        # It is safer than replacing only .db while -wal/-shm may be present.
        with closing(_read_only(snapshot)) as original:
            with closing(sqlite3.connect(str(live), timeout=30)) as destination:
                original.backup(destination)
        verify_backup(live)
    except Exception:
        # If the destination was touched and a guard exists, attempt rollback.
        # On a second failure, abort startup; do not open a possibly damaged DB.
        if guard is not None:
            with closing(_read_only(guard)) as old:
                with closing(sqlite3.connect(str(live), timeout=30)) as destination:
                    old.backup(destination)
            verify_backup(live)
        raise
    # Removing the marker last makes an interrupted restore retryable.
    _marker_path().unlink()
    snapshot.unlink(missing_ok=True)
    return guard
