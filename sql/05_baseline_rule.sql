/* =====================================================================================================
   05  Your baseline rule, written once as a view
   -----------------------------------------------------------------------------------------------------
   Input:  table rule_input (target_year, target_week, weekday, hour, src_pos, value)
           = for one target week, one weekday and hour: the values of the window weeks.
   Output: view rule_values_v = every value with its rank and the rule's verdict
           ('outlier', 'used', 'not used'); the baseline of a cell = AVG(value) WHERE status = 'used'.

   The rule, for the n values of one cell:
     1. rank from high to low (ties keep time order)
     2. threshold = value at rank floor(n/2)+1
                    + sd_multiplier x population sd of the middle ranks (round(n/6) left out at each end)
                    + threshold_constant;  values above it are outliers
     3. c = n - floor((n - outliers) / 2)           -- median rank of the values that are left
     4. used = ranks max(outliers+1, c-1) ... min(n, c+1)   (+/- 2 when n >= large_n)
   Steps 06 and 07 fill rule_input (first the control brand, then Deo Test Brand) and read this view.
   ===================================================================================================== */

DROP TABLE IF EXISTS rule_input;
CREATE TABLE rule_input (
  target_year INT,
  target_week INT,
  weekday     INT,
  hour        INT,
  src_pos     INT,
  value       FLOAT
);

DROP VIEW IF EXISTS rule_values_v;
CREATE VIEW rule_values_v AS
WITH ranked AS (
  SELECT i.*,
         ROW_NUMBER() OVER (PARTITION BY target_year, target_week, weekday, hour ORDER BY value DESC, src_pos ASC) AS rnk,
         COUNT(*)     OVER (PARTITION BY target_year, target_week, weekday, hour) AS n
  FROM rule_input i
),
trimmed AS (
  SELECT r.*,
         MAX(CASE WHEN r.rnk = FLOOR(r.n / 2.0) + 1 THEN r.value END)
           OVER (PARTITION BY r.target_year, r.target_week, r.weekday, r.hour) AS mid,
         CASE WHEN ROUND(r.n / (p.trim_divisor * 1.0)) > 1 THEN ROUND(r.n / (p.trim_divisor * 1.0)) ELSE 1 END AS k
  FROM ranked r
  CROSS JOIN model_params p
),
middle_mean AS (
  SELECT t.*,
         AVG(CASE WHEN t.rnk > t.k AND t.rnk <= t.n - t.k THEN t.value END)
           OVER (PARTITION BY t.target_year, t.target_week, t.weekday, t.hour) AS m,
         SUM(CASE WHEN t.rnk > t.k AND t.rnk <= t.n - t.k THEN 1 ELSE 0 END)
           OVER (PARTITION BY t.target_year, t.target_week, t.weekday, t.hour) AS n_middle
  FROM trimmed t
),
middle_var AS (
  SELECT mm.*,
         AVG(CASE WHEN mm.rnk > mm.k AND mm.rnk <= mm.n - mm.k THEN (mm.value - mm.m) * (mm.value - mm.m) END)
           OVER (PARTITION BY mm.target_year, mm.target_week, mm.weekday, mm.hour) AS var_middle
  FROM middle_mean mm
),
thresholds AS (
  SELECT v.*,
         v.mid + p.sd_multiplier * CASE WHEN v.n_middle > 1 THEN SQRT(v.var_middle) ELSE 0 END + p.threshold_constant AS threshold
  FROM middle_var v
  CROSS JOIN model_params p
),
outliers AS (
  SELECT t.*,
         SUM(CASE WHEN t.value > t.threshold THEN 1 ELSE 0 END)
           OVER (PARTITION BY t.target_year, t.target_week, t.weekday, t.hour) AS n_out
  FROM thresholds t
),
center AS (
  SELECT o.*,
         o.n - FLOOR((o.n - o.n_out) / 2.0) AS c,
         CASE WHEN o.n >= p.large_n THEN p.half_window_large ELSE p.half_window END AS hw
  FROM outliers o
  CROSS JOIN model_params p
)
SELECT target_year, target_week, weekday, hour, src_pos, value, rnk, n, mid, threshold, n_out, c,
       CASE
         WHEN rnk <= n_out THEN 'outlier'
         WHEN rnk >= CASE WHEN n_out + 1 > c - hw THEN n_out + 1 ELSE c - hw END
          AND rnk <= CASE WHEN n < c + hw THEN n ELSE c + hw END THEN 'used'
         ELSE 'not used'
       END AS status
FROM center;
