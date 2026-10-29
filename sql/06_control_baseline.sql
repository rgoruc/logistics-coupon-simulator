/* =====================================================================================================
   06  The control brand's neutral baseline (Deo Control Brand), per target week, weekday and hour
   -----------------------------------------------------------------------------------------------------
   Same rule as for Deo Test Brand (step 07), on the control's neutral values:
       neutral value = demand / control_drivers_factor of that day
   The control is not part of Deo Test Brand's counterfactual any more (its daily deviation is one of the
   six drivers, fitted in Python). Its own counterfactual in step 08 is a cross-check: in the coupon weeks
   the control should stay on its normal path (no sign that the coupon took sales from it).
   ===================================================================================================== */

DELETE FROM rule_input;
INSERT INTO rule_input (target_year, target_week, weekday, hour, src_pos, value)
SELECT b.target_year, b.target_week, d.weekday, d.hour, b.src_pos, d.demand / f.control_drivers_factor
FROM baseline_windows b
JOIN hourly_demand d
  ON d.year = b.src_year
 AND d.week = b.src_week
 AND d.brand = 'control'
JOIN day_factors f
  ON f.sales_date = d.sales_date;

DROP TABLE IF EXISTS control_rule_values;
CREATE TABLE control_rule_values AS
SELECT * FROM rule_values_v;

DROP TABLE IF EXISTS control_baseline_cells;
CREATE TABLE control_baseline_cells AS
SELECT target_year, target_week, weekday, hour,
       AVG(CASE WHEN status = 'used' THEN value END) AS baseline_level,
       MAX(threshold) AS threshold,
       MAX(n)         AS n,
       MAX(n_out)     AS n_out
FROM control_rule_values
GROUP BY target_year, target_week, weekday, hour;

-- the neutral level per weekday: all hours (l_day) and the hours the store is open on that date (l_open)
DROP TABLE IF EXISTS control_baseline_day;
CREATE TABLE control_baseline_day AS
SELECT c.target_year, c.target_week, c.weekday,
       SUM(c.baseline_level)                                                  AS l_day,
       SUM(CASE WHEN c.hour < f.close_hour THEN c.baseline_level ELSE 0 END) AS l_open
FROM control_baseline_cells c
JOIN day_factors f
  ON f.year = c.target_year AND f.week = c.target_week AND f.weekday = c.weekday
GROUP BY c.target_year, c.target_week, c.weekday;
