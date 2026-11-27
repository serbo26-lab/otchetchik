"""Measure real branch/line coverage using isolated Windows UI test modules.

Install coverage and the project dependencies first. Each module runs with the
same watchdog as the ordinary Windows build. Reports are produced even when a
test fails; a failing or empty module never counts as an accepted run.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "coverage-results"


def command(*args, env=None):
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=False).returncode


def function_inventory(report):
    """List functions whose body has no executed statement (not just definition)."""
    inventory = []
    for filename, coverage in sorted(report["files"].items()):
        path = ROOT / filename
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        executed = set(coverage["executed_lines"])
        statements = executed | set(coverage["missing_lines"])
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_start = node.body[0].lineno
            # The function definition is executed on import; that does not
            # establish that the function was called.
            if body_start == node.lineno:
                # A single-line function cannot be distinguished using lines.
                status = "same-line-definition"
            else:
                body = {line for line in statements if body_start <= line <= node.end_lineno}
                if not body:
                    status = "no-measurable-body"
                else:
                    status = "executed" if body & executed else "not-executed"
            inventory.append({"file": filename, "name": node.name,
                              "line": node.lineno, "status": status})
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
    print(f"Functions with no measured execution: {sum(f['status'] == 'not-executed' for f in inventory)}",
          flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
