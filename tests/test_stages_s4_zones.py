"""S4 per zone (src/models/stages_s4_zones.py): every zone takes the rain curve or the lingering table, picked inside
each fold on its stated score; the two candidates differ from their base in S4 only. Offline.
Run: venv/bin/python tests/test_stages_s4_zones.py
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

import compose_v2 as C  # noqa: E402
import stages_build as SB  # noqa: E402
import stages_candidates as SC  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_s4_zones as Z  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402

SETS = {"sfpuc4_shared8_v3": "s4", "sfpuc4_shared8_v3b": "out"}
BASE = "sfpuc4_shared8_v2"


def test_combine_takes_each_zone_from_its_pick():
    zones = list(ZONES)
    rain = {"background": {"coef": {z: {"intercept": -1.0, **{f: 0.5 for f in Z.S4.FEATURES}} for z in zones}},
            "buckets": {z: {"0_small": 0.9} for z in zones}, "zone_median_mg": {z: 2.0 for z in zones}}
    table = {"coef": {z: {"intercept": -2.0} for z in zones}, "buckets": {z: {"0_small": 0.4} for z in zones},
             "medians": {z: 3.0 for z in zones}}
    pick = {z: ("rain" if i % 2 == 0 else "table") for i, z in enumerate(zones)}
    got = Z.combine(rain, table, pick)
    for z in zones:
        if pick[z] == "rain":
            assert got["coef"][z]["intercept"] == -1.0 and got["buckets"][z]["0_small"] == 0.9 and got["medians"][z] == 2.0
        else:
            assert got["coef"][z] == {"intercept": -2.0, **{f: 0.0 for f in Z.S4.FEATURES}}   # a table zone has no rain term
            assert got["buckets"][z]["0_small"] == 0.4 and got["medians"][z] == 3.0


def test_the_public_numbers_days_are_the_week_after_an_overflow():
    idx = pd.date_range("2020-01-01", periods=12)
    p = pd.DataFrame(0.0, idx, list(ZONES))
    p.loc[idx[2], "east"] = 1.0
    m = Z.after_overflow(C.History(p, p), idx)
    east = m[:, list(ZONES).index("east")]
    assert list(np.flatnonzero(east)) == list(range(3, 10))      # D−7…D−1 holds the overflow, D does not
    assert not m[:, list(ZONES).index("ocean")].any()


def test_each_fold_picks_inside_itself_on_its_score():
    geo = G.get(Z.S4.GEOGRAPHY)
    keys = {(p[0], p[1]) for p in S2._plan(S2.TIERS)}
    for name, criterion in SETS.items():
        st = SC.load_set(name)
        assert st.components["s4"] == Z.KINDS[criterion], name
        spec = st.s4_quality
        recs = spec["fit"]["folds"]
        assert {(r["tier"], r["fold"]) for r in recs} == keys and spec["fit"]["criterion"] == criterion, name
        for r in recs:
            assert r["picked_on"] == criterion
            for z in ZONES:
                b = r["inner_brier"][criterion][z]
                assert r["pick"][z] == ("rain" if b["rain"] < b["table"] else "table"), (name, r["fold"], z)
                if r["pick"][z] == "table":
                    assert all(r["background"][z][f] == 0.0 for f in Z.S4.FEATURES), (name, r["fold"], z)
            if r["tier"] == "T2":                                   # a season's pick never saw the season
                assert int(r["fold"][:4]) not in r["fit_seasons"], (name, r["fold"])
            SB.s4_fold_spec(spec, (r["tier"], r["fold"]), geo)        # compose_v2 reads every fold's spec


def test_the_sets_differ_from_their_base_in_s4_only():
    base = SC.load_set(BASE)
    for name in SETS:
        st = SC.load_set(name)
        for c in ("s1", "s2", "s3", "s5"):
            assert st.components[c] == base.components[c], (name, c)
        assert json.dumps(st.s3_links["links"], sort_keys=True) == json.dumps(base.s3_links["links"], sort_keys=True), name
        import pickle
        for k in base.keys:                                         # the same S2 estimators (the saver re-stamps the files)
            for part in ("models", "volume", "holdout_models", "holdout_volume"):
                a_, b_ = getattr(st, part).get(k), getattr(base, part).get(k)
                assert (a_ is None) == (b_ is None), (name, part, k)
                if a_ is not None:
                    assert pickle.dumps(a_["model"]) == pickle.dumps(b_["model"]), (name, part, k)
        assert "post_seen" in (st.manifest.get("tags") or {}), name


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
