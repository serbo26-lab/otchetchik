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
from PySide6.QtCore import QPoint, QSettings
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
        if isinstance(w, QScrollArea):
            item["horizontal_max"] = w.horizontalScrollBar().maximum()
            item["vertical_max"] = w.verticalScrollBar().maximum()
        controls.append(item)
    buttons = [c for c in controls if c["visible"] and "checked" in c]
    primary = [c["text"] for c in buttons if c["object"] in ("PrimaryAction", "PrimaryButton")]
    overflow = [c["text"] for c in buttons if not c["inside_table"]
                and (c["rect"][0] < 0 or c["rect"][0] + c["rect"][2] > root.width())]
    hscroll = [c["object"] for c in controls if c["visible"] and c.get("horizontal_max", 0)]
    print("UX", name, theme, size, "buttons", len(buttons), "primary", primary,
          "horizontal_scroll", hscroll, "outside_root", overflow, flush=True)
    records.append({"state": name, "theme": theme, "requested_size": size,
                    "actual_size": [root.width(), root.height()], "controls": controls,
                    "visible_primary": primary, "horizontal_scroll": hscroll,
                    "outside_root": overflow})
    if size[0] in (1366, 860):
        root.grab().save(str(output / (name + "_" + theme + "_" + str(size[0]) + ".png")))


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
            win.close()
            win.deleteLater()
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
    repository = Repository(folder / "assistant.db")
    for state in ("initial", "populated"):
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
