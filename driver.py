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

__version__ = "0.4.0"  # bump in CHANGELOG.md too; executor adapters drift over time

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

# ---- AUTHOR STEP (standalone `author` subcommand: raw story -> task.md) ----
# Not part of the plan/execute/verify loop. Turns a requirements/Jira story (from
# --from FILE or stdin) into a well-formed task.md, asking clarifying questions in a
# TTY. Reasoning-heavy but human-reviewed, so a mid-tier model is the default;
# --author-model overrides. Read-only tools only (grounds criteria in the real repo).
AUTHOR_MODEL_NAME = "sonnet"
AUTHOR_MAX_ROUNDS = 3  # interactive draft/Q&A rounds (mirrors CLARIFY_MAX_ROUNDS)
INTERACTIVE_AUTHOR = True  # TTY: ask live & refine; non-TTY: write best-effort draft
AUTHOR_ALLOWED_TOOLS = ["Read", "Grep", "Glob"]
AUTHOR_STORY_FILE = "author_story.md"  # basename under WORK_DIR (already gitignored)
AUTHOR_ANSWERS_FILE = "author_answers.md"  # author Q&A scratch — NOT clarifications.md
AUTHOR_STORY_SRC = None  # set from --from ("-"/None => stdin)
AUTHOR_FORCE = False  # set from --force (overwrite an existing task.md without asking)

# ---- LOOP BUDGET / STOP GUARDS ----
MAX_ITERATIONS = 8  # hard cap; loop stops even if not "pass"
MAX_IDENTICAL_FAILURES = (
    2  # stop if the loop stalls (same failure / no new diff) this many times
)
MAX_PHASE_ATTEMPTS = 2  # re-entries at one phase before a resume backs up to the
# previous one. MAX_IDENTICAL_FAILURES catches a loop going nowhere WITHIN a run
# (verify keeps returning fail); this catches one going nowhere ACROSS runs (the
# same phase crashes every time), where retrying it identically cannot help.
MAX_COST_USD = 0.0  # hard cap on cumulative Claude spend; 0.0 = no dollar limit

# ---- TEST / GATE COMMANDS (the objective half of verification) ----
# Deterministic commands the driver runs after each execute. ALL must pass; their
# combined pass/fail is the ground truth handed to the verifier. Empty list = skip
# and judge on the diff alone. A list lets you gate on lint + build + test as
# separate, independently-reported checks.
TEST_COMMANDS = []  # e.g. ["ruff check .", "pytest -q"]  |  ["swift build", "swift test"]

# ---- MODES (set via CLI; see parse_cli_overrides) ----
DRY_RUN = False  # --dry-run: print every command + prompt, but run/spend/edit nothing
FRESH = False  # --fresh: ignore any resume checkpoint and start the run from scratch

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
STATE_FILE = "state.json"  # basename under WORK_DIR — the resume checkpoint
STATE_VERSION = 1  # bump when the state.json shape changes; older files are ignored

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


# Optional ANSI colour for the terminal — off when stdout isn't a TTY or NO_COLOR is
# set, so piped/captured output stays plain (file writes never go through here).
# Cosmetic only; computed once since stdout doesn't change under us.
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_PREFIX_COLOR = {"✓": "32", "✗": "31", "!": "33", "↻": "36", "±": "35"}


def _c(text, code):
    return f"\033[{code}m{text}\033[0m" if (_USE_COLOR and code) else text


def log(msg, *, prefix="·"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {_c(prefix, _PREFIX_COLOR.get(prefix))} {msg}", flush=True)


def banner(text):
    rule = "=" * 72
    print("\n" + _c(rule, "36"), flush=True)
    print(f"  {_c(text, '1')}", flush=True)
    print(_c(rule, "36"), flush=True)


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
    output until its timeout). Closing stdin gives them an immediate EOF.

    Resolve cmd[0] on PATH ourselves (shutil.which respects Windows PATHEXT) and
    pass the full path. subprocess(shell=False) uses CreateProcess on Windows,
    which does NOT search PATHEXT — so an npm shim like `claude.cmd` is invisible
    by the bare name `claude`, even though it runs fine when typed. This mirrors
    the resolution `doctor` already trusts (_probe_tool), so a tool doctor reports
    as present actually launches."""
    name = cmd[0]
    exe = shutil.which(name)
    if exe is None:
        raise FatalError(f"command not found: {name} — is it installed and on PATH?")
    cmd = [exe, *cmd[1:]]
    # Pin UTF-8 for stdin/stdout/stderr. The prompts (and Claude's output) are UTF-8
    # and contain non-ASCII (→, em dashes, smart quotes); without this, text mode
    # uses the platform default — cp1252 on Windows — and the stdin write of the
    # prompt raises UnicodeEncodeError on the first such character. errors="replace"
    # keeps a stray undecodable byte in a child's output from crashing the loop.
    kwargs = dict(
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if stdin is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin
    try:
        proc = subprocess.run(cmd, **kwargs)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        raise StepError(f"command timed out after {timeout}s: {name} ...")
    except FileNotFoundError:
        raise FatalError(f"command not found: {name} — is it installed and on PATH?")


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
    cmd, stdin_text = build_claude_argv(
        prompt,
        model=model,
        system_prompt_file=system_prompt_file,
        allowed_tools=allowed_tools,
        bare=bare,
        skip_permissions=skip_permissions,
    )
    rc, out, err = run(cmd, timeout=timeout, stdin=stdin_text)
    return parse_claude_envelope(out, err, rc)


# Windows: the `claude` CLI is an npm `.cmd` shim, which subprocess runs through
# cmd.exe; cmd.exe ends a command at the first newline, so any argv element holding
# a multi-line value (the `-p` prompt, or --append-system-prompt's contents) arrives
# truncated at its first '\n'. The CLI has no *-system-prompt-file flag (verified
# against claude 2.1.x), so on Windows we route BOTH the user prompt and the
# system-prompt-file contents through STDIN — `claude -p` reads the prompt from
# stdin when no positional prompt is given — and omit --append-system-prompt. This
# marker separates the folded-in system instructions from the user request.
WIN_STDIN_PROMPT_SEP = "===== END SYSTEM INSTRUCTIONS — USER REQUEST FOLLOWS ====="


def _running_on_windows():
    return os.name == "nt"


def build_claude_argv(
    prompt,
    *,
    model,
    system_prompt_file,
    allowed_tools,
    bare=False,
    skip_permissions=False,
    windows=None,
):
    """Assemble the `claude` argv for a headless one-shot, plus its STDIN payload.
    Returns (argv, stdin_text). On macOS/Linux stdin_text is None and the prompt is
    the `-p` positional with the system-prompt-file contents passed via
    --append-system-prompt — unchanged. On Windows both are routed through stdin and
    --append-system-prompt is dropped (see WIN_STDIN_PROMPT_SEP), because cmd.exe
    truncates multi-line argv. Pure except for reading the system-prompt file.
    `windows` overrides the platform check (tests). Shared by run_claude and the
    --dry-run preview so the two can never drift."""
    if windows is None:
        windows = _running_on_windows()
    sys_text = read_file(system_prompt_file) if system_prompt_file else ""
    cmd = ["claude", "-p"]
    stdin_text = None
    if windows:
        cmd += ["--model", model, "--output-format", "json"]
        stdin_text = (
            f"{sys_text}\n\n{WIN_STDIN_PROMPT_SEP}\n\n{prompt}" if sys_text else prompt
        )
    else:
        cmd += [prompt, "--model", model, "--output-format", "json"]
    if bare:
        cmd.append("--bare")
    if system_prompt_file and not windows:
        cmd += ["--append-system-prompt", sys_text]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    elif allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    return cmd, stdin_text


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
    """Snapshot the repo's current state as a git tree (via the index), WITHOUT
    committing, so the loop can diff the executor's changes against the start of the
    run. `write-tree` works whether or not the repo has any commits yet — a fresh
    `git init` with no HEAD is fine (unlike `rev-parse HEAD`) — and it isolates the
    executor's edits from any pre-existing uncommitted work. Returns a tree SHA, used
    only as the left side of `git diff --cached`; never checked out or reset."""
    git("add", "-A")
    return git("write-tree").strip()


def staged_diff_against(baseline):
    """Stage everything and return the
    cumulative diff vs the loop's start snapshot (see capture_baseline).
    Staging is how we capture new/untracked files in the diff;
    we never commit."""
    git("add", "-A")
    return git("diff", "--cached", baseline)


def changed_files(baseline):
    """(status, path) for everything staged vs baseline — the files the executor has
    touched this run. Reuses the index `staged_diff_against` already populated (call
    it after that). Rename lines (R100\\told\\tnew) collapse to (status, new-path)."""
    out = git("diff", "--cached", "--name-status", baseline).strip()
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    return rows


def test_shell_argv(cmd, *, windows=None, have_bash=None):
    """Wrap a gate command for execution. Prefer `bash -lc` on every platform so one
    --test-command stays portable; when bash isn't on PATH, fall back to the
    platform's always-present shell so the gate still runs: `cmd /c` on Windows (no
    Git Bash/WSL), `sh -c` on a bash-less POSIX host (Alpine, distroless, busybox).
    Those shells' semantics differ from bash, so complex syntax may not behave the
    same. Pure: the platform and bash probes are injectable for tests."""
    if windows is None:
        windows = _running_on_windows()
    if have_bash is None:
        have_bash = shutil.which("bash") is not None
    if have_bash:
        return ["bash", "-lc", cmd]
    if windows:
        return ["cmd", "/c", cmd]
    return ["sh", "-c", cmd]


def run_tests():
    """Run each configured gate in order; ALL must pass. Returns
    (ran: bool, passed: bool, output: str). The output's FIRST LINE is the overall
    "TESTS: PASSED/FAILED/SKIPPED" status the verifier keys on, followed by a
    per-gate breakdown. A gate timeout raises StepError and stops the loop."""
    gates = [c for c in TEST_COMMANDS if c.strip()]
    if not gates:
        return False, True, "TESTS: SKIPPED\n(no test/gate command configured)"
    have_bash = shutil.which("bash") is not None
    if not have_bash:
        via = "cmd.exe" if _running_on_windows() else "sh"
        fix = "Install Git Bash or use WSL" if _running_on_windows() else "Install bash"
        log(
            f"bash not on PATH — running the test gate via {via}; shell-specific "
            f"syntax may differ. {fix} for parity.",
            prefix="!",
        )
    all_passed = True
    sections = []
    for cmd in gates:
        rc, out, err = run(test_shell_argv(cmd, have_bash=have_bash), timeout=CURSOR_TIMEOUT)
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

    changed = changed_files(baseline)
    if changed:
        shown = "  ".join(f"{s} {p}" for s, p in changed[:12])
        extra = f"  (+{len(changed) - 12} more)" if len(changed) > 12 else ""
        log(f"files changed ({len(changed)}): {shown}{extra}", prefix="±")

    ran, passed, test_out = run_tests()
    write_file(os.path.join(WORK_DIR, "test_output.txt"), test_out)
    log(
        f"diff={len(diff)} chars | tests: {'PASSED' if passed else 'FAILED' if ran else 'SKIPPED'}"
    )

    if not diff.strip():
        # Executor changed nothing at all vs baseline — nothing to verify.
        return {
            "status": "blocked",
            "reasons": ["Executor produced no changes against the baseline snapshot."],
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


def _extract_json_object(text):
    """Pull the first balanced JSON object out of text that may be wrapped in prose
    — for a model that narrates around the JSON ("All criteria pass.\n\n{...}")
    despite the JSON-only contract. Tries each '{' as a start via raw_decode (which
    ignores trailing text) and returns the first that decodes to an object, else
    None. Pure."""
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    return None


def parse_verdict(raw_result):
    """Parse the verifier's raw stdout into a verdict dict: tolerate ```json fences
    and a prose preamble/epilogue around the JSON (a strong verify model sometimes
    narrates despite the JSON-only contract), require an object carrying a 'status'
    field. Pure (no subprocess, no file writes), so it can be unit-tested against
    sample and malformed output. Raises StepError on anything malformed (the caller
    has already saved the raw text to verify_raw.txt for inspection)."""
    raw = strip_fences(raw_result)
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        verdict = _extract_json_object(raw)  # recover JSON wrapped in narration
    if not isinstance(verdict, dict):
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


def _as_list(v):
    """Coerce a JSON field to a list: None -> [], a lone object/string -> [it].
    The triage model is told to emit arrays but sometimes returns a single value."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def normalize_question(q):
    """Coerce one clarity question to the {id, question, why} shape the gate prints
    and serializes. triage.md specifies that schema, but the model sometimes returns
    a bare string or an object keyed differently (title/text/description). Pure, so
    it's unit-tested; without it a non-dict question crashes the gate on `q.get`."""
    if isinstance(q, dict):
        text = q.get("question") or q.get("title") or q.get("text") or ""
        why = q.get("why") or q.get("description") or ""
        return {"id": str(q.get("id") or "?"), "question": str(text), "why": str(why)}
    return {"id": "?", "question": str(q), "why": ""}


def normalize_issue(i):
    """Coerce one clarity issue to a readable string. Issues are meant to be strings;
    the model sometimes returns finding-shaped objects, which would otherwise print
    as raw `{...}` reprs. Pure."""
    if isinstance(i, dict):
        parts = [str(i[k]) for k in ("title", "issue", "text", "description") if i.get(k)]
        return " — ".join(parts) if parts else json.dumps(i, ensure_ascii=False)
    return str(i)


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

        issues = [normalize_issue(i) for i in _as_list(parsed.get("issues"))]
        questions = [normalize_question(q) for q in _as_list(parsed.get("questions"))]
        assumptions = [str(a) for a in _as_list(parsed.get("assumptions_if_unanswered"))]

        if not questions:
            # Not-ready but nothing to ask — the model set ready=false yet returned no
            # questions (or keyed them wrong). Don't trap the user with an empty
            # halt/file: if there's nothing actionable at all, warn and proceed (same
            # spirit as the non-JSON fallback); if there are issues, surface & halt.
            if not issues:
                log(
                    "clarity gate said 'not ready' but gave no questions or issues — "
                    "proceeding as if clear.",
                    prefix="!",
                )
                return cost
            raise NeedsClarification(questions, issues, assumptions)

        log(f"task not ready (round {round_no}):", prefix="!")
        for i in issues:
            log(f"  issue: {i}")

        if not (INTERACTIVE_CLARIFY and sys.stdin.isatty()):
            raise NeedsClarification(questions, issues, assumptions)  # unattended -> halt

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
    log(
        "not asking interactively (no terminal to prompt in, or the clarification "
        "rounds ran out), so the open questions are below and saved to a file.",
        prefix="·",
    )
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
    # Absolute path: the file is written to cwd after os.chdir(REPO_ROOT) and is
    # gitignored, so a bare relative name leaves users hunting for it (esp. on
    # Windows / inside an IDE panel where the cwd isn't obvious).
    log(f"questions also written to {os.path.abspath(CLARIFY_NEEDED_FILE)}")
    log(f"claude spend : ${total_cost:.4f}")
    sys.exit(2)


# ============================================================================
# Author subcommand (turn a requirements/Jira story into a task.md)
# ============================================================================
#
# Standalone helper, NOT part of the plan/execute/verify loop: it reads a raw
# requirements/Jira story (--from FILE or stdin), runs a bounded interactive Claude
# loop that DRAFTS a task.md, and the driver writes the file. The author agent's
# readiness bar is triage.md's, so the authored task then sails through the clarity
# gate. Mirrors clarify_gate's interaction model, with one deliberate difference:
# clarify GATES spend and halts when unclear; author PRODUCES a file, so on a non-TTY
# run (or exhausted rounds) it writes the best-effort draft instead of halting.


def read_story(source):
    """Raw requirements text from --from FILE, or from stdin when source is None/'-'
    and stdin isn't a TTY. Raises FatalError (before any Claude spend) on a missing
    file, no available source, or empty input."""
    if source and source != "-":
        if not os.path.exists(source):
            raise FatalError(f"--from {source}: file not found")
        text = read_file(source)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise FatalError(
            "no requirements input: pass --from <path>, or pipe a story on stdin "
            "(e.g. `pbpaste | python driver.py author`)."
        )
    if not text.strip():
        raise FatalError("requirements input is empty — nothing to author a task from.")
    return text


def author_instruction():
    """The AUTHOR step's user prompt. Pure (modulo os.path.exists); shared by
    author_step and the --dry-run preview so the two can't drift. Names the on-disk
    story + answers scratch files the stateless agent reads, mirroring how
    triage_instruction points the gate at task.md/context.md."""
    # Forward-slash prompt paths (Claude's Read tool normalizes '/' on Windows), like
    # verify_instruction/triage_instruction — the driver's real file I/O uses os.path.join.
    story = f"{WORK_DIR}/{AUTHOR_STORY_FILE}"
    answers = f"{WORK_DIR}/{AUTHOR_ANSWERS_FILE}"
    files = [
        f"{story} (the raw requirements/Jira story to turn into a task.md)",
        f"{answers} (answers to your earlier questions — authoritative; a "
        "header-only file means none yet)",
    ]
    if os.path.exists(CONTEXT_FILE):
        files.append(
            f"{CONTEXT_FILE} (architecture map — ground the criteria in real paths)"
        )
    return (
        "Draft a single focused task.md from the requirements. Read: "
        + "; ".join(files)
        + ". Output the author JSON exactly per your instructions — JSON only."
    )


def normalize_author_result(parsed):
    """Coerce the author agent's JSON into (draft, questions, assumptions, multi_note,
    ready). Reuses normalize_question/_as_list and degrades safely so loose model
    output can't crash the loop. Pure, so it's unit-tested."""
    draft = str(parsed.get("draft_task_md") or parsed.get("task_md") or "")
    questions = [normalize_question(q) for q in _as_list(parsed.get("questions"))]
    assumptions = [str(a) for a in _as_list(parsed.get("assumptions_if_unanswered"))]
    note = str(parsed.get("multi_unit_note") or "")
    if not note and parsed.get("multi_unit"):
        note = "This story spans multiple units — split it and loop `run` per unit."
    return draft, questions, assumptions, note, bool(parsed.get("ready"))


def is_affirmative(raw):
    """True for a yes-ish answer to a [y/N] prompt. Pure, so it's unit-tested."""
    return raw.strip().lower() in ("y", "yes")


def author_step():
    """Ask the author agent for a task.md draft + any clarifying questions. Returns
    (parsed_dict, cost). Unlike the clarity gate this does NOT fail open — the whole
    point of this step is the draft, so a garbled round yields {} and the caller falls
    back to the last good draft (or stops if there never was one)."""
    res = run_claude(
        author_instruction(),
        model=AUTHOR_MODEL_NAME,
        system_prompt_file=os.path.join(PROMPTS_DIR, "author.md"),
        allowed_tools=AUTHOR_ALLOWED_TOOLS,
    )
    write_file(os.path.join(WORK_DIR, "author_raw.txt"), res["result"])
    raw = strip_fences(res["result"])
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = _extract_json_object(raw)  # recover JSON wrapped in narration
    return (parsed if isinstance(parsed, dict) else {}), res["cost"]


def _write_task_or_refuse(draft):
    """Write the drafted task.md, but never clobber an existing one silently (the
    non-destructive invariant). In a TTY, confirm the overwrite; otherwise refuse and
    tell the user how to proceed. --force skips the guard."""
    content = draft.rstrip() + "\n"
    if os.path.exists(TASK_FILE) and not AUTHOR_FORCE:
        if _interactive():
            if not is_affirmative(input(f"{TASK_FILE} already exists. Overwrite? [y/N] ")):
                log(
                    f"kept existing {TASK_FILE}. Re-run with --task <other path> or "
                    f"--force to replace it.",
                    prefix="!",
                )
                sys.exit(1)
        else:
            log(
                f"{TASK_FILE} already exists; refusing to overwrite non-interactively. "
                f"Pass --task <path> to write elsewhere, or --force to replace it.",
                prefix="✗",
            )
            sys.exit(1)
    write_file(TASK_FILE, content)


def author():
    """The `author` subcommand: raw story -> task.md, asking clarifying questions in a
    TTY. Self-contained (sys.exits like doctor/dry_run), so it returns before the
    run loop is ever reached."""
    banner(f"AUTHOR — draft {TASK_FILE} from requirements  ({AUTHOR_MODEL_NAME})")
    if not os.path.exists(os.path.join(PROMPTS_DIR, "author.md")):
        log(
            f"missing {os.path.join(PROMPTS_DIR, 'author.md')} — run "
            f"`python driver.py doctor`.",
            prefix="✗",
        )
        sys.exit(1)

    if DRY_RUN:  # print the command + prompt only — create/write nothing
        _preview_claude(
            "AUTHOR",
            model=AUTHOR_MODEL_NAME,
            prompt=author_instruction(),
            system_prompt_file=os.path.join(PROMPTS_DIR, "author.md"),
            allowed_tools=AUTHOR_ALLOWED_TOOLS,
        )
        banner("DRY RUN — end (nothing was executed)")
        sys.exit(0)

    # Read the story BEFORE creating any scratch (a bad --from should fail with no
    # side effects), then make the scratch dir for the story/answers/raw artifacts.
    story = read_story(AUTHOR_STORY_SRC)
    os.makedirs(os.path.join(REPO_ROOT, WORK_DIR), exist_ok=True)
    if len(story) > 64 * 1024:
        log(
            f"story is large ({len(story) // 1024} KB) — the author focuses on one "
            f"unit and will flag a split if it spans several.",
            prefix="!",
        )
    write_file(os.path.join(WORK_DIR, AUTHOR_STORY_FILE), story)
    # Fresh answers file each invocation (truncate, never delete) so a prior author
    # session's answers never leak into this one.
    write_file(os.path.join(WORK_DIR, AUTHOR_ANSWERS_FILE), "# Author answers\n")

    cost = 0.0
    last_draft = ""
    open_questions, assumptions, multi_note = [], [], ""
    for round_no in range(1, AUTHOR_MAX_ROUNDS + 1):
        parsed, c = author_step()
        cost += c
        draft, questions, assumptions, multi_note, ready = normalize_author_result(parsed)
        if draft.strip():
            last_draft = draft

        if ready or not questions:
            open_questions = []
            break

        if not (INTERACTIVE_AUTHOR and _interactive()):
            open_questions = questions  # non-TTY: write best-effort below, don't halt
            break

        log(f"draft needs input (round {round_no}):", prefix="?")
        block = ["", f"## Author round {round_no}"]
        for q in questions:
            print(f"\nQ ({q.get('id', '?')}): {q.get('question', '')}")
            if q.get("why"):
                print(f"   (why it matters: {q['why']})")
            ans = input("   your answer > ").strip()
            block += [f"- Q ({q.get('id', '?')}): {q.get('question', '')}", f"  A: {ans}"]
        existing = read_file(os.path.join(WORK_DIR, AUTHOR_ANSWERS_FILE))
        write_file(
            os.path.join(WORK_DIR, AUTHOR_ANSWERS_FILE),
            existing + "\n".join(block) + "\n",
        )
        log("answers recorded; regenerating draft.", prefix="↻")
    else:
        open_questions = questions  # rounds exhausted — write best-effort below

    if not last_draft.strip():
        log(
            f"no usable draft produced (see {WORK_DIR}/author_raw.txt).",
            prefix="✗",
        )
        log(f"claude spend : ${cost:.4f}")
        sys.exit(1)

    _write_task_or_refuse(last_draft)

    banner("AUTHOR — done")
    log(f"wrote {os.path.abspath(TASK_FILE)}", prefix="✓")
    if multi_note:
        log(multi_note, prefix="!")
    if open_questions:
        log(
            "open questions remain — the run's clarity gate will re-check before "
            "planning. Sharpen task.md or answer them, then run the loop:",
            prefix="?",
        )
        for q in open_questions:
            log(f"  - [{q.get('id', '?')}] {q.get('question', '')}")
        for a in assumptions:
            log(f"  if unanswered, the task assumes: {a}", prefix="·")
    log(f"review {TASK_FILE}, then: python driver.py  (or `doctor` / `--dry-run` first)")
    log(f"claude spend : ${cost:.4f}")
    sys.exit(0)


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
# Resume checkpoint (.loop/state.json) — so a failed run doesn't re-buy work
#
# Without this, a second `python driver.py` after a crash re-runs the clarity gate,
# re-plans, and re-executes. Worse than the wasted spend: capture_baseline() would
# snapshot a tree that ALREADY contains the executor's partial edits, so they vanish
# from `git diff --cached <baseline>` and the verifier scores a truncated diff —
# failing criteria that were in fact met, and buying yet another iteration.
#
# The driver stays the only stateful actor (agents remain stateless one-shots); this
# is simply that state surviving the process. Decisions are pure (resume_decision);
# all file/git I/O happens in the callers and is passed in.
# ============================================================================

# The loop's phases, in order. Backing up means moving one step left.
PHASE_ORDER = ("plan", "execute", "verify")

# Which steps a resume at each phase can skip, in loop order.
SKIPPED_BY_PHASE = {
    "plan": ("clarify",),
    "execute": ("clarify", "plan"),
    "verify": ("clarify", "plan", "execute"),
}
SKIP_REASON = {
    "clarify": "passed on identical inputs",
    "plan": f"{PLAN_FILE} still valid",
    "execute": "edits already applied",
}


def input_fingerprint(task_text, context_text, clarify_text):
    """One hash over the three human inputs the clarity gate and planner read. If it
    changes, every conclusion drawn from them is stale — that's what invalidates a
    checkpoint when someone edits task.md between runs. Pure."""
    blob = "\x00".join((task_text, context_text, clarify_text))
    return hashlib.sha256(blob.encode()).hexdigest()


def resume_decision(state, *, fingerprint, head, fresh):
    """Decide what a new invocation may reuse from a prior run. PURE — the caller
    reads state.json, hashes the inputs, and asks git for HEAD, then passes them in.

    Returns (action, reason, state): action is "resume" (re-enter at state["phase"]
    of state["iteration"]) or "fresh" (start over; state is None). Every rejection
    carries a reason, because silently discarding a checkpoint is how a user ends up
    paying twice and not knowing why."""
    if fresh:
        return ("fresh", "--fresh requested", None)
    if not state:
        return ("fresh", "no checkpoint from a prior run", None)
    if state.get("version") != STATE_VERSION:
        return ("fresh", "checkpoint was written by a different driver version", None)
    if state.get("phase") not in SKIPPED_BY_PHASE:
        return ("fresh", "checkpoint names no phase to resume at", None)
    if not state.get("baseline"):
        return ("fresh", "checkpoint has no baseline snapshot", None)
    if state.get("inputs") != fingerprint:
        return (
            "fresh",
            f"{TASK_FILE} / {CONTEXT_FILE} / {CLARIFY_FILE} changed since that run",
            None,
        )
    if state.get("head") != head:
        # A commit, reset, or branch switch moves what the baseline tree means, so the
        # diff it anchors would be nonsense. Cheaper to re-plan than to verify a lie.
        return ("fresh", "git HEAD moved since that run", None)
    it = state.get("iteration")
    if not isinstance(it, int) or it < 1:
        return ("fresh", "checkpoint has no usable iteration number", None)
    return ("resume", f"iteration {it}, stopped in {state['phase']}", state)


def resume_entry(phase, *, attempts, have_plan, have_diff, max_attempts=None):
    """Where a resume should ACTUALLY re-enter the loop. PURE — the caller probes the
    artifacts and passes the answers in.

    The recorded phase says where the run died. It does NOT say that re-entering
    there can work, and re-entering a step that is bound to fail is exactly the spend
    this checkpoint exists to avoid. Two things override it, both backing the resume
    UP the pipeline so the next attempt differs from the last:

      - **A missing input.** EXECUTE's only input is plan.md; VERIFY's is the staged
        diff. Resuming into a step whose input isn't there buys the same failure over
        again — or, for VERIFY, buys a verdict of "nothing changed" and a whole
        iteration to fix it.
      - **A phase that keeps dying.** Retrying a deterministic executor failure a
        third time changes nothing; a different plan might. Same for a verifier that
        can't be parsed: a different diff is the only new input available.

    Returns (phase, notes). Every step back gets a note, because a resume that
    quietly decides to re-plan is a resume that spends money without saying why."""
    if max_attempts is None:
        max_attempts = MAX_PHASE_ATTEMPTS
    notes = []
    cur = phase
    for _ in range(len(PHASE_ORDER)):  # each pass moves left or stops — bounded
        i = PHASE_ORDER.index(cur)
        if cur == "verify" and not have_diff:
            notes.append("no staged edits to verify — backing up to EXECUTE")
            cur = "execute"
            continue
        if cur == "execute" and not have_plan:
            notes.append(f"{PLAN_FILE} is missing or empty — backing up to PLAN")
            cur = "plan"
            continue
        tries = attempts.get(cur, 0)
        if tries >= max_attempts and i > 0:
            prev = PHASE_ORDER[i - 1]
            notes.append(
                f"{cur.upper()} failed {tries}x — backing up to {prev.upper()}; "
                f"retrying it unchanged would only fail again"
            )
            cur = prev
            continue
        break
    return cur, notes


def plan_artifact_ok():
    """Does plan.md exist with content? It is EXECUTE's only input, so 'skip PLAN,
    the plan is still valid' has to be checked rather than assumed."""
    try:
        return bool(read_file(os.path.join(REPO_ROOT, PLAN_FILE)).strip())
    except FileNotFoundError:
        return False


def has_staged_edits(baseline):
    """Did the executor actually change anything vs the run's baseline? VERIFY has
    nothing to score without it. Stages (like every other diff call here); never
    commits."""
    try:
        return bool(staged_diff_against(baseline).strip())
    except StepError:
        return False


def state_path():
    return os.path.join(REPO_ROOT, WORK_DIR, STATE_FILE)


def read_state():
    """The prior run's checkpoint, or None. A missing, unreadable, or corrupt file is
    simply "no checkpoint" — never a crash, since this runs before any real work."""
    try:
        with open(state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_state(**fields):
    """Checkpoint the run. Written via a temp file + os.replace so a driver killed
    mid-write leaves the previous checkpoint intact rather than half a JSON file."""
    os.makedirs(os.path.join(REPO_ROOT, WORK_DIR), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": STATE_VERSION, **fields}, f, indent=2)
    os.replace(tmp, state_path())


def clear_state():
    """Drop the checkpoint — on a pass (the run is over) or when it can't be used."""
    try:
        os.remove(state_path())
    except OSError:
        pass


def current_fingerprint():
    """input_fingerprint over the three files as they are now. Missing files hash as
    empty, so creating a previously-absent clarifications.md invalidates correctly."""

    def _read(p):
        try:
            return read_file(os.path.join(REPO_ROOT, p))
        except FileNotFoundError:
            return ""

    return input_fingerprint(_read(TASK_FILE), _read(CONTEXT_FILE), _read(CLARIFY_FILE))


def current_head():
    """HEAD's sha, or "" for a repo with no commits yet (which write-tree still
    handles, so an empty string is a legitimate value to record and compare)."""
    try:
        return git("rev-parse", "HEAD").strip()
    except StepError:
        return ""


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
        for p in ("plan.md", "triage.md", "verify.md", "execute.md", "author.md")
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
    argv, stdin_text = build_claude_argv(
        prompt,
        model=model,
        system_prompt_file=system_prompt_file,
        allowed_tools=allowed_tools,
        bare=bare,
        skip_permissions=skip_permissions,
    )
    subs = {prompt: "<USER PROMPT ↓>", sys_content: f"<{system_prompt_file} contents>"}
    if stdin_text is None:
        log(
            f"system prompt: {system_prompt_file} "
            f"({len(sys_content)} chars, via --append-system-prompt)"
        )
        log(f"argv: {_render_argv(argv, subs)}")
        print("\n  user prompt:\n" + _indent(prompt) + "\n", flush=True)
    else:
        # Windows: prompt + system prompt are folded into STDIN (cmd.exe truncates
        # multi-line argv); argv carries no prompt/system-prompt tokens.
        log(
            f"system prompt: {system_prompt_file} "
            f"({len(sys_content)} chars, folded into STDIN — Windows)"
        )
        log(f"argv: {_render_argv(argv, subs)}  < STDIN")
        print(
            "\n  STDIN (system instructions + user prompt):\n"
            + _indent(stdin_text)
            + "\n",
            flush=True,
        )


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
        shell = " ".join(test_shell_argv("CMD")[:-1])  # "bash -lc" / "cmd /c" / "sh -c"
        log(f"ALL must pass; each runs via `{shell}`; the verifier sees the result.")
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
    if command == "author":
        try:
            author()  # drafts task.md from a story, then exits (never enters the loop)
        except StepError as e:  # bad/empty/missing --from input — fail cleanly
            log(str(e), prefix="✗")
            sys.exit(1)
    if DRY_RUN:
        dry_run()  # prints the planned commands and exits
    try:
        preflight()
    except StepError as e:  # includes FatalError (missing files, not a repo)
        log(str(e), prefix="✗")
        sys.exit(1)

    total_cost = 0.0  # Claude spend only; non-Claude executors bill separately
    prev_verdict = None
    last_stall = None
    stall_count = 0
    resume_phase = None
    start_iter = 1
    attempts = {}  # phase -> consecutive entries that never completed

    # Can we pick up where a previous invocation died, instead of re-buying the
    # clarity gate, the plan, and possibly the whole executor run?
    fingerprint = current_fingerprint()
    try:
        head = current_head()
    except StepError as e:  # a broken git repo — fail cleanly, no traceback
        log(str(e), prefix="✗")
        sys.exit(1)
    prior = read_state()
    action, why, prior = resume_decision(
        prior, fingerprint=fingerprint, head=head, fresh=FRESH
    )

    if action == "resume":
        banner("RESUMING")
        log(f"{WORK_DIR}/{STATE_FILE}: {why}")
        baseline = prior["baseline"]
        start_iter = prior["iteration"]
        total_cost = float(prior.get("total_cost") or 0.0)
        prev_verdict = prior.get("prev_verdict")
        stall = prior.get("stall") or {}
        last_stall = stall.get("sig")
        stall_count = int(stall.get("count") or 0)
        attempts = dict(prior.get("attempts") or {})
        log(f"inputs unchanged — reusing baseline {baseline[:10]}")
        # The recorded phase is where we died; this is where it's worth restarting,
        # given what the artifacts show and how prior attempts at it went.
        resume_phase, notes = resume_entry(
            prior["phase"],
            attempts=attempts,
            have_plan=plan_artifact_ok(),
            have_diff=has_staged_edits(baseline),
        )
        for note in notes:
            log(note, prefix="↻")
        for step in SKIPPED_BY_PHASE[resume_phase]:
            log(f"skipping {step.upper():<8}({SKIP_REASON[step]})")
        cap_note = f" / ${MAX_COST_USD:.2f} cap" if MAX_COST_USD > 0 else ""
        # Carried forward so --max-cost-usd/--max-iterations bound the WORK, not each
        # invocation — otherwise a crash-loop silently resets both caps to zero.
        log(f"carried spend: ${total_cost:.4f}{cap_note}")
    else:
        if read_state():  # there was a checkpoint; say why we're not using it
            log(f"ignoring the checkpoint — {why}", prefix="↻")
        clear_state()

        # GUARDRAIL — clarity gate: never plan against a vague or missing task.
        try:
            total_cost += clarify_gate()
        except NeedsClarification as nc:
            halt_needs_clarification(nc, total_cost)  # writes file, prints, exits(2)
        except StepError as e:
            log(str(e), prefix="✗")
            sys.exit(1)

        try:
            baseline = capture_baseline()
        except StepError as e:  # a broken git repo — fail cleanly, no traceback
            log(str(e), prefix="✗")
            sys.exit(1)
    log(f"baseline snapshot: {baseline[:10]}  | max_iterations={MAX_ITERATIONS}")
    cap = f"${MAX_COST_USD:.2f}" if MAX_COST_USD > 0 else "none"
    log(
        f"models: clarify={CLARIFY_MODEL_NAME} plan={PLAN_CLAUDE_MODEL_NAME} "
        f"verify={VERIFICATION_CLAUDE_MODEL_NAME}  |  executor="
        f"{EXECUTOR_BACKEND}:{IMPLEMENTATION_MODEL_NAME}  |  cost cap={cap}"
    )

    final_status = "incomplete"

    def enter(phase, iteration):
        """Record the phase we're ABOUT to run, with the spend banked so far, so a
        crash inside it resumes there rather than at iteration 1. Also counts the
        attempt: a phase entered MAX_PHASE_ATTEMPTS times without ever completing is
        one the next resume backs away from. Reads the enclosing locals at call time,
        so each call captures the current cost/verdict/stall."""
        attempts[phase] = attempts.get(phase, 0) + 1
        write_state(
            baseline=baseline,
            head=head,
            inputs=fingerprint,
            iteration=iteration,
            phase=phase,
            total_cost=total_cost,
            prev_verdict=prev_verdict,
            stall={"sig": last_stall, "count": stall_count},
            attempts=attempts,
        )

    for it in range(start_iter, MAX_ITERATIONS + 1):
        if MAX_COST_USD > 0 and total_cost >= MAX_COST_USD:
            final_status = "cost_exhausted"
            banner(
                f"STOPPED — Claude spend ${total_cost:.4f} hit cap ${MAX_COST_USD:.2f}"
            )
            break
        # A resumed phase applies to the first iteration only; every later one runs
        # the full plan → execute → verify.
        phase, resume_phase = resume_phase or "plan", None
        try:
            # A completed phase resets its attempt count — the next resume only backs
            # away from a phase that has never got through.
            if phase == "plan":
                enter("plan", it)
                total_cost += plan_step(it, prev_verdict)
                attempts["plan"] = 0
            if phase in ("plan", "execute"):
                enter("execute", it)
                total_cost += execute_step(it)
                attempts["execute"] = 0
            enter("verify", it)
            verdict, vcost = verify_step(it, baseline)
            attempts["verify"] = 0
            total_cost += vcost
            log(
                f"cumulative Claude spend: ${total_cost:.4f}"
                + (f" / ${MAX_COST_USD:.2f} cap" if MAX_COST_USD > 0 else "")
            )
        except StepError as e:
            banner("STOPPED — hard failure")
            log(str(e), prefix="✗")
            log(
                f"re-run to resume from here — {WORK_DIR}/{STATE_FILE} holds the "
                f"checkpoint (--fresh to start over)",
                prefix="↻",
            )
            final_status = "error"
            break

        status = verdict.get("status")

        if status == "pass":
            final_status = "pass"
            # The run is over, so the checkpoint would only mislead the next one.
            # Every other exit keeps it: after a human fixes what blocked or stalled
            # the loop, re-running re-verifies the tree instead of re-planning it.
            clear_state()
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
    if final_status != "pass":
        log(
            f"resume       : re-run the same command to continue from "
            f"{WORK_DIR}/{STATE_FILE} (--fresh to start over)"
        )
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


def resolve_model(raw, suggestions, default):
    """Parse a model-picker answer. Pure (no I/O) so it's unit-tested. Unlike
    resolve_choice, ANY typed string is accepted (a model may be a pinned id not in
    the suggestion list). Empty -> default; a 1-based index into suggestions -> that
    model; an out-of-range number -> None (re-ask); any other text -> that slug."""
    raw = raw.strip()
    if not raw:
        return default
    if raw.isdigit():
        i = int(raw)
        return suggestions[i - 1] if 1 <= i <= len(suggestions) else None
    return raw


def _interactive():
    """True only when we can both ask and be answered — never prompt a headless/CI
    run (it would block on input that never comes, the very hang we avoid elsewhere)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


# Suggested Claude models offered as a numbered menu in the interactive wizard; any
# other slug (e.g. a pinned id) can still be typed. Not exhaustive by design.
CLAUDE_MODEL_CHOICES = ["opus", "sonnet", "haiku"]


def prompt_run_settings(
    a,
    *,
    executor_default,
    impl_default,
    iter_default,
    cost_default,
    clarify_default,
    plan_default,
    verify_default,
):
    """For an interactive `run`, ask for any knob NOT passed on the CLI (a flag the
    user did pass is kept as-is). Walks the pipeline: clarify/plan models, executor +
    its model, verify model, then the iteration/cost caps. Returns a settings dict.
    I/O layer only — parsing lives in the pure resolve_* helpers."""
    shown = [False]

    def intro():
        if not shown[0]:
            print("\nSome run settings weren't passed — choose them (Enter = [default]):")
            shown[0] = True

    def ask_model(label, default):
        intro()
        print(f"\n  {label}:")
        for i, m in enumerate(CLAUDE_MODEL_CHOICES, 1):
            print(f"    {i}) {m}" + ("   (default)" if m == default else ""))
        picked = None
        while picked is None:
            picked = resolve_model(
                input(
                    f"  choice [1-{len(CLAUDE_MODEL_CHOICES)} or a model slug, "
                    f"Enter={default}]: "
                ),
                CLAUDE_MODEL_CHOICES,
                default,
            )
            if picked is None:
                print("    pick a listed number or type a model slug.")
        return picked

    clarify_model = a.clarify_model
    if clarify_model is None:
        clarify_model = ask_model("Clarify-gate model", clarify_default)

    plan_model = a.plan_model
    if plan_model is None:
        plan_model = ask_model("Plan model", plan_default)

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

    verify_model = a.verify_model
    if verify_model is None:
        verify_model = ask_model("Verify model", verify_default)

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

    return {
        "clarify_model": clarify_model,
        "plan_model": plan_model,
        "executor": executor,
        "impl_model": impl_model,
        "verify_model": verify_model,
        "max_iter": max_iter,
        "max_cost": max_cost,
    }


def parse_cli_overrides():
    """Let the three model variables (and a couple of knobs) be set at launch
    without editing the file. Returns the optional subcommand ('doctor' or None).

    The model flags (`--clarify-model`/`--plan-model`/`--verify-model`) and the run
    knobs (`--executor`/`--impl-model`/`--max-iterations`/`--max-cost-usd`) all default
    to None so we can tell "unset" from "set to the default value": when unset on an
    interactive `run`, we prompt for them (prompt_run_settings walks the whole
    pipeline); otherwise we fall back to the module-constant defaults (so headless/CI
    runs and the documented multi-unit loop — which passes them — are unchanged and
    never block on input)."""
    global PLAN_CLAUDE_MODEL_NAME, CLARIFY_MODEL_NAME, IMPLEMENTATION_MODEL_NAME
    global EXECUTOR_BACKEND
    global VERIFICATION_CLAUDE_MODEL_NAME, MAX_ITERATIONS, TEST_COMMANDS, REPO_ROOT
    global MAX_COST_USD, DRY_RUN, FRESH
    global TASK_FILE, CONTEXT_FILE, WORK_DIR
    global AUTHOR_MODEL_NAME, AUTHOR_STORY_SRC, AUTHOR_FORCE
    p = argparse.ArgumentParser(description="Agentic plan/execute/verify loop.")
    p.add_argument("--version", action="version", version=f"agents-collab {__version__}")
    p.add_argument(
        "command",
        nargs="?",
        choices=["run", "doctor", "author"],
        default="run",
        help="'run' (default) the loop; 'doctor' to preflight the environment "
        "(checks git + the claude/executor CLIs and their versions) without spending; "
        "'author' to draft task.md from a requirements/Jira story (--from/stdin).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact commands and prompts each step would issue, then "
        "exit — no Claude calls, no executor, no edits, no spend.",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help=f"ignore any resume checkpoint in {WORK_DIR}/{STATE_FILE} and start the "
        "run from scratch (re-runs the clarity gate and re-plans from iteration 1).",
    )
    p.add_argument(
        "--clarify-model",
        default=None,
        help="Claude model for the clarity gate (prompts if omitted on an interactive run)",
    )
    p.add_argument(
        "--plan-model",
        default=None,
        help="Claude model for planning (prompts if omitted on an interactive run)",
    )
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
    p.add_argument(
        "--verify-model",
        default=None,
        help="Claude model for verification (prompts if omitted on an interactive run)",
    )
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
    p.add_argument(
        "--from",
        dest="story_from",
        default=None,
        help="author: the requirements/Jira story file to turn into task.md. Omit "
        "(or pass '-') to read the story from stdin when stdin isn't a TTY.",
    )
    p.add_argument(
        "--author-model",
        default=None,
        help="Claude model for the `author` step (default: sonnet).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="author: overwrite an existing --task file without confirming.",
    )
    a = p.parse_args()

    # Every model + run knob defaults to None so we can tell "unset" from "set to the
    # default": on an interactive `run` we prompt for the unset ones; otherwise each
    # falls back to its module-constant default (headless/CI/--dry-run never block).
    if a.command == "run" and not a.dry_run and _interactive():
        s = prompt_run_settings(
            a,
            executor_default=EXECUTOR_BACKEND,
            impl_default=IMPLEMENTATION_MODEL_NAME,
            iter_default=MAX_ITERATIONS,
            cost_default=MAX_COST_USD,
            clarify_default=CLARIFY_MODEL_NAME,
            plan_default=PLAN_CLAUDE_MODEL_NAME,
            verify_default=VERIFICATION_CLAUDE_MODEL_NAME,
        )
        CLARIFY_MODEL_NAME = s["clarify_model"]
        PLAN_CLAUDE_MODEL_NAME = s["plan_model"]
        VERIFICATION_CLAUDE_MODEL_NAME = s["verify_model"]
        EXECUTOR_BACKEND = s["executor"]
        IMPLEMENTATION_MODEL_NAME = s["impl_model"]
        MAX_ITERATIONS = s["max_iter"]
        MAX_COST_USD = s["max_cost"]
    else:
        CLARIFY_MODEL_NAME = (
            a.clarify_model if a.clarify_model is not None else CLARIFY_MODEL_NAME
        )
        PLAN_CLAUDE_MODEL_NAME = (
            a.plan_model if a.plan_model is not None else PLAN_CLAUDE_MODEL_NAME
        )
        VERIFICATION_CLAUDE_MODEL_NAME = (
            a.verify_model if a.verify_model is not None else VERIFICATION_CLAUDE_MODEL_NAME
        )
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
    FRESH = a.fresh
    # Author knobs — set unconditionally (the interactive run-wizard above is gated on
    # command == "run", so it never fires for `author`).
    AUTHOR_MODEL_NAME = (
        a.author_model if a.author_model is not None else AUTHOR_MODEL_NAME
    )
    AUTHOR_STORY_SRC = a.story_from
    AUTHOR_FORCE = a.force
    return a.command


if __name__ == "__main__":
    # Exit cleanly (no traceback) when a prompt — the run-settings chooser or the
    # clarify gate — is interrupted with Ctrl-C, or hits EOF (Ctrl-D / closed stdin).
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted — exiting.", file=sys.stderr)
        sys.exit(130)
    except EOFError:
        print("\nno input received (EOF) — exiting.", file=sys.stderr)
        sys.exit(1)
