"""Shared visual building blocks for the reporting workspace.

Action groups are semantic and placed in task order, not inferred from labels.
They never change reporting calculations, database rows, or export formats.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTableWidget, QVBoxLayout, QWidget,
)

from .ui_shell import ActionFlowLayout

class ActionPanel(QFrame):
    """Compact, wrap-safe action group with its own clear purpose."""

    def __init__(self, title: str, *, purpose: str = "normal", parent=None):
        super().__init__(parent)
        self.setObjectName("ActionPanel")
        self.setProperty("purpose", purpose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 8)
        layout.setSpacing(5)
        heading = QLabel(title)
        heading.setObjectName("ActionPanelTitle")
        self.heading = heading
        layout.addWidget(heading)
        self.actions = ActionFlowLayout(spacing=7)
        layout.addLayout(self.actions)

    def add_action(self, button: QPushButton, *, role: str = "neutral"):
        if role == "primary":
            button.setObjectName("PrimaryAction")
        elif role == "danger":
            button.setObjectName("DangerAction")
        elif role == "quiet":
            button.setObjectName("QuietAction")
        self.actions.addWidget(button)
        button.setToolTip(button.toolTip() or button.text())
        return button


def action_panel(title: str, *buttons: QPushButton, purpose: str = "normal", primary=None):
    """Compose a single, meaningful group; destructive groups are always separate."""
    panel = ActionPanel(title, purpose=purpose)
    for button in buttons:
        role = "danger" if purpose == "danger" else "primary" if button is primary else "neutral"
        panel.add_action(button, role=role)
    # A single operation should be a visible button, not a nested
    # frame with another heading repeating the purpose of the button.
    if len(buttons) == 1:
        panel.setProperty("compact", True)
        panel.heading.hide()
        panel.body.setContentsMargins(0, 0, 0, 0)
        panel.body.setSpacing(0)
    return panel


class ContentCard(QFrame):
    """Reusable framed section for explanatory text, selectors, tables and forms."""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("ContentCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 10, 12, 12)
        self.body.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        heading.setWordWrap(True)
        self.body.addWidget(heading)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setObjectName("CardSubtitle")
            hint.setWordWrap(True)
            self.body.addWidget(hint)

    def add_widget(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout):
        self.body.addLayout(layout)
        return layout


def table_card(title: str, table: QTableWidget, *, subtitle: str = "") -> ContentCard:
    card = ContentCard(title, subtitle)
    card.add_widget(table, 1)
    return card


def describe_context(tab, main_window) -> tuple[str, str]:
    """Current page-specific context and relevant next step; no mutations."""
    name = tab.__class__.__name__
    current = getattr(tab, "current_id", None)

    def selected(combo, placeholders=()):
        if combo is None:
            return ""
        text = combo.currentText().strip()
        return text if combo.currentData() is not None and text not in placeholders else ""

    if name == "CompaniesTab":
        item = tab.list.currentItem()
        if current is not None and item:
            return f"Управляющая организация: {item.text()}", "Проверьте реквизиты выбранной организации. После заполнения откройте «Дома»."
        return "Управляющая организация не выбрана", "Создайте управляющую организацию кнопкой под перечнем."

    if name == "HousesTab":
        company = selected(tab.company_filter)
        if not company:
            return "Управляющая организация не выбрана", "Сначала выберите УО — после этого станет доступно добавление дома."
        item = tab.list.currentItem()
        if current and item:
            return f"УО: {company}   •   Дом: {item.text()}", "Проверьте адрес, площадь и период управления, затем переходите к отчетам."
        return f"УО: {company}", "Добавьте дом или выберите его из списка."

    if name == "CategoriesTab":
        item = tab.list.currentItem()
        if current and item:
            return f"Категория: {item.text()}", "Укажите раздел формы, при необходимости добавьте подкатегории и сохраните."
        return "Категория не выбрана", "Создайте категорию, затем добавляйте подкатегории."

    if name == "WorksTab":
        category = selected(tab.category_combo)
        sub = selected(tab.subcategory_combo)
        context = "Справочник работ"
        if category:
            context += f"   •   Категория: {category}"
        if sub:
            context += f"   •   Подкатегория: {sub}"
        return context, "Добавьте работу из выбранной категории или создайте строку вручную. Укажите раздел формы и стоимость."

    if name == "TemplatesTab":
        item = tab.list.currentItem()
        if current and item:
            return f"Шаблон: {item.text()}", "Добавьте строки из справочника, укажите количества и сохраните шаблон."
        return "Шаблон не выбран", "Создайте новый шаблон, выберите раздел формы и наполните его строками."

    if name == "MassApplyTab":
        company = selected(tab.company)
        work = selected(tab.work)
        chosen = len(tab.selected_house_ids())
        prefix = f"Массовое заполнение   •   Год: {tab.year.value()}   •   Домов выбрано: {chosen}"
        if company:
            prefix += f"   •   УО: {company}"
        if not company or not work:
            return prefix, "Шаг 1: выберите УО и работу, затем укажите способ расчета."
        if not chosen:
            return prefix, "Шаг 3: отметьте хотя бы один дом, который нужно заполнить."
        if tab._preview_dirty:
            return prefix, "Шаг 4: обновите предварительный просмотр и проверьте суммы и причины пропусков."
        count = tab._preview_plan["summary"]["insert"] + tab._preview_plan["summary"]["update"]
        if not count:
            return prefix, "Нет строк для записи. Проверьте выбор домов и параметры."
        return prefix, "Шаг 5: проверьте готовый предпросмотр, затем подтвердите запись."

    if name == "ReportsTab":
        company = selected(tab.company_filter)
        house = selected(tab.house_filter)
        parts = [f"Отчетный год: {tab.year_filter.value()}"]
        if company:
            parts.insert(0, f"УО: {company}")
        if house:
            parts.insert(1 if company else 0, f"Дом: {house}")
        if current:
            item = tab.list.currentItem()
            return "   •   ".join(parts), (
                "Проверьте введенные значения по разделам формы, затем переходите к выгрузке."
                if item else "Заполните разделы отчета."
            )
        return "   •   ".join(parts), ("Выберите дом и создайте отчет за выбранный год."
                                         if house else "Выберите дом, затем создайте или откройте отчет.")

    if name == "BackupsTab":
        return "Локальные данные программы", "Создайте резервную копию перед обновлением программы или крупными изменениями."

    return "Рабочий контекст", "Выберите запись и продолжите заполнение."
