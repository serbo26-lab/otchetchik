from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    path = app_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    (path / "backups").mkdir(parents=True, exist_ok=True)
    (path / "exports").mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "reporting_728.db"


def safe_filename(value: str, fallback: str = "file", max_len: int = 80) -> str:
    value = (value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    value = value[:max_len].strip(" .")
    return value or fallback


def export_dir(company_name: str, year: int | str, house_address: str) -> Path:
    path = data_dir() / "exports" / safe_filename(company_name, "УО", 60) / str(year) / safe_filename(house_address, "Дом", 60)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _logical_digest(path: Path) -> bytes:
    """Compare SQL contents, including committed WAL transactions, not mtimes.

    Restarting or checkpointing SQLite may change DB bytes without changing
    any report data. A logical signature also works across SQLite backups.
    """
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=30)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        return logical_connection_digest(conn)


def logical_connection_digest(conn: sqlite3.Connection) -> bytes:
    """Hash a consistent caller-owned SQLite snapshot."""
    digest = hashlib.sha256()
    schema = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT GLOB 'sqlite_autoindex_*' "
        "ORDER BY type,name"
    ).fetchall()
    for item in schema:
        digest.update(repr(tuple(item)).encode("utf-8"))
        digest.update(b"\n")
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name NOT GLOB 'sqlite_*' OR name='sqlite_sequence') "
        "ORDER BY name"
    ).fetchall()
    for (name,) in tables:
        escaped = name.replace('"', '""')
        columns = conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        # Do not treat sqlite_sequence/app_meta rowid churn on reopening as
        # a data change. Sort by explicit PK or all columns otherwise.
        ordered = sorted(((int(col[5]), str(col[1])) for col in columns
                          if col[5]), key=lambda pair: pair[0])
        keys = [key for _, key in ordered] or [str(col[1]) for col in columns]
        order_by = ",".join('"' + key.replace('"', '""') + '"' for key in keys)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        query = f'SELECT * FROM "{escaped}"'
        if order_by:
            query += f" ORDER BY {order_by}"
        for row in conn.execute(query):
            digest.update(repr(tuple(row)).encode("utf-8"))
            digest.update(b"\n")
    return digest.digest()


def _matching_latest_backup(src: Path) -> Path | None:
    candidates = list((data_dir() / "backups").glob("reporting_728_*.db"))
    if not candidates:
        return None
    try:
        latest = max(candidates, key=lambda p: p.stat().st_mtime_ns)
        if _logical_digest(src) == _logical_digest(latest):
            return latest
    except (OSError, sqlite3.DatabaseError):
        # Never skip a protective backup on a failed comparison.
        pass
    return None


def make_backup(reason: str = "auto", *, reuse_existing: bool = False) -> Path | None:
    src = db_path()
    if not src.exists():
        return None
    # Manual copies, schema changes, restores and destructive actions always
    # get their own guard. Routine sync and timer backups are content-aware.
    if reason in ("auto", "before_assistant_sync"):
        matching = _matching_latest_backup(src)
        if matching is not None:
            return matching if reuse_existing else None
    # SQLite uses WAL: copying only the .db file can omit committed data
    # that is still stored in the -wal journal. The online backup API
    # creates a consistent snapshot, including those committed transactions.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = data_dir() / "backups" / f"reporting_728_{reason}_{stamp}.db"
    temporary = out.with_suffix(".tmp")
    try:
        # Close actual handles before renaming: Windows locks open SQLite files.
        with (closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro",
                                       uri=True, timeout=30)) as source,
              closing(sqlite3.connect(str(temporary), timeout=30)) as destination):
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Резервная копия SQLite не прошла проверку целостности.")
        os.replace(temporary, out)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    cleanup_backups(max_files=120)
    return out


def cleanup_backups(max_files: int = 120) -> None:
    folder = data_dir() / "backups"
    # Only rotate automatic copies. Manual and pre-change safety snapshots
    # must never silently disappear after the 120 newest auto backups.
    files = sorted(folder.glob("reporting_728_auto_*.db"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for item in files[max_files:]:
        try:
            item.unlink()
        except OSError:
            pass
