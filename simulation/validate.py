"""Test the model on many simulated datasets with known coupon effects and known drivers.

    python simulation/validate.py --seeds 20

For each seed it makes a dataset (generate_data.py), runs the model with the settings in config.py and compares
(1) the measured incremental units and dip of each coupon with the true ones and (2) the fitted driver effects
with the true ones. It reports the average ratio (1.00 = unbiased), the spread, and how often the 95% interval
contains the true value. 20 seeds take about a minute.
"""
from __future__ import annotations

import argparse
import math
import statistics as stats
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import config  # noqa: E402
from generate_data import Inputs, simulate, write  # noqa: E402
from model import calibrate, load  # noqa: E402
from model.baseline import RuleSettings  # noqa: E402

DRIVERS = [("pre1", "pre_holiday_1", True), ("pre2", "pre_holiday_2", True), ("anomaly", "weather_per_degree", False),
           ("z", "price_elasticity_test", False)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20, help="number of datasets (seeds 1 ... n)")
    args = ap.parse_args()
    inp = Inputs()
    rule = RuleSettings.from_config(config)
    res = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in range(1, args.seeds + 1):
            sim = simulate(seed, inp)
            folder = Path(tmp) / str(seed)
            write(sim, folder, ROOT / config.DATA_DIR)
            cal = calibrate(load(folder, config.OPENING_HOURS), rule, config.REFERENCE_COUPON_YEAR, config.THETA_ITERATIONS)
            t = sim["truth"]
            row = {"seed": seed, "holdout_w": cal.holdout["weekly_rms"], "holdout_bias": cal.holdout["weekly_bias"]}
            for y, m in cal.measurements.items():
                row[f"inc{y}"] = m.extra / t[f"incremental_{y}"]
                row[f"dip{y}"] = m.dip / t[f"dip_{y}"]
                row[f"cov{y}"] = abs(m.extra - t[f"incremental_{y}"]) <= m.extra_half
            eff = {e["key"]: e for e in cal.drivers.effects()}
            for key, tkey, is_factor in DRIVERS:
                truth = math.log(t[tkey]) if is_factor else t[tkey]
                row[f"d_{key}"] = eff[key]["coef"]
                row[f"dcov_{key}"] = abs(eff[key]["coef"] - truth) <= 1.96 * eff[key]["se"]
            sc = [math.log(cal.season_curve[w - 1] / t["season_by_week"][str(w)]) if isinstance(t["season_by_week"], dict) and str(w) in t["season_by_week"]
                  else math.log(cal.season_curve[w - 1] / t["season_by_week"][w]) for w in range(1, 53)]
            row["season_rms"] = math.sqrt(sum(x * x for x in sc) / 52)
            res.append(row)
            print(f"seed {seed:3d}: holdout weekly {row['holdout_w']:.3f} | incremental " +
                  ", ".join(f"{y} x{row[f'inc{y}']:.3f}" for y in cal.measurements) + " | dip " +
                  ", ".join(f"{y} x{row[f'dip{y}']:.3f}" for y in cal.measurements))
    n = len(res)
    t = simulate(1, inp)["truth"]
    print(f"\n{n} datasets:")
    print(f"  holdout weekly error: mean {stats.mean(r['holdout_w'] for r in res):.3f}; bias {stats.mean(r['holdout_bias'] for r in res):+.3f}")
    for y in sorted({k for r in res for k in r if k.startswith('inc')}):
        yy = y[3:]
        inc = [r[y] for r in res]
        print(f"  coupon {yy}: incremental measured / true = {stats.mean(inc):.3f} on average (range {min(inc):.3f} to {max(inc):.3f}); "
              f"dip {stats.mean(r[f'dip{yy}'] for r in res):.3f}; 95% interval held the true value in {sum(r[f'cov{yy}'] for r in res)} of {n}")
    for key, tkey, is_factor in DRIVERS:
        truth = math.log(t[tkey]) if is_factor else t[tkey]
        vals = [r[f"d_{key}"] for r in res]
        print(f"  driver {key}: true {truth:.4f}, estimated {stats.mean(vals):.4f} on average (sd {stats.pstdev(vals):.4f}); "
              f"95% interval held the true value in {sum(r[f'dcov_{key}'] for r in res)} of {n}")
    print(f"  season curve: rms difference to the true season {stats.mean(r['season_rms'] for r in res):.3f} (log)")


if __name__ == "__main__":
    main()
