# Security Policy

`agentic-loop` runs autonomous AI coding agents against your repository with
**file edits auto-approved and no human in the loop**. Treat it accordingly.

## Trust model — read before running

- **The executor can edit any file in the working tree.** It runs headless with
  approvals disabled (`cursor-agent --force`, `claude --permission-mode acceptEdits`, `codex exec --ask-for-approval never`, `gemini --yolo`,
  `agy --approve all`). Assume it can rewrite anything in the repo.
- **Several backends can also run shell commands.** `codex` (`--sandbox workspace-write`), `gemini` (`--yolo`), and `antigravity` execute commands, not
  just edits. A sandbox confines them to the workspace _only if it holds_ — do not
  rely on it as your sole boundary.
- **Your inputs become prompts to these tools.** `task.md`, `context.md`, and any
  source the agent reads are fed to powerful models. Hostile or accidental
  instructions in those files — or in code the agent reads — can be followed. This
  is prompt injection. Only run against code you trust, or in isolation.

## What the driver guarantees

- **Non-destructive.** The driver stages (`git add -A`) only to compute diffs
  against the baseline commit. It never commits, pushes, resets, or deletes.
- **Bounded.** It stops on `pass`, `blocked`, a stall, the iteration cap
  (`--max-iterations`), or the dollar cap (`--max-cost-usd`).
- **No stored credentials.** Nothing here holds secrets; each CLI authenticates
  through its own login or environment variable.

## What it does NOT guarantee

- It does **not** sandbox the executor itself — that is the backend CLI's job, and
  the shell-capable backends can reach the network and (sandbox permitting) the
  filesystem outside the repo.
- It does **not** scrub secrets from artifacts. The `.loop/` scratch dir
  (`diff.patch`, `executor_output.txt`, raw model output) and `verdict.json` can
  capture secrets present in your code or test output. **Redact before sharing a
  bug report.**

## Running it safely

- Point it only at a **dedicated branch, a `git worktree`, or a disposable
  container** you can throw away.
- Review every diff before merging. The LLM verifier is a strong check, not a
  guarantee — with soft acceptance criteria and no test gate it judges on the diff
  alone and can be wrong or fooled. Give it real, machine-checkable criteria and at
  least one `--test-command` gate.
- Run unattended only after a supervised run on the same class of task.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue:

- Use GitHub's **"Report a vulnerability"** button (the repo's _Security →
  Advisories_ tab), or
- email the maintainer at `<ahr.srivaths@gmail.com>`.

Include the backend and models used, the command you ran, and a minimal
reproduction with anything sensitive redacted. We aim to acknowledge within a few
days and ask for a reasonable window to fix before public disclosure.
