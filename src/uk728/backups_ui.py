"""Backup and restore page; Assistant synchronization is read-only at source."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout,
)

from . import assistant_source, db
from .backup_restore import verify_backup, pending_restore, stage_restore, cancel_restore
from .paths import data_dir, make_backup
from .ui_components import ContentCard, action_panel
from .ui_editors import BaseTab, selectable_label


class BackupsTab(BaseTab):
    """Browse, verify and stage a restore; never touch a live SQLite database."""
    def __init__(self, *, main_window_type: type | None = None):
        super().__init__()
        self._main_window_type = main_window_type
        layout = QVBoxLayout(self)
        assistant_card = ContentCard(
            "Общая база ГИС ЖКХ Ассистента",
            "Только чтение: УО и дома поступают из базы Ассистента. "
            "Отчёты, контакты для разъяснений и другие локальные поля "
            "сохраняются отдельно, только в базе Отчётчика."
        )
        self.assistant_path_label = selectable_label("")
        self.assistant_path_label.setObjectName("CardSubtitle")
        assistant_card.add_widget(self.assistant_path_label)
        self.assistant_summary = selectable_label("")
        assistant_card.add_widget(self.assistant_summary)
        self.assistant_choose_button = QPushButton("Выбрать базу Ассистента…")
        self.assistant_choose_button.clicked.connect(self.choose_assistant_source)
        self.assistant_sync_button = QPushButton("Обновить сведения из Ассистента")
        self.assistant_sync_button.clicked.connect(self.refresh_assistant_source)
        assistant_card.add_widget(action_panel(
            "Подключение только для чтения",
            self.assistant_choose_button, self.assistant_sync_button,
            primary=self.assistant_sync_button,
        ))
        layout.addWidget(assistant_card)
        overview = ContentCard(
            "Резервные копии базы",
            "Каждые 5 минут проверяются изменения; копия создаётся только при их наличии. "
            "Ручные копии и "
            "защитные снимки перед изменениями не удаляются автоматически."
        )
        self.archive_list = QListWidget()
        self.archive_list.setMinimumHeight(165)
        self.archive_list.setMaximumHeight(320)
        self.archive_list.currentItemChanged.connect(self.update_controls)
        overview.add_widget(self.archive_list)
        self.empty_archive_hint = selectable_label(
            "Резервных копий пока нет. Нажмите «Создать копию», "
            "чтобы сохранить текущую базу перед проверкой или изменениями."
        )
        self.empty_archive_hint.setObjectName("CardSubtitle")
        overview.add_widget(self.empty_archive_hint)
        layout.addWidget(overview)

        make = QPushButton("Создать копию")
        make.clicked.connect(self.backup_now)
        check = QPushButton("Проверить выбранную")
        check.clicked.connect(self.check_backup)
        self.check_button = check
        restore = QPushButton("Восстановить выбранную")
        restore.clicked.connect(self.restore_selected)
        self.restore_button = restore
        external = QPushButton("Выбрать файл .db…")
        external.clicked.connect(self.choose_external)
        self.external_button = external
        refresh = QPushButton("Обновить список")
        refresh.clicked.connect(self.refresh)
        open_data = QPushButton("Открыть папку data")
        open_data.clicked.connect(self.show_data_path)
        layout.addWidget(action_panel(
            "Работа с копиями", make, check, restore, external, refresh, open_data,
            primary=make
        ))
        self.pending_message = selectable_label("")
        self.pending_message.setObjectName("CardSubtitle")
        layout.addWidget(self.pending_message)
        cancel = QPushButton("Отменить подготовленное восстановление")
        cancel.clicked.connect(self.cancel_pending)
        self.cancel_button = cancel
        layout.addWidget(action_panel("Отмена восстановления", cancel))
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(125)
        layout.addWidget(self.info)
        layout.addStretch(1)
        self.refresh()

    def update_assistant_status(self, message: str = ""):
        path = assistant_source.configured_source_path()
        ready = path.is_file()
        self.assistant_path_label.setText(
            f"Источник: {path}\n"
            + ("Файл доступен. Режим подключения — только чтение." if ready
               else "Файл не найден. Укажите путь к data.db Ассистента.")
        )
        linked = db.row(
            "SELECT (SELECT COUNT(*) FROM companies WHERE assistant_source_id IS NOT NULL) AS orgs, "
            "(SELECT COUNT(*) FROM houses WHERE assistant_source_id IS NOT NULL) AS homes"
        )
        summary = (f"Связанных локальных записей: УО — {linked['orgs']}, "
                   f"домов — {linked['homes']}.")
        if message:
            summary += "\n" + message
        self.assistant_summary.setText(summary)
        self.assistant_sync_button.setEnabled(ready)

    def _apply_assistant_sync(self, path: Path | None = None, *, remember: bool = False):
        win = self.window()
        is_main_window = self._main_window_type is not None and isinstance(win, self._main_window_type)
        if is_main_window and not win.save_before_assistant_sync():
            return
        try:
            # synchronize() compares source data and makes a safety copy
            # only when an actual import is necessary.
            result = assistant_source.synchronize(path, save_path=remember)
        except Exception as exc:
            QMessageBox.warning(
                self, "Сведения Ассистента не обновлены",
                f"{exc}\n\nБаза Ассистента не изменялась."
            )
            self.update_assistant_status(f"Последняя попытка: ошибка чтения ({exc}).")
            return
        if is_main_window:
            win.reload_after_assistant_sync()
        message = (
            f"Загружено: УО — {result.organizations}, домов — {result.houses}, "
            f"подписантов — {result.signatories}. "
            f"Новых: УО — {result.new_organizations}, домов — {result.new_houses}."
        )
        if result.unchanged:
            message += " Изменений в исходных сведениях нет."
        if result.missing_area:
            message += (
                f" У {result.missing_area} домов отсутствует общая площадь "
                "помещений: нет площади к начислению и недостаточно "
                "составляющих для расчёта. Её можно указать локально."
            )
        if result.missing_management_start:
            message += (
                f" У {result.missing_management_start} домов в источнике "
                "не указана дата начала управления."
            )
        self.update_assistant_status(message)
        self.msg("Данные Ассистента прочитаны. Изменена только локальная база Отчётчика.")

    def choose_assistant_source(self):
        default = assistant_source.configured_source_path()
        filename, _filter = QFileDialog.getOpenFileName(
            self, "Выберите data.db программы «ГИС ЖКХ Ассистент»",
            str(default.parent if default.parent.is_dir() else data_dir()),
            "SQLite (*.db);;Все файлы (*)"
        )
        if filename:
            self._apply_assistant_sync(Path(filename), remember=True)

    def refresh_assistant_source(self):
        self._apply_assistant_sync()

    def _selected_path(self):
        item = self.archive_list.currentItem()
        return Path(item.data(Qt.UserRole)) if item else None

    def update_controls(self, *_args):
        try:
            request = pending_restore()
        except ValueError as exc:
            request = {"selected_name": f"Ошибка заявки: {exc}"}
        selected = self._selected_path() is not None
        self.check_button.setEnabled(selected)
        self.restore_button.setEnabled(selected and request is None)
        self.external_button.setEnabled(request is None)
        self.cancel_button.setEnabled(request is not None)
        if request:
            self.pending_message.setText(
                "Восстановление подготовлено: "
                f"{request.get('selected_name', 'неизвестная копия')}. "
                "Закройте программу и откройте её снова. Текущая база будет "
                "сохранена отдельно перед восстановлением."
            )
        else:
            self.pending_message.setText(
                "Для восстановления выберите копию. После подтверждения "
                "перезапустите программу: открытая база не заменяется."
            )

    def refresh(self):
        self.update_assistant_status()
        selected = self._selected_path()
        folder = data_dir() / "backups"
        backups = sorted(
            folder.glob("reporting_728_*.db"),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        self.archive_list.blockSignals(True)
        self.archive_list.clear()
        for source in backups:
            stamp = datetime.fromtimestamp(source.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            size = source.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(f"{stamp} · {size:.2f} МБ · {source.name}")
            item.setToolTip(str(source))
            item.setData(Qt.UserRole, str(source))
            self.archive_list.addItem(item)
            if selected == source:
                self.archive_list.setCurrentItem(item)
        self.archive_list.blockSignals(False)
        self.archive_list.setVisible(bool(backups))
        self.empty_archive_hint.setVisible(not backups)
        self.update_controls()
        self.info.setPlainText(
            f"Папка данных: {data_dir()}\n"
            f"Резервных копий в архиве: {len(backups)}\n"
            "Перед восстановлением проверяется целостность и структура базы. "
            "Последняя текущая база также будет сохранена защитной копией."
        )

    def backup_now(self):
        try:
            path = make_backup("manual")
            if path is None:
                raise RuntimeError("Рабочая база данных ещё не создана.")
        except Exception as exc:
            QMessageBox.warning(self, "Резервная копия не создана", str(exc))
            return
        self.refresh()
        self.msg(f"Резервная копия создана: {path.name}")

    def check_backup(self):
        source = self._selected_path()
        if source is None:
            return
        try:
            result = verify_backup(source)
        except Exception as exc:
            QMessageBox.warning(self, "Копия не прошла проверку", str(exc))
            return
        self.info.setPlainText(
            f"Копия проверена: {source.name}\n"
            f"УО: {result['companies']} · домов: {result['houses']} · "
            f"отчётов: {result['reports']} · "
            f"структура: {result['schema_version'] or 'предыдущая версия'}"
        )
        self.msg("Целостность резервной копии проверена.")

    def _confirm_restore(self, source: Path):
        try:
            result = verify_backup(source, include_digest=True)
        except Exception as exc:
            QMessageBox.warning(self, "Копия не прошла проверку", str(exc))
            return
        answer = QMessageBox.question(
            self, "Подготовить восстановление?",
            f"Файл: {source.name}\n"
            f"УО: {result['companies']}, домов: {result['houses']}, "
            f"отчётов: {result['reports']}.\n\n"
            "После следующего запуска текущая база будет заменена "
            "данными выбранной копии. Сначала программа создаст "
            "отдельную защитную копию текущей базы.\n\n"
            "Подготовить восстановление?"
        )
        if answer != QMessageBox.Yes:
            return
        try:
            stage_restore(source, expected_digest=result["logical_digest"])
        except Exception as exc:
            QMessageBox.warning(self, "Восстановление не подготовлено", str(exc))
            return
        self.refresh()
        QMessageBox.information(
            self, "Восстановление подготовлено",
            "Закройте «Отчётность УК» и запустите программу заново. "
            "Восстановление произойдёт до открытия базы. До перезапуска "
            "его можно отменить в этом разделе."
        )

    def restore_selected(self):
        path = self._selected_path()
        if path:
            self._confirm_restore(path)

    def choose_external(self):
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self, "Выберите резервную копию Отчётности УК",
            str(data_dir() / "backups"), "База SQLite (*.db);;Все файлы (*)"
        )
        if filename:
            self._confirm_restore(Path(filename))

    def cancel_pending(self):
        if QMessageBox.question(
            self, "Отменить восстановление?",
            "Отменить подготовленное восстановление? "
            "Текущая база останется без изменений."
        ) != QMessageBox.Yes:
            return
        try:
            cancel_restore()
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось отменить", str(exc))
            return
        self.refresh()
        self.msg("Восстановление отменено.")

    def show_data_path(self):
        folder = data_dir()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.msg(f"Папка data: {folder}")
