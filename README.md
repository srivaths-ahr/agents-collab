# agentic-loop

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

Each tool authenticates through its own login or environment variable. **No
credentials are stored in this repo.**

## Install

This is a template you drop into the repository you want to work on:

```bash
# from the root of YOUR target repo:
git clone https://github.com/<you>/agents-collab /tmp/agents-collab
cp -r /tmp/agents-collab/{driver.py,executors.py,prompts,AGENTS.md} .
```

Run it from inside that repo (the prompt files are read by relative path).

## Quickstart

```bash
# 1) first run scaffolds a task template, then stops
python driver.py

# 2) fill in task.md (goal + checkable acceptance criteria),
#    and add a context.md describing your codebase.
#    Starting points are provided:
#      cp task.md.example task.md
#      cp context.md.example context.md

# 3) run the loop with your chosen models/executor
python driver.py \
  --plan-model opus \
  --executor codex --impl-model gpt-5.4 \
  --verify-model haiku \
  --test-command "pytest -q" \
  --max-iterations 8
```

Changes are **staged but never committed** — run on a dedicated branch or git
worktree, then review and commit (or discard) yourself.

## Configuration

Set defaults at the top of `driver.py`, or override per run:

| Flag               | Meaning                                                  |
| ------------------ | -------------------------------------------------------- |
| `--plan-model`     | Claude model for planning (e.g. `opus`)                  |
| `--executor`       | `cursor` · `claude` · `codex` · `gemini` · `antigravity` |
| `--impl-model`     | executor model slug for the chosen backend               |
| `--verify-model`   | Claude model for verification (e.g. `haiku`)             |
| `--test-command`   | deterministic test gate; its pass/fail is ground truth   |
| `--max-iterations` | hard cap on loop rounds                                  |
| `--repo`           | path to the target git repo (default: current dir)       |

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

## Guardrails

- **Clarity gate** — a cheap Claude pass checks `task.md` before any planning
  spend. In a terminal it asks you the blocking questions and re-checks; run
  unattended, it writes them to `clarifications_needed.json` and halts.
- **Executor resilience** — transient executor failures (hang/timeout/non-zero)
  are retried with backoff; a missing CLI or bad config aborts immediately.
- **Stop conditions** — `pass` (done), `blocked` (needs a human), `stalled` (no
  progress across rounds), budget exhausted, or malformed verifier output.
- **Non-destructive** — the driver stages to compute diffs but never commits,
  resets, or deletes.

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
verdict.sample.json  example verifier output
task.md.example      filled-in sample task (copy to task.md)
context.md.example   sample codebase map (copy to context.md)
README.md · CONTRIBUTING.md · LICENSE · .gitignore
```

## Safety

This runs AI coding agents with **auto-approved file edits**. Only point it at a
repository and branch you can throw away, ideally inside a git worktree or a
disposable container. Review every diff before merging.

## License

MIT — see [LICENSE](LICENSE). Update the copyright holder before publishing.
