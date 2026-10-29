/* =====================================================================================================
   06  The control brand's neutral baseline (Deo Control Brand), per target week and weekday
   -----------------------------------------------------------------------------------------------------
   Same rule as for Deo Test Brand (step 07), on the control's neutral values:
       neutral value = demand / control_drivers_factor of that day
   The control is not part of Deo Test Brand's counterfactual. Its own counterfactual in step 08 is a
   cross-check: in the coupon weeks the control should stay on its normal path (no sign that the coupon took
   sales from it).
   ===================================================================================================== */

DELETE FROM rule_input;
INSERT INTO rule_input (target_year, target_week, weekday, src_pos, value)
SELECT b.target_year, b.target_week, d.weekday, b.src_pos, d.demand / f.control_drivers_factor
FROM baseline_windows b
JOIN daily_demand d
  ON d.year = b.src_year
 AND d.week = b.src_week
 AND d.brand = 'control'
JOIN day_factors f
  ON f.sales_date = d.sales_date;

DROP TABLE IF EXISTS control_rule_values;
CREATE TABLE control_rule_values AS
SELECT * FROM rule_values_v;

-- the neutral level per weekday (l_day) and how the rule got there
DROP TABLE IF EXISTS control_baseline_day;
CREATE TABLE control_baseline_day AS
SELECT target_year, target_week, weekday,
       AVG(CASE WHEN status = 'used' THEN value END) AS l_day,
       MAX(threshold) AS threshold,
       MAX(n)         AS n,
       MAX(n_out)     AS n_out
FROM control_rule_values
GROUP BY target_year, target_week, weekday;
