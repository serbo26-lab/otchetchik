"""Shared desktop navigation, responsive action rows and page framing."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QListWidget,
    QListWidgetItem, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .ui_style import svg_icon


PAGES = [
    ("Управляющие организации", "organization", "Управляющие организации и их реквизиты", "Добавьте УО и заполните основные сведения."),
    ("Дома", "house", "Адреса и площади многоквартирных домов", "Выберите УО, добавьте дом, затем заполните его данные."),
    ("Категории работ", "categories", "Группы и подгруппы работ", "Создайте категорию и укажите раздел отчетной формы."),
    ("Справочник работ", "works", "Единый перечень работ и услуг", "Добавьте работы и услуги, затем назначьте единицы и разделы."),
    ("Шаблоны", "templates", "Типовые перечни для заполнения отчетов", "Выберите раздел, создайте шаблон и наполните его работами."),
    ("Массовое заполнение", "mass", "Добавление работ сразу в несколько отчетов", "Выберите способ расчета, проверьте предпросмотр и примените изменения."),
    ("Отчеты", "reports", "Отчет за выбранный год и дом", "Выберите УО и дом, создайте отчет и заполните необходимые разделы."),
    ("Данные и резервные копии", "backup", "Хранение и восстановление данных", "Выберите копию, проверьте её и подготовьте восстановление. Замена базы выполняется после перезапуска."),
]


class ActionFlowLayout(QLayout):
    """Flow layout that never hides actions off the right edge of a narrow window."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        widget = item.widget()
        if widget:
            widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._items.append(item)

    def addStretch(self, _stretch=0):
        # Command groups stay together; empty stretches must not split them.
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def minimumSize(self):
        result = QSize(0, 0)
        for item in self._items:
            result = result.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return result + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def _arrange(self, rect, measure):
        margins = self.contentsMargins()
        space = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = space.x(), space.y()
        row_height = 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            gap = max(4, self.spacing())
            width = min(max(1, hint.width()), max(1, space.width()))
            if x > space.x() and x + width > space.right() + 1:
                x, y = space.x(), y + row_height + gap
                row_height = 0
            height = hint.height()
            if not measure:
                item.setGeometry(QRect(x, y, width, height))
            x += width + gap
            row_height = max(row_height, height)
        return y + row_height - rect.y() + margins.bottom()


class NavigationSidebar(QFrame):
    navigateRequested = Signal(int)
    themeRequested = Signal()

    def __init__(self, theme: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        # The previous 245px width truncated "Управляющие организации"
        # and "Данные и резервные копии" even at normal 100% UI scale.
        self.setFixedWidth(278)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 16, 10, 12)
        layout.setSpacing(9)

        brand = QLabel("ОТЧЕТНОСТЬ УК")
        brand.setObjectName("AppBrand")
        layout.addWidget(brand)
        self.organization_caption = QLabel("УО не выбрана")
        self.organization_caption.setObjectName("ContextCaption")
        self.organization_caption.setWordWrap(True)
        self.organization_caption.setToolTip("Выберите организацию в разделе «Управляющие организации».")
        layout.addWidget(self.organization_caption)
        tagline = QLabel("Приказ Минстроя № 728/пр")
        tagline.setObjectName("PageSubtitle")
        layout.addWidget(tagline)

        self.navigation = QListWidget()
        self.navigation.setObjectName("SidebarNavigation")
        self.navigation.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setIconSize(QSize(20, 20))
        self.navigation.setSpacing(3)
        for title, _glyph, subtitle, _help in PAGES:
            item = QListWidgetItem(title)
            item.setToolTip(title + "\n" + subtitle)
            item.setSizeHint(QSize(0, 44))
            self.navigation.addItem(item)
        self.navigation.currentRowChanged.connect(self.navigateRequested.emit)
        layout.addWidget(self.navigation, 1)

        self.theme_button = QPushButton()
        self.theme_button.setObjectName("SideUtility")
        self.theme_button.clicked.connect(self.themeRequested.emit)
        layout.addWidget(self.theme_button)

        self.refresh_theme(theme)

    def refresh_theme(self, theme: str):
        self.theme_button.setText("☀ Светлая тема" if theme == "dark" else "☾ Темная тема")
        for index, (_title, glyph, _subtitle, _help) in enumerate(PAGES):
            self.navigation.item(index).setIcon(svg_icon(glyph, theme))

    def set_company_name(self, name: str):
        self.organization_caption.setText(name or "УО не выбрана")
        self.organization_caption.setToolTip(
            (name + "\n" if name else "")
            + "Организация выбирается в разделе «Управляющие организации»."
        )

    def select(self, index: int):
        if not 0 <= index < self.navigation.count():
            return
        blocked = self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(index)
        self.navigation.blockSignals(blocked)


class PageChrome(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageHeader")
        container = QVBoxLayout(self)
        container.setContentsMargins(12, 8, 12, 8)
        container.setSpacing(7)

        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("PageTitle")
        header.addWidget(self.title, 1)

        self.save_status = QLabel("Сохранение при переходе")
        self.save_status.setObjectName("SaveStatus")
        self.save_status.setProperty("state", "idle")
        header.addWidget(self.save_status)
        container.addLayout(header)

        context_row = QGridLayout()
        context_row.setContentsMargins(0, 0, 0, 0)
        context_row.setHorizontalSpacing(8)
        context_row.setVerticalSpacing(6)
        self._context_grid = context_row
        self.context = QLabel()
        self.context.setObjectName("ContextCaption")
        self.context.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context.setWordWrap(True)
        self.context.setMinimumWidth(0)
        context_row.setColumnStretch(0, 1)
        context_row.addWidget(self.context, 0, 0)
        self.guide_button = QPushButton("Как продолжить")
        self.guide_button.setCheckable(True)
        self.guide_button.toggled.connect(self._toggle_guide)
        self.next_button = QPushButton()
        self.next_button.setObjectName("PrimaryAction")
        self.next_button.hide()
        context_row.addWidget(self.next_button, 0, 1)
        context_row.addWidget(self.guide_button, 0, 2)
        container.addLayout(context_row)

        self.recommendation = QLabel()
        self.recommendation.setObjectName("ContextCaption")
        self.recommendation.setWordWrap(True)
        self.recommendation.setMinimumWidth(0)
        container.addWidget(self.recommendation)

        self.guide = QLabel()
        self.guide.setObjectName("Guide")
        self.guide.setWordWrap(True)
        self.guide.hide()
        container.addWidget(self.guide)

    def set_compact_layout(self, compact: bool):
        """Put context above actions instead of squeezing it between buttons."""
        grid = self._context_grid
        for widget in (self.context, self.next_button, self.guide_button):
            grid.removeWidget(widget)
        if compact:
            grid.addWidget(self.context, 0, 0, 1, 3)
            grid.addWidget(self.next_button, 1, 0)
            grid.addWidget(self.guide_button, 1, 1)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 0)
            grid.setColumnStretch(2, 1)
        else:
            grid.addWidget(self.context, 0, 0)
            grid.addWidget(self.next_button, 0, 1)
            grid.addWidget(self.guide_button, 0, 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0)
            grid.setColumnStretch(2, 0)
        self.updateGeometry()

    def _toggle_guide(self, checked):
        self.guide.setVisible(checked)

    def show_page(self, index: int):
        title, _glyph, subtitle, guidance = PAGES[index]
        self.title.setText(title)
        self.title.setToolTip(subtitle)
        self.guide.setText(guidance)

    def set_context(self, text: str):
        self.context.setText(text)

    def set_save_status(self, state: str, message: str | None = None):
        labels = {"saved": "Локально: сохранено", "pending": "Локально: есть изменения", "saving": "Локально: сохранение…", "idle": "Сохранение при переходе", "error": "Локально: ошибка сохранения"}
        self.save_status.setText(message or labels.get(state, labels["saved"]))
        self.save_status.setProperty("state", state)
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)
