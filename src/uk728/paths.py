from __future__ import annotations

import os
import re
import sqlite3
import sys
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


def make_backup(reason: str = "auto") -> Path | None:
    src = db_path()
    if not src.exists():
        return None
    # SQLite uses WAL: copying only the .db file can omit committed data
    # that is still stored in the -wal journal. The online backup API
    # creates a consistent snapshot, including those committed transactions.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = data_dir() / "backups" / f"reporting_728_{reason}_{stamp}.db"
    temporary = out.with_suffix(".tmp")
    try:
        with sqlite3.connect(str(src), timeout=30) as source:
            with sqlite3.connect(str(temporary), timeout=30) as destination:
                source.backup(destination)
        os.replace(temporary, out)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    cleanup_backups(max_files=120)
    return out


def cleanup_backups(max_files: int = 120) -> None:
    folder = data_dir() / "backups"
    files = sorted(folder.glob("reporting_728_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for item in files[max_files:]:
        try:
            item.unlink()
        except OSError:
            pass
