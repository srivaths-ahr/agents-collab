# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because the executor backends wrap fast-moving third-party CLIs, **adapter and
flag changes are expected to be the most frequent entries here** — check this file
when an executor stops behaving.

## [Unreleased]

### Added

- **`--clarify-model` flag.** The clarity gate's model was hardcoded to `haiku`
  (the `CLARIFY_MODEL_NAME` module constant) with no way to override it per run,
  unlike `--plan-model` and `--verify-model`. It now has a matching flag, wired the
  same way (a passed value overrides the constant; never prompted). Use a stronger
  clarify model when the gate is mis-judging task readiness.

### Fixed

- **Windows: subprocess I/O is pinned to UTF-8.** `run()` ran children in text mode
  without an explicit encoding, so it used the platform default — cp1252 on Windows.
  Once the prompt (and Claude's output) started flowing through the pipe, the first
  non-ASCII character crashed the run with `UnicodeEncodeError: 'charmap' codec can't
  encode character '→'` (the `→` in `prompts/plan.md`). `run()` now passes
  `encoding="utf-8", errors="replace"` for stdin/stdout/stderr, matching the UTF-8
  prompt files and Claude's UTF-8 output; `errors="replace"` keeps a stray
  undecodable byte in any child's output from aborting the loop. No change on
  macOS/Linux, where the default was already UTF-8.
- **Windows: Claude prompts no longer corrupted by cmd.exe newline truncation.**
  `claude` is an npm `.cmd` shim on Windows, which `subprocess` runs through cmd.exe;
  cmd.exe ends a command at the first newline, so a multi-line `-p` prompt or
  `--append-system-prompt` value arrived truncated at its first `\n`. The plan step
  failed hard (`claude returned non-JSON envelope` — the cut dropped `--output-format
  json` along with most of the prompt); the clarity gate only survived because its
  user prompt is a single line. `build_claude_argv` now returns `(argv, stdin_text)`:
  on Windows the user prompt **and** the system-prompt-file contents are delivered
  via STDIN (`claude -p` reads the prompt from stdin) with `--append-system-prompt`
  dropped, so no multi-line value ever rides in argv. macOS/Linux are unchanged
  (prompt as the `-p` argument, system prompt via `--append-system-prompt`). Verified
  against claude 2.1.x — which has no `--*-system-prompt-file` flag, hence the fold
  into stdin. This sits on top of the earlier `shutil.which` fix (which is what let
  the shim launch at all).
- **Clarity gate tolerates schema drift in the triage output.** The gate assumed
  every `questions` item was a `{id, question, why}` object and called `q.get(...)`
  on it; when the model returned a bare string (or a finding-shaped object keyed
  `title`/`description`), it crashed with `AttributeError: 'str' object has no
  attribute 'get'`. `questions` and `issues` are now coerced to their expected
  shapes (`normalize_question` / `normalize_issue`, both pure and unit-tested), and
  all three fields are forced to lists, so a model schema slip degrades gracefully
  instead of aborting the run. Cleans up the raw `{...}` reprs that issues printed.
- **Windows: external commands now launch via their PATH-resolved path.** `run()`
  resolves `cmd[0]` with `shutil.which()` (which respects Windows `PATHEXT`) and
  passes the full path to `subprocess`, instead of handing the bare name to a
  `shell=False` `CreateProcess` that never searches `PATHEXT`. npm-installed CLIs
  land as `.cmd` shims (e.g. `claude.cmd`), so the old code died at the clarity gate
  with `command not found: claude` even though `claude` ran when typed and `doctor`
  reported it present — `doctor` already resolved via `shutil.which`, so the two
  disagreed. The fix is at the single subprocess chokepoint, so it covers `claude`,
  every executor CLI, the `bash -lc` test gate, and `git` uniformly. No behavior
  change on macOS/Linux (running a `which`-resolved full path is identical to
  running by name). Requires a Python with the `.bat`/`.cmd` arg-escaping fix
  (3.8.19 / 3.9.19 / 3.10.14 / 3.11.9 / 3.12.3+).

### Changed

- **The clarity-gate halt now logs the absolute path** of the
  `clarifications_needed.json` it writes, instead of a bare filename. The file is
  written to the cwd after `os.chdir(REPO_ROOT)` and is gitignored, so a relative
  name left users (especially on Windows / inside an IDE panel) unsure where it
  landed — or thinking it was never written.
- **README Windows guidance.** Spelled out the WSL vs. hand-copy paths and their
  caveats (the test gate still needs `bash` on `PATH`; `AGENTS.md` is only read by
  the Codex/Cursor executors), and cross-linked them from the Install section.

## [0.2.0] — 2026-06-28

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

- **Interactive prompts exit cleanly on Ctrl-C / EOF.** A `KeyboardInterrupt` or
  `EOFError` at the run-settings chooser or the clarify gate now prints a short
  message and exits (130 / 1) instead of dumping a Python traceback.
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
