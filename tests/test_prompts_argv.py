"""The Claude argv builder and the per-step instruction builders are pure (modulo
reading prompt/clarification files). They are the single source of truth shared by
the real run and the --dry-run preview, so a regression here silently changes what
every run sends to Claude. Lock the load-bearing pieces down."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver  # noqa: E402


class TestBuildClaudeArgv(unittest.TestCase):
    def test_minimal_argv_shape(self):
        argv = driver.build_claude_argv(
            "PROMPT", model="opus", system_prompt_file=None, allowed_tools=None
        )
        self.assertEqual(
            argv,
            ["claude", "-p", "PROMPT", "--model", "opus", "--output-format", "json"],
        )

    def test_bare_flag(self):
        argv = driver.build_claude_argv(
            "P", model="haiku", system_prompt_file=None, allowed_tools=None, bare=True
        )
        self.assertIn("--bare", argv)

    def test_allowed_tools_joined_with_commas(self):
        argv = driver.build_claude_argv(
            "P",
            model="m",
            system_prompt_file=None,
            allowed_tools=["Read", "Grep", "Glob"],
        )
        i = argv.index("--allowedTools")
        self.assertEqual(argv[i + 1], "Read,Grep,Glob")

    def test_skip_permissions_replaces_allowed_tools(self):
        # skip_permissions and allowedTools are mutually exclusive (elif): when
        # skipping, no --allowedTools should appear.
        argv = driver.build_claude_argv(
            "P",
            model="m",
            system_prompt_file=None,
            allowed_tools=["Read"],
            skip_permissions=True,
        )
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--allowedTools", argv)

    def test_system_prompt_file_contents_are_appended(self):
        # Write a temp prompt file and confirm its CONTENTS (not its path) follow
        # --append-system-prompt.
        path = os.path.join(os.path.dirname(__file__), "_tmp_sys_prompt.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("SYSTEM-PROMPT-BODY")
        try:
            argv = driver.build_claude_argv(
                "P", model="m", system_prompt_file=path, allowed_tools=["Read"]
            )
            i = argv.index("--append-system-prompt")
            self.assertEqual(argv[i + 1], "SYSTEM-PROMPT-BODY")
        finally:
            os.remove(path)


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


if __name__ == "__main__":
    unittest.main()
