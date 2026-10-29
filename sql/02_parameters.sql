/* =====================================================================================================
   02  Parameters of the model
   -----------------------------------------------------------------------------------------------------
   The drivers (season, calendar, weather, price against the category price index) are fitted in Python
   (model/drivers.py, a regression on the years before the plan year) and come into SQL as one factor per
   day: the table day_factors of step 01. The numbers below are your baseline rule; they match config.py.
   ===================================================================================================== */

DROP TABLE IF EXISTS model_params;
CREATE TABLE model_params AS
SELECT 6                                  AS window_weeks,        -- baseline window: the last 6 weeks
       CAST(2.0 AS FLOAT)                 AS sd_multiplier,       -- threshold = middle value + 2 x sd + 2
       CAST(2.0 AS FLOAT)                 AS threshold_constant,
       6                                  AS trim_divisor,        -- sd of the middle ranks: round(n/6) left out at each end
       1                                  AS half_window,         -- baseline = average of ranks c-1 ... c+1
       2                                  AS half_window_large,   -- c-2 ... c+2 when there are at least large_n values
       20                                 AS large_n,
       2026                               AS plan_year;
