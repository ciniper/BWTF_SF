"""S1 data (P5, 2026-10-01): the fixed-lead forecast caches, the complete-day rule, the floor.

What is pinned, and why:
- the Previous Runs caches hold fixed leads 1–5 only, in the agreed schema, and
  lead 0 (the stitched short lead, optimistic) lives apart and is tagged as such
  in the manifest, so the two can never be mixed up;
- lead L means "predicted 24·L hours before valid time": the archive began at
  one instant, so each lead's first archived hour is one day after the lead
  before it, for every model;
- a model-day with a missing hour is left out, never summed as a dry day (the
  ECMWF Feb 2024 gap, X-S1-NWPGAP);
- the representativeness floor reproduces the design's preliminary numbers
  (0.184" wet MAE, CSI 0.73 at 0.5") on the served gauge record, and the
  strict floor (masked days left out) is recorded beside it.
Counts are as of the 2026-10-01 fetch; a refresh that only appends days keeps them.

Run: venv/bin/python tests/test_s1_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
for p in (str(ROOT), str(FORECAST), str(FORECAST / "src" / "models")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.collectors import openmeteo_previous_runs as OMP  # noqa: E402
from src.models import weather_models_eval as W  # noqa: E402

AS_OF = "2026-10-01"
_cache = {}


def _prev(model):
    if model not in _cache:
        _cache[model] = pd.read_csv(OMP.prev_runs_path(model), parse_dates=["timestamp"])
    return _cache[model]


# ── the caches ──────────────────────────────────────────────────────────────

def test_fixed_lead_caches_have_the_agreed_schema_and_only_fixed_leads():
    for m in OMP.MODELS:
        df = _prev(m)
        assert list(df.columns) == ["timestamp", "lead_day", "precip_mm", "precip_inches"], (m, list(df.columns))
        assert set(df["lead_day"].unique()) == set(OMP.FIXED_LEADS), (m, sorted(df["lead_day"].unique()))
        ok = df["precip_mm"].notna()
        assert (df["precip_inches"].isna() == ~ok).all(), m
        assert np.allclose(df.loc[ok, "precip_inches"], df.loc[ok, "precip_mm"] / 25.4), m
        for L, g in df.groupby("lead_day"):
            ts = g["timestamp"]
            assert ts.is_monotonic_increasing and not ts.duplicated().any(), (m, L)
            assert (ts.diff().dropna() == pd.Timedelta(hours=1)).all(), (m, L, "hours must be contiguous (a gap is a NaN row)")
            assert pd.notna(g["precip_mm"].iloc[0]), (m, L, "a lead's rows start at its first archived hour")
        assert df["timestamp"].max() >= pd.Timestamp("2026-09-30 23:00"), (m, df["timestamp"].max())


def test_lead_l_is_24l_hours_before_valid_time():
    """The archive started at one moment, so lead L+1's first archived hour is exactly a day after lead L's."""
    for m in OMP.MODELS:
        firsts = _prev(m).groupby("lead_day")["timestamp"].min().sort_index()
        steps = firsts.diff().dropna()
        assert (steps == pd.Timedelta(days=1)).all(), (m, firsts.to_dict())
    assert _prev("icon_seamless").groupby("lead_day")["timestamp"].min()[1] == pd.Timestamp("2024-01-19 05:00")
    assert _prev("ecmwf_ifs025").groupby("lead_day")["timestamp"].min()[1] == pd.Timestamp("2024-02-03 15:00")


def test_skill_falls_with_lead():
    """A sanity check of the lead order: ICON's lead-5 rain is further from the gauges than its lead-1 rain."""
    g = W.load_gauges()
    d = OMP.daily_by_lead("icon_seamless", leads=(1, 5))
    days = d.dropna().index.intersection(g["truth"]["avg"].dropna().index)
    m1 = W.suite(d.loc[days, 1].to_numpy(), g["truth"]["avg"][days].to_numpy())["continuous"]["either_wet"]["mae"]
    m5 = W.suite(d.loc[days, 5].to_numpy(), g["truth"]["avg"][days].to_numpy())["continuous"]["either_wet"]["mae"]
    assert m5 > m1 + 0.05, (m1, m5)


def test_short_lead_lives_apart_and_is_tagged_optimistic():
    man = json.loads(OMP.MANIFEST.read_text())
    assert man["utc_offset_hours"] == -7 and man["complete_day_hours"] == 24
    for m in OMP.MODELS:
        assert man["models"][m]["0"]["kind"] == "short_lead_optimistic"
        assert man["models"][m]["0"]["file"] == f"openmeteo_hist_forecast_{m}.csv"
        for L in OMP.FIXED_LEADS:
            assert man["models"][m][str(L)]["kind"] == "fixed_lead"
            assert man["models"][m][str(L)]["file"] == f"openmeteo_prev_runs_{m}.csv"
        note = man["fetch_notes"][f"prev_runs_{m}"]["lead0_vs_short_lead_cache"]
        assert note["hours_compared"] > 20000 and note["identical"] == note["hours_compared"], (m, note)   # the UTC−7 day, proved
    assert OMP.LEAD_KIND[0] == "short_lead_optimistic" and W.LEAD_LABEL[0].endswith("optimistic)")
    for m in OMP.MODELS:
        hf = pd.read_csv(OMP.short_lead_path(m), nrows=2)
        assert list(hf.columns) == ["timestamp", "precip_mm", "precip_inches"], m        # no lead column: the file name is the tag


def test_icon_short_lead_reaches_back_to_2022_11_16():
    h = OMP.load_hourly("icon_seamless", 0)
    assert h["timestamp"].min() == pd.Timestamp("2022-11-16 00:00")
    assert (h["timestamp"].diff().dropna() == pd.Timedelta(hours=1)).all() and not h["timestamp"].duplicated().any()
    d = OMP.daily_totals(h)
    assert pd.isna(d.loc["2022-11-16", "total"]) and d.loc["2022-11-16", "hours"] == 23   # first archived hour 08:00 UTC
    assert d["total"].first_valid_index() == pd.Timestamp("2022-11-17")
    assert int(d.loc["2022-11-17":"2026-08-27", "total"].notna().sum()) == 1380            # as of AS_OF


# ── the complete-day rule ───────────────────────────────────────────────────

def test_a_day_with_a_missing_hour_is_left_out_not_zero():
    ts = pd.date_range("2025-01-01", periods=72, freq="h")
    x = np.full(72, 0.02)
    x[30] = np.nan                                    # one hour missing on day 2
    d = OMP.daily_totals(pd.DataFrame({"timestamp": ts, "precip_inches": x}))
    assert np.isclose(d.loc["2025-01-01", "total"], 0.48) and np.isclose(d.loc["2025-01-03", "total"], 0.48)
    assert pd.isna(d.loc["2025-01-02", "total"]) and d.loc["2025-01-02", "hours"] == 23
    # the suite drops the row: same scores as leaving the day out, not as calling it dry
    fc, obs = d["total"].to_numpy(), np.array([0.5, 0.6, 0.5])
    s = W.suite(fc, obs)
    assert s["n"] == 2 and s["continuous"]["all"] == W.suite(fc[[0, 2]], obs[[0, 2]])["continuous"]["all"]
    assert s["continuous"]["all"]["mae"] != W.suite(np.nan_to_num(fc), obs)["continuous"]["all"]["mae"]


def test_ecmwf_feb_2024_gap_is_excluded_not_dry():
    fc = W.load_forecasts()
    days = pd.date_range("2024-02-03", "2024-02-29")
    st = W.model_day_status(fc, "ecmwf_ifs025", 0, days)
    gap = st[st == "gap"].index
    assert len(gap) >= 10, st.value_counts().to_dict()                  # 11 days in the window, as of AS_OF
    assert fc["ecmwf_ifs025"]["daily"][0].reindex(gap).isna().all()
    assert fc["mean3"]["daily"][0].reindex(gap).isna().all()            # the mean of three needs all three
    assert (W.model_day_status(fc, "icon_seamless", 0, days) == "ok").all()
    before = W.model_day_status(fc, "ecmwf_ifs025", 1, pd.date_range("2024-01-25", "2024-02-03"))
    assert (before[:-1] == "nolead").all()                             # before the archive: not archived, not a gap
    assert before.iloc[-1] == "gap"                                    # the partial first day (from 15:00) is left out too


# ── the floor ───────────────────────────────────────────────────────────────

def test_floor_reproduces_the_design_numbers_on_the_served_gauge_record():
    g = W.load_gauges()
    f = W.floor_table(g, "2024-02-01", "2026-08-27", ci=False)
    served = f["as_served"][f"{W.OC} as {W.DT}"]                       # Oceanside as a forecast of Downtown
    wet_mae = served["continuous"]["obs_wet"]["mae"]
    csi = served["thresholds"]["0.5"]["csi"]
    assert abs(wet_mae - 0.184) < 0.005, wet_mae
    assert abs(csi - 0.73) < 0.02, csi
    strict = f["masked"][f"{W.OC} as {W.DT}"]                          # masked days left out, not agreeing by construction
    assert 0.19 < strict["continuous"]["obs_wet"]["mae"] < 0.23, strict["continuous"]["obs_wet"]
    assert strict["continuous"]["either_wet"]["mae"] > served["continuous"]["either_wet"]["mae"]
    assert strict["n"] < served["n"]
    unmasked = f["unmasked"][f"{W.OC} as {W.DT}"]                      # dead-gauge zeros make the floor look worse still
    assert unmasked["continuous"]["obs_wet"]["mae"] > strict["continuous"]["obs_wet"]["mae"]
    # either-wet MAE does not depend on which gauge is called the forecast
    assert np.isclose(f["masked"][f"{W.DT} as {W.OC}"]["continuous"]["either_wet"]["mae"], strict["continuous"]["either_wet"]["mae"])


def test_truth_masks_outages_on_every_day_and_never_fills():
    g = W.load_gauges()
    st = g["status"]
    assert set(st[W.DT].unique()) <= {"ok", "missing", "outage"}
    out = st[W.OC][st[W.OC] == "outage"].index
    assert len(out) and g["truth"][W.OC][out].isna().all()            # a dead gauge's zeros are not truth
    assert g["truth_unmasked"][W.OC][out].notna().all()
    both = st[W.DT].index[(st[W.DT] == "ok") & (st[W.OC] != "ok")]
    assert np.allclose(g["truth"]["avg"][both], g["truth"][W.DT][both])   # the mean falls back to the usable gauge
    assert (g["truth"][W.DT].dropna() != W.TRACE_IN).all()             # a trace scores as 0
    runs = [r for r in g["runs"] if r["start"] <= "2026-09-01"]        # the served rule on the full record, as of AS_OF
    assert len(runs) == 12 and sum(r["days"] for r in runs) == 378, (len(runs), sum(r["days"] for r in runs))


# ── the report ──────────────────────────────────────────────────────────────

def test_report_has_no_cost_and_keeps_the_served_model():
    src = (FORECAST / "src" / "models" / "weather_models_eval.py").read_text()
    assert "MISS_WEIGHT" not in src and "cost" not in src.lower(), "Part A A3: no cost anywhere in the S1 report"
    assert W.SERVED_WX == "icon_seamless"
    assert "lead 0 (short, optimistic)" in W.LEAD_LABEL.values()
    assert "day-0" not in W.OUT_HTML.read_text()                       # the archive is lead 0, not "day-0 / day-1"


def test_json_keeps_the_fields_export_how_it_works_reads():
    r = json.loads(W.OUT_JSON.read_text())                             # committed with the report: absent is a failure
    assert r["schema"] == "bwtf.s1_weather_models/2", r.get("schema")
    assert r["served"]["weather_model"] == "icon_seamless"
    assert r["gauge_days"]["avg"]["wet_days"] > 50
    for k in ("ecmwf_ifs025", "gfs_seamless", "icon_seamless", "mean3"):
        v = r["verification"]["avg"][k]
        assert v["wet_mae_in"] is not None and v["thresholds"]["0.5"]["csi"] is not None
    assert set(r["by_lead"]) == {str(L) for L in OMP.LEADS}
    for L, blk in r["exclusions"].items():
        for s, e in blk.items():
            assert e["n_total"] == e["n_scored"] + sum(v for k, v in e.items() if k.startswith("X-S1-")), (L, s, e)
    assert all("cost" not in json.dumps(a) for a in r["appendix"]["arms"])
    html = W.OUT_HTML.read_text()
    assert "freq. bias" in html and "cost" not in html.lower()


def test_appendix_scores_post_training_days_on_one_row_set():
    """The served weights saw every day before 2025-11-01, so the appendix never scores one (X-ALL-INSAMPLE);
    every arm sits on the same basin-days, and ΔBS is the arm's Brier minus rain known's (above 0 = worse)."""
    app = json.loads(W.OUT_JSON.read_text())["appendix"]
    assert app["window"][0] >= str(W.POST_START.date()), app["window"]
    ex = app["excluded"]
    assert ex["X-ALL-INSAMPLE"] > 0 and ex["n_total"] == ex["n_scored"] + ex["X-ALL-INSAMPLE"] + ex["no_forecast"], ex
    arms = app["arms"]
    assert {(a["n"], a["events"]) for a in arms} == {(ex["n_scored"], app["events"])}, "every arm on identical rows"
    base = next(a for a in arms if a["arm"] == "gauges")
    for a in arms:
        if a["arm"] != "gauges":
            assert abs(a["delta"]["delta"] - (a["brier"] - base["brier"])) < 1e-4, a["arm"]
    assert "ΔBS above 0" in W.OUT_HTML.read_text()


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
