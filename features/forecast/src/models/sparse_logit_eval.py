#!/usr/bin/env python3
"""Could stage 1 be simpler? The served weights model has 38 terms per basin
(19 inputs + 19 bends). This refits the same design with fewer terms and asks
what it costs, on the same days and labels the served model was judged on:

    full38     every term, the served C                      (the served design)
    raw19      the 19 inputs, no bends
    topK       the K terms the served model weighs most (K = 12, 8, 5, 3)
    l1         an L1 fit that chooses its own terms, C by season cross-validation
    tiny4      today's total, its bend at half an inch, the last two days, the wettest hour

Two exams per basin, both out of sample: the holdout (fit on the seasons before
Jul 2023, scored Jul 2023 → Oct 2025) and since training (fit through Oct 2025,
scored on the days after, with the gauge-outage rule as serving applies it).
Season cross-validation before the holdout says how stable each design is.

Writes features/forecast/data/models/sparse_logit_eval.json; the how-it-works
report renders it. Chase, 2026-09-30: "simpler is king".
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for p in (str(REPO), str(FORECAST), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard as L  # noqa: E402
import train_v4 as T  # noqa: E402
from candidates import served_info  # noqa: E402
from groups import BASIN_KEYS  # noqa: E402

_main = sys.modules.get("__main__")
if _main is not None and not hasattr(_main, "add_hinges"):
    _main.add_hinges = L.add_hinges

MODEL_DIR = FORECAST / "data" / "models"
OUT_JSON = MODEL_DIR / "sparse_logit_eval.json"
NAMES = list(L.FEATS) + list(L.HINGE_NAMES)
L1_GRID = [0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
TOPS = (12, 8, 5, 3)
TINY = ["precip_avg", "precip_avg>0.5", "rain_2d_cum", "rain_max1h"]
POST_END = pd.Timestamp("2026-08-31")


def design(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(L.add_hinges(df[L.FEATS]), columns=NAMES, index=df.index)


def fit(X: pd.DataFrame, y, C: float, l1: bool = False) -> Pipeline:
    lr = LogisticRegression(C=C, penalty="l1", solver="liblinear", max_iter=5000) if l1 else LogisticRegression(C=C, max_iter=5000)
    return Pipeline([("scale", StandardScaler()), ("lr", lr)]).fit(X, y)


def season_cv(sub: pd.DataFrame, X: pd.DataFrame, cols: list, C: float, l1: bool = False) -> dict:
    pre = sub["date"] < T.HOLDOUT_START
    ys, ps = [], []
    for s in sorted(sub.loc[pre, "season"].unique()):
        tr = pre & (sub["season"] != s)
        te = pre & (sub["season"] == s)
        if sub.loc[tr, "y"].sum() < 5 or te.sum() == 0:
            continue
        m = fit(X.loc[tr, cols], sub.loc[tr, "y"], C, l1)
        ys.append(sub.loc[te, "y"].values)
        ps.append(m.predict_proba(X.loc[te, cols])[:, 1])
    return L.scores(np.concatenate(ys), np.concatenate(ps)) if ys else {}


def exam(sub: pd.DataFrame, X: pd.DataFrame, cols: list, C: float, split: pd.Timestamp, l1: bool = False) -> tuple:
    tr, te = sub["date"] < split, sub["date"] >= split
    m = fit(X.loc[tr, cols], sub.loc[tr, "y"], C, l1)
    sc = L.scores(sub.loc[te, "y"], m.predict_proba(X.loc[te, cols])[:, 1]) if te.sum() else {}
    nz = [c for c, w in zip(cols, m.named_steps["lr"].coef_[0]) if abs(w) > 1e-9]
    return sc, nz


def run() -> dict:
    sv = served_info()
    sources = sv.get("rain_sources") or {}
    ev = json.loads((T.SERVE_DIR / "eval_report.json").read_text()) if (T.SERVE_DIR / "eval_report.json").exists() else {}
    use_archive = ev.get("stage1_archive_labels", {})
    trained_through = pd.Timestamp(sv.get("trained_through") or T.TRAIN_END)
    srcs = sorted(set(sources.get(k, "avg") for k in BASIN_KEYS.values()))
    frames_h, _ = T.build_dataset(sources=srcs)                                              # the training record, for the holdout exam
    frames_p, _ = T.build_dataset(end=POST_END, sources=srcs, input_rules=["gauge_outage_v1"])  # through today, rain as serving treats it
    out = {"generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "holdout_start": str(T.HOLDOUT_START.date()),
           "trained_through": str(trained_through.date()), "post_end": str(POST_END.date()), "n_terms_full": len(NAMES), "basins": {}}
    for basin, key in BASIN_KEYS.items():
        src, C = sources.get(key, "avg"), float((sv.get("per_basin") or {}).get(key, {}).get("C") or 0.1)
        with open(MODEL_DIR / f"{key}_model.pkl", "rb") as f:
            served = pickle.load(f)
        coef = served["model"].named_steps["lr"].coef_[0]
        ranked = [NAMES[i] for i in np.argsort(-np.abs(coef))]

        def prep(frames):
            sub = T.target_frame(frames[src], basin)
            if not use_archive.get(basin, True):
                sub = sub[sub[f"{basin}_label_source"] != "poobot"]
            sub = sub.reset_index(drop=True)
            return sub, design(sub)

        sub_h, X_h = prep(frames_h)
        sub_p, X_p = prep(frames_p)
        variants = [("full38", NAMES, C, False), ("raw19", list(L.FEATS), C, False)] + \
                   [(f"top{k}", ranked[:k], C, False) for k in TOPS] + [("tiny4", TINY, C, False)]
        rows = []
        for name, cols, c, l1 in variants:
            ho, nz_h = exam(sub_h, X_h, cols, c, T.HOLDOUT_START, l1)
            po, _ = exam(sub_p, X_p, cols, c, trained_through + pd.Timedelta(days=1), l1)
            cv = season_cv(sub_h, X_h, cols, c, l1)
            rows.append({"name": name, "terms": cols, "n_terms": len(cols), "C": c, "cv": cv, "holdout": ho, "post": po})
            print(f"{key:12} {name:7} {len(cols):2d} terms  cv PR {cv.get('pr_auc', float('nan')):.3f}  holdout PR {ho.get('pr_auc', float('nan')):.3f} Brier {ho.get('brier', float('nan')):.4f}  since PR {po.get('pr_auc', float('nan')):.3f} Brier {po.get('brier', float('nan')):.4f}", flush=True)
        # L1: let the fit choose, C by season CV
        best = None
        for c in L1_GRID:
            cv = season_cv(sub_h, X_h, NAMES, c, l1=True)
            if cv and (best is None or cv["pr_auc"] > best[1]["pr_auc"]):
                best = (c, cv)
        if best:
            c, cv = best
            ho, nz = exam(sub_h, X_h, NAMES, c, T.HOLDOUT_START, l1=True)
            po, nz_p = exam(sub_p, X_p, NAMES, c, trained_through + pd.Timedelta(days=1), l1=True)
            rows.append({"name": "l1", "terms": nz, "n_terms": len(nz), "C": c, "cv": cv, "holdout": ho, "post": po, "terms_post": nz_p})
            print(f"{key:12} l1      {len(nz):2d} terms  C={c}  cv PR {cv.get('pr_auc', float('nan')):.3f}  holdout PR {ho.get('pr_auc', float('nan')):.3f} Brier {ho.get('brier', float('nan')):.4f}  since PR {po.get('pr_auc', float('nan')):.3f} Brier {po.get('brier', float('nan')):.4f}  → {nz}", flush=True)
        out["basins"][key] = {"name": basin, "source": src, "C": C, "ranked": ranked,
                              "n_holdout_events": int(sub_h.loc[sub_h["date"] >= T.HOLDOUT_START, "y"].sum()),
                              "n_post_events": int(sub_p.loc[sub_p["date"] > trained_through, "y"].sum()), "rows": rows}
    OUT_JSON.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT_JSON.relative_to(REPO)}")
    return out


if __name__ == "__main__":
    run()
