/* =====================================================================================================
   01  Snowflake: create the tables and load the CSV files
   -----------------------------------------------------------------------------------------------------
   Run this file once. Steps 02 to 09 then work on these tables.

   Four files: three from data/input and the drivers of every day from data/output (written by
   `python run_model.py`, see step 02).

   Two ways to get the files into the stage:
   A) SnowSQL (command line), from the project folder:
        PUT file://data/input/daily_sales.csv   @coupon_stage AUTO_COMPRESS = TRUE;
        PUT file://data/input/weekly.csv        @coupon_stage AUTO_COMPRESS = TRUE;
        PUT file://data/input/own_coupons.csv   @coupon_stage AUTO_COMPRESS = TRUE;
        PUT file://data/output/day_factors.csv  @coupon_stage AUTO_COMPRESS = TRUE;
      (on Windows write the full path, e.g. file://C:/Users/<you>/Documents/<project folder>/data/input/weekly.csv)
   B) Snowsight (browser): open the stage COUPON_STAGE and upload the four files with "+ Files".
   The COPY commands below find the files either way (compressed or not).
   ===================================================================================================== */

CREATE DATABASE IF NOT EXISTS COUPON_CASE;
CREATE SCHEMA IF NOT EXISTS COUPON_CASE.DEO_TEST;
USE SCHEMA COUPON_CASE.DEO_TEST;

CREATE OR REPLACE FILE FORMAT csv_format
  TYPE = CSV
  FIELD_DELIMITER = ','
  SKIP_HEADER = 1
  FIELD_OPTIONALLY_ENCLOSED_BY = '"'
  EMPTY_FIELD_AS_NULL = TRUE;

CREATE OR REPLACE STAGE coupon_stage FILE_FORMAT = csv_format;

-- daily store sales, one row per day and brand (closed days have no rows)
CREATE OR REPLACE TABLE daily_sales (
  sales_date DATE,
  year       INT,      -- ISO year of the calendar week
  week       INT,      -- ISO calendar week
  weekday    INT,      -- 1 = Monday ... 6 = Saturday
  brand      VARCHAR,  -- 'test' (coupon brand) or 'control' (control brand)
  units      NUMBER(38, 0)
);

-- one row per calendar week: units, fill rate and shelf price of both brands
CREATE OR REPLACE TABLE weekly (
  year             INT,
  week             INT,
  test_units      NUMBER(38, 0),
  test_fill_rate  FLOAT,
  test_price      FLOAT,
  control_units     NUMBER(38, 0),
  control_fill_rate FLOAT,
  control_price     FLOAT,
  category_price    FLOAT,         -- category price index: volume-weighted average shelf price of men's deo
  test_dc_orders  NUMBER(38, 0)   -- distribution centre orders (only for 2025)
);

-- Deo Test Brand's own coupons inside the data: measured, and kept out of the baseline
CREATE OR REPLACE TABLE own_coupons (
  year         INT,
  start_week   INT,
  full_weeks   INT,
  partial_week INT,     -- a last week with Monday and Tuesday only (empty = none)
  dip_weeks    INT,     -- weeks after the coupon that belong to the dip
  discount     FLOAT,
  note         VARCHAR
);

-- the drivers of every day, from the first data day to the end of the plan year (data/output/day_factors.csv).
-- Fitted in Python on the years before the plan year: season, calendar (days before a holiday, the run-up to
-- Christmas, the short day 24 December), weather (temperature anomaly) and price (the brand's price against the
-- category price index).
-- A factor of 1.10 = that driver lifts the day's demand by 10 %.
CREATE OR REPLACE TABLE day_factors (
  sales_date             DATE,
  year                   INT,      -- ISO year of the calendar week
  week                   INT,      -- ISO calendar week
  weekday                INT,      -- 1 = Monday ... 7 = Sunday
  day_name               VARCHAR,
  open                   INT,      -- 1 = the store is open on this day
  close_hour             INT,      -- 20 on a normal day, 14 on 24 December, 0 when closed
  season_factor          FLOAT,
  calendar_factor        FLOAT,
  weather_factor         FLOAT,
  price_factor           FLOAT,
  drivers_factor         FLOAT,    -- product of the four: what the day's demand is against a neutral day
  plan_factor            FLOAT,    -- the plan's version: season x calendar (normal weather, normal prices)
  control_drivers_factor FLOAT,    -- the same product for the control brand (its own drivers)
  temperature_anomaly    FLOAT,    -- daily maximum against the normal of the date, degrees C
  relative_price_test    FLOAT,    -- log(Deo Test Brand price / category price index) against its usual level
  pre_holiday_1          INT,      -- 1 = the last open day before a closed holiday
  pre_holiday_2          INT,      -- 1 = the open day before that
  christmas_ramp         FLOAT,    -- 0 outside December, rising to 1 on 23 December
  short_day              INT       -- 1 = the store closes early (24 December, 14:00)
);

COPY INTO daily_sales  FROM @coupon_stage PATTERN = '.*daily_sales[.]csv.*';
COPY INTO weekly       FROM @coupon_stage PATTERN = '.*weekly[.]csv.*';
COPY INTO own_coupons  FROM @coupon_stage PATTERN = '.*own_coupons[.]csv.*';
COPY INTO day_factors  FROM @coupon_stage PATTERN = '.*day_factors[.]csv.*';

SELECT 'daily_sales' AS table_name, COUNT(*) AS row_count FROM daily_sales
UNION ALL SELECT 'weekly', COUNT(*) FROM weekly
UNION ALL SELECT 'own_coupons', COUNT(*) FROM own_coupons
UNION ALL SELECT 'day_factors', COUNT(*) FROM day_factors;
