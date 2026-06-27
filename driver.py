#!/usr/bin/env python3
"""
Agentic build loop driver.

Sequences stateless one-shot agents around a shared git workspace:

    CLARIFY  (Claude)   gate: is task.md clear enough to plan? if not, ask/halt
    PLAN     (Claude)   reads task.md + context.md (+ codegraph) + last verdict
                        -> writes plan.md
    EXECUTE  (executor) reads plan.md (+ AGENTS.md) -> edits files
                        (pluggable: cursor | claude
                        | codex | gemini | antigravity)
    VERIFY   (Claude)   reads the git diff + test output + task.md criteria
                        -> writes verdict.json

The driver is the ONLY stateful actor. It owns: iteration budget, the stop
conditions, the file handoff between agents, and per-step cost accounting.
It does NOT decide "done" — the verifier does, in verdict.json. The driver
just reads verdict.status and acts.

Each role's MODEL is a variable you fill before running
(or override on the CLI).
Each role's PROMPT lives in prompts/*.md, next to this driver, so prompt-tuning
never touches loop logic and the verify prompt stays in sync with the schema
this driver parses.

Nothing here is destructive:
it stages changes (git add -A) to compute diffs but
never commits, pushes, resets, or deletes. Run it inside a dedicated branch or
git worktree so you can inspect/commit/discard the result yourself.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import executors  # executor adapter registry (the pluggable EXECUTE step)

# ============================================================================
# CONFIG — fill these before a run (all overridable via CLI flags below)
# ============================================================================

# ---- MODELS (filled before a run; also overridable on the CLI) ----
PLAN_CLAUDE_MODEL_NAME = "opus"  # planning: strong, long-thinking
VERIFICATION_CLAUDE_MODEL_NAME = "haiku"  # verification: cheap, bounded

# ---- EXECUTOR (pluggable — plan & verify stay on Claude, only this swaps) ---
# One of executors.EXECUTORS:
#   "cursor" | "claude" | "codex" | "gemini" | "antigravity"
#   EXECUTOR_BACKEND="claude" -> Claude plans, Claude executes, Claude verifies
#   EXECUTOR_BACKEND="codex"  -> Claude plans, Codex  executes, Claude verifies
#   EXECUTOR_BACKEND="gemini" -> Claude plans, Gemini executes, Claude verifies
EXECUTOR_BACKEND = "cursor"
IMPLEMENTATION_MODEL_NAME = "composer-2.5"  # executor model slug FOR THE CHOSEN BACKEND

# ---- EXECUTOR RESILIENCE (guardrails for a failing executor) ----
EXECUTOR_MAX_RETRIES = (
    1  # extra attempts on a TRANSIENT executor failure (hang/timeout/nonzero)
)
EXECUTOR_RETRY_BACKOFF = 8  # seconds to wait between executor retries

# ---- CLARIFICATION GATE (front-of-loop: is task.md clear enough to plan?) ---
CLARIFY_MODEL_NAME = "haiku"  # cheap; judges task clarity before any planning happens
CLARIFY_MAX_ROUNDS = 3  # interactive Q&A rounds before giving up
INTERACTIVE_CLARIFY = (
    True  # TTY: ask questions live; non-TTY: write questions to a file and halt
)

# ---- LOOP BUDGET / STOP GUARDS ----
MAX_ITERATIONS = 8  # hard cap; loop stops even if not "pass"
MAX_IDENTICAL_FAILURES = (
    2  # stop if the loop stalls (same failure / no new diff) this many times
)

# ---- TEST GATE (the objective half of verification) ----
# Deterministic command the driver runs after each execute. Its pass/fail is
# ground truth handed to the verifier.
# Empty string = skip, judge on diff alone.
TEST_COMMAND = ""  # e.g. "pytest -q"  |  "npm test --silent"  |  "swift test"

# ---- PATHS (relative to REPO_ROOT) ----
REPO_ROOT = "."
TASK_FILE = "task.md"
CONTEXT_FILE = "context.md"
PLAN_FILE = "plan.md"
VERDICT_FILE = "verdict.json"
CLARIFY_FILE = "clarifications.md"  # human answers; gate & planner read it if present
CLARIFY_NEEDED_FILE = (
    "clarifications_needed.json"  # questions written here in unattended (non-TTY) mode
)
PROMPTS_DIR = "prompts"
WORK_DIR = ".loop"  # scratch: diff.patch, test_output.txt, logs, last raw outputs

# ---- TIMEOUTS (seconds) — protect against the cursor-agent headless hang ----
CURSOR_TIMEOUT = 1200
CLAUDE_TIMEOUT = 600

# ---- PERMISSIONS / TOOLS ----
# PLAN is read-only on purpose
# (it writes nothing; the driver writes plan.md from
# its stdout), so it can never pollute the diff that execute+verify depend on.
# "mcp__codegraph" allows the codegraph MCP server's tools. If your codegraph
# tools are named differently and plan can't reach them, broaden this entry or
# (in an isolated worktree only) swap to PLAN_SKIP_PERMISSIONS = True.
PLAN_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "mcp__codegraph"]
PLAN_SKIP_PERMISSIONS = False
# VERIFY only needs to read the scratch files;
# --bare keeps it lean and predictable.
VERIFY_ALLOWED_TOOLS = ["Read"]

# ============================================================================
# Internals
# ============================================================================


class StepError(Exception):
    """A recoverable failure: the driver may retry, then stop with a reason."""


class FatalError(StepError):
    """Non-retryable (CLI missing, unknown backend, bad config). Abort at once."""


class NeedsClarification(Exception):
    """task.md is too unclear to plan and no answers are available. Carries the
    open questions so the driver can surface them and halt cleanly."""

    def __init__(self, questions, issues, assumptions):
        self.questions, self.issues, self.assumptions = questions, issues, assumptions
        super().__init__("task needs clarification before planning")


def log(msg, *, prefix="·"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def banner(text):
    print("\n" + "=" * 72, flush=True)
    print(f"  {text}", flush=True)
    print("=" * 72, flush=True)


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def strip_fences(text):
    """Tolerate a model wrapping JSON in ```json fences despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def run(cmd, *, timeout, cwd=REPO_ROOT, stdin=None):
    """Thin subprocess wrapper. Returns (returncode, stdout, stderr).
    Raises StepError on timeout so the loop can stop cleanly."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=stdin,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        raise StepError(f"command timed out after {timeout}s: {cmd[0]} ...")
    except FileNotFoundError:
        raise FatalError(f"command not found: {cmd[0]} — is it installed and on PATH?")


# ---- agent invocations -----------------------------------------------------


def run_claude(
    prompt,
    *,
    model,
    system_prompt_file,
    allowed_tools,
    bare=False,
    skip_permissions=False,
    timeout=CLAUDE_TIMEOUT,
):
    """Invoke Claude Code headless. Returns dict: {result, cost, is_error, raw}.
    Model is selected per-invocation, so each role can run on a different one."""
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    if bare:
        cmd.append("--bare")
    if system_prompt_file:
        cmd += ["--append-system-prompt", read_file(system_prompt_file)]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    elif allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]

    rc, out, err = run(cmd, timeout=timeout)

    # --output-format json envelope: result, total_cost_usd, is_error (guard all).
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        raise StepError(
            f"claude returned non-JSON envelope (rc={rc}). stderr:\n{err}\nstdout:\n{out[:800]}"
        )
    return {
        "result": env.get("result", ""),
        "cost": float(env.get("total_cost_usd", 0.0) or 0.0),
        "is_error": bool(env.get("is_error", False)) or rc != 0,
        "raw": env,
    }


def run_executor(prompt, *, backend, model, timeout=CURSOR_TIMEOUT):
    """Invoke the chosen executor CLI headless, edits auto-applied.
    Blocks until exit.
    The contract is identical across backends: read plan.md, edit files in
    the workspace, exit. Returns (returncode, combined_output, cost).
    `cost` is nonzero only for JSON-envelope backends (claude-as-executor)."""
    try:
        build = executors.EXECUTORS[backend]
    except KeyError:
        raise FatalError(
            f"unknown executor backend '{backend}'. "
            f"choose one of: {', '.join(executors.EXECUTORS)}"
        )
    rc, out, err = run(build(model, prompt), timeout=timeout)
    combined = out + ("\n" + err if err else "")
    cost = 0.0
    if backend in executors.JSON_ENVELOPE_BACKENDS:
        try:  # claude returns a JSON envelope
            env = json.loads(out)
            cost = float(env.get("total_cost_usd", 0.0) or 0.0)
            combined = env.get("result", combined)
        except json.JSONDecodeError:
            pass
    return rc, combined, cost


# ---- git / tests -----------------------------------------------------------


def git(*args):
    rc, out, err = run(["git", *args], timeout=120)
    if rc != 0:
        raise StepError(f"git {' '.join(args)} failed: {err.strip()}")
    return out


def capture_baseline():
    return git("rev-parse", "HEAD").strip()


def staged_diff_against(baseline):
    """Stage everything and return the
    cumulative diff vs the loop's start commit.
    Staging is how we capture new/untracked files in the diff;
    we never commit."""
    git("add", "-A")
    return git("diff", "--cached", baseline)


def run_tests():
    """Returns (ran: bool, passed: bool, header+output: str)."""
    if not TEST_COMMAND.strip():
        return False, True, "TESTS: SKIPPED\n(no TEST_COMMAND configured)"
    rc, out, err = run(["bash", "-lc", TEST_COMMAND], timeout=CURSOR_TIMEOUT)
    passed = rc == 0
    header = "TESTS: PASSED" if passed else "TESTS: FAILED"
    return True, passed, f"{header}\n$ {TEST_COMMAND}\n{out}\n{err}"


# ============================================================================
# Steps
# ============================================================================


def plan_step(iteration, prev_verdict):
    banner(f"ITERATION {iteration} — PLAN  ({PLAN_CLAUDE_MODEL_NAME})")
    payload = [
        "Produce the implementation plan. Read these files in the repo:",
        f"- {TASK_FILE} (goal + acceptance criteria — the source of truth)",
        f"- {CONTEXT_FILE} (architecture map)",
        "Use the codegraph tools to locate the exact symbols and files involved.",
    ]
    if os.path.exists(CLARIFY_FILE):
        payload.append(
            f"- {CLARIFY_FILE} (human answers to clarifying questions — authoritative)"
        )
    if prev_verdict is not None:
        payload += [
            "",
            "PREVIOUS VERDICT (the last attempt FAILED). Plan only the fixes for",
            "the criteria still not met; do not redo work that already passed:",
            json.dumps(
                {
                    "criteria": prev_verdict.get("criteria", []),
                    "reasons": prev_verdict.get("reasons", []),
                    "next_actions": prev_verdict.get("next_actions", []),
                },
                indent=2,
            ),
        ]
    payload.append("\nOutput ONLY the plan markdown, per your instructions.")

    res = run_claude(
        "\n".join(payload),
        model=PLAN_CLAUDE_MODEL_NAME,
        system_prompt_file=os.path.join(PROMPTS_DIR, "plan.md"),
        allowed_tools=PLAN_ALLOWED_TOOLS,
        skip_permissions=PLAN_SKIP_PERMISSIONS,
    )
    if res["is_error"] or not res["result"].strip():
        raise StepError("plan step produced no usable plan (see .loop for raw output).")
    write_file(PLAN_FILE, res["result"].strip() + "\n")
    log(f"plan.md written ({len(res['result'])} chars). cost=${res['cost']:.4f}")
    return res["cost"]


def execute_step(iteration):
    banner(
        f"ITERATION {iteration} — EXECUTE  "
        f"({EXECUTOR_BACKEND}:{IMPLEMENTATION_MODEL_NAME})"
    )
    prompt = read_file(os.path.join(PROMPTS_DIR, "execute.md"))
    attempts = EXECUTOR_MAX_RETRIES + 1
    last_err = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            rc, output, cost = run_executor(
                prompt,
                backend=EXECUTOR_BACKEND,
                model=IMPLEMENTATION_MODEL_NAME,
            )
            write_file(os.path.join(WORK_DIR, "executor_output.txt"), output)
            if rc == 0:
                log("execute complete (edits auto-applied).")
                return cost  # nonzero only when the executor is Claude
            last_err = f"executor '{EXECUTOR_BACKEND}' exited non-zero (rc={rc})"
        except FatalError:
            raise  # CLI missing / bad backend — retry can't help
        except StepError as e:
            last_err = str(e)  # transient: a hang/timeout from the executor
        if attempt < attempts:
            log(
                f"execute attempt {attempt}/{attempts} failed: {last_err}; "
                f"retrying in {EXECUTOR_RETRY_BACKOFF}s",
                prefix="↻",
            )
            time.sleep(EXECUTOR_RETRY_BACKOFF)
    raise StepError(
        f"executor failed after {attempts} attempt(s): {last_err}. "
        f"See {WORK_DIR}/executor_output.txt"
    )


def verify_step(iteration, baseline):
    banner(f"ITERATION {iteration} — VERIFY  ({VERIFICATION_CLAUDE_MODEL_NAME})")

    diff = staged_diff_against(baseline)
    write_file(os.path.join(WORK_DIR, "diff.patch"), diff)

    ran, passed, test_out = run_tests()
    write_file(os.path.join(WORK_DIR, "test_output.txt"), test_out)
    log(
        f"diff={len(diff)} chars | tests: {'PASSED' if passed else 'FAILED' if ran else 'SKIPPED'}"
    )

    if not diff.strip():
        # Executor changed nothing at all vs baseline — nothing to verify.
        return {
            "status": "blocked",
            "reasons": ["Executor produced no changes against the baseline commit."],
            "criteria": [],
            "tests": {"ran": ran, "passed": passed, "summary": "no diff"},
            "_no_diff": True,
        }, 0.0

    instruction = (
        f"Read {TASK_FILE} (acceptance criteria), {WORK_DIR}/diff.patch (the work "
        f"done), and {WORK_DIR}/test_output.txt (test result). Judge each criterion "
        f"and output the verdict JSON exactly per your instructions — JSON only."
    )
    res = run_claude(
        instruction,
        model=VERIFICATION_CLAUDE_MODEL_NAME,
        system_prompt_file=os.path.join(PROMPTS_DIR, "verify.md"),
        allowed_tools=VERIFY_ALLOWED_TOOLS,
        bare=True,
    )
    raw = strip_fences(res["result"])
    write_file(os.path.join(WORK_DIR, "verify_raw.txt"), res["result"])
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        raise StepError(
            f"verifier did not return valid JSON. Raw saved to {WORK_DIR}/verify_raw.txt"
        )

    if "status" not in verdict:
        raise StepError(
            f"verdict JSON missing 'status'. Raw saved to {WORK_DIR}/verify_raw.txt"
        )

    write_file(VERDICT_FILE, json.dumps(verdict, indent=2) + "\n")
    log(f"verdict: {verdict['status'].upper()}  cost=${res['cost']:.4f}")
    return verdict, res["cost"]


# ============================================================================
# Clarity gate (front-of-loop guardrail)
# ============================================================================

TASK_TEMPLATE = """# Task

## Goal
<One or two sentences: what should be true when this is done?>

## Acceptance criteria
<Numbered, each INDEPENDENTLY CHECKABLE. A coding agent and an autonomous
verifier must be able to tell whether each is met — prefer tests or observable
behavior over adjectives like "better" or "clean".>
1. C1: ...
2. C2: ...

## In scope
- <files / modules / behaviors this task may touch>

## Out of scope
- <things the executor must NOT change>

## Notes / constraints
- <perf targets, compatibility requirements, anything load-bearing>
"""


def triage_step():
    """Judge whether task.md is clear enough to plan. Returns (parsed, cost)."""
    files = [f"{TASK_FILE} (the task to evaluate)", f"{CONTEXT_FILE} (architecture)"]
    if os.path.exists(CLARIFY_FILE):
        files.append(f"{CLARIFY_FILE} (human answers already given — authoritative)")
    instruction = (
        "Evaluate task readiness. Read: "
        + "; ".join(files)
        + ". Output the clarity JSON exactly per your instructions — JSON only."
    )
    res = run_claude(
        instruction,
        model=CLARIFY_MODEL_NAME,
        system_prompt_file=os.path.join(PROMPTS_DIR, "triage.md"),
        allowed_tools=["Read"],
        bare=True,
    )
    write_file(os.path.join(WORK_DIR, "triage_raw.txt"), res["result"])
    try:
        return json.loads(strip_fences(res["result"])), res["cost"]
    except json.JSONDecodeError:
        # If the gate itself misbehaves,
        # don't trap the user — warn and proceed.
        log("clarity gate returned non-JSON; proceeding without it.", prefix="!")
        return {
            "ready": True,
            "questions": [],
            "issues": [],
            "assumptions_if_unanswered": [],
        }, res["cost"]


def clarify_gate():
    """Run the clarity gate. In a TTY, collect answers and re-check; otherwise
    halt with the open questions. Returns cost; raises NeedsClarification to stop."""
    banner(f"CLARITY GATE  ({CLARIFY_MODEL_NAME})")
    cost = 0.0
    questions, issues, assumptions = [], [], []
    for round_no in range(1, CLARIFY_MAX_ROUNDS + 1):
        parsed, c = triage_step()
        cost += c
        if parsed.get("ready"):
            log("task is clear enough to plan.", prefix="✓")
            return cost

        issues = parsed.get("issues", [])
        questions = parsed.get("questions", [])
        assumptions = parsed.get("assumptions_if_unanswered", [])
        log(f"task not ready (round {round_no}):", prefix="!")
        for i in issues:
            log(f"  issue: {i}")

        if not (INTERACTIVE_CLARIFY and sys.stdin.isatty()):
            raise NeedsClarification(
                questions, issues, assumptions
            )  # unattended -> halt

        block = ["", f"## Clarification round {round_no}"]
        for q in questions:
            print(f"\nQ ({q.get('id','?')}): {q.get('question','')}")
            if q.get("why"):
                print(f"   (why it matters: {q['why']})")
            ans = input("   your answer > ").strip()
            block += [f"- Q ({q.get('id','?')}): {q.get('question','')}", f"  A: {ans}"]
        existing = (
            read_file(CLARIFY_FILE)
            if os.path.exists(CLARIFY_FILE)
            else "# Clarifications\n"
        )
        write_file(CLARIFY_FILE, existing + "\n".join(block) + "\n")
        log("answers recorded; re-checking clarity.", prefix="↻")

    raise NeedsClarification(questions, issues, assumptions)  # rounds exhausted


def halt_needs_clarification(nc, total_cost):
    banner("STOPPED — task needs clarification before planning")
    for i in nc.issues:
        log(f"  unclear: {i}", prefix="!")
    if nc.questions:
        log(
            "answer these (edit task.md, or add answers to clarifications.md), then re-run:",
            prefix="?",
        )
        for q in nc.questions:
            log(f"  - [{q.get('id','?')}] {q.get('question','')}")
    for a in nc.assumptions:
        log(f"  if unanswered, planner would assume: {a}", prefix="·")
    write_file(
        CLARIFY_NEEDED_FILE,
        json.dumps(
            {
                "issues": nc.issues,
                "questions": nc.questions,
                "assumptions_if_unanswered": nc.assumptions,
            },
            indent=2,
        )
        + "\n",
    )
    log(f"questions also written to {CLARIFY_NEEDED_FILE}")
    log(f"claude spend : ${total_cost:.4f}")
    sys.exit(2)


# ============================================================================
# Main loop
# ============================================================================


def preflight():
    # task.md missing is special: scaffold a template and stop, not a bare error.
    if not os.path.exists(os.path.join(REPO_ROOT, TASK_FILE)):
        write_file(os.path.join(REPO_ROOT, TASK_FILE), TASK_TEMPLATE)
        raise FatalError(
            f"{TASK_FILE} did not exist — I wrote a template there. "
            f"Fill in the goal and acceptance criteria, then re-run."
        )
    missing = [
        p
        for p in (
            CONTEXT_FILE,
            os.path.join(PROMPTS_DIR, "plan.md"),
            os.path.join(PROMPTS_DIR, "triage.md"),
            os.path.join(PROMPTS_DIR, "verify.md"),
            os.path.join(PROMPTS_DIR, "execute.md"),
        )
        if not os.path.exists(os.path.join(REPO_ROOT, p))
    ]
    if missing:
        raise FatalError("missing required files: " + ", ".join(missing))
    rc, _, _ = run(["git", "rev-parse", "--is-inside-work-tree"], timeout=30)
    if rc != 0:
        raise FatalError("REPO_ROOT is not a git repository (needed to diff/verify).")
    os.makedirs(os.path.join(REPO_ROOT, WORK_DIR), exist_ok=True)


def stall_signature(verdict, diff_path):
    """A fingerprint of 'no progress': same failure reasons + same diff."""
    reasons = "||".join(verdict.get("reasons", []))
    try:
        diff = read_file(os.path.join(REPO_ROOT, diff_path))
    except FileNotFoundError:
        diff = ""
    return hashlib.sha256((reasons + "\n" + diff).encode()).hexdigest()


def main():
    parse_cli_overrides()
    os.chdir(REPO_ROOT)
    try:
        preflight()
    except StepError as e:  # includes FatalError (missing files, not a repo)
        log(str(e), prefix="✗")
        sys.exit(1)

    total_cost = 0.0  # Claude spend only; non-Claude executors bill separately

    # GUARDRAIL — clarity gate: never plan against a vague or missing task.
    try:
        total_cost += clarify_gate()
    except NeedsClarification as nc:
        halt_needs_clarification(nc, total_cost)  # writes file, prints, exits(2)
    except StepError as e:
        log(str(e), prefix="✗")
        sys.exit(1)

    baseline = capture_baseline()
    log(f"baseline commit: {baseline[:10]}  | max_iterations={MAX_ITERATIONS}")

    prev_verdict = None
    last_stall = None
    stall_count = 0
    final_status = "incomplete"

    for it in range(1, MAX_ITERATIONS + 1):
        try:
            total_cost += plan_step(it, prev_verdict)
            total_cost += execute_step(it)
            verdict, vcost = verify_step(it, baseline)
            total_cost += vcost
        except StepError as e:
            banner("STOPPED — hard failure")
            log(str(e), prefix="✗")
            final_status = "error"
            break

        status = verdict.get("status")

        if status == "pass":
            final_status = "pass"
            banner("DONE — all criteria met")
            break

        if status == "blocked":
            final_status = "blocked"
            banner("STOPPED — blocked (needs a human)")
            for r in verdict.get("reasons", []):
                log(r, prefix="!")
            break

        # status == "fail": check whether we're actually making progress.
        sig = stall_signature(verdict, os.path.join(WORK_DIR, "diff.patch"))
        if sig == last_stall:
            stall_count += 1
        else:
            stall_count = 0
            last_stall = sig
        if stall_count >= MAX_IDENTICAL_FAILURES:
            final_status = "stalled"
            banner("STOPPED — no progress across iterations (same failure + same diff)")
            break

        log(f"iteration {it} failed; feeding reasons into next plan:", prefix="↻")
        for r in verdict.get("reasons", []):
            log(f"  - {r}")
        prev_verdict = verdict
    else:
        final_status = "budget_exhausted"
        banner(f"STOPPED — hit MAX_ITERATIONS ({MAX_ITERATIONS}) without passing")

    banner("SUMMARY")
    log(f"final status : {final_status}")
    log(f"claude spend : ${total_cost:.4f}  (non-Claude executors bill separately)")
    log(f"artifacts    : {PLAN_FILE}, {VERDICT_FILE}, {WORK_DIR}/diff.patch")
    log("changes are staged but NOT committed — review, then commit or discard.")
    sys.exit(0 if final_status == "pass" else 1)


def parse_cli_overrides():
    """Let the three model variables (and a couple of knobs) be set at launch
    without editing the file."""
    global PLAN_CLAUDE_MODEL_NAME, IMPLEMENTATION_MODEL_NAME, EXECUTOR_BACKEND
    global VERIFICATION_CLAUDE_MODEL_NAME, MAX_ITERATIONS, TEST_COMMAND, REPO_ROOT
    p = argparse.ArgumentParser(description="Agentic plan/execute/verify loop.")
    p.add_argument("--plan-model", default=PLAN_CLAUDE_MODEL_NAME)
    p.add_argument(
        "--executor",
        default=EXECUTOR_BACKEND,
        choices=list(executors.EXECUTORS),
        help="which coding CLI runs the execute step",
    )
    p.add_argument(
        "--impl-model",
        default=IMPLEMENTATION_MODEL_NAME,
        help="executor model slug for the chosen --executor",
    )
    p.add_argument("--verify-model", default=VERIFICATION_CLAUDE_MODEL_NAME)
    p.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS)
    p.add_argument("--test-command", default=TEST_COMMAND)
    p.add_argument("--repo", default=REPO_ROOT, help="path to the target git repo")
    a = p.parse_args()
    PLAN_CLAUDE_MODEL_NAME = a.plan_model
    EXECUTOR_BACKEND = a.executor
    IMPLEMENTATION_MODEL_NAME = a.impl_model
    VERIFICATION_CLAUDE_MODEL_NAME = a.verify_model
    MAX_ITERATIONS = a.max_iterations
    TEST_COMMAND = a.test_command
    REPO_ROOT = a.repo


if __name__ == "__main__":
    main()
