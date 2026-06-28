# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`agentic-loop` is a single-purpose tool, not an application: a **plan → execute → verify**
loop for autonomous code changes. It is a _template_ you copy into a target repo
(`driver.py`, `executors.py`, `prompts/`, `AGENTS.md`) and run from that repo's root.
Claude does the judgment work (clarify, plan, verify); a pluggable coding CLI does the
editing. There is no package, no server, no pip dependencies — **standard library only**.

## Commands

```bash
# Unit tests (stdlib unittest — no dependencies). Also: `make test` / `make check`.
python -m unittest discover -s tests -t . -v

# Byte-compile (catches syntax/import errors):
python -m py_compile driver.py executors.py

# Preflight the environment (checks git + claude + executor CLIs; no spend):
python driver.py doctor

# Preview every step's command + prompt without calling anything (no spend/edits):
python driver.py --dry-run

# Run the loop (first run with no task.md scaffolds a template and exits):
python driver.py \
  --plan-model opus \
  --executor codex --impl-model gpt-5.4 \
  --verify-model haiku \
  --test-command "pytest -q" \
  --max-iterations 8
```

The unit tests cover only the **pure** logic — the executor argv builders and the
parse/branch helpers (`parse_claude_envelope`, `parse_executor_output`, `parse_verdict`,
`strip_fences`, `progress_fingerprint`, `run_tests` aggregation). They spawn nothing, so
keep those functions free of subprocess/file I/O; if you change `verdict.json` or an
executor's flags, update the matching test. To exercise loop logic **end to end**, do a
smoke run against `task.md.example` + `context.md.example` in a throwaway git repo and
confirm the clarity gate, an iteration, and the stop conditions still behave. CLI flags
only override a subset of the config — most knobs are module-level constants at the top of
`driver.py` (timeouts, retry policy, clarify rounds, allowed-tools, stall threshold).
`--task`/`--context`/`--work-dir` make the single-unit run addressable (still **one unit
per invocation** — loop `run` yourself for a multi-unit story; no batch/story mode by
design), and like `--repo`/`--test-command` they just reassign the matching path globals.

## Architecture

Four stateless one-shot agents sequenced by one stateful driver. Everything between agents
is a **file on disk** — that is the entire contract; no agent holds memory between calls.

```
CLARIFY  (Claude/triage.md)  is task.md clear enough to plan? ask in TTY, else halt
PLAN     (Claude/plan.md)    task.md + context.md (+codegraph) + last verdict → plan.md
EXECUTE  (executor/execute.md) plan.md (+AGENTS.md) → edits files in the git workspace
VERIFY   (Claude/verify.md)  git diff + test output + task.md criteria → verdict.json
   └── driver reads verdict.status: pass→stop · fail→re-plan · blocked→human
```

Key files and their roles:

- **`driver.py`** — the _only_ stateful actor. Owns the iteration budget, stop conditions,
  file handoff, cost accounting, and the clarity gate. It does **not** decide "done" — the
  verifier does, in `verdict.json`; the driver just reads `verdict.status` and branches.
- **`prompts/*.md`** — own agent _behavior_. `triage.md` (clarity gate), `plan.md`,
  `execute.md` (handed to the executor), `verify.md` (verifier contract + verdict schema).
  Each is loaded via `--append-system-prompt` for the Claude steps.
- **`executors.py`** — the pluggable EXECUTE step. Each backend is a pure
  `build(model, prompt) -> argv` function registered in the `EXECUTORS` dict. Backends
  whose stdout is a JSON cost envelope go in `JSON_ENVELOPE_BACKENDS`.

State lives in the driver and on disk: `task.md` (input, the source of truth), `context.md`
(input, architecture map), `plan.md`, the staged git diff, `verdict.json`, plus the
`.loop/` scratch dir (`diff.patch`, `test_output.txt`, `*_raw.txt`, `executor_output.txt`).
`task.md`/`context.md` and all generated artifacts are gitignored — they are per-run user
content, never committed into this tool repo.

## Invariants — preserve these when editing

- **Driver owns control flow; prompts own behavior.** Don't bake role behavior into Python,
  and don't put loop/stop logic into a prompt.
- **`verify.md` and the driver's parser move together.** The `verdict.json` shape is parsed
  in `verify_step` (status, criteria, reasons, next*actions). Change the schema in \_both*
  `prompts/verify.md` and `driver.py`, and update `verdict.sample.json`, in the same change.
- **Standard library only.** No pip dependencies, ever. If a feature seems to need one, that
  is a signal to reconsider the feature.
- **Non-destructive by default.** The driver stages (`git add -A`) only to compute diffs vs
  the baseline commit — it never commits, pushes, resets, or deletes. Don't add a step that
  mutates history without an explicit, off-by-default flag.
- **Agents are stateless one-shots.** New features must keep state in the driver/on disk,
  not in any agent's memory.
- **One source of truth for what gets run.** The real steps and the `--dry-run` preview
  both build their commands from the same helpers — `build_claude_argv` and the
  `*_instruction` builders (`plan_instruction`, `verify_instruction`, `triage_instruction`),
  and each executor's adapter. Don't inline a command or prompt into a step; route it
  through the builder so the preview can't lie. `doctor` likewise derives the executor's
  binary from `executors.EXECUTORS[backend](...)[0]`, not a hardcoded name.

## Error/stop model

`driver.py` uses an exception hierarchy: `StepError` (recoverable — retry then stop with a
reason) and its subclass `FatalError` (non-retryable — missing CLI, unknown backend, bad
config — abort at once). `NeedsClarification` carries open questions to halt the loop
cleanly before any planning spend. Stop conditions: `pass`, `blocked` (human needed),
`stalled` (same failure + same diff `MAX_IDENTICAL_FAILURES` times), iteration budget or
`MAX_COST_USD` exhausted, or malformed verifier output.

## Adding an executor backend

In `executors.py`: add a `build(model, prompt) -> argv` adapter (argv construction only —
no I/O, no side effects) that runs the CLI **headless with edits auto-applied and no
interactive prompts**, reads `plan.md`, and exits non-zero on failure. Register it in
`EXECUTORS`; if its stdout is a JSON envelope with a cost field, add it to
`JSON_ENVELOPE_BACKENDS`. Then document it in the README's Executors table. Note that
`AGENTS.md` is the auto-loaded context file for the Cursor and Codex backends.
Third-party CLI flags change fast; verify against current docs before a long
unattended run.
