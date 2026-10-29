"""The drivers of normal demand, each one a separate factor:

    normal demand of a day = baseline[weekday, hour] x season x calendar x weather x price

- season:   a smooth curve over the year (5 sine/cosine pairs), the same every year
- calendar: the first and second shopping day before a public holiday, and the run-up to Christmas
            (1 to 24 December, rising to its full effect on 23 December)
- weather:  the day's maximum temperature against the seasonal normal (degrees above or below)
- price:    the brand's shelf price against the category price index (the volume-weighted average shelf price of
            men's deo in the Drugstore, weekly), relative to its usual level

The effects are measured together in one regression on the log of daily demand (counterfactual.py), on the
years before the plan year, outside Deo Test Brand's own coupons. The weekday and the level of demand are not
drivers: the baseline rule carries them (baseline.py), week by week. This file holds the inputs each driver
needs: the holiday calendar, the temperature normal and the small regression.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

from .mathutil import NAN, mean, total

SEASON_HARMONICS = 5   # sine/cosine pairs of the season curve
TEMP_HARMONICS = 2     # sine/cosine pairs of the temperature normal
XMAS_FROM, XMAS_TO = 1, 24  # the Christmas run-up: December 1 to 24, rising to its full effect on December 23


def year_fraction(d: dt.date) -> float:
    """Position of a day in its year, 0 at January 1 and just below 1 at December 31."""
    days = 366 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 365
    return (d - dt.date(d.year, 1, 1)).days / days


def fourier(frac: float, harmonics: int) -> list:
    """cos and sin of 1 ... harmonics cycles a year."""
    out = []
    for k in range(1, harmonics + 1):
        out.append(math.cos(2 * math.pi * k * frac))
        out.append(math.sin(2 * math.pi * k * frac))
    return out


def christmas_ramp(d: dt.date) -> float:
    """0 outside the run-up, rising from 1/23 on December 1 to 1 on December 23 and 24."""
    if d.month != 12 or d.day < XMAS_FROM or d.day > XMAS_TO:
        return 0.0
    return min(1.0, d.day / (XMAS_TO - 1))


# ---------------------------------------------------------------- the small regression (also in the JavaScript model)
def solve(a: list, b: list) -> list:
    """Solves A x = b by Gaussian elimination with partial pivoting (A is copied)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = col
        for r in range(col + 1, n):
            if abs(m[r][col]) > abs(m[piv][col]):
                piv = r
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        if p == 0:
            raise ValueError("singular design matrix")
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / p
            if f == 0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def inverse(a: list) -> list:
    """Inverse of a square matrix by Gauss-Jordan elimination (for the standard errors)."""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        piv = col
        for r in range(col + 1, n):
            if abs(m[r][col]) > abs(m[piv][col]):
                piv = r
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        for c in range(2 * n):
            m[col][c] /= p
        for r in range(n):
            if r == col:
                continue
            f = m[r][col]
            if f == 0:
                continue
            for c in range(2 * n):
                m[r][c] -= f * m[col][c]
    return [row[n:] for row in m]


@dataclass
class Fit:
    coef: list
    se: list          # standard error of each coefficient
    sd: float         # standard deviation of the residuals
    n: int
    residuals: list


def ols(rows: list, y: list) -> Fit:
    """Ordinary least squares: rows are the x values of each observation (no intercept is added)."""
    n, p = len(rows), len(rows[0])
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for row, v in zip(rows, y):
        for i in range(p):
            xi = row[i]
            if xi == 0:
                continue
            xty[i] += xi * v
            xr = xtx[i]
            for j in range(p):
                xr[j] += xi * row[j]
    coef = solve(xtx, xty)
    res = [v - total(c * x for c, x in zip(coef, row)) for row, v in zip(rows, y)]
    s2 = total(r * r for r in res) / (n - p)
    inv = inverse(xtx)
    return Fit(coef, [math.sqrt(max(0.0, s2 * inv[i][i])) for i in range(p)], math.sqrt(s2), n, res)


# ---------------------------------------------------------------- the holiday calendar
@dataclass
class Calendar:
    """Public holidays (stores closed) and short days (stores close early), from holidays.csv."""
    closed: set
    short: dict          # date -> closing hour
    names: dict

    @classmethod
    def load(cls, folder) -> "Calendar":
        from .data import _read, _num
        closed, short, names = set(), {}, {}
        for r in _read(Path(folder) / "holidays.csv"):
            d = dt.date.fromisoformat(r["date"].strip())
            names[d] = r.get("name", "")
            if r["kind"].strip().lower() == "short":
                short[d] = int(_num(r.get("close_hour")) or 14)
            else:
                closed.add(d)
        return cls(closed, short, names)

    def is_open(self, d: dt.date) -> bool:
        return d.weekday() != 6 and d not in self.closed

    def close_hour(self, d: dt.date, last_hour: int) -> int:
        """First hour the store is closed: the usual closing hour, earlier on a short day, 0 when closed."""
        if not self.is_open(d):
            return 0
        return self.short.get(d, last_hour + 1)

    def pre_holiday(self, d: dt.date) -> tuple:
        """How many closed holidays (Monday to Saturday) have this day as their first or second shopping day before."""
        n1 = n2 = 0
        if not self.is_open(d):
            return 0, 0
        for h in self.closed:
            if h.weekday() == 6 or h <= d or (h - d).days > 6:
                continue
            k, day = 0, h - dt.timedelta(days=1)
            while k < 2 and day >= d:
                if self.is_open(day):
                    k += 1
                    if day == d:
                        if k == 1:
                            n1 += 1
                        else:
                            n2 += 1
                        break
                day -= dt.timedelta(days=1)
        return n1, n2


# ---------------------------------------------------------------- weather: the seasonal normal of the daily maximum
@dataclass
class Weather:
    tmax: dict       # date -> maximum temperature
    coef: list       # temperature normal: intercept + 2 sine/cosine pairs

    @classmethod
    def load(cls, folder) -> "Weather":
        from .data import _read
        tmax = {}
        for r in _read(Path(folder) / "temperature.csv"):
            tmax[dt.date.fromisoformat(r["date"].strip())] = float(r["tmax_c"])
        days = sorted(tmax)
        fit = ols([[1.0] + fourier(year_fraction(d), TEMP_HARMONICS) for d in days], [tmax[d] for d in days])
        return cls(tmax, fit.coef)

    def normal(self, d: dt.date) -> float:
        x = [1.0] + fourier(year_fraction(d), TEMP_HARMONICS)
        return total(c * v for c, v in zip(self.coef, x))

    def anomaly(self, d: dt.date):
        """Degrees above the seasonal normal; None when the day has no temperature."""
        t = self.tmax.get(d)
        return None if t is None else t - self.normal(d)

    @property
    def last_day(self) -> dt.date:
        return max(self.tmax)


# ---------------------------------------------------------------- the features of one day
DRIVER_NAMES = ["season", "calendar", "weather", "price"]


@dataclass
class DayFeatures:
    date: dt.date
    weekday: int          # 0 = Monday
    open: bool
    close_hour: int       # first closed hour (0 when closed all day)
    frac: float
    season: list          # fourier terms
    pre1: float
    pre2: float
    xmas: float
    anomaly: float | None  # None after the temperature data
    z: float              # Deo Test Brand: log(price / category price index) against its norm
    zc: float             # the control brand: the same with its own price
    t_years: float        # years since the first data day (for the trend in the fit)


def z_norm(data, brand: str) -> float:
    """The norm of a brand's relative price (its price / the category price index): the median over the weeks with
    data (log)."""
    vals = sorted(math.log(data.price(y, brand, w) / data.price(y, "category", w)) for (y, w) in data.order)
    return vals[len(vals) // 2]


def build_features(data, cal: Calendar, weather: Weather, first: dt.date, last: dt.date) -> dict:
    """DayFeatures for every date from first to last. Weeks without prices (after the data) have z = 0."""
    zn, znc = z_norm(data, "test"), z_norm(data, "control")
    out = {}
    d = first
    while d <= last:
        iy, iw, wd = d.isocalendar()
        has = data.has_data(iy, iw)
        z = math.log(data.price(iy, "test", iw) / data.price(iy, "category", iw)) - zn if has else 0.0
        zc = math.log(data.price(iy, "control", iw) / data.price(iy, "category", iw)) - znc if has else 0.0
        n1, n2 = cal.pre_holiday(d)
        out[d] = DayFeatures(d, wd - 1, cal.is_open(d), cal.close_hour(d, data.hours[-1]), year_fraction(d),
                             fourier(year_fraction(d), SEASON_HARMONICS), float(n1), float(n2), christmas_ramp(d),
                             weather.anomaly(d), z, zc, (d - first).days / 365.25)
        d += dt.timedelta(days=1)
    return out
