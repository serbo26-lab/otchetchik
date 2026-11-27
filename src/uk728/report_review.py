"""Read-only, explainable validation of a saved report bundle before export.

This is a usability and consistency check, NOT a legal compliance assessment.
No database writes and no changes to the official 728/пр output.
"""
from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any

from .report_signing import verify_snapshot


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


def _valid_numbers(item, fields):
    for field in fields:
        try:
            valid = isfinite(float(item.get(field) or 0))
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            return False
    return True


def _check_identity(report, company, house, errors, warnings):
    rid = report.get("id")
    if not rid:
        errors.append("Отчет не выбран или не найден.")
    if not str(company.get("name") or "").strip():
        errors.append("Не указано наименование управляющей организации.")
    if not str(house.get("address") or "").strip():
        errors.append("Не указан адрес многоквартирного дома.")
    if _number(house.get("total_area")) <= 0:
        warnings.append("Не заполнена общая площадь жилых и нежилых помещений МКД.")
    # Assistant's charge_area is the total premises area without common
    # property. Missing residential/non-residential breakdown is not an error
    # when the required aggregate has been populated from that source.
    for field, description in (
        ("ogrn_inn", "ОГРН / ИНН управляющей организации"),
        ("reception_address", "адрес приёма населения по вопросам отчёта"),
        ("report_person", "ФИО и должность лица, дающего разъяснения"),
        ("report_contact", "контактные данные лица, дающего разъяснения"),
    ):
        if not str(company.get(field) or "").strip():
            warnings.append(f"Не заполнены сведения: {description}.")
    if not str(report.get("publish_date") or "").strip():
        warnings.append("Не заполнена дата размещения отчёта.")


def _check_period_dates(report, year, errors, warnings):
    start_raw = report.get("period_start")
    end_raw = report.get("period_end")
    start, end = _date(start_raw), _date(end_raw)
    if start_raw and not start:
        errors.append("Неверный формат даты начала управления.")
    if end_raw and not end:
        errors.append("Неверный формат даты окончания управления.")
    if start and end and start > end:
        errors.append("Период управления: дата начала позже даты окончания.")
    if 2000 <= year <= 2100 and any(
        boundary is not None and boundary.year != year for boundary in (start, end)
    ):
        errors.append("Даты периода управления в отчете должны относиться к отчетному году.")
    if bool(report.get("show_period_note")) and (not start or not end):
        errors.append("Включено отображение периода управления, но его даты не заполнены.")
    if not start_raw or not end_raw:
        warnings.append("Период управления не заполнен полностью.")
    return start


def _check_period(report, errors, warnings):
    try:
        year = int(report.get("year") or 0)
    except (ValueError, TypeError, OverflowError):
        year = 0
    if not 2000 <= year <= 2100:
        errors.append("Некорректный отчетный год.")

    start = _check_period_dates(report, year, errors, warnings)

    if report.get("publish_date") and not _date(report["publish_date"]):
        errors.append("Неверный формат даты размещения отчета.")

    return year, start


def _check_previous(bundle, report, start, warnings):
    previous = bundle.get("previous_report") or {}
    if previous:
        previous_end = _date(previous.get("period_end"))
        if (
            not start or not previous_end or (start - previous_end).days != 1
            or previous.get("status") not in ("Проверен", "Размещен", "Размещён")
            or previous.get("company_id") != report.get("company_id")
        ):
            warnings.append(
                "Предыдущий отчет не проверен, относится к другой УО "
                "или его период не примыкает к текущему. "
                "Сверьте начальные остатки и задолженность с учетными данными."
            )


def _check_numbers(bundle, report, house, errors):
    numeric_groups = (
        ("Площадь", [house], ("total_area",)),
        ("Общие сведения", [report], ("management_cost", "repair_start_balance",
          "repair_accrual", "repair_work_cost", "repair_end_balance")),
        ("Содержание", bundle.get("content") or [],
         ("unit_price", "plan_qty", "plan_cost", "actual_qty", "actual_cost")),
        ("Текущий ремонт", bundle.get("repair") or [], ("cost",)),
        ("Управление", bundle.get("management") or [], ("unit_price", "qty", "cost")),
        ("Начисления", bundle.get("financials") or [], ("debt_start", "accrued", "paid", "debt_end")),
        ("Претензионная работа", [bundle.get("claims") or {}],
         ("pretensions_count", "lawsuits_count", "recovered_amount")),
    )
    for section, entries, fields in numeric_groups:
        for index, item in enumerate(entries, 1):
            if not _valid_numbers(item, fields):
                errors.append(f"{section}: строка {index} содержит некорректное число.")


def _incomplete_imported_repair(item):
    return (str(item.get("source") or "").startswith("assistant-work-list:")
            and (not str(item.get("basis") or "").strip()
                 or not str(item.get("volume") or "").strip()
                 or _number(item.get("cost")) <= 0))


def _check_section_rows(section, key, entries, errors):
    for index, item in enumerate(entries, 1):
        if key == "financials" and item.get("payer_type") not in ("Собственники", "Наниматели"):
            errors.append(
                f"Начисления: строка {index} имеет неизвестный вид плательщика."
            )
        if key in ("content", "repair", "management") and not str(item.get("name") or "").strip():
            errors.append(f"{section}: строка {index} не имеет наименования.")
        if key == "repair" and _incomplete_imported_repair(item):
            errors.append(
                f"Текущий ремонт: строка {index} перенесена из перечня "
                "как черновик. Укажите подтверждённые основание, объём и "
                "стоимость выполненной работы либо удалите строку."
            )


def _check_sections(bundle, errors, warnings):
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
        _check_section_rows(section, key, entries, errors)
    return counts


def _check_imported_work(bundle, warnings):
    # An annual work-list is a plan, not evidence that the services were
    # actually performed. Imported zeroes are unknown until the user checks
    # and completes the performed quantity/cost columns.
    unconfirmed_work_list_rows = sum(
        1 for item in (bundle.get("content") or [])
        if str(item.get("source") or "").startswith("assistant-work-list:")
        and _number(item.get("actual_qty")) == 0
        and _number(item.get("actual_cost")) == 0
    )
    if unconfirmed_work_list_rows:
        warnings.append(
            f"В разделе 1 {unconfirmed_work_list_rows} строк(и) из перечня "
            "Ассистента с нулевым выполнением. Перечень не подтверждает "
            "фактическое выполнение: проверьте и заполните графы «Выполнено» "
            "или подтвердите обоснованность нулевых значений перед выгрузкой."
        )


def _check_management(bundle, report, counts, warnings):
    management_total = _number(report.get("management_cost"))
    if counts["management"]:
        inner_total = sum(_number(row.get("cost")) for row in bundle["management"])
        if abs(inner_total - management_total) > 0.01:
            warnings.append(
                "Итоговая сумма раздела 3 отличается от суммы внутренних строк управления. "
                "Это может быть намеренным — проверьте перед выгрузкой."
            )
    elif management_total == 0:
        warnings.append("В разделе 3 «Управление» итоговая сумма равна нулю.")


def inspect_report(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return {errors,warnings,counts,report_id} from persisted report data."""
    report = bundle.get("report") or {}
    company = bundle.get("company") or {}
    house = bundle.get("house") or {}
    errors, warnings = [], []
    rid = report.get("id")
    _check_identity(report, company, house, errors, warnings)
    year, start = _check_period(report, errors, warnings)
    _check_previous(bundle, report, start, warnings)
    _check_numbers(bundle, report, house, errors)
    counts = _check_sections(bundle, errors, warnings)
    _check_imported_work(bundle, warnings)
    _check_management(bundle, report, counts, warnings)
    try:
        verify_snapshot(bundle)
    except ValueError as exc:
        errors.append(str(exc))

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
