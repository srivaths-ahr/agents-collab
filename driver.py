#!/usr/bin/env python3
"""
Agentic build loop driver.

Sequences stateless one-shot agents around a shared git workspace:

    CLARIFY  (Claude)   gate: is task.md clear enough to plan? if not, ask/halt
    PLAN     (Claude)   reads task.md + context.md (+ codegraph) + last verdict
                        -> writes plan.md
    EXECUTE  (executor) reads plan.md (+ AGENTS.md) -> edits files
                        (pluggable: cursor | claude | codex | antigravity)
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
import shlex
import shutil
import subprocess
import sys
import time

import executors  # executor adapter registry (the pluggable EXECUTE step)

__version__ = "0.1.0"  # bump in CHANGELOG.md too; executor adapters drift over time

# ============================================================================
# CONFIG — fill these before a run (all overridable via CLI flags below)
# ============================================================================

# ---- MODELS (filled before a run; also overridable on the CLI) ----
PLAN_CLAUDE_MODEL_NAME = "opus"  # planning: strong, long-thinking
VERIFICATION_CLAUDE_MODEL_NAME = "haiku"  # verification: cheap, bounded

# ---- EXECUTOR (pluggable — plan & verify stay on Claude, only this swaps) ---
# One of executors.EXECUTORS:
#   "cursor" | "claude" | "codex" | "antigravity"
#   EXECUTOR_BACKEND="claude" -> Claude plans, Claude executes, Claude verifies
#   EXECUTOR_BACKEND="codex"  -> Claude plans, Codex  executes, Claude verifies
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
MAX_COST_USD = 0.0  # hard cap on cumulative Claude spend; 0.0 = no dollar limit

# ---- TEST / GATE COMMANDS (the objective half of verification) ----
# Deterministic commands the driver runs after each execute. ALL must pass; their
# combined pass/fail is the ground truth handed to the verifier. Empty list = skip
# and judge on the diff alone. A list lets you gate on lint + build + test as
# separate, independently-reported checks.
TEST_COMMANDS = []  # e.g. ["ruff check .", "pytest -q"]  |  ["swift build", "swift test"]

# ---- MODES (set via CLI; see parse_cli_overrides) ----
DRY_RUN = False  # --dry-run: print every command + prompt, but run/spend/edit nothing

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
# VERIFY only needs to read the scratch files.
# NOTE: we deliberately do NOT pass claude's --bare for verify/clarify. --bare
# ("minimal mode") skips hooks/LSP/plugins — and on some setups that also skips the
# plugin-provided login, so claude returns "Not logged in" for those steps while
# plan (which omits --bare) works. Avoid it. (Observed on claude-cli 2.1.195.)
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
    Raises StepError on timeout so the loop can stop cleanly.

    When no input is provided we close the child's stdin (DEVNULL) rather than
    letting it inherit ours. Headless agent CLIs that probe stdin otherwise block
    forever waiting on input that never comes (e.g. `agy --print` hangs with no
    output until its timeout). Closing stdin gives them an immediate EOF."""
    kwargs = dict(cwd=cwd, timeout=timeout, capture_output=True, text=True)
    if stdin is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin
    try:
        proc = subprocess.run(cmd, **kwargs)
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
    cmd = build_claude_argv(
        prompt,
        model=model,
        system_prompt_file=system_prompt_file,
        allowed_tools=allowed_tools,
        bare=bare,
        skip_permissions=skip_permissions,
    )
    rc, out, err = run(cmd, timeout=timeout)
    return parse_claude_envelope(out, err, rc)


def build_claude_argv(
    prompt,
    *,
    model,
    system_prompt_file,
    allowed_tools,
    bare=False,
    skip_permissions=False,
):
    """Assemble the exact `claude` argv for a headless one-shot. Pure except for
    reading the system-prompt file (its contents are passed via
    --append-system-prompt). Shared by run_claude and the --dry-run preview so the
    two can never drift."""
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    if bare:
        cmd.append("--bare")
    if system_prompt_file:
        cmd += ["--append-system-prompt", read_file(system_prompt_file)]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    elif allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    return cmd


def parse_claude_envelope(out, err, rc):
    """Parse Claude Code's --output-format json envelope into
    {result, cost, is_error, raw}. Pure (no subprocess), so it can be unit-tested
    against sample and malformed output. Raises StepError if stdout is not the
    expected JSON envelope. Guards every field; a nonzero rc forces is_error."""
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        raise StepError(
            f"claude returned non-JSON envelope (rc={rc}). "
            f"stderr:\n{err}\nstdout:\n{out[:800]}"
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
    return parse_executor_output(out, err, rc, backend)


def parse_executor_output(out, err, rc, backend):
    """Combine an executor's stdout/stderr and, for JSON-envelope backends, pull
    the final result text and cost out of the envelope. Non-envelope backends are
    plain text; a malformed envelope falls back to the combined text with zero
    cost. Pure (no subprocess). Returns (rc, combined_output, cost)."""
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
    """Run each configured gate in order; ALL must pass. Returns
    (ran: bool, passed: bool, output: str). The output's FIRST LINE is the overall
    "TESTS: PASSED/FAILED/SKIPPED" status the verifier keys on, followed by a
    per-gate breakdown. A gate timeout raises StepError and stops the loop."""
    gates = [c for c in TEST_COMMANDS if c.strip()]
    if not gates:
        return False, True, "TESTS: SKIPPED\n(no test/gate command configured)"
    all_passed = True
    sections = []
    for cmd in gates:
        rc, out, err = run(["bash", "-lc", cmd], timeout=CURSOR_TIMEOUT)
        passed = rc == 0
        all_passed = all_passed and passed
        sections.append(f"[{'PASS' if passed else 'FAIL'}] $ {cmd}\n{out}{err}")
    header = "TESTS: PASSED" if all_passed else "TESTS: FAILED"
    return True, all_passed, header + "\n" + "\n\n".join(sections)


# ============================================================================
# Steps
# ============================================================================


def plan_instruction(prev_verdict):
    """The PLAN step's user prompt. Pure (modulo reading whether clarifications.md
    exists); shared by plan_step and the --dry-run preview."""
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
    return "\n".join(payload)


def plan_step(iteration, prev_verdict):
    banner(f"ITERATION {iteration} — PLAN  ({PLAN_CLAUDE_MODEL_NAME})")
    res = run_claude(
        plan_instruction(prev_verdict),
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


def verify_instruction():
    """The VERIFY step's user prompt. Pure; shared by verify_step and --dry-run."""
    return (
        f"Read {TASK_FILE} (acceptance criteria), {WORK_DIR}/diff.patch (the work "
        f"done), and {WORK_DIR}/test_output.txt (test result). Judge each criterion "
        f"and output the verdict JSON exactly per your instructions — JSON only."
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

    res = run_claude(
        verify_instruction(),
        model=VERIFICATION_CLAUDE_MODEL_NAME,
        system_prompt_file=os.path.join(PROMPTS_DIR, "verify.md"),
        allowed_tools=VERIFY_ALLOWED_TOOLS,
    )
    write_file(os.path.join(WORK_DIR, "verify_raw.txt"), res["result"])
    verdict = parse_verdict(res["result"])
    write_file(VERDICT_FILE, json.dumps(verdict, indent=2) + "\n")
    log(f"verdict: {verdict['status'].upper()}  cost=${res['cost']:.4f}")
    return verdict, res["cost"]


def parse_verdict(raw_result):
    """Parse the verifier's raw stdout into a verdict dict: tolerate ```json
    fences, require valid JSON carrying a 'status' field. Pure (no subprocess, no
    file writes), so it can be unit-tested against sample and malformed output.
    Raises StepError on anything malformed (the caller has already saved the raw
    text to verify_raw.txt for inspection)."""
    raw = strip_fences(raw_result)
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
    return verdict


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


def triage_instruction():
    """The CLARITY-GATE user prompt. Pure; shared by triage_step and --dry-run."""
    files = [f"{TASK_FILE} (the task to evaluate)", f"{CONTEXT_FILE} (architecture)"]
    if os.path.exists(CLARIFY_FILE):
        files.append(f"{CLARIFY_FILE} (human answers already given — authoritative)")
    return (
        "Evaluate task readiness. Read: "
        + "; ".join(files)
        + ". Output the clarity JSON exactly per your instructions — JSON only."
    )


def triage_step():
    """Judge whether task.md is clear enough to plan. Returns (parsed, cost)."""
    res = run_claude(
        triage_instruction(),
        model=CLARIFY_MODEL_NAME,
        system_prompt_file=os.path.join(PROMPTS_DIR, "triage.md"),
        allowed_tools=["Read"],
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
            print(f"\nQ ({q.get('id', '?')}): {q.get('question', '')}")
            if q.get("why"):
                print(f"   (why it matters: {q['why']})")
            ans = input("   your answer > ").strip()
            block += [
                f"- Q ({q.get('id', '?')}): {q.get('question', '')}",
                f"  A: {ans}",
            ]
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
            log(f"  - [{q.get('id', '?')}] {q.get('question', '')}")
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


def progress_fingerprint(reasons, diff_text):
    """Hash of (failure reasons + diff). Identical fingerprints across iterations
    mean the loop made no progress. Pure, so it's unit-testable."""
    return hashlib.sha256(("||".join(reasons) + "\n" + diff_text).encode()).hexdigest()


def stall_signature(verdict, diff_path):
    """A fingerprint of 'no progress': same failure reasons + same diff."""
    try:
        diff = read_file(os.path.join(REPO_ROOT, diff_path))
    except FileNotFoundError:
        diff = ""
    return progress_fingerprint(verdict.get("reasons", []), diff)


# ============================================================================
# Subcommands / modes (doctor, dry-run) — run before any spend
# ============================================================================


def _probe_tool(binary):
    """(present, detail) for a CLI: is it on PATH, and what is its --version line?
    Uses shutil.which so an absent tool never spawns a process; the --version probe
    is best-effort (some CLIs differ) and its failure never flips presence."""
    path = shutil.which(binary)
    if not path:
        return False, f"NOT FOUND — `{binary}` is not on PATH"
    try:
        rc, out, err = run([binary, "--version"], timeout=15)
        line = (out.strip() or err.strip()).splitlines()
        return True, (f"{line[0]}   [{path}]" if line else path)
    except StepError:  # timeout (or a FileNotFoundError race) — still 'present'
        return True, f"{path}   (--version probe failed)"


def doctor():
    """Preflight the environment: are the CLIs the loop needs installed, and is
    this a git repo with the prompt files in place? Prints a checklist and exits 0
    only if a real run could start — turning a mid-run 'command not found' into a
    two-second report. Never spends or edits anything."""
    banner("DOCTOR — environment preflight")
    checks = []  # (ok, message) — these gate the exit code

    git_ok, git_detail = _probe_tool("git")
    checks.append((git_ok, f"git: {git_detail}"))

    claude_ok, claude_detail = _probe_tool("claude")
    checks.append((claude_ok, f"claude (plan / verify / clarify): {claude_detail}"))

    # The executor's binary is argv[0] of its own adapter — single source of truth.
    if EXECUTOR_BACKEND in executors.EXECUTORS:
        exec_bin = executors.EXECUTORS[EXECUTOR_BACKEND]("MODEL", "PROMPT")[0]
        exec_ok, exec_detail = _probe_tool(exec_bin)
        checks.append(
            (exec_ok, f"executor '{EXECUTOR_BACKEND}' ({exec_bin}): {exec_detail}")
        )
    else:
        checks.append(
            (
                False,
                f"executor '{EXECUTOR_BACKEND}': unknown backend "
                f"(choose from {', '.join(executors.EXECUTORS)})",
            )
        )

    if git_ok:
        rc, _, _ = run(["git", "rev-parse", "--is-inside-work-tree"], timeout=30)
        checks.append(
            (
                rc == 0,
                "git repository: "
                + (os.getcwd() if rc == 0 else f"{os.getcwd()} is NOT one"),
            )
        )
    else:
        checks.append((False, "git repository: skipped (git missing)"))

    missing = [
        p
        for p in ("plan.md", "triage.md", "verify.md", "execute.md")
        if not os.path.exists(os.path.join(PROMPTS_DIR, p))
    ]
    checks.append(
        (
            not missing,
            "prompt files: "
            + (
                "all present in prompts/"
                if not missing
                else "MISSING " + ", ".join(missing)
            ),
        )
    )

    for ok, msg in checks:
        log(msg, prefix="✓" if ok else "✗")

    # Informational only (not gating): task/context are user-provided or scaffolded.
    for path, note in (
        (TASK_FILE, "the task to run"),
        (CONTEXT_FILE, "architecture map"),
    ):
        here = os.path.exists(path)
        log(
            f"{path} ({note}): {'present' if here else 'missing — create before a run'}",
            prefix="·" if here else "!",
        )

    all_ok = all(ok for ok, _ in checks)
    banner("DOCTOR — all systems go" if all_ok else "DOCTOR — problems found above")
    if not all_ok:
        log("fix the ✗ items, then re-run `python driver.py doctor`.", prefix="!")
    sys.exit(0 if all_ok else 1)


def _indent(text, pad="    "):
    return "\n".join(pad + line for line in text.splitlines())


def _render_argv(argv, subs):
    """Render an argv list as a readable single line for preview: replace the long
    known values (the user prompt, the system-prompt contents) with the named
    placeholders in `subs`, abbreviate anything else long, shell-quote the rest."""
    parts = []
    for tok in argv:
        if tok in subs:
            parts.append(subs[tok])
        elif len(tok) > 80 or "\n" in tok:
            parts.append(f"<{len(tok)} chars>")
        else:
            parts.append(shlex.quote(tok))
    return " ".join(parts)


def _preview_claude(
    title,
    *,
    model,
    prompt,
    system_prompt_file,
    allowed_tools,
    bare=False,
    skip_permissions=False,
):
    banner(f"{title}  (claude, model={model})")
    sys_content = read_file(system_prompt_file)
    argv = build_claude_argv(
        prompt,
        model=model,
        system_prompt_file=system_prompt_file,
        allowed_tools=allowed_tools,
        bare=bare,
        skip_permissions=skip_permissions,
    )
    subs = {prompt: "<USER PROMPT ↓>", sys_content: f"<{system_prompt_file} contents>"}
    log(
        f"system prompt: {system_prompt_file} "
        f"({len(sys_content)} chars, via --append-system-prompt)"
    )
    log(f"argv: {_render_argv(argv, subs)}")
    print("\n  user prompt:\n" + _indent(prompt) + "\n", flush=True)


def _preview_executor():
    banner(f"EXECUTE  (executor={EXECUTOR_BACKEND}, model={IMPLEMENTATION_MODEL_NAME})")
    prompt = read_file(os.path.join(PROMPTS_DIR, "execute.md"))
    if EXECUTOR_BACKEND not in executors.EXECUTORS:
        log(
            f"unknown backend '{EXECUTOR_BACKEND}'; choose from "
            f"{', '.join(executors.EXECUTORS)}",
            prefix="✗",
        )
        return
    argv = executors.EXECUTORS[EXECUTOR_BACKEND](IMPLEMENTATION_MODEL_NAME, prompt)
    log(f"argv: {_render_argv(argv, {prompt: '<EXECUTE PROMPT ↓>'})}")
    log("in a real run this command AUTO-APPLIES edits to the workspace.")
    print(
        "\n  execute prompt (from prompts/execute.md):\n" + _indent(prompt) + "\n",
        flush=True,
    )


def dry_run():
    """Print the exact commands and prompts the loop would issue for iteration 1 —
    clarity gate, plan, execute, verify — without calling Claude, running the
    executor, or editing a single file. The argv and prompts come from the same
    builders the real steps use, so the preview cannot lie about what will run."""
    banner("DRY RUN — printing commands only; no Claude calls, no edits, no spend")
    missing = [
        p
        for p in ("triage.md", "plan.md", "execute.md", "verify.md")
        if not os.path.exists(os.path.join(PROMPTS_DIR, p))
    ]
    if missing:
        log(
            "missing prompt files: "
            + ", ".join(missing)
            + " — run `python driver.py doctor`.",
            prefix="✗",
        )
        sys.exit(1)

    _preview_claude(
        "CLARITY GATE",
        model=CLARIFY_MODEL_NAME,
        prompt=triage_instruction(),
        system_prompt_file=os.path.join(PROMPTS_DIR, "triage.md"),
        allowed_tools=["Read"],
    )
    _preview_claude(
        "PLAN  (iteration 1 — no previous verdict)",
        model=PLAN_CLAUDE_MODEL_NAME,
        prompt=plan_instruction(None),
        system_prompt_file=os.path.join(PROMPTS_DIR, "plan.md"),
        allowed_tools=PLAN_ALLOWED_TOOLS,
        skip_permissions=PLAN_SKIP_PERMISSIONS,
    )
    _preview_executor()

    banner("TEST GATES  (run before VERIFY; their pass/fail is ground truth)")
    gates = [c for c in TEST_COMMANDS if c.strip()]
    if gates:
        for cmd in gates:
            log(f"$ {cmd}")
        log("ALL must pass; the verifier sees the combined result.")
    else:
        log("none configured — the verifier judges on the diff alone.", prefix="!")

    _preview_claude(
        "VERIFY",
        model=VERIFICATION_CLAUDE_MODEL_NAME,
        prompt=verify_instruction(),
        system_prompt_file=os.path.join(PROMPTS_DIR, "verify.md"),
        allowed_tools=VERIFY_ALLOWED_TOOLS,
    )

    banner("DRY RUN — end (nothing was executed)")
    log("run `python driver.py doctor` to confirm these CLIs are installed.")
    sys.exit(0)


def main():
    command = parse_cli_overrides()
    os.chdir(REPO_ROOT)

    if command == "doctor":
        doctor()  # prints the checklist and exits
    if DRY_RUN:
        dry_run()  # prints the planned commands and exits
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
        if MAX_COST_USD > 0 and total_cost >= MAX_COST_USD:
            final_status = "cost_exhausted"
            banner(
                f"STOPPED — Claude spend ${total_cost:.4f} hit cap ${MAX_COST_USD:.2f}"
            )
            break
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


def resolve_choice(raw, options, default):
    """Map a raw prompt answer to one of `options`. Pure (no I/O) so it's unit-tested.
    Empty -> default; a 1-based index or an exact option -> that option; anything else
    -> None (the caller re-asks)."""
    raw = raw.strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    if raw in options:
        return raw
    return None


def resolve_number(raw, default, *, cast, minimum):
    """Parse a numeric prompt answer. Pure (no I/O) so it's unit-tested. Empty ->
    default; a value >= minimum -> cast(value); non-numeric or below minimum -> None."""
    raw = raw.strip()
    if not raw:
        return default
    try:
        val = cast(raw)
    except ValueError:
        return None
    return val if val >= minimum else None


def _interactive():
    """True only when we can both ask and be answered — never prompt a headless/CI
    run (it would block on input that never comes, the very hang we avoid elsewhere)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt_run_settings(a, *, executor_default, impl_default, iter_default, cost_default):
    """For an interactive `run`, ask for any of the four run knobs NOT passed on the
    CLI; a flag the user did pass is kept as-is. Returns (executor, impl_model,
    max_iter, max_cost). I/O layer only — parsing lives in the pure resolve_* helpers."""
    shown = [False]

    def intro():
        if not shown[0]:
            print("\nSome run settings weren't passed — choose them (Enter = [default]):")
            shown[0] = True

    executor = a.executor
    if executor is None:
        intro()
        names = list(executors.EXECUTORS)
        print("\n  Executor (the coding CLI that runs EXECUTE):")
        for i, n in enumerate(names, 1):
            print(f"    {i}) {n}" + ("   (default)" if n == executor_default else ""))
        while executor is None:
            executor = resolve_choice(
                input(f"  choice [1-{len(names)}, Enter={executor_default}]: "),
                names,
                executor_default,
            )
            if executor is None:
                print("    not a valid choice.")

    impl_model = a.impl_model
    if impl_model is None:
        intro()
        suggested = executors.SUGGESTED_IMPL_MODELS.get(executor, impl_default)
        impl_model = input(f"  Model slug for '{executor}' [Enter={suggested}]: ").strip() or suggested

    max_iter = a.max_iterations
    if max_iter is None:
        intro()
        while max_iter is None:
            max_iter = resolve_number(
                input(f"  Max iterations [Enter={iter_default}]: "),
                iter_default,
                cast=int,
                minimum=1,
            )
            if max_iter is None:
                print("    enter a positive integer.")

    max_cost = a.max_cost_usd
    if max_cost is None:
        intro()
        while max_cost is None:
            max_cost = resolve_number(
                input(f"  Max Claude spend USD, 0 = no cap [Enter={cost_default}]: "),
                cost_default,
                cast=float,
                minimum=0.0,
            )
            if max_cost is None:
                print("    enter a non-negative number.")

    return executor, impl_model, max_iter, max_cost


def parse_cli_overrides():
    """Let the three model variables (and a couple of knobs) be set at launch
    without editing the file. Returns the optional subcommand ('doctor' or None).

    `--executor`/`--impl-model`/`--max-iterations`/`--max-cost-usd` default to None so
    we can tell "unset" from "set to the default value": when unset on an interactive
    `run`, we prompt for them; otherwise we fall back to the module-constant defaults
    (so headless/CI runs and the documented multi-unit loop — which passes them — are
    unchanged and never block on input)."""
    global PLAN_CLAUDE_MODEL_NAME, IMPLEMENTATION_MODEL_NAME, EXECUTOR_BACKEND
    global VERIFICATION_CLAUDE_MODEL_NAME, MAX_ITERATIONS, TEST_COMMANDS, REPO_ROOT
    global MAX_COST_USD, DRY_RUN
    global TASK_FILE, CONTEXT_FILE, WORK_DIR
    p = argparse.ArgumentParser(description="Agentic plan/execute/verify loop.")
    p.add_argument("--version", action="version", version=f"agentic-loop {__version__}")
    p.add_argument(
        "command",
        nargs="?",
        choices=["run", "doctor"],
        default="run",
        help="'run' (default) the loop, or 'doctor' to preflight the environment "
        "(checks git + the claude/executor CLIs and their versions) without spending.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact commands and prompts each step would issue, then "
        "exit — no Claude calls, no executor, no edits, no spend.",
    )
    p.add_argument("--plan-model", default=PLAN_CLAUDE_MODEL_NAME)
    p.add_argument(
        "--executor",
        default=None,
        choices=list(executors.EXECUTORS),
        help="which coding CLI runs the execute step "
        "(prompts if omitted on an interactive run)",
    )
    p.add_argument(
        "--impl-model",
        default=None,
        help="executor model slug for the chosen --executor "
        "(prompts if omitted on an interactive run)",
    )
    p.add_argument("--verify-model", default=VERIFICATION_CLAUDE_MODEL_NAME)
    p.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="hard cap on loop rounds (prompts if omitted on an interactive run)",
    )
    p.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="hard cap on cumulative Claude spend in USD (0 = no limit; "
        "prompts if omitted on an interactive run)",
    )
    p.add_argument(
        "--test-command",
        action="append",
        default=None,
        help="deterministic gate; repeat for multiple (e.g. lint, build, test). "
        "ALL must pass. Omit to judge on the diff alone.",
    )
    p.add_argument("--repo", default=REPO_ROOT, help="path to the target git repo")
    p.add_argument(
        "--task",
        default=TASK_FILE,
        help="path to the task file to run (default: task.md). Point at one unit's "
        "task.md to loop over an externally-decomposed story — one unit per invocation.",
    )
    p.add_argument(
        "--context",
        default=CONTEXT_FILE,
        help="path to the architecture-map file (default: context.md); shared across units.",
    )
    p.add_argument(
        "--work-dir",
        default=WORK_DIR,
        help="scratch dir for diff/test/raw artifacts (default: .loop). Override per unit "
        "so a loop's per-unit artifacts don't overwrite each other.",
    )
    a = p.parse_args()
    PLAN_CLAUDE_MODEL_NAME = a.plan_model
    VERIFICATION_CLAUDE_MODEL_NAME = a.verify_model

    # The four prompt-able knobs: a passed flag always wins; otherwise ask on an
    # interactive `run`, else keep the module-constant default (unchanged headless).
    if a.command == "run" and not a.dry_run and _interactive():
        EXECUTOR_BACKEND, IMPLEMENTATION_MODEL_NAME, MAX_ITERATIONS, MAX_COST_USD = (
            prompt_run_settings(
                a,
                executor_default=EXECUTOR_BACKEND,
                impl_default=IMPLEMENTATION_MODEL_NAME,
                iter_default=MAX_ITERATIONS,
                cost_default=MAX_COST_USD,
            )
        )
    else:
        EXECUTOR_BACKEND = a.executor if a.executor is not None else EXECUTOR_BACKEND
        IMPLEMENTATION_MODEL_NAME = (
            a.impl_model if a.impl_model is not None else IMPLEMENTATION_MODEL_NAME
        )
        MAX_ITERATIONS = (
            a.max_iterations if a.max_iterations is not None else MAX_ITERATIONS
        )
        MAX_COST_USD = a.max_cost_usd if a.max_cost_usd is not None else MAX_COST_USD

    if a.test_command is not None:  # keep module-level TEST_COMMANDS if flag unused
        TEST_COMMANDS = a.test_command
    REPO_ROOT = a.repo
    TASK_FILE = a.task
    CONTEXT_FILE = a.context
    WORK_DIR = a.work_dir
    DRY_RUN = a.dry_run
    return a.command


if __name__ == "__main__":
    main()
