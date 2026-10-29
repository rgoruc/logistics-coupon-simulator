"""Run the SQL steps 02-08 on your own computer, without Snowflake, and compare them with the Python model.

    python sql/run_sql_locally.py

It uses SQLite, which comes with Python: the CSV files from data/input and the drivers of every day
(data/output/day_factors.csv, written by `python run_model.py`) are loaded into a database file
(data/output/coupon_case.sqlite), the same SQL files run on it, and the results are compared with the Python
model. Step 01 is Snowflake-only (stage and COPY INTO) and is replaced here by a small loader.
The SQL is written so that it runs unchanged on Snowflake and on SQLite.

To look at the tables, open data/output/coupon_case.sqlite with a free tool such as "DB Browser for SQLite",
or in VS Code with a SQLite extension.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from model import calibrate, load  # noqa: E402
from model.baseline import RuleSettings  # noqa: E402
from model.data import _read  # noqa: E402
from model.outputs import day_factors  # noqa: E402

SQL_DIR = ROOT / "sql"
STEPS = ["02_parameters.sql", "03_demand.sql", "04_windows.sql", "05_baseline_rule.sql",
         "06_control_baseline.sql", "07_test_brand_baseline.sql", "08_counterfactual.sql"]

TABLES = {
    "daily_sales": ("daily_sales.csv", [("sales_date", "TEXT", "date"), ("year", "INT", None), ("week", "INT", None),
                                        ("weekday", "INT", None), ("brand", "TEXT", None), ("units", "INT", None)]),
    "weekly": ("weekly.csv", [("year", "INT", None), ("week", "INT", None), ("test_units", "INT", None),
                              ("test_fill_rate", "REAL", None), ("test_price", "REAL", None), ("control_units", "INT", None),
                              ("control_fill_rate", "REAL", None), ("control_price", "REAL", None), ("category_price", "REAL", None),
                              ("test_dc_orders", "INT", None)]),
    "own_coupons": ("own_coupons.csv", [("year", "INT", None), ("start_week", "INT", None), ("full_weeks", "INT", None),
                                        ("partial_week", "INT", None), ("dip_weeks", "INT", None), ("discount", "REAL", None),
                                        ("note", "TEXT", None)]),
}
# the drivers of every day: the same table that run_model.py writes to data/output/day_factors.csv
DAY_FACTORS = [("sales_date", "TEXT", "date"), ("year", "INT", None), ("week", "INT", None), ("weekday", "INT", None),
               ("day_name", "TEXT", None), ("open", "INT", None), ("close_hour", "INT", None),
               ("season_factor", "REAL", None), ("calendar_factor", "REAL", None), ("weather_factor", "REAL", None),
               ("price_factor", "REAL", None),
               ("drivers_factor", "REAL", None), ("plan_factor", "REAL", None), ("control_drivers_factor", "REAL", None),
               ("temperature_anomaly", "REAL", None), ("relative_price_test", "REAL", None), ("pre_holiday_1", "INT", None),
               ("pre_holiday_2", "INT", None), ("christmas_ramp", "REAL", None), ("short_day", "INT", None)]


def _cast(v, typ):
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    if typ == "INT":
        return int(float(v))
    if typ == "REAL":
        return float(v)
    return v


def _create(con: sqlite3.Connection, table: str, cols: list, rows: list) -> None:
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f"CREATE TABLE {table} ({', '.join(f'{c} {t}' for c, t, _ in cols)})")
    con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})",
                    [[_cast(r[src or c], t) for c, t, src in cols] for r in rows])


def load_tables(con: sqlite3.Connection, folder: Path, cal) -> None:
    for table, (fname, cols) in TABLES.items():
        _create(con, table, cols, _read(folder / fname))
    # the drivers as the CSV carries them (rounded like the file), so that Snowflake and this run see the same numbers
    df = day_factors(cal)
    _create(con, "day_factors", DAY_FACTORS, [{k: (None if v is None or (isinstance(v, float) and math.isnan(v)) else v)
                                              for k, v in r.items()} for r in df.to_dict("records")])


def rel(a, b):
    if a is None or b is None:
        return math.inf if (a is None) != (b is None) else 0.0
    return abs(a - b) / max(abs(b), 1e-12)


def main() -> int:
    data = load(ROOT / config.DATA_DIR)
    rule = RuleSettings.from_config(config)
    cal = calibrate(data, rule, config.REFERENCE_COUPON_YEAR)

    out_dir = ROOT / config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / "coupon_case.sqlite"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    load_tables(con, ROOT / config.DATA_DIR, cal)
    for step in STEPS:
        con.executescript((SQL_DIR / step).read_text(encoding="utf-8"))
        if step == "02_parameters.sql":  # use the rule settings of this Python run
            con.execute("UPDATE model_params SET window_weeks = ?, sd_multiplier = ?, threshold_constant = ?, "
                        "trim_divisor = ?, half_window = ?, half_window_large = ?, large_n = ?, plan_year = ?",
                        (rule.window_weeks, rule.sd_multiplier, rule.threshold_constant, rule.trim_divisor,
                         rule.half_window, rule.half_window_large, rule.large_n, data.plan_year()))
        con.commit()
    print(f"Ran {len(STEPS)} SQL files on {db.name}.")

    # ---- compare with Python ----
    worst, n = 0.0, 0
    full = {(r["year"], r["week"]) for r in cal.all.weeks if len(r["window"]) == rule.window_weeks}  # weeks with a full window
    sql_weeks = {(y, w): v for y, w, *v in con.execute(
        "SELECT year, week, neutral_baseline, drivers_factor, counterfactual, demand, demand_minus_counterfactual FROM weekly_counterfactual")}
    for r in cal.all.weeks:
        if (r["year"], r["week"]) not in full:
            continue
        b, f, cf, d, e = sql_weeks[(r["year"], r["week"])]
        for x, y in ((b, r["level"]), (f, r["factor"]), (cf, r["cf"]), (d, r["actual"])):
            worst, n = max(worst, rel(x, y)), n + 1
        worst, n = max(worst, abs(e - r["effect"]) / r["actual"]), n + 1  # demand - counterfactual can be near 0: against the demand
    full_c = {(r["year"], r["week"]) for r in cal.all_control.weeks if len(r["window"]) == rule.window_weeks}
    sql_control = {(y, w): (d, cf) for y, w, d, cf in con.execute("SELECT year, week, demand, counterfactual FROM control_counterfactual")}
    for r in cal.all_control.weeks:
        if (r["year"], r["week"]) in full_c:
            d, cf = sql_control[(r["year"], r["week"])]
            worst, n = max(worst, rel(d, r["actual"]), rel(cf, r["cf"])), n + 2
    for year, inc, dip in con.execute("SELECT coupon_year, incremental_units, dip_units FROM coupon_measurement"):
        m = cal.measurements[year]
        worst, n = max(worst, rel(inc, m.extra), rel(dip, m.dip)), n + 2
        print(f"Coupon {year}: SQL +{inc:,.0f} incremental, dip -{dip:,.0f}   Python +{m.extra:,.0f}, -{m.dip:,.0f}")
    for year, c, d in con.execute("SELECT coupon_year, coupon_weeks_deviation, dip_weeks_deviation FROM control_check"):
        chk = cal.control_check["years"][year]
        worst, n = max(worst, rel(c, chk["coupon"]), rel(d, chk["dip"])), n + 2
        print(f"Control check {year}: SQL {c * 100:+.1f} % in the coupon weeks, {d * 100:+.1f} % in the dip   "
              f"Python {chk['coupon'] * 100:+.1f} %, {chk['dip'] * 100:+.1f} %")
    nw, tw = con.execute("SELECT neutral_week, typical_plan_week FROM plan_summary").fetchone()
    worst, n = max(worst, rel(nw, cal.plan.level_week), rel(tw, cal.plan.week)), n + 2
    print(f"Plan: neutral week SQL {nw:,.1f} / Python {cal.plan.level_week:,.1f}; typical plan week SQL {tw:,.1f} / Python {cal.plan.week:,.1f}")
    sql_plan = {w: (s, c, cf) for w, s, c, cf in con.execute("SELECT week, season_factor, calendar_factor, counterfactual FROM plan_weeks_counterfactual")}
    display = 0.0  # season and calendar of a plan week come from the rounded CSV columns: checked loosely
    for w in data.plan_weeks():
        s, c, cf = sql_plan[w]
        worst, n = max(worst, rel(cf, cal.plan.weeks[w])), n + 1
        display = max(display, rel(s, cal.plan.parts[w]["season"]), rel(c, cal.plan.parts[w]["calendar"]))
    sql_days = {(w, d): cf for w, d, cf in con.execute("SELECT week, weekday, counterfactual FROM plan_days_counterfactual")}
    for w in data.plan_weeks():
        for d in range(6):
            py_v = cal.plan.days[w][d]
            worst, n = max(worst, rel(sql_days.get((w, d + 1), 0.0), py_v)), n + 1
    lpw = data.plan_weeks()[-1]
    print(f"Plan week {lpw} (neutral baseline x season x calendar): SQL {sql_plan[lpw][2]:,.1f}   Python {cal.plan.weeks[lpw]:,.1f}")
    py, pw = data.plan_year(), data.plan_weeks()[0]
    for d, lvl in con.execute("SELECT weekday, l_day FROM test_baseline_day WHERE target_year = ? AND target_week = ?", (py, pw)):
        worst, n = max(worst, rel(lvl, cal.plan_window.L.cells[d - 1].level)), n + 1
    statuses = {(d, pos): s for d, pos, s in con.execute(
        "SELECT weekday, src_pos, status FROM test_rule_values WHERE target_year = ? AND target_week = ?", (py, pw))}
    from model.counterfactual import positions
    _, pos = positions(data)
    bad = 0
    for d in range(6):
        for x in cal.sample(d):
            k = (d + 1, pos[(x["year"], x["week"])] + 1)
            if x["status"] in ("used", "outlier", "not used"):
                n += 1
                bad += statuses.get(k) != x["status"]
    print(f"Compared {n} values. Largest relative difference: {worst:.1e} (season and calendar of the plan weeks, "
          f"from the rounded CSV columns: {display:.1e}). Rule verdicts that differ: {bad}.")
    con.close()
    ok = worst < 1e-9 and display < 1e-5 and bad == 0
    print("SQL and Python agree." if ok else "SQL and Python differ - see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
