"""Output tables (pandas DataFrames) and writing them to data/output as CSV and one Excel file."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

import datetime as dt

from .calibrate import Calibration, plan_factor
from .data import DAYS, day_date, iso_monday
from .drivers import DRIVER_NAMES
from .mathutil import js_round
from .plan import Forecast, Scenario, reference_rate, share_for, weather_factor
from .supply import deadline, shelf_check


def _r(x, nd=1):
    """Round for the tables: whole units as integers (half up, like the HTML page)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return js_round(float(x)) if nd == 0 else round(float(x), nd)


def window_label(win) -> str:
    """'2026: 36 37 38 39 40 41' or '2025: 51 52 | 2026: 1 2 3 4'."""
    parts, cur = [], None
    for y, w in win:
        if y != cur:
            parts.append([y, []])
            cur = y
        parts[-1][1].append(str(w))
    return " | ".join(f"{y}: {' '.join(ws)}" for y, ws in parts)


def _period(cal: Calibration, y: int, w: int) -> str:
    c = cal.data.own_of(y, w)
    if not c:
        return ""
    return f"coupon {c.year}" if w in c.weeks else f"dip after coupon {c.year}"


def _week_parts(cal: Calibration, y: int, w: int) -> dict:
    """The driver factors of a week with data, weighted by the day's counterfactual."""
    ds = [p for p in cal.all.days if p["year"] == y and p["week"] == w and not p["closed"]]
    tot = sum(p["cf"] for p in ds)
    return {k: (sum(p["cf"] * p["parts"][k] for p in ds) / tot if tot else None) for k in DRIVER_NAMES}


def weekly_counterfactual(cal: Calibration) -> pd.DataFrame:
    """Every week with a full rolling window (the 6 weeks before it have data): neutral baseline x the drivers of the
    week = counterfactual. The driver columns are the week's factors (weighted by the day's counterfactual)."""
    rows = []
    for r in cal.all.weeks:
        if len(r["window"]) < cal.rule.window_weeks:
            continue
        parts = _week_parts(cal, r["year"], r["week"])
        rows.append({
            "year": r["year"], "week": r["week"], "monday": iso_monday(r["year"], r["week"]).isoformat(),
            "period": _period(cal, r["year"], r["week"]), "baseline_window": window_label(r["window"]),
            "neutral_baseline": _r(r["level"]), "drivers_factor": _r(r["factor"], 4),
            **{f"{k}_factor": _r(parts[k], 4) for k in DRIVER_NAMES},
            "counterfactual": _r(r["cf"]), "demand": _r(r["actual"]), "demand_minus_counterfactual": _r(r["effect"]),
            "fill_rate": r["fr"], "sales": _r(r["sales"], 0),
        })
    return pd.DataFrame(rows)


def daily_counterfactual(cal: Calibration) -> pd.DataFrame:
    full = {(r["year"], r["week"]) for r in cal.all.weeks if len(r["window"]) == cal.rule.window_weeks}
    rows = []
    for p in cal.all.days:
        if (p["year"], p["week"]) not in full or p["closed"]:
            continue
        rows.append({"date": p["date"], "year": p["year"], "week": p["week"], "weekday": DAYS[p["d"]],
                     "period": _period(cal, p["year"], p["week"]), "demand": _r(p["P"], 2), "sales": _r(p["Psales"], 0),
                     **{f"{k}_factor": _r(p["parts"][k], 4) for k in DRIVER_NAMES}, "drivers_factor": _r(p["F"], 4),
                     "counterfactual": _r(p["cf"], 2), "demand_minus_counterfactual": _r(p["effect"], 2),
                     "cumulative_effect": _r(p.get("cum"), 1), "cumulative_95_half_width": _r(p.get("cumHalf"), 1)})
    return pd.DataFrame(rows)


def coupon_measurement(cal: Calibration) -> pd.DataFrame:
    rows = []
    for year, m in cal.measurements.items():
        c = m.coupon
        rows.append({"year": year, "coupon_weeks": " ".join(map(str, m.weeks)), "dip_weeks": " ".join(map(str, c.post)),
                     "discount": c.discount, "incremental_units": _r(m.extra, 0), "incremental_95_low": _r(m.extra - m.extra_half, 0),
                     "incremental_95_high": _r(m.extra + m.extra_half, 0), "uplift_vs_counterfactual": _r(m.extra / m.cf_coupon, 4),
                     "dip_units": _r(m.dip, 0), "net_units": _r(m.net, 0), "net_95_low": _r(m.net - m.net_half, 0),
                     "net_95_high": _r(m.net + m.net_half, 0), "counterfactual_in_coupon_weeks": _r(m.cf_coupon, 0),
                     "fill_rate_during_coupon": _r(m.frc, 4), "demand_lost_on_empty_shelves": _r(m.lost, 0)})
    return pd.DataFrame(rows)


def baseline_cells(cal: Calibration) -> pd.DataFrame:
    """The plan's neutral baseline per weekday (frozen window)."""
    lv = cal.plan_window
    rows = []
    for d in range(6):
        c = lv.L.cells[d]
        rows.append({"weekday": DAYS[d], "neutral_baseline": _r(c.level, 2), "threshold": _r(c.threshold, 2),
                     "values": c.n, "outliers": c.n_out})
    return pd.DataFrame(rows)


def baseline_rule_detail(cal: Calibration) -> pd.DataFrame:
    """Every value the rule saw for the plan baseline: actual demand, adjusted value, rank and the rule's verdict."""
    rows = []
    for d in range(6):
        smp = cal.sample(d)
        ranked = sorted([x for x in smp if x["adj"] is not None], key=lambda x: -x["adj"])
        rank = {x["week"]: k + 1 for k, x in enumerate(ranked)}
        for x in smp:
            rows.append({"weekday": DAYS[d], "year": x["year"], "week": x["week"], "date": x["date"],
                         "demand": _r(x["raw"], 2), "drivers_factor": _r(x["factor"], 4),
                         "neutral_value": _r(x["adj"], 2), "rank": rank.get(x["week"]), "rule": x["status"]})
    df = pd.DataFrame(rows)
    df["rank"] = df["rank"].astype("Int64")
    return df


def past_coupons(cal: Calibration) -> pd.DataFrame:
    return pd.DataFrame([{"year": p["year"], "weeks": p["weeks"], "discount": p["discount"], "app_users": int(p["mau"]),
                          "observed_rate": p["rateObs"], "fill_rate": _r(p["fillRate"], 4), "rate_at_full_availability": _r(p["rateFull"], 4),
                          "units_per_redemption": p["upr"], "incremental_share": _r(p["extra"], 4), "dip_share": _r(p["dip"], 4),
                          "source": "measured with the counterfactual" if p["measured"] else "coupon report"} for p in cal.past])


def plan_weeks(cal: Calibration, sc: Scenario, res: Forecast) -> pd.DataFrame:
    return pd.DataFrame([{"week": r["week"], "monday": r["monday"].isoformat(), "coupon_week": r["coupon"],
                          "coupon_days": " ".join(DAYS[d] for d in r["couponDays"]), "share_of_coupon": _r(r["share"], 4),
                          "redemptions": _r(r["redemptions"], 0), "units_with_coupon": _r(r["couponUnits"], 0),
                          "counterfactual": _r(r["baseline"], 0), "incremental": _r(r["incremental"], 0), "dip": _r(-r["dip"], 0),
                          "demand": _r(r["demand"], 0), "sales_at_target_fill_rate": _r(r["sales"], 0), "lost": _r(r["lost"], 0)} for r in res.rows])


def full_year(cal: Calibration, sc: Scenario, res: Forecast) -> pd.DataFrame:
    """The plan year week by week, like the chart: measured weeks from the data, then the plan."""
    py = cal.data.plan_year()
    plan = {p["week"]: p for p in res.rows}
    rows = []
    for r in cal.all.weeks:
        if r["year"] != py:
            continue
        p = plan[r["week"]]
        if p["coupon"] or p["dipWeek"]:  # a coupon planned in a week with data: what-if on that week's normal demand
            eff = p["incremental"] - p["dip"]
            rows.append({"week": r["week"], "type": "what-if", "neutral_baseline": _r(r["level"], 0), "drivers_factor": _r(r["factor"], 4),
                         "counterfactual": _r(p["baseline"], 0), "coupon_effect": _r(eff, 0), "demand": _r(p["demand"], 0),
                         "fill_rate": sc.fill_rate, "empty_shelves": _r(p["lost"], 0), "sales": _r(p["sales"], 0)})
            continue
        eff = r["effect"] if r["own"] else None
        rows.append({"week": r["week"], "type": "actual", "neutral_baseline": _r(r["level"], 0), "drivers_factor": _r(r["factor"], 4),
                     "counterfactual": _r(r["cf"], 0), "coupon_effect": _r(eff, 0), "demand": _r(r["actual"], 0),
                     "fill_rate": r["fr"], "empty_shelves": _r(r["actual"] - r["sales"], 0), "sales": _r(r["sales"], 0)})
    for r in [x for x in res.rows if x["week"] > cal.data.last_data_week()]:
        eff = r["incremental"] - r["dip"]
        # the drivers of a plan week: this year's calendar and the season, the weather of the scenario
        f = r["baseline"] / cal.plan.level_week if cal.plan.level_week else None
        rows.append({"week": r["week"], "type": "plan", "neutral_baseline": _r(cal.plan.level_week, 0), "drivers_factor": _r(f, 4),
                     "counterfactual": _r(r["baseline"], 0), "coupon_effect": _r(eff, 0) if abs(eff) > 1e-9 else None,
                     "demand": _r(r["demand"], 0), "fill_rate": sc.fill_rate, "empty_shelves": _r(r["lost"], 0), "sales": _r(r["sales"], 0)})
    return pd.DataFrame(rows)


DRIVER_LABELS = {"pre1": "First shopping day before a public holiday", "pre2": "Second shopping day before a public holiday",
                 "xmas": "Run-up to Christmas (full effect on 23 December)",
                 "short": "Short day: 24 December, the store closes at 14:00", "anomaly": "Weather: per degree above the seasonal normal",
                 "z": "Price: per log point of the brand's price against the category price index (0.05 = the category about 5% cheaper)",
                 "trend": "Trend: per year (not a driver of the plan; the baseline carries the level)"}


def drivers_table(cal: Calibration) -> pd.DataFrame:
    """The fitted drivers: coefficient (log scale), its 95% half-width and the effect as a percentage."""
    rows = []
    for brand, drv in (("test", cal.drivers), ("control", cal.drivers_control)):
        for e in drv.effects():
            rows.append({"brand": brand, "driver": e["key"], "meaning": DRIVER_LABELS[e["key"]], "coefficient": round(e["coef"], 5),
                         "half_width_95": round(1.96 * e["se"], 5), "effect_pct": round((math.exp(e["coef"]) - 1) * 100, 2),
                         "fitted_on": " ".join(map(str, drv.train_years)), "days_in_fit": drv.fit.n, "residual_sd": round(drv.fit.sd, 5)})
    return pd.DataFrame(rows)


def season_table(cal: Calibration) -> pd.DataFrame:
    """The season curve by week (the Thursday of each week), centred on 1, and the true pattern's source: none here."""
    return pd.DataFrame([{"week": w, "season_factor": round(v, 4)} for w, v in enumerate(cal.season_curve, start=1)])


def day_factors(cal: Calibration) -> pd.DataFrame:
    """The drivers of every day from the first data day to the end of the plan year, the input of the SQL steps:
    each factor of Deo Test Brand, their product (drivers_factor), the plan's version of the product (plan_factor:
    season and calendar only, normal weather, normal prices), the product for the control brand (its own drivers)
    and whether the store is open (close_hour 14 on the short day, 24 December). The products carry
    12 decimals because the SQL divides and multiplies by them; the single factors are rounded for reading."""
    rows = []
    drv, drv_c = cal.drivers, cal.drivers_control
    for d, f in sorted(cal.feats.items()):
        parts = drv.parts(f)
        y, w, wd = d.isocalendar()
        rows.append({"date": d.isoformat(), "year": y, "week": w, "weekday": wd, "day_name": DAYS[f.weekday],
                     "open": int(f.open), "close_hour": f.close_hour,
                     **{f"{k}_factor": round(parts[k], 6) for k in DRIVER_NAMES},
                     "drivers_factor": round(drv.factor(f), 12), "plan_factor": round(plan_factor(drv, f), 12),
                     "control_drivers_factor": round(drv_c.factor(f), 12),
                     "temperature_anomaly": None if f.anomaly is None else round(f.anomaly, 2), "relative_price_test": round(f.z, 4),
                     "pre_holiday_1": int(f.pre1), "pre_holiday_2": int(f.pre2), "christmas_ramp": round(f.xmas, 4),
                     "short_day": int(f.short)})
    return pd.DataFrame(rows)


def model_parameters(cal: Calibration) -> pd.DataFrame:
    p, h, cc = cal.plan, cal.holdout, cal.control_check
    items = [
        ("drivers_fitted_on", " ".join(map(str, cal.train_years)), "Years of the drivers regression (the plan year is the test)"),
        ("drivers_fit_days", cal.drivers.fit.n, "Days in the regression of Deo Test Brand"),
        ("sd_in_sample", cal.sd_in, "Daily error (log) of the drivers regression"),
        ("sd_daily_out_of_sample", cal.sd_d, "Daily error (log) of the counterfactual on rolling days outside the coupons"),
        ("sd_level", cal.sd_l, "Error of the frozen baseline level (one number for the whole coupon and its dip)"),
        ("sd_weekly_out_of_sample", cal.sd_w, "Weekly error (log) of the counterfactual outside the coupons"),
        ("out_of_sample_days", cal.oos_days, "Days in the out-of-sample error"),
        ("holdout_year", h["year"], "The plan year's weeks with data: the drivers never saw them"),
        ("holdout_weeks", h["weeks"], "Weeks in the holdout (outside the plan year's coupon)"),
        ("holdout_weekly_rms", h["weekly_rms"], "Weekly error (log) in the holdout"),
        ("holdout_weekly_bias", h["weekly_bias"], "Average of actual / counterfactual - 1 in the holdout"),
        ("holdout_daily_rms", h["daily_rms"], "Daily error (log) in the holdout"),
        ("control_check_weekly_corr", cc["weekly_corr"], "Correlation of the weekly errors of the two brands outside the coupons"),
        *[(f"control_check_{y}_coupon", v["coupon"], f"Control brand vs its own counterfactual in the {y} coupon weeks (0 = no sign of cannibalisation)")
          for y, v in cc["years"].items()],
        ("plan_window", window_label(p.window), "Frozen baseline window of the plan"),
        ("plan_neutral_week", p.level_week, "Weekly sum of the plan's neutral baseline (rule output)"),
        ("plan_typical_week", p.week, "Average normal demand of the plan weeks (the shelf check's normal week)"),
        ("plan_weeks_normal_demand", " ".join(f"{w}:{v:.0f}" for w, v in p.weeks.items()), "Normal demand of each plan week at normal weather"),
        ("weather_scenario_degrees", cal.anomaly_sd, "Cold / warm scenario: one standard deviation of the weekly temperature anomaly"),
        ("plan_error_last_year_rms", cal.base_err_ref,
         "The same plan made one year earlier vs the counterfactual in the coupon weeks (what the plan cannot know)"),
        ("fill_rate_normal_weeks", cal.fr_normal, "Average fill rate outside the coupons"),
        ("units_per_redemption_past", cal.upr, "Average of past coupons"),
        ("dip_share_past", cal.dip_default, "Average pulled-forward share of past coupons"),
        ("incremental_share_rel_se", cal.share_rel_se, "Relative standard error of the measured incremental share"),
        ("rate_line_slope", cal.fit_rate.slope, "Redemption rate at full availability = intercept + slope x discount"),
        ("rate_line_intercept", cal.fit_rate.intercept, ""),
        ("share_line_slope", cal.fit_share.slope, "Incremental share = intercept + slope x discount"),
        ("share_line_intercept", cal.fit_share.intercept, ""),
        ("weekly_shape_full_weeks", " ".join(f"{v:.4f}" for v in cal.profile_full), "Share of the reference coupon's incremental units per full week"),
        ("dip_weights", " ".join(f"{v:.4f}" for v in cal.dip_weights), "How the dip spreads over the weeks after the coupon"),
    ]
    return pd.DataFrame(items, columns=["parameter", "value", "note"])


def plan_summary(cal: Calibration, sc: Scenario, res: Forecast) -> pd.DataFrame:
    s, C = res.summary, res.summary["commit"]
    sh = shelf_check(cal, sc, res)
    ref = reference_rate(cal, sc.discount, sc.full_weeks)
    items = [
        ("discount", sc.discount), ("coupon_weeks", " ".join(map(str, s["weeks"]))), ("app_users", js_round(sc.mau)),
        ("redemption_rate", sc.rate), ("reference_rate_for_this_discount", round(ref["rate"], 4)),
        ("units_per_redemption", sc.upr), ("incremental_share", sc.inc_share),
        ("reference_share_for_this_discount", round(share_for(cal, sc.discount), 4)), ("pull_forward_share", sc.pull_share),
        ("redemptions", js_round(s["redemptions"])), ("units_with_coupon", js_round(s["couponUnits"])),
        ("bought_anyway", js_round(s["anyway"])), ("incremental_units", js_round(s["incremental"])), ("dip_units", js_round(s["dip"])),
        ("net_incremental_units", js_round(s["net"])), ("dip_after_week_52", js_round(s["dipOutside"])),
        ("demand_in_coupon_weeks", js_round(C["mu"])), ("sigma", js_round(C["sigma"])), ("sigma_from_baseline", js_round(C["parts"]["base"])),
        ("sigma_from_redemption_rate", js_round(C["parts"]["rate"])), ("sigma_from_incremental_share", js_round(C["parts"]["share"])),
        ("target_fill_rate", sc.fill_rate), ("z", round(C["z"], 4)), ("volume_to_commit", js_round(C["q"])), ("buffer", js_round(C["buffer"])),
        ("fill_rate_if_only_forecast_committed", round(C["fillIfMu"], 4)),
        ("coupon_start", s["startDate"].isoformat()), ("lead_time_days", sc.lead_time_days),
        ("commit_deadline", deadline(s["startDate"], sc.lead_time_days).isoformat()),
        ("delivery_days", " ".join(DAYS[d] for d in sc.delivery_days)),
        ("shelf_peak_stretch", f"{sh['peak']['label'].replace('–', '-')} week {sh['peak']['week']}"),
        ("shelf_peak_share_of_normal_week", round(sh["peak"]["factor"], 4)),
        ("shelf_products_short", sum(1 for x in sh["items"] if x["needed"] > x["facings"])),
        ("shelf_suggested_extra_delivery", sh["suggestion"]["day"] if sh["suggestion"] else ""),
    ]
    return pd.DataFrame(items, columns=["item", "value"])


def shelf_table(cal: Calibration, sc: Scenario, res: Forecast) -> pd.DataFrame:
    sh = shelf_check(cal, sc, res)
    return pd.DataFrame([{"sku": x["sku"], "normal_week_units": x["normalWeek"], "peak_stretch_units": _r(x["peakDemand"], 1),
                          "same_stretch_without_coupon": _r(x["normalDemand"], 1), "shelf_capacity": x["capacity"], "facings": x["facings"],
                          "facings_needed": x["needed"]} for x in sh["items"]])


def all_tables(cal: Calibration, sc: Scenario, res: Forecast) -> dict:
    py = cal.data.plan_year()
    return {
        f"full_year_{py}": full_year(cal, sc, res),
        "weekly_counterfactual": weekly_counterfactual(cal),
        "daily_counterfactual": daily_counterfactual(cal),
        "coupon_measurement": coupon_measurement(cal),
        "plan_weeks": plan_weeks(cal, sc, res),
        "plan_summary": plan_summary(cal, sc, res),
        "shelf_check": shelf_table(cal, sc, res),
        "baseline_cells_plan": baseline_cells(cal),
        "baseline_rule_detail_plan": baseline_rule_detail(cal),
        "past_coupons_measured": past_coupons(cal),
        "model_parameters": model_parameters(cal),
        "drivers": drivers_table(cal),
        "season_curve": season_table(cal),
        "day_factors": day_factors(cal),
    }


def write_outputs(tables: dict, folder, excel: bool = True) -> list:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, df in tables.items():
        p = folder / f"{name}.csv"
        df.to_csv(p, index=False)
        written.append(p)
    if excel:
        try:
            p = folder / "all_tables.xlsx"
            with pd.ExcelWriter(p) as xw:
                for name, df in tables.items():
                    df.to_excel(xw, sheet_name=name[:31], index=False)
            written.append(p)
        except ImportError:
            print("openpyxl is not installed, so no Excel file was written (pip install openpyxl).")
    return written


def chart_rows_full_year(cal: Calibration, sc: Scenario, res: Forecast, ready: bool = True) -> list:
    """Data of the full-year chart (like the HTML page): the plan view of the plan year. Normal demand of every week
    (from the data up to the last data week, the plan baseline after it), the coupon you plan, the dip after it and the
    volume lost on empty shelves at the target fill rate. actualDemand: actual demand (sales / fill rate) in the weeks with data; own: "coupon" / "dip" in the weeks of the plan
    year's own coupon in the data."""
    rows = []
    for r in res.rows:
        coupon = r["incremental"] if ready else 0.0
        dip = -r["dip"] if ready else 0.0
        demand = r["baseline"] + coupon + dip
        shelves = -demand * (1 - sc.fill_rate)
        wk = cal.all.week(cal.data.plan_year(), r["week"]) if r["week"] <= cal.data.last_data_week() else None
        c26 = next((c for c in cal.data.own if c.year == cal.data.plan_year()), None)
        own = ("coupon" if r["week"] in c26.weeks else "dip" if r["week"] in c26.post else None) if c26 else None
        rows.append({"week": r["week"], "base": r["baseline"], "coupon": coupon, "dip": dip, "shelves": shelves, "demand": demand,
                     "sales": demand + shelves, "actualSales": None, "actualDemand": wk["actual"] if wk else None, "own": own,
                     "forecast": True})
    return rows
