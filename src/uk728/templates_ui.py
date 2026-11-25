"""Template composition editor."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from . import db
from .db import connect
from .paths import make_backup
from .utils import to_float
from .template_storage import save_template
from .ui_components import ContentCard, action_panel, table_card, fit_table_columns
from .ui_editors import (
    BaseTab, ClickComboBox, CopyPasteTable, EditableComboDelegate,
    COMMON_SECTIONS, COMMON_UNITS, SECTION_CONTENT, DELETE_ROWS_TEXT,
    DELETE_ROWS_TITLE, distinct_texts, empty_record_editor, normalize_section,
    numeric_error, polish_form, restore_combo_selection, row_get,
    selectable_label, set_widgets_enabled, work_display,
)
from .ui_labels import (
    CREATE_TITLE, NOTES_LABEL, WORK_ID_LABEL, UNIT_LABEL, QUANTITY_LABEL,
    DELETE_QUESTION, DELETE_CANCELLED,
)


class TemplatesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        root = QHBoxLayout(self)
        splitter = QSplitter(); self.splitter = splitter; root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        selection = ContentCard("1. Выберите шаблон", "Шаблон относится к одному разделу отчета.")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        selection.add_widget(self.list, 1)
        add = QPushButton("Добавить шаблон")
        add.clicked.connect(self.add_template)
        selection.add_widget(action_panel(CREATE_TITLE, add, primary=add))
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
        form.addRow(selectable_label(NOTES_LABEL), self.notes)
        self.form_widgets = [self.name, self.category, self.notes]
        polish_form(form)
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
        self.table.setHorizontalHeaderLabels(["ID", WORK_ID_LABEL, "Наименование", UNIT_LABEL, "Цена", "Кол-во по перечню"])
        self.table.setColumnHidden(0, True); self.table.setColumnHidden(1, True)
        fit_table_columns(self.table, flex=(2,),
                          widths={3: 110, 4: 120, 5: 145})
        self.table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("work_items", "unit", COMMON_UNITS), self.table))
        self.table.context_add_handler = self.add_work_to_template
        composition = table_card("4. Состав шаблона", self.table,
                                 subtitle="Проверьте цену и количество в каждой строке.")
        right_l.addWidget(composition, 1)
        self.remove_item_button = QPushButton(DELETE_ROWS_TEXT)
        self.remove_item_button.clicked.connect(self.table.delete_selected_rows)
        self.save_button = QPushButton("Сохранить шаблон")
        self.save_button.clicked.connect(self.save)
        composition.add_widget(action_panel(DELETE_ROWS_TITLE, self.remove_item_button, purpose="danger"))
        composition.add_widget(action_panel("Завершение заполнения", self.save_button, primary=self.save_button))
        self.editor_stack = empty_record_editor(
            right, "Шаблон не выбран",
            "Выберите шаблон слева или создайте новый. "
            "Только после этого станут доступны его состав и сохранение.",
            self.add_template, "Добавить шаблон",
        )
        splitter.addWidget(self.editor_stack)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([315, 1010])
        self.reload_work_combo()
        self.refresh()
        self.update_form_state()

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.work_combo, self.table,
                            self.add_item_button, self.remove_item_button, self.save_button], has_record)
        self.delete_button.setEnabled(has_record)
        if hasattr(self, "editor_stack"):
            self.editor_stack.setCurrentIndex(1 if has_record else 0)
        self.add_item_button.setEnabled(bool(has_record and self.work_combo.currentData() is not None))
        if not has_record:
            self.name.clear(); self.notes.clear(); self.table.setRowCount(0)

    def reload_work_combo(self):
        old_data = self.work_combo.currentData()
        old_text = self.work_combo.currentText()
        self.work_combo.blockSignals(True)
        self.work_combo.clear()
        section = normalize_section(self.category.currentText(), "")
        rows_ = db.named_rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, category, name")
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
        for r in db.named_rows("SELECT * FROM templates ORDER BY name"):
            item = QListWidgetItem(f"{r['name']} · {r['category']}")
            item.setData(Qt.UserRole, r["id"])
            self.list.addItem(item)
            if target_id is not None and r["id"] == target_id:
                select_row = self.list.count() - 1
        self.list.blockSignals(False)
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
        elif self.list.count() == 1 and target_id is None:
            self.list.setCurrentRow(0)

    def _load_current(self):
        item = self.list.currentItem()
        next_id = item.data(Qt.UserRole) if item else None
        if not self._commit_form_before_load():
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
        for it in db.named_rows("SELECT * FROM template_items WHERE template_id=? ORDER BY position,id", (self.current_id,)):
            row = self.table.rowCount(); self.table.insertRow(row)
            vals = [it["id"], it["work_item_id"] or "", it["name"], it["unit"], it["unit_price"], it["plan_qty"]]
            for c, val in enumerate(vals): self.table.set_value(row, c, val)
        # Preserve the flexible widths configured at table creation.
        self._remember_form_state()

    def add_template(self):
        if self.current_id and self.save() is False:
            return
        tid = db.execute("INSERT INTO templates(name, category) VALUES(?,?)", ("Новый шаблон", SECTION_CONTENT))
        self.current_id = tid; self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == tid:
                self.list.setCurrentRow(i); break

    def delete_template(self):
        if self.current_id and QMessageBox.question(self, DELETE_QUESTION, "Удалить шаблон?") == QMessageBox.Yes:
            try:
                if make_backup("before_delete_template") is None:
                    raise RuntimeError("Не удалось создать резервную копию.")
                db.execute("DELETE FROM templates WHERE id=?", (self.current_id,))
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, f"Не удалось сохранить резервную копию или удалить шаблон: {exc}")
                return
            # Clear the deleted editor and its snapshot before refresh can
            # select another template and populate its rows.
            self.current_id = None
            self.update_form_state()
            self._remember_form_state()
            self.refresh()

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
        if not r or normalize_section(r["category"], "") != normalize_section(self.category.currentText(), ""):
            QMessageBox.warning(self, "Раздел шаблона", "Работа не соответствует разделу выбранного шаблона.")
            return
        row = self.table.rowCount(); self.table.insertRow(row)
        vals = ["", wid, r["name"], r["unit"], r["default_price"], "0"]
        for c, val in enumerate(vals): self.table.set_value(row, c, val)

    def _validate_template_rows(self, section):
        for index in range(self.table.rowCount()):
            for column, label in ((4, "Цена"), (5, QUANTITY_LABEL)):
                problem = numeric_error(
                    self.table.value(index, column), f"{label} шаблона, строка {index + 1}"
                )
                if problem:
                    QMessageBox.warning(self, "Проверьте числа", problem)
                    return False
            wid = int(self.table.value(index, 1) or 0)
            if not wid:
                continue  # Retained legacy row with no active work ID.
            work = db.row("SELECT category FROM work_items WHERE id=?", (wid,))
            if work and normalize_section(work["category"], "") != section:
                QMessageBox.warning(
                    self, "Несоответствие шаблона",
                    f"Работа в строке {index + 1} относится к другому разделу. "
                    "Исправьте состав шаблона или верните прежний раздел.",
                )
                return False
        return True

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить шаблон», затем заполните поля и сохраните.")
            return
        # Navigation, sync and timer saves must not mutate untouched templates.
        if (getattr(self, "_loaded_form_id", None) == self.current_id
                and not self.has_unsaved_changes()):
            return
        section = normalize_section(self.category.currentText(), "")
        if section not in COMMON_SECTIONS:
            QMessageBox.warning(self, "Раздел шаблона", "Выберите официальный раздел формы.")
            return False
        if not self._validate_template_rows(section):
            return False
        rows = [
            {
                "id": self.table.value(r, 0),
                "work_item_id": int(self.table.value(r, 1) or 0) or None,
                "name": self.table.value(r, 2),
                "unit": self.table.value(r, 3),
                "unit_price": to_float(self.table.value(r, 4)),
                "plan_qty": to_float(self.table.value(r, 5)),
            }
            for r in range(self.table.rowCount())
        ]
        try:
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                inserted = save_template(
                    conn, self.current_id, self.name.text(),
                    self.category.currentText(), self.notes.toPlainText(), rows,
                )
                conn.commit()
        except (ValueError, OSError, sqlite3.Error) as exc:
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            QMessageBox.warning(self, "Шаблон не сохранён", str(exc))
            return False
        # Reconcile hidden row IDs even when saving before a selection change.
        for index, row_id in inserted:
            self.table.set_value(index, 0, row_id)
        self._remember_form_state()
        self.msg("Шаблон сохранен")
        if refresh_after:
            self.refresh()
