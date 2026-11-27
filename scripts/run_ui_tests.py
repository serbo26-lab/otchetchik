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


def accepted(result: unittest.TestResult) -> bool:
    """No empty, skipped or expected-failure suite may certify a build."""
    return (result.testsRun > 0 and result.wasSuccessful()
            and not result.skipped and not result.expectedFailures)


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
        if not accepted(result):
            print("Acceptance requires nonempty tests without skips or expected failures.", file=sys.stderr)
            return 1
        return 0
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    sys.exit(main())
