"""Diagnostic UX comparison; fictional records only, no live GIS commands."""
import ast
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QWidget, QAbstractButton, QComboBox, QLineEdit, QTextEdit,
    QListWidget, QTableWidget, QSpinBox, QScrollArea, QLabel, QTabWidget,
)

CONTROLS = (QAbstractButton, QComboBox, QLineEdit, QTextEdit, QListWidget,
            QTableWidget, QSpinBox, QScrollArea, QLabel, QTabWidget)
UI_CALLS = {"connect", "setEnabled", "setDisabled", "setVisible", "show", "hide",
            "setToolTip", "setAccessibleName", "setTabOrder", "setFocus",
            "addAction", "addWidget", "addTab", "setShortcut",
            "setMinimumWidth", "setMaximumWidth", "setFixedWidth"}


def inventory(root):
    entries = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                entries.append({"file": str(path.relative_to(root)), "line": node.lineno,
                                "type": type(node).__name__, "name": node.name})
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name.startswith(("QPushButton", "QToolButton", "QCheckBox",
                                    "QRadioButton", "QAction", "QComboBox",
                                    "QDialogButtonBox", "QMenu", "QLineEdit", "QTableWidget")) or name in UI_CALLS:
                    entries.append({"file": str(path.relative_to(root)), "line": node.lineno,
                                    "type": "call", "name": name, "expression": ast.unparse(node)[:500]})
    return entries


def rect_in_root(widget, root):
    return QRect(widget.mapTo(root, QPoint(0, 0)), widget.size())


def clipped_rect(widget, root):
    """Qt visibility alone excludes hidden widgets, not ancestor clipping."""
    full = rect_in_root(widget, root)
    visible = full
    parent = widget.parentWidget()
    while parent is not None:
        visible = visible.intersected(rect_in_root(parent, root))
        if parent is root:
            break
        parent = parent.parentWidget()
    return full, visible


def reveal_by_scroll(widget, root, app):
    """Try ordinary scroll containers, then restore their original positions.

    A clipped result is a review candidate, not automatically a UX defect:
    splitters, minimum supported size, intentional scrollable tables, and
    composite controls must be evaluated in their actual working context.
    """
    full, visible = clipped_rect(widget, root)
    if full == visible and not full.isEmpty():
        return "visible", (0, 0)
    areas = []
    parent = widget.parentWidget()
    while parent is not None and parent is not root:
        if isinstance(parent, QScrollArea) and parent.widget() is not None and parent.widget().isAncestorOf(widget):
            areas.append(parent)
        parent = parent.parentWidget()
    original = [(a, a.horizontalScrollBar().value(), a.verticalScrollBar().value()) for a in areas]
    try:
        for area in areas:
            area.ensureWidgetVisible(widget, 0, 0)
            app.processEvents()
        full, visible = clipped_rect(widget, root)
        loss = (max(0, full.width() - visible.width()), max(0, full.height() - visible.height()))
        if full == visible and not full.isEmpty():
            return "reachable_by_scroll", loss
        if visible.isEmpty():
            return "not_revealed", loss
        return "partly_revealed", loss
    finally:
        for area, horizontal, vertical in reversed(original):
            area.horizontalScrollBar().setValue(horizontal)
            area.verticalScrollBar().setValue(vertical)
        if areas:
            app.processEvents()


def keyboard_inventory(root):
    """Inspect the Qt focus chain, without executing actions or changing data."""
    result = []
    seen = set()
    widget = root.nextInFocusChain()
    while widget is not root and id(widget) not in seen:
        seen.add(id(widget))
        if (widget.isVisibleTo(root) and widget.isEnabled()
                and widget.focusPolicy() & Qt.TabFocus):
            result.append({
                "class": type(widget).__name__, "object": widget.objectName(),
                "text": widget.text() if isinstance(widget, QAbstractButton) else "",
                "accessible_name": widget.accessibleName(),
            })
        widget = widget.nextInFocusChain()
    return result


def snapshot(root, name, theme, size, app, output, records):
    app.processEvents()
    app.processEvents()
    controls = []
    for w in root.findChildren(QWidget):
        if not isinstance(w, CONTROLS):
            continue
        pos = w.mapTo(root, QPoint(0, 0))
        ancestry = []
        parent = w.parentWidget()
        while parent is not None and parent is not root:
            ancestry.append(type(parent).__name__ + ":" + parent.objectName())
            parent = parent.parentWidget()
        item = {"class": type(w).__name__, "object": w.objectName(),
                "visible": w.isVisibleTo(root), "enabled": w.isEnabled(),
                "tooltip": w.toolTip(), "accessible_name": w.accessibleName(),
                "focus_policy": str(w.focusPolicy()), "ancestors": ancestry,
                "rect": [pos.x(), pos.y(), w.width(), w.height()],
                "size_hint": [w.sizeHint().width(), w.sizeHint().height()]}
        if isinstance(w, (QAbstractButton, QLabel)):
            item["text"] = w.text()
        if isinstance(w, QAbstractButton):
            item["checked"] = w.isChecked()
            item["inside_table"] = any("Table" in parent for parent in ancestry)
            if item["visible"] and item["text"] and not item["inside_table"]:
                full, visible = clipped_rect(w, root)
                item["visible_rect"] = [visible.x(), visible.y(), visible.width(), visible.height()]
                item["reachability"], item["clipped_pixels"] = reveal_by_scroll(w, root, app)
                item["label_width"] = w.fontMetrics().horizontalAdvance(w.text())
                item["widget_width"] = full.width()
        if isinstance(w, QScrollArea):
            item["horizontal_max"] = w.horizontalScrollBar().maximum()
            item["vertical_max"] = w.verticalScrollBar().maximum()
        controls.append(item)
    buttons = [c for c in controls if c["visible"] and "checked" in c]
    primary = [c["text"] for c in buttons if c["object"] in ("PrimaryAction", "PrimaryButton")]
    overflow = [c["text"] for c in buttons if not c["inside_table"]
                and (c["rect"][0] < 0 or c["rect"][0] + c["rect"][2] > root.width())]
    hscroll = [c["object"] for c in controls if c["visible"] and c.get("horizontal_max", 0)]
    reachability = {state: [c["text"] for c in buttons if c.get("reachability") == state]
                    for state in ("visible", "reachable_by_scroll", "partly_revealed", "not_revealed")}
    focus_chain = keyboard_inventory(root)
    clipped = [(c["text"], c["clipped_pixels"]) for c in buttons
               if c.get("reachability") in ("partly_revealed", "not_revealed")]
    print("UX", name, theme, size, "buttons", len(buttons),
          "scroll_reachable", len(reachability["reachable_by_scroll"]),
          "partly_revealed", reachability["partly_revealed"],
          "not_revealed", reachability["not_revealed"],
          "clipped_pixels", clipped,
          "keyboard_controls", len(focus_chain), flush=True)
    records.append({"state": name, "theme": theme, "requested_size": size,
                    "actual_size": [root.width(), root.height()], "controls": controls,
                    "visible_primary": primary, "horizontal_scroll": hscroll,
                    "outside_root": overflow, "reachability": reachability,
                    "focus_chain": focus_chain})
    if size[0] in (1366, 860):
        root.grab().save(str(output / (name + "_" + theme + "_" + str(size[0]) + ".png")))
    if size[0] == 1366 and theme == "light" and name.startswith("populated_"):
        records[-1]["tab_walk"] = keyboard_walk(root, app)
        print("TAB_WALK", name, records[-1]["tab_walk"], flush=True)


def keyboard_walk(root, app):
    """Exercise Tab without activating commands; report actual focus order."""
    original = app.focusWidget()
    scrolls = [(a, a.horizontalScrollBar().value(), a.verticalScrollBar().value())
               for a in root.findChildren(QScrollArea)]
    root.activateWindow()
    app.processEvents()
    first = next((w for w in root.findChildren(QWidget)
                  if w.isVisibleTo(root) and w.isEnabled()
                  and w.focusPolicy() & Qt.TabFocus), None)
    if first is None:
        return []
    first.setFocus(Qt.TabFocusReason)
    app.processEvents()
    seen = set()
    result = []
    try:
        for _ in range(200):
            widget = app.focusWidget()
            if widget is None or not root.isAncestorOf(widget) or id(widget) in seen:
                break
            seen.add(id(widget))
            full, visible = clipped_rect(widget, root)
            result.append({"class": type(widget).__name__, "object": widget.objectName(),
                           "text": widget.text() if isinstance(widget, QAbstractButton) else "",
                           "in_view": not visible.isEmpty()})
            QTest.keyClick(widget, Qt.Key_Tab)
            app.processEvents()
    finally:
        if original is not None and root.isAncestorOf(original) and original.isVisible():
            original.setFocus()
        for area, horizontal, vertical in scrolls:
            area.horizontalScrollBar().setValue(horizontal)
            area.verticalScrollBar().setValue(vertical)
        app.processEvents()
    return result


SIZES = ((1366, 900), (1100, 700), (860, 520))


def reporter(app, folder, output):
    from src.uk728 import db
    from src.uk728.app import MainWindow
    from src.uk728.ui_style import apply_theme
    from src.uk728.reports_ui import AddFromReferenceDialog, CreateReportDialog
    from src.uk728.paths import make_backup
    records = []
    with patch("src.uk728.paths.app_dir", return_value=folder):
        db.init_db()
        for state in ("initial", "populated"):
            if state == "populated":
                cid = db.execute("INSERT INTO companies(name) VALUES(?)", ("Учебная УО",))
                hid = db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (cid, "Учебный дом А"))
                db.execute("INSERT INTO houses(company_id,address) VALUES(?,?)", (cid, "Учебный дом Б"))
                db.create_report(cid, hid, 2026)
                make_backup("manual")
            win = MainWindow()
            win.timer.stop()
            win.context_timer.stop()
            win.show()
            for theme in ("light", "dark"):
                apply_theme(app, theme)
                for size in SIZES:
                    win.resize(*size)
                    for index in range(win.tabs.count()):
                        win.navigate(index)
                        page = win.tabs.widget(index)
                        if state == "populated" and index == 6:
                            page.company_filter.setCurrentIndex(page.company_filter.findData(cid))
                            page.house_filter.setCurrentIndex(page.house_filter.findData(hid))
                            for section in range(page.report_tabs.count()):
                                page.report_tabs.setCurrentIndex(section)
                                win.refresh_context()
                                snapshot(win, f"{state}_report_{section}", theme, size, app, output, records)
                            page.run_review()
                            win.refresh_context()
                            snapshot(win, f"{state}_report_reviewed", theme, size, app, output, records)
                        else:
                            win.refresh_context()
                            snapshot(win, f"{state}_page_{index}", theme, size, app, output, records)
                    if state == "populated" and size[0] == 1366:
                        for dialog in (CreateReportDialog(2026, win), AddFromReferenceDialog("Содержание", win)):
                            dialog.show()
                            snapshot(dialog, "dialog_" + type(dialog).__name__, theme, size, app, output, records)
                            dialog.reject()
                            dialog.deleteLater()
                            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                            app.processEvents()
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    return records


def assistant(app, folder, output):
    from gis_jkh_assistant.app import main_window
    from gis_jkh_assistant.app.styles import apply_theme
    from gis_jkh_assistant.database import Repository
    from gis_jkh_assistant.gis_integration import IntegrationStatus

    class OfflineGateway:
        def __init__(self):
            self.started = False
        def start(self):
            self.started = True
        def stop(self):
            self.started = False
        def status(self):
            return IntegrationStatus(running=self.started, helper_connected=False,
                                     gis_page_ready=False, helper_version="0.1.30")
        def execute(self, *args, **kwargs):
            raise AssertionError("Live GIS operation forbidden in UX inventory")

    records = []
    for state in ("initial", "populated"):
        # MainWindow.close() closes its repository; isolate each state.
        repository = Repository(folder / f"assistant_{state}.db")
        if state == "populated":
            org = repository.create_organization(name="Учебная УО", ogrn="0000000000000")
            house = repository.create_house(organization_id=org.id, address="Учебный дом А",
                                            house_object_guid="synthetic-house", fias_house_guid="synthetic-fias")
            work = repository.create_work_list(house_id=house.id, base_year=2026, items=[
                SimpleNamespace(position=1, work_code="12", work_name="Учебная работа",
                                work_guid="synthetic-work", unit_name="усл.", price="100",
                                volume="1", quantity="12", cost="1200")])
            repository.set_setting("ui.shared_context_enabled", "1")
            for key, value in (("organization_id", org.id), ("house_id", house.id),
                               ("work_list_id", work.id), ("year", "2026")):
                repository.set_setting("ui.context." + key, value)
        with patch.object(main_window, "LocalIntegrationGateway", return_value=OfflineGateway()):
            win = main_window.MainWindow(repository)
            win.integration_status_timer.stop()
            win.show()
            for theme in ("light", "dark"):
                apply_theme(app, theme)
                for size in SIZES:
                    win.resize(*size)
                    for index in range(win.pages.count()):
                        win.navigation.setCurrentRow(index)
                        snapshot(win, f"{state}_page_{index}", theme, size, app, output, records)
            win.close()
            win.deleteLater()
            app.processEvents()
    return records


def main():
    kind, root, output = sys.argv[1:]
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = root / "src" / ("uk728" if kind == "reporter" else "gis_jkh_assistant/app")
    entries = inventory(source)
    (output / "static.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print("STATIC", kind, len(entries), flush=True)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "src"))
    with TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        folder = Path(temp)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(folder))
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        records = (reporter if kind == "reporter" else assistant)(app, folder, output)
        (output / "runtime.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RUNTIME", kind, len(records), "states", sum(len(r["controls"]) for r in records), "observations", flush=True)


if __name__ == "__main__":
    main()
