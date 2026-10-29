"""Rebuild html/coupon_planner.html from the CSV files in data/input.

    python html/build_html.py

What it takes from the CSVs: the daily and weekly sales, the daily temperatures, the holiday calendar, the past
coupons, the shelf data and the plan defaults (discount, start week, app users, lead time ...).
The page then recalculates everything in the browser, including the drivers regression.

What it does not take: changes to the Python model or to config.py. The page has its own copy of the logic in
JavaScript (html/src/model.src.js), and its texts are written for this case's calendar: data up to week 42 of
2026, the coupons of 2024 (weeks 18-21) and 2025 (weeks 43-47), a plan between weeks 43 and 52. For other
calendars or a changed method, use the Streamlit app.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from model.data import DAYS, Data, _num, _read, day_key  # noqa: E402

FIXED = {"plan_year": 2026, "last_data_week": 42, "own": [(2024, 18, [18, 19, 20, 21], None, [22, 23, 24]),
                                                          (2025, 43, [43, 44, 45, 46], 47, [48, 49, 50])]}


def fail(msg: str) -> None:
    print("Cannot build the page: " + msg)
    sys.exit(1)


def num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return repr(v) if isinstance(v, float) else str(v)


def build(folder: Path) -> str:
    data = Data.load(folder)
    if data.plan_year() != FIXED["plan_year"] or data.last_data_week() != FIXED["last_data_week"]:
        fail(f"the page is written for data up to week 42 of 2026; the data end in week {data.last_data_week()} of {data.plan_year()}.")
    own = [(c.year, c.start, c.full, c.partial_week, c.post) for c in data.own]
    if own != FIXED["own"]:
        fail("the page is written for the coupons 2024 weeks 18-21 and 2025 weeks 43-47 (own_coupons.csv was changed).")

    weekly = ["year;week;test;fr_test;price_test;control;fr_control;price_control;price_category;dc_orders_test"]
    for (y, w) in data.order:
        r = data.weekly[(y, w)]
        weekly.append(";".join(num(r[k]) for k in ["year", "week", "test_units", "test_fill_rate", "test_price", "control_units",
                                                     "control_fill_rate", "control_price", "category_price", "test_dc_orders"]))
    daily = ["date;test;control"] + [f"{date};{num(data.daily[date]['test'])};{num(data.daily[date]['control'])}" for date in sorted(data.daily)]
    temperature = ["date;tmax"] + [f"{r['date']};{num(_num(r['tmax_c']))}" for r in _read(folder / "temperature.csv")]
    holidays = ["date;name;kind;close_hour"] + [f"{r['date']};{r['name']};{r['kind']};{r.get('close_hour', '')}" for r in _read(folder / "holidays.csv")]
    if sorted({y for (y, w) in data.order if y < data.plan_year()}) != [2024, 2025]:
        fail("the page fits the drivers on 2024 and 2025 and tests them on 2026 (the data start in another year).")

    own_years = [c.year for c in data.own]
    past = []
    for p in data.past:
        e = {"year": p.year, "weeks": p.weeks.replace("-", "–"), "discount": p.discount, "mau": p.mau, "rateObs": p.rate_obs, "upr": p.upr}
        if p.source == "measured" and p.year in own_years:
            e["own"] = own_years.index(p.year)
        else:
            e.update({"fillRate": p.fill_rate, "extra": p.extra, "dip": p.dip})
        past.append(e)
    shelf = [{"sku": s.sku, "normalWeek": s.normal_week, "facings": s.facings} for s in data.shelf]

    a = data.assumptions
    disc = round((a.get("discount") if a.get("discount") is not None else 0.20) * 100)
    if disc not in (10, 20, 30):
        fail("the page offers discounts of 10, 20 or 30%.")
    full = int(a.get("full_weeks") or 4)
    if full not in (2, 4):
        fail("the page offers coupons of 2 or 4 weeks (full_weeks in plan_assumptions.csv).")
    start = int(a.get("start_week") or 43)
    if not 1 <= start <= 52 - full + 1:
        fail(f"a {full}-week coupon has to start between week 1 and week {52 - full + 1} (start_week in plan_assumptions.csv).")
    delivery = sorted({DAYS.index(d.strip()[:3].title()) for d in str(a.get("delivery_days") or "Mon Wed Fri").replace(";", " ").replace(",", " ").split()})
    pct = lambda k: None if a.get(k) is None else round(a[k] * 100)  # noqa: E731
    defaults = {
        "discount": disc, "startWeek": start, "fullWeeks": full,
        "unc": round((a.get("rate_uncertainty") if a.get("rate_uncertainty") is not None else 0.20) * 100),
        "lead": int(a.get("lead_time_days") if a.get("lead_time_days") is not None else 96), "depth": int(a.get("units_per_facing") or 6),
        "rate": a.get("redemption_rate"), "upr": a.get("units_per_redemption"), "inc": pct("incremental_share"),
        "pull": pct("pull_forward_share"), "fill": pct("target_fill_rate"), "price": a.get("test_price"), "controlPrice": a.get("control_price"),
        "marketAvg": a.get("market_avg_price"),
    }
    src = (HERE / "src" / "model.src.js").read_text(encoding="utf-8")
    for key, val in {"__MAU__": str(int(a.get("app_users") or 0)),
                     "__DELIVERY__": json.dumps(delivery), "__DEFAULTS__": json.dumps(defaults), "__SHELF__": json.dumps(shelf, ensure_ascii=False),
                     "__PAST__": json.dumps(past, ensure_ascii=False), "__WEEKLY__": "\n".join(weekly), "__DAILY__": "\n".join(daily),
                     "__TEMPERATURE__": "\n".join(temperature), "__HOLIDAYS__": "\n".join(holidays)}.items():
        assert key in src, key
        src = src.replace(key, val)
    model_js = src.replace('if (typeof module !== "undefined") module.exports = Model;\n', "")
    page = (HERE / "src" / "page.template.html").read_text(encoding="utf-8").replace("/*__MODEL__*/", model_js)
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body>'
            + page + "</body></html>")
    return html, src, page


def main() -> None:
    html, _, _ = build(ROOT / config.DATA_DIR)
    out = HERE / "coupon_planner.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(html) // 1024} KB). Open it in a browser.")


if __name__ == "__main__":
    main()
