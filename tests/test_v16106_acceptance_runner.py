"""Regression tests for the acceptance runner itself."""
import io
import unittest

from scripts.run_ui_tests import accepted


def outcome(action):
    class Example(unittest.TestCase):
        def runTest(self):
            action(self)

    suite = unittest.TestSuite([Example()])
    return unittest.TextTestRunner(stream=io.StringIO()).run(suite)


class AcceptanceRunnerTests(unittest.TestCase):
    def test_success_is_accepted(self):
        self.assertTrue(accepted(outcome(lambda case: case.assertEqual(1, 1))))

    def test_empty_suite_is_rejected(self):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite())
        self.assertFalse(accepted(result))

    def test_skipped_suite_is_rejected(self):
        self.assertFalse(accepted(outcome(lambda case: case.skipTest("synthetic skip"))))

    def test_failed_suite_is_rejected(self):
        self.assertFalse(accepted(outcome(lambda case: case.fail("synthetic failure"))))

    def test_expected_failure_is_rejected(self):
        class Example(unittest.TestCase):
            @unittest.expectedFailure
            def runTest(self):
                self.fail("synthetic expected failure")

        result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([Example()]))
        self.assertFalse(accepted(result))
