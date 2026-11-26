"""The audit parser must never mistake incomplete or skipped tests for success."""
import unittest

from scripts.audit_ci_log import summarize


class CiAuditTests(unittest.TestCase):
    def test_complete_modules_ignore_focused_runs_before_inventory(self):
        log = """Ran 4 tests in 0.1s
OK
=== UI regression: test_a.py ===
Ran 3 tests in 0.1s
OK
=== UI regression: test_b.py ===
Ran 2 tests in 0.1s
OK
"""
        result = summarize(log, {"test_a.py", "test_b.py"})
        self.assertTrue(result["complete"])
        self.assertEqual(result["test_count"], 5)

    def test_incomplete_failed_skipped_and_repeated_modules_rejected(self):
        log = """=== UI regression: test_a.py ===
Ran 3 tests in 0.1s
OK (skipped=1)
=== UI regression: test_b.py ===
Ran 2 tests in 0.1s
FAILED (failures=1)
=== UI regression: test_c.py ===
Ran 1 test in 0.1s
=== UI regression: test_d.py ===
Ran 1 test in 0.1s
OK
=== UI regression: test_d.py ===
Ran 1 test in 0.1s
OK
"""
        result = summarize(log, {"test_a.py", "test_b.py", "test_c.py", "test_d.py", "test_e.py"})
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], ["test_e.py"])
        self.assertEqual(result["unsuccessful_or_repeated"],
                         ["test_a.py", "test_b.py", "test_c.py", "test_d.py"])


if __name__ == "__main__":
    unittest.main()
