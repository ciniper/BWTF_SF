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
    assert LR.RULES["downgrade"]["recall_by_quiet_days"]["southeast"] == [0.60, 0.87, 0.95] and LR.RULES["downgrade"]["recall_by_quiet_days"]["westside"] == [0.0, 0.0, 0.0]
    assert round(LR.bayes_downgrade(0.7, 0.95), 3) == 0.104 and round(LR.bayes_downgrade(0.5, 0.95), 3) == 0.048


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
    # no-flag downgrade: a schedule by quiet days observed after the day, bayside basins only
    assert round(p2[4]["north_shore"], 3) == 0.07                            # two quiet days observed → R = 0.95: 0.6·0.05/(0.03+0.4)
    assert notes[str(DATES[4])]["north_shore"] == {"rule": "no_flag_downgrade", "from": 0.6, "to": 0.07, "quiet_days": 2}
    assert p2[0]["westside"] == 0.8 and "westside" not in notes.get(str(DATES[0]), {})   # Westside recall unmeasured → off
    assert p2[6]["north_shore"] == 0.1 and str(DATES[6]) not in notes     # under min_p 0.15: left alone
    assert p2[7]["north_shore"] == 0.1 and p2[8]["north_shore"] == 0.1     # today / forecast days: never
    # the schedule itself: the morning after is conservative, one quiet day uses the archive recall, two or more 0.95
    probs_s = _probs({(6, "central"): 0.7, (5, "central"): 0.7, (4, "central"): 0.7})
    ps, _, ns = LR.adjust_stage1(probs_s, vols, DATES, {}, {}, {}, TODAY, watcher_from=D0, watcher_ok=True)
    assert (round(ps[6]["central"], 3), ns[str(DATES[6])]["central"]["quiet_days"]) == (0.483, 0)   # yesterday: silence overnight only
    assert (round(ps[5]["central"], 3), ns[str(DATES[5])]["central"]["quiet_days"]) == (0.233, 1)   # one full quiet day after
    assert (round(ps[4]["central"], 3), ns[str(DATES[4])]["central"]["quiet_days"]) == (0.104, 2)   # two quiet days: 0.95
    # an onset on the day after cancels the downgrade of the day before (the discharge likely started then)
    ps2, _, ns2 = LR.adjust_stage1(probs_s, vols, DATES, {DATES[6]: {"central"}}, {}, {}, TODAY, watcher_from=D0, watcher_ok=True)
    assert ps2[5]["central"] == 1.0 and ns2[str(DATES[5])]["central"]["rule"] == "cso_anchor_prev_day"
    ps3, _, ns3 = LR.adjust_stage1(_probs({(5, "central"): 0.2, (6, "central"): 0.1}), vols, DATES, {DATES[6]: {"central"}}, {}, {}, TODAY, watcher_from=D0, watcher_ok=True)
    assert ps3[5]["central"] == 0.2 and "central" not in ns3.get(str(DATES[5]), {})                # no anchor (0.2 < 0.25, no rain) and no downgrade either
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
    today_terms = {"Southeast": 0.03, "Ocean Beach": 0.1, "Baker-China": 0.1, "Crissy": 0.02}   # per group, after the split
    samples = {("Southeast", DATES[4]): True,        # elevated in a tail → floor at 0.80
               ("Ocean Beach", DATES[4]): False,      # clean in a tail → cap the persistence at 0.42, recombine with today's 0.1
               ("Crissy", DATES[4]): True,            # elevated, no discharge near → the dry floor is off (rate 0.16 < 0.5): nothing
               ("Baker-China", DATES[1]): True}       # too old (day 1, horizon 3) → ignored
    out, notes = LR.adjust_groups(risks, persist, today_terms, day, samples, probs_by_date, {}, {}, zone_of, basin_of, large)
    assert out["Southeast"] == 0.8 and notes["Southeast"]["rule"] == "sample_elevated_floor"
    assert out["Ocean Beach"] == round(1 - (1 - 0.42) * (1 - 0.1), 3) and notes["Ocean Beach"]["rule"] == "sample_clean_cap"
    assert out["Crissy"] == 0.10 and "Crissy" not in notes
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


def _stage2_v2():
    """(spec, split, impact table) of the outfall split, stage 2 v2 (served since 2026-09-28), loaded as LiveData loads it."""
    import impact as IM
    import stage2 as S2
    spec = json.loads(S2.variant_path("v2").read_text())
    return spec, S2.make_split(spec), IM.smooth_table(spec["impact_table"])


def test_clean_sample_cap_never_raises_a_group():
    """A clean sample in a discharge tail can only lower a group's risk. The cap
    recombines with the group's own day term after the stage 2 split; until
    2026-10-01 it used the basin's whole p, which raised every group whose
    share is under 1 (Ocean Beach, Baker-China, Aquatic Park). Swept over storm
    shapes and sizes and today's probability and size, under v1 (no split) and
    the outfall split, with every group sampled clean the day before."""
    import impact as IM
    from groups import GROUPS_BY_BASIN, ZONE_GROUPS
    spec, split, table = _stage2_v2()
    assert min(spec["shares"][g]["small"]["p"] for g in ("Ocean Beach", "Baker-China", "Aquatic Park")) < 0.85   # the sweep has shares under 1
    v1_table = IM.smooth_table(json.loads((ROOT / "features/forecast/data/models/impact_table.json").read_text()))
    zone_of = {g: zk for zk, gs in ZONE_GROUPS.items() for g in gs}
    basin_of = {g: bk for bk, gs in GROUPS_BY_BASIN.items() for g in gs}
    caps = LR.RULES["samples"]["cap_clean_tail"]
    days = DATES[:8]
    n = bound = basin_p_would_raise = 0
    for split_, table_ in ((None, v1_table), (split, table)):
        for storm in ([1], [2], [1, 2], [1, 2, 3], [3, 4, 5]):
            for v_storm in (0.5, 3.7, 60.0):
                for p_today in (0.0, 0.05, 0.3, 0.7, 0.95):
                    for v_today in (0.5, 5.0, 60.0):
                        probs = [{b: (1.0 if 7 - i in storm else 0.0) for b in GROUPS_BY_BASIN} for i in range(8)]
                        vols = [{b: (v_storm if 7 - i in storm else 1.0) for b in GROUPS_BY_BASIN} for i in range(8)]
                        probs[7], vols[7] = {b: p_today for b in GROUPS_BY_BASIN}, {b: v_today for b in GROUPS_BY_BASIN}
                        _, groups = IM.compose(table_, GROUPS_BY_BASIN, probs, vols, 7, days, {}, split=split_)
                        p_only = [dict(p) for p in probs]
                        p_only[7] = {b: 0.0 for b in GROUPS_BY_BASIN}
                        _, persist = IM.compose(table_, GROUPS_BY_BASIN, p_only, vols, 7, days, {}, split=split_)
                        today = IM.day_terms(GROUPS_BY_BASIN, probs[7], vols[7], split=split_)
                        samples = {(g, days[6]): False for g in groups}
                        out, notes = LR.adjust_groups(groups, persist, today, days[7], samples, dict(zip(days, probs)), {}, {},
                                                      zone_of, basin_of, lambda g, k: 0.0)
                        for g in groups:
                            n += 1
                            case = (split_ is not None, storm, v_storm, p_today, v_today, g, groups[g], persist[g], out[g])
                            # the day term is the composition's own: recombined with the persistence it gives the risk back
                            assert abs(1 - (1 - persist[g]) * (1 - today[g]) - groups[g]) <= 0.001 + 1e-9, case
                            assert out[g] <= groups[g], case
                            assert g not in notes or (notes[g]["rule"] == "sample_clean_cap" and notes[g]["to"] <= notes[g]["from"]), (case, notes[g])
                            capped = min(groups[g], 1 - (1 - min(persist[g], caps[zone_of[g]])) * (1 - today[g]))
                            assert abs(out[g] - capped) <= 0.0005 + 1e-9, (case, capped)
                            if groups[g] - capped > 0.001:
                                bound += 1
                                assert out[g] < groups[g], case     # a binding cap lowers the number
                            # the pre-2026-10-01 recombination, with the basin's own p
                            basin_p_would_raise += 1 - (1 - min(persist[g], caps[zone_of[g]])) * (1 - probs[7][basin_of[g]]) > groups[g] + 0.001
    assert n == 2 * 5 * 3 * 5 * 3 * 6 and bound > 50 and basin_p_would_raise > 50, (n, bound, basin_p_would_raise)
    print(f"   {n} group-days, the cap bound on {bound}; the basin-p recombination would have raised {basin_p_would_raise}")


def test_westside_clean_sample_under_the_outfall_split():
    """The Westside case, through LiveData's own wiring with the outfall split on:
    a two-day 60 MG storm, then today p = 0.9 for a 0.5 MG event, which the split
    gives Ocean Beach at about half and Baker-China at 0.8. A clean sample at
    both yesterday: Ocean Beach's persistence (0.77) is over its 0.42 cap, so
    the cap lowers it and recombines with Ocean Beach's own 0.47, not the
    basin's 0.9 (which gave 0.94 > its 0.88). Baker-China's persistence (0.34)
    is under its 0.36 cap, so it keeps its composed risk (the basin-p
    recombination took it 0.82 → 0.93, the bug the stages golden caught on
    2026-02-17)."""
    import pandas as pd
    import stage2 as S2
    from features.forecast import live_dashboard as ld
    spec, split, table = _stage2_v2()
    eng = ld.LiveData.__new__(ld.LiveData)
    eng.stage2, eng.split, eng.impact_table = spec, split, table
    eng.specs = eng._load_specs()   # the composition runs on the stage specs built from the same spec (compose_v2's adapter)
    rain = [0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 1.5, 0.6, 0.0, 0.0]
    frames = {"avg": pd.DataFrame({"date": pd.to_datetime(DATES), "precip_inches": rain, "rain_source": ["observed"] * 8 + ["forecast"] * 2})}
    feats = [{"avg": {"precip_avg": r, "rain_2d_cum": 0.0, "rain_3d_cum": 0.0}} for r in rain]
    probs = [{b: 0.0 for b in BASINS} for _ in DATES]
    vols = [{b: 1.0 for b in BASINS} for _ in DATES]
    for i, p, v in ((5, 1.0, 60.0), (6, 1.0, 60.0), (7, 0.9, 0.5)):
        probs[i]["westside"], vols[i]["westside"] = p, v
    eng._live_corrections_enabled = lambda: (True, "default")
    eng._watcher_health = lambda: {"ok": False, "mode": None, "reason": "test"}
    eng._cso_flag_days = lambda s, e, t: {}
    eng._sample_flags = lambda s, e: {("Ocean Beach", DATES[6]): False, ("Baker-China", DATES[6]): False}
    eng._feed_sample_flags = lambda s, e: {}
    live = eng._live_context(frames, probs, vols, DATES, {}, TODAY)
    pay = eng._day_payload(frames, 7, feats, probs, vols, DATES, {}, live)
    composed, rules = pay["plain"]["impact_groups"], pay["live_corrections"]["groups"]   # nothing moved stage 1, so plain = the composition
    term = {g: 0.9 * S2.group_share(spec, g, 0.5) for g in ("Ocean Beach", "Baker-China")}
    assert 0.45 < term["Ocean Beach"] < 0.5 and 0.7 < term["Baker-China"] < 0.75, term
    cap_ob, cap_bc = LR.RULES["samples"]["cap_clean_tail"]["ocean"], LR.RULES["samples"]["cap_clean_tail"]["baker_china"]
    assert pay["impact_groups"]["Ocean Beach"] == round(1 - (1 - cap_ob) * (1 - term["Ocean Beach"]), 3) < composed["Ocean Beach"], (pay["impact_groups"], composed)
    assert rules["Ocean Beach"] == {"rule": "sample_clean_cap", "from": composed["Ocean Beach"], "to": pay["impact_groups"]["Ocean Beach"]}
    assert 1 - (1 - cap_ob) * (1 - 0.9) > composed["Ocean Beach"]                       # what the basin's p would have done
    assert pay["impact_groups"]["Baker-China"] == composed["Baker-China"] and "Baker-China" not in rules
    assert 1 - (1 - cap_bc) * (1 - 0.9) > composed["Baker-China"] + 0.05
    assert all(pay["impact_groups"][g] <= composed[g] for g in composed)
    assert pay["zones"]["ocean"] == pay["impact_groups"]["Ocean Beach"] and pay["zones"]["baker_china"] == composed["Baker-China"]
    print(f"   Ocean Beach {composed['Ocean Beach']} → {pay['impact_groups']['Ocean Beach']} (cap binds), Baker-China {composed['Baker-China']} kept (cap does not bind)")


def test_sample_rates_refit_reproduces_the_constants():
    """RULES["samples"] came from fit_sample_rates on the served artifact (2026-09-26)."""
    sys.path.insert(0, str(ROOT / "features" / "forecast" / "src" / "models"))
    from groups import ZONE_GROUPS
    with gzip.open(ROOT / "features/forecast/data/models/scorecard.json.gz", "rt") as f:
        days = json.load(f)["days"]
    rates = LR.fit_sample_rates(days, ZONE_GROUPS)
    for zk in ZONE_GROUPS:
        for key in ("floor_elevated_tail", "cap_clean_tail", "floor_elevated_dry"):
            assert abs(rates[zk][key] - LR.SAMPLE_RATES[key][zk]) < 0.011, (zk, key, rates[zk][key])
    # the policy: caps everywhere, floors only at or above 0.5 (the East's tail floor), the rest off
    assert LR.RULES["samples"]["cap_clean_tail"] == LR.SAMPLE_RATES["cap_clean_tail"]
    assert LR.RULES["samples"]["floor_elevated_tail"] == {"ocean": 0.0, "baker_china": 0.0, "north": 0.0, "east": 0.80}
    assert LR.RULES["samples"]["floor_elevated_dry"] == {"ocean": 0.0, "baker_china": 0.0, "north": 0.0, "east": 0.0}
    fitted = LR.rules_with_fitted_rates(rates)
    assert fitted["samples"]["floor_elevated_tail"]["east"] == rates["east"]["floor_elevated_tail"] and fitted["samples"]["floor_elevated_tail"]["ocean"] == 0.0 and fitted is not LR.RULES


def test_live_dashboard_wiring_is_the_identity_without_observations_and_moves_with_them():
    """LiveData._live_context + _day_payload: no watcher, no samples → the plain
    composition, byte for byte; an onset, a flag and a sample → the recorded rules."""
    import json
    import pandas as pd
    sys.path.insert(0, str(ROOT / "features" / "forecast"))
    from features.forecast import live_dashboard as ld
    eng = ld.LiveData.__new__(ld.LiveData)
    eng.impact_table = ld._smooth_table(json.loads((ROOT / "features/forecast/data/models/impact_table.json").read_text()))
    eng.stage2 = None                 # stage 2 v1: no split
    eng.specs = eng._load_specs()     # the composition's stage specs, from the same impact_table.json
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
    eng._feed_sample_flags = lambda s, e: {}
    live = eng._live_context(frames, probs, vols, DATES, {}, TODAY)
    plain = eng._day_payload(frames, 7, feats, probs, vols, DATES, {}, None)
    ruled = eng._day_payload(frames, 7, feats, probs, vols, DATES, {}, live)
    assert ruled["predictions"] == plain["predictions"] and ruled["impact_groups"] == plain["impact_groups"] and ruled["zones"] == plain["zones"]
    assert ruled["live_corrections"] == {"version": LR.VERSION, "enabled": True, "source": "default", "stage1": {}, "groups": {}, "flags_active": [], "watcher_ok": False, "sample_sources": {"feed": 0, "datasf": 0}}
    assert ruled["discharge_probs_live"] == probs[7] and plain["live_corrections"] is None
    assert ruled["plain"]["predictions"] == plain["predictions"] and ruled["plain"]["zones"] == plain["zones"]   # the plain composition rides along

    # an onset yesterday in the Southeast (flag still up today), a clean Ocean Beach sample yesterday after a 0.9 day,
    # and a 0.6 North Shore day three days ago the feed never flagged
    onsets = {DATES[6]: {"southeast"}}
    eng._watcher_health = lambda: {"ok": True, "mode": "live", "reason": "ok"}
    eng._cso_flag_days = lambda s, e, t: {DATES[6]: {"southeast"}, DATES[7]: {"southeast"}}
    eng._sample_flags = lambda s, e: {("Ocean Beach", DATES[6]): False}
    eng._feed_sample_flags = lambda s, e: {}
    live = eng._live_context(frames, probs, vols, DATES, onsets, TODAY)
    assert live["probs"][6]["southeast"] == 1.0 and live["notes"][str(DATES[6])]["southeast"]["rule"] == "cso_onset"
    assert live["probs"][5]["southeast"] == 1.0 and live["notes"][str(DATES[5])]["southeast"]["rule"] == "cso_anchor_prev_day"   # 0.3" fell the day before (≥ 0.25")
    assert round(live["probs"][4]["north_shore"], 3) == 0.07 and live["notes"][str(DATES[4])]["north_shore"]["rule"] == "no_flag_downgrade"
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
    persist = eng._compose_impact(p_only, live["vols"], 7, DATES, {})["_groups"]["Ocean Beach"]
    assert persist > LR.RULES["samples"]["cap_clean_tail"]["ocean"], persist          # the scenario is set up so the cap bites
    assert rules["groups"]["Ocean Beach"]["rule"] == "sample_clean_cap"
    assert today["impact_groups"]["Ocean Beach"] == round(1 - (1 - 0.42) * (1 - live["probs"][7]["westside"]), 3)
    assert today["impact_groups"]["Ocean Beach"] < plain["impact_groups"]["Ocean Beach"]
    assert today["predictions"]["southeast"] == today["impact_groups"]["Southeast"]      # the basin follows its (only) group
    assert today["zones"]["east"] == today["impact_groups"]["Southeast"] and today["zones"]["ocean"] == today["impact_groups"]["Ocean Beach"]
    past = eng._day_payload(frames, 4, feats, probs, vols, DATES, onsets, live)
    assert past["discharge_probs"]["north_shore"] == 0.6 and round(past["discharge_probs_live"]["north_shore"], 3) == 0.07
    assert past["live_corrections"]["stage1"]["north_shore"]["rule"] == "no_flag_downgrade"
    # the switch: LIVE_CORRECTIONS=off → plain numbers, the block says so, nothing is fetched
    import os
    del eng._live_corrections_enabled   # back to the real method: the env var wins over any config
    os.environ["LIVE_CORRECTIONS"] = "off"
    try:
        off = eng._live_context(frames, probs, vols, DATES, onsets, TODAY)
        assert off == {"enabled": False, "source": "env"}
        pay = eng._day_payload(frames, 7, feats, probs, vols, DATES, onsets, off)
        assert pay["predictions"] == plain["predictions"] and pay["live_corrections"] == {"version": LR.VERSION, "enabled": False, "source": "env"}
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
    zones (labels independent of the feed) the live rules are no worse than the model
    alone at the 50% line."""
    import json
    res = json.loads((ROOT / "reports" / "2026-09_live_replay.json").read_text())
    assert res["self_check_worst_delta"] <= 0.001, res["self_check_worst_delta"]
    assert set(res["variants"]) >= {"plain", LR.VERSION, "no_downgrade", "no_samples", "cso_flags_only"}
    for name, v in res["variants"].items():
        for t_ in ("0.25", "0.5"):
            g = v["grades"][t_]
            assert {"combined", "discharge", "posted"} <= set(g) and all("bayside" in g[r] for r in ("combined", "discharge", "posted")), (name, t_)
    plain, live = res["variants"]["plain"]["grades"]["0.5"]["combined"]["bayside"], res["variants"][LR.VERSION]["grades"]["0.5"]["combined"]["bayside"]
    assert live["cost"] <= plain["cost"] and live["tp"] >= plain["tp"], (plain, live)
    assert res["variants"]["plain"]["days_changed"] == {z: 0 for z in res["variants"]["plain"]["days_changed"]}
    assert (ROOT / "reports" / "2026-09_live_replay.html").exists()
    # the synthetic-feed replay over the out-of-sample years: same shape, degraded variants are means over draws
    syn = json.loads((ROOT / "reports" / "2026-09_live_replay_synthetic.json").read_text())
    # the artifact stores p and risks at 3 dp; with the served outfall split (shares × volume weights over an
    # 8-day product) the recomposition from rounded p lands within 0.002 of the stored risk, so the check is
    # "within rounding", 0.0025, not 0.001 (which held for the v1 composition alone)
    assert syn["self_check_worst_delta"] <= 0.0025 and syn["cutoff"] == "start" and len(syn["feed"]["seeds"]) >= 3
    assert {"plain", LR.VERSION, LR.VERSION + "_perfect_feed", "no_downgrade", "no_samples", "cso_flags_only", "all_floors"} <= set(syn["variants"])
    pl, lv, pf = (syn["variants"][k]["grades"]["0.5"]["combined"] for k in ("plain", LR.VERSION, LR.VERSION + "_perfect_feed"))
    assert lv["tp"] > pl["tp"] and pf["cost"] <= lv["cost"], (pl["tp"], lv["tp"], pf["cost"], lv["cost"])   # more confirmed-persistence days caught; the perfect feed is the bound
    assert syn["feed"]["draws"] and all(d["dropped"] > 0 and d["lagged"] > 0 for d in syn["feed"]["draws"])
    assert (ROOT / "reports" / "2026-09_live_replay_synthetic.html").exists()


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
