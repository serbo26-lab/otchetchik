from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\xa0", " ").replace(" ", "").replace(",", ".")
    if text == "":
        return default
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return default


def money(value: Any) -> str:
    num = to_float(value)
    sign = "-" if num < 0 else ""
    num = abs(num)
    s = f"{num:,.2f}".replace(",", " ").replace(".", ",")
    if s.endswith(",00"):
        s = s[:-3]
    return f"{sign}{s}"


def balance_text(value: Any) -> str:
    num = to_float(value)
    if num < 0:
        return f"перерасход {money(abs(num))} руб."
    if num > 0:
        return f"остаток {money(num)} руб."
    return "0 руб."


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def format_date(value: str | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    parsed = parse_date(str(value))
    return parsed.strftime("%d.%m.%Y") if parsed else str(value)


def iso_date(value: str | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    parsed = parse_date(str(value))
    return parsed.isoformat() if parsed else ""


def full_year_period(year: int) -> tuple[str, str]:
    return f"{year}-01-01", f"{year}-12-31"


def is_full_year(year: int, start: str | None, end: str | None) -> bool:
    a, b = full_year_period(year)
    return (start or "") == a and (end or "") == b

