"""A shared visual language for the reporting application's light and dark UI."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

PALETTES = {
    "light": {
        "background": "#f3f5f8", "sidebar": "#eaf0f6", "surface": "#ffffff",
        "raised": "#f5f7fa", "border": "#c9d3df", "text": "#18283b",
        "muted": "#52647a", "accent": "#155aa8", "accent_hover": "#104c90",
        "selected": "#e5effb", "on_accent": "#ffffff", "danger": "#b32c36",
        "success": "#167249", "readonly": "#edf1f6",
    },
    "dark": {
        "background": "#111923", "sidebar": "#15202d", "surface": "#1b2735",
        "raised": "#223143", "border": "#41536a", "text": "#ecf2fa",
        "muted": "#b4c2d5", "accent": "#9bc7ff", "accent_hover": "#b2d4ff",
        "selected": "#263f60", "on_accent": "#10243b", "danger": "#ff9099",
        "success": "#6ed5a4", "readonly": "#263246",
    },
}

# Small SVG outlines, made into Qt icons at runtime so the one-file EXE requires no icon folder.
ICON_PATHS = {
    "organization": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h2m4 0h2M8 11h2m4 0h2M10 21v-5h4v5"/>',
    "house": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M9 21v-9h6v9"/>',
    "categories": '<path d="M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z"/>',
    "works": '<path d="M5 5h15M5 12h15M5 19h15"/><path d="m2 5 1 1 1-2m-2 8 1 1 1-2m-2 8 1 1 1-2"/>',
    "templates": '<path d="M7 3h9l4 4v14H7z"/><path d="M16 3v5h4M10 12h7M10 16h7M10 20h4"/>',
    "reports": '<path d="M6 2h9l5 5v15H6z"/><path d="M15 2v6h5M9 12h8M9 16h8M9 20h5"/>',
    "mass": '<rect x="3" y="4" width="7" height="7" rx="1"/><rect x="14" y="4" width="7" height="7" rx="1"/><path d="M6 16h12m-4-3 4 3-4 3"/>',
    "backup": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v13c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M4.9 4.9l2.2 2.2m9.8 9.8 2.2 2.2m0-14.2-2.2 2.2M7.1 16.9l-2.2 2.2"/>',
    "moon": '<path d="M20 15.7A9 9 0 0 1 8.3 4 9 9 0 1 0 20 15.7Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M19 5l-1.5 1.5m-11 11L5 19"/>',
}

def svg_icon(name: str, theme: str, size: int = 20) -> QIcon:
    color = PALETTES.get(theme, PALETTES["light"])["accent"]
    body = ICON_PATHS.get(name, ICON_PATHS["reports"])
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="' + color +
        '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        body + '</svg>'
    ).encode("utf-8")
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(source)).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)

QSS = """
QWidget { font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 10pt; color: @text; }
QMainWindow, QDialog, QWidget#ContentPane, QWidget#PageSurface { background: @background; }
QFrame#Sidebar { background: @sidebar; border-right: 1px solid @border; }
QFrame#ContentCard { background: @surface; border: 1px solid @border; border-radius: 7px; }
QFrame#ActionPanel { background: @surface; border: 1px solid @border; border-radius: 6px; }
QFrame#ActionPanel[purpose="danger"] { border-left: 2px solid @danger; }
QFrame#ActionPanel[compact="true"] { background: transparent; border: none; }
QLabel#ActionPanelTitle { font-size: 9pt; font-weight: 600; color: @muted; }
QLabel#CardTitle { font-size: 10pt; font-weight: 600; color: @text; }
QLabel#CardSubtitle { font-size: 9pt; color: @muted; }
QLabel#SelectedWorkLabel { font-size: 9pt; font-weight: 600; color: @accent; padding: 3px 1px; }
QPushButton#QuietAction { background: transparent; border-color: @border; }
QLabel#AppBrand { font-size: 13pt; font-weight: 700; }
QLabel#PageTitle { font-size: 16pt; font-weight: 600; }
QLabel#PageSubtitle, QLabel#ContextCaption, QLabel#SaveStatus, QLabel#Guide { color: @muted; font-size: 9pt; }
QLabel#SaveStatus[state="error"] { color: @danger; }
QLabel#SaveStatus[state="saved"] { color: @success; }
QFrame#PageHeader, QFrame#ContextStrip, QFrame#WorkflowGuide { background: @surface; border: 1px solid @border; border-radius: 6px; }
QFrame#WorkflowGuide { border-left: 3px solid @accent; }
QListWidget#SidebarNavigation { background: transparent; border: none; outline: 0; padding: 3px; }
QListWidget#SidebarNavigation::item { border-radius: 6px; padding: 10px 8px; margin: 2px 0; color: @text; }
QListWidget#SidebarNavigation::item:hover:!selected { background: @raised; }
QListWidget#SidebarNavigation::item:selected { background: @selected; color: @accent; border-left: 3px solid @accent; }
QListWidget, QTableWidget, QTreeWidget { background: @surface; alternate-background-color: @raised; border: 1px solid @border; border-radius: 5px; gridline-color: @border; outline: 0; }
QListWidget::item:selected, QTableWidget::item:selected { background: @selected; color: @text; }
QHeaderView::section { background: @raised; border: none; border-right: 1px solid @border; border-bottom: 1px solid @border; padding: 6px; font-size: 9pt; font-weight: 600; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {
    color: @text; background: @surface; border: 1px solid @border;
    border-radius: 5px; padding: 5px 7px; min-height: 23px;
    selection-background-color: @selected;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus { border: 2px solid @accent; }
QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled, QSpinBox:disabled { background: @readonly; color: @muted; }
QComboBox { padding-right: 23px; }
QComboBox::drop-down { width: 23px; border-left: 1px solid @border; }
QComboBox QAbstractItemView { background: @surface; color: @text; selection-background-color: @selected; }
QPushButton, QToolButton {
    background: @raised; color: @text; border: 1px solid @border;
    border-radius: 5px; padding: 6px 10px; min-height: 23px;
}
QPushButton:hover, QToolButton:hover { background: @selected; border-color: @accent; }
QPushButton:pressed, QToolButton:pressed { background: @readonly; }
QPushButton:disabled, QToolButton:disabled { background: @readonly; color: @muted; border-color: @border; }
QPushButton#PrimaryAction { background: @accent; color: @on_accent; border-color: @accent; font-weight: 600; }
QPushButton#PrimaryAction:hover { background: @accent_hover; }
QPushButton#DangerAction { color: @danger; border-color: @danger; }
QPushButton#DangerAction:hover { background: @selected; }
QPushButton#SideUtility { text-align: left; padding: 7px 9px; }
QTabWidget::pane { background: @surface; border: 1px solid @border; border-radius: 5px; }
QTabBar::tab { background: @raised; color: @text; border: 1px solid @border; border-bottom: none; padding: 8px 11px; }
QTabBar::tab:selected { background: @surface; border-top: 2px solid @accent; font-weight: 600; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 12px; }
QScrollBar:horizontal { background: transparent; height: 12px; }
QScrollBar::handle { background: @border; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: @border; }
QGroupBox { background: @surface; border: 1px solid @border; border-radius: 5px; margin-top: 8px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QToolTip { background: @surface; color: @text; border: 1px solid @border; padding: 7px; }
QMenu { background: @surface; color: @text; border: 1px solid @border; }
QMenu::item:selected { background: @selected; }
QStatusBar { background: @surface; color: @muted; border-top: 1px solid @border; }
QMessageBox { background: @background; }
"""

def apply_theme(app, theme: str, scale: int = 100) -> None:
    theme = theme if theme in PALETTES else "light"
    palette = PALETTES[theme]
    q_palette = QPalette()
    for role, key in [
        (QPalette.ColorRole.Window, "background"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "raised"),
        (QPalette.ColorRole.Button, "raised"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.Highlight, "selected"),
        (QPalette.ColorRole.HighlightedText, "text"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
    ]:
        q_palette.setColor(role, QColor(palette[key]))
    app.setPalette(q_palette)
    percent = 100
    base = QSS
    for key, value in sorted(palette.items(), key=lambda pair: len(pair[0]), reverse=True):
        base = base.replace("@" + key, value)
    # Text and controls scale together, without changing business or report amounts.
    base = base.replace("font-size: 10pt;", f"font-size: {10 * percent / 100:g}pt;")
    app.setStyleSheet(base)
