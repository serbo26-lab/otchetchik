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
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=30)) as conn:
        conn.execute("PRAGMA query_only=ON")
        for statement in conn.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
    return digest.digest()


def make_backup(reason: str = "auto") -> Path | None:
    src = db_path()
    if not src.exists():
        return None
    # Manual copies, schema changes, restores and destructive actions always
    # get their own guard. Routine sync and timer backups are content-aware.
    if reason in ("auto", "before_assistant_sync"):
        folder = data_dir() / "backups"
        candidates = list(folder.glob("reporting_728_*.db"))
        if candidates:
            latest = max(candidates, key=lambda p: p.stat().st_mtime_ns)
            try:
                if _logical_digest(src) == _logical_digest(latest):
                    return None
            except (OSError, sqlite3.DatabaseError):
                # Never skip a protective backup on a failed comparison.
                pass
    # SQLite uses WAL: copying only the .db file can omit committed data
    # that is still stored in the -wal journal. The online backup API
    # creates a consistent snapshot, including those committed transactions.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = data_dir() / "backups" / f"reporting_728_{reason}_{stamp}.db"
    temporary = out.with_suffix(".tmp")
    try:
        # Close actual handles before renaming: Windows locks open SQLite files.
        with closing(sqlite3.connect(str(src), timeout=30)) as source:
            with closing(sqlite3.connect(str(temporary), timeout=30)) as destination:
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
