/* =====================================================================================================
   07  Deo Test Brand's neutral baseline L, per target week, weekday and hour
   -----------------------------------------------------------------------------------------------------
   Before the rule every hour is put on a neutral day: the drivers of its day are divided out.
       neutral value = demand / drivers_factor of that day
   drivers_factor = season x calendar x weather x price (against the category price index) (table
   day_factors). So L = "Deo Test Brand on a neutral day": average season, no holiday nearby, normal
   weather, normal prices.
   ===================================================================================================== */

-- every hour of the window weeks, on a neutral day
DROP TABLE IF EXISTS test_neutral_values;
CREATE TABLE test_neutral_values AS
SELECT b.target_year, b.target_week, h.weekday, h.hour,
       b.src_year, b.src_week, b.src_pos, h.sales_date,
       h.demand,
       f.drivers_factor,
       h.demand / f.drivers_factor AS neutral_value
FROM baseline_windows b
JOIN hourly_demand h
  ON h.year = b.src_year AND h.week = b.src_week AND h.brand = 'test'
JOIN day_factors f
  ON f.sales_date = h.sales_date;

DELETE FROM rule_input;
INSERT INTO rule_input (target_year, target_week, weekday, hour, src_pos, value)
SELECT target_year, target_week, weekday, hour, src_pos, neutral_value
FROM test_neutral_values;

DROP TABLE IF EXISTS test_rule_values;
CREATE TABLE test_rule_values AS
SELECT * FROM rule_values_v;

DROP TABLE IF EXISTS test_baseline_cells;
CREATE TABLE test_baseline_cells AS
SELECT target_year, target_week, weekday, hour,
       AVG(CASE WHEN status = 'used' THEN value END) AS baseline_level,
       MAX(threshold) AS threshold,
       MAX(n)         AS n,
       MAX(n_out)     AS n_out
FROM test_rule_values
GROUP BY target_year, target_week, weekday, hour;

-- the neutral level per weekday: all hours (l_day) and the hours the store is open on that date (l_open)
DROP TABLE IF EXISTS test_baseline_day;
CREATE TABLE test_baseline_day AS
SELECT c.target_year, c.target_week, c.weekday,
       SUM(c.baseline_level)                                                  AS l_day,
       SUM(CASE WHEN c.hour < f.close_hour THEN c.baseline_level ELSE 0 END) AS l_open
FROM test_baseline_cells c
JOIN day_factors f
  ON f.year = c.target_year AND f.week = c.target_week AND f.weekday = c.weekday
GROUP BY c.target_year, c.target_week, c.weekday;
