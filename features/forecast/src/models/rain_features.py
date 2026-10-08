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

Wind (`wind_rain_features`), from an hourly series of rain and 10 m wind:
    wind_v_rain               the day's rain-weighted south → north wind, m/s
                              (positive: from the south; 0 on a day under
                              WIND_WET_DAY_IN of rain, which has no rain to
                              weigh the wind by). Read only by a model whose
                              `features` name it; the 19 above are unchanged.
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
WIND_FEATURES = ["wind_v_rain"]     # extra inputs: a model reads them only when its `features` list does
WIND_WET_DAY_IN = 0.005

_HALF_LIFE_DAYS = 3
_AM_WINDOW = 14
_AM_WEIGHTS = np.exp(-np.log(2) / _HALF_LIFE_DAYS * np.arange(_AM_WINDOW))


def _antecedent(x: np.ndarray) -> float:
    w = _AM_WEIGHTS[: len(x)][::-1]
    return float(np.sum(x * w) / np.sum(w))


# ── gauge outage rule ────────────────────────────────────────────────────────
# ACIS reports a dead NOAA gauge as 0.00, not "M": Oceanside 047767 sat at
# exactly 0.00 from Jan 29 to Feb 25 2026 while Downtown 047772 accumulated
# 4.37", and the record has eight such Oceanside runs and four Downtown runs
# since 2018. Westside and Southeast run on the two-gauge mean, so they saw
# half the rain (the Feb 16 and Feb 19 2026 Westside discharges scored 24%
# and 1%). The rule: a run of ≥ MIN_RUN_DAYS consecutive exactly-0.00 days at
# one gauge while the other gauge totals ≥ OTHER_GAUGE_MIN_IN over the run is
# an outage → those days become missing at the dead gauge and the ordinary
# fill (the other gauge) takes over. Single dry coastal days while downtown
# is wet are real and are deliberately NOT touched.
GAUGE_OUTAGE_RULE = {"name": "gauge_outage_v1", "min_run_days": 2, "other_gauge_min_in": 0.5}
INPUT_RULES_LIVE = [GAUGE_OUTAGE_RULE["name"]]   # what serving and post-training rescoring apply
# gauge_outage_v2 (2026-10-08, RAIN_SOURCES.md; STAGES_DESIGN Part B 40): the same runs as v1, but a dead (or missing)
# gauge day reads MRMS's 24-hour multisensor total at that gauge's cell where MRMS has the day (Oct 2020 on), and the
# other gauge only before that. Opt-in: a set names it (its manifest's input_rules), nothing served reads it, and the
# live page cannot apply it until MRMS reaches it (INPUT_RULES_SERVABLE; TODO "MRMS on the live board").
GAUGE_OUTAGE_V2 = {"name": "gauge_outage_v2", "runs": GAUGE_OUTAGE_RULE["name"], "fill": "mrms"}
INPUT_RULES_KNOWN = (GAUGE_OUTAGE_RULE["name"], GAUGE_OUTAGE_V2["name"])
INPUT_RULES_SERVABLE = (GAUGE_OUTAGE_RULE["name"],)   # the rules live_dashboard applies today


def masks_outages(rules) -> bool:
    """True when ``rules`` blank gauge_outage_v1's runs (v1 itself, or v2, which fills the same runs from MRMS)."""
    return any(r in INPUT_RULES_KNOWN for r in (rules or ()))


def find_gauge_outages(daily: pd.DataFrame, gauges=("SF Downtown", "SF Oceanside"), date_col: str = "date",
                       min_run_days: int = GAUGE_OUTAGE_RULE["min_run_days"],
                       other_min: float = GAUGE_OUTAGE_RULE["other_gauge_min_in"]) -> list[dict]:
    """Outage runs in a daily table with one column per gauge (NaN = missing).
    Each run: {gauge, start, end, days, other_total}. Dates must be daily and
    sorted; gaps end a run."""
    df = daily.sort_values(date_col).reset_index(drop=True)
    runs = []
    for g in gauges:
        if g not in df:
            continue
        others = [o for o in gauges if o != g and o in df]
        if not others:
            continue
        start = None
        prev_date = None
        for i in range(len(df) + 1):
            row = df.iloc[i] if i < len(df) else None
            zero = row is not None and pd.notna(row[g]) and float(row[g]) == 0.0
            contiguous = row is not None and (prev_date is None or (row[date_col] - prev_date).days == 1)
            if zero and contiguous and start is not None:
                pass                                        # run continues
            else:
                if start is not None:                       # a run just ended (at i-1)
                    seg = df.iloc[start:i]
                    tot = float(seg[others].sum(axis=1).sum())
                    if len(seg) >= min_run_days and tot >= other_min:
                        runs.append({"gauge": g, "start": str(seg[date_col].iloc[0].date()), "end": str(seg[date_col].iloc[-1].date()),
                                     "days": int(len(seg)), "other_total": round(tot, 2)})
                    start = None
                if zero:
                    start = i
            prev_date = row[date_col] if row is not None else None
    return sorted(runs, key=lambda r: (r["start"], r["gauge"]))


def mask_gauge_outages(daily: pd.DataFrame, gauges=("SF Downtown", "SF Oceanside"), date_col: str = "date", **kw) -> tuple:
    """(copy of `daily` with the dead gauge's run days set to NaN, the runs)."""
    runs = find_gauge_outages(daily, gauges, date_col, **kw)
    out = daily.copy()
    for r in runs:
        m = (out[date_col] >= pd.Timestamp(r["start"])) & (out[date_col] <= pd.Timestamp(r["end"]))
        out.loc[m, r["gauge"]] = np.nan
    return out, runs


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


def wind_uv(speed, direction) -> tuple:
    """(u, v): the west → east and south → north parts of a wind of ``speed`` blowing FROM ``direction``
    degrees (meteorological: 270 is a west wind, so u > 0; 180 is a south wind, so v > 0)."""
    th = np.radians(np.asarray(direction, dtype=float))
    spd = np.asarray(speed, dtype=float)
    return -spd * np.sin(th), -spd * np.cos(th)


def wind_rain_features(hourly: pd.DataFrame, ts_col: str = "timestamp", col: str = "precip_inches",
                       speed_col: str = "wind_speed_ms", dir_col: str = "wind_dir_deg") -> pd.DataFrame:
    """Per calendar day: wind_v_rain = Σ rain·v / Σ rain over the day's hours (v from ``wind_uv``, m/s), 0 on a
    day with under WIND_WET_DAY_IN of rain. Returns [date, wind_v_rain] with `date` at midnight. A missing rain
    hour weighs nothing (hourly_intensity reads it as 0); on a wet day, a missing wind hour that rained makes the
    day NaN, never a calm hour."""
    h = hourly[[ts_col, col, speed_col, dir_col]].copy().sort_values(ts_col).reset_index(drop=True)
    r = h[col].astype(float).fillna(0.0).to_numpy()
    _u, v = wind_uv(h[speed_col], h[dir_col])
    rv = np.where(r > 0, r * v, 0.0)                        # NaN only where it rained and the wind is missing
    d = pd.DataFrame({"date": pd.to_datetime(h[ts_col]).dt.normalize(), "r": r, "rv": np.nan_to_num(rv), "gap": np.isnan(rv)})
    g = d.groupby("date").agg(tot=("r", "sum"), rv=("rv", "sum"), gap=("gap", "any"))
    wet = g["tot"] >= WIND_WET_DAY_IN
    out = np.where(wet, np.where(g["gap"], np.nan, g["rv"] / g["tot"].where(wet, 1.0)), 0.0)
    return pd.DataFrame({"date": g.index, "wind_v_rain": out}).reset_index(drop=True)
