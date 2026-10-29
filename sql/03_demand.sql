/* =====================================================================================================
   03  Demand = sales / fill rate of that week
   -----------------------------------------------------------------------------------------------------
   Weeks with empty shelves would otherwise pull the baseline down. Closed days (Sundays, public holidays)
   have no rows, so they simply drop out of every later step.
   ===================================================================================================== */

DROP TABLE IF EXISTS daily_demand;
CREATE TABLE daily_demand AS
SELECT d.sales_date,
       d.year,
       d.week,
       d.weekday,
       d.brand,
       d.units,
       d.units / CASE WHEN d.brand = 'test' THEN w.test_fill_rate ELSE w.control_fill_rate END AS demand
FROM daily_sales d
JOIN weekly w
  ON w.year = d.year
 AND w.week = d.week
JOIN day_factors f
  ON f.sales_date = d.sales_date
WHERE f.open = 1;
