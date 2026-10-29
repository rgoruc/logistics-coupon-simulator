"""Loading the input CSVs (data/input) and looking up demand.

Demand = sales / fill rate of that week, so weeks with empty shelves do not pull the baseline down.
Days without a row in daily_sales.csv are closed (Sundays and public holidays). The holiday calendar, the daily
temperatures (the drivers' inputs) are loaded by drivers.py from the same folder. weekly.csv carries the
category price index (category_price: the volume-weighted average shelf price of men's deo) next to the brands' prices.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

BRANDS = ("test", "control")  # the coupon brand and the control brand
NAME, CONTROL = "Deo Test Brand", "Deo Control Brand"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_INDEX = {d: i for i, d in enumerate(DAYS)}


def iso_monday(year: int, week: int) -> dt.date:
    """Monday of ISO calendar week `week` of `year`."""
    jan4 = dt.date(year, 1, 4)
    return jan4 - dt.timedelta(days=jan4.weekday()) + dt.timedelta(weeks=week - 1)


def day_date(year: int, week: int, d: int) -> dt.date:
    """Date of weekday d (0 = Monday ... 6 = Sunday) in ISO week `week`."""
    return iso_monday(year, week) + dt.timedelta(days=d)


def day_key(year: int, week: int, d: int) -> str:
    return day_date(year, week, d).isoformat()


def week_key(year: int, week: int) -> str:
    return f"{year}-{week}"


@dataclass
class OwnCoupon:
    """A Deo Test Brand coupon inside the data: its weeks are measured and kept out of the baseline."""
    year: int
    start: int
    full: list
    partial_week: int | None
    post: list  # the dip weeks after the coupon
    discount: float | None = None
    note: str = ""

    @property
    def weeks(self) -> list:
        return self.full + ([self.partial_week] if self.partial_week else [])

    def covers(self, year: int, week: int) -> bool:
        return year == self.year and (week in self.full or week == self.partial_week or week in self.post)


@dataclass
class PastCoupon:
    year: int
    weeks: str
    discount: float
    mau: float
    rate_obs: float
    fill_rate: float | None
    upr: float
    extra: float | None
    dip: float | None
    source: str  # "report" or "measured"


@dataclass
class ShelfItem:
    sku: str
    normal_week: float
    facings: int


def _num(text: str):
    """Number from a CSV cell; accepts 0.2 and 0,2 (Excel in German) and 1.234,5."""
    text = (text or "").strip().replace(" ", "")
    if text == "":
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".") if "." in text else text.replace(",", ".")
    v = float(text)
    return int(v) if v.is_integer() and "." not in text and "e" not in text.lower() else v


_GERMAN_NUMBER = re.compile(r"^-?[0-9.]*,?[0-9]+$")


def _read(path: Path) -> list:
    """Rows of a CSV file as dicts. Accepts comma or semicolon separated files, UTF-8 or Windows encoding,
    so a file saved again from Excel still loads."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    first = text.splitlines()[0] if text else ""
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
    out = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if k is None:  # extra cells (an unquoted comma in a note): ignore
                continue
            v = (v or "").strip()
            if delim == ";" and _GERMAN_NUMBER.match(v):  # 1.234,5 -> 1234.5
                v = v.replace(".", "").replace(",", ".")
            row[(k or "").strip()] = v
        out.append(row)
    return out


@dataclass
class Data:
    weekly: dict                      # (year, week) -> row dict
    order: list                       # [(year, week)] weeks with data, in time order
    daily: dict                       # "YYYY-MM-DD" -> {"test": units sold, "control": units sold}
    own: list                         # [OwnCoupon]
    past: list                        # [PastCoupon]
    shelf: list                       # [ShelfItem]
    assumptions: dict                 # parameter -> value (None when empty)
    folder: Path = Path("data/input")
    _dd: dict = field(default_factory=dict, repr=False)

    # ---------------- loading ----------------
    @classmethod
    def load(cls, folder) -> "Data":
        folder = Path(folder)
        weekly, order = {}, []
        for r in _read(folder / "weekly.csv"):
            row = {k: _num(v) for k, v in r.items()}
            key = (int(row["year"]), int(row["week"]))
            weekly[key] = row
            order.append(key)
        order.sort()

        daily = {}
        for r in _read(folder / "daily_sales.csv"):
            day = daily.setdefault(r["date"].strip(), {b: None for b in BRANDS})
            day[r["brand"].strip().lower()] = float(_num(r["units"]))
        for date, day in daily.items():
            for b in BRANDS:
                if day[b] is None:
                    raise ValueError(f"daily_sales.csv: {date} has no row for {b}")

        own = []
        for r in _read(folder / "own_coupons.csv"):
            start, n_full = int(_num(r["start_week"])), int(_num(r["full_weeks"]))
            full = list(range(start, start + n_full))
            partial = _num(r.get("partial_week"))
            last = partial or full[-1]
            n_dip = int(_num(r.get("dip_weeks")) or 3)
            own.append(OwnCoupon(int(_num(r["year"])), start, full, int(partial) if partial else None,
                                 list(range(last + 1, last + 1 + n_dip)), _num(r.get("discount")), r.get("note", "")))

        past = []
        for r in _read(folder / "past_coupons.csv"):
            past.append(PastCoupon(int(_num(r["year"])), r["weeks"], float(_num(r["discount"])), float(_num(r["app_users"])),
                                   float(_num(r["observed_rate"])), _num(r["fill_rate"]), float(_num(r["units_per_redemption"])),
                                   _num(r["incremental_share"]), _num(r["dip_share"]), r["source"].strip().lower()))

        shelf = [ShelfItem(r["sku"], float(_num(r["normal_week_units"])), int(_num(r["facings"]))) for r in _read(folder / "shelf.csv")]

        assumptions = {}
        for r in _read(folder / "plan_assumptions.csv"):
            v = (r["value"] or "").strip()
            if r["parameter"] == "delivery_days":
                assumptions["delivery_days"] = v
            else:
                assumptions[r["parameter"]] = _num(v)
        return cls(weekly, order, daily, own, past, shelf, assumptions, folder)

    # ---------------- lookups ----------------
    def row(self, y: int, w: int) -> dict:
        return self.weekly[(y, w)]

    def has_data(self, y: int, w: int) -> bool:
        return (y, w) in self.weekly

    def fill_rate(self, y: int, brand: str, w: int) -> float:
        return self.weekly[(y, w)][f"{brand}_fill_rate"]

    def price(self, y: int, brand: str, w: int) -> float:
        return self.weekly[(y, w)][f"{brand}_price"]

    def day_demand(self, y: int, brand: str, w: int, d: int):
        """Demand on weekday d (sales / fill rate of the week), None when the store was closed."""
        k = (brand, y, w, d)
        if k not in self._dd:
            r = self.daily.get(day_key(y, w, d))
            self._dd[k] = r[brand] / self.fill_rate(y, brand, w) if r else None
        return self._dd[k]

    def day_sales(self, y: int, brand: str, w: int, d: int):
        r = self.daily.get(day_key(y, w, d))
        return r[brand] if r else None

    # ---------------- calendar of the plan ----------------
    def plan_year(self) -> int:
        return int(self.assumptions.get("plan_year") or self.order[-1][0])

    def last_data_week(self) -> int:
        y = self.plan_year()
        return max(w for (yy, w) in self.order if yy == y)

    def plan_weeks(self) -> list:
        return list(range(self.last_data_week() + 1, 53))

    def own_of(self, y: int, w: int):
        for c in self.own:
            if c.covers(y, w):
                return c
        return None

    def own_coupon(self, year: int) -> OwnCoupon:
        for c in self.own:
            if c.year == year:
                return c
        raise ValueError(f"own_coupons.csv has no coupon in {year}")
