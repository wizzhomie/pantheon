"""A tiny cart whose type annotation is a promise the code does not keep.

`total` says it returns an int and adds a float zero, so it returns 6.0 for three items of 2.
The test asserts `total(...) == 6`, which is true for 6.0, so the suite is green and the
mismatch travels on: a caller that formats the result prints "6.0", and a caller that does
integer arithmetic gets a float back. This is the defect the demo is built around, and it is
invisible to the check that was required.
"""

from __future__ import annotations

from operator import mul


def total(prices: list[int], quantities: list[int]) -> int:
    """Sum price × quantity, annotated as an int because money is counted in cents."""
    return sum(map(mul, prices, quantities)) + 0.0
