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
        # A user file that is neither an untouched seed nor forced is KEPT — even if
        # it happens to be git-tracked & clean. git-state is no longer a signal,
        # because it can't tell the tool's file from the user's pre-existing one.
        self.assertEqual(
            install.removal_reason(force=False, matches_seed=False), ""
        )

    def test_force(self):
        self.assertEqual(
            install.removal_reason(force=True, matches_seed=False), "--force"
        )

    def test_untouched_seed(self):
        self.assertEqual(
            install.removal_reason(force=False, matches_seed=True), "untouched seed"
        )

    def test_force_wins_over_seed(self):
        self.assertEqual(
            install.removal_reason(force=True, matches_seed=True), "--force"
        )

    def test_removed_iff_reason_nonempty(self):
        self.assertTrue(install.removal_reason(force=True, matches_seed=False))
        self.assertTrue(install.removal_reason(force=False, matches_seed=True))
        self.assertFalse(install.removal_reason(force=False, matches_seed=False))


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

    def test_shipped_prompts_are_the_contracts(self):
        # Derived from SRC/prompts, not hardcoded — install and uninstall share it.
        self.assertEqual(
            install.shipped_prompts(),
            ["author.md", "execute.md", "plan.md", "triage.md", "verify.md"],
        )

    def test_shipped_skills_need_a_skill_md(self):
        # Derived from SRC/skills/*/SKILL.md — a dir without one isn't a skill.
        self.assertIn("jira-to-task", install.shipped_skills())

    def test_skill_ships_as_one_whole_folder(self):
        # The user places the folder themselves, so everything travels together:
        # body, INSTALL.md (how to place it), reference/, and the Cursor pointer.
        rels = install.skill_files("jira-to-task")
        for expected in ("SKILL.md", "INSTALL.md", "cursor-rule.mdc"):
            self.assertIn(expected, rels)
        self.assertTrue(
            any(r.startswith("reference/") for r in rels),
            "expected the skill's reference/ files to ship too",
        )
        # forward-slash relpaths, so one list works on Windows too
        self.assertNotIn("\\", "".join(rels))
        self.assertEqual(rels, sorted(rels))

    def test_skill_files_of_unknown_skill_is_empty(self):
        self.assertEqual(install.skill_files("no-such-skill"), [])

    def test_skill_dirs_are_deepest_first(self):
        # Uninstall rmdirs in this order, so a nested dir is empty when it's tried.
        dirs = install.skill_dirs("jira-to-task")
        self.assertIn("reference", dirs)
        depths = [d.count("/") for d in dirs]
        self.assertEqual(depths, sorted(depths, reverse=True))

    def test_skill_dirs_of_flat_or_unknown_skill_is_empty(self):
        self.assertEqual(install.skill_dirs("no-such-skill"), [])


class TestRenderGitExclude(unittest.TestCase):
    PATS = ["/driver.py", "/prompts/plan.md", "/.loop/"]

    def _block_count(self, text):
        return text.count(install.EXCLUDE_BEGIN)

    def test_add_to_empty(self):
        out = install.render_git_exclude("", self.PATS, add=True)
        self.assertIn(install.EXCLUDE_BEGIN, out)
        self.assertIn(install.EXCLUDE_END, out)
        for p in self.PATS:
            self.assertIn(p, out)
        self.assertTrue(out.endswith("\n"))

    def test_add_is_idempotent(self):
        once = install.render_git_exclude("", self.PATS, add=True)
        twice = install.render_git_exclude(once, self.PATS, add=True)
        self.assertEqual(self._block_count(twice), 1)
        self.assertEqual(once, twice)

    def test_remove_strips_block(self):
        with_block = install.render_git_exclude("", self.PATS, add=True)
        removed = install.render_git_exclude(with_block, self.PATS, add=False)
        self.assertNotIn(install.EXCLUDE_BEGIN, removed)
        self.assertNotIn("/driver.py", removed)

    def test_preserves_user_lines_and_round_trips(self):
        user = "*.log\n/build/\n"
        added = install.render_git_exclude(user, self.PATS, add=True)
        self.assertIn("*.log", added)
        self.assertIn("/build/", added)
        # add then remove returns to the user's original content
        removed = install.render_git_exclude(added, self.PATS, add=False)
        self.assertEqual(removed, user)

    def test_remove_on_empty_is_empty(self):
        self.assertEqual(install.render_git_exclude("", self.PATS, add=False), "")


class TestExcludePatterns(unittest.TestCase):
    def test_covers_tool_and_artifacts_not_user_content(self):
        pats = install._exclude_patterns()
        self.assertIn("/driver.py", pats)
        self.assertIn("/executors.py", pats)
        self.assertIn("/plan.md", pats)
        self.assertIn("/.loop/", pats)
        self.assertIn("/prompts/plan.md", pats)  # per-file, not the whole dir
        self.assertNotIn("/prompts/", pats)
        # skills: per-skill, not the whole /skills/ dir, so a user's own top-level
        # skills/ is never shadowed. The installer writes nothing under .claude/ or
        # .cursor/, so it must never exclude anything there either.
        self.assertIn("/skills/jira-to-task/", pats)
        self.assertNotIn("/skills/", pats)
        for theirs in ("/.claude/", "/.cursor/", "/.claude/skills/jira-to-task/"):
            self.assertNotIn(theirs, pats)
        # user content the installer must NOT hide from git
        for user in ("/task.md", "/context.md", "/AGENTS.md", "/clarifications.md"):
            self.assertNotIn(user, pats)


if __name__ == "__main__":
    unittest.main()
