"""Read-only inventory of historical Git revisions; run from a full clone."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

BASES = {
    "V1.6.0": "4913bd1d1ed312dace9b54552ec011ae2f15cc2e",
    "V1.6.20": "fa1869e77242093c247da636ebda700f5a8c1b61",
    "V1.6.63": "712e8818eedd762cfb6df4452ff802e7348f28ba",
}
CORE = ("db.py", "exporters.py", "utils.py", "mass_apply_logic.py")
ALLOWED = {
    "MainWindow.set_scale",
    "CompaniesTab.delete_report", "HousesTab.delete_report",
    "CategoriesTab.delete_report", "TemplatesTab.delete_report",
    "MassApplyTab._section_for_work", "MassApplyTab._target_table",
    "MassApplyTab._find_existing", "MassApplyTab._calc_for_house",
}


def git(*args):
    return subprocess.check_output(("git", *args), text=True, encoding="utf-8")


def inventory(ref):
    paths = git("ls-tree", "-r", "--name-only", ref).splitlines()
    classes, methods, core = set(), set(), {}
    for path in paths:
        if not path.startswith("src/uk728/") or not path.endswith(".py"):
            continue
        root = ast.parse(git("show", f"{ref}:{path}"), filename=path)
        for node in root.body:
            if isinstance(node, ast.ClassDef):
                classes.add(node.name)
                methods.update(f"{node.name}.{child.name}" for child in node.body
                               if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
        if Path(path).name in CORE:
            core[Path(path).name] = {node.name for node in root.body
                                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return classes, methods, core


def compare(base, current):
    classes, methods, core = base
    current_classes, current_methods, current_core = current
    missing = methods - current_methods
    return {
        "missing_classes": sorted(classes - current_classes),
        "documented_removed_methods": sorted(missing & ALLOWED),
        "unexpected_removed_methods": sorted(missing - ALLOWED),
        "missing_core_functions": {
            path: sorted(names - current_core.get(path, set()))
            for path, names in core.items()
        },
    }


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    current = inventory(ref)
    results = {version: compare(inventory(sha), current) for version, sha in BASES.items()}
    print(json.dumps({"candidate": git("rev-parse", ref).strip(), "results": results},
                     ensure_ascii=False, indent=2))
    failed = any(result["missing_classes"] or result["unexpected_removed_methods"]
                 or any(result["missing_core_functions"].values()) for result in results.values())
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
