# Task

## Goal
Implement `to_roman(n)` in `roman.py` so it converts an integer to its Roman
numeral, making the existing test suite pass.

## Acceptance criteria
1. C1: `to_roman(n)` returns the correct Roman-numeral string for every integer
   in 1..3999, using standard subtractive notation (e.g. 4 → "IV", 9 → "IX",
   1987 → "MCMLXXXVII", 3999 → "MMMCMXCIX").
2. C2: `to_roman(n)` raises `ValueError` when n is out of range (n < 1 or
   n > 3999).
3. C3: the test gate `python3 test_roman.py` exits 0 (all cases pass).

## In scope
- `roman.py`

## Out of scope
- Do NOT modify `test_roman.py` — it is the spec.
- Do NOT add third-party dependencies.

## Notes / constraints
- Standard library only, Python 3.8+.
- Keep the change to the single `to_roman` function.
