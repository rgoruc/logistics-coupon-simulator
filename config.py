"""Settings of the model. Change a number here, then run `python run_model.py` again.

Plan assumptions (discount, app users, redemption rate, fill rate, lead time ...) are not here:
they are in data/input/plan_assumptions.csv, and the Streamlit app has sliders for them.
"""

# Folders, relative to this file
DATA_DIR = "data/input"
OUTPUT_DIR = "data/output"

# ---- Baseline rule (applied per weekday, on daily demand) ----
# How many weeks the baseline looks back. The window skips weeks of Deo Test Brand's own coupons
# and the dip after them, and is frozen during a coupon at the weeks before it.
BASELINE_WEEKS = 6
# Outlier threshold = value at the middle rank + OUTLIER_SD_MULTIPLIER x standard deviation
# of the middle ranks + OUTLIER_CONSTANT
OUTLIER_SD_MULTIPLIER = 2.0
OUTLIER_CONSTANT = 2.0
# For the standard deviation, round(n / SPREAD_TRIM_DIVISOR) ranks are left out at each end
SPREAD_TRIM_DIVISOR = 6
# The baseline is the average of the values within +/- MEDIAN_HALF_WINDOW ranks of the
# median rank of the values that are not outliers (+/- MEDIAN_HALF_WINDOW_LARGE when there
# are at least LARGE_N values)
MEDIAN_HALF_WINDOW = 1
MEDIAN_HALF_WINDOW_LARGE = 2
LARGE_N = 20

# ---- Counterfactual ----
# The plan takes the weekly shape of this coupon and of the dip after it, and its measured
# incremental share as the reference (it must be a row of own_coupons.csv). The drivers are
# fitted on the years before the plan year.
REFERENCE_COUPON_YEAR = 2025
THETA_ITERATIONS = 5  # not used by the drivers model; kept so older scripts still run
