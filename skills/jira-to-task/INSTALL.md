# Installing the `jira-to-task` skill

This folder is a self-contained agent skill. `install.py` dropped it next to
`driver.py` and stopped there on purpose — where your agent reads skills from is
your setup, not ours to guess or to write into.

Pick your host below. All of them read the same `SKILL.md`; nothing here is
host-specific except the path.

## What's in the folder

```
jira-to-task/
├── SKILL.md                        the workflow — this is the skill
├── INSTALL.md                      this file
├── cursor-rule.mdc                 optional pointer, for Cursor's rules system
└── reference/
    ├── jira-fields.md              Jira MCP tool names, field → section mapping
    ├── task-md-contract.md         exact task.md / clarifications.md shapes
    └── example-walkthrough.md      a full worked run, ticket to files
```

`SKILL.md` reads the `reference/` files on demand, so **keep the folder together**.

## Claude Code

Copy the folder into either skills directory — per-project or global:

```bash
mkdir -p .claude/skills && cp -R skills/jira-to-task .claude/skills/      # this repo only
mkdir -p ~/.claude/skills && cp -R skills/jira-to-task ~/.claude/skills/  # every repo
```

Then invoke it with `/jira-to-task PROJ-123`, or just ask in plain language — the
skill's description covers phrasings like "turn PROJ-123 into a task.md".

If you put it in `.claude/skills/` inside the repo the loop runs on, add
`/.claude/skills/jira-to-task/` to your ignores so the loop's `git add -A` doesn't
stage it into a verified diff. (The copy at `skills/jira-to-task/` is already
excluded by `install.py`; a copy you make is not.)

## Cursor

Two ways, depending on your Cursor version:

- **Rules (works everywhere).** Copy the pointer rule into Cursor's rules dir and
  leave the folder where it is:

  ```bash
  mkdir -p .cursor/rules && cp skills/jira-to-task/cursor-rule.mdc .cursor/rules/jira-to-task.mdc
  ```

  The rule tells the agent to read `skills/jira-to-task/SKILL.md`. If you move the
  folder elsewhere, edit the paths at the top of the copied `.mdc` to match.

- **Agent Skills, if your Cursor supports them.** Copy the whole folder into the
  skills directory Cursor documents for your version, and skip the rule file.

## Claude Desktop

Desktop can't read an arbitrary repo, so give it the folder directly — via the
skills section of Settings, however your version accepts them (folder picker or zip
upload):

```bash
cd skills && zip -r jira-to-task.zip jira-to-task
```

Desktop has **no filesystem access to your repo**, so the skill adapts: it does the
Jira fetch and the full interview, then prints `task.md` and `clarifications.md` as
code blocks for you to save into the repo yourself. Its Step 2 (grounding scope in
real file paths) is skipped, so expect to confirm a path or two by hand.

## Other agents

`SKILL.md` is plain markdown with YAML frontmatter and no host-specific calls. Any
agent that can read a file and call your Jira MCP can follow it — point yours at
`SKILL.md` however it takes instructions.

## Prerequisites

- **A Jira MCP server connected to your host.** The skill uses *your* connection; it
  never asks for a token, and `driver.py` itself still touches no network. Without
  one, it falls back to a story you paste or a local file.
- **`context.md` in the target repo** — optional but worth having. It's the
  architecture map the loop's planner uses, and the skill reads it to ground
  `## In scope` in real paths.

## Upgrading

Re-running `python install.py <target>` refreshes this folder **only where your copy
is still byte-identical to what we shipped**. Any file you edited is kept, and the
installer prints its path so you can diff it against the new version in the
agents-collab clone. Copies you made elsewhere (`.claude/skills/`, `~/.claude/`) are
yours — the installer neither updates nor removes them.
