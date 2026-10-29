"""The baseline rule (your SQL logic from the minute-level TV baseline), here per weekday and hour.

For one weekday and opening hour, take that weekday and hour in each of the last 6 weeks (n values):
  1. Rank the values from high to low.
  2. Outlier threshold = value at rank floor(n/2)+1
                         + 2 x standard deviation of the middle ranks (round(n/6) ranks left out at each end)
                         + 2.
     Values above the threshold are outliers.
  3. Center rank c = n - floor((n - outliers) / 2), i.e. the median rank of the values that are left.
  4. Baseline = average of the values at ranks c-1 ... c+1 (c-2 ... c+2 when n >= 20).
Closed days (no sales row) are left out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .mathutil import NAN, js_round, mean, total


@dataclass(frozen=True)
class RuleSettings:
    window_weeks: int = 6
    sd_multiplier: float = 2.0
    threshold_constant: float = 2.0
    trim_divisor: int = 6
    half_window: int = 1
    half_window_large: int = 2
    large_n: int = 20

    @classmethod
    def from_config(cls, cfg) -> "RuleSettings":
        return cls(cfg.BASELINE_WEEKS, cfg.OUTLIER_SD_MULTIPLIER, cfg.OUTLIER_CONSTANT, cfg.SPREAD_TRIM_DIVISOR,
                   cfg.MEDIAN_HALF_WINDOW, cfg.MEDIAN_HALF_WINDOW_LARGE, cfg.LARGE_N)


@dataclass
class RuleResult:
    level: float
    threshold: float | None
    n_out: int
    n: int
    status: dict = field(default_factory=dict)  # key -> "used" | "outlier" | "not used" | "closed"


def robust_level(items, rule: RuleSettings = RuleSettings()) -> RuleResult:
    """items: list of (key, value); value None = closed day."""
    status = {k: ("closed" if v is None else "not used") for k, v in items}
    v = sorted([(k, x) for k, x in items if x is not None], key=lambda it: -it[1])  # high to low, ties keep time order
    n = len(v)
    if not n:
        return RuleResult(0.0, None, 0, 0, status)
    mid = v[n // 2][1]
    k = max(1, js_round(n / rule.trim_divisor))
    middle = [x for _, x in v[k:n - k]]
    m = mean(middle) if middle else mid
    sd = math.sqrt(mean([(x - m) ** 2 for x in middle])) if len(middle) > 1 else 0.0
    threshold = mid + rule.sd_multiplier * sd + rule.threshold_constant
    n_out = sum(1 for _, x in v if x > threshold)
    c = n - (n - n_out) // 2
    hw = rule.half_window_large if n >= rule.large_n else rule.half_window
    used = v[max(n_out + 1, c - hw) - 1:min(n, c + hw)]
    for r, (key, _) in enumerate(v):
        if r < n_out:
            status[key] = "outlier"
    for key, _ in used:
        status[key] = "used"
    level = mean([x for _, x in used]) if used else NAN
    return RuleResult(level, threshold, n_out, n, status)


@dataclass
class Baseline:
    cells: list   # [weekday 0-5][hour index] -> RuleResult
    day: list     # baseline per weekday (sum of the hours)
    week: float   # sum of the weekdays


def baseline_of(data, brand: str, window, rule: RuleSettings = RuleSettings()) -> Baseline:
    """The rule on one brand's hourly demand over a window of (year, week) pairs."""
    from .data import week_key
    cells = []
    for d in range(6):
        row = []
        for i in range(len(data.hours)):
            items = []
            for (y, w) in window:
                hd = data.hour_demand(y, brand, w, d)
                items.append((week_key(y, w), hd[i] if hd else None))
            row.append(robust_level(items, rule))
        cells.append(row)
    day = [total(c.level for c in r) for r in cells]
    return Baseline(cells, day, total(day))
