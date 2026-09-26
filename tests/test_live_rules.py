"""Live composition rules (src/models/live_rules.py): CSO onset / anchor /
no-flag downgrade on stage 1; sample floors and caps and the flag hold on
stage 2; identity when nothing was observed; the sample rates refit.

    venv/bin/python tests/test_live_rules.py
"""
from __future__ import annotations

import gzip
import json
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))
import live_rules as LR  # noqa: E402

D0 = date(2026, 12, 1)
DATES = [D0 + timedelta(days=i) for i in range(10)]     # D0 … D0+9; "today" = D0+7 → two forecast days
TODAY = D0 + timedelta(days=7)
BASINS = ("westside", "north_shore", "central", "southeast")


def _probs(vals):
    return [{b: vals.get((i, b), 0.1) for b in BASINS} for i in range(len(DATES))]


def test_bayes_downgrade_is_the_conservative_posterior():
    assert round(LR.bayes_downgrade(0.7, 0.6), 3) == 0.483
    assert round(LR.bayes_downgrade(0.5, 0.6), 3) == 0.286
    assert round(LR.bayes_downgrade(0.9, 0.6), 3) == 0.783
    assert round(LR.bayes_downgrade(0.7, 0.87), 2) == 0.23           # the archive figure, for reference
    assert LR.bayes_downgrade(0.7, 0.0) == 0.7 and LR.bayes_downgrade(1.0, 0.6) == 1.0 and LR.bayes_downgrade(0.0, 0.6) == 0.0
    assert LR.RULES["downgrade"]["recall"] == {"westside": 0.0, "north_shore": 0.60, "central": 0.60, "southeast": 0.60}


def test_stage1_onset_anchor_and_downgrade():
    probs = _probs({(2, "southeast"): 0.7, (3, "southeast"): 0.2, (1, "central"): 0.05, (0, "westside"): 0.8, (4, "north_shore"): 0.6})
    vols = [{b: 1.0 for b in BASINS} for _ in DATES]
    onsets = {DATES[3]: {"southeast"}, DATES[2]: {"central"}}
    flags = {DATES[3]: {"southeast"}, DATES[4]: {"southeast"}, DATES[2]: {"central"}}
    rain = {DATES[1]: 0.05}
    p2, v2, notes = LR.adjust_stage1(probs, vols, DATES, onsets, flags, rain, TODAY, watcher_from=D0, watcher_ok=True)
    # onset day → 1; flag up two days → the onset's volume goes to the large curve
    assert p2[3]["southeast"] == 1.0 and notes[str(DATES[3])]["southeast"]["rule"] == "cso_onset"
    assert v2[3]["southeast"] == LR.RULES["cso"]["large_volume_mg"] and notes[str(DATES[3])]["_volume"]["southeast"] == "large_when_flag_persists"
    assert v2[2]["central"] == 1.0                                          # flag up one day only: volume untouched
    # the day before an onset is anchored when the model had it ≥ 0.25 …
    assert p2[2]["southeast"] == 1.0 and notes[str(DATES[2])]["southeast"]["rule"] == "cso_anchor_prev_day"
    # … but not when it had nothing and it barely rained
    assert p2[1]["central"] == 0.05 and "central" not in notes.get(str(DATES[1]), {})
    # no-flag downgrade: applies to days ≤ today − 2 in the bayside basins only
    assert round(p2[4]["north_shore"], 3) == 0.375                          # 0.6 → 0.6·0.4/(0.24+0.4)
    assert notes[str(DATES[4])]["north_shore"]["rule"] == "no_flag_downgrade"
    assert p2[0]["westside"] == 0.8 and "westside" not in notes.get(str(DATES[0]), {})   # Westside recall unmeasured → off
    assert p2[6]["north_shore"] == 0.1 and str(DATES[6]) not in notes     # D+1 = today has not ended → not yet
    assert p2[7]["north_shore"] == 0.1 and p2[8]["north_shore"] == 0.1     # today / forecast days: never
    # a basin with an onset on D or D+1 is never downgraded
    assert str(DATES[2]) in notes and notes[str(DATES[2])].get("central", {}).get("rule") == "cso_onset"
    # the downgrade is off when the watcher is not live for that day, or not healthy now
    p3, _, n3 = LR.adjust_stage1(probs, vols, DATES, onsets, flags, rain, TODAY, watcher_from=DATES[5], watcher_ok=True)
    assert p3[4]["north_shore"] == 0.6 and "north_shore" not in n3.get(str(DATES[4]), {})
    p4, _, n4 = LR.adjust_stage1(probs, vols, DATES, onsets, flags, rain, TODAY, watcher_from=D0, watcher_ok=False)
    assert p4[4]["north_shore"] == 0.6
    # no observations at all → byte-identical probabilities, no notes
    p5, v5, n5 = LR.adjust_stage1(probs, vols, DATES, {}, {}, {}, TODAY, watcher_from=None, watcher_ok=False)
    assert p5 == probs and v5 == vols and n5 == {}


def test_stage2_sample_rules_and_flag_hold():
    zone_of = {"Southeast": "east", "Ocean Beach": "ocean", "Baker-China": "baker_china", "Crissy": "north"}
    basin_of = {"Southeast": "southeast", "Ocean Beach": "westside", "Baker-China": "westside", "Crissy": "north_shore"}
    large = lambda g, k: {1: 0.6, 2: 0.5, 3: 0.4}.get(k, 0.3)  # noqa: E731
    day = DATES[5]
    probs_by_date = {DATES[3]: {"southeast": 0.9, "westside": 0.9, "north_shore": 0.1}}   # a discharge two days before the sample
    risks = {"Southeast": 0.30, "Ocean Beach": 0.50, "Baker-China": 0.20, "Crissy": 0.10}
    persist = {"Southeast": 0.28, "Ocean Beach": 0.45, "Baker-China": 0.18, "Crissy": 0.08}
    today_terms = {"southeast": 0.03, "westside": 0.1, "north_shore": 0.02}
    samples = {("Southeast", DATES[4]): True,        # elevated in a tail → floor at 0.80
               ("Ocean Beach", DATES[4]): False,      # clean in a tail → cap the persistence at 0.42, recombine with today's 0.1
               ("Crissy", DATES[4]): True,            # elevated, no discharge near → dry floor 0.16
               ("Baker-China", DATES[1]): True}       # too old (day 1, horizon 3) → ignored
    out, notes = LR.adjust_groups(risks, persist, today_terms, day, samples, probs_by_date, {}, {}, zone_of, basin_of, large)
    assert out["Southeast"] == 0.8 and notes["Southeast"]["rule"] == "sample_elevated_floor"
    assert out["Ocean Beach"] == round(1 - (1 - 0.42) * (1 - 0.1), 3) and notes["Ocean Beach"]["rule"] == "sample_clean_cap"
    assert out["Crissy"] == 0.16 and notes["Crissy"]["rule"] == "sample_dry_floor"
    assert out["Baker-China"] == 0.20 and "Baker-China" not in notes
    # a sample whose result is not yet known (lag 1 day) does nothing
    out2, n2 = LR.adjust_groups(risks, persist, today_terms, day, {("Southeast", day): True}, probs_by_date, {}, {}, zone_of, basin_of, large)
    assert out2 == risks and n2 == {}
    # the flag hold: flag up on the day, onset two days earlier → hold at the large curve's day-2 value
    out3, n3 = LR.adjust_groups(risks, persist, today_terms, day, {}, {}, {DATES[3]: {"southeast"}}, {day: {"southeast"}}, zone_of, basin_of, large)
    assert out3["Southeast"] == 0.5 and n3["Southeast"]["rule"] == "flag_hold" and out3["Ocean Beach"] == 0.5
    # on the onset day itself the hold does not apply (stage 1 already has p = 1)
    out4, n4 = LR.adjust_groups(risks, persist, today_terms, day, {}, {}, {day: {"southeast"}}, {day: {"southeast"}}, zone_of, basin_of, large)
    assert out4 == risks and n4 == {}
    # nothing observed → identity
    out5, n5 = LR.adjust_groups(risks, persist, today_terms, day, {}, {}, {}, {}, zone_of, basin_of, large)
    assert out5 == risks and n5 == {}


def test_sample_rates_refit_reproduces_the_constants():
    """RULES["samples"] came from fit_sample_rates on the served artifact (2026-09-26)."""
    sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))
    from groups import ZONE_GROUPS
    with gzip.open(ROOT / "features/forecast/data/models/scorecard.json.gz", "rt") as f:
        days = json.load(f)["days"]
    rates = LR.fit_sample_rates(days, ZONE_GROUPS)
    for zk in ZONE_GROUPS:
        for key in ("floor_elevated_tail", "cap_clean_tail", "floor_elevated_dry"):
            assert abs(rates[zk][key] - LR.RULES["samples"][key][zk]) < 0.011, (zk, key, rates[zk][key])
    fitted = LR.rules_with_fitted_rates(rates)
    assert fitted["samples"]["floor_elevated_tail"]["east"] == rates["east"]["floor_elevated_tail"] and fitted is not LR.RULES


def test_live_dashboard_wiring_is_the_identity_without_observations_and_moves_with_them():
    """LiveData._live_context + _day_payload: no watcher, no samples → the plain
    composition, byte for byte; an onset, a flag and a sample → the recorded rules."""
    import json
    import pandas as pd
    sys.path.insert(0, str(ROOT / "features" / "forecast"))
    from features.forecast import live_dashboard as ld
    eng = ld.LiveData.__new__(ld.LiveData)
    eng.impact_table = ld._smooth_table(json.loads((ROOT / "features/forecast/data/models/impact_table.json").read_text()))
    rain = [0.0, 0.0, 0.0, 0.4, 1.1, 0.3, 0.05, 0.0, 0.0, 0.0]
    frames = {"avg": pd.DataFrame({"date": pd.to_datetime(DATES), "precip_inches": rain, "rain_source": ["observed"] * 8 + ["forecast"] * 2})}
    feats = [{"avg": {"precip_avg": r, "rain_2d_cum": 0.0, "rain_3d_cum": 0.0}} for r in rain]
    probs = _probs({(4, "north_shore"): 0.6, (4, "westside"): 0.9, (5, "westside"): 0.95, (6, "westside"): 0.95, (4, "southeast"): 0.7, (6, "southeast"): 0.3})
    vols = [{b: (60.0 if b == "westside" else 2.0) for b in BASINS} for _ in DATES]   # a big Westside event: its persistence sits above the clean-sample cap

    # nothing observed: the rules are the identity (the switch pinned "on" so no config lookup happens in the test)
    eng._live_corrections_enabled = lambda: (True, "default")
    eng._watcher_health = lambda: {"ok": False, "mode": None, "reason": "supabase not configured"}
    eng._cso_flag_days = lambda s, e, t: {}
    eng._sample_flags = lambda s, e: {}
    live = eng._live_context(frames, probs, vols, DATES, {}, TODAY)
    plain = eng._day_payload(frames, 7, feats, probs, vols, DATES, {}, None)
    ruled = eng._day_payload(frames, 7, feats, probs, vols, DATES, {}, live)
    assert ruled["predictions"] == plain["predictions"] and ruled["impact_groups"] == plain["impact_groups"] and ruled["zones"] == plain["zones"]
    assert ruled["live_corrections"] == {"version": "live_v1", "enabled": True, "source": "default", "stage1": {}, "groups": {}, "flags_active": [], "watcher_ok": False}
    assert ruled["discharge_probs_live"] == probs[7] and plain["live_corrections"] is None
    assert ruled["plain"]["predictions"] == plain["predictions"] and ruled["plain"]["zones"] == plain["zones"]   # the plain composition rides along

    # an onset yesterday in the Southeast (flag still up today), a clean Ocean Beach sample yesterday after a 0.9 day,
    # and a 0.6 North Shore day three days ago the feed never flagged
    onsets = {DATES[6]: {"southeast"}}
    eng._watcher_health = lambda: {"ok": True, "mode": "live", "reason": "ok"}
    eng._cso_flag_days = lambda s, e, t: {DATES[6]: {"southeast"}, DATES[7]: {"southeast"}}
    eng._sample_flags = lambda s, e: {("Ocean Beach", DATES[6]): False}
    live = eng._live_context(frames, probs, vols, DATES, onsets, TODAY)
    assert live["probs"][6]["southeast"] == 1.0 and live["notes"][str(DATES[6])]["southeast"]["rule"] == "cso_onset"
    assert live["probs"][5]["southeast"] == 1.0 and live["notes"][str(DATES[5])]["southeast"]["rule"] == "cso_anchor_prev_day"   # 0.3" fell the day before (≥ 0.25")
    assert round(live["probs"][4]["north_shore"], 3) == 0.375 and live["notes"][str(DATES[4])]["north_shore"]["rule"] == "no_flag_downgrade"
    assert live["probs"][4]["westside"] == 0.9                       # Westside: recall unmeasured, never downgraded
    today = eng._day_payload(frames, 7, feats, probs, vols, DATES, onsets, live)
    rules = today["live_corrections"]
    assert today["plain"]["impact_groups"] == plain["impact_groups"]                                    # …even when the corrections moved the numbers
    assert rules["flags_active"] == ["southeast"] and rules["watcher_ok"] is True
    # the flag stayed up two days → the onset's volume went to the large curve, so today's Southeast risk already sits at
    # or above the large-event day-1 value; the flag hold is then redundant (it only bites when the onset predates the window)
    assert live["notes"][str(DATES[6])]["_volume"]["southeast"] == "large_when_flag_persists"
    hold = ld._impact_fraction(eng.impact_table, "Southeast", 1, LR.RULES["cso"]["large_volume_mg"])
    assert today["impact_groups"]["Southeast"] >= round(hold, 3) - 0.001
    assert "Southeast" not in rules["groups"] or rules["groups"]["Southeast"]["rule"] == "flag_hold"
    # the clean Ocean Beach sample caps the persistence term at 0.42 and recombines with today's own term
    p_only = [dict(x) for x in live["probs"]]; p_only[7] = {b: 0.0 for b in p_only[7]}
    persist = ld._compose_risk(eng.impact_table, ld.GROUPS_BY_BASIN, p_only, live["vols"], 7, DATES, {})[1]["Ocean Beach"]
    assert persist > LR.RULES["samples"]["cap_clean_tail"]["ocean"], persist          # the scenario is set up so the cap bites
    assert rules["groups"]["Ocean Beach"]["rule"] == "sample_clean_cap"
    assert today["impact_groups"]["Ocean Beach"] == round(1 - (1 - 0.42) * (1 - live["probs"][7]["westside"]), 3)
    assert today["impact_groups"]["Ocean Beach"] < plain["impact_groups"]["Ocean Beach"]
    assert today["predictions"]["southeast"] == today["impact_groups"]["Southeast"]      # the basin follows its (only) group
    assert today["zones"]["east"] == today["impact_groups"]["Southeast"] and today["zones"]["ocean"] == today["impact_groups"]["Ocean Beach"]
    past = eng._day_payload(frames, 4, feats, probs, vols, DATES, onsets, live)
    assert past["discharge_probs"]["north_shore"] == 0.6 and round(past["discharge_probs_live"]["north_shore"], 3) == 0.375
    assert past["live_corrections"]["stage1"]["north_shore"]["rule"] == "no_flag_downgrade"
    # the switch: LIVE_CORRECTIONS=off → plain numbers, the block says so, nothing is fetched
    import os
    del eng._live_corrections_enabled   # back to the real method: the env var wins over any config
    os.environ["LIVE_CORRECTIONS"] = "off"
    try:
        off = eng._live_context(frames, probs, vols, DATES, onsets, TODAY)
        assert off == {"enabled": False, "source": "env"}
        pay = eng._day_payload(frames, 7, feats, probs, vols, DATES, onsets, off)
        assert pay["predictions"] == plain["predictions"] and pay["live_corrections"] == {"version": "live_v1", "enabled": False, "source": "env"}
    finally:
        os.environ.pop("LIVE_CORRECTIONS", None)
    os.environ["LIVE_CORRECTIONS"] = "on"
    try:
        assert eng._live_corrections_enabled() == (True, "env")
    finally:
        os.environ.pop("LIVE_CORRECTIONS", None)
    print(f"   today: Southeast {plain['impact_groups']['Southeast']} → {today['impact_groups']['Southeast']} (observed onset, large), Ocean Beach {plain['impact_groups']['Ocean Beach']} → {today['impact_groups']['Ocean Beach']} (clean sample)")


def test_replay_report_exists_and_its_self_check_held():
    """replay_live.py wrote the archive-era replay: plain recomposition matched the
    stored risks, every variant is graded on the three rulers, and on the bayside
    zones (labels independent of the feed) live_v1 is no worse than the model
    alone at the 50% line."""
    import json
    res = json.loads((ROOT / "reports" / "2026-09_live_replay.json").read_text())
    assert res["self_check_worst_delta"] <= 0.001, res["self_check_worst_delta"]
    assert set(res["variants"]) >= {"plain", "live_v1", "no_downgrade", "no_samples", "cso_flags_only"}
    for name, v in res["variants"].items():
        for t_ in ("0.25", "0.5"):
            g = v["grades"][t_]
            assert {"combined", "discharge", "posted"} <= set(g) and all("bayside" in g[r] for r in ("combined", "discharge", "posted")), (name, t_)
    plain, live = res["variants"]["plain"]["grades"]["0.5"]["combined"]["bayside"], res["variants"]["live_v1"]["grades"]["0.5"]["combined"]["bayside"]
    assert live["cost"] <= plain["cost"] and live["tp"] >= plain["tp"], (plain, live)
    assert res["variants"]["plain"]["days_changed"] == {z: 0 for z in res["variants"]["plain"]["days_changed"]}
    assert (ROOT / "reports" / "2026-09_live_replay.html").exists()


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
