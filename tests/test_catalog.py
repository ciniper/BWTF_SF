"""The model catalog (src/models/export_catalog.py → data/models/catalog.json): current, every set on disk once with the
live one first, each named by its lineup, and its weights the pickles' own (the catalog's numbers reproduce every
logistic basin model's p). Offline. Run: venv/bin/python tests/test_catalog.py
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "features/forecast/src/models"), str(ROOT / "features/forecast/src/collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import candidates as CAND  # noqa: E402
import export_catalog as XC  # noqa: E402
import stages_candidates as SC  # noqa: E402
from shared import lineup as LU  # noqa: E402

_CAT = {}


def catalog() -> dict:
    if not _CAT:
        _CAT["disk"] = json.loads(XC.OUT.read_text())
    return _CAT["disk"]


def test_the_catalog_is_current():
    assert XC.OUT.read_text() == json.dumps(XC.build(), indent=1, ensure_ascii=False, allow_nan=False) + "\n", \
        "data/models/catalog.json is stale: run export_catalog.py"


def test_every_set_is_listed_once_by_its_lineup():
    c = catalog()
    names = [s["name"] for s in c["sets"]]
    sv = CAND.served_info()
    want = {sv["name"]} | {m["name"] for m in CAND.list_candidates()} | {m["name"] for m in SC.list_sets()}
    assert len(names) == len(set(names)) == c["n_sets"] and set(names) == want
    assert c["live"] == names[0] == sv["name"] and [s["status"] for s in c["sets"]].count("live") == 1
    for s in c["sets"]:
        lineup = {"geography": {"BWTF basins": "geo_v1", "SFPUC basins": "sfpuc4_v1"}[s["basins"]],
                  **{k: v["id"] for k, v in s["parts"].items()}}
        assert LU.set_id(lineup) == s["name"] and s["words"] == LU.full_words(lineup), s["name"]
        assert s["status"] in ("live", "candidate", "retired") and s["overflow_model"], s["name"]


def _pickles(entry: dict) -> dict:
    if entry["status"] == "live":
        return {k: pickle.load(open(CAND.SERVE_DIR / f"{k}_model.pkl", "rb")) for k in entry["overflow_model"]}
    if entry["basins"] == "SFPUC basins":
        st = SC.load_set(entry["name"])
        return dict(st.models, **({"citywide": st.citywide} if st.citywide else {}))
    return CAND.load_models(entry["name"])


def test_the_weights_reproduce_every_logistic_model():
    """p from the catalog's intercept and each term's weight, mean and sd, on the pipeline's own term columns, is the
    pickle's predict_proba to 1e-9: the catalog lists the weights the forecast runs."""
    from sklearn.pipeline import Pipeline
    rng = np.random.default_rng(0)
    checked = 0
    for entry in catalog()["sets"]:
        for key, m in _pickles(entry).items():
            cat = entry["overflow_model"][key]
            if cat["family"] != "logistic":
                assert cat["n_trees"] == m["model"].n_estimators, (entry["name"], key)
                continue
            X = pd.DataFrame(rng.gamma(0.5, 0.8, size=(40, len(m["features"]))), columns=m["features"])
            pipe = m["model"]
            assert [n for n, _ in pipe.steps[-2:]] == ["scale", "lr"], pipe.steps
            cols = Pipeline(pipe.steps[:-2]).transform(X) if len(pipe.steps) > 2 else X.to_numpy()
            w = np.array([t["weight"] for t in cat["terms"]])
            mu = np.array([t["mean"] for t in cat["terms"]])
            sd = np.array([t["sd"] for t in cat["terms"]])
            p = 1.0 / (1.0 + np.exp(-(cat["intercept"] + ((np.asarray(cols, float) - mu) / sd) @ w)))
            assert np.allclose(p, pipe.predict_proba(X)[:, 1], rtol=0, atol=1e-9), (entry["name"], key)
            checked += 1
    assert checked >= 80, checked   # 86 on 2026-10-08, after the trim (Part B 39)


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
