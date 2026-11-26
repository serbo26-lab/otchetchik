"""Mass-application page and its read-only preview table."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGridLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QTableWidget, QVBoxLayout,
)

from . import db
from .db import connect
from .paths import make_backup
from .utils import money
from .mass_apply_logic import (
    ACTION_TEXT as MASS_ACTION_TEXT, OutdatedPreview as MassOutdatedPreview,
    build_plan as build_mass_plan, apply_plan as apply_mass_plan, validate_inputs,
)
from .ui_shell import ActionFlowLayout
from .ui_components import ContentCard, action_panel, fit_table_columns
from .ui_editors import (
    BaseTab, ClickComboBox, CopyPasteTable, SafeSpinBox, fill_combo,
    polish_form, selectable_label, work_display,
)
from .ui_labels import COST_LABEL, QUANTITY_LABEL, REPORT_YEAR_LABEL


class MassPreviewTable(CopyPasteTable):
    """Review-only; copying is supported, pasting and row mutation are forbidden."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)

    def paste_selection(self):
        return

    def delete_selected_rows(self, confirmed: bool = False) -> bool:
        # Read-only preview: preserve the base method signature but reject
        # deletion even when called with confirmed=True.
        return False

    def _menu(self, pos):
        menu = QMenu(self)
        act_copy = QAction("Копировать выделенное", self)
        act_copy.triggered.connect(self.copy_selection)
        menu.addAction(act_copy)
        menu.exec(self.viewport().mapToGlobal(pos))


class MassApplyTab(BaseTab):
    """Guided bulk entry: sources, calculation, targets, review, confirmation."""

    PREVIEW_COLUMNS = [
        "Дом", "Площадь, м²", "Раздел / работа", "Отчет", COST_LABEL,
        "Цена в строке", QUANTITY_LABEL, "Действие",
    ]
    MODES = [
        ("Цена за 1 м² площади дома", "area"),
        ("Цена за 1 м² в месяц", "monthly"),
        ("Готовая сумма на один дом", "fixed"),
        ("Общая сумма на выбранные дома, распределить по площади", "split"),
        ("Цена × количество", "quantity"),
    ]

    def __init__(self):
        super().__init__()
        self._preview_dirty = True
        self._preview_plan = None
        self._target_company_id = None

        root = QVBoxLayout(self)
        root.setSpacing(8)
        top = QGridLayout()
        top.setHorizontalSpacing(9)
        top.setVerticalSpacing(9)
        top.setContentsMargins(0, 0, 0, 0)
        self.top_layout = top
        root.addLayout(top)

        # Step 1. The work and reporting period.
        context = ContentCard("1. Что заполняем",
            "Выберите УО, отчетный год и конкретную работу из справочника.")
        source = QFormLayout()
        self.source_form = source
        self.company = ClickComboBox()
        self.company.currentIndexChanged.connect(self._company_changed)
        self.year = SafeSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(2026)
        self.work = ClickComboBox()
        source.addRow(selectable_label("Управляющая организация"), self.company)
        source.addRow(selectable_label(REPORT_YEAR_LABEL), self.year)
        source.addRow(selectable_label("Работа / услуга"), self.work)
        polish_form(source, label_width=155)
        context.add_layout(source)
        self.source_card = context
        top.addWidget(context, 0, 0)

        # Step 2. Only input controls required by the selected formula are shown.
        calculation = ContentCard("2. Как рассчитываем")
        form = QFormLayout()
        self.calculation_form = form
        self.mode = ClickComboBox()
        for title, key in self.MODES:
            self.mode.addItem(title, key)
        self.rate_label = selectable_label("Ставка / цена")
        self.months_label = selectable_label("Количество месяцев")
        self.quantity_label = selectable_label(QUANTITY_LABEL)
        self.total_label = selectable_label("Сумма")
        self.rate = QLineEdit()
        self.rate.setPlaceholderText("Например: 6,76")
        self.months = QLineEdit("12")
        self.months.setPlaceholderText("От 1 до 12")
        self.quantity = QLineEdit()
        self.quantity.setPlaceholderText("Например: 12")
        self.total = QLineEdit()
        self.total.setPlaceholderText("Например: 60000")
        form.addRow(selectable_label("Способ расчета"), self.mode)
        for label, field in [
            (self.rate_label, self.rate), (self.months_label, self.months),
            (self.quantity_label, self.quantity), (self.total_label, self.total),
        ]:
            form.addRow(label, field)
        polish_form(form, label_width=135)
        calculation.add_layout(form)
        self.help = selectable_label("")
        self.help.setObjectName("CardSubtitle")
        calculation.add_widget(self.help)
        self.calculation_card = calculation
        top.addWidget(calculation, 0, 1)
        top.setColumnStretch(0, 1)
        top.setColumnStretch(1, 1)

        # Step 3. Explicitly choose which houses are eligible for the calculation.
        targets = ContentCard("3. Какие дома заполняем",
            "Отметьте нужные дома. Архивные дома не включаются.")
        self.house_list = QListWidget()
        self.house_list.setMinimumHeight(100)
        self.house_list.setMaximumHeight(170)
        self.house_list.itemChanged.connect(self.mark_dirty)
        targets.add_widget(self.house_list)
        self.no_houses_hint = selectable_label(
            "Дома не найдены. Выберите управляющую организацию "
            "и добавьте для неё хотя бы один действующий дом в разделе «Дома»."
        )
        self.no_houses_hint.setObjectName("CardSubtitle")
        targets.add_widget(self.no_houses_hint)
        self.selected_house_count = selectable_label("Выбрано домов: 0")
        targets.add_widget(self.selected_house_count)
        select_all = QPushButton("Выбрать все")
        select_all.clicked.connect(lambda: self._check_all(True))
        clear_all = QPushButton("Снять выделение")
        clear_all.clicked.connect(lambda: self._check_all(False))
        self.select_all_houses = select_all
        self.clear_all_houses = clear_all
        targets.add_widget(action_panel("Выбор домов", select_all, clear_all))

        options = ActionFlowLayout(spacing=10)
        self.create_missing = QCheckBox("Создавать недостающие отчеты")
        self.update_existing = QCheckBox("Обновлять существующие строки")
        self.override_manual = QCheckBox("Разрешить замену ручных изменений")
        self.override_manual.setToolTip(
            "Обычно строки, которые пользователь изменил вручную, пропускаются."
        )
        self.override_manual.setEnabled(False)
        self.update_existing.toggled.connect(self.override_manual.setEnabled)
        self.update_existing.toggled.connect(
            lambda active: self.override_manual.setChecked(False) if not active else None
        )
        for option in (self.create_missing, self.update_existing, self.override_manual):
            options.addWidget(option)
        targets.add_layout(options)
        root.addWidget(targets)

        # Step 4. Immutable review of exact actions, including skip reasons.
        review = ContentCard("4. Проверка перед записью",
            "После изменения условий предыдущий расчет становится недействительным.")
        self.preview_button = QPushButton("Рассчитать и проверить")
        self.preview_button.clicked.connect(self.preview)
        review.add_widget(action_panel("Предварительный просмотр",
                                       self.preview_button, primary=self.preview_button))
        self.status_label = selectable_label("Выберите условия, затем нажмите «Рассчитать и проверить».")
        self.status_label.setObjectName("CardSubtitle")
        review.add_widget(self.status_label)
        self.summary = selectable_label("")
        self.summary.setObjectName("ContextCaption")
        review.add_widget(self.summary)
        self.table = MassPreviewTable(0, len(self.PREVIEW_COLUMNS))
        self.table.setHorizontalHeaderLabels(self.PREVIEW_COLUMNS)
        fit_table_columns(self.table, flex=(0, 2, 7),
                          widths={1: 135, 3: 115, 4: 135, 5: 135, 6: 120})
        review.add_widget(self.table, 1)
        root.addWidget(review, 1)

        # Step 5. Recording is impossible without a fresh, actionable preview.
        finish = QGridLayout()
        finish.setContentsMargins(0, 0, 0, 0)
        finish.setHorizontalSpacing(8)
        finish.setVerticalSpacing(6)
        self.finish_layout = finish
        final_text = selectable_label(
            "5. Применение: записи добавляются только после проверки и подтверждения."
        )
        self.finish_caption = final_text
        finish.addWidget(final_text, 0, 0)
        finish.setColumnStretch(0, 1)
        self.apply_button = QPushButton("Подтвердить и применить")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setObjectName("PrimaryAction")
        self.apply_button.setEnabled(False)
        finish.addWidget(self.apply_button, 0, 1)
        root.addLayout(finish)

        self.mode.currentIndexChanged.connect(self.update_mode_help)
        for combo in (self.work, self.mode):
            combo.currentIndexChanged.connect(self.mark_dirty)
        self.year.valueChanged.connect(self.mark_dirty)
        for field in (self.rate, self.months, self.quantity, self.total):
            field.textChanged.connect(self.mark_dirty)
        for option in (self.create_missing, self.update_existing, self.override_manual):
            option.stateChanged.connect(self.mark_dirty)
        self.reload()
        self.update_mode_help()

    def set_compact_layout(self, compact: bool):
        """Stack calculation below source and keep apply action reachable."""
        top = self.top_layout
        top.removeWidget(self.source_card)
        top.removeWidget(self.calculation_card)
        if compact:
            top.addWidget(self.source_card, 0, 0)
            top.addWidget(self.calculation_card, 1, 0)
            top.setColumnStretch(0, 1)
            top.setColumnStretch(1, 0)
        else:
            top.addWidget(self.source_card, 0, 0)
            top.addWidget(self.calculation_card, 0, 1)
            top.setColumnStretch(0, 1)
            top.setColumnStretch(1, 1)
        finish = self.finish_layout
        finish.removeWidget(self.finish_caption)
        finish.removeWidget(self.apply_button)
        if compact:
            finish.addWidget(self.finish_caption, 0, 0, 1, 2)
            finish.addWidget(self.apply_button, 1, 0)
            finish.setColumnStretch(0, 0)
            finish.setColumnStretch(1, 1)
        else:
            finish.addWidget(self.finish_caption, 0, 0)
            finish.addWidget(self.apply_button, 0, 1)
            finish.setColumnStretch(0, 1)
            finish.setColumnStretch(1, 0)
        self.updateGeometry()

    def _company_changed(self):
        self._load_target_houses()
        self.mark_dirty()

    def _load_target_houses(self):
        cid = self.company.currentData()
        same_company = cid == self._target_company_id
        checked = {int(self.house_list.item(i).data(Qt.UserRole))
                   for i in range(self.house_list.count())
                   if self.house_list.item(i).checkState() == Qt.Checked} if same_company else set()
        self.house_list.blockSignals(True)
        self.house_list.clear()
        if cid is not None:
            houses = db.named_rows(
                "SELECT id,address,total_area FROM houses WHERE company_id=? AND status!='Архив' ORDER BY address,id",
                (cid,),
            )
            for house in houses:
                hid = int(house["id"])
                item = QListWidgetItem(
                    f'{house["address"] or "Без адреса"} · {money(house["total_area"])} м²'
                )
                item.setData(Qt.UserRole, hid)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                # Never silently select every house when switching UO.
                item.setCheckState(Qt.Checked if (same_company and hid in checked) else Qt.Unchecked)
                self.house_list.addItem(item)
        self.house_list.blockSignals(False)
        has_houses = self.house_list.count() > 0
        self.house_list.setVisible(has_houses)
        self.no_houses_hint.setVisible(not has_houses)
        self.select_all_houses.setEnabled(has_houses)
        self.clear_all_houses.setEnabled(has_houses)
        self._target_company_id = cid
        self.mark_dirty()

    def _check_all(self, value: bool):
        state = Qt.Checked if value else Qt.Unchecked
        for i in range(self.house_list.count()):
            self.house_list.item(i).setCheckState(state)
        self.mark_dirty()

    def selected_house_ids(self):
        return [int(self.house_list.item(i).data(Qt.UserRole))
                for i in range(self.house_list.count())
                if self.house_list.item(i).checkState() == Qt.Checked]

    def _inputs(self):
        return {
            "company_id": self.company.currentData(),
            "work_id": self.work.currentData(),
            "year": self.year.value(),
            "house_ids": self.selected_house_ids(),
            "mode": self.mode.currentData(),
            "rate": self.rate.text(), "months": self.months.text(),
            "quantity": self.quantity.text(), "total": self.total.text(),
            "create_missing": self.create_missing.isChecked(),
            "update_existing": self.update_existing.isChecked(),
            "override_manual": self.override_manual.isChecked(),
        }

    def mark_dirty(self, *_):
        self._preview_dirty = True
        self._preview_plan = None
        if hasattr(self, "selected_house_count"):
            self.selected_house_count.setText(
                f"Выбрано домов: {len(self.selected_house_ids())} из {self.house_list.count()}"
            )
        self.apply_button.setEnabled(False)
        self.table.setRowCount(0)
        self.summary.clear()
        self._update_preview_action()

    def _update_preview_action(self):
        try:
            validate_inputs(self._inputs())
        except ValueError as exc:
            reason = str(exc)
        else:
            reason = ""
        self.preview_button.setEnabled(not reason)
        self.preview_button.setToolTip(reason or "Рассчитать выбранные дома без записи в базу.")
        self.status_label.setText(reason or "Параметры изменены. Рассчитайте и проверьте заново.")

    def update_mode_help(self, *_):
        mode = self.mode.currentData()
        show_rate = mode in ("area", "monthly", "quantity")
        show_months = mode == "monthly"
        show_quantity = mode == "quantity"
        show_total = mode in ("fixed", "split")
        for label, widget, visible in [
            (self.rate_label, self.rate, show_rate),
            (self.months_label, self.months, show_months),
            (self.quantity_label, self.quantity, show_quantity),
            (self.total_label, self.total, show_total),
        ]:
            label.setVisible(visible)
            widget.setVisible(visible)
        explanations = {
            "area": "Ставка за весь период × площадь дома.",
            "monthly": "Ставка за 1 м² за месяц × число месяцев × площадь дома.",
            "fixed": "Одинаковая готовая сумма для каждого выбранного дома.",
            "split": "Вся сумма распределяется по площади только между домами, в которые будет записана работа.",
            "quantity": "Цена за единицу × указанное количество.",
        }
        self.help.setText(explanations.get(mode, "Укажите способ расчета."))
        self.mark_dirty()

    def reload(self):
        previous_company = self.company.currentData()
        fill_combo(self.company, "SELECT id,name FROM companies ORDER BY name", empty="Выберите УО")
        if previous_company is not None and self.company.findData(previous_company) >= 0:
            self.company.setCurrentIndex(self.company.findData(previous_company))
        old_work = self.work.currentData()
        self.work.blockSignals(True)
        self.work.clear()
        self.work.addItem("Выберите работу / услугу", None)
        for item in db.named_rows(
            "SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name,subgroup_name,category,name"
        ):
            self.work.addItem(work_display(item), item["id"])
        if old_work is not None and self.work.findData(old_work) >= 0:
            self.work.setCurrentIndex(self.work.findData(old_work))
        self.work.blockSignals(False)
        self.work._update_selected_tooltip(self.work.currentText())
        self._load_target_houses()

    def preview(self):
        self.mark_dirty()
        try:
            with connect() as conn:
                conn.execute("BEGIN")
                plan = build_mass_plan(conn, self._inputs())
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status_label.setText(f"Проверьте условия: {exc}")
            return
        self._preview_plan = plan
        self._preview_dirty = False
        for row in plan["rows"]:
            position = self.table.rowCount()
            self.table.insertRow(position)
            action_text = MASS_ACTION_TEXT[row["action"]]
            if row["action"].startswith("skip"):
                action_text = "Пропуск: " + action_text
            values = [
                row["address"], money(row["area"]),
                f'{plan["section"]} · {plan["work_name"]}',
                str(row["report_id"]) if row["report_id"] else "Нет отчета",
                money(row["cost"]) if not row["action"].startswith("skip") else "—",
                money(row["price"]) if not row["action"].startswith("skip") else "—",
                money(row["quantity"]) if not row["action"].startswith("skip") else "—",
                action_text,
            ]
            for column, value in enumerate(values):
                self.table.set_value(position, column, value)
        # Preserve the flexible widths configured at table creation.
        data = plan["summary"]
        self.summary.setText(
            f'Домов проверено: {len(plan["rows"])} · добавить: {data["insert"]} · '
            f'обновить: {data["update"]} · пропустить: {data["skip"]} · '
            f'создать отчетов: {data["create_reports"]} · '
            f'общая сумма записей: {money(data["cost"])}'
        )
        count = data["insert"] + data["update"]
        self.apply_button.setEnabled(count > 0)
        self.status_label.setText(
            "Предпросмотр готов. Проверьте суммы и причины пропуска для каждого дома."
            if count else "Нет строк, которые можно добавить или обновить. Проверьте параметры."
        )

    def apply(self):
        if self._preview_dirty or not self._preview_plan or not self.apply_button.isEnabled():
            self.status_label.setText("Сначала выполните актуальный предпросмотр.")
            return
        plan = self._preview_plan
        summary = plan["summary"]
        message = (
            f'Вы действительно хотите записать результаты?\n\n'
            f'Добавить: {summary["insert"]}\nОбновить: {summary["update"]}\n'
            f'Пропустить: {summary["skip"]}\n'
            f'Сумма добавляемых и обновляемых строк: {money(summary["cost"])}'
        )
        if plan["inputs"]["override_manual"]:
            message += "\n\nВНИМАНИЕ: разрешена перезапись вручную измененных строк."
        if QMessageBox.question(self, "Подтверждение массового заполнения", message) != QMessageBox.Yes:
            return
        try:
            # A backup taken after mass application cannot restore the
            # previous values. Require a verified pre-change snapshot.
            if make_backup("before_mass_apply") is None:
                raise RuntimeError("Не удалось создать резервную копию перед записью.")
            with connect() as conn:
                result = apply_mass_plan(conn, plan)
        except MassOutdatedPreview as exc:
            self.mark_dirty()
            self.status_label.setText(str(exc))
            self.msg(str(exc))
            return
        except Exception as exc:
            self.mark_dirty()
            self.status_label.setText(f"Запись не выполнена: {exc}")
            self.msg(f"Массовое заполнение не выполнено: {exc}")
            return
        self.mark_dirty()
        self.status_label.setText(
            f'Выполнено: добавлено {result["insert"]}, обновлено {result["update"]}, '
            f'пропущено {result["skip"]}. Для повторной операции обновите предпросмотр.'
        )
        self.msg(f'Массовое заполнение выполнено: добавлено {result["insert"]}, '
                 f'обновлено {result["update"]}, пропущено {result["skip"]}. '
                 'Резервная копия создана до записи.')
