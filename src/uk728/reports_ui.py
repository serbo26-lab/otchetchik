"""Report editor, creation dialog and reference-work selection."""
from __future__ import annotations

import sqlite3
from typing import Any
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QSizePolicy, QGroupBox,
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSplitter, QTabWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from . import db
from .db import connect
from .exporters import export_docx, export_pdf, export_xlsx
from .paths import make_backup
from .utils import full_year_period, iso_date, money, to_float
from .ui_shell import ActionFlowLayout
from .report_storage import sync_report_rows
from .report_review import inspect_report, review_text, bundle_fingerprint
from .ui_components import ContentCard, action_panel, table_card_with_selection, fit_table_columns
from .ui_editors import (
    SECTION_CONTENT, SECTION_REPAIR, SECTION_MANAGEMENT,
    DELETE_ROWS_TITLE, DELETE_ROWS_TEXT, ADD_ROW_TEXT,
    selectable_label, COMMON_UNITS, COMMON_PAYER_TYPES, COMMON_REPAIR_BASIS,
    numeric_error, distinct_texts, ClickComboBox, SafeSpinBox,
    EditableComboDelegate, CopyPasteTable, BaseTab, form_scroll,
    restore_combo_selection, row_get, normalize_section, work_display, fill_combo,
)
from .ui_labels import (
    COST_LABEL, REPORT_YEAR_LABEL, CREATE_TITLE, WORK_ID_LABEL, NOTES_LABEL,
    UNIT_LABEL, SELECT_COMPANY_MESSAGE, HOUSE_COMPANY_SQL, COMPANY_LIST_SQL,
    ADD_REFERENCE_TEXT, DATE_FORMAT_HINT, TEMPLATE_LABEL, APPLY_TEMPLATE_TEXT,
    SECTION_CONTENT_LABEL, MANUAL_SOURCE, TEMPLATE_SOURCE, MASS_SOURCE, REFERENCE_SOURCE,
)


class ReportChangedError(ValueError):
    """The persisted report no longer matches the loaded editor."""


class AddFromReferenceDialog(QDialog):
    def __init__(self, category: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(ADD_REFERENCE_TEXT)
        self.selected_id = None
        layout = QVBoxLayout(self)
        self.resize(620, 190)
        target = normalize_section(category, category)
        self.records = [
            (work_display(r), r["id"])
            for r in db.named_rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, name")
            if normalize_section(row_get(r, "category", ""), "") == target
        ]
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию или категории работы…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.combo = ClickComboBox()
        layout.addWidget(self.combo)
        self.result_hint = selectable_label("")
        layout.addWidget(self.result_hint)
        btns = ActionFlowLayout()
        self.add_button = QPushButton("Добавить")
        cancel = QPushButton("Отмена")
        self.add_button.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(self.add_button); btns.addWidget(cancel); layout.addLayout(btns)
        self.search.textChanged.connect(self.filter_works)
        self.combo.currentIndexChanged.connect(self._update_selection)
        self.filter_works()
        self.search.setFocus()

    def filter_works(self, *_args):
        previous = self.combo.currentData()
        query = self.search.text().strip().casefold()
        matches = [(title, ident) for title, ident in self.records if query in title.casefold()]
        blocked = self.combo.blockSignals(True)
        try:
            self.combo.clear()
            for title, ident in matches:
                self.combo.addItem(title, ident)
            index = self.combo.findData(previous)
            if index >= 0:
                self.combo.setCurrentIndex(index)
        finally:
            self.combo.blockSignals(blocked)
        self.combo.setEnabled(bool(matches))
        if matches:
            self.result_hint.setText(f"Найдено работ: {len(matches)} из {len(self.records)}")
        elif self.records:
            self.result_hint.setText("По запросу ничего не найдено. Измените или очистите поиск.")
        else:
            self.result_hint.setText("В справочнике нет работ для этого раздела. Можно добавить строку вручную в отчете.")
        self.combo.setToolTip(self.combo.currentText())
        self._update_selection()

    def _update_selection(self, *_args):
        self.add_button.setEnabled(self.combo.currentData() is not None)

    def accept(self):
        selected = self.combo.currentData()
        if selected is None:
            return
        self.selected_id = selected
        super().accept()


class CreateReportDialog(QDialog):
    def __init__(self, default_year: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать отчет")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.year = SafeSpinBox(); self.year.setRange(2000, 2100); self.year.setValue(int(default_year or 2026))
        self.year.valueChanged.connect(self.fill_full_year)
        self.period_start = QLineEdit(); self.period_end = QLineEdit()
        self.period_start.setPlaceholderText(DATE_FORMAT_HINT)
        self.period_end.setPlaceholderText(DATE_FORMAT_HINT)
        self.show_period = QCheckBox("Выводить строку фактического периода управления в отчете")
        form.addRow(selectable_label(REPORT_YEAR_LABEL), self.year)
        form.addRow(selectable_label("Период управления с"), self.period_start)
        form.addRow(selectable_label("Период управления по"), self.period_end)
        layout.addLayout(form)
        full_btn = QPushButton("Заполнить полный год")
        full_btn.clicked.connect(self.fill_full_year)
        layout.addWidget(full_btn)
        layout.addWidget(self.show_period)
        buttons = QHBoxLayout()
        ok = QPushButton("Создать"); cancel = QPushButton("Отмена")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        buttons.addStretch(); buttons.addWidget(ok); buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.fill_full_year()

    def fill_full_year(self) -> None:
        start, end = full_year_period(int(self.year.value()))
        self.period_start.setText(start)
        self.period_end.setText(end)
        self.show_period.setChecked(False)

    def values(self) -> tuple[int, str, str, bool]:
        year = int(self.year.value())
        start = iso_date(self.period_start.text()) or self.period_start.text().strip()
        end = iso_date(self.period_end.text()) or self.period_end.text().strip()
        return year, start, end, bool(self.show_period.isChecked())


class ReportsTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        self._loading_report = False
        self._saving_report = False
        self._dirty_report = False
        self._loaded_report_snapshot = None
        self._review_dirty = True
        self._review_snapshot = None
        self._report_tab_index = 0
        self._last_report_filters = None
        self._template_apply_buttons = {}
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_current_report)
        root = QHBoxLayout(self)
        splitter = QSplitter(); self.splitter = splitter; root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        self.company_filter = ClickComboBox(); self.company_filter.currentIndexChanged.connect(self._on_company_filter_changed)
        self.house_filter = ClickComboBox(); self.house_filter.currentIndexChanged.connect(self._on_report_filter_changed)
        self.year_filter = SafeSpinBox(); self.year_filter.setRange(2000, 2100); self.year_filter.setValue(2026); self.year_filter.valueChanged.connect(self._on_report_filter_changed)
        context_card = ContentCard("1. Выберите УО, дом и отчетный год")
        choices = QFormLayout()
        choices.addRow(selectable_label("УО"), self.company_filter)
        choices.addRow(selectable_label("Дом"), self.house_filter)
        choices.addRow(selectable_label("Год"), self.year_filter)
        context_card.add_layout(choices)
        left_l.addWidget(context_card)
        reports_card = ContentCard("2. Выберите или создайте отчет",
                                   "Для одного дома могут существовать отчеты за разные периоды.")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        reports_card.add_widget(self.list, 1)
        self.create_report_btn = QPushButton("Создать отчет за выбранный год")
        self.create_report_btn.clicked.connect(self.create_report)
        reports_card.add_widget(action_panel(CREATE_TITLE, self.create_report_btn,
                                             primary=self.create_report_btn))
        left_l.addWidget(reports_card, 1)
        self.delete_report_btn = QPushButton("Удалить выбранный отчет")
        self.delete_report_btn.clicked.connect(self.delete_report)
        left_l.addWidget(action_panel("Удаление отчета", self.delete_report_btn, purpose="danger"))
        splitter.addWidget(left)

        right = QWidget(); right_l = QVBoxLayout(right)
        self.report_workflow_caption = selectable_label(
            "3. Заполнение разделов → 4. Проверка отчета → 5. Выгрузка документов"
        )
        right_l.addWidget(self.report_workflow_caption)
        self.report_empty_card = ContentCard(
            "Отчёт пока не выбран",
            "Укажите данные слева, чтобы открыть редактор официальной формы."
        )
        self.report_empty_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.report_empty_hint = selectable_label(
            SELECT_COMPANY_MESSAGE
        )
        self.report_empty_card.add_widget(self.report_empty_hint)
        right_l.addWidget(self.report_empty_card)
        # Hidden report_tabs do not contribute to layout sizing. Without
        # an explicit expandable filler the remaining ContentCard was being
        # stretched to the entire height and its labels spread far apart.
        self.report_empty_fill = QWidget()
        self.report_empty_fill.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        right_l.addWidget(self.report_empty_fill, 1)
        self.report_tabs = QTabWidget()
        self.report_tabs.setUsesScrollButtons(True)
        right_l.addWidget(self.report_tabs, 1)
        self._build_general_tab()
        self._build_content_tab()
        self._build_repair_tab()
        self._build_management_tab()
        self._build_finance_tab()
        self._build_export_tab()
        self.report_tabs.setEnabled(False)
        self.save_button = QPushButton("Сохранить отчет")
        self.save_button.clicked.connect(self.save_manually)
        self.save_panel = action_panel("Сохранение выбранного отчета", self.save_button,
                                       primary=self.save_button)
        right_l.addWidget(self.save_panel)
        splitter.addWidget(right)
        splitter.setSizes([300, 1000])
        self._connect_autosave_signals()
        self.reload_filters()
        self._update_report_actions()

    def _update_report_actions(self):
        has_report = bool(self.current_id)
        self.report_tabs.setEnabled(has_report)
        self.report_tabs.setVisible(has_report)
        self.report_workflow_caption.setVisible(has_report)
        self.report_empty_card.setVisible(not has_report)
        self.report_empty_fill.setVisible(not has_report)
        self.save_panel.setVisible(has_report)
        if not has_report:
            if self.company_filter.currentData() is None:
                instruction = SELECT_COMPANY_MESSAGE
            elif self.house_filter.currentData() is None:
                instruction = "Выберите дом выбранной организации."
            else:
                instruction = "Выберите существующий отчет или создайте его за указанный год."
            self.report_empty_hint.setText(instruction)
        self.save_button.setEnabled(has_report)
        self.delete_report_btn.setEnabled(has_report)
        self.create_report_btn.setEnabled(
            self.company_filter.currentData() is not None
            and self.house_filter.currentData() is not None
        )
        self._update_export_actions()
        self._update_template_actions()

    def _update_template_actions(self, *_args):
        for combo, button in self._template_apply_buttons.items():
            available = self.current_id is not None and combo.currentData() is not None
            button.setEnabled(available)
            button.setToolTip("Добавить строки выбранного шаблона." if available else
                              "Выберите отчет и шаблон этого раздела.")

    def _update_export_actions(self):
        snapshot = self._review_snapshot
        if not self.current_id:
            reason = "Сначала выберите отчет."
        elif self._review_dirty or not snapshot or snapshot[0] != self.current_id:
            reason = "Сначала нажмите «Проверить заполнение»."
        elif snapshot[2]["errors"]:
            reason = "Исправьте ошибки и выполните проверку повторно."
        else:
            reason = ""
        for button in self.export_buttons.values():
            button.setEnabled(not reason)
            button.setToolTip(reason or button.text())

    def _invalidate_review(self):
        self._review_dirty = True
        self._review_snapshot = None
        if hasattr(self, "review_status"):
            self.review_text.clear()
            self.review_status.setText(
                "Отчет изменен. Перед выгрузкой повторите проверку."
            )
            self._update_export_actions()

    def _on_report_section_changed(self, index):
        previous_index = self._report_tab_index
        if previous_index == index:
            return
        try:
            self.flush_autosave()
        except Exception as exc:
            blocked = self.report_tabs.blockSignals(True)
            self.report_tabs.setCurrentIndex(previous_index)
            self.report_tabs.blockSignals(blocked)
            self.msg(f"Переход отменен: отчет не сохранен ({exc})")
            return
        self._report_tab_index = index

    def _go_to_report_section(self, index):
        if not self.current_id:
            return
        try:
            self.flush_autosave()
        except Exception as exc:
            self.msg(f"Не удалось сохранить отчет: {exc}")
            return
        self.report_tabs.setCurrentIndex(index)

    def _next_report_action(self, layout, title, next_index):
        button = QPushButton(title)
        button.clicked.connect(lambda _checked=False, i=next_index: self._go_to_report_section(i))
        layout.addWidget(action_panel("Следующий шаг", button, primary=button))

    def add_blank_report_row(self, section: str):
        if not self.current_id:
            return
        if section == SECTION_CONTENT:
            table, add = self.content_table, self.add_content_row
        elif section == SECTION_REPAIR:
            table, add = self.repair_table, self.add_repair_row
        else:
            table, add = self.management_table, self.add_management_row
        add({})
        row = table.rowCount() - 1
        if row >= 0:
            table.setCurrentCell(row, 2)
            table.scrollToBottom()
            table.setFocus()

    def _delete_selected_report_rows(self, table):
        count = len(table.selected_visible_rows())
        if count == 0:
            self.msg("Выделите строки, которые требуется удалить.")
            return
        if QMessageBox.question(
            self, DELETE_ROWS_TITLE, f"Удалить из отчета выбранные строки ({count})?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        table.delete_selected_rows(confirmed=True)
        self.schedule_autosave()

    def _connect_autosave_signals(self) -> None:
        # Автосохранение отчета должно срабатывать не только при переходе между
        # главными вкладками, но и при изменении любого поля отчета, переключении
        # под-вкладок или выборе другого отчета. Используем небольшой debounce,
        # чтобы не писать в базу на каждый символ мгновенно.
        for widget in [
            self.period_start, self.period_end, self.publish, self.management_cost,
            self.repair_start, self.repair_accrual, self.repair_work_cost, self.repair_end,
            self.claim_pret, self.claim_lawsuits, self.claim_amount,
        ]:
            widget.textChanged.connect(self.schedule_autosave)
        self.year.valueChanged.connect(self.schedule_autosave)
        self.show_period.stateChanged.connect(self.schedule_autosave)
        self.status.currentIndexChanged.connect(self.schedule_autosave)
        self.notes.textChanged.connect(self.schedule_autosave)
        self.content_table.itemChanged.connect(lambda _item: self.schedule_autosave())
        self.repair_table.itemChanged.connect(lambda _item: self.schedule_autosave())
        self.management_table.itemChanged.connect(lambda _item: self.schedule_autosave())
        self.finance_table.itemChanged.connect(lambda _item: self.schedule_autosave())
        self.report_tabs.currentChanged.connect(self._on_report_section_changed)

    def schedule_autosave(self, *args) -> None:
        if self._loading_report or self._saving_report or not self.current_id:
            return
        self._dirty_report = True
        self._invalidate_review()
        self._autosave_timer.start(700)
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("pending")

    def flush_autosave(self) -> None:
        if not self._dirty_report or not self.current_id or self._loading_report:
            return
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
        self.save(silent=True, refresh_after=False)

    def _autosave_current_report(self) -> None:
        if not self._dirty_report or not self.current_id or self._loading_report or self._saving_report:
            return
        try:
            self.save(silent=True, refresh_after=False)
        except Exception as exc:
            # A timer must not raise an unhandled error or pretend that the write succeeded.
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            self.msg(f"Не удалось сохранить отчет: {exc}")

    def _build_general_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        card = ContentCard("Общие сведения", "Укажите отчетный год, фактический период управления и статус.")
        form = QFormLayout()
        self.year = SafeSpinBox(); self.year.setRange(2000, 2100); self.year.setValue(2026)
        self.period_start = QLineEdit(); self.period_start.setPlaceholderText(DATE_FORMAT_HINT)
        self.period_end = QLineEdit(); self.period_end.setPlaceholderText(DATE_FORMAT_HINT)
        fast = QPushButton("Заполнить полный год 01.01–31.12"); fast.clicked.connect(self.fill_full_year)
        self.show_period = QCheckBox("Выводить строку: Фактический период управления с ... по ...")
        self.publish = QLineEdit(); self.publish.setPlaceholderText("дд.мм.гггг")
        self.status = ClickComboBox(); self.status.addItems(["Черновик", "Проверен", "Размещен", "Архив"])
        self.notes = QTextEdit(); self.notes.setFixedHeight(80)
        form.addRow(selectable_label(REPORT_YEAR_LABEL), self.year)
        form.addRow(selectable_label("Период управления с"), self.period_start)
        form.addRow(selectable_label("Период управления по"), self.period_end)
        form.addRow(fast)
        form.addRow(self.show_period)
        form.addRow(selectable_label("Дата размещения отчета"), self.publish)
        form.addRow(selectable_label("Статус"), self.status)
        form.addRow(selectable_label(NOTES_LABEL), self.notes)
        card.add_layout(form)
        layout.addWidget(card)
        self._next_report_action(layout, "Перейти к содержанию", 1)
        layout.addStretch()
        self.report_tabs.addTab(form_scroll(w), "Общие сведения")

    def _build_content_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        help_text = selectable_label("Раздел 1. Содержание общего имущества. Логика строки: цена единицы × количество = стоимость.")
        layout.addWidget(help_text)
        template_row = QHBoxLayout()
        self.content_template_combo = ClickComboBox()
        self.content_template_combo.currentTextChanged.connect(self.content_template_combo.setToolTip)
        template_row.addWidget(selectable_label(TEMPLATE_LABEL)); template_row.addWidget(self.content_template_combo, 1)
        layout.addLayout(template_row)
        apply = QPushButton(APPLY_TEMPLATE_TEXT); apply.clicked.connect(lambda: self.apply_template(SECTION_CONTENT, self.content_template_combo))
        self._template_apply_buttons[self.content_template_combo] = apply
        self.content_template_combo.currentIndexChanged.connect(self._update_template_actions)
        add_ref = QPushButton(ADD_REFERENCE_TEXT); add_ref.clicked.connect(lambda: self.add_from_reference(SECTION_CONTENT))
        add_empty = QPushButton(ADD_ROW_TEXT); add_empty.clicked.connect(lambda: self.add_blank_report_row(SECTION_CONTENT))
        from_assistant = QPushButton("Перенести перечень из Ассистента…")
        from_assistant.clicked.connect(self.import_assistant_work_list)
        # Keep the complete template title on its own wide row. Actions
        # can wrap below, instead of shrinking the selection field.
        layout.addWidget(action_panel(
            SECTION_CONTENT_LABEL, apply, add_ref, add_empty, from_assistant, primary=from_assistant
        ))
        self.content_table = CopyPasteTable(0, 9)
        self.content_table.setHorizontalHeaderLabels(["ID", WORK_ID_LABEL, "Наименование работы/услуги", UNIT_LABEL, "Цена единицы", "Кол-во по перечню", "Стоимость по перечню", "Выполнено кол-во", "Стоимость выполнено"])
        self.content_table.setColumnHidden(0, True); self.content_table.setColumnHidden(1, True)
        fit_table_columns(self.content_table, flex=(2,),
                          widths={3: 95, 4: 125, 5: 120, 6: 130, 7: 120, 8: 135})
        self.content_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_content_items", "unit", distinct_texts("work_items", "unit", COMMON_UNITS)), self.content_table))
        self._content_recalc_lock = False
        self.content_table.itemChanged.connect(self.on_content_item_changed)
        self.content_table.context_add_handler = lambda: self.add_blank_report_row(SECTION_CONTENT)
        self.content_table.allow_paste_append = True
        self.content_table.context_delete_handler = lambda: self._delete_selected_report_rows(self.content_table)
        content_card = table_card_with_selection(
            "Строки содержания", self.content_table, name_column=2
        )
        layout.addWidget(content_card, 1)
        delete = QPushButton(DELETE_ROWS_TEXT)
        delete.clicked.connect(lambda: self._delete_selected_report_rows(self.content_table))
        content_card.add_widget(action_panel(DELETE_ROWS_TITLE, delete, purpose="danger"))
        self._next_report_action(layout, "Перейти к текущему ремонту", 2)
        self.report_tabs.addTab(w, "1. Содержание")

    def _build_repair_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        form = QFormLayout()
        self.repair_start = QLineEdit(); self.repair_accrual = QLineEdit(); self.repair_work_cost = QLineEdit(); self.repair_end = QLineEdit()
        for label, widget in [
            ("Остаток/перерасход на 1 января. Плюс = остаток, минус = перерасход", self.repair_start),
            ("Начислено/подлежит внесению на текущий ремонт", self.repair_accrual),
            ("Стоимость работ текущего ремонта за период", self.repair_work_cost),
            ("Остаток/перерасход на 31 декабря", self.repair_end),
        ]:
            form.addRow(selectable_label(label), widget)
        balance_card = ContentCard("Остаток и начисления текущего ремонта",
                                   "Сведения о фонде текущего ремонта заполняются отдельно от перечня работ.")
        balance_card.add_layout(form)
        layout.addWidget(balance_card)
        template_row = QHBoxLayout()
        self.repair_template_combo = ClickComboBox()
        self.repair_template_combo.currentTextChanged.connect(self.repair_template_combo.setToolTip)
        template_row.addWidget(selectable_label(TEMPLATE_LABEL)); template_row.addWidget(self.repair_template_combo, 1)
        layout.addLayout(template_row)
        apply = QPushButton(APPLY_TEMPLATE_TEXT); apply.clicked.connect(lambda: self.apply_template(SECTION_REPAIR, self.repair_template_combo))
        self._template_apply_buttons[self.repair_template_combo] = apply
        self.repair_template_combo.currentIndexChanged.connect(self._update_template_actions)
        add_ref = QPushButton(ADD_REFERENCE_TEXT); add_ref.clicked.connect(lambda: self.add_from_reference(SECTION_REPAIR))
        add_empty = QPushButton(ADD_ROW_TEXT); add_empty.clicked.connect(lambda: self.add_blank_report_row(SECTION_REPAIR))
        from_assistant_repair = QPushButton("Перенести перечень из Ассистента…")
        from_assistant_repair.clicked.connect(self.import_assistant_work_list)
        # Keep the complete template title on its own wide row. Actions
        # can wrap below, instead of shrinking the selection field.
        layout.addWidget(action_panel(
            SECTION_CONTENT_LABEL, apply, add_ref, add_empty, from_assistant_repair, primary=from_assistant_repair
        ))
        self.repair_table = CopyPasteTable(0, 7)
        self.repair_table.setHorizontalHeaderLabels(["ID", WORK_ID_LABEL, "Наименование работы", "Основание", COST_LABEL, "Объем", "Акт/ссылка"])
        self.repair_table.setColumnHidden(0, True); self.repair_table.setColumnHidden(1, True)
        fit_table_columns(self.repair_table, flex=(2, 3, 6),
                          widths={4: 120, 5: 150})
        self.repair_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_repair_items", "basis", COMMON_REPAIR_BASIS), self.repair_table))
        self.repair_table.context_add_handler = lambda: self.add_blank_report_row(SECTION_REPAIR)
        self.repair_table.allow_paste_append = True
        self.repair_table.context_delete_handler = lambda: self._delete_selected_report_rows(self.repair_table)
        repair_card = table_card_with_selection(
            "Работы текущего ремонта", self.repair_table, name_column=2
        )
        layout.addWidget(repair_card, 1)
        delete = QPushButton(DELETE_ROWS_TEXT)
        delete.clicked.connect(lambda: self._delete_selected_report_rows(self.repair_table))
        repair_card.add_widget(action_panel(DELETE_ROWS_TITLE, delete, purpose="danger"))
        self._next_report_action(layout, "Перейти к управлению", 3)
        self.report_tabs.addTab(w, "2. Текущий ремонт")

    def _build_management_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        layout.addWidget(selectable_label(
            "Раздел 3 формы 728/пр. В официальный отчет попадает только итоговая сумма услуг по управлению МКД. "
            "Строки ниже нужны для удобного заполнения суммы раздела 3: цена × количество = стоимость."
        ))
        box = QGroupBox("3. Стоимость услуг по управлению многоквартирным домом")
        box_l = QVBoxLayout(box)
        form = QFormLayout()
        self.management_cost = QLineEdit()
        self.management_cost.setPlaceholderText("Можно ввести вручную или посчитать из строк ниже")
        form.addRow(selectable_label("Итоговая сумма для официального раздела 3, руб."), self.management_cost)
        box_l.addLayout(form)
        recalc_row = QHBoxLayout()
        recalc = QPushButton("Посчитать из строк ниже"); recalc.clicked.connect(self.recalc_management_total)
        recalc_row.addStretch(); recalc_row.addWidget(recalc)
        box_l.addLayout(recalc_row)
        layout.addWidget(box)

        layout.addWidget(selectable_label(
            "Внутренняя детализация ниже не изменяет итог раздела 3 автоматически. "
            "Кнопка «Посчитать из строк ниже» применяется только по вашему выбору."
        ))
        template_row = QHBoxLayout()
        self.management_template_combo = ClickComboBox()
        self.management_template_combo.currentTextChanged.connect(self.management_template_combo.setToolTip)
        template_row.addWidget(selectable_label(TEMPLATE_LABEL)); template_row.addWidget(self.management_template_combo, 1)
        layout.addLayout(template_row)
        apply = QPushButton(APPLY_TEMPLATE_TEXT); apply.clicked.connect(lambda: self.apply_template(SECTION_MANAGEMENT, self.management_template_combo))
        self._template_apply_buttons[self.management_template_combo] = apply
        self.management_template_combo.currentIndexChanged.connect(self._update_template_actions)
        add_ref = QPushButton(ADD_REFERENCE_TEXT); add_ref.clicked.connect(lambda: self.add_from_reference(SECTION_MANAGEMENT))
        add_empty = QPushButton(ADD_ROW_TEXT); add_empty.clicked.connect(lambda: self.add_blank_report_row(SECTION_MANAGEMENT))
        # Keep the complete template title on its own wide row. Actions
        # can wrap below, instead of shrinking the selection field.
        layout.addWidget(action_panel(
            SECTION_CONTENT_LABEL, apply, add_ref, add_empty, primary=add_ref
        ))

        self.management_table = CopyPasteTable(0, 7)
        self.management_table.setHorizontalHeaderLabels(["ID", WORK_ID_LABEL, "Наименование услуги", UNIT_LABEL, "Цена единицы", "Кол-во", COST_LABEL])
        self.management_table.setColumnHidden(0, True); self.management_table.setColumnHidden(1, True)
        fit_table_columns(self.management_table, flex=(2,),
                          widths={3: 110, 4: 135, 5: 115, 6: 145})
        self.management_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_management_items", "unit", distinct_texts("work_items", "unit", COMMON_UNITS)), self.management_table))
        self._management_recalc_lock = False
        self.management_table.itemChanged.connect(self.on_management_item_changed)
        self.management_table.context_add_handler = lambda: self.add_blank_report_row(SECTION_MANAGEMENT)
        self.management_table.allow_paste_append = True
        self.management_table.context_delete_handler = lambda: self._delete_selected_report_rows(self.management_table)
        management_card = table_card_with_selection(
            "Внутренний расчет услуг управления", self.management_table, name_column=2
        )
        layout.addWidget(management_card, 1)
        delete = QPushButton(DELETE_ROWS_TEXT)
        delete.clicked.connect(lambda: self._delete_selected_report_rows(self.management_table))
        management_card.add_widget(action_panel("Удаление строк расчета", delete, purpose="danger"))
        layout.addWidget(selectable_label(
            "В официальной форме раздел 3 содержит итоговую сумму, а не внутренние строки расчета."
        ))
        self._next_report_action(layout, "Перейти к финансам", 4)
        self.report_tabs.addTab(w, "3. Управление")

    def _build_finance_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        layout.addWidget(selectable_label("Раздел 4 и 5. Если строка 'Наниматели' пустая, она не выводится в официальном отчете."))
        claims = QGroupBox("4. Претензионно-исковая работа")
        form = QFormLayout(claims)
        self.claim_pret = QLineEdit(); self.claim_lawsuits = QLineEdit(); self.claim_amount = QLineEdit()
        form.addRow(selectable_label("Количество направленных претензий"), self.claim_pret)
        form.addRow(selectable_label("Количество исков / заявлений на судебный приказ"), self.claim_lawsuits)
        form.addRow(selectable_label("Сумма поступивших средств, руб."), self.claim_amount)
        layout.addWidget(claims)
        self.finance_table = CopyPasteTable(2, 6)
        self.finance_table.setHorizontalHeaderLabels(["ID", "Плательщик", "Долг на начало", "Начислено", "Поступило", "Долг на 1 января следующего периода"])
        self.finance_table.setColumnHidden(0, True)
        fit_table_columns(self.finance_table, flex=(1,),
                          widths={2: 155, 3: 155, 4: 155, 5: 200})
        self.finance_table.setItemDelegateForColumn(1, EditableComboDelegate(lambda: distinct_texts("report_financials", "payer_type", COMMON_PAYER_TYPES), self.finance_table))
        self.finance_table.set_value(0, 1, "Собственники")
        self.finance_table.set_value(1, 1, "Наниматели")
        self.finance_table.protected_columns = {1}
        for payer_row in range(self.finance_table.rowCount()):
            item = self.finance_table.item(payer_row, 1)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        # The official finance section has fixed payers; arbitrary insertion
        # from the generic context menu would violate the payer layout.
        self.finance_table.allow_context_add = False
        self.finance_table.allow_context_delete = False
        self.finance_table.context_add_handler = lambda: self.msg(
            "В разделе 5 предусмотрены строки для собственников и нанимателей."
        )
        self.finance_table.context_delete_handler = lambda: self.msg(
            "Строки плательщиков не удаляются: при необходимости оставьте значения пустыми."
        )
        layout.addWidget(table_card_with_selection(
            "5. Начисления и поступления", self.finance_table, name_column=1
        ), 1)
        self._next_report_action(layout, "Проверить и выгрузить отчет", 5)
        self.report_tabs.addTab(w, "4–5. Финансы")

    def _build_export_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(9)

        review_card = ContentCard(
            "4. Проверка перед выгрузкой",
            "Предварительная проверка полноты и непротиворечивости сведений. "
            "Она не заменяет юридическую проверку отчета."
        )
        self.review_button = QPushButton("Проверить заполнение")
        self.review_button.clicked.connect(self.run_review)
        review_card.add_widget(action_panel("Проверка отчета", self.review_button,
                                            primary=self.review_button))
        self.review_status = selectable_label("Проверка еще не проводилась.")
        self.review_status.setObjectName("CardSubtitle")
        review_card.add_widget(self.review_status)
        self.review_text = QTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setMinimumHeight(140)
        self.review_text.setPlaceholderText("После проверки здесь появятся результаты по разделам.")
        review_card.add_widget(self.review_text)
        layout.addWidget(review_card, 1)

        export_card = ContentCard(
            "5. Выгрузка официальной формы",
            "Экспорт не меняет структуру приложения № 2 к приказу № 728/пр."
        )
        self.export_buttons = {}
        for kind, label in (
            ("xlsx", "Excel (.xlsx)"),
            ("docx", "Word (.docx)"),
            ("pdf", "PDF (.pdf)"),
            ("all", "Выгрузить все форматы"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, k=kind: self.export(k))
            self.export_buttons[kind] = button
        export_card.add_widget(action_panel(
            "Выберите формат", self.export_buttons["xlsx"],
            self.export_buttons["docx"], self.export_buttons["pdf"]
        ))
        export_card.add_widget(action_panel(
            "Выгрузить все", self.export_buttons["all"],
            primary=self.export_buttons["all"]
        ))
        layout.addWidget(export_card)
        self.report_tabs.addTab(w, "Проверка и выгрузка")

    def run_review(self):
        if not self.current_id:
            self.review_status.setText("Сначала выберите отчет.")
            return None
        try:
            self.flush_autosave()
            bundle = db.load_report_bundle(int(self.current_id))
            result = inspect_report(bundle)
            self._review_snapshot = (
                int(self.current_id), bundle_fingerprint(bundle), result
            )
            self._review_dirty = False
            self.review_text.setPlainText(review_text(result))
            if result["errors"]:
                self.review_status.setText(
                    f'Проверка: найдено ошибок {len(result["errors"])}. Исправьте их.'
                )
            else:
                self.review_status.setText(
                    f'Ошибок нет. Предупреждений: {len(result["warnings"])}.'
                )
            self._update_export_actions()
            return result
        except Exception as exc:
            self._invalidate_review()
            self.review_status.setText(f"Проверка не выполнена: {exc}")
            self.msg(f"Проверка не выполнена: {exc}")
            return None

    def _current_report_filters(self):
        return (self.company_filter.currentData(), self.house_filter.currentData(),
                self.year_filter.value())

    def _restore_report_filters(self):
        if self._last_report_filters is None:
            return
        company_id, house_id, year = self._last_report_filters
        for combo, ident in ((self.company_filter, company_id),
                             (self.house_filter, house_id)):
            blocked = combo.blockSignals(True)
            try:
                combo.setCurrentIndex(combo.findData(ident))
                combo._update_selected_tooltip(combo.currentText())
            finally:
                combo.blockSignals(blocked)
        blocked = self.year_filter.blockSignals(True)
        try:
            self.year_filter.setValue(year)
        finally:
            self.year_filter.blockSignals(blocked)

    def _save_before_filter_change(self):
        try:
            self.flush_autosave()
        except Exception as exc:
            self._restore_report_filters()
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            self.msg(f"Переход отменен: отчет не сохранен ({exc})")
            return False
        return True

    def _on_company_filter_changed(self, *_args):
        # The old house list is still intact until the previous report is saved.
        if self._save_before_filter_change():
            self.reload_houses_filter()

    def _on_report_filter_changed(self, *_args):
        if self._save_before_filter_change():
            self.refresh()

    def reload_filters(self):
        self.flush_autosave()
        fill_combo(self.company_filter, COMPANY_LIST_SQL, empty="Выберите организацию")
        # A single UO needs no extra selection; never silently choose
        # the first of several organizations.
        if self.company_filter.count() == 2 and self.company_filter.currentData() is None:
            self.company_filter.setCurrentIndex(1)
        self.reload_houses_filter(refresh=False)
        self.reload_template_combo()
        self.refresh()

    def reload(self):
        # Вкладка отчетов зависит от УО, домов и шаблонов, которые могли быть
        # созданы на других вкладках. Обновляем списки, но не сбрасываем выбор.
        self.reload_filters()

    def reload_houses_filter(self, refresh: bool = True):
        old_data = self.house_filter.currentData()
        old_text = self.house_filter.currentText()
        cid = self.company_filter.currentData()
        self.house_filter.blockSignals(True)
        self.house_filter.clear(); self.house_filter.addItem("Выберите дом", None)
        data = db.rows(
            "SELECT id, address AS name FROM houses WHERE company_id=? ORDER BY address",
            (cid,),
        ) if cid is not None else []
        for r in data:
            self.house_filter.addItem(r["name"], r["id"])
        restore_combo_selection(self.house_filter, old_data, old_text)
        if len(data) == 1 and self.house_filter.currentData() is None:
            self.house_filter.setCurrentIndex(1)
        self.house_filter.blockSignals(False)
        if refresh:
            self.refresh()

    def _fill_template_combo(self, combo: QComboBox, section: str) -> None:
        old_data = combo.currentData()
        old_text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        target = normalize_section(section, section)
        for r in db.named_rows("SELECT * FROM templates ORDER BY name"):
            r_section = normalize_section(row_get(r, "category", ""), "")
            if r_section != target:
                continue
            title = f"{r['name']} · {r_section}"
            combo.addItem(title, r["id"])
            combo.setItemData(combo.count() - 1, title, Qt.ToolTipRole)
        restore_combo_selection(combo, old_data, old_text)
        combo.setToolTip(combo.currentText())
        combo.blockSignals(False)

    def reload_template_combo(self):
        # В каждом разделе показываем только шаблоны этого раздела.
        # Это защищает от случайного применения шаблона текущего ремонта в содержание
        # или шаблона управления в другой раздел.
        if hasattr(self, "content_template_combo"):
            self._fill_template_combo(self.content_template_combo, SECTION_CONTENT)
        if hasattr(self, "repair_template_combo"):
            self._fill_template_combo(self.repair_template_combo, SECTION_REPAIR)
        if hasattr(self, "management_template_combo"):
            self._fill_template_combo(self.management_template_combo, SECTION_MANAGEMENT)
        self._update_template_actions()

    def refresh(self):
        # Before rebuilding the report list, persist the report still on screen.
        if self._dirty_report and self.current_id and not self._loading_report:
            self.flush_autosave()
        self._last_report_filters = self._current_report_filters()
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        params = [self.year_filter.value()]
        sql = "SELECT r.*, h.address FROM reports r LEFT JOIN houses h ON h.id=r.house_id WHERE r.year=?"
        # A report must not appear until the user has a specific UO
        # and a specific house. Avoid opening the first report in a global list.
        if self.company_filter.currentData() is None or self.house_filter.currentData() is None:
            self.list.blockSignals(False)
            self.current_id = None
            self._invalidate_review()
            self._update_report_actions()
            return
        sql += " AND r.company_id=? AND r.house_id=?"
        params.extend((self.company_filter.currentData(), self.house_filter.currentData()))
        sql += " ORDER BY h.address, r.period_start"
        select_row = -1
        for r in db.named_rows(sql, tuple(params)):
            start = r["period_start"] or "начало не указано"
            end = r["period_end"] or "конец не указан"
            item = QListWidgetItem(
                f"{r['address'] or 'Дом'} · {r['year']} · {start} — {end} · {r['status']}"
            )
            item.setToolTip(item.text())
            item.setData(Qt.UserRole, r["id"])
            self.list.addItem(item)
            if target_id is not None and r["id"] == target_id:
                select_row = self.list.count() - 1
        self.list.blockSignals(False)
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
        elif self.list.count() > 0:
            self.list.setCurrentRow(0)
        else:
            self.current_id = None
            self._invalidate_review()
            self.report_tabs.setEnabled(False)
        self._update_report_actions()

    def fill_full_year(self):
        y = self.year.value()
        a, b = full_year_period(y)
        self.period_start.setText(a); self.period_end.setText(b); self.show_period.setChecked(False)

    def create_report(self):
        cid = self.company_filter.currentData()
        hid = self.house_filter.currentData()
        if not hid:
            self.msg("Сначала выберите конкретный дом слева.")
            return
        if not cid:
            house = db.row(HOUSE_COMPANY_SQL, (hid,))
            cid = house["company_id"] if house else None
        if not cid:
            self.msg("У дома не выбрана управляющая организация.")
            return
        dlg = CreateReportDialog(int(self.year_filter.value()), self)
        if dlg.exec() != QDialog.Accepted:
            return
        year, p_start, p_end, show_period = dlg.values()
        # Do not change current_id or create a new record until the previous
        # editor has been committed; the current year may remain unchanged.
        if not self._save_before_filter_change():
            return
        try:
            rid = db.create_report(int(cid), int(hid), int(year), p_start, p_end, show_period)
        except Exception as e:
            QMessageBox.warning(self, "Не удалось создать", f"Возможно, отчет за этот период уже есть.\n\n{e}")
            return
        self.year_filter.setValue(year)
        self.current_id = rid; self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == rid:
                self.list.setCurrentRow(i); break

    def _require_current_report_snapshot(self, conn, rid):
        try:
            current = db.read_report_editor_snapshot(conn, rid)
        except ValueError as exc:
            raise ReportChangedError("Отчет больше не существует.") from exc
        if self.current_id != rid or self._loaded_report_snapshot != current:
            raise ReportChangedError(
                "Отчет изменился после открытия. Операция отменена."
            )

    def delete_report(self):
        rid = self.current_id
        if not rid:
            self.msg("Выберите отчет для удаления.")
            return
        item = self.list.currentItem()
        title = item.text() if item else "выбранный отчет"
        if QMessageBox.question(
            self,
            "Удалить отчет?",
            f"Удалить отчет: {title}?\n\nСтроки содержания, текущего ремонта, управления, финансов и претензионной работы по этому отчету также будут удалены. "
            "Несохраненные изменения удаляемого отчета будут отброшены.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            with connect() as conn:
                conn.execute("BEGIN")
                self._require_current_report_snapshot(conn, rid)
            if make_backup("before_delete_report") is None:
                raise RuntimeError("Резервная копия базы не создана.")
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require_current_report_snapshot(conn, rid)
                conn.execute("DELETE FROM reports WHERE id=?", (rid,))
                conn.commit()
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось удалить отчет", str(exc))
            return
        self._autosave_timer.stop()
        self._dirty_report = False
        self.current_id = None
        self._loaded_report_snapshot = None
        self._invalidate_review()
        self._update_report_actions()
        self.refresh()
        self.msg("Отчет удален")

    def _restore_report_selection(self, old_id, exc):
        # The model selection has changed but the old editor must not be discarded.
        blocked = self.list.blockSignals(True)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == old_id:
                self.list.setCurrentRow(i)
                break
        self.list.blockSignals(blocked)
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("error")
        self.msg(f"Не удалось сохранить предыдущий отчет: {exc}")

    def _save_before_report_switch(self, old_id):
        if old_id and not self._loading_report and not self._saving_report:
            try:
                self.flush_autosave()
            except Exception as exc:
                self._restore_report_selection(old_id, exc)
                return False
        return True

    def _populate_loaded_report(self, bundle):
        r = bundle["report"]
        self.year.setValue(r["year"]); self.period_start.setText(r["period_start"]); self.period_end.setText(r["period_end"])
        self.show_period.setChecked(bool(r["show_period_note"]))
        self.publish.setText(r["publish_date"]); self.status.setCurrentText(r["status"])
        self.management_cost.setText(str(r["management_cost"] or "")); self.notes.setPlainText(r["notes"])
        self.repair_start.setText(str(r["repair_start_balance"] or "")); self.repair_accrual.setText(str(r["repair_accrual"] or "")); self.repair_work_cost.setText(str(r["repair_work_cost"] or "")); self.repair_end.setText(str(r["repair_end_balance"] or ""))
        self.content_table.setRowCount(0)
        for item in bundle["content"]: self.add_content_row(item)
        self.repair_table.setRowCount(0)
        for item in bundle["repair"]: self.add_repair_row(item)
        self.management_table.setRowCount(0)
        for item in bundle.get("management", []): self.add_management_row(item)
        self.finance_table.setRowCount(0)
        for fin in bundle["financials"]:
            row = self.finance_table.rowCount(); self.finance_table.insertRow(row)
            vals = [fin["id"], fin["payer_type"], fin["debt_start"], fin["accrued"], fin["paid"], fin["debt_end"]]
            for c, val in enumerate(vals): self.finance_table.set_value(row, c, val)
            payer_item = self.finance_table.item(row, 1)
            payer_item.setFlags(payer_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        claims = bundle["claims"] or {}
        self.claim_pret.setText(str(claims.get("pretensions_count") or "")); self.claim_lawsuits.setText(str(claims.get("lawsuits_count") or "")); self.claim_amount.setText(str(claims.get("recovered_amount") or ""))

    def _clear_failed_report_load(self, exc):
        # A partially populated editor must never remain bound to a report ID.
        self.current_id = None
        self._dirty_report = False
        self._autosave_timer.stop()
        blocked = self.list.blockSignals(True)
        try:
            self.list.setCurrentRow(-1)
        finally:
            self.list.blockSignals(blocked)
        self._invalidate_review()
        self._update_report_actions()
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("error")
        self.msg(f"Не удалось открыть отчет: {exc}. Выберите отчет повторно.")

    def _load_current(self):
        item = self.list.currentItem()
        new_id = item.data(Qt.UserRole) if item else None
        if not self._save_before_report_switch(self.current_id):
            return
        self._invalidate_review()
        self._loading_report = True
        self._loaded_report_snapshot = None
        try:
            self.current_id = None
            if new_id is not None:
                bundle = db.load_report_bundle(new_id)
                self._populate_loaded_report(bundle)
                self._loaded_report_snapshot = {key: bundle[key] for key in db.REPORT_EDITOR_KEYS}
                self.current_id = new_id
        except Exception as exc:
            self._clear_failed_report_load(exc)
            return
        finally:
            self._loading_report = False
        self._dirty_report = False
        self._update_report_actions()
        if self.current_id is not None:
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("saved")

    def _content_calc_values(self, row: int) -> tuple[float, float]:
        unit_price = to_float(self.content_table.value(row, 4))
        plan_qty = to_float(self.content_table.value(row, 5))
        actual_qty = to_float(self.content_table.value(row, 7))
        return unit_price * plan_qty, unit_price * actual_qty

    def _set_content_value_silent(self, row: int, col: int, value: Any) -> None:
        self._content_recalc_lock = True
        try:
            self.content_table.set_value(row, col, money(value))
        finally:
            self._content_recalc_lock = False

    def recalc_content_row(self, row: int, update_plan: bool = True, update_actual: bool = True) -> None:
        if row < 0 or row >= self.content_table.rowCount():
            return
        plan_cost, actual_cost = self._content_calc_values(row)
        if update_plan:
            self._set_content_value_silent(row, 6, plan_cost)
        if update_actual:
            self._set_content_value_silent(row, 8, actual_cost)

    def on_content_item_changed(self, item: QTableWidgetItem) -> None:
        if self._content_recalc_lock or item is None:
            return
        row = item.row()
        col = item.column()
        # Автоматический пересчет официальных граф формы 728/пр:
        # графа 6 = цена × количество по перечню, графа 8 = цена × выполненное количество.
        # Если пользователь вручную правит сами графы стоимости (6 или 8), не перетираем их сразу.
        if col in (4, 5):
            self.recalc_content_row(row, update_plan=True, update_actual=(col == 4))
        elif col == 7:
            self.recalc_content_row(row, update_plan=False, update_actual=True)
        self.schedule_autosave()

    def add_content_row(self, item: dict[str, Any]):
        row = self.content_table.rowCount(); self.content_table.insertRow(row)
        unit_price = item.get("unit_price", "0")
        plan_qty = item.get("plan_qty", "0")
        actual_qty = item.get("actual_qty", None)
        if actual_qty in (None, ""):
            actual_qty = plan_qty
        plan_cost = item.get("plan_cost", None)
        if plan_cost in (None, ""):
            plan_cost = to_float(unit_price) * to_float(plan_qty)
        actual_cost = item.get("actual_cost", None)
        if actual_cost in (None, ""):
            actual_cost = to_float(unit_price) * to_float(actual_qty)
        vals = [item.get("id", ""), item.get("work_item_id", ""), item.get("name", ""), item.get("unit", "усл."), unit_price, plan_qty, plan_cost, actual_qty, actual_cost]
        self._content_recalc_lock = True
        try:
            for c, val in enumerate(vals): self.content_table.set_value(row, c, val)
        finally:
            self._content_recalc_lock = False
        self.content_table.item(row, 0).setData(Qt.UserRole, {
            "source": item.get("source", MANUAL_SOURCE),
            "is_manual": item.get("is_manual", 0 if item.get("source") in (MASS_SOURCE, TEMPLATE_SOURCE) else 1),
        })
        self.schedule_autosave()

    def add_repair_row(self, item: dict[str, Any]):
        row = self.repair_table.rowCount(); self.repair_table.insertRow(row)
        vals = [item.get("id", ""), item.get("work_item_id", ""), item.get("name", ""), item.get("basis", ""), item.get("cost", "0"), item.get("volume", ""), item.get("act_details", "")]
        for c, val in enumerate(vals): self.repair_table.set_value(row, c, val)
        self.repair_table.item(row, 0).setData(Qt.UserRole, {
            "source": item.get("source", MANUAL_SOURCE),
            "is_manual": item.get("is_manual", 0 if item.get("source") in (MASS_SOURCE, TEMPLATE_SOURCE) else 1),
        })
        self.schedule_autosave()

    def _management_calc_value(self, row: int) -> float:
        return to_float(self.management_table.value(row, 4)) * to_float(self.management_table.value(row, 5))

    def _set_management_value_silent(self, row: int, col: int, value: Any) -> None:
        self._management_recalc_lock = True
        try:
            self.management_table.set_value(row, col, money(value))
        finally:
            self._management_recalc_lock = False

    def recalc_management_row(self, row: int) -> None:
        if row < 0 or row >= self.management_table.rowCount():
            return
        self._set_management_value_silent(row, 6, self._management_calc_value(row))

    def on_management_item_changed(self, item: QTableWidgetItem) -> None:
        if self._management_recalc_lock or item is None:
            return
        if item.column() in (4, 5):
            self.recalc_management_row(item.row())
        self.schedule_autosave()

    def add_management_row(self, item: dict[str, Any]):
        row = self.management_table.rowCount(); self.management_table.insertRow(row)
        unit_price = item.get("unit_price", item.get("default_price", "0"))
        qty = item.get("qty", item.get("plan_qty", "0"))
        cost = item.get("cost", None)
        if cost in (None, ""):
            cost = to_float(unit_price) * to_float(qty)
        vals = [item.get("id", ""), item.get("work_item_id", ""), item.get("name", ""), item.get("unit", "усл."), unit_price, qty, cost]
        self._management_recalc_lock = True
        try:
            for c, val in enumerate(vals): self.management_table.set_value(row, c, val)
        finally:
            self._management_recalc_lock = False
        self.management_table.item(row, 0).setData(Qt.UserRole, {
            "source": item.get("source", MANUAL_SOURCE),
            "is_manual": item.get("is_manual", 0 if item.get("source") in (MASS_SOURCE, TEMPLATE_SOURCE) else 1),
        })
        self.schedule_autosave()

    def recalc_management_total(self) -> None:
        total = 0.0
        for r in range(self.management_table.rowCount()):
            total += to_float(self.management_table.value(r, 6))
        if self.management_table.rowCount() > 0:
            self.management_cost.setText(money(total))
        self.schedule_autosave()

    def import_assistant_work_list(self):
        """Read the linked annual list, preview it, and add only chosen rows."""
        if not self.current_id:
            self.msg("Сначала выберите или создайте отчёт.")
            return
        try:
            self.flush_autosave()
            from .assistant_work_list_ui import AssistantWorkListDialog
            dialog = AssistantWorkListDialog(int(self.current_id), self)
        except Exception as exc:
            QMessageBox.warning(
                self, "Не удалось открыть перечень Ассистента", str(exc)
            )
            return
        if dialog.exec() == QDialog.Accepted and dialog.import_result:
            if (dialog.import_result.added_content
                    or dialog.import_result.added_repair):
                self._load_current()

    def add_from_reference(self, category: str):
        if not self.current_id:
            self.msg("Сначала выберите или создайте отчет.")
            return
        report_id = self.current_id
        dlg = AddFromReferenceDialog(category, self)
        if dlg.exec() != QDialog.Accepted or not dlg.selected_id:
            return
        if self.current_id != report_id:
            self.msg("Отчет изменился. Повторите выбор работы.")
            return
        r = db.row("SELECT * FROM work_items WHERE id=? AND is_active=1", (dlg.selected_id,))
        if not r or normalize_section(r["category"], "") != category:
            QMessageBox.warning(self, "Работа недоступна", "Работа удалена или изменен ее раздел. Повторите выбор.")
            return
        self._append_reference_work(category, r)
        table = self._template_target_table(category)
        table.focus_cell(table.rowCount() - 1, 2)

    def _append_reference_work(self, category, r):
        price = to_float(r["default_price"])
        if category == SECTION_CONTENT:
            self.add_content_row({"work_item_id": r["id"], "name": r["name"], "unit": r["unit"], "unit_price": price, "plan_qty": 1, "plan_cost": price, "actual_qty": 1, "actual_cost": price, "source": REFERENCE_SOURCE})
        elif category == SECTION_MANAGEMENT:
            self.add_management_row({"work_item_id": r["id"], "name": r["name"], "unit": r["unit"], "unit_price": price, "qty": 1, "cost": price, "source": REFERENCE_SOURCE})
        else:
            self.add_repair_row({"work_item_id": r["id"], "name": r["name"], "source": REFERENCE_SOURCE})

    def _template_section(self, template_id: int, fallback: str) -> str:
        tr = db.row("SELECT category FROM templates WHERE id=?", (template_id,))
        return normalize_section(row_get(tr, "category", fallback), fallback)

    def _template_item_section(self, item: Any, template_section: str) -> str:
        section = template_section
        work_id = row_get(item, "work_item_id", None)
        if work_id:
            wr = db.row("SELECT category FROM work_items WHERE id=?", (int(work_id),))
            section = normalize_section(row_get(wr, "category", template_section), template_section)
        return section

    def _append_template_item(self, target_section, it, cost):
        if target_section == SECTION_MANAGEMENT:
            self.add_management_row({"work_item_id": it["work_item_id"], "name": it["name"], "unit": it["unit"], "unit_price": it["unit_price"], "qty": it["plan_qty"], "cost": cost, "source": TEMPLATE_SOURCE})
        elif target_section == SECTION_REPAIR:
            self.add_repair_row({"work_item_id": it["work_item_id"], "name": it["name"], "cost": cost, "volume": f"{money(it['plan_qty'])} {it['unit']}", "source": TEMPLATE_SOURCE})
        else:
            self.add_content_row({"work_item_id": it["work_item_id"], "name": it["name"], "unit": it["unit"], "unit_price": it["unit_price"], "plan_qty": it["plan_qty"], "plan_cost": cost, "actual_qty": it["plan_qty"], "actual_cost": cost, "source": TEMPLATE_SOURCE})

    def _template_target_table(self, target_section):
        if target_section == SECTION_CONTENT:
            target_table = self.content_table
        elif target_section == SECTION_REPAIR:
            target_table = self.repair_table
        else:
            target_table = self.management_table
        return target_table

    def _apply_template_items(self, tid, target_section, template_section):
        added = 0
        skipped = 0
        target_table = self._template_target_table(target_section)
        known_ids = {target_table.value(row, 1)
                     for row in range(target_table.rowCount()) if target_table.value(row, 1)}
        known_names = {target_table.value(row, 2).strip().casefold()
                       for row in range(target_table.rowCount()) if target_table.value(row, 2).strip()}
        for it in db.named_rows("SELECT * FROM template_items WHERE template_id=? ORDER BY position,id", (tid,)):
            item_section = self._template_item_section(it, template_section)
            if item_section != target_section:
                skipped += 1
                continue
            work_key = str(it["work_item_id"] or "")
            name_key = str(it["name"] or "").strip().casefold()
            if (work_key and work_key in known_ids) or (name_key and name_key in known_names):
                skipped += 1
                continue
            cost = to_float(it["unit_price"]) * to_float(it["plan_qty"])
            if work_key:
                known_ids.add(work_key)
            if name_key:
                known_names.add(name_key)
            self._append_template_item(target_section, it, cost)
            added += 1
        return added, skipped

    def apply_template(self, target_section: str, combo: QComboBox):
        if not self.current_id:
            return
        tid = combo.currentData()
        if not tid:
            self.msg("Выберите шаблон для этого раздела.")
            return
        report_id = self.current_id
        if QMessageBox.question(
            self, APPLY_TEMPLATE_TEXT,
            f"Добавить строки шаблона «{combo.currentText()}» в раздел «{target_section}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        if self.current_id != report_id or combo.currentData() != tid:
            self.msg("Выбор изменился. Повторите применение шаблона.")
            return
        template_section = self._template_section(int(tid), "")
        if template_section != target_section:
            QMessageBox.warning(self, "Шаблон недоступен", "Шаблон удален или изменен его раздел. Повторите выбор.")
            self.reload_template_combo()
            return
        table = self._template_target_table(target_section)
        first = table.rowCount()
        added, skipped = self._apply_template_items(tid, target_section, template_section)
        if added:
            table.focus_cell(first, 2)
        self.msg(f"Шаблон применен. Раздел: {target_section}. Добавлено: {added}. Пропущено: {skipped}")

    def _validate_numeric_tables(self):
        sections = (
            (self.content_table, SECTION_CONTENT, (4, 5, 6, 7, 8)),
            (self.repair_table, SECTION_REPAIR, (4,)),
            (self.management_table, SECTION_MANAGEMENT, (4, 5, 6)),
            (self.finance_table, "Финансы", (2, 3, 4, 5)),
        )
        for table, section, columns in sections:
            for row in range(table.rowCount()):
                for column in columns:
                    problem = numeric_error(
                        table.value(row, column),
                        f"{section}, строка {row + 1}, графа «{table.horizontalHeaderItem(column).text()}»",
                    )
                    if problem:
                        raise ValueError(problem)

    def validate_numeric_fields(self) -> None:
        inputs = [
            (self.management_cost.text(), "Стоимость управления"),
            (self.repair_start.text(), "Остаток на начало текущего ремонта"),
            (self.repair_accrual.text(), "Начисления на текущий ремонт"),
            (self.repair_work_cost.text(), "Стоимость текущего ремонта"),
            (self.repair_end.text(), "Остаток на конец текущего ремонта"),
            (self.claim_amount.text(), "Сумма поступивших средств по претензиям"),
        ]
        for widget, label in ((self.claim_pret, "Количество претензий"),
                              (self.claim_lawsuits, "Количество исков")):
            problem = numeric_error(widget.text(), label, whole=True)
            if problem:
                raise ValueError(problem)
        for value, label in inputs:
            problem = numeric_error(value, label)
            if problem:
                raise ValueError(problem)
        self._validate_numeric_tables()

    def save_manually(self):
        try:
            self.save()
        except ReportChangedError as exc:
            answer = QMessageBox.question(
                self, "Отчет изменился",
                f"{exc}\n\nОткрыть актуальные данные из базы? "
                "Несохраненные изменения в редакторе будут отброшены. "
                "Выберите «Нет», чтобы оставить их на экране.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                self._autosave_timer.stop()
                self._dirty_report = False
                self._load_current()
        except (ValueError, OSError, sqlite3.Error) as exc:
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            QMessageBox.warning(self, "Проверьте отчет", str(exc))

    def _save_report_fields(self, conn, rid):
        conn.execute(
            "UPDATE reports SET year=?, period_start=?, period_end=?, show_period_note=?, publish_date=?, status=?, management_cost=?, repair_start_balance=?, repair_accrual=?, repair_work_cost=?, repair_end_balance=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (self.year.value(), iso_date(self.period_start.text()) or self.period_start.text(), iso_date(self.period_end.text()) or self.period_end.text(), 1 if self.show_period.isChecked() else 0, iso_date(self.publish.text()) or self.publish.text(), self.status.currentText(), to_float(self.management_cost.text()), to_float(self.repair_start.text()), to_float(self.repair_accrual.text()), to_float(self.repair_work_cost.text()), to_float(self.repair_end.text()), self.notes.toPlainText(), rid),
        )

    def _report_row_fields(self, widget, table_name, row_index):
        if table_name == "report_content_items":
            price = to_float(widget.value(row_index, 4))
            plan_qty = to_float(widget.value(row_index, 5))
            actual_qty = to_float(widget.value(row_index, 7))
            plan_text = widget.value(row_index, 6)
            actual_text = widget.value(row_index, 8)
            fields = {
                "unit": widget.value(row_index, 3),
                "unit_price": price, "plan_qty": plan_qty,
                "plan_cost": to_float(plan_text) if plan_text else price * plan_qty,
                "actual_qty": actual_qty,
                "actual_cost": to_float(actual_text) if actual_text else price * actual_qty,
            }
        elif table_name == "report_repair_items":
            fields = {
                "basis": widget.value(row_index, 3),
                "cost": to_float(widget.value(row_index, 4)),
                "volume": widget.value(row_index, 5),
                "act_details": widget.value(row_index, 6),
            }
        else:
            price = to_float(widget.value(row_index, 4))
            qty = to_float(widget.value(row_index, 5))
            cost_text = widget.value(row_index, 6)
            fields = {
                "unit": widget.value(row_index, 3),
                "unit_price": price, "qty": qty,
                "cost": to_float(cost_text) if cost_text else price * qty,
            }
        return fields

    def _report_row_payload(self, widget, table_name, row_index):
        name = widget.value(row_index, 2)
        meta_item = widget.item(row_index, 0)
        meta = meta_item.data(Qt.UserRole) if meta_item else None
        meta = meta if isinstance(meta, dict) else {}
        common = {
            "id": int(widget.value(row_index, 0) or 0),
            "work_item_id": int(widget.value(row_index, 1) or 0) or None,
            "name": name,
            "source": meta.get("source", MANUAL_SOURCE),
            "is_manual": meta.get("is_manual", 1),
        }
        return {**common, **self._report_row_fields(widget, table_name, row_index)}

    def _sync_report_sections(self, conn, rid):
        # Reconcile the three report sections by row ID. Unchanged bulk
        # lines must NOT become manually overridden after autosave.
        inserted_rows = []
        report_tables = (
            (self.content_table, "report_content_items"),
            (self.repair_table, "report_repair_items"),
            (self.management_table, "report_management_items"),
        )
        for widget, table_name in report_tables:
            records = []
            for row_index in range(widget.rowCount()):
                records.append(self._report_row_payload(widget, table_name, row_index))
            result = sync_report_rows(conn, table_name, rid, records)
            inserted_rows.append((widget, result))
        return inserted_rows

    def _sync_finance_and_claims(self, conn, rid):
        # Keep stable financial row IDs rather than deleting and recreating
        # all owner/tenant rows on every autosave.
        payer_types = []
        for row_index in range(self.finance_table.rowCount()):
            payer = self.finance_table.value(row_index, 1).strip()
            if not payer:
                continue
            if payer in payer_types:
                raise ValueError(f"Повторяется вид плательщика: {payer}")
            payer_types.append(payer)
            conn.execute(
                """INSERT INTO report_financials
                   (report_id,payer_type,debt_start,accrued,paid,debt_end)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(report_id,payer_type) DO UPDATE SET
                   debt_start=excluded.debt_start,
                   accrued=excluded.accrued,
                   paid=excluded.paid,
                   debt_end=excluded.debt_end""",
                (rid, payer, to_float(self.finance_table.value(row_index, 2)),
                 to_float(self.finance_table.value(row_index, 3)),
                 to_float(self.finance_table.value(row_index, 4)),
                 to_float(self.finance_table.value(row_index, 5))),
            )
        if payer_types:
            placeholders = ",".join("?" for _ in payer_types)
            conn.execute(
                f"DELETE FROM report_financials WHERE report_id=? "
                f"AND payer_type NOT IN ({placeholders})", (rid, *payer_types)
            )
        else:
            conn.execute("DELETE FROM report_financials WHERE report_id=?", (rid,))
        conn.execute(
            """INSERT INTO report_claims
               (report_id,pretensions_count,lawsuits_count,recovered_amount)
               VALUES(?,?,?,?)
               ON CONFLICT(report_id) DO UPDATE SET
               pretensions_count=excluded.pretensions_count,
               lawsuits_count=excluded.lawsuits_count,
               recovered_amount=excluded.recovered_amount""",
            (rid, int(to_float(self.claim_pret.text())),
             int(to_float(self.claim_lawsuits.text())),
             to_float(self.claim_amount.text())),
        )

    def _reconcile_report_ids(self, inserted_rows):
        # Update hidden IDs before allowing UI change signals to mark new edits.
        for widget, inserts in inserted_rows:
            for row_index, record_id in inserts:
                widget.set_value(row_index, 0, record_id)

    def save(self, silent: bool = False, refresh_after: bool = True):
        if not self.current_id:
            if not silent:
                self.msg("Сначала выберите или создайте отчет, затем заполните поля и сохраните.")
            return
        self.validate_numeric_fields()
        rid = self.current_id
        self._saving_report = True
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("saving")
        try:
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._require_current_report_snapshot(conn, rid)
                self._save_report_fields(conn, rid)
                inserted_rows = self._sync_report_sections(conn, rid)
                self._sync_finance_and_claims(conn, rid)
                saved_snapshot = db.read_report_editor_snapshot(conn, rid)
                conn.commit()
                self._loaded_report_snapshot = saved_snapshot
                self._reconcile_report_ids(inserted_rows)
        except Exception:
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            raise
        finally:
            self._saving_report = False
        self._dirty_report = False
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("saved")
        if not silent:
            self.msg("Отчет сохранен")
        if refresh_after:
            self.refresh()

    def _reviewed_export_id(self):
        if not self.current_id:
            self.msg("Выберите отчет для выгрузки.")
            return None
        if self._review_dirty or not self._review_snapshot:
            self.review_status.setText(
                "Сначала нажмите «Проверить заполнение», затем выберите формат."
            )
            self.report_tabs.setCurrentIndex(self.report_tabs.count() - 1)
            return None
        rid, fingerprint, review = self._review_snapshot
        if rid != int(self.current_id):
            self._invalidate_review()
            self.msg("Отчет изменился. Выполните проверку заново.")
            return None
        try:
            self.flush_autosave()
            current_bundle = db.load_report_bundle(rid)
            if bundle_fingerprint(current_bundle) != fingerprint:
                self._invalidate_review()
                self.msg("Данные отчета изменились после проверки. Повторите ее.")
                return None
        except Exception as exc:
            self._invalidate_review()
            self.msg(f"Экспорт отменен: не удалось проверить сохраненные данные ({exc})")
            return None
        if review["errors"]:
            self.review_status.setText(
                "Экспорт заблокирован: исправьте ошибки и выполните проверку снова."
            )
            return None
        if review["warnings"]:
            warning_text = "\n".join(review["warnings"])
            if QMessageBox.question(
                self, "Есть замечания к отчету",
                "В отчете остались предупреждения:\n\n" + warning_text +
                "\n\nПродолжить выгрузку без устранения замечаний?"
            ) != QMessageBox.Yes:
                return None
        return rid

    def _checked_export_bundle(self, rid):
        # A modal warning dialog can allow changes after the first review check.
        # Capture the verified snapshot once; all formats must use that data.
        try:
            self.flush_autosave()
            snapshot = self._review_snapshot
            bundle = db.load_report_bundle(rid)
            if (self._review_dirty or not snapshot or snapshot[0] != rid
                    or self.current_id != rid or bundle_fingerprint(bundle) != snapshot[1]):
                self._invalidate_review()
                self.msg("Данные отчета изменились после проверки. Повторите ее.")
                return None
            return bundle
        except Exception as exc:
            self._invalidate_review()
            self.msg(f"Экспорт отменен: не удалось проверить сохраненные данные ({exc})")
            return None

    def export(self, kind: str):
        if kind not in ("xlsx", "docx", "pdf", "all"):
            return
        rid = self._reviewed_export_id()
        if rid is None:
            return
        bundle = self._checked_export_bundle(rid)
        if bundle is None:
            return
        # Export reads the saved report but does not modify SQLite. A new
        # database copy for every Word/Excel/PDF export is unnecessary.
        paths = []
        try:
            for fmt, export_func in (
                ("xlsx", export_xlsx),
                ("docx", export_docx),
                ("pdf", export_pdf),
            ):
                if kind in (fmt, "all"):
                    paths.append(export_func(rid, bundle=bundle))
        except Exception as exc:
            QMessageBox.critical(
                self, "Ошибка экспорта",
                f"Не удалось завершить выгрузку.\n\n{exc}\n\n"
                "Уже созданные файлы, если они есть, не удалялись."
            )
            return
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Готово")
        msg.setText("Файлы сформированы:\n" + "\n".join(str(path) for path in paths))
        open_button = msg.addButton("Открыть папку", QMessageBox.ActionRole)
        msg.addButton("OK", QMessageBox.AcceptRole)
        msg.exec()
        if msg.clickedButton() == open_button and paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0].parent)))
