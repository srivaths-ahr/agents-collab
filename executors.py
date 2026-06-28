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


def _antigravity(model, prompt):
    """Antigravity CLI (`agy`) — Google's headless agent CLI.

    Changing fast — VERIFY against current `agy --help` before scripting. Flags as
    of agy 1.0.13:
      * `--print <prompt>` is the headless one-shot mode (the prompt is the FLAG'S
        VALUE, not a positional — the old `--headless`/`--approve` flags were
        removed and now error).
      * `--dangerously-skip-permissions` auto-approves tool actions (file edits),
        replacing the old `--approve all`.
      * the model is auto-selected; no model flag is passed here.
    NOTE: `agy --print` blocks reading stdin and will hang (no output) if stdin is
    left open — the driver's run() closes the child's stdin (DEVNULL) so it gets
    EOF and runs headless. Verified end-to-end against agy 1.0.13. (agy may also
    want its folder trusted before it will apply edits.)
    """
    return ["agy", "--dangerously-skip-permissions", "--print", prompt]


# name -> adapter
EXECUTORS = {
    "cursor": _cursor,
    "claude": _claude,
    "codex": _codex,
    "antigravity": _antigravity,
}

# Backends whose stdout is a JSON envelope
# (driver parses it for result + cost).
# Everything else is treated as plain text.
JSON_ENVELOPE_BACKENDS = {"claude"}

# Suggested impl-model per backend, used only to seed the interactive prompt when
# --impl-model is omitted (the driver still falls back to its own default headless).
# Encodes the codex ChatGPT-auth lesson: 'default' lets codex pick the model from
# ~/.codex/config.toml, since naming a model a ChatGPT-plan account can't serve is
# rejected. `antigravity` (agy) auto-selects and ignores any model, so 'default' too.
SUGGESTED_IMPL_MODELS = {
    "cursor": "composer-2.5",
    "claude": "sonnet",
    "codex": "default",
    "antigravity": "default",
}
