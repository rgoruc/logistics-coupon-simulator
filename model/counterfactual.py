"""Counterfactual: what Deo Test Brand would have sold without its coupon.

    counterfactual of a day = L[weekday] x F(day)

- F(day) is the product of the drivers of that day: season x calendar x weather x price (drivers.py). Their effects come from one regression on the log of daily demand, fitted on the years before
  the plan year, outside Deo Test Brand's own coupons and the dip after them (fit_drivers).
- L is the baseline from the rule (baseline.py) on "neutral" values: every day's demand divided by F of that day,
  so the 6 weeks of the window are put on the same footing before the median is taken. L is "Deo Test Brand on
  a neutral day": average season, no holiday nearby, a full opening day, normal weather, normal prices.
- The window rolls: each week's baseline comes from the 6 latest weeks before it that have data and are not a
  Deo Test Brand coupon or the dip after one. During a coupon and its dip the window is frozen at the 6 weeks
  before the coupon, so the coupon cannot leak into its own baseline.
- The control brand (Deo Control Brand) gets the same model (its own drivers and baseline). In the coupon weeks it
  is the cross-check of the control assumption: if the coupon had pulled the control's buyers, the control would
  fall below its own counterfactual.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from .baseline import Baseline, RuleSettings, baseline_of, robust_level
from .data import Data, day_date, day_key, week_key
from .drivers import DRIVER_NAMES, SEASON_HARMONICS, DayFeatures, Fit, ols, year_fraction, fourier
from .mathutil import NAN, div, mean, or0, total

PRE_WEEKDAYS = 6


def positions(data: Data) -> tuple:
    """Every week in time order: the weeks with data, then the plan weeks."""
    order = list(data.order) + [(data.plan_year(), w) for w in data.plan_weeks()]
    return order, {k: i for i, k in enumerate(order)}


def window_for(data: Data, y: int, w: int, rule: RuleSettings = RuleSettings()) -> list:
    """The weeks a week's baseline comes from (frozen at the coupon start during a coupon and its dip)."""
    order, pos = positions(data)
    own = data.own_of(y, w)
    i = pos[(own.year, own.start) if own else (y, w)] - 1
    out = []
    while len(out) < rule.window_weeks and i >= 0:
        yy, ww = order[i]
        if data.has_data(yy, ww) and not data.own_of(yy, ww):
            out.insert(0, (yy, ww))
        i -= 1
    return out


# ---------------------------------------------------------------- the drivers regression
@dataclass
class Drivers:
    """Fitted effects of the drivers (log scale) for one brand."""
    brand: str
    train_years: list
    fit: Fit
    names: list           # column names of the regression
    season_mean: float    # mean of the season term over a year (the season is shown and used centred on it)

    # column layout: 6 weekday dummies, 2 x SEASON_HARMONICS season terms, pre1, pre2, xmas, short, anomaly, z, trend
    # (z = the brand's own relative price against the category price index)
    @property
    def season_coef(self) -> list:
        return self.fit.coef[PRE_WEEKDAYS:PRE_WEEKDAYS + 2 * SEASON_HARMONICS]

    def _c(self, name: str) -> float:
        return self.fit.coef[self.names.index(name)]

    def _se(self, name: str) -> float:
        return self.fit.se[self.names.index(name)]

    def season_log(self, frac: float) -> float:
        """The season term (log) at a position in the year, centred on its yearly mean."""
        return total(c * v for c, v in zip(self.season_coef, fourier(frac, SEASON_HARMONICS))) - self.season_mean

    def parts(self, f: DayFeatures, anomaly=None) -> dict:
        """The factor of each driver for a day (anomaly=None: the day's own anomaly; 0 when it has none)."""
        a = f.anomaly if anomaly is None else anomaly
        return {
            "season": math.exp(self.season_log(f.frac)),
            "calendar": math.exp(self._c("pre1") * f.pre1 + self._c("pre2") * f.pre2 + self._c("xmas") * f.xmas
                                 + self._c("short") * f.short),
            "weather": math.exp(self._c("anomaly") * (a or 0.0)),
            "price": math.exp(self._c("z") * (f.z if self.brand == "test" else f.zc)),
        }

    def factor(self, f: DayFeatures, anomaly=None) -> float:
        p = self.parts(f, anomaly)
        out = 1.0
        for k in DRIVER_NAMES:
            out *= p[k]
        return out

    def effects(self) -> list:
        """The effects as shown on the page: name, size (log), standard error, and a few readable numbers."""
        coef, se = self._c, self._se
        return [
            {"key": "pre1", "coef": coef("pre1"), "se": se("pre1")},
            {"key": "pre2", "coef": coef("pre2"), "se": se("pre2")},
            {"key": "xmas", "coef": coef("xmas"), "se": se("xmas")},
            {"key": "short", "coef": coef("short"), "se": se("short")},
            {"key": "anomaly", "coef": coef("anomaly"), "se": se("anomaly")},
            {"key": "z", "coef": coef("z"), "se": se("z")},
            {"key": "trend", "coef": coef("trend"), "se": se("trend")},
        ]


def design_row(f: DayFeatures, brand: str) -> list:
    row = [1.0 if f.weekday == d else 0.0 for d in range(PRE_WEEKDAYS)]
    row += f.season
    row += [f.pre1, f.pre2, f.xmas, f.short, f.anomaly or 0.0, f.z if brand == "test" else f.zc, f.t_years]
    return row


COLUMNS = [f"wd{d}" for d in range(PRE_WEEKDAYS)] + [f"s{k}{t}" for k in range(1, SEASON_HARMONICS + 1) for t in ("c", "s")] + \
          ["pre1", "pre2", "xmas", "short", "anomaly", "z", "trend"]


def fit_drivers(data: Data, feats: dict, brand: str, train_years: list) -> Drivers:
    """One regression per brand on the log of daily demand: open days of the training years with a temperature,
    and (for the coupon brand) outside its own coupons and the dip after them."""
    rows, y = [], []
    for (yy, ww) in data.order:
        if yy not in train_years or (brand == "test" and data.own_of(yy, ww)):
            continue
        for d in range(6):
            f = feats[day_date(yy, ww, d)]
            if not f.open or f.anomaly is None:
                continue
            P = data.day_demand(yy, brand, ww, d)
            if not P:
                continue
            rows.append(design_row(f, brand))
            y.append(math.log(P))
    fit = ols(rows, y)
    coef = fit.coef[PRE_WEEKDAYS:PRE_WEEKDAYS + 2 * SEASON_HARMONICS]
    smean = mean(total(c * v for c, v in zip(coef, fourier((i + 0.5) / 365, SEASON_HARMONICS))) for i in range(365))
    return Drivers(brand, list(train_years), fit, COLUMNS, smean)


# ---------------------------------------------------------------- levels of one window
class Levels:
    """The baseline L of one window on neutral values (demand / F of the day), per weekday."""

    def __init__(self, data: Data, win: list, brand: str, drv: Drivers, feats: dict, rule: RuleSettings):
        self.data, self.win, self.brand = data, win, brand
        cells = []
        for d in range(6):
            items = []
            for (y, w) in win:
                f = feats[day_date(y, w, d)]
                P = data.day_demand(y, brand, w, d)
                items.append((week_key(y, w), P / drv.factor(f) if P is not None and f.open else None))
            cells.append(robust_level(items, rule))
        day = [c.level for c in cells]
        s = total(day)
        self.L = Baseline(cells, day, s)
        self.day_share = [div(v, s) for v in day]

    def day_level(self, d: int) -> float:
        """The neutral level of a weekday."""
        return self.L.day[d]


class Counterfactuals:
    """Daily and weekly counterfactual of one brand for every week with data."""

    def __init__(self, data: Data, brand: str, drv: Drivers, feats: dict, rule: RuleSettings):
        self.data, self.brand, self.drv, self.feats, self.rule = data, brand, drv, feats, rule
        self._cache = {}
        self.days, self.weeks = [], []
        for (y, w) in data.order:
            v, own = self.levels(y, w), data.own_of(y, w)
            ds = []
            for d in range(6):
                f = feats[day_date(y, w, d)]
                P = data.day_demand(y, brand, w, d)
                base = {"year": y, "week": w, "d": d, "date": day_key(y, w, d), "own": own is not None}
                if P is None or not f.open:
                    ds.append({**base, "closed": True, "cf": 0.0, "effect": 0.0})
                    continue
                F = drv.factor(f)
                cf = v.day_level(d) * F
                ds.append({**base, "closed": False, "P": P, "Psales": data.day_sales(y, brand, w, d), "F": F,
                           "parts": drv.parts(f), "cf": cf, "effect": P - cf})
            self.days.extend(ds)
            actual = total(or0(p.get("P")) for p in ds)
            cf = total(or0(p.get("cf")) for p in ds)
            self.weeks.append({"year": y, "week": w, "actual": actual, "cf": cf, "effect": actual - cf,
                               "own": own is not None, "sales": total(or0(p.get("Psales")) for p in ds),
                               "level": v.L.week, "window": v.win, "fr": data.fill_rate(y, brand, w),
                               "factor": div(cf, total(v.day_level(p["d"]) for p in ds if not p["closed"]))})

    def levels(self, y: int, w: int) -> Levels:
        win = window_for(self.data, y, w, self.rule)
        key = ",".join(week_key(a, b) for a, b in win)
        if key not in self._cache:
            self._cache[key] = Levels(self.data, win, self.brand, self.drv, self.feats, self.rule)
        return self._cache[key]

    def week(self, y: int, w: int) -> dict:
        for r in self.weeks:
            if r["year"] == y and r["week"] == w:
                return r
        raise KeyError((y, w))
