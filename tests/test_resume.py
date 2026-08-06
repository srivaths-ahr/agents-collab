"""The resume checkpoint's decision logic, exercised without touching disk or git.

`resume_decision` is what stands between a crashed run and paying twice for the same
clarity gate, plan, and executor pass — and, because a stale checkpoint's baseline
would anchor the verifier's diff to the wrong tree, between a resumed run and a
verdict scored on a truncated diff. So the rejection cases matter more than the happy
path here: every way a checkpoint can go stale is tested, and each must come back
"fresh" with a reason rather than being silently honoured."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import driver  # noqa: E402

FP = "f" * 64  # the input fingerprint a run was checkpointed against
HEAD = "a" * 40


def state(**over):
    """A checkpoint that WOULD resume, so each test can spoil exactly one field."""
    base = {
        "version": driver.STATE_VERSION,
        "baseline": "b" * 40,
        "head": HEAD,
        "inputs": FP,
        "iteration": 3,
        "phase": "verify",
        "total_cost": 1.2345,
        "prev_verdict": {"status": "fail", "reasons": ["C2 not met"]},
        "stall": {"sig": "s" * 64, "count": 1},
    }
    base.update(over)
    return base


def decide(st, *, fingerprint=FP, head=HEAD, fresh=False):
    return driver.resume_decision(st, fingerprint=fingerprint, head=head, fresh=fresh)


class TestInputFingerprint(unittest.TestCase):
    def test_stable_and_sensitive(self):
        a = driver.input_fingerprint("task", "ctx", "clar")
        self.assertEqual(a, driver.input_fingerprint("task", "ctx", "clar"))
        for changed in (
            ("task!", "ctx", "clar"),
            ("task", "ctx!", "clar"),
            ("task", "ctx", "clar!"),
        ):
            self.assertNotEqual(a, driver.input_fingerprint(*changed))

    def test_fields_cannot_smear_into_each_other(self):
        # Concatenation without a separator would make these two collide, and a task
        # edit that merely shifts a boundary would then reuse a stale plan.
        self.assertNotEqual(
            driver.input_fingerprint("ab", "c", ""),
            driver.input_fingerprint("a", "bc", ""),
        )

    def test_missing_files_hash_as_empty(self):
        # current_fingerprint() maps an absent file to "", so creating a previously
        # missing clarifications.md must invalidate the checkpoint.
        self.assertNotEqual(
            driver.input_fingerprint("t", "c", ""),
            driver.input_fingerprint("t", "c", "an answer"),
        )


class TestResumes(unittest.TestCase):
    def test_valid_checkpoint_resumes_at_its_phase(self):
        action, why, st = decide(state())
        self.assertEqual(action, "resume")
        self.assertEqual(st["phase"], "verify")
        self.assertEqual(st["iteration"], 3)
        self.assertIn("verify", why)

    def test_every_phase_is_resumable(self):
        for phase in ("plan", "execute", "verify"):
            action, _, st = decide(state(phase=phase))
            self.assertEqual(action, "resume", phase)
            self.assertEqual(st["phase"], phase)

    def test_carried_state_survives_for_the_caller(self):
        # Cost, verdict and stall must come back intact: they bound --max-cost-usd
        # across invocations and keep stall detection from resetting on every crash.
        _, _, st = decide(state())
        self.assertEqual(st["total_cost"], 1.2345)
        self.assertEqual(st["prev_verdict"]["status"], "fail")
        self.assertEqual(st["stall"]["count"], 1)


class TestStartsFresh(unittest.TestCase):
    def assertFresh(self, result):
        action, why, st = result
        self.assertEqual(action, "fresh")
        self.assertIsNone(st)
        self.assertTrue(why, "a discarded checkpoint must always carry a reason")
        return why

    def test_no_checkpoint(self):
        self.assertFresh(decide(None))
        self.assertFresh(decide({}))

    def test_fresh_flag_wins_over_a_valid_checkpoint(self):
        self.assertIn("--fresh", self.assertFresh(decide(state(), fresh=True)))

    def test_edited_inputs(self):
        # The whole point: editing task.md must not reuse a plan written for the old
        # one. Names the files, so the user can see which change invalidated it.
        why = self.assertFresh(decide(state(), fingerprint="9" * 64))
        self.assertIn(driver.TASK_FILE, why)

    def test_head_moved(self):
        # A commit/reset/branch switch changes what the baseline tree means, so the
        # diff it anchors — and any verdict scored on that diff — would be wrong.
        self.assertIn("HEAD", self.assertFresh(decide(state(), head="c" * 40)))

    def test_older_state_version(self):
        self.assertFresh(decide(state(version=driver.STATE_VERSION - 1)))
        self.assertFresh(decide(state(version=None)))

    def test_unusable_phase(self):
        for bad in ("clarify", "", None, "PLAN", 7):
            self.assertFresh(decide(state(phase=bad)))

    def test_missing_baseline(self):
        # Without it the run has no diff anchor at all; re-capturing one now would
        # swallow the executor's existing edits, which is the bug resume exists to
        # avoid. Start over instead.
        for bad in ("", None):
            self.assertFresh(decide(state(baseline=bad)))

    def test_unusable_iteration(self):
        for bad in (0, -1, None, "3", 2.0):
            self.assertFresh(decide(state(iteration=bad)))


class TestResumeEntry(unittest.TestCase):
    """Where a resume actually re-enters, once the artifacts and the prior attempts
    are taken into account. The recorded phase is only a starting proposal."""

    def entry(self, phase, *, attempts=None, have_plan=True, have_diff=True, mx=2):
        return driver.resume_entry(
            phase,
            attempts=attempts or {},
            have_plan=have_plan,
            have_diff=have_diff,
            max_attempts=mx,
        )

    def test_healthy_checkpoint_enters_where_it_died(self):
        for phase in driver.PHASE_ORDER:
            got, notes = self.entry(phase)
            self.assertEqual(got, phase)
            self.assertEqual(notes, [], "a clean resume should explain nothing")

    def test_verify_with_no_edits_backs_up_to_execute(self):
        # Re-verifying an unchanged tree buys a verdict of "nothing changed" and then
        # a whole iteration to fix it.
        got, notes = self.entry("verify", have_diff=False)
        self.assertEqual(got, "execute")
        self.assertIn("EXECUTE", notes[0])

    def test_execute_without_a_plan_backs_up_to_plan(self):
        # "skipping PLAN (plan.md still valid)" has to be checked, not assumed.
        got, notes = self.entry("execute", have_plan=False)
        self.assertEqual(got, "plan")
        self.assertIn(driver.PLAN_FILE, notes[0])

    def test_missing_artifacts_cascade_all_the_way_back(self):
        got, notes = self.entry("verify", have_plan=False, have_diff=False)
        self.assertEqual(got, "plan")
        self.assertEqual(len(notes), 2, "each step back is explained separately")

    def test_repeated_execute_failures_force_a_replan(self):
        # The across-runs case: re-running would otherwise retry the same executor
        # against the same plan forever, never trying anything different.
        got, notes = self.entry("execute", attempts={"execute": 2})
        self.assertEqual(got, "plan")
        self.assertIn("2x", notes[0])

    def test_under_the_threshold_still_retries_in_place(self):
        self.assertEqual(self.entry("execute", attempts={"execute": 1})[0], "execute")

    def test_repeated_verify_failures_back_up_to_execute(self):
        # A verifier that can't be parsed twice needs a different diff, not a third
        # identical call.
        self.assertEqual(self.entry("verify", attempts={"verify": 2})[0], "execute")

    def test_plan_has_nowhere_to_back_up_to(self):
        # It's first in the pipeline — stay put rather than inventing a phase.
        got, notes = self.entry("plan", attempts={"plan": 99})
        self.assertEqual(got, "plan")
        self.assertEqual(notes, [])

    def test_escalation_cascades_across_phases(self):
        got, _ = self.entry("verify", attempts={"verify": 2, "execute": 2})
        self.assertEqual(got, "plan")

    def test_always_terminates_on_a_fully_poisoned_checkpoint(self):
        # Every guard tripping at once must still land on a real phase, not spin.
        got, _ = self.entry(
            "verify",
            attempts={"verify": 9, "execute": 9, "plan": 9},
            have_plan=False,
            have_diff=False,
        )
        self.assertEqual(got, "plan")

    def test_entry_phase_is_always_skippable(self):
        # main() indexes SKIPPED_BY_PHASE with whatever comes back, so anything
        # resume_entry can return must be a key there.
        for phase in driver.PHASE_ORDER:
            for plan_ok in (True, False):
                for diff_ok in (True, False):
                    got, _ = self.entry(
                        phase,
                        attempts={p: 5 for p in driver.PHASE_ORDER},
                        have_plan=plan_ok,
                        have_diff=diff_ok,
                    )
                    self.assertIn(got, driver.SKIPPED_BY_PHASE)

    def test_default_threshold_is_the_module_constant(self):
        got, _ = driver.resume_entry(
            "execute",
            attempts={"execute": driver.MAX_PHASE_ATTEMPTS},
            have_plan=True,
            have_diff=True,
        )
        self.assertEqual(got, "plan")


class TestSkipTable(unittest.TestCase):
    def test_each_phase_skips_everything_before_it(self):
        self.assertEqual(driver.SKIPPED_BY_PHASE["plan"], ("clarify",))
        self.assertEqual(driver.SKIPPED_BY_PHASE["execute"], ("clarify", "plan"))
        self.assertEqual(
            driver.SKIPPED_BY_PHASE["verify"], ("clarify", "plan", "execute")
        )

    def test_resumable_phases_match_the_skip_table(self):
        # resume_decision validates phase against this table, so a phase added to one
        # and not the other would either be unreachable or crash the resume banner.
        for phase in driver.SKIPPED_BY_PHASE:
            self.assertEqual(decide(state(phase=phase))[0], "resume")

    def test_every_skipped_step_has_a_reason_to_print(self):
        for steps in driver.SKIPPED_BY_PHASE.values():
            for step in steps:
                self.assertIn(step, driver.SKIP_REASON)


if __name__ == "__main__":
    unittest.main()
