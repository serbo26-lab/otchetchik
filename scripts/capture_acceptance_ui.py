"""Capture visual acceptance snapshots of the real Qt workspace on Windows.

The data set is synthetic and stored in a temporary isolated data directory.
Images are uploaded only as CI test artifacts; never included in the EXE/release.
"""
from __future__ import annotations

import argparse
import json
import gc
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from src.uk728 import db
from src.uk728.paths import make_backup
from src.uk728.app import MainWindow


SCREENS = [
    ("uo", 0, None),
    ("houses", 1, None),
    ("categories", 2, None),
    ("works", 3, None),
    ("templates", 4, None),
    ("bulk", 5, None),
    ("reports_content", 6, 1),
    ("reports_repair", 6, 2),
    ("reports_management", 6, 3),
    ("reports_finance", 6, 4),
    ("reports_export", 6, 5),
    ("backups", 7, None),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    result = {
        "internal_scale": 100,
        "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", "1.0"),
        "windows_size": [1366, 900],
        "screens": [],
    }
    with tempfile.TemporaryDirectory(prefix="otchetchik-ui-", ignore_cleanup_errors=True) as workdir:
        with patch("src.uk728.paths.app_dir", return_value=Path(workdir)):
            db.init_db()
            org = db.execute("INSERT INTO companies(name) VALUES(?)", ("УК Приёмка",))
            house = db.execute("INSERT INTO houses(company_id,address,total_area) VALUES(?,?,?)",
                               (org, "ул. Центральная, д. 5", 1500))
            db.create_report(org, house, 2026)
            make_backup("manual")
            window = MainWindow()
            window.resize(1366, 900)
            window.show()
            try:
                window.houses_tab.reload()
                window.reports_tab.reload_filters()
                window.reports_tab.company_filter.setCurrentIndex(
                    window.reports_tab.company_filter.findData(org)
                )
                window.reports_tab.house_filter.setCurrentIndex(
                    window.reports_tab.house_filter.findData(house)
                )
                for theme in ("light", "dark"):
                    # Capture both themes, not just the one saved in runner settings.
                    if window._theme != theme:
                        window.switch_theme()
                    app.processEvents()
                    for name, section, tab in SCREENS:
                        window.navigate(section)
                        app.processEvents()
                        if tab is not None:
                            window.reports_tab.report_tabs.setCurrentIndex(tab)
                            app.processEvents()
                        image = window.grab()
                        if image.isNull() or image.width() < 1150 or image.height() < 750:
                            raise RuntimeError(
                                f"Qt screenshot failed or layout too small: {theme}/{name}"
                            )
                        destination = args.out_dir / f"{theme}_{name}.png"
                        if not image.save(str(destination), "PNG"):
                            raise RuntimeError(f"Could not save screenshot: {destination}")
                        result["screens"].append({
                            "theme": theme, "page": name,
                            "width": image.width(), "height": image.height(),
                            "filename": destination.name,
                        })
                if window._scale != 100 or window.sidebar.scale_combo.count() != 1:
                    raise RuntimeError("Experimental 125%/150% scale became visible.")
            finally:
                window.close()
                app.processEvents()
                window.deleteLater()
                del window
                gc.collect()
    summary = args.out_dir / "manifest.json"
    summary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Visual acceptance snapshots saved: {len(result['screens'])}; {summary}")


if __name__ == "__main__":
    main()
