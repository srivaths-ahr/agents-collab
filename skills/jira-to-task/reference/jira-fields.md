# Jira → task.md field mapping

Read this when fetching an issue in Step 1.

## Finding the right MCP tool

Jira MCP servers differ in naming. Look for a tool matching one of these shapes and
use whichever is present — do not guess a name that isn't in your tool list:

| Server | Fetch one issue | Search |
|---|---|---|
| Atlassian official (Rovo / `mcp-atlassian`) | `getJiraIssue`, `atlassian_getJiraIssue` | `searchJiraIssuesUsingJql` |
| Community `mcp-atlassian` (sooperset) | `jira_get_issue` | `jira_search` |
| Composio / Zapier wrappers | `JIRA_GET_ISSUE` | `JIRA_SEARCH_ISSUES` |

If a tool takes a `fields` or `expand` argument, ask for the full description,
comments, and subtasks rather than the summary-only default. If the fetch returns
Atlassian Document Format (ADF) JSON rather than markdown, flatten it to text —
keep bullet lists, headings, code blocks, and checkbox items; drop styling.

If **no** Jira tool is available, say so in one line and ask the user to paste the
story or name a local file. Never construct Jira REST calls yourself, and never ask
for an API token.

## What to pull, and what it becomes

| Jira field | Becomes | Notes |
|---|---|---|
| Summary | Seed for `## Goal` | Rewrite as an outcome, not a title. "Add rate limiting" → "Requests beyond the configured rate are rejected with 429 rather than queued." |
| Description | `## Goal` + raw material for criteria | Usually mixes intent, constraints, and implementation hints. Split them. |
| Acceptance Criteria field / "AC:" section / checklist | `## Acceptance criteria` | The highest-value field. Rarely checkable as written — see below. |
| Issue type | Framing | `Bug` → the criterion is a regression test that fails before and passes after. `Spike` → not loop-runnable; say so. |
| Components / labels | `## In scope` hints | Map to real directories via the repo, never verbatim. |
| Parent / epic link | Split signal | Read the epic for constraints the child assumes. |
| Subtasks | Context, **not** a split signal | Read what they say. Usually workflow tracking ("write tests", "QA") — one unit. Only relevant if they describe changes that can't land together, and even then Step 5 asks rather than decides. |
| Linked issues (`blocks`, `depends on`) | `## Out of scope` or `## Notes` | A still-open blocked-by link is a blocking unknown — ask. |
| Comments | Everything | Decisions, reversals, and "actually we decided X" live here. A late comment overrides the description; say so when it does. |
| Attachments / design links | `## Notes / constraints` | Reference them; you generally can't read images. If a criterion depends on one, ask. |
| Status, assignee, sprint, story points | **Nothing** | Workflow metadata. Never goes in `task.md`. |

## Turning Jira ACs into checkable criteria

Jira acceptance criteria are written for humans. The loop's verifier is not one.
Rewrite each into a test, an observable behavior, or a concrete file state.

| Jira says | `task.md` criterion |
|---|---|
| "The endpoint should be fast" | "C1 — `GET /v1/items` p95 is under 200 ms for a 1000-row fixture, asserted in `tests/perf/test_items.py`." |
| "Handle errors gracefully" | "C2 — a timeout from `PaymentClient.charge()` raises `PaymentUnavailable`, not a bare `TimeoutError`; the caller's retry path is exercised in `tests/test_payments.py`." |
| "Users can filter the list" | "C3 — `GET /v1/items?status=active` returns only rows with `status == 'active'`; unknown values return 400 with an `invalid status` error body. Covered in `tests/test_items_filter.py`." |
| "Refactor for maintainability" | Not checkable. Either find the behavior that must be preserved ("C4 — all existing tests in `tests/test_items.py` pass unchanged") or say in `## Notes` that this can't be verified autonomously. |
| "Should not break existing functionality" | "C5 — no existing public signature in `api/items.py` is modified or removed; only additive changes." |

If a Jira AC maps to nothing checkable and nothing is lost by dropping it, drop it
and say so in Step 4. If dropping it would lose real intent, ask.

## Epics and multi-unit stories

**Default to one unit.** The question is never "how many subtasks does it have?" but
"can these acceptance criteria land and be verified together?"

Real signals it's more than one loop run:

- Acceptance criteria span layers that can't land together — the schema migration
  has to ship before the API that reads it can be tested.
- Two criteria could each pass while the other fails, with no change in common.
- The work spans different repos or services.
- Issue type is `Epic` — usually, though an epic holding one real change is still
  one unit.

**Not signals on their own:** having subtasks (usually workflow tracking), a long
description, phases in the prose, or a lot of acceptance criteria. Five criteria on
one function are one unit.

When a real signal fires, go to Step 5 of `SKILL.md`, which **asks** the user with a
stated default of one unit. Never split silently, and never emit two files.
