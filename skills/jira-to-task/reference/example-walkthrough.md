# Worked example — PAY-482 from ticket to task.md

A synthetic but realistic run, showing the shape of each step. The ticket is
deliberately typical: real intent, unusable acceptance criteria, one hidden decision
in the comments, and slightly more work than one loop run.

---

## Step 1 — What the MCP returned

```
PAY-482  ·  Story  ·  In Progress  ·  components: payments, api

Summary:  Payment retries are hammering the provider

Description:
  When the provider times out we retry immediately and forever. Support is seeing
  duplicate charges. We should back off properly and give up eventually. Also the
  errors we log are useless, you can't tell a timeout from a decline.

Acceptance criteria:
  - Retries use exponential backoff
  - Retries stop after a reasonable number of attempts
  - Errors are logged with enough detail to debug
  - No duplicate charges

Comments:
  [Priya, 3 days ago] Talked to the provider — they want us under 5 req/s per
  merchant and they support idempotency keys now, header is `Idempotency-Key`.
  [Dan, 2 days ago] Let's not do the rate limiting in this ticket, that's PAY-501.
```

## Step 2 — Grounding in the repo

```
context.md         → payments live in src/payments/, HTTP in src/payments/client.py
grep "retry"       → src/payments/client.py:88  _retry_loop()  (bare while True)
grep "Idempotency" → no hits — the header is not sent anywhere today
grep class Payment → src/payments/errors.py  PaymentError (base), PaymentDeclined
tests              → tests/test_payments.py, pytest
```

Two findings worth surfacing: `_retry_loop()` is genuinely unbounded, and the
idempotency key Priya mentions does not exist in the codebase at all — that is new
work, not a config change.

## Step 3 — The interview

Four ACs, three of them unmeasurable. The draft exposed exactly three questions
whose answers change the file:

> **Q1. What is "a reasonable number of attempts"?**
> (a) 3 attempts total, (b) 5 attempts, (c) a 30-second total deadline regardless of
> count. The stop condition and its test both hang off this.
> *Default if skipped: (a), 3 attempts.*
>
> **Q2. Idempotency keys — in this ticket or not?**
> Priya says the provider supports `Idempotency-Key`; Dan scoped rate limiting out
> but did not mention this. "No duplicate charges" is only actually achievable with
> it. (a) in scope, (b) out, and C4 becomes "no duplicate *retry* charges" only.
> *Default if skipped: (a) — it is the only way C4 is checkable.*
>
> **Q3. What does "enough detail" mean for the log line?**
> (a) structured fields — `attempt`, `delay_ms`, `error_type`, `merchant_id`;
> (b) just distinguish timeout from decline in the message.
> *Default if skipped: (a).*

**Answers:** Q1 → (b) 5 attempts. Q2 → (a) in scope, keys are the point of the
ticket. Q3 → default (a).

Note what was *not* asked: the backoff multiplier (2× is the obvious default, and
being wrong costs nothing), the log level, whether to use an existing retry library.
Those are recorded as assumptions, not questions.

## Step 4 — What was flagged back

- Invented a number: base delay 200 ms, doubling, ±10% jitter. Nothing in the ticket
  said; the ceiling is what matters and Q1 settled that.
- Dropped: "under 5 req/s per merchant" from Priya's comment — Dan scoped it to
  PAY-501. Recorded in `## Out of scope` so the executor does not drift into it.
- Unresolved reference: none. `Idempotency-Key` is absent from the repo, which is
  the work, not a broken reference.

## Step 5 — Split check

Borderline. Backoff and idempotency keys are separable — but C4 ("no duplicate
charges") is unverifiable without both, and the user confirmed in Q2 that keys are
the point. **One unit.** Structured logging could have been split out; it is one
small change in the same function, so it stays.

Had the user answered Q2 with (b), this would have been two units: backoff first,
then keys.

## Step 6 — The files written

**`task.md`**

```markdown
# Task

## Goal

Bound and space out payment retries in `src/payments/client.py`, and make retried
charges idempotent, so a provider timeout can no longer produce duplicate charges or
an unbounded retry storm.

## Acceptance criteria

1. **C1 — retries are bounded at 5 attempts.** `_retry_loop()` makes at most 5 total
   attempts (1 initial + 4 retries); after the last failure it raises
   `PaymentUnavailable` rather than looping. Covered by a test in
   `tests/test_payments.py` that asserts the call count.
2. **C2 — backoff is exponential with jitter.** Delay before retry *n* is
   `200ms * 2**(n-1)` with ±10% jitter, capped at 5s. Asserted in
   `tests/test_payments.py` against a patched clock — no real sleeping in tests.
3. **C3 — retried charges carry a stable idempotency key.** Every attempt for a
   single logical charge sends the same `Idempotency-Key` header, generated once per
   `charge()` call; two different `charge()` calls send different keys. Covered in
   `tests/test_payments.py`.
4. **C4 — timeouts are distinguishable from declines in logs.** Each failed attempt
   logs a structured record with `attempt`, `delay_ms`, `error_type`, and
   `merchant_id`; `error_type` is `timeout` for a provider timeout and `declined`
   for a `PaymentDeclined`. Covered by a caplog-based test.
5. **C5 — existing public behavior is unchanged.** `charge()` keeps its current
   signature and its success path is untouched; `PaymentUnavailable` is added to
   `src/payments/errors.py` as a new subclass of `PaymentError`.

## In scope

- `src/payments/client.py` — `_retry_loop()`, and the `Idempotency-Key` header in
  the request builder
- `src/payments/errors.py` — the new `PaymentUnavailable` class
- `tests/test_payments.py` — tests for C1–C4

## Out of scope

- Per-merchant rate limiting (5 req/s) — that is PAY-501, do not implement it here.
- `src/payments/webhooks.py` and refund handling.
- Any change to `charge()`'s signature or to the success path.
- Swapping in a third-party retry library.

## Notes / constraints

- Tests must not sleep. Patch the clock / sleep function.
- Idempotency key format: the provider accepts any string up to 255 chars; a UUID4
  per `charge()` call is fine.
- Assumed without asking: 2× backoff multiplier, ±10% jitter, 5s per-delay cap,
  WARNING log level for failed attempts.
```

**`clarifications.md`**

```markdown
# Clarifications

Source: PAY-482 — "Payment retries are hammering the provider"
Authored: 2026-08-05 via the jira-to-task skill

## Q1 — How many retry attempts?

**Asked:** 3 attempts, 5 attempts, or a 30s total deadline?
**Answer:** 5 total attempts.
**Affects:** C1.

## Q2 — Are idempotency keys in this ticket?

**Asked:** Priya's comment says the provider now supports `Idempotency-Key`; Dan
scoped rate limiting out but not this. In or out?
**Answer:** In scope — it is the point of the ticket. Without it "no duplicate
charges" is not checkable.
**Affects:** C3, C4, and the one-unit decision.

## Q3 — What must the failure log contain?

**Asked:** Structured fields, or just a distinguishable message?
**Answer:** *(assumed)* Structured — `attempt`, `delay_ms`, `error_type`,
`merchant_id`. No answer given; proceeding on this default.
**Affects:** C4.

## Not asked — assumed outright

- Backoff multiplier 2×, ±10% jitter, 5s cap per delay (C2).
- WARNING log level for failed attempts (C4).
- Rate limiting excluded per Dan's comment; tracked in PAY-501.
```

## Step 7 — Handoff

```bash
python driver.py \
  --executor codex --impl-model gpt-5.4 \
  --plan-model opus --verify-model haiku \
  --test-command "pytest -q tests/test_payments.py" \
  --max-iterations 8
```

Run on a dedicated branch or git worktree — the executor's edits are auto-applied.

---

## What made this work

- **The comments changed the task.** Dan's scoped-out rate limiting became
  `## Out of scope`; Priya's idempotency header became C3. Neither is in the
  description or the ACs.
- **Every original AC survived, transformed.** "Reasonable number of attempts" →
  a count and a test. "Enough detail" → four named fields. "No duplicate charges" →
  a stable header across attempts.
- **Three questions, not ten.** Everything decidable without the human was decided
  and written down under "Not asked — assumed outright".
