#!/usr/bin/env python3
"""term_lab — try overflow-model (S2) term sets in seconds, graded the way the stages build grades S2. Local only.

    venv/bin/python features/forecast/src/models/term_lab.py            # the lab page, http://localhost:8095
    venv/bin/python features/forecast/src/models/term_lab.py --check    # the fidelity checks, then exit

Pick terms (the 19 inputs, their hinges, and the new inputs below), a C, whether every weight must be ≥ 0, and a
training record. The lab fits one logistic model per basin on those terms (the same terms in every basin), fold by
fold exactly as stages_s2 refits a set: each of the nine T2 seasons left out in turn, and the holdout fit on the days
before 2023-07-01. It grades the out-of-fold predictions on the live set's own scored S2 rows
(data/models/stages/<served>/rows.csv.gz): the same basin-days, truth, exclusions, reference climatology, storm
blocks and bootstrap (B = 2000, seed 0) as the stages build. A lab number is the number the stages build would give
the same model's S2, and its change against the live model is paired on identical rows. ``check`` proves it: the
live design refit in the lab reproduces the stored predictions, skills and ranges.

Post-training days and the live season are never shown. They are the confirmation windows (STAGES_PROTOCOL.md §2):
a model picked by looking at them could no longer be confirmed on them.

"Choose for me" is nested (protocol §2: new design choices are made inside the folds). Inside each graded season's
fold, terms are added one at a time while they lower the inner leave-one-season-out Brier score of all four basins
together, so its score is honest for the procedure. The same procedure on all nine seasons gives the terms to adopt.

New inputs, from ERA5's hourly rain and wind at the city point (data/raw/hourly_rain_openmeteo*.csv, openmeteo_wind_hourly.csv):

    rain_max24h        the largest running 24-hour rain ending on the day (a storm across midnight counts once)
    rain_west          the day's rain × the share of ERA5's rain that fell while the wind blew from the west
    wind_u_rain        the rain-weighted west → east wind, m/s (positive: from the west)
    wind_v_rain        the rain-weighted south → north wind, m/s (positive: from the south)
    max3h_after_wet    the day's 3-hour peak when the three days before were wet

The live page and the stages build do not compute them yet, so a model that uses one can be graded here but not
saved, served or stage-scored until they do (TODO.md).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import candidates as CAND  # noqa: E402
import leaderboard as L  # noqa: E402
import stages_build as B  # noqa: E402  (references: the protocol's climatology for a row's fold)
import stages_entries as E  # noqa: E402
import stages_s2 as S2  # noqa: E402
import train_v4 as T  # noqa: E402
import truth as TR  # noqa: E402
import verify as V  # noqa: E402

PORT = 8095
TIERS = ("T2", "T1-holdout")                 # never T1 or T0: the confirmation windows
WINDOW_WORDS = {"T2": "Nine seasons", "T1-holdout": "Holdout"}
N_BOOT, SEED, LEVEL = B.B_PROTOCOL, B.SEED, B.LEVEL
OLDER = {"labels": "csd_pre2018", "day_rule": "every", "start": "2011-03-01"}   # OLDER_REPORTS.md's best record
RECORDS = {"served": None, "older11": OLDER}
RECORD_WORDS = {"served": "CIWQS record (from 2016)", "older11": "+ older reports (from 2011, all days)"}
WIND_CSV = T.RAW_DIR / "openmeteo_wind_hourly.csv"      # collectors/historical.py --wind

BASE = list(L.FEATS)
NEW = ["rain_max24h", "rain_west", "wind_u_rain", "wind_v_rain", "max3h_after_wet"]
HINGES = {**L.HINGES, "rain_max24h": [0.75, 1.0], "rain_west": [0.5, 1.0]}
LIVE_TERMS = BASE + list(L.HINGE_NAMES)      # the live 38-weight design, in add_hinges' column order


def _with_hinges(names: list) -> list:
    return [t for n in names for t in [n] + [f"{n}>{k}" for k in HINGES.get(n, [])]]


GROUPS = [
    ("The day's rain", _with_hinges(["precip_avg"])),
    ("The days before", _with_hinges(["rain_2d_cum", "rain_3d_cum", "rain_5d_cum", "rain_7d_cum", "rain_14d_cum",
                                      "rain_30d_cum", "rain_lag1d", "rain_lag2d", "rain_lag3d", "rain_lag5d",
                                      "rain_lag7d", "antecedent_moisture", "wet_prior_3d", "peak_3d", "dry_spell_days"])),
    ("Peak hours", _with_hinges(["rain_max1h", "rain_max3h", "rain_max6h"])),
    ("New", _with_hinges(NEW)),
]
ALL_TERMS = [t for _, ts in GROUPS for t in ts]
PRESETS = {
    "Live (38 weights)": LIVE_TERMS,
    "The 19 inputs": BASE,
    "Rain and peaks": ["precip_avg", "rain_2d_cum", "rain_max3h", "rain_max6h", "antecedent_moisture"],
    "The engineer's rules": ["precip_avg", "rain_max3h>0.5", "max3h_after_wet", "rain_max24h>0.75", "rain_max24h>1.0",
                             "rain_west", "antecedent_moisture"],
}
assert set(LIVE_TERMS) <= set(ALL_TERMS) and all(set(v) <= set(ALL_TERMS) for v in PRESETS.values())


# ── the new inputs ──────────────────────────────────────────────────────────

def hourly(older: bool) -> pd.DataFrame:
    """ERA5 hourly rain (inches) and wind at the city point, local clock; the 2011–2015 rain in front when ``older``."""
    h = pd.read_csv(T.RAW_DIR / "hourly_rain_openmeteo.csv", parse_dates=["timestamp"])[["timestamp", "precip_inches"]]
    if older:
        o = pd.read_csv(T.OLDER_HOURLY_CSV, parse_dates=["timestamp"])[["timestamp", "precip_inches"]]
        h = pd.concat([o[o["timestamp"] < h["timestamp"].min()], h], ignore_index=True)
    w = pd.read_csv(WIND_CSV, parse_dates=["timestamp"])
    return h.merge(w, on="timestamp", how="left").sort_values("timestamp").reset_index(drop=True)


def wind_uv(speed, direction) -> tuple:
    """(u, v): the west → east and south → north parts of a wind of ``speed`` blowing FROM ``direction`` degrees
    (meteorological: 270 is a west wind, so u > 0; 180 is a south wind, so v > 0)."""
    th = np.radians(np.asarray(direction, dtype=float))
    spd = np.asarray(speed, dtype=float)
    return -spd * np.sin(th), -spd * np.cos(th)


def new_inputs(older: bool = False) -> pd.DataFrame:
    """Per day: rain_max24h, west_share, wind_u_rain, wind_v_rain (see the module notes). A day with under 0.005″ of
    ERA5 rain has no rain to weigh the wind by: its shares and winds are 0."""
    h = hourly(older)
    r = h["precip_inches"].astype(float).fillna(0.0).to_numpy()
    u, v = wind_uv(h["wind_speed_ms"].astype(float).fillna(0.0), h["wind_dir_deg"].astype(float).fillna(0.0))
    d = pd.DataFrame({"date": h["timestamp"].dt.normalize(), "r": r, "roll24": pd.Series(r).rolling(24, min_periods=1).sum(),
                      "ru": r * u, "rv": r * v, "rw": r * (u > 0)})
    g = d.groupby("date").agg(tot=("r", "sum"), rain_max24h=("roll24", "max"), ru=("ru", "sum"), rv=("rv", "sum"), rw=("rw", "sum"))
    wet = g["tot"] >= 0.005
    out = pd.DataFrame({"date": g.index, "rain_max24h": g["rain_max24h"].to_numpy()})
    for col, src in (("west_share", "rw"), ("wind_u_rain", "ru"), ("wind_v_rain", "rv")):
        out[col] = np.where(wet, g[src] / g["tot"].where(wet, 1.0), 0.0)
    return out.reset_index(drop=True)


def add_new(frame: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """``frame`` with the new inputs: ``extra`` (new_inputs) by date, then the two built on the frame's own rain."""
    f = frame.merge(extra, on="date", how="left")
    for c in ("rain_max24h", "west_share", "wind_u_rain", "wind_v_rain"):
        if f[c].isna().any():
            raise ValueError(f"{c} is missing on {f.loc[f[c].isna(), 'date'].iloc[0].date()}: refetch the hourly files")
    f["rain_west"] = f["precip_avg"] * f["west_share"]
    f["max3h_after_wet"] = f["rain_max3h"] * f["wet_prior_3d"]
    return f


def design(frame: pd.DataFrame, terms: list) -> np.ndarray:
    """The terms as columns: an input as it is, ``x>k`` as max(0, x − k) (leaderboard.add_hinges' hinge)."""
    cols = []
    for t in terms:
        name, _, knot = t.partition(">")
        x = frame[name].to_numpy(dtype=float)
        cols.append(np.maximum(0.0, x - float(knot)) if knot else x)
    return np.column_stack(cols)


def fit(X: np.ndarray, y: np.ndarray, C: float, nonneg: bool):
    """Standardise → L2 logistic, as leaderboard.make_model; NonNegLogit when every weight must be ≥ 0."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    lr = L.NonNegLogit(C=C) if nonneg else LogisticRegression(C=C, max_iter=5000)
    return Pipeline([("scale", StandardScaler()), ("lr", lr)]).fit(X, y)


# ── the bootstrap, vectorized (verify._replicates' own draws) ──────────────

def boot_counts(blocks: np.ndarray, n: int = N_BOOT, seed: int = SEED) -> tuple:
    """(inverse block index per row, n × K resample counts): verify._replicates draws rng.integers(0, K, size=K) per
    replicate from one generator, which is the same stream as one (n, K) draw, over np.unique's sorted blocks."""
    _, inv = np.unique(np.asarray(blocks), return_inverse=True)
    k = int(inv.max()) + 1 if len(inv) else 0
    picks = np.random.default_rng(seed).integers(0, k, size=(n, k))
    counts = np.bincount((picks + k * np.arange(n)[:, None]).ravel(), minlength=n * k).reshape(n, k)
    return inv, counts.astype(float)


def _sums(inv, k, *rows) -> list:
    return [np.bincount(inv, weights=r, minlength=k) for r in rows]


def cell(y, a, b, ref, blocks, n_boot: int = N_BOOT) -> dict:
    """One unit × window: the lab's skill (BSS vs the reference) with its 90% range, the live model's skill, and the
    paired Δ Brier (lab − live) with its range and verdict, as verify.scores_bundle / paired_delta compute them."""
    y, a, b, ref = (np.asarray(x, dtype=float) for x in (y, a, b, ref))
    inv, counts = boot_counts(blocks, n_boot)
    k = counts.shape[1]
    ea, eb, er, one = (a - y) ** 2, (b - y) ** 2, (ref - y) ** 2, np.ones(len(y))
    sa, sb, sr, sn = (counts @ s for s in _sums(inv, k, ea, eb, er, one))
    with np.errstate(all="ignore"):
        skill_reps, delta_reps = 1.0 - sa / sr, (sa - sb) / sn
    s_lo, s_hi = V._ci(skill_reps, LEVEL)
    d_lo, d_hi = V._ci(delta_reps, LEVEL)
    delta = float(ea.mean() - eb.mean())
    ap, prev = V.pr_auc(y, a)
    return {"n": int(len(y)), "pos": int(y.sum()), "skill": V.bss(y, a, ref), "skill_lo": float(s_lo),
            "skill_hi": float(s_hi), "live_skill": V.bss(y, b, ref), "delta": delta, "lo": float(d_lo),
            "hi": float(d_hi), "verdict": V.verdict(d_lo, d_hi), "pr": ap, "live_pr": V.pr_auc(y, b)[0]}


# ── the lab ─────────────────────────────────────────────────────────────────

class Lab:
    """The live set's scored S2 rows, their references and blocks, and the input frames, loaded once."""

    def __init__(self):
        self.served = CAND.served_info()["name"]
        s = S2.load_set(self.served, "served")
        self.s2set, self.keys = s, list(s.keys)
        self.names = {k: s.geo.basin(k).name for k in self.keys}
        self.source = {k: s.models[k]["rain_source"] for k in self.keys}
        self.live_C = {k: float(s.models[k]["C"]) for k in self.keys}
        self.end = E.data_end()
        r = pd.read_csv(B.STAGES_DIR / self.served / "rows.csv.gz", low_memory=False, keep_default_na=False, na_values=[""],
                        parse_dates=["date"])
        r = r[(r["stage"] == "s2") & (r["entry"] == "oracle") & r["tier"].isin(TIERS) & r["excl"].isna()]
        rows = r[["unit", "date", "tier", "y", "p"]].rename(columns={"p": "live"}).reset_index(drop=True)
        rows["fold"] = np.where(rows["tier"] == "T2", T.wet_season(rows["date"]).map(S2.season_label), S2.FOLD_HOLDOUT)
        rows["ref"] = B.references(rows, rows[rows["tier"] == "T2"][["unit", "date", "y"]].reset_index(drop=True))
        rows["block"] = TR.blocks(end=self.end).set_index("date")["block"].reindex(rows["date"]).to_numpy()
        if rows[["ref", "block"]].isna().any().any():
            raise ValueError("a scored row has no reference or no storm block")
        self.rows = rows
        extra = new_inputs(older=False)
        sources = sorted(set(self.source.values()))
        self.entry = {src: add_new(f, extra).set_index("date") for src, f in E.frames("oracle", sources, end=self.end).items()}
        self._train, self._masks = {}, {}

    # the training record and folds
    def train(self, record: str) -> dict:
        if record not in self._train:
            rec = RECORDS[record]
            frames, _ = T.build_dataset(sources=sorted(set(self.source.values())), record=rec)
            extra = new_inputs(older=rec is not None and pd.Timestamp(rec["start"]) < T.TRAIN_START)
            self._train[record] = {src: add_new(f, extra) for src, f in frames.items()}
        return self._train[record]

    def plan(self, record: str) -> list:
        rec = RECORDS[record]
        extra = () if rec is None else tuple(range(int(T.wet_season(pd.Series([pd.Timestamp(rec["start"])])).iloc[0]), S2.T2_SEASONS[0]))
        return S2._plan(TIERS, extra)

    def training_rows(self, record: str, key: str, keep=None) -> pd.DataFrame:
        """stages_s2.set_rows on the record: covered days, archive days dropped where the trainer dropped them,
        through the training end, then the fold's filter."""
        name = self.names[key]
        sub = T.target_frame(self.train(record)[self.source[key]], name)
        if not self.s2set.use_archive[name]:
            sub = sub[sub[f"{name}_label_source"] != "poobot"]
        sub = sub[sub["date"] <= T.TRAIN_END]
        return sub[keep(sub)] if keep is not None else sub

    def mask(self, tier: str, fold: str, key: str) -> np.ndarray:
        k = (tier, fold, key)
        if k not in self._masks:
            r = self.rows
            self._masks[k] = ((r["tier"] == tier) & (r["fold"] == fold) & (r["unit"] == key)).to_numpy()
        return self._masks[k]

    def C(self, C, key: str) -> float:
        return self.live_C[key] if C in (None, "live") else float(C)

    def nine(self, record: str):
        """The rows a design is fit on: the nine T2 seasons, plus a longer record's seasons before them."""
        older = RECORDS[record] is not None
        return lambda f: f["season"].isin(S2.T2_SEASONS) | ((f["season"] < S2.T2_SEASONS[0]) if older else False)

    # out-of-fold predictions and the grade
    def oof(self, terms, C="live", nonneg: bool = False, record: str = "served", terms_by_fold: dict | None = None) -> np.ndarray:
        """p on every scored row, each from the fold that never saw it. ``terms_by_fold`` = {fold: terms} (nested)."""
        p = np.full(len(self.rows), np.nan)
        for tier, fold, _start, _stop, _season, keep in self.plan(record):
            ts = list(terms_by_fold[fold] if terms_by_fold else terms)
            for key in self.keys:
                m = self.mask(tier, fold, key)
                if not m.any():
                    continue
                tr = self.training_rows(record, key, keep)
                mdl = fit(design(tr, ts), tr["y"].to_numpy(), self.C(C, key), nonneg)
                ent = self.entry[self.source[key]].loc[self.rows["date"][m]]
                p[m] = mdl.predict_proba(design(ent, ts))[:, 1]
        if not np.isfinite(p).all():
            raise ValueError("a scored row got no prediction")
        return p

    def grade(self, p: np.ndarray, n_boot: int = N_BOOT) -> dict:
        r = self.rows
        out = {}
        for tier in TIERS:
            t = (r["tier"] == tier).to_numpy()
            out[tier] = {}
            for unit in ["pooled"] + self.keys:
                m = t & ((r["unit"] == unit).to_numpy() if unit != "pooled" else True)
                out[tier][unit] = cell(r["y"][m], p[m], r["live"][m], r["ref"][m], r["block"][m], n_boot)
        return out

    def weights(self, terms, C="live", nonneg: bool = False, record: str = "served") -> dict:
        """{basin: {term: weight per standard deviation}} of a fit on all nine seasons (what the model leans on)."""
        out = {}
        for key in self.keys:
            tr = self.training_rows(record, key, self.nine(record))
            lr = fit(design(tr, terms), tr["y"].to_numpy(), self.C(C, key), nonneg).named_steps["lr"]
            out[key] = {t: float(w) for t, w in zip(terms, np.ravel(lr.coef_))}
        return out

    def run(self, terms, C="live", nonneg: bool = False, record: str = "served", n_boot: int = N_BOOT) -> dict:
        bad = [t for t in terms if t not in ALL_TERMS]
        if bad or not terms:
            raise ValueError(f"unknown terms {bad}" if bad else "no terms")
        return {"terms": list(terms), "n_weights": len(terms) * len(self.keys), "grade": self.grade(self.oof(terms, C, nonneg, record), n_boot),
                "weights": self.weights(terms, C, nonneg, record), "new_inputs": [t for t in terms if t.partition(">")[0] in NEW]}

    # choose for me: nested forward selection
    def _selection_data(self, record: str, keep, pool: list, C) -> list:
        """Per basin: (the pool's columns, y, season, C) on a fold's training rows."""
        out = []
        for key in self.keys:
            tr = self.training_rows(record, key, keep)
            out.append((design(tr, pool), tr["y"].to_numpy(), tr["season"].to_numpy(), self.C(C, key)))
        return out

    def choose(self, pool=None, C="live", nonneg: bool = False, record: str = "served", max_terms: int = 8,
               min_gain: float = 0.0025, jobs: int = -1, n_boot: int = N_BOOT) -> dict:
        """Forward selection inside every graded fold (nested), then on all nine seasons (the terms to adopt)."""
        from joblib import Parallel, delayed
        pool = list(pool or ALL_TERMS)
        runs = [(fold, keep) for _tier, fold, *_rest, keep in self.plan(record)] + [("all nine seasons", self.nine(record))]
        jobs_in = [(fold, self._selection_data(record, keep, pool, C)) for fold, keep in runs]
        picked = Parallel(n_jobs=jobs, backend="loky")(
            delayed(forward_select)(data, len(pool), max_terms, min_gain, nonneg) for _fold, data in jobs_in)
        by_fold = {fold: [pool[i] for i in idx] for (fold, _), (idx, _trace) in zip(jobs_in, picked)}
        traces = {fold: tr for (fold, _), (_idx, tr) in zip(jobs_in, picked)}
        final = by_fold.pop("all nine seasons")
        folds = [f for f, _ in runs[:-1]]
        nested = self.oof(None, C, nonneg, record, terms_by_fold={f: by_fold[f] for f in folds})
        counts = {t: sum(t in by_fold[f] for f in folds) for t in pool if any(t in by_fold[f] for f in folds)}
        return {"terms": final, "nested_grade": self.grade(nested, n_boot), "by_fold": by_fold,
                "chosen_in": dict(sorted(counts.items(), key=lambda kv: -kv[1])), "n_folds": len(folds),
                "trace": traces["all nine seasons"], "min_gain": min_gain, "max_terms": max_terms}


def inner_brier(data: list, cols: list, nonneg: bool) -> float:
    """Leave-one-season-out Brier score of all basins together, over the T2 seasons the rows hold."""
    sse, n = 0.0, 0
    for X, y, season, C in data:
        Xc = X[:, cols]
        for s in np.unique(season):
            if s not in S2.T2_SEASONS:
                continue                         # a longer record's older seasons train every inner fold, score none
            te = season == s
            if not te.any() or y[~te].sum() < S2.WEIGHTS_MIN_POSITIVES:
                continue
            p = fit(Xc[~te], y[~te], C, nonneg).predict_proba(Xc[te])[:, 1]
            sse += float(((p - y[te]) ** 2).sum())
            n += int(te.sum())
    return sse / n


def forward_select(data: list, n_pool: int, max_terms: int, min_gain: float, nonneg: bool) -> tuple:
    """Add the pool column that lowers inner_brier most, while it lowers it by more than ``min_gain`` (relative)."""
    chosen, best, trace = [], np.inf, []
    while len(chosen) < max_terms:
        scores = {j: inner_brier(data, chosen + [j], nonneg) for j in range(n_pool) if j not in chosen}
        j, s = min(scores.items(), key=lambda kv: kv[1])
        if np.isfinite(best) and s > best * (1.0 - min_gain):
            break
        chosen.append(j)
        best = s
        trace.append(float(s))
    return chosen, trace


# ── fidelity ────────────────────────────────────────────────────────────────

def check(lab: Lab | None = None) -> dict:
    """The live design refit in the lab reproduces the stored predictions, the stage scores' skill and ranges, and
    a zero change against itself; the vectorized Δ range is verify.paired_delta's."""
    lab = lab or Lab()
    p = lab.oof(LIVE_TERMS, "live", False, "served")
    rel = float(np.max(np.abs(p - lab.rows["live"]) / np.maximum(lab.rows["live"], 1e-9)))
    if rel > 1e-4:
        raise AssertionError(f"the live design refit misses the stored predictions by {rel:.1e} (relative)")
    sc = json.loads((B.STAGES_DIR / lab.served / "scores.json").read_text())
    g = lab.grade(lab.rows["live"].to_numpy())
    worst = 0.0
    for tier in TIERS:
        for unit in ["pooled"] + lab.keys:
            want = sc["s2"][unit]["oracle"][tier]
            got = g[tier][unit]
            worst = max(worst, abs(got["skill"] - want["bss"]), abs(got["skill_lo"] - want["ci"]["bss"][0]),
                        abs(got["skill_hi"] - want["ci"]["bss"][1]), abs(got["delta"]), abs(got["lo"]), abs(got["hi"]))
    if worst > 1e-5:                 # rows.csv.gz holds p to 6 significant digits; scores.json was scored before rounding
        raise AssertionError(f"the lab's grade of the live rows misses scores.json by {worst:.1e}")
    r = lab.rows[lab.rows["tier"] == "T2"]
    alt = r["live"].to_numpy() * 1.1
    mine = cell(r["y"], alt, r["live"], r["ref"], r["block"])
    theirs = V.paired_delta(r["y"], alt, r["live"], r["block"], n=N_BOOT, seed=SEED, level=LEVEL)
    gap = max(abs(mine["lo"] - theirs["lo"]), abs(mine["hi"] - theirs["hi"]), abs(mine["delta"] - theirs["delta"]))
    if gap > 1e-12:
        raise AssertionError(f"the vectorized Δ range misses verify.paired_delta by {gap:.1e}")
    return {"prediction_rel_gap": rel, "grade_gap": worst, "delta_gap": gap, "rows": int(len(lab.rows))}


# ── the page ────────────────────────────────────────────────────────────────

def meta(lab: Lab) -> dict:
    from shared import lineup as LU
    return {"groups": [{"title": t, "terms": ts} for t, ts in GROUPS], "presets": PRESETS, "new": NEW,
            "basins": [{"key": k, "name": lab.names[k]} for k in lab.keys], "live_C": lab.live_C,
            "records": RECORD_WORDS, "windows": WINDOW_WORDS, "live": LU.words("s2", lab.s2set.stage1),
            "served": lab.served, "C_choices": ["live", 0.01, 0.03, 0.1, 0.3, 1.0]}


def make_app(lab: Lab):
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    app.json.sort_keys = False               # the windows, presets and records keep their order on the page

    def args():
        a = request.get_json(force=True) or {}
        return {"C": a.get("C", "live"), "nonneg": bool(a.get("nonneg")), "record": a.get("record", "served")}

    @app.get("/")
    def page():
        return PAGE

    @app.get("/api/meta")
    def api_meta():
        return jsonify(meta(lab))

    @app.post("/api/grade")
    def api_grade():
        a = request.get_json(force=True) or {}
        try:
            return jsonify(lab.run(a.get("terms") or [], **args()))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/choose")
    def api_choose():
        a = request.get_json(force=True) or {}
        return jsonify(lab.choose(a.get("pool") or None, max_terms=int(a.get("max_terms", 8)), **args()))

    return app


PAGE = (HERE / "term_lab.html").read_text() if (HERE / "term_lab.html").exists() else "term_lab.html is missing"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="run the fidelity checks and exit")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args(argv)
    lab = Lab()
    if a.check:
        print(json.dumps(check(lab), indent=1))
        return
    print(f"term lab: {len(lab.rows):,} scored basin-days of {lab.served}; http://localhost:{a.port}")
    make_app(lab).run(host="127.0.0.1", port=a.port, debug=False, threaded=False)


if __name__ == "__main__":
    main()
