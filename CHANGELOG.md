# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because the executor backends wrap fast-moving third-party CLIs, **adapter and
flag changes are expected to be the most frequent entries here** — check this file
when an executor stops behaving.

## [Unreleased]

### Added

- **Interactive prompts for the run knobs.** Omit `--executor`, `--impl-model`,
  `--max-iterations`, or `--max-cost-usd` on an interactive `run` and the driver asks
  you to choose (Enter accepts the default; `--impl-model` suggests a per-executor model
  — e.g. `default` for `codex`). Passed flags are never asked about; a non-interactive
  run (piped/CI or `--dry-run`) silently uses the defaults, so scripts and the multi-unit
  loop never block. New `executors.SUGGESTED_IMPL_MODELS` map backs the model suggestion.
- `--task` / `--context` / `--work-dir` flags make the single-unit run **addressable**:
  point them at one unit's files to loop `run` over an externally-decomposed story (one
  unit per invocation). `--work-dir` isolates each unit's `.loop/` scratch so a loop's
  per-unit artifacts don't overwrite each other. Still no batch/story mode by design —
  decomposition and looping stay yours; the tool owns the per-unit verification gate.
- `examples/romannumbers/` — a real end-to-end run (stubbed function → verified PASS
  in one iteration) with the opus plan, the cursor diff, and the haiku verdict
  committed under `run/`. Shows the loop working before you point it at your repo.

### Fixed

- **Executor flag drift, found by running the example against live CLIs:**
  - `codex` adapter: dropped `--ask-for-approval never` (removed in codex-cli
    0.142.x) and made `--model` optional (`default`/`auto` uses your codex config
    default). `codex exec` now also requires a git repo trusted by codex.
  - `antigravity` (`agy`) adapter: `--headless --approve all` → `--print <prompt>`
    + `--dangerously-skip-permissions` (agy 1.0.13).
- **Driver closes the executor's stdin (DEVNULL).** `agy --print` (and likely other
  headless CLIs) blocks reading stdin and hangs with no output until its timeout
  when stdin is left open; closing it gives an immediate EOF. With this, `agy` ran
  the example end-to-end to a verified PASS.
- **Driver no longer passes claude's `--bare`** for the clarify/verify steps. On
  some setups `--bare` ("minimal mode") skips plugin/hook-provided login, so those
  steps returned "Not logged in" while plan (no `--bare`) worked.

### Removed

- **The `gemini` executor backend.** Google retired the free/Pro/Ultra Gemini CLI
  in favor of Antigravity (`agy`); use `--executor antigravity` instead. Removed the
  adapter, its test, and the `GEMINI.md` convention. (Antigravity replaces it.)

## [0.1.0] — 2026-06-27

Initial public release.

### Added

- Plan → execute → verify loop driven by a single stateful `driver.py`; agents are
  stateless one-shots and the git workspace + on-disk files are the entire contract.
- Front-of-loop **clarity gate** (`prompts/triage.md`): blocks planning spend on a
  vague `task.md`; asks questions interactively in a TTY, halts with
  `clarifications_needed.json` when unattended.
- Pluggable **executors** (`executors.py`): `cursor`, `claude`, `codex`, `gemini`,
  `antigravity`. Plan and verify always run on Claude.
- `--max-cost-usd`: hard cap on cumulative Claude spend (`0` = no limit).
- `--test-command` is **repeatable** — gate on lint, build, and test separately;
  all must pass. The verifier sees the overall status plus a per-gate breakdown.
- `doctor` subcommand: preflights the environment (git + the `claude`/executor CLIs,
  printing each `--version`, the git repo, and the prompt files) and exits non-zero
  with a checklist if anything is missing — no spend, no edits.
- `--dry-run`: prints the exact command line and full prompt for every step (clarity
  gate, plan, execute, test gates, verify), then exits without calling Claude, running
  the executor, or editing anything. Shares the command/prompt builders with the real
  run so the preview cannot drift.
- `--version` flag and `__version__` in `driver.py`.
- `install.sh` + `make install TARGET=../repo` to drop the tool into a target repo
  without clobbering your `AGENTS.md` or in-progress task.
- `SECURITY.md` documenting the trust model and vulnerability reporting.
- **Test suite** (stdlib `unittest`, zero dependencies) covering the executor argv
  builders and the pure parse/branch helpers; the parsing logic was factored out of
  the subprocess calls (`parse_claude_envelope`, `parse_executor_output`,
  `parse_verdict`, `progress_fingerprint`) so it is testable without spawning.
- **CI** (GitHub Actions): byte-compile + unit tests on Python 3.8–3.12, plus a
  CI-only `ruff` lint. Status badge in the README.
- README docs for adopters: a **cost-expectations** section (where Claude spend
  goes and how to bound it), an **executor compatibility matrix** (last-verified CLI
  versions, since the backends drift), an explicit **platform note** (macOS/Linux;
  Windows via WSL), and a **Troubleshooting** section mapping each stop status and
  error to a cause and fix.
- Non-destructive by design: the driver stages to diff but never commits, pushes,
  resets, or deletes.

[unreleased]: https://github.com/srivaths-ahr/agents-collab/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/srivaths-ahr/agents-collab/releases/tag/v0.1.0
