"""
Executor adapters: the EXECUTE step of the loop is pluggable.

The executor contract is deliberately tiny — that's why almost any agentic
coding CLI drops in:
  1. read plan.md from the shared git workspace
  2. apply edits to files, with NO interactive approval prompts (headless)
  3. exit 0 on success
The driver computes the git diff and Claude verifies it afterward, so the
executor needs to do nothing but edit files and return.

Each adapter is `build(model, prompt) -> argv list`. The driver runs that argv
headless with a timeout. Choose the backend with EXECUTOR_BACKEND in driver.py
(or --executor on the CLI). Plan + verify always stay on Claude.

Per-backend notes live next to each adapter. Flags for these third-party CLIs
move fast — verify against the tool's own docs before a long unattended run.
"""


def _cursor(model, prompt):
    """Cursor CLI. -p print mode + --force applies edits. Auto-reads AGENTS.md
    and .cursor/rules. Auth: `cursor-agent login` or CURSOR_API_KEY."""
    return [
        "cursor-agent",
        "-p",
        "--force",
        "--model",
        model,
        "--output-format",
        "text",
        prompt,
    ]


def _claude(model, prompt):
    """Claude Code AS THE EXECUTOR (claude + claude). Unlike the read-only
    planner, it must WRITE files, so it needs edit permission: acceptEdits
    auto-approves Edit/Write without prompting. Auto-reads CLAUDE.md / AGENTS.md.
    Returns a JSON envelope on stdout (see JSON_ENVELOPE_BACKENDS)."""
    return [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "json",
    ]


def _codex(model, prompt):
    """OpenAI Codex CLI, `codex exec` (the non-interactive one-shot mode).
    `--sandbox workspace-write` confines edits to the repo; in exec mode codex
    applies them without an interactive approval prompt. Auto-reads AGENTS.md — THE
    SAME FILE Cursor uses, so your standing rules are shared for free. Final message
    -> stdout, progress -> stderr. Auth: `codex login` (ChatGPT) or API key.

    Pass an explicit model with --impl-model (e.g. gpt-5.4, gpt-5.3-codex), or use
    'default'/'auto' to let codex pick the model from ~/.codex/config.toml.

    NOTE (codex-cli 0.142.x): older versions took `--ask-for-approval never`; that
    flag was removed, and exec now refuses to run unless the folder is a git repo
    AND trusted by codex (approve it once interactively, else: "not inside a trusted
    directory"). Verify flags against `codex exec --help` before a long run."""
    argv = ["codex", "exec", "--sandbox", "workspace-write"]
    if model and model.lower() not in ("default", "auto"):
        argv += ["--model", model]
    argv.append(prompt)
    return argv


def _gemini(model, prompt):
    """Legacy Gemini CLI (`gemini`).

    IMPORTANT: Google retired the free/Pro/Ultra Gemini CLI on 2026-06-18 and
    replaced it with Antigravity CLI (`agy`). This adapter targets the legacy
    `gemini` binary, which still works if you have a paid GEMINI_API_KEY.
    If you have migrated, use EXECUTOR_BACKEND="antigravity" instead.

    -p = headless; --yolo auto-approves all tool actions (file edits + shell)
    and turns on its sandbox by default; -m selects the model.
    Auto-reads GEMINI.md (note: different context filename from AGENTS.md)."""
    return [
        "gemini",
        "-p",
        prompt,
        "-m",
        model,
        "--yolo",
        "--output-format",
        "json",
    ]


def _antigravity(model, prompt):
    """Antigravity CLI (`agy`) — Gemini CLI's replacement as of 2026-06-18.

    Different surface from `gemini`, and changing fast — VERIFY against current
    `agy --help` before scripting. Flags as of agy 1.0.13:
      * `--print <prompt>` is the headless one-shot mode (the prompt is the FLAG'S
        VALUE, not a positional — the old `--headless`/`--approve` flags were
        removed and now error).
      * `--dangerously-skip-permissions` auto-approves tool actions (file edits),
        replacing the old `--approve all`.
      * the model is auto-selected; no model flag is passed here.
    NOTE: headless `agy` still requires its folder/project to be set up and trusted
    for non-interactive use — otherwise `--print` can hang waiting on first-run
    setup. Approve the folder in `agy` interactively once before an unattended run.
    """
    return ["agy", "--dangerously-skip-permissions", "--print", prompt]


# name -> adapter
EXECUTORS = {
    "cursor": _cursor,
    "claude": _claude,
    "codex": _codex,
    "gemini": _gemini,
    "antigravity": _antigravity,
}

# Backends whose stdout is a JSON envelope
# (driver parses it for result + cost).
# Everything else is treated as plain text.
JSON_ENVELOPE_BACKENDS = {"claude"}
