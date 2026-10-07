#!/usr/bin/env python3
"""forecast_uncertainty — the weather forecast's own misses in the percentage. Research: nothing served reads it.

TODO.md "Forecast uncertainty into the percentage" (Chase, 2026-10-07: "OK I'm down"). A lead-L percentage today
is the overflow model (S2) on ICON's rain, as if exactly that will fall. It will not, and the model is steep where
it matters: a forecast of 0.6" that comes in at 1.1" is a discharge, at 0.3" is not. The honest number averages
the model over the rain that could fall given the forecast, E[p(rain) | forecast], not p(forecast).

The analog ensemble (the forecast's errors, from the Previous Runs archive alone; GEFS members are the later step):

  analogs   a forecast day at lead ℓ with ICON total f: the N_ANALOGS archived days at lead ℓ whose forecast was
            closest to f (on log1p inches), from seasons other than the scored day's, never after training's end
            (2025-10-31). What the gauges measured on them (the basin's rain source, as S2 reads it) is what fell
            when ICON said about f.
  members   K quantiles of those totals (q = 1/2K, 3/2K, …). Member j of an issue day sets every forecast day
            I … D to its own lead's j-th quantile (one storm's errors move together); the gauge days before I
            stay. add_daily_features (the shared code) reruns on each member's frames exactly as the lead entries
            build theirs (stages_entries: the 13-row frame, the history features from the 35-day one, the dry spell
            carried); D's peak hours scale with D's total (the forecast's own shape; a forecast under PEAK_MIN_IN
            lends the archive's median shape); the south wind stays the forecast's.
  p         the mean of the K members' S2 p.

Members come three ways (``METHODS``; ``quantiles``): the analogs' gauge totals as they fell; the analogs' errors
shifted onto this forecast (log1p); or a fitted error model per lead (P(wet | forecast) and a log-normal spread on
wet days). The first is pulled toward lighter forecasts where heavy ones are rare (two seasons of leads 1–5 hold
about ten forecasts over 1"); the others correct that.

The grade: the served set's S2 rows at L0 … L5 on T1-holdout (2023-07-01 → 2025-10-31; the lead-1 … 5 archive
starts 2024-01-20), scored rows only. Each row's p is recomputed from the holdout fold's weights (stages_s2) on
the unperturbed frames and must equal the build's; the ensemble's p is paired against it on identical rows:
Brier skill vs the protocol's climatology, the storm-block bootstrap (B = 2000, seed 0, 90%), the verdict
(term_lab.cell). Post-training days and the live season are never read: they confirm a design, so they cannot
pick one.

    venv/bin/python features/forecast/src/models/forecast_uncertainty.py            # → data/models/forecast_uncertainty.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402
import rain_features as RF  # noqa: E402
import stages_build as B  # noqa: E402
import stages_entries as E  # noqa: E402
import stages_s2 as S2  # noqa: E402
import term_lab as TL  # noqa: E402  (cell: the paired grade, the stages build's bootstrap)
import train_v4 as T  # noqa: E402
import truth as TR  # noqa: E402

MODEL = "icon_seamless"
LEADS = tuple(E.LEADS)                    # 0 (the stitched short lead) … 5
TIER = "T1-holdout"
K = 20                                    # members: quantile levels (j + 0.5) / K
N_ANALOGS = 40
PEAK_MIN_IN = 0.02                        # a forecast day under this lends the archive's median peak shape
RULES = (RF.GAUGE_OUTAGE_RULE["name"],)
OUT_JSON = T.SERVE_DIR / "forecast_uncertainty.json"
_DAY = pd.Timedelta(days=1)


# ── the forecast's misses ──────────────────────────────────────────────────

def pairs(source: str, lead: int) -> pd.DataFrame:
    """[date, f, o, season]: every complete lead-``lead`` day through training's end, ICON's total and the gauges'."""
    d = E._days(MODEL, lead)
    d = d[(d["status"] == E.OK) & (d.index <= T.TRAIN_END)]
    g = E._gauge(source, RULES)
    out = pd.DataFrame({"date": d.index, "f": d["total"].to_numpy(dtype=float)})
    out["o"] = g.reindex(out["date"]).to_numpy(dtype=float)
    out = out.dropna(subset=["o"]).reset_index(drop=True)
    out["season"] = T.wet_season(out["date"]).to_numpy()
    return out


METHODS = ("analog", "shifted", "emos")
WET_IN = 0.01                             # emos: a measured day under this is dry


def quantiles(f: np.ndarray, pool: pd.DataFrame, k: int = K, n: int = N_ANALOGS, method: str = "analog") -> np.ndarray:
    """[len(f), k]: k members (quantile levels (j + 0.5)/k) of the rain that falls given each forecast f.

    analog   the gauge totals on the n pool days whose forecast was closest to f (every day tied with the n-th
             closest too: a dry forecast reads all the dry forecasts, never an arbitrary 40)
    shifted  the same neighbours' errors on log1p, added to f's own log1p (removes the pull toward lighter
             neighbours where heavy forecasts are rare)
    emos     a fitted error model, one per lead: P(wet | f) logistic on log1p(f) and whether f ≥ WET_IN; on a wet
             day log1p(rain) normal around a line in log1p(f) with the residual spread; a member below the dry
             share is 0"""
    qs = (np.arange(k) + 0.5) / k
    if method == "emos":
        return _emos(f, pool, qs)
    if method not in METHODS:
        raise KeyError(f"unknown method {method!r}; known: {METHODS}")
    pf, po = np.log1p(pool["f"].to_numpy()), pool["o"].to_numpy()
    lo = np.log1p(po)
    out = np.empty((len(f), k))
    for i, x in enumerate(np.log1p(np.asarray(f, dtype=float))):
        d = np.abs(pf - x)
        nb = d <= np.partition(d, min(n, len(d)) - 1)[min(n, len(d)) - 1] + 1e-12
        vals = po[nb] if method == "analog" else np.maximum(0.0, np.expm1(x + lo[nb] - pf[nb]))
        out[i] = np.quantile(vals, qs)
    return out


def _emos(f: np.ndarray, pool: pd.DataFrame, qs: np.ndarray) -> np.ndarray:
    from scipy.stats import norm
    from sklearn.linear_model import LogisticRegression
    x, o = np.log1p(pool["f"].to_numpy()), pool["o"].to_numpy()
    X = np.column_stack([x, pool["f"].to_numpy() >= WET_IN])
    wet = o >= WET_IN
    clf = LogisticRegression(C=1e6, max_iter=5000).fit(X, wet)
    b1, b0 = np.polyfit(x[wet], np.log1p(o[wet]), 1)
    sd = float(np.std(np.log1p(o[wet]) - (b0 + b1 * x[wet]), ddof=2))
    fx = np.log1p(np.asarray(f, dtype=float))
    pw = clf.predict_proba(np.column_stack([fx, np.asarray(f) >= WET_IN]))[:, 1]
    u = (qs[None, :] - (1.0 - pw[:, None])) / pw[:, None]                 # the level inside the wet part
    y = b0 + b1 * fx[:, None] + sd * norm.ppf(np.clip(u, 1e-9, 1 - 1e-9))
    return np.where(u > 0, np.maximum(np.expm1(y), WET_IN), 0.0)


# ── the members' frames (stages_entries' own code path) ────────────────────

def _features(source: str, fc: np.ndarray, issues: pd.DatetimeIndex, lead: int) -> np.ndarray:
    """[issue, DAILY_FEATURES] on D = I + lead from the forecast table ``fc`` [issue, 6]: stages_entries._lead_values
    for a fixed-lead entry (near features from the 13-row frame, history from the 35-day frame, the dry spell
    carried), each issue day's gauges as it knew them (stages_entries._issue_records)."""
    gauge = E._gauge(source, RULES)
    t = E._lead_tables(MODEL, source, RULES)
    recs = E._issue_records(source, RULES)
    near = E._issue_features(gauge, fc, issues, E.SERVED_PAST_DAYS)
    full = E._issue_features(gauge, fc, issues, E.FULL_PAST_DAYS)
    for i, I in enumerate(issues):
        if I in recs:
            near[i] = E._issue_features(recs[I][0], fc[i:i + 1], pd.DatetimeIndex([I]), E.SERVED_PAST_DAYS)[0]
            full[i] = E._issue_features(recs[I][0], fc[i:i + 1], pd.DatetimeIndex([I]), E.FULL_PAST_DAYS)[0]
    col = {f: i for i, f in enumerate(RF.DAILY_FEATURES)}
    hist = [col[f] for f in E.HISTORY_FEATURES]
    vals = near[:, lead, :].copy()
    vals[:, hist] = full[:, lead, hist]
    d = col["dry_spell_days"]
    k = t["issues"].get_indexer(issues)
    before = np.array([recs[I][1] if I in recs else t["dry_before"][j] for I, j in zip(issues, k)])
    whole = full[:, lead, d] == E.FULL_PAST_DAYS + lead + 1
    vals[whole, d] = full[whole, lead, d] + before[whole]
    return vals


def frame(source: str, vals: np.ndarray, dates: pd.DatetimeIndex, peaks: np.ndarray, wind: np.ndarray) -> pd.DataFrame:
    f = pd.DataFrame({"date": dates})
    for i, c in enumerate(RF.DAILY_FEATURES):
        f[c] = np.round(vals[:, i]) if c in E.INT_FEATURES else vals[:, i]
    for i, c in enumerate(RF.INTENSITY_FEATURES):
        f[c] = peaks[:, i]
    f["wind_v_rain"] = wind
    return f


# ── the grade ──────────────────────────────────────────────────────────────

def scored_rows(served: str) -> pd.DataFrame:
    r = pd.read_csv(B.STAGES_DIR / served / "rows.csv.gz", low_memory=False, keep_default_na=False, na_values=[""],
                    parse_dates=["date"])
    r = r[(r["stage"] == "s2") & r["excl"].isna()]
    pool = r[(r["entry"] == "oracle") & (r["tier"] == "T2")][["unit", "date", "y"]].reset_index(drop=True)
    rows = r[r["entry"].isin([f"L{L}" for L in LEADS]) & (r["tier"] == TIER)][["unit", "date", "entry", "tier", "y", "p"]]
    rows = rows.reset_index(drop=True)
    rows["lead"] = rows["entry"].str[1:].astype(int)
    rows["ref"] = B.references(rows, pool)
    rows["block"] = TR.blocks(end=E.data_end()).set_index("date")["block"].reindex(rows["date"]).to_numpy()
    oracle = r[(r["entry"] == "oracle") & (r["tier"] == TIER)].set_index(["unit", "date"])["p"]
    rows["p_oracle"] = oracle.reindex(pd.MultiIndex.from_frame(rows[["unit", "date"]])).to_numpy()
    if rows[["ref", "block"]].isna().any().any():
        raise ValueError("a scored row has no reference or storm block")
    return rows


def _shapes() -> dict:
    """{lead: the archive's median peak shape (rain_max1h/3h/6h ÷ the day's total) on wet forecast days, to training's end}."""
    out = {}
    for L in LEADS:
        pk = E._peaks(MODEL, L)
        tot = E._days(MODEL, L)["total"].reindex(pk.index)
        wet = (tot >= 0.1) & (pk["peak_status"] == E.OK) & (pk.index <= T.TRAIN_END)
        out[L] = np.array([float(np.median(pk.loc[wet, c] / tot[wet])) for c in RF.INTENSITY_FEATURES])
    return out


def lead_ps(L: int, dates: pd.DatetimeIndex, weights: dict, src_of: dict, shape: np.ndarray, method: str = "analog") -> dict:
    """{basin key: (p on the unperturbed frames, the ensemble's p)} on ``dates`` at lead L."""
    issues = dates - L * _DAY
    fc = np.column_stack([E._total(MODEL, j, issues + j * _DAY) for j in range(E.FC_DAYS)])
    if np.isnan(fc[:, :L + 1]).any():
        raise AssertionError(f"L{L}: a scored row's forecast day is not complete")
    pk = E._peaks(MODEL, L).reindex(dates)[list(RF.INTENSITY_FEATURES)].to_numpy(dtype=float)
    wind = E._wind(MODEL, L).reindex(dates).to_numpy(dtype=float)
    seasons = T.wet_season(pd.Series(dates)).to_numpy()
    out = {}
    for source in sorted(set(src_of.values())):
        keys = [k for k in src_of if src_of[k] == source]
        point = frame(source, _features(source, fc, issues, L), dates, pk, wind)
        members = np.empty((len(dates), L + 1, K))
        for j in range(L + 1):
            pool = pairs(source, j)
            for s in np.unique(seasons):
                m = seasons == s
                members[m, j, :] = quantiles(fc[m, j], pool[pool["season"] != s], method=method)
        ens = {k: np.zeros(len(dates)) for k in keys}
        fD = fc[:, L]
        for q in range(K):
            fq = fc.copy()
            fq[:, :L + 1] = members[:, :, q]
            mD = fq[:, L]
            scale = np.where(fD >= PEAK_MIN_IN, mD / np.where(fD > 0, fD, 1.0), np.nan)
            pq = np.where(np.isfinite(scale)[:, None], pk * np.nan_to_num(scale)[:, None], mD[:, None] * shape[None, :])
            fr = frame(source, _features(source, fq, issues, L), dates, pq, wind)
            for k in keys:
                ens[k] += T.calibrated(weights[k], fr) / K
        for k in keys:
            out[k] = (T.calibrated(weights[k], point), ens[k])
    return out


def run(log=print, jobs: int = len(LEADS), method: str = "analog") -> dict:
    from joblib import Parallel, delayed
    served = CAND.served_info()["name"]
    s2set = S2.load_set(served, "served")
    train = S2.training_frames(s2set)
    S2.check_training_record(s2set, train)
    tier, fold, a, b, season, keep = S2._plan((TIER,))[0]
    weights = S2.fit_fold(s2set, train, tier, fold, a, b, season, keep).weights
    rows = scored_rows(served)
    src_of = {k: s2set.models[k]["rain_source"] for k in s2set.keys}
    shape = _shapes()
    days = {L: pd.DatetimeIndex(sorted(rows.loc[rows["lead"] == L, "date"].unique())) for L in LEADS}
    t0 = time.time()
    got = Parallel(n_jobs=jobs, backend="loky")(delayed(lead_ps)(L, days[L], weights, src_of, shape[L], method) for L in LEADS)
    log(f"  {len(LEADS)} leads, {len(rows)} rows ({time.time() - t0:.0f}s)")
    rows["p_point"] = np.nan
    rows["p_ens"] = np.nan
    for L, ps in zip(LEADS, got):
        for k, (pp, pe) in ps.items():
            m = (rows["lead"] == L) & (rows["unit"] == k)
            at = days[L].get_indexer(pd.DatetimeIndex(rows.loc[m, "date"]))
            rows.loc[m, "p_point"] = pp[at]
            rows.loc[m, "p_ens"] = pe[at]
    # rows.csv.gz keeps 6 significant figures: the recomputed p must round to the build's
    rel = float((np.abs(rows["p_point"] - rows["p"]) / np.maximum(np.abs(rows["p"]), 1e-12)).max())
    if not rel < 1e-5:
        raise AssertionError(f"the recomputed point p misses the build's rows by {rel:.1e} (relative): not the lead entries' frames")
    return {"served": served, "rows": rows, "point_gap": rel, "method": method}


def grade(rows: pd.DataFrame) -> dict:
    out = {}
    for L in LEADS:
        r = rows[rows["lead"] == L]
        out[f"L{L}"] = {}
        for unit in ["pooled"] + sorted(r["unit"].unique()):
            m = (r["unit"] == unit) if unit != "pooled" else np.ones(len(r), bool)
            g = r[m]
            c = TL.cell(g["y"], g["p_ens"], g["p_point"], g["ref"], g["block"])
            c["oracle_skill"] = TL.V.bss(g["y"].to_numpy(dtype=float), g["p_oracle"].to_numpy(dtype=float), g["ref"].to_numpy(dtype=float))
            c["mean_p_point"], c["mean_p_ens"], c["base_rate"] = float(g["p_point"].mean()), float(g["p_ens"].mean()), float(g["y"].mean())
            out[f"L{L}"][unit] = c
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    doc = {"schema": "bwtf.forecast_uncertainty/1", "model": MODEL, "tier": TIER, "k": K, "n_analogs": N_ANALOGS,
           "peak_min_in": PEAK_MIN_IN, "wet_in": WET_IN, "methods": {}}
    for method in a.methods.split(","):
        got = run(method=method)
        g = grade(got["rows"])
        print(method)
        for L, cells in g.items():
            c = cells["pooled"]
            print(f"  {L}: n {c['n']} pos {c['pos']}  skill point {c['live_skill']:.3f} → ensemble {c['skill']:.3f} "
                  f"[{c['skill_lo']:.2f}–{c['skill_hi']:.2f}]  Δ Brier {c['delta']:+.5f} [{c['lo']:+.5f}, {c['hi']:+.5f}] {c['verdict']}"
                  f"  (rain known {c['oracle_skill']:.3f}; mean p {c['mean_p_point']:.4f} → {c['mean_p_ens']:.4f}, base {c['base_rate']:.4f})",
                  flush=True)
        doc["served"], doc["point_gap"] = got["served"], got["point_gap"]
        doc["methods"][method] = g
    if not a.no_write:
        OUT_JSON.write_text(json.dumps(doc, indent=1, default=float) + "\n")
        print(f"→ {OUT_JSON}")


if __name__ == "__main__":
    main()
