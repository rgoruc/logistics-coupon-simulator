"""Run the model on data/input and write every table to data/output.

    python run_model.py

Settings of the baseline rule: config.py. Plan assumptions: data/input/plan_assumptions.csv.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from model import calibrate, default_scenario, forecast, load  # noqa: E402
from model.baseline import RuleSettings  # noqa: E402
from model.outputs import all_tables, write_outputs  # noqa: E402


def main() -> None:
    data = load(ROOT / config.DATA_DIR)
    rule = RuleSettings.from_config(config)
    cal = calibrate(data, rule, config.REFERENCE_COUPON_YEAR, config.THETA_ITERATIONS)
    sc = default_scenario(cal)
    res = forecast(cal, sc)
    files = write_outputs(all_tables(cal, sc, res), ROOT / config.OUTPUT_DIR)

    s, C, m = res.summary, res.summary["commit"], cal.ref
    e = {x["key"]: x for x in cal.drivers.effects()}
    pct = lambda k: f"{(math.exp(e[k]['coef']) - 1) * 100:+.1f}%"  # noqa: E731
    print(f"Drivers (fitted on {', '.join(map(str, cal.train_years))}): day before a holiday {pct('pre1')}, two days before {pct('pre2')}, "
          f"weather {(math.exp(e['anomaly']['coef']) - 1) * 100:+.1f}% per degree, price elasticity against the category {e['z']['coef']:.2f}")
    h = cal.holdout
    print(f"Test on {h['year']} (weeks the drivers never saw): weekly error {h['weekly_rms']:.1%}, daily {h['daily_rms']:.1%}, "
          f"bias {h['weekly_bias']:+.1%}   (all years out of sample: daily {cal.sd_d:.1%}, weekly {cal.sd_w:.1%})")
    for year, mm in cal.measurements.items():
        print(f"Coupon {year}: +{mm.extra:,.0f} incremental units (95%: {mm.extra - mm.extra_half:,.0f} to {mm.extra + mm.extra_half:,.0f}), "
              f"dip -{mm.dip:,.0f}, net +{mm.net:,.0f}")
    pw = cal.plan.weeks
    print(f"Plan: neutral baseline {cal.plan.level_week:,.0f} a week; normal demand {pw[min(pw)]:,.0f} in week {min(pw)} to "
          f"{max(pw.values()):,.0f} in week {max(pw, key=pw.get)}")
    print(f"Plan, weeks {s['weeks'][0]}-{s['weeks'][-1]} at {sc.discount:.0%} off: {s['redemptions']:,.0f} redemptions, "
          f"{s['couponUnits']:,.0f} units with the coupon, +{s['incremental']:,.0f} incremental, net +{s['net']:,.0f}")
    print(f"Commit {C['q']:,.0f} units for a {sc.fill_rate:.0%} fill rate (forecast {C['mu']:,.0f}, buffer {C['buffer']:+,.0f})")
    print(f"Wrote {len(files)} files to {ROOT / config.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
