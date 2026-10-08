"""gauge_outage_v2: a dead gauge's days read from MRMS at its cell (STAGES_DESIGN Part B 40, 41; RAIN_SOURCES.md).

Pins: the rule is S1's part, not the overflow model's (Part B 41: the sets are iconmrms-t9wind3h-…, the live design
refit on that rain); the rule is opt-in (every default path reads gauge_outage_v1 or the raw record, as before); it masks v1's runs
and changes nothing before MRMS starts (Oct 2020); after, a gauge's blank day reads MRMS at that gauge's cell; the
entries and the training frames read the same rain under it; the candidates name it in their manifests and the stages
build reads it from there, pinning the MRMS file; the build's S2 score is the term lab's grade under it; promote.py
refuses a set the live page cannot serve.

    venv/bin/python tests/test_gauge_outage_v2.py
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

import candidates as CAND  # noqa: E402
import promote as PR  # noqa: E402
import rain_features as RF  # noqa: E402
import stages_entries as E  # noqa: E402
import train_v4 as T  # noqa: E402

V1, V2 = RF.GAUGE_OUTAGE_RULE["name"], RF.GAUGE_OUTAGE_V2["name"]
SETS = ["iconmrms-t9wind3h-nosplit-lt1-bflags", "iconmrms-t9wind3h-osplit-lt2-bflags", "iconmrms-t9wind3h-osplit-lt2more-bflags"]
S1_PART = "icon_seamless_mrmsfill"
STAGES = ROOT / "features/forecast/data/models/stages"
MRMS_START = pd.Timestamp("2020-10-15")


def test_the_rule_is_stage_ones_part():
    from shared import lineup as LU
    assert LU.GAUGE_RULE == V1 and set(r for _, r in LU.S1_RAIN.values()) <= set(RF.INPUT_RULES_KNOWN)
    assert LU.s1_part("icon_seamless", V2) == S1_PART and LU.s1_part("icon_seamless") == LU.s1_part("icon_seamless", V1) == "icon_seamless"
    assert LU.weather_of(S1_PART) == "icon_seamless" and LU.rain_rule_of(S1_PART) == V2 and LU.rain_rule_of("ecmwf_ifs025") == V1
    assert LU.code("s1", S1_PART) == "iconmrms" and not any("mrms" in c for c in LU.CODES["s2"].values())
    for old, new in zip([n.replace("iconmrms-t9wind3h", "icon-t9wind3hmrms") for n in SETS], SETS):
        assert LU.current_name(old) == new
    try:
        LU.s1_part("gfs_seamless", V2)
    except KeyError:
        pass
    else:
        raise AssertionError("an S1 part with no words was made up")


def test_the_rule_is_known_opt_in_and_alone():
    assert RF.INPUT_RULES_LIVE == [V1] and RF.INPUT_RULES_SERVABLE == (V1,)          # the live page: unchanged
    assert E._rules(None) == () and E._rules([V2]) == (V2,) and RF.masks_outages([V2]) and not RF.masks_outages([])
    for bad in ([V1, V2], ["gauge_outage_v9"]):
        try:
            E._rules(bad)
        except (KeyError, ValueError):
            continue
        raise AssertionError(f"{bad} was accepted")


def test_v2_masks_v1s_runs_and_reads_mrms_only_where_it_has_the_day():
    m = T.mrms_at_gauges()
    raw, _ = T.rain_series("SF Oceanside", None)
    for src in ("avg", "SF Downtown", "SF Oceanside"):
        a, _ = T.rain_series(src, [V1])
        b, nb = T.rain_series(src, [V2])
        assert a.index.equals(b.index) and nb["outage_runs_masked"] > 0
        assert np.array_equal(a.loc[:MRMS_START - pd.Timedelta(days=1)].to_numpy(), b.loc[:MRMS_START - pd.Timedelta(days=1)].to_numpy()), src
    # February 2026, the dead Oceanside gauge: v1 reads Downtown's rain at Oceanside, v2 MRMS at Ocean Beach
    feb = slice("2026-02-01", "2026-02-25")
    one, _ = T.rain_series("SF Oceanside", [V1])
    two, _ = T.rain_series("SF Oceanside", [V2])
    down, _ = T.rain_series("SF Downtown", [V1])
    assert np.allclose(one.loc[feb], down.loc[feb]) and np.allclose(two.loc[feb], m["SF Oceanside"].loc[feb])
    assert raw.loc[feb].sum() == 0.0                                                # the gauge read 0.00 all through


def test_the_entries_and_the_training_frames_read_the_same_rain_under_v2():
    frames, _ = T.build_dataset(sources=["avg", "SF Downtown"], input_rules=[V2])
    ent = E.frames("rain", ["avg", "SF Downtown"], input_rules=[V2])
    for src in ("avg", "SF Downtown"):
        a = frames[src].set_index("date")
        b = ent[src].set_index("date")
        days = a.index.intersection(b.index)
        assert len(days) > 3000
        for col in RF.DAILY_FEATURES:
            assert np.allclose(a.loc[days, col].to_numpy(float), b.loc[days, col].to_numpy(float)), (src, col)
    assert ent["avg"].attrs["input_rules"] == [V2]
    assert E.frames("rain", ["avg"])["avg"].attrs["input_rules"] == [V1]           # the default is v1, as ever


def test_the_candidates_name_the_rule_and_the_build_reads_it():
    import stages_build as B
    import stages_s2 as S2
    for name in SETS:
        man = json.loads((CAND.candidate_dir(name) / "manifest.json").read_text())
        assert man["input_rules"] == man["train_input_rules"] == man["input_rules_post"] == [V2], name
        assert man["lineup"]["s1"] == S1_PART and man["lineup"]["s2"] == "logit_wind8_max3h_older11" and "post_seen" in man["tags"]
        bundle = B.load_set(name, "candidates")
        assert B.input_rules(bundle) == (V2,) and S2.load_set(name, "candidates").train_input_rules == (V2,)
        assert B.weather_model(bundle) == "icon_seamless" and B.components(bundle)["s1"] == S1_PART
        built = json.loads((STAGES / name / "manifest.json").read_text())
        assert built["input_rules"] == [V2] and str(T.MRMS_DAILY_CSV.relative_to(ROOT)) in built["inputs"], name
        assert built["weather_model"] == "icon_seamless" and built["components"]["s1"] == S1_PART, name
        prim = json.loads((STAGES / name / "scores.json").read_text())["primaries"]
        assert prim["changed"]["s1"] is False and prim["changed"]["s2"] is True, name    # S2 is fit and scored on its rain
    served = json.loads((STAGES / CAND.served_info()["name"] / "manifest.json").read_text())
    assert served["input_rules"] == [V1] and str(T.MRMS_DAILY_CSV.relative_to(ROOT)) not in served["inputs"]


def test_the_fold_refits_reproduce_the_finals_on_v2_rain():
    import stages_s2 as S2
    s = S2.load_set(SETS[-1], "candidates")
    S2.check_training_record(s, S2.training_frames(s))               # raises unless the record is the set's own


def test_the_builds_s2_score_is_the_labs_grade_under_v2():
    for name in SETS:
        man = json.loads((CAND.candidate_dir(name) / "manifest.json").read_text())
        sc = json.loads((STAGES / name / "scores.json").read_text())
        for tier in ("T2", "T1-holdout"):
            got, lab = sc["s2"]["pooled"]["oracle"][tier]["bss"], man["term_selection"]["lab_grade"][tier]["skill"]
            assert abs(got - lab) < 1e-5, (name, tier, got, lab)


def test_promote_refuses_a_rule_the_live_page_cannot_apply():
    man = json.loads((CAND.candidate_dir(SETS[-1]) / "manifest.json").read_text())
    try:
        PR.check_servable(man)
    except SystemExit as e:
        assert "gauge_outage_v2" in str(e) and "live page" in str(e)
    else:
        raise AssertionError("promote.py would serve gauge_outage_v2 with no MRMS on the live page")
    PR.check_servable({"name": "x", "input_rules": [V1]})
    PR.check_servable({"name": "x"})
    assert {"input_rules", "train_input_rules"} <= set(PR.CARRIED)


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
