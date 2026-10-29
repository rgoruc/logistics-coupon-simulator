"""Charts of the Streamlit app (matplotlib). Each function returns a figure.

Colors follow the HTML page: blue = counterfactual, pink = coupon, grey = context, red = sales.
"""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

BLUE, PINK, GREY, RED, INK, INK2, GRID = "#2a78d6", "#e87ba4", "#898781", "#e34948", "#0b0b0b", "#52514e", "#e1e0d9"
GOOD, BAD, BAND = "#0ca30c", "#d03b3b", "#d9d8d2"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "legend.frameon": False, "figure.dpi": 110, "hatch.linewidth": 0.8,
})


def _k(v, _pos=None):
    if abs(v) >= 1000:
        s = f"{v / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{s}K"
    return f"{v:,.0f}"


def _nice_floor(v, step=5000):
    return math.floor(v / step) * step


def full_year_chart(rows: list, plan_year: int, last_data_week: int):
    """rows: week, base, coupon, dip (<=0), demand, actualDemand (None in plan weeks) and own ("coupon" / "dip" in the weeks
    of the plan year's own coupon in the data). Predicted demand without the coupon (blue), with the coupon you plan (pink
    area and red dashed line, hatched: the dip) and actual demand (black)."""
    x = [r["week"] for r in rows]
    base = [r["base"] for r in rows]
    top = [r["base"] + r["coupon"] for r in rows]
    with_c = [r["demand"] for r in rows]
    fig, ax = plt.subplots(figsize=(11, 4.4))
    act = [(r["week"], r["actualDemand"]) for r in rows if r.get("actualDemand") is not None]
    lo = _nice_floor(min(min(with_c), min(base), *[a for _, a in act]) * 0.9)
    own = [r["week"] for r in rows if r.get("own") == "coupon"]
    if own:
        ax.axvspan(min(own) - 0.5, max(own) + 0.5, color=BAND, alpha=0.6, lw=0)
        ax.text((min(own) + max(own)) / 2, 0.97, f"{plan_year} coupon", transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=9, color=INK2)
    ax.fill_between(x, lo, base, color=BLUE, lw=0, label="Predicted demand without the coupon")
    ax.fill_between(x, base, top, color=PINK, lw=0, label="Predicted incremental demand from the planned coupon")
    ax.fill_between(x, with_c, top, facecolor="white", edgecolor=PINK, hatch="////", lw=0, label="Dip after the coupon")
    if any(r["coupon"] > 0.5 for r in rows):
        ax.plot(x, with_c, color=RED, lw=2, ls=(0, (4, 3)), label="Predicted demand with the coupon")
    if act:
        ax.plot(*zip(*act), color=INK, lw=1.6, label=f"Actual demand (weeks 1 to {last_data_week})")
    ax.axvline(last_data_week + 0.5, color=INK2, lw=0.8, ls=":")
    ax.text(last_data_week + 0.8, 0.97, "plan →", transform=ax.get_xaxis_transform(), va="top", fontsize=9, color=INK2)
    ax.set_xlim(1, max(x))
    ax.set_ylim(lo, max(top + [a for _, a in act]) * 1.06)
    ax.set_xticks([1, 10, 20, 30, 40, 50])
    ax.set_xlabel(f"Calendar week {plan_year}")
    ax.yaxis.set_major_formatter(FuncFormatter(_k))
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.22), ncol=3, fontsize=9)
    ax.text(1.0, -0.13, f"axis starts at {_k(lo)}", transform=ax.transAxes, ha="right", fontsize=8, color=GREY)
    fig.tight_layout()
    return fig

def measurement_chart(weeks: list, sd_w: float, coupon_weeks: list, year: int):
    """weeks: dicts with week, actual, cf. Band = counterfactual x/÷ exp(1.96 x weekly error)."""
    k = math.exp(1.96 * sd_w)
    x = [w["week"] for w in weeks]
    cf = [w["cf"] for w in weeks]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.axvspan(min(coupon_weeks) - 0.5, max(coupon_weeks) + 0.5, color=BAND, alpha=0.5, lw=0)
    ax.text((min(coupon_weeks) + max(coupon_weeks)) / 2, 1.0, "Coupon", transform=ax.get_xaxis_transform(), ha="center", va="bottom", color=INK2, fontsize=9)
    ax.fill_between(x, [c / k for c in cf], [c * k for c in cf], color=GREY, alpha=0.25, lw=0, label="95% interval of the counterfactual")
    ax.plot(x, cf, color=GREY, lw=2, ls=(0, (4, 3)), label="Counterfactual")
    ax.plot(x, [w["actual"] for w in weeks], color=BLUE, lw=2, label="Actual demand")
    ax.set_xlim(1, max(x))
    ax.set_xticks([1, 10, 20, 30, 40, 50])
    ax.set_xlabel(f"Calendar week {year}")
    ax.yaxis.set_major_formatter(FuncFormatter(_k))
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", ncol=3, fontsize=9)
    fig.tight_layout()
    return fig


def cumulative_chart(days: list, coupon_weeks: list):
    """days: day records of the coupon and dip weeks with cum, cumHalf, week, d."""
    idx = list(range(len(days)))
    cum = [p["cum"] for p in days]
    half = [p["cumHalf"] for p in days]
    last_c = max(i for i, p in enumerate(days) if p["week"] in coupon_weeks)
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.axvspan(0, last_c, color=BAND, alpha=0.5, lw=0)
    ax.text(last_c / 2, 1.0, "Coupon", transform=ax.get_xaxis_transform(), ha="center", va="bottom", color=INK2, fontsize=9)
    ax.fill_between(idx, [c - h for c, h in zip(cum, half)], [c + h for c, h in zip(cum, half)], color=BLUE, alpha=0.2, lw=0, label="95% interval")
    ax.plot(idx, cum, color=BLUE, lw=2, label="Cumulative incremental units")
    ax.set_xticks([i for i, p in enumerate(days) if p["d"] == 0])
    ax.set_xticklabels([f"W{p['week']}" for p in days if p["d"] == 0])
    ax.set_xlim(0, len(days) - 1)
    ax.set_ylim(bottom=min(0, min(c - h for c, h in zip(cum, half))))
    ax.yaxis.set_major_formatter(FuncFormatter(_k))
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    return fig


def bridge_chart(summary: dict):
    """From units sold with the coupon to net incremental units."""
    s = summary
    steps = [("Units sold with the coupon", s["couponUnits"], "total"),
             (f"Would have been bought anyway · {s['anyway'] / s['couponUnits']:.0%}", -s["anyway"], "delta"),
             (f"Incremental units · {s['incremental'] / s['couponUnits']:.0%}", s["incremental"], "total"),
             (f"Dip after the coupon · {s['dip'] / s['incremental']:.0%}" if s["incremental"] else "Dip after the coupon", -s["dip"], "delta"),
             (f"Net incremental units · {s['net'] / s['incremental']:.0%}" if s["incremental"] else "Net incremental units", s["net"], "total")]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    running = 0.0
    hi_all = max(1.0, s["couponUnits"])
    for i, (label, v, kind) in enumerate(steps):
        if kind == "total":
            lo, hi, color = 0.0, v, GREY
            running = v
        else:
            lo, hi = sorted([running, running + v])
            color = GOOD if v >= 0 else BAD
            running += v
        y = len(steps) - 1 - i
        ax.barh(y, hi - lo, left=lo, height=0.5, color=color)
        v100 = round(v / 100) * 100
        txt = f"{v100:,.0f}" if kind == "total" else f"{v100:+,.0f}".replace("-", "−")
        ax.text(hi + hi_all * 0.01, y, txt, va="center", fontsize=10, fontweight="bold", color=INK)
        ax.text(0, y + 0.36, label, va="bottom", fontsize=9, color=INK2, fontweight="bold" if i == 4 else "normal")
    ax.set_yticks([])
    ax.set_ylim(-0.6, len(steps) - 0.1)
    ax.set_xlim(0, hi_all * 1.18)
    ax.xaxis.set_major_formatter(FuncFormatter(_k))
    ax.spines["left"].set_visible(False)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def season_chart(curve: list):
    """The fitted season factor by calendar week (52 values, centred on 1)."""
    x = list(range(1, len(curve) + 1))
    fig, ax = plt.subplots(figsize=(11, 2.8))
    ax.axhline(1.0, color=GREY, lw=1, ls=(0, (4, 3)))
    ax.plot(x, curve, color=BLUE, lw=2, label="Season factor")
    lo, hi = min(curve), max(curve)
    for v, lab in ((lo, "low"), (hi, "peak")):
        w = curve.index(v) + 1
        ax.plot([w], [v], "o", color=BLUE, ms=6)
        ax.annotate(f"{lab}: week {w}, {v:.2f}", (w, v), textcoords="offset points", xytext=(6, -12 if lab == "peak" else 6),
                    fontsize=9, color=INK2)
    ax.set_xlim(1, len(curve))
    ax.set_xticks([1, 10, 20, 30, 40, 50])
    ax.set_xlabel("Calendar week")
    ax.set_ylim(min(0.8, lo - 0.05), max(1.2, hi + 0.05))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:.2f}"))
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    return fig
