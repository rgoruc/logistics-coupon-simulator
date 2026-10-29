"""Small number helpers.

They add up numbers strictly left to right and round like JavaScript, so this Python model gives
the same numbers as the HTML version (written in JavaScript) to the last digit. Python's own sum()
uses a more exact algorithm since Python 3.12, which can differ in the last digit.
"""
from __future__ import annotations

import math

NAN = float("nan")


def total(values) -> float:
    s = 0.0
    for v in values:
        s += v
    return s


def mean(values) -> float:
    values = list(values)
    return total(values) / len(values) if values else NAN


def rms(values) -> float:
    values = list(values)
    return math.sqrt(mean([v * v for v in values])) if values else NAN


def js_round(x: float) -> int:
    """Round half up, like JavaScript's Math.round (Python's round() rounds half to even)."""
    r = math.floor(x)
    return int(r + 1) if x - r >= 0.5 else int(r)


def or0(x) -> float:
    """None, 0 and NaN count as 0 (like `x || 0` in JavaScript)."""
    if x is None or x != x:
        return 0.0
    return x


def div(a: float, b: float) -> float:
    """Division that returns inf or nan instead of an error when b is 0."""
    if b == 0:
        if a == 0 or a != a:
            return NAN
        return math.inf if a > 0 else -math.inf
    return a / b
