/* =====================================================================================================
   04  Which weeks each week's baseline comes from (rolling window, frozen during a coupon)
   -----------------------------------------------------------------------------------------------------
   - Every week gets the 6 latest weeks before it that have data and are not a Deo Test Brand coupon week or
     a dip week after a coupon.
   - During a coupon and its dip the window is frozen at the 6 weeks before the coupon start, so the
     coupon cannot leak into its own baseline.
   - The plan gets one extra "target": the first week after the data (its window = the frozen plan baseline).
   ===================================================================================================== */

-- every week in time order, plus the first plan week
DROP TABLE IF EXISTS week_positions;
CREATE TABLE week_positions AS
SELECT year, week,
       ROW_NUMBER() OVER (ORDER BY year, week) AS pos,
       1 AS has_data
FROM weekly;

INSERT INTO week_positions (year, week, pos, has_data)
SELECT p.plan_year,
       MAX(w.week) + 1,
       (SELECT MAX(pos) FROM week_positions) + 1,
       0
FROM weekly w
CROSS JOIN model_params p
WHERE w.year = p.plan_year
GROUP BY p.plan_year;

-- weeks of Deo Test Brand's own coupons and the dip weeks after them
DROP TABLE IF EXISTS own_weeks;
CREATE TABLE own_weeks AS
SELECT w.year,
       w.week,
       c.year       AS coupon_year,
       c.start_week AS coupon_start_week,
       CASE WHEN w.week BETWEEN c.start_week AND c.start_week + c.full_weeks - 1
              OR w.week = c.partial_week THEN 'coupon' ELSE 'dip' END AS period
FROM week_positions w
JOIN own_coupons c
  ON w.year = c.year
 AND w.week BETWEEN c.start_week AND COALESCE(c.partial_week, c.start_week + c.full_weeks - 1) + c.dip_weeks;

-- target week -> the weeks of its baseline window
DROP TABLE IF EXISTS baseline_windows;
CREATE TABLE baseline_windows AS
WITH targets AS (
  SELECT t.year AS target_year,
         t.week AS target_week,
         t.pos  AS target_pos,
         COALESCE(s.pos, t.pos) AS anchor_pos          -- frozen at the coupon start during a coupon and its dip
  FROM week_positions t
  LEFT JOIN own_weeks o
    ON o.year = t.year AND o.week = t.week
  LEFT JOIN week_positions s
    ON s.year = o.coupon_year AND s.week = o.coupon_start_week
),
candidates AS (
  SELECT t.target_year, t.target_week, t.target_pos,
         c.year AS src_year, c.week AS src_week, c.pos AS src_pos,
         ROW_NUMBER() OVER (PARTITION BY t.target_year, t.target_week ORDER BY c.pos DESC) AS k
  FROM targets t
  JOIN week_positions c
    ON c.pos < t.anchor_pos
   AND c.has_data = 1
  LEFT JOIN own_weeks o
    ON o.year = c.year AND o.week = c.week
  WHERE o.year IS NULL
)
SELECT c.target_year, c.target_week, c.target_pos, c.src_year, c.src_week, c.src_pos
FROM candidates c
CROSS JOIN model_params p
WHERE c.k <= p.window_weeks;
