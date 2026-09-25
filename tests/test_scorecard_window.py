"""Model-check scorecard windows (v4, 2026-09).

One rule for the scorecard numbers: features/forecast/src/models/scorecard.py,
used by train_v4 (the season block stored in scorecard.json.gz, and --rescore's
post-training days) and by live_dashboard (the time-boxed block the Model
check page requests). Pins:

1. the artifact's stored season block is exactly what the shared function
   recomputes over the holdout;
2. windows behave — seasons, clamping to the artifact span, the
   holdout / post-training / in-sample grading, invalid dates rejected;
3. the served holdout window reproduces the eval report's per-basin metrics;
4. post-training days (if the artifact has been rescored) are what they claim.

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


def _day(date, risk, risk_h, discharge, elevated, y=None, post=False):
    d = {"date": date, "season": S.season_of(date),
         "zones": {"z": {"risk": risk, "risk_h": risk_h, "discharge": discharge, "elevated": elevated}},
         "basins": {"b": {"p": risk, "ph": risk_h, "y": y}}}
    if post:
        d["post_training"] = True
    return d


# ── seasons / windows ──────────────────────────────────────────────────────

def test_seasons_are_july_to_june_named_for_the_july_year():
    assert S.season_of("2024-01-13") == 2023
    assert S.season_of("2024-06-30") == 2023
    assert S.season_of("2024-07-01") == 2024
    assert S.season_bounds(2023) == ("2023-07-01", "2024-06-30")
    assert S.season_label(2024) == "2024–25"


def test_seasons_in_span_newest_first_with_grades_and_clips():
    ss = S.seasons_in(SPAN, "2023-07-01")
    assert [s["season"] for s in ss] == list(range(2025, 2014, -1))
    top = ss[0]
    assert (top["season"], top["label"], top["start"], top["end"]) == (2025, "2025–26", "2025-07-01", "2025-10-31")
    assert (top["holdout"], top["post"], top["clip"]) == ("full", "none", "through Oct 2025")
    assert ss[-1]["start"] == "2016-03-01" and ss[-1]["clip"] == "from Mar 2016" and ss[-1]["holdout"] == "none"
    assert ss[2]["clip"] is None                                        # 2023–24 is whole
    flags = {s["season"]: s["holdout"] for s in ss}
    assert flags[2023] == "full" and flags[2022] == "none"
    assert {s["season"]: s["holdout"] for s in S.seasons_in(SPAN, "2024-01-01")}[2023] == "partial"
    assert all(s["holdout"] == "none" and s["post"] == "none" for s in S.seasons_in(SPAN, None))
    # a rescored artifact: trained through Oct 2025, days appended to Aug 2026
    ext = S.seasons_in(("2016-03-01", "2026-08-27"), "2023-07-01", "2025-10-31")
    by = {s["season"]: s for s in ext}
    assert (by[2026]["holdout"], by[2026]["post"], by[2026]["clip"]) == ("none", "full", "through Aug 2026")
    assert (by[2025]["holdout"], by[2025]["post"], by[2025]["clip"]) == ("partial", "partial", None)
    assert (by[2024]["holdout"], by[2024]["post"]) == ("full", "none")
    assert (by[2022]["holdout"], by[2022]["post"]) == ("none", "none")


def test_clamp_window():
    assert S.clamp_window(None, None, SPAN) == SPAN
    assert S.clamp_window("2010-01-01", "2030-01-01", SPAN) == SPAN
    assert S.clamp_window("2024-06-01", "2024-01-01", SPAN) == ("2024-01-01", "2024-06-01")   # reversed → swapped
    lo, hi = S.clamp_window("2030-01-01", "2030-02-01", SPAN)
    assert lo > hi                                                                             # entirely outside → empty


def test_grade_words():
    assert S.grade(0, 0, 0) == "empty"
    assert S.grade(5, 0, 0) == "holdout"
    assert S.grade(0, 5, 0) == "post_training"
    assert S.grade(5, 5, 0) == "out_of_sample"
    assert S.grade(0, 0, 5) == "in_sample"
    assert S.grade(5, 0, 5) == "mixed" and S.grade(0, 5, 5) == "mixed"


# ── the shared functions on synthetic days ─────────────────────────────────

def test_zone_confusion_counts_only_known_labels_and_falls_back_in_sample():
    days = [
        _day("2023-01-01", 0.9, None, True, None),    # before the holdout: only an in-sample risk
        _day("2024-01-01", 0.9, 0.9, True, None),     # tp
        _day("2024-01-02", 0.9, 0.3, False, True),    # fp vs discharge, tp vs bacteria
        _day("2024-01-03", 0.1, 0.1, True, False),    # fn, tn
        _day("2024-01-04", 0.1, 0.1, False, None),    # tn
        _day("2024-01-05", 0.9, 0.9, None, None),     # labels unknown → counts nowhere
        _day("2026-01-05", 0.9, None, True, None, post=True),   # post-training: served model's risk counts
    ]
    c = S.zone_confusion(days, ["z"], holdout_only=True)["z"]["0.25"]
    assert c["vs_discharge_posting"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert c["vs_bacteria_elevated"] == {"tp": 1, "fp": 0, "fn": 0, "tn": 1}
    c2 = S.zone_confusion(days, ["z"], holdout_only=False)["z"]["0.25"]["vs_discharge_posting"]
    assert c2 == {"tp": 3, "fp": 1, "fn": 1, "tn": 1}          # 2023 (in-sample) and 2026 (post) days scored with `risk`
    c3 = S.zone_confusion(days, ["z"], start="2024-01-03", end="2024-01-04")["z"]["0.25"]["vs_discharge_posting"]
    assert c3 == {"tp": 0, "fp": 0, "fn": 1, "tn": 1}
    assert set(S.zone_confusion(days, ["z"])["z"]) == {"0.1", "0.25", "0.5"}


def test_basin_metrics_need_both_classes_for_auc():
    days = [_day("2024-01-01", .9, .9, True, None, y=1), _day("2024-01-02", .2, .2, False, None, y=0),
            _day("2024-01-03", .8, .1, True, None, y=1), _day("2024-01-04", .1, .1, None, None, y=None),
            _day("2026-01-05", .8, None, True, None, y=1, post=True)]
    m = S.basin_metrics(days, ["b"])["b"]
    assert (m["n_days"], m["n_events"], m["n_holdout"], m["n_post"]) == (4, 3, 3, 1)
    assert 0 < m["pr_auc"] <= 1 and 0 <= m["roc_auc"] <= 1 and m["brier"] is not None
    one = S.basin_metrics(days, ["b"], start="2024-01-01", end="2024-01-01")["b"]
    assert one["n_days"] == 1 and one["pr_auc"] is None and one["roc_auc"] is None and one["brier"] is not None
    assert S.basin_metrics(days, ["b"], start="2030-01-01")["b"] == {
        "n_days": 0, "n_events": 0, "n_holdout": 0, "n_post": 0, "pr_auc": None, "roc_auc": None, "brier": None}


def test_window_summary_grades_the_mix():
    days = [_day("2023-01-01", .9, None, None, None), _day("2024-01-01", .9, .9, True, None),
            _day("2024-01-02", .2, .2, False, True), _day("2026-01-05", .9, None, True, None, post=True)]
    s = S.window_summary(days, ["z"])
    assert s == {"n_days": 4, "n_holdout": 2, "n_post": 1, "n_insample": 1, "grade": "mixed",
                 "n_discharge_known": 3, "n_discharge_days": 2, "n_sampled_days": 1}
    assert S.window_summary(days, ["z"], start="2024-01-01", end="2024-12-31")["grade"] == "holdout"
    assert S.window_summary(days, ["z"], start="2024-01-01")["grade"] == "out_of_sample"
    assert S.window_summary(days, ["z"], start="2026-01-01")["grade"] == "post_training"
    assert S.window_summary(days, ["z"], end="2023-12-31")["grade"] == "in_sample"
    assert S.window_summary(days, ["z"], start="2030-01-01")["grade"] == "empty"


# ── against the real artifact and the served endpoint ──────────────────────

def test_artifact_season_block_is_the_shared_function():
    sc = _artifact()
    zones = list(sc["zone_confusion_holdout"])
    trained_through = sc.get("trained_through") or sc["span"][1]
    assert S.zone_confusion(sc["days"], zones, holdout_only=True) == sc["zone_confusion_holdout"]
    # the holdout window with fallback allowed is the same block: every holdout day has a holdout probability
    assert S.zone_confusion(sc["days"], zones, start=sc["holdout_start"], end=trained_through,
                            holdout_only=False) == sc["zone_confusion_holdout"]


def test_post_training_days_are_what_they_claim():
    sc = _artifact()
    trained_through = sc.get("trained_through") or sc["span"][1]
    post = [d for d in sc["days"] if d.get("post_training")]
    pre = [d for d in sc["days"] if not d.get("post_training")]
    assert pre[-1]["date"] == trained_through, "trained_through must be the last non-post day"
    if not post:
        print("   (artifact not rescored yet — no post-training days to check)")
        return
    assert all(d["date"] > trained_through for d in post)
    assert all(z["risk_h"] is None for d in post for z in d["zones"].values())
    assert all(b["ph"] is None for d in post for b in d["basins"].values())
    assert [d["date"] for d in sc["days"]] == sorted(d["date"] for d in sc["days"])
    assert sc["span"][1] == post[-1]["date"]
    zones = list(sc["zone_confusion_holdout"])
    assert S.window_summary(sc["days"], zones, post[0]["date"], sc["span"][1])["grade"] == "post_training"
    # the 2025-26 winter must be in there with real discharges
    win = S.window_summary(sc["days"], zones, "2025-11-01", "2026-04-30")
    assert win["n_discharge_days"] >= 8, win


def test_served_windows():
    from features.forecast import live_dashboard as ld
    eng = ld.LiveData.__new__(ld.LiveData)          # no models needed for the scorecard
    sc = eng._scorecard()
    trained_through = sc.get("trained_through") or sc["span"][1]

    # default = the rain season the date falls in
    r = eng.get_scorecard("2024-01-13")
    w = r["window"]
    assert (w["preset"], w["season"], w["start"], w["end"]) == ("season", 2023, "2023-07-01", "2024-06-30")
    assert w["grade"] == "holdout" and w["holdout_only"] and w["n_days"] == 366 and w["n_insample"] == 0
    assert set(w["zone_confusion"]) == set(sc["zone_confusion_holdout"])
    assert set(w["basins"]) == {"westside", "north_shore", "central", "southeast"}
    assert r["trained_through"] == trained_through
    assert all(t in S.LINE_GRID for t in S.THRESHOLDS)   # every stored line is on the served grid
    assert [s["season"] for s in r["seasons"]][:2] == [S.season_of(sc["span"][1]), S.season_of(sc["span"][1]) - 1]

    # outside the artifact → the post-training stretch if there is one, else the whole holdout
    r = eng.get_scorecard("2030-01-01")
    w = r["window"]
    assert r["in_span"] is False
    if r["post_start"]:
        assert w["preset"] == "post" and (w["start"], w["end"]) == (r["post_start"], sc["span"][1]) and w["grade"] == "post_training"
    else:
        assert w["preset"] == "holdout" and (w["start"], w["end"]) == (sc["holdout_start"], trained_through)

    # the explicit holdout window = the stored season block = the eval report
    # (the served window is scored on the finer LINE_GRID; the artifact stores three of those lines)
    w = eng.get_scorecard("2024-01-13", sc["holdout_start"], trained_through)["window"]
    assert w["grade"] == "holdout"
    for zk, stored in sc["zone_confusion_holdout"].items():
        assert set(w["zone_confusion"][zk]) == {str(t) for t in S.LINE_GRID}
        assert {t: w["zone_confusion"][zk][t] for t in stored} == stored, zk
    assert set(w["zone_confusion"]) == set(sc["zone_confusion_holdout"])
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
    assert w["season"] == 2018 and w["grade"] == "in_sample" and w["n_holdout"] == 0
    w = eng.get_scorecard("2024-01-13", "2010-01-01", "2030-01-01")["window"]
    assert (w["start"], w["end"]) == tuple(sc["span"])
    assert eng.get_scorecard("2024-01-13", "2030-01-01", "2030-02-01")["window"]["grade"] == "empty"
    assert "error" in eng.get_scorecard("2024-01-13", "yesterday", None)
    assert "error" in eng.get_scorecard("not-a-date")


def test_model_sets_are_selectable_but_never_served():
    import candidates as C
    from features.forecast import live_dashboard as ld
    eng = ld.LiveData.__new__(ld.LiveData)
    models = eng.list_models()
    assert models[0] == {"key": "", "label": "v4 (served)", "family": "gb", "served": True}
    assert all(m["served"] is False for m in models[1:])
    assert "error" in eng.get_scorecard("2024-01-13", model="../x")
    assert "error" in eng.get_scorecard("2024-01-13", model="doesnotexist")
    assert C.valid_name("logit_v1") and not C.valid_name("../x") and not C.valid_name("Bad Name") and not C.valid_name("")
    served = {d["date"]: d for d in _artifact()["days"]}
    for m in models[1:]:
        r = eng.get_scorecard("2024-01-13", model=m["key"])
        assert r["model"] == m["key"] and r["window"]["preset"] == "season" and r["window"]["grade"] == "holdout"
        assert set(r["zone_confusion_holdout"]) == set(_artifact()["zone_confusion_holdout"])
        assert set(r["window"]["zone_confusion"]["ocean"]) == {str(t) for t in S.LINE_GRID}
        cs = C.load_scorecard(m["key"])
        assert cs["trained_through"] == (_artifact().get("trained_through") or _artifact()["span"][1])
        # same days, same labels as the served artifact — only the probabilities may differ
        for d in cs["days"]:
            s = served.get(d["date"])
            if s is None:
                continue
            assert {z: v["discharge"] for z, v in d["zones"].items()} == {z: v["discharge"] for z, v in s["zones"].items()}, d["date"]
            assert {z: v["elevated"] for z, v in d["zones"].items()} == {z: v["elevated"] for z, v in s["zones"].items()}, d["date"]
        post = [d for d in cs["days"] if d.get("post_training")]
        assert all(z["risk_h"] is None for d in post for z in d["zones"].values())
        print(f"   candidate {m['key']}: {len(cs['days'])} days, {len(post)} post-training, labels identical to the served artifact")


def test_candidate_models_unpickle_anywhere_and_explorers_are_built():
    """A candidate's pickles must load from any module (they once referenced
    __main__.add_hinges and only the leaderboard script could open them), and
    the explorer pages that open the models up must exist with their data
    injected. Serving never unpickles a candidate; offline tooling does."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features" / "forecast" / "src" / "models"))
    import candidates as C
    for m in C.list_candidates():
        models = C.load_models(m["name"])
        assert set(models) >= {"westside", "north_shore", "central", "southeast", "citywide"}, sorted(models)
        for key, pk in models.items():
            assert hasattr(pk["model"], "predict_proba"), key
            assert pk["features"], key
            if pk.get("family") == "logit":
                fn = pk["model"].named_steps["hinges"].func
                assert fn.__module__ == "leaderboard", f"{key}: hinge transform pickled as {fn.__module__}.{fn.__name__}"
        print(f"   candidate {m['name']}: {len(models)} models unpickle with a plain import")
    reports = Path(__file__).resolve().parents[1] / "reports"
    for name in ("2026-09_forecast_v4_model_explorer.html", "2026-09_forecast_stage2_explorer.html",
                 *[f"2026-09_forecast_{m['name']}_model_explorer.html" for m in C.list_candidates()]):
        p = reports / name
        assert p.exists(), f"missing report {name} — run the exporter"
        text = p.read_text()
        assert "__DATA__" not in text and ('"model_sets"' in text or '"models"' in text), f"{name}: data not injected"
        print(f"   report {name}: {p.stat().st_size / 1e6:.2f} MB")


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
