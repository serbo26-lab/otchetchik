"""Editors for management organizations and apartment buildings."""
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
from .signatories import SignatoryListDialog, count_signatories
from .utils import iso_date, money, to_float, parse_date, format_date
from .ui_components import ContentCard, action_panel
from .ui_editors import (
    BaseTab, ClickComboBox, clear_widgets, empty_record_editor, fill_combo,
    numeric_error, polish_form, selectable_label, set_widgets_enabled,
)
from .ui_labels import (
    CREATE_TITLE, NOTES_LABEL, UNNAMED_LABEL, DELETE_QUESTION, DELETE_CANCELLED,
    NOT_SELECTED, SELECT_COMPANY_MESSAGE, HOUSE_COMPANY_SQL, COMPANY_LIST_SQL,
)


COMPANY_RECORD_SQL = "SELECT * FROM companies WHERE id=?"
HOUSE_RECORD_SQL = "SELECT * FROM houses WHERE id=?"


def _require_record_snapshot(conn, query, record_id, expected):
    record = conn.execute(query, (record_id,)).fetchone()
    if record is None or dict(record) != expected:
        raise ValueError("Запись изменилась после открытия. Операция отменена.")
    return record


def _property_save_error(page, exc):
    win = page.window()
    if hasattr(win, "set_save_status"):
        win.set_save_status("error")
    QMessageBox.warning(page, "Запись не сохранена", str(exc))
    return False


class CompaniesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        self._loaded_record = None
        root = QHBoxLayout(self)
        splitter = QSplitter()
        self.splitter = splitter
        root.addWidget(splitter)
        left = QWidget()
        left_l = QVBoxLayout(left)
        selector = ContentCard("1. Выбор организации", "Добавьте УО или выберите ее из перечня.")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        selector.add_widget(self.list, 1)
        self.add_button = QPushButton("Добавить организацию")
        self.add_button.clicked.connect(self.add_company)
        selector.add_widget(action_panel(CREATE_TITLE, self.add_button, primary=self.add_button))
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
        self.person.setPlaceholderText("ФИО и должность уполномоченного лица")
        self.contact = QLineEdit()
        self.contact.setPlaceholderText("Телефон и адрес электронной почты, при наличии")
        self.notes = QTextEdit(); self.notes.setFixedHeight(80)
        self.form_widgets = [self.name, self.ogrn, self.addr, self.person, self.contact, self.notes]
        basic = ContentCard("2. Основные сведения")
        self.source_note = selectable_label(
            "Эта УО получена из ГИС ЖКХ Ассистента. Название и ОГРН "
            "изменяются только в Ассистенте; дополнительные сведения "
            "для отчёта сохраняются в Отчётчике."
        )
        self.source_note.setObjectName("CardSubtitle")
        basic.add_widget(self.source_note)
        basic_form = QFormLayout()
        basic_form.addRow(selectable_label("Полное наименование"), self.name)
        basic_form.addRow(selectable_label("ОГРН / ИНН"), self.ogrn)
        polish_form(basic_form)
        basic.add_layout(basic_form)
        form_layout.addWidget(basic)

        report_contact = ContentCard("3. Сведения для разъяснений по отчету")
        contact_form = QFormLayout()
        for title, widget in [("Адрес приема населения", self.addr),
                              ("Кто дает разъяснения по отчету", self.person),
                              ("Телефон / email", self.contact)]:
            contact_form.addRow(selectable_label(title), widget)
        polish_form(contact_form)
        report_contact.add_layout(contact_form)
        form_layout.addWidget(report_contact)

        signers = ContentCard(
            "4. Подписанты управляющей организации",
            "ФИО, должности, основания и периоды полномочий. "
            "История сохраняется отдельно для выбора на дату документа."
        )
        self.sign_summary = selectable_label("")
        self.sign_summary.setObjectName("CardSubtitle")
        signers.add_widget(self.sign_summary)
        self.sign_button = QPushButton("Подписанты УО и история полномочий…")
        self.sign_button.clicked.connect(self.open_signatories)
        signers.add_widget(self.sign_button)
        form_layout.addWidget(signers)

        extra = ContentCard("5. Примечания")
        extra_form = QFormLayout()
        extra_form.addRow(selectable_label(NOTES_LABEL), self.notes)
        polish_form(extra_form)
        extra.add_layout(extra_form)
        form_layout.addWidget(extra)

        self.save_button = QPushButton("Сохранить организацию")
        self.save_button.clicked.connect(self.save)
        form_layout.addWidget(action_panel("Сохранение сведений", self.save_button, primary=self.save_button))
        form_layout.addStretch()
        self.editor_stack = empty_record_editor(
            formw, "Управляющая организация не выбрана",
            "Поля реквизитов откроются после создания или выбора УО слева.",
            self.add_company, "Добавить организацию",
        )
        splitter.addWidget(self.editor_stack)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([315, 1000])
        self.refresh()
        self.update_form_state()

    def _clear_form(self):
        clear_widgets(self.form_widgets)

    def update_form_state(self):
        has_record = self.current_id is not None
        link = db.row(
            "SELECT assistant_source_id FROM companies WHERE id=?", (self.current_id,)
        ) if has_record else None
        source_linked = bool(link and link["assistant_source_id"])
        set_widgets_enabled(self.form_widgets + [self.save_button], has_record)
        self.name.setEnabled(has_record and not source_linked)
        self.ogrn.setEnabled(has_record and not source_linked)
        self.name.setToolTip("Название УО изменяется в ГИС ЖКХ Ассистенте." if source_linked else "")
        self.ogrn.setToolTip("ОГРН изменяется в ГИС ЖКХ Ассистенте." if source_linked else "")
        self.source_note.setVisible(source_linked)
        self.delete_button.setEnabled(has_record and not source_linked)
        self.sign_button.setEnabled(has_record)
        self.refresh_sign_summary()
        if hasattr(self, "editor_stack"):
            self.editor_stack.setCurrentIndex(1 if has_record else 0)
        if not has_record:
            self._clear_form()

    def refresh(self):
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        select_row = -1
        for r in db.named_rows("SELECT * FROM companies ORDER BY name, id"):
            linked = bool(r["assistant_source_id"])
            item = QListWidgetItem(
                ("Ассистент · " if linked else "") + (r["name"] or UNNAMED_LABEL)
            )
            item.setToolTip(
                "Данные УО из общей базы (только чтение)" if linked
                else "Локальная организация Отчётчика"
            )
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
        r = db.row(COMPANY_RECORD_SQL, (next_id,)) if next_id else None
        self.current_id = next_id if r is not None else None
        self._loaded_record = dict(r) if r is not None else None
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

    def refresh_sign_summary(self):
        amount = count_signatories(company_id=self.current_id)
        self.sign_summary.setText(
            f"Записей о подписантах УО: {amount}. "
            "Запись лица не означает подписания отчёта."
            if self.current_id else SELECT_COMPANY_MESSAGE
        )

    def open_signatories(self):
        if not self.current_id:
            return
        if self.save(refresh_after=False) is False:
            return
        dialog = SignatoryListDialog(company_id=self.current_id, parent=self)
        dialog.exec()
        self.refresh_sign_summary()

    def add_company(self):
        if self.current_id and self.save() is False:
            return
        cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Новая управляющая организация",))
        self.current_id = cid
        self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == cid:
                self.list.setCurrentRow(i); break

    def delete_company(self):
        record_id = self.current_id
        expected = self._loaded_record
        if not record_id:
            return
        if db.row("SELECT id FROM companies WHERE id=? AND assistant_source_id IS NOT NULL",
                  (record_id,)):
            self.msg("Связанную УО нельзя удалить здесь. Изменения выполняются в Ассистенте.")
            return
        linked = db.row(
            "SELECT (SELECT COUNT(*) FROM houses WHERE company_id=?) + "
            "(SELECT COUNT(*) FROM reports WHERE company_id=?) AS total",
            (record_id, record_id),
        )
        if linked and linked["total"]:
            QMessageBox.warning(
                self, "Удаление невозможно",
                "У организации есть дома или отчеты. Сначала перенесите связанные объекты "
                "в другую УО или удалите их отдельно. Связи не будут потеряны.",
            )
            return
        if QMessageBox.question(self, DELETE_QUESTION, "Удалить выбранную организацию без связанных домов и отчетов?") == QMessageBox.Yes:
            try:
                with connect() as conn:
                    _require_record_snapshot(conn, COMPANY_RECORD_SQL, record_id, expected)
                if make_backup("before_delete_company") is None:
                    raise RuntimeError("Не удалось создать резервную копию.")
                with connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    _require_record_snapshot(conn, COMPANY_RECORD_SQL, record_id, expected)
                    deleted = conn.execute(
                        "DELETE FROM companies WHERE id=? AND assistant_source_id IS NULL "
                        "AND NOT EXISTS(SELECT 1 FROM houses WHERE company_id=?) "
                        "AND NOT EXISTS(SELECT 1 FROM reports WHERE company_id=?)",
                        (record_id, record_id, record_id),
                    )
                    if deleted.rowcount != 1:
                        raise ValueError("Запись или ее связи изменились. Обновите список перед удалением.")
                    conn.commit()
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, f"Не удалось сохранить резервную копию или удалить УО: {exc}")
                return
            self.current_id = None
            self._loaded_record = None
            self.update_form_state()
            self._remember_form_state()
            self.refresh()

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить организацию», затем заполните поля и сохраните.")
            return
        if (getattr(self, "_loaded_form_id", None) == self.current_id
                and self._form_snapshot == self._form_signature()):
            return True
        try:
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                source = _require_record_snapshot(conn, COMPANY_RECORD_SQL, self.current_id, self._loaded_record)
                if source and source["assistant_source_id"]:
                    # Never override linked source identity through the local form.
                    conn.execute(
                        "UPDATE companies SET reception_address=?,report_person=?,"
                        "report_contact=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (self.addr.toPlainText(), self.person.text(), self.contact.text(),
                         self.notes.toPlainText(), self.current_id),
                    )
                else:
                    conn.execute(
                        "UPDATE companies SET name=?, ogrn_inn=?, reception_address=?, report_person=?, report_contact=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (self.name.text(), self.ogrn.text(), self.addr.toPlainText(), self.person.text(), self.contact.text(), self.notes.toPlainText(), self.current_id),
                    )
                saved = dict(conn.execute(COMPANY_RECORD_SQL, (self.current_id,)).fetchone())
                conn.commit()
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _property_save_error(self, exc)
        self._loaded_record = saved
        self._remember_form_state()
        self.msg("Организация сохранена")
        if refresh_after:
            self.refresh()


class HousesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
        self._loaded_record = None
        root = QHBoxLayout(self)
        splitter = QSplitter()
        self.splitter = splitter
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
        houses.add_widget(action_panel(CREATE_TITLE, self.add_button, primary=self.add_button))
        left_l.addWidget(houses, 1)
        self.delete_button = QPushButton("Удалить дом")
        self.delete_button.clicked.connect(self.delete_house)
        left_l.addWidget(action_panel("Удаление выбранного дома", self.delete_button, purpose="danger"))
        splitter.addWidget(left)

        formw = QWidget(); form_l = QVBoxLayout(formw)
        self.company = ClickComboBox()
        self.company.setToolTip(
            "УО определяется выбранным домом. Изменение принадлежности дома "
            "не выполняется через карточку, чтобы не потерять связи отчетов."
        )
        self.address = QTextEdit(); self.address.setFixedHeight(70)
        self.area = QLineEdit()
        self.residential_area = QLineEdit()
        self.non_residential_area = QLineEdit()
        self._loading_area = False
        self.residential_area.textChanged.connect(self._recalculate_area)
        self.non_residential_area.textChanged.connect(self._recalculate_area)
        self.start = QLineEdit(); self.start.setPlaceholderText("дд.мм.гггг или оставить пустым")
        self.end = QLineEdit(); self.end.setPlaceholderText("дд.мм.гггг или оставить пустым")
        self.agreement_number = QLineEdit()
        self.agreement_date = QLineEdit(); self.agreement_date.setPlaceholderText("ДД.ММ.ГГГГ")
        self.agreement_valid_from = QLineEdit()
        self.agreement_valid_from.setPlaceholderText("ДД.ММ.ГГГГ")
        self.agreement_valid_to = QLineEdit()
        self.agreement_valid_to.setPlaceholderText("ДД.ММ.ГГГГ или оставить пустым")
        self.status = ClickComboBox(); self.status.addItems(["В управлении", "Выбыл", "Архив"])
        self.notes = QTextEdit(); self.notes.setFixedHeight(90)
        self.form_widgets = [
            self.company, self.address, self.area,
            self.residential_area, self.non_residential_area,
            self.start, self.end, self.agreement_number, self.agreement_date,
            self.agreement_valid_from, self.agreement_valid_to,
            self.status, self.notes,
        ]

        official = ContentCard("3. Сведения о доме", "Площадь — сумма площадей жилых и нежилых помещений без общего имущества.")
        self.source_note = selectable_label(
            "Дом связан с базой ГИС ЖКХ Ассистента. Адрес и период управления "
            "редактируются только там; статус и примечание — локально. "
            "Недостающие площади и срок действия договора можно дополнить "
            "локально, не изменяя базу Ассистента."
        )
        self.source_note.setObjectName("CardSubtitle")
        official.add_widget(self.source_note)
        official_form = QFormLayout()
        for label, widget in [
            ("Управляющая организация", self.company),
            ("Адрес дома", self.address),
            ("Жилая площадь, м²", self.residential_area),
            ("Нежилая площадь, м²", self.non_residential_area),
            ("Общая площадь помещений, м²", self.area),
        ]:
            official_form.addRow(selectable_label(label), widget)
        polish_form(official_form)
        official.add_layout(official_form)
        form_l.addWidget(official)

        management = ContentCard("4. Период управления")
        management_form = QFormLayout()
        for label, widget in [("Дата начала управления", self.start),
                              ("Дата прекращения управления", self.end),
                              ("Статус", self.status)]:
            management_form.addRow(selectable_label(label), widget)
        polish_form(management_form)
        management.add_layout(management_form)
        form_l.addWidget(management)

        agreement = ContentCard(
            "5. Договор управления",
            "Дата заключения договора отличается от даты начала управления "
            "домом. Период действия договора хранится отдельно."
        )
        agreement_form = QFormLayout()
        for label, widget in [
            ("Номер договора", self.agreement_number),
            ("Дата заключения договора", self.agreement_date),
            ("Начало действия договора", self.agreement_valid_from),
            ("Окончание действия договора", self.agreement_valid_to),
        ]:
            agreement_form.addRow(selectable_label(label), widget)
        polish_form(agreement_form)
        agreement.add_layout(agreement_form)
        form_l.addWidget(agreement)

        signers = ContentCard(
            "6. Подписывающие лица",
            "УО и собственники представлены раздельно. "
            "Можно хранить несколько лиц и периоды их полномочий."
        )
        self.org_sign_summary = selectable_label("")
        signers.add_widget(self.org_sign_summary)
        self.org_sign_button = QPushButton("Подписанты со стороны УО…")
        self.org_sign_button.clicked.connect(self.open_org_signatories)
        signers.add_widget(self.org_sign_button)
        self.house_sign_summary = selectable_label("")
        signers.add_widget(self.house_sign_summary)
        self.house_sign_button = QPushButton("Представители со стороны дома…")
        self.house_sign_button.clicked.connect(self.open_house_signatories)
        signers.add_widget(self.house_sign_button)
        form_l.addWidget(signers)

        other = ContentCard("7. Дополнительно")
        extra_form = QFormLayout()
        extra_form.addRow(selectable_label(NOTES_LABEL), self.notes)
        polish_form(extra_form)
        other.add_layout(extra_form)
        form_l.addWidget(other)

        self.save_button = QPushButton("Сохранить дом"); self.save_button.clicked.connect(self.save)
        form_l.addWidget(action_panel("Сохранение карточки", self.save_button, primary=self.save_button))
        form_l.addStretch(1)
        self.editor_stack = empty_record_editor(
            formw, "Дом не выбран",
            "Выберите управляющую организацию слева и добавьте дом. "
            "После этого откроются адрес, площадь и период управления.",
            self.add_house, "Добавить дом",
        )
        splitter.addWidget(self.editor_stack)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([360, 1000])
        self.reload_companies()
        self.refresh()
        self.update_form_state()

    def _clear_form(self):
        clear_widgets(self.form_widgets)
        self.address.setPlainText("")
        self.notes.setPlainText("")

    def _recalculate_area(self, *_args):
        """Sum known component areas; otherwise keep a legacy/manual total."""
        if self._loading_area:
            return
        values = [self.residential_area.text().strip(),
                  self.non_residential_area.text().strip()]
        complete = all(values) and all(
            numeric_error(v, "Площадь") is None and to_float(v) >= 0
            for v in values
        )
        row = db.row(
            "SELECT assistant_source_id,assistant_area_missing "
            "FROM houses WHERE id=?", (self.current_id,),
        ) if self.current_id else None
        # Assistant's charge_area already represents the total premises area.
        # Editing optional local breakdown fields must never replace that
        # authoritative total in the UI while the database remains unchanged.
        if row and row["assistant_source_id"] and not row["assistant_area_missing"]:
            self.area.setEnabled(False)
            self.area.setToolTip(
                "Итоговая площадь получена из ГИС ЖКХ Ассистента. "
                "Отдельная разбивка на жилую и нежилую площадь необязательна."
            )
            return
        if complete:
            from decimal import Decimal
            amounts = [
                Decimal(v.replace("\xa0", "").replace(" ", "").replace(",", "."))
                for v in values
            ]
            self.area.setText(format(sum(amounts), "f"))
            self.area.setEnabled(False)
            self.area.setToolTip(
                "Автоматически вычисляется как сумма площадей жилых "
                "и нежилых помещений."
            )
        else:
            can_edit = bool(self.current_id and (
                not row or not row["assistant_source_id"]
                or row["assistant_area_missing"]
            ))
            self.area.setEnabled(can_edit)
            self.area.setToolTip(
                "Если составляющие площади неизвестны, общую площадь "
                "можно указать отдельно. Уже сохранённые значения не стираются."
            )

    def refresh_sign_summary(self):
        if not self.current_id:
            self.org_sign_summary.setText("Сначала выберите дом.")
            self.house_sign_summary.setText("Сначала выберите дом.")
            return
        house = db.row(HOUSE_COMPANY_SQL, (self.current_id,))
        cid = house["company_id"] if house else None
        org_count = count_signatories(company_id=cid)
        home_count = count_signatories(house_id=self.current_id)
        self.org_sign_summary.setText(f"Подписантов УО: {org_count}")
        self.house_sign_summary.setText(f"Представителей дома: {home_count}")
        self.org_sign_button.setEnabled(cid is not None)

    def open_org_signatories(self):
        if not self.current_id or self.save(refresh_after=False) is False:
            return
        house = db.row(HOUSE_COMPANY_SQL, (self.current_id,))
        if not house or house["company_id"] is None:
            return
        SignatoryListDialog(company_id=house["company_id"], parent=self).exec()
        self.refresh_sign_summary()

    def open_house_signatories(self):
        if not self.current_id or self.save(refresh_after=False) is False:
            return
        SignatoryListDialog(house_id=self.current_id, parent=self).exec()
        self.refresh_sign_summary()

    def update_form_state(self):
        has_record = self.current_id is not None
        record = db.row(
            "SELECT assistant_source_id,assistant_area_missing,"
            "assistant_residential_missing,assistant_non_residential_missing,"
            "residential_area,non_residential_area "
            "FROM houses WHERE id=?", (self.current_id,),
        ) if has_record else None
        source_linked = bool(record and record["assistant_source_id"])
        set_widgets_enabled(self.form_widgets + [self.save_button], has_record)
        self.company.setEnabled(False)
        if source_linked:
            self.address.setEnabled(False)
            self.start.setEnabled(False)
            self.end.setEnabled(False)
            self.residential_area.setEnabled(bool(record["assistant_residential_missing"]))
            self.non_residential_area.setEnabled(
                bool(record["assistant_non_residential_missing"])
            )
            self.agreement_number.setEnabled(False)
            self.agreement_date.setEnabled(False)
        if source_linked:
            self.source_note.setText(self._source_house_note(record))
        self.source_note.setVisible(source_linked)
        self.delete_button.setEnabled(has_record and not source_linked)
        self.org_sign_button.setEnabled(has_record)
        self.house_sign_button.setEnabled(has_record)
        self.refresh_sign_summary()
        self._recalculate_area()
        if hasattr(self, "editor_stack"):
            self.editor_stack.setCurrentIndex(1 if has_record else 0)
            self.editor_stack.empty_add_button.setEnabled(
                self.company_filter.currentData() is not None
            )
        if not has_record:
            self._clear_form()

    def _on_company_filter_changed(self):
        previous = getattr(self, "_selected_company_filter_id", None)
        if not self._commit_form_before_load():
            self.company_filter.blockSignals(True)
            self.company_filter.setCurrentIndex(max(0, self.company_filter.findData(previous)))
            self.company_filter.blockSignals(False)
            return
        self._selected_company_filter_id = self.company_filter.currentData()
        self.current_id = None
        self.refresh()

    def reload_companies(self):
        old_filter = self.company_filter.currentData()
        companies = db.named_rows(COMPANY_LIST_SQL)
        self.company_filter.blockSignals(True)
        self.company_filter.clear()
        if len(companies) == 1:
            r = companies[0]
            self.company_filter.addItem(str(r["name"] or UNNAMED_LABEL), r["id"])
            self.company_filter.setCurrentIndex(0)
        else:
            self.company_filter.addItem("Выберите организацию", None)
            for r in companies:
                self.company_filter.addItem(str(r["name"] or UNNAMED_LABEL), r["id"])
            if old_filter is not None:
                idx = self.company_filter.findData(old_filter)
                if idx >= 0:
                    self.company_filter.setCurrentIndex(idx)
        self.company_filter.blockSignals(False)
        self._selected_company_filter_id = self.company_filter.currentData()
        fill_combo(self.company, COMPANY_LIST_SQL, empty=NOT_SELECTED)
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
            source_tag = "Ассистент · " if r["assistant_source_id"] else ""
            item = QListWidgetItem(
                f"{source_tag}{r['address'] or 'Без адреса'} · "
                f"{money(r['total_area'])} м²"
            )
            item.setToolTip("Источник: ГИС ЖКХ Ассистент (только чтение)"
                            if r["assistant_source_id"] else "Локальный дом")
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
        if not self._commit_form_before_load():
            return
        r = db.row(HOUSE_RECORD_SQL, (next_id,)) if next_id else None
        self.current_id = next_id if r is not None else None
        self._loaded_record = dict(r) if r is not None else None
        if not r:
            self.update_form_state()
            self._remember_form_state()
            return
        self.update_form_state()
        idx = self.company.findData(r["company_id"])
        self.company.setCurrentIndex(max(0, idx))
        self.address.setPlainText(r["address"])
        self._loading_area = True
        try:
            self.area.setText(str(r["total_area"]) if r["total_area"] is not None else "")
            self.residential_area.setText(
                str(r["residential_area"]) if r["residential_area"] is not None else ""
            )
            self.non_residential_area.setText(
                str(r["non_residential_area"]) if r["non_residential_area"] is not None else ""
            )
        finally:
            self._loading_area = False
        self._recalculate_area()
        self.start.setText(format_date(r["management_start"]))
        self.end.setText(format_date(r["management_end"]))
        self.agreement_number.setText(r["management_agreement_number"])
        self.agreement_date.setText(format_date(r["management_agreement_date"]))
        self.agreement_valid_from.setText(format_date(r["agreement_valid_from"]))
        self.agreement_valid_to.setText(format_date(r["agreement_valid_to"]))
        self.status.setCurrentText(r["status"])
        self.notes.setPlainText(r["notes"])
        self._remember_form_state()

    def add_house(self):
        if self.current_id and self.save() is False:
            return
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
        record_id = self.current_id
        expected = self._loaded_record
        if not record_id:
            return
        if db.row("SELECT id FROM houses WHERE id=? AND assistant_source_id IS NOT NULL",
                  (record_id,)):
            self.msg("Связанный дом нельзя удалить из Отчётчика. Данные ведутся в Ассистенте.")
            return
        linked = db.row("SELECT COUNT(*) AS total FROM reports WHERE house_id=?", (record_id,))
        if linked and linked["total"]:
            QMessageBox.warning(
                self, "Удаление невозможно",
                "У дома есть отчеты. Чтобы сохранить их привязку к дому, удаление запрещено. "
                "Если дом выбыл из управления, установите соответствующий статус.",
            )
            return
        if QMessageBox.question(self, DELETE_QUESTION, "Удалить выбранный дом без отчетов?") == QMessageBox.Yes:
            try:
                with connect() as conn:
                    _require_record_snapshot(conn, HOUSE_RECORD_SQL, record_id, expected)
                if make_backup("before_delete_house") is None:
                    raise RuntimeError("Не удалось создать резервную копию.")
                with connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    _require_record_snapshot(conn, HOUSE_RECORD_SQL, record_id, expected)
                    deleted = conn.execute(
                        "DELETE FROM houses WHERE id=? AND assistant_source_id IS NULL "
                        "AND NOT EXISTS(SELECT 1 FROM reports WHERE house_id=?)",
                        (record_id, record_id),
                    )
                    if deleted.rowcount != 1:
                        raise ValueError("Запись или ее связи изменились. Обновите список перед удалением.")
                    conn.commit()
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, f"Не удалось сохранить резервную копию или удалить дом: {exc}")
                return
            self.current_id = None
            self._loaded_record = None
            self.update_form_state()
            self._remember_form_state()
            self.refresh()

    def _source_house_note(self, record):
        note = (
            "Дом связан с базой ГИС ЖКХ Ассистента. Адрес, даты "
            "управления и реквизиты договора берутся оттуда; "
            "статус, примечание и сроки действия договора — локальные."
        )
        missing_parts = []
        if record["assistant_residential_missing"]:
            missing_parts.append("жилых помещений")
        if record["assistant_non_residential_missing"]:
            missing_parts.append("нежилых помещений")
        if not record["assistant_area_missing"]:
            note += (
                "\nОбщая площадь помещений берётся из итоговой площади "
                "к начислению в Ассистенте либо рассчитывается из "
                "предусмотренных там составляющих."
            )
            if missing_parts:
                note += (
                    " Разбивка на жилую и нежилую площадь указана не полностью; "
                    "это не мешает использовать уже известную общую площадь."
                )
        else:
            note += (
                "\nВ Ассистенте отсутствует итоговая площадь помещений "
                "и не хватает данных для её расчёта. Укажите общую "
                "площадь здесь либо дополните сведения в Ассистенте."
            )
        return note

    def _validate_house_areas(self):
        for field, label in (
            (self.area, "Общая площадь помещений"),
            (self.residential_area, "Площадь жилых помещений"),
            (self.non_residential_area, "Площадь нежилых помещений"),
        ):
            problem = numeric_error(field.text(), label)
            if not problem and field.text().strip() and to_float(field.text()) < 0:
                problem = f"«{label}»: площадь не может быть отрицательной."
            if problem:
                QMessageBox.warning(self, "Проверьте площади", problem)
                return False
        return True

    def _validated_house_dates(self):
        dates = {}
        for key, edit, label in (
            ("management_start", self.start, "Дата начала управления"),
            ("management_end", self.end, "Дата прекращения управления"),
            ("agreement_date", self.agreement_date, "Дата договора"),
            ("agreement_valid_from", self.agreement_valid_from, "Начало действия договора"),
            ("agreement_valid_to", self.agreement_valid_to, "Окончание действия договора"),
        ):
            raw = edit.text().strip()
            if raw and parse_date(raw) is None:
                QMessageBox.warning(
                    self, "Проверьте дату",
                    f"«{label}»: укажите ДД.ММ.ГГГГ либо оставьте пустым.",
                )
                return None
            dates[key] = iso_date(raw)
        if (dates["agreement_valid_from"] and dates["agreement_valid_to"]
                and dates["agreement_valid_to"] < dates["agreement_valid_from"]):
            QMessageBox.warning(
                self, "Проверьте срок",
                "Дата окончания действия договора раньше начала его действия.",
            )
            return None
        return dates

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Сначала выберите или добавьте дом, затем заполните поля и сохраните.")
            return
        if (getattr(self, "_loaded_form_id", None) == self.current_id
                and self._form_snapshot == self._form_signature()):
            return True
        if not self._validate_house_areas():
            return False
        dates = self._validated_house_dates()
        if dates is None:
            return False

        residential = (
            to_float(self.residential_area.text())
            if self.residential_area.text().strip() else None
        )
        non_residential = (
            to_float(self.non_residential_area.text())
            if self.non_residential_area.text().strip() else None
        )
        if residential is not None and non_residential is not None:
            total = residential + non_residential
        else:
            total = to_float(self.area.text())

        try:
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                source = _require_record_snapshot(conn, HOUSE_RECORD_SQL, self.current_id, self._loaded_record)
                if source and source["assistant_source_id"]:
                    # No sourced identity/period/agreement or known component may be
                    # modified from this form. Missing components and contract
                    # validity dates belong only to the Reporter's local database.
                    conn.execute(
                        "UPDATE houses SET "
                        "residential_area=CASE WHEN assistant_residential_missing=1 "
                        "THEN ? ELSE residential_area END,"
                        "non_residential_area=CASE WHEN assistant_non_residential_missing=1 "
                        "THEN ? ELSE non_residential_area END,"
                        "total_area=CASE WHEN assistant_area_missing=1 "
                        "THEN ? ELSE total_area END,"
                        "agreement_valid_from=?,agreement_valid_to=?,"
                        "status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (
                            residential, non_residential, total,
                            dates["agreement_valid_from"], dates["agreement_valid_to"],
                            self.status.currentText(), self.notes.toPlainText(),
                            self.current_id,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE houses SET company_id=?,address=?,total_area=?,"
                        "residential_area=?,non_residential_area=?,"
                        "management_start=?,management_end=?,"
                        "management_agreement_number=?,management_agreement_date=?,"
                        "agreement_valid_from=?,agreement_valid_to=?,"
                        "status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (
                            source["company_id"], self.address.toPlainText(),
                            total, residential, non_residential,
                            dates["management_start"], dates["management_end"],
                            self.agreement_number.text(), dates["agreement_date"],
                            dates["agreement_valid_from"], dates["agreement_valid_to"],
                            self.status.currentText(), self.notes.toPlainText(),
                            self.current_id,
                        ),
                    )
                saved = dict(conn.execute(HOUSE_RECORD_SQL, (self.current_id,)).fetchone())
                conn.commit()
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _property_save_error(self, exc)
        self._loaded_record = saved
        self._remember_form_state()
        self.msg("Дом сохранен")
        if refresh_after:
            self.refresh()
