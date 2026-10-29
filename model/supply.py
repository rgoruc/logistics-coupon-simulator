"""Supply chain checks: commit deadline and the shelf check for a high-volume store.

Shelf: each delivery has to cover the selling days until the next delivery. The check takes the heaviest
stretch between two deliveries in the coupon weeks and compares it with facings x units per facing. Demand is
measured against the plan's typical week (the shelf data describe a normal week), so a coupon in a busy season also
shows the season.
"""
from __future__ import annotations

import datetime as dt
import math

from .calibrate import Calibration
from .data import DAYS
from .mathutil import total
from .plan import Forecast, Scenario, daily


def deadline(start: dt.date, lead_days: int) -> dt.date:
    return start - dt.timedelta(days=lead_days)


def intervals(delivery_days: list) -> list:
    """For each delivery day, the selling days it has to cover (Sunday is closed)."""
    ds = sorted(delivery_days)
    out = []
    for i, d in enumerate(ds):
        nxt, span, k = ds[(i + 1) % len(ds)], [], d
        while True:
            if k != 6:
                span.append(k)
            k = (k + 1) % 7
            if k == nxt:
                break
        out.append(span)
    return out


def span_label(span: list) -> str:
    return DAYS[span[0]] if len(span) == 1 else f"{DAYS[span[0]]}–{DAYS[span[-1]]}"


def peak_interval(cal: Calibration, sc: Scenario, res: Forecast, delivery_days: list) -> dict:
    iv = intervals(delivery_days)
    share = list(cal.plan.day_share) + [0.0]
    normal = max(total(share[d] for d in span) for span in iv)
    best = {"factor": 0.0, "week": None, "span": None, "base": 0.0}
    for r in [x for x in res.rows if x["coupon"]]:
        dly = daily(cal, sc, res, r["week"])
        for span in iv:
            factor = total(dly[d]["demand"] for d in span) / cal.plan.week
            if factor > best["factor"]:
                best = {"factor": factor, "week": r["week"], "span": span, "base": total(dly[d]["baseline"] for d in span) / cal.plan.week}
    return {**best, "label": span_label(best["span"]) if best["span"] else "", "normal": normal}


def shelf_check(cal: Calibration, sc: Scenario, res: Forecast, delivery_days: list = None, depth: int = None) -> dict:
    delivery_days = list(sc.delivery_days if delivery_days is None else delivery_days)
    depth = sc.units_per_facing if depth is None else depth
    peak = peak_interval(cal, sc, res, delivery_days)
    items = []
    for s in cal.data.shelf:
        peak_demand, capacity = s.normal_week * peak["factor"], s.facings * depth
        items.append({"sku": s.sku, "normalWeek": s.normal_week, "peakDemand": peak_demand, "normalDemand": s.normal_week * peak["base"],
                      "capacity": capacity, "facings": s.facings, "needed": max(s.facings, math.ceil(peak_demand / depth - 1e-9))})
    suggestion = None
    for d in range(6):
        if d in delivery_days:
            continue
        p = peak_interval(cal, sc, res, delivery_days + [d])
        if suggestion is None or p["factor"] < suggestion["factor"]:
            suggestion = {"day": DAYS[d], "factor": p["factor"], "label": p["label"], "week": p["week"]}
    if suggestion:
        suggestion["fitsAll"] = all(s.normal_week * suggestion["factor"] <= s.facings * depth + 1e-9 for s in cal.data.shelf)
    return {"peak": peak, "items": items, "suggestion": suggestion}
