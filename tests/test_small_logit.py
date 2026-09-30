"""logit_v2_small (2026-09-30): the weights model on a few terms per basin.

The small pipeline must take the same 19-input frame serving already passes,
pickle through the importable leaderboard module, export to the explorer as all
38 weights with zeros on the unselected terms (the page's arithmetic then equals
sklearn's), and be refit for a holdout sibling by cloning its own design.
Run: venv/bin/python tests/test_small_logit.py
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


def _toy(n=400, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.gamma(0.4, 0.5, size=(n, len(L.FEATS))), columns=L.FEATS)
    y = (X["precip_avg"] + 0.5 * X["rain_2d_cum"] + rng.normal(0, 0.3, n) > 1.2).astype(int)
    return X, y


def test_small_pipeline_keeps_the_19_input_contract_and_pickles():
    X, y = _toy()
    terms = ["precip_avg", "precip_avg>0.5", "rain_2d_cum"]
    m = L.make_small_model(0.3, terms).fit(X, y)
    assert m.named_steps["lr"].coef_.shape == (1, 3)
    back = pickle.loads(pickle.dumps(m))
    assert np.allclose(back.predict_proba(X)[:, 1], m.predict_proba(X)[:, 1])
    assert L.TERM_NAMES[:19] == list(L.FEATS) and len(L.TERM_NAMES) == 38


def test_explorer_export_is_exact_for_a_small_model():
    import export_model_explorer as E
    X, y = _toy(seed=1)
    m = L.make_small_model(0.3, ["precip_avg", "rain_max3h>0.25", "peak_3d>1.0"]).fit(X, y)
    ex = E.export_logit(m, list(L.FEATS))
    assert len(ex["coef"]) == 38 and sum(1 for c in ex["coef"] if c != 0.0) == 3
    assert ex["selected"] == ["precip_avg", "rain_max3h>0.25", "peak_3d>1.0"]
    # the page's arithmetic: every column standardised with the exported mean/sd, times its weight, plus the intercept
    H = L.add_hinges(X[L.FEATS])
    z = (H - np.array(ex["means"])) / np.array(ex["scales"])
    p = 1 / (1 + np.exp(-(ex["intercept"] + z @ np.array(ex["coef"]))))
    assert np.allclose(p, m.predict_proba(X)[:, 1], atol=1e-9)


def test_the_saved_candidate_sets():
    names = {m["name"] for m in C.list_candidates()}
    assert {"logit_v2_small", "logit_v2_small_s2v2"} <= names, names
    for name in ("logit_v2_small", "logit_v2_small_s2v2"):
        models = C.load_models(name)
        assert set(BASINS) <= set(models), (name, sorted(models))
        for key in BASINS:
            md = models[key]
            assert md["family"] == "logit" and md["features"] == list(L.FEATS)
            assert 1 <= len(md["terms"]) <= 5, (name, key, md["terms"])
            assert set(md["terms"]) <= set(L.TERM_NAMES)
            assert md["model"].named_steps["lr"].coef_.shape[1] == len(md["terms"])
    # both sets share the same stage 1, byte for byte in behaviour
    a, b = C.load_models("logit_v2_small"), C.load_models("logit_v2_small_s2v2")
    X, _ = _toy(seed=2)
    for key in BASINS:
        assert np.allclose(a[key]["model"].predict_proba(X)[:, 1], b[key]["model"].predict_proba(X)[:, 1])
    assert C.load_stage2("logit_v2_small") is None and (C.load_stage2("logit_v2_small_s2v2") or {}).get("variant") == "v2"
    sc = C.load_scorecard("logit_v2_small_s2v2")
    assert sc.get("input_rules_post") == ["gauge_outage_v1"] and any(d.get("post_training") for d in sc["days"])


def test_holdout_refit_clones_the_source_design():
    from sklearn.base import clone
    X, y = _toy(seed=3)
    m = L.make_small_model(1.0, ["precip_avg", "rain_14d_cum"]).fit(X, y)
    c = clone(m).fit(X, y)
    assert c.named_steps["select"].kw_args["idx"] == m.named_steps["select"].kw_args["idx"]
    assert np.allclose(c.predict_proba(X)[:, 1], m.predict_proba(X)[:, 1])


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
