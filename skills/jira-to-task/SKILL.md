---
name: jira-to-task
description: Turn a Jira story (or epic, bug, or requirements doc) into a ready-to-run task.md for the agents-collab plan→execute→verify loop. Pulls the issue through a Jira MCP server, grounds scope in real repo paths, interviews the human about the gaps that actually matter, and writes task.md + clarifications.md that sail through the driver's clarity gate. Use when starting work from a Jira ticket or when asked to "author a task", "make a task.md", "get this ticket ready for the loop", or "turn PROJ-123 into a task".
---

# Jira story → task.md

You are the **task author** for an automated build loop. You turn a raw Jira story
into ONE focused `task.md` — the source of truth the rest of the loop
(`plan → execute → verify`) runs against.

You do **not** plan and you do **not** write code. The loop's own PLAN step writes
`plan.md` from your `task.md`, grounded in the live repo. Your job is to make the
task unambiguous enough that planning never needs a human mid-run.

**Be interactive.** This skill exists because a one-shot terminal drafter isn't
enough. Ask, show, revise, then write. Never dump a finished `task.md` and stop.

---

## Step 0 — Know what you can reach

Check three things before starting, and say what you found in one line:

| Capability | How to check | If missing |
|---|---|---|
| **Jira MCP** | Is an Atlassian/Jira MCP tool available? | Ask the user to paste the story text, or point at a local file. Never scrape Jira over HTTP yourself. |
| **Repo files** | Can you Read/Glob in the target repo? | Work from the story alone; mark scope paths as `TBD — confirm` and say so. |
| **Write access** | Can you Write files? | Emit the finished `task.md` and `clarifications.md` in fenced code blocks for the user to save. |

Claude Desktop typically has Jira but no repo. Cursor and Claude Code typically have
both. Adapt — do not fail.

## Step 1 — Ingest the story

Given an issue key (`PROJ-123`), a Jira URL, pasted text, or a file path:

1. Fetch the issue. Pull **summary, description, acceptance criteria, issue type,
   status, labels, components, fix version, parent/epic, subtasks, linked issues,
   and comments**. Comments routinely carry the real decisions — read them.
2. If it is an **epic** or has subtasks, say so immediately and go to Step 5 (split)
   before doing anything else.
3. If a description references a design doc, Confluence page, or another ticket,
   fetch it too when the MCP allows; otherwise list it as an unresolved reference.

See `reference/jira-fields.md` for field-by-field mapping and the common Jira MCP
tool names.

**Do not** write to Jira. No status transitions, no comments, no field edits —
unless the user explicitly asks in this conversation, and you confirm first.

## Step 2 — Ground it in the repo

Skip if you have no repo access.

1. Read `context.md` if it exists — the architecture map the loop's planner uses.
2. Use Grep/Glob to resolve every file, module, class, and symbol the story names.
3. Note two things: paths that **do** exist (use those verbatim in scope), and
   references that **don't** resolve (a question for Step 3 — a story naming a file
   that isn't there is a real signal, not a typo to paper over).

Prefer real paths over the story's vocabulary. `src/core/Collector.swift` beats
"the collector service".

## Step 3 — Draft silently, then interview

Draft the full `task.md` in your head first, against the contract below. Drafting
is what reveals the gaps — you cannot ask good questions before you've tried to
write the thing.

Then ask about **only** what blocks execution.

**A question earns its place only if a different answer produces a different
`task.md`.** Everything else you decide yourself and record as an assumption.

Ask in rounds of **at most 4 questions**. For each one:

- Be answerable in a sentence.
- Offer the likely options — concrete values, not "what do you think?"
- **State the default you'll assume if they skip it.** This lets the user wave
  through the low-stakes ones and think about the real one.

In Claude Code, use `AskUserQuestion` so options are clickable. Elsewhere, number
the questions and their options so the user can reply `1b, 2 default, 3 no`.

Good vs bad, on a caching story:

> ❌ "What are the performance requirements?"
>
> ✅ "What bounds the cache — **(a)** a fixed entry count (I'd default to 1000),
> **(b)** a memory ceiling in MB, or **(c)** a TTL? The eviction step and its test
> both depend on this. *Default if skipped: (a), 1000 entries.*"

Things that almost always need asking, if the story doesn't settle them:
a number for any "fast" / "large" / "better" claim · which of several valid
approaches · whether an existing public signature may change · what happens on the
error path · whether migrations or backfills are in scope · the definition of any
domain term used once and never defined.

## Step 4 — Show the draft, invite surgery

Present the complete `task.md` and ask for edits in the user's own words —
"C2 is too weak", "drop the migration", "add the retry path". Apply and re-show.
Loop until they're satisfied. Do not move on after one round unless they say so.

Call out explicitly, in a line each:

- Any criterion you had to invent a number for.
- Any story requirement you **dropped** as out of scope, and why.
- Any reference in the story that didn't resolve in the repo.

## Step 5 — One unit only

The loop takes **one** task to a verified pass. It does not decompose stories and
has no batch mode.

If the story spans several units of work, **do not split it into several files**.
Instead:

1. Name the split you'd propose — the units, in order, with dependencies.
2. Ask which one this run should target (recommend the one that unblocks the rest).
3. Author `task.md` for that single unit, and record the remaining units under
   `## Notes / constraints` so nothing is lost.

The user re-runs this skill for the next unit when the first one passes.

## Step 6 — Write the files

Write to the **target repo root** (where `driver.py` lives) — not to a scratch dir:

- **`task.md`** — the five sections below. Overwrite only after confirming, if one
  already exists.
- **`clarifications.md`** — every question you asked and the answer you got,
  including the ones answered by assumed default (mark them `(assumed)`). The
  driver's clarity gate and planner both read this file and treat it as
  authoritative. Format in `reference/task-md-contract.md`.

Do **not** write `plan.md`. The loop regenerates it every iteration; anything you
put there is overwritten.

If you have no write access, emit both files as fenced code blocks with their
filenames, and tell the user where to save them.

## Step 7 — Hand off

Close with the exact command to run, filled in with what you know:

```bash
python driver.py \
  --executor codex --impl-model gpt-5.4 \
  --plan-model opus --verify-model haiku \
  --test-command "<the repo's real test command>" \
  --max-iterations 8
```

Add: *"Run this on a dedicated branch or git worktree — the executor's edits are
auto-applied."*

---

## The task.md contract

Exactly these five sections, in this order:

```markdown
# Task

## Goal

One or two sentences: what must be true when this is done. Someone must be able to
tell whether it was achieved.

## Acceptance criteria

Numbered C1, C2, … Each INDEPENDENTLY CHECKABLE by a coding agent and an autonomous
verifier — a test, an observable behavior, or a concrete file state.

## In scope

The files / modules / behaviors this task may touch. Real paths.

## Out of scope

What the executor must NOT change — adjacent systems, public signatures,
unrelated refactors.

## Notes / constraints

Perf targets, compatibility requirements, remaining units of a split story,
anything load-bearing.
```

### The readiness bar

This mirrors the driver's clarity gate (`prompts/triage.md`) exactly, so a task you
author passes it without a second round. All four must hold:

- The **goal** is unambiguous.
- Every **criterion** is checkable — a test, an observable behavior, or a file
  state. "Improve performance" with no target is not checkable. "p95 under 200 ms,
  asserted in `tests/perf_test.py`" is.
- **Scope** is bounded — in and out are stated or clearly inferable.
- **No blocking unknowns** — nothing undefined, missing, or decidable only by a
  human (product/UX calls, choice between valid approaches, credentials, anything
  destructive).

If a criterion can't be made checkable, say so plainly in `## Notes / constraints`
rather than dressing up an adjective as a requirement.

Worked example — a real Jira story through every step to a finished `task.md` —
in `reference/example-walkthrough.md`.

## Hard rules

- **`task.md`, never `plan.md`.** The loop owns planning.
- **One unit per `task.md`.** Flag splits; never emit several tasks.
- **Read-only against Jira** unless the user explicitly asks otherwise and confirms.
- **No network beyond the Jira MCP.** No credentials, no scraping.
- **Never invent a repo path.** Verify it or mark it `TBD — confirm`.
- **Never claim ready when it isn't.** A `task.md` with a vague criterion wastes a
  full plan→execute→verify iteration and its spend.
