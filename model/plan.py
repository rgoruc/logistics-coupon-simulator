"""The coupon plan: redemptions -> units with the coupon -> incremental units -> dip, week by week,
and the volume to commit for a target fill rate.

  redemptions        = app users x redemption rate at full availability (Einloesequote)
  units with coupon  = redemptions x units per redemption
  incremental units  = units with coupon x incremental share
  dip                = incremental units x pulled-forward share, spread over the weeks after the coupon
  demand of a week   = plan counterfactual + incremental units of the week - dip of the week
  expected sales     = demand x target fill rate

Volume to commit (newsvendor with a fill-rate target): Q = mu + z x sigma, where the expected shortfall
sigma x G(z) equals (1 - fill rate) x mu, and G(z) = phi(z) - z (1 - Phi(z)).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .calibrate import Calibration, plan_factor
from .data import DAY_INDEX, DAYS, iso_monday, day_key, day_date
from .mathutil import js_round, total


@dataclass
class Scenario:
    discount: float = 0.20
    start_week: int = 43
    full_weeks: int = 4
    mau: float = 260000
    rate: float = 0.0                  # redemption rate at full availability
    upr: float = 1.8                   # units per redemption
    inc_share: float = 0.47            # incremental share
    pull_share: float = 0.24           # pulled-forward share (dip)
    fill_rate: float = 0.98            # target fill rate
    rate_unc: float = 0.20             # +/- uncertainty of the redemption rate
    price: float = 1.00                # Deo Test Brand regular price
    control_price: float = 2.00        # Deo Control Brand regular price
    market_avg_price: float = 1.50     # the usual category price (volume-weighted average deo price), for the price line
    lead_time_days: int = 96
    units_per_facing: int = 6
    delivery_days: list = field(default_factory=lambda: [0, 2, 4])  # 0 = Mon ... 5 = Sat
    plan_anomaly: float = 0.0          # weather in the plan weeks: degrees above the seasonal normal (0 = normal)

    def with_(self, **kw) -> "Scenario":
        return replace(self, **kw)


COUPON_LENGTHS = (2, 4)  # weeks, Monday to Saturday
LAST_WEEK = 52           # the plan year runs from week 1 to week 52; a coupon has to end by then


def length_share(cal: Calibration, weeks: int) -> float:
    """Share of a 4-week coupon's redemptions that comes in its first `weeks` weeks, from the weekly shape of the
    reference coupon. Past coupons all ran 4 weeks, so a 2-week coupon is planned as the first two weeks of one."""
    p = cal.profile_full
    return total(p[:min(weeks, len(p))]) / total(p)


def reference_rate(cal: Calibration, discount: float, weeks: int = 4) -> dict:
    """Reference redemption rate for the whole coupon: the straight line through past coupons by discount
    (a 4-week rate) x the share of a 4-week coupon's redemptions that falls in the chosen weeks."""
    f = cal.fit_rate
    share = length_share(cal, weeks)
    return {"rate": (f.intercept + f.slope * discount) * share, "extrapolated": discount < f.min - 1e-9 or discount > f.max + 1e-9,
            "lengthShare": share}


def share_for(cal: Calibration, discount: float) -> float:
    return cal.fit_share.intercept + cal.fit_share.slope * discount


def max_full_weeks(start_week: int) -> int:
    """The coupon runs whole weeks (Monday to Saturday), at most 6, and has to end by week 52."""
    return max(1, min(6, 52 - start_week + 1))


def start_weeks(data, weeks: int) -> list:
    """Weeks of the plan year a coupon of `weeks` weeks can start in: any week, as long as it ends by week 52.
    Weeks with data are a what-if on that week's normal demand."""
    return [w for w in range(1, LAST_WEEK + 1) if w + weeks - 1 <= LAST_WEEK]


def weather_factor(cal: Calibration, anomaly: float) -> float:
    """The weather driver at a temperature anomaly (degrees above the seasonal normal)."""
    return math.exp(cal.drivers.fit.coef[cal.drivers.names.index("anomaly")] * anomaly)


def normal_demand(cal: Calibration, week: int, anomaly: float = 0.0) -> float:
    """Normal demand (no coupon) of a week of the plan year: the counterfactual from the data for weeks with data
    (season, calendar, weather and prices as they were); after the data, the plan's normal demand
    (this year's calendar and the season, normal weather unless a scenario says otherwise)."""
    data = cal.data
    if week <= data.last_data_week():
        return cal.all.week(data.plan_year(), week)["cf"]
    return cal.plan.weeks[week] * weather_factor(cal, anomaly)


def day_shares(cal: Calibration, week: int) -> tuple:
    """Weekday shares and closed days of a week of the plan year. Weeks with data: from that week's daily
    counterfactual; weeks after the data: from the plan's daily normal demand. Both carry the holiday calendar
    (closed days, the shopping days before a holiday, the short day before Christmas). Sunday is closed."""
    data = cal.data
    if week <= data.last_data_week():
        ds = [p for p in cal.all.days if p["year"] == data.plan_year() and p["week"] == week]
        tot = total(p["cf"] for p in ds)
        return [p["cf"] / tot for p in ds] + [0.0], [bool(p["closed"]) for p in ds] + [True]
    vals = cal.plan.days[week]
    tot = total(vals)
    return [v / tot for v in vals], [v == 0 for v in vals]


def default_scenario(cal: Calibration) -> Scenario:
    """Scenario from data/input/plan_assumptions.csv. Empty values get the defaults of the HTML page,
    rounded the same way (reference rate to 0.1 point, shares to 1 point, units per redemption to 0.1)."""
    a, data = cal.data.assumptions, cal.data
    py, lw = data.plan_year(), data.last_data_week()
    discount = a.get("discount") if a.get("discount") is not None else 0.20
    pct = js_round(discount * 100)  # the page works in whole percent
    disc = pct / 100
    weeks = int(a.get("full_weeks") or 4)
    if weeks not in COUPON_LENGTHS:
        raise ValueError(f"full_weeks in plan_assumptions.csv must be 2 or 4, not {weeks}.")
    start = int(a.get("start_week") or (lw + 1))
    if start not in start_weeks(data, weeks):
        ok = start_weeks(data, weeks)
        raise ValueError(f"start_week {start} in plan_assumptions.csv does not work for a {weeks}-week coupon: "
                         f"it has to be between {ok[0]} and {ok[-1]}.")
    rate = a.get("redemption_rate")
    if rate is None:
        rate = js_round(reference_rate(cal, disc, weeks)["rate"] * 1000) / 1000
    upr = a.get("units_per_redemption")
    if upr is None:
        upr = js_round(cal.upr * 10) / 10
    inc = a.get("incremental_share")
    inc_share = inc if inc is not None else js_round(share_for(cal, disc) * 100) / 100
    pull = a.get("pull_forward_share")
    pull_share = pull if pull is not None else js_round(cal.dip_default * 100) / 100
    fill = a.get("target_fill_rate")
    fill_rate = fill if fill is not None else js_round(cal.fr_normal * 100) / 100
    unc = a.get("rate_uncertainty")
    days = [DAY_INDEX[d.strip()[:3].title()] for d in str(a.get("delivery_days") or "Mon Wed Fri").replace(";", " ").replace(",", " ").split()]
    return Scenario(
        discount=disc, start_week=start, full_weeks=weeks,
        mau=float(a.get("app_users") or 0), rate=rate, upr=upr, inc_share=inc_share, pull_share=pull_share,
        fill_rate=fill_rate, rate_unc=unc if unc is not None else 0.20,
        price=a.get("test_price") or data.price(py, "test", lw), control_price=a.get("control_price") or data.price(py, "control", lw),
        market_avg_price=a.get("market_avg_price") or 1.50,
        lead_time_days=int(a.get("lead_time_days") if a.get("lead_time_days") is not None else 96),
        units_per_facing=int(a.get("units_per_facing") or 6), delivery_days=sorted(set(days)),)


def coupon_plan(cal: Calibration, sc: Scenario) -> dict:
    """Weekly split of the coupon: the shape of the reference coupon, launch week strongest. The coupon runs
    whole weeks, Monday to Saturday."""
    weeks, raw, days = [], [], {}
    for i in range(sc.full_weeks):
        w = sc.start_week + i
        weeks.append(w)
        raw.append(cal.profile_full[i] if i < len(cal.profile_full) else cal.tail)
        days[w] = [0, 1, 2, 3, 4, 5]
    if max(weeks) > 52:
        raise ValueError("Coupon runs past week 52.")
    t = total(raw)
    return {"weeks": weeks, "share": {w: raw[i] / t for i, w in enumerate(weeks)}, "days": days}


# standard normal helpers (Abramowitz-Stegun erf, as in the HTML version)
def phi(z: float) -> float:
    return math.exp(-z * z / 2) / math.sqrt(2 * math.pi)


def Phi(z: float) -> float:
    t = 1 / (1 + 0.3275911 * abs(z) / math.sqrt(2))
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * math.exp(-z * z / 2)
    return (1 + y) / 2 if z >= 0 else (1 - y) / 2


def loss_g(z: float) -> float:
    return phi(z) - z * (1 - Phi(z))


@dataclass
class Forecast:
    rows: list
    plan: dict
    summary: dict


def forecast(cal: Calibration, sc: Scenario) -> Forecast:
    plan = coupon_plan(cal, sc)
    data = cal.data
    py = data.plan_year()
    redemptions = sc.mau * sc.rate
    coupon_units = redemptions * sc.upr
    incremental = coupon_units * sc.inc_share
    dip = incremental * sc.pull_share
    rows = []
    for w in range(1, LAST_WEEK + 1):
        share = plan["share"].get(w, 0.0)
        rows.append({"week": w, "monday": iso_monday(py, w), "coupon": share > 0, "share": share,
                     "couponDays": plan["days"].get(w, []), "baseline": normal_demand(cal, w, sc.plan_anomaly), "redemptions": redemptions * share,
                     "couponUnits": coupon_units * share, "incremental": incremental * share, "dip": 0.0, "dipWeek": False})
    by_week = {r["week"]: r for r in rows}
    end = max(plan["weeks"])
    dip_outside = 0.0
    for j, wgt in enumerate(cal.dip_weights):
        r = by_week.get(end + 1 + j)
        if r:
            r["dip"], r["dipWeek"] = dip * wgt, True
        else:
            dip_outside += dip * wgt
    for r in rows:
        r["demand"] = r["baseline"] + r["incremental"] - r["dip"]
        r["sales"] = r["demand"] * sc.fill_rate
        r["lost"] = r["demand"] - r["sales"]
    c_rows = [r for r in rows if r["coupon"]]
    mu = total(r["demand"] for r in c_rows)
    base_c = total(r["baseline"] for r in c_rows)
    inc_c = total(r["incremental"] for r in c_rows)
    parts = {"base": cal.base_err_ref * base_c, "share": inc_c * cal.share_rel_se, "rate": inc_c * sc.rate_unc}
    sigma = math.sqrt(parts["base"] ** 2 + parts["share"] ** 2 + parts["rate"] ** 2)

    def fill_at(z):
        return 1 - sigma * loss_g(z) / mu

    lo, hi = -3.0, 5.0
    for _ in range(80):
        z = (lo + hi) / 2
        if fill_at(z) < sc.fill_rate:
            lo = z
        else:
            hi = z
    z = (lo + hi) / 2
    promo = sc.price * (1 - sc.discount)
    summary = {
        "weeks": plan["weeks"], "redemptions": redemptions, "couponUnits": coupon_units, "anyway": coupon_units - incremental,
        "incremental": incremental, "dip": dip, "net": incremental - dip, "dipOutside": dip_outside,
        "couponDemand": mu, "couponBaseline": base_c, "couponSales": mu * sc.fill_rate, "couponLost": mu * (1 - sc.fill_rate),
        "commit": {"mu": mu, "sigma": sigma, "z": z, "q": mu + z * sigma, "buffer": z * sigma, "fillIfMu": fill_at(0), "parts": parts},
        "price": {"regular": sc.price, "promo": promo, "control": sc.control_price, "rpRegular": sc.price / sc.control_price,
                  "rpCoupon": promo / sc.control_price, "marketAvg": sc.market_avg_price},
        "startDate": iso_monday(py, sc.start_week),
    }
    return Forecast(rows, plan, summary)


def daily(cal: Calibration, sc: Scenario, res: Forecast, week: int) -> list:
    """Daily plan of one week: normal demand split by the weekday shares of that week; incremental units on the open
    coupon days."""
    py = cal.data.plan_year()
    r = next(x for x in res.rows if x["week"] == week)
    share, closed = day_shares(cal, week)
    c_days = [d for d in r["couponDays"] if not closed[d]]
    c_tot = total(share[d] for d in c_days) or 1
    out = []
    for d in range(7):
        date = day_key(py, week, d)
        if closed[d]:
            out.append({"date": date, "day": DAYS[d], "closed": True, "baseline": 0.0, "incremental": 0.0, "dip": 0.0, "demand": 0.0, "coupon": False})
            continue
        s = share[d]
        baseline, incremental, dip = r["baseline"] * s, (r["incremental"] * s / c_tot if d in c_days else 0.0), r["dip"] * s
        out.append({"date": date, "day": DAYS[d], "closed": False, "share": s, "baseline": baseline, "incremental": incremental,
                    "dip": dip, "demand": baseline + incremental - dip, "coupon": d in c_days})
    return out


def day_drivers(cal: Calibration, week: int, d: int, anomaly: float = 0.0):
    """The drivers of one day of the plan year, as the plan sees them: weeks with data as they were, plan weeks with
    the season and this year's calendar, the weather of the scenario and the rest at their usual level. None when closed."""
    data = cal.data
    f = cal.feats[day_date(data.plan_year(), week, d)]
    if not f.open:
        return None
    if week <= data.last_data_week():
        p = next(q for q in cal.all.days if q["year"] == data.plan_year() and q["week"] == week and q["d"] == d)
        return {**p["parts"], "total": p["F"], "actual": True}
    p = cal.drivers.parts(f, anomaly)
    return {"season": p["season"], "calendar": p["calendar"], "weather": p["weather"], "price": 1.0,
            "total": plan_factor(cal.drivers, f, anomaly), "actual": False}
