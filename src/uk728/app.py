from __future__ import annotations

import sys
import traceback
from pathlib import Path
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, QSettings
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QSizePolicy,
    QGroupBox,
    QFrame,
    QStackedWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import db, assistant_source
from .signatories import SignatoryListDialog, count_signatories
from .db import connect
from .exporters import export_docx, export_pdf, export_xlsx
from .paths import make_backup
from .backup_restore import apply_pending_restore
from .backups_ui import BackupsTab
from .utils import full_year_period, iso_date, money, to_float, parse_date, format_date
from .ui_style import apply_theme
from .ui_shell import ActionFlowLayout, NavigationSidebar, PageChrome, PAGES
from .report_storage import sync_report_rows
from .template_storage import save_template
from .report_review import inspect_report, review_text, bundle_fingerprint
from .mass_apply_ui import MassApplyTab, MassPreviewTable
from .ui_labels import COST_LABEL, QUANTITY_LABEL, REPORT_YEAR_LABEL
from .ui_components import (
    ActionPanel, ContentCard, action_panel, describe_context,
    table_card, table_card_with_selection, fit_table_columns,
)

from .ui_editors import (
    SECTION_CONTENT, SECTION_REPAIR, SECTION_MANAGEMENT,
    DELETE_ROWS_TITLE, DELETE_ROWS_TEXT, ADD_ROW_TEXT,
    selectable_label, polish_form, empty_record_editor, COMMON_SECTIONS,
    COMMON_UNITS, COMMON_FILL_MODES, COMMON_PAYER_TYPES, COMMON_REPAIR_BASIS,
    numeric_error, distinct_texts, ClickComboBox, SafeSpinBox,
    EditableComboDelegate, CopyPasteTable, BaseTab, form_scroll,
    restore_combo_selection, row_get, normalize_section, work_display,
    fill_combo, set_widgets_enabled, clear_widgets,
)

APP_TITLE = "Отчетность УК по приказу № 728/пр"

CREATE_TITLE = "Создание"
NOTES_LABEL = "Примечание"
UNNAMED_LABEL = "Без названия"
SELECT_COMPANY_MESSAGE = "Сначала выберите управляющую организацию."
DELETE_QUESTION = "Удалить?"
DELETE_CANCELLED = "Удаление отменено"
HOUSE_COMPANY_SQL = "SELECT company_id FROM houses WHERE id=?"
COMPANY_LIST_SQL = "SELECT id, name FROM companies ORDER BY name"
NOT_SELECTED = "Не выбрано"
SECTION_LABEL = "Раздел формы"
UNIT_LABEL = "Ед. изм."
CATEGORY_BY_ID_SQL = "SELECT * FROM work_categories WHERE id=?"
WORK_ID_LABEL = "Работа ID"
ADD_REFERENCE_TEXT = "Добавить из справочника"
DATE_FORMAT_HINT = "дд.мм.гггг или гггг-мм-дд"
TEMPLATE_LABEL = "Шаблон:"
APPLY_TEMPLATE_TEXT = "Применить шаблон"
SECTION_CONTENT_LABEL = "Наполнение раздела"
MANUAL_SOURCE = "ручной ввод/редактирование"
TEMPLATE_SOURCE = "шаблон"
MASS_SOURCE = "массовое применение"
REFERENCE_SOURCE = "справочник"
NEXT_REPORTS_TEXT = "Далее: отчеты →"


class CompaniesTab(BaseTab):
    def __init__(self):
        super().__init__()
        self.current_id: int | None = None
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
        if not self.current_id:
            return
        if db.row("SELECT id FROM companies WHERE id=? AND assistant_source_id IS NOT NULL",
                  (self.current_id,)):
            self.msg("Связанную УО нельзя удалить здесь. Изменения выполняются в Ассистенте.")
            return
        linked = db.row(
            "SELECT (SELECT COUNT(*) FROM houses WHERE company_id=?) + "
            "(SELECT COUNT(*) FROM reports WHERE company_id=?) AS total",
            (self.current_id, self.current_id),
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
                make_backup("before_delete_company")
                db.execute("DELETE FROM companies WHERE id=?", (self.current_id,))
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, f"Не удалось сохранить резервную копию или удалить УО: {exc}")
                return
            self.current_id = None
            self.refresh()
            self.update_form_state()

    def save(self, *_args, refresh_after: bool = True):
        if not self.current_id:
            self.msg("Нажмите «Добавить организацию», затем заполните поля и сохраните.")
            return
        if (getattr(self, "_loaded_form_id", None) == self.current_id
                and self._form_snapshot == self._form_signature()):
            return True
        with connect() as conn:
            source = conn.execute(
                "SELECT assistant_source_id FROM companies WHERE id=?", (self.current_id,)
            ).fetchone()
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
        if not self.current_id:
            return
        if db.row("SELECT id FROM houses WHERE id=? AND assistant_source_id IS NOT NULL",
                  (self.current_id,)):
            self.msg("Связанный дом нельзя удалить из Отчётчика. Данные ведутся в Ассистенте.")
            return
        linked = db.row("SELECT COUNT(*) AS total FROM reports WHERE house_id=?", (self.current_id,))
        if linked and linked["total"]:
            QMessageBox.warning(
                self, "Удаление невозможно",
                "У дома есть отчеты. Чтобы сохранить их привязку к дому, удаление запрещено. "
                "Если дом выбыл из управления, установите соответствующий статус.",
            )
            return
        if QMessageBox.question(self, DELETE_QUESTION, "Удалить выбранный дом без отчетов?") == QMessageBox.Yes:
            try:
                make_backup("before_delete_house")
                db.execute("DELETE FROM houses WHERE id=?", (self.current_id,))
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, f"Не удалось сохранить резервную копию или удалить дом: {exc}")
                return
            self.current_id = None
            self.refresh()
            self.update_form_state()

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

        with connect() as conn:
            source = conn.execute(
                "SELECT assistant_source_id,assistant_area_missing,"
                "assistant_residential_missing,assistant_non_residential_missing "
                "FROM houses WHERE id=?", (self.current_id,)
            ).fetchone()
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
                        self.company.currentData(), self.address.toPlainText(),
                        total, residential, non_residential,
                        dates["management_start"], dates["management_end"],
                        self.agreement_number.text(), dates["agreement_date"],
                        dates["agreement_valid_from"], dates["agreement_valid_to"],
                        self.status.currentText(), self.notes.toPlainText(),
                        self.current_id,
                    ),
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
        target_id = self.current_id
        self.list.blockSignals(True)
        self.list.clear()
        select_row = -1
        for r in db.named_rows("SELECT * FROM work_categories WHERE parent_id IS NULL AND is_active=1 ORDER BY name"):
            item = QListWidgetItem(r["name"] or UNNAMED_LABEL)
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
        if not self._commit_form_before_load():
            return
        self.current_id = next_id
        r = db.row(CATEGORY_BY_ID_SQL, (self.current_id,)) if self.current_id else None
        if not r:
            self.update_form_state()
            self._remember_form_state()
            return
        self.update_form_state()
        self.name.setText(r["name"]); self.section.setCurrentText(r["section"] or NOT_SELECTED); self.unit.setCurrentText(r["unit"] or NOT_SELECTED)
        self.price.setText(str(r["default_price"] or "")); self.notes.setPlainText(r["notes"])
        self.sub_table.setRowCount(0)
        for sub in db.named_rows("SELECT * FROM work_categories WHERE parent_id=? AND is_active=1 ORDER BY name", (self.current_id,)):
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
        if not self.current_id:
            return
        if QMessageBox.question(self, DELETE_QUESTION, "Удалить категорию и ее подкатегории? Справочник работ не будет удален.") == QMessageBox.Yes:
            try:
                if make_backup("before_category_deletion") is None:
                    raise RuntimeError("Не удалось создать резервную копию.")
                db.execute("UPDATE work_categories SET is_active=0 WHERE id=? OR parent_id=?", (self.current_id, self.current_id))
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, str(exc))
                return
            self.current_id = None
            self.refresh(); self.sub_table.setRowCount(0); self.update_form_state()

    def add_subcategory(self):
        if not self.current_id:
            self.msg("Сначала выберите или создайте категорию.")
            return
        row = self.sub_table.rowCount(); self.sub_table.insertRow(row)
        section = "" if self.section.currentText() == NOT_SELECTED else self.section.currentText()
        unit = "" if self.unit.currentText() == NOT_SELECTED else self.unit.currentText()
        vals = ["", "Новая подкатегория", section, unit, self.price.text() or "0", ""]
        for c, val in enumerate(vals): self.sub_table.set_value(row, c, val)

    def _save_subcategories(self, conn, section, unit):
        existing_ids: list[int] = []
        new_subrows: list[tuple[int, int]] = []
        for r in range(self.sub_table.rowCount()):
            name = self.sub_table.value(r, 1)
            if not name:
                continue
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
        problem = numeric_error(self.price.text(), "Цена / ставка категории")
        for index in range(self.sub_table.rowCount()):
            problem = problem or numeric_error(
                self.sub_table.value(index, 4), f"Цена подкатегории, строка {index + 1}"
            )
        if problem:
            QMessageBox.warning(self, "Проверьте стоимость", problem)
            return False
        prior = {int(r["id"]) for r in db.rows(
            "SELECT id FROM work_categories WHERE parent_id=? AND is_active=1", (self.current_id,)
        )}
        remaining = {
            int(self.sub_table.value(row, 0))
            for row in range(self.sub_table.rowCount())
            if self.sub_table.value(row, 0) and self.sub_table.value(row, 1)
        }
        if prior - remaining:
            if make_backup("before_subcategory_deletion") is None:
                raise RuntimeError("Не удалось создать резервную копию перед удалением подкатегорий.")
        with connect() as conn:
            conn.execute(
                "UPDATE work_categories SET name=?, section=?, unit=?, default_price=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (self.name.text(), section, unit, to_float(self.price.text()), self.notes.toPlainText(), self.current_id),
            )
            new_subrows = self._save_subcategories(conn, section, unit)
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
        self.table.setRowCount(0)
        for r in db.named_rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, category, name"):
            row = self.table.rowCount(); self.table.insertRow(row)
            vals = [r["id"], r["group_name"], r["subgroup_name"], r["name"], r["category"], r["unit"], r["default_price"], r["notes"]]
            for col, val in enumerate(vals):
                self.table.set_value(row, col, val)
        # Preserve the flexible widths configured at table creation.
        self._initial_work_rows = {
            self.table.value(row, 0): self._row_signature(row)
            for row in range(self.table.rowCount())
        }
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
        for index in range(self.table.rowCount()):
            existing = self.table.value(index, 0)
            if existing and self._initial_work_rows.get(existing) == self._row_signature(index):
                continue  # Preserve unchanged legacy work, including missing section.
            name = self.table.value(index, 3)
            section = self.table.value(index, 4)
            if not name or normalize_section(section, "") not in COMMON_SECTIONS:
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
        active_ids_before = {int(row["id"]) for row in db.rows("SELECT id FROM work_items WHERE is_active=1")}
        if active_ids_before - visible_ids:
            if make_backup("before_work_deletion") is None:
                raise RuntimeError("Не удалось создать резервную копию справочника перед удалением.")
        with connect() as conn:
            self._save_work_rows(conn)
            conn.commit()
        # Reconcile generated IDs and the in-memory snapshot even when the
        # caller requested no refresh. Otherwise newly added rows could be
        # inserted twice on a subsequent edit/save.
        self.refresh()
        self.msg("Справочник сохранен")
        # Preserve the existing implicit None success result: callers and
        # historical UI tests depend on that contract.


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
                make_backup("before_delete_template")
                db.execute("DELETE FROM templates WHERE id=?", (self.current_id,))
            except Exception as exc:
                QMessageBox.warning(self, DELETE_CANCELLED, f"Не удалось сохранить резервную копию или удалить шаблон: {exc}")
                return
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
        except ValueError as exc:
            QMessageBox.warning(self, "Шаблон не сохранён", str(exc))
            return False
        # Reconcile hidden row IDs even when saving before a selection change.
        for index, row_id in inserted:
            self.table.set_value(index, 0, row_id)
        self._remember_form_state()
        self.msg("Шаблон сохранен")
        if refresh_after:
            self.refresh()


class AddFromReferenceDialog(QDialog):
    def __init__(self, category: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(ADD_REFERENCE_TEXT)
        self.selected_id = None
        layout = QVBoxLayout(self)
        self.combo = ClickComboBox()
        target = normalize_section(category, category)
        for r in db.named_rows("SELECT * FROM work_items WHERE is_active=1 ORDER BY group_name, subgroup_name, name"):
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
        self._review_dirty = True
        self._review_snapshot = None
        self._report_tab_index = 0
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_current_report)
        root = QHBoxLayout(self)
        splitter = QSplitter(); self.splitter = splitter; root.addWidget(splitter)
        left = QWidget(); left_l = QVBoxLayout(left)
        self.company_filter = ClickComboBox(); self.company_filter.currentIndexChanged.connect(lambda _i: self.reload_houses_filter())
        self.house_filter = ClickComboBox(); self.house_filter.currentIndexChanged.connect(self.refresh)
        self.year_filter = SafeSpinBox(); self.year_filter.setRange(2000, 2100); self.year_filter.setValue(2026); self.year_filter.valueChanged.connect(self.refresh)
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

    def _invalidate_review(self):
        self._review_dirty = True
        self._review_snapshot = None
        if hasattr(self, "review_status"):
            self.review_status.setText(
                "Отчет изменен. Перед выгрузкой повторите проверку."
            )

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
        count = len({cell.row() for cell in table.selectedIndexes()})
        if count == 0:
            self.msg("Выделите строки, которые требуется удалить.")
            return
        if QMessageBox.question(
            self, DELETE_ROWS_TITLE, f"Удалить из отчета выбранные строки ({count})?"
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
            return result
        except Exception as exc:
            self._invalidate_review()
            self.review_status.setText(f"Проверка не выполнена: {exc}")
            self.msg(f"Проверка не выполнена: {exc}")
            return None

    def reload_filters(self):
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

    def refresh(self):
        # Before rebuilding the report list, persist the report still on screen.
        if self._dirty_report and self.current_id and not self._loading_report:
            self.flush_autosave()
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
            if make_backup("before_delete_report") is None:
                raise RuntimeError("Резервная копия базы не создана.")
            db.execute("DELETE FROM reports WHERE id=?", (int(self.current_id),))
            self.current_id = None
            self.refresh()
            self.msg("Отчет удален")
        except Exception as e:
            QMessageBox.warning(self, "Не удалось удалить отчет", str(e))

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

    def _save_before_report_switch(self, old_id, new_id):
        if old_id and old_id != new_id and not self._loading_report and not self._saving_report:
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

    def _load_current(self):
        item = self.list.currentItem()
        new_id = item.data(Qt.UserRole) if item else None
        old_id = self.current_id
        if not self._save_before_report_switch(old_id, new_id):
            return
        self.current_id = new_id
        if not self.current_id:
            self._invalidate_review()
            self._update_report_actions()
            return
        self._invalidate_review()
        self._update_report_actions()
        self._loading_report = True
        try:
            bundle = db.load_report_bundle(self.current_id)
            self._populate_loaded_report(bundle)
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
        dlg = AddFromReferenceDialog(category, self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_id:
            r = db.row("SELECT * FROM work_items WHERE id=?", (dlg.selected_id,))
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
        if QMessageBox.question(self, APPLY_TEMPLATE_TEXT, f"Добавить строки выбранного шаблона в раздел «{target_section}»?") != QMessageBox.Yes:
            return
        template_section = self._template_section(int(tid), target_section)
        added, skipped = self._apply_template_items(tid, target_section, template_section)
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
        except (ValueError, OSError) as exc:
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
                self._save_report_fields(conn, rid)
                inserted_rows = self._sync_report_sections(conn, rid)
                self._sync_finance_and_claims(conn, rid)
                conn.commit()
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

    def export(self, kind: str):
        if kind not in ("xlsx", "docx", "pdf", "all"):
            return
        rid = self._reviewed_export_id()
        if rid is None:
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
                    paths.append(export_func(rid))
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



class MainWindow(QMainWindow):
    """Navigation shell; pages retain their own data, calculations and exports."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 850)
        self.setMinimumSize(860, 520)
        self.settings = QSettings("OtchetnostUK", "728")
        self._theme = str(self.settings.value("ui/theme", "light"))
        if self._theme not in ("light", "dark"):
            self._theme = "light"
        # Qt handles monitor DPI; no separate application scaling setting.
        apply_theme(QApplication.instance(), self._theme)
        central = QWidget(self)
        central.setObjectName("ContentPane")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)
        self.sidebar = NavigationSidebar(self._theme, self)
        root.addWidget(self.sidebar)

        workspace = QWidget()
        workspace.setObjectName("PageSurface")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 9, 10, 8)
        workspace_layout.setSpacing(8)
        self.page_header = PageChrome()
        workspace_layout.addWidget(self.page_header)
        self.tabs = QStackedWidget()
        self.tabs.setMinimumSize(0, 0)
        workspace_layout.addWidget(self.tabs, 1)
        self.workspace_scroll = QScrollArea(self)
        self.workspace_scroll.setObjectName("WorkspaceScroll")
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace_scroll.setMinimumSize(0, 0)
        self.workspace_scroll.setWidget(workspace)
        root.addWidget(self.workspace_scroll, 1)

        # Preserve the existing screens, data bindings and per-section logic.
        self.company_tab = CompaniesTab()
        self.houses_tab = HousesTab()
        self.categories_tab = CategoriesTab()
        self.works_tab = WorksTab()
        self.templates_tab = TemplatesTab()
        self.reports_tab = ReportsTab()
        self.mass_tab = MassApplyTab()
        self.backups_tab = BackupsTab(main_window_type=MainWindow)
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
        self.sidebar.select(0)
        self.page_header.show_page(0)
        self._decorate_buttons()
        self._active_company_id = self.company_tab.current_id
        self._next_page = None
        self.page_header.next_button.clicked.connect(self._follow_next_action)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.auto_backup)
        self.timer.start(5 * 60 * 1000)
        self.context_timer = QTimer(self)
        self.context_timer.timeout.connect(self.refresh_context)
        self.context_timer.start(1500)
        self.refresh_context()
        self._layout_compact = None
        QTimer.singleShot(0, self._update_responsive_layout)
        self.statusBar().showMessage("Готово")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "mass_tab") and hasattr(self, "workspace_scroll"):
            self._update_responsive_layout()

    def _update_responsive_layout(self):
        """Reflow two-column editors without discarding selected records."""
        if not hasattr(self, "mass_tab"):
            return
        available = max(0, self.width() - self.sidebar.width() - 24)
        compact = available < 880
        if compact == getattr(self, "_layout_compact", None):
            return
        self._layout_compact = compact
        for tab in (
            self.company_tab, self.houses_tab, self.categories_tab,
            self.templates_tab, self.reports_tab,
        ):
            splitter = tab.splitter
            target = Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal
            if splitter.orientation() != target:
                splitter.setOrientation(target)
                splitter.setSizes([230, 720] if compact else [325, 1050])
        self.mass_tab.set_compact_layout(compact)
        self.page_header.set_compact_layout(compact)
        self.tabs.updateGeometry()

    def _decorate_buttons(self):
        """Do not override roles assigned by the actual action groups."""
        for button in self.findChildren(QPushButton):
            if button.objectName() in ("PrimaryAction", "DangerAction", "QuietAction"):
                continue
            label = button.text().strip().lower()
            if label.startswith(("удалить", "очистить", "аннулировать")):
                button.setObjectName("DangerAction")

    def _can_autosave_tab(self, tab) -> bool:
        # Only forms with a genuine save implementation participate here.
        # Reports use flush_autosave(); mass fill and backups have explicit actions.
        if isinstance(tab, WorksTab):
            return True
        if isinstance(tab, (CompaniesTab, HousesTab, CategoriesTab, TemplatesTab)):
            return tab.current_id is not None
        return False

    def set_save_status(self, state: str, message: str | None = None):
        self.page_header.set_save_status(state, message)

    def notification_received(self, text: str):
        low = text.lower()
        if "не удалось" in low and "сохран" in low:
            self.set_save_status("error")
        elif "сохран" in low and any(word in low for word in ("выполнено", "сохранен", "сохранён", "сохранена")):
            self.set_save_status("saved")

    def _remember_company_context(self, page):
        company = None
        if isinstance(page, CompaniesTab):
            company = page.current_id
        elif isinstance(page, HousesTab):
            company = page.company_filter.currentData()
        elif isinstance(page, ReportsTab):
            company = page.company_filter.currentData()
        elif isinstance(page, MassApplyTab):
            company = page.company.currentData()
        if company is not None or isinstance(page, CompaniesTab):
            self._active_company_id = company

    def _select_company_row(self, page, cid):
        for row in range(page.list.count()):
            if page.list.item(row).data(Qt.UserRole) == cid:
                if page.list.currentRow() != row:
                    page.list.setCurrentRow(row)
                break

    def _company_context_combo(self, page):
        if isinstance(page, (HousesTab, ReportsTab)):
            return page.company_filter
        if isinstance(page, MassApplyTab):
            return page.company
        return None

    def _apply_company_context(self, page):
        cid = self._active_company_id
        if cid is None:
            return
        if isinstance(page, CompaniesTab):
            self._select_company_row(page, cid)
        else:
            combo = self._company_context_combo(page)
            if combo is not None:
                index = combo.findData(cid)
                if index >= 0 and combo.currentIndex() != index:
                    combo.setCurrentIndex(index)

    def _follow_next_action(self):
        if self._next_page is not None:
            self.navigate(self._next_page)

    def _update_next_action(self, page):
        action = None
        if isinstance(page, CompaniesTab) and page.current_id:
            action = ("Далее: дома →", 1)
        elif isinstance(page, HousesTab) and page.current_id:
            # Categories and templates are helpful but optional once the
            # directory is populated. Do not force users to visit them.
            has_works = db.row("SELECT id FROM work_items WHERE is_active=1 LIMIT 1")
            action = (NEXT_REPORTS_TEXT, 6) if has_works else ("Далее: справочник работ →", 3)
        elif isinstance(page, CategoriesTab) and page.current_id:
            action = ("Далее: справочник работ →", 3)
        elif isinstance(page, WorksTab) and page.table.rowCount():
            action = (NEXT_REPORTS_TEXT, 6)
        elif isinstance(page, TemplatesTab) and page.current_id:
            action = (NEXT_REPORTS_TEXT, 6)
        self._next_page = action[1] if action else None
        self.page_header.next_button.setVisible(action is not None)
        if action:
            self.page_header.next_button.setText(action[0])

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
            elif self._can_autosave_tab(previous) and previous.save() is False:
                raise ValueError("Исправьте ошибки заполнения перед переходом.")
        except Exception as exc:
            self.set_save_status("error")
            self.statusBar().showMessage(f"Не удалось сохранить изменения: {exc}", 12000)
            self.sidebar.select(self._last_tab)
            return
        self._remember_company_context(previous)
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
        self._apply_company_context(current)
        self.refresh_context()
        if isinstance(current, ReportsTab) and current.current_id and not current._dirty_report:
            self.set_save_status("saved")

    def _refresh_sidebar_company(self, current):
        display_cid = current.current_id if isinstance(current, CompaniesTab) else self._active_company_id
        if isinstance(current, HousesTab):
            display_cid = current.company_filter.currentData()
        elif isinstance(current, ReportsTab):
            display_cid = current.company_filter.currentData()
        elif isinstance(current, MassApplyTab):
            display_cid = current.company.currentData()
        company = db.row("SELECT name FROM companies WHERE id=?", (display_cid,)) if display_cid else None
        self.sidebar.set_company_name(str(company["name"]) if company else "")

    def _refresh_editor_save_status(self, current):
        if isinstance(current, BaseTab) and not isinstance(current, ReportsTab):
            if current.has_unsaved_changes():
                if self.page_header.save_status.property("state") not in ("pending", "error"):
                    self.set_save_status("pending")
            elif (self._can_autosave_tab(current)
                  and self.page_header.save_status.property("state") == "pending"):
                self.set_save_status("saved")

    def refresh_context(self):
        """Show only the active page's selection and its meaningful next step."""
        try:
            current = self.tabs.currentWidget()
            context, guidance = describe_context(current)
            self.page_header.set_context(context)
            self.page_header.guide.setText(guidance)
            self._refresh_sidebar_company(current)
            self._update_next_action(current)
            self._refresh_editor_save_status(current)
        except (AttributeError, RuntimeError):
            self.page_header.set_context("Рабочий контекст")

    def switch_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.settings.setValue("ui/theme", self._theme)
        apply_theme(QApplication.instance(), self._theme)
        self.sidebar.refresh_theme(self._theme)

    def save_before_assistant_sync(self) -> bool:
        """Do not replace form values while there are unsaved local edits."""
        active = self.tabs.currentWidget()
        try:
            if isinstance(active, ReportsTab):
                active.flush_autosave()
            elif isinstance(active, (CompaniesTab, HousesTab, CategoriesTab,
                                      WorksTab, TemplatesTab)):
                if active.save(refresh_after=False) is False:
                    raise ValueError("Сначала исправьте ошибки в текущей форме.")
        except Exception as exc:
            QMessageBox.warning(
                self, "Обновление данных отменено",
                f"Не удалось сохранить текущую форму: {exc}"
            )
            return False
        return True

    def reload_after_assistant_sync(self):
        """Update all selectors after committing only local mirrored records."""
        self.company_tab.refresh()
        if self.company_tab.current_id:
            self.company_tab._load_current()
        self.houses_tab.reload_companies()
        self.houses_tab.refresh()
        if self.houses_tab.current_id:
            self.houses_tab._load_current()
        self.reports_tab.reload_filters()
        self.mass_tab.reload()
        self._apply_company_context(self.tabs.currentWidget())
        self.refresh_context()

    def auto_backup(self):
        try:
            tab = self.tabs.currentWidget()
            if isinstance(tab, ReportsTab):
                tab.flush_autosave()
            elif self._can_autosave_tab(tab) and tab.save() is False:
                raise ValueError("Не все обязательные поля заполнены.")
            path = make_backup("auto")
            if path is not None:
                self.statusBar().showMessage(f"Резервная копия создана: {path.name}", 5000)
            else:
                self.statusBar().showMessage("Данные не изменились — новая копия не требуется", 5000)
        except Exception as exc:
            self.set_save_status("error")
            self.statusBar().showMessage(f"Не удалось создать резервную копию: {exc}", 8000)

    def closeEvent(self, event):
        # A failed write must never silently discard edited form fields.
        try:
            tab = self.tabs.currentWidget()
            if isinstance(tab, ReportsTab):
                tab.flush_autosave()
            elif self._can_autosave_tab(tab) and tab.save() is False:
                raise ValueError("Не все обязательные поля заполнены.")
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
    app = QApplication(sys.argv)
    install_exception_hook()
    try:
        # A restore is only safe before opening SQLite through any app page.
        protected = apply_pending_restore()
        db.init_db()
    except Exception as exc:
        QMessageBox.critical(
            None, "Не удалось открыть базу",
            f"Восстановление или открытие базы не завершено:\n{exc}\n\n"
            "Программа остановлена, чтобы не затереть пользовательские данные. "
            "Проверьте подготовленную копию и файлы в папке data/backups."
        )
        return 1
    # Auto-refresh shared source links on startup. An unavailable or damaged
    # Assistant DB never blocks independent reporting work and is NEVER
    # recreated by the Reporter.
    assistant_warning = None
    try:
        source = assistant_source.configured_source_path()
        if source.is_file():
            assistant_source.synchronize()
        else:
            assistant_warning = f"База Ассистента не найдена: {source}"
    except Exception as exc:
        assistant_warning = f"Не удалось обновить сведения Ассистента: {exc}"
    window = MainWindow()
    window.show()
    if assistant_warning:
        window.backups_tab.update_assistant_status(assistant_warning)
        window.statusBar().showMessage(assistant_warning, 15000)
    if protected is not None:
        QTimer.singleShot(
            0, lambda: QMessageBox.information(
                window, "База восстановлена",
                "Восстановление выполнено. Предыдущая база сохранена как:\n"
                + str(protected)
            )
        )
    sys.exit(app.exec())
