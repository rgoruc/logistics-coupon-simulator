# Logistics Coupon Simulator

For the marketing team: what an app coupon means for logistics before it is launched. Case study with fictional
data: the **Drugstore** runs an app coupon on **Deo Test Brand** (men's deo, shelf price €1.00; the average deo in
the Drugstore costs €1.50). The model measures how many units a coupon really adds (incremental units, against a
counterfactual) and turns the next coupon into what logistics needs to know: the extra units, the volume supply
chain has to commit 96 days ahead and the deadline, the weekly volume in the distribution centre, the dip after
the coupon and the shelf in the stores. Normal demand is the baseline rule × four measured drivers (season, calendar,
weather, price against the category price index). **Deo Control Brand** (€2.00) is the control brand: it gets the
same model as a cross-check.

Everything here works on the same data and gives the same numbers:

| Part | What it is | How you open it |
|---|---|---|
| `data/input/*.csv` | the data and the plan assumptions | Excel, VS Code |
| `model/` + `run_model.py` | the model in Python | `python run_model.py` |
| `data/output/` | every result table (CSV + one Excel file) | Excel |
| `sql/` | the baseline rule, the counterfactual and the plan in Snowflake SQL (the drivers come from Python) | Snowflake, or locally with `python sql/run_sql_locally.py` |
| `app/streamlit_app.py` | interactive app with sliders | `streamlit run app/streamlit_app.py` |
| `html/coupon_planner.html` | the phone-friendly page (same as the published one) | double-click |
| `simulation/` | makes new test data with known effects and checks the model on it | `python simulation/validate.py` |
| `tests/check_numbers.py` | checks that Python gives the HTML page's numbers | `python tests/check_numbers.py` |

## 1. Set up once

1. Unzip the folder, for example into `Documents`.
2. Install Python 3.9 or newer from python.org if you do not have it (on Windows tick "Add python.exe to PATH").
   Check in a terminal: `python --version` (on a Mac it may be `python3`; then use `python3` and `pip3` below).
3. Open a terminal **in the project folder**. Easiest: open the folder in VS Code (File > Open Folder), then
   Terminal > New Terminal.
4. Install the packages:
   ```
   pip install -r requirements.txt
   ```
   Optional, to keep these packages separate from others: `python -m venv .venv`, then activate it
   (Mac: `source .venv/bin/activate`, Windows: `.venv\Scripts\activate`) before `pip install`.

All assumptions of the case (prices, coupon, normal demand, supply chain) are listed in both apps under
**Assumptions**.

## 2. Run

```
python run_model.py                 # runs the model, writes data/output/*.csv and all_tables.xlsx
streamlit run app/streamlit_app.py  # opens the app in your browser (stop it with Ctrl+C)
python tests/check_numbers.py       # Python vs the HTML page: should say "All equal"
python sql/run_sql_locally.py       # runs the SQL on your computer and compares it with Python
python html/build_html.py           # rebuilds html/coupon_planner.html from data/input
```

## 3. What to change where

| You want to change | Where | Then |
|---|---|---|
| discount (10, 20 or 30%), coupon length (2 or 4 weeks), start week (any week of 2026), store delivery day (Monday), app users, redemption rate (Einlösequote), units per redemption, incremental share, dip share, fill rate, lead time, delivery days, prices (Deo Test Brand, Deo Control Brand, market average) | `data/input/plan_assumptions.csv` (empty value = default from past coupons or the data), or the sliders in the app | `python run_model.py`, `python html/build_html.py` |
| the baseline rule: window weeks (6), outlier threshold (2 × sd + 2), ranks around the median | `config.py` (the app has no settings for it) | `python run_model.py`; in SQL: `sql/02_parameters.sql` |
| the data | replace `data/input/hourly_sales.csv` and `weekly.csv` with the same columns; `holidays.csv` and `temperature.csv` have to cover the same days; `weekly.csv` carries the category price index | `python run_model.py` |
| past coupons, own coupons in the data, shelf | `past_coupons.csv`, `own_coupons.csv`, `shelf.csv` | `python run_model.py` |
| the method itself | the Python files in `model/` (each file starts with an explanation) | `python run_model.py`, the app picks it up |

CSV files can be edited in VS Code or Excel. If Excel saves them with semicolons and decimal commas, they still load.

## 4. How the model works

- **Baseline (your rule, per weekday and opening hour):** the same weekday and hour in the last 6 weeks, as neutral
  demand (demand ÷ the drivers of the day), ranked high to low. Outlier threshold = value at rank floor(n/2)+1 + 2 ×
  standard deviation of the middle ranks + 2. Baseline = average of the 3 values around the median of the values that
  are not outliers. Closed days and closed hours (24 December from 14:00) are left out.
- **Rolling, then frozen:** the window rolls with every week and skips weeks of Deo Test Brand's own coupons and the dip
  after them. During a coupon and its dip it is frozen at the 6 weeks before the coupon.
- **Drivers:** normal demand of a day = baseline × season × calendar × weather × price.
  Season: a smooth yearly curve (5 sine/cosine pairs). Calendar: the first and second shopping day before a public
  holiday (`data/input/holidays.csv`) and the run-up to Christmas (1 to 24 December). Weather: the daily maximum
  temperature against its seasonal normal (`temperature.csv`, Karlsruhe, Open-Meteo archive). Price: Deo Test Brand's
  shelf price ÷ the category price index (`category_price` in `weekly.csv`: the volume-weighted average shelf price of
  men's deo in the Drugstore, weekly), against its usual level. The effects are measured together in one regression on the log of daily demand (`model/drivers.py`),
  on 2024 and 2025, outside Deo Test Brand's own coupons; 2026 is the test. The weekday and the level are not in the
  regression: the baseline carries them. Fitted values: `data/output/drivers.csv`; the factors of every day:
  `day_factors.csv`.
- **Counterfactual:** for a day = the sum over its open hours of baseline[weekday, hour] × the drivers of the day.
  **Incremental units** = demand − counterfactual in the coupon weeks; the **dip** = the same in the weeks after.
  Demand = sales ÷ fill rate. The 95% intervals use the day-to-day error of the regression and the error of the
  frozen baseline level (one number for the whole coupon and its dip); both come from days outside the coupons.
- **Control brand:** Deo Control Brand gets the same model (its own baseline and drivers, its own price against the
  category index): the cross-check of the control assumption. In the coupon weeks the control stayed on its normal
  path (2024: 0.0%, 2025: −0.4%).
- **Start week:** any week of 2026; the coupon has to end by week 52. Weeks 1 to 42 are already in the data, so a
  coupon there is a what-if on that week's normal demand (its counterfactual, with the drivers as they were).
- **Store delivery:** assumption: one delivery a week, on Monday. The shelf check compares the heaviest stretch
  between two deliveries with facings × units per facing.
- **Coupon length:** 2 or 4 weeks. Past coupons all ran 4 weeks, so the reference redemption rate is a 4-week rate;
  a 2-week coupon is planned as the first two weeks of a 4-week coupon (the share of the 2025 coupon's extra units
  that came in its first two weeks). No 2-week coupon has been run, so this is an assumption.
- **Plan:** redemptions = app users × redemption rate; units with the coupon = redemptions × units per redemption;
  incremental = that × incremental share; dip = incremental × pulled-forward share. Normal demand of the plan weeks
  (43 to 52) = the baseline frozen at weeks 37 to 42 of 2026 × the drivers the plan knows: the season, 2026's holiday
  calendar and the weather scenario (normal, or cold / warm: one standard deviation of the weekly temperature anomaly,
  ±3.1 °C). Prices stay at their usual level (factor 1). The same plan made at week 42 of 2025 missed the
  counterfactual of the 2025 coupon weeks by 5% on average; that error goes into the commit buffer.
- **Volume to commit:** Q = forecast + z × σ, with z chosen so that the expected shortfall matches the target fill rate.
- **Actual against the model:** for the weeks with data the year chart also shows actual demand (sales ÷ fill rate); its
  gap to the normal demand is actual minus the model. The daily plan of a week with data shows the same per day.
- **Default plan:** the coupon starts in week 1 (`start_week` in `data/input/plan_assumptions.csv`), a what-if on that
  week's data; the page starts from the reference redemption rate, so the coupon shows at once.

The model's files, in the order the calculation runs: `model/data.py` → `drivers.py` → `baseline.py` →
`counterfactual.py` → `calibrate.py` → `plan.py` → `supply.py` → `outputs.py`.

## 5. SQL (Snowflake)

Run the files in order: `01_create_and_load_tables.sql` (creates the tables and loads the CSVs; the file explains
the upload) → `02_parameters.sql` → `03_demand.sql` → `04_windows.sql` → `05_baseline_rule.sql` →
`06_control_baseline.sql` → `07_test_brand_baseline.sql` → `08_counterfactual.sql`. `09_look_at_results.sql` has queries
to look at the results. Your rule is written once, as the view `rule_values_v` in step 05; steps 06 and 07 use it
for the control brand and for Deo Test Brand.

The drivers are fitted in Python and come into SQL as one factor per day: after `python run_model.py` upload
`data/output/day_factors.csv` together with the three input files (step 01). Step 07 divides every hour by the
drivers of its day before the rule (neutral values), step 08 multiplies the baseline by the drivers of the target
day (the counterfactual), measures the coupons, runs the control cross-check and builds the plan weeks from the
frozen baseline and the plan factor of every day. Steps 02 to 08 use only standard SQL, so `sql/run_sql_locally.py`
runs the same files with SQLite (part of Python) and compares every week, the coupon measurements, the control
check, the plan days and the rule's verdicts with Python. Step 01 is Snowflake-only.

## 6. The HTML page

`html/coupon_planner.html` is the same page as the published one and works offline. `python html/build_html.py`
rebuilds it from `data/input` (sales, past coupons, shelf, plan defaults). The page has its own copy of the logic in
JavaScript (`html/src/model.src.js`, the same regression and rule as `model/`) and its texts are written for this case's
calendar (data up to week 42 of 2026, data from week 1 of 2024, coupons in 2024 weeks 18-21 and 2025 weeks 43-47;
no coupon in 2026 so far). Changes to `model/` or `config.py` do not reach the page; use the Streamlit app for those.

## 7. Test data and checks

- The data are fictional, made by `simulation/generate_data.py` with seed 37 from known drivers: a season shaped like
  the usual Google Trends pattern for "deo" in Germany, +20% and +8% on the two shopping days before a public holiday,
  +2% per degree above the seasonal normal (the real Karlsruhe temperatures in `data/input/temperature.csv`,
  Open-Meteo archive), a price elasticity of −2 against the category price index, weekly and daily shocks, Poisson
  counts per hour and two coupons (2024 weeks 18-21 and 2025 weeks 43-47; none in 2026 so far). The category price index is made up too: 12% of the category's volume is Deo Test
  Brand (€1.00), 10% the control brand (€2.00, −20% in three weeks of 2025 and three of 2026) and 78% other brands
  (on promotion 4 to 7% below normal in 6 or 7 weeks a year); each brand's volume rises when it is on promotion. The
  index is €1.50 at normal prices and between €1.42 and €1.47 in promotion weeks. The true effects are in
  `simulation/truth_seed_37.json`. Incremental units, true and measured: 2024 coupon 8,140 and 9,124, 2025 coupon
  23,921 and 24,168. Drivers, true and measured: before a holiday +20% / +19.8% and
  +8% / +7.7%, weather +2.0% / +2.1% per degree, price elasticity −2.0 / −2.09. The data run from week 1 of 2024 to
  week 42 of 2026.
- `simulation/google_trends_deo_de.csv`: weekly search interest for "deo" in Germany, past 5 years, retrieved
  28 October 2026. Data source: Google Trends (https://www.google.com/trends). Only the generator uses it, for the
  shape of the season (the pattern within each year); it is not an input of the model. `holidays.csv`: the public
  holidays of Baden-Württemberg 2024 to 2026 and the short day on 24 December.
- `python simulation/generate_data.py --seed 7` makes another dataset in `simulation/output_seed_7/`.
  To use it, set `DATA_DIR = "simulation/output_seed_7"` in `config.py`.
- `python simulation/validate.py --seeds 200` runs the model on 200 datasets with the same drivers and coupons and
  reports the bias of the measured coupons and drivers and how often the 95% intervals held the true value.
  Latest run (`python simulation/validate.py --seeds 200`): on 200 simulated datasets with the same known drivers and
  coupons, the measured incremental units were 1 to 8% too high on average (2024: ×1.01, 2025: ×1.08;
  single runs ranged from ×0.3 to ×1.7 for the smaller coupon), and the 95% intervals held the true value in 88 to
  96% of the runs. The dip is hard to measure: on average 55 to 69% of its true size. The drivers came back close to
  the truth (before a holiday +20.4% and +8.1% against +20% and +8%, weather +2.0% per degree, price elasticity −2.06
  against −2.0); their intervals held the true value in 68 to 95% of the runs, too few for weather and price, because
  the regression treats the days of a week as independent while a week's shock is shared by its six days. The
  counterfactual of the weeks the drivers never saw missed actual weekly demand by 3.8% on average, with a bias of
  +1.0%.
- `python tests/check_numbers.py` compares about 15,500 numbers of the Python model with the HTML page's JavaScript
  model (`tests/reference_from_html.json`). If you change the model on purpose, differences are expected.
