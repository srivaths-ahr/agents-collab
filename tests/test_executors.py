"""Executor adapters are pure argv construction — assert each backend builds the
exact command line. This doubles as the early-warning system for the executor-flag
drift the README keeps warning about: if a backend's flags change, this test fails
loudly instead of a long unattended run failing silently."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import executors  # noqa: E402

MODEL = "MODEL-SENTINEL"
PROMPT = "PROMPT-SENTINEL"


class TestExecutorArgv(unittest.TestCase):
    def build(self, backend):
        return executors.EXECUTORS[backend](MODEL, PROMPT)

    def test_cursor(self):
        self.assertEqual(
            self.build("cursor"),
            [
                "cursor-agent",
                "-p",
                "--force",
                "--model",
                MODEL,
                "--output-format",
                "text",
                PROMPT,
            ],
        )

    def test_claude(self):
        self.assertEqual(
            self.build("claude"),
            [
                "claude",
                "-p",
                PROMPT,
                "--model",
                MODEL,
                "--permission-mode",
                "acceptEdits",
                "--output-format",
                "json",
            ],
        )

    def test_codex(self):
        self.assertEqual(
            self.build("codex"),
            [
                "codex",
                "exec",
                "--model",
                MODEL,
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "never",
                PROMPT,
            ],
        )

    def test_gemini(self):
        self.assertEqual(
            self.build("gemini"),
            ["gemini", "-p", PROMPT, "-m", MODEL, "--yolo", "--output-format", "json"],
        )

    def test_antigravity(self):
        self.assertEqual(
            self.build("antigravity"),
            ["agy", "--headless", "--approve", "all", PROMPT],
        )


class TestExecutorRegistry(unittest.TestCase):
    def test_every_adapter_is_callable_and_returns_str_argv(self):
        for name, build in executors.EXECUTORS.items():
            argv = build(MODEL, PROMPT)
            self.assertIsInstance(argv, list, name)
            self.assertTrue(argv, f"{name}: empty argv")
            for tok in argv:
                self.assertIsInstance(tok, str, f"{name}: non-str token {tok!r}")

    def test_model_and_prompt_are_passed_through(self):
        # A drift that silently drops the model or prompt is a real bug — catch it.
        for name, build in executors.EXECUTORS.items():
            argv = build(MODEL, PROMPT)
            self.assertIn(PROMPT, argv, f"{name}: prompt missing from argv")
            if name != "antigravity":  # early agy builds ignore an explicit model
                self.assertIn(MODEL, argv, f"{name}: model missing from argv")

    def test_json_envelope_backends_are_registered_executors(self):
        self.assertTrue(
            executors.JSON_ENVELOPE_BACKENDS.issubset(set(executors.EXECUTORS))
        )

    def test_claude_is_the_json_envelope_backend(self):
        # parse_executor_output keys cost extraction off this set.
        self.assertIn("claude", executors.JSON_ENVELOPE_BACKENDS)
        self.assertNotIn("cursor", executors.JSON_ENVELOPE_BACKENDS)


if __name__ == "__main__":
    unittest.main()
