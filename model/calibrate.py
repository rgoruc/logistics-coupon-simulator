"""Everything the plan needs from the data: the drivers, the errors of the counterfactual, the measured coupons,
the reference lines through past coupons and the plan's normal demand.

Incremental units of a coupon = actual demand - counterfactual, summed over the coupon weeks.
The dip = the same sum over the weeks after the coupon (negative), shown as a positive number.
95% intervals: the day-to-day error of the drivers regression is independent from day to day; the rest of the
out-of-sample error is the error of the frozen baseline level and is shared by all days of a weekday.

The drivers are fitted on the years before the plan year. The plan year's weeks with data are the test:
the model did not see them, so its error there is an honest number (holdout).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from .baseline import RuleSettings
from .counterfactual import Counterfactuals, Drivers, Levels, fit_drivers
from .data import Data, OwnCoupon, day_date, day_key, iso_monday, week_key
from .drivers import DRIVER_NAMES, Calendar, Weather, build_features
from .mathutil import NAN, div, mean, rms, total


@dataclass
class Measurement:
    coupon: OwnCoupon
    weeks: list           # coupon weeks
    days: list            # day records of the coupon and dip weeks, with "cum" and "cumHalf"
    extra: float          # incremental units in the coupon weeks
    dip: float            # units missing in the dip weeks (positive)
    extra_half: float     # 95% half-width of extra
    net_half: float       # 95% half-width of extra - dip
    frc: float            # fill rate during the coupon, weighted by the incremental units
    cf_coupon: float      # counterfactual in the coupon weeks
    lost: float           # demand that found an empty shelf in the coupon weeks
    wk: dict              # week -> weekly record

    @property
    def net(self) -> float:
        return self.extra - self.dip


@dataclass
class LineFit:
    slope: float
    intercept: float
    min: float
    max: float


def line_fit(points) -> LineFit:
    mx, my = mean(p[0] for p in points), mean(p[1] for p in points)
    sxx = total((p[0] - mx) ** 2 for p in points)
    sxy = total((p[0] - mx) * (p[1] - my) for p in points)
    slope = sxy / sxx
    return LineFit(slope, my - slope * mx, min(p[0] for p in points), max(p[0] for p in points))


@dataclass
class PlanBaseline:
    """The plan's normal demand: the frozen neutral baseline x the drivers of each plan day (this year's calendar,
    the season, normal weather, normal prices)."""
    levels: Levels
    level_week: float     # the neutral week: sum of the baseline cells
    window: list
    weeks: dict           # plan week -> normal demand at normal weather
    days: dict            # plan week -> normal demand per weekday (7 values, closed days 0)
    parts: dict           # plan week -> the driver factors of the week (demand-weighted)
    week: float           # a typical plan week (average of the plan weeks), the shelf check's normal week
    day_share: list       # weekday shares of the neutral week (6 values)
    cells: list           # the rule's result per weekday


@dataclass
class Calibration:
    data: Data
    rule: RuleSettings
    cal: Calendar
    weather: Weather
    feats: dict                # date -> DayFeatures
    drivers: Drivers           # Deo Test Brand
    drivers_control: Drivers
    train_years: list
    sd_in: float
    sd_d: float
    sd_l: float
    sd_w: float
    oos_days: int
    all: Counterfactuals
    all_control: Counterfactuals
    ref: Measurement           # the reference coupon (2025), measured
    measurements: dict         # year -> Measurement for every own coupon
    lv_ref: Levels
    profile_full: list
    profile_partial: float | None
    tail: float
    last_chance: float
    dip_weights: list
    fr_normal: float
    extra_on_sales_ref: float
    past: list
    fit_rate: LineFit
    fit_share: LineFit
    measured: dict
    upr: float
    dip_default: float
    share_rel_se: float
    plan: PlanBaseline
    plan_window: Levels
    plan_err: list
    base_bias_ref: float
    base_err_ref: float
    holdout: dict              # the plan year's weeks with data, outside its coupon: weekly and daily errors
    control_check: dict        # the control brand against its own counterfactual in the coupon weeks
    season_curve: list         # 52 season factors (one per week, centred on 1)
    anomaly_sd: float          # sd of the weekly mean temperature anomaly: the cold / warm scenario
    extra: dict = field(default_factory=dict)

    def sample(self, d: int) -> list:
        """The worked example of the rule: the plan window, one weekday."""
        lv = self.plan.levels
        out = []
        for (y, w) in lv.win:
            f = self.feats[day_date(y, w, d)]
            P = self.data.day_demand(y, "test", w, d)
            ok = P is not None and f.open
            F = self.drivers.factor(f) if ok else None
            out.append({"year": y, "week": w, "date": day_key(y, w, d), "raw": P if ok else None, "factor": F,
                        "parts": self.drivers.parts(f) if ok else None, "adj": P / F if ok else None,
                        "status": lv.L.cells[d].status.get(week_key(y, w))})
        return out


def _measure(all_: Counterfactuals, data: Data, c: OwnCoupon, sd_in: float, sd_l: float) -> Measurement:
    weeks = c.weeks
    post = weeks + c.post
    days = [p for p in all_.days if p["year"] == c.year and p["week"] in post]
    # the level error of the frozen window is one number for the whole coupon and its dip (the same 6 weeks feed every cell)
    cum = s2 = s_cf = half = 0.0
    for p in days:
        if not p["closed"]:
            cum += p["effect"]
            s2 += p["cf"] * p["cf"]
            s_cf += p["cf"]
            half = 1.96 * math.sqrt(sd_in * sd_in * s2 + (s_cf * sd_l) ** 2)
        p["cum"], p["cumHalf"] = cum, half
    wk = {w: all_.week(c.year, w) for w in post}
    extra = total(wk[w]["effect"] for w in weeks)
    dip = -total(wk[w]["effect"] for w in c.post)
    last_c = [p for p in days if p["week"] in weeks and not p["closed"]][-1]
    last_all = [p for p in days if not p["closed"]][-1]
    pos_effect = [max(0.0, wk[w]["effect"]) for w in weeks]
    frc = total(e * data.fill_rate(c.year, "test", w) for e, w in zip(pos_effect, weeks)) / total(pos_effect)
    return Measurement(c, weeks, days, extra, dip, last_c["cumHalf"], last_all["cumHalf"], frc,
                       total(wk[w]["cf"] for w in weeks), total(wk[w]["actual"] - wk[w]["sales"] for w in weeks), wk)


def plan_factor(drv: Drivers, f, anomaly: float = 0.0) -> float:
    """The drivers of a day as the plan knows them: season and calendar as they are, weather at the given anomaly,
    and normal prices."""
    p = drv.parts(f, anomaly)
    return p["season"] * p["calendar"] * math.exp(drv.fit.coef[drv.names.index("anomaly")] * anomaly)


def _plan_days(lv: Levels, drv: Drivers, feats: dict, y: int, weeks: list) -> tuple:
    """Normal demand of every day of the given weeks at normal weather: neutral level x the plan's drivers."""
    wk, days, parts = {}, {}, {}
    for w in weeks:
        vals, pw = [], {k: 0.0 for k in DRIVER_NAMES}
        for d in range(7):
            f = feats[day_date(y, w, d)]
            if d == 6 or not f.open:
                vals.append(0.0)
                continue
            v = lv.day_level(d) * plan_factor(drv, f)
            vals.append(v)
            p = drv.parts(f, 0.0)
            for k in DRIVER_NAMES:
                pw[k] += v * (p[k] if k in ("season", "calendar") else 1.0)  # weather and price: unknown, 1
        s = total(vals)
        wk[w], days[w] = s, vals
        parts[w] = {k: div(pw[k], s) for k in DRIVER_NAMES}
    return wk, days, parts


def calibrate(data: Data, rule: RuleSettings = RuleSettings(), reference_year: int = 2025, theta_iterations: int = 5) -> Calibration:
    folder = data.folder
    cal, weather = Calendar.load(folder), Weather.load(folder)
    py, pw = data.plan_year(), data.plan_weeks()[0]
    first = iso_monday(*data.order[0])
    feats = build_features(data, cal, weather, first, dt.date(py, 12, 31))
    train_years = sorted({y for (y, w) in data.order if y < py})
    # the control brand gets the same model: the cross-check of the control assumption in the coupon weeks
    drv_c = fit_drivers(data, feats, "control", train_years)
    all_c = Counterfactuals(data, "control", drv_c, feats, rule)
    drv = fit_drivers(data, feats, "test", train_years)
    all_ = Counterfactuals(data, "test", drv, feats, rule)
    sd_in = drv.fit.sd
    # out-of-sample errors and normal weeks: every week with a full rolling window (the 6 weeks before it have data),
    # outside the own coupons and the dip after them
    full = {(r["year"], r["week"]) for r in all_.weeks if len(r["window"]) == rule.window_weeks}
    oos = [p for p in all_.days if not p["closed"] and not p["own"] and (p["year"], p["week"]) in full]
    sd_d = rms([math.log(p["P"] / p["cf"]) for p in oos])
    sd_l = math.sqrt(max(0.0, sd_d * sd_d - sd_in * sd_in))
    wk_oos = [r for r in all_.weeks if not r["own"] and (r["year"], r["week"]) in full]
    sd_w = rms([math.log(r["actual"] / r["cf"]) for r in wk_oos])
    # holdout: the plan year's weeks the drivers never saw
    hw = [r for r in wk_oos if r["year"] == py]
    hd = [p for p in oos if p["year"] == py]
    holdout = {"year": py, "weeks": len(hw), "days": len(hd),
               "weekly_rms": rms([math.log(r["actual"] / r["cf"]) for r in hw]) if hw else NAN,
               "weekly_bias": mean([r["actual"] / r["cf"] - 1 for r in hw]) if hw else NAN,
               "weekly_mad": mean([abs(r["actual"] / r["cf"] - 1) for r in hw]) if hw else NAN,
               "daily_rms": rms([math.log(p["P"] / p["cf"]) for p in hd]) if hd else NAN}

    measurements = {c.year: _measure(all_, data, c, sd_in, sd_l) for c in data.own}
    C = data.own_coupon(reference_year)
    m_ref = measurements[reference_year]
    lv_ref = all_.levels(C.year, C.start)
    profile_full = [m_ref.wk[w]["effect"] / m_ref.extra for w in C.full]
    profile_partial = m_ref.wk[C.partial_week]["effect"] / m_ref.extra if C.partial_week else None
    tail = mean(profile_full[1:])
    last_chance = (profile_partial / (tail * (lv_ref.day_share[0] + lv_ref.day_share[1]))) if C.partial_week else 1.0
    dip_weights = [-m_ref.wk[w]["effect"] / m_ref.dip for w in C.post]
    fr_normal = mean(r["fr"] for r in wk_oos)
    extra_on_sales = total(m_ref.wk[w]["sales"] - m_ref.wk[w]["cf"] * fr_normal for w in m_ref.weeks)

    # the control brand against its own counterfactual: a cross-check of the control assumption
    checks = {}
    for c in data.own:
        wk_c = {w: all_c.week(c.year, w) for w in c.weeks + c.post}
        checks[c.year] = {"coupon": div(total(wk_c[w]["effect"] for w in c.weeks), total(wk_c[w]["cf"] for w in c.weeks)),
                          "dip": div(total(wk_c[w]["effect"] for w in c.post), total(wk_c[w]["cf"] for w in c.post))}
    full_c = {(r["year"], r["week"]) for r in all_c.weeks if len(r["window"]) == rule.window_weeks}
    pairs = [(math.log(r["actual"] / r["cf"]), math.log(all_c.week(r["year"], r["week"])["actual"] / all_c.week(r["year"], r["week"])["cf"]))
             for r in wk_oos if (r["year"], r["week"]) in full_c]
    ma, mb = mean(p[0] for p in pairs), mean(p[1] for p in pairs)
    sab = total((a - ma) * (b - mb) for a, b in pairs)
    saa, sbb = total((a - ma) ** 2 for a, _ in pairs), total((b - mb) ** 2 for _, b in pairs)
    control_check = {"years": checks, "weekly_corr": div(sab, math.sqrt(saa * sbb)), "weeks": len(pairs),
                     "sd_w_control": rms([b for _, b in pairs])}

    # the plan: the frozen neutral baseline before the first plan week x the drivers of every plan day
    lv26 = all_.levels(py, pw)
    weeks26, days26, parts26 = _plan_days(lv26, drv, feats, py, data.plan_weeks())
    plan = PlanBaseline(lv26, lv26.L.week, lv26.win, weeks26, days26, parts26, mean(weeks26.values()), lv26.day_share,
                        lv26.L.cells)
    # the same plan one year earlier: what it missed in the coupon weeks (weather, prices, shocks)
    lv25 = all_.levels(C.year, C.start)
    weeks25, _, _ = _plan_days(lv25, drv, feats, C.year, m_ref.weeks)
    plan_err = [weeks25[w] / m_ref.wk[w]["cf"] - 1 for w in m_ref.weeks]

    own_years = [c.year for c in data.own]
    past = []
    for p in data.past:
        r = {"year": p.year, "weeks": p.weeks, "discount": p.discount, "mau": p.mau, "rateObs": p.rate_obs,
             "fillRate": p.fill_rate, "upr": p.upr, "extra": p.extra, "dip": p.dip, "source": p.source, "measured": False}
        if p.source == "measured" and p.year in own_years:
            m = measurements[p.year]
            r["fillRate"] = m.frc
            r["rateFull"] = p.rate_obs / r["fillRate"]
            r["couponUnitsFull"] = p.mau * r["rateFull"] * p.upr
            r["extra"] = m.extra / r["couponUnitsFull"]
            r["dip"] = m.dip / m.extra
            r["extraHalf"] = m.extra_half / r["couponUnitsFull"]
            r["measured"] = True
        else:
            r["rateFull"] = p.rate_obs / r["fillRate"]
            r["couponUnitsFull"] = p.mau * r["rateFull"] * p.upr
        past.append(r)
    fit_rate = line_fit([(p["discount"], p["rateFull"]) for p in past])
    fit_share = line_fit([(p["discount"], p["extra"]) for p in past])
    measured = next(p for p in past if p["year"] == reference_year)

    # the season curve by week (the Thursday of each week) and the cold / warm scenario
    season_curve = [math.exp(drv.season_log(feats[iso_monday(py, w) + dt.timedelta(days=3)].frac)) for w in range(1, 53)]
    wk_anom = {}
    for d, f in feats.items():
        if f.anomaly is not None:
            wk_anom.setdefault(d.isocalendar()[:2], []).append(f.anomaly)
    a = [mean(v) for v in wk_anom.values() if len(v) == 7]
    anomaly_sd = math.sqrt(mean([(x - mean(a)) ** 2 for x in a]))

    return Calibration(
        data=data, rule=rule, cal=cal, weather=weather, feats=feats, drivers=drv, drivers_control=drv_c,
        train_years=train_years, sd_in=sd_in, sd_d=sd_d, sd_l=sd_l, sd_w=sd_w, oos_days=len(oos), all=all_, all_control=all_c,
        ref=m_ref, measurements=measurements, lv_ref=lv_ref, profile_full=profile_full, profile_partial=profile_partial,
        tail=tail, last_chance=last_chance, dip_weights=dip_weights, fr_normal=fr_normal, extra_on_sales_ref=extra_on_sales,
        past=past, fit_rate=fit_rate, fit_share=fit_share, measured=measured, upr=mean(p["upr"] for p in past),
        dip_default=mean(p["dip"] for p in past), share_rel_se=(measured["extraHalf"] / 1.96) / measured["extra"],
        plan=plan, plan_window=lv26, plan_err=plan_err, base_bias_ref=mean(plan_err), base_err_ref=rms(plan_err),
        holdout=holdout, control_check=control_check, season_curve=season_curve, anomaly_sd=anomaly_sd)
