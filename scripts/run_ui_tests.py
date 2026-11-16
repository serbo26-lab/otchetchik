"""Run the Windows UI acceptance suite with deadlock diagnostics.

Qt modal dialogs and layout recursion can otherwise stall an unattended runner.
Print the current test name, then dump all thread stacks and fail if a test
suite exceeds the watchdog, rather than publishing an unverified executable.
"""
from __future__ import annotations

import argparse
import faulthandler
import sys
import unittest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    faulthandler.enable()
    faulthandler.dump_traceback_later(args.timeout, repeat=False, exit=True)
    try:
        suite = unittest.defaultTestLoader.discover("tests", pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    sys.exit(main())
