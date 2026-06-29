"""The Claude argv builder and the per-step instruction builders are pure (modulo
reading prompt/clarification files). They are the single source of truth shared by
the real run and the --dry-run preview, so a regression here silently changes what
every run sends to Claude. Lock the load-bearing pieces down."""

import argparse
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver  # noqa: E402


class TestBuildClaudeArgv(unittest.TestCase):
    # Pin windows=False so the POSIX argv shape is asserted regardless of the host
    # the tests run on; the Windows/stdin branch is covered separately below.
    def test_minimal_argv_shape(self):
        argv, stdin_text = driver.build_claude_argv(
            "PROMPT",
            model="opus",
            system_prompt_file=None,
            allowed_tools=None,
            windows=False,
        )
        self.assertEqual(
            argv,
            ["claude", "-p", "PROMPT", "--model", "opus", "--output-format", "json"],
        )
        self.assertIsNone(stdin_text)  # POSIX: prompt is an argv token, not stdin

    def test_bare_flag(self):
        argv, _ = driver.build_claude_argv(
            "P",
            model="haiku",
            system_prompt_file=None,
            allowed_tools=None,
            bare=True,
            windows=False,
        )
        self.assertIn("--bare", argv)

    def test_allowed_tools_joined_with_commas(self):
        argv, _ = driver.build_claude_argv(
            "P",
            model="m",
            system_prompt_file=None,
            allowed_tools=["Read", "Grep", "Glob"],
            windows=False,
        )
        i = argv.index("--allowedTools")
        self.assertEqual(argv[i + 1], "Read,Grep,Glob")

    def test_skip_permissions_replaces_allowed_tools(self):
        # skip_permissions and allowedTools are mutually exclusive (elif): when
        # skipping, no --allowedTools should appear.
        argv, _ = driver.build_claude_argv(
            "P",
            model="m",
            system_prompt_file=None,
            allowed_tools=["Read"],
            skip_permissions=True,
            windows=False,
        )
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--allowedTools", argv)

    def test_system_prompt_file_contents_are_appended(self):
        # Write a temp prompt file and confirm its CONTENTS (not its path) follow
        # --append-system-prompt on the POSIX path.
        path = os.path.join(os.path.dirname(__file__), "_tmp_sys_prompt.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("SYSTEM-PROMPT-BODY")
        try:
            argv, stdin_text = driver.build_claude_argv(
                "P",
                model="m",
                system_prompt_file=path,
                allowed_tools=["Read"],
                windows=False,
            )
            i = argv.index("--append-system-prompt")
            self.assertEqual(argv[i + 1], "SYSTEM-PROMPT-BODY")
            self.assertIsNone(stdin_text)
        finally:
            os.remove(path)

    def test_windows_routes_prompt_and_system_via_stdin(self):
        # On Windows nothing multi-line may ride in argv (cmd.exe truncates it at the
        # first newline). The prompt + system-prompt contents go via stdin instead,
        # and --append-system-prompt is dropped.
        path = os.path.join(os.path.dirname(__file__), "_tmp_sys_prompt_win.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("SYSTEM-BODY\nwith-a-newline")
        try:
            argv, stdin_text = driver.build_claude_argv(
                "USER-PROMPT\nline-two",
                model="opus",
                system_prompt_file=path,
                allowed_tools=["Read"],
                windows=True,
            )
            # No prompt/system tokens in argv; nothing in argv carries a newline.
            self.assertNotIn("--append-system-prompt", argv)
            self.assertNotIn("USER-PROMPT\nline-two", argv)
            self.assertTrue(all("\n" not in tok for tok in argv))
            self.assertEqual(
                argv,
                [
                    "claude",
                    "-p",
                    "--model",
                    "opus",
                    "--output-format",
                    "json",
                    "--allowedTools",
                    "Read",
                ],
            )
            # stdin carries system body, separator, then the user prompt, in order.
            self.assertIn("SYSTEM-BODY\nwith-a-newline", stdin_text)
            self.assertIn(driver.WIN_STDIN_PROMPT_SEP, stdin_text)
            self.assertIn("USER-PROMPT\nline-two", stdin_text)
            self.assertLess(
                stdin_text.index("SYSTEM-BODY"), stdin_text.index("USER-PROMPT")
            )
        finally:
            os.remove(path)

    def test_windows_without_system_prompt_sends_only_prompt_via_stdin(self):
        argv, stdin_text = driver.build_claude_argv(
            "JUST-THE-PROMPT",
            model="m",
            system_prompt_file=None,
            allowed_tools=None,
            windows=True,
        )
        self.assertEqual(stdin_text, "JUST-THE-PROMPT")
        self.assertNotIn(driver.WIN_STDIN_PROMPT_SEP, stdin_text)
        self.assertNotIn("JUST-THE-PROMPT", argv)


class TestInstructionBuilders(unittest.TestCase):
    def setUp(self):
        # plan_instruction / triage_instruction branch on whether clarifications.md
        # exists in cwd; pin it absent for deterministic assertions.
        self._existed = os.path.exists(driver.CLARIFY_FILE)
        self.assertFalse(
            self._existed, "test assumes no clarifications.md in the working dir"
        )

    def test_plan_instruction_without_prev_verdict(self):
        text = driver.plan_instruction(None)
        self.assertIn(driver.TASK_FILE, text)
        self.assertIn(driver.CONTEXT_FILE, text)
        self.assertNotIn("PREVIOUS VERDICT", text)

    def test_plan_instruction_includes_prev_verdict_fields(self):
        verdict = {
            "criteria": [{"id": "C1"}],
            "reasons": ["C1 not met: foo"],
            "next_actions": ["do bar"],
            "status": "fail",
        }
        text = driver.plan_instruction(verdict)
        self.assertIn("PREVIOUS VERDICT", text)
        self.assertIn("C1 not met: foo", text)
        self.assertIn("do bar", text)

    def test_verify_instruction_names_the_inputs(self):
        text = driver.verify_instruction()
        self.assertIn(driver.TASK_FILE, text)
        self.assertIn(f"{driver.WORK_DIR}/diff.patch", text)
        self.assertIn(f"{driver.WORK_DIR}/test_output.txt", text)

    def test_triage_instruction_names_task_and_context(self):
        text = driver.triage_instruction()
        self.assertIn(driver.TASK_FILE, text)
        self.assertIn(driver.CONTEXT_FILE, text)


class TestPathOverridesFlowToInstructions(unittest.TestCase):
    """--task/--context/--work-dir reassign the module path globals; the builders
    read them at call time, so an overridden path must surface in the prompts. This
    proves the multi-unit override is transparent without spawning anything."""

    def setUp(self):
        # Builders branch on clarifications.md existing in cwd; pin it absent.
        self.assertFalse(
            os.path.exists(driver.CLARIFY_FILE),
            "test assumes no clarifications.md in the working dir",
        )
        self._saved = (driver.TASK_FILE, driver.CONTEXT_FILE, driver.WORK_DIR)
        driver.TASK_FILE = "units/01-to_roman/task.md"
        driver.CONTEXT_FILE = "shared/context.md"
        driver.WORK_DIR = "units/01-to_roman/.loop"

    def tearDown(self):
        driver.TASK_FILE, driver.CONTEXT_FILE, driver.WORK_DIR = self._saved

    def test_overridden_paths_appear_in_plan_and_verify_and_triage(self):
        plan = driver.plan_instruction(None)
        self.assertIn("units/01-to_roman/task.md", plan)
        self.assertIn("shared/context.md", plan)

        verify = driver.verify_instruction()
        self.assertIn("units/01-to_roman/task.md", verify)
        self.assertIn("units/01-to_roman/.loop/diff.patch", verify)

        triage = driver.triage_instruction()
        self.assertIn("units/01-to_roman/task.md", triage)


class TestRunSettingResolvers(unittest.TestCase):
    """The interactive run-setting prompt parses answers through these pure helpers,
    so a regression here changes what an Enter/index/number answer resolves to."""

    def test_resolve_choice_empty_is_default(self):
        self.assertEqual(driver.resolve_choice("", ["a", "b"], "b"), "b")
        self.assertEqual(driver.resolve_choice("   ", ["a", "b"], "a"), "a")

    def test_resolve_choice_by_index_is_one_based(self):
        opts = ["cursor", "claude", "codex"]
        self.assertEqual(driver.resolve_choice("1", opts, "cursor"), "cursor")
        self.assertEqual(driver.resolve_choice("3", opts, "cursor"), "codex")

    def test_resolve_choice_by_exact_name(self):
        opts = ["cursor", "claude", "codex"]
        self.assertEqual(driver.resolve_choice("codex", opts, "cursor"), "codex")

    def test_resolve_choice_invalid_is_none(self):
        opts = ["cursor", "claude"]
        self.assertIsNone(driver.resolve_choice("0", opts, "cursor"))  # 1-based
        self.assertIsNone(driver.resolve_choice("9", opts, "cursor"))  # out of range
        self.assertIsNone(driver.resolve_choice("nope", opts, "cursor"))

    def test_resolve_number_empty_is_default(self):
        self.assertEqual(driver.resolve_number("", 8, cast=int, minimum=1), 8)

    def test_resolve_number_parses_and_floors_at_minimum(self):
        self.assertEqual(driver.resolve_number("5", 8, cast=int, minimum=1), 5)
        self.assertIsNone(driver.resolve_number("0", 8, cast=int, minimum=1))
        self.assertEqual(driver.resolve_number("2.5", 0.0, cast=float, minimum=0.0), 2.5)
        self.assertIsNone(driver.resolve_number("-1", 0.0, cast=float, minimum=0.0))
        self.assertIsNone(driver.resolve_number("abc", 8, cast=int, minimum=1))


class TestPromptRunSettings(unittest.TestCase):
    """The interactive layer: only the knobs left None are asked; passed flags pass
    through, and answers route through the pure resolvers above."""

    def _run(self, namespace, answers):
        it = iter(answers)
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch("builtins.input", lambda *_: next(it)):
                return driver.prompt_run_settings(
                    namespace,
                    executor_default="cursor",
                    impl_default="composer-2.5",
                    iter_default=8,
                    cost_default=0.0,
                )

    def test_prompts_only_for_unset_knobs(self):
        # executor passed; the rest None -> 3 answers consumed (model, iters, cost).
        ns = argparse.Namespace(
            executor="codex", impl_model=None, max_iterations=None, max_cost_usd=None
        )
        ex, im, mi, mc = self._run(ns, ["", "5", "1.50"])
        self.assertEqual(ex, "codex")  # passed through, never asked
        # blank model -> the per-executor suggestion for codex
        self.assertEqual(im, driver.executors.SUGGESTED_IMPL_MODELS["codex"])
        self.assertEqual(mi, 5)
        self.assertEqual(mc, 1.50)

    def test_all_unset_uses_index_and_defaults(self):
        ns = argparse.Namespace(
            executor=None, impl_model=None, max_iterations=None, max_cost_usd=None
        )
        names = list(driver.executors.EXECUTORS)
        # choose executor #2 by index, accept suggested model, accept default iters+cost
        ex, im, mi, mc = self._run(ns, ["2", "", "", ""])
        self.assertEqual(ex, names[1])
        self.assertEqual(im, driver.executors.SUGGESTED_IMPL_MODELS.get(names[1]))
        self.assertEqual(mi, 8)
        self.assertEqual(mc, 0.0)

    def test_nothing_asked_when_all_passed(self):
        ns = argparse.Namespace(
            executor="claude", impl_model="sonnet", max_iterations=3, max_cost_usd=2.0
        )
        # no answers available; if it tried to input() it would StopIteration
        ex, im, mi, mc = self._run(ns, [])
        self.assertEqual((ex, im, mi, mc), ("claude", "sonnet", 3, 2.0))


if __name__ == "__main__":
    unittest.main()
