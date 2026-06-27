# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because the executor backends wrap fast-moving third-party CLIs, **adapter and
flag changes are expected to be the most frequent entries here** — check this file
when an executor stops behaving.

## [Unreleased]

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
- Non-destructive by design: the driver stages to diff but never commits, pushes,
  resets, or deletes.

[unreleased]: https://github.com/srivaths-ahr/agents-collab/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/srivaths-ahr/agents-collab/releases/tag/v0.1.0
