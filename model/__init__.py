"""Logistics Coupon Simulator model: baseline rule, counterfactual, coupon measurement and plan.

Typical use:
    from model import load, calibrate, default_scenario, forecast
    data = load("data/input")
    cal = calibrate(data)
    sc = default_scenario(cal)
    res = forecast(cal, sc)
"""
from .baseline import RuleSettings, robust_level, baseline_of
from .data import Data, NAME, CONTROL, DAYS, iso_monday, day_key
from .counterfactual import window_for, fit_drivers, Counterfactuals, Drivers
from .drivers import Calendar, Weather, DRIVER_NAMES
from .calibrate import Calibration, calibrate
from .plan import (Scenario, default_scenario, reference_rate, share_for, coupon_plan, forecast, daily, max_full_weeks,
                   COUPON_LENGTHS, LAST_WEEK, length_share, start_weeks, normal_demand, day_shares, weather_factor, day_drivers)
from .supply import deadline, intervals, span_label, shelf_check


def load(folder="data/input") -> Data:
    return Data.load(folder)
