"""Local, state-based next-step guidance; never writes data or runs a command."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowHint:
    text: str
    destination: int | None = None
    navigation_text: str = ""


REPORTS = WorkflowHint("Перейдите к отчётам. Шаблоны и массовое заполнение необязательны.",
                       6, "Далее: отчеты →")
DIRECTORY = WorkflowHint("Перейдите в справочник, чтобы использовать категории при добавлении работ.",
                         3, "Далее: справочник работ →")


def _companies(page):
    if page.current_id is None:
        return WorkflowHint("Выберите УО или нажмите «Добавить организацию».")
    if page.has_unsaved_changes():
        return WorkflowHint("Проверьте реквизиты и нажмите «Сохранить организацию».")
    return WorkflowHint("Откройте дома выбранной организации.", 1, "Далее: дома →")


def _houses(page):
    if page.company_filter.currentData() is None:
        return WorkflowHint("Выберите управляющую организацию.")
    if page.current_id is None:
        return WorkflowHint("Выберите дом или нажмите «Добавить дом».")
    if page.has_unsaved_changes():
        return WorkflowHint("Проверьте карточку и нажмите «Сохранить дом».")
    return REPORTS


def _categories(page):
    if page.current_id is None:
        return WorkflowHint("Выберите категорию или нажмите «Добавить категорию».")
    if page.has_unsaved_changes():
        return WorkflowHint("Нажмите «Сохранить категорию и подкатегории».")
    return DIRECTORY


def _works(page):
    if page.has_unsaved_changes():
        return WorkflowHint("Проверьте новые и изменённые строки, затем сохраните справочник.")
    if not page.table.rowCount():
        return WorkflowHint("Добавьте работу вручную или из выбранной категории.")
    return REPORTS


def _templates(page):
    if page.current_id is None:
        return WorkflowHint("Выберите шаблон или нажмите «Добавить шаблон».")
    if page.has_unsaved_changes():
        return WorkflowHint("Проверьте состав и нажмите «Сохранить шаблон».")
    if not page.table.rowCount():
        if page.work_combo.currentData() is None:
            return WorkflowHint("Для наполнения шаблона добавьте работу нужного раздела в справочник.",
                                3, "Открыть справочник работ →")
        return WorkflowHint("Выберите работу и нажмите «Добавить работу в шаблон».")
    return REPORTS


def _mass(page):
    if not page.preview_button.isEnabled():
        return WorkflowHint(page.preview_button.toolTip())
    if page._preview_dirty:
        return WorkflowHint("Нажмите «Рассчитать и проверить». Записи пока не меняются.")
    if page.apply_button.isEnabled():
        return WorkflowHint("Проверьте строки предпросмотра и нажмите «Подтвердить и применить».")
    return WorkflowHint("Нет строк для записи. Уточните параметры или выбор домов.")


REPORT_SECTION_HINTS = {
    0: "Проверьте общие сведения и перейдите к содержанию.",
    1: "Проверьте количество и стоимость работ, затем перейдите к текущему ремонту.",
    2: "Заполните сведения о ремонте при необходимости, затем перейдите к управлению.",
    3: "Проверьте официальный итог управления и перейдите к финансам.",
    4: "Проверьте претензионную работу и финансы, затем откройте проверку и выгрузку.",
}


def _report_review(page):
    snapshot = page._review_snapshot
    if page._review_dirty or not snapshot:
        return WorkflowHint("Нажмите «Проверить заполнение».")
    if snapshot[2]["errors"]:
        return WorkflowHint("Исправьте ошибки, перечисленные в результате проверки, и повторите её.")
    return WorkflowHint("Выберите формат выгрузки. Предупреждения потребуют подтверждения.")


def _reports(page):
    if page.company_filter.currentData() is None or page.house_filter.currentData() is None:
        return WorkflowHint("Выберите УО, дом и отчётный год.")
    if not page.current_id:
        if page.list.count():
            return WorkflowHint("Выберите существующий отчёт из списка.")
        return WorkflowHint("Нажмите «Создать отчет за выбранный год».")
    index = page.report_tabs.currentIndex()
    if index == 1 and not page.content_table.rowCount():
        return WorkflowHint("Добавьте работу в содержание через шаблон, справочник или ручную строку.")
    if index == 5:
        return _report_review(page)
    return WorkflowHint(REPORT_SECTION_HINTS.get(index, "Заполните выбранный раздел."))


def _backups(page):
    if page.cancel_button.isEnabled():
        return WorkflowHint("Для восстановления перезапустите программу; до перезапуска его можно отменить.")
    if page.check_button.isEnabled():
        return WorkflowHint("Нажмите «Проверить выбранную», прежде чем использовать копию.")
    return WorkflowHint("Создайте копию либо выберите существующую для проверки.")


_HANDLERS = {
    "CompaniesTab": _companies,
    "HousesTab": _houses,
    "CategoriesTab": _categories,
    "WorksTab": _works,
    "TemplatesTab": _templates,
    "MassApplyTab": _mass,
    "ReportsTab": _reports,
    "BackupsTab": _backups,
}


def recommend_next(page) -> WorkflowHint:
    handler = _HANDLERS.get(type(page).__name__)
    return handler(page) if handler else WorkflowHint("Выберите запись.")
