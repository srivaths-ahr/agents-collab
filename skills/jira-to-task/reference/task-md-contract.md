# Output contract — task.md and clarifications.md

Read this in Step 6, before writing.

Both files go in the **target repo root**, alongside `driver.py`. Both are per-run
user content — the installer keeps them out of the loop's `git add -A`.

## task.md

Exactly five `##` sections, in this order, under a `# Task` heading. The driver's
clarity gate reads this file first; the planner reads it every iteration; the
verifier scores the final diff against the criteria in it.

```markdown
# Task

## Goal

Make the Collector SDK safe against oversized and repeated initialization: bound the
payload size with a typed error, and make `configure()` idempotent — without changing
the existing public API surface.

## Acceptance criteria

1. **C1 — `configure()` is idempotent.** Calling `configure(options:)` more than once
   does not re-initialize or duplicate state; the second and later calls are safe
   no-ops. Covered by a test in `CollectorTests`.
2. **C2 — `collect()` bounds payload size.** If an assembled payload exceeds 256 KB
   (measured via `Encoding.encodedByteLength`), `collect()` throws a new
   `PayloadTooLarge` case on the public `Errors` enum instead of enqueuing it.
   Payloads at or under the limit are unaffected. Covered by tests in
   `CollectorTests`.
3. **C3 — public API is unchanged for existing callers.** No existing public
   signature on `Collector` is modified or removed; only additive changes (a new
   error case, internal guards) are allowed.

## In scope

- `src/core/Collector.swift` (guards in `configure()` and `collect()`)
- `src/core/Errors.swift` (the new `PayloadTooLarge` case)
- `tests/CollectorTests.swift` (new tests for C1 and C2)

## Out of scope

- `src/net/Uploader.swift` and retry/backoff behavior.
- Any change to `Payload`'s shape or to existing public method signatures.
- Performance refactors beyond what C1–C3 require.

## Notes / constraints

- `collect()` is on a hot path — the size check must be O(1)-ish, no extra
  serialization passes; reuse `Encoding.encodedByteLength`.
- The 256 KB limit is `256 * 1024` bytes of the encoded payload.
- Do not modify existing tests; add new ones and make them pass.
```

### Rules that matter

- **Criterion ids are `C1`, `C2`, …** The verifier reports per-criterion status
  against these ids, and the planner targets only the failed ones on a re-plan.
  Renumbering between runs breaks that correspondence.
- **Each criterion names its evidence** — the test file, the observable behavior, or
  the file state. A criterion with no stated evidence is one the verifier has to
  guess about.
- **Criteria are independent.** If C2 can only be judged after C1 lands, they are
  one criterion, or the task is two units.
- **`## Out of scope` is load-bearing**, not filler. It is what stops the executor
  from drive-by refactoring, and the verifier flags changes outside it.
- **Real paths only.** Anything you could not resolve in the repo is written as
  `TBD — confirm: <what you looked for>`, never as a plausible-looking invention.

## clarifications.md

Every question you asked and how it was settled. The clarity gate and the planner
both read this file when present and treat it as authoritative — it is how an
answer survives into iteration 5 without a human in the room.

```markdown
# Clarifications

Source: JIRA-1234 — "Harden Collector against oversized payloads"
Authored: 2026-08-05 via the jira-to-task skill

## Q1 — What bounds the payload?

**Asked:** A fixed byte ceiling, or a field-count limit?
**Answer:** 256 KB of encoded payload, measured with the existing
`Encoding.encodedByteLength`. (Confirmed by the user; the ticket said only "too big".)
**Affects:** C2.

## Q2 — May `Errors` gain a case?

**Asked:** `Errors` is public — is adding a case acceptable, or should this reuse
`Errors.invalidPayload`?
**Answer:** Adding `PayloadTooLarge` is fine; the enum is not `@frozen`.
**Affects:** C2, C3.

## Q3 — Does `configure()` need to be thread-safe?

**Asked:** Idempotent only, or idempotent under concurrent calls?
**Answer:** *(assumed)* Idempotent only — single-threaded init is the documented
contract. No answer given; proceeding on this default.
**Affects:** C1.
```

### Rules

- **Record the assumed defaults too**, marked `(assumed)`. A skipped question is
  still a decision, and the gate should see it rather than re-ask.
- **Name which criteria each answer affects.** That is what lets a re-plan after a
  failed verdict pick the answer back up.
- **Never restate the whole task here.** This file is the delta — the questions and
  their answers. `task.md` is the source of truth.
- **Do not confuse it with `.loop/author_answers.md`**, which is the `driver.py
  author` subcommand's own scratch file. This skill writes `clarifications.md`.

## What this skill never writes

- **`plan.md`** — the loop's PLAN step regenerates it on every iteration (including
  after each failed verdict, targeting only the failed criteria). Anything written
  there is overwritten before the executor reads it.
- **`verdict.json`** — the verifier's output.
- **`context.md`** — the architecture map. If it is missing, say so and offer to
  draft one as a separate step; do not silently fold it into `task.md`.
