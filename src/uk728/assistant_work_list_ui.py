"""Preview and explicit classification of Assistant's annual work-list items."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .assistant_work_list import (
    SKIP, CONTENT, REPAIR,
    apply_selected, read_preview,
)


class AssistantWorkListDialog(QDialog):
    """Nothing is written until the user confirms a full source preview."""

    def __init__(self, report_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Перечень работ из ГИС ЖКХ Ассистента")
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(980, 660)
        else:
            available = screen.availableGeometry()
            self.resize(
                min(1120, max(560, available.width() - 60)),
                min(700, max(380, available.height() - 70)),
            )
        self.preview = read_preview(report_id)
        self.import_result = None

        root = QVBoxLayout(self)
        details = QLabel(
            f"Перечень за {self.preview.year} год · период {self.preview.period} · "
            f"позиций: {len(self.preview.items)}."
        )
        details.setWordWrap(True)
        root.addWidget(details)

        notice = QLabel(
            "Отнесение работы к содержанию или текущему ремонту выбирается вручную. "
            "По умолчанию все строки пропущены. В разделе 1 графы «Выполнено» "
            "остаются нулевыми, поскольку перечень не подтверждает исполнение. "
            "В раздел 2 переносится только наименование — без выдуманных "
            "основания, стоимости, объёма или акта; такую строку необходимо "
            "заполнить или удалить до выгрузки отчёта. "
            "Количество по перечню = объём × количество в Ассистенте."
        )
        notice.setWordWrap(True)
        root.addWidget(notice)

        self.table = QTableWidget(len(self.preview.items), 7, self)
        self.table.setHorizontalHeaderLabels([
            "Код", "Наименование", "Ед. изм.", "Цена", "Кол-во по перечню",
            "Стоимость", "Раздел отчёта",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for index, width in ((0, 95), (2, 90), (3, 90), (4, 135), (5, 125), (6, 220)):
            self.table.setColumnWidth(index, width)
        self.choices = []
        missing_units, invalid, imported = self._fill_preview_table()
        root.addWidget(self.table, 1)
        count_message = (
            f"Уже перенесено: {imported}; с ошибками исходных показателей: "
            f"{invalid}; без единицы измерения: {missing_units}. "
            "Отсутствующую единицу можно заполнить в третьей колонке."
        )
        summary = QLabel(count_message)
        summary.setWordWrap(True)
        root.addWidget(summary)
        controls = QHBoxLayout()
        self.select_content = QPushButton("Все заполненные — в содержание")
        self.select_content.clicked.connect(self._select_all_content)
        controls.addWidget(self.select_content)
        self.reset = QPushButton("Снять выбор")
        self.reset.clicked.connect(self._clear_selection)
        controls.addWidget(self.reset)
        controls.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        controls.addWidget(cancel)
        self.apply_button = QPushButton("Добавить выбранные")
        self.apply_button.clicked.connect(self._apply)
        controls.addWidget(self.apply_button)
        root.addLayout(controls)
        for choice in self.choices:
            choice.currentIndexChanged.connect(self._update_apply_button)
        self._update_apply_button()

    def _fill_preview_table(self):
        missing_units = 0
        invalid = 0
        imported = 0
        for row, candidate in enumerate(self.preview.items):
            if not candidate.unit:
                missing_units += 1
            if candidate.problem:
                invalid += 1
            if candidate.already_imported:
                imported += 1
            self._add_candidate_row(row, candidate)
        return missing_units, invalid, imported

    def _add_candidate_row(self, row, candidate):
        fields = [
            candidate.code, candidate.name, candidate.unit,
            "" if candidate.unit_price is None else str(candidate.unit_price),
            "" if candidate.plan_qty is None else str(candidate.plan_qty),
            "" if candidate.plan_cost is None else str(candidate.plan_cost),
        ]
        for column, value in enumerate(fields):
            cell = QTableWidgetItem(value)
            if column != 2:
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if candidate.problem:
                cell.setToolTip(candidate.problem)
            if candidate.already_imported:
                cell.setToolTip("Эта запись уже была перенесена в отчёт.")
            self.table.setItem(row, column, cell)
        choices = QComboBox()
        choices.addItem("Пропустить", SKIP)
        choices.addItem("1. Содержание", CONTENT)
        choices.addItem("2. Текущий ремонт (черновик)", REPAIR)
        choices.setEnabled(not (candidate.problem or candidate.already_imported))
        self.table.setCellWidget(row, 6, choices)
        self.choices.append(choices)

    def _update_apply_button(self, *_args) -> None:
        self.apply_button.setEnabled(
            any(choice.isEnabled() and choice.currentData() != SKIP
                for choice in self.choices)
        )

    def _select_all_content(self) -> None:
        if QMessageBox.question(
            self, "Массовое назначение раздела",
            "Назначить все заполненные позиции разделу «Содержание»? "
            "В перечне могут быть работы текущего ремонта. "
            "Проверьте каждую строку перед подтверждением переноса."
        ) != QMessageBox.StandardButton.Yes:
            return
        self._set_all(CONTENT)

    def _clear_selection(self) -> None:
        self._set_all(SKIP)

    def _set_all(self, section: str) -> None:
        for row, choice in enumerate(self.choices):
            if not choice.isEnabled():
                continue
            # Bulk selection must not stall on rows whose unit is missing.
            # These rows remain available for explicit unit entry and selection.
            if section == CONTENT and not self.table.item(row, 2).text().strip():
                continue
            idx = choice.findData(section)
            if idx >= 0:
                choice.setCurrentIndex(idx)

    def _apply(self) -> None:
        selections = {
            candidate.item_id: (
                self.choices[row].currentData(),
                self.table.item(row, 2).text().strip(),
            )
            for row, candidate in enumerate(self.preview.items)
        }
        requested_repairs = sum(
            1 for section, _ in selections.values() if section == REPAIR
        )
        if requested_repairs:
            question = (
                "Вы выбрали перенос наименований в раздел 2 «Текущий ремонт». "
                "Перечень не подтверждает фактически выполненные работы. "
                "Сумма, основание и объём останутся незаполненными. "
                "Перед выгрузкой отчёта черновые строки потребуется заполнить "
                "по документам либо удалить.\n\nПродолжить?"
            )
            if QMessageBox.question(self, "Проверьте текущий ремонт", question) != QMessageBox.StandardButton.Yes:
                return
        try:
            result = apply_selected(self.preview, selections)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Перенос отменён", str(exc))
            return
        self.import_result = result
        QMessageBox.information(
            self, "Перенос завершён",
            f"Раздел 1: добавлено {result.added_content}. "
            f"Раздел 2 (черновики): {result.added_repair}. "
            f"Пропущено повторов: {result.skipped}. "
            "Другие разделы отчёта не изменены."
        )
        self.accept()
