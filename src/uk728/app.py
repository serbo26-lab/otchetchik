from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QStackedWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import db, assistant_source
from .property_ui import CompaniesTab, HousesTab
from .db import connect
from .reports_ui import AddFromReferenceDialog, CreateReportDialog, ReportsTab
from .paths import make_backup
from .backup_restore import apply_pending_restore
from .backups_ui import BackupsTab
from .ui_style import apply_theme
from .ui_shell import NavigationSidebar, PageChrome, PAGES
from .templates_ui import TemplatesTab
from .mass_apply_ui import MassApplyTab, MassPreviewTable
from .directory_ui import CategoriesTab, WorksTab
from .ui_labels import (
    COST_LABEL, QUANTITY_LABEL, REPORT_YEAR_LABEL, CREATE_TITLE, WORK_ID_LABEL,
    NOTES_LABEL, UNNAMED_LABEL, DELETE_QUESTION, DELETE_CANCELLED,
    NOT_SELECTED, SECTION_LABEL, UNIT_LABEL, CATEGORY_BY_ID_SQL,
    SELECT_COMPANY_MESSAGE, HOUSE_COMPANY_SQL, COMPANY_LIST_SQL,
    ADD_REFERENCE_TEXT, DATE_FORMAT_HINT, TEMPLATE_LABEL, APPLY_TEMPLATE_TEXT,
    SECTION_CONTENT_LABEL, MANUAL_SOURCE, TEMPLATE_SOURCE, MASS_SOURCE, REFERENCE_SOURCE,
)
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

NEXT_REPORTS_TEXT = "Далее: отчеты →"


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
        if "сохран" not in low:
            return
        if any(word in low for word in ("не удалось", "не сохран", "не выполн", "отменен", "отменён")):
            self.set_save_status("error")
        elif any(word in low for word in ("выполнено", "сохранен", "сохранён", "сохранена")):
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
