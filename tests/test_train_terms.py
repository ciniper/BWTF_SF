"""The term-list candidates (src/models/train_terms.py): the pipeline is the term lab's design, every fold refits on
its own nested pick, the two linger tables share one overflow model, and the stages build grades what the lab
graded. Offline. Run: venv/bin/python tests/test_train_terms.py
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "features/forecast/src/models"), str(ROOT / "features/forecast/src/collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import candidates as CAND  # noqa: E402
import leaderboard as L  # noqa: E402
import stages_build as B  # noqa: E402
import stages_entries as E  # noqa: E402
import stages_s2 as S2  # noqa: E402
import term_lab as TL  # noqa: E402
import train_terms as TT  # noqa: E402

NAMES = [TT.set_name(k, s) for k in TT.SIZES for s in TT.STAGE2]
PEAKS = [TT.set_name(k, s, added) for k, added, s in TT.PEAK_SETS]


def _manifest(name: str) -> dict:
    return json.loads((CAND.candidate_dir(name) / "manifest.json").read_text())


def test_the_term_pipeline_is_the_labs_design():
    f = E.frames("oracle", ["avg"], start="2023-01-01", end="2023-03-31")["avg"]
    terms = ["precip_avg", "precip_avg>0.25", "rain_4d_cum", "rain_4d_cum>1.0", "wind_v_rain", "rain_lag1d", "rain_max3h>0.25"]
    lab = TL.design(f.assign(rain_4d_cum=f["rain_3d_cum"] + f["rain_lag3d"]), terms)
    assert np.array_equal(L.add_terms(f, terms), lab)
    assert L.term_inputs(["rain_4d_cum>1.0", "wind_v_rain", "precip_avg>0.25"]) == ["precip_avg", "rain_3d_cum", "rain_lag3d", "wind_v_rain"]
    m = L.make_terms_model(["precip_avg", "wind_v_rain"], 0.3).set_params(terms__kw_args={"terms": ["precip_avg"]})
    assert m.named_steps["terms"].kw_args == {"terms": ["precip_avg"]}       # a fold swaps its terms in place


def test_each_fold_refits_on_its_own_nested_pick():
    for name in NAMES:
        man = _manifest(name)
        sel = json.loads((CAND.candidate_dir(name) / TT.SELECTION_FILE).read_text())
        k = man["term_selection"]["size"]
        assert man["terms"] == sel["terms"][:k] and len(man["terms"]) == k, name
        assert man["fold_terms"] == {f: ts[:k] for f, ts in sel["by_fold"].items()}, name
        assert all(ts[:len(TT.MUST)] == TT.MUST for ts in [man["terms"], *man["fold_terms"].values()]), name
        assert all(t in TT.servable(TL.ALL_TERMS) for ts in man["fold_terms"].values() for t in ts), name
        s = S2.load_set(name, "candidates")
        assert s.fold_terms == man["fold_terms"] and s.train_record == man["record"], name
        need = L.term_inputs(sorted({t for ts in [man["terms"], *man["fold_terms"].values()] for t in ts}))
        for key, m in s.models.items():
            assert m["model"].named_steps["terms"].kw_args["terms"] == man["terms"] and m["features"] == need, (name, key)


def test_the_peak_set_is_the_8_terms_plus_the_3_hour_peak():
    """Chase, 2026-10-07: "the current 8 term set that you selected PLUS the 3 hour peak". The finals are the 8-term
    set's terms plus rain_max3h; every graded fold has its own 8 picks plus the peak (a fold that picked it takes its
    next pick), 9 terms each; the term lab's nested grade is the stages build's S2 score, as for every size."""
    for name, (k, added, suffix) in zip(PEAKS, TT.PEAK_SETS):
        man, base = _manifest(name), _manifest(TT.set_name(k, suffix))
        assert man["terms"] == base["terms"] + TT.ADDED[added] and man["term_selection"]["added"] == TT.ADDED[added]
        sel = json.loads((CAND.candidate_dir(name) / TT.SELECTION_FILE).read_text())
        for f, ts in man["fold_terms"].items():
            assert len(ts) == k + len(TT.ADDED[added]) and set(TT.ADDED[added]) <= set(ts), (name, f)
            if not set(TT.ADDED[added]) & set(sel["by_fold"][f][:k]):
                assert ts == sel["by_fold"][f][:k] + TT.ADDED[added], (name, f)
        assert man["fold_terms"]["2016-17"][:8] == [t for t in sel["by_fold"]["2016-17"] if t != "rain_max3h"][:8]
        assert man["lineup"]["s2"] == TT.stage1_name(k, added) and man["stage2"]["variant"] == TT.STAGE2[suffix]
        assert man["tags"]["post_seen"] == base["tags"]["post_seen"]
        s = S2.load_set(name, "candidates")
        assert s.fold_terms == man["fold_terms"] and all(m["terms"] == man["terms"] for m in s.models.values())
        p = B.STAGES_DIR / name / "scores.json"
        if p.exists():
            sc = json.loads(p.read_text())
            for tier in TL.TIERS:
                got, lab = sc["s2"]["pooled"]["oracle"][tier]["bss"], man["term_selection"]["lab_grade"][tier]["skill"]
                assert abs(got - lab) < 1e-5, (name, tier, got, lab)


def test_both_tables_share_one_overflow_model():
    for k in TT.SIZES:
        a, b = (CAND.load_models(TT.set_name(k, s)) for s in TT.STAGE2)
        assert set(a) == set(b)
        for key in a:
            assert pickle.dumps(a[key]["model"]) == pickle.dumps(b[key]["model"]), (k, key)
        assert _manifest(TT.set_name(k, "s2v2"))["stage2"]["variant"] == "v2"
        assert _manifest(TT.set_name(k, "s2v2d10"))["stage2"]["variant"] == "v2_d10"


def test_the_stage_build_grades_what_the_lab_graded():
    """The lab's nested grade at each size is the stages build's S2 score of that candidate (the lab is the build's
    S2, fold by fold), and its T2 stays labelled selection-contaminated (the forced terms and sizes saw it)."""
    for name in NAMES:
        p = B.STAGES_DIR / name / "scores.json"
        if not p.exists():
            print(f"SKIP {name}: not built yet")
            continue
        sc, lab = json.loads(p.read_text()), _manifest(name)["term_selection"]["lab_grade"]
        for tier in TL.TIERS:
            got = sc["s2"]["pooled"]["oracle"][tier]["bss"]
            assert abs(got - lab[tier]["skill"]) < 1e-5, (name, tier, got, lab[tier]["skill"])
        assert sc["windows"]["T2"].get("label", S2.T2_LABEL) == S2.T2_LABEL, name
        why = _manifest(name)["tags"]["post_seen"]           # the record was chosen with post-training scores in view
        assert sc["windows"]["T1"]["tag"] == "post_seen" and sc["windows"]["T1"]["post_seen"] == why, name
        prim = {r["id"]: r for r in sc["primaries"]["rows"]}
        assert f"post_seen (protocol §2): {why}" in prim["OUT"]["caveat"], name      # OUT is decided on post-training days


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
