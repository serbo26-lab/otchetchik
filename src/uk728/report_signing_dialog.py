"""Choose optional report signatories without asserting a legal signature."""
from contextlib import closing

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from . import db, report_signing
from .utils import format_date


class ReportSigningDialog(QDialog):
    def __init__(self, report_id, parent=None):
        super().__init__(parent)
        self.report_id = report_id
        bundle = db.load_report_bundle(report_id)
        self.report = bundle["report"]
        self.context = report_signing.report_context(self.report)
        self.previous = report_signing.read_snapshot(report_id)
        self.setWindowTitle("Подписанты отчета")
        self.resize(690, 520)
        root = QVBoxLayout(self)
        hint = QLabel("Дополнительный блок реквизитов. Выберите дату и одного или нескольких подписантов. "
                      "Сохранение реквизитов не означает юридического подписания отчета.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addWidget(QLabel("Дата подписания (ДД.ММ.ГГГГ)"))
        self.date = QLineEdit(format_date(self.previous.get("date") or self.report["publish_date"]
                                          or self.report["period_end"]))
        root.addWidget(self.date)
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("Сохранить реквизиты")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        remove = QPushButton("Убрать блок подписантов из отчета")
        remove.setEnabled(bool(self.previous))
        remove.clicked.connect(self._remove)
        root.addWidget(remove)
        root.addWidget(buttons)
        self.date.editingFinished.connect(self._refresh)
        self.list.itemChanged.connect(self._update_save)
        self._refresh({p["id"] for p in self.previous.get("people", [])})

    def _selected(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _refresh(self, selected=None):
        selected = set(self._selected()) if selected is None else selected
        self.list.clear()
        try:
            with closing(db.connect()) as conn:
                people = report_signing.available_people(conn, self.report, self.date.text())
            for person in people:
                side = "УО" if person["side"] == "company" else "МКД"
                item = QListWidgetItem(f"{side} · {person['name']} — {person['position'] or person['role']}")
                item.setData(Qt.UserRole, person["id"])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if person["id"] in selected else Qt.Unchecked)
                self.list.addItem(item)
            self.status.setText("Доступные лица на указанную дату: " + str(len(people)) +
                                ". Сохранение заменяет прежний снимок, отмена сохраняет его.")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
        self._update_save()

    def _update_save(self, *_args):
        self.save_button.setEnabled(bool(self._selected()))

    def _save(self):
        try:
            report_signing.save_selection(self.report_id, self.date.text(), self._selected(),
                                          expected_context=self.context)
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить реквизиты", str(exc))
            return
        self.accept()

    def _remove(self):
        if QMessageBox.question(self, "Удаление реквизитов", "Убрать дополнительный блок из отчета?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            try:
                report_signing.clear_selection(self.report_id)
            except Exception as exc:
                QMessageBox.warning(self, "Не удалось удалить реквизиты", str(exc))
                return
            self.accept()
