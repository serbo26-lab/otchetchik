"""Editors for work categories and the work directory."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from . import db
from .db import connect
from .paths import make_backup
from .utils import to_float
from .ui_components import ContentCard, action_panel, table_card, fit_table_columns
from .ui_editors import (
    BaseTab, ClickComboBox, CopyPasteTable, EditableComboDelegate,
    COMMON_SECTIONS, COMMON_UNITS, DELETE_ROWS_TEXT, DELETE_ROWS_TITLE,
    distinct_texts, normalize_section, numeric_error, polish_form,
    restore_combo_selection, selectable_label, set_widgets_enabled,
)
from .ui_labels import (
    NOTES_LABEL, UNNAMED_LABEL, DELETE_QUESTION, DELETE_CANCELLED,
    NOT_SELECTED, SECTION_LABEL, UNIT_LABEL, CATEGORY_BY_ID_SQL,
)


def _category_snapshot(conn, category_id):
    parent = conn.execute(
        "SELECT * FROM work_categories WHERE id=? AND parent_id IS NULL AND is_active=1",
        (category_id,),
    ).fetchone()
    if parent is None:
        return None
    children = [dict(row) for row in conn.execute(
        "SELECT * FROM work_categories WHERE parent_id=? AND is_active=1 ORDER BY name,id",
        (category_id,),
    )]
    return dict(parent), children


class CategoriesTab(BaseTab):
    """Справочник категорий и подкатегорий работ.

    Это не официальный раздел отчета, а удобный пользовательский классификатор:
    пользователь заранее заводит категорию/подкатегории с типовыми настройками,
    а потом быстро добавляет их в справочник работ.
    """
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        self._loaded_subcategory_ids: set[int] = set()
        self._loaded_category_snapshot = None
        root = QHBoxLayout(self)
        splitter = QSplitter(); self.splitter = splitter; root.addWidget(splitter)

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
        self.editor_form = form
        self.name = QLineEdit()
        self.section = ClickComboBox(); self.section.setEditable(True); self.section.addItems([NOT_SELECTED, *COMMON_SECTIONS])
        self.unit = ClickComboBox(); self.unit.setEditable(True); self.unit.addItems([NOT_SELECTED, *COMMON_UNITS])
        self.price = QLineEdit()
        self.notes = QTextEdit(); self.notes.setFixedHeight(55)
        form.addRow(selectable_label("Название категории"), self.name)
        form.addRow(selectable_label(SECTION_LABEL), self.section)
        form.addRow(selectable_label("Единица измерения"), self.unit)
        form.addRow(selectable_label("Цена / ставка"), self.price)
        form.addRow(selectable_label(NOTES_LABEL), self.notes)
        polish_form(form)
        details.add_layout(form)
        right_l.addWidget(details)

        # Добавление подкатегории относится к ее таблице, а не к категории в целом.
        self.sub_table = CopyPasteTable(0, 6)
        self.sub_table.setHorizontalHeaderLabels(["ID", "Название подкатегории", "Раздел", UNIT_LABEL, "Цена/ставка", NOTES_LABEL])
        self.sub_table.setColumnHidden(0, True)
        fit_table_columns(self.sub_table, flex=(1, 5),
                          widths={2: 160, 3: 105, 4: 120})
        self.sub_table.setItemDelegateForColumn(2, EditableComboDelegate(lambda: distinct_texts("work_categories", "section", [NOT_SELECTED, *COMMON_SECTIONS]), self.sub_table))
        self.sub_table.setItemDelegateForColumn(3, EditableComboDelegate(lambda: distinct_texts("work_categories", "unit", [NOT_SELECTED, *COMMON_UNITS]), self.sub_table))
        self.sub_table.context_add_handler = self.add_subcategory
        self.sub_table.allow_paste_append = True
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
        subcategories.add_widget(action_panel(DELETE_ROWS_TITLE, self.delete_sub_button, purpose="danger"))
        subcategories.add_widget(action_panel("Завершение заполнения", self.save_button, primary=self.save_button))
        self.form_widgets = [self.name, self.section, self.unit, self.price, self.notes, self.sub_table]
        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([330, 1100])
        self.refresh()
        self.update_form_state()

    def update_form_state(self):
        has_record = self.current_id is not None
        set_widgets_enabled(self.form_widgets + [self.add_sub_button, self.delete_sub_button, self.save_button], has_record)
        self.delete_button.setEnabled(has_record)
        if not has_record:
            self.name.clear(); self.price.clear(); self.notes.clear(); self.sub_table.setRowCount(0)
            self.section.setCurrentText(NOT_SELECTED); self.unit.setCurrentText(NOT_SELECTED)

    def refresh(self):
        if not self._commit_form_before_load():
            return
        records = db.named_rows("SELECT * FROM work_categories WHERE parent_id IS NULL AND is_active=1 ORDER BY name")
        self._replace_record_list(
            records, lambda record: QListWidgetItem(record["name"] or UNNAMED_LABEL),
            auto_select=self.current_id is None,
        )

    def _load_current(self):
        self._load_selected_record(self._populate_selected_record)

    def _populate_selected_record(self, next_id):
        self.current_id = None
        self._loaded_category_snapshot = None
        self._loaded_subcategory_ids = set()
        snapshot = None
        if next_id is not None:
            with connect() as conn:
                conn.execute("BEGIN")
                snapshot = _category_snapshot(conn, next_id)
        self.current_id = next_id if snapshot is not None else None
        self._loaded_category_snapshot = snapshot
        if snapshot is None:
            self._loaded_subcategory_ids = set()
            self.update_form_state()
            self._remember_form_state()
            return
        r, subcategories = snapshot
        self.update_form_state()
        self.name.setText(r["name"]); self.section.setCurrentText(r["section"] or NOT_SELECTED); self.unit.setCurrentText(r["unit"] or NOT_SELECTED)
        self.price.setText(str(r["default_price"] or "")); self.notes.setPlainText(r["notes"])
        self.sub_table.setRowCount(0)
        self._loaded_subcategory_ids = {int(sub["id"]) for sub in subcategories}
        for sub in subcategories:
            row = self.sub_table.rowCount(); self.sub_table.insertRow(row)
            vals = [sub["id"], sub["name"], sub["section"], sub["unit"], sub["default_price"], sub["notes"]]
            for c, val in enumerate(vals): self.sub_table.set_value(row, c, val)
        # Keep adaptive column modes; resizeColumnsToContents() would
        # shrink the stretched text columns back to their shortest values.
        self._remember_form_state()

    def add_category(self):
        if self.current_id and self.save() is False:
            return
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
        category_id = self.current_id
        expected = self._loaded_category_snapshot
        if not category_id:
            return
        if QMessageBox.question(self, DELETE_QUESTION, "Удалить категорию и ее подкатегории? Справочник работ не будет удален.") == QMessageBox.Yes:
            try:
                with connect() as conn:
                    conn.execute("BEGIN")
                    self._check_subcategory_context(conn, category_id, expected)
                if make_backup("before_category_deletion") is None:
                    raise RuntimeError("Не удалось создать резервную копию.")
                with connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._check_subcategory_context(conn, category_id, expected)
                    conn.execute("UPDATE work_categories SET is_active=0 WHERE id=? OR parent_id=?", (category_id, category_id))
                    conn.commit()
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, str(exc))
                return
            # Do not clear the next category's rows or autosave the deleted one.
            self.current_id = None
            self._loaded_category_snapshot = None
            self._loaded_subcategory_ids = set()
            self.update_form_state()
            self._remember_form_state()
            self.refresh()

    def add_subcategory(self):
        if not self.current_id:
            self.msg("Сначала выберите или создайте категорию.")
            return
        row = self.sub_table.rowCount(); self.sub_table.insertRow(row)
        section = "" if self.section.currentText() == NOT_SELECTED else self.section.currentText()
        unit = "" if self.unit.currentText() == NOT_SELECTED else self.unit.currentText()
        vals = ["", "Новая подкатегория", section, unit, self.price.text() or "0", ""]
        for c, val in enumerate(vals): self.sub_table.set_value(row, c, val)

    def _validated_subcategory_ids(self):
        remaining = set()
        for index in range(self.sub_table.rowCount()):
            if not self.sub_table.value(index, 1).strip():
                raise ValueError(
                    f"Подкатегория, строка {index + 1}: заполните наименование или явно удалите строку."
                )
            raw_id = self.sub_table.value(index, 0)
            if raw_id:
                row_id = int(raw_id)
                if row_id not in self._loaded_subcategory_ids or row_id in remaining:
                    raise ValueError("Состав подкатегорий содержит чужой или повторяющийся ID. Запись отменена.")
                remaining.add(row_id)
        return remaining

    def _check_subcategory_context(self, conn, category_id, expected):
        if (self.current_id != category_id or expected is None
                or self._loaded_category_snapshot != expected
                or _category_snapshot(conn, category_id) != expected):
            raise ValueError(
                "Категория или подкатегории изменились после открытия формы. Операция отменена."
            )

    def _save_subcategories(self, conn, section, unit):
        existing_ids: list[int] = []
        new_subrows: list[tuple[int, int]] = []
        for r in range(self.sub_table.rowCount()):
            name = self.sub_table.value(r, 1)
            sub_id = self.sub_table.value(r, 0)
            sub_section = self.sub_table.value(r, 2)
            sub_unit = self.sub_table.value(r, 3)
            if sub_section == NOT_SELECTED: sub_section = ""
            if sub_unit == NOT_SELECTED: sub_unit = ""
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
        return new_subrows

    def _category_price_error(self):
        problem = numeric_error(self.price.text(), "Цена / ставка категории")
        for index in range(self.sub_table.rowCount()):
            problem = problem or numeric_error(
                self.sub_table.value(index, 4), f"Цена подкатегории, строка {index + 1}"
            )
        return problem

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить категорию», затем заполните поля и сохраните.")
            return
        if not self.has_unsaved_changes():
            return True
        section = "" if self.section.currentText() == NOT_SELECTED else self.section.currentText()
        unit = "" if self.unit.currentText() == NOT_SELECTED else self.unit.currentText()
        if normalize_section(section, "") not in COMMON_SECTIONS:
            QMessageBox.warning(self, SECTION_LABEL,
                "Выберите раздел формы: Содержание, Текущий ремонт или Управление.")
            return False
        problem = self._category_price_error()
        if problem:
            QMessageBox.warning(self, "Проверьте стоимость", problem)
            return False
        try:
            remaining = self._validated_subcategory_ids()
            if self._loaded_subcategory_ids - remaining:
                if make_backup("before_subcategory_deletion") is None:
                    raise RuntimeError("Не удалось создать резервную копию перед удалением подкатегорий.")
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._check_subcategory_context(conn, self.current_id, self._loaded_category_snapshot)
                conn.execute(
                    "UPDATE work_categories SET name=?, section=?, unit=?, default_price=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (self.name.text(), section, unit, to_float(self.price.text()), self.notes.toPlainText(), self.current_id),
                )
                new_subrows = self._save_subcategories(conn, section, unit)
                saved_snapshot = _category_snapshot(conn, self.current_id)
                conn.commit()
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            QMessageBox.warning(self, "Категория не сохранена", str(exc))
            return False
        for row_index, new_id in new_subrows:
            self.sub_table.set_value(row_index, 0, new_id)
        self._loaded_subcategory_ids = remaining | {new_id for _, new_id in new_subrows}
        self._loaded_category_snapshot = saved_snapshot
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
        self.table.setHorizontalHeaderLabels(["ID", "Категория", "Подкатегория", "Наименование", SECTION_LABEL, UNIT_LABEL, "Цена/ставка", NOTES_LABEL])
        self.table.setColumnHidden(0, True)
        fit_table_columns(self.table, flex=(1, 3, 7),
                          widths={2: 180, 4: 145, 5: 100, 6: 125})
        self.table.setItemDelegateForColumn(1, EditableComboDelegate(lambda: self.category_names(), self.table))
        self.table.setItemDelegateForColumn(2, EditableComboDelegate(lambda: self.subcategory_names(), self.table))
        self.table.setItemDelegateForColumn(4, EditableComboDelegate(lambda: distinct_texts("work_items", "category", COMMON_SECTIONS), self.table))
        self.table.setItemDelegateForColumn(5, EditableComboDelegate(lambda: distinct_texts("work_items", "unit", COMMON_UNITS), self.table))
        self.table.context_add_handler = self.add_row
        self.table.allow_paste_append = True
        self.form_widgets = [self.table]
        self._initial_work_rows = {}
        self._loaded_work_records = {}
        entries = table_card("2. Справочник работ и услуг", self.table,
            subtitle="Заполните раздел формы, наименование, единицу измерения и цену.")
        search_row = QHBoxLayout()
        self.work_search = QLineEdit()
        self.work_search.setPlaceholderText("Поиск по названию, категории, разделу или единице…")
        self.work_search.setClearButtonEnabled(True)
        self.work_search.textChanged.connect(self.filter_rows)
        search_row.addWidget(self.work_search, 1)
        self.work_match_count = QLabel("Показано: 0")
        self.work_match_count.setObjectName("CardSubtitle")
        search_row.addWidget(self.work_match_count)
        entries.body.insertLayout(2, search_row)
        self.add_manual_button = QPushButton("Добавить работу вручную")
        self.add_manual_button.clicked.connect(self.add_row)
        entries.add_widget(action_panel("Добавление строки", self.add_manual_button,
                                       primary=self.add_manual_button))
        root.addWidget(entries, 1)
        delete = QPushButton(DELETE_ROWS_TEXT)
        delete.clicked.connect(self.table.delete_selected_rows)
        save = QPushButton("Сохранить справочник")
        save.clicked.connect(self.save)
        entries.add_widget(action_panel(DELETE_ROWS_TITLE, delete, purpose="danger"))
        entries.add_widget(action_panel("3. Сохранение справочника", save, primary=save))
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
        self.category_combo.clear(); self.category_combo.addItem(NOT_SELECTED, None)
        for r in db.named_rows("SELECT * FROM work_categories WHERE parent_id IS NULL AND is_active=1 ORDER BY name"):
            self.category_combo.addItem(r["name"], r["id"])
        restore_combo_selection(self.category_combo, old_data, old_text)
        self.category_combo.blockSignals(False)
        self.reload_subcategory_combo()

    def reload_subcategory_combo(self):
        old_data = self.subcategory_combo.currentData(); old_text = self.subcategory_combo.currentText()
        self.subcategory_combo.blockSignals(True)
        self.subcategory_combo.clear(); self.subcategory_combo.addItem(NOT_SELECTED, None)
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

    def filter_rows(self, *_args):
        query = self.work_search.text().strip().casefold()
        shown = 0
        for row in range(self.table.rowCount()):
            searchable = " ".join(self.table.value(row, col) for col in range(1, 8)).casefold()
            matches = not query or query in searchable
            self.table.setRowHidden(row, not matches)
            shown += int(matches)
        self.work_match_count.setText(f"Показано: {shown} из {self.table.rowCount()}")

    def _row_signature(self, row):
        return tuple(self.table.value(row, col) for col in range(1, 8))

    def refresh(self):
        records = db.named_rows(
            "SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, category, name"
        )
        self.table.setRowCount(0)
        for r in records:
            row = self.table.rowCount(); self.table.insertRow(row)
            vals = [r["id"], r["group_name"], r["subgroup_name"], r["name"], r["category"], r["unit"], r["default_price"], r["notes"]]
            for col, val in enumerate(vals):
                self.table.set_value(row, col, val)
        # Preserve the flexible widths configured at table creation.
        self._initial_work_rows = {
            self.table.value(row, 0): self._row_signature(row)
            for row in range(self.table.rowCount())
        }
        self._loaded_work_records = {int(record["id"]): record for record in records}
        self._remember_form_state()
        self.filter_rows()

    def add_row(self):
        self.work_search.clear()
        row = self.table.rowCount(); self.table.insertRow(row)
        for c, val in enumerate(["", "", "", "Новая работа", "", "усл.", "0", ""]):
            self.table.set_value(row, c, val)
        self.table.setCurrentCell(row, 3)
        self.table.scrollToBottom()
        self.filter_rows()

    def add_from_category(self):
        cid = self.category_combo.currentData()
        sid = self.subcategory_combo.currentData()
        if not cid and not sid:
            self.add_row(); return
        cat = db.row(CATEGORY_BY_ID_SQL, (cid,)) if cid else None
        sub = db.row(CATEGORY_BY_ID_SQL, (sid,)) if sid else None
        source = sub or cat
        if not source:
            self.add_row(); return
        group_name = cat["name"] if cat else ""
        subgroup_name = sub["name"] if sub else ""
        name = subgroup_name or group_name or "Новая работа"
        self.work_search.clear()
        row = self.table.rowCount(); self.table.insertRow(row)
        vals = ["", group_name, subgroup_name, name, source["section"], source["unit"], source["default_price"], source["notes"]]
        for c, val in enumerate(vals): self.table.set_value(row, c, val)
        self.table.setCurrentCell(row, 3)
        self.table.scrollToBottom()
        self.filter_rows()

    def _validate_work_rows(self):
        seen = set()
        for index in range(self.table.rowCount()):
            existing = self.table.value(index, 0)
            if existing:
                if existing not in self._initial_work_rows or existing in seen:
                    QMessageBox.warning(
                        self, "Проверьте справочник",
                        f"Строка {index + 1}: неизвестный или повторяющийся ID работы.",
                    )
                    return False
                seen.add(existing)
            if existing and self._initial_work_rows.get(existing) == self._row_signature(index):
                continue  # Preserve unchanged legacy work, including missing section.
            name = self.table.value(index, 3)
            section = self.table.value(index, 4)
            if not name.strip() or normalize_section(section, "") not in COMMON_SECTIONS:
                QMessageBox.warning(
                    self, "Проверьте справочник",
                    f"Строка {index + 1}: заполните наименование и раздел формы "
                    "(Содержание, Текущий ремонт или Управление).",
                )
                return False
            problem = numeric_error(self.table.value(index, 6), f"Цена работы, строка {index + 1}")
            if problem:
                QMessageBox.warning(self, "Проверьте цену", problem)
                return False
        return True

    def _save_work_rows(self, conn):
        active_ids: list[int] = []
        for r in range(self.table.rowCount()):
            wid = self.table.value(r, 0)
            vals = (
                self.table.value(r, 1),
                self.table.value(r, 2),
                self.table.value(r, 3),
                "" if self.table.value(r, 4) == NOT_SELECTED else self.table.value(r, 4),
                "" if self.table.value(r, 5) == NOT_SELECTED else (self.table.value(r, 5) or "усл."),
                to_float(self.table.value(r, 6)),
                self.table.value(r, 7),
            )
            if wid:
                if self._initial_work_rows[wid] != self._row_signature(r):
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

    def save(self, *_args, refresh_after: bool = True):
        # Accept the same optional refresh_after argument as other tabs:
        # save_before_assistant_sync() invokes this from the Works tab.
        # Navigation and timed backups should not rewrite an untouched
        # directory or block users because of incomplete legacy records.
        if self._form_signature() == getattr(self, "_form_snapshot", None):
            return True
        if not self._validate_work_rows():
            return False
        visible_ids = {
            int(self.table.value(row, 0))
            for row in range(self.table.rowCount()) if self.table.value(row, 0)
        }
        try:
            if self._loaded_work_records.keys() - visible_ids:
                if make_backup("before_work_deletion") is None:
                    raise RuntimeError("Не удалось создать резервную копию справочника перед удалением.")
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = {int(row["id"]): dict(row) for row in conn.execute(
                    "SELECT * FROM work_items WHERE is_active=1"
                )}
                if current != self._loaded_work_records:
                    raise ValueError(
                        "Справочник изменился после открытия. Запись отменена, введенные данные сохранены в форме."
                    )
                self._save_work_rows(conn)
                conn.commit()
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            win = self.window()
            if hasattr(win, "set_save_status"):
                win.set_save_status("error")
            QMessageBox.warning(self, "Справочник не сохранен", str(exc))
            return False
        # Reconcile generated IDs and the in-memory snapshot even when the
        # caller requested no refresh. Otherwise newly added rows could be
        # inserted twice on a subsequent edit/save.
        self.refresh()
        self.msg("Справочник сохранен")
        # Preserve the existing implicit None success result: callers and
        # historical UI tests depend on that contract.
