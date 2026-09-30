#!/usr/bin/env python3
"""logit_v2_small — the weights model with a handful of terms per basin.

Chase, 2026-09-30: "simpler is king". sparse_logit_eval.py showed 8 terms match
all 38 and 3–5 hold within a few points, but its "top K" rows were ranked by the
served model, which had seen the holdout. This picks the terms honestly:

    for each basin (and citywide), forward stepwise over the 38 terms (the 19
    inputs and their 19 bends), each step adding the term — and C from the grid —
    that most raises the pooled leave-one-season-out PR-AUC on the seasons BEFORE
    the holdout; up to MAX_TERMS terms; then keep the smallest set whose CV score
    is within PARSIMONY of the best set seen (simpler wins ties).

Nothing after Jul 2023 is looked at while choosing. Same rows as the served
fit (the per-basin archive-label decision from eval_report), same rain source
per basin as the served set, so the only change is the terms. The final models
are fit on the whole training window, holdout siblings on the pre-holdout rows,
and saved as the candidate set `logit_v2_small` (stage 2 v1); pair it with the
outfall split with

    venv/bin/python features/forecast/src/models/stage2_variants.py save --stage1 logit_v2_small --variant v2 --name logit_v2_small_s2v2

Usage: venv/bin/python features/forecast/src/models/small_logit.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for p in (str(REPO), str(FORECAST), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard as L  # noqa: E402
import train_v4 as T  # noqa: E402
import candidates  # noqa: E402
from groups import BASIN_KEYS  # noqa: E402

NAME = "logit_v2_small"
MAX_TERMS = 5
PARSIMONY = 0.005      # keep the smallest set whose CV PR-AUC is within this of the best seen
C_GRID = L.C_GRID


def design(df: pd.DataFrame) -> np.ndarray:
    return L.add_hinges(df[L.FEATS])


def cv_pr_auc(A: np.ndarray, y: np.ndarray, seasons: np.ndarray, pre: np.ndarray, cols: list, C: float) -> float:
    """Pooled leave-one-season-out PR-AUC over the pre-holdout seasons, the leaderboard's rule."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    ys, ps = [], []
    for s in np.unique(seasons[pre]):
        tr, te = pre & (seasons != s), pre & (seasons == s)
        if y[tr].sum() < 5 or te.sum() == 0:
            continue
        m = Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression(C=C, max_iter=5000))]).fit(A[tr][:, cols], y[tr])
        ys.append(y[te]); ps.append(m.predict_proba(A[te][:, cols])[:, 1])
    sc = L.scores(np.concatenate(ys), np.concatenate(ps)) if ys else {}
    return sc.get("pr_auc", 0.0)


def select(sub: pd.DataFrame) -> dict:
    A = design(sub)
    y = sub["y"].values.astype(int)
    seasons = sub["season"].values
    pre = (sub["date"] < T.HOLDOUT_START).values
    chosen, history = [], []
    while len(chosen) < MAX_TERMS:
        best = None
        for j, name in enumerate(L.TERM_NAMES):
            if j in chosen:
                continue
            for C in C_GRID:
                score = cv_pr_auc(A, y, seasons, pre, chosen + [j], C)
                if best is None or score > best[0] + 1e-9:
                    best = (score, j, C)
        chosen.append(best[1])
        history.append({"terms": [L.TERM_NAMES[i] for i in chosen], "C": best[2], "cv_pr_auc": round(best[0], 4)})
        print(f"      step {len(chosen)}: + {L.TERM_NAMES[best[1]]:26} C={best[2]:<5} CV PR-AUC {best[0]:.4f}", flush=True)
    top = max(h["cv_pr_auc"] for h in history)
    pick = next(h for h in history if h["cv_pr_auc"] >= top - PARSIMONY)
    return {"terms": pick["terms"], "C": pick["C"], "cv_pr_auc": pick["cv_pr_auc"], "best_cv_pr_auc": top, "history": history}


def main(dry_run: bool = False):
    sv = candidates.served_info()
    sources = sv.get("rain_sources") or {}
    ev = json.loads((T.SERVE_DIR / "eval_report.json").read_text())
    use_archive = ev.get("stage1_archive_labels", {})
    chosen_src = {b: sources.get(BASIN_KEYS[b], "avg") for b in T.APP_BASINS}
    chosen_src["citywide"] = sources.get("citywide", "avg")
    frames, _ = T.build_dataset(sources=sorted(set(chosen_src.values()) | {"avg"}))
    finals, holdouts, per_basin = {}, {}, {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        sub = T.target_frame(frames[chosen_src[basin]], basin)
        if basin != "citywide" and not use_archive.get(basin, True):
            sub = sub[sub[f"{basin}_label_source"] != "poobot"]
        elif basin == "citywide" and not all(use_archive.values()):
            sub = sub[~(sub[[f"{b}_label_source" for b in T.APP_BASINS]] == "poobot").any(axis=1)]
        sub = sub.reset_index(drop=True)
        print(f"── {basin} ({chosen_src[basin]}): {len(sub)} days, {int(sub['y'].sum())} overflow days", flush=True)
        sel = select(sub)
        pre = sub[sub["date"] < T.HOLDOUT_START]
        post = sub[sub["date"] >= T.HOLDOUT_START]
        ho_model = L.make_small_model(sel["C"], sel["terms"]).fit(pre[L.FEATS], pre["y"]) if pre["y"].sum() >= 5 else None
        ho = L.scores(post["y"], ho_model.predict_proba(post[L.FEATS])[:, 1]) if ho_model is not None else {}
        final = L.make_small_model(sel["C"], sel["terms"]).fit(sub[L.FEATS], sub["y"])
        print(f"   picked {len(sel['terms'])} terms {sel['terms']}  C={sel['C']}  CV {sel['cv_pr_auc']:.4f} (best seen {sel['best_cv_pr_auc']:.4f})  "
              f"holdout PR-AUC {ho.get('pr_auc', float('nan')):.3f} Brier {ho.get('brier', float('nan')):.4f}", flush=True)
        finals[key] = {"model": final, "features": list(L.FEATS), "calibration_offset": 0.0, "C": sel["C"], "terms": sel["terms"]}
        holdouts[key] = ho_model
        per_basin[key] = {"source": chosen_src[basin], "C": sel["C"], "terms": sel["terms"], "selection": sel["history"],
                          "season_cv_pre_holdout": {"pr_auc": sel["cv_pr_auc"]}, "holdout": ho, "n_events": int(sub["y"].sum())}
    if dry_run:
        print(json.dumps({k: {"terms": v["terms"], "C": v["C"], "holdout_pr_auc": v["holdout"].get("pr_auc")} for k, v in per_basin.items()}, indent=1))
        return
    note = (f"Weights model on a few terms per basin (Chase 2026-09-30: simpler is king): forward stepwise over the 38 terms "
            f"(19 inputs + 19 bends) by pooled leave-one-season-out PR-AUC on the pre-holdout seasons, ≤ {MAX_TERMS} terms, "
            f"smallest set within {PARSIMONY} of the best. Served rain sources and training rows. Stage 2 v1.")
    d = candidates.save_candidate(NAME, "logit", finals, holdouts, {b: s for b, s in chosen_src.items()}, list(L.FEATS), per_basin,
                                  note=note, extra={"hinges": L.HINGES, "selection_rule": {"max_terms": MAX_TERMS, "parsimony": PARSIMONY, "C_grid": C_GRID}},
                                  stage1_name=NAME)
    print(f"candidate → {d.relative_to(REPO)}")


if __name__ == "__main__":
    # dispatch through the importable module so the pickles reference leaderboard.*, not __main__
    import small_logit as _sl
    _sl.main(dry_run="--dry-run" in sys.argv)
