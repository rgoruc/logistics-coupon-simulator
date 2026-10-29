/* =====================================================================================================
   09  Look at the results (run one query at a time)
   ===================================================================================================== */

-- the whole plan year week by week: neutral baseline, the week's drivers, counterfactual, demand and the measured coupon
SELECT year, week, period, ROUND(neutral_baseline) AS neutral_baseline, ROUND(drivers_factor, 3) AS drivers_factor,
       ROUND(counterfactual) AS counterfactual, ROUND(demand) AS demand,
       ROUND(demand_minus_counterfactual) AS difference, sales
FROM weekly_counterfactual
WHERE year = 2026
ORDER BY year, week;

-- incremental units of Deo Test Brand's own coupons and the dip after them
SELECT * FROM coupon_measurement ORDER BY coupon_year;

-- cross-check: the control brand against its own counterfactual in the same weeks (near 0 = no sign of cannibalisation)
SELECT coupon_year, ROUND(coupon_weeks_deviation * 100, 1) AS coupon_weeks_pct, ROUND(dip_weeks_deviation * 100, 1) AS dip_weeks_pct
FROM control_check
ORDER BY coupon_year;

-- the drivers of one week, day by day (the week before Easter 2025: Wednesday and Thursday carry the pre-holiday factors)
SELECT sales_date, day_name, open, close_hour, season_factor, calendar_factor, weather_factor,
       price_factor, drivers_factor, temperature_anomaly, pre_holiday_1, pre_holiday_2
FROM day_factors
WHERE sales_date BETWEEN '2025-04-14' AND '2025-04-19'
ORDER BY sales_date;

-- the plan: the neutral week behind it, then week by week with the season and calendar of the week
SELECT * FROM plan_summary;
SELECT week, ROUND(season_factor, 4) AS season_factor, ROUND(calendar_factor, 4) AS calendar_factor,
       ROUND(neutral_level) AS neutral_level, ROUND(counterfactual) AS normal_demand
FROM plan_weeks_counterfactual
ORDER BY week;

-- the plan's weeks 51 and 52 day by day: the run-up to Christmas, the short day (24 December), the holidays
SELECT sales_date, day_name, close_hour, ROUND(season_factor, 3) AS season, ROUND(calendar_factor, 3) AS calendar,
       ROUND(neutral_level) AS neutral_level, ROUND(counterfactual) AS normal_demand
FROM plan_days_counterfactual
WHERE week IN (51, 52)
ORDER BY sales_date;

-- the rule at work: the 6 values behind the plan baseline of one weekday (Friday)
SELECT v.weekday, a.src_year, a.src_week, a.sales_date,
       ROUND(a.demand, 1) AS demand, ROUND(a.drivers_factor, 3) AS drivers_factor, ROUND(a.neutral_value, 1) AS neutral_value,
       v.rnk, ROUND(v.threshold, 1) AS threshold, v.status
FROM test_rule_values v
JOIN test_neutral_values a
  ON a.target_year = v.target_year AND a.target_week = v.target_week
 AND a.weekday = v.weekday AND a.src_pos = v.src_pos
JOIN plan_summary p
  ON p.plan_year = v.target_year AND p.first_plan_week = v.target_week
WHERE v.weekday = 5
ORDER BY v.rnk;

-- the plan baseline per weekday
SELECT c.weekday, ROUND(c.l_day, 1) AS baseline_level, c.n, c.n_out
FROM test_baseline_day c
JOIN plan_summary p
  ON p.plan_year = c.target_year AND p.first_plan_week = c.target_week
ORDER BY c.weekday;

-- how many values the rule set aside as outliers, per target week
SELECT target_year, target_week, SUM(CASE WHEN status = 'outlier' THEN 1 ELSE 0 END) AS outliers, COUNT(*) AS values_seen
FROM test_rule_values
GROUP BY target_year, target_week
ORDER BY target_year, target_week;
