"""Observed-rain overlay for the forecast dashboard (live_dashboard.py).

Past hours must come from the SFO gauge when available, fall back to the
model when the gauge day is unknown, and never touch future hours.
No network: the NWS fetch is stubbed. Runs under pytest or directly:

    venv/bin/python tests/test_rain_overlay.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from features.forecast import live_dashboard as ld  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0)


def _feat(ts_utc: str, mm):
    return {"properties": {"timestamp": ts_utc,
                           "precipitationLastHour": None if mm is None else {"value": mm}}}


def _overlay(df, fetch):
    with patch.object(ld, "_now_local", lambda: NOW), \
         patch.object(ld.LiveData, "_fetch_nws_observed_rain", fetch):
        return ld.LiveData.__new__(ld.LiveData)._overlay_observed_rain(df)


def test_observed_hourly_buckets_to_hour_ending_and_keeps_max():
    feats = [
        _feat("2026-09-04T03:56:00+00:00", 0.3),   # 20:56 PT -> hour ending 21:00
        _feat("2026-09-04T03:24:00+00:00", 0.2),   # special, same water -> max, not sum
        _feat("2026-09-04T05:00:00+00:00", 1.5),   # exactly on the hour -> 22:00 PT bucket
        _feat("2026-09-04T06:56:00+00:00", None),  # dry hours report null, ignored
    ]
    out = ld.observed_hourly_from_features(feats)
    assert out == {datetime(2026, 9, 3, 21): 0.3, datetime(2026, 9, 3, 22): 1.5}


def test_overlay_replaces_past_keeps_model_for_unknown_and_future():
    hours = pd.date_range(NOW - timedelta(hours=5), NOW + timedelta(hours=2), freq="h")
    df = pd.DataFrame({"timestamp": hours, "precip_mm": 1.0})
    df["precip_inches"] = df["precip_mm"] / 25.4
    # gauge knows the first three past hours (0, 2.54, 0); the two hours before
    # 'now' are unknown (gauge outage) and must keep the model's 1.0
    obs = pd.Series({hours[0].to_pydatetime(): 0.0,
                     hours[1].to_pydatetime(): 2.54,
                     hours[2].to_pydatetime(): 0.0})
    out = _overlay(df, lambda self, a, b: obs)
    assert list(out["rain_source"]) == ["observed"] * 3 + ["model"] * 3 + ["forecast"] * 2
    assert list(out["precip_mm"]) == [0.0, 2.54, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert np.isclose(out["precip_inches"].iloc[1], 0.1)


def test_overlay_survives_gauge_failure():
    df = pd.DataFrame({"timestamp": pd.date_range(NOW - timedelta(hours=2), NOW, freq="h"),
                       "precip_mm": [0.5, 0.5, 0.5]})
    df["precip_inches"] = df["precip_mm"] / 25.4

    def boom(self, a, b):
        raise RuntimeError("api.weather.gov down")
    out = _overlay(df, boom)
    assert list(out["precip_mm"]) == [0.5, 0.5, 0.5]
    assert set(out["rain_source"]) == {"model"}


def test_meteo_uses_ecmwf():
    assert ld.METEO_PARAMS["models"] == "ecmwf_ifs025"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
