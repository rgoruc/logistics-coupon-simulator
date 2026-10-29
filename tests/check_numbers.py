"""Check that the Python model gives the same numbers as the HTML version.

reference_from_html.json holds the numbers of the JavaScript model inside html/coupon_planner.html, run on
the same data with the default settings. This script runs the Python model with the default settings and
compares several thousand numbers. Run it from the project folder:

    python tests/check_numbers.py

If you change the model or the data on purpose, the check will show the differences; that is expected.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import (calibrate, daily, day_drivers, deadline, default_scenario, forecast, intervals, load,  # noqa: E402
                   reference_rate, shelf_check, share_for, start_weeks)
from model.mathutil import js_round  # noqa: E402
from model.baseline import RuleSettings  # noqa: E402

TOL = 1e-9  # relative tolerance
ABS_TOL = 1e-12  # absolute tolerance for numbers that are zero up to rounding noise


def python_numbers() -> dict:
    data = load(ROOT / "data" / "input")
    cal = calibrate(data, RuleSettings(), 2025, 5)
    m24, m25, m26 = cal.measurements[2024], cal.ref, cal.measurements.get(2026)
    lv = cal.plan_window
    sc = default_scenario(cal).with_(start_week=43)  # the check's main scenario: the plan weeks after the data
    res = forecast(cal, sc)
    sc1 = sc.with_(start_week=default_scenario(cal).start_week)  # the default of plan_assumptions.csv
    res1 = forecast(cal, sc1)
    s = res.summary
    weeks = [{"year": w["year"], "week": w["week"], "actual": w["actual"], "cf": w["cf"], "effect": w["effect"], "own": w["own"],
              "sales": w["sales"], "level": w["level"], "fr": w["fr"], "factor": w["factor"],
              "window": [f"{a}-{b}" for a, b in w["window"]]} for w in cal.all.weeks]
    full = {(w["year"], w["week"]) for w in cal.all.weeks if len(w["window"]) == 6}
    days = [{"date": p["date"], "closed": p["closed"], "P": p.get("P"), "cf": p["cf"], "F": p.get("F"), "parts": p.get("parts"),
             "effect": p["effect"], "cum": p.get("cum"), "cumHalf": p.get("cumHalf")} for p in cal.all.days if (p["year"], p["week"]) in full]
    weeks_c = [{"year": w["year"], "week": w["week"], "actual": w["actual"], "cf": w["cf"], "factor": w["factor"]}
               for w in cal.all_control.weeks if len(w["window"]) == 6]
    drv = lambda d: {"coef": d.fit.coef, "se": d.fit.se, "sd": d.fit.sd, "n": d.fit.n, "names": d.names, "seasonMean": d.season_mean,  # noqa: E731
                     "effects": d.effects()}
    sh = shelf_check(cal, sc, res, [0, 2, 4], 6)
    # a 2-week coupon late in the year (start week 50): reference by length, weekly split, the dip running past week 52
    sc2 = sc.with_(full_weeks=2, start_week=50, rate=js_round(reference_rate(cal, 0.2, 2)["rate"] * 1000) / 1000)
    # a coupon in weeks with data (start week 13, 4 weeks: Good Friday in week 14, Easter Monday in week 15), Monday deliveries only
    sc3 = sc.with_(full_weeks=4, start_week=13)
    res3 = forecast(cal, sc3)
    s3 = res3.summary
    res2 = forecast(cal, sc2)
    s2 = res2.summary
    # a warm scenario in the plan weeks (+3 degrees), start week 48 (the Christmas week 52 is in the dip)
    sc4 = sc.with_(start_week=48, plan_anomaly=3.0)
    res4 = forecast(cal, sc4)
    s4 = res4.summary
    summ = lambda x, keys: {k: x[k] for k in keys}  # noqa: E731
    rows_of = lambda r, keys: [{k: x[k] for k in keys} for x in r.rows]  # noqa: E731
    feats = ["2024-01-02", "2024-03-28", "2024-12-24", "2025-04-17", "2025-07-02", "2026-05-01", "2026-12-23", "2026-12-24"]
    return {
        "reference2w": reference_rate(cal, 0.2, 2), "reference30_2w": reference_rate(cal, 0.3, 2),
        "startWeeks2": start_weeks(data, 2), "startWeeks4": start_weeks(data, 4),
        "rows2w": [{"week": r["week"], "coupon": r["coupon"], "share": r["share"], "redemptions": r["redemptions"],
                    "incremental": r["incremental"], "dip": r["dip"], "demand": r["demand"]}
                   for r in res2.rows],
        "shelf2w": shelf_check(cal, sc2, res2, [0], 6),
        "default1": {"startWeek": sc1.start_week, "summary": summ(res1.summary, ["weeks", "couponDemand", "couponBaseline", "commit"]),
                     "rows": rows_of(res1, ["week", "baseline", "incremental", "dip", "demand"])[:8]},
        "summary2w": summ(s2, ["weeks", "redemptions", "incremental", "dip", "dipOutside", "couponDemand", "commit"]),
        "rows3w13": rows_of(res3, ["week", "coupon", "baseline", "incremental", "dip", "demand"]),
        "what3": {"summary": summ(s3, ["weeks", "couponDemand", "couponBaseline", "commit"]),
                  "daily14": daily(cal, sc3, res3, 14), "daily15": daily(cal, sc3, res3, 15),
                  "shelfMon": shelf_check(cal, sc3, res3, [0], 6),
                  "startWeeks4": start_weeks(data, 4), "drivers14": [day_drivers(cal, 14, d) for d in range(6)]},
        "warm48": {"summary": summ(s4, ["weeks", "couponDemand", "couponBaseline", "commit"]),
                   "rows": [{k: r[k] for k in ["week", "baseline", "incremental", "dip", "demand"]} for r in res4.rows if r["week"] >= 43],
                   "daily52": daily(cal, sc4, res4, 52),
                   "drivers52": [day_drivers(cal, 52, d, 3.0) for d in range(6)], "shelfMon": shelf_check(cal, sc4, res4, [0], 6)},
        "shelfMonDefault": shelf_check(cal, sc, res, [0], 6),
        "cal": {"sdIn": cal.sd_in, "sdD": cal.sd_d, "sdL": cal.sd_l, "sdW": cal.sd_w, "oosDays": cal.oos_days, "trainYears": cal.train_years,
                "extra25": m25.extra, "dip25": m25.dip, "extraHalf": m25.extra_half,
                "netHalf": m25.net_half, "frc25": m25.frc, "lost25": m25.lost, "extraOnSales25": cal.extra_on_sales_ref, "cfCoupon25": m25.cf_coupon,
                "m26": {"extra": m26.extra, "dip": m26.dip, "extraHalf": m26.extra_half, "netHalf": m26.net_half, "frc": m26.frc,
                        "cfCoupon": m26.cf_coupon, "lost": m26.lost} if m26 else None,
                "m24": {"extra": m24.extra, "dip": m24.dip, "extraHalf": m24.extra_half},
                "profile": {"full": cal.profile_full, "partial": cal.profile_partial}, "tail": cal.tail, "lastChance": cal.last_chance,
                "dipWeights": cal.dip_weights, "frNormal": cal.fr_normal,
                "past": [{k: p.get(k) for k in ["year", "discount", "mau", "rateObs", "fillRate", "rateFull", "couponUnitsFull", "upr",
                                                "extra", "dip", "extraHalf", "measured"]} for p in cal.past],
                "fitRate": vars(cal.fit_rate), "fitShare": vars(cal.fit_share), "upr": cal.upr, "dipDefault": cal.dip_default,
                "shareRelSE": cal.share_rel_se,
                "B26": {"levelWeek": cal.plan.level_week, "week": cal.plan.week, "weeks": [cal.plan.weeks[w] for w in data.plan_weeks()],
                        "days": [cal.plan.days[w] for w in data.plan_weeks()], "parts": [cal.plan.parts[w] for w in data.plan_weeks()],
                        "dayShare": cal.plan.day_share, "window": [f"{a}-{b}" for a, b in cal.plan.window]},
                "planErr": cal.plan_err, "baseBias25": cal.base_bias_ref, "baseErr25": cal.base_err_ref,
                "drivers": drv(cal.drivers), "driversControl": drv(cal.drivers_control),
                "holdout": {"year": cal.holdout["year"], "weeks": cal.holdout["weeks"], "days": cal.holdout["days"], "weeklyRms": cal.holdout["weekly_rms"],
                            "weeklyBias": cal.holdout["weekly_bias"], "weeklyMad": cal.holdout["weekly_mad"], "dailyRms": cal.holdout["daily_rms"]},
                "controlCheck": {"years": {str(y): v for y, v in cal.control_check["years"].items()}, "weeklyCorr": cal.control_check["weekly_corr"],
                                 "weeks": cal.control_check["weeks"], "sdWControl": cal.control_check["sd_w_control"]},
                "seasonCurve": cal.season_curve, "anomalySd": cal.anomaly_sd},
        "weather": {"coef": cal.weather.coef, "lastDay": cal.weather.last_day.isoformat()},
        "feats": [{"date": s_, "open": f.open, "closeHour": f.close_hour, "pre1": f.pre1, "pre2": f.pre2, "xmas": f.xmas, "short": f.short, "anomaly": f.anomaly,
                   "z": f.z, "zc": f.zc} for s_ in feats for f in [cal.feats[dt.date.fromisoformat(s_)]]],
        "planCells": [{"level": c.level, "threshold": c.threshold, "nOut": c.n_out, "n": c.n, "status": c.status} for c in lv.L.cells],
        "sample": cal.sample(4), "weeks": weeks, "days": days, "weeksControl": weeks_c,
        "reference": reference_rate(cal, 0.2), "shareFor20": share_for(cal, 0.2),
        "scenario": {"discount": sc.discount, "startWeek": sc.start_week, "fullWeeks": sc.full_weeks,
                     "mau": sc.mau, "rate": sc.rate, "upr": sc.upr, "incShare": sc.inc_share, "pullShare": sc.pull_share,
                     "fillRate": sc.fill_rate, "rateUnc": sc.rate_unc, "price": sc.price, "controlPrice": sc.control_price, "planAnomaly": sc.plan_anomaly},
        "rows": [{"week": r["week"], "coupon": r["coupon"], "share": r["share"], "couponDays": r["couponDays"], "baseline": r["baseline"],
                  "redemptions": r["redemptions"], "couponUnits": r["couponUnits"], "incremental": r["incremental"], "dip": r["dip"],
                  "demand": r["demand"], "sales": r["sales"], "lost": r["lost"]} for r in res.rows],
        "summary": {**{k: s[k] for k in ["weeks", "redemptions", "couponUnits", "anyway", "incremental", "dip", "net", "dipOutside",
                                         "couponDemand", "couponBaseline", "couponSales", "couponLost", "commit", "price"]},
                    "startDate": s["startDate"].isoformat(), "deadline": deadline(s["startDate"], 96).isoformat()},
        "daily43": daily(cal, sc, res, 43),
        "shelf": sh, "intervals": intervals([0, 2, 4]),
    }


def compare(a, b, path, out):
    """a = Python, b = reference. Appends (path, python, reference) for every mismatch; returns (count, max rel diff)."""
    if b is None and a is None:
        return 0, 0.0
    if isinstance(b, dict):
        n, mx = 0, 0.0
        for k, v in b.items():
            if isinstance(a, dict) and k in a:
                c, m = compare(a[k], v, f"{path}.{k}", out)
                n, mx = n + c, max(mx, m)
        return n, mx
    if isinstance(b, list):
        if not isinstance(a, (list, tuple)) or len(a) != len(b):
            out.append((path, f"length {len(a) if isinstance(a, (list, tuple)) else a}", f"length {len(b)}"))
            return 1, math.inf
        n, mx = 0, 0.0
        for i, (x, y) in enumerate(zip(a, b)):
            c, m = compare(x, y, f"{path}[{i}]", out)
            n, mx = n + c, max(mx, m)
        return n, mx
    if isinstance(b, bool) or isinstance(b, str) or b is None or a is None:
        if a != b and not (b is None and isinstance(a, float) and math.isnan(a)):  # JSON writes NaN as null
            out.append((path, a, b))
        return 1, 0.0
    if isinstance(b, (int, float)):
        if isinstance(a, bool) or not isinstance(a, (int, float)):
            out.append((path, a, b))
            return 1, math.inf
        if math.isnan(a) and math.isnan(b):
            return 1, 0.0
        rel = abs(a - b) / max(abs(b), 1e-12) if a != b else 0.0
        if abs(a - b) < ABS_TOL:  # numbers that are zero up to rounding noise (the season term averages to 0)
            rel = 0.0
        if rel > TOL:
            out.append((path, a, b))
        return 1, rel
    return 0, 0.0


def main() -> int:
    ref = json.loads((ROOT / "tests" / "reference_from_html.json").read_text(encoding="utf-8"))
    py = python_numbers()
    mism = []
    n, mx = compare(py, ref, "", mism)
    print(f"Compared {n} numbers and labels with the HTML version. Largest relative difference: {mx:.1e}")
    if mism:
        print(f"{len(mism)} differ (tolerance {TOL:g}):")
        for p, a, b in mism[:40]:
            print(f"  {p}: python {a!r}  html {b!r}")
        return 1
    print("All equal: the Python model reproduces the HTML numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
