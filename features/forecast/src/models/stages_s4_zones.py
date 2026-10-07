#!/usr/bin/env python3
"""stages_s4_zones — S4 per zone: the rain curve where it scores better, the lingering table elsewhere.

Chase, 2026-10-07: "let's do it including rain aware. if it's worse for a zone, then just don't include it for the
zone!" The first rain curve (S4 v3, "Rain curve", sfpuc4_shared8_v1) scored a little better on its own stage but
made the public number worse for East (0.49 vs 0.64 over the nine seasons). It was fit on first-look samples only,
leaving out the resample days after an exceedance that the public number grades, so it under-predicted how long
East's bad water lingers. This module fits two S4s in every fold of the served plan, and each zone takes one,
chosen inside the fold:

    rain    stages_s4_v3's zone_v3 fit (a rain background, a monotone lingering curve per zone, λ chosen nested),
            fit on every sampled zone-day the public number grades: first looks AND resample days (X-S4-RESAMPLE);
            the other S4 rules (X-S4-HISTUNK, X-LEDGER-SUSPECT, X-S4-FOLLOWUP) as S4 v3 applies them.
    table   the lingering table per zone (stages_s4_v3.zone_table → zone_curves: the served recipe at zone level,
            "Linger table 2 per zone"), fit on more samples: DataSF and the Poo Bot archive as before, plus SFPUC's
            STARDB export 2016-10 → 2020-07, the years between them (samples.D10_SOURCES, the S4 truth's sources).

**The choice is nested (protocol §2).** Inside a fold, leave one of its training seasons out at a time, fit both on
the rest (the fold's λ, the medians and the table on the rest's days), and score the season's sampled zone-days,
resamples included, on the S4 truth. A zone takes the rain curve only when its inner Brier score is lower than the
table's, so no zone gets the rain curve on the strength of the days it is graded on. Each fold records its picks
and inner scores; the finals' picks are the spec's.

**The spec** is compose_v2's zone_v3 under sfpuc4_v1 (per zone): one background feature list (stages_s4_v3.FEATURES),
a table zone with its constant background (intercept logit(baseline), the rain coefficients 0) and its table curves,
a rain zone with the rain curve's background and curves. Sizes are φ 1 on the candidate's own fold v̂, as
stages_s4_v3.served_recipe sizes them, so the set's S3 must size every link at φ 1 (checked).

**The candidate** (``assemble``): ``base``'s S2 copied, its S3 split refit exactly as assemble_served_parts does,
this S4, S1 the served weather model and ``base``'s S5. With base sfpuc4_shared8_v2 the two sets differ in S4 only,
so their paired scores are this S4's own effect. Tagged post_seen: designed after the earlier sets' post-training
scores were seen.

    venv/bin/python features/forecast/src/models/stages_s4_zones.py --assemble sfpuc4_shared8_v3 --base sfpuc4_shared8_v2
    venv/bin/python features/forecast/src/models/stages_build.py --set sfpuc4_shared8_v3 --root stages_candidates --write
    venv/bin/python features/forecast/src/models/stages_s4_zones.py --compare sfpuc4_shared8_v3 --base sfpuc4_shared8_v2
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "collectors", HERE.parents[3]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import compose_v2 as C  # noqa: E402
import samples as SMP  # noqa: E402
import stages_build as SB  # noqa: E402
import stages_s4_v3 as S4  # noqa: E402
from shared import clock  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402

KINDS = {"s4": "zone_choice_v1", "out": "zone_choice_v2"}   # the S4 component's name, by the score a zone picks on
CRITERIA = {"s4": "S4's own score: every sampled zone-day, resamples included, on q (background and lingering)",
            "out": ("the public number's: the sampled days in the week after a zone overflow (none on the day), on the "
                    "lingering part alone (compose_v2.out: x(0) = 1, no background), the only days the two S4s give the "
                    "public number different values")}
ARMS = ("rain", "table")
FIT_EXCL = ("", "X-S4-RESAMPLE")             # the rain curve's fit rows: first looks and resamples
POST_SEEN = ("designed 2026-10-07 after sfpuc4_shared8_v1 / v2's post-training scores were seen: the first rain curve "
             "under-predicted East's resample days, which the public number grades")


# ── the two S4s ─────────────────────────────────────────────────────────────

def table_samples(sources=SMP.D10_SOURCES) -> pd.DataFrame:
    """The S4 truth's samples (DataSF, STARDB 2016-10 → 2020-07, Poo Bot), in train_v4.load_samples' columns."""
    return SMP.as_training_samples(sources)


def fit_rows(inp: S4.Inputs, fold, hist: C.History) -> pd.DataFrame:
    """stages_s4_v3.fit_rows with the resample days kept (FIT_EXCL): every sampled zone-day of the fold's training
    days that the public number grades, its read span clear of the scored window, the follow-up rule as there."""
    pool = inp.pool
    kept = pool[pool["excl"].isin(FIT_EXCL)].assign(resample=lambda d: d["excl"] == "X-S4-RESAMPLE", excl="")
    orig = inp.pool
    try:
        inp.pool = kept
        return S4.fit_rows(inp, fold, hist)
    finally:
        inp.pool = orig


def table_spec(bk: dict, coef: dict, medians: dict) -> dict:
    """A zone_v3 spec of the table alone (constant background), to score it with compose_v2.s4."""
    return {"geography": S4.GEOGRAPHY, "pipeline": S4.PIPELINE, "kind": S4.KIND, "unit": "zone",
            "background": {"kind": "logistic", "features": [], "coef": coef}, "buckets": bk,
            "zone_median_mg": medians, "monotone": True}


def fit_table(inp: S4.Inputs, s2v: pd.DataFrame, td: pd.DatetimeIndex, smp: pd.DataFrame) -> dict:
    tab = S4.zone_table(G.get(S4.GEOGRAPHY), S4.candidate_truths(inp), inp.days, s2v, smp, td)
    bk, coef, filled = S4.zone_curves(tab)
    med = {z: float(tab[z]["median_event_volume_mg"]) for z in ZONES}
    return {"buckets": bk, "coef": coef, "medians": med, "filled": filled, "table": tab,
            "spec": table_spec(bk, coef, med)}


def season_window(s: int) -> tuple:
    return pd.Timestamp(s, 7, 1), pd.Timestamp(s + 1, 6, 30)


def after_overflow(hist: C.History, dates) -> np.ndarray:
    """(rows, zones) True where the zone overflowed on one of D−7…D−1 and not on D: where OUT reads the lingering part."""
    o = hist.p.astype(float)
    prior = o.shift(1).rolling(len(S4.LAGS) - 1, min_periods=1).max().fillna(0.0)
    m = (prior > 0) & (o == 0)
    return m.reindex(pd.DatetimeIndex(dates)).to_numpy(dtype=bool)


def inner_choice(inp: S4.Inputs, ch: dict, rows: pd.DataFrame, td: pd.DatetimeIndex, lam: float, s2v: pd.DataFrame,
                 smp: pd.DataFrame, criterion: str = "s4") -> tuple:
    """({zone: 'rain' | 'table'}, {criterion: {zone: {arm: inner Brier}}}, {criterion: {zone: rows}}): leave one
    training season out at a time, fit both S4s on the rest, and score the season's rows on the S4 truth y, both
    ways (CRITERIA); a zone picks on ``criterion``."""
    if criterion not in CRITERIA:
        raise ValueError(f"criterion {criterion!r}; known: {sorted(CRITERIA)}")
    geo = G.get(S4.GEOGRAPHY)
    sse = {c: {z: {a: 0.0 for a in ARMS} for z in ZONES} for c in CRITERIA}
    n = {c: {z: 0 for z in ZONES} for c in CRITERIA}
    rs = S4._season(rows["date"])
    ts = S4._season(td)
    for s in sorted(set(rs)):
        lo, hi = season_window(s)
        held = rows[rs == s]
        tr_rows = rows[(rs != s) & S4.window_clear(rows["date"], lo, hi)]
        td_in = td[(ts != s)]
        if not len(held) or tr_rows["y_fit"].sum() < 1:
            continue
        rain = S4.fit(ch["hist"], inp.rain, tr_rows, td_in, lam, "zone").spec
        table = fit_table(inp, s2v, td_in, smp)["spec"]
        pos = ch["hist"].p.index.get_indexer(pd.DatetimeIndex(held["date"]))
        zpos = np.array([list(ZONES).index(z) for z in held["zone"]])
        y = held["y"].to_numpy(dtype=float)
        specs = {"rain": rain, "table": table}
        pred = {"s4": {a: S4.q_use(sp, ch["hist"], inp.rain).to_numpy()[pos, zpos] for a, sp in specs.items()},
                "out": {a: C.out(geo, sp, ch["hist"]).zone[list(ZONES)].to_numpy()[pos, zpos] for a, sp in specs.items()}}
        lingering = after_overflow(ch["hist"], held["date"])[np.arange(len(held)), zpos]
        for c in CRITERIA:
            for z in ZONES:
                m = (held["zone"].to_numpy() == z) & (lingering if c == "out" else True)
                n[c][z] += int(m.sum())
                for a in ARMS:
                    sse[c][z][a] += float(((pred[c][a][m] - y[m]) ** 2).sum())
    brier = {c: {z: {a: (sse[c][z][a] / n[c][z] if n[c][z] else math.nan) for a in ARMS} for z in ZONES} for c in CRITERIA}
    b = brier[criterion]
    pick = {z: ("rain" if n[criterion][z] and b[z]["rain"] < b[z]["table"] else "table") for z in ZONES}
    return pick, brier, n


def combine(rain: dict, table: dict, pick: dict) -> dict:
    """{background coef, buckets, medians} per zone from the picked S4 (a table zone: its intercept, rain slopes 0)."""
    coef, bk, med = {}, {}, {}
    for z in ZONES:
        if pick[z] == "rain":
            coef[z] = {k: float(v) for k, v in rain["background"]["coef"][z].items()}
            bk[z], med[z] = dict(rain["buckets"][z]), float(rain["zone_median_mg"][z])
        else:
            coef[z] = {"intercept": float(table["coef"][z]["intercept"]), **{f: 0.0 for f in S4.FEATURES}}
            bk[z], med[z] = dict(table["buckets"][z]), float(table["medians"][z])
    return {"coef": coef, "buckets": bk, "medians": med}


def check_unit_phi(name: str, root=None) -> None:
    saved = S4._saver().load_set(name, root=root).s3_links
    bad = {lid: lk.get("vol_share") for lid, lk in (saved or {}).get("links", {}).items() if float(lk.get("vol_share", 1.0)) != 1.0}
    if saved is None or bad:
        raise ValueError(f"{name}'s S3 must size every link at φ 1 for this S4's sizes; it has {bad or 'no s3_links.json'}")


def build(inp: S4.Inputs, s2_source, name: str, log=print, criterion: str = "s4") -> dict:
    """s4_quality.json for stage candidate ``name``: every fold of the served plan with both S4s, the nested picks and
    the combined curves (stages_build.s4_fold_spec's record format); the finals' on top. Checked by compose_v2."""
    smp = table_samples()
    geo = G.get(S4.GEOGRAPHY)
    recs = []
    for f in inp.fitted.folds:
        t0 = time.time()
        key = (f.tier, f.fold)
        td, win = S4.train_days(inp, f), S4.scored_window(inp, f)
        s2v = s2_source.values(key, "v_hat", inp.days)
        ch = S4.served_chain(inp, f, None, s2v, s2_source.name)
        rows = fit_rows(inp, f, ch["hist"])
        lam, lam_inner = S4.choose_lambda(ch["hist"], inp.rain, rows, td)
        rain = S4.fit(ch["hist"], inp.rain, rows, td, lam, "zone").spec
        table = fit_table(inp, s2v, td, smp)
        pick, inner, n_inner = inner_choice(inp, ch, rows, td, lam, s2v, smp, criterion)
        parts = combine(rain, table, pick)
        recs.append({"tier": f.tier, "fold": f.fold, "scores": [str(win[0].date()), str(win[-1].date())],
                     "fit_span": [str(td.min().date()), str(td.max().date())],
                     "fit_seasons": sorted(int(s) for s in set(S4._season(td))), "background": parts["coef"],
                     "buckets": parts["buckets"], "zone_median_mg": parts["medians"], "monotone": True,
                     "vol_share": S4.unit_phi(), "v_hat_from": name, "history_geography": S4.GEOGRAPHY,
                     "pick": pick, "picked_on": criterion, "inner_brier": inner, "inner_rows": n_inner, "lambda": lam,
                     "lambda_inner_brier": {str(k): v for k, v in lam_inner.items()}, "rain_fit_rows": int(len(rows)),
                     "rain_fit_resamples": int(rows["resample"].sum()),
                     "table_filled": table["filled"]})
        log(f"  {f.tier} {f.fold}: λ {lam:g}, picks {pick} ({time.time() - t0:.0f}s)")
    fin = next(r for r in recs if (r["tier"], r["fold"]) == S4.FINAL)
    spec = {"geography": S4.GEOGRAPHY, "pipeline": S4.PIPELINE, "kind": S4.KIND, "unit": "zone",
            "background": {"kind": "logistic", "features": list(S4.FEATURES), "coef": fin["background"],
                           "rain": "two-gauge masked daily rain (truth.gauge_rain mean), rain3 = D−2…D; a table zone's "
                                   "rain slopes are 0",
                           "wet_season_months": list(S4.WET_SEASON_MONTHS)},
            "buckets": fin["buckets"], "zone_median_mg": fin["zone_median_mg"], "monotone": True,
            "sources": {"truth": [{"source": s, "from": lo, "to": hi} for s, lo, hi in SMP.D10_SOURCES],
                        "rain_curve_fit_on": "every sampled zone-day the public number grades: first looks and resamples",
                        "table_fit_on": "the same samples (DataSF, STARDB 2016-10 → 2020-07, Poo Bot)",
                        "history": "the CIWQS ledger: zone overflow days, sizes Σ the fired basins' measured volume (φ 1)"},
            "fit": {"recipe": ("per fold: the rain curve (stages_s4_v3.fit on stages_s4_zones.fit_rows, λ by "
                               "stages_s4_v3.choose_lambda) and the table (stages_s4_v3.zone_table → zone_curves); each "
                               f"zone picks by inner leave-one-season-out Brier (stages_s4_zones.inner_choice) on {CRITERIA[criterion]}"),
                    "criterion": criterion,
                    "window": f"each fold's training days; the finals through {S4.S2.TRAINED_THROUGH.date()}",
                    "built_at": clock.utc_iso(), "served_set": inp.bundle.name, "s2_set": name, "s3_set": name,
                    "folds": recs},
            "note": ("S4 per zone (Chase, 2026-10-07): the rain curve where it scores better inside the fold, the "
                     f"lingering table per zone elsewhere, judged on {CRITERIA[criterion]}. Large ≥ small is not "
                     "imposed on a table zone.")}
    spec = json.loads(json.dumps(spec, default=float))
    C.check_s4_spec(spec, geo)
    for r in recs:
        SB.s4_fold_spec({**spec, "set": name, "component": KINDS[criterion]}, (r["tier"], r["fold"]), geo)
    return spec


# ── the candidate ───────────────────────────────────────────────────────────

def assemble(name: str, base: str, root=None, log=print, criterion: str = "s4") -> dict:
    """Stage candidate ``name``: ``base``'s S2 (copied), its S3 split refit as assemble_served_parts does, this S4,
    S1 the served weather model, ``base``'s S5; tagged post_seen."""
    import stages_candidates as SC
    import stages_entries as E
    import stages_s3_links as S3L
    t0 = time.time()
    src = SC.load_set(base, root)
    model = E.served_weather_model()
    if model != src.components["s1"]:
        raise ValueError(f"{base}'s S1 is {src.components['s1']!r}, the served weather model {model!r}")
    # the S2 and its S3 union rule come from the set whose S2 the bake-off picked (``base``'s own s2_from, when its
    # S2 is a copy), exactly as assemble_served_parts made ``base``: then the two sets differ in S4 only
    origin = src.manifest.get("s2_from") or base
    org = SC.load_set(origin, root)
    SC.copy_s2(origin, name, root)
    inp = S4.load_inputs(log=log)
    split = S3L.served_split(inp.bundle, inp.fitted.folds, inp.plan, inp.train, inp.events, inp.samples_v1)
    s3 = S3L.split_spec(org.s3_links, split, name)
    if json.dumps(s3["links"], sort_keys=True, default=float) != json.dumps(src.s3_links["links"], sort_keys=True, default=float):
        raise AssertionError(f"{name}'s refit S3 differs from {base}'s: the sets would differ in more than S4")
    SC.save_component(name, "s3_links", {**s3, "component": S3L.SPLIT_KIND}, root=root)
    check_unit_phi(name, root)
    s4 = build(inp, S3L.candidate_s2(name, root=root), name, log=log, criterion=criterion)
    SC.save_component(name, "s4_quality", {**s4, "component": KINDS[criterion]}, root=root)
    SC.save_component(name, "s1", {"geography": src.geo.version, "component": model, "spec": src.manifest["s1"]}, root=root)
    SC.save_component(name, "s5", {"geography": src.geo.version, "component": src.components["s5"], "spec": src.manifest["s5"]},
                      root=root)
    SC.tag(name, "post_seen", POST_SEEN + ("" if criterion == "s4" else "; and after sfpuc4_shared8_v3's (picked on S4's "
                                           "own score), whose public number was worse for East"), root)
    log(f"{name}: components {SC.load_set(name, root).components} ({time.time() - t0:.0f}s)")
    return {"set": name, "s4": s4}


# ── against its base: S4's own effect ──────────────────────────────────────

def compare(name: str, base: str, n_boot: int = SB.B_PROTOCOL) -> dict:
    """Paired Δ Brier (``name`` − ``base``) per stage, zone and window on the unit-days both scored, from the two
    builds' rows (stages/<set>/rows.csv.gz): S4 oracle and rain known, the public number rain known and one day ahead."""
    import verify as V
    read = lambda s: pd.read_csv(SB.STAGES_DIR / s / "rows.csv.gz", low_memory=False, keep_default_na=False,  # noqa: E731
                                 na_values=[""], parse_dates=["date"])
    a, b = read(name), read(base)
    blocks = SB.T.blocks(end=SB.E.data_end()).set_index("date")
    out = {}
    for stage, entry in (("s4", "oracle"), ("s4", "rain"), ("out", "rain"), ("out", "L1")):
        for tier in ("T2", "T1-holdout", "T1"):
            sel = lambda r: r[(r["stage"] == stage) & (r["entry"] == entry) & (r["tier"] == tier) & r["excl"].isna()]  # noqa: E731
            ra, rb = sel(a).set_index(["unit", "date"]), sel(b).set_index(["unit", "date"])
            common = ra.index.intersection(rb.index)
            if not len(common):
                continue
            ra, rb = ra.loc[common], rb.loc[common]
            if not np.array_equal(ra["y"].to_numpy(dtype=float), rb["y"].to_numpy(dtype=float)):
                raise AssertionError(f"{stage} {entry} {tier}: the two sets' rows carry different truths")
            units = common.get_level_values("unit").to_numpy()
            blk = blocks["block"].reindex(common.get_level_values("date")).to_numpy()
            for u in ["pooled"] + list(ZONES):
                m = np.ones(len(common), bool) if u == "pooled" else units == u
                if not m.any():
                    continue
                d = V.paired_delta(ra["y"].to_numpy(dtype=float)[m], ra["p"].to_numpy(dtype=float)[m],
                                   rb["p"].to_numpy(dtype=float)[m], blk[m], n=n_boot, seed=SB.SEED, level=SB.LEVEL)
                out.setdefault(f"{stage}·{entry}", {}).setdefault(u, {})[tier] = V.clean(d)
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--assemble", metavar="NAME")
    ap.add_argument("--compare", metavar="NAME")
    ap.add_argument("--base", default="sfpuc4_shared8_v2")
    ap.add_argument("--criterion", default="s4", choices=sorted(CRITERIA), help="the score a zone picks on")
    a = ap.parse_args(argv)
    if a.assemble:
        assemble(a.assemble, a.base, criterion=a.criterion)
    if a.compare:
        print(json.dumps(compare(a.compare, a.base), indent=1))


if __name__ == "__main__":
    main()
