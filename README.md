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

The executor is swappable: **Cursor, Claude itself, OpenAI Codex, or
Antigravity**. Planning and verification always run on Claude.

**See it actually run:** [`examples/romannumbers`](examples/romannumbers) is a real
run — a stubbed function taken to a verified PASS in one iteration, with the opus
plan, the cursor diff, and the haiku verdict all committed. Total Claude spend: $0.25.

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
  `cursor-agent` · `claude` · `codex` · `agy` (Antigravity).
- **macOS, Linux, or Windows.** Everything is portable, stdlib-only Python —
  **including the installer** (`install.py`), so there's no bash needed to set up or
  run the tool. On **Windows** the only nuance is the **test gate**: it prefers
  `bash -lc` (so one `--test-command` stays portable) and falls back to `cmd /c` when
  `bash` isn't on `PATH`, so gates still run on bare cmd/PowerShell — install **Git
  Bash** or use **WSL** for exact parity with macOS/Linux test commands. (On a
  bash-less Linux container — Alpine, distroless — the gate falls back to `sh -c`.)

Each tool authenticates through its own login or environment variable. **No
credentials are stored in this repo.**

Run `python driver.py doctor` to confirm git and the CLIs you need are installed
and print their versions before your first run.

## Install

This is a template you drop into the repository you want to work on. Clone it
once, then run the installer against your target repo:

```bash
git clone https://github.com/srivaths-ahr/agents-collab
python agents-collab/install.py /path/to/your-repo
# or:  make -C agents-collab install TARGET=/path/to/your-repo
```

`install.py` is stdlib-only Python, so it runs the same on **Windows
(cmd/PowerShell), macOS, and Linux** — no bash required. (`./agents-collab/install.sh`
still works too; it's a thin bash shim that forwards to `install.py`.)

The installer copies the tool files (`driver.py`, `executors.py`, `prompts/`) and
seeds `AGENTS.md` + the `*.example` files **only if absent** — re-run it to upgrade
without clobbering your standing rules or an in-progress task. Prefer to do it by
hand? Copy `{driver.py,executors.py,prompts,AGENTS.md}` into the repo yourself.

It also drops the [`jira-to-task` skill](#the-jira-to-task-skill--interactive-mcp-connected-recommended)
at `skills/jira-to-task/`, next to `driver.py` — and stops there. It does **not**
write into your `.claude/` or `.cursor/`: which agent you use and where it reads
skills from is your setup, not the installer's guess. The folder's `INSTALL.md` maps
the options; copying it is a one-liner.

It also adds the tool's own files + per-run artifacts to `.git/info/exclude` (a
local ignore, not your tracked `.gitignore`), so the loop's `git add -A` stages
**only your project edits** — not `driver.py`/`prompts/`/`skills/`/artifacts. Skills
are listed **per skill** (`/skills/jira-to-task/`), never as `/skills/`, so your own
top-level `skills/` is never shadowed — the same care already taken with `prompts/`.
Uninstall removes that block and unstages what it deletes, leaving `git status`
clean. (Did a hand-copy instead? Add those paths to your ignores yourself.)

Run the loop from inside that repo (the prompt files are read by relative path).

### Uninstall

To remove the tool again, run the installer in reverse from the same clone:

```bash
python agents-collab/install.py --uninstall --dry-run /path/to/your-repo  # preview, delete nothing
python agents-collab/install.py --uninstall /path/to/your-repo            # apply
# or:  make -C agents-collab uninstall TARGET=/path/to/your-repo ARGS=--dry-run
```

It removes the tool files (`driver.py`, `executors.py`, the shipped `prompts/*.md`,
`__pycache__`) and the generated artifacts (`.loop/`, `plan.md`, `verdict.json`,
`clarifications_needed.json`) outright. Your content — `AGENTS.md`, `task.md`,
`context.md`, `clarifications.md`, the `*.example` files, **and `skills/`** — is
**guarded**: it's removed only when it's **byte-identical to what the tool seeded**
(an untouched copy). Skills are checked file by file, and the emptied directories
dropped after, so a `SKILL.md` you tuned survives with its folder. Copies you placed
elsewhere (`.claude/skills/`, `~/.claude/skills/`) are never touched — the installer
doesn't know about them. Anything you wrote or changed — including a pre-existing `AGENTS.md` you had
before installing — is **kept** and reported, so your own work is never deleted by
surprise (being merely committed is *not* reason enough to remove it). Pass
`--force` to remove your content too. Always preview with `--dry-run` first.

## Quickstart

```bash
# 1) first run scaffolds a task template, then stops
python driver.py

# 2) create BOTH input files — the run aborts without them.
#    task.md is auto-scaffolded above; context.md is NOT — you must add it.
#      cp task.md.example task.md        # then fill in goal + checkable acceptance criteria
#      cp context.md.example context.md  # then describe your codebase (required, not optional)
#    Working from a Jira ticket? Two ways to get a task.md (see "Authoring task.md"):
#      - the jira-to-task SKILL, in Claude Code / Cursor / Claude Desktop — pulls the
#        issue over your Jira MCP and interviews you (recommended)
#      - python driver.py author --from jira-STORY-123.txt   # terminal, local file only

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

### Authoring `task.md` from a Jira story or requirements doc

The whole loop is only as good as `task.md` — a sharp, checkable task converges in
fewer, cheaper iterations. If what you have is a Jira story rather than a written
task, there are two ways to get one, and they hold themselves to the same bar.

#### The `jira-to-task` skill — interactive, MCP-connected (recommended)

`install.py` drops a self-contained agent skill at **`skills/jira-to-task/`**, next
to `driver.py`. Copy that folder to wherever your agent reads skills from — one
command, covered per host in the folder's `INSTALL.md`:

| Host | Where you put it | How you invoke it |
|---|---|---|
| Claude Code | `.claude/skills/` (this repo) or `~/.claude/skills/` (everywhere) | `/jira-to-task PROJ-123`, or just ask |
| Cursor | copy `cursor-rule.mdc` → `.cursor/rules/`, leave the folder put | "turn PROJ-123 into a task.md" |
| Claude Desktop | add the folder in Settings (folder picker or zip) | "turn PROJ-123 into a task.md" |

The installer deliberately stops at the copy. Your `.claude/` and `.cursor/` are
yours — which agent you run, and whether its skills live per-repo or globally, isn't
something an installer should decide for you.

It **pulls the issue through whatever Jira MCP server you have connected** — no
export, no copy-paste — reads the description, acceptance criteria, subtasks, linked
issues, and the comments (where the real decisions usually are). Then it grounds the
scope in real paths from your repo, interviews you in rounds of at most four
questions — each with concrete options and a stated default you can wave through —
shows you the draft, and takes edits in plain language before writing anything.

It writes **`task.md` plus `clarifications.md`** (every question and answer,
including the defaults it assumed). The clarity gate and the planner both read
`clarifications.md` as authoritative, so your answers survive into iteration 5
without you in the room. It does **not** write `plan.md` — the loop regenerates that
every iteration.

Jira access is **read-only**: it never transitions a ticket or posts a comment
unless you ask in the conversation and confirm. The MCP connection is your host's —
`driver.py` still touches no network and stores no credentials.

Re-running `install.py` upgrades the folder **per file, only where your copy is
untouched**; anything you edited is kept and its path printed, so a house-style
`SKILL.md` survives an upgrade. Copies you placed elsewhere are yours to update.

#### `driver.py author` — the terminal path, no MCP needed

Same readiness bar, no network: a bounded interactive Claude run over a story you
already have on disk.

```bash
python driver.py author --from jira-STORY-123.txt   # or: pbpaste | python driver.py author
```

It reads the story, asks the few clarifying questions whose answers would actually
change the task, and writes a well-formed `task.md` (goal, numbered
independently-checkable acceptance criteria, in/out of scope, constraints) — grounding
the criteria in your `context.md` and real file paths. Because it holds itself to the
**same readiness bar the clarity gate uses**, the task it writes then passes `clarify`
with few or no follow-ups.

- **Input is a local file or stdin only** — no Jira API, no network, no stored
  credentials (the tool's standing invariants). Export or copy the story into a text
  file, or pipe it in. Want it to fetch the ticket for you? That's the
  `jira-to-task` skill above, where the MCP connection is your host's, not the
  driver's.
- **`--from <path>` enables the interactive Q&A**; piping the story on **stdin** makes
  the run one-shot best-effort (stdin is then a non-TTY, so it can't prompt) — it still
  writes the best draft it can and prints any open questions.
- **One `task.md` out.** If a story clearly spans several units of work, `author`
  doesn't split it — it writes the single most sensible unit and prints a **suggested
  split** so you can [loop `run` per unit](#running-multiple-units-a-decomposed-story).
- **Non-destructive.** It won't overwrite an existing `task.md` without a `[y/N]`
  confirm (or `--force`); use `--task units/03/task.md` to write elsewhere. Pick the
  drafting model with `--author-model` (default `sonnet`).

Then review the `task.md` it wrote and run the loop as usual.

### Resuming a failed run

A run that dies partway — the executor times out, the verifier returns something
malformed, you Ctrl-C — leaves a checkpoint at `.loop/state.json`. **Re-run the same
command and it picks up where it stopped**, instead of re-buying work you already
paid for:

| Died in | Re-run does | Skipped |
|---|---|---|
| PLAN     | plan → execute → verify | clarity gate |
| EXECUTE  | execute → verify        | clarity gate + **plan** |
| VERIFY   | verify only             | clarity gate + plan + **the whole executor run** |

```
── RESUMING ──
· .loop/state.json: iteration 3, stopped in verify
· inputs unchanged — reusing baseline a1b2c3d4e5
· skipping CLARIFY (passed on identical inputs)
· skipping PLAN    (plan.md still valid)
· skipping EXECUTE (edits already applied)
· carried spend: $1.2345 / $5.00 cap
```

**The recorded phase is where it died, not necessarily where it's worth restarting.**
Before re-entering, the driver checks whether that step can actually succeed, and
backs up when it can't — saying so each time:

- `plan.md` missing or empty → back up to PLAN. EXECUTE has no input without it.
- No staged edits → back up to EXECUTE. Verifying an unchanged tree buys a verdict
  of "nothing changed" and then a whole iteration to fix it.
- The same phase has now failed twice → back up one step. A deterministic executor
  failure will fail identically a third time; a *different plan* might not. (This is
  the across-runs sibling of the in-run stall guard — `MAX_PHASE_ATTEMPTS` vs
  `MAX_IDENTICAL_FAILURES`.)

```
· EXECUTE failed 2x — backing up to PLAN; retrying it unchanged would only fail again
· skipping CLARIFY (passed on identical inputs)
```

This is about correctness as much as cost. The baseline is a snapshot of the working
tree taken at the start of a run; re-capturing it after a partial execution would
absorb the edits the executor already made, so they'd vanish from the diff and the
verifier would score a **truncated** diff — failing criteria that were actually met,
and buying another iteration to "fix" them. Resuming reuses the original baseline.

Carrying the cost and iteration forward also means `--max-cost-usd` and
`--max-iterations` bound *the work*, not each invocation. Without that, a run that
crashes three times spends three times its cap.

The checkpoint is **discarded, with a printed reason**, whenever it can't be trusted:

- `task.md`, `context.md`, or `clarifications.md` changed — the old plan was written
  for a different task, so re-planning is the point.
- `HEAD` moved (you committed, reset, or switched branch) — the baseline tree no
  longer anchors a meaningful diff.
- It came from a different driver version, or you passed `--fresh`.

It's cleared on a **pass**. It's deliberately *kept* after `blocked` or `stalled`:
fix the code yourself and re-run, and the loop goes straight to VERIFY to score your
fix — no re-plan, no second executor pass.

### Before you spend: `doctor` and `--dry-run`

Two zero-cost previews, so an unfamiliar tool with auto-approved edits never
surprises you:

- **`python driver.py doctor`** — checks that `git`, `claude`, and the chosen
  executor CLI are installed (printing each one's `--version`), that you're in a
  git repo, and that the prompt files are present. Exits non-zero with a checklist
  if anything is missing — turning a mid-run "command not found" into a two-second
  report. Worth running first, since the third-party executor CLIs drift. It
  *reports* whether `task.md` and `context.md` are present but does **not** fail if
  they are missing — both are required for a run, so create them regardless of what
  `doctor` says (the run itself aborts if `context.md` is absent).
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
| `--clarify-model`  | Claude model for the clarity gate (e.g. `haiku`) †                              |
| `--plan-model`     | Claude model for planning (e.g. `opus`) †                                       |
| `--executor`       | `cursor` · `claude` · `codex` · `antigravity` †                                 |
| `--impl-model`     | executor model slug for the chosen backend †                                    |
| `--verify-model`   | Claude model for verification (e.g. `haiku`) †                                  |
| `--test-command`   | deterministic gate; pass/fail is ground truth. Repeatable — all gates must pass |
| `--max-iterations` | hard cap on loop rounds †                                                       |
| `--max-cost-usd`   | hard cap on cumulative Claude spend in USD (0 = no limit) †                     |
| `--dry-run`        | print each step's command + prompt and exit; no calls, edits, or spend          |
| `--fresh`          | ignore `.loop/state.json` and restart from the clarity gate (see [Resuming](#resuming-a-failed-run)) |
| `--repo`           | path to the target git repo (default: current dir)                              |
| `--task`           | task file to run (default: `task.md`) — point at one unit to loop a story        |
| `--context`        | architecture-map file (default: `context.md`); shared across units              |
| `--work-dir`       | scratch dir for diff/test/raw artifacts (default: `.loop`); override per unit    |
| `--from`           | `author` only: the requirements/Jira story file (or stdin) to draft `task.md` from |
| `--author-model`   | `author` only: Claude model that drafts `task.md` (default: `sonnet`)            |
| `--force`          | `author` only: overwrite an existing `--task` file without confirming            |

**†** Omit any of these on an interactive `run` and the driver **walks you through
them** (a numbered menu for the executor and each Claude model — opus/sonnet/haiku, or
type any slug — plus the iteration/cost caps; Enter accepts the shown default; for
`--impl-model` the default is a per-executor suggestion, e.g. `default` for `codex`).
Passed flags are never asked about, and a non-interactive run (piped/CI, or `--dry-run`)
silently uses the defaults — so scripts and the multi-unit loop below, which pass the
flags, never block.

The `doctor` subcommand (`python driver.py doctor`) takes the same `--executor` /
`--repo` flags, so it checks the exact CLIs the run you're about to launch needs.

## Running multiple units (a decomposed story)

This tool does one thing: take **one** task to a verified PASS. It deliberately does
*not* decompose a Jira story or orchestrate a batch — splitting work is a judgment call
you (or any other tool) make, and looping is a shell `for`. What the loop gives you is
the part worth owning: each unit is checked against an objective gate.

(This is why [`author`](#authoring-taskmd-from-a-jira-story-or-requirements-doc) writes
**one** `task.md` and, when a story spans several units, only prints a *suggested split*
rather than emitting multiple tasks — the decomposition stays your call.)

So decompose however you like into one `task.md` per unit, then point `--task` /
`--context` / `--work-dir` at each and loop:

```bash
# units/01-to_roman/task.md, units/02-from_roman/task.md (depends on 01), + a shared context.md
for u in units/*/; do
  python driver.py run \
    --task "$u/task.md" --context context.md --work-dir "$u/.loop" \
    --executor codex --impl-model default \
    --test-command "python3 test_roman.py" \
    --max-iterations 5 --max-cost-usd 2.00 || { echo "stopped at $u"; break; }
  cp verdict.json "$u/verdict.json"   # optional: keep each unit's verdict
done
```

Dependent units compose because earlier units' edits stay in the working tree for later
ones. `--work-dir` keeps each unit's `diff.patch` / `test_output.txt` from overwriting
the last. The loop stops on the first non-pass (the run exits non-zero). Nothing touches
git history — you commit at whatever checkpoints you choose.

## Executors

| Backend       | Command                                   | Context file              | Notes                                                                     |
| ------------- | ----------------------------------------- | ------------------------- | ------------------------------------------------------------------------- |
| `cursor`      | `cursor-agent -p --force`                 | `AGENTS.md`               | default                                                                   |
| `claude`      | `claude -p --permission-mode acceptEdits` | `CLAUDE.md` / `AGENTS.md` | all-Claude pairing                                                        |
| `codex`       | `codex exec --sandbox workspace-write`    | `AGENTS.md`               | shares AGENTS.md with Cursor                                              |
| `antigravity` | `agy --print --dangerously-skip-permissions` | —                      | Google's headless agent CLI; flags change fast — verify against current docs |

> **codex on ChatGPT auth:** use `--impl-model default` (codex then picks the model
> from `~/.codex/config.toml`). A ChatGPT-plan account rejects explicitly-named models
> — `--impl-model gpt-5.4`, `composer-2.5`, etc. fail with *"model is not supported when
> using Codex with a ChatGPT account"*. `default`/`auto` sidesteps it. Only name a model
> explicitly when codex is authenticated with an API key.

### Compatibility matrix

These are third-party CLIs and their flags drift — that is the most common source
of breakage (see the "executor flags changed" issue template). This table records
the **last CLI version each adapter was verified against**; treat anything newer as
unverified until confirmed. Run `python driver.py doctor` to print the versions
installed on your machine, and please PR an update here when you verify a backend.

| Backend       | CLI binary     | Last verified version | Verified on | Example model slug      |
| ------------- | -------------- | --------------------- | ----------- | ----------------------- |
| `cursor`      | `cursor-agent` | `2026.06.26-7079533`  | 2026-06-27  | `composer-2.5`          |
| `claude`      | `claude`       | `2.1.195`             | 2026-06-27  | `opus`, `haiku`         |
| `codex`       | `codex`        | `0.142.3` (adapter)   | 2026-06-28  | codex default; or `gpt-5.x` |
| `antigravity` | `agy`          | `1.0.13`              | 2026-06-27  | (auto-selected)         |

> All four executors (`cursor`, `claude`, `codex`, and `antigravity`) ran the
> example task end-to-end to a verified PASS. `agy` needed a driver fix — it
> blocks on stdin in `--print` mode, so the driver now closes the executor's
> stdin. See [`examples/romannumbers`](examples/romannumbers).

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
  resets, or deletes. The `author` step likewise won't overwrite an existing
  `task.md` without a confirm (or `--force`), reads its input from a local file or
  stdin only (no network, no credentials), and uses read-only tools.

## What it costs

Two separate bills: **Claude** (plan + verify + clarify) and, if you use a
non-Claude executor, **that CLI's own account** (Cursor, Codex, Antigravity) on its own
plan. The driver only meters and caps the Claude side.

Where the Claude spend goes, per loop:

- **Clarity gate** — one cheap call up front (`--clarify-model`, e.g. `haiku`).
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
  CLI you like (Cursor, Codex, Antigravity, or Claude itself); planning and
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
  (the `codex` and `agy` adapter fixes in this repo's history are real examples).
  Expect occasional fixes — see `CHANGELOG.md` and the "executor flags changed"
  issue template.
- **One task per run.** There is no task queue or parallelism, on purpose. State is
  one `task.md`; orchestration of many tasks is out of scope.
- **It does not sandbox the executor.** Edits are auto-applied and shell-capable
  backends can run commands; confinement is the backend CLI's job, not the
  driver's. See [SECURITY.md](SECURITY.md).
- **Judgment costs money.** Planning and verifying every iteration on Claude is the
  spend; `--max-iterations` and `--max-cost-usd` bound it, but a hard task that
  never converges will burn the budget before stopping.

### What we're watching (v0.2.0 — early access)

This is a young tool, shipped to learn from real use before adding more. The areas most
likely to surprise you — and the ones we'd most like reports on:

- **Cumulative diffs in the multi-unit loop.** Without a commit between dependent units,
  unit K's verify step sees units 1..K in its diff. It judged correctly in our testing,
  but a strict "only touches file X" criterion could mis-flag an earlier unit's change.
  If you hit this, commit at each checkpoint for crisp per-unit diffs — and tell us.
- **Per-unit `verdict.json` is overwritten.** A loop leaves only the *last* unit's
  `verdict.json` at the repo root; copy it aside per unit (the loop above shows `cp`).
  We're watching whether per-unit verdict isolation should be built in.
- **Interactive run-knob prompts are new.** They're TTY-gated and skipped when the flags
  are passed, so scripts and CI shouldn't see them — but if a prompt fires when you
  didn't expect it, or blocks something, that's a report we want.
- **Third-party executor CLIs drift.** We pin the version each adapter was verified
  against (see the compatibility matrix); a newer CLI may have moved flags. `doctor`
  prints your installed versions.

Found a rough edge? Open an issue — `bug_report.md`, or `executor_flags_changed.md` for
adapter drift. We're deliberately holding new features for ~a week to act on real
feedback first.

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
install.py · install.sh · Makefile   drop the tool into a target repo (or --uninstall it)
examples/            worked runs with committed plan/diff/verdict artifacts
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
