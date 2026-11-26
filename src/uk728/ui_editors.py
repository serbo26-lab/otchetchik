"""Shared widgets, form state, and editor helpers for the reporting workspace.

No application-shell or page imports: Qt callbacks and save hooks retain their
original behavior. The public names remain available through app.py.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QSizePolicy, QStackedWidget, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QStyledItemDelegate, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import db
from .ui_components import ContentCard


def selectable_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    label.setWordWrap(True)
    return label




def polish_form(form: QFormLayout, *, label_width: int = 165) -> None:
    """Keep combobox fields wide and let form rows wrap on narrow screens."""
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(9)
    for row in range(form.rowCount()):
        entry = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        label = entry.widget() if entry is not None else None
        if isinstance(label, QLabel):
            title = label.text().strip()
            label.setMinimumWidth(min(label_width, 165))
            label.setMaximumWidth(max(label_width + 65, 245))
            label.setWordWrap(len(title) > 43)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setToolTip(title)
        field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        field = field_item.widget() if field_item is not None else None
        if isinstance(field, QComboBox):
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            field.setMinimumWidth(160)
            field.setMaximumWidth(16777215)
        elif isinstance(field, QLineEdit):
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def empty_record_editor(
    form_widget: QWidget,
    title: str,
    description: str,
    add_handler,
    button_title: str = "Добавить запись",
) -> QStackedWidget:
    """Replace a wall of disabled controls by one explicit first action."""
    stack = QStackedWidget()
    empty = QWidget()
    column = QVBoxLayout(empty)
    card = ContentCard(title, description)
    button = QPushButton(button_title)
    button.setObjectName("PrimaryAction")
    button.clicked.connect(add_handler)
    card.add_widget(button)
    column.addWidget(card)
    column.addStretch(1)
    stack.addWidget(empty)
    stack.addWidget(form_scroll(form_widget))
    stack.setCurrentIndex(0)
    stack.empty_add_button = button
    return stack


SECTION_CONTENT = "Содержание"
SECTION_REPAIR = "Текущий ремонт"
SECTION_MANAGEMENT = "Управление"
DELETE_ROWS_TITLE = "Удаление строк"
DELETE_ROWS_TEXT = "Удалить выбранные строки"
ADD_ROW_TEXT = "Добавить строку"

COMMON_SECTIONS = [SECTION_CONTENT, SECTION_REPAIR, SECTION_MANAGEMENT]
COMMON_UNITS = ["усл.", "шт.", "м²", "м³", "п.м.", "мес.", "год", "работа", "комплект", "акт", "раз"]
COMMON_FILL_MODES = [
    "Цена × количество",
    "Цена за 1 м² площади дома",
    "Цена за 1 м² в месяц",
    "Готовая сумма на каждый дом",
    "Общая сумма по выбранным домам, распределить по площади",
]
COMMON_PAYER_TYPES = ["Собственники", "Наниматели"]
COMMON_REPAIR_BASIS = ["Решение ОСС", "План текущего ремонта", "Акт осмотра", "Предписание", "Договор управления", "Заявка", "Иное"]


def numeric_error(value: Any, label: str, *, whole: bool = False) -> str | None:
    """Validate before to_float: invalid text must never silently become zero."""
    raw = str(value or "").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not raw:
        return None  # Blank is allowed; zero remains an explicit numeric value.
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return f"«{label}»: укажите число, например 1200 или 1 200,50."
    if not amount.is_finite() or not float("-inf") < float(amount) < float("inf"):
        return f"«{label}»: некорректное числовое значение."
    if whole and amount > Decimal(2**53 - 1):
        return f"«{label}»: слишком большое количество."
    if whole and (amount < 0 or amount != amount.to_integral_value()):
        return f"«{label}»: требуется целое неотрицательное количество."
    return None


def distinct_texts(table: str, column: str, extra: list[str] | None = None) -> list[str]:
    vals: list[str] = []
    seen: set[str] = set()
    for v in extra or []:
        t = str(v).strip()
        if t and t.lower() not in seen:
            vals.append(t); seen.add(t.lower())
    try:
        for r in db.rows(f"SELECT DISTINCT {column} AS v FROM {table} WHERE TRIM(COALESCE({column},''))<>'' ORDER BY {column}"):
            # The query returns exactly one column.
            t = str(r[0]).strip()
            if t and t.lower() not in seen:
                vals.append(t); seen.add(t.lower())
    except Exception:
        pass
    return vals


class ClickComboBox(QComboBox):
    """Комбобокс для обычного UX: список открывается кликом по любому месту поля.

    Важно: поле остается редактируемым там, где это нужно. Пользователь может
    выбрать старое значение из списка или ввести новое вручную.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumHeight(28)
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(13)
        self.currentTextChanged.connect(self._update_selected_tooltip)

    def _update_selected_tooltip(self, value: str):
        self.setToolTip(value or "Нажмите, чтобы выбрать значение")

    def setEditable(self, editable: bool) -> None:
        super().setEditable(editable)
        le = self.lineEdit()
        if le is not None:
            le.installEventFilter(self)
            le.setContextMenuPolicy(Qt.DefaultContextMenu)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self.isEnabled():
            QTimer.singleShot(0, self.showPopup)

    def wheelEvent(self, event):
        # Scrolling a form must not accidentally change its selected value.
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.MouseButtonPress and self.isEnabled():
            QTimer.singleShot(0, self.showPopup)
        return super().eventFilter(obj, event)


class SafeSpinBox(QSpinBox):
    """Prevent accidental year changes while the user scrolls a form."""

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class EditableComboDelegate(QStyledItemDelegate):
    """Редактор ячейки как выпадающий список, но без жесткого ограничения.
    Пользователь может выбрать старое значение или набрать любое новое.
    """
    def __init__(self, values_func, parent=None):
        super().__init__(parent)
        self.values_func = values_func

    def createEditor(self, parent, option, index):
        combo = ClickComboBox(parent)
        combo.setEditable(True)
        combo.addItems(list(self.values_func()))
        combo.setInsertPolicy(QComboBox.InsertAtTop)
        QTimer.singleShot(0, combo.showPopup)
        return combo

    def setEditorData(self, editor, index):
        text = index.model().data(index, Qt.EditRole) or ""
        editor.setCurrentText(str(text))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)


class CopyPasteTable(QTableWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
        self.setTextElideMode(Qt.ElideRight)
        self.setSelectionMode(QTableWidget.ExtendedSelection)
        self.setSelectionBehavior(QTableWidget.SelectItems)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setMinimumSectionSize(65)
        self.horizontalHeader().setStretchLastSection(False)
        self.verticalHeader().setDefaultSectionSize(31)
        self.verticalHeader().setVisible(False)
        self.itemChanged.connect(self._sync_cell_tooltip)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        # A report row needs its own initializer and autosave after deletion.
        # Keep generic behavior for standalone directories and editing tables.
        self.context_add_handler = None
        self.context_delete_handler = None
        self.allow_paste_append = False
        self.protected_columns: set[int] = set()

    def _sync_cell_tooltip(self, item):
        if item and item.toolTip() != item.text():
            blocked = self.blockSignals(True)
            item.setToolTip(item.text())
            self.blockSignals(blocked)

    def mousePressEvent(self, event):
        # Для ячеек со справочниками открываем выпадающий список сразу по одному клику,
        # а не только при попадании в маленькую стрелку редактора.
        idx = self.indexAt(event.position().toPoint() if hasattr(event, "position") else event.pos())
        super().mousePressEvent(event)
        if idx.isValid() and isinstance(self.itemDelegateForColumn(idx.column()), EditableComboDelegate):
            self.setCurrentCell(idx.row(), idx.column())
            QTimer.singleShot(0, lambda ix=idx: self.edit(ix))

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        if event.matches(QKeySequence.Paste):
            self.paste_selection()
            return
        super().keyPressEvent(event)

    def _menu(self, pos):
        menu = QMenu(self)
        act_copy = QAction("Копировать", self)
        act_copy.triggered.connect(self.copy_selection)
        menu.addAction(act_copy)
        act_paste = QAction("Вставить", self)
        act_paste.triggered.connect(self.paste_selection)
        menu.addAction(act_paste)
        act_add = QAction(ADD_ROW_TEXT, self)
        act_add.triggered.connect(
            self.context_add_handler if self.context_add_handler is not None
            else lambda: self.insertRow(self.rowCount())
        )
        menu.addAction(act_add)
        act_del = QAction(DELETE_ROWS_TEXT, self)
        act_del.triggered.connect(
            self.context_delete_handler if self.context_delete_handler is not None
            else self.delete_selected_rows
        )
        menu.addAction(act_del)
        menu.exec(self.viewport().mapToGlobal(pos))

    def copy_selection(self):
        ranges = self.selectedRanges()
        if not ranges:
            item = self.currentItem()
            if item:
                QGuiApplication.clipboard().setText(item.text())
            return
        r = ranges[0]
        lines = []
        for row in range(r.topRow(), r.bottomRow() + 1):
            vals = []
            for col in range(r.leftColumn(), r.rightColumn() + 1):
                item = self.item(row, col)
                vals.append(item.text() if item else "")
            lines.append("\t".join(vals))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def paste_selection(self):
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        lines = text.splitlines()
        if len(lines) > 1000:
            QMessageBox.warning(self, "Вставка таблицы",
                                "За один раз можно вставить не более 1000 строк.")
            return
        start_row = self.currentRow() if self.currentRow() >= 0 else 0
        start_col = self.currentColumn() if self.currentColumn() >= 0 else 0
        for offset, line in enumerate(lines):
            row = start_row + offset
            if not self._prepare_paste_row(row):
                break
            self._paste_line(row, start_col, line)

    def _prepare_paste_row(self, row):
        if row < self.rowCount():
            return True
        if not self.allow_paste_append:
            return False  # Fixed legal tables cannot gain arbitrary payers.
        before = self.rowCount()
        if self.context_add_handler is not None:
            self.context_add_handler()  # Creates business metadata/defaults.
        else:
            self.insertRow(self.rowCount())
        return self.rowCount() > before

    def _paste_line(self, row, start_col, line):
        for shift, value in enumerate(line.split("\t")):
            col = start_col + shift
            if col >= self.columnCount():
                break
            if self.isColumnHidden(col) or col in self.protected_columns:
                continue
            self.setItem(row, col, QTableWidgetItem(value))

    def delete_selected_rows(self, confirmed: bool = False):
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True)
        if not rows:
            return False
        if not confirmed and QMessageBox.question(
            self, DELETE_ROWS_TITLE,
            f"Удалить выбранные строки ({len(rows)})? Изменения будут сохранены при следующем сохранении формы.",
        ) != QMessageBox.Yes:
            return False
        for row in rows:
            self.removeRow(row)
        return True

    def set_value(self, row: int, col: int, value: Any):
        text = "" if value is None else str(value)
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        self.setItem(row, col, item)

    def value(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text().strip() if item else ""


class EditorConflictError(ValueError):
    """The stored record differs from the editor's loaded snapshot."""


class BaseTab(QWidget):
    def _form_signature(self):
        """Non-invasive snapshot of a form; used to detect edits before changing records."""
        widgets = list(getattr(self, "form_widgets", []))
        if self.__class__.__name__ == "TemplatesTab":
            widgets.append(self.table)
        snapshot = []
        for widget in widgets:
            if isinstance(widget, QLineEdit):
                snapshot.append(("line", widget.text()))
            elif isinstance(widget, QTextEdit):
                snapshot.append(("text", widget.toPlainText()))
            elif isinstance(widget, QComboBox):
                snapshot.append(("combo", widget.currentText(), widget.currentData()))
            elif isinstance(widget, QTableWidget):
                snapshot.append(("table", tuple(
                    tuple(widget.value(r, c) for c in range(widget.columnCount()))
                    for r in range(widget.rowCount())
                )))
        return tuple(snapshot)

    def _remember_form_state(self):
        self._loaded_form_id = getattr(self, "current_id", None)
        self._form_snapshot = self._form_signature()

    def has_unsaved_changes(self):
        snapshot = getattr(self, "_form_snapshot", None)
        return snapshot is not None and self._form_signature() != snapshot

    def _commit_form_before_load(self):
        """Persist the *previous* record before selection, refresh or reload replaces it."""
        old_id = getattr(self, "_loaded_form_id", None)
        if old_id is None or self._form_signature() == getattr(self, "_form_snapshot", None):
            return True
        selected_id = getattr(self, "current_id", None)
        self.current_id = old_id
        try:
            if self.save(refresh_after=False) is False:
                raise ValueError("Исправьте ошибки перед переключением записи.")
        except Exception as exc:
            # Keep old values visible and revert list selection after any write failure.
            self.current_id = old_id
            choices = getattr(self, "list", None)
            if choices is not None:
                blocked = choices.blockSignals(True)
                for row in range(choices.count()):
                    if choices.item(row).data(Qt.UserRole) == old_id:
                        choices.setCurrentRow(row)
                        break
                choices.blockSignals(blocked)
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            self.msg(f"Не удалось сохранить предыдущую запись: {exc}")
            return False
        self.current_id = selected_id
        return True

    def _load_selected_record(self, populate):
        """Only a fully populated editor may remain bound to a selected record."""
        item = self.list.currentItem()
        next_id = item.data(Qt.UserRole) if item else None
        if not self._commit_form_before_load():
            return
        try:
            populate(next_id)
        except Exception as exc:
            self.current_id = None
            self._clear_record_selection()
            self.update_form_state()
            self._remember_form_state()
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            QMessageBox.warning(self, "Не удалось открыть запись", str(exc))
            return
        if next_id is not None and self.current_id is None:
            self._clear_record_selection()

    def _handle_save_error(self, title, exc):
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("error")
        if isinstance(exc, EditorConflictError) and getattr(self, "_manual_save_requested", False):
            self._manual_conflict = str(exc)
        else:
            QMessageBox.warning(self, title, str(exc))
        return False

    def save_manually(self, *_args):
        """Only an explicit Save click may offer to discard conflicting edits."""
        self._manual_conflict = None
        self._manual_save_requested = True
        try:
            result = self.save()
        finally:
            self._manual_save_requested = False
        if result is False and self._manual_conflict:
            self._offer_conflict_reload()
        return result

    def _offer_conflict_reload(self):
        answer = QMessageBox.question(
            self, "Данные изменились",
            f"{self._manual_conflict}\n\nЗагрузить актуальные данные? "
            "Несохраненные изменения будут отброшены. "
            "Выберите «Нет», чтобы оставить их в редакторе.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        previous = getattr(self, "_form_snapshot", None)
        self._remember_form_state()
        try:
            self.refresh()
        except Exception as exc:
            self._form_snapshot = previous
            self._handle_save_error("Не удалось обновить данные", exc)
            return
        win = self.window()
        if hasattr(win, "set_save_status") and not self.has_unsaved_changes():
            win.set_save_status("saved")

    def _clear_record_selection(self):
        blocked = self.list.blockSignals(True)
        try:
            self.list.setCurrentRow(-1)
        finally:
            self.list.blockSignals(blocked)

    def save(self, *_args, refresh_after: bool = True) -> bool | None:
        # Intentional default hook: tabs without editable form data may
        # inherit this. Data-editing tabs override it. Keep a uniform
        # refresh_after interface for _commit_form_before_load().
        return None

    def refresh(self) -> None:
        # Intentional no-op: not every tab has a refreshable data list.
        return None

    def msg(self, text: str) -> None:
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(text, 5000)
            if hasattr(win, 'notification_received'):
                win.notification_received(text)


def form_scroll(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    return scroll


def restore_combo_selection(combo: QComboBox, old_data: Any = None, old_text: str = "") -> None:
    """Вернуть выбранное значение после обновления списка.
    Это важно для UX: при переходе между вкладками выбранный дом/шаблон/работа
    не должны слетать, если запись всё еще существует в списке.
    """
    idx = -1
    if old_data is not None:
        idx = combo.findData(old_data)
    if idx < 0 and old_text:
        idx = combo.findText(old_text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    elif combo.count() > 0:
        combo.setCurrentIndex(0)




def row_get(row: Any, key: str, default: Any = "") -> Any:
    """Безопасно читает значение из dict или sqlite3.Row.

    sqlite3.Row похож на словарь, но у него нет метода .get(). Из-за этого
    массовое заполнение падало при предпросмотре/применении.
    """
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def normalize_section(value: Any, default: str = SECTION_CONTENT) -> str:
    raw = str(value or "").strip()
    low = raw.lower()
    if "управ" in low:
        return SECTION_MANAGEMENT
    if "ремонт" in low:
        return SECTION_REPAIR
    if "содерж" in low:
        return SECTION_CONTENT
    return raw if raw in COMMON_SECTIONS else default


def work_display(row: Any) -> str:
    parts = []
    group = str(row_get(row, "group_name", "") or "").strip()
    subgroup = str(row_get(row, "subgroup_name", "") or "").strip()
    name = str(row_get(row, "name", "") or "").strip()
    section = str(row_get(row, "category", "") or "").strip()
    if group:
        parts.append(group)
    if subgroup and subgroup.lower() != name.lower():
        parts.append(subgroup)
    base = " / ".join(parts)
    if base:
        return f"{section} · {base} · {name}"
    return f"{section} · {name}"

def fill_combo(combo: QComboBox, sql: str, params: tuple = (), empty: str | None = None, preserve: bool = True):
    old_data = combo.currentData() if preserve else None
    old_text = combo.currentText() if preserve else ""
    combo.blockSignals(True)
    combo.clear()
    if empty is not None:
        combo.addItem(empty, None)
    for r in db.rows(sql, params):
        fields = dict(r)
        label = fields["name"] if "name" in fields else r[1]
        combo.addItem(str(label), fields["id"])
    if preserve:
        restore_combo_selection(combo, old_data, old_text)
    combo.blockSignals(False)
    if isinstance(combo, ClickComboBox):
        combo._update_selected_tooltip(combo.currentText())


def set_widgets_enabled(widgets: list[QWidget], enabled: bool) -> None:
    for widget in widgets:
        widget.setEnabled(enabled)


def clear_widgets(widgets: list[QWidget]) -> None:
    for widget in widgets:
        if isinstance(widget, QLineEdit):
            widget.clear()
        elif isinstance(widget, QTextEdit):
            widget.clear()
        elif isinstance(widget, QComboBox):
            if widget.count() > 0:
                widget.setCurrentIndex(0)
        elif isinstance(widget, QSpinBox):
            # Спинбоксы обычно имеют осмысленное значение по умолчанию.
            pass
