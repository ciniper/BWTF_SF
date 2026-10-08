"""The rain-source investigation (src/collectors/rain_grids.py → data/raw/rain_grids/, src/models/rain_sources.py →
data/models/rain_sources.json → RAIN_SOURCES.md). Research only: nothing served reads these files.

Pins: the regions are what the module notes say; AQPI's gap rule fills what it says and nothing else; a Pacific day
needs every hour (23 and 25 on the clock-change days); the grade's arithmetic; the committed series cover their
records; the report is the script's rendering of the committed result, with every overflow-model variant in it.
Offline and fast: the overflow-model test itself (--s2, minutes) is not rerun here.

    venv/bin/python tests/test_rain_sources.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "features/forecast/src/models"), str(ROOT / "features/forecast/src/collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rain_grids as RG  # noqa: E402
import rain_sources as RS  # noqa: E402


def test_the_regions_are_one_cell_per_gauge_and_rectangles_that_tile_the_city():
    lon, lat = np.meshgrid(np.arange(-122.53, -122.34, 0.01), np.arange(37.69, 37.83, 0.01))
    m = RG.region_weights(lon, lat)
    assert list(m) == RG.REGIONS
    for name in RG.POINTS:
        assert m[name].sum() == 1, name
        x, y = RG.POINTS[name]
        assert abs(lon[m[name]][0] - x) <= 0.005 and abs(lat[m[name]][0] - y) <= 0.005, name
    basins = ["westside", "north_shore", "central", "southeast"]
    assert not any((m[a] & m[b]).any() for i, a in enumerate(basins) for b in basins[i + 1:])   # no cell in two basins
    assert ((m["westside"] | m["north_shore"] | m["central"] | m["southeast"]) <= m["city"]).all()
    v = np.arange(lon.size, dtype=float).reshape(1, *lon.shape)
    means = RG.region_means(v, m)
    assert np.isclose(means["city"][0], v[0][m["city"]].mean())


def test_aqpi_gaps_fill_only_short_runs():
    t = pd.date_range("2026-01-01 00:15", periods=24, freq="15min", tz="UTC")
    city = [0, 0, np.nan, 0,  0.1, np.nan, 0.3, 0.2,  0, np.nan, np.nan, np.nan,  0, 0, 0, 0,
            0.2, np.nan, np.nan, np.nan,  0.1, 0, 0, 0]
    q = pd.DataFrame({k: city for k in RG.REGIONS}, index=t)
    h, n = RG.aqpi_fill(q)
    assert list(h.index) == list(pd.date_range("2026-01-01 01:00", periods=6, freq="h", tz="UTC"))   # stamped at the end
    c = h["city"]
    assert c.iloc[0] == 0.0                                     # a lone blank between dry periods: dry
    assert np.isclose(c.iloc[1], 0.1 + 0.2 + 0.3 + 0.2)         # a lone blank in rain: its neighbours' line
    assert c.iloc[2] == 0.0                                     # three blanks between dry periods: dry (≤ 8)
    assert c.iloc[3] == 0.0
    assert np.isnan(c.iloc[4])                                  # three blanks in rain: missing, and so is the hour
    assert n["dry"] == 4 and n["wet"] == 1 and n["missing"] == 3


def test_a_pacific_day_needs_every_hour_23_and_25_on_clock_days():
    idx = pd.date_range("2026-03-07", "2026-03-10", freq="h", tz=RS.TZ, inclusive="left")   # Mar 8: 23 hours
    h = pd.DataFrame({"city": 0.01}, index=idx.tz_localize(None))
    d = RS.daily_from_hourly(h)["city"]
    assert d.notna().all() and np.isclose(d.loc["2026-03-08"], 0.23) and np.isclose(d.loc["2026-03-09"], 0.24)
    d2 = RS.daily_from_hourly(h.drop(h.index[30]))["city"]
    assert np.isnan(d2.loc["2026-03-08"]) and d2.loc["2026-03-07":"2026-03-09"].notna().sum() == 2


def test_the_grade():
    days = pd.date_range("2025-10-01", periods=40, freq="D")
    g = pd.Series(np.r_[np.zeros(30), [0.2, 0.6, 1.2, 0.05, 0, 0, 0.3, 0, 0, 0]], index=days)
    same = RS.grade(g, g)
    assert same["ratio"] == 1 and same["r"] == 1 and same["mae_wet"] == 0 and same["csi_0.5"] == 1 and same["mae_2day"] == 0
    wet = RS.grade(g * 1.5, g)
    assert wet["ratio"] == 1.5 and wet["csi_0.5"] == 1 and wet["csi_1.0"] == 1          # 0.9 stays under 1″: no false alarm
    wetter = RS.grade(g * 2, g)
    assert wetter["csi_1.0"] == 0.5 and wetter["source_wet_gauge_dry"] == 0              # 0.6 × 2 = 1.2: one false alarm
    dry = RS.grade(g.where(g < 1, 0), g)
    assert dry["gauge_wet_source_dry"] == 1 and dry["csi_1.0"] == 0


def test_the_series_cover_their_records():
    a = RG.read("aorc")
    assert list(a.columns) == RG.REGIONS and a.index.is_monotonic_increasing
    assert a.index.min() == pd.Timestamp("2010-01-01 00:00", tz="UTC") and a.index.max() == pd.Timestamp("2025-12-31 23:00", tz="UTC")
    assert len(a) == len(pd.date_range(a.index.min(), a.index.max(), freq="h"))      # every hour of 16 years
    m = RG.read("mrms", "daily")
    assert m.index.min() == RG.MRMS_START and m.index.max() >= pd.Timestamp("2026-08-30")
    assert len(m) >= 0.99 * len(pd.date_range(m.index.min(), m.index.max(), freq="D"))
    q = RG.read("aqpi")
    assert q.index.min() <= pd.Timestamp("2025-10-01 02:00", tz="UTC") and q["city"].notna().mean() > 0.85
    assert RG.read("mrms").index.is_monotonic_increasing


def test_the_report_is_the_committed_result_rendered():
    res = json.loads(RS.OUT_JSON.read_text())
    assert RS.REPORT.read_text() == RS.render(res), "RAIN_SOURCES.md is stale: run rain_sources.py --report"
    assert list(res["s2"]["variants"]) == list(RS.S2_VARIANTS), "an overflow-model variant was not run: --report --s2"
    for v in res["s2"]["variants"].values():
        for t in ("T2", "T1-holdout"):
            c = v["grade"][t]["pooled"]
            assert c["verdict"] in ("better", "worse", "no clear difference") and c["lo"] <= c["delta"] <= c["hi"]
    # the short version's numbers are the result's
    text = RS.REPORT.read_text()
    mf = res["s2"]["variants"]["mrms_fill"]["grade"]
    assert f"{mf['T1-holdout']['westside']['delta'] * 1000:+.2f}".replace("-", "−") in text
    assert mf["T1-holdout"]["westside"]["verdict"] == "better"


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
