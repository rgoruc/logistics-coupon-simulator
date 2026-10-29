/* =====================================================================================================
   03  Demand = sales / fill rate of that week
   -----------------------------------------------------------------------------------------------------
   Weeks with empty shelves would otherwise pull the baseline down. Closed days (Sundays, public holidays)
   have no rows, so they simply drop out of every later step; the closed hours of a short day
   (24 December from 14:00) are left out here.
   ===================================================================================================== */

DROP TABLE IF EXISTS hourly_demand;
CREATE TABLE hourly_demand AS
SELECT h.sales_date,
       h.year,
       h.week,
       h.weekday,
       h.hour,
       h.brand,
       h.units,
       h.units / CASE WHEN h.brand = 'test' THEN w.test_fill_rate ELSE w.control_fill_rate END AS demand
FROM hourly_sales h
JOIN weekly w
  ON w.year = h.year
 AND w.week = h.week
JOIN day_factors f
  ON f.sales_date = h.sales_date
WHERE f.open = 1
  AND h.hour < f.close_hour;

DROP TABLE IF EXISTS daily_demand;
CREATE TABLE daily_demand AS
SELECT sales_date, year, week, weekday, brand,
       SUM(units)  AS units,
       SUM(demand) AS demand
FROM hourly_demand
GROUP BY sales_date, year, week, weekday, brand;
