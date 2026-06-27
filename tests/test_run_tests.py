"""run_tests() aggregates one or more gate commands. The contract the verifier
depends on: the FIRST line is the overall TESTS: PASSED/FAILED/SKIPPED status, and
overall is PASSED only if every gate passed. We fake driver.run so nothing is
actually executed — this tests the aggregation, not the shell."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver  # noqa: E402


class TestRunTests(unittest.TestCase):
    def setUp(self):
        self._orig_run = driver.run
        self._orig_cmds = driver.TEST_COMMANDS

    def tearDown(self):
        driver.run = self._orig_run
        driver.TEST_COMMANDS = self._orig_cmds

    def fake_run(self, results):
        """results: dict mapping the gate command -> (rc, stdout, stderr)."""
        calls = []

        def _run(cmd, *, timeout, cwd=driver.REPO_ROOT, stdin=None):
            gate = cmd[-1]  # ["bash", "-lc", gate]
            calls.append(gate)
            return results[gate]

        driver.run = _run
        return calls

    def test_no_gates_is_skipped(self):
        driver.TEST_COMMANDS = []
        ran, passed, out = driver.run_tests()
        self.assertFalse(ran)
        self.assertTrue(passed)
        self.assertTrue(out.startswith("TESTS: SKIPPED"))

    def test_blank_gates_are_ignored(self):
        driver.TEST_COMMANDS = ["", "   "]
        ran, passed, out = driver.run_tests()
        self.assertFalse(ran)
        self.assertTrue(out.startswith("TESTS: SKIPPED"))

    def test_all_gates_pass(self):
        driver.TEST_COMMANDS = ["lint", "pytest"]
        self.fake_run({"lint": (0, "ok\n", ""), "pytest": (0, "5 passed\n", "")})
        ran, passed, out = driver.run_tests()
        self.assertTrue(ran)
        self.assertTrue(passed)
        self.assertEqual(out.splitlines()[0], "TESTS: PASSED")
        self.assertIn("[PASS] $ lint", out)
        self.assertIn("[PASS] $ pytest", out)

    def test_one_gate_fails_makes_overall_fail(self):
        driver.TEST_COMMANDS = ["lint", "pytest"]
        self.fake_run({"lint": (0, "ok\n", ""), "pytest": (1, "1 failed\n", "boom")})
        ran, passed, out = driver.run_tests()
        self.assertTrue(ran)
        self.assertFalse(passed)
        self.assertEqual(out.splitlines()[0], "TESTS: FAILED")
        self.assertIn("[PASS] $ lint", out)
        self.assertIn("[FAIL] $ pytest", out)

    def test_gates_run_in_order(self):
        driver.TEST_COMMANDS = ["first", "second", "third"]
        calls = self.fake_run({k: (0, "", "") for k in ["first", "second", "third"]})
        driver.run_tests()
        self.assertEqual(calls, ["first", "second", "third"])


if __name__ == "__main__":
    unittest.main()
