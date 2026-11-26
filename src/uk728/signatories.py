"""Representatives of the UO and house, with historic periods of authority.

Source-derived rows are read-only mirrors of GIS JKH Assistant act_people.
Manual rows are stored in the Reporter's SQLite, never in Assistant.
Storing a person is NOT signing or certifying any report.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from . import db
from .paths import make_backup
from .utils import format_date, iso_date, parse_date

PERSON_FIELDS = (
    "full_name", "name_genitive", "position", "position_genitive", "role",
    "premises", "gender", "basis_kind", "basis_number", "basis_date",
    "basis_text", "valid_from", "valid_to",
)
DATE_FIELDS = ("basis_date", "valid_from", "valid_to")

UNSPECIFIED_LABEL = "Не указано"


def _normalize(values: dict, *, organization: bool) -> dict:
    fields = {key: str(values.get(key) or "").strip() for key in PERSON_FIELDS}
    if not fields["full_name"]:
        raise ValueError("Укажите ФИО подписанта.")
    for key in DATE_FIELDS:
        raw = fields[key]
        if raw and parse_date(raw) is None:
            raise ValueError("Проверьте даты: используйте ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.")
        fields[key] = iso_date(raw)
    if (fields["valid_from"] and fields["valid_to"]
            and fields["valid_to"] < fields["valid_from"]):
        raise ValueError("Окончание полномочий раньше начала.")
    if organization and not fields["valid_from"]:
        raise ValueError("Для подписанта УО укажите начало полномочий.")
    if organization and not fields["basis_kind"]:
        raise ValueError("Для подписанта УО укажите основание полномочий.")
    if fields["basis_kind"] == "Иное" and not fields["basis_text"]:
        raise ValueError("Для основания «Иное» укажите полный текст.")
    return fields


def list_signatories(*, company_id: int | None = None,
                     house_id: int | None = None) -> list[dict]:
    if (company_id is None) == (house_id is None):
        raise ValueError("Нужно выбрать либо УО, либо дом.")
    field = "company_id" if company_id is not None else "house_id"
    ident = company_id if company_id is not None else house_id
    result = db.named_rows(
        f"SELECT * FROM signatories WHERE {field}=? ORDER BY id", (ident,)
    )
    items = []
    for row in result:
        data = json.loads(row["data_json"])
        items.append({
            "id": row["id"],
            "company_id": row["company_id"],
            "house_id": row["house_id"],
            "assistant_source_id": row["assistant_source_id"],
            **{key: str(data.get(key) or "") for key in PERSON_FIELDS},
        })
    return items


def _validate_existing_signatory(conn, existing_id, company_id, house_id):
    if existing_id is not None:
        row = conn.execute(
            "SELECT * FROM signatories WHERE id=?", (existing_id,)
        ).fetchone()
        if row is None or row["assistant_source_id"] is not None:
            raise ValueError("Исходного подписанта Ассистента нельзя изменять здесь.")
        if row["company_id"] != company_id or row["house_id"] != house_id:
            raise ValueError("Подписант относится к другому дому или УО.")


def _reject_duplicate_signatory(conn, existing_id, company_id, house_id, data):
    field = "company_id" if company_id is not None else "house_id"
    owner_id = company_id if company_id is not None else house_id
    for row in conn.execute(
        f"SELECT id,data_json FROM signatories WHERE {field}=?", (owner_id,)
    ):
        if row["id"] == existing_id:
            continue
        other = json.loads(row["data_json"])
        if (str(other.get("full_name") or "").casefold() ==
                data["full_name"].casefold()
                and str(other.get("valid_from") or "") == data["valid_from"]):
            raise ValueError(
                "Запись подписанта с таким ФИО и началом полномочий уже есть."
            )


def save_local_signatory(values: dict, *, company_id: int | None = None,
                         house_id: int | None = None,
                         existing_id: int | None = None) -> int:
    if (company_id is None) == (house_id is None):
        raise ValueError("Подписант должен относиться к одной УО или одному дому.")
    data = _normalize(values, organization=company_id is not None)
    with db.connect() as conn:
        _validate_existing_signatory(conn, existing_id, company_id, house_id)
        _reject_duplicate_signatory(conn, existing_id, company_id, house_id, data)
        payload = json.dumps(data, ensure_ascii=False)
        if existing_id is not None:
            conn.execute(
                "UPDATE signatories SET data_json=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (payload, existing_id),
            )
            ident = existing_id
        else:
            cursor = conn.execute(
                "INSERT INTO signatories(company_id,house_id,data_json) "
                "VALUES(?,?,?)", (company_id, house_id, payload),
            )
            ident = int(cursor.lastrowid)
        conn.commit()
    return ident


def delete_local_signatory(record_id: int, *, company_id: int | None = None,
                           house_id: int | None = None) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM signatories WHERE id=?", (record_id,)).fetchone()
        if row is None or row["assistant_source_id"] is not None:
            raise ValueError("Удалять исходные записи Ассистента нельзя.")
        if row["company_id"] != company_id or row["house_id"] != house_id:
            raise ValueError("Этот подписант относится к другому объекту.")
    if make_backup("before_delete_signatory") is None:
        raise RuntimeError("Не удалось создать резервную копию.")
    db.execute("DELETE FROM signatories WHERE id=? AND assistant_source_id IS NULL",
               (record_id,))


def count_signatories(*, company_id: int | None = None,
                      house_id: int | None = None) -> int:
    if (company_id is None) == (house_id is None):
        return 0
    field = "company_id" if company_id is not None else "house_id"
    ident = company_id if company_id is not None else house_id
    result = db.row(
        f"SELECT COUNT(*) AS n FROM signatories WHERE {field}=?", (ident,)
    )
    return int(result["n"])


class PersonEditor(QDialog):
    """One signed-party record; supports both manual and imported readonly."""

    def __init__(self, person: dict | None = None, *,
                 organization: bool = False, readonly: bool = False, parent=None,
                 persist=None):
        super().__init__(parent)
        self.original = person or {}
        self.organization = organization
        self.readonly = readonly
        self.result_data: dict | None = None
        self.persist = persist
        self.saved_id = None
        self.setWindowTitle(
            ("Просмотр: " if readonly else "Реквизиты: ") +
            ("подписант УО" if organization else "представитель дома")
        )
        self.resize(670, 610)
        root = QVBoxLayout(self)
        hint = QLabel(
            "Данные из Ассистента доступны только для просмотра. "
            "Исправления внесите в Ассистенте."
            if readonly else
            "Сохранение записывает реквизиты только в базу Отчётчика. "
            "Это не означает подписания документа."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        scrolling = QScrollArea()
        scrolling.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._build_identity_fields(form, organization)
        self._build_role_fields(form, organization)
        self._build_basis_fields(form, organization)
        self._build_authority_fields(form)
        scrolling.setWidget(body)
        root.addWidget(scrolling, 1)
        self._build_buttons(root, readonly)

    def _build_identity_fields(self, form, organization):
        self.fields = {}
        for field, label in (
            ("full_name", "ФИО"),
            ("name_genitive", "ФИО в родительном падеже"),
            ("position", "Должность"),
            ("position_genitive", "Должность в родительном падеже"),
        ):
            item = QLineEdit(str(self.original.get(field) or ""))
            self.fields[field] = item
            if field in ("position", "position_genitive") and not organization:
                continue
            form.addRow(label, item)

    def _build_role_fields(self, form, organization):
        self.role = QComboBox()
        self.role.addItems(
            ["Руководитель", "Уполномоченный сотрудник"]
            if organization else ["Председатель совета МКД", "Уполномоченный собственник"]
        )
        current_role = str(self.original.get("role") or "")
        if current_role and self.role.findText(current_role) < 0:
            self.role.addItem(current_role)
        if current_role:
            self.role.setCurrentText(current_role)
        form.addRow("Роль / статус", self.role)
        premises = QLineEdit(str(self.original.get("premises") or ""))
        self.fields["premises"] = premises
        if not organization:
            form.addRow("Квартира / помещение", premises)
        self.gender = QComboBox()
        self.gender.addItems([UNSPECIFIED_LABEL, "Мужской", "Женский"])
        self.gender.setCurrentIndex({"male": 1, "female": 2}.get(
            str(self.original.get("gender") or ""), 0,
        ))
        form.addRow("Род для склонения", self.gender)

    def _build_basis_fields(self, form, organization):
        self.basis = QComboBox()
        self.basis.addItems(
            [UNSPECIFIED_LABEL, "Устав", "Доверенность", "Иное"]
            if organization else [UNSPECIFIED_LABEL, "Решение ОСС", "Доверенность", "Иное"]
        )
        basis = str(self.original.get("basis_kind") or "")
        if basis and self.basis.findText(basis) < 0:
            self.basis.addItem(basis)
        if basis:
            self.basis.setCurrentText(basis)
        form.addRow("Основание полномочий", self.basis)

    def _build_authority_fields(self, form):
        for field, label in (
            ("basis_number", "Номер документа-основания"),
            ("basis_date", "Дата документа-основания"),
            ("basis_text", "Основание полным текстом"),
            ("valid_from", "Начало полномочий"),
            ("valid_to", "Окончание полномочий"),
        ):
            value = str(self.original.get(field) or "")
            item = QLineEdit(format_date(value) if field in DATE_FIELDS else value)
            if field in DATE_FIELDS:
                item.setPlaceholderText("ДД.ММ.ГГГГ")
            self.fields[field] = item
            form.addRow(label, item)

    def _build_buttons(self, root, readonly):
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close if readonly else
            (QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        )
        if readonly:
            buttons.rejected.connect(self.reject)
        else:
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if readonly:
            for w in [*self.fields.values(), self.role, self.gender, self.basis]:
                w.setEnabled(False)

    def _save(self):
        data = {field: widget.text() for field, widget in self.fields.items()}
        data.update(
            role=self.role.currentText(), basis_kind=(
                "" if self.basis.currentIndex() == 0 else self.basis.currentText()
            ),
            gender={0: "", 1: "male", 2: "female"}[self.gender.currentIndex()],
        )
        try:
            normalized = _normalize(data, organization=self.organization)
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте подписанта", str(exc))
            return
        if self.persist is not None:
            try:
                self.saved_id = self.persist(normalized)
            except (sqlite3.DatabaseError, ValueError, OSError) as exc:
                QMessageBox.warning(self, "Не удалось сохранить", str(exc))
                return
        self.result_data = normalized
        self.accept()


class SignatoryListDialog(QDialog):
    def __init__(self, *, company_id: int | None = None,
                 house_id: int | None = None, parent=None):
        super().__init__(parent)
        if (company_id is None) == (house_id is None):
            raise ValueError("Нужен один владелец записей подписантов.")
        self.company_id = company_id
        self.house_id = house_id
        self.organization = company_id is not None
        self.setWindowTitle(
            "Подписанты управляющей организации" if self.organization
            else "Представители собственников дома"
        )
        self.resize(760, 540)
        root = QVBoxLayout(self)
        help_text = QLabel(
            "Можно хранить несколько лиц и разные периоды полномочий. "
            "Для будущего подписания отчёта нужно будет выбрать лицо "
            "на соответствующую дату. Записи из Ассистента защищены от изменений."
        )
        help_text.setWordWrap(True)
        root.addWidget(help_text)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self.open_selected())
        root.addWidget(self.list, 1)
        actions = QHBoxLayout()
        add = QPushButton("Добавить локально")
        add.clicked.connect(self.add_person)
        self.edit_button = QPushButton("Просмотреть / изменить")
        self.edit_button.clicked.connect(self.open_selected)
        self.delete_button = QPushButton("Удалить локальную запись")
        self.delete_button.clicked.connect(self.delete_selected)
        for button in (add, self.edit_button, self.delete_button):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.list.currentRowChanged.connect(self._selection_state)
        self.refresh()

    def _owner(self):
        return {"company_id": self.company_id, "house_id": self.house_id}

    def refresh(self, selected_id=None):
        self.list.clear()
        self._records = list_signatories(**self._owner())
        target = -1
        for index, record in enumerate(self._records):
            name = record["full_name"]
            role = record["role"] or record["position"] or "без статуса"
            date_label = format_date(record["valid_from"]) or "без даты"
            end = format_date(record["valid_to"])
            line = ("Ассистент · " if record["assistant_source_id"] else "Локально · ")
            line += f"{name} — {role} (с {date_label}" + (f" по {end}" if end else "") + ")"
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, record["id"])
            self.list.addItem(item)
            if record["id"] == selected_id:
                target = index
        if self._records:
            self.list.setCurrentRow(target if target >= 0 else 0)
        self._selection_state()

    def _selected(self):
        row = self.list.currentRow()
        return self._records[row] if 0 <= row < len(self._records) else None

    def _selection_state(self, *_args):
        record = self._selected()
        self.edit_button.setEnabled(record is not None)
        linked = bool(record and record["assistant_source_id"] is not None)
        self.edit_button.setText("Просмотреть" if linked else "Изменить")
        self.delete_button.setEnabled(bool(record and not linked))
        if linked:
            reason = "Запись из Ассистента доступна только для просмотра."
        elif record:
            reason = "Удалить выбранную локальную запись."
        else:
            reason = "Сначала выберите запись."
        self.delete_button.setToolTip(reason)

    def add_person(self):
        editor = PersonEditor(
            organization=self.organization, parent=self,
            persist=lambda values: save_local_signatory(values, **self._owner()),
        )
        if editor.exec() == QDialog.DialogCode.Accepted:
            self.refresh(editor.saved_id)

    def open_selected(self):
        record = self._selected()
        if not record:
            return
        readonly = record["assistant_source_id"] is not None
        editor = PersonEditor(
            record, organization=self.organization,
            readonly=readonly, parent=self,
            persist=None if readonly else lambda values: save_local_signatory(
                values, existing_id=record["id"], **self._owner()
            ),
        )
        if editor.exec() == QDialog.DialogCode.Accepted and not readonly:
            self.refresh(editor.saved_id)

    def delete_selected(self):
        record = self._selected()
        if not record or record["assistant_source_id"] is not None:
            return
        if QMessageBox.question(
            self, "Удаление подписанта",
            f"Удалить локальную запись «{record['full_name']}»? "
            "Ранее созданные отчёты останутся без изменений.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_local_signatory(record["id"], **self._owner())
        except (sqlite3.DatabaseError, ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Не удалось удалить", str(exc))
            return
        self.refresh()
