"""Measure real branch/line coverage using isolated Windows UI test modules.

Install coverage and the project dependencies first. Each module runs with the
same watchdog as the ordinary Windows build. Reports are produced even when a
test fails; a failing or empty module never counts as an accepted run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "coverage-results"


def command(*args, env=None):
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=False).returncode


def function_status(region):
    """Importing a definition is not evidence of executing its body."""
    start = region["start_line"]
    body = set(region["executed_lines"]) | set(region["missing_lines"])
    if not body:
        return "no-measurable-body"
    if body == {start}:
        return "same-line-definition"
    return "executed" if set(region["executed_lines"]) - {start} else "not-executed"


def function_inventory(report):
    """Use coverage's native qualified function regions, including nested code."""
    inventory = []
    for filename, file_report in sorted(report["files"].items()):
        for name, region in sorted(file_report["functions"].items()):
            if name:
                inventory.append({"file": filename, "name": name,
                                  "line": region["start_line"],
                                  "status": function_status(region)})
    return inventory


def main():
    OUTPUT.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["COVERAGE_FILE"] = str(OUTPUT / ".coverage")
    if command("-m", "coverage", "erase", env=env):
        return 1
    modules = sorted((ROOT / "tests").glob("test_*.py"))
    if not modules:
        print("No test modules discovered.", flush=True)
        return 1
    failures = []
    for module in modules:
        print(f"=== Coverage regression: {module.name} ===", flush=True)
        rc = command("-m", "coverage", "run", "--parallel-mode", "--branch",
                     "--source=src/uk728", "-m", "scripts.run_ui_tests",
                     "--pattern", module.name, "--timeout", "180", env=env)
        if rc:
            failures.append(module.name)
    if command("-m", "coverage", "combine", str(OUTPUT), env=env):
        return 1
    if command("-m", "coverage", "xml", "-o", str(OUTPUT / "coverage.xml"), env=env):
        return 1
    report_path = OUTPUT / "coverage.json"
    if command("-m", "coverage", "json", "-o", str(report_path), env=env):
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    inventory = function_inventory(report)
    result = {"modules": len(modules), "failed_modules": failures,
              "totals": report["totals"], "functions": inventory}
    (OUTPUT / "function-inventory.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    command("-m", "coverage", "report", "--show-missing", env=env)
    print(f"Modules: {len(modules)}; failed: {failures}", flush=True)
    missing = [f for f in inventory if f["status"] == "not-executed"]
    print(f"Functions with no measured execution: {len(missing)}", flush=True)
    for entry in missing:
        print(f"{entry['file']}:{entry['line']} {entry['name']}", flush=True)
    print(f"Line coverage: {report['totals']['percent_statements_covered']:.2f}%", flush=True)
    print(f"Branch coverage: {report['totals']['percent_branches_covered']:.2f}%", flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
