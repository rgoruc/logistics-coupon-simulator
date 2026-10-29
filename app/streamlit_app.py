"""Logistics Coupon Simulator, interactive version (Streamlit).

Run from the project folder:
    streamlit run app/streamlit_app.py

The numbers come from the Python model in model/ (the same one run_model.py uses), so every slider here
recalculates with the same logic. Plan defaults: data/input/plan_assumptions.csv. Rule defaults: config.py.
"""
from __future__ import annotations

import datetime as dt
import io
import math
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent
ROOT = APP.parent
for p in (str(ROOT), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import planner_charts as charts  # noqa: E402
import config  # noqa: E402
from model import (COUPON_LENGTHS, CONTROL, DAYS, NAME, calibrate, daily, day_drivers, deadline, default_scenario,  # noqa: E402
                   forecast, hourly, intervals, iso_monday, length_share, load, pallets, reference_rate, share_for,
                   shelf_check, normal_demand, span_label, start_weeks)
from model.baseline import RuleSettings  # noqa: E402
from model.mathutil import js_round  # noqa: E402
from model.outputs import all_tables, chart_rows_full_year, window_label  # noqa: E402

st.set_page_config(page_title="Logistics Coupon Simulator", layout="wide")


# ---------------- helpers ----------------
def h100(v: float) -> str:
    """Rounded to the nearest 100, like the HTML page."""
    return f"{js_round(v / 100) * 100:,.0f}"


def s100(v: float) -> str:
    r = js_round(v / 100) * 100
    return ("+" if r > 0 else "−" if r < 0 else "") + f"{abs(r):,.0f}"


def pct(v: float, nd: int = 1) -> str:
    return f"{v * 100:.{nd}f}%"


def arrow_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Columns that mix numbers and text are shown as text (st.dataframe needs one type per column)."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object and len({type(v) for v in out[col].dropna()}) > 1:
            out[col] = out[col].astype(str)
    return out


def show(fig) -> None:
    st.pyplot(fig)
    plt.close(fig)


def weeks_text(win) -> str:
    """'weeks 37 to 42 of 2026' for a block of weeks in one year, else the list."""
    ys = {y for y, _ in win}
    ws = [w for _, w in win]
    if len(ys) == 1 and ws == list(range(ws[0], ws[0] + len(ws))):
        return f"weeks {ws[0]} to {ws[-1]} of {next(iter(ys))}"
    return "weeks " + window_label(win)


def and_list(items: list) -> str:
    """'a, b and c'"""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def short_sku(name: str) -> str:
    """'Deo Test Brand spray 200 ml (top seller)' -> 'spray' (for the assumptions list)."""
    s = re.sub(r" \d+ ml$", "", name.replace(NAME + " ", "").split(" (")[0])
    return s[:1].lower() + s[1:]


FULL_DAY = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday"}


def minus(text: str) -> str:
    return text.replace("-", "−")


def ddmmm(d: dt.date, year: bool = False) -> str:
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if year else "")


@st.cache_resource(show_spinner="Fitting the drivers, the baseline and the counterfactual ...")
def get_model(inputs_stamp: str):
    """The rule is fixed (config.py: the last 6 weeks); the drivers are fitted on the years before the plan year.
    inputs_stamp: the contents of data/input, so the cache refreshes when an input file changes."""
    data = load(ROOT / config.DATA_DIR, config.OPENING_HOURS)
    return calibrate(data, RuleSettings.from_config(config), config.REFERENCE_COUPON_YEAR)


def pct_signed(v: float, nd: int = 1) -> str:
    return minus(f"{v * 100:+.{nd}f}%")


def eff(cal, key: str, x: float = 1.0) -> str:
    """The effect of a driver as a percentage: exp(coefficient x x) - 1."""
    return pct_signed(math.exp(cal.drivers.fit.coef[cal.drivers.names.index(key)] * x) - 1)


def eff_pm(cal, key: str, x: float = 1.0) -> str:
    """The 95% half-width of that effect, in points."""
    return f"±{(math.exp(1.96 * cal.drivers.fit.se[cal.drivers.names.index(key)] * x) - 1) * 100:.1f}"


# ---------------- sidebar ----------------
plan_box = st.sidebar.container()
supply_box = st.sidebar.container()

def inputs_stamp() -> str:
    import hashlib
    h = hashlib.md5()
    for f in sorted((ROOT / config.DATA_DIR).glob("*.csv")):
        h.update(f.name.encode()); h.update(f.read_bytes())
    return h.hexdigest()


cal = get_model(inputs_stamp())
data = cal.data
py, lw = data.plan_year(), data.last_data_week()
D = default_scenario(cal)
A = data.assumptions

with plan_box:
    st.header("Coupon plan")
    disc_options = [10, 20, 30]
    d_default = js_round(D.discount * 100)
    discount = st.select_slider("Discount", options=disc_options, value=d_default if d_default in disc_options else 20,
                                format_func=lambda v: f"{v}% off")
    lengths = list(COUPON_LENGTHS)
    full = st.radio("Coupon length (Monday to Saturday)", lengths, index=lengths.index(D.full_weeks) if D.full_weeks in lengths else 1,
                    horizontal=True, format_func=lambda n: f"{n} weeks")
    start_options = start_weeks(data, full)
    start = st.selectbox("Start week", start_options,
                         index=start_options.index(D.start_week) if D.start_week in start_options else len(start_options) - 1,
                         format_func=lambda w: f"Week {w} · Monday {ddmmm(iso_monday(py, w), iso_monday(py, w).year != py)}")
    mau = st.number_input("Monthly active male app users", min_value=0, value=int(D.mau), step=5000)
    ref = reference_rate(cal, discount / 100, full)
    ref_r = js_round(ref["rate"] * 1000) / 1000
    own_rate = A.get("redemption_rate")
    rate_mode = st.radio("Redemption rate for the whole coupon at full availability (Einlösequote)",
                         ["Reference from past coupons", "My own estimate"], index=1 if own_rate is not None else 0)
    if rate_mode == "My own estimate":
        rate = st.number_input("Redemption rate, %", 0.0, 100.0, float(round((own_rate if own_rate is not None else ref_r) * 100, 1)),
                               0.1, format="%.1f") / 100
    else:
        rate = ref_r
    st.caption(f"Reference at {discount}% off for {full} weeks: {pct(ref_r)}"
               + ("" if full == 4 else f" (the 4-week line × {ref['lengthShare']:.0%}, the share of the first two weeks)")
               + ". Past coupons at full availability: " +
               ", ".join(f"{pct(p['rateFull'])} at {p['discount']:.0%} off" for p in cal.past) +
               (" No past coupon had this discount, so the reference is extrapolated." if ref["extrapolated"] else ""))
    upr = st.number_input("Units per redemption", 0.1, 20.0, float(D.upr), 0.1, format="%.1f")
    inc_ref = js_round(share_for(cal, discount / 100) * 100)
    # incremental share: the line through past coupons at this discount (or the value in plan_assumptions.csv)
    inc = js_round(D.inc_share * 100) if A.get("incremental_share") is not None else inc_ref
    # fixed at the defaults (plan_assumptions.csv and past coupons): dip share, rate uncertainty, fill rate, normal weather
    pull = js_round(D.pull_share * 100)
    unc = js_round(D.rate_unc * 100)
    fill = js_round(D.fill_rate * 100)
    WEATHER = {"Normal": 0.0}
    weather = "Normal"

with supply_box:
    st.header("Supply chain")
    lead = st.number_input("Lead time, days", 0, 365, int(D.lead_time_days), 1)
    depth = st.number_input("Units per facing", 1, 20, int(D.units_per_facing), 1)
    deliv = [DAYS[d] for d in D.delivery_days]
    st.caption(("Store delivery (assumption): one delivery a week, on " + {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday",
                "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday"}[deliv[0]] + "." if len(deliv) == 1 else
                "Store deliveries (assumption): " + ", ".join(deliv) + "."))

sc = D.with_(discount=discount / 100, start_week=start, full_weeks=full, mau=float(mau),
             rate=rate, upr=upr, inc_share=inc / 100, pull_share=pull / 100, rate_unc=unc / 100, fill_rate=fill / 100,
             lead_time_days=int(lead), units_per_facing=int(depth), delivery_days=sorted(DAYS.index(d) for d in deliv),
             plan_anomaly=WEATHER[weather])
res = forecast(cal, sc)
s, C = res.summary, res.summary["commit"]
ready = mau > 0 and rate > 0

# ---------------- header and KPIs ----------------
st.title("Logistics Coupon Simulator")
st.markdown("For the marketing team: what a coupon means for logistics before you launch it.")
# ---------------- assumptions (values you can change in the sidebar show the current setting) ----------------
first_wk, last_wk = data.order[0], data.order[-1]
ctrl_promo = min(data.price(y, "control", w) for (y, w) in data.order)
cat_low = min(data.price(y, "category", w) for (y, w) in data.order)
cat_weeks = sum(1 for (y, w) in data.order if data.price(y, "category", w) < D.market_avg_price - 1e-9)
upr_d, pull_d, fill_d = D.upr, js_round(D.pull_share * 100), js_round(D.fill_rate * 100)
ry = cal.ref.coupon.year
ASSUMPTIONS = [
    ("Context", [
        "A drugstore publishes app coupons for a men's deo brand (test brand). Customers activate them in the app and redeem them "
        "at the store.",
        "Region: Baden-Württemberg, 1 distribution centre and 450 stores, open 08:00 to 20:00, Monday to Saturday. "
        "Closed on Sundays and public holidays.",
        f"Data: fictional hourly store sales from week {first_wk[1]} of {first_wk[0]} to week {last_wk[1]} of {last_wk[0]} "
        "mimicking the seasonality shown in Google Trends Search for keyword \"deo\" in Germany (Data source: Google Trends, google.com/trends).",
    ]),
    ("Prices", [
        f"{NAME}: €{D.price:.2f} shelf price. With the coupon: €{D.price * 0.9:.2f}, €{D.price * 0.8:.2f} or €{D.price * 0.7:.2f} "
        "(10, 20 or 30% off).",
        f"{CONTROL}: €{D.control_price:.2f}, or €{ctrl_promo:.2f} in its promotion weeks ({1 - ctrl_promo / D.control_price:.0%} off).",
        f"Category price index: the volume-weighted average shelf price of men's deo in the Drugstore, weekly. €{D.market_avg_price:.2f} at "
        f"normal prices; down to €{cat_low:.2f} in the {cat_weeks} weeks when the control brand or other brands were on promotion. "
        "The plan weeks assume normal prices.",
    ]),
    ("Coupon", [
        f"Runs 2 or 4 weeks, Monday to Saturday. It can start in any week of {py} and has to end by week 52.",
        "Past coupons in the data: " + and_list([f"{c.year} weeks {c.weeks[0]} to {c.weeks[-1]} ({c.discount:.0%} off)" for c in data.own])
        + ". Both are measured against normal demand (Last coupon tab).",
        f"Monthly active male app users: {mau:,}.",
        "Redemption rate (Einlösequote) at full availability: user's estimate or the reference line through the past coupons.",
        f"A 2-week coupon gets {length_share(cal, 2):.0%} of a 4-week coupon's redemptions: the share of the first two weeks in {ry}.",
        f"Units per redemption: {upr:.1f} " + ("(average of the past coupons)." if abs(upr - upr_d) < 1e-9
                                                else f"(your setting; past coupons: {upr_d:.1f})."),
        (f"Incremental share: {inc}% at {discount}% off (line through the past coupons by discount)." if inc == inc_ref else
         f"Incremental share: {inc}% (your setting; line through the past coupons: {inc_ref}% at {discount}% off)."),
        f"Dip after the coupon: {pull}% of the incremental units " + ("(average of the past coupons)" if pull == pull_d else
                                                                      f"(your setting; past coupons: {pull_d}%)")
        + f", spread over the 3 weeks after it as in {ry}.",
        f"Weekly split: the shape of the {ry} coupon, launch week strongest. Hourly split: the normal hourly pattern.",
    ]),
    ("Normal demand (without the coupon)", [
        f"Baseline rule, fixed: same weekday and hour in the last {cal.rule.window_weeks} weeks. A value is an outlier above the "
        f"middle value + {cal.rule.sd_multiplier:g} × standard deviation + {cal.rule.threshold_constant:g}. The baseline is the "
        f"average of the {2 * cal.rule.half_window + 1} values around the median.",
        "Normal demand of a day = baseline × season × calendar (shopping days before a holiday, the run-up to Christmas) × weather × "
        f"price (against the category price index). The effects are measured on {and_list([str(y) for y in cal.train_years])} and tested on "
        f"{py} (Drivers section in the Forecast tab).",
        f"Weeks 1 to {lw}: the drivers as they were (the counterfactual). Weeks {lw + 1} to 52: baseline of {weeks_text(cal.plan.window)} "
        f"× the season × {py}'s holiday calendar × normal weather; prices at their usual level.",
        "Demand = sales ÷ fill rate.",
    ]),
    ("Supply chain", [
        f"Lead time: {lead} days between the volume commit and the coupon start.",
        f"Target fill rate: {fill}% " + ("(average of normal weeks)." if fill == fill_d else f"(your setting; normal weeks: {fill_d}%)."),
        f"Redemption-rate uncertainty: ±{unc}%, used for the buffer in the volume to commit.",
        f"{sc.pallet_units:,} units per pallet.",
        "Store delivery: " + (f"once a week, on {FULL_DAY[deliv[0]]}." if len(deliv) == 1 else "on " + and_list(deliv) + "."),
        "Shelf, one high-volume store: " + ", ".join(f"{short_sku(x.sku)} {x.facings}" + (" facings" if i == 0 else "")
                                                     for i, x in enumerate(data.shelf))
        + f"; {depth} units per facing; a normal week sells " + " / ".join(f"{x.normal_week:g}" for x in data.shelf) + " units.",
    ]),
]
end = max(s["weeks"])
end_date = iso_monday(py, end) + dt.timedelta(days=5)
st.markdown(f"**{NAME} · {discount}% off · week {start} to {end}** · {ddmmm(s['startDate'])} to {ddmmm(end_date, True)}")

due = deadline(s["startDate"], int(lead))
cw = [r for r in res.rows if r["coupon"]]
pal_base, pal_with = sum(pallets(r["baseline"], sc.pallet_units) for r in cw), sum(pallets(r["demand"], sc.pallet_units) for r in cw)
k1, k2, k3, k4 = st.columns(4)
k1.metric(f"Without the coupon, weeks {min(s['weeks'])} to {max(s['weeks'])}", h100(s["couponBaseline"]))
k1.caption(f"units of normal demand · {pal_base} pallets")
# shown rounded to 100: "with" = rounded "without" + rounded extra, so the three tiles add up
with_disp = js_round(s["couponBaseline"] / 100) * 100 + js_round(s["incremental"] / 100) * 100
k2.metric("With the coupon", h100(with_disp))
k2.caption("units of demand with the coupon")
k3.metric("Extra units from the coupon", s100(s["incremental"]))
k3.caption(f"{js_round(s['incremental'] / s['couponUnits'] * 100)}% of the {h100(s['couponUnits'])} units bought with the coupon · "
           f"{s100(s['net'])} net after the dip")
k4.metric(f"Commit by {ddmmm(due, True)}", h100(C["q"]))
k4.caption(f"units for a {fill}% fill rate · " + ("no buffer needed" if abs(C["buffer"]) < 50 else f"{s100(C['buffer'])} buffer on the forecast"))
if ready:
    # what the coupon means for logistics, in three lines: sales, logistics, shelf
    shelf = shelf_check(cal, sc, res)
    short = [x for x in shelf["items"] if x["needed"] > x["facings"]]
    sug = shelf["suggestion"]
    peak_row = max((r for r in res.rows if r["coupon"]), key=lambda r: pallets(r["demand"], sc.pallet_units))
    peak_pal = pallets(peak_row["demand"], sc.pallet_units)
    shelf_text = ("the current facings hold the peak in a high-volume store." if not short else
                  f"in a high-volume store {'one product needs' if len(short) == 1 else f'{len(short)} products need'} more facings "
                  "in the coupon weeks" + ((f", or an extra {sug['day']} delivery, "
                                            + ("which is enough." if sug["fitsAll"] else "which is not enough on its own.")) if sug else "."))
    st.markdown(
        f"**Sales:** {h100(with_disp)} units in the coupon weeks instead of {h100(s['couponBaseline'])}: **{h100(s['incremental'])} extra**; "
        f"of the {h100(s['couponUnits'])} units sold with the coupon the rest would have been bought anyway. After the dip in the three weeks "
        f"that follow: **{h100(s['net'])} extra units net**."
        + (f" About {h100(s['dipOutside'])} units of the dip fall after week 52." if s["dipOutside"] > 0 else "")
        + f"\n\n**Logistics:** supply chain {'would have had' if start <= lw else 'has'} to commit **{h100(C['q'])} units** for the "
        f"coupon weeks by **{ddmmm(due, True)}**, {lead} days before the start, for a {fill}% fill rate. The distribution centre peaks at "
        f"**{peak_pal} pallets a week** instead of {pallets(peak_row['baseline'], sc.pallet_units)} in week {peak_row['week']}."
        + "\n\n**Shelf:** " + shelf_text)
else:
    st.info("Enter the number of app users and a redemption rate above zero to run the plan.")

tab_f, tab_s, tab_l, tab_b, tab_t, tab_m = st.tabs(["Forecast", "Logistics", "Last coupon", "Baseline rule", "Tables & downloads", "Method"])

# ---------------- Forecast ----------------
with tab_f:
    st.subheader(f"{NAME} {py}, calendar weeks 1 to 52")
    rows = chart_rows_full_year(cal, sc, res, ready)
    show(charts.full_year_chart(rows, py, lw))
    # actual against the model in the weeks with data of the plan year
    w_out = [r for r in cal.all.weeks if r["year"] == py and r["week"] <= lw and not r["own"]]
    dev_w = lambda r: r["actual"] / r["cf"] - 1  # noqa: E731
    big = max(w_out, key=lambda r: abs(dev_w(r)))
    m_py = cal.measurements.get(py)
    actual_note = (f"Actual demand = units sold ÷ that week's fill rate, up to the end of the data, Saturday "
                   f"{ddmmm(iso_monday(py, lw) + dt.timedelta(days=5))}. " + (f"Outside the {py} coupon it was " if m_py else "It was ")
                   + f"{pct(sum(abs(dev_w(r)) for r in w_out) / len(w_out))} away from the prediction on average (largest in week "
                   f"{big['week']}: {minus(f'{dev_w(big):+.1%}')})"
                   + (f". In the grey weeks {m_py.weeks[0]} to {m_py.weeks[-1]} {NAME} ran its own {py} coupon: actual demand was "
                      f"{s100(m_py.extra)} units above the prediction (what the coupon added), and {s100(-m_py.dip)} in the dip weeks "
                      f"{m_py.coupon.post[0]} to {m_py.coupon.post[-1]} (buyers who had stocked up)" if m_py else "")
                   + ("" if m_py else f"; {py} had no coupon so far, so this gap is the model's error") + ". Past coupons are measured in the Last coupon tab.")
    st.caption(
        f"Normal demand without a coupon for every week of {py}: weeks 1 to {lw} from the data (your rolling baseline of the "
        f"{cal.rule.window_weeks} weeks before each week × the drivers of each day as they were: season, calendar, weather and "
        f"price), from week {lw + 1} the plan (the baseline frozen at {weeks_text(cal.plan.window)}, "
        f"{h100(cal.plan.level_week)} a week on a neutral day, × the season × {py}'s holiday calendar × {weather.lower()} weather: "
        f"{h100(normal_demand(cal, lw + 1, sc.plan_anomaly))} in week {lw + 1}, {h100(normal_demand(cal, 52, sc.plan_anomaly))} in "
        "week 52). Pink and the red dashed line: the predicted demand with the coupon you plan; hatched: the dip after it. " + actual_note)
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("From coupon units to incremental units")
        if ready:
            show(charts.bridge_chart(s))
            st.caption("Rounded to the nearest 100. Grey bars are totals, red bars take units away.")
        else:
            st.info("Enter the redemption rate to see the bridge.")
    view_from = min(start, lw + 1)  # a 10-week window from the coupon start (weeks 43 to 52 for a start in week 43 or later)
    view = [r for r in res.rows if view_from <= r["week"] <= view_from + 9]
    with c2:
        st.subheader("Plan by week")
        st.dataframe(pd.DataFrame([{
            "Week": f"{r['week']} · {ddmmm(r['monday'])}", "Redemptions": round(r["redemptions"]) if r["coupon"] else None,
            "Predicted without the coupon": round(r["baseline"]), "Incremental": round(r["incremental"]) if r["incremental"] else None,
            "Dip": -round(r["dip"]) if r["dip"] else None, "Demand": round(r["demand"]), f"Sales at {fill}%": round(r["sales"]),
            "Actual demand": round(cal.all.week(py, r["week"])["actual"]) if r["week"] <= lw else None,
            "Actual − predicted without": round(cal.all.week(py, r["week"])["actual"] - r["baseline"]) if r["week"] <= lw else None}
            for r in view]), hide_index=True)
    # ---- the drivers of normal demand ----
    st.subheader(f"Drivers of normal demand · {NAME}, measured on {and_list([str(y) for y in cal.train_years])}, tested on {py}")
    st.markdown(
        "Normal demand of a day = the baseline for that weekday and hour × season × calendar × weather × price. "
        f"The effects are measured together, in one regression on the log of daily demand ({cal.drivers.fit.n:,} days of "
        f"{and_list([str(y) for y in cal.train_years])}, outside {NAME}'s own coupons), so a hot week in July is not mistaken for the "
        "season. The weekday and the level are not drivers: the baseline rule carries them, 6 weeks at a time.")
    sc_lo, sc_hi = min(cal.season_curve), max(cal.season_curve)
    w_lo, w_hi = cal.season_curve.index(sc_lo) + 1, cal.season_curve.index(sc_hi) + 1
    ln5 = -math.log(0.95)
    a_sd = cal.anomaly_sd
    st.dataframe(pd.DataFrame([
        {"Driver": "Season", "What it measures": "a smooth yearly curve (5 sine/cosine pairs), the same every year",
         "Effect on normal demand": f"{pct_signed(sc_lo - 1)} to {pct_signed(sc_hi - 1)}", "95%": f"low in week {w_lo}, peak in week {w_hi}",
         "In the plan weeks": "this year's curve, week by week"},
        {"Driver": "Day before a holiday", "What it measures": "the first shopping day before a public holiday",
         "Effect on normal demand": eff(cal, "pre1"), "95%": eff_pm(cal, "pre1"),
         "In the plan weeks": "24 December (before 25 and 26 December: two holidays, so twice)"},
        {"Driver": "Two days before a holiday", "What it measures": "the second shopping day before a public holiday",
         "Effect on normal demand": eff(cal, "pre2"), "95%": eff_pm(cal, "pre2"), "In the plan weeks": "23 December"},
        {"Driver": "Run-up to Christmas", "What it measures": "1 to 24 December, rising to the full effect on 23 December",
         "Effect on normal demand": eff(cal, "xmas"), "95%": eff_pm(cal, "xmas"), "In the plan weeks": "as measured"},
        {"Driver": "Weather", "What it measures": "per 1 °C the daily maximum is above the seasonal normal",
         "Effect on normal demand": eff(cal, "anomaly"), "95%": eff_pm(cal, "anomaly"),
         "In the plan weeks": f"normal, cold or warm (±{a_sd:.1f} °C: {eff(cal, 'anomaly', -a_sd)} / {eff(cal, 'anomaly', a_sd)})"},
        {"Driver": "Price", "What it measures": f"the category price index 5% lower (other brands on promotion), {NAME}'s price unchanged",
         "Effect on normal demand": eff(cal, "z", ln5), "95%": eff_pm(cal, "z", ln5), "In the plan weeks": "normal prices (1.00)"},
    ]), hide_index=True)

# ---------------- Last coupon ----------------
with tab_l:
    m = cal.ref
    c = m.coupon
    st.subheader(f"{c.year} coupon: what it really added")
    a, b, cc, d = st.columns(4)
    a.metric("Incremental units", s100(m.extra))
    a.caption(f"weeks {min(m.weeks)}–{max(m.weeks)} (95%: {h100(m.extra - m.extra_half)} to {h100(m.extra + m.extra_half)})")
    b.metric("Against the counterfactual", f"{m.extra / m.cf_coupon:+.1%}")
    cc.metric("Dip", s100(-m.dip))
    cc.caption(f"weeks {min(c.post)}–{max(c.post)}")
    d.metric("Net", s100(m.net))
    d.caption(f"95%: {h100(m.net - m.net_half)} to {h100(m.net + m.net_half)}")
    w_ref = [w for w in cal.all.weeks if w["year"] == c.year]
    show(charts.measurement_chart(w_ref, cal.sd_w, m.weeks, c.year))
    st.caption("Weekly sums by calendar week. Counterfactual = the neutral baseline × the drivers of each day (season, holidays, "
               f"weather, prices). Outside the coupon the baseline rolls (each week from the "
               f"{cal.rule.window_weeks} weeks before it), so these weeks are an out-of-sample test: the counterfactual misses actual "
               f"weekly demand by {pct(cal.sd_w)} on average over all years. During the coupon and its dip the baseline is frozen at "
               f"{weeks_text(cal.lv_ref.win)}, so the coupon cannot leak into it. Demand = sales ÷ fill rate.")
    st.subheader("Incremental units, adding up")
    show(charts.cumulative_chart(m.days, m.weeks))
    st.caption("The line climbs during the coupon and falls back in the weeks after it, when buyers who stocked up stay away. "
               "The band widens because daily errors and the error of the baseline level add up.")
    st.subheader("The control brand as a cross-check")
    chk = cal.control_check
    yrs = sorted(chk["years"])
    y_last, y_ref = yrs[-1], c.year
    a, b, cc = st.columns(3)
    a.metric(f"Control vs its own normal, {y_ref} coupon weeks", pct_signed(chk["years"][y_ref]["coupon"]))
    b.metric(f"The same, {y_last} coupon weeks", pct_signed(chk["years"][y_last]["coupon"]))
    cc.metric("The control's usual weekly error", pct(chk["sd_w_control"]))
    st.caption(f"{CONTROL} gets the same model: its own baseline and its own drivers (its price against the category price index). "
               "If the coupon pulled the control's buyers, the control would fall below its own normal demand in the coupon weeks; it "
               f"was {pct_signed(chk['years'][y_ref]['coupon'])} in {y_ref} and {pct_signed(chk['years'][y_last]['coupon'])} in {y_last}, "
               f"within its weekly error, so no sign of that. Competitors' promotions, the control's included, enter {NAME} through the "
               f"category price index: when the index is 5% lower, {NAME} sells {eff(cal, 'z', -math.log(0.95))}.")
    st.subheader("By hour, full coupon days")
    H = cal.hours_ref
    show(charts.hours_chart(data.hours, H["base"], H["actual"]))
    st.caption(f"Average of {H['days']} full coupon days. The gap between the lines is the coupon's incremental demand by hour.")
    st.subheader("Past coupons")
    st.dataframe(pd.DataFrame([{
        "Coupon": f"{p['year']} · W{p['weeks']}", "Discount": f"{p['discount']:.0%}", "App users": f"{p['mau']:,.0f}",
        "Observed rate": pct(p["rateObs"]), "Fill rate": pct(p["fillRate"]), "Rate at full availability": pct(p["rateFull"]),
        "Units per redemption": p["upr"], "Incremental share": pct(p["extra"]), "Dip": pct(p["dip"]),
        "Source": "measured" if p["measured"] else "report"} for p in cal.past]), hide_index=True)
    st.caption("All past coupons are measured with the counterfactual. The references for the "
               "redemption rate and the incremental share are straight lines through these coupons by discount.")

# ---------------- Baseline rule ----------------
with tab_b:
    st.subheader(f"Baseline: your rule, last {cal.rule.window_weeks} weeks")
    st.markdown(
        "For each weekday and opening hour the rule takes the same weekday and hour in the last weeks as neutral demand: the hour's "
        "demand ÷ the drivers of its day, so a hot day or a day before a holiday does not count more than it should. Ranked high to "
        "low, a value is an **outlier** when it is above the middle "
        f"value + {cal.rule.sd_multiplier:g} × the standard deviation of the middle ranks + {cal.rule.threshold_constant:g}. The baseline "
        f"is the average of the values within ±{cal.rule.half_window} {'rank' if cal.rule.half_window == 1 else 'ranks'} of the median "
        f"of the values that are left. Closed days are left out. Shown: the frozen baseline of the plan, {weeks_text(cal.plan.window)}.")
    c1, c2 = st.columns(2)
    day = c1.selectbox("Weekday of the example", DAYS[:6], index=4)
    hour = c2.selectbox("Hour of the example", data.hours, index=data.hours.index(17) if 17 in data.hours else 0,
                        format_func=lambda h: f"{h:02d}:00–{h + 1:02d}:00")
    d0, i0 = DAYS.index(day), data.hours.index(hour)
    show(charts.baseline_heatmap([[x.level for x in row] for row in cal.plan.cells], data.hours, (d0, i0)))
    smp = cal.sample(d0, i0)
    cell = cal.plan.cells[d0][i0]
    ranked = sorted([x for x in smp if x["adj"] is not None], key=lambda x: -x["adj"])
    rank = {x["week"]: k + 1 for k, x in enumerate(ranked)}
    st.markdown(f"**Example: {day} {hour:02d}:00–{hour + 1:02d}:00**")
    st.dataframe(pd.DataFrame([{
        "Week": x["week"], "Date": x["date"], "Actual demand": None if x["raw"] is None else round(x["raw"]),
        "Drivers": None if x["factor"] is None else round(x["factor"], 3),
        "Neutral": None if x["adj"] is None else round(x["adj"]), "Rank": rank.get(x["week"]), "Rule": x["status"]} for x in smp]),
        hide_index=True)
    st.caption("Neutral = actual ÷ the drivers of that day (season × calendar × weather × price). "
               f"Threshold {cell.threshold:,.0f}: {cell.n_out} {'value is' if cell.n_out == 1 else 'values are'} above it. "
               f"Baseline {cell.level:,.0f} = average of the values marked used. The weekly baseline is the sum of the 72 cells: "
               f"{h100(cal.plan.level_week)}.")
    n_out = sum(x.n_out for row in cal.plan.cells for x in row)
    n_all = sum(x.n for row in cal.plan.cells for x in row)
    st.caption(f"The rule set aside {n_out} of {n_all} values as outliers.")

# ---------------- Supply chain ----------------
with tab_s:
    st.subheader("When does logistics need to know?")
    left = (due - dt.date.today()).days
    when = f"{lead} days before the coupon starts on {ddmmm(s['startDate'], True)}"
    if left < 0:
        st.error(f"Supply chain needed the coupon by {ddmmm(due, True)}, {when}. That date passed {-left} days ago.")
    else:
        st.success(f"Tell supply chain by {ddmmm(due, True)}, {when}." + (" That is today." if left == 0 else f" That is in {left} days."))
    st.caption(f"Supply chain fixes the volume {lead} days ahead, before any redemption is seen, so the coupon has to be in the "
               "replenishment plan by this date. Last year it was not, and the fill rate fell to "
               f"{data.fill_rate(cal.ref.coupon.year, 'test', cal.ref.coupon.start):.0%} in the launch week. The volume to commit "
               "includes a buffer for the forecast error.")
    st.subheader(f"Volume to commit, weeks {min(s['weeks'])} to {end}")
    a, b, cc = st.columns(3)
    a.metric("Forecast demand", h100(C["mu"]))
    b.metric(f"To commit for a {fill}% fill rate", h100(C["q"]))
    cc.metric("Buffer", s100(C["buffer"]))
    st.caption(f"Why more than the forecast: the volume is fixed {lead} days ahead, and demand could be off by ±{h100(C['sigma'])} units "
               f"(one standard deviation): ±{h100(C['parts']['base'])} from normal demand "
               f"(last year the same plan missed the counterfactual in the coupon weeks by {cal.base_err_ref:.0%}), "
               f"±{h100(C['parts']['rate'])} from the redemption rate (±{unc}%), ±{h100(C['parts']['share'])} from the incremental share. "
               f"Committing only the forecast gives an expected fill rate of {pct(C['fillIfMu'])}."
               + (" The target is below that, so less than the forecast is enough." if C["buffer"] < 0 else ""))
    st.subheader("Fill rate in the coupon weeks: what empty shelves cost")
    a, b, cc = st.columns(3)
    a.metric("Demand", h100(s["couponDemand"]))
    b.metric(f"Sales at {fill}%", h100(s["couponSales"]))
    cc.metric("Lost on empty shelves", h100(s["couponLost"]))
    st.caption(f"Last year the coupon was not in the replenishment plan. The fill rate averaged {pct(cal.ref.frc)} over the coupon: "
               f"{h100(cal.ref.lost)} units of demand found an empty shelf. Measured on sales the {cal.ref.coupon.year} coupon added "
               f"{h100(cal.extra_on_sales_ref)} units; with full shelves it would have been {h100(cal.ref.extra)} (demand = sales ÷ fill rate).")
    st.subheader("Distribution centre: pallets per week")
    st.dataframe(pd.DataFrame([{"Week": f"{r['week']} · {ddmmm(r['monday'])}", "Without coupon": pallets(r["baseline"], sc.pallet_units),
                                "With coupon": pallets(r["demand"], sc.pallet_units)} for r in view]), hide_index=True)
    c0 = cal.ref.coupon
    dc = lambda w: js_round(data.row(c0.year, w)["test_dc_orders"] / sc.pallet_units)  # noqa: E731
    st.caption((f"Peak: {peak_pal} pallets in week {peak_row['week']} against {pallets(peak_row['baseline'], sc.pallet_units)} without "
                f"the coupon ({sc.pallet_units:,} units per pallet). " if ready else "")
               + f"Last year's coupon was not in the replenishment forecast: distribution centre orders jumped from {dc(c0.start - 1)} to "
               f"{dc(c0.start)} pallets when it started and fell to {dc(min(c0.post))} after it ended.")
    st.subheader("Daily and hourly plan")
    wk = st.selectbox("Week", [r["week"] for r in view], index=[r["week"] for r in view].index(start),
                      format_func=lambda w: f"Week {w}" + (" · coupon" if w in s["weeks"] else ""))
    days = daily(cal, sc, res, wk)

    def actual_day(week: int, d: int):
        """Actual demand of a day of the plan year (sales ÷ fill rate); None on closed days and after the data."""
        p = next((q for q in cal.all.days if q["year"] == py and q["week"] == week and q["d"] == d), None)
        return None if p is None or p["closed"] else p["P"]

    st.dataframe(pd.DataFrame([{"Day": f"{x['day']} {x['date']}", "Baseline": None if x["closed"] else round(x["baseline"]),
                                "Incremental": None if x["closed"] or not x["incremental"] else round(x["incremental"]),
                                "Dip": None if x["closed"] or not x["dip"] else -round(x["dip"]),
                                "Demand": None if x["closed"] else round(x["demand"]),
                                f"Sales at {fill}%": None if x["closed"] else round(x["demand"] * sc.fill_rate),
                                **({"Actual demand": None if actual_day(wk, i) is None else round(actual_day(wk, i)),
                                    "Actual − predicted": None if actual_day(wk, i) is None else round(actual_day(wk, i) - x["baseline"])}
                                   if wk <= lw else {}),
                                "Delivery": "yes" if DAYS.index(x["day"]) in sc.delivery_days else ""} for i, x in enumerate(days)]),
                 hide_index=True)
    st.caption(f"Week {wk} is in the data: each weekday's share comes from that week's normal demand, so closed days and public "
               "holidays are in it (what-if). Actual demand is what the day sold ÷ the fill rate, against the prediction without the coupon." if wk <= lw else
               f"Each weekday's share of the week comes from the plan's normal demand of that week: the neutral baseline of "
               f"{weeks_text(cal.plan.window)} × the drivers of each day, so {py}'s closed days and public holidays are in it.")
    # the drivers of each day of the week
    drv_rows = [(d, day_drivers(cal, wk, d, sc.plan_anomaly)) for d in range(6)]
    fx = lambda v: "1.00" if abs(v - 1) < 0.0005 else f"{v:.2f}"  # noqa: E731

    def day_label(d: int) -> str:
        f = cal.feats[dt.date.fromisoformat(days[d]["date"])]
        short = f.open and f.close_hour <= data.hours[-1]  # a short day (24 December closes at 14:00)
        return f"{DAYS[d]} {days[d]['date']}" + (f" · until {f.close_hour:02d}:00" if short else "")

    st.markdown(f"**The drivers of each day, week {wk}**" + (" (as they were)" if wk <= lw else f" (plan: {weather.lower()} weather)"))
    st.dataframe(pd.DataFrame([
        {"Day": day_label(d), "Season": "Closed", "Calendar": "", "Weather": "", "Price": "", "All together": ""}
        if p is None else
        {"Day": day_label(d), "Season": fx(p["season"]), "Calendar": fx(p["calendar"]), "Weather": fx(p["weather"]),
         "Price": fx(p["price"]), "All together": f"{p['total']:.2f}"}
        for d, p in drv_rows]), hide_index=True)
    cal_days = [(d, p) for d, p in drv_rows if p is not None and abs(p["calendar"] - 1) > 0.0005]
    st.caption("Normal demand of a day = the neutral baseline for that weekday × the factors in its row. "
               + (f"Weather is the day's maximum temperature against its seasonal normal; price is {NAME}'s price against the category "
                  "price index that week (below 1.00 when other brands were on promotion). " if wk <= lw else
                  "Prices are not known in advance, so the plan keeps them at their usual level (1.00); "
                  "weather at its seasonal normal. ")
               + (("Calendar above 1.00: " + ", ".join(f"{DAYS[d]} {p['calendar']:.2f}" for d, p in cal_days)
                   + " (a shopping day before a public holiday, or the run-up to Christmas).") if cal_days else "No public holiday nearby this week."))
    hd = st.radio("Day of the hourly plan", DAYS[:6], index=5, horizontal=True)
    hrs = hourly(cal, sc, res, wk, DAYS.index(hd))
    st.dataframe(pd.DataFrame([{"Hour": f"{r['hour']:02d}–{r['hour'] + 1:02d}", "Baseline": round(r["baseline"]),
                                "Incremental − dip": round(r["incremental"] - r["dip"]), "Demand": round(r["demand"]),
                                "Share of the day": pct(r["share"])} for r in hrs["rows"]]), hide_index=True)
    st.subheader("Shelf check, high-volume store")
    if ready:
        sh = shelf_check(cal, sc, res)
        short = [x for x in sh["items"] if x["needed"] > x["facings"]]
        peak = sh["peak"]
        txt = (f"The tightest stretch is {peak['label']} in week {peak['week']}: {peak['factor']:.0%} of a normal week's sales "
               "between two deliveries.")
        if short:
            sug = sh["suggestion"]
            st.warning(txt + f" The shelf runs empty for {'one product' if len(short) == 1 else f'{len(short)} products'}. "
                       "Add facings for the coupon weeks" + (f", or add a {sug['day']} delivery" + (", which is enough." if sug["fitsAll"]
                                                                                                   else ", which is not enough on its own.") if sug else "."))
        else:
            st.success(txt + " The current shelf holds it.")
        st.dataframe(pd.DataFrame([{"Product": x["sku"], f"Units {peak['label']}, week {peak['week']}": round(x["peakDemand"], 1),
                                    "Same stretch, without the coupon": round(x["normalDemand"], 1), "Shelf capacity": x["capacity"],
                                    "Facings": x["facings"], "Facings needed": x["needed"]} for x in sh["items"]]), hide_index=True)
        st.caption("Each delivery has to last until the next one: " + "; ".join(
            f"{DAYS[sp[0]]} delivery covers {span_label(sp)}" for sp in intervals(sc.delivery_days)) + ". Stores are closed on Sunday.")
    else:
        st.info("Enter the redemption rate to check the shelf.")

# ---------------- Tables & downloads ----------------
with tab_t:
    tables = all_tables(cal, sc, res)
    st.markdown("Every table of the model for the current settings. The same tables are written to data/output by "
                "`python run_model.py` (with the defaults of plan_assumptions.csv).")
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            for name, df in tables.items():
                df.to_excel(xw, sheet_name=name[:31], index=False)
        st.download_button("Download all tables (Excel)", buf.getvalue(), file_name="test_coupon_tables.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except ImportError:
        st.caption("Install openpyxl for the Excel download (pip install openpyxl).")
    pick = st.selectbox("Table", list(tables.keys()))
    st.dataframe(arrow_safe(tables[pick]), hide_index=True)
    st.download_button(f"Download {pick}.csv", tables[pick].to_csv(index=False).encode("utf-8"), file_name=f"{pick}.csv", mime="text/csv")

# ---------------- Method ----------------
with tab_m:
    st.subheader("How the numbers are made")
    yrs = and_list([str(y) for y in cal.train_years])
    chk = cal.control_check
    c_yrs = sorted(chk["years"])
    st.markdown("\n".join(f"- {t}" for t in [
        "**Goal:** show the marketing team, before a coupon is launched, what it means for logistics: the extra units, the volume "
        "and the deadline for supply chain, the pallets in the distribution centre and the shelf in the stores. Extra (incremental) "
        "units are the units the coupon adds, which would not have been sold without it. Customers activate the coupon in the "
        "Drugstore app and redeem it at the till; the data are store sales.",
        f"**Baseline (your rule, per hour):** for each weekday and opening hour, the same weekday and hour in the last "
        f"{cal.rule.window_weeks} weeks, as neutral demand (demand ÷ the drivers of the day). Values above the middle value + "
        f"{cal.rule.sd_multiplier:g} × the standard deviation of the middle ranks + {cal.rule.threshold_constant:g} are outliers; the "
        f"baseline is the average of the values within ±{cal.rule.half_window} {'rank' if cal.rule.half_window == 1 else 'ranks'} of "
        "the median of the rest. Closed days and closed hours are left out.",
        f"**Rolling, then frozen:** the window moves with every week (the {cal.rule.window_weeks} weeks before that week) and skips "
        f"weeks of {NAME}'s own coupons and the dip after them. During a coupon and its dip it is frozen at the weeks before the "
        f"coupon, so the coupon cannot leak into its own baseline. With data up to week {lw}, the {py} plan uses "
        f"{weeks_text(cal.plan.window)}.",
        "**Drivers:** normal demand of a day = baseline × season × calendar × weather × price. Season: a "
        "smooth yearly curve (5 sine/cosine pairs). Calendar: the first and second shopping day before a public holiday and the run-up "
        "to Christmas (1 to 24 December). Weather: the daily maximum temperature against its seasonal normal (Karlsruhe). Price: "
        f"{NAME}'s shelf price ÷ the category price index (the volume-weighted average shelf price of men's deo, weekly), against "
        "its usual level. "
        f"The effects are measured together in one regression on the log of daily demand, on {yrs}, outside {NAME}'s own coupons "
        "(Drivers section in the Forecast tab). The weekday and the level are not in the regression: the baseline carries them.",
        "**Counterfactual:** for a day = the sum over its open hours of baseline[weekday, hour] × the drivers of the day. "
        f"Incremental = actual − counterfactual. The 95% intervals use the day-to-day error of the regression ({pct(cal.sd_in)}), "
        f"independent from day to day, and the error of the frozen baseline level ({pct(cal.sd_l)}), one number for the whole coupon "
        "and its dip, because the same 6 weeks feed every cell of the frozen baseline; both come from days outside the coupons.",
        f"**Test:** the drivers never saw {py}. On its {cal.holdout['weeks']} weeks with data, the "
        f"counterfactual missed actual weekly demand by {pct(cal.holdout['weekly_rms'])} (daily {pct(cal.holdout['daily_rms'])}), "
        f"bias {pct_signed(cal.holdout['weekly_bias'])}. Over all years, rolling weeks outside the coupons miss by {pct(cal.sd_w)}.",
        f"**Control brand:** {CONTROL} gets the same model (its own drivers, its price against the category index): the cross-check "
        "of the control assumption. If the coupon pulled the control's buyers, the control would fall below its own normal in the "
        "coupon weeks (" + ", ".join(f"{y}: {pct_signed(chk['years'][y]['coupon'])}" for y in c_yrs) + ").",
        "**Plan:** redemptions = app users × your redemption rate at full availability (Einlösequote); units with the coupon = "
        "redemptions × units per redemption; incremental = that × incremental share; the dip = incremental × pulled-forward share, "
        f"spread over the three weeks after the coupon like in {cal.ref.coupon.year}. Weekly split: the shape of the "
        f"{cal.ref.coupon.year} coupon, launch week strongest. The coupon runs 2 or 4 whole weeks, Monday to Saturday.",
        "**Coupon length:** past coupons all ran 4 weeks, so the reference redemption rate is a 4-week rate. A 2-week coupon is "
        f"planned as the first two weeks of a 4-week coupon: the reference rate × {length_share(cal, 2):.0%}, the share of the "
        f"{cal.ref.coupon.year} coupon's extra units that came in its first two weeks. No 2-week coupon has been run, so this is an "
        "assumption: if a shorter coupon makes customers redeem faster, its weekly peak is higher than planned.",
        "**Demand, not sales:** sales ÷ that week's fill rate, so empty shelves last year do not shrink this year's numbers. "
        "Expected sales = demand × target fill rate.",
        f"**Plan weeks:** for the weeks after the data, normal demand = the baseline frozen at {weeks_text(cal.plan.window)} (neutral: "
        f"{h100(cal.plan.level_week)} a week) × the drivers the plan knows: the season, {py}'s holiday calendar and the weather "
        "scenario. Prices stay at their usual level. Done the same way for "
        f"{cal.ref.coupon.year}'s coupon weeks at week {cal.ref.coupon.start - 1} of {cal.ref.coupon.year}, the plan missed the "
        f"counterfactual by {cal.base_err_ref:.0%} on average: that is the cost of what the plan cannot know.",
        "**Volume to commit:** the lead time fixes the volume before any redemption is seen. Commit Q = forecast + z × σ, where the "
        "expected shortfall σ × G(z) equals (1 − fill rate) × forecast. σ combines the plan baseline's error in last year's coupon "
        f"weeks ({cal.base_err_ref:.0%}), the redemption-rate uncertainty and the incremental share's interval.",
        "**Shelf:** each delivery has to cover the selling days until the next one. Assumption: stores get one delivery a week, on "
        "Monday. The check takes the heaviest stretch in the coupon weeks for one high-volume store, measured against a normal week "
        "of the plan, and compares it with facings × units per facing.",
        f"**Start week:** a coupon can start in any week of {py} and has to end by week 52. Weeks 1 to {lw} are already in the data, "
        "so a coupon there is a what-if on that week's normal demand (the counterfactual, with the drivers as they were); its weekday "
        "shares and closed days come from that week.",
        f"**Data:** fictional. Hourly sales of {NAME} and the control brand from week {first_wk[1]} of {first_wk[0]} to week {lw} of "
        f"{py}, made from known drivers: a season shaped like the usual Google Trends pattern for \"deo\" in Germany, +20% and +8% on "
        "the two shopping days before a public holiday, +2% per degree above the seasonal normal (real Karlsruhe temperatures), a "
        "price elasticity of −2 against a category price index that moves with the control brand's and other brands' promotions, "
        "weekly and daily shocks and two coupons (2024 and 2025). Inputs of the model: weekly.csv (sales, fill rates and prices, with the category "
        "price index), temperature.csv (Open-Meteo archive), holidays.csv.",
        "**Test of the measurement:** on 200 simulated datasets with the same known drivers and coupons, the measured incremental units were 1 to 8% too "
        "high on average (2024: ×1.01, 2025: ×1.08; single runs ranged from ×0.3 to ×1.7 for the smaller coupon), "
        "and the 95% intervals held the true value in 87 to 96% of the runs. The dip is hard to measure: on average 55 to 69% "
        "of its true size. The drivers came back close to the truth (before a holiday +20.4% and +8.1% against +20% and +8%, "
        "weather +2.0% per degree, price elasticity −2.06 against −2.0); their intervals held the true value in 68 to 95% of "
        "the runs, too few for weather and price, because the regression treats the days of a week as independent while a "
        "week's shock is shared by its six days. The counterfactual of the weeks the drivers never saw missed actual weekly "
        "demand by 3.8% on average, with a bias of +0.9%.",
    ]))

# ---------------- assumptions, at the end ----------------
st.divider()
with st.expander("Assumptions"):
    st.markdown("\n\n".join(
        f"**{title}**\n" + "\n".join(f"- {t}" for t in items) for title, items in ASSUMPTIONS))
