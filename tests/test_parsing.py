"""Pure parsing/branching logic, exercised without spawning any subprocess.

These cover the functions that decide what the loop believes happened: the Claude
JSON envelope, the executor output, the verifier's verdict, the fence-stripping
tolerance, and the stall fingerprint. Malformed input is tested as carefully as
the happy path — that is where an autonomous loop gets it wrong."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver  # noqa: E402


class TestStripFences(unittest.TestCase):
    def test_plain_json_unchanged(self):
        self.assertEqual(driver.strip_fences('{"a": 1}'), '{"a": 1}')

    def test_json_language_fence(self):
        self.assertEqual(driver.strip_fences('```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_bare_fence(self):
        self.assertEqual(driver.strip_fences('```\n{"a": 1}\n```'), '{"a": 1}')

    def test_surrounding_whitespace(self):
        self.assertEqual(driver.strip_fences('  \n{"a": 1}\n  '), '{"a": 1}')

    def test_fence_without_closing(self):
        # Tolerate a truncated/streamed fence: strip the opener, keep the body.
        self.assertEqual(driver.strip_fences('```json\n{"a": 1}'), '{"a": 1}')


class TestParseClaudeEnvelope(unittest.TestCase):
    def test_valid_envelope(self):
        out = json.dumps({"result": "hello", "total_cost_usd": 0.12, "is_error": False})
        res = driver.parse_claude_envelope(out, "", 0)
        self.assertEqual(res["result"], "hello")
        self.assertEqual(res["cost"], 0.12)
        self.assertFalse(res["is_error"])
        self.assertEqual(res["raw"]["result"], "hello")

    def test_missing_fields_default_safely(self):
        res = driver.parse_claude_envelope("{}", "", 0)
        self.assertEqual(res["result"], "")
        self.assertEqual(res["cost"], 0.0)
        self.assertFalse(res["is_error"])

    def test_null_cost_coerces_to_zero(self):
        out = json.dumps({"result": "x", "total_cost_usd": None})
        self.assertEqual(driver.parse_claude_envelope(out, "", 0)["cost"], 0.0)

    def test_nonzero_returncode_forces_is_error(self):
        out = json.dumps({"result": "x", "is_error": False})
        self.assertTrue(driver.parse_claude_envelope(out, "boom", 1)["is_error"])

    def test_envelope_is_error_true_is_respected(self):
        out = json.dumps({"result": "x", "is_error": True})
        self.assertTrue(driver.parse_claude_envelope(out, "", 0)["is_error"])

    def test_non_json_raises_steperror(self):
        with self.assertRaises(driver.StepError):
            driver.parse_claude_envelope("not json at all", "stderr", 0)


class TestParseExecutorOutput(unittest.TestCase):
    def test_plain_text_backend_combines_stdout_stderr(self):
        rc, combined, cost = driver.parse_executor_output(
            "did stuff\n", "a warning", 0, "cursor"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(combined, "did stuff\n\na warning")
        self.assertEqual(cost, 0.0)

    def test_plain_text_no_stderr(self):
        _, combined, _ = driver.parse_executor_output("only stdout", "", 0, "cursor")
        self.assertEqual(combined, "only stdout")

    def test_claude_envelope_extracts_result_and_cost(self):
        out = json.dumps({"result": "applied edits", "total_cost_usd": 0.4})
        rc, combined, cost = driver.parse_executor_output(out, "", 0, "claude")
        self.assertEqual(combined, "applied edits")
        self.assertEqual(cost, 0.4)

    def test_claude_malformed_envelope_falls_back_to_text(self):
        # A non-JSON stdout from the claude backend must NOT raise here; it falls
        # back to the combined text with zero cost.
        rc, combined, cost = driver.parse_executor_output("oops", "err", 3, "claude")
        self.assertEqual(rc, 3)
        self.assertEqual(combined, "oops\nerr")
        self.assertEqual(cost, 0.0)

    def test_returncode_passes_through(self):
        rc, _, _ = driver.parse_executor_output("", "", 42, "cursor")
        self.assertEqual(rc, 42)


class TestParseVerdict(unittest.TestCase):
    def test_valid_verdict(self):
        v = driver.parse_verdict('{"status": "pass", "criteria": []}')
        self.assertEqual(v["status"], "pass")

    def test_fenced_verdict(self):
        v = driver.parse_verdict('```json\n{"status": "fail"}\n```')
        self.assertEqual(v["status"], "fail")

    def test_invalid_json_raises(self):
        with self.assertRaises(driver.StepError):
            driver.parse_verdict("definitely not json")

    def test_missing_status_raises(self):
        with self.assertRaises(driver.StepError):
            driver.parse_verdict('{"criteria": [], "reasons": []}')

    def test_prose_preamble_before_json(self):
        # A strong verify model sometimes narrates before the JSON despite the
        # JSON-only contract (observed with opus): recover the object anyway.
        raw = 'All criteria are satisfied and tests pass.\n\n{"status": "pass", "criteria": []}'
        v = driver.parse_verdict(raw)
        self.assertEqual(v["status"], "pass")

    def test_prose_around_json(self):
        raw = 'Here is the verdict:\n{"status": "fail", "reasons": ["x"]}\nHope that helps!'
        v = driver.parse_verdict(raw)
        self.assertEqual(v["status"], "fail")
        self.assertEqual(v["reasons"], ["x"])

    def test_fenced_with_preamble(self):
        raw = 'Summary line.\n```json\n{"status": "blocked"}\n```'
        self.assertEqual(driver.parse_verdict(raw)["status"], "blocked")

    def test_non_object_json_raises(self):
        # Valid JSON that isn't an object (a bare array) is still malformed.
        with self.assertRaises(driver.StepError):
            driver.parse_verdict("[1, 2, 3]")


class TestProgressFingerprint(unittest.TestCase):
    def test_deterministic(self):
        a = driver.progress_fingerprint(["C1 failed"], "diff-text")
        b = driver.progress_fingerprint(["C1 failed"], "diff-text")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)  # sha256 hex

    def test_different_reasons_differ(self):
        a = driver.progress_fingerprint(["C1 failed"], "d")
        b = driver.progress_fingerprint(["C2 failed"], "d")
        self.assertNotEqual(a, b)

    def test_different_diff_differs(self):
        a = driver.progress_fingerprint(["C1"], "diff-A")
        b = driver.progress_fingerprint(["C1"], "diff-B")
        self.assertNotEqual(a, b)

    def test_reason_order_matters(self):
        a = driver.progress_fingerprint(["C1", "C2"], "d")
        b = driver.progress_fingerprint(["C2", "C1"], "d")
        self.assertNotEqual(a, b)

    def test_empty_inputs_are_stable(self):
        self.assertEqual(
            driver.progress_fingerprint([], ""),
            driver.progress_fingerprint([], ""),
        )


class TestNormalizeQuestion(unittest.TestCase):
    def test_well_formed_dict_passthrough(self):
        q = {"id": "Q1", "question": "Which API?", "why": "plan depends on it"}
        self.assertEqual(driver.normalize_question(q), q)

    def test_bare_string_becomes_question_text(self):
        self.assertEqual(
            driver.normalize_question("Which API?"),
            {"id": "?", "question": "Which API?", "why": ""},
        )

    def test_alternate_keys_are_mapped(self):
        # the model sometimes emits finding-shaped objects (title/description)
        q = {"id": "X", "title": "Ambiguous output format", "description": "sha or hex?"}
        self.assertEqual(
            driver.normalize_question(q),
            {"id": "X", "question": "Ambiguous output format", "why": "sha or hex?"},
        )

    def test_missing_id_defaults(self):
        self.assertEqual(driver.normalize_question({"question": "q"})["id"], "?")

    def test_non_string_values_are_stringified(self):
        out = driver.normalize_question({"id": 3, "question": 42})
        self.assertEqual(out, {"id": "3", "question": "42", "why": ""})


class TestNormalizeIssue(unittest.TestCase):
    def test_plain_string_passthrough(self):
        self.assertEqual(driver.normalize_issue("missing criteria"), "missing criteria")

    def test_finding_shaped_dict_renders_readable(self):
        i = {"id": "I1", "title": "Unclear format", "description": "sha or hex?"}
        self.assertEqual(driver.normalize_issue(i), "Unclear format — sha or hex?")

    def test_unknown_dict_falls_back_to_json(self):
        self.assertEqual(driver.normalize_issue({"x": 1}), '{"x": 1}')


class TestAsList(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(driver._as_list(None), [])

    def test_list_passthrough(self):
        self.assertEqual(driver._as_list([1, 2]), [1, 2])

    def test_scalar_is_wrapped(self):
        self.assertEqual(driver._as_list("x"), ["x"])
        self.assertEqual(driver._as_list({"a": 1}), [{"a": 1}])


if __name__ == "__main__":
    unittest.main()
