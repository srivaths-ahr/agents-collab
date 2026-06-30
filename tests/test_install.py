"""install.py is the cross-platform installer (stdlib only). Its destructive logic
lives in one pure function — removal_reason — that decides whether a guarded user
file may be deleted from the three I/O probes (force / untouched-seed / git-clean).
Lock that decision down here; the file-copying I/O is exercised by an install ->
uninstall smoke run in a throwaway repo, not unit tests."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import install  # noqa: E402


class TestRemovalReason(unittest.TestCase):
    def test_keep_when_nothing_qualifies(self):
        self.assertEqual(
            install.removal_reason(force=False, matches_seed=False, git_clean=False),
            "",
        )

    def test_force(self):
        self.assertEqual(
            install.removal_reason(force=True, matches_seed=False, git_clean=False),
            "--force",
        )

    def test_untouched_seed(self):
        self.assertEqual(
            install.removal_reason(force=False, matches_seed=True, git_clean=False),
            "untouched seed",
        )

    def test_git_clean(self):
        self.assertEqual(
            install.removal_reason(force=False, matches_seed=False, git_clean=True),
            "git-clean (recoverable)",
        )

    def test_force_wins_over_seed_and_git(self):
        self.assertEqual(
            install.removal_reason(force=True, matches_seed=True, git_clean=True),
            "--force",
        )

    def test_seed_wins_over_git(self):
        self.assertEqual(
            install.removal_reason(force=False, matches_seed=True, git_clean=True),
            "untouched seed",
        )

    def test_any_qualifier_removes(self):
        # Removed iff the reason is non-empty.
        for f, s, g in [(True, False, False), (False, True, False), (False, False, True)]:
            self.assertTrue(install.removal_reason(force=f, matches_seed=s, git_clean=g))


class TestFileLists(unittest.TestCase):
    def test_tool_files(self):
        self.assertEqual(install.TOOL_FILES, ["driver.py", "executors.py"])

    def test_artifacts_are_generated_outputs(self):
        for a in (".loop", "plan.md", "verdict.json", "clarifications_needed.json"):
            self.assertIn(a, install.ARTIFACTS)

    def test_user_files_seed_mapping(self):
        seeds = dict(install.USER_FILES)
        # seeded files compare against their seed; pure-user files have no seed.
        self.assertEqual(seeds["AGENTS.md"], "AGENTS.md")
        self.assertIsNone(seeds["task.md"])
        self.assertIsNone(seeds["clarifications.md"])

    def test_shipped_prompts_are_the_four_contracts(self):
        # Derived from SRC/prompts, not hardcoded — install and uninstall share it.
        self.assertEqual(
            install.shipped_prompts(),
            ["execute.md", "plan.md", "triage.md", "verify.md"],
        )


if __name__ == "__main__":
    unittest.main()
