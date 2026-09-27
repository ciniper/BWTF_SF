"""Model-check scorecard windows (2026-09).

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


def test_zone_fp_tail_counts_alarms_in_the_week_after_a_posting():
    days = [
        _day("2023-12-30", 0.9, 0.9, True, None),     # a posting just before the window
        _day("2024-01-02", 0.9, 0.9, False, None),    # false alarm, 3 days after → tail
        _day("2024-01-05", 0.9, 0.9, True, None),     # posting inside the window (a catch, not a false alarm)
        _day("2024-01-09", 0.9, 0.9, False, None),    # false alarm, 4 days after → tail
        _day("2024-01-20", 0.9, 0.9, False, None),    # false alarm, 15 days after → clean day
        _day("2024-01-21", 0.1, 0.1, False, None),    # no alarm
        _day("2024-01-22", 0.9, 0.9, None, None),     # label unknown → counts nowhere
    ]
    t = S.zone_fp_tail(days, ["z"], start="2024-01-01", end="2024-01-31")["z"]
    assert t["0.25"] == 2 and t["0.5"] == 2, t
    c = S.zone_confusion(days, ["z"], start="2024-01-01", end="2024-01-31")["z"]["0.25"]["vs_discharge_posting"]
    assert c["fp"] == 3 and c["fp"] - t["0.25"] == 1        # 3 false alarms: 2 in a tail, 1 on a clean day
    assert S.zone_fp_tail(days, ["z"], start="2024-01-15")["z"]["0.25"] == 0


def test_gauge_outage_rule_masks_dead_gauge_runs_only():
    """A run of ≥2 exactly-0.00 days at one gauge while the other totals ≥0.5"
    is an outage → missing at the dead gauge (the other gauge stands in). A
    single dry day, a run with a dry other gauge, or a genuinely wetter coast
    must be left alone."""
    import pandas as pd
    from rain_features import GAUGE_OUTAGE_RULE, find_gauge_outages, mask_gauge_outages
    dates = pd.date_range("2026-02-01", periods=10)
    dtn = [0.3, 0.9, 0.7, 0.0, 0.0, 0.1, 0.0, 0.2, 0.0, 0.0]
    ocn = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0]   # dead Feb 1–6 (downtown 2.0"), then alive
    df = pd.DataFrame({"date": dates, "SF Downtown": dtn, "SF Oceanside": ocn})
    runs = find_gauge_outages(df)
    assert [(r["gauge"], r["start"], r["end"], r["days"]) for r in runs] == [("SF Oceanside", "2026-02-01", "2026-02-06", 6)], runs
    assert runs[0]["other_total"] == 2.0
    masked, _ = mask_gauge_outages(df)
    assert masked["SF Oceanside"].isna().sum() == 6 and masked["SF Downtown"].isna().sum() == 0
    assert masked.loc[6, "SF Oceanside"] == 0.5                       # the live reading survives
    # Feb 8–10: Oceanside 0.00 for 3 days but downtown only 0.2" → not an outage
    assert not any(r["start"] == "2026-02-08" for r in runs)
    # a single 0.00 day never qualifies, whatever the other gauge did
    one = pd.DataFrame({"date": dates[:3], "SF Downtown": [2.0, 0.0, 1.5], "SF Oceanside": [0.0, 1.0, 1.0]})
    assert find_gauge_outages(one) == []
    assert GAUGE_OUTAGE_RULE["name"] == "gauge_outage_v1"


def test_gauge_outage_rule_in_the_record_and_the_rescored_post_training_days():
    """The rule is opt-in for training frames (None = the record gb_v1 was trained
    on) and applied to the post-training block by --rescore --replace-post."""
    import pandas as pd
    sys.path.insert(0, str(ROOT / "features" / "forecast"))
    import train_v4 as T
    raw, _ = T.rain_series("avg")
    fixed, note = T.rain_series("avg", ["gauge_outage_v1"])
    feb16 = pd.Timestamp("2026-02-16")
    assert abs(raw[feb16] - 0.535) < 0.01 and abs(fixed[feb16] - 1.07) < 0.01, (raw[feb16], fixed[feb16])
    assert note["outage_runs_masked"] >= 10 and note["outage_days_masked"] > 300
    assert (raw != fixed).sum() < 100, "the rule should move only storm days inside dead-gauge runs"
    assert T.rain_series("avg")[1]["outage_runs_masked"] == 0                  # None = untouched record
    sc = _artifact()
    assert sc.get("input_rules_post") == ["gauge_outage_v1"], sc.get("input_rules_post")
    day = {d["date"]: d for d in sc["days"]}
    assert day["2026-02-16"]["post_training"] and day["2026-02-19"]["post_training"]
    assert day["2026-02-16"]["basins"]["westside"]["p"] > 0.5, day["2026-02-16"]["basins"]["westside"]
    # Feb 19: stage 1 stays low (0.08 on 0.77") but the corrected Feb 16–17 tail carries Ocean Beach over the line
    assert day["2026-02-19"]["basins"]["westside"]["p"] > 0.05 and day["2026-02-19"]["zones"]["ocean"]["risk"] >= 0.25, day["2026-02-19"]["zones"]["ocean"]
    # pre-training days are exactly as trained: no rule marker, holdout probabilities present
    assert day["2024-01-13"]["basins"]["westside"]["ph"] is not None and not day["2024-01-13"].get("post_training")
    print(f"   Feb 16 2026 Westside p {day['2026-02-16']['basins']['westside']['p']}, Feb 19 {day['2026-02-19']['basins']['westside']['p']} (were 0.24 / 0.01 on the raw record)")


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
    assert models[0] == {"key": "", "label": "gb_v1 (served)", "family": "gb", "served": True, "stage1": "gb_v1", "stage2": "v1"}
    assert all(m.get("stage2") in ("v1", "v2") for m in models), [m.get("stage2") for m in models]
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
    for name in ("2026-09_forecast_gb_v1_model_explorer.html", "2026-09_forecast_stage2_explorer.html", "2026-09_model_analysis.html", "2026-09_live_replay.html", "2026-09_live_replay_synthetic.html",
                 *[f"2026-09_forecast_{m['name']}_model_explorer.html" for m in C.list_candidates()]):
        p = reports / name
        assert p.exists(), f"missing report {name} — run the exporter"
        text = p.read_text()
        assert "__DATA__" not in text and "__RECS__" not in text and ('"model_sets"' in text or '"models"' in text or "King under the primary setting" in text or "Live corrections replay" in text or "synthetic feed" in text), f"{name}: data not injected"
        print(f"   report {name}: {p.stat().st_size / 1e6:.2f} MB")


def test_stage2_variants_pair_with_any_stage1_and_served_gb_v1_is_untouched():
    """A stage 2 variant is a fitted spec; a candidate may carry one. The served gb_v1's
    composition must be byte-identical with the hook in place (split=None);
    the outfall shares must be probabilities and the identity for groups whose
    outfalls are the whole basin; a candidate whose stage 1 is the served set's must carry
    its exact stage-1 probabilities and a faithful holdout refit."""
    root = Path(__file__).resolve().parents[1] / "features" / "forecast"
    sys.path.insert(0, str(root / "src" / "models"))
    import candidates as C
    import stage2 as S2
    from impact import compose
    # 1. the hook: split=None composes exactly as before
    table = {"G": {"basin": "Westside", "median_event_volume_mg": 3.7, "buckets": {
        "baseline_no_recent_discharge": {"p_elevated": 0.04, "n": 100}, "d0_large": {"p_elevated": 0.9, "n": 10},
        "d1_large": {"p_elevated": 0.5, "n": 10}, "d1_small": {"p_elevated": 0.3, "n": 10}}}}
    probs = [{"westside": 0.9}, {"westside": 0.001}]
    vols = [{"westside": 50.0}, {"westside": 1.0}]
    a = compose(table, {"westside": ["G"]}, probs, vols, 1)
    b = compose(table, {"westside": ["G"]}, probs, vols, 1, split=None)
    assert a == b, (a, b)
    halve = compose(table, {"westside": ["G"]}, probs, vols, 1, split=lambda g, p, v: p * 0.5)
    assert halve[1]["G"] < a[1]["G"], "a split below 1 must lower the group risk"
    # 2. the fitted variant on disk
    spec = S2.load_variant("v2")
    assert spec["variant"] == "v2" and spec.get("kind") == "outfall_split" and spec.get("impact_table"), "variant file incomplete"
    for g, sh in spec["shares"].items():
        for size in ("large", "small", "all"):
            p = sh[size]["p"]
            assert p is None or 0.0 <= p <= 1.0, (g, size, p)
        assert set(spec["group_outfalls"][g]) == set(S2.outfalls_posting(g)), g
    for g in ("Mission Creek", "Southeast"):
        assert spec["shares"][g]["all"]["p"] == 1.0, f"{g}: its outfalls are the whole basin, the split must be the identity"
    assert spec["shares"]["Ocean Beach"]["all"]["p"] < 1.0 and spec["shares"]["Baker-China"]["all"]["p"] < 1.0, "Westside groups must split"
    print(f"   stage 2 v2 (outfall split): " + ", ".join(f"{g} {sh['all']['p']:.2f}" for g, sh in spec["shares"].items()))
    # 3. candidates carrying it
    root_sc = json.load(gzip.open(root / "data" / "models" / "scorecard.json.gz"))
    served = {d["date"]: d for d in root_sc["days"]}
    n_variant = 0
    for m in C.list_candidates():
        s2 = m.get("stage2") or {}
        sc = C.load_scorecard(m["name"])
        assert (sc.get("stage2") or {}).get("variant", "v1") == s2.get("variant", "v1"), m["name"]
        if s2.get("variant", "v1") == "v1":
            continue
        n_variant += 1
        assert C.load_stage2(m["name"]), f"{m['name']}: stage2.json missing"
        hold = [d for d in sc["days"] if d["basins"]["westside"].get("ph") is not None]
        assert hold, f"{m['name']}: no holdout probabilities — the holdout siblings were not refit"
        if (m.get("stage1") or {}).get("from") == "v4":
            worst_p = max(abs(d["basins"][k]["p"] - served[d["date"]]["basins"][k]["p"]) for d in sc["days"] for k in d["basins"] if d["date"] in served)
            worst_ph = max(abs(d["basins"][k]["ph"] - served[d["date"]]["basins"][k]["ph"]) for d in hold for k in d["basins"]
                           if d["date"] in served and d["basins"][k].get("ph") is not None and served[d["date"]]["basins"][k].get("ph") is not None)
            assert worst_p == 0.0, f"{m['name']}: stage 1 is the served gb_v1's, probabilities must be identical (worst {worst_p})"
            assert worst_ph <= 0.001, f"{m['name']}: holdout refit drifted from the served set's stored ph (worst {worst_ph})"
            # and the composition differs only where the split bites (Westside / North Shore groups)
            east_same = all(d["groups"]["Southeast"]["risk"] == served[d["date"]]["groups"]["Southeast"]["risk"] for d in sc["days"] if d["date"] in served)
            assert east_same, f"{m['name']}: Southeast group must be unchanged by the split"
            print(f"   {m['name']}: stage 1 identical to the served gb_v1, holdout refit within {worst_ph:.4f}, Southeast untouched")
    assert n_variant >= 1, "expected at least one candidate on a stage 2 variant"


def test_posting_label_grades_the_signs_not_the_discharge_day():
    """zone_confusion_posted: a posted (sewage/rain) day is a hit or a miss, a
    known-unposted day is quiet, other-cause postings are only counted, and
    days outside the record are unknown and not graded."""
    import posting_label as PL
    dates = [f"2024-01-{d:02d}" for d in range(1, 13)]
    risks = [0.1, 0.6, 0.9, 0.8, 0.6, 0.3, 0.55, 0.1, 0.7, 0.2, 0.9, 0.0]
    days = [{"date": d, "zones": {"east": {"risk": r, "risk_h": None, "discharge": d == "2024-01-03", "elevated": None}}}
            for d, r in zip(dates, risks)]
    label = PL.PostingLabel({"east": {"2024-01-03": "cso", "2024-01-04": "cso", "2024-01-05": "cso", "2024-01-06": "cso", "2024-01-09": "other"}},
                            [("2024-01-01", "2024-01-10")], "synthetic")
    c = S.zone_confusion_posted(days, ["east"], label, holdout_only=False, thresholds=(0.5,))["east"]["0.5"]
    assert c == {"tp": 3, "fn": 1, "fp": 2, "tn": 3, "fp_after_discharge": 1, "other_posted": 1, "other_flagged": 1, "unknown": 2}, c
    assert label.cls("east", "2024-01-11") == PL.UNKNOWN and label.cls("east", "2024-01-08") is None and label.cls("east", "2024-01-04") == "cso"
    cov = label.coverage("2024-01-01", "2024-01-12")
    assert (cov["known_days"], cov["unknown_days"], cov["known_through"]) == (10, 2, "2024-01-10")
    # zone_confusion itself is untouched by the new label (the artifact parity test relies on it)
    d = S.zone_confusion(days, ["east"], holdout_only=False, thresholds=(0.5,))["east"]["0.5"]["vs_discharge_posting"]
    assert d == {"tp": 1, "fp": 6, "fn": 0, "tn": 5}, d   # seven alarms, one on the discharge day


def test_combined_label_grades_discharge_days_and_confirmed_persistence():
    """The primary ruler: discharge day = bad; elevated sample in the week after
    = bad (persistence confirmed); clean sample = good; unsampled tail day =
    not graded; elevated sample with no recent discharge = out of scope."""
    dates = [f"2024-01-{d:02d}" for d in range(1, 13)]
    risks = [0.1, 0.1, 0.9, 0.8, 0.6, 0.3, 0.55, 0.1, 0.7, 0.2, 0.9, 0.0]
    dis = {d: (None if d == "2024-01-01" else d == "2024-01-03") for d in dates}
    elev = {"2024-01-04": False, "2024-01-05": True, "2024-01-09": True, "2024-01-12": True}
    days = [{"date": d, "zones": {"east": {"risk": r, "risk_h": None, "discharge": dis[d], "elevated": elev.get(d)}}} for d, r in zip(dates, risks)]
    c = S.zone_confusion_combined(days, ["east"], holdout_only=False, thresholds=(0.5,))["east"]["0.5"]
    assert c == {"tp": 3, "fp": 2, "fn": 0, "tn": 1, "tp_discharge": 1, "tp_sample": 2, "fn_discharge": 0, "fn_sample": 0,
                 "fp_sample": 1, "fp_quiet": 1, "tn_sample": 0, "tn_quiet": 1, "unknown_tail": 4, "unknown_uncovered": 1,
                 "dry_elevated": 1, "dry_flagged": 0}, c
    dis_set = {d for d, v in dis.items() if v}
    assert S.combined_label(days[2], "east", dis_set) == ("bad", "discharge")
    assert S.combined_label(days[3], "east", dis_set) == ("good", "sample")       # clean sample in the tail → the alarm there is false
    assert S.combined_label(days[8], "east", dis_set) == ("bad", "sample")        # elevated 6 days after → persistence confirmed
    assert S.combined_label(days[11], "east", dis_set) == ("scope", "dry")        # elevated 9 days after → dry-weather, out of scope
    assert S.combined_label(days[5], "east", dis_set) == ("unknown", "tail")
    assert S.combined_label(days[0], "east", dis_set) == ("unknown", "uncovered")


def test_posted_confusion_in_the_api_matches_the_shared_function_and_the_record():
    """The Model check window carries the same days graded against BeachWatch
    postings, computed by the shared function, with the record's coverage."""
    import posting_label as PL
    from features.forecast import live_dashboard as ld
    label = PL.from_beachwatch()
    assert label is not None and label.source == "beachwatch"
    eng = ld.LiveData.__new__(ld.LiveData)
    sc = eng._scorecard()
    w = eng.get_scorecard("2024-01-13", sc["holdout_start"], sc["span"][1])["window"]
    assert set(w["posted_confusion"]) == set(w["zone_confusion"])
    assert w["combined_confusion"] == S.zone_confusion_combined(sc["days"], list(w["zone_confusion"]), w["start"], w["end"], holdout_only=False, thresholds=S.LINE_GRID)
    ce = w["combined_confusion"]["east"]["0.5"]
    assert ce["tp_discharge"] + ce["fn_discharge"] == w["zone_confusion"]["east"]["0.5"]["vs_discharge_posting"]["tp"] + w["zone_confusion"]["east"]["0.5"]["vs_discharge_posting"]["fn"]
    assert ce["tp_sample"] + ce["fn_sample"] >= 50 and ce["dry_elevated"] >= 50 and ce["unknown_tail"] >= 10, ce   # the East: confirmed persistence, dry-weather dirtiness, unsampled tail days all present
    assert w["posted_confusion"] == S.zone_confusion_posted(sc["days"], list(w["zone_confusion"]), label, w["start"], w["end"],
                                                            holdout_only=False, thresholds=S.LINE_GRID)
    e = w["posted_confusion"]["east"]["0.5"]
    assert e["tp"] + e["fn"] >= 100 and e["tp"] / (e["tp"] + e["fn"]) >= 0.6 and e["fp"] <= 20, e   # the East: most posted days flagged, few alarms on unposted days
    assert e["other_posted"] > 50 and e["unknown"] > 0                                                # dry-weather postings counted apart; days after the record not graded
    pl = w["posting_label"]
    assert pl["known_through"] == label.known_through() and pl["unknown_days"] == e["unknown"] and pl["in_scope"] == ["cso", "rain"]
    # a window entirely after the record: nothing graded, everything unknown
    w2 = eng.get_scorecard("2024-01-13", "2026-06-01", sc["span"][1])["window"]
    assert all(v["0.5"]["tp"] + v["0.5"]["fn"] + v["0.5"]["fp"] + v["0.5"]["tn"] == 0 and v["0.5"]["unknown"] == w2["n_days"] for v in w2["posted_confusion"].values())
    print(f"   East vs postings at 50%: {e['tp']}/{e['tp']+e['fn']} posted days caught, {e['fp']} alarms on unposted days, {e['other_posted']} other-cause posted days, {e['unknown']} days after the record")


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
