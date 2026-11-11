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


def test_meteo_fetches_the_served_parts_models():
    """ICON for today and the next two days (the closest single model one day ahead: reports/2026-09_weather_models.html),
    ECMWF 9 km from three days ahead since 2026-11-10 (STAGES_DESIGN Part B 44); one call fetches both. ICON alone
    2026-09-30 → 11-10; ECMWF 2026-09-04 → 09-30."""
    assert ld.WEATHER_MODEL == "icon_ecmwf_ifs_d3" and ld.METEO_PARAMS["models"] == "icon_seamless,ecmwf_ifs"
    assert [ld._LU.weather_at(ld.WEATHER_MODEL, L) for L in range(6)] == ["icon_seamless"] * 3 + ["ecmwf_ifs"] * 3


TODAY = pd.Timestamp("2026-11-10")


def _response(icon_mm=0.1, ecmwf_mm=0.2, days=(-2, 6)):
    """A two-model Open-Meteo response over TODAY + days[0] … TODAY + days[1] − 1, hourly: each model's rain, wind
    and temperature constant, so a day shows whose it is."""
    t = pd.date_range(TODAY + pd.Timedelta(days=days[0]), TODAY + pd.Timedelta(days=days[1]) - pd.Timedelta(hours=1), freq="h")
    h = {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t]}
    for m, mm, spd in (("icon_seamless", icon_mm, 10.0), ("ecmwf_ifs", ecmwf_mm, 20.0)):
        h[f"precipitation_{m}"] = [mm] * len(t)
        h[f"wind_speed_10m_{m}"] = [spd] * len(t)
        h[f"wind_direction_10m_{m}"] = [180.0] * len(t)
        h[f"temperature_2m_{m}"] = [12.0] * len(t)
    return {"hourly": h}


def test_each_day_reads_its_leads_model():
    """Today and the next two days (and the past days) from ICON, days 3–5 from ECMWF 9 km: rain, wind and all."""
    fr = ld.weather_frames(_response(), ld._LU.weathers_of(ld.WEATHER_MODEL))
    sp = ld.splice_by_lead(fr, ld.WEATHER_MODEL, TODAY.date())
    by_day = sp.groupby(sp["timestamp"].dt.normalize())
    model = by_day["weather_model"].agg(lambda x: set(x))
    for d, ms in model.items():
        lead = (d - TODAY).days
        want = "ecmwf_ifs" if lead >= 3 else "icon_seamless"
        assert ms == {want}, (d, ms)
        rain = by_day.get_group(d)
        assert np.allclose(rain["precip_mm"], 0.2 if lead >= 3 else 0.1) and np.allclose(rain["wind_speed_kmh"], 20.0 if lead >= 3 else 10.0), d
    assert np.allclose(sp["precip_inches"], sp["precip_mm"] / 25.4)


def test_a_day_whose_model_lacks_a_rain_hour_reads_lead_0s():
    """A missing hour is never a dry one: that whole day reads ICON, and the frame says so."""
    r = _response()
    t = r["hourly"]["time"].index((TODAY + pd.Timedelta(days=4, hours=7)).strftime("%Y-%m-%dT%H:%M"))
    r["hourly"]["precipitation_ecmwf_ifs"][t] = None
    sp = ld.splice_by_lead(ld.weather_frames(r, ("icon_seamless", "ecmwf_ifs")), ld.WEATHER_MODEL, TODAY.date())
    day = sp.groupby(sp["timestamp"].dt.normalize())["weather_model"].first()
    assert day[TODAY + pd.Timedelta(days=4)] == "icon_seamless"
    assert day[TODAY + pd.Timedelta(days=3)] == day[TODAY + pd.Timedelta(days=5)] == "ecmwf_ifs"
    assert not sp["precip_mm"].isna().any()


def test_a_single_models_response_reads_plain_names():
    t = ["2026-11-10T00:00", "2026-11-10T01:00"]
    fr = ld.weather_frames({"hourly": {"time": t, "precipitation": [0.0, 2.54], "wind_speed_10m": [5, 6]}}, ["icon_seamless"])
    f = fr["icon_seamless"]
    assert list(f["precip_inches"]) == [0.0, 0.1] and f["temp_c"].isna().all() and list(f["wind_speed_kmh"]) == [5.0, 6.0]
    try:
        ld.weather_frames({"hourly": {"time": t}}, ["icon_seamless", "ecmwf_ifs"])
    except ValueError:
        pass
    else:
        raise AssertionError("a response without a model's rain must raise")


def test_a_day_from_ecmwf_takes_its_peaks_from_ecmwfs_own_evening():
    """The 3- and 6-hour windows run across midnight: day 3's read ECMWF 9 km's last hours of day 2 (as the build's
    lead-3 rows do), not ICON's, which the spliced frame holds there."""
    r = _response(icon_mm=0.0, ecmwf_mm=0.0)
    late = (TODAY + pd.Timedelta(days=2, hours=23)).strftime("%Y-%m-%dT%H:%M")
    first = (TODAY + pd.Timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    i, j = r["hourly"]["time"].index(late), r["hourly"]["time"].index(first)
    r["hourly"]["precipitation_ecmwf_ifs"][i] = r["hourly"]["precipitation_ecmwf_ifs"][j] = 25.4   # 1" each, ECMWF only
    fr = ld.weather_frames(r, ("icon_seamless", "ecmwf_ifs"))
    sp = ld.splice_by_lead(fr, ld.WEATHER_MODEL, TODAY.date())
    sp["date"] = sp["timestamp"].dt.date
    live = ld.LiveData.__new__(ld.LiveData)
    live._weather_hours = fr
    pk = live._peaks_by_lead(sp).set_index("date")
    d3 = TODAY + pd.Timedelta(days=3)
    assert abs(ld._hourly_intensity(sp).set_index("date").at[d3, "rain_max3h"] - 1.0) < 1e-9     # the splice: ICON's dry evening
    assert abs(pk.at[d3, "rain_max3h"] - 2.0) < 1e-9 and abs(pk.at[d3, "rain_max1h"] - 1.0) < 1e-9
    assert pk.at[TODAY + pd.Timedelta(days=2), "rain_max3h"] == 0.0                              # day 2 is ICON's, dry
    # a hindcast frame names no model per hour: its peaks are its own
    h = sp.drop(columns="weather_model")
    assert live._peaks_by_lead(h).equals(ld._hourly_intensity(h))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
