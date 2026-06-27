"""Acceptance tests for roman.to_roman — the objective gate for this task.

Stdlib only (no pytest): run `python3 test_roman.py`. Exits 0 if every case
passes, non-zero with a report otherwise. This file IS the spec; do not edit it.
"""

from roman import to_roman

CASES = [
    (1, "I"),
    (3, "III"),
    (4, "IV"),
    (9, "IX"),
    (14, "XIV"),
    (40, "XL"),
    (90, "XC"),
    (400, "CD"),
    (900, "CM"),
    (1987, "MCMLXXXVII"),
    (2024, "MMXXIV"),
    (3999, "MMMCMXCIX"),
]


def main():
    failures = []

    for n, expected in CASES:
        try:
            got = to_roman(n)
        except Exception as e:  # noqa: BLE001 - test harness reports any failure
            failures.append(f"to_roman({n}) raised {type(e).__name__}: {e}")
            continue
        if got != expected:
            failures.append(f"to_roman({n}) = {got!r}, expected {expected!r}")

    for bad in (0, -1, 4000):
        try:
            to_roman(bad)
        except ValueError:
            pass
        except Exception as e:  # noqa: BLE001
            failures.append(
                f"to_roman({bad}) raised {type(e).__name__}, expected ValueError"
            )
        else:
            failures.append(f"to_roman({bad}) returned a value; expected ValueError")

    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)

    print(f"OK: {len(CASES)} conversions + 3 range checks passed")


if __name__ == "__main__":
    main()
