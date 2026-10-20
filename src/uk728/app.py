from __future__ import annotations

import sys
import traceback
from typing import Any

from PySide6.QtCore import Qt, QTimer, QEvent, QUrl, QSettings
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QFrame,
    QStackedWidget,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import db
from .db import connect
from .exporters import export_docx, export_pdf, export_xlsx
from .paths import data_dir, make_backup
from .utils import full_year_period, iso_date, money, to_float
from .ui_style import apply_theme
from .ui_shell import ActionFlowLayout, NavigationSidebar, PageChrome, PAGES
from .report_storage import sync_report_rows
from .mass_apply_logic import (ACTION_TEXT as MASS_ACTION_TEXT, OutdatedPreview as MassOutdatedPreview,
                               build_plan as build_mass_plan, apply_plan as apply_mass_plan)
from .ui_components import ActionPanel, ContentCard, action_panel, describe_context, table_card

APP_TITLE = "Отчетность УК по приказу 728/пр — V1.6.4 MASS WORKFLOW"


def selectable_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    label.setWordWrap(True)
    return label




COMMON_SECTIONS = ["Содержание", "Текущий ремонт", "Управление"]
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


def distinct_texts(table: str, column: str, extra: list[str] | None = None) -> list[str]:
    vals: list[str] = []
    seen: set[str] = set()
    for v in extra or []:
        t = str(v).strip()
        if t and t.lower() not in seen:
            vals.append(t); seen.add(t.lower())
    try:
        for r in db.rows(f"SELECT DISTINCT {column} AS v FROM {table} WHERE TRIM(COALESCE({column},''))<>'' ORDER BY {column}"):
            t = str(r["v"]).strip()
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
        act_add = QAction("Добавить строку", self)
        act_add.triggered.connect(lambda: self.insertRow(self.rowCount()))
        menu.addAction(act_add)
        act_del = QAction("Удалить выбранные строки", self)
        act_del.triggered.connect(self.delete_selected_rows)
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
        start_row = self.currentRow() if self.currentRow() >= 0 else 0
        start_col = self.currentColumn() if self.currentColumn() >= 0 else 0
        for r, line in enumerate(text.splitlines()):
            row = start_row + r
            if row >= self.rowCount():
                self.insertRow(row)
            for c, value in enumerate(line.split("\t")):
                col = start_col + c
                if col >= self.columnCount():
                    continue
                if self.isColumnHidden(col):
                    continue
                self.setItem(row, col, QTableWidgetItem(value))

    def delete_selected_rows(self):
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True)
        for row in rows:
            self.removeRow(row)

    def set_value(self, row: int, col: int, value: Any):
        text = "" if value is None else str(value)
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        self.setItem(row, col, item)

    def value(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text().strip() if item else ""


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

    def _commit_form_before_load(self, new_id):
        """Persist the *previous* record before selection, refresh or reload replaces it."""
        old_id = getattr(self, "_loaded_form_id", None)
        if old_id is None or self._form_signature() == getattr(self, "_form_snapshot", None):
            return True
        selected_id = getattr(self, "current_id", None)
        self.current_id = old_id
        try:
            self.save(refresh_after=False)
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

    def save(self) -> None:
        pass

    def refresh(self) -> None:
        pass

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


def normalize_section(value: Any, default: str = "Содержание") -> str:
    raw = str(value or "").strip()
    low = raw.lower()
    if "управ" in low:
        return "Управление"
    if "ремонт" in low:
        return "Текущий ремонт"
    if "содерж" in low:
        return "Содержание"
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
        combo.addItem(str(r["name"] if "name" in r.keys() else r[1]), r["id"])
    if preserve:
        restore_combo_selection(combo, old_data, old_text)
    combo.blockSignals(False)



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


class CompaniesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        root = QHBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter)
        left = QWidget()
        left_l = QVBoxLayout(left)
        selector = ContentCard("1. Выбор организации", "Добавьте УО или выберите ее из перечня.")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        selector.add_widget(self.list, 1)
        self.add_button = QPushButton("Добавить организацию")
        self.add_button.clicked.connect(self.add_company)
        selector.add_widget(action_panel("Создание", self.add_button, primary=self.add_button))
        left_l.addWidget(selector, 1)
        self.delete_button = QPushButton("Удалить организацию")
        self.delete_button.clicked.connect(self.delete_company)
        left_l.addWidget(action_panel("Удаление выбранной УО", self.delete_button, purpose="danger"))
        splitter.addWidget(left)

        formw = QWidget()
        form_layout = QVBoxLayout(formw)
        self.name = QLineEdit()
        self.ogrn = QLineEdit()
        self.addr = QTextEdit(); self.addr.setFixedHeight(70)
        self.person = QLineEdit()
        self.contact = QLineEdit()
        self.notes = QTextEdit(); self.notes.setFixedHeight(80)
        self.form_widgets = [self.name, self.ogrn, self.addr, self.person, self.contact, self.notes]
        basic = ContentCard("2. Основные сведения")
        basic_form = QFormLayout()
        basic_form.addRow(selectable_label("Полное наименование"), self.name)
        basic_form.addRow(selectable_label("ОГРН / ИНН"), self.ogrn)
        basic.add_layout(basic_form)
        form_layout.addWidget(basic)

        report_contact = ContentCard("3. Сведения для разъяснений по отчету")
        contact_form = QFormLayout()
        for title, widget in [("Адрес приема населения", self.addr),
                              ("Кто дает разъяснения по отчету", self.person),
                              ("Телефон / email", self.contact)]:
            contact_form.addRow(selectable_label(title), widget)
        report_contact.add_layout(contact_form)
        form_layout.addWidget(report_contact)

        extra = ContentCard("4. Примечания")
        extra_form = QFormLayout()
        extra_form.addRow(selectable_label("Примечание"), self.notes)
        extra.add_layout(extra_form)
        form_layout.addWidget(extra)

        self.save_button = QPushButton("Сохранить организацию")
        self.save_button.clicked.connect(self.save)
        form_layout.addWidget(action_panel("Сохранение сведений", self.save_button, primary=self.save_button))
        form_layout.addStretch()
        splitter.addWidget(form_scroll(formw))
        splitter.setSizes([260, 700])
        self.refresh()
        self.update_form_state()

    def _clear_form(self):
        clear_widgets(self.form_widgets)

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.save_button], has_record)
        self.delete_button.setEnabled(has_record)
        if not has_record:
            self._clear_form()

    def refresh(self):
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        select_row = -1
        for r in db.rows("SELECT * FROM companies ORDER BY name, id"):
            item = QListWidgetItem(r["name"] or "Без названия")
            item.setData(Qt.UserRole, r["id"])
            self.list.addItem(item)
            if target_id is not None and r["id"] == target_id:
                select_row = self.list.count() - 1
        self.list.blockSignals(False)
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
        elif self.list.count() and target_id is None:
            self.list.setCurrentRow(0)

    def _load_current(self):
        item = self.list.currentItem()
        next_id = item.data(Qt.UserRole) if item else None
        if not self._commit_form_before_load(next_id):
            return
        self.current_id = next_id
        r = db.row("SELECT * FROM companies WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
            self._remember_form_state()
            return
        self.update_form_state()
        self.name.setText(r["name"])
        self.ogrn.setText(r["ogrn_inn"])
        self.addr.setPlainText(r["reception_address"])
        self.person.setText(r["report_person"])
        self.contact.setText(r["report_contact"])
        self.notes.setPlainText(r["notes"])
        self._remember_form_state()

    def add_company(self):
        if self.current_id:
            self.save()
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Новая управляющая организация",))
        self.current_id = cid
        self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == cid:
                self.list.setCurrentRow(i); break

    def delete_company(self):
        if not self.current_id:
            return
        if QMessageBox.question(self, "Удалить?", "Удалить выбранную организацию? Дома и отчеты останутся, но привязка может быть очищена.") == QMessageBox.Yes:
            db.execute("DELETE FROM companies WHERE id=?", (self.current_id,))
            self.current_id = None
            self.refresh()
            self.update_form_state()

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить организацию», затем заполните поля и сохраните.")
            return
        with connect() as conn:
            conn.execute(
                "UPDATE companies SET name=?, ogrn_inn=?, reception_address=?, report_person=?, report_contact=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.name.text(), self.ogrn.text(), self.addr.toPlainText(), self.person.text(), self.contact.text(), self.notes.toPlainText(), self.current_id),
            )
            conn.commit()
        self._remember_form_state()
        self.msg("Организация сохранена")
        if refresh_after:
            self.refresh()


class HousesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        root = QHBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        organization = ContentCard("1. Управляющая организация")
        self.company_filter = ClickComboBox()
        self.company_filter.currentIndexChanged.connect(self._on_company_filter_changed)
        organization.add_widget(self.company_filter)
        left_l.addWidget(organization)
        houses = ContentCard("2. Перечень домов", "Сначала выберите УО, затем дом.")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        houses.add_widget(self.list, 1)
        self.add_button = QPushButton("Добавить дом")
        self.add_button.clicked.connect(self.add_house)
        houses.add_widget(action_panel("Создание", self.add_button, primary=self.add_button))
        left_l.addWidget(houses, 1)
        self.delete_button = QPushButton("Удалить дом")
        self.delete_button.clicked.connect(self.delete_house)
        left_l.addWidget(action_panel("Удаление выбранного дома", self.delete_button, purpose="danger"))
        splitter.addWidget(left)

        formw = QWidget(); form_l = QVBoxLayout(formw)
        self.company = ClickComboBox()
        self.address = QTextEdit(); self.address.setFixedHeight(70)
        self.area = QLineEdit()
        self.start = QLineEdit(); self.start.setPlaceholderText("дд.мм.гггг или оставить пустым")
        self.end = QLineEdit(); self.end.setPlaceholderText("дд.мм.гггг или оставить пустым")
        self.status = ClickComboBox(); self.status.addItems(["В управлении", "Выбыл", "Архив"])
        self.notes = QTextEdit(); self.notes.setFixedHeight(90)
        self.form_widgets = [self.company, self.address, self.area, self.start, self.end, self.status, self.notes]

        official = ContentCard("3. Сведения о доме", "Площадь — сумма площадей жилых и нежилых помещений без общего имущества.")
        official_form = QFormLayout()
        for label, widget in [("Управляющая организация", self.company),
                              ("Адрес дома", self.address),
                              ("Площадь помещений, м²", self.area)]:
            official_form.addRow(selectable_label(label), widget)
        official.add_layout(official_form)
        form_l.addWidget(official)

        management = ContentCard("4. Период управления")
        management_form = QFormLayout()
        for label, widget in [("Дата начала управления", self.start),
                              ("Дата прекращения управления", self.end),
                              ("Статус", self.status)]:
            management_form.addRow(selectable_label(label), widget)
        management.add_layout(management_form)
        form_l.addWidget(management)

        other = ContentCard("5. Дополнительно")
        extra_form = QFormLayout()
        extra_form.addRow(selectable_label("Примечание"), self.notes)
        other.add_layout(extra_form)
        form_l.addWidget(other)

        self.save_button = QPushButton("Сохранить дом"); self.save_button.clicked.connect(self.save)
        form_l.addWidget(action_panel("Сохранение карточки", self.save_button, primary=self.save_button))
        splitter.addWidget(form_scroll(formw))
        splitter.setSizes([340, 700])
        self.reload_companies()
        self.refresh()
        self.update_form_state()

    def _clear_form(self):
        clear_widgets(self.form_widgets)
        self.address.setPlainText("")
        self.notes.setPlainText("")

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.save_button], has_record)
        self.delete_button.setEnabled(has_record)
        if not has_record:
            self._clear_form()

    def _on_company_filter_changed(self):
        previous = getattr(self, "_selected_company_filter_id", None)
        if not self._commit_form_before_load(None):
            self.company_filter.blockSignals(True)
            self.company_filter.setCurrentIndex(max(0, self.company_filter.findData(previous)))
            self.company_filter.blockSignals(False)
            return
        self._selected_company_filter_id = self.company_filter.currentData()
        self.current_id = None
        self.refresh()

    def reload_companies(self):
        old_filter = self.company_filter.currentData()
        companies = db.rows("SELECT id, name FROM companies ORDER BY name")
        self.company_filter.blockSignals(True)
        self.company_filter.clear()
        if len(companies) == 1:
            r = companies[0]
            self.company_filter.addItem(str(r["name"] or "Без названия"), r["id"])
            self.company_filter.setCurrentIndex(0)
        else:
            self.company_filter.addItem("Выберите организацию", None)
            for r in companies:
                self.company_filter.addItem(str(r["name"] or "Без названия"), r["id"])
            if old_filter is not None:
                idx = self.company_filter.findData(old_filter)
                if idx >= 0:
                    self.company_filter.setCurrentIndex(idx)
        self.company_filter.blockSignals(False)
        self._selected_company_filter_id = self.company_filter.currentData()
        fill_combo(self.company, "SELECT id, name FROM companies ORDER BY name", empty="Не выбрано")
        if hasattr(self, "add_button"):
            self.add_button.setEnabled(self.company_filter.currentData() is not None)

    def reload(self):
        # Обновляет список УО после изменений на вкладке "Управляющие организации",
        # но сохраняет текущий выбранный дом и фильтр.
        self.reload_companies()
        self.refresh()

    def refresh(self):
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        cid = self.company_filter.currentData()
        if hasattr(self, "add_button"):
            self.add_button.setEnabled(cid is not None)
        if cid:
            result = db.rows("SELECT * FROM houses WHERE company_id=? ORDER BY address", (cid,))
        else:
            result = []
        select_row = -1
        for r in result:
            item = QListWidgetItem(f"{r['address'] or 'Без адреса'} · {money(r['total_area'])} м²")
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
            self.update_form_state()
            self._remember_form_state()

    def _load_current(self):
        item = self.list.currentItem()
        next_id = item.data(Qt.UserRole) if item else None
        if not self._commit_form_before_load(next_id):
            return
        self.current_id = next_id
        r = db.row("SELECT * FROM houses WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
            self._remember_form_state()
            return
        self.update_form_state()
        idx = self.company.findData(r["company_id"])
        self.company.setCurrentIndex(max(0, idx))
        self.address.setPlainText(r["address"])
        self.area.setText(str(r["total_area"] or ""))
        self.start.setText(r["management_start"])
        self.end.setText(r["management_end"])
        self.status.setCurrentText(r["status"])
        self.notes.setPlainText(r["notes"])
        self._remember_form_state()

    def add_house(self):
        if self.current_id:
            self.save()
        cid = self.company_filter.currentData()
        if not cid:
            self.msg("Выберите управляющую организацию, затем нажмите «Добавить дом».")
            return
        hid = db.execute("INSERT INTO houses(company_id, address) VALUES(?,?)", (cid, "Новый дом"))
        self.current_id = hid
        self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == hid:
                self.list.setCurrentRow(i); break

    def delete_house(self):
        if not self.current_id:
            return
        if QMessageBox.question(self, "Удалить?", "Удалить выбранный дом? Отчеты по нему также могут быть удалены.") == QMessageBox.Yes:
            db.execute("DELETE FROM houses WHERE id=?", (self.current_id,))
            self.current_id = None
            self.refresh()
            self.update_form_state()

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Сначала выберите или добавьте дом, затем заполните поля и сохраните.")
            return
        with connect() as conn:
            conn.execute(
                "UPDATE houses SET company_id=?, address=?, total_area=?, management_start=?, management_end=?, status=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.company.currentData(), self.address.toPlainText(), to_float(self.area.text()), self.start.text(), self.end.text(), self.status.currentText(), self.notes.toPlainText(), self.current_id),
            )
            conn.commit()
        self._remember_form_state()
        self.msg("Дом сохранен")
        if refresh_after:
            self.refresh()



class CategoriesTab(BaseTab):
    """Справочник категорий и подкатегорий работ.

    Это не официальный раздел отчета, а удобный пользовательский классификатор:
    пользователь заранее заводит категорию/подкатегории с типовыми настройками,
    а потом быстро добавляет их в справочник работ.
    """
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        root = QHBoxLayout(self)
        splitter = QSplitter(); root.addWidget(splitter)

        left = QWidget(); left_l = QVBoxLayout(left)
        selection = ContentCard("1. Выберите категорию")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        selection.add_widget(self.list, 1)
        add = QPushButton("Добавить категорию")
        add.clicked.connect(self.add_category)
        selection.add_widget(action_panel("Создание категории", add, primary=add))
        left_l.addWidget(selection, 1)
        self.delete_button = QPushButton("Удалить категорию")
        self.delete_button.clicked.connect(self.delete_category)
        left_l.addWidget(action_panel("Удаление выбранной категории", self.delete_button, purpose="danger"))
        splitter.addWidget(left)

        right = QWidget(); right_l = QVBoxLayout(right)
        details = ContentCard("2. Параметры категории",
            "Раздел формы определяет место новых работ в отчете. Примечание не влияет на расчет.")
        form = QFormLayout()
        self.name = QLineEdit()
        self.section = ClickComboBox(); self.section.setEditable(True); self.section.addItems(["Не выбрано", *COMMON_SECTIONS])
        self.unit = ClickComboBox(); self.unit.setEditable(True); self.unit.addItems(["Не выбрано", *COMMON_UNITS])
        self.price = QLineEdit()
        self.notes = QTextEdit(); self.notes.setFixedHeight(55)
        form.addRow(selectable_label("Название категории"), self.name)
        form.addRow(selectable_label("Раздел формы"), self.section)
        form.addRow(selectable_label("Единица измерения"), self.unit)
        form.addRow(selectable_label("Цена / ставка"), self.price)
        form.addRow(selectable_label("Примечание"), self.notes)
        details.add_layout(form)
        right_l.addWidget(details)

        # Добавление подкатегории относится к ее таблице, а не к категории в целом.
        self.sub_table = CopyPasteTable(0, 6)
        self.sub_table.setHorizontalHeaderLabels(["ID", "Название подкатегории", "Раздел", "Ед. изм.", "Цена/ставка", "Примечание"])
        self.sub_table.setColumnHidden(0, True)
        self.sub_table.setItemDelegateForColumn(2, EditableComboDelegate(lambda: distinct_texts("work_categories", "section", ["Не выбрано", *COMMON_SECTIONS]), self.sub_table))
        self.sub_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("work_categories", "unit", ["Не выбрано", *COMMON_UNITS]), self.sub_table))
        subcategories = table_card("3. Подкатегории", self.sub_table,
                                   subtitle="Сначала добавьте строку, затем заполните ее поля.")
        self.add_sub_button = QPushButton("Добавить подкатегорию")
        self.add_sub_button.clicked.connect(self.add_subcategory)
        subcategories.add_widget(action_panel("Добавление", self.add_sub_button, primary=self.add_sub_button))
        right_l.addWidget(subcategories, 1)
        self.delete_sub_button = QPushButton("Удалить выбранные подкатегории")
        self.delete_sub_button.clicked.connect(self.sub_table.delete_selected_rows)
        self.save_button = QPushButton("Сохранить категорию и подкатегории")
        self.save_button.clicked.connect(self.save)
        right_l.addWidget(action_panel("Удаление строк", self.delete_sub_button, purpose="danger"))
        right_l.addWidget(action_panel("Завершение заполнения", self.save_button, primary=self.save_button))
        self.form_widgets = [self.name, self.section, self.unit, self.price, self.notes, self.sub_table]
        splitter.addWidget(right)
        splitter.setSizes([300, 900])
        self.refresh()
        self.update_form_state()

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.add_sub_button, self.delete_sub_button, self.save_button], has_record)
        self.delete_button.setEnabled(has_record)
        if not has_record:
            self.name.clear(); self.price.clear(); self.notes.clear(); self.sub_table.setRowCount(0)
            self.section.setCurrentText("Не выбрано"); self.unit.setCurrentText("Не выбрано")

    def refresh(self):
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        select_row = -1
        for r in db.rows("SELECT * FROM work_categories WHERE parent_id IS NULL AND is_active=1 ORDER BY name"):
            item = QListWidgetItem(r["name"] or "Без названия")
            item.setData(Qt.UserRole, r["id"])
            self.list.addItem(item)
            if target_id is not None and r["id"] == target_id:
                select_row = self.list.count() - 1
        self.list.blockSignals(False)
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
        elif self.list.count() and target_id is None:
            self.list.setCurrentRow(0)

    def _load_current(self):
        item = self.list.currentItem()
        next_id = item.data(Qt.UserRole) if item else None
        if not self._commit_form_before_load(next_id):
            return
        self.current_id = next_id
        r = db.row("SELECT * FROM work_categories WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
            self._remember_form_state()
            return
        self.update_form_state()
        self.name.setText(r["name"]); self.section.setCurrentText(r["section"] or "Не выбрано"); self.unit.setCurrentText(r["unit"] or "Не выбрано")
        self.price.setText(str(r["default_price"] or "")); self.notes.setPlainText(r["notes"])
        self.sub_table.setRowCount(0)
        for sub in db.rows("SELECT * FROM work_categories WHERE parent_id=? AND is_active=1 ORDER BY name", (self.current_id,)):
            row = self.sub_table.rowCount(); self.sub_table.insertRow(row)
            vals = [sub["id"], sub["name"], sub["section"], sub["unit"], sub["default_price"], sub["notes"]]
            for c, val in enumerate(vals): self.sub_table.set_value(row, c, val)
        self.sub_table.resizeColumnsToContents()
        self._remember_form_state()

    def add_category(self):
        if self.current_id:
            self.save()
        cid = db.execute(
            "INSERT INTO work_categories(name, section, unit) VALUES(?,?,?)",
            ("Новая категория", "", ""),
        )
        self.current_id = cid
        self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == cid:
                self.list.setCurrentRow(i); break

    def delete_category(self):
        if not self.current_id:
            return
        if QMessageBox.question(self, "Удалить?", "Удалить категорию и ее подкатегории? Справочник работ не будет удален.") == QMessageBox.Yes:
            db.execute("UPDATE work_categories SET is_active=0 WHERE id=? OR parent_id=?", (self.current_id, self.current_id))
            self.current_id = None
            self.refresh(); self.sub_table.setRowCount(0); self.update_form_state()

    def add_subcategory(self):
        if not self.current_id:
            self.msg("Сначала выберите или создайте категорию.")
            return
        row = self.sub_table.rowCount(); self.sub_table.insertRow(row)
        section = "" if self.section.currentText() == "Не выбрано" else self.section.currentText()
        unit = "" if self.unit.currentText() == "Не выбрано" else self.unit.currentText()
        vals = ["", "Новая подкатегория", section, unit, self.price.text() or "0", ""]
        for c, val in enumerate(vals): self.sub_table.set_value(row, c, val)

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить категорию», затем заполните поля и сохраните.")
            return
        section = "" if self.section.currentText() == "Не выбрано" else self.section.currentText()
        unit = "" if self.unit.currentText() == "Не выбрано" else self.unit.currentText()
        with connect() as conn:
            conn.execute(
                "UPDATE work_categories SET name=?, section=?, unit=?, default_price=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.name.text(), section, unit, to_float(self.price.text()), self.notes.toPlainText(), self.current_id),
            )
            existing_ids: list[int] = []
            new_subrows: list[tuple[int, int]] = []
            for r in range(self.sub_table.rowCount()):
                name = self.sub_table.value(r, 1)
                if not name:
                    continue
                sub_id = self.sub_table.value(r, 0)
                sub_section = self.sub_table.value(r, 2)
                sub_unit = self.sub_table.value(r, 3)
                if sub_section == "Не выбрано": sub_section = ""
                if sub_unit == "Не выбрано": sub_unit = ""
                vals = (self.current_id, name, sub_section or section, sub_unit or unit, to_float(self.sub_table.value(r, 4)), self.sub_table.value(r, 5))
                if sub_id:
                    conn.execute("UPDATE work_categories SET parent_id=?, name=?, section=?, unit=?, default_price=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*vals, int(sub_id)))
                    existing_ids.append(int(sub_id))
                else:
                    cur = conn.execute("INSERT INTO work_categories(parent_id, name, section, unit, default_price, notes) VALUES(?,?,?,?,?,?)", vals)
                    existing_ids.append(int(cur.lastrowid))
                    new_subrows.append((r, int(cur.lastrowid)))
            if existing_ids:
                placeholders = ",".join("?" for _ in existing_ids)
                conn.execute(f"UPDATE work_categories SET is_active=0 WHERE parent_id=? AND id NOT IN ({placeholders})", (self.current_id, *existing_ids))
            else:
                conn.execute("UPDATE work_categories SET is_active=0 WHERE parent_id=?", (self.current_id,))
            conn.commit()
        for row_index, new_id in new_subrows:
            self.sub_table.set_value(row_index, 0, new_id)
        self._remember_form_state()
        self.msg("Категории сохранены")
        if refresh_after:
            self.refresh()


class WorksTab(BaseTab):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        sources = ContentCard("1. Выберите источник работы",
            "Категории задают типовые данные. Работы можно создавать и без категории.")
        top = QHBoxLayout()
        top.addWidget(selectable_label("Категория:"))
        self.category_combo = ClickComboBox()
        self.category_combo.currentIndexChanged.connect(self.reload_subcategory_combo)
        top.addWidget(self.category_combo, 1)
        top.addWidget(selectable_label("Подкатегория:"))
        self.subcategory_combo = ClickComboBox()
        top.addWidget(self.subcategory_combo, 1)
        sources.add_layout(top)
        self.add_from_category_button = QPushButton("Добавить из выбранной категории")
        self.add_from_category_button.clicked.connect(self.add_from_category)
        sources.add_widget(action_panel("Добавление из категории", self.add_from_category_button,
                                        primary=self.add_from_category_button))
        root.addWidget(sources)

        self.table = CopyPasteTable(0, 8)
        self.table.setHorizontalHeaderLabels(["ID", "Категория", "Подкатегория", "Наименование", "Раздел формы", "Ед. изм.", "Цена/ставка", "Примечание"])
        self.table.setColumnHidden(0, True)
        self.table.setItemDelegateForColumn(1, EditableComboDelegate(lambda: self.category_names(), self.table))
        self.table.setItemDelegateForColumn(2, EditableComboDelegate(lambda: self.subcategory_names(), self.table))
        self.table.setItemDelegateForColumn(4, EditableComboDelegate(lambda: distinct_texts("work_items", "category", COMMON_SECTIONS), self.table))
        self.table.setItemDelegateForColumn(5, EditableComboDelegate(lambda: distinct_texts("work_items", "unit", COMMON_UNITS), self.table))
        entries = table_card("2. Справочник работ и услуг", self.table,
            subtitle="Заполните раздел формы, наименование, единицу измерения и цену.")
        self.add_manual_button = QPushButton("Добавить работу вручную")
        self.add_manual_button.clicked.connect(self.add_row)
        entries.add_widget(action_panel("Добавление строки", self.add_manual_button,
                                       primary=self.add_manual_button))
        root.addWidget(entries, 1)
        delete = QPushButton("Удалить выбранные строки")
        delete.clicked.connect(self.table.delete_selected_rows)
        save = QPushButton("Сохранить справочник")
        save.clicked.connect(self.save)
        root.addWidget(action_panel("Удаление строк", delete, purpose="danger"))
        root.addWidget(action_panel("3. Сохранение справочника", save, primary=save))
        self.reload_category_combo()
        self.refresh()

    def category_names(self) -> list[str]:
        vals = [r["name"] for r in db.rows("SELECT DISTINCT name FROM work_categories WHERE parent_id IS NULL AND is_active=1 AND TRIM(name)<>'' ORDER BY name")]
        return distinct_texts("work_items", "group_name", vals)

    def subcategory_names(self) -> list[str]:
        vals = [r["name"] for r in db.rows("SELECT DISTINCT name FROM work_categories WHERE parent_id IS NOT NULL AND is_active=1 AND TRIM(name)<>'' ORDER BY name")]
        return distinct_texts("work_items", "subgroup_name", vals)

    def reload_category_combo(self):
        old_data = self.category_combo.currentData(); old_text = self.category_combo.currentText()
        self.category_combo.blockSignals(True)
        self.category_combo.clear(); self.category_combo.addItem("Не выбрано", None)
        for r in db.rows("SELECT * FROM work_categories WHERE parent_id IS NULL AND is_active=1 ORDER BY name"):
            self.category_combo.addItem(r["name"], r["id"])
        restore_combo_selection(self.category_combo, old_data, old_text)
        self.category_combo.blockSignals(False)
        self.reload_subcategory_combo()

    def reload_subcategory_combo(self):
        old_data = self.subcategory_combo.currentData(); old_text = self.subcategory_combo.currentText()
        self.subcategory_combo.blockSignals(True)
        self.subcategory_combo.clear(); self.subcategory_combo.addItem("Не выбрано", None)
        cid = self.category_combo.currentData()
        if cid:
            data = db.rows("SELECT * FROM work_categories WHERE parent_id=? AND is_active=1 ORDER BY name", (cid,))
        else:
            data = []
        for r in data:
            self.subcategory_combo.addItem(r["name"], r["id"])
        restore_combo_selection(self.subcategory_combo, old_data, old_text)
        self.subcategory_combo.blockSignals(False)
        self.add_from_category_button.setEnabled(self.category_combo.currentData() is not None)

    def reload(self):
        self.reload_category_combo()
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        for r in db.rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, category, name"):
            row = self.table.rowCount(); self.table.insertRow(row)
            vals = [r["id"], r["group_name"], r["subgroup_name"], r["name"], r["category"], r["unit"], r["default_price"], r["notes"]]
            for col, val in enumerate(vals):
                self.table.set_value(row, col, val)
        self.table.resizeColumnsToContents()

    def add_row(self):
        row = self.table.rowCount(); self.table.insertRow(row)
        for c, val in enumerate(["", "", "", "Новая работа", "", "усл.", "0", ""]):
            self.table.set_value(row, c, val)

    def add_from_category(self):
        cid = self.category_combo.currentData()
        sid = self.subcategory_combo.currentData()
        if not cid and not sid:
            self.add_row(); return
        cat = db.row("SELECT * FROM work_categories WHERE id=?", (cid,)) if cid else None
        sub = db.row("SELECT * FROM work_categories WHERE id=?", (sid,)) if sid else None
        source = sub or cat
        if not source:
            self.add_row(); return
        group_name = cat["name"] if cat else ""
        subgroup_name = sub["name"] if sub else ""
        name = subgroup_name or group_name or "Новая работа"
        row = self.table.rowCount(); self.table.insertRow(row)
        vals = ["", group_name, subgroup_name, name, source["section"], source["unit"], source["default_price"], source["notes"]]
        for c, val in enumerate(vals): self.table.set_value(row, c, val)

    def save(self):
        with connect() as conn:
            active_ids: list[int] = []
            for r in range(self.table.rowCount()):
                wid = self.table.value(r, 0)
                vals = (
                    self.table.value(r, 1),
                    self.table.value(r, 2),
                    self.table.value(r, 3),
                    "" if self.table.value(r, 4) == "Не выбрано" else self.table.value(r, 4),
                    "" if self.table.value(r, 5) == "Не выбрано" else (self.table.value(r, 5) or "усл."),
                    to_float(self.table.value(r, 6)),
                    self.table.value(r, 7),
                )
                if wid:
                    conn.execute("UPDATE work_items SET group_name=?, subgroup_name=?, name=?, category=?, unit=?, default_price=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*vals, int(wid)))
                    active_ids.append(int(wid))
                elif vals[2].strip():
                    cur = conn.execute("INSERT INTO work_items(group_name, subgroup_name, name, category, unit, default_price, notes) VALUES(?,?,?,?,?,?,?)", vals)
                    active_ids.append(int(cur.lastrowid))
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                conn.execute(f"UPDATE work_items SET is_active=0 WHERE is_active=1 AND id NOT IN ({placeholders})", tuple(active_ids))
            else:
                conn.execute("UPDATE work_items SET is_active=0 WHERE is_active=1")
            conn.commit()
        self.refresh()
        self.msg("Справочник сохранен")


class TemplatesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        root = QHBoxLayout(self)
        splitter = QSplitter(); root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        selection = ContentCard("1. Выберите шаблон", "Шаблон относится к одному разделу отчета.")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        selection.add_widget(self.list, 1)
        add = QPushButton("Добавить шаблон")
        add.clicked.connect(self.add_template)
        selection.add_widget(action_panel("Создание", add, primary=add))
        left_l.addWidget(selection, 1)
        self.delete_button = QPushButton("Удалить шаблон")
        self.delete_button.clicked.connect(self.delete_template)
        left_l.addWidget(action_panel("Удаление выбранного шаблона", self.delete_button, purpose="danger"))
        splitter.addWidget(left)
        right = QWidget(); right_l = QVBoxLayout(right)
        details = ContentCard("2. Основные сведения шаблона")
        form = QFormLayout()
        self.name = QLineEdit()
        self.category = ClickComboBox(); self.category.setEditable(True); self.category.addItems(COMMON_SECTIONS)
        self.category.currentTextChanged.connect(self.reload_work_combo)
        self.notes = QTextEdit(); self.notes.setFixedHeight(55)
        form.addRow(selectable_label("Название шаблона"), self.name)
        form.addRow(selectable_label("Раздел"), self.category)
        form.addRow(selectable_label("Примечание"), self.notes)
        self.form_widgets = [self.name, self.category, self.notes]
        details.add_layout(form)
        right_l.addWidget(details)
        source = ContentCard("3. Наполнение шаблона", "Список работ зависит от раздела шаблона.")
        add_from = QHBoxLayout()
        self.work_combo = ClickComboBox()
        add_from.addWidget(selectable_label("Работа из справочника:"))
        add_from.addWidget(self.work_combo, 1)
        source.add_layout(add_from)
        self.add_item_button = QPushButton("Добавить работу в шаблон")
        self.add_item_button.clicked.connect(self.add_work_to_template)
        source.add_widget(action_panel("Добавление строк", self.add_item_button,
                                       primary=self.add_item_button))
        right_l.addWidget(source)
        self.table = CopyPasteTable(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование", "Ед. изм.", "Цена", "Кол-во по перечню"])
        self.table.setColumnHidden(0, True); self.table.setColumnHidden(1, True)
        self.table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("work_items", "unit", COMMON_UNITS), self.table))
        right_l.addWidget(table_card("4. Состав шаблона", self.table,
                             subtitle="Проверьте цену и количество в каждой строке."), 1)
        self.remove_item_button = QPushButton("Удалить выбранные строки")
        self.remove_item_button.clicked.connect(self.table.delete_selected_rows)
        self.save_button = QPushButton("Сохранить шаблон")
        self.save_button.clicked.connect(self.save)
        right_l.addWidget(action_panel("Удаление строк", self.remove_item_button, purpose="danger"))
        right_l.addWidget(action_panel("Завершение заполнения", self.save_button, primary=self.save_button))
        splitter.addWidget(right)
        splitter.setSizes([280, 800])
        self.reload_work_combo()
        self.refresh()
        self.update_form_state()

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.work_combo, self.table,
                            self.add_item_button, self.remove_item_button, self.save_button], has_record)
        self.delete_button.setEnabled(has_record)
        self.add_item_button.setEnabled(bool(has_record and self.work_combo.currentData() is not None))
        if not has_record:
            self.name.clear(); self.notes.clear(); self.table.setRowCount(0)

    def reload_work_combo(self):
        old_data = self.work_combo.currentData()
        old_text = self.work_combo.currentText()
        self.work_combo.blockSignals(True)
        self.work_combo.clear()
        section = normalize_section(self.category.currentText(), "")
        rows_ = db.rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, category, name")
        for r in rows_:
            if section and normalize_section(row_get(r, "category", ""), "") != section:
                continue
            self.work_combo.addItem(work_display(r), r["id"])
        restore_combo_selection(self.work_combo, old_data, old_text)
        self.work_combo.blockSignals(False)
        if hasattr(self, "add_item_button"):
            self.add_item_button.setEnabled(
                self.current_id is not None and self.work_combo.currentData() is not None
            )

    def reload(self):
        # После изменения справочника работ новые строки должны сразу появляться
        # в выпадающем списке "Добавить из справочника".
        self.reload_work_combo()
        self.refresh()

    def refresh(self):
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        select_row = -1
        for r in db.rows("SELECT * FROM templates ORDER BY name"):
            item = QListWidgetItem(f"{r['name']} · {r['category']}")
            item.setData(Qt.UserRole, r["id"])
            self.list.addItem(item)
            if target_id is not None and r["id"] == target_id:
                select_row = self.list.count() - 1
        self.list.blockSignals(False)
        if select_row >= 0:
            self.list.setCurrentRow(select_row)

    def _load_current(self):
        item = self.list.currentItem()
        next_id = item.data(Qt.UserRole) if item else None
        if not self._commit_form_before_load(next_id):
            return
        self.current_id = next_id
        r = db.row("SELECT * FROM templates WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
            self._remember_form_state()
            return
        self.update_form_state()
        self.name.setText(r["name"]); self.category.setCurrentText(r["category"]); self.notes.setPlainText(r["notes"])
        self.reload_work_combo()
        self.table.setRowCount(0)
        for it in db.rows("SELECT * FROM template_items WHERE template_id=? ORDER BY position,id", (self.current_id,)):
            row = self.table.rowCount(); self.table.insertRow(row)
            vals = [it["id"], it["work_item_id"] or "", it["name"], it["unit"], it["unit_price"], it["plan_qty"]]
            for c, val in enumerate(vals): self.table.set_value(row, c, val)
        self.table.resizeColumnsToContents()
        self._remember_form_state()

    def add_template(self):
        if self.current_id:
            self.save()
        tid = db.execute("INSERT INTO templates(name, category) VALUES(?,?)", ("Новый шаблон", "Содержание"))
        self.current_id = tid; self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == tid:
                self.list.setCurrentRow(i); break

    def delete_template(self):
        if self.current_id and QMessageBox.question(self, "Удалить?", "Удалить шаблон?") == QMessageBox.Yes:
            db.execute("DELETE FROM templates WHERE id=?", (self.current_id,))
            self.current_id = None; self.refresh(); self.table.setRowCount(0); self.update_form_state()

    def add_work_to_template(self):
        if not self.current_id:
            self.msg("Сначала выберите или создайте шаблон.")
            return
        wid = self.work_combo.currentData()
        if not wid:
            return
        for row_index in range(self.table.rowCount()):
            if self.table.value(row_index, 1) == str(wid):
                self.msg("Эта работа уже добавлена в шаблон.")
                self.table.selectRow(row_index)
                return
        r = db.row("SELECT * FROM work_items WHERE id=?", (wid,))
        row = self.table.rowCount(); self.table.insertRow(row)
        vals = ["", wid, r["name"], r["unit"], r["default_price"], "0"]
        for c, val in enumerate(vals): self.table.set_value(row, c, val)

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить шаблон», затем заполните поля и сохраните.")
            return
        with connect() as conn:
            conn.execute("UPDATE templates SET name=?, category=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (self.name.text(), self.category.currentText(), self.notes.toPlainText(), self.current_id))
            conn.execute("DELETE FROM template_items WHERE template_id=?", (self.current_id,))
            for r in range(self.table.rowCount()):
                name = self.table.value(r, 2)
                if not name:
                    continue
                conn.execute(
                    "INSERT INTO template_items(template_id, work_item_id, name, unit, unit_price, plan_qty, position) VALUES(?,?,?,?,?,?,?)",
                    (self.current_id, int(self.table.value(r, 1) or 0) or None, name, self.table.value(r, 3), to_float(self.table.value(r, 4)), to_float(self.table.value(r, 5)), r + 1),
                )
            conn.commit()
        self._remember_form_state()
        self.msg("Шаблон сохранен")
        if refresh_after:
            self.refresh()


class AddFromReferenceDialog(QDialog):
    def __init__(self, category: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить из справочника")
        self.selected_id = None
        layout = QVBoxLayout(self)
        self.combo = ClickComboBox()
        target = normalize_section(category, category)
        for r in db.rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, name"):
            if normalize_section(row_get(r, "category", ""), "") != target:
                continue
            self.combo.addItem(work_display(r), r["id"])
        layout.addWidget(self.combo)
        btns = ActionFlowLayout(); ok = QPushButton("Добавить"); cancel = QPushButton("Отмена")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(ok); btns.addWidget(cancel); layout.addLayout(btns)

    def accept(self):
        self.selected_id = self.combo.currentData()
        super().accept()


class CreateReportDialog(QDialog):
    def __init__(self, default_year: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать отчет")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.year = QSpinBox(); self.year.setRange(2000, 2100); self.year.setValue(int(default_year or 2026))
        self.year.valueChanged.connect(self.fill_full_year)
        self.period_start = QLineEdit(); self.period_end = QLineEdit()
        self.period_start.setPlaceholderText("дд.мм.гггг или гггг-мм-дд")
        self.period_end.setPlaceholderText("дд.мм.гггг или гггг-мм-дд")
        self.show_period = QCheckBox("Выводить строку фактического периода управления в отчете")
        form.addRow(selectable_label("Отчетный год"), self.year)
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
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_current_report)
        root = QHBoxLayout(self)
        splitter = QSplitter(); root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        self.company_filter = ClickComboBox(); self.company_filter.currentIndexChanged.connect(self.reload_houses_filter)
        self.house_filter = ClickComboBox(); self.house_filter.currentIndexChanged.connect(self.refresh)
        self.year_filter = QSpinBox(); self.year_filter.setRange(2000, 2100); self.year_filter.setValue(2026); self.year_filter.valueChanged.connect(self.refresh)
        left_l.addWidget(selectable_label("УО")); left_l.addWidget(self.company_filter)
        left_l.addWidget(selectable_label("Дом")); left_l.addWidget(self.house_filter)
        left_l.addWidget(selectable_label("Год")); left_l.addWidget(self.year_filter)
        self.list = QListWidget(); self.list.currentItemChanged.connect(self._load_current)
        left_l.addWidget(self.list)
        create = QPushButton("Создать отчет за выбранный год"); create.clicked.connect(self.create_report)
        self.delete_report_btn = QPushButton("Удалить отчет")
        self.delete_report_btn.clicked.connect(self.delete_report)
        left_l.addWidget(action_panel("Создание отчета", create, primary=create))
        left_l.addWidget(action_panel("Удаление отчета", self.delete_report_btn, purpose="danger"))
        splitter.addWidget(left)

        right = QWidget(); right_l = QVBoxLayout(right)
        self.report_tabs = QTabWidget()
        right_l.addWidget(self.report_tabs)
        self._build_general_tab()
        self._build_content_tab()
        self._build_repair_tab()
        self._build_management_tab()
        self._build_finance_tab()
        self._build_export_tab()
        self.report_tabs.setEnabled(False)
        save = QPushButton("Сохранить отчет"); save.clicked.connect(self.save)
        right_l.addWidget(action_panel("Сохранение отчета", save, primary=save))
        splitter.addWidget(right)
        splitter.setSizes([300, 1000])
        self._connect_autosave_signals()
        self.reload_filters()

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
        self.report_tabs.currentChanged.connect(lambda _idx: self.flush_autosave())

    def schedule_autosave(self, *args) -> None:
        if self._loading_report or self._saving_report or not self.current_id:
            return
        self._dirty_report = True
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
        w = QWidget(); form = QFormLayout(w)
        self.year = QSpinBox(); self.year.setRange(2000, 2100); self.year.setValue(2026)
        self.period_start = QLineEdit(); self.period_start.setPlaceholderText("дд.мм.гггг или гггг-мм-дд")
        self.period_end = QLineEdit(); self.period_end.setPlaceholderText("дд.мм.гггг или гггг-мм-дд")
        fast = QPushButton("Заполнить полный год 01.01–31.12"); fast.clicked.connect(self.fill_full_year)
        self.show_period = QCheckBox("Выводить строку: Фактический период управления с ... по ...")
        self.publish = QLineEdit(); self.publish.setPlaceholderText("дд.мм.гггг")
        self.status = ClickComboBox(); self.status.addItems(["Черновик", "Проверен", "Размещен", "Архив"])
        self.notes = QTextEdit(); self.notes.setFixedHeight(80)
        form.addRow(selectable_label("Отчетный год"), self.year)
        form.addRow(selectable_label("Период управления с"), self.period_start)
        form.addRow(selectable_label("Период управления по"), self.period_end)
        form.addRow(fast)
        form.addRow(self.show_period)
        form.addRow(selectable_label("Дата размещения отчета"), self.publish)
        form.addRow(selectable_label("Статус"), self.status)
        form.addRow(selectable_label("Примечание"), self.notes)
        self.report_tabs.addTab(form_scroll(w), "Общие сведения")

    def _build_content_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        help_text = selectable_label("Раздел 1. Содержание общего имущества. Логика строки: цена единицы × количество = стоимость.")
        layout.addWidget(help_text)
        template_row = QHBoxLayout()
        self.content_template_combo = ClickComboBox()
        template_row.addWidget(selectable_label("Шаблон:")); template_row.addWidget(self.content_template_combo, 1)
        layout.addLayout(template_row)
        apply = QPushButton("Применить шаблон"); apply.clicked.connect(lambda: self.apply_template("Содержание", self.content_template_combo))
        add_ref = QPushButton("Добавить из справочника"); add_ref.clicked.connect(lambda: self.add_from_reference("Содержание"))
        add_empty = QPushButton("Добавить строку"); add_empty.clicked.connect(lambda: self.add_content_row({}))
        groups = QHBoxLayout()
        groups.addWidget(action_panel("Применение шаблона", apply))
        groups.addWidget(action_panel("Добавление строк", add_ref, add_empty, primary=add_ref))
        layout.addLayout(groups)
        self.content_table = CopyPasteTable(0, 9)
        self.content_table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование работы/услуги", "Ед. изм.", "Цена единицы", "Кол-во по перечню", "Стоимость по перечню", "Выполнено кол-во", "Стоимость выполнено"])
        self.content_table.setColumnHidden(0, True); self.content_table.setColumnHidden(1, True)
        self.content_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_content_items", "unit", distinct_texts("work_items", "unit", COMMON_UNITS)), self.content_table))
        self._content_recalc_lock = False
        self.content_table.itemChanged.connect(self.on_content_item_changed)
        layout.addWidget(table_card("Строки содержания", self.content_table))
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
        layout.addLayout(form)
        template_row = QHBoxLayout()
        self.repair_template_combo = ClickComboBox()
        template_row.addWidget(selectable_label("Шаблон:")); template_row.addWidget(self.repair_template_combo, 1)
        layout.addLayout(template_row)
        apply = QPushButton("Применить шаблон"); apply.clicked.connect(lambda: self.apply_template("Текущий ремонт", self.repair_template_combo))
        add_ref = QPushButton("Добавить из справочника"); add_ref.clicked.connect(lambda: self.add_from_reference("Текущий ремонт"))
        add_empty = QPushButton("Добавить строку"); add_empty.clicked.connect(lambda: self.add_repair_row({}))
        groups = QHBoxLayout()
        groups.addWidget(action_panel("Применение шаблона", apply))
        groups.addWidget(action_panel("Добавление строк", add_ref, add_empty, primary=add_ref))
        layout.addLayout(groups)
        self.repair_table = CopyPasteTable(0, 7)
        self.repair_table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование работы", "Основание", "Стоимость", "Объем", "Акт/ссылка"])
        self.repair_table.setColumnHidden(0, True); self.repair_table.setColumnHidden(1, True)
        self.repair_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_repair_items", "basis", COMMON_REPAIR_BASIS), self.repair_table))
        layout.addWidget(table_card("Работы текущего ремонта", self.repair_table))
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

        template_row = QHBoxLayout()
        self.management_template_combo = ClickComboBox()
        template_row.addWidget(selectable_label("Шаблон:")); template_row.addWidget(self.management_template_combo, 1)
        layout.addLayout(template_row)
        apply = QPushButton("Применить шаблон"); apply.clicked.connect(lambda: self.apply_template("Управление", self.management_template_combo))
        add_ref = QPushButton("Добавить из справочника"); add_ref.clicked.connect(lambda: self.add_from_reference("Управление"))
        add_empty = QPushButton("Добавить строку"); add_empty.clicked.connect(lambda: self.add_management_row({}))
        groups = QHBoxLayout()
        groups.addWidget(action_panel("Применение шаблона", apply))
        groups.addWidget(action_panel("Добавление строк", add_ref, add_empty, primary=add_ref))
        layout.addLayout(groups)

        self.management_table = CopyPasteTable(0, 7)
        self.management_table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование услуги", "Ед. изм.", "Цена единицы", "Кол-во", "Стоимость"])
        self.management_table.setColumnHidden(0, True); self.management_table.setColumnHidden(1, True)
        self.management_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_management_items", "unit", distinct_texts("work_items", "unit", COMMON_UNITS)), self.management_table))
        self._management_recalc_lock = False
        self.management_table.itemChanged.connect(self.on_management_item_changed)
        layout.addWidget(table_card("Расчет по услугам управления", self.management_table))
        layout.addWidget(selectable_label(
            "В форму 728/пр по разделу 3 выводится итоговая сумма. Строки выше нужны, чтобы удобно ее заполнить."
        ))
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
        self.finance_table.setItemDelegateForColumn(1, EditableComboDelegate(lambda: distinct_texts("report_financials", "payer_type", COMMON_PAYER_TYPES), self.finance_table))
        self.finance_table.set_value(0, 1, "Собственники")
        self.finance_table.set_value(1, 1, "Наниматели")
        layout.addWidget(table_card("Начисления и поступления", self.finance_table))
        self.report_tabs.addTab(w, "4–5. Финансы")

    def _build_export_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        layout.addWidget(selectable_label("Выгрузка отчета 728/пр. Структура формы защищена: дополнительные поля не добавляются в официальный отчет."))
        xlsx = QPushButton("Выгрузить в Excel (.xlsx)"); xlsx.clicked.connect(lambda: self.export("xlsx"))
        docx = QPushButton("Выгрузить в Word (.docx)"); docx.clicked.connect(lambda: self.export("docx"))
        pdf = QPushButton("Выгрузить в PDF (.pdf)"); pdf.clicked.connect(lambda: self.export("pdf"))
        allb = QPushButton("Выгрузить все форматы"); allb.clicked.connect(lambda: self.export("all"))
        for b in (xlsx, docx, pdf, allb): layout.addWidget(b)
        layout.addStretch()
        self.report_tabs.addTab(w, "Экспорт")

    def reload_filters(self):
        fill_combo(self.company_filter, "SELECT id, name FROM companies ORDER BY name", empty="Все организации")
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
        self.house_filter.clear(); self.house_filter.addItem("Все дома", None)
        if cid:
            data = db.rows("SELECT id, address AS name FROM houses WHERE company_id=? ORDER BY address", (cid,))
        else:
            data = db.rows("SELECT id, address AS name FROM houses ORDER BY address")
        for r in data:
            self.house_filter.addItem(r["name"], r["id"])
        restore_combo_selection(self.house_filter, old_data, old_text)
        self.house_filter.blockSignals(False)
        if refresh:
            self.refresh()

    def _fill_template_combo(self, combo: QComboBox, section: str) -> None:
        old_data = combo.currentData()
        old_text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        target = normalize_section(section, section)
        for r in db.rows("SELECT * FROM templates ORDER BY name"):
            r_section = normalize_section(row_get(r, "category", ""), "")
            if r_section != target:
                continue
            combo.addItem(f"{r['name']} · {r_section}", r["id"])
        restore_combo_selection(combo, old_data, old_text)
        combo.blockSignals(False)

    def reload_template_combo(self):
        # В каждом разделе показываем только шаблоны этого раздела.
        # Это защищает от случайного применения шаблона текущего ремонта в содержание
        # или шаблона управления в другой раздел.
        if hasattr(self, "content_template_combo"):
            self._fill_template_combo(self.content_template_combo, "Содержание")
        if hasattr(self, "repair_template_combo"):
            self._fill_template_combo(self.repair_template_combo, "Текущий ремонт")
        if hasattr(self, "management_template_combo"):
            self._fill_template_combo(self.management_template_combo, "Управление")

    def refresh(self):
        # Before rebuilding the report list, persist the report still on screen.
        if self._dirty_report and self.current_id and not self._loading_report:
            self.flush_autosave()
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        params = [self.year_filter.value()]
        sql = "SELECT r.*, h.address FROM reports r LEFT JOIN houses h ON h.id=r.house_id WHERE r.year=?"
        if self.company_filter.currentData():
            sql += " AND r.company_id=?"; params.append(self.company_filter.currentData())
        if self.house_filter.currentData():
            sql += " AND r.house_id=?"; params.append(self.house_filter.currentData())
        sql += " ORDER BY h.address, r.period_start"
        select_row = -1
        for r in db.rows(sql, tuple(params)):
            item = QListWidgetItem(f"{r['address'] or 'Дом'} · {r['year']} · {r['status']}")
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
            self.report_tabs.setEnabled(False)
        if hasattr(self, "delete_report_btn"):
            self.delete_report_btn.setEnabled(bool(self.current_id))

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
            house = db.row("SELECT company_id FROM houses WHERE id=?", (hid,))
            cid = house["company_id"] if house else None
        if not cid:
            self.msg("У дома не выбрана управляющая организация.")
            return
        dlg = CreateReportDialog(int(self.year_filter.value()), self)
        if dlg.exec() != QDialog.Accepted:
            return
        year, p_start, p_end, show_period = dlg.values()
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

    def delete_report(self):
        if not self.current_id:
            self.msg("Выберите отчет для удаления.")
            return
        item = self.list.currentItem()
        title = item.text() if item else "выбранный отчет"
        if QMessageBox.question(
            self,
            "Удалить отчет?",
            f"Удалить отчет: {title}?\n\nСтроки содержания, текущего ремонта, управления, финансов и претензионной работы по этому отчету также будут удалены.",
        ) != QMessageBox.Yes:
            return
        try:
            db.execute("DELETE FROM reports WHERE id=?", (int(self.current_id),))
            make_backup("delete_report")
            self.current_id = None
            self.refresh()
            self.msg("Отчет удален")
        except Exception as e:
            QMessageBox.warning(self, "Не удалось удалить отчет", str(e))

    def _load_current(self):
        item = self.list.currentItem()
        new_id = item.data(Qt.UserRole) if item else None
        old_id = self.current_id
        if old_id and old_id != new_id and not self._loading_report and not self._saving_report:
            try:
                self.flush_autosave()
            except Exception as exc:
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
                return
        self.current_id = new_id
        if not self.current_id:
            self.report_tabs.setEnabled(False)
            if hasattr(self, "delete_report_btn"):
                self.delete_report_btn.setEnabled(False)
            return
        self.report_tabs.setEnabled(True)
        if hasattr(self, "delete_report_btn"):
            self.delete_report_btn.setEnabled(True)
        self._loading_report = True
        try:
            bundle = db.load_report_bundle(self.current_id)
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
            claims = bundle["claims"] or {}
            self.claim_pret.setText(str(claims.get("pretensions_count") or "")); self.claim_lawsuits.setText(str(claims.get("lawsuits_count") or "")); self.claim_amount.setText(str(claims.get("recovered_amount") or ""))
        finally:
            self._loading_report = False
        self._dirty_report = False
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
            "source": item.get("source", "ручной ввод/редактирование"),
            "is_manual": item.get("is_manual", 0 if item.get("source") in ("массовое применение", "шаблон") else 1),
        })
        self.schedule_autosave()

    def add_repair_row(self, item: dict[str, Any]):
        row = self.repair_table.rowCount(); self.repair_table.insertRow(row)
        vals = [item.get("id", ""), item.get("work_item_id", ""), item.get("name", ""), item.get("basis", ""), item.get("cost", "0"), item.get("volume", ""), item.get("act_details", "")]
        for c, val in enumerate(vals): self.repair_table.set_value(row, c, val)
        self.repair_table.item(row, 0).setData(Qt.UserRole, {
            "source": item.get("source", "ручной ввод/редактирование"),
            "is_manual": item.get("is_manual", 0 if item.get("source") in ("массовое применение", "шаблон") else 1),
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
            "source": item.get("source", "ручной ввод/редактирование"),
            "is_manual": item.get("is_manual", 0 if item.get("source") in ("массовое применение", "шаблон") else 1),
        })
        self.schedule_autosave()

    def recalc_management_total(self) -> None:
        total = 0.0
        for r in range(self.management_table.rowCount()):
            total += to_float(self.management_table.value(r, 6))
        if self.management_table.rowCount() > 0:
            self.management_cost.setText(money(total))
        self.schedule_autosave()

    def add_from_reference(self, category: str):
        if not self.current_id:
            self.msg("Сначала выберите или создайте отчет.")
            return
        dlg = AddFromReferenceDialog(category, self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_id:
            r = db.row("SELECT * FROM work_items WHERE id=?", (dlg.selected_id,))
            price = to_float(r["default_price"])
            if category == "Содержание":
                self.add_content_row({"work_item_id": r["id"], "name": r["name"], "unit": r["unit"], "unit_price": price, "plan_qty": 1, "plan_cost": price, "actual_qty": 1, "actual_cost": price, "source": "справочник"})
            elif category == "Управление":
                self.add_management_row({"work_item_id": r["id"], "name": r["name"], "unit": r["unit"], "unit_price": price, "qty": 1, "cost": price, "source": "справочник"})
            else:
                self.add_repair_row({"work_item_id": r["id"], "name": r["name"], "source": "справочник"})

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

    def apply_template(self, target_section: str, combo: QComboBox):
        if not self.current_id:
            return
        tid = combo.currentData()
        if not tid:
            self.msg("Выберите шаблон для этого раздела.")
            return
        if QMessageBox.question(self, "Применить шаблон", f"Добавить строки выбранного шаблона в раздел «{target_section}»?") != QMessageBox.Yes:
            return
        template_section = self._template_section(int(tid), target_section)
        added = 0
        skipped = 0
        for it in db.rows("SELECT * FROM template_items WHERE template_id=? ORDER BY position,id", (tid,)):
            item_section = self._template_item_section(it, template_section)
            if item_section != target_section:
                skipped += 1
                continue
            cost = to_float(it["unit_price"]) * to_float(it["plan_qty"])
            if target_section == "Управление":
                self.add_management_row({"work_item_id": it["work_item_id"], "name": it["name"], "unit": it["unit"], "unit_price": it["unit_price"], "qty": it["plan_qty"], "cost": cost, "source": "шаблон"})
            elif target_section == "Текущий ремонт":
                self.add_repair_row({"work_item_id": it["work_item_id"], "name": it["name"], "cost": cost, "volume": f"{money(it['plan_qty'])} {it['unit']}", "source": "шаблон"})
            else:
                self.add_content_row({"work_item_id": it["work_item_id"], "name": it["name"], "unit": it["unit"], "unit_price": it["unit_price"], "plan_qty": it["plan_qty"], "plan_cost": cost, "actual_qty": it["plan_qty"], "actual_cost": cost, "source": "шаблон"})
            added += 1
        self.msg(f"Шаблон применен. Раздел: {target_section}. Добавлено: {added}. Пропущено: {skipped}")

    def save(self, silent: bool = False, refresh_after: bool = True):
        if not self.current_id:
            if not silent:
                self.msg("Сначала выберите или создайте отчет, затем заполните поля и сохраните.")
            return
        rid = self.current_id
        self._saving_report = True
        win = self.window()
        if hasattr(win, "set_save_status"):
            win.set_save_status("saving")
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE reports SET year=?, period_start=?, period_end=?, show_period_note=?, publish_date=?, status=?, management_cost=?, repair_start_balance=?, repair_accrual=?, repair_work_cost=?, repair_end_balance=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (self.year.value(), iso_date(self.period_start.text()) or self.period_start.text(), iso_date(self.period_end.text()) or self.period_end.text(), 1 if self.show_period.isChecked() else 0, iso_date(self.publish.text()) or self.publish.text(), self.status.currentText(), to_float(self.management_cost.text()), to_float(self.repair_start.text()), to_float(self.repair_accrual.text()), to_float(self.repair_work_cost.text()), to_float(self.repair_end.text()), self.notes.toPlainText(), rid),
                )
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
                        name = widget.value(row_index, 2)
                        meta_item = widget.item(row_index, 0)
                        meta = meta_item.data(Qt.UserRole) if meta_item else None
                        meta = meta if isinstance(meta, dict) else {}
                        common = {
                            "id": int(widget.value(row_index, 0) or 0),
                            "work_item_id": int(widget.value(row_index, 1) or 0) or None,
                            "name": name,
                            "source": meta.get("source", "ручной ввод/редактирование"),
                            "is_manual": meta.get("is_manual", 1),
                        }
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
                        records.append({**common, **fields})
                    result = sync_report_rows(conn, table_name, rid, records)
                    inserted_rows.append((widget, result))
                conn.execute("DELETE FROM report_financials WHERE report_id=?", (rid,))
                for r in range(self.finance_table.rowCount()):
                    payer = self.finance_table.value(r, 1)
                    if not payer:
                        continue
                    conn.execute(
                        "INSERT INTO report_financials(report_id, payer_type, debt_start, accrued, paid, debt_end) VALUES(?,?,?,?,?,?)",
                        (rid, payer, to_float(self.finance_table.value(r,2)), to_float(self.finance_table.value(r,3)), to_float(self.finance_table.value(r,4)), to_float(self.finance_table.value(r,5))),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO report_claims(report_id, pretensions_count, lawsuits_count, recovered_amount) VALUES(?,?,?,?)",
                    (rid, int(to_float(self.claim_pret.text())), int(to_float(self.claim_lawsuits.text())), to_float(self.claim_amount.text())),
                )
                conn.commit()
                # Update hidden IDs before allowing UI change signals to mark new edits.
                for widget, inserts in inserted_rows:
                    for row_index, record_id in inserts:
                        widget.set_value(row_index, 0, record_id)
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

    def export(self, kind: str):
        if not self.current_id:
            self.msg("Выберите отчет для выгрузки.")
            return
        try:
            self.save(silent=True, refresh_after=False)
        except Exception as exc:
            self.msg(f"Экспорт отменен: отчет не удалось сохранить ({exc})")
            return
        make_backup("before_export")
        paths = []
        try:
            if kind in ("xlsx", "all"): paths.append(export_xlsx(self.current_id))
            if kind in ("docx", "all"): paths.append(export_docx(self.current_id))
            if kind in ("pdf", "all"): paths.append(export_pdf(self.current_id))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось сформировать файл.\n\n{e}")
            return
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Готово")
        msg.setText("Файлы сформированы:\n" + "\n".join(str(p) for p in paths))
        open_btn = msg.addButton("Открыть папку", QMessageBox.ActionRole)
        msg.addButton("OK", QMessageBox.AcceptRole)
        msg.exec()
        if msg.clickedButton() == open_btn and paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0].parent)))


class MassPreviewTable(CopyPasteTable):
    """Review-only; copying is supported, pasting and row mutation are forbidden."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)

    def paste_selection(self):
        return

    def delete_selected_rows(self):
        return

    def _menu(self, pos):
        menu = QMenu(self)
        act_copy = QAction("Копировать выделенное", self)
        act_copy.triggered.connect(self.copy_selection)
        menu.addAction(act_copy)
        menu.exec(self.viewport().mapToGlobal(pos))


class MassApplyTab(BaseTab):
    """Guided bulk entry: sources, calculation, targets, review, confirmation."""

    PREVIEW_COLUMNS = [
        "Дом", "Площадь, м²", "Раздел / работа", "Отчет", "Стоимость",
        "Цена в строке", "Количество", "Действие",
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
        top = QHBoxLayout()
        top.setSpacing(9)
        root.addLayout(top)

        # Step 1. The work and reporting period.
        context = ContentCard("1. Что заполняем",
            "Выберите УО, отчетный год и конкретную работу из справочника.")
        source = QFormLayout()
        self.company = ClickComboBox()
        self.company.currentIndexChanged.connect(self._company_changed)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(2026)
        self.work = ClickComboBox()
        source.addRow(selectable_label("Управляющая организация"), self.company)
        source.addRow(selectable_label("Отчетный год"), self.year)
        source.addRow(selectable_label("Работа / услуга"), self.work)
        context.add_layout(source)
        top.addWidget(context, 1)

        # Step 2. Only input controls required by the selected formula are shown.
        calculation = ContentCard("2. Как рассчитываем")
        form = QFormLayout()
        self.mode = ClickComboBox()
        for title, key in self.MODES:
            self.mode.addItem(title, key)
        self.rate_label = selectable_label("Ставка / цена")
        self.months_label = selectable_label("Количество месяцев")
        self.quantity_label = selectable_label("Количество")
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
        calculation.add_layout(form)
        self.help = selectable_label("")
        self.help.setObjectName("CardSubtitle")
        calculation.add_widget(self.help)
        top.addWidget(calculation, 1)

        # Step 3. Explicitly choose which houses are eligible for the calculation.
        targets = ContentCard("3. Какие дома заполняем",
            "Отметьте нужные дома. Архивные дома не включаются.")
        self.house_list = QListWidget()
        self.house_list.setMinimumHeight(100)
        self.house_list.setMaximumHeight(170)
        self.house_list.itemChanged.connect(self.mark_dirty)
        targets.add_widget(self.house_list)
        select_all = QPushButton("Выбрать все")
        select_all.clicked.connect(lambda: self._check_all(True))
        clear_all = QPushButton("Снять выделение")
        clear_all.clicked.connect(lambda: self._check_all(False))
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
        review.add_widget(self.table, 1)
        root.addWidget(review, 1)

        # Step 5. Recording is impossible without a fresh, actionable preview.
        finish = QHBoxLayout()
        final_text = selectable_label(
            "5. Применение: записи добавляются только после проверки и подтверждения."
        )
        finish.addWidget(final_text, 1)
        self.apply_button = QPushButton("Подтвердить и применить")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setObjectName("PrimaryAction")
        self.apply_button.setEnabled(False)
        finish.addWidget(self.apply_button)
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
            houses = db.rows(
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
                item.setCheckState(Qt.Checked if (hid in checked or not same_company) else Qt.Unchecked)
                self.house_list.addItem(item)
        self.house_list.blockSignals(False)
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
        self.apply_button.setEnabled(False)
        self.table.setRowCount(0)
        self.summary.clear()
        self.status_label.setText("Параметры изменены. Рассчитайте и проверьте заново.")

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
        for item in db.rows(
            "SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name,subgroup_name,category,name"
        ):
            self.work.addItem(work_display(item), item["id"])
        if old_work is not None and self.work.findData(old_work) >= 0:
            self.work.setCurrentIndex(self.work.findData(old_work))
        self.work.blockSignals(False)
        self._load_target_houses()

    def preview(self):
        self.mark_dirty()
        try:
            with connect() as conn:
                plan = build_mass_plan(conn, self._inputs())
        except (ValueError, OSError) as exc:
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
        self.table.resizeColumnsToContents()
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
        try:
            make_backup("mass_apply")
        except Exception as exc:
            self.msg(f"Данные записаны, но резервную копию создать не удалось: {exc}")
            return
        self.msg(f'Массовое заполнение выполнено: добавлено {result["insert"]}, '
                 f'обновлено {result["update"]}, пропущено {result["skip"]}.')

class BackupsTab(BaseTab):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(selectable_label("Данные программы хранятся в папке data рядом с EXE/исходным файлом. Резервная копия создается автоматически раз в 5 минут."))
        backup = QPushButton("Создать резервную копию сейчас"); backup.clicked.connect(self.backup_now)
        open_data = QPushButton("Показать путь к папке data"); open_data.clicked.connect(self.show_data_path)
        backup_actions = QHBoxLayout()
        backup_actions.addWidget(action_panel("Создание копии", backup, primary=backup))
        backup_actions.addWidget(action_panel("Расположение данных", open_data))
        layout.addLayout(backup_actions)
        self.info = QTextEdit(); self.info.setReadOnly(True)
        layout.addWidget(self.info)
        self.refresh()

    def refresh(self):
        folder = data_dir()
        backups = sorted((folder / "backups").glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        text = f"Папка данных:\n{folder}\n\nПоследние резервные копии:\n" + "\n".join(str(p.name) for p in backups)
        self.info.setPlainText(text)

    def backup_now(self):
        path = make_backup("manual")
        self.msg(f"Резервная копия создана: {path}")
        self.refresh()

    def show_data_path(self):
        self.msg(f"Папка data: {data_dir()}")


class MainWindow(QMainWindow):
    """Navigation shell; pages retain their own data, calculations and exports."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 850)
        self.settings = QSettings("OtchetnostUK", "728")
        self._theme = str(self.settings.value("ui/theme", "light"))
        if self._theme not in ("light", "dark"):
            self._theme = "light"
        try:
            self._scale = int(self.settings.value("ui/scale", 100))
        except (TypeError, ValueError):
            self._scale = 100
        # Non-100 UI scaling was experimental in the reference application.
        self._scale = 100

        apply_theme(QApplication.instance(), self._theme, self._scale)
        central = QWidget(self)
        central.setObjectName("ContentPane")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)
        self.sidebar = NavigationSidebar(self._theme, self._scale, self)
        root.addWidget(self.sidebar)

        workspace = QWidget()
        workspace.setObjectName("PageSurface")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 9, 10, 8)
        workspace_layout.setSpacing(8)
        self.page_header = PageChrome()
        workspace_layout.addWidget(self.page_header)
        self.tabs = QStackedWidget()
        workspace_layout.addWidget(self.tabs, 1)
        root.addWidget(workspace, 1)

        # Preserve the existing screens, data bindings and per-section logic.
        self.company_tab = CompaniesTab()
        self.houses_tab = HousesTab()
        self.categories_tab = CategoriesTab()
        self.works_tab = WorksTab()
        self.templates_tab = TemplatesTab()
        self.reports_tab = ReportsTab()
        self.mass_tab = MassApplyTab()
        self.backups_tab = BackupsTab()
        for tab in (
            self.company_tab, self.houses_tab, self.categories_tab,
            self.works_tab, self.templates_tab, self.mass_tab,
            self.reports_tab, self.backups_tab,
        ):
            self.tabs.addWidget(tab)

        self._last_tab = 0
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.sidebar.navigateRequested.connect(self.navigate)
        self.sidebar.themeRequested.connect(self.switch_theme)
        self.sidebar.scaleRequested.connect(self.set_scale)
        self.sidebar.select(0)
        self.page_header.show_page(0)
        self._decorate_buttons()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.auto_backup)
        self.timer.start(5 * 60 * 1000)
        self.context_timer = QTimer(self)
        self.context_timer.timeout.connect(self.refresh_context)
        self.context_timer.start(1500)
        self.refresh_context()
        self.statusBar().showMessage("Готово")

    def _decorate_buttons(self):
        """Do not override roles assigned by the actual action groups."""
        for button in self.findChildren(QPushButton):
            if button.objectName() in ("PrimaryAction", "DangerAction", "QuietAction"):
                continue
            label = button.text().strip().lower()
            if label.startswith(("удалить", "очистить", "аннулировать")):
                button.setObjectName("DangerAction")

    def _can_autosave_tab(self, tab) -> bool:
        if not isinstance(tab, BaseTab):
            return False
        if hasattr(tab, "current_id"):
            return getattr(tab, "current_id") is not None
        return True

    def set_save_status(self, state: str, message: str | None = None):
        self.page_header.set_save_status(state, message)

    def notification_received(self, text: str):
        low = text.lower()
        if "не удалось" in low and "сохран" in low:
            self.set_save_status("error")
        elif "сохран" in low and any(word in low for word in ("выполнено", "сохранен", "сохранён", "сохранена")):
            self.set_save_status("saved")

    def navigate(self, index: int):
        if not 0 <= index < self.tabs.count():
            return
        if index == self._last_tab:
            return
        previous = self.tabs.widget(self._last_tab)
        try:
            if isinstance(previous, ReportsTab):
                # No pending edit may be thrown away when selecting another page.
                previous.flush_autosave()
            elif self._can_autosave_tab(previous):
                previous.save()
        except Exception as exc:
            self.set_save_status("error")
            self.statusBar().showMessage(f"Не удалось сохранить изменения: {exc}", 12000)
            self.sidebar.select(self._last_tab)
            return
        self.tabs.setCurrentIndex(index)

    def on_tab_changed(self, index: int):
        self._last_tab = index
        self.sidebar.select(index)
        self.page_header.show_page(index)
        self.set_save_status("idle")
        current = self.tabs.widget(index)
        if hasattr(current, "reload"):
            try:
                current.reload()
            except Exception as exc:
                self.statusBar().showMessage(f"Не удалось обновить список: {exc}", 8000)
        if isinstance(current, BaseTab):
            try:
                current.refresh()
            except Exception as exc:
                self.statusBar().showMessage(f"Не удалось обновить вкладку: {exc}", 8000)
        self.refresh_context()
        if isinstance(current, ReportsTab) and current.current_id and not current._dirty_report:
            self.set_save_status("saved")

    def refresh_context(self):
        """Show only the active page's selection and its meaningful next step."""
        try:
            current = self.tabs.currentWidget()
            context, guidance = describe_context(current, self)
            self.page_header.set_context(context)
            self.page_header.guide.setText(guidance)
        except (AttributeError, RuntimeError):
            self.page_header.set_context("Рабочий контекст")

    def switch_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.settings.setValue("ui/theme", self._theme)
        apply_theme(QApplication.instance(), self._theme, self._scale)
        self.sidebar.refresh_theme(self._theme)

    def set_scale(self, scale: int):
        # Intentionally locked until each size has a verified layout.
        self._scale = 100
        self.settings.setValue("ui/scale", 100)

    def auto_backup(self):
        try:
            tab = self.tabs.currentWidget()
            if isinstance(tab, ReportsTab):
                tab.flush_autosave()
            elif self._can_autosave_tab(tab):
                tab.save()
            path = make_backup("auto")
            self.statusBar().showMessage(f"Резервная копия создана: {path.name if path else ''}", 5000)
        except Exception as exc:
            self.set_save_status("error")
            self.statusBar().showMessage(f"Не удалось создать резервную копию: {exc}", 8000)

    def closeEvent(self, event):
        # A failed write must never silently discard edited form fields.
        try:
            tab = self.tabs.currentWidget()
            if isinstance(tab, ReportsTab):
                tab.flush_autosave()
            elif self._can_autosave_tab(tab):
                tab.save()
        except Exception as exc:
            self.set_save_status("error")
            self.statusBar().showMessage(f"Закрытие отменено: данные не сохранены ({exc})", 12000)
            event.ignore()
            return
        super().closeEvent(event)


def install_exception_hook():
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        QMessageBox.critical(None, "Ошибка", text[-3000:])
    sys.excepthook = hook


def main():
    db.init_db()
    app = QApplication(sys.argv)
    install_exception_hook()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
