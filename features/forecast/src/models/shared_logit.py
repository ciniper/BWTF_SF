#!/usr/bin/env python3
"""logit_v2_shared5 / shared6 / shared8 — one set of terms for every basin, each
basin its own weights, no odd weights.

Chase, 2026-09-30: the sites should share the same terms (daily rain, bends)
and differ only in their weights; up to 12 terms is fine; no odd weights. The
analysis behind the two designs (scratch, summarised in the analysis report):

  - a discharge day is the day a CIWQS event STARTS, and every one had ≥ 0.10"
    that day; the risk is flat below 0.5", climbs to 1", then levels off;
  - rain in the day or two before raises it (0.75–1" today: 35% after a dry
    spell, 72% after > 0.5" in the two days before); the 1–2 week background
    adds nothing once the last three days are known, and the 14-day total
    counts today twice;
  - the peak-hour inputs trade the holdout for the forward test and are ERA5 in
    training but SFO / ICON live, so they stay out;
  - 5 to 12 shared terms all tie the served 38 within the season-bootstrap
    noise (≈ ±16 cost on 247 discharge days); the served 38 gave Central 0% on
    Dec 31 2022 (5.46") in the fold that had not seen that season.

Design (leaderboard.SHARED_DESIGNS): today's rain (precip_avg) and yesterday's
(rain_lag1d), each cut into bands at shared bends —
    shared5: today 0 / 0.5 / 1",  yesterday 0 / 0.5"            (5 terms)
    shared8: today 0 / 0.5 / 0.75 / 1 / 1.5",  yesterday 0 / 0.25 / 0.5"   (8 terms)
    shared6: shared5 + the peak 6-hour burst (rain_max6h), one weight ≥ 0      (6 terms)
             — Chase asked whether the 2-day total or peak intensity matter: a 2-day
             bend drew a zero weight in almost every basin (today + yesterday already
             sum to it); the burst drew ≈ 2 log-odds per inch everywhere. The hindcast
             scores it on ERA5 bursts; live, today's past hours come from SFO and the
             forecast hours from ICON (6 h vs ERA5: r = 0.73 on wet days since 2024).
A band's weight is the slope of the risk inside it and is held ≥ 0
(leaderboard.NonNegLogit), so more rain never lowers the risk in any basin.
C per basin by pre-holdout leave-one-season-out PR-AUC (the leaderboard rule);
the served rain sources and training rows (per-basin archive-label decision
from eval_report). Holdout siblings fit before Jul 2023, finals on the whole
training window. Each design is saved twice — stage 2 v1 and the outfall split
(v2) — and the post-training days of all four sets are rescored with the
gauge-outage rule, like every other candidate.

Usage: venv/bin/python features/forecast/src/models/shared_logit.py [shared5|shared8 ...] [--dry-run]
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

C_GRID = L.C_GRID


def name_of(design_key: str) -> str:
    """The overflow model's own id (its S2 component, shared/lineup.py); the sets are named by their lineups."""
    return f"logit_v2_{design_key}"


def season_cv(sub: pd.DataFrame, design: dict, C: float, return_oof: bool = False):
    """Pooled leave-one-season-out over the pre-holdout seasons, the leaderboard's rule.
    ``return_oof``: also return the pooled out-of-fold predictions, (scores,
    leaderboard.oof_frame) — date, season, y, p (stages S2, STAGES_DESIGN.md §8 P7a)."""
    pre = sub[sub["date"] < T.HOLDOUT_START]
    ys, ps = [], []
    parts = []
    for s in sorted(pre["season"].unique()):
        tr, te = pre[pre["season"] != s], pre[pre["season"] == s]
        if tr["y"].sum() < 5 or len(te) == 0:
            continue
        m = L.make_shared_model(C, design).fit(tr[L.FEATS], tr["y"])
        ys.append(te["y"].values); ps.append(m.predict_proba(te[L.FEATS])[:, 1])
        if return_oof:
            parts.append((te, ps[-1]))
    out = L.scores(np.concatenate(ys), np.concatenate(ps)) if ys else {}
    return (out, L.oof_frame(parts)) if return_oof else out


def basin_rows(basin: str, frames: dict, chosen_src: dict, use_archive: dict) -> pd.DataFrame:
    """The served training rows for one basin (or citywide): its rain source, its archive-label decision."""
    sub = T.target_frame(frames[chosen_src[basin]], basin)
    if basin != "citywide" and not use_archive.get(basin, True):
        sub = sub[sub[f"{basin}_label_source"] != "poobot"]
    elif basin == "citywide" and not all(use_archive.values()):
        sub = sub[~(sub[[f"{b}_label_source" for b in T.APP_BASINS]] == "poobot").any(axis=1)]
    return sub.reset_index(drop=True)


# ── "half" (Chase 2026-09-30: "a model with half as many factors as before but still several") ──
# Start from all 19 inputs, each banded at the served model's knots (38 terms, the served
# count), and drop one whole input (with its bands) at a time — the one whose removal
# hurts least (lowest mean over the four basins of pre-holdout season-CV log loss, each
# basin at its best C) — until at most HALF_TERMS terms remain. Weights ≥ 0 throughout;
# the same inputs for every basin. Nothing after Jun 2023 is seen while choosing.
HALF_TERMS = 19
FULL_DESIGN = {f: tuple(L.HINGES.get(f, ())) for f in L.FEATS}


def pre_holdout_rows(frames: dict, chosen_src: dict, use_archive: dict) -> dict:
    """{basin: (19-input frame, y, season)} over the seasons before the holdout — all a design search may see."""
    out = {}
    for basin in T.APP_BASINS:
        sub = basin_rows(basin, frames, chosen_src, use_archive)
        pre = sub[sub["date"] < T.HOLDOUT_START]
        out[basin] = (pre[L.FEATS].reset_index(drop=True), pre["y"].values.astype(int), pre["season"].values)
    return out


def mean_cv_log_loss(data: dict, design: dict) -> float:
    """The design-search score: mean over the four basins of pooled leave-one-season-out log loss on
    the pre-holdout seasons, each basin at its best C; scaler + weights ≥ 0 fit inside every fold."""
    from sklearn.metrics import log_loss
    from sklearn.preprocessing import StandardScaler
    per = []
    for basin, (F, y, se) in data.items():
        X = L.add_bands(F, design)
        best = None
        for C in C_GRID:
            p = np.full(len(y), np.nan)
            for s in np.unique(se):
                tr, te = se != s, se == s
                if y[tr].sum() < 5:
                    continue
                sc = StandardScaler().fit(X[tr])
                p[te] = L.NonNegLogit(C=C).fit(sc.transform(X[tr]), y[tr]).predict_proba(sc.transform(X[te]))[:, 1]
            k = ~np.isnan(p)
            ll = log_loss(y[k], np.clip(p[k], 1e-6, 1 - 1e-6))
            best = ll if best is None else min(best, ll)
        per.append(best)
    return float(np.mean(per))


def backward_select(frames: dict, chosen_src: dict, use_archive: dict, target_terms: int = HALF_TERMS) -> tuple[dict, list]:
    data = pre_holdout_rows(frames, chosen_src, use_archive)
    groups = {f: len(L.band_columns({f: k})) for f, k in FULL_DESIGN.items()}

    def score(inputs):
        return mean_cv_log_loss(data, {f: FULL_DESIGN[f] for f in inputs})

    cols = L.band_columns(FULL_DESIGN)
    keep = list(FULL_DESIGN)
    path = [{"inputs": list(keep), "terms": len(cols), "cv_log_loss": score(keep), "dropped": None}]
    print(f"   start: {len(keep)} inputs, {len(cols)} terms, mean season-CV log loss {path[0]['cv_log_loss']:.5f}", flush=True)
    while sum(groups[f] for f in keep) > target_terms and len(keep) > 1:
        best = min(((score([g for g in keep if g != f]), f) for f in keep), key=lambda t: t[0])
        keep.remove(best[1])
        n = sum(groups[f] for f in keep)
        path.append({"inputs": list(keep), "terms": n, "cv_log_loss": best[0], "dropped": best[1]})
        print(f"   drop {best[1]:20} → {len(keep)} inputs, {n} terms, mean season-CV log loss {best[0]:.5f}", flush=True)
    return {f: FULL_DESIGN[f] for f in keep}, path


# ── "four" (Chase 2026-10-01: "just today, yesterday, 2d ago, and wettest 3 hours with the
# number of bends as you choose") ── the inputs are Chase's; the bends are searched. Coordinate
# descent: starting from no bends anywhere, for one input at a time try every option in
# BEND_OPTIONS with the others held, keep the fewest-bend option within BEND_TOL of that input's
# best score (mean_cv_log_loss, pre-holdout seasons only), and loop over the inputs until a full
# round changes nothing.
FOUR_INPUTS = ("precip_avg", "rain_lag1d", "rain_lag2d", "rain_max3h")
BEND_OPTIONS = {
    "precip_avg": [(), (0.5,), (1.0,), (0.5, 1.0), (0.25, 0.5, 1.0), (0.5, 0.75, 1.0), (0.5, 1.0, 1.5), (0.25, 0.5, 0.75, 1.0, 1.5)],
    "rain_lag1d": [(), (0.25,), (0.5,), (1.0,), (0.25, 0.5), (0.5, 1.0)],
    "rain_lag2d": [(), (0.25,), (0.5,), (1.0,), (0.5, 1.0)],
    "rain_max3h": [(), (0.1,), (0.2,), (0.3,), (0.5,), (0.2, 0.4), (0.25, 0.5)],
}
BEND_TOL = 3e-5   # a log-loss gain smaller than this does not earn an extra bend


def choose_bends(frames: dict, chosen_src: dict, use_archive: dict, inputs=FOUR_INPUTS, options=None, max_rounds: int = 4) -> tuple[dict, list]:
    options = options or BEND_OPTIONS
    data = pre_holdout_rows(frames, chosen_src, use_archive)
    design = {f: () for f in inputs}
    cache = {}

    def score(d):
        key = tuple(d[f] for f in inputs)
        if key not in cache:
            cache[key] = mean_cv_log_loss(data, d)
        return cache[key]

    path = [{"round": 0, "design": {f: list(k) for f, k in design.items()}, "cv_log_loss": score(design)}]
    print(f"   start (no bends): mean season-CV log loss {path[0]['cv_log_loss']:.5f}", flush=True)
    for rnd in range(1, max_rounds + 1):
        changed = False
        for f in inputs:
            tried = {opt: score({**design, f: opt}) for opt in options[f]}
            top = min(tried.values())
            pick = min((o for o, s in tried.items() if s <= top + BEND_TOL), key=lambda o: (len(o), tried[o]))
            print(f"   round {rnd} {f:12} " + "  ".join(f"{list(o) or 'none'} {s:.5f}" for o, s in tried.items()) + f"  → {list(pick) or 'none'}", flush=True)
            if pick != design[f]:
                design[f], changed = pick, True
        path.append({"round": rnd, "design": {f: list(k) for f, k in design.items()}, "cv_log_loss": score(design)})
        if not changed:
            break
    return design, path


def slopes(model, design: dict) -> dict:
    """Each band's weight in plain units: log-odds per inch of rain inside the band (≥ 0 by construction)."""
    lr, sc = model.named_steps["lr"], model.named_steps["scale"]
    return {L.band_name(*c): round(float(w / s), 4) for c, w, s in zip(L.band_columns(design), lr.coef_[0], sc.scale_)}


def fit_design(design_key: str, frames: dict, chosen_src: dict, use_archive: dict):
    design = L.SHARED_DESIGNS[design_key]
    terms = [L.band_name(*c) for c in L.band_columns(design)]
    finals, holdouts, per_basin = {}, {}, {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        sub = T.target_frame(frames[chosen_src[basin]], basin)
        if basin != "citywide" and not use_archive.get(basin, True):
            sub = sub[sub[f"{basin}_label_source"] != "poobot"]
        elif basin == "citywide" and not all(use_archive.values()):
            sub = sub[~(sub[[f"{b}_label_source" for b in T.APP_BASINS]] == "poobot").any(axis=1)]
        sub = sub.reset_index(drop=True)
        cvs = {C: season_cv(sub, design, C) for C in C_GRID}
        C = max(C_GRID, key=lambda c: (cvs[c].get("pr_auc", 0.0), -c))   # ties → the smaller C (more shrinkage)
        pre, post = sub[sub["date"] < T.HOLDOUT_START], sub[sub["date"] >= T.HOLDOUT_START]
        ho_model = L.make_shared_model(C, design).fit(pre[L.FEATS], pre["y"]) if pre["y"].sum() >= 5 else None
        ho = L.scores(post["y"], ho_model.predict_proba(post[L.FEATS])[:, 1]) if ho_model is not None else {}
        final = L.make_shared_model(C, design).fit(sub[L.FEATS], sub["y"])
        sl = slopes(final, design)
        print(f"   {basin:12} ({chosen_src[basin]}): {len(sub)} days, {int(sub['y'].sum())} discharge days | C={C} "
              f"CV PR-AUC {cvs[C].get('pr_auc', float('nan')):.3f} | holdout PR-AUC {ho.get('pr_auc', float('nan')):.3f} | "
              + " ".join(f"{ {'precip_avg': 'today', 'rain_lag1d': 'yday'}.get(t.split(':')[0], t.split(':')[0])} {t.split(':')[1]} {v:.2f}" for t, v in sl.items()), flush=True)
        finals[key] = {"model": final, "features": list(L.FEATS), "calibration_offset": 0.0, "C": C, "terms": terms}
        holdouts[key] = ho_model
        per_basin[key] = {"source": chosen_src[basin], "C": C, "terms": terms, "slope_per_inch": sl,
                          "season_cv_pre_holdout": cvs[C], "season_cv_by_C": {str(c): v.get("pr_auc") for c, v in cvs.items()},
                          "holdout": ho, "n_events": int(sub["y"].sum())}
    return finals, holdouts, per_basin, terms


def main(design_keys: list, dry_run: bool = False):
    sv = candidates.served_info()
    sources = sv.get("rain_sources") or {}
    use_archive = json.loads((T.SERVE_DIR / "eval_report.json").read_text()).get("stage1_archive_labels", {})
    chosen_src = {b: sources.get(BASIN_KEYS[b], "avg") for b in T.APP_BASINS}
    chosen_src["citywide"] = sources.get("citywide", "avg")
    frames, _ = T.build_dataset(sources=sorted(set(chosen_src.values()) | {"avg"}))
    import stage2_variants
    for dk in design_keys:
        name = name_of(dk)
        design = L.SHARED_DESIGNS[dk]
        selection = None
        if dk == "half":
            picked, selection = backward_select(frames, chosen_src, use_archive)
            assert picked == design, f"backward selection now picks {picked}, SHARED_DESIGNS['half'] records {design}"
        elif dk == "four":
            picked, selection = choose_bends(frames, chosen_src, use_archive)
            assert picked == design, f"the bend search now picks {picked}, SHARED_DESIGNS['four'] records {design}"
        print(f"── {name}: {json.dumps({f: list(k) for f, k in design.items()})}", flush=True)
        finals, holdouts, per_basin, terms = fit_design(dk, frames, chosen_src, use_archive)
        if dry_run:
            continue
        note = (f"The same {len(terms)} terms in every basin, each basin its own weights (Chase 2026-09-30): today's rain and "
                f"yesterday's, cut into bands at shared bends ({', '.join(terms)}); every weight ≥ 0, so more rain never lowers "
                f"the risk. C per basin by pre-holdout season CV. Served rain sources and training rows. Stage 2 v1.")
        set1, lineup = candidates.geo_v1_set(name, None)
        d = candidates.save_candidate(set1, "logit", finals, holdouts, dict(chosen_src), list(L.FEATS), per_basin, note=note,
                                      extra={"design": {f: list(k) for f, k in design.items()}, "terms": terms, "selection_path": selection,
                                             "constraint": "every weight >= 0", "C_grid": C_GRID},
                                      stage1_name=name, lineup=lineup)
        print(f"candidate → {d.relative_to(REPO)}", flush=True)
        set2 = stage2_variants.save(set1, "v2", None, "").name
        for n in (set1, set2):
            candidates.rescore_post(n)
            print(f"   {n}: post-training days rescored with {candidates.load_scorecard(n).get('input_rules_post')}", flush=True)


if __name__ == "__main__":
    # dispatch through the importable module so the pickles reference leaderboard.*, not __main__
    import shared_logit as _sl
    keys = [a for a in sys.argv[1:] if a in L.SHARED_DESIGNS] or list(L.SHARED_DESIGNS)
    _sl.main(keys, dry_run="--dry-run" in sys.argv)
