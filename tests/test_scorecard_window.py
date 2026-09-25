"""Model-check scorecard windows (v4, 2026-09).

One rule for the scorecard numbers: features/forecast/src/models/scorecard.py,
used by train_v4 (the season block stored in scorecard.json.gz) and by
live_dashboard (the time-boxed block the Model check page requests). Pins:

1. the artifact's stored season block is exactly what the shared function
   recomputes over the holdout;
2. windows behave — seasons, clamping to the artifact span, in-sample
   fallback flagged, invalid dates rejected;
3. the served holdout window reproduces the eval report's per-basin metrics.

    venv/bin/python tests/test_scorecard_window.py
"""
from __future__ import annotations

import gzip
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))

import scorecard as S  # noqa: E402

MODEL_DIR = ROOT / "features" / "forecast" / "data" / "models"
SPAN = ("2016-03-01", "2025-10-31")


def _artifact() -> dict:
    with gzip.open(MODEL_DIR / "scorecard.json.gz", "rt") as f:
        return json.load(f)


def _day(date, risk, risk_h, discharge, elevated, y=None):
    return {"date": date, "season": S.season_of(date),
            "zones": {"z": {"risk": risk, "risk_h": risk_h, "discharge": discharge, "elevated": elevated}},
            "basins": {"b": {"p": risk, "ph": risk_h, "y": y}}}


# ── seasons / windows ──────────────────────────────────────────────────────

def test_seasons_are_july_to_june_named_for_the_july_year():
    assert S.season_of("2024-01-13") == 2023
    assert S.season_of("2024-06-30") == 2023
    assert S.season_of("2024-07-01") == 2024
    assert S.season_bounds(2023) == ("2023-07-01", "2024-06-30")
    assert S.season_label(2024) == "2024–25"


def test_seasons_in_span_newest_first_with_holdout_flag():
    ss = S.seasons_in(SPAN, "2023-07-01")
    assert [s["season"] for s in ss] == list(range(2025, 2014, -1))
    assert ss[0] == {"season": 2025, "label": "2025–26", "start": "2025-07-01", "end": "2025-10-31", "holdout": "full"}
    assert ss[-1]["start"] == "2016-03-01" and ss[-1]["holdout"] == "none"
    flags = {s["season"]: s["holdout"] for s in ss}
    assert flags[2023] == "full" and flags[2022] == "none"
    assert {s["season"]: s["holdout"] for s in S.seasons_in(SPAN, "2024-01-01")}[2023] == "partial"
    assert all(s["holdout"] == "none" for s in S.seasons_in(SPAN, None))


def test_clamp_window():
    assert S.clamp_window(None, None, SPAN) == SPAN
    assert S.clamp_window("2010-01-01", "2030-01-01", SPAN) == SPAN
    assert S.clamp_window("2024-06-01", "2024-01-01", SPAN) == ("2024-01-01", "2024-06-01")   # reversed → swapped
    lo, hi = S.clamp_window("2025-12-01", "2026-02-01", SPAN)
    assert lo > hi                                                                             # entirely outside → empty


# ── the shared functions on synthetic days ─────────────────────────────────

def test_zone_confusion_counts_only_known_labels_and_falls_back_in_sample():
    days = [
        _day("2023-01-01", 0.9, None, True, None),    # before the holdout: only an in-sample risk
        _day("2024-01-01", 0.9, 0.9, True, None),     # tp
        _day("2024-01-02", 0.9, 0.3, False, True),    # fp vs discharge, tp vs bacteria
        _day("2024-01-03", 0.1, 0.1, True, False),    # fn, tn
        _day("2024-01-04", 0.1, 0.1, False, None),    # tn
        _day("2024-01-05", 0.9, 0.9, None, None),     # labels unknown → counts nowhere
    ]
    c = S.zone_confusion(days, ["z"], holdout_only=True)["z"]["0.25"]
    assert c["vs_discharge_posting"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert c["vs_bacteria_elevated"] == {"tp": 1, "fp": 0, "fn": 0, "tn": 1}
    c2 = S.zone_confusion(days, ["z"], holdout_only=False)["z"]["0.25"]["vs_discharge_posting"]
    assert c2 == {"tp": 2, "fp": 1, "fn": 1, "tn": 1}          # the 2023 day now scored with its in-sample risk
    c3 = S.zone_confusion(days, ["z"], start="2024-01-03", end="2024-01-04")["z"]["0.25"]["vs_discharge_posting"]
    assert c3 == {"tp": 0, "fp": 0, "fn": 1, "tn": 1}
    assert set(S.zone_confusion(days, ["z"])["z"]) == {"0.1", "0.25", "0.5"}


def test_basin_metrics_need_both_classes_for_auc():
    days = [_day("2024-01-01", .9, .9, True, None, y=1), _day("2024-01-02", .2, .2, False, None, y=0),
            _day("2024-01-03", .8, .1, True, None, y=1), _day("2024-01-04", .1, .1, None, None, y=None)]
    m = S.basin_metrics(days, ["b"])["b"]
    assert (m["n_days"], m["n_events"], m["n_holdout"]) == (3, 2, 3)
    assert 0 < m["pr_auc"] <= 1 and 0 <= m["roc_auc"] <= 1 and m["brier"] is not None
    one = S.basin_metrics(days, ["b"], start="2024-01-01", end="2024-01-01")["b"]
    assert one["n_days"] == 1 and one["pr_auc"] is None and one["roc_auc"] is None and one["brier"] is not None
    assert S.basin_metrics(days, ["b"], start="2030-01-01")["b"] == {
        "n_days": 0, "n_events": 0, "n_holdout": 0, "pr_auc": None, "roc_auc": None, "brier": None}


def test_window_summary():
    days = [_day("2023-01-01", .9, None, None, None), _day("2024-01-01", .9, .9, True, None), _day("2024-01-02", .2, .2, False, True)]
    assert S.window_summary(days, ["z"]) == {"n_days": 3, "n_holdout": 2, "n_insample": 1,
                                             "n_discharge_known": 2, "n_discharge_days": 1, "n_sampled_days": 1}
    assert S.window_summary(days, ["z"], start="2024-01-02")["n_days"] == 1


# ── against the real artifact and the served endpoint ──────────────────────

def test_artifact_season_block_is_the_shared_function():
    sc = _artifact()
    zones = list(sc["zone_confusion_holdout"])
    assert S.zone_confusion(sc["days"], zones, holdout_only=True) == sc["zone_confusion_holdout"]
    # the holdout window with fallback allowed is the same block: every holdout day has a holdout probability
    assert S.zone_confusion(sc["days"], zones, start=sc["holdout_start"], end=sc["span"][1],
                            holdout_only=False) == sc["zone_confusion_holdout"]


def test_served_windows():
    from features.forecast import live_dashboard as ld
    eng = ld.LiveData.__new__(ld.LiveData)          # no models needed for the scorecard
    sc = eng._scorecard()

    # default = the rain season the date falls in
    r = eng.get_scorecard("2024-01-13")
    w = r["window"]
    assert (w["preset"], w["season"], w["start"], w["end"]) == ("season", 2023, "2023-07-01", "2024-06-30")
    assert w["holdout_only"] and w["n_days"] == 366 and w["n_insample"] == 0
    assert set(w["zone_confusion"]) == set(sc["zone_confusion_holdout"])
    assert set(w["basins"]) == {"westside", "north_shore", "central", "southeast"}
    assert [s["season"] for s in r["seasons"]][:2] == [2025, 2024]

    # outside the artifact → the whole holdout = the stored season block = the eval report
    r = eng.get_scorecard("2026-09-20")
    w = r["window"]
    assert r["in_span"] is False and w["preset"] == "holdout"
    assert (w["start"], w["end"]) == (sc["holdout_start"], sc["span"][1])
    assert w["zone_confusion"] == sc["zone_confusion_holdout"]
    ev = json.loads((MODEL_DIR / "eval_report.json").read_text())
    for key, b in w["basins"].items():
        h = ev["targets"][key]["holdout"]
        assert (b["n_days"], b["n_events"]) == (h["n_test"], h["pos_test"]), key
        # the artifact stores probabilities to 3 dp, hence the tolerance
        assert abs(b["pr_auc"] - h["pr_auc"]) < 0.005 and abs(b["roc_auc"] - h["roc_auc"]) < 0.005, key

    # explicit windows: reversed → swapped, clamped to the span, in-sample flagged, empty, invalid
    w = eng.get_scorecard("2024-01-13", "2025-04-30", "2024-11-01")["window"]
    assert (w["preset"], w["start"], w["end"], w["n_days"]) == ("custom", "2024-11-01", "2025-04-30", 181)
    w = eng.get_scorecard("2019-02-14")["window"]
    assert w["season"] == 2018 and w["holdout_only"] is False and w["n_holdout"] == 0
    w = eng.get_scorecard("2024-01-13", "2010-01-01", "2030-01-01")["window"]
    assert (w["start"], w["end"]) == tuple(sc["span"])
    assert eng.get_scorecard("2024-01-13", "2025-12-01", "2026-02-01")["window"]["n_days"] == 0
    assert "error" in eng.get_scorecard("2024-01-13", "yesterday", None)
    assert "error" in eng.get_scorecard("not-a-date")


if __name__ == "__main__":
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
