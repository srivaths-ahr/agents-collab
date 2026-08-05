# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because the executor backends wrap fast-moving third-party CLIs, **adapter and
flag changes are expected to be the most frequent entries here** — check this file
when an executor stops behaving.

## [Unreleased]

### Added

- **`jira-to-task` agent skill — a Jira ticket to a `task.md`, interactively, in the
  tool you already use.** `driver.py author` works, but it's a one-shot terminal pass
  over a story you had to export yourself. The skill is the same job done where your
  agent already lives: it **pulls the issue over your host's Jira MCP** (description,
  acceptance criteria, subtasks, linked issues, and the *comments*, where the real
  decisions usually are), grounds `## In scope` in paths it greps for in the repo,
  then interviews you in rounds of at most four questions — each with concrete
  lettered options and a stated default you can wave through — and shows the draft for
  edits in plain language before writing anything. It writes `task.md` **and**
  `clarifications.md` (every question, answer, and assumed default), which the clarity
  gate and planner both read as authoritative, so your answers survive to iteration 5
  without you in the room. It never writes `plan.md` — the loop regenerates that every
  iteration. Ships as `skills/jira-to-task/` with `SKILL.md`, an `INSTALL.md` mapping
  the three hosts, a `cursor-rule.mdc` pointer, and `reference/` files (Jira MCP tool
  names and field mapping, the exact output contract, a full worked run) that
  `SKILL.md` reads on demand. Holds the same invariants as the rest of the tool: Jira
  access is **read-only** unless you ask and confirm; the MCP connection is your
  host's, so `driver.py` still touches no network and stores no credentials; and it
  emits **one** `task.md`, flagging a multi-unit split rather than decomposing.
- **`install.py` ships agent skills — to your repo, not into your agent's config.**
  Skills land as `skills/<name>/` beside `driver.py`, and the installer stops there:
  nothing is written to `.claude/`, `.cursor/`, or `~/`. Which agent you run and
  whether its skills live per-repo or globally is your setup, not an installer's guess
  — each skill's `INSTALL.md` maps the options and the copy is a one-liner. The
  shipped set is derived at runtime (`shipped_skills()` / `skill_files()` /
  `skill_dirs()`), so install and uninstall share one source of truth and a new skill
  needs only a folder with a `SKILL.md`. Unlike `prompts/` — clobbered on upgrade
  because `verify.md` and the driver's parser are one contract — skills carry no such
  contract and teams do edit them, so **both directions are guarded by the same
  byte-compare** (`_same_bytes`): re-installing upgrades only the files still identical
  to what we shipped and reports the ones you changed, and uninstall removes only
  untouched copies, keeping an edited `SKILL.md` with its folder. Skills are added to
  `.git/info/exclude` **per skill** (`/skills/jira-to-task/`, never `/skills/`), so a
  project's own top-level `skills/` is never shadowed — the same care already taken
  with `prompts/`.
- **`author` subcommand — turn a Jira story / requirements doc into a `task.md`.**
  `python driver.py author --from STORY-123.txt` (or pipe the story on stdin) runs an
  interactive Claude pass that drafts a well-formed `task.md` — unambiguous goal,
  numbered independently-checkable acceptance criteria, in/out of scope, constraints —
  asking only the clarifying questions whose answers would change the task, then writing
  the file the rest of the loop already depends on. It's a **standalone** step (a sibling
  of `doctor`), not part of the plan/execute/verify loop, and the author agent's
  readiness bar is literally the clarity gate's, so an authored task sails through
  `clarify` with few or no follow-ups. Stays true to the invariants: **standard library
  only, no network, no credentials** (local file or stdin in — no Jira API); **one**
  focused `task.md` out (if a story spans several units it prints a *suggested split* and
  tells you to loop `run` per unit — it never auto-decomposes); and **non-destructive**
  (won't overwrite an existing `task.md` without a `[y/N]` confirm or `--force`). New
  flags: `--from <path>`, `--author-model` (default `sonnet`), `--force`; reuses `--task`
  as the output path. Piping the story on stdin makes the run one-shot best-effort
  (stdin isn't a TTY, so the live Q&A can't run) — use `--from <path>` for the
  interactive loop. Author-stage Q&A is kept in a separate `.loop/author_answers.md`, so
  it never pollutes the run's `clarifications.md`.

## [0.4.0] — 2026-07-01

### Added

- **Interactive `run` now walks you through all five models + the executor.** The
  run-settings wizard (shown for any knob you don't pass on an interactive `run`)
  previously only asked for executor / impl-model / max-iters / max-cost; it now also
  offers **clarify-model, plan-model, verify-model** — a numbered menu (opus/sonnet/
  haiku, or type any slug), Enter accepts the default. The three model flags now
  default to `None` like the other prompt-able knobs; headless/CI/`--dry-run` still
  never prompt and use the module defaults.
- **Per-iteration "files changed" line + a config recap + running cost.** Verify now
  logs the files the executor touched this run (`git diff --name-status`,
  e.g. `files changed (3): M Collector.swift  A CollectorTests.swift`); the run opens
  with a one-line recap of the active models/executor/cost-cap; each iteration prints
  the cumulative Claude spend. Optional ANSI colour on the phase banners / status
  prefixes, off when stdout isn't a TTY or `NO_COLOR` is set (never touches files).
- **`make release VERSION=X.Y.Z`.** Automates the mechanical half of a release: runs
  the tests, bumps `driver.py`'s `__version__`, turns the CHANGELOG's `[Unreleased]`
  section into a dated `[VERSION]` one (leaving a fresh empty `[Unreleased]`), then
  commits and tags `vVERSION`. It deliberately **stops before pushing** — the push
  and the GitHub release (with your own notes) stay manual, and the commands are
  printed. Refuses on a dirty tree, off `main`, a bad/duplicate version, or an empty
  `[Unreleased]`, and leaves the repo untouched when it refuses.

### Fixed

- **Clarity gate no longer stops with an empty `clarifications_needed.json`.** When
  the triage model set `ready:false` but returned no usable questions (none, or under
  a wrong key like `"clarifications"`), the loop halted and wrote an empty questions
  file — looking like "the clarity model failed." Now: if it's not-ready with no
  questions *and* no issues, the gate warns and proceeds (like the existing non-JSON
  fallback); with issues but no questions it surfaces them and halts cleanly; the halt
  also explains it's writing a file because there's no terminal to prompt in (or the
  clarification rounds ran out) — reducing the "sometimes on the console, sometimes in
  a file" confusion (that split is just TTY vs non-TTY). `prompts/triage.md` is also
  hardened: the questions key is fixed verbatim, `ready:false` must carry at least one
  question (else return `ready:true`), with a worked not-ready example.
- **Runs on a repo with no commits.** `capture_baseline` used `git rev-parse HEAD`,
  which has no answer in a freshly `git init`'d repo — so the loop crashed with a raw
  `git rev-parse HEAD failed: ... unknown revision 'HEAD'` traceback, *after* paying
  for the clarity gate. It now snapshots the start state with `git add -A; git
  write-tree` (a tree object, non-committing), which works with or without commits.
  Bonus: the baseline is now the actual loop-start state, so the verified diff
  isolates the executor's edits from any pre-existing *uncommitted* work (identical to
  before for a clean repo, where the index equals `HEAD`). `capture_baseline` is also
  wrapped so a genuinely broken git repo fails cleanly instead of via traceback.

## [0.3.1] — 2026-07-01

### Fixed

- **Uninstall no longer deletes your pre-existing files.** The guarded uninstall
  removed any user file that was git-tracked-and-clean ("recoverable"). But
  git-clean can't tell the tool's file from your own: a `AGENTS.md` you had *before*
  installing (install seeds it only if absent, so it may never have been ours) is
  committed-and-unmodified, so uninstall deleted it — you had to `git restore` your
  own file. A guarded user file is now removed **only when it's byte-identical to the
  tool's seed** (an untouched copy) or with `--force`. Files with no seed —
  `task.md`, `context.md`, `clarifications.md` — are removed only with `--force`.
  Tool files and generated artifacts are unaffected (still removed outright).
- **The loop no longer stages the tool's own files into your repo.** The verifier's
  diff is built with `git add -A` (to capture files the executor creates), which also
  swept up `driver.py`, `executors.py`, `prompts/`, and the per-run artifacts — so
  they polluted the verified diff and, once `install.py --uninstall` deleted them,
  lingered as "deleted but staged" ghosts in `git status`. The installer now adds
  those paths to the target's `.git/info/exclude` (a local ignore — no visible change
  to your tracked `.gitignore`), so `git add -A` stages **only your project edits**.
  Uninstall removes that block and unstages exactly the files it deletes (tool,
  artifacts, or an untouched seed), so the repo ends clean — including for repos
  already in the broken state. The driver's `git add -A` is unchanged. Already
  affected and keeping the tool? Re-run `install.py` (adds the exclude), then
  `git rm -r --cached --ignore-unmatch driver.py executors.py prompts __pycache__ .loop plan.md verdict.json clarifications_needed.json`
  once to drop the stale staged copies.

## [0.3.0] — 2026-06-30

### Added

- **`install.py` — a cross-platform installer.** Stdlib-only Python that does
  everything `install.sh` did (copy tool files, seed `AGENTS.md`/`*.example` only if
  absent, and the tiered, git-guarded `--uninstall` with `--dry-run`/`--force`), but
  runs the same on Windows (cmd/PowerShell), macOS, and Linux — no bash needed, since
  Python is already the one prerequisite. `install.sh` is now a thin shim that
  forwards to it, so there's a single implementation; `make install`/`make uninstall`
  call `install.py` directly. The guard decision (`removal_reason`) is pure and
  unit-tested; the shipped prompt set is derived from `prompts/*.md`.

### Fixed

- **Test gate falls back to `sh -c` on a bash-less POSIX host** (Alpine, distroless,
  busybox), mirroring the `cmd /c` fallback already used on Windows. `run_tests`
  previously always shelled out to `bash -lc` on POSIX, so a `--test-command` on a
  bash-less Linux died with `command not found: bash`. Shell preference is now
  bash → (`cmd.exe` on Windows | `sh` on POSIX), and the missing-bash note names the
  shell actually used.

### Changed

- **CI runs across ubuntu, windows, and macos** (was ubuntu-only). The byte-compile
  and unit tests now run on all three, so the cmd.exe / `PATH` / encoding class of
  platform regression fixed in 0.2.1 is caught automatically rather than by a human
  tester. (macOS skips py3.8/3.9, which arm64 runners don't provide; both stay
  covered on ubuntu + windows.)

## [0.2.1] — 2026-06-30

### Added

- **`install.sh --uninstall` (and `make uninstall`).** Removes the tool from a
  target repo as the inverse of install, in tiers: tool files (`driver.py`,
  `executors.py`, the shipped `prompts/*.md`, `__pycache__`) and generated artifacts
  (`.loop/`, `plan.md`, `verdict.json`, `clarifications_needed.json`) go outright;
  user content (`AGENTS.md`, `task.md`, `context.md`, `clarifications.md`, the
  `*.example` seeds) is **guarded** — deleted only when byte-identical to the seed or
  git-tracked-and-clean (recoverable via `git restore`), otherwise kept and reported.
  `--dry-run` previews without deleting; `--force` overrides the guard. The installer
  is deliberately asymmetric (seed-if-absent, never-clobber), so the uninstall refuses
  to destroy modified or untracked work by default. The file list is shared with
  install — the shipped prompt set is derived from `prompts/*.md`, not hardcoded.
- **`--clarify-model` flag.** The clarity gate's model was hardcoded to `haiku`
  (the `CLARIFY_MODEL_NAME` module constant) with no way to override it per run,
  unlike `--plan-model` and `--verify-model`. It now has a matching flag, wired the
  same way (a passed value overrides the constant; never prompted). Use a stronger
  clarify model when the gate is mis-judging task readiness.

### Fixed

- **Verifier verdict survives a prose preamble/epilogue around the JSON.** A strong
  verify model (observed with `--verify-model opus`) sometimes narrates despite the
  JSON-only contract — e.g. `All criteria are satisfied and tests pass.\n\n{...}` —
  and `parse_verdict` (which only stripped ```` ``` ```` fences) failed with
  `verifier did not return valid JSON`, reporting a **passing** run as `error`.
  It now falls back to extracting the first balanced JSON object from the text (new
  pure `_extract_json_object`, via `json.JSONDecoder().raw_decode`), and rejects
  valid-but-non-object JSON explicitly. Not platform-specific. `verify.md` already
  says "Output ONE JSON object and NOTHING else"; this hardens the parser for when a
  model ignores it.
- **Windows: the test gate no longer hard-requires `bash`.** `run_tests` shelled
  out to `bash -lc <cmd>` unconditionally, so on a Windows box without Git Bash/WSL
  the loop reached VERIFY and then died with `command not found: bash`. It now
  prefers `bash -lc` on every platform (so one `--test-command` stays portable) and
  falls back to `cmd /c` on Windows when `bash` isn't on `PATH`, logging a note that
  cmd.exe shell semantics differ. macOS/Linux are unchanged. New pure
  `test_shell_argv` helper (unit-tested); the `--dry-run` preview shows which shell
  the gates run under.
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

- **Renamed the project `agentic-loop` → `agents-collab`** (matching the repo) for one
  consistent name across `--version`, the installer messages, the Makefile, and the
  docs. `python driver.py --version` now prints `agents-collab 0.2.1`.
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
