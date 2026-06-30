# Example: Roman numerals

The smallest task that still exercises the whole **plan → execute → verify** loop
against an objective gate. A real run produced everything in [`run/`](run/).

## The task

Implement `to_roman(n)` in [`roman.py`](roman.py) (a stub that raises
`NotImplementedError`) so it converts integers to Roman numerals and the test gate
passes. The full spec is in [`task.md`](task.md); the acceptance gate is
[`test_roman.py`](test_roman.py) — a stdlib script (no pytest) that exits non-zero
until the function is correct.

```
roman.py        to_roman(n) — stub to implement   (the only file the task changes)
test_roman.py   the acceptance gate (the spec; do not edit)
task.md         goal + 3 checkable acceptance criteria
context.md      one-paragraph architecture map for the planner
```

## What the loop did (real run, 2026-06-27)

| Step | Engine | Result |
| --- | --- | --- |
| CLARITY | claude `haiku` | task is clear enough to plan ✓ |
| PLAN | claude `opus` | wrote [`run/plan.md`](run/plan.md) — a greedy value/symbol table |
| EXECUTE | `cursor` `composer-2.5` | edited `roman.py` — see [`run/roman.diff`](run/roman.diff) |
| TEST GATE | `python3 test_roman.py` | `TESTS: PASSED` — see [`run/test_output.txt`](run/test_output.txt) |
| VERIFY | claude `haiku` | **PASS**, all criteria met — see [`run/verdict.json`](run/verdict.json) |

Converged in **one iteration**. Claude spend: **$0.25** (plan on opus was ~$0.19
of it; verify on haiku ~$0.03). The cursor execution bills separately on its own
account. The change `cursor` produced:

```python
    if n < 1 or n > 3999:
        raise ValueError("n must be in 1..3999")

    pairs = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    result = ""
    for value, symbol in pairs:
        while n >= value:
            result += symbol
            n -= value
    return result
```

The verifier didn't take this on faith — `run/verdict.json` cites, per criterion,
the exact lines and the test output that satisfy it.

## Same task, every executor

The execute step is swappable — planning and verification stay on Claude. This one
task was run end-to-end on each backend reachable at the time:

| Executor | Model | Result |
| --- | --- | --- |
| `cursor` | `composer-2.5` | ✅ PASS — the run captured in [`run/`](run/) |
| `claude` | `haiku` | ✅ PASS |
| `antigravity` (`agy`) | auto-selected | ✅ PASS (after the driver's stdin fix) |
| `codex` | auto-selected (gpt-5.5) | ✅ PASS |

All of them produced essentially the same greedy value/symbol implementation —
which is the point: a cheap editor does the typing, and the same Claude verifier
judges the result the same way regardless of who wrote it.

## Run it yourself

From a copy of this folder (the loop edits files and needs an isolated git repo, so
don't run it inside the agents-collab repo itself):

```bash
cp -r examples/romannumbers /tmp/romannumbers && cd /tmp/romannumbers
python ../../path/to/agents-collab/install.py .   # drop the tool in (driver.py, prompts, …)
git init && git add -A && git commit -m baseline
python driver.py doctor --executor cursor          # check your CLIs first
python driver.py --executor cursor --impl-model composer-2.5 \
  --plan-model opus --verify-model haiku \
  --test-command "python3 test_roman.py" \
  --max-iterations 3 --max-cost-usd 2.00
```

Swap `--executor`/`--impl-model` for any backend you have (`codex`, `claude`, …).

## What this trial shook out (and we fixed)

This run is also the reason the tool got more robust — point it at real CLIs and
the drift shows up fast. All three were fixed on the branch that added this example:

- **antigravity (`agy` 1.0.13):** the old adapter's `--headless --approve all` flags
  no longer exist; current `agy` uses `--print <prompt>` + `--dangerously-skip-permissions`
  (and needs its folder trusted for headless use).
- **codex (`codex-cli 0.142.3`):** dropped the `--ask-for-approval never` flag the
  adapter passed, and refuses to run unless the folder is git + trusted. Adapter now
  omits that flag and makes `--model` optional (falls back to your codex default).
- **driver `--bare`:** claude's `--bare` ("minimal mode") skips plugin/hook-provided
  login on some setups, so the clarify/verify steps returned *"Not logged in"* while
  plan (no `--bare`) worked. The driver no longer uses `--bare`.

That is the whole pitch in miniature: a cheap editor does the typing, an expensive
model judges the result against a real gate, and every step is a file you can read.
