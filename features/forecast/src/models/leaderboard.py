#!/usr/bin/env python3
"""Model leaderboard — candidate model families × rain sources, per basin,
scored the way train_v4 scores, without touching what is served.

Families
  gb     the production family: GradientBoostingClassifier(**MODEL_PARAMS)
  logit  a weights model: hinge features on the rain inputs → standardise →
         L2 logistic regression. One coefficient per (transformed) feature,
         readable as "how many log-odds per standard deviation". C chosen by
         leave-one-season-out CV on the pre-holdout seasons only.
         The shared-terms variant (make_shared_model, built by shared_logit.py)
         cuts today's and yesterday's rain into bands at bends shared by every
         basin and holds every weight ≥ 0.

Rain sources
  the two production sources per basin (two-gauge mean, local NOAA gauge)
  plus the CoCoRaHS Potrero gauge (US1CASF0017, between Mission Creek and
  Islais Creek, daily since 1998) as filed and shifted a day earlier
  (its observation day ends ~07:00), and 'avg3' = mean of the three.

Scores per candidate: pre-holdout season-CV (pooled predictions) and the
chronological holdout (train < HOLDOUT_START, test after), PR-AUC / ROC-AUC /
Brier, same definitions as train_v4/eval_report.json. Output:
data/models/leaderboard.json and reports/2026-09_model_leaderboard.html
(tables per basin + the weights of the best logistic model).

    venv/bin/python features/forecast/src/models/leaderboard.py [--quick]

--quick skips the season-CV (holdout only) for a fast look.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import train_v4 as T  # noqa: E402
from groups import BASIN_KEYS  # noqa: E402

REPO = HERE.parents[3]
OUT_JSON = T.SERVE_DIR / "leaderboard.json"
OUT_HTML = REPO / "reports" / "2026-09_model_leaderboard.html"

FEATS = T.get_feature_columns_v21()
POTRERO = "US1CASF0017"
EXTRA_SOURCES = [POTRERO, f"{POTRERO}@-1"]   # 'avg3' (three-gauge mean) is a rain_series source too
# hinge knots: max(0, x − knot) on the rain inputs where the response bends
HINGES = {
    "precip_avg": [0.25, 0.5, 1.0], "rain_2d_cum": [0.5, 1.0, 2.0], "rain_3d_cum": [1.0, 2.0],
    "rain_7d_cum": [2.0], "rain_max1h": [0.1, 0.2], "rain_max3h": [0.25, 0.5], "rain_max6h": [0.5, 1.0],
    "antecedent_moisture": [0.2, 0.5], "peak_3d": [0.5, 1.0],
}
HINGE_NAMES = [f"{f}>{k}" for f, ks in HINGES.items() for k in ks]
C_GRID = [0.03, 0.1, 0.3, 1.0, 3.0]


def add_hinges(X):
    """19 base features (DataFrame in FEATS order, or ndarray) → base + hinge columns.
    Lives at module level so a fitted pipeline can be pickled and served with
    the 19-feature contract serving already uses."""
    A = X.values if hasattr(X, "values") else np.asarray(X, dtype=float)
    cols = [A]
    for f, ks in HINGES.items():
        j = FEATS.index(f)
        for k in ks:
            cols.append(np.maximum(0.0, A[:, j] - k)[:, None])
    return np.hstack(cols)


# ── the shared-terms weights model (logit_v2_shared5 / logit_v2_shared8, 2026-09-30) ──
# Every basin asks the same questions (how much rain today, how much yesterday)
# and answers with its own weights. Each rain input is cut into bands at its
# bends: a band holds the rain that fell between one bend and the next, so its
# weight is the slope of the risk inside that band. Every weight is held at zero
# or above, so more rain never lowers the risk (Chase: "no odd weights"). The
# bends are shared by all basins; the weights are each basin's own.
SHARED_DESIGNS = {
    "shared5": {"precip_avg": (0.5, 1.0), "rain_lag1d": (0.5,)},
    "shared8": {"precip_avg": (0.5, 0.75, 1.0, 1.5), "rain_lag1d": (0.25, 0.5)},
    # shared5 + the peak 6-hour burst (Chase: "does 2 day matter or peak intensity?"). A 2-day bend
    # at 1 / 1.5 / 2" drew a zero weight in almost every basin (today + yesterday already sum to it); the
    # burst drew ≈ 2 log-odds per inch in every basin, and 6 h is the burst ICON tracks best vs ERA5.
    "shared6": {"precip_avg": (0.5, 1.0), "rain_lag1d": (0.5,), "rain_max6h": ()},
    # half the served model's 38 terms (Chase: "half as many factors as before but still several"):
    # shared_logit.backward_select dropped whole inputs from all 19 (banded at the served knots), least
    # useful first by pre-holdout season-CV log loss, until 19 terms were left. Drop order: max1h, max3h,
    # max6h, lag3d, antecedent_moisture, 7d, 30d, 14d, lag7d, lag2d. The build re-runs the selection
    # and refuses to save if it no longer lands here.
    "half": {"precip_avg": (0.25, 0.5, 1.0), "rain_2d_cum": (0.5, 1.0, 2.0), "rain_3d_cum": (1.0, 2.0), "rain_5d_cum": (),
             "rain_lag1d": (), "rain_lag5d": (), "wet_prior_3d": (), "peak_3d": (0.5, 1.0), "dry_spell_days": ()},
    # Chase's four inputs (2026-10-01: today, yesterday, 2 days ago, wettest 3 hours), bends searched by
    # shared_logit.choose_bends on pre-holdout season CV: bends on 2-days-ago or the burst scored worse,
    # so they enter as straight lines. The build re-runs the search and refuses to save if it moves.
    "four": {"precip_avg": (0.25, 0.5, 1.0), "rain_lag1d": (0.25, 0.5), "rain_lag2d": (), "rain_max3h": ()},
}


def band_columns(design: dict) -> list:
    """[(feature, lo, hi)] in column order; hi None = open-ended. A key ending in
    ">" keeps only the part above its first knot: a bend with no band below it
    ({"rain_2d_cum>": (1.5,)} → the 2-day total's excess over 1.5")."""
    out = []
    for key, knots in design.items():
        f, edges = (key[:-1], [*knots, None]) if key.endswith(">") else (key, [0.0, *knots, None])
        out += [(f, lo, hi) for lo, hi in zip(edges[:-1], edges[1:])]
    return out


def band_name(f: str, lo: float, hi) -> str:
    return f"{f}:{lo:g}-{hi:g}" if hi is not None else f"{f}:{lo:g}+"


def add_bands(X, design):
    """19 base features (DataFrame, or ndarray in FEATS order) → the rain in each
    band, clip(x − lo, 0, hi − lo). An input's bands sum back to the input."""
    cols = []
    for f, lo, hi in band_columns(design):
        x = X[f].to_numpy(dtype=float) if hasattr(X, "columns") else np.asarray(X, dtype=float)[:, FEATS.index(f)]
        v = np.maximum(0.0, x - lo)
        cols.append(np.minimum(v, hi - lo) if hi is not None else v)
    return np.column_stack(cols)


class NonNegLogit(BaseEstimator, ClassifierMixin):
    """L2 logistic regression with every weight ≥ 0: scikit-learn's objective
    (½‖w‖² + C · Σ log-loss, intercept unpenalised), solved by L-BFGS-B with
    bounds. Same C scale as LogisticRegression, so the C grid means the same."""

    def __init__(self, C: float = 1.0, max_iter: int = 5000):
        self.C = C
        self.max_iter = max_iter

    def fit(self, X, y):
        from scipy.optimize import minimize
        from scipy.special import expit
        X = np.asarray(X, dtype=float)
        s = 2.0 * np.asarray(y).astype(int) - 1.0
        k = X.shape[1]

        def objective(theta):
            w, b = theta[:k], theta[k]
            m = -s * (X @ w + b)
            g = -s * expit(m)                       # d log-loss / d score, per day
            return (0.5 * w @ w + self.C * np.logaddexp(0.0, m).sum(),
                    np.concatenate([w + self.C * (X.T @ g), [self.C * g.sum()]]))

        res = minimize(objective, np.zeros(k + 1), jac=True, method="L-BFGS-B",
                       bounds=[(0.0, None)] * k + [(None, None)],
                       options={"maxiter": self.max_iter, "ftol": 1e-12, "gtol": 1e-8})
        self.classes_ = np.array([0, 1])
        self.coef_ = res.x[:k][None, :]
        self.intercept_ = res.x[k:]
        self.n_iter_ = np.array([res.nit])
        return self

    def decision_function(self, X):
        return np.asarray(X, dtype=float) @ self.coef_[0] + self.intercept_[0]

    def predict_proba(self, X):
        from scipy.special import expit
        p = expit(self.decision_function(X))
        return np.column_stack([1.0 - p, p])

    def predict(self, X):
        return (self.decision_function(X) > 0).astype(int)


def make_shared_model(C: float, design: dict):
    """bands → standardise → L2 logistic with weights ≥ 0. Takes the same 19-input
    DataFrame serving already passes, so nothing downstream changes."""
    return Pipeline([("bands", FunctionTransformer(add_bands, kw_args={"design": design}, validate=False)),
                     ("scale", StandardScaler()),
                     ("lr", NonNegLogit(C=C))])


def make_model(family: str, C: float = 0.3):
    if family == "gb":
        return GradientBoostingClassifier(**T.MODEL_PARAMS)
    if family == "logit":
        return Pipeline([("hinges", FunctionTransformer(add_hinges, validate=False)),
                         ("scale", StandardScaler()),
                         ("lr", LogisticRegression(C=C, max_iter=5000))])   # L2 is the default
    raise ValueError(family)


def scores(y, p) -> dict:
    y = np.asarray(y); p = np.asarray(p)
    if y.sum() == 0 or y.sum() == len(y):
        return {}
    return {"n": int(len(y)), "pos": int(y.sum()), "roc_auc": float(roc_auc_score(y, p)),
            "pr_auc": float(average_precision_score(y, p)), "brier": float(brier_score_loss(y, p))}


OOF_COLUMNS = ("date", "season", "y", "p")


def oof_frame(parts: list) -> pd.DataFrame:
    """Pooled out-of-fold predictions, one row per held-out day in fold order:
    date, season (the fold that held it out), y, p. ``parts`` = [(held-out rows, p)]."""
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype=t) for c, t in zip(OOF_COLUMNS, ("datetime64[ns]", "int64", "int64", "float64"))})
    return pd.concat([pd.DataFrame({"date": te["date"].to_numpy(), "season": te["season"].to_numpy(),
                                    "y": te["y"].to_numpy(), "p": p}) for te, p in parts], ignore_index=True)


def season_cv(sub: pd.DataFrame, family: str, C: float, return_oof: bool = False):
    """Leave-one-season-out over the PRE-holdout seasons, pooled predictions.
    ``return_oof`` (stages S2, STAGES_DESIGN.md §8 P7a): also return the pooled
    out-of-fold predictions, (scores, oof_frame); False returns the scores alone."""
    pre = sub[sub["date"] < T.HOLDOUT_START]
    seasons = sorted(pre["season"].unique())
    ys, ps = [], []
    parts = []
    for s in seasons:
        tr, te = pre[pre["season"] != s], pre[pre["season"] == s]
        if tr["y"].sum() < 5 or len(te) == 0:
            continue
        m = make_model(family, C).fit(tr[FEATS], tr["y"])
        ys.append(te["y"].values); ps.append(m.predict_proba(te[FEATS])[:, 1])
        if return_oof:
            parts.append((te, ps[-1]))
    out = scores(np.concatenate(ys), np.concatenate(ps)) if ys else {}
    return (out, oof_frame(parts)) if return_oof else out


def holdout(sub: pd.DataFrame, family: str, C: float):
    tr, te = sub[sub["date"] < T.HOLDOUT_START], sub[sub["date"] >= T.HOLDOUT_START]
    m = make_model(family, C).fit(tr[FEATS], tr["y"])
    return scores(te["y"], m.predict_proba(te[FEATS])[:, 1]), m


def logit_weights(model) -> dict:
    lr, sc = model.named_steps["lr"], model.named_steps["scale"]
    names = FEATS + HINGE_NAMES
    coef = lr.coef_[0]
    return {"intercept": float(lr.intercept_[0]),
            "coef_per_sd": {n: float(c) for n, c in zip(names, coef)},
            "coef_raw_units": {n: float(c / s) if s else 0.0 for n, c, s in zip(names, coef, sc.scale_)},
            "feature_means": {n: float(mu) for n, mu in zip(names, sc.mean_)}}


def main(quick: bool = False) -> dict:
    sources = list(dict.fromkeys(T.RAIN_SOURCES + EXTRA_SOURCES + ["avg3"]))
    frames, notes = T.build_dataset(sources=sources)

    ev_report = json.loads((T.SERVE_DIR / "eval_report.json").read_text())
    prod = ev_report.get("rain_source_chosen", {})
    # the trainer's per-basin archive-ablation decision: a basin whose holdout got
    # worse with the 2016-17 feed labels is fit without them (Westside) — every
    # candidate here gets the same rows the served model was fit on
    use_archive = ev_report.get("stage1_archive_labels", {})
    board = {"generated_at": datetime.now().isoformat(timespec="seconds"), "holdout_start": str(T.HOLDOUT_START.date()),
             "train_end": str(frames["avg"]["date"].max().date()), "families": ["gb", "logit"], "sources": sources,
             "hinges": HINGES, "stage1_archive_labels": use_archive, "basins": {}}
    for basin in T.APP_BASINS:
        key = BASIN_KEYS[basin]
        cands = {src: [] for src in ["avg", T.LOCAL_GAUGE[basin]] + EXTRA_SOURCES + ["avg3"]}
        best_logit = None
        print(f"\n── {basin}" + ("" if use_archive.get(basin, True) else "  (archive labels dropped, as the trainer does)"))
        for src in list(cands):
            sub = T.target_frame(frames[src], basin)
            if not use_archive.get(basin, True):
                sub = sub[sub[f"{basin}_label_source"] != "poobot"]
            n_ev = int(sub["y"].sum())
            for family in ("gb", "logit"):
                Cs = C_GRID if family == "logit" else [None]
                picked, pick_cv = None, None
                if family == "logit" and not quick:
                    for C in Cs:
                        cv = season_cv(sub, family, C)
                        if cv and (pick_cv is None or cv["pr_auc"] > pick_cv["pr_auc"]):
                            picked, pick_cv = C, cv
                if picked is None:
                    picked = 0.3 if family == "logit" else None
                    pick_cv = {} if quick else season_cv(sub, family, picked)
                ho, model = holdout(sub, family, picked)
                row = {"family": family, "source": src, "C": picked, "n_days": len(sub), "n_events": n_ev,
                       "season_cv_pre_holdout": pick_cv, "holdout": ho,
                       "production": bool(family == "gb" and src == prod.get(key))}
                if family == "logit" and ho and (best_logit is None or ho["pr_auc"] > best_logit[0]):
                    best_logit = (ho["pr_auc"], src, picked, logit_weights(model))
                cands[src].append(row)
                print(f"   {family:5} {src:16} C={str(picked):5} events {n_ev:>3}  cv PR {pick_cv.get('pr_auc', float('nan')):.3f}  "
                      f"holdout PR {ho.get('pr_auc', float('nan')):.3f}  ROC {ho.get('roc_auc', float('nan')):.3f}  Brier {ho.get('brier', float('nan')):.4f}"
                      + ("  ← production" if row["production"] else ""), flush=True)
        rows = [r for rs in cands.values() for r in rs]
        rows.sort(key=lambda r: -(r["holdout"].get("pr_auc") or 0))
        board["basins"][key] = {"name": basin, "rows": rows,
                                "best_logit": None if best_logit is None else {"holdout_pr_auc": best_logit[0], "source": best_logit[1], "C": best_logit[2], **best_logit[3]}}
    OUT_JSON.write_text(json.dumps(board, indent=1))
    OUT_HTML.write_text(render_html(board))
    print(f"\nwrote {OUT_JSON.relative_to(REPO)} and {OUT_HTML.relative_to(REPO)}")
    return board


def render_html(b: dict) -> str:
    def f3(v): return "—" if v is None else f"{v:.3f}"
    def f4(v): return "—" if v is None else f"{v:.4f}"
    src_label = {"avg": "two-gauge mean", "SF Downtown": "Downtown 047772", "SF Oceanside": "Oceanside 047767",
                 POTRERO: "Potrero CoCoRaHS (as filed)", f"{POTRERO}@-1": "Potrero CoCoRaHS (shifted −1 day)", "avg3": "three-gauge mean"}
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forecast model leaderboard</title><link rel="icon" href="/static/brand/favicon.ico">
<style>body{{margin:0;background:#E3EBF2;color:#26272a;font-family:Roboto,'Segoe UI',Arial,sans-serif;font-size:14.5px}}.wrap{{max-width:1120px;margin:0 auto;padding:22px}}
header{{background:#0072BC;color:#fff;border-radius:20px;padding:22px 26px;margin-bottom:14px}}h1{{margin:0 0 6px;font-size:28px}}section{{background:#fff;border:1px solid #d9e4e8;border-radius:18px;padding:18px 22px;margin:14px 0}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{padding:6px 8px;border-bottom:1px solid #e3ebf2;text-align:left}}th{{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:#54576F}}td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
.badge{{display:inline-block;border-radius:999px;padding:2px 9px;font-size:11.5px;font-weight:700;background:#e3ebf2;color:#0072BC}}.best{{background:#e0f0ea}}.mute{{color:#54576F}}code{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;background:#eef3f7;padding:1px 5px;border-radius:5px}}
.bar{{display:inline-block;height:9px;border-radius:5px;vertical-align:middle}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:14px}}.card{{background:#f3f6f9;border-radius:12px;padding:12px 14px}}p.fine{{color:#54576F;font-size:12.5px}}</style></head><body><div class="wrap">
<header><h1>Forecast model leaderboard</h1><div>Candidate model families × rain sources per basin, scored like the trainer: leave-one-season-out CV on the pre-holdout seasons, then the chronological holdout from {b['holdout_start']}. Generated {b['generated_at']}; training data through {b['train_end']}. Nothing here is served — the production row is marked.</div></header>
<section><h2>How to read it</h2><p><b>gb</b> is the production family (gradient-boosted trees). <b>logit</b> is the weights model: the 19 features plus hinge terms max(0, x − knot) at the points where the rain response bends, standardised, then L2 logistic regression with C picked by pre-holdout season-CV. PR-AUC is the headline (discharge days are ~2% of days, so ROC-AUC flatters everything); Brier is calibration + sharpness, lower is better. Rain sources: the NOAA pair the models were trained on, the CoCoRaHS Potrero gauge (US1CASF0017, between Mission Creek and Islais Creek, daily since 1998; a missing day falls back to the two-gauge mean) as filed and shifted a day earlier (observers read it at ~07:00), and a three-gauge mean.</p></section>"""]
    for key, bd in b["basins"].items():
        parts.append(f"<section><h2>{bd['name']}</h2><table><tr><th>Family</th><th>Rain source</th><th class='num'>C</th><th class='num'>Events</th><th class='num'>CV PR-AUC</th><th class='num'>Holdout PR-AUC</th><th class='num'>Holdout ROC-AUC</th><th class='num'>Holdout Brier</th></tr>")
        top = bd["rows"][0]["holdout"].get("pr_auc") if bd["rows"] else None
        for r in bd["rows"]:
            ho, cv = r["holdout"], r["season_cv_pre_holdout"]
            cls = " class='best'" if ho.get("pr_auc") == top else ""
            parts.append(f"<tr{cls}><td><b>{r['family']}</b>{' <span class=badge>production</span>' if r['production'] else ''}</td><td>{src_label.get(r['source'], r['source'])}</td><td class='num'>{'' if r['C'] is None else r['C']}</td><td class='num'>{r['n_events']}</td><td class='num'>{f3(cv.get('pr_auc'))}</td><td class='num'><b>{f3(ho.get('pr_auc'))}</b></td><td class='num'>{f3(ho.get('roc_auc'))}</td><td class='num'>{f4(ho.get('brier'))}</td></tr>")
        parts.append("</table>")
        bl = bd.get("best_logit")
        if bl:
            w = sorted(bl["coef_per_sd"].items(), key=lambda kv: -abs(kv[1]))[:14]
            mx = max(abs(v) for _, v in w) or 1
            parts.append(f"<h3>Weights of the best logistic model ({src_label.get(bl['source'], bl['source'])}, C={bl['C']}, holdout PR-AUC {bl['holdout_pr_auc']:.3f})</h3><p class='fine'>Log-odds change per one standard deviation of the (standardised) feature; intercept {bl['intercept']:.2f} → baseline {100/(1+np.exp(-bl['intercept'])):.2f}% when every feature sits at its training mean. Red raises discharge odds, green lowers them.</p><table>")
            for n, v in w:
                parts.append(f"<tr><td><code>{n}</code></td><td style='width:55%'><span class='bar' style='width:{abs(v)/mx*100:.0f}%;background:{'#b5310a' if v > 0 else '#237059'}'></span></td><td class='num'>{v:+.3f}</td></tr>")
            parts.append("</table>")
        parts.append("</section>")
    parts.append("</div></body></html>")
    return "".join(parts)


def save_best_logit(name: str, note: str = "") -> Path:
    """Fit the leaderboard's best logistic configuration per basin (rain source
    + C, by holdout PR-AUC) on the full training window, plus its holdout-fit
    sibling, and write them as a candidate set the Model check can select.
    Reads data/models/leaderboard.json — run the leaderboard first. The served
    served gb_v1 set is untouched."""
    import candidates
    board = json.loads(OUT_JSON.read_text())
    per_basin, chosen = {}, {}
    for key, bd in board["basins"].items():
        rows = [r for r in bd["rows"] if r["family"] == "logit" and r["holdout"]]
        best = max(rows, key=lambda r: r["holdout"]["pr_auc"])
        per_basin[key] = {"source": best["source"], "C": best["C"], "holdout": best["holdout"],
                          "season_cv_pre_holdout": best["season_cv_pre_holdout"], "n_events": best["n_events"]}
        chosen[bd["name"]] = best["source"]
    chosen["citywide"] = "avg"
    use_archive = board.get("stage1_archive_labels", {})
    frames, _ = T.build_dataset(sources=sorted(set(chosen.values()) | {"avg"}))
    finals, holdouts = {}, {}
    for basin in T.APP_BASINS + ["citywide"]:
        key = BASIN_KEYS.get(basin, "citywide")
        C = per_basin[key]["C"] if key in per_basin else float(np.median([v["C"] for v in per_basin.values()]))
        sub = T.target_frame(frames[chosen[basin]], basin)
        if basin != "citywide" and not use_archive.get(basin, True):
            sub = sub[sub[f"{basin}_label_source"] != "poobot"]
        elif basin == "citywide" and not all(use_archive.values()):
            sub = sub[~(sub[[f"{b}_label_source" for b in T.APP_BASINS]] == "poobot").any(axis=1)]
        pre = sub[sub["date"] < T.HOLDOUT_START]
        finals[key] = {"model": make_model("logit", C).fit(sub[FEATS], sub["y"]), "features": FEATS, "calibration_offset": 0.0, "C": C}
        holdouts[key] = make_model("logit", C).fit(pre[FEATS], pre["y"]) if pre["y"].sum() >= 5 else None
        print(f"   {basin:12} source {chosen[basin]:16} C={C}  fit on {len(sub)} days / {int(sub['y'].sum())} events")
    d = candidates.save_candidate(name, "logit", finals, holdouts, chosen, FEATS, per_basin, note=note,
                                  extra={"hinges": HINGES, "C_grid": C_GRID, "leaderboard_generated_at": board["generated_at"]})
    print(f"candidate → {d.relative_to(REPO)} (pickles, manifest, scorecard)")
    return d


if __name__ == "__main__":
    # Dispatch through the importable module, not this __main__ namespace: a
    # fitted logit pipeline pickles a reference to its hinge function by module
    # + name, and `__main__.add_hinges` cannot be unpickled by anything except a
    # rerun of this script. `leaderboard.add_hinges` can (candidates.load_models).
    import leaderboard as _lb
    if "--save" in sys.argv:
        i = sys.argv.index("--save")
        note = sys.argv[sys.argv.index("--note") + 1] if "--note" in sys.argv else ""
        _lb.save_best_logit(sys.argv[i + 1], note)
    else:
        _lb.main(quick="--quick" in sys.argv)
