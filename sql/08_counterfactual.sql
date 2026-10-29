/* =====================================================================================================
   08  Counterfactual (CausalImpact logic), the coupon measurement, the control cross-check and the plan
   -----------------------------------------------------------------------------------------------------
   counterfactual of a day = L[weekday, open hours] x drivers_factor of the day
                           = "the neutral level, on this day's season, calendar, weather and price"
   Incremental units of a coupon = demand - counterfactual, summed over the coupon weeks; the dip after it
   the same way over the dip weeks.
   Plan: the frozen neutral baseline (the 6 weeks before the first plan week) x plan_factor of every plan day
         (season x calendar; weather and prices are unknown, so 1).
   ===================================================================================================== */

DROP TABLE IF EXISTS daily_counterfactual;
CREATE TABLE daily_counterfactual AS
SELECT p.sales_date, p.year, p.week, p.weekday,
       p.demand,
       p.units                     AS sales,
       l.l_open                    AS neutral_level,
       f.drivers_factor,
       l.l_open * f.drivers_factor AS counterfactual,
       CASE WHEN o.period IS NULL THEN '' ELSE o.period || ' ' || CAST(o.coupon_year AS VARCHAR) END AS period
FROM daily_demand p
JOIN test_baseline_day l
  ON l.target_year = p.year AND l.target_week = p.week AND l.weekday = p.weekday
JOIN day_factors f
  ON f.sales_date = p.sales_date
LEFT JOIN own_weeks o
  ON o.year = p.year AND o.week = p.week
WHERE p.brand = 'test';

DROP TABLE IF EXISTS weekly_counterfactual;
CREATE TABLE weekly_counterfactual AS
WITH lvl AS (
  SELECT target_year, target_week, SUM(l_day) AS baseline
  FROM test_baseline_day
  GROUP BY target_year, target_week
)
SELECT d.year, d.week,
       MAX(d.period)                                 AS period,
       MAX(lv.baseline)                              AS neutral_baseline,   -- the neutral week (all weekdays, all hours)
       SUM(d.counterfactual) / SUM(d.neutral_level)  AS drivers_factor,     -- the week's drivers, demand-weighted
       SUM(d.counterfactual)                         AS counterfactual,
       SUM(d.demand)                                 AS demand,
       SUM(d.demand) - SUM(d.counterfactual)         AS demand_minus_counterfactual,
       SUM(d.sales)                                  AS sales
FROM daily_counterfactual d
JOIN lvl lv
  ON lv.target_year = d.year AND lv.target_week = d.week
GROUP BY d.year, d.week;

-- coupon measurement: incremental units in the coupon weeks, the dip after them
DROP TABLE IF EXISTS coupon_measurement;
CREATE TABLE coupon_measurement AS
SELECT o.coupon_year,
       SUM(CASE WHEN o.period = 'coupon' THEN wc.demand_minus_counterfactual ELSE 0 END)  AS incremental_units,
       -SUM(CASE WHEN o.period = 'dip' THEN wc.demand_minus_counterfactual ELSE 0 END)    AS dip_units,
       SUM(wc.demand_minus_counterfactual)                                                AS net_units,
       SUM(CASE WHEN o.period = 'coupon' THEN wc.counterfactual ELSE 0 END)               AS counterfactual_in_coupon_weeks
FROM weekly_counterfactual wc
JOIN own_weeks o
  ON o.year = wc.year AND o.week = wc.week
GROUP BY o.coupon_year;

-- the control brand against its own counterfactual (neutral level x its own drivers), week by week
DROP TABLE IF EXISTS control_counterfactual;
CREATE TABLE control_counterfactual AS
SELECT p.year, p.week,
       SUM(p.demand)                                               AS demand,
       SUM(l.l_open * f.control_drivers_factor)                    AS counterfactual,
       SUM(p.demand) - SUM(l.l_open * f.control_drivers_factor)    AS demand_minus_counterfactual
FROM daily_demand p
JOIN control_baseline_day l
  ON l.target_year = p.year AND l.target_week = p.week AND l.weekday = p.weekday
JOIN day_factors f
  ON f.sales_date = p.sales_date
WHERE p.brand = 'control'
GROUP BY p.year, p.week;

-- cross-check: the control's deviation in Deo Test Brand's coupon weeks and dip weeks (0 = on its normal path)
DROP TABLE IF EXISTS control_check;
CREATE TABLE control_check AS
SELECT o.coupon_year,
       SUM(CASE WHEN o.period = 'coupon' THEN cc.demand_minus_counterfactual ELSE 0 END)
         / SUM(CASE WHEN o.period = 'coupon' THEN cc.counterfactual ELSE 0 END)  AS coupon_weeks_deviation,
       SUM(CASE WHEN o.period = 'dip' THEN cc.demand_minus_counterfactual ELSE 0 END)
         / SUM(CASE WHEN o.period = 'dip' THEN cc.counterfactual ELSE 0 END)     AS dip_weeks_deviation
FROM control_counterfactual cc
JOIN own_weeks o
  ON o.year = cc.year AND o.week = cc.week
GROUP BY o.coupon_year;

-- the plan, day by day: the frozen neutral baseline (window before the first plan week) x plan_factor of the day
DROP TABLE IF EXISTS plan_days_counterfactual;
CREATE TABLE plan_days_counterfactual AS
WITH anchor AS (
  SELECT year AS target_year, week AS target_week FROM week_positions WHERE has_data = 0
),
cells AS (
  SELECT c.weekday, c.hour, c.baseline_level
  FROM test_baseline_cells c
  JOIN anchor a ON a.target_year = c.target_year AND a.target_week = c.target_week
)
SELECT f.sales_date, f.year AS plan_year, f.week, f.weekday, f.day_name, f.close_hour,
       f.season_factor, f.calendar_factor, f.plan_factor,
       SUM(CASE WHEN c.hour < f.close_hour THEN c.baseline_level ELSE 0 END)                 AS neutral_level,
       SUM(CASE WHEN c.hour < f.close_hour THEN c.baseline_level ELSE 0 END) * f.plan_factor AS counterfactual
FROM day_factors f
JOIN anchor a
  ON f.year = a.target_year AND f.week >= a.target_week AND f.week <= 52
JOIN cells c
  ON c.weekday = f.weekday
WHERE f.open = 1
  AND f.weekday <= 6
GROUP BY f.sales_date, f.year, f.week, f.weekday, f.day_name, f.close_hour, f.season_factor, f.calendar_factor, f.plan_factor;

-- the plan weeks: normal demand at normal weather, with the season and calendar factors of the week (demand-weighted)
DROP TABLE IF EXISTS plan_weeks_counterfactual;
CREATE TABLE plan_weeks_counterfactual AS
SELECT plan_year, week,
       SUM(counterfactual * season_factor)   / SUM(counterfactual) AS season_factor,
       SUM(counterfactual * calendar_factor) / SUM(counterfactual) AS calendar_factor,
       SUM(neutral_level)                                          AS neutral_level,
       SUM(counterfactual)                                         AS counterfactual
FROM plan_days_counterfactual
GROUP BY plan_year, week;

-- the plan in one row: the neutral week behind it and a typical plan week
DROP TABLE IF EXISTS plan_summary;
CREATE TABLE plan_summary AS
WITH anchor AS (
  SELECT year AS target_year, week AS target_week FROM week_positions WHERE has_data = 0
),
lvl AS (
  SELECT SUM(l.l_day) AS neutral_week
  FROM test_baseline_day l
  JOIN anchor a ON a.target_year = l.target_year AND a.target_week = l.target_week
)
SELECT a.target_year               AS plan_year,
       a.target_week               AS first_plan_week,
       lvl.neutral_week,
       (SELECT AVG(counterfactual) FROM plan_weeks_counterfactual) AS typical_plan_week,
       (SELECT MIN(counterfactual) FROM plan_weeks_counterfactual) AS lowest_plan_week,
       (SELECT MAX(counterfactual) FROM plan_weeks_counterfactual) AS highest_plan_week
FROM anchor a
CROSS JOIN lvl;
