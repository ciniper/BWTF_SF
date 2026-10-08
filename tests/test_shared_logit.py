"""logit_v2_shared5 / shared6 / shared8 / half / four (2026-09-30 → 10-01): the same terms in every basin, each basin its own weights, no odd weights.

The shared pipeline must take the same 19-input frame serving already passes,
pickle through the importable leaderboard module, and clone for holdout
siblings; every weight must be ≥ 0 so more rain never lowers the risk; every
basin of a set must carry the same terms; the explorer's in-page arithmetic
(bands → standardise → weights) must equal scikit-learn's.
Run: venv/bin/python tests/test_shared_logit.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(ROOT / "features" / "forecast"), str(MODELS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard as L  # noqa: E402
import candidates as C  # noqa: E402

BASINS = ("westside", "north_shore", "central", "southeast", "citywide")
# the saved sets of each design still on disk: the 5-, 6-, 8- and 19-term ones were trimmed 2026-10-08 (STAGES_DESIGN
# Part B 39; tag archive/pre-trim-2026-10-08), the designs themselves stay in leaderboard.SHARED_DESIGNS
SETS = {"logit_v2_four": "four"}


def _sets(stage1: str) -> tuple:
    """The two sets of an overflow model: with stage 2 v1 and with the outfall split (their lineups' ids)."""
    import stage2_variants as SV
    return C.geo_v1_set(stage1, None)[0], C.geo_v1_set(stage1, SV.load_spec("v2"))[0]


def _toy(n=600, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.gamma(0.4, 0.6, size=(n, len(L.FEATS))), columns=L.FEATS)
    y = (X["precip_avg"] + 0.5 * X["rain_lag1d"] + rng.normal(0, 0.3, n) > 1.1).astype(int)
    return X, y


def test_bands_add_back_up_and_both_input_paths_agree():
    X, _ = _toy()
    for key, design in L.SHARED_DESIGNS.items():
        B = L.add_bands(X, design)
        cols = L.band_columns(design)
        assert B.shape[1] == len(cols)
        for f in design:
            idx = [i for i, c in enumerate(cols) if c[0] == f]
            assert np.allclose(B[:, idx].sum(axis=1), X[f]), (key, f)
        assert np.allclose(L.add_bands(X.values, design), B), key


def test_nonneg_logit_matches_sklearn_when_nothing_binds_and_clips_when_it_would():
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(1)
    X = rng.normal(size=(3000, 4))
    y = (rng.random(3000) < 1 / (1 + np.exp(-(X @ np.array([1.0, 0.5, 2.0, 0.2]) - 2)))).astype(int)
    for c in (0.03, 1.0):
        a, b = LogisticRegression(C=c, max_iter=5000).fit(X, y), L.NonNegLogit(C=c).fit(X, y)
        assert np.abs(a.coef_ - b.coef_).max() < 2e-3 and abs(a.intercept_[0] - b.intercept_[0]) < 2e-3
    X[:, 1] *= -1                                 # the true weight is now negative → held at 0
    b = L.NonNegLogit(C=1.0).fit(X, y)
    assert b.coef_[0, 1] == 0.0 and (b.coef_ >= 0).all()


def test_shared_pipeline_keeps_the_19_input_contract_pickles_and_clones():
    from sklearn.base import clone
    X, y = _toy(seed=2)
    m = L.make_shared_model(0.3, L.SHARED_DESIGNS["shared8"]).fit(X, y)
    assert m.named_steps["lr"].coef_.shape == (1, 8) and (m.named_steps["lr"].coef_ >= 0).all()
    back = pickle.loads(pickle.dumps(m))
    assert np.allclose(back.predict_proba(X)[:, 1], m.predict_proba(X)[:, 1])
    c = clone(m).fit(X, y)
    assert c.named_steps["bands"].kw_args["design"] == m.named_steps["bands"].kw_args["design"]
    assert np.allclose(c.predict_proba(X)[:, 1], m.predict_proba(X)[:, 1])


def test_explorer_export_is_exact_for_a_shared_model():
    import export_model_explorer as E
    X, y = _toy(seed=3)
    m = L.make_shared_model(1.0, L.SHARED_DESIGNS["shared5"]).fit(X, y)
    ex = E.export_logit(m, list(L.FEATS))
    assert ex["nonneg"] and len(ex["coef"]) == len(ex["bands"]) == 5 and min(ex["coef"]) >= 0
    # the page's arithmetic: clip(x − lo, 0, hi − lo) per band, standardised, times its weight, plus the intercept
    H = np.column_stack([np.clip(X[b["f"]].values - b["lo"], 0, np.inf if b["hi"] is None else b["hi"] - b["lo"]) for b in ex["bands"]])
    z = (H - np.array(ex["means"])) / np.array(ex["scales"])
    p = 1 / (1 + np.exp(-(ex["intercept"] + z @ np.array(ex["coef"]))))
    assert np.allclose(p, m.predict_proba(X)[:, 1], atol=1e-9)


def _frame(today, yday):
    row = {f: 0.0 for f in L.FEATS}
    row.update(precip_avg=today, rain_lag1d=yday)
    return pd.DataFrame([row])


def test_the_saved_sets_share_their_terms_and_never_lower_the_risk_with_more_rain():
    names = {m["name"] for m in C.list_candidates()}
    assert {n for s1 in SETS for n in _sets(s1)} <= names, names
    assert not any(n.startswith("logit_v2_small") for n in names), "logit_v2_small was retired 2026-09-30"
    grid = np.round(np.arange(0, 6.01, 0.05), 2)
    for name, dk in SETS.items():
        terms = [L.band_name(*c) for c in L.band_columns(L.SHARED_DESIGNS[dk])]
        for set_name in _sets(name):
            models = C.load_models(set_name)
            assert set(BASINS) <= set(models), (set_name, sorted(models))
            for key in BASINS:
                md = models[key]
                assert md["family"] == "logit" and md["features"] == list(L.FEATS)
                assert md["terms"] == terms, (set_name, key, md["terms"])           # the same terms in every basin
                w = md["model"].named_steps["lr"].coef_[0]
                assert (w >= 0).all(), (set_name, key, w)                           # no odd weights
                for yday in (0.0, 0.25, 0.5, 1.0, 2.0):                             # more rain today never lowers the risk …
                    p = np.array([md["model"].predict_proba(_frame(t, yday))[0, 1] for t in grid])
                    assert (np.diff(p) >= -1e-12).all(), (set_name, key, yday)
                for today in (0.0, 0.5, 1.0, 2.0):                                   # … nor more rain yesterday
                    p = np.array([md["model"].predict_proba(_frame(today, y))[0, 1] for y in grid])
                    assert (np.diff(p) >= -1e-12).all(), (set_name, key, today)
        # both sets of a design share one stage 1, and the second carries the outfall split
        a, b = (C.load_models(n) for n in _sets(name))
        X, _ = _toy(seed=4)
        for key in BASINS:
            assert np.allclose(a[key]["model"].predict_proba(X)[:, 1], b[key]["model"].predict_proba(X)[:, 1])
        assert C.load_stage2(_sets(name)[0]) is None and (C.load_stage2(_sets(name)[1]) or {}).get("variant") == "v2"
        for set_name in _sets(name):
            sc = C.load_scorecard(set_name)
            assert sc.get("input_rules_post") == ["gauge_outage_v1"] and any(d.get("post_training") for d in sc["days"])


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
