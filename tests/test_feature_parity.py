"""Rain-feature parity: training and serving must compute the SAME numbers.

The models are fit on features from src/models/rain_features.py; the live
dashboard builds its daily frame with the same module. If either side ever
re-implements the arithmetic, the models get fed numbers they were not
trained on and every risk percentage shifts with no error anywhere — so this
pins both callers to the shared formula and the formula to known values.

    venv/bin/python tests/test_feature_parity.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (ROOT, FORECAST, FORECAST / "src" / "models", FORECAST / "src" / "collectors"):
    sys.path.insert(0, str(p))

import rain_features as rf  # noqa: E402


def test_known_values():
    rain = [0, 0, 1.0, 0.5, 0, 0, 0.2, 0, 0, 0, 0, 0]
    daily = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(rain)), "precip_inches": rain})
    f = rf.add_daily_features(daily)
    assert list(f["precip_avg"]) == rain
    assert f["rain_2d_cum"].iloc[3] == 1.5 and f["rain_3d_cum"].iloc[3] == 1.5 and f["rain_7d_cum"].iloc[6] == 1.7
    assert f["rain_lag1d"].iloc[3] == 1.0 and f["rain_lag2d"].iloc[4] == 1.0 and f["rain_lag1d"].iloc[0] == 0
    assert f["peak_3d"].iloc[4] == 1.0 and f["peak_3d"].iloc[5] == 0.5
    assert f["wet_prior_3d"].iloc[3] == 1 and f["wet_prior_3d"].iloc[2] == 0   # prior 3 days exclude today
    assert list(f["dry_spell_days"]) == [1, 2, 0, 0, 1, 2, 0, 1, 2, 3, 4, 5]
    assert f["antecedent_moisture"].iloc[2] > f["antecedent_moisture"].iloc[5] > 0
    for c in rf.DAILY_FEATURES:
        assert c in f.columns, c


def test_hourly_intensity_spans_midnight():
    ts = pd.date_range("2026-01-01 21:00", periods=8, freq="h")   # 21:00 → 04:00 next day
    h = pd.DataFrame({"timestamp": ts, "precip_inches": [0.1, 0.2, 0.3, 0.4, 0.1, 0, 0, 0]})
    out = rf.hourly_intensity(h).set_index("date")
    assert out.loc["2026-01-01", "rain_max1h"] == 0.3
    assert abs(out.loc["2026-01-02", "rain_max3h"] - 0.9) < 1e-9   # 23:00 + 00:00 + 01:00 → 0.3+0.4+0.1... window crosses midnight
    assert abs(out.loc["2026-01-02", "rain_max6h"] - 1.1) < 1e-9


def test_training_and_serving_share_the_formula():
    """train_v4's features over the real gauge series == the dashboard's, to
    the bit (requires data/raw, which is gitignored — skipped without it)."""
    if not (FORECAST / "data" / "raw" / "historical_rain.csv").exists():
        print("   (skipped: data/raw not present)"); return
    import train_v4
    from features.forecast import live_dashboard as ld
    s, _ = train_v4.rain_series("avg")
    train = train_v4.rain_features("avg").set_index("date")
    serve = ld.LiveData._add_daily_features(pd.DataFrame({"date": s.index, "precip_inches": s.values})).set_index("date")
    for c in rf.DAILY_FEATURES:
        assert np.array_equal(train[c].values, serve[c].values), c


def test_models_were_trained_on_exactly_these_columns():
    for pkl in (FORECAST / "data" / "models").glob("*_model.pkl"):
        with open(pkl, "rb") as f:
            md = pickle.load(f)
        assert list(md["features"]) == rf.ALL_FEATURES, pkl.name
    for pkl in (FORECAST / "data" / "models").glob("*_volume.pkl"):
        with open(pkl, "rb") as f:
            md = pickle.load(f)
        assert list(md["features"]) == rf.ALL_FEATURES, pkl.name


def test_serving_feature_row_covers_every_model_column():
    from features.forecast import live_dashboard as ld
    daily = rf.add_daily_features(pd.DataFrame({"date": pd.date_range("2026-01-01", periods=3), "precip_inches": [0, 0.4, 0]}))
    daily["rain_max1h"] = daily["rain_max3h"] = daily["rain_max6h"] = daily["precip_max_hourly"] = 0.1
    row = ld.LiveData._features_from_row(daily.iloc[1])
    assert set(rf.ALL_FEATURES) <= set(row), set(rf.ALL_FEATURES) - set(row)
    assert row["precip_avg"] == 0.4 and row["rain_2d_cum"] == 0.4


def test_the_south_wind_means_what_it_says():
    ts = pd.date_range("2026-01-01", periods=48, freq="h")
    h = pd.DataFrame({"timestamp": ts, "precip_inches": 0.0, "wind_speed_ms": 4.0, "wind_dir_deg": 180.0})
    h.loc[2:5, "precip_inches"] = 0.1                          # day 1 wet under a south wind
    h.loc[30, "precip_inches"] = 0.001                         # day 2 under the wet-day floor: calm by definition
    h.loc[29, "wind_speed_ms"] = np.nan                        # a missing wind hour on a dry hour weighs nothing
    out = rf.wind_rain_features(h).set_index("date")["wind_v_rain"]
    assert abs(out.iloc[0] - 4.0) < 1e-12 and out.iloc[1] == 0.0
    h.loc[3, "wind_dir_deg"] = 0.0                             # one rainy hour from the north: the weighted mean drops
    assert abs(rf.wind_rain_features(h)["wind_v_rain"].iloc[0] - 2.0) < 1e-12
    h.loc[4, "wind_speed_ms"] = np.nan                         # a rainy hour without wind: the day is unknown, not calm
    assert np.isnan(rf.wind_rain_features(h)["wind_v_rain"].iloc[0])


def test_the_page_computes_the_wind_as_training_does():
    """The page's south wind is rain_features.wind_rain_features on its own hours (km/h → m/s), every day, once a
    loaded model reads it; until then the frames are exactly what they were."""
    from datetime import date
    from features.forecast import live_dashboard as ld
    rng = np.random.default_rng(0)
    n = 24 * 5
    ts = pd.date_range("2026-01-01", periods=n, freq="h")
    rain = np.where(rng.random(n) < 0.3, rng.random(n) * 0.2, 0.0)
    spd, dr = rng.random(n) * 12, rng.random(n) * 360
    hourly = pd.DataFrame({"timestamp": ts, "precip_inches": rain, "wind_speed_kmh": spd * 3.6, "wind_dir_deg": dr})
    live = ld.LiveData.__new__(ld.LiveData)
    live.models, live.volume_models = {}, {}
    plain = live._daily_frames(hourly, today=date(2025, 12, 31))       # every day ahead: no gauge fetch
    assert not live.reads_wind and all("wind_v_rain" not in f for f in plain.values())   # no model reads it: as before
    live.models = {"w": {"features": rf.ALL_FEATURES + rf.WIND_FEATURES, "rain_source": "avg"}}
    frames = live._daily_frames(hourly, today=date(2025, 12, 31))
    want = rf.wind_rain_features(hourly.assign(wind_speed_ms=spd)).set_index("date")["wind_v_rain"]
    for src, f in frames.items():
        got = f.set_index("date")["wind_v_rain"]
        assert np.allclose(got.to_numpy(), want.reindex(got.index).to_numpy(), rtol=0, atol=1e-12), src
        assert f.drop(columns="wind_v_rain").equals(plain[src]), src                    # nothing else moves
    row = {**ld.LiveData._features_from_row(frames["avg"].iloc[0]), **ld.LiveData._wind_from_row(frames["avg"].iloc[0])}
    assert set(rf.WIND_FEATURES) <= set(row) and abs(row["wind_v_rain"] - want.iloc[0]) < 1e-12


def test_every_candidate_reads_columns_the_page_computes():
    """A candidate's pickles read the 19 inputs and, at most, the wind: the columns the page and the frames hold."""
    ok = set(rf.ALL_FEATURES) | set(rf.WIND_FEATURES)
    for pkl in (FORECAST / "data" / "models" / "candidates").glob("*/*_model.pkl"):
        with open(pkl, "rb") as f:
            md = pickle.load(f)
        assert set(md["features"]) <= ok, (pkl.parent.name, pkl.name, sorted(set(md["features"]) - ok))


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"  FAIL {name}: {exc!r}")
    print("PASS" if not failures else f"{failures} FAILED")
    raise SystemExit(1 if failures else 0)
