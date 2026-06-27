## Objective

Replace the `NotImplementedError` stub of `to_roman(n)` in `roman.py` with a working implementation that converts any integer in 1..3999 to its standard subtractive Roman-numeral string, and raises `ValueError` for any `n < 1` or `n > 3999`. Done means `python3 test_roman.py` prints the OK line and exits 0, with no other file touched.

## Affected files

- `roman.py` — implement the body of the existing `to_roman` function (the only in-scope change).

## Steps

1. In `roman.py`, keep the existing function signature `def to_roman(n: int) -> str:` and its docstring. Replace only the `raise NotImplementedError` body.
2. Add a range guard first: if `n < 1 or n > 3999`, `raise ValueError(...)` with a short message. This must come before any conversion logic so out-of-range inputs never produce a string.
3. Define an ordered list of (value, symbol) pairs covering the subtractive forms, from largest to smallest: `(1000,"M"), (900,"CM"), (500,"D"), (400,"CD"), (100,"C"), (90,"XC"), (50,"L"), (40,"XL"), (10,"X"), (9,"IX"), (5,"V"), (4,"IV"), (1,"I")`.
4. Greedily build the result: iterate the pairs, and while `n >= value`, append `symbol` to the output and subtract `value` from `n`. Return the accumulated string.
5. Do NOT change `test_roman.py`, do not add imports or third-party dependencies, and do not add any second public function — keep the change confined to the body of `to_roman`. Standard library only, Python 3.8+ compatible.

## Verification mapping

- C1 (correct numerals for 1..3999, subtractive) → the greedy pairs table in Step 3 includes the subtractive forms (CM, CD, XC, XL, IX, IV); the conversion cases in `test_roman.py` (e.g. 4→"IV", 1987→"MCMLXXXVII", 3999→"MMMCMXCIX") pass.
- C2 (`ValueError` out of range) → the guard in Step 2; `to_roman(0)`, `to_roman(-1)`, `to_roman(4000)` each raise `ValueError`.
- C3 (test gate exits 0) → run `python3 test_roman.py`; it prints `OK: 12 conversions + 3 range checks passed` and exits 0.

## Risks

- None significant. The value/symbol table must include all six subtractive pairs and be in strictly descending order — a missing pair (e.g. omitting `CD`/`XL`) or wrong order is the only realistic way to fail C1; double-check the table against Step 3 verbatim.
- Ensure the range check precedes the conversion loop, otherwise C2 could regress while C1 still passes.
