"""Read-only, explainable validation of a saved report bundle before export.

This is a usability and consistency check, NOT a legal compliance assessment.
No database writes and no changes to the official 728/пр output.
"""
from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any


def _date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def inspect_report(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return {errors,warnings,counts,report_id} from persisted report data."""
    report = bundle.get("report") or {}
    company = bundle.get("company") or {}
    house = bundle.get("house") or {}
    errors, warnings = [], []
    rid = report.get("id")
    if not rid:
        errors.append("Отчет не выбран или не найден.")
    if not str(company.get("name") or "").strip():
        errors.append("Не указано наименование управляющей организации.")
    if not str(house.get("address") or "").strip():
        errors.append("Не указан адрес многоквартирного дома.")
    year = int(report.get("year") or 0)
    if not 2000 <= year <= 2100:
        errors.append("Некорректный отчетный год.")

    start_raw = report.get("period_start")
    end_raw = report.get("period_end")
    start, end = _date(start_raw), _date(end_raw)
    if start_raw and not start:
        errors.append("Неверный формат даты начала управления.")
    if end_raw and not end:
        errors.append("Неверный формат даты окончания управления.")
    if start and end and start > end:
        errors.append("Период управления: дата начала позже даты окончания.")
    if bool(report.get("show_period_note")) and (not start or not end):
        errors.append("Включено отображение периода управления, но его даты не заполнены.")
    if not start_raw or not end_raw:
        warnings.append("Период управления не заполнен полностью.")

    if report.get("publish_date") and not _date(report["publish_date"]):
        errors.append("Неверный формат даты размещения отчета.")

    counts = {}
    for section, key in (
        ("Содержание", "content"),
        ("Текущий ремонт", "repair"),
        ("Управление (внутренние строки)", "management"),
        ("Начисления и поступления", "financials"),
    ):
        entries = bundle.get(key) or []
        counts[key] = len(entries)
        if key == "content" and not entries:
            warnings.append("В разделе 1 «Содержание» нет работ или услуг.")
        if key == "financials" and not entries:
            warnings.append("Не заполнены строки начислений и поступлений.")
        for index, item in enumerate(entries, 1):
            if key in ("content", "repair", "management") and not str(item.get("name") or "").strip():
                errors.append(f"{section}: строка {index} не имеет наименования.")

    management_total = _number(report.get("management_cost"))
    if counts["management"] and not isfinite(management_total):
        errors.append("В разделе 3 указана некорректная итоговая сумма.")
    if counts["management"]:
        inner_total = sum(_number(row.get("cost")) for row in bundle["management"])
        if abs(inner_total - management_total) > 0.01:
            warnings.append(
                "Итоговая сумма раздела 3 отличается от суммы внутренних строк управления. "
                "Это может быть намеренным — проверьте перед выгрузкой."
            )
    elif management_total == 0:
        warnings.append("В разделе 3 «Управление» итоговая сумма равна нулю.")

    return {
        "report_id": rid,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
        "year": year,
    }



def bundle_fingerprint(bundle: dict[str, Any]) -> str:
    """Stable digest detects SQLite changes after the user reviewed the report."""
    import hashlib
    import json
    serial = json.dumps(bundle, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()

def review_text(result: dict[str, Any]) -> str:
    items = [
        "Предварительная проверка введенных данных (не является юридической экспертизой).",
        f'Содержание: {result["counts"]["content"]} строк',
        f'Текущий ремонт: {result["counts"]["repair"]} строк',
        f'Расчет управления: {result["counts"]["management"]} строк',
        f'Финансы: {result["counts"]["financials"]} строк',
        "",
    ]
    if result["errors"]:
        items.append(f'Ошибок: {len(result["errors"])}')
        items.extend(f"ОШИБКА: {item}" for item in result["errors"])
    else:
        items.append("Критических ошибок не обнаружено.")
    if result["warnings"]:
        items.append(f'Предупреждений: {len(result["warnings"])}')
        items.extend(f"ПРОВЕРЬТЕ: {item}" for item in result["warnings"])
    else:
        items.append("Замечаний по полноте основных полей нет.")
    return "\n".join(items)
