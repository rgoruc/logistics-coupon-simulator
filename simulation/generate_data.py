"""Make a new fictional dataset with known coupon effects and known drivers, to test the model.

    python simulation/generate_data.py --seed 7                 # writes simulation/output_seed_7/
    python simulation/generate_data.py --seed 37 --out data/input   # the data shipped with the project

Daily demand of each brand is built from drivers the model has to find again:

    level x weekday share x season x pre-holiday lifts x weather x price x noise

- season: the usual pattern of Google Trends search interest for "deo" in Germany by week of the year (the average
  over the complete years in simulation/google_trends_deo_de.csv, smoothed), so the summer peak and the rise before
  Christmas look like the real search behaviour. Trends is only used for this shape.
- pre-holiday lifts: +20% on the first shopping day before a public holiday, +8% on the second (holidays.csv,
  Baden-Wuerttemberg; stores closed on the holiday; 24 December closes at 14:00)
- weather: +2% per degree the daily maximum (temperature.csv, Karlsruhe, Open-Meteo archive) is above its
  seasonal normal
- price: each brand's shelf price against the category price index (the volume-weighted average shelf price of men's
  deo in the Drugstore, weekly, in weekly.csv). The index moves with the control brand's -20% promotions and with
  the promotions of other brands. Deo Test Brand: elasticity -2 (the category 5% cheaper: -10%); the control brand:
  -2 against its own relative price (its promotion: about +50%).
- noise: a weekly market shock shared by both brands, a weekly brand shock, a daily shock and Poisson counts per hour
- two Deo Test Brand app coupons (2024 weeks 18-21, 10% off; 2025 weeks 43-47, 20% off), with a dip in the three
  weeks after each. The control brand is not affected by the coupons.

The data run from week 1 of 2024 to week 42 of 2026. truth.json holds the true coupon effects and driver effects.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from model.data import iso_monday  # noqa: E402
from dataclasses import dataclass

from model.drivers import Calendar, Weather, year_fraction  # noqa: E402
from model.mathutil import mean, total  # noqa: E402

TRENDS = HERE / "google_trends_deo_de.csv"   # only for the shape of the season

INPUT = ROOT / "data" / "input"
BRANDS = ("test", "control")
YEARS = (2024, 2025, 2026)
WEEKS = list(range(1, 53))
FIRST_DAY = dt.date(2024, 1, 1)
LEVEL_2024 = {"test": 25500, "control": 16700}      # units a week at the start of 2024 (before the season)
GROWTH = {"test": 0.045, "control": -0.015}          # a year, continuous
PRICE = {"test": 1.00, "control": 2.00}
PROMO = {2024: {}, 2025: {14: 0.80, 27: 0.80, 39: 0.80}, 2026: {12: 0.80, 27: 0.80, 38: 0.80}}
ETA_TEST, EPS_CONTROL = -2.0, -2.0    # each brand's elasticity to its price against the category price index
BETA = 0.02                            # weather: per degree above the seasonal normal
# the category: volume shares at normal prices and the average shelf price of all other men's deo brands
SHARE = {"test": 0.12, "control": 0.10, "other": 0.78}
OTHER_PRICE = (1.50 - SHARE["test"] * PRICE["test"] - SHARE["control"] * PRICE["control"]) / SHARE["other"]  # index 1.50 at normal prices
# promotions of other brands: week -> the other brands' average price that week (relative to normal)
OTHER_PROMO = {2024: {6: 0.95, 15: 0.94, 23: 0.96, 31: 0.93, 36: 0.95, 44: 0.94, 49: 0.96},
               2025: {4: 0.95, 10: 0.96, 19: 0.93, 33: 0.95, 41: 0.94, 50: 0.95},
               2026: {3: 0.94, 9: 0.95, 17: 0.96, 31: 0.93, 35: 0.95, 41: 0.96}}
PRE1, PRE2 = 1.20, 1.08                # first and second shopping day before a public holiday
DOW = [0.138, 0.129, 0.138, 0.169, 0.206, 0.220, 0.0]
HOURS = list(range(8, 20))
HOUR_WD = [4.0, 6.0, 7.5, 8.5, 9.0, 8.5, 8.0, 8.5, 9.5, 10.5, 10.5, 9.5]
HOUR_SAT = [4.0, 7.0, 9.5, 11.0, 11.5, 11.0, 10.0, 9.0, 8.0, 7.0, 6.5, 5.5]
FULL_W = [1.30, 1.00, 0.92, 0.98]      # weekly shape of a coupon
DIPW = [0.52, 0.29, 0.19]             # weekly shape of the dip
FR_COUPON = {2025: {43: 0.890, 44: 0.935, 45: 0.962, 46: 0.972, 47: 0.970, 48: 0.990, 49: 0.987, 50: 0.984}}
SD_MARKET, SD_BRAND, SD_DAY = 0.025, 0.015, 0.02
CASE, PALLET = 6, 1200


def price(b, y, w):
    return round(PRICE[b] * (PROMO[y].get(w, 1.0) if b == "control" else 1.0), 2)


def category_price(y, w):
    """The volume-weighted average shelf price of men's deo that week: each brand's price weighted by its volume,
    which rises when the brand is on promotion (elasticity -2 against its normal price)."""
    parts = [(SHARE["test"], price("test", y, w), PRICE["test"]), (SHARE["control"], price("control", y, w), PRICE["control"]),
             (SHARE["other"], OTHER_PRICE * OTHER_PROMO[y].get(w, 1.0), OTHER_PRICE)]
    vol = [s * (p / p0) ** -2.0 for s, p, p0 in parts]
    return round(sum(v * p for v, (_, p, _) in zip(vol, parts)) / sum(vol), 3)


def price_factor(b, y, w):
    """The brand's price against the category index, relative to the usual ratio (normal prices: index 1.50)."""
    rp = price(b, y, w) / category_price(y, w)
    return (rp / (PRICE[b] / 1.50)) ** (ETA_TEST if b == "test" else EPS_CONTROL)


@dataclass
class SearchInterest:
    values: dict     # (iso year, iso week) -> search interest
    pattern: dict    # iso week -> usual log level (over the complete years), after a straight-line trend
    deviation: dict  # (iso year, iso week) -> log deviation from the usual level

    @classmethod
    def load(cls, folder) -> "SearchInterest":
        from model.data import _read
        rows = [r for r in _read(Path(folder) / "google_trends_deo_de.csv") if str(r.get("partial", "0")).strip() in ("0", "", "False", "false")]
        keys = [(int(r["iso_year"]), int(r["iso_week"])) for r in rows]
        vals = {k: float(r["search_interest"]) for k, r in zip(keys, rows)}
        logs = [math.log(vals[k]) for k in keys]
        n = len(keys)
        mt, my = (n - 1) / 2, mean(logs)
        slope = total((i - mt) * (v - my) for i, v in enumerate(logs)) / total((i - mt) ** 2 for i in range(n))
        resid = {k: v - my - slope * (i - mt) for i, (k, v) in enumerate(zip(keys, logs))}
        years = [y for y in sorted({k[0] for k in keys}) if all((y, w) in resid for w in range(1, 53))]
        pattern = {w: mean(resid[(y, w)] for y in years) for w in range(1, 53)}
        pattern[53] = pattern[52]
        deviation = {k: resid[k] - pattern[k[1]] for k in keys}
        return cls(vals, pattern, deviation)

    def dev(self, y: int, w: int) -> float:
        """Log deviation of search interest from its usual level; 0 when the week is not in the file."""
        return self.deviation.get((y, w), 0.0)


SEASON_TRUTH_HARMONICS = 6


def season_truth(search: SearchInterest) -> dict:
    """The true season by week: a smooth curve (6 sine/cosine pairs) through the usual Trends pattern, centred so it
    averages 1. Smoother than the raw pattern, which carries the sampling noise of the Trends values."""
    from model.drivers import fourier, ols
    rows = [[1.0] + fourier(year_fraction(dt.date.fromisocalendar(2025, w, 4)), SEASON_TRUTH_HARMONICS) for w in WEEKS]
    fit = ols(rows, [search.pattern[w] for w in WEEKS])
    sm = [sum(c * v for c, v in zip(fit.coef, r)) for r in rows]
    m = sum(sm) / 52
    return {w: math.exp(v - m) for w, v in zip(WEEKS, sm)}


def season_of_day(season: dict, d: dt.date) -> float:
    """The weekly season interpolated to the day (between the Thursdays of the weeks)."""
    thu = d + dt.timedelta(days=3 - d.weekday())
    iy, iw, _ = thu.isocalendar()
    w0 = min(iw, 52)
    f = (d - thu).days / 7  # -3/7 ... +3/7
    if f >= 0:
        w1 = w0 % 52 + 1
    else:
        w1, f = (w0 - 2) % 52 + 1, -f
    return season[w0] * (1 - f) + season[w1] * f


def hour_shares(day: dt.date, close_hour: int) -> list:
    prof = HOUR_SAT if day.weekday() == 5 else HOUR_WD
    prof = [v if h < close_hour else 0.0 for v, h in zip(prof, HOURS)]
    t = sum(prof)
    return [v / t for v in prof]


def coupon_spec(mau, rate_full, upr, extra, dip, start, full, partial, last_chance=1.4):
    raw = {start + i: FULL_W[i] for i in range(full)}
    if partial:
        raw[start + full] = (sum(FULL_W[1:]) / 3) * (DOW[0] + DOW[1]) * last_chance
    prof = {w: v / sum(raw.values()) for w, v in raw.items()}
    g = mau * rate_full * upr * extra
    end = max(raw)
    return {"extra": {w: g * s for w, s in prof.items()}, "dip": {end + 1 + j: -dip * g * wgt for j, wgt in enumerate(DIPW)},
            "partial": end if partial else None, "g": g}


COUPONS = {2024: coupon_spec(205000, 0.064 / 0.98, 1.60, 0.38, 0.22, 18, 4, False),
           2025: coupon_spec(230000, 0.119, 1.90, 0.46, 0.27, 43, 4, True)}


def level(b, d: dt.date) -> float:
    return LEVEL_2024[b] * (1 + GROWTH[b]) ** ((d - FIRST_DAY).days / 365.25)


class Inputs:
    def __init__(self):
        self.cal = Calendar.load(INPUT)
        self.weather = Weather.load(INPUT)
        self.search = SearchInterest.load(HERE)
        self.season = season_truth(self.search)


def simulate(seed: int, inp: Inputs) -> dict:
    """Returns {"weekly": [...], "hourly": [...], "truth": {...}} in the project's CSV formats."""
    # separate random streams, so a run without the coupons differs only in the coupon and dip weeks
    rng_shock, rng_fr = np.random.default_rng([seed, 1]), np.random.default_rng([seed, 2])
    hourly, fr, sales = {}, {}, {}
    keep = [(2024, w) for w in WEEKS] + [(2025, w) for w in WEEKS] + [(2026, w) for w in range(1, 43)]
    for (y, w) in keep:
        market = rng_shock.normal(0, SD_MARKET)
        c = COUPONS.get(y)
        mon = iso_monday(y, w)
        wdays = [mon + dt.timedelta(days=d) for d in range(7)]
        for b in BRANDS:
            brand_shock = rng_shock.normal(0, SD_BRAND)
            day_shock = rng_shock.normal(0, SD_DAY, 6)
            f = float(np.clip(0.980 + rng_fr.normal(0, 0.0025), 0.972, 0.988))
            if b == "test" and w in FR_COUPON.get(y, {}):
                f = FR_COUPON[y][w]
            f = round(f, 3)
            fr[(y, b, w)] = f
            # demand of every day without the coupon
            dem = {}
            for d in range(6):
                day = wdays[d]
                if not inp.cal.is_open(day):
                    dem[day] = 0.0
                    continue
                n1, n2 = inp.cal.pre_holiday(day)
                anom = inp.weather.anomaly(day) or 0.0
                v = (level(b, day) * DOW[d] * season_of_day(inp.season, day) * PRE1 ** n1 * PRE2 ** n2
                     * math.exp(BETA * anom) * price_factor(b, y, w)
                     * math.exp(market + brand_shock + day_shock[d]))
                close = inp.cal.close_hour(day, HOURS[-1])
                dem[day] = v * sum(hour_shares(day, HOURS[-1] + 1)[i] for i, h in enumerate(HOURS) if h < close)
            amount = c["extra"].get(w, 0.0) + c["dip"].get(w, 0.0) if (c and b == "test") else 0.0
            cdays = [d for d in range(6) if dem[wdays[d]] > 0]
            if amount and c and w == c["partial"]:
                cdays = [d for d in cdays if d <= 1]
            ctot = sum(dem[wdays[d]] for d in cdays)
            tot_sales = 0
            for d in range(6):
                day = wdays[d]
                v = dem[day] + (amount * dem[day] / ctot if amount and d in cdays else 0.0)
                close = inp.cal.close_hour(day, HOURS[-1])
                hs = hour_shares(day, close) if dem[day] > 0 else [0.0] * len(HOURS)
                rng_p = np.random.default_rng([seed, 3, y, w, d, BRANDS.index(b)])
                vals = [int(rng_p.poisson(max(v, 0) * s * f)) for s in hs]
                hourly[(b, day)] = vals
                tot_sales += sum(vals)
            sales[(y, b, w)] = tot_sales

    # distribution centre orders in 2025 (a simple order policy that does not know the coupon)
    round_to = lambda x, q: int(q * round(x / q))  # noqa: E731
    ceil_to = lambda x, q: int(q * math.ceil(x / q - 1e-9))  # noqa: E731
    DELTA_S, ALPHA_S, DELTA_D, ALPHA_D, TP_D = 1.0, 0.3, 1.0, 0.2, 1
    dmd = {w: sales[(2025, "test", w)] for w in WEEKS}
    fc = dmd[1]
    ns, o_prev, so = DELTA_S * fc, fc, {}
    for w in WEEKS:  # store orders: exponential smoothing of sales plus a safety stock
        ns = ns + o_prev - dmd[w]
        fc = ALPHA_S * dmd[w] + (1 - ALPHA_S) * fc
        so[w] = o_prev = max(0, round_to(fc * (1 + DELTA_S) - ns, CASE))
    fD = so[1]
    nsD, O0 = DELTA_D * fD, ceil_to(fD, PALLET)
    hist, wip, dc = {-1: O0, 0: O0}, O0, {}
    for w in WEEKS:  # distribution centre orders with one week of transport
        r = hist[w - 2]
        nsD = nsD + r - so[w]
        wip = wip + hist[w - 1] - r
        fD = ALPHA_D * so[w] + (1 - ALPHA_D) * fD
        dc[w] = hist[w] = max(0, ceil_to((1 + DELTA_D + TP_D) * fD - (nsD + wip), PALLET))

    weekly_rows, hourly_rows = [], []
    for (y, w) in keep:
        weekly_rows.append({"year": y, "week": w, "test_units": sales[(y, "test", w)], "test_fill_rate": fr[(y, "test", w)],
                            "test_price": price("test", y, w), "control_units": sales[(y, "control", w)],
                            "control_fill_rate": fr[(y, "control", w)], "control_price": price("control", y, w), "category_price": category_price(y, w),
                            "test_dc_orders": dc[w] if y == 2025 else ""})
        mon = iso_monday(y, w)
        for dd in range(6):
            day = mon + dt.timedelta(days=dd)
            if not inp.cal.is_open(day):
                continue
            iy, iw, wd = day.isocalendar()
            for b in BRANDS:
                for h, v in zip(HOURS, hourly[(b, day)]):
                    hourly_rows.append([day.isoformat(), iy, iw, wd, h, b, v])
    truth = {"seed": seed}
    for y in COUPONS:
        truth[f"incremental_{y}"] = round(COUPONS[y]["g"])
        truth[f"dip_{y}"] = round(-sum(COUPONS[y]["dip"].values()))
    truth.update({"weather_per_degree": BETA, "pre_holiday_1": PRE1, "pre_holiday_2": PRE2,
                  "price_elasticity_test": ETA_TEST, "price_elasticity_control": EPS_CONTROL, "season_by_week": {w: round(v, 4) for w, v in inp.season.items()}})
    return {"weekly": weekly_rows, "hourly": hourly_rows, "truth": truth}


def write(sim: dict, out: Path, copy_static_from: Path | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "weekly.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(sim["weekly"][0].keys()))
        wr.writeheader()
        wr.writerows(sim["weekly"])
    with open(out / "hourly_sales.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["date", "year", "week", "weekday", "hour", "brand", "units"])
        wr.writerows(sim["hourly"])
    (out / "truth.json").write_text(json.dumps(sim["truth"], indent=1), encoding="utf-8")
    if copy_static_from and copy_static_from.resolve() != out.resolve():
        for name in ("own_coupons.csv", "past_coupons.csv", "plan_assumptions.csv", "shelf.csv", "holidays.csv",
                     "temperature.csv"):
            shutil.copy(copy_static_from / name, out / name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=37)
    ap.add_argument("--out", default=None, help="output folder (default simulation/output_seed_<seed>)")
    args = ap.parse_args()
    out = Path(args.out) if args.out else HERE / f"output_seed_{args.seed}"
    sim = simulate(args.seed, Inputs())
    write(sim, out, INPUT)
    print(f"Wrote {out}: {len(sim['weekly'])} weeks, {len(sim['hourly'])} hourly rows. True effects: {sim['truth']}")


if __name__ == "__main__":
    main()
