/* =====================================================================================================
   07  Deo Test Brand's neutral baseline L, per target week and weekday
   -----------------------------------------------------------------------------------------------------
   Before the rule every day is put on a neutral footing: the drivers of the day are divided out.
       neutral value = demand / drivers_factor of that day
   drivers_factor = season x calendar x weather x price (against the category price index) (table
   day_factors). So L = "Deo Test Brand on a neutral day": average season, no holiday nearby, a full opening
   day, normal weather, normal prices.
   ===================================================================================================== */

-- every day of the window weeks, on a neutral footing
DROP TABLE IF EXISTS test_neutral_values;
CREATE TABLE test_neutral_values AS
SELECT b.target_year, b.target_week, d.weekday,
       b.src_year, b.src_week, b.src_pos, d.sales_date,
       d.demand,
       f.drivers_factor,
       d.demand / f.drivers_factor AS neutral_value
FROM baseline_windows b
JOIN daily_demand d
  ON d.year = b.src_year AND d.week = b.src_week AND d.brand = 'test'
JOIN day_factors f
  ON f.sales_date = d.sales_date;

DELETE FROM rule_input;
INSERT INTO rule_input (target_year, target_week, weekday, src_pos, value)
SELECT target_year, target_week, weekday, src_pos, neutral_value
FROM test_neutral_values;

DROP TABLE IF EXISTS test_rule_values;
CREATE TABLE test_rule_values AS
SELECT * FROM rule_values_v;

-- the neutral level per weekday (l_day) and how the rule got there
DROP TABLE IF EXISTS test_baseline_day;
CREATE TABLE test_baseline_day AS
SELECT target_year, target_week, weekday,
       AVG(CASE WHEN status = 'used' THEN value END) AS l_day,
       MAX(threshold) AS threshold,
       MAX(n)         AS n,
       MAX(n_out)     AS n_out
FROM test_rule_values
GROUP BY target_year, target_week, weekday;
