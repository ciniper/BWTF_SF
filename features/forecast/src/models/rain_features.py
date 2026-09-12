"""Rain → model features. THE one implementation, used by training
(train_v2 / train_v4) and serving (live_dashboard) alike.

Before 2026-09-10 the same arithmetic lived in three places with "keep in
lockstep" comments doing the guarding. A silent divergence would feed the
models numbers they were not trained on and shift every risk percentage
with no error anywhere — so the formula lives here and
`tests/test_feature_parity.py` pins both callers to it.

Daily features (`add_daily_features`), from one daily-total series:
    precip_avg                today's total (inches)
    rain_{2,3,5,7,14,30}d_cum trailing sums including today
    rain_lag{1,2,3,5,7}d      the total N days ago
    antecedent_moisture       14-day exponentially weighted mean, 3-day half-life
    wet_prior_3d              1 if the prior 3 days (excluding today) summed > 0.1"
    peak_3d                   max daily total over the trailing 3 days
    dry_spell_days            consecutive days < 0.05" ending today (0 on a wet day)

Hourly intensity (`hourly_intensity`), from an hourly series:
    rain_max1h / rain_max3h / rain_max6h — each calendar day's peak 1/3/6-hour
    total; the rolling windows run across midnight before the daily max.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAILY_FEATURES = [
    "precip_avg",
    "rain_2d_cum", "rain_3d_cum", "rain_5d_cum", "rain_7d_cum", "rain_14d_cum", "rain_30d_cum",
    "rain_lag1d", "rain_lag2d", "rain_lag3d", "rain_lag5d", "rain_lag7d",
    "antecedent_moisture", "wet_prior_3d", "peak_3d", "dry_spell_days",
]
INTENSITY_FEATURES = ["rain_max1h", "rain_max3h", "rain_max6h"]
ALL_FEATURES = DAILY_FEATURES + INTENSITY_FEATURES

_HALF_LIFE_DAYS = 3
_AM_WINDOW = 14
_AM_WEIGHTS = np.exp(-np.log(2) / _HALF_LIFE_DAYS * np.arange(_AM_WINDOW))


def _antecedent(x: np.ndarray) -> float:
    w = _AM_WEIGHTS[: len(x)][::-1]
    return float(np.sum(x * w) / np.sum(w))


def add_daily_features(daily: pd.DataFrame, col: str = "precip_inches") -> pd.DataFrame:
    """Return a copy of `daily` (one row per consecutive calendar day, sorted,
    with a daily rain total in `col`) with every DAILY_FEATURES column added.
    `precip_avg` is the model's name for the daily total."""
    out = daily.copy()
    rain = out[col].astype(float).fillna(0.0)
    out["precip_avg"] = rain
    for w in (2, 3, 5, 7, 14, 30):
        out[f"rain_{w}d_cum"] = rain.rolling(window=w, min_periods=1).sum()
    for lag in (1, 2, 3, 5, 7):
        out[f"rain_lag{lag}d"] = rain.shift(lag).fillna(0.0)
    out["antecedent_moisture"] = rain.rolling(window=_AM_WINDOW, min_periods=1).apply(_antecedent, raw=True)
    out["wet_prior_3d"] = (out["rain_3d_cum"].shift(1).fillna(0.0) > 0.1).astype(int)
    out["peak_3d"] = rain.rolling(window=3, min_periods=1).max()
    is_dry = (rain < 0.05).astype(int)
    out["dry_spell_days"] = is_dry.groupby(is_dry.ne(is_dry.shift()).cumsum()).cumsum()
    return out


def hourly_intensity(hourly: pd.DataFrame, ts_col: str = "timestamp", col: str = "precip_inches") -> pd.DataFrame:
    """Per-calendar-day peak 1h / 3h / 6h rain from an hourly series; returns
    columns [date, rain_max1h, rain_max3h, rain_max6h] with `date` at midnight."""
    h = hourly[[ts_col, col]].copy().sort_values(ts_col).reset_index(drop=True)
    h[col] = h[col].astype(float).fillna(0.0)
    for w in (3, 6):
        h[f"roll{w}h"] = h[col].rolling(w, min_periods=1).sum()
    h["date"] = pd.to_datetime(h[ts_col]).dt.normalize()
    return h.groupby("date").agg(
        rain_max1h=(col, "max"), rain_max3h=("roll3h", "max"), rain_max6h=("roll6h", "max"),
    ).reset_index()
