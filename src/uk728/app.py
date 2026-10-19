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

APP_TITLE = "Отчетность УК по приказу 728/пр — V1.6.0 UI/UX REDESIGN"


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
        self.setWordWrap(True)
        self.setSelectionMode(QTableWidget.ExtendedSelection)
        self.setSelectionBehavior(QTableWidget.SelectItems)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.verticalHeader().setVisible(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

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
        self.setItem(row, col, QTableWidgetItem("" if value is None else str(value)))

    def value(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text().strip() if item else ""


class BaseTab(QWidget):
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
        left_l.addWidget(selectable_label("Управляющие организации"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        left_l.addWidget(self.list)
        btns = ActionFlowLayout()
        add = QPushButton("Добавить организацию")
        add.clicked.connect(self.add_company)
        delete = QPushButton("Удалить")
        delete.clicked.connect(self.delete_company)
        btns.addWidget(add); btns.addWidget(delete)
        left_l.addLayout(btns)
        splitter.addWidget(left)

        formw = QWidget()
        form = QFormLayout(formw)
        self.name = QLineEdit()
        self.ogrn = QLineEdit()
        self.addr = QTextEdit(); self.addr.setFixedHeight(70)
        self.person = QLineEdit()
        self.contact = QLineEdit()
        self.notes = QTextEdit(); self.notes.setFixedHeight(80)
        self.form_widgets = [self.name, self.ogrn, self.addr, self.person, self.contact, self.notes]
        for label, widget in [
            ("Полное наименование", self.name),
            ("ОГРН / ИНН", self.ogrn),
            ("Адрес приема населения", self.addr),
            ("Кто дает разъяснения по отчету", self.person),
            ("Телефон / email", self.contact),
            ("Примечание", self.notes),
        ]:
            form.addRow(selectable_label(label), widget)
        self.save_button = QPushButton("Сохранить организацию")
        self.save_button.clicked.connect(self.save)
        form.addRow(self.save_button)
        splitter.addWidget(form_scroll(formw))
        splitter.setSizes([260, 700])
        self.refresh()
        self.update_form_state()

    def _clear_form(self):
        clear_widgets(self.form_widgets)

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.save_button], has_record)
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
        self.current_id = item.data(Qt.UserRole) if item else None
        r = db.row("SELECT * FROM companies WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
            return
        self.update_form_state()
        self.name.setText(r["name"])
        self.ogrn.setText(r["ogrn_inn"])
        self.addr.setPlainText(r["reception_address"])
        self.person.setText(r["report_person"])
        self.contact.setText(r["report_contact"])
        self.notes.setPlainText(r["notes"])

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

    def save(self):
        if not self.current_id:
            self.msg("Нажмите «Добавить организацию», затем заполните поля и сохраните.")
            return
        with connect() as conn:
            conn.execute(
                "UPDATE companies SET name=?, ogrn_inn=?, reception_address=?, report_person=?, report_contact=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.name.text(), self.ogrn.text(), self.addr.toPlainText(), self.person.text(), self.contact.text(), self.notes.toPlainText(), self.current_id),
            )
            conn.commit()
        self.msg("Организация сохранена")
        self.refresh()


class HousesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        root = QHBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        top = QHBoxLayout()
        top.addWidget(selectable_label("УО:"))
        self.company_filter = ClickComboBox()
        self.company_filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.company_filter, 1)
        left_l.addLayout(top)
        self.list = QListWidget(); self.list.currentItemChanged.connect(self._load_current)
        left_l.addWidget(self.list)
        btns = ActionFlowLayout()
        self.add_button = QPushButton("Добавить дом"); self.add_button.clicked.connect(self.add_house)
        delete = QPushButton("Удалить"); delete.clicked.connect(self.delete_house)
        btns.addWidget(self.add_button); btns.addWidget(delete)
        left_l.addLayout(btns)
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

        official_box = QGroupBox("Официальные данные для отчета 728/пр")
        official_form = QFormLayout(official_box)
        for label, widget in [
            ("Управляющая организация", self.company),
            ("Адрес дома", self.address),
            ("Общая площадь жилых и нежилых помещений, м²", self.area),
            ("Дата начала управления", self.start),
            ("Дата прекращения управления", self.end),
            ("Статус", self.status),
            ("Примечание", self.notes),
        ]:
            official_form.addRow(selectable_label(label), widget)
        form_l.addWidget(official_box)

        self.save_button = QPushButton("Сохранить дом"); self.save_button.clicked.connect(self.save)
        form_l.addWidget(self.save_button)
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
        if not has_record:
            self._clear_form()

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
        self.current_id = item.data(Qt.UserRole) if item else None
        r = db.row("SELECT * FROM houses WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
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

    def save(self):
        if not self.current_id:
            self.msg("Сначала выберите или добавьте дом, затем заполните поля и сохраните.")
            return
        with connect() as conn:
            conn.execute(
                "UPDATE houses SET company_id=?, address=?, total_area=?, management_start=?, management_end=?, status=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.company.currentData(), self.address.toPlainText(), to_float(self.area.text()), self.start.text(), self.end.text(), self.status.currentText(), self.notes.toPlainText(), self.current_id),
            )
            conn.commit()
        self.msg("Дом сохранен")
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
        left_l.addWidget(selectable_label("Категории работ/услуг"))
        self.list = QListWidget(); self.list.currentItemChanged.connect(self._load_current)
        left_l.addWidget(self.list)
        btns = ActionFlowLayout()
        add = QPushButton("Добавить категорию"); add.clicked.connect(self.add_category)
        delete = QPushButton("Удалить категорию"); delete.clicked.connect(self.delete_category)
        btns.addWidget(add); btns.addWidget(delete)
        left_l.addLayout(btns)
        splitter.addWidget(left)

        right = QWidget(); right_l = QVBoxLayout(right)
        right_l.addWidget(selectable_label(
            "Категории помогают быстро создавать строки справочника. Главное поле — «Раздел формы»: "
            "Содержание — раздел 1, Текущий ремонт — раздел 2, Управление — раздел 3. "
            "Примечание — это только комментарий для пользователя. На попадание строки в раздел отчета влияет поле «Раздел формы»."
        ))
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
        right_l.addLayout(form)

        right_l.addWidget(selectable_label("Подкатегории выбранной категории"))
        self.sub_table = CopyPasteTable(0, 6)
        self.sub_table.setHorizontalHeaderLabels(["ID", "Название подкатегории", "Раздел", "Ед. изм.", "Цена/ставка", "Примечание"])
        self.sub_table.setColumnHidden(0, True)
        self.sub_table.setItemDelegateForColumn(2, EditableComboDelegate(lambda: distinct_texts("work_categories", "section", ["Не выбрано", *COMMON_SECTIONS]), self.sub_table))
        self.sub_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("work_categories", "unit", ["Не выбрано", *COMMON_UNITS]), self.sub_table))
        right_l.addWidget(self.sub_table)
        self.form_widgets = [self.name, self.section, self.unit, self.price, self.notes, self.sub_table]
        bottom = QHBoxLayout()
        add_sub = QPushButton("Добавить подкатегорию"); add_sub.clicked.connect(self.add_subcategory)
        del_sub = QPushButton("Удалить выбранные подкатегории"); del_sub.clicked.connect(self.sub_table.delete_selected_rows)
        save = QPushButton("Сохранить категорию и подкатегории"); save.clicked.connect(self.save)
        bottom.addWidget(add_sub); bottom.addWidget(del_sub); bottom.addStretch(); bottom.addWidget(save)
        right_l.addLayout(bottom)
        splitter.addWidget(right)
        splitter.setSizes([300, 900])
        self.refresh()
        self.update_form_state()

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets, has_record)
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
        self.current_id = item.data(Qt.UserRole) if item else None
        r = db.row("SELECT * FROM work_categories WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
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

    def save(self):
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
            if existing_ids:
                placeholders = ",".join("?" for _ in existing_ids)
                conn.execute(f"UPDATE work_categories SET is_active=0 WHERE parent_id=? AND id NOT IN ({placeholders})", (self.current_id, *existing_ids))
            else:
                conn.execute("UPDATE work_categories SET is_active=0 WHERE parent_id=?", (self.current_id,))
            conn.commit()
        self.msg("Категории сохранены")
        self.refresh()


class WorksTab(BaseTab):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        info = selectable_label(
            "Справочник работ/услуг. Работу можно добавить произвольно или быстро создать из категории/подкатегории. "
            "Категория и подкатегория нужны для удобного поиска. В отчет попадает наименование работы/услуги и поля официальной формы."
        )
        root.addWidget(info)
        top = QHBoxLayout()
        top.addWidget(selectable_label("Категория:"))
        self.category_combo = ClickComboBox(); self.category_combo.currentIndexChanged.connect(self.reload_subcategory_combo)
        top.addWidget(self.category_combo, 1)
        top.addWidget(selectable_label("Подкатегория:"))
        self.subcategory_combo = ClickComboBox()
        top.addWidget(self.subcategory_combo, 1)
        add_cat = QPushButton("Добавить работу/услугу из выбранного")
        add_cat.clicked.connect(self.add_from_category)
        top.addWidget(add_cat)
        root.addLayout(top)

        self.table = CopyPasteTable(0, 8)
        self.table.setHorizontalHeaderLabels(["ID", "Категория", "Подкатегория", "Наименование", "Раздел формы", "Ед. изм.", "Цена/ставка", "Примечание"])
        self.table.setColumnHidden(0, True)
        self.table.setItemDelegateForColumn(1, EditableComboDelegate(lambda: self.category_names(), self.table))
        self.table.setItemDelegateForColumn(2, EditableComboDelegate(lambda: self.subcategory_names(), self.table))
        self.table.setItemDelegateForColumn(4, EditableComboDelegate(lambda: distinct_texts("work_items", "category", COMMON_SECTIONS), self.table))
        self.table.setItemDelegateForColumn(5, EditableComboDelegate(lambda: distinct_texts("work_items", "unit", COMMON_UNITS), self.table))
        root.addWidget(self.table)
        btns = ActionFlowLayout()
        add = QPushButton("Добавить произвольную строку"); add.clicked.connect(self.add_row)
        save = QPushButton("Сохранить справочник"); save.clicked.connect(self.save)
        delete = QPushButton("Удалить выбранные строки"); delete.clicked.connect(self.table.delete_selected_rows)
        btns.addWidget(add); btns.addWidget(delete); btns.addStretch(); btns.addWidget(save)
        root.addLayout(btns)
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
            data = db.rows("SELECT * FROM work_categories WHERE parent_id IS NOT NULL AND is_active=1 ORDER BY name")
        for r in data:
            self.subcategory_combo.addItem(r["name"], r["id"])
        restore_combo_selection(self.subcategory_combo, old_data, old_text)
        self.subcategory_combo.blockSignals(False)

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
        left_l.addWidget(selectable_label("Шаблоны перечней"))
        self.list = QListWidget(); self.list.currentItemChanged.connect(self._load_current)
        left_l.addWidget(self.list)
        add = QPushButton("Добавить шаблон"); add.clicked.connect(self.add_template)
        delete = QPushButton("Удалить шаблон"); delete.clicked.connect(self.delete_template)
        left_l.addWidget(add); left_l.addWidget(delete)
        splitter.addWidget(left)
        right = QWidget(); right_l = QVBoxLayout(right)
        form = QFormLayout()
        self.name = QLineEdit()
        self.category = ClickComboBox(); self.category.setEditable(True); self.category.addItems(COMMON_SECTIONS)
        self.category.currentTextChanged.connect(self.reload_work_combo)
        self.notes = QTextEdit(); self.notes.setFixedHeight(55)
        form.addRow(selectable_label("Название шаблона"), self.name)
        form.addRow(selectable_label("Раздел"), self.category)
        form.addRow(selectable_label("Примечание"), self.notes)
        self.form_widgets = [self.name, self.category, self.notes]
        right_l.addLayout(form)
        add_from = QHBoxLayout()
        self.work_combo = ClickComboBox()
        add_from.addWidget(selectable_label("Добавить из справочника:"))
        add_from.addWidget(self.work_combo, 1)
        btn = QPushButton("Добавить строку"); btn.clicked.connect(self.add_work_to_template)
        add_from.addWidget(btn)
        right_l.addLayout(add_from)
        self.table = CopyPasteTable(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование", "Ед. изм.", "Цена", "Кол-во по перечню"])
        self.table.setColumnHidden(0, True); self.table.setColumnHidden(1, True)
        self.table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("work_items", "unit", COMMON_UNITS), self.table))
        right_l.addWidget(self.table)
        save = QPushButton("Сохранить шаблон"); save.clicked.connect(self.save)
        right_l.addWidget(save)
        splitter.addWidget(right)
        splitter.setSizes([280, 800])
        self.reload_work_combo()
        self.refresh()
        self.update_form_state()

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.work_combo, self.table], has_record)
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
        self.current_id = item.data(Qt.UserRole) if item else None
        r = db.row("SELECT * FROM templates WHERE id=?", (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
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
        r = db.row("SELECT * FROM work_items WHERE id=?", (wid,))
        row = self.table.rowCount(); self.table.insertRow(row)
        vals = ["", wid, r["name"], r["unit"], r["default_price"], "0"]
        for c, val in enumerate(vals): self.table.set_value(row, c, val)

    def save(self):
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
        self.msg("Шаблон сохранен")
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
        left_l.addWidget(create)
        left_l.addWidget(self.delete_report_btn)
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
        btns = ActionFlowLayout()
        save = QPushButton("Сохранить отчет"); save.clicked.connect(self.save)
        btns.addStretch(); btns.addWidget(save)
        right_l.addLayout(btns)
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
        self._autosave_timer.start(700)
        win = self.window()
        if hasattr(win, 'set_save_status'):
            win.set_save_status('saving')

    def flush_autosave(self) -> None:
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
        if self.current_id and not self._loading_report and not self._saving_report:
            self.save(silent=True, refresh_after=False)

    def _autosave_current_report(self) -> None:
        if not self.current_id or self._loading_report or self._saving_report:
            return
        self.save(silent=True, refresh_after=False)

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
        btns = ActionFlowLayout()
        apply = QPushButton("Применить шаблон"); apply.clicked.connect(lambda: self.apply_template("Содержание", self.content_template_combo))
        add_ref = QPushButton("Добавить из справочника"); add_ref.clicked.connect(lambda: self.add_from_reference("Содержание"))
        add_empty = QPushButton("Добавить строку"); add_empty.clicked.connect(lambda: self.add_content_row({}))
        btns.addWidget(apply); btns.addWidget(add_ref); btns.addWidget(add_empty); btns.addStretch()
        layout.addLayout(btns)
        self.content_table = CopyPasteTable(0, 9)
        self.content_table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование работы/услуги", "Ед. изм.", "Цена единицы", "Кол-во по перечню", "Стоимость по перечню", "Выполнено кол-во", "Стоимость выполнено"])
        self.content_table.setColumnHidden(0, True); self.content_table.setColumnHidden(1, True)
        self.content_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_content_items", "unit", distinct_texts("work_items", "unit", COMMON_UNITS)), self.content_table))
        self._content_recalc_lock = False
        self.content_table.itemChanged.connect(self.on_content_item_changed)
        layout.addWidget(self.content_table)
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
        btns = ActionFlowLayout()
        apply = QPushButton("Применить шаблон"); apply.clicked.connect(lambda: self.apply_template("Текущий ремонт", self.repair_template_combo))
        add_ref = QPushButton("Добавить из справочника"); add_ref.clicked.connect(lambda: self.add_from_reference("Текущий ремонт"))
        add_empty = QPushButton("Добавить строку"); add_empty.clicked.connect(lambda: self.add_repair_row({}))
        btns.addWidget(apply); btns.addWidget(add_ref); btns.addWidget(add_empty); btns.addStretch()
        layout.addLayout(btns)
        self.repair_table = CopyPasteTable(0, 7)
        self.repair_table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование работы", "Основание", "Стоимость", "Объем", "Акт/ссылка"])
        self.repair_table.setColumnHidden(0, True); self.repair_table.setColumnHidden(1, True)
        self.repair_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_repair_items", "basis", COMMON_REPAIR_BASIS), self.repair_table))
        layout.addWidget(self.repair_table)
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
        btns = ActionFlowLayout()
        apply = QPushButton("Применить шаблон"); apply.clicked.connect(lambda: self.apply_template("Управление", self.management_template_combo))
        add_ref = QPushButton("Добавить из справочника"); add_ref.clicked.connect(lambda: self.add_from_reference("Управление"))
        add_empty = QPushButton("Добавить строку"); add_empty.clicked.connect(lambda: self.add_management_row({}))
        btns.addWidget(apply); btns.addWidget(add_ref); btns.addWidget(add_empty); btns.addStretch()
        layout.addLayout(btns)

        self.management_table = CopyPasteTable(0, 7)
        self.management_table.setHorizontalHeaderLabels(["ID", "Работа ID", "Наименование услуги", "Ед. изм.", "Цена единицы", "Кол-во", "Стоимость"])
        self.management_table.setColumnHidden(0, True); self.management_table.setColumnHidden(1, True)
        self.management_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("report_management_items", "unit", distinct_texts("work_items", "unit", COMMON_UNITS)), self.management_table))
        self._management_recalc_lock = False
        self.management_table.itemChanged.connect(self.on_management_item_changed)
        layout.addWidget(self.management_table)
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
        layout.addWidget(self.finance_table)
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
            self.flush_autosave()
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
        if actual_cost in (None, "") or (to_float(actual_cost) == 0 and to_float(actual_qty) != 0 and to_float(unit_price) != 0):
            actual_cost = to_float(unit_price) * to_float(actual_qty)
        vals = [item.get("id", ""), item.get("work_item_id", ""), item.get("name", ""), item.get("unit", "усл."), unit_price, plan_qty, plan_cost, actual_qty, actual_cost]
        self._content_recalc_lock = True
        try:
            for c, val in enumerate(vals): self.content_table.set_value(row, c, val)
        finally:
            self._content_recalc_lock = False
        self.schedule_autosave()

    def add_repair_row(self, item: dict[str, Any]):
        row = self.repair_table.rowCount(); self.repair_table.insertRow(row)
        vals = [item.get("id", ""), item.get("work_item_id", ""), item.get("name", ""), item.get("basis", ""), item.get("cost", "0"), item.get("volume", ""), item.get("act_details", "")]
        for c, val in enumerate(vals): self.repair_table.set_value(row, c, val)
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
                conn.execute("DELETE FROM report_content_items WHERE report_id=?", (rid,))
                for r in range(self.content_table.rowCount()):
                    name = self.content_table.value(r, 2)
                    if not name:
                        continue
                    unit_price = to_float(self.content_table.value(r, 4))
                    plan_qty = to_float(self.content_table.value(r, 5))
                    actual_qty = to_float(self.content_table.value(r, 7))
                    plan_cost = to_float(self.content_table.value(r, 6)) or unit_price * plan_qty
                    actual_cost = to_float(self.content_table.value(r, 8)) or unit_price * actual_qty
                    conn.execute(
                        "INSERT INTO report_content_items(report_id, work_item_id, name, unit, unit_price, plan_qty, plan_cost, actual_qty, actual_cost, source, is_manual, position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, int(self.content_table.value(r,1) or 0) or None, name, self.content_table.value(r,3), unit_price, plan_qty, plan_cost, actual_qty, actual_cost, "ручной ввод/редактирование", 1, r+1),
                    )
                conn.execute("DELETE FROM report_repair_items WHERE report_id=?", (rid,))
                for r in range(self.repair_table.rowCount()):
                    name = self.repair_table.value(r, 2)
                    if not name:
                        continue
                    conn.execute(
                        "INSERT INTO report_repair_items(report_id, work_item_id, name, basis, cost, volume, act_details, source, is_manual, position) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (rid, int(self.repair_table.value(r,1) or 0) or None, name, self.repair_table.value(r,3), to_float(self.repair_table.value(r,4)), self.repair_table.value(r,5), self.repair_table.value(r,6), "ручной ввод/редактирование", 1, r+1),
                    )
                conn.execute("DELETE FROM report_management_items WHERE report_id=?", (rid,))
                for r in range(self.management_table.rowCount()):
                    name = self.management_table.value(r, 2)
                    if not name:
                        continue
                    unit_price = to_float(self.management_table.value(r, 4))
                    qty = to_float(self.management_table.value(r, 5))
                    cost = to_float(self.management_table.value(r, 6)) or unit_price * qty
                    conn.execute(
                        "INSERT INTO report_management_items(report_id, work_item_id, name, unit, unit_price, qty, cost, source, is_manual, position) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (rid, int(self.management_table.value(r,1) or 0) or None, name, self.management_table.value(r,3), unit_price, qty, cost, "ручной ввод/редактирование", 1, r+1),
                    )
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
        finally:
            self._saving_report = False
        if silent:
            self.msg("Автосохранение отчета выполнено")
        else:
            self.msg("Отчет сохранен")
        if refresh_after:
            self.refresh()

    def export(self, kind: str):
        if not self.current_id:
            self.msg("Выберите отчет для выгрузки.")
            return
        self.save()
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


class MassApplyTab(BaseTab):
    PREVIEW_COLUMNS = [
        "Дом ID", "Работа ID", "Дом", "Площадь", "Услуга/работа", "Раздел", "Отчет", "Сумма", "Цена в строке", "Кол-во в строке", "Ед. изм.", "Действие"
    ]

    def __init__(self):
        super().__init__()
        self._preview_dirty = True
        root = QVBoxLayout(self)
        root.addWidget(selectable_label(
            "Массовое заполнение отчетов. Выберите услугу/работу и добавьте ее сразу в несколько отчетов за выбранный год. Перед записью всегда показывается предпросмотр."
        ))
        form = QGridLayout()
        self.company = ClickComboBox()
        self.year = QSpinBox(); self.year.setRange(2000, 2100); self.year.setValue(2026)
        self.work = ClickComboBox()
        self.mode = ClickComboBox(); self.mode.addItems([
            "Цена за 1 м² площади дома",
            "Цена за 1 м² в месяц",
            "Готовая сумма на один дом",
            "Общая сумма на несколько домов, распределить по площади",
            "Цена × количество",
        ])
        self.mode.currentIndexChanged.connect(self.update_mode_help)
        self.rate_label = selectable_label("Ставка / цена")
        self.months_label = selectable_label("Количество месяцев")
        self.quantity_label = selectable_label("Количество")
        self.total_label = selectable_label("Готовая / общая сумма")
        self.rate = QLineEdit(); self.rate.setPlaceholderText("например 2 или 1000")
        self.months = QLineEdit(); self.months.setPlaceholderText("12")
        self.months.setText("12")
        self.quantity = QLineEdit(); self.quantity.setPlaceholderText("например 12")
        self.total = QLineEdit(); self.total.setPlaceholderText("например 60000")
        self.help = selectable_label("")
        self.create_missing = QCheckBox("Создать отчет, если его еще нет")
        self.update_existing = QCheckBox("Обновлять строку, если эта же услуга уже есть")
        self.override_manual = QCheckBox("Разрешить замену строк с ручными правками")
        self.override_manual.setToolTip("По умолчанию ручные изменения защищены от повторного массового заполнения.")
        self.override_manual.setEnabled(False)
        self.update_existing.toggled.connect(self.override_manual.setEnabled)
        self.update_existing.toggled.connect(
            lambda active: self.override_manual.setChecked(False) if not active else None
        )
        form.addWidget(selectable_label("УО"), 0, 0); form.addWidget(self.company, 0, 1)
        form.addWidget(selectable_label("Год"), 0, 2); form.addWidget(self.year, 0, 3)
        form.addWidget(selectable_label("Услуга"), 1, 0); form.addWidget(self.work, 1, 1, 1, 3)
        form.addWidget(selectable_label("Способ расчета"), 2, 0); form.addWidget(self.mode, 2, 1, 1, 3)
        form.addWidget(self.rate_label, 3, 0); form.addWidget(self.rate, 3, 1, 1, 3)
        form.addWidget(self.months_label, 4, 0); form.addWidget(self.months, 4, 1, 1, 3)
        form.addWidget(self.quantity_label, 5, 0); form.addWidget(self.quantity, 5, 1, 1, 3)
        form.addWidget(self.total_label, 6, 0); form.addWidget(self.total, 6, 1, 1, 3)
        form.addWidget(self.create_missing, 7, 1); form.addWidget(self.update_existing, 7, 2)
        form.addWidget(self.override_manual, 8, 1, 1, 3)
        form.addWidget(self.help, 9, 0, 1, 4)
        root.addLayout(form)
        btns = ActionFlowLayout(); preview = QPushButton("Предпросмотр"); preview.clicked.connect(self.preview); apply = QPushButton("Применить"); apply.clicked.connect(self.apply)
        btns.addStretch(); btns.addWidget(preview); btns.addWidget(apply); root.addLayout(btns)
        self.table = CopyPasteTable(0, len(self.PREVIEW_COLUMNS))
        self.table.setHorizontalHeaderLabels(self.PREVIEW_COLUMNS)
        self.table.setColumnHidden(0, True)
        self.table.setColumnHidden(1, True)
        root.addWidget(self.table)
        # Любое изменение условий делает старый предпросмотр недействительным.
        # Это убирает ситуацию, когда пользователь выбрал другую услугу, а таблица
        # все еще показывает старую строку "уже есть, пропуск".
        for widget in (self.company, self.work, self.mode):
            widget.currentIndexChanged.connect(self.mark_dirty)
        self.year.valueChanged.connect(self.mark_dirty)
        self.rate.textChanged.connect(self.mark_dirty)
        self.months.textChanged.connect(self.mark_dirty)
        self.quantity.textChanged.connect(self.mark_dirty)
        self.total.textChanged.connect(self.mark_dirty)
        self.create_missing.stateChanged.connect(self.mark_dirty)
        self.update_existing.stateChanged.connect(self.mark_dirty)
        self.override_manual.stateChanged.connect(self.mark_dirty)
        self.reload()
        self.update_mode_help()

    def mark_dirty(self):
        self._preview_dirty = True
        self.table.setRowCount(0)

    def update_mode_help(self):
        mode = self.mode.currentText()

        is_monthly_m2 = mode.startswith("Цена за 1 м² в месяц")
        is_year_m2 = mode.startswith("Цена за 1 м² площади")
        show_rate = is_year_m2 or is_monthly_m2 or mode.startswith("Цена ×")
        show_months = is_monthly_m2
        show_quantity = mode.startswith("Цена ×")
        show_total = mode.startswith("Готовая сумма") or mode.startswith("Общая сумма")

        for widget in (self.rate_label, self.rate):
            widget.setVisible(show_rate)
        for widget in (self.months_label, self.months):
            widget.setVisible(show_months)
        for widget in (self.quantity_label, self.quantity):
            widget.setVisible(show_quantity)
        for widget in (self.total_label, self.total):
            widget.setVisible(show_total)

        if is_monthly_m2:
            self.rate_label.setText("Ставка за 1 м² в месяц")
            self.rate.setPlaceholderText("например 6,76")
            self.months_label.setText("Количество месяцев")
            self.months.setPlaceholderText("например 12")
            if not self.months.text().strip():
                self.months.setText("12")
            text = "Введите месячную ставку и количество месяцев. В отчет попадет цена за период = ставка × месяцы, количество = площадь дома."
        elif is_year_m2:
            self.rate_label.setText("Ставка за 1 м²")
            self.rate.setPlaceholderText("например 2")
            text = "Введите ставку за 1 м² за весь отчетный период. В строку отчета попадет цена = ставка, количество = площадь дома."
        elif mode.startswith("Готовая сумма"):
            self.total_label.setText("Сумма на один дом")
            self.total.setPlaceholderText("например 60000")
            text = "Введите сумму, которую нужно добавить каждому дому."
        elif mode.startswith("Общая сумма"):
            self.total_label.setText("Общая сумма")
            self.total.setPlaceholderText("например 300000")
            text = "Введите общую сумму. Она будет распределена между домами пропорционально площади."
        else:
            self.rate_label.setText("Цена за единицу")
            self.rate.setPlaceholderText("например 5000")
            self.quantity_label.setText("Количество")
            self.quantity.setPlaceholderText("например 12")
            text = "Введите цену за единицу и количество. Стоимость будет рассчитана как цена × количество."
        self.help.setText(text)

    def reload(self):
        old_company = self.company.currentData()
        fill_combo(self.company, "SELECT id, name FROM companies ORDER BY name", empty="Выберите УО")
        if old_company is not None:
            idx = self.company.findData(old_company)
            if idx >= 0:
                self.company.setCurrentIndex(idx)
        old_data = self.work.currentData()
        old_text = self.work.currentText()
        self.work.blockSignals(True)
        self.work.clear()
        for r in db.rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, category, name"):
            self.work.addItem(work_display(r), r["id"])
        restore_combo_selection(self.work, old_data, old_text)
        self.work.blockSignals(False)
        self.mark_dirty()

    def _section_for_work(self, work: Any | None) -> str:
        return normalize_section(row_get(work, "category", ""), "Содержание")

    def _target_table(self, section: str) -> str:
        low = (section or "").lower()
        if "управ" in low:
            return "report_management_items"
        if "ремонт" in low and "тек" in low:
            return "report_repair_items"
        return "report_content_items"

    def _find_existing(self, conn, table: str, report_id: int, work_id: int | None, name: str):
        if work_id:
            row = conn.execute(f"SELECT id, is_manual FROM {table} WHERE report_id=? AND work_item_id=? LIMIT 1", (report_id, work_id)).fetchone()
            if row:
                return row
        # Запасной поиск для старых строк без work_item_id: считаем дублем только
        # строку с тем же названием в том же разделе/таблице, а не любую услугу отчета.
        return conn.execute(f"SELECT id, is_manual FROM {table} WHERE report_id=? AND TRIM(name)=TRIM(?) LIMIT 1", (report_id, name or "")).fetchone()

    def _calc_for_house(self, house_area: float, total_area: float) -> tuple[float, float, float, str]:
        mode = self.mode.currentText()
        rate = to_float(self.rate.text())
        months = to_float(self.months.text()) or 12.0
        qty = to_float(self.quantity.text())
        total = to_float(self.total.text())
        area = house_area or 0.0
        if mode.startswith("Цена за 1 м² в месяц"):
            line_qty = area
            line_price = rate * months
            cost = line_qty * line_price
            unit = "м²"
        elif mode.startswith("Цена за 1 м² площади"):
            line_qty = area
            line_price = rate
            cost = line_qty * line_price
            unit = "м²"
        elif mode.startswith("Готовая сумма"):
            cost = total
            line_price = cost
            line_qty = 1.0
            unit = "усл."
        elif mode.startswith("Общая сумма"):
            cost = total * (area / (total_area or 1.0))
            line_qty = area if area else 1.0
            line_price = cost / line_qty if line_qty else cost
            unit = "м²" if area else "усл."
        else:
            line_qty = qty if qty else 1.0
            line_price = rate
            cost = line_qty * line_price
            unit = "усл."
        return cost, line_price, line_qty, unit

    def preview(self):
        self.table.setRowCount(0)
        cid = self.company.currentData(); wid = self.work.currentData()
        if not cid or not wid:
            return
        work = db.row("SELECT * FROM work_items WHERE id=?", (wid,))
        if not work:
            return
        service_name = work_display(work)
        section = self._section_for_work(work)
        table = self._target_table(section)
        houses = db.rows("SELECT * FROM houses WHERE company_id=? AND status!='Архив' ORDER BY address", (cid,))
        total_area = sum(to_float(h["total_area"]) for h in houses) or 1.0
        with connect() as conn:
            for h in houses:
                report = conn.execute("SELECT * FROM reports WHERE house_id=? AND year=? ORDER BY id LIMIT 1", (h["id"], self.year.value())).fetchone()
                cost, line_price, line_qty, unit = self._calc_for_house(to_float(h["total_area"]), total_area)
                action = "нет отчета"
                if report:
                    existing = self._find_existing(conn, table, int(report["id"]), int(wid), str(work["name"]))
                    if existing and int(existing["is_manual"] or 0) and not (
                        self.update_existing.isChecked() and self.override_manual.isChecked()
                    ):
                        action = "изменено вручную — защищено, пропуск"
                    elif existing and self.update_existing.isChecked():
                        action = "эта услуга уже есть, будет обновлена"
                    elif existing:
                        action = "эта услуга уже есть, пропуск"
                    else:
                        action = "будет добавлено"
                elif self.create_missing.isChecked():
                    action = "будет создан отчет и добавлено"
                row = self.table.rowCount(); self.table.insertRow(row)
                vals = [h["id"], wid, h["address"], h["total_area"], service_name, section, report["id"] if report else "", money(cost), money(line_price), money(line_qty), unit, action]
                for c, v in enumerate(vals):
                    self.table.set_value(row, c, v)
        self.table.resizeColumnsToContents()
        self._preview_dirty = False

    def apply(self):
        cid = self.company.currentData(); wid = self.work.currentData()
        if not cid or not wid:
            self.msg("Выберите управляющую организацию и услугу/работу.")
            return
        # Всегда пересчитываем предпросмотр перед записью, чтобы не применить
        # устаревшую таблицу после смены услуги, года или параметров заполнения.
        self.preview()
        if self.table.rowCount() == 0:
            self.msg("Нет домов или отчетов для применения.")
            return
        confirmation = "Добавить/обновить выбранную услугу по данным предпросмотра?"
        if self.override_manual.isChecked():
            confirmation += "\n\nВНИМАНИЕ: вручную измененные строки будут перезаписаны!"
        if QMessageBox.question(self, "Применить", confirmation) != QMessageBox.Yes:
            return
        work = db.row("SELECT * FROM work_items WHERE id=?", (wid,))
        if not work:
            return
        section = self._section_for_work(work)
        table = self._target_table(section)
        applied = 0
        skipped = 0
        with connect() as conn:
            for r in range(self.table.rowCount()):
                action = self.table.value(r, 11)
                if "пропуск" in action or action == "нет отчета":
                    skipped += 1
                    continue
                hid = int(self.table.value(r, 0)); row_wid = int(to_float(self.table.value(r, 1)) or wid)
                rid_txt = self.table.value(r, 6)
                if not rid_txt:
                    rid = db.create_report_in_conn(conn, int(cid), hid, int(self.year.value()))
                else:
                    rid = int(to_float(rid_txt))
                cost = to_float(self.table.value(r, 7))
                unit_price = to_float(self.table.value(r, 8))
                qty = to_float(self.table.value(r, 9))
                unit = self.table.value(r, 10) or (work["unit"] if work else "усл.")
                existing = self._find_existing(conn, table, rid, row_wid, str(work["name"]))
                if table == "report_management_items":
                    if existing and self.update_existing.isChecked():
                        conn.execute("UPDATE report_management_items SET unit=?, unit_price=?, qty=?, cost=?, source=?, is_manual=0 WHERE id=?", (unit, unit_price, qty, cost, "массовое применение", existing["id"]))
                        applied += 1
                    elif not existing:
                        pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM report_management_items WHERE report_id=?", (rid,)).fetchone()[0]
                        conn.execute("INSERT INTO report_management_items(report_id, work_item_id, name, unit, unit_price, qty, cost, source, is_manual, position) VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, row_wid, work["name"], unit, unit_price, qty, cost, "массовое применение", 0, pos))
                        applied += 1
                    else:
                        skipped += 1
                elif table == "report_repair_items":
                    volume = f"{money(qty)} {unit}" if qty else unit
                    if existing and self.update_existing.isChecked():
                        conn.execute("UPDATE report_repair_items SET cost=?, volume=?, source=?, is_manual=0 WHERE id=?", (cost, volume, "массовое применение", existing["id"]))
                        applied += 1
                    elif not existing:
                        pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM report_repair_items WHERE report_id=?", (rid,)).fetchone()[0]
                        conn.execute("INSERT INTO report_repair_items(report_id, work_item_id, name, basis, cost, volume, act_details, source, is_manual, position) VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, row_wid, work["name"], "", cost, volume, "", "массовое применение", 0, pos))
                        applied += 1
                    else:
                        skipped += 1
                else:
                    if existing and self.update_existing.isChecked():
                        conn.execute("UPDATE report_content_items SET unit=?, unit_price=?, plan_qty=?, plan_cost=?, actual_qty=?, actual_cost=?, source=?, is_manual=0 WHERE id=?", (unit, unit_price, qty, cost, qty, cost, "массовое применение", existing["id"]))
                        applied += 1
                    elif not existing:
                        pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM report_content_items WHERE report_id=?", (rid,)).fetchone()[0]
                        conn.execute("INSERT INTO report_content_items(report_id, work_item_id, name, unit, unit_price, plan_qty, plan_cost, actual_qty, actual_cost, source, is_manual, position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (rid, row_wid, work["name"], unit, unit_price, qty, cost, qty, cost, "массовое применение", 0, pos))
                        applied += 1
                    else:
                        skipped += 1
            conn.commit()
        make_backup("mass_apply")
        self.msg(f"Массовое заполнение выполнено. Добавлено/обновлено: {applied}. Пропущено: {skipped}")
        self.preview()


class BackupsTab(BaseTab):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(selectable_label("Данные программы хранятся в папке data рядом с EXE/исходным файлом. Резервная копия создается автоматически раз в 5 минут."))
        btns = ActionFlowLayout()
        backup = QPushButton("Создать резервную копию сейчас"); backup.clicked.connect(self.backup_now)
        open_data = QPushButton("Показать путь к папке data"); open_data.clicked.connect(self.show_data_path)
        btns.addWidget(backup); btns.addWidget(open_data); btns.addStretch()
        layout.addLayout(btns)
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
        if self._scale not in (100, 125, 150):
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
            self.works_tab, self.templates_tab, self.reports_tab,
            self.mass_tab, self.backups_tab,
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
        """Mark primary and destructive commands consistently on every page."""
        for button in self.findChildren(QPushButton):
            label = button.text().strip().lower()
            if any(word in label for word in ("удалить", "очистить", "аннулировать")):
                button.setObjectName("DangerAction")
            elif label in ("применить", "сохранить отчет", "сохранить справочник", "выгрузить все форматы"):
                button.setObjectName("PrimaryAction")

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
        self.set_save_status("saved")

    def refresh_context(self):
        """A compact, always visible reporting context without mutating selectors."""
        try:
            company = self.reports_tab.company_filter.currentText().strip()
            house = self.reports_tab.house_filter.currentText().strip()
            year = self.reports_tab.year_filter.value()
            parts = []
            if company and company not in ("Выберите УО", "Выберите организацию", "Все организации"):
                parts.append(f"УО: {company}")
            if house and house not in ("Выберите дом", "Все дома"):
                parts.append(f"Дом: {house}")
            parts.append(f"Год отчета: {year}")
            text = "   •   ".join(parts)
        except (AttributeError, RuntimeError):
            text = "Рабочий контекст"
        self.page_header.set_context(text)

    def switch_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.settings.setValue("ui/theme", self._theme)
        apply_theme(QApplication.instance(), self._theme, self._scale)
        self.sidebar.refresh_theme(self._theme)

    def set_scale(self, scale: int):
        if scale not in (100, 125, 150):
            return
        self._scale = scale
        self.settings.setValue("ui/scale", scale)
        self.sidebar.setFixedWidth(round(245 * scale / 100))
        apply_theme(QApplication.instance(), self._theme, scale)
        self.sidebar.refresh_theme(self._theme)

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
