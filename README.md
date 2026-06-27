# agents-collab

[![CI](https://github.com/srivaths-ahr/agents-collab/actions/workflows/ci.yml/badge.svg)](https://github.com/srivaths-ahr/agents-collab/actions/workflows/ci.yml)

A small, model-agnostic **plan → execute → verify** loop for autonomous code changes.
Claude does the thinking (planning and verification); a pluggable coding CLI does
the editing. A single Python driver is the only stateful part — it owns the
iteration budget, the stop conditions, and the file handoff between otherwise
stateless one-shot agents.

```
CLARIFY  (Claude)    is task.md clear enough to plan? if not, ask or halt
PLAN     (Claude)    task.md + context.md (+ codegraph) + last verdict  →  plan.md
EXECUTE  (executor)  plan.md (+ AGENTS.md)  →  edits files in the git workspace
VERIFY   (Claude)    git diff + test output + acceptance criteria  →  verdict.json
   └── driver reads verdict: pass → stop · fail → re-plan · blocked → human
```

The executor is swappable: **Cursor, Claude itself, OpenAI Codex, or Gemini /
Antigravity**. Planning and verification always run on Claude.

## Why

Use an expensive, long-thinking model where judgment pays off (planning and
review) and a cheap, fast model for the mechanical editing — without giving up a
human-auditable contract. Everything between agents is a file on disk (`task.md`,
`plan.md`, the git diff, `verdict.json`), so every step is inspectable and the
loop converges against objective, machine-checkable acceptance criteria.

## Requirements

- **Python 3.8+** — standard library only, no `pip install`.
- **git** — the workspace is the handoff and verification substrate.
- **Claude Code** (`claude`) — used for clarify, plan, and verify.
- **One executor CLI**, matching your chosen backend:
  `cursor-agent` · `claude` · `codex` · `gemini` · `agy` (Antigravity).
- **macOS or Linux.** The driver is portable Python, but `install.sh` is a bash
  script (`set -euo pipefail`) and the test gate runs via `bash -lc`. On **Windows,
  use WSL**, or copy the four files in by hand and run `python driver.py` directly.

Each tool authenticates through its own login or environment variable. **No
credentials are stored in this repo.**

Run `python driver.py doctor` to confirm git and the CLIs you need are installed
and print their versions before your first run.

## Install

This is a template you drop into the repository you want to work on. Clone it
once, then run the installer against your target repo:

```bash
git clone https://github.com/srivaths-ahr/agents-collab
./agents-collab/install.sh /path/to/your-repo
# or:  make -C agents-collab install TARGET=/path/to/your-repo
```

The installer copies the tool files (`driver.py`, `executors.py`, `prompts/`) and
seeds `AGENTS.md` + the `*.example` files **only if absent** — re-run it to upgrade
without clobbering your standing rules or an in-progress task. Prefer to do it by
hand? Copy `{driver.py,executors.py,prompts,AGENTS.md}` into the repo yourself.

Run the loop from inside that repo (the prompt files are read by relative path).

## Quickstart

```bash
# 1) first run scaffolds a task template, then stops
python driver.py

# 2) fill in task.md (goal + checkable acceptance criteria),
#    and add a context.md describing your codebase.
#    Starting points are provided:
#      cp task.md.example task.md
#      cp context.md.example context.md

# 3) check your environment is ready (no spend, no edits)
python driver.py doctor

# 4) preview exactly what each step will run, still without spending a cent
python driver.py --dry-run --executor codex --impl-model gpt-5.4

# 5) run the loop with your chosen models/executor
python driver.py \
  --plan-model opus \
  --executor codex --impl-model gpt-5.4 \
  --verify-model haiku \
  --test-command "ruff check ." --test-command "pytest -q" \
  --max-iterations 8 --max-cost-usd 5.00
```

`--test-command` is repeatable: pass it once per gate (lint, build, test) and
**all** must pass. `--max-cost-usd` stops the loop once cumulative Claude spend
reaches the cap (`0` = no limit).

### Before you spend: `doctor` and `--dry-run`

Two zero-cost previews, so an unfamiliar tool with auto-approved edits never
surprises you:

- **`python driver.py doctor`** — checks that `git`, `claude`, and the chosen
  executor CLI are installed (printing each one's `--version`), that you're in a
  git repo, and that the prompt files are present. Exits non-zero with a checklist
  if anything is missing — turning a mid-run "command not found" into a two-second
  report. Worth running first, since the third-party executor CLIs drift.
- **`python driver.py --dry-run`** — prints the exact command line and full prompt
  for every step (clarity gate, plan, execute, the test gates, verify) and then
  exits. No Claude calls, no executor, no edits, no spend. The argv and prompts
  come from the same builders the real run uses, so the preview can't lie.

Changes are **staged but never committed** — run on a dedicated branch or git
worktree, then review and commit (or discard) yourself.

## Configuration

Set defaults at the top of `driver.py`, or override per run:

| Flag               | Meaning                                                                         |
| ------------------ | ------------------------------------------------------------------------------- |
| `--plan-model`     | Claude model for planning (e.g. `opus`)                                         |
| `--executor`       | `cursor` · `claude` · `codex` · `gemini` · `antigravity`                        |
| `--impl-model`     | executor model slug for the chosen backend                                      |
| `--verify-model`   | Claude model for verification (e.g. `haiku`)                                    |
| `--test-command`   | deterministic gate; pass/fail is ground truth. Repeatable — all gates must pass |
| `--max-iterations` | hard cap on loop rounds                                                         |
| `--max-cost-usd`   | hard cap on cumulative Claude spend in USD (0 = no limit)                       |
| `--dry-run`        | print each step's command + prompt and exit; no calls, edits, or spend          |
| `--repo`           | path to the target git repo (default: current dir)                              |

The `doctor` subcommand (`python driver.py doctor`) takes the same `--executor` /
`--repo` flags, so it checks the exact CLIs the run you're about to launch needs.

## Executors

| Backend       | Command                                   | Context file              | Notes                                                                     |
| ------------- | ----------------------------------------- | ------------------------- | ------------------------------------------------------------------------- |
| `cursor`      | `cursor-agent -p --force`                 | `AGENTS.md`               | default                                                                   |
| `claude`      | `claude -p --permission-mode acceptEdits` | `CLAUDE.md` / `AGENTS.md` | all-Claude pairing                                                        |
| `codex`       | `codex exec --sandbox workspace-write`    | `AGENTS.md`               | shares AGENTS.md with Cursor                                              |
| `gemini`      | `gemini -p --yolo`                        | `GEMINI.md`               | **legacy CLI; needs a paid key**                                          |
| `antigravity` | `agy --headless --approve all`            | —                         | Gemini CLI's replacement; flags change fast — verify against current docs |

> Google retired the free/Pro/Ultra Gemini CLI in June 2026 in favor of
> Antigravity (`agy`). Use `gemini` only if your legacy CLI is still active on a
> paid key; otherwise use `antigravity` and confirm its current flags first.

### Compatibility matrix

These are third-party CLIs and their flags drift — that is the most common source
of breakage (see the "executor flags changed" issue template). This table records
the **last CLI version each adapter was verified against**; treat anything newer as
unverified until confirmed. Run `python driver.py doctor` to print the versions
installed on your machine, and please PR an update here when you verify a backend.

| Backend       | CLI binary     | Last verified version | Verified on | Example model slug      |
| ------------- | -------------- | --------------------- | ----------- | ----------------------- |
| `cursor`      | `cursor-agent` | _not yet pinned_      | —           | `composer-2.5`          |
| `claude`      | `claude`       | `2.1.195`             | 2026-06-27  | `opus`, `haiku`         |
| `codex`       | `codex`        | _not yet pinned_      | —           | `gpt-5.4`, `gpt-5.3-codex` |
| `gemini`      | `gemini`       | _not yet pinned_      | —           | (legacy; paid key)      |
| `antigravity` | `agy`          | _not yet pinned_      | —           | (auto-selected)         |

> `claude` is also the engine for the plan / verify / clarify steps, so its
> verified version above applies to those regardless of which executor you pick.

## Guardrails

- **Clarity gate** — a cheap Claude pass checks `task.md` before any planning
  spend. In a terminal it asks you the blocking questions and re-checks; run
  unattended, it writes them to `clarifications_needed.json` and halts.
- **Executor resilience** — transient executor failures (hang/timeout/non-zero)
  are retried with backoff; a missing CLI or bad config aborts immediately.
- **Stop conditions** — `pass` (done), `blocked` (needs a human), `stalled` (no
  progress across rounds), iteration budget or `--max-cost-usd` exhausted, or
  malformed verifier output.
- **Non-destructive** — the driver stages to compute diffs but never commits,
  resets, or deletes.

## What it costs

Two separate bills: **Claude** (plan + verify + clarify) and, if you use a
non-Claude executor, **that CLI's own account** (Cursor, Codex, Gemini) on its own
plan. The driver only meters and caps the Claude side.

Where the Claude spend goes, per loop:

- **Clarity gate** — one cheap call up front (`--verify-model`-class, e.g. `haiku`).
- **Each iteration** — one **plan** call (`--plan-model`, e.g. `opus`) plus one
  **verify** call (`--verify-model`, e.g. `haiku`). The planning model dominates;
  verification is comparatively negligible.

So total Claude cost ≈ `iterations × (plan + verify) + one clarify`, and is driven
almost entirely by your plan model and how large `task.md` + `context.md` + the
accumulating diff are (they are re-read each round).

As a rough order of magnitude, a small task with `opus` planning and `haiku`
verification tends to land in the **low tens of cents to a few dollars of Claude
spend per iteration** — bigger context or a pricier verify model pushes it up. Treat
that as illustrative, not a quote: **the driver prints actual `claude spend` at the
end of every run**, so measure your own first task. Bound it with `--max-cost-usd`,
keep `--max-iterations` tight, and use `--dry-run` to see the prompt sizes before
you spend anything. A clear `task.md` with a real test gate is also the cheapest
path — it converges in fewer iterations.

## How this compares

Plenty of tools write code autonomously. This one is deliberately narrow, and the
combination is the point:

- **Swappable executor, fixed judges.** The mechanical editing runs on whatever
  CLI you like (Cursor, Codex, Gemini, or Claude itself); planning and
  verification always run on Claude. Most agents bind you to one model end-to-end.
- **Claude-as-judge against machine-checkable criteria.** A separate verifier
  decides "done" from the diff + your test gate and writes a structured
  `verdict.json` — the loop converges on criteria, not on the model declaring
  itself finished.
- **A file-based, auditable contract.** Every handoff is a file on disk
  (`task.md`, `plan.md`, the diff, `verdict.json`). You can read, diff, and replay
  every step. Nothing hides in an agent's memory.
- **Standard library only, non-destructive.** One ~700-line Python file, zero pip
  installs, and it never commits or resets your repo.

Versus the usual suspects: **Aider** is an excellent interactive pair-programmer
but human-in-the-loop by design; **OpenHands / SWE-agent** are heavier autonomous
frameworks with their own runtimes and dependencies; **Cursor's background agents**
are powerful but closed and Cursor-bound. If you want a small, inspectable harness
that separates a cheap editor from an expensive judge and leaves a paper trail,
this is that. If you want a full agent platform, use one of those instead.

## Limitations

Be clear-eyed about what this does and doesn't give you:

- **The verifier is only as good as your criteria.** With soft acceptance criteria
  and no `--test-command`, the verifier judges the diff on vibes and can be wrong
  or talked past. Give it concrete, checkable criteria and a real test gate — that
  is where the guarantees come from.
- **Executor backends drift.** They wrap fast-moving third-party CLIs; flags change
  (the Gemini → Antigravity churn is in this repo's history). Expect occasional
  adapter fixes — see `CHANGELOG.md` and the "executor flags changed" issue
  template.
- **One task per run.** There is no task queue or parallelism, on purpose. State is
  one `task.md`; orchestration of many tasks is out of scope.
- **It does not sandbox the executor.** Edits are auto-applied and shell-capable
  backends can run commands; confinement is the backend CLI's job, not the
  driver's. See [SECURITY.md](SECURITY.md).
- **Judgment costs money.** Planning and verifying every iteration on Claude is the
  spend; `--max-iterations` and `--max-cost-usd` bound it, but a hard task that
  never converges will burn the budget before stopping.

## Troubleshooting

Two things to reach for first: run **`python driver.py doctor`** (catches missing
or unauthenticated CLIs), and read the **`.loop/` scratch dir** — every step writes
its raw I/O there (`plan.md`, `diff.patch`, `executor_output.txt`, `verify_raw.txt`,
`test_output.txt`). The driver also prints a final **status** that names what
happened. Common cases:

- **`command not found: <cli>` and the run aborts immediately.** The backend CLI
  isn't installed or not on `PATH`. Run `doctor` to see which one; install it and
  log in (each tool has its own auth). This is non-retryable on purpose.

- **It halts before planning, writing `clarifications_needed.json` (exit 2).** The
  clarity gate judged `task.md` too vague to plan unattended. Read the questions it
  wrote, then either sharpen `task.md` or drop answers into `clarifications.md` and
  re-run. In a terminal it asks you live instead.

- **Status `blocked` — "Executor produced no changes against the baseline commit."**
  The executor ran but edited nothing. Usually the plan was too vague or named the
  wrong files, or the executor didn't actually apply edits. Inspect `.loop/plan.md`
  and `.loop/executor_output.txt`; tighten `task.md`/`context.md` so the plan can
  name concrete paths. (`context.md` is what tells the planner where things live.)

- **Status `stalled` — "no progress across iterations (same failure + same diff)."**
  The verifier failed the same way twice with no new changes. The criterion may be
  unsatisfiable or contradictory, or the executor can't see what it needs. Read the
  `reasons` in `verdict.json` and `.loop/verify_raw.txt`; fix or split the criterion,
  or add detail to `context.md`.

- **Status `budget_exhausted` / `cost_exhausted`.** Hit `--max-iterations` or
  `--max-cost-usd` without passing. The task is likely too big for one run, the
  criteria too strict, or the impl model too weak. Split the task, raise the caps
  deliberately, or use a stronger `--impl-model`.

- **"verifier did not return valid JSON" / "verdict JSON missing 'status'."** The
  verify model returned something unparseable (raw saved to `.loop/verify_raw.txt`).
  A stronger `--verify-model` is the usual fix.

- **"plan step produced no usable plan."** The planner erred or returned nothing.
  If it can't reach your codegraph tools, widen `PLAN_ALLOWED_TOOLS` in `driver.py`
  (it defaults to `Read, Grep, Glob, mcp__codegraph`); check `.loop/` for the raw
  output.

- **"claude returned non-JSON envelope."** The `claude` CLI didn't emit the expected
  JSON — often an auth prompt or a CLI-version mismatch. Run `claude` once manually
  to clear login, and check the [compatibility matrix](#compatibility-matrix).

- **The executor hangs, then retries.** Headless executor hangs/timeouts are retried
  with backoff (notably the `cursor-agent` headless hang); tune `CURSOR_TIMEOUT` /
  `EXECUTOR_MAX_RETRIES` in `driver.py` if your tasks legitimately run long.

Still stuck? Open an issue with the backend, models, the command you ran, and the
relevant `.loop/` contents **redacted** — see [SECURITY.md](SECURITY.md), since
those files can capture secrets.

## Files

```
driver.py            the loop (stateful orchestrator)
executors.py         pluggable executor adapters
prompts/
  triage.md          clarity-gate contract
  plan.md            planner contract
  execute.md         instruction handed to the executor
  verify.md          verifier contract + verdict.json schema
AGENTS.md            standing rules auto-loaded by Cursor / Codex executors
install.sh · Makefile   drop the tool into a target repo
tests/               stdlib unittest suite (pure logic; no dependencies)
verdict.sample.json  example verifier output
task.md.example      filled-in sample task (copy to task.md)
context.md.example   sample codebase map (copy to context.md)
.github/             issue + PR templates, CI workflow
README.md · CONTRIBUTING.md · SECURITY.md · CHANGELOG.md · LICENSE · .gitignore
```

## Safety

This runs AI coding agents with **auto-approved file edits**. Only point it at a
repository and branch you can throw away, ideally inside a git worktree or a
disposable container. Review every diff before merging. See
[SECURITY.md](SECURITY.md) for the full trust model, prompt-injection exposure,
and how to report a vulnerability.

## License

MIT — see [LICENSE](LICENSE).
