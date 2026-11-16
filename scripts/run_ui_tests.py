"""Run the Windows UI acceptance suite with deadlock diagnostics.

Qt modal dialogs and layout recursion can otherwise stall an unattended runner.
Print the current test name, then dump all thread stacks and fail if a test
suite exceeds the watchdog, rather than publishing an unverified executable.
"""
from __future__ import annotations

import faulthandler
import sys
import unittest


def main() -> int:
    faulthandler.enable()
    # GitHub Windows runners historically complete this suite in ~1 minute;
    # generous watchdog permits slow machines while exposing a stuck UI.
    faulthandler.dump_traceback_later(180, repeat=False, exit=True)
    try:
        suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    sys.exit(main())
