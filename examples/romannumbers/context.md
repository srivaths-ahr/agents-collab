# Context

A tiny single-module Python library — the smallest thing that still exercises the
full plan → execute → verify loop against an objective test gate.

## Layout
- `roman.py` — the library. `to_roman(n)` is currently a stub that raises
  `NotImplementedError`. This is the only file the task should change.
- `test_roman.py` — a stdlib test runner (no pytest) that defines the expected
  behavior. It is the spec and the acceptance gate; do not edit it.

## Conventions
- Standard library only, Python 3.8+.
- One public function, `to_roman`.

## How to verify
- Run `python3 test_roman.py`. It exits 0 when all conversions and range checks
  pass, non-zero otherwise.
