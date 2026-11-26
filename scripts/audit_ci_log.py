"""Check that the Windows CI log actually contains a successful run of every test module."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MODULE = re.compile(r"=== UI regression: (test_[\w]+\.py) ===")
COUNT = re.compile(r"\bRan (\d+) tests? in ")
RESULT = re.compile(r"(?:^|\s)(OK|FAILED)(?:\s*\(([^)]*)\))?\s*$")


def summarize(log, expected):
    modules = {}
    current = None
    for line in log.splitlines():
        marker = MODULE.search(line)
        if marker:
            current = marker.group(1)
            modules.setdefault(current, []).append({"count": None, "result": None, "details": ""})
        elif current:
            run = modules[current][-1]
            count = COUNT.search(line)
            result = RESULT.search(line)
            if count:
                run["count"] = int(count.group(1))
            if result:
                run["result"] = result.group(1)
                run["details"] = result.group(2) or ""
                current = None
    missing = sorted(set(expected) - set(modules))
    unsuccessful = sorted(
        name for name, runs in modules.items()
        if len(runs) != 1 or any(
            run["count"] is None or run["count"] == 0 or run["result"] != "OK" or run["details"]
            for run in runs
        )
    )
    return {
        "expected_modules": len(expected), "found_modules": len(modules),
        "test_count": sum(run["count"] or 0 for runs in modules.values() for run in runs),
        "missing": missing, "unsuccessful_or_repeated": unsuccessful,
        "complete": not missing and not unsuccessful and set(modules) == set(expected),
    }


def main():
    log = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
    expected = {path.name for path in Path("tests").glob("test_*.py")}
    result = summarize(log, expected)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
