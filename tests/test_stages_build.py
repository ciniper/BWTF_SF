"""stages_build: every stage of one set scored on days no fitted component saw (P7, P8; STAGES_DESIGN.md Part C §7,
Part B 1, 2, 7, 9, 13, 16; STAGES_PROTOCOL.md stages_v3 §2–§9).

On a small slice of the served set (its name read from served.json), so the file runs in under two minutes:
entries oracle, rain, L1 and L1s; tiers T1, T1-holdout and the T2 season 2019-20; S5 on the perfect feed and
degraded:1 in T1; a 50-resample bootstrap (the CLI and ``write`` use the protocol's 2,000). The pins:
  - no T3 row, and every row's tier and fold are its date's (a T2 row's fold is its season); a row on a
    day its fold's weights saw is counted and refused;
  - a fold never sees its scored days, the S3 / S4 specs included: perturbing every scored day's labels,
    volumes, events and samples leaves the fold's specs as they were;
  - the climatology reference reads the fold's training days only: flipping every scored day's truth in
    the pool leaves each window's reference as it was, flipping a training season moves it;
  - on the full training window the S3 / S4 fitter reproduces the served stage2.json and impact_table.json;
  - as served (L1s) differs from lead 1 only in the history features, through stages_entries;
  - a lead entry's S4 / OUT row reads its issue day's chain (rain known before I, L0 on I, L1 on D),
    equal to compose_v2 on the real 8-day window, never the lead-1 forecast of every day;
  - the counts partition every (stage, unit, entry, window), S5 one variant at a time;
  - the S3 oracle reads v̂ from rain, never the filed volume; the S4 and OUT oracles read the true history;
  - X-S3-ID is read off the set's S3 spec (North under stage 2 v2), with 0 mismatches;
  - the CIs resample storm blocks (truth.blocks), seed 0; paired deltas, the ladder's drops and the S5
    primary (link_zone_swap − basin_swap) are the Brier differences on the shared rows, recomputed here;
  - the figure block holds every scored node, and the scored figure renders its numbers;
  - the figure shows each stage's powered window (S1 Previous Runs, S2 → OUT cross-season labelled
    development, S5 its protocol window) with the post-training value and CI in each tooltip, fades only
    the pills whose own cell is X-POWER, and carries a one-line caption; a set with post-training only shows it;
    a GEO_V1 set's post-training words say those days also picked the served set (X-SEL post_selected);
  - S5's pills are the change in Brier of the served rule (perfect feed; the degraded feeds' mean), signed,
    never BSS, link/zone injection in the tooltip; S5's chip prints the perfect feed's counts in S5's window
    (distinct zone-days), 0 with its reason, and every feed's own counts in its tooltip;
  - OUT's card has oracle and lead-1 pills beside its lead strip; S3's chained pill is lead 1 on the oracle's
    own rows, against its reference (every day in the tooltip);
  - S4 is graded on design D10's three records (STARDB scores the 2019-20 season);
  - the protocol stamp is the protocol file's sha; s1_scores.json is pinned without its built_at, every
    other input and the code by their bytes; a rebuild gives the same scores.json;
  - writing goes only under data/models/stages/<set>/, rows only for the served set and stage candidates, never
    with B < 2,000.

A stub sfpuc4_v1 stage candidate, saved through stages_candidates into a temporary root with a stub bake-off,
scored on the same slice (no lead entry beyond L1) against the served slice written to a temporary directory:
  - it builds from its own folds: SFPUC4's four basins, T1 finals, T1-holdout siblings, T2 the bake-off's
    outer-fold rows (no warm-up: S4 / OUT start a week in, counted), each fold's S3 / S4 from its own record, a
    head under the floor the declared fallback; no T3 row, the counts partition, no live_v2 variant in S5;
  - a lead entry's S4 under a rain background reads the issue day's rain, equal to compose_v2 on the window;
  - the comparisons pair identical rows: vs_served on the intersection (zones, S4, OUT only across
    geographies), s2_vs_recipe on (tier, fold, basin, day) with both arms at the file's precision and the
    recipe's labels the build's, S3b on each oracle row's own benchmark (recomposed here); every CI is the
    storm-block bootstrap's on those rows, S3a's and S3b's 95% bounds Holm's; a served-recipe arm the
    candidate's own rows cannot vouch for, or one flipped label, raises;
  - the primaries hold every protocol §8 row in its words with Δ, CI, MDE, margin and status, S3a / S3b one
    Holm family, S2 volume decided by the fallback's existence (checked on every fold, T2's outer-fold heads
    included) with the bake-off's head − fallback Δ beside it, S3's rows naming the S2 its shares were fit on
    (a development stand-in, an unstated or another S2 carries a caveat), S5 saying it was read from the served
    set's chain, OUT saying T0 is empty; the §9 block states each criterion and never says promote;
  - S4's fix clause reads both S4s' chained − oracle on the same rows (recomputed here), the served table's
    defect shown only by its CI (constructed cells); the fix-not-tested caveat stays on S4's row, never OUT's,
    which carries S4's size caveat only; Holm is handed the rows' own bounds and the protocol's margins;
  - §8's S4 and S5 rows decide only for the component they test (S4 v3, link_zone_swap): another changed
    component has no row, so §9.1 says so; a nested candidate's S2 T2 is labelled 'development (nested)'; a
    post_seen candidate's post-training scores carry the tag (pill words, caption, primaries decided on them);
  - a spec fit on its own fold's days, a head under the floor that is not the fallback (T2's included), a
    missing fold or head record, rows missing a basin and a negative weight are refused; the candidate's
    manifest pins the served build its comparisons read;
  - a stage candidate is read through stages_candidates.load_set, which refuses a tampered set.

Counts are as of the committed data's end, 2026-08-17 (Part B 23).

    venv/bin/python tests/test_stages_build.py
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
MODELS = FORECAST / "src" / "models"
for p in (ROOT, MODELS, FORECAST / "src" / "collectors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compose_v2 as C  # noqa: E402
import exclusions as X  # noqa: E402
import stages_build as B  # noqa: E402
import stages_entries as E  # noqa: E402
from shared import lineup as LU  # noqa: E402
import stages_flowchart as F  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_spec as SP  # noqa: E402
import train_v4 as T4  # noqa: E402
import truth as T  # noqa: E402

SLICE = dict(entries=("oracle", "rain", "L1", "L1s"), tiers=("T1", "T1-holdout", "T2"), seasons=(2019,),
             feeds=("oracle", "degraded:1"), s5_tiers=("T1",), n_boot=50, log=lambda *a: None)
AS_OF = "2026-08-17"
HOLM_95 = 0.95      # Holm's first step at one-sided α/2 = 0.025 (protocol §6, α = 0.05): typed here, never read from B
TOL = 1e-9         # one model's predictions in different batch sizes differ by ~1e-16 (stages_build.EPS)


@functools.lru_cache(maxsize=1)
def _b():
    return B.build("served", **SLICE)


@functools.lru_cache(maxsize=1)
def _t1_folds():
    return S2.fit(B.served_name(), "served", ("T1",)).folds


@functools.lru_cache(maxsize=1)
def _train():
    bundle = B.load_set("served")
    need = sorted(set(bundle.s2.sources) | set(bundle.chosen.values()) | {"avg"})
    return bundle, T4.build_dataset(sources=need)[0]


def _raises(fn, *exc) -> bool:
    try:
        fn()
    except exc or Exception:
        return True
    return False


def _season(d) -> pd.Series:
    d = pd.DatetimeIndex(d)
    return pd.Series(np.where(d.month >= 7, d.year, d.year - 1), index=d)


# ── windows and folds ───────────────────────────────────────────────────────

def test_no_t3_row_and_every_tier_and_fold_is_its_dates():
    b = _b()
    assert b.manifest["set"] == json.loads((T4.SERVE_DIR / "served.json").read_text())["name"]
    for st, r in b.rows.items():
        assert len(r), st
        assert set(r["tier"]) <= {"T1", "T1-holdout", "T2"} and not (r["tier"] == "T3").any(), st
        d = pd.DatetimeIndex(r["date"])
        t1, ho, t2 = (r["tier"] == "T1").to_numpy(), (r["tier"] == "T1-holdout").to_numpy(), (r["tier"] == "T2").to_numpy()
        assert (d[t1] >= S2.POST_START).all() and (d[t1] <= pd.Timestamp(AS_OF)).all(), st
        assert (d[ho] >= S2.HOLDOUT_START).all() and (d[ho] < S2.POST_START).all(), st
        assert (r.loc[t1, "fold"] == "final").all() and (r.loc[ho, "fold"] == "pre_holdout").all(), st
        seas = _season(d[t2]).to_numpy()
        assert (r.loc[t2, "fold"].to_numpy() == np.array([S2.season_label(s) for s in seas])).all(), st
        assert set(seas) <= {2019} or st == "s5", st
        assert r["excl"].ne("X-ALL-INSAMPLE").all(), st
    assert b.scores["integrity"]["t3_rows"] == 0
    # a T3 row is refused before it is scored
    r = b.rows["out"].head(3).copy()
    r["tier"] = "T3"
    assert _raises(lambda: X.apply(r, "out", X.context("geo_v1", end=AS_OF)), ValueError)
    # a row on a day its fold's weights saw is counted and refused (stages_s2's ``seen``), whatever its label
    folds = _t1_folds()
    t1 = b.rows["out"][b.rows["out"]["tier"] == "T1"]
    assert B.t3_rows({"out": t1, "s5": b.rows["s5"]}, folds) == {"out": 0, "s5": 0}
    sneak = {"out": t1.head(3).assign(date=pd.Timestamp("2025-10-31"))}
    assert B.t3_rows(sneak, folds) == {"out": 3}
    saw = [dataclasses.replace(f, seen={**f.seen, "westside": f.seen["westside"].union(pd.DatetimeIndex(t1["date"][:5]))})
           for f in folds]                                              # weights that saw a scored day
    assert B.t3_rows({"out": t1}, saw)["out"] == int(pd.DatetimeIndex(t1["date"]).isin(pd.DatetimeIndex(t1["date"][:5])).sum()) > 0
    assert _raises(lambda: B.check_integrity(sneak, B.Composed(), 0.0, {}, folds), AssertionError)
    assert _raises(lambda: B.t3_rows({"out": t1.head(3).assign(fold="2019-20")}, folds), AssertionError)


def test_a_fold_never_sees_its_scored_days_s3_s4_specs_included():
    b = _b()
    info = b.spec_info
    assert info[("T1-holdout", "pre_holdout")]["fit_span"][1] == "2023-06-30"
    assert 2019 not in info[("T2", "2019-20")]["seasons"] and info[("T2", "2019-20")]["seasons"][0] == 2016
    assert 2025 not in info[("T2", "2019-20")]["seasons"]
    for key, i in info.items():
        if key[0] != "T1":
            assert i["scored_overlap"] == 0, key
    # the served S2 folds' weights and heads never saw a row they score (stages_s2's own check, re-run)
    for f in S2.fit(B.served_name(), "served", ("T1-holdout",)).folds:
        assert not f.seen["westside"].isin(pd.date_range(f.start, f.end)).any()
    # perturb every scored day of 2019-20 (labels, volumes, events, samples): the fold's specs do not move
    bundle, train = _train()
    fitted = S2.fit(bundle.name, "served", ("T2",), train_frames=train)
    fold = next(f for f in fitted.folds if f.fold == "2019-20")
    keep = {(p[0], p[1]): p for p in S2._plan(("T2",))}[("T2", "2019-20")][5]
    events, samples = T4.load_events(), T4.load_samples()
    base, _ = B.fold_specs(bundle, fold, keep, train, events, samples)
    bad = {}
    for s, f in train.items():
        g = f.copy()
        m = (g["season"] == 2019).to_numpy()
        for basin in (bb.name for bb in bundle.geo.basins):
            g.loc[m, f"{basin}_csd"] = 1 - g.loc[m, f"{basin}_csd"]
            g.loc[m, f"{basin}_volume_mg"] = 999.0
        bad[s] = g
    ev = pd.concat([events, events.assign(event_date=pd.Timestamp("2020-01-15"))], ignore_index=True)
    sm = samples.copy()
    sm.loc[_season(sm["sample_date"]).to_numpy() == 2019, "exceeds_standard"] = True
    moved, _ = B.fold_specs(bundle, fold, keep, bad, ev, sm)
    assert moved == base
    # …while the same perturbation on a training season does move them
    bad2 = {s: f.assign(**{"Westside_csd": np.where(f["season"] == 2018, 1 - f["Westside_csd"], f["Westside_csd"])}) for s, f in train.items()}
    assert B.fold_specs(bundle, fold, keep, bad2, events, samples)[0] != base


def test_the_reference_reads_the_training_fold_never_the_scored_days():
    b = _b()
    ctx = X.context(b.bundle.geo, end=AS_OF)
    pool = B._pool("out", ctx, b.bundle.geo, "rain")
    rows = b.rows["out"]
    ref = B.references(rows, pool)
    assert np.array_equal(ref, rows["ref"].to_numpy())                 # what the build scored with
    d = pd.DatetimeIndex(pool["date"])
    seas = _season(d).to_numpy()
    scored = {"T1": d >= S2.POST_START, "T1-holdout": (d >= S2.HOLDOUT_START) & (d < S2.POST_START), "T2": seas == 2019}
    for tier, on in scored.items():
        m = (rows["tier"] == tier).to_numpy()
        assert m.sum() > 100, tier
        flip = pool.assign(y=np.where(on, 1 - pool["y"], pool["y"]))  # every scored day of the window, flipped
        assert np.array_equal(B.references(rows[m], flip), ref[m]), tier
        train = pool.assign(y=np.where(seas == 2018, 1 - pool["y"], pool["y"]))   # a training season, flipped
        assert not np.array_equal(B.references(rows[m], train), ref[m]), tier
    # T1's reference is every T2 season's climatology (protocol §4.2), recomputed here
    t1 = rows[rows["tier"] == "T1"]
    fit = pool[(d >= pd.Timestamp(2016, 7, 1)) & (d <= pd.Timestamp(2025, 6, 30))]
    want = B.V.climatology_ref(fit["y"], (fit["unit"], fit["date"]), (t1["unit"], pd.DatetimeIndex(t1["date"])))
    assert np.array_equal(want, t1["ref"].to_numpy())


def test_the_s3_s4_fitter_reproduces_the_served_artifacts_on_the_training_window():
    bundle, train = _train()
    heads, raw_json = T4.stage2_from_served()
    events, samples = T4.load_events(), T4.load_samples()
    served = json.loads((T4.SERVE_DIR / "stage2.json").read_text())
    variant = B.CAND.stage2_variant_id(served)          # v2_d10 since 2026-10-07: its table on the stages' S4 samples
    own = B.SV.impact_samples(served["impact_samples"]) if served.get("impact_samples") else samples
    fit = B.fit_s3_s4(train, bundle.chosen, heads, own, events, variant)
    assert fit["raw"] == raw_json or own is not samples       # impact_table.json is the raw table on the served record's samples
    for k in ("variant", "kind", "group_outfalls", "shares", "median_event_volume_mg", "impact_table", "impact_samples"):
        assert fit["stage2"].get(k) == served.get(k), k
    a = C.geo_v1_adapter_specs(stage2=fit["stage2"])
    t1 = C.geo_v1_adapter_specs()
    for k in ("s3", "s4"):
        assert {x: v for x, v in a[k].items() if x != "sources"} == {x: v for x, v in t1[k].items() if x != "sources"}, k
    assert bundle.t1_specs["s3"]["links"] == t1["s3"]["links"]
    assert B.fit_s3_s4(train, bundle.chosen, heads, samples, events, "v1")["table"] == raw_json
    assert _raises(lambda: B.fit_s3_s4(train, bundle.chosen, heads, samples, events, "v3"), ValueError)


# ── entries ────────────────────────────────────────────────────────────────

def test_as_served_differs_from_lead_1_only_in_history_features():
    src = ["avg", "SF Downtown"]
    l1, ls = E.frames("L1", src, model=E.served_weather_model()), E.frames("L1s", src, model=E.served_weather_model())
    hist = list(E.HISTORY_FEATURES)
    for s in src:
        a, c = l1[s].set_index("date"), ls[s].set_index("date")
        assert a.index.equals(c.index)
        other = [x for x in E.FEATURES if x not in hist]
        pd.testing.assert_frame_equal(a[other], c[other])
        assert (a[hist] != c[hist]).any(axis=1).sum() > 100, s
    # through the chain: L1s with lead 1's history features put back is lead 1, exactly, at S2 (the T1 finals)
    bundle = B.load_set("served")
    fold = S2.fit(bundle.name, "served", ("T1",)).folds[0]
    back = {s: ls[s].assign(**{h: l1[s][h] for h in hist}) for s in src}
    days = pd.DatetimeIndex(l1["avg"]["date"])
    days = days[days >= S2.POST_START]
    p1, v1 = B._s2_values(bundle.s2, fold, S2._entry_frames(bundle.s2, "L1", l1), days)
    pb, vb = B._s2_values(bundle.s2, fold, S2._entry_frames(bundle.s2, "L1s", back), days)
    assert p1.equals(pb) and v1.equals(vb)
    s2 = _b().rows["s2"]
    p = s2[s2["entry"].isin(["L1", "L1s"]) & (s2["tier"] == "T1")].pivot_table(index=["unit", "date"], columns="entry", values="p")
    w = p.xs("westside", level="unit")
    assert np.array_equal(w["L1"].to_numpy(), p1.loc[w.index, "westside"].to_numpy())
    assert (p["L1"] != p["L1s"]).mean() > 0.5


def test_a_lead_entrys_s4_and_out_read_its_issue_days_chain():
    b = _b()
    assert B.issue_entries("L1") == ("L0", "L1") and B.issue_entries("L1s") == ("L0s", "L1s")
    assert B.issue_entries("L5") == ("L0", "L1", "L2", "L3", "L4", "L5") and B.issue_entries("rain") == ("rain",)
    bundle, geo = b.bundle, b.bundle.geo
    fold = _t1_folds()[0]
    spec = b.specs[("T1", "final")]
    model = E.served_weather_model()
    src = list(bundle.s2.sources)
    ef = {e: S2._entry_frames(bundle.s2, e, E.frames(e, src, model=model)) for e in ("oracle", "L0", "L1")}
    out = b.rows["out"]
    o1 = out[(out["entry"] == "L1") & (out["tier"] == "T1")]
    targets = pd.DatetimeIndex(sorted(set(o1.nlargest(40, "p")["date"])))[:8]   # days after a storm: the tail matters
    s4 = b.rows["s4"]
    moved = 0
    for D in targets:
        win = pd.date_range(D - pd.Timedelta(days=7), D)
        parts = [B._s2_values(bundle.s2, fold, ef["oracle"], win[:-2]),        # D−7 … D−2: before the issue day D−1
                 B._s2_values(bundle.s2, fold, ef["L0"], win[-2:-1]),          # D−1 = I: the issue day's own forecast
                 B._s2_values(bundle.s2, fold, ef["L1"], win[-1:])]            # D: lead 1
        p, v = pd.concat([x[0] for x in parts]), pd.concat([x[1] for x in parts])
        comp = C.compose(geo, spec, C.BasinInputs(p, v))
        for zk in ("ocean", "baker_china", "north", "east"):
            row = o1[(o1["unit"] == zk) & (o1["date"] == D)]
            assert len(row) == 1 and abs(row["p"].iloc[0] - comp.out.zone.loc[D, zk]) < TOL, (D, zk)
            q = s4[(s4["entry"] == "L1") & (s4["tier"] == "T1") & (s4["unit"] == zk) & (s4["date"] == D)]["p"]
            assert len(q) == 1 and abs(q.iloc[0] - comp.s4.zone.loc[D, zk]) < TOL, (D, zk)
        # …never the lead-1 forecast of every day of the window (issued before I, when the gauges were in)
        pl, vl = B._s2_values(bundle.s2, fold, ef["L1"], win)
        stale = C.compose(geo, spec, C.BasinInputs(pl, vl)).out.zone.loc[D]
        moved += int((np.abs(stale - comp.out.zone.loc[D]) > TOL).any())
    assert moved >= 3
    # a target whose issue day lacks a day of its forecast has no S4 / OUT row (counted, not composed)
    ins, kept, _ = B.issue_inputs(bundle.s2, fold, "L1", {"L0": E.frames("L0", src, model=model, end="2026-04-01"),
                                                          "L1": E.frames("L1", src, model=model)},
                                  B.runs_for(bundle.s2, fold, "oracle", E.frames("oracle", src, model=model),
                                             pd.date_range(S2.POST_START, AS_OF))[0], pd.date_range("2026-03-30", "2026-04-04"))
    assert list(kept) == list(pd.date_range("2026-03-30", "2026-04-02")) and len(ins.p) == 8 * len(kept)


def test_the_partition_holds_for_every_stage_unit_entry_and_window():
    b = _b()
    for st, r in b.rows.items():
        parts = [r[r["variant"] == v] for v in sorted(set(r["variant"]))] if st == "s5" else [r]
        for g in parts:
            X.counts(g)                                     # asserts n_total = n_scored + Σ n_rule
    part = b.scores["partition"]
    n = 0
    for st, units in part.items():
        for u, entries in units.items():
            for e, wins in entries.items():
                for w, cell in wins.items():
                    assert cell["n_total"] == cell["n_scored"] + sum(cell["excluded"].values()), (st, u, e, w)
                    n += cell["n_total"]
    s5 = b.rows["s5"]
    assert n == sum(len(r) for k, r in b.rows.items() if k != "s5") + int((s5["variant"] == "plain").sum())
    # S5's variants share one conditional set
    ex = s5.pivot_table(index=["entry", "tier", "unit", "date"], columns="variant", values="excl", aggfunc="first")
    assert (ex.nunique(axis=1) == 1).all()


# ── the oracles ─────────────────────────────────────────────────────────────

def test_the_s3_oracle_reads_v_hat_from_rain_never_the_filed_volume():
    b = _b()
    s2, s3 = b.rows["s2"], b.rows["s3"]
    o2 = s2[(s2["entry"] == "oracle") & (s2["unit"] == "westside")].set_index(["tier", "date"])
    spec = b.specs[("T1", "final")]["s3"]["links"]
    for zone, lid in (("ocean", "westside>ocean"), ("baker_china", "westside>baker_china")):
        o3 = s3[(s3["entry"] == "oracle") & (s3["unit"] == zone) & (s3["tier"] == "T1")].set_index(["tier", "date"])
        v = o2.loc[o3.index, "v_hat"].to_numpy()
        assert np.array_equal(o3["v_hat"].to_numpy(), v)               # the zone's size is S2's v̂
        y = T.basin_onsets("geo_v1", end=AS_OF).set_index(["basin", "date"]).loc["westside", "y"]
        yb = y.reindex(o3.index.get_level_values("date")).fillna(0).to_numpy(dtype=float)
        sh = spec[lid]["share"]
        want = C._apply_share(sh, yb, v)
        assert np.array_equal(o3["p"].to_numpy(), want), zone
        vol = o2.loc[o3.index, "y2"].to_numpy()                        # the filed volume
        fired = (yb == 1) & np.isfinite(vol)
        assert fired.sum() >= 5 and not np.allclose(C._apply_share(sh, yb, np.nan_to_num(vol))[fired], want[fired])
    # p = 1 on Westside's own day only where the share is 1 for that size: never the truth itself
    sc = s3[(s3["entry"] == "oracle") & (s3["excl"] == "")]
    assert set(sc["unit"]) == {"ocean", "baker_china"} and ((sc["p"] > 0) & (sc["p"] < 1)).any()


def test_the_s4_and_out_oracles_read_the_true_history():
    b = _b()
    s4, out = b.rows["s4"], b.rows["out"]
    spec = b.specs[("T1", "final")]["s4"]
    geo = b.bundle.geo
    lo = T.link_onsets(geo, end=AS_OF)
    fired = lo.assign(f=lo["y"].eq(1).fillna(False)).pivot(index="date", columns="link", values="f").astype(bool)
    o4 = s4[(s4["entry"] == "oracle") & (s4["excl"] == "")]
    assert len(o4) > 50
    assert spec["background"]["kind"] == "constant"
    quiet = checked = 0
    for z in ("ocean", "baker_china", "north", "east"):
        links = [lk.id for lk in geo.links_into(z)]
        g = o4[o4["unit"] == z]
        for d, q, t, fo in zip(pd.DatetimeIndex(g["date"]), g["p"], g["tier"], g["fold"]):
            base = b.specs[(t, fo)]["s4"]["background"]["p"]          # each fold's own table
            win = fired.loc[d - pd.Timedelta(days=7):d, links]
            if not win.to_numpy().any():                   # no true overflow in D−7…D: q is the background alone
                assert abs(q - max(base[lk] for lk in links)) < 1e-12, (z, d)
                quiet += 1
            else:
                assert q > max(base[lk] for lk in links) - 1e-12, (z, d)
                checked += 1
    assert quiet > 20 and checked > 5
    # the chained entry on the same rows does not read the truth: it differs somewhere
    ch = s4[(s4["entry"] == "rain") & (s4["excl"] == "")].set_index(["tier", "unit", "date"])
    ora = o4.set_index(["tier", "unit", "date"])
    common = ora.index.intersection(ch.index)
    assert (ora.loc[common, "p"] != ch.loc[common, "p"]).any()
    # OUT's oracle is 1 on every zone overflow day (x(0) = 1) and 0 when the week was quiet
    oo = out[(out["entry"] == "oracle") & (out["excl"] == "")]
    zo = T.zone_overflow(geo, end=AS_OF).set_index(["zone", "date"])["y"]
    over = zo.reindex(pd.MultiIndex.from_arrays([oo["unit"], pd.DatetimeIndex(oo["date"])])).eq(1).fillna(False).to_numpy(dtype=bool)
    assert over.sum() > 5 and (oo["p"].to_numpy()[over] == 1).all()
    assert ((oo["p"] == 0) & ~pd.Series(over, index=oo.index)).sum() > 100
    assert b.scores["integrity"]["oracle_unknown_reads"] == {"s3": 0, "s4": 0, "out": 0}


def test_x_s3_id_is_read_off_the_spec_with_no_mismatch():
    b = _b()
    geo = b.bundle.geo
    assert B.identity_zones(geo, b.specs[("T1", "final")]["s3"]) == {"ocean": False, "baker_china": False, "north": True, "east": True}
    v1 = C.geo_v1_adapter_specs(stage2=None, impact_table=json.loads((T4.SERVE_DIR / "impact_table.json").read_text()))
    assert B.identity_zones(geo, v1["s3"]) == {"ocean": False, "baker_china": False, "north": True, "east": True}
    ctx = X.context(geo, end=AS_OF)
    assert not ctx.frames["zone"].loc["north", "identity"].any()      # Link.identity would score North
    s3 = b.rows["s3"]
    idr = s3[(s3["entry"] == "oracle") & (s3["excl"] == "X-S3-ID")]
    assert set(idr["unit"]) == {"north", "east"} and len(idr) == b.scores["integrity"]["s3_identity_checked"] > 10
    assert b.scores["integrity"]["s3_identity_mismatches"] == 0 and (idr["p"] == idr["y"]).all()
    assert not (s3[s3["entry"] != "oracle"]["excl"] == "X-S3-ID").any()


# ── scores, the figure, the stamp ───────────────────────────────────────────

def test_scores_hold_every_stage_window_and_comparison():
    sc = _b().scores
    for st in ("s2", "s3", "s4", "out"):
        cell = sc[st]["pooled"]["oracle"]["T1"]
        for k in ("n", "n_pos", "n_blocks", "bs", "bs_ref", "bss", "bss_sample", "logs", "clipped", "roc", "pr", "prev",
                  "corp", "ci", "mde", "contingency", "low_power"):
            assert k in cell, (st, k)
        assert set(cell["contingency"]) == {"0.205", "0.505", "0.805"}
        assert cell["ci"]["level"] == 0.9
    assert sc["aliases"] == {"s2": {"rain": "oracle"}} and "rain" not in sc["s2"]["pooled"]
    vol = sc["s2_volume"]["pooled"]["oracle"]["T1"]
    assert vol["n"] > 20 and 0 < vol["log_mae"] < 3 and 0 <= vol["size_class_acc"] <= 1
    assert sc["s2_volume"]["westside"]["oracle"]["T1"]["median_mg"] > 0
    assert set(sc["s2"]["pooled"]["oracle"]) == {"T1", "T1-holdout", "T2"}
    d = sc["paired"]["oracle_vs_chained"]["out"]["L1"]["pooled"]["T1"]
    assert {"delta", "lo", "hi", "se", "mde", "verdict", "noninferior_5pct"} <= set(d)
    lad = sc["paired"]["ladder"]["pooled"]["T1"]
    assert list(lad["bs"]) == list(B.LADDER) and len(lad["drops"]) == 3
    assert "L1s − L1" in sc["paired"]["as_served"]["s2"]
    prim = sc["paired"]["s5_primary"]
    assert set(prim) == {"oracle", "degraded:1"} and "pooled" in prim["oracle"]["T1"]
    s5 = sc["s5"]["pooled"]["oracle"]["T1"]
    assert set(s5) == set(B.S5.VARIANTS) and s5["sample_swap"]["delta_vs_plain"]["delta"] == 0
    # stated protocol numbers (the reference's training fold: test_the_reference_reads_the_training_fold_…)
    assert sc["bootstrap"] == {"n": 50, "seed": 0, "level": 0.9, "protocol": False}
    assert (B.B_PROTOCOL, B.SEED, B.LEVEL) == (2000, 0, 0.9)
    for k in ("exclusions", "partition", "claims", "catalog", "figure", "figures", "figure_counts", "integrity", "dropped"):
        assert sc[k], k
    assert set(sc["exclusions"]) == {"s2", "s3", "s4", "out", "s5"} and set(sc["claims"]) >= {"C-DRY", "C-RUNOFF"}
    r = _b().rows["out"]
    assert r.loc[r["excl"] == "", "ref"].notna().all()


def test_cis_resample_storm_blocks_and_paired_scores_are_the_brier_differences():
    b = _b()
    sc, blocks = b.scores, T.blocks(end=AS_OF).set_index("date")
    out = b.rows["out"]
    # a cell's CI resamples truth.blocks' storm / quiet blocks with seed 0, never rows
    g = out[(out["entry"] == "L1") & (out["tier"] == "T1") & (out["excl"] == "")]
    blk = blocks["block"].reindex(pd.DatetimeIndex(g["date"])).to_numpy()
    cell = sc["out"]["pooled"]["L1"]["T1"]
    assert cell["n_blocks"] == len(np.unique(blk)) < len(g) / 3
    want = B.V.scores_bundle(g["y"], g["p"], g["ref"], blk, edges=B.RL.edges(), n=50, seed=0, level=0.9)
    assert cell["ci"] == want["ci"] and cell["bss"] == want["bss"]
    assert abs(cell["bss"] - (1 - ((g["p"] - g["y"]) ** 2).sum() / ((g["ref"] - g["y"]) ** 2).sum())) < 1e-12   # Hamill–Juras
    # chained − oracle on the rows both scored
    o = out[(out["entry"] == "oracle") & (out["tier"] == "T1") & (out["excl"] == "")].set_index(["unit", "date"])
    a = g.set_index(["unit", "date"])
    common = a.index.intersection(o.index)
    d = sc["paired"]["oracle_vs_chained"]["out"]["L1"]["pooled"]["T1"]
    bs = lambda f: float(((f.loc[common, "p"] - f.loc[common, "y"]) ** 2).mean())  # noqa: E731
    assert d["n"] == len(common) < len(a) + len(o) and abs(d["delta"] - (bs(a) - bs(o))) < 1e-12 and d["delta"] != 0
    # the ladder's drops are its rungs' Brier differences
    lad = sc["paired"]["ladder"]["pooled"]["T1"]
    for x, y in zip(B.LADDER[1:], B.LADDER[:-1]):
        assert abs(lad["drops"][f"{x} − {y}"]["delta"] - (lad["bs"][x] - lad["bs"][y])) < 1e-12
    # S5: corrected − plain, and the §8 primary link_zone_swap − basin_swap, on the feed's one conditional set
    s5 = b.rows["s5"]
    s5 = s5[(s5["entry"] == "oracle") & (s5["tier"] == "T1") & (s5["excl"] == "")]
    by = {v: g5.set_index(["unit", "date"]).sort_index() for v, g5 in s5.groupby("variant")}
    bs5 = lambda v, col="p": float(((by[v][col] - by[v]["y"]) ** 2).mean())  # noqa: E731
    prim = sc["paired"]["s5_primary"]["oracle"]["T1"]["pooled"]
    assert abs(prim["delta"] - (bs5("link_zone_swap") - bs5("basin_swap"))) < 1e-12 and prim["delta"] != 0
    dv = sc["s5"]["pooled"]["oracle"]["T1"]["basin_swap"]["delta_vs_plain"]
    assert abs(dv["delta"] - (bs5("basin_swap") - bs5("plain"))) < 1e-12 and dv["delta"] != 0
    assert sc["s5"]["pooled"]["oracle"]["T1"]["basin_swap"]["n_blocks"] == prim["n_blocks"] < len(by["plain"])


def test_the_figure_has_every_scored_node_and_renders_its_numbers():
    """One figure per window (scores['figures']); on post-training every node has both pills and they print."""
    sc = _b().scores
    fig = sc["figures"]["T1"]
    nodes = {s["node"] for s in SP.STAGES}
    assert set(fig) - {"caption"} == nodes, sorted(set(fig) ^ nodes)
    assert set(sc["figure"]) - {"caption"} == nodes and set(sc["figures"]) == {"T1", "T1-holdout", "T2"}
    for node in nodes:
        for k in ("oracle", "chained"):
            assert fig[node][k] and fig[node][k]["v"] is not None, (node, k)
    assert len(fig["m.out"]["lead"]) == 6 and fig["m.out"]["lead"][1]["v"] == fig["m.out"]["chained"]["v"]
    svg, phone = F.render("geo_v1", fig, sc["figure_counts"])
    for node in nodes:
        unit = SP.STAGE_OF_CODE[SP.NODE[node]["stage"]]["unit_fmt"]
        for k in ("oracle", "chained"):
            assert F.fmt_score(fig[node][k]["v"], unit) in svg, (node, k)
    assert F.fmt_count(F.flat_counts(sc["figure_counts"]), "X-S4-UNSAMPLED") in svg
    assert "{n_city_days}" not in svg and F.fmt_count(sc["figure_counts"]["slots"], "n_city_days") in svg


def _pills(fig: dict) -> list:
    return [(n, k, e) for n, v in fig.items() if isinstance(v, dict) for k in ("oracle", "chained") for e in [v.get(k)] if isinstance(e, dict)]


def test_the_figure_shows_each_stages_powered_window_with_post_training_in_the_tooltip():
    """Fix A1: every post-training cell is X-POWER, so the figure shows each stage where it has power — S1 its
    Previous Runs window, S2 → OUT cross-season (T2, labelled development; an existing set's S2 development
    (selection-contaminated)), S5 its protocol window — with the post-training value and its CI in the tooltip;
    a pill fades only when its own cell is X-POWER; a one-line caption says so."""
    sc = _b().scores
    fig, shown = sc["figure"], sc["figure_window"]
    assert shown == {"m.s1": "previous_runs", "m.s2": "T2", "m.s3": "T2", "m.s4": "T2", "m.out": "T2", "m.s5": "S5"}
    assert sc["figure_post_window"] == "T1"
    for st, node in (("s2", "m.s2"), ("s4", "m.s4"), ("out", "m.out")):
        t2, t1 = sc[st]["pooled"]["oracle"]["T2"], sc[st]["pooled"]["oracle"]["T1"]
        o = fig[node]["oracle"]
        assert (o["v"], o["lo"], o["n"], o["low_power"]) == (t2["bss"], t2["ci"]["bss"][0], t2["n"], t2["low_power"]), st
        assert (o["post"]["v"], o["post"]["hi"], o["post"]["n"]) == (t1["bss"], t1["ci"]["bss"][1], t1["n"]), st
        assert o["span"][0] >= "2019-07-01" and o["span"][1] <= "2020-06-30" and o["n_seasons"] == 1, (st, o["span"])
        assert ("development (selection-contaminated)" in o["window"]) == (st == "s2") and ": development" in o["window"], st
        assert o["post"]["window"].startswith("post-training, Nov 2025"), o["post"]["window"]
    # chained is lead 1 on the same window: 2019-20 predates the forecast archive, so "—" (never another window)
    assert fig["m.s2"]["chained"] is None and fig["m.out"]["lead"][1] is None and sc["figures"]["T1"]["m.s2"]["chained"]
    assert sc["figures"]["T1"]["m.s2"]["oracle"]["v"] == sc["s2"]["pooled"]["oracle"]["T1"]["bss"] != fig["m.s2"]["oracle"]["v"]
    # S1: the whole Previous Runs window; its post-training cells (stages_s1.post_training) in the tooltip
    s1 = json.loads(B.S1_SCORES.read_text())
    m = s1["by_lead"]["1"]["avg"]["models"][s1["served_model"]]
    post = B.S1.post_training(s1["served_model"])
    c = fig["m.s1"]["chained"]
    assert c["v"] == m["continuous"]["either_wet"]["mae"] and c["span"] == ["2024-01-20", "2026-08-17"]
    assert c["post"]["v"] == post["lead1"]["continuous"]["either_wet"]["mae"] and c["post"]["span"] == ["2025-11-01", "2026-08-17"]
    assert fig["m.s1"]["oracle"]["post"]["v"] == post["floor"][B.S1_FLOOR]["continuous"]["either_wet"]["mae"]
    # a pill fades only when its own cell is X-POWER: one faded element per X-POWER pill or lead bar, no more
    svg = F.render("geo_v1", fig, sc["figure_counts"])[0]
    lead = [e for e in fig["m.out"]["lead"] if isinstance(e, dict)]
    assert svg.count('opacity=".55"') == sum(bool(e["low_power"]) for _, _, e in _pills(fig)) + sum(bool(e["low_power"]) for e in lead)
    assert any(not e["low_power"] for _, _, e in _pills(fig)) and any(e["post"]["low_power"] for _, _, e in _pills(fig) if e.get("post"))
    t = re.search(r"<title>(oracle: BSS [^<]*selection-contaminated[^<]*)</title>", svg).group(1)
    seen = f"; {B.POST_SEEN_WORDS}" if B.CAND.served_info().get("tags", {}).get("post_seen") else ""   # promoted post_seen
    assert f"post-training, Nov 2025 – Aug 2026 ({B.POST_SELECTED_WORDS}){seen}: BSS" in t and t.count("[") == 2, t   # both CIs
    # a GEO_V1 set's post-training days also picked the served set (X-SEL post_selected): its S2 → OUT pills say so,
    # S1 (set-independent) and S5 (the correction rule, which X-SEL does not tag) do not
    for node in ("m.s2", "m.s3", "m.s4", "m.out"):
        for k in ("oracle", "chained"):
            post = (fig[node].get(k) or {}).get("post")
            if post:
                assert post["window"].endswith(f"({B.POST_SELECTED_WORDS}){seen}"), (node, k, post["window"])
    assert all(B.POST_SELECTED_WORDS not in (fig[n][k].get("post") or {}).get("window", "") for n in ("m.s1", "m.s5")
               for k in ("oracle", "chained") if fig[n].get(k))
    # the caption: one plain line, in the figure's title and under the page's
    cap = fig["caption"]
    assert cap == F.caption(sc) and cap.startswith("Pills: S1 one day ahead, Jan 2024 – Aug 2026 · S2 to OUT on 1 season"), cap
    tail = f": {B.POST_SEEN_WORDS}." if seen else "."                 # a served set promoted post_seen says so
    assert "each scored by weights that never saw it" in cap and cap.endswith(f"Post-training (Nov 2025 – Aug 2026) is in each pill’s tooltip{tail}"), cap
    assert "never saw it: development scores, S2's selection-contaminated" in cap, cap      # protocol §2's label, on the face
    assert "\n" not in cap and not re.search(r"\bT[0-3]\b|geo_v1|sfpuc4|X-[A-Z]|\bBSS\b|cost", cap), cap
    assert f'</h3><p class="figcap">{F.K.esc(cap)}</p>' in F.page("geo_v1", fig, sc["figure_counts"]) and F.K.esc(cap) in svg
    # a set the build does not refit per season (gb: post-training only) shows what it has, and says so
    gb = {"s2": {"pooled": {"oracle": {"T1": sc["s2"]["pooled"]["oracle"]["T1"]}}}}
    assert B.window_for(gb, "m.s2", "T2") == "T1" and B.window_for(gb, "m.s2", "T2", fallback=False) == "T2"
    gbf = B.figure(gb, B.FIGURE_WINDOWS, None, B.G.GEO_V1, fallback=True)
    assert "post" not in gbf["m.s2"]["oracle"] and "S2 on post-training days only" in gbf["caption"], gbf["caption"]


def test_s5_pills_are_the_change_in_brier_of_the_served_rule():
    """Fix A2: S5's pills are ΔBrier (corrected − no correction) for the set's own rule (GEO_V1: basin_swap =
    live_v2): the perfect feed, and the degraded feeds' mean over seeds, signed, lower is better; never called
    BSS or skill. The tooltip adds link/zone injection's value and the post-training one."""
    sc = _b().scores
    p = sc["figure"]["m.s5"]
    s5 = sc["s5"]["pooled"]
    d = s5["oracle"]["S5"]["basin_swap"]["delta_vs_plain"]
    assert (p["oracle"]["v"], p["oracle"]["lo"], p["oracle"]["hi"]) == (d["delta"], d["lo"], d["hi"])
    seeds = [f for f in s5 if f.startswith("degraded:")]
    assert seeds and p["chained"]["seeds"] == len(seeds)
    assert abs(p["chained"]["v"] - np.mean([s5[f]["S5"]["basin_swap"]["delta_vs_plain"]["delta"] for f in seeds])) < 1e-12
    assert p["oracle"]["also"] == [dict(p["oracle"]["also"][0], v=s5["oracle"]["S5"]["link_zone_swap"]["delta_vs_plain"]["delta"],
                                        what="link/zone injection")]
    assert p["oracle"]["post"]["v"] == s5["oracle"]["T1"]["basin_swap"]["delta_vs_plain"]["delta"]
    assert SP.STAGE["s5"]["unit_fmt"] == "delta" and "lower is better" in SP.NODE["m.s5"]["metric"]
    assert F.fmt_score(-0.035, "delta") == "−0.035" and F.fmt_score(0.08, "delta") == "+0.080" and F.fmt_score(0.123, "bss") == "0.12"
    svg, ph = F.render("geo_v1", sc["figure"], sc["figure_counts"])
    for k in ("oracle", "chained"):
        words = F.fmt_score(p[k]["v"], "delta")
        assert words[0] in "+−" and f"{k} {words}" in svg and f"{k} {words}" in ph, (k, words)
    tips = re.findall(r"<title>((?:oracle|chained): change in Brier [^<]*)</title>", svg)
    assert len(tips) == 2 and all("lower is better" in t and "link/zone injection: change in Brier" in t for t in tips), tips
    assert not any(re.search(r"\bBSS\b|skill", t) for t in tips), tips
    shown = re.sub(r"<[^>]+>", " ", re.sub(r"<title>.*?</title>", " ", svg, flags=re.S))
    assert "change in Brier (lower is better)" in shown


def test_x_power_counts_rain_storm_blocks_in_s5_and_s1_too():
    """Protocol §6's X-POWER, the same rule everywhere: fewer than 10 positives or fewer than 8 storm blocks, the rain
    storms of truth.blocks. S5's cells resample observation events but count the storms their rows touch
    (n_storm_blocks), never the observation events; S1's pills count the storm blocks overlapping their days, so the
    post-training S1 pills are X-POWER and the Previous Runs ones are not."""
    b = _b()
    blocks = T.blocks(end=AS_OF).set_index("date")
    s5 = b.rows["s5"]
    checked, differ = 0, 0
    for feed, by_w in b.scores["s5"]["pooled"].items():
        for w, by_v in by_w.items():
            g = s5[(s5["entry"] == feed) & s5["tier"].isin(B.S5_WINDOW if w == "S5" else (w,)) & (s5["excl"] == "")
                   & (s5["variant"] == "plain")]
            sid, storm = B._storm_blocks(g["date"], blocks)
            n = len(np.unique(sid[storm]))
            for v, cell in by_v.items():
                assert cell["n_storm_blocks"] == n and cell["low_power"] == (cell["n_pos"] < 10 or n < 8), (feed, w, v)
                checked, differ = checked + 1, differ + (n != cell["n_blocks"])
    assert checked and differ, (checked, differ)                     # observation events are not storms
    s1 = json.loads(B.S1_SCORES.read_text())
    fig = B.s1_figure(s1, B.S1.post_training(s1["served_model"]), blocks)

    def storms(span):
        d = blocks.loc[span[0]:span[1]]
        return d.loc[d["block_kind"] == "storm", "block"].nunique()
    for k in ("oracle", "chained"):
        p, post = fig[k], fig[k]["post"]
        assert p["low_power"] is False and p["pos"] >= 10 and storms(p["span"]) >= 8, (k, p)
        assert post["low_power"] is True and post["pos"] >= 10 and storms(post["span"]) < 8, (k, post)   # too few storms alone
    short = dict(s1, by_lead={"1": {"avg": {"models": {s1["served_model"]: dict(
        s1["by_lead"]["1"]["avg"]["models"][s1["served_model"]], first="2025-12-01", last="2025-12-31")}}}})
    assert B.s1_figure(short, None, blocks)["chained"]["low_power"] is True


def test_figure_words_on_fixture_cells():
    """On fixture cells (no build): an S5 pill keeps its cell's seasons, so a cross-season S5 pill (scores['figures']
    ['T2']) says how many; the degraded feeds' pill is the seeds' mean change in Brier, from the lowest seed's lower
    bound to the highest's upper; and the caption labels cross-season pills 'development' on the face (an existing
    set's S2 'selection-contaminated', protocol §2), since a phone shows no tooltip."""
    span = ["2018-11-22", "2025-02-18"]

    def s5(d, lo, hi, n_seasons):
        return {"n_pos": 20, "low_power": False, "span": span, "n_seasons": n_seasons,
                "delta_vs_plain": {"delta": d, "lo": lo, "hi": hi, "n": 100}}
    pooled = {"oracle": {"T2": {"basin_swap": s5(-0.01, -0.03, 0.01, 3), "link_zone_swap": s5(-0.02, -0.04, 0.0, 3)}},
              "degraded:1": {"T2": {"basin_swap": s5(0.02, 0.01, 0.05, 2), "link_zone_swap": s5(0.0, -0.01, 0.01, 2)}},
              "degraded:2": {"T2": {"basin_swap": s5(0.04, 0.0, 0.07, 3), "link_zone_swap": s5(0.0, -0.01, 0.01, 3)}}}
    fig = B.figure({"s5": {"pooled": pooled}}, "T2", None, B.G.GEO_V1)
    o, c = fig["m.s5"]["oracle"], fig["m.s5"]["chained"]
    assert "3 seasons, each scored by weights that never saw it" in o["window"], o["window"]
    assert abs(c["v"] - 0.03) < 1e-12 and (c["lo"], c["hi"], c["seeds"], c["n_seasons"]) == (0.0, 0.07, 2, 3), c
    assert "the mean of 2 degraded feeds" in c["window"] and "3 seasons" in c["window"], c["window"]
    bss = {"bss": 0.5, "ci": {"bss": [0.4, 0.6]}, "n": 100, "n_pos": 20, "low_power": False,
           "span": ["2016-07-01", "2025-06-30"], "n_seasons": 9}
    sc = {st: {"pooled": {"oracle": {"T2": bss}}} for st in ("s2", "s4", "out")}
    for geo, tail in ((B.G.GEO_V1, "development scores, S2's selection-contaminated"), (B.G.SFPUC4_V1, "development scores")):
        cap = B.figure(sc, B.FIGURE_WINDOWS, None, geo)["caption"]
        assert cap.startswith(f"Pills: S2, S4, OUT on 9 seasons (Jul 2016 – Jun 2025), each scored by weights that never saw it: {tail}."), cap
    cap = B.figure({st: sc[st] for st in ("s4", "out")}, B.FIGURE_WINDOWS, None, B.G.GEO_V1)["caption"]
    assert "development scores" in cap and "selection-contaminated" not in cap, cap          # S2's label only where S2 is
    # post-training: a GEO_V1 set's words say those days also picked the served set (X-SEL post_selected); an SFPUC4
    # set's do not, nor does S5's (X-SEL does not tag it)
    cell = {"span": ["2025-11-01", "2026-08-17"]}
    assert B.window_words("T1", cell, "out", B.G.GEO_V1) == f"post-training, Nov 2025 – Aug 2026 ({B.POST_SELECTED_WORDS})"
    assert B.window_words("T1", cell, "out", B.G.SFPUC4_V1) == "post-training, Nov 2025 – Aug 2026"
    assert B.window_words("T1", cell, "s5", B.G.GEO_V1) == "post-training, Nov 2025 – Aug 2026"
    t1 = {st: {"pooled": {"oracle": {"T1": dict(bss, span=cell["span"], n_seasons=None)}}} for st in ("s2", "s4", "out")}
    for geo, note in ((B.G.GEO_V1, True), (B.G.SFPUC4_V1, False)):
        f = B.figure({st: {"pooled": {"oracle": {"T2": bss, "T1": t1[st]["pooled"]["oracle"]["T1"]}}} for st in t1},
                     B.FIGURE_WINDOWS, None, geo)
        assert all((B.POST_SELECTED_WORDS in f[n]["oracle"]["post"]["window"]) == note for n in ("m.s2", "m.s4", "m.out")), geo
        assert (B.POST_SELECTED_WORDS in B.figure(t1, "T1", None, geo)["m.out"]["oracle"]["window"]) == note, geo


S5_OWN = ("X-S5-HEALTH", "X-S5-CIRC", "X-S5-PERFECT-SIBLING", "X-S5-SELF", "X-S5-QUIET")


def test_s5_chips_print_the_perfect_feeds_distinct_zone_days():
    """Fix A3, as amended 2026-10-02: S5's not-scored chip prints the perfect feed's counts in S5's window (T1-holdout ∪
    T1; this slice replays T1), so each count is distinct zone-days, never a sum over feeds and windows that see the
    same zone-days; every feed's own counts are in the chip's tooltip; a rule that is 0 by construction prints 0 with
    its reason; and S5's own copy of OUT's rules does not overwrite OUT's chip."""
    sc = _b().scores
    fc = sc["figure_counts"]
    part = sc["partition"]["s5"]
    want = {x: sum(part[u]["oracle"][t]["excluded"].get(x, 0) for u in part for t in B.S5_WINDOW if t in part[u].get("oracle", {}))
            for x in S5_OWN}
    for x in S5_OWN:
        assert fc["exclusions"][x] == fc["stages"]["s5"][x] == want[x], x
    assert fc["exclusions"]["X-S5-SELF"] > 0 and fc["exclusions"]["X-S5-QUIET"] == fc["exclusions"]["X-S5-HEALTH"] == 0
    assert fc["exclusions"]["X-S5-CIRC"] == 0                                         # the archive's rule: not the perfect feed's
    tot = {x: sum(u.values()) for x, u in sc["exclusions"]["s5"].items()}
    assert tot["X-S5-SELF"] > fc["exclusions"]["X-S5-SELF"], "the old chip summed the degraded feed's zone-days in too"
    assert fc["exclusions"]["X-E2E-UNK"] == sum(sc["catalog"]["exclusions"]["out"]["X-E2E-UNK"].values())
    feeds = fc["feeds"]["s5"]
    assert [f["what"].split(",")[0] for f in feeds["by_feed"]] == ["the perfect feed", "degraded feed 1"], feeds
    assert feeds["by_feed"][0]["counts"] == want and "each zone-day once" in feeds["chip"], feeds
    svg, ph = F.render("geo_v1", sc["figure"], fc)
    for words in (f"the replaced day {fc['exclusions']['X-S5-SELF']:,}", "nothing seen nearby 0", "watcher down 0", "archive Westside days 0"):
        assert words in svg and words in ph, words
    tip = re.search(r'<a href="#s5"><title>(x\.s5 [^<]*)</title>', svg).group(1)
    for x in ("X-S5-QUIET", "X-S5-HEALTH", "X-S5-CIRC"):
        assert f"(0: {F.K.esc(SP.EXCLUSIONS[x]['zero'])})" in tip, x
    assert "(0: " not in tip.split("X-S5-SELF")[1].split("|")[0], "a counted rule with rows has no zero reason"
    assert F.K.esc(f"counts: {feeds['chip']}") in tip, tip
    per = tip.split("per feed: ", 1)[1].split("; ")
    assert per[0].startswith("the perfect feed, Nov 2025 – Aug 2026: ") and f"the replaced day {want['X-S5-SELF']:,}" in per[0], per
    n1 = feeds["by_feed"][1]["counts"]["X-S5-SELF"]
    assert per[1] == f"degraded feed 1, Nov 2025 – Aug 2026: the replaced day {n1:,}" and n1 > 0, per    # no sibling rule off the perfect feed


def test_s5_feed_counts_never_count_a_zone_day_twice():
    """On a fixture partition (no build): the perfect feed and each degraded feed count S5's window only (their T2
    seasons overlap the holdout), the 2016-17 archive its own season; S5's own rules are present as 0; the chip reads
    the perfect feed, and with no perfect-feed rows it prints "—" rather than the truth catalog's archive count."""
    def cell(ex=None):
        ex = ex or {}
        return {"n_total": 10, "n_scored": 10 - sum(ex.values()), "excluded": ex}
    part = {"ocean": {"oracle": {"T2": cell({"X-S5-SELF": 7}), "T1-holdout": cell({"X-S5-SELF": 2}), "T1": cell({"X-S5-SELF": 1})},
                      "degraded:2": {"T1": cell({"X-S5-SELF": 4, "X-E2E-UNK": 1})},
                      "archive": {"T2": cell({"X-S5-CIRC": 5})}},
            "east": {"oracle": {"T1": cell({"X-S5-PERFECT-SIBLING": 3})}}}
    got = B.s5_feed_counts(part, AS_OF)
    assert list(got) == ["oracle", "archive", "degraded:2"]
    assert got["oracle"]["tiers"] == ["T1-holdout", "T1"] and got["oracle"]["span"] == ["2023-07-01", AS_OF]
    assert got["oracle"]["counts"] == {"X-S5-HEALTH": 0, "X-S5-CIRC": 0, "X-S5-PERFECT-SIBLING": 3, "X-S5-SELF": 3, "X-S5-QUIET": 0}
    assert got["degraded:2"]["span"] == ["2025-11-01", AS_OF] and got["degraded:2"]["counts"]["X-E2E-UNK"] == 1
    assert got["archive"]["tiers"] == ["T2"] and got["archive"]["span"] is None and got["archive"]["counts"]["X-S5-CIRC"] == 5
    assert _raises(lambda: B.s5_feed_counts({"ocean": {"sideways": {"T1": cell()}}}, AS_OF), KeyError)
    assert B.feed_words("degraded:3") == "degraded feed 3" and B.feed_words("oracle") == "the perfect feed"
    fc = B.figure_counts("geo_v1", AS_OF, None, part)[0]
    assert {x: fc["exclusions"][x] for x in S5_OWN} == got["oracle"]["counts"] == {x: fc["stages"]["s5"][x] for x in S5_OWN}
    assert [f["what"] for f in fc["feeds"]["s5"]["by_feed"]] == ["the perfect feed, Jul 2023 – Aug 2026", "the 2016-17 archive, its own season",
                                                               "degraded feed 2, Nov 2025 – Aug 2026"]
    none = B.figure_counts("geo_v1", AS_OF, None, {"ocean": {"archive": part["ocean"]["archive"]}})[0]
    assert not any(x in none["exclusions"] for x in S5_OWN) and none["stages"]["s5"] == {} and none["feeds"]["s5"]["chip"] is None
    svg = F.render("geo_v1", None, none)[0]
    assert "archive Westside days —" in svg and "the replaced day —" in svg
    assert "per feed: the 2016-17 archive, its own season: archive Westside days 5" in svg
    # a perfect feed scored only cross-season (a build without S5's window) says so: never "its own season", never a
    # window its counts do not come from; a feed with no words for its windows raises
    t2 = B.figure_counts("geo_v1", AS_OF, None, {"ocean": {"oracle": {"T2": cell({"X-S5-SELF": 7})}}})[0]
    assert t2["exclusions"]["X-S5-SELF"] == 7 and t2["feeds"]["s5"]["by_feed"][0]["what"] == "the perfect feed, the cross-season folds"
    assert t2["feeds"]["s5"]["chip"] == "the perfect feed's zone-days in the cross-season folds, each zone-day once", t2["feeds"]
    assert fc["feeds"]["s5"]["chip"] == "the perfect feed's zone-days in S5's window (Jul 2023 – Aug 2026), each zone-day once"
    assert B.s5_feed_counts({"ocean": {"oracle": {"T9": cell()}}}, AS_OF)["oracle"]["span"] is None      # counted, no span …
    assert _raises(lambda: B.figure_counts("geo_v1", AS_OF, None, {"ocean": {"oracle": {"T9": cell()}}}), ValueError)  # … no words
    # T0 is in S5's window (2023-07-01 → the data end, protocol §8) once the data reach it; before, a T0 row cannot be
    t0 = B.s5_feed_counts({"ocean": {"oracle": {"T1": cell({"X-S5-SELF": 1}), "T0": cell({"X-S5-SELF": 2})}}}, "2026-12-31")
    assert t0["oracle"]["tiers"] == ["T1", "T0"] and t0["oracle"]["span"] == ["2025-11-01", "2026-12-31"] and t0["oracle"]["counts"]["X-S5-SELF"] == 3
    assert _raises(lambda: B.s5_feed_counts({"ocean": {"oracle": {"T0": cell()}}}, AS_OF), ValueError)


def test_the_out_card_shows_oracle_and_lead_1_pills_and_its_lead_strip():
    """Fix A4: OUT's card carries oracle and chained (lead 1) pills like every stage card, keeps the lead strip,
    fades an X-POWER lead bar, and the 'today' bar says lead 0 is the optimistic stitched archive."""
    sc = _b().scores
    fig = sc["figures"]["T1"]
    o, c = fig["m.out"]["oracle"], fig["m.out"]["chained"]
    assert o["v"] == sc["out"]["pooled"]["oracle"]["T1"]["bss"] and c["v"] == sc["out"]["pooled"]["L1"]["T1"]["bss"] == fig["m.out"]["lead"][1]["v"]
    svg = F.render("geo_v1", fig, sc["figure_counts"])[0]
    card = svg.split('<a href="#out"><title>m.out ', 1)[1].split("</a>", 1)[0]
    assert f"oracle {F.fmt_score(o['v'], 'bss')}" in card and f"chained {F.fmt_score(c['v'], 'bss')}" in card
    assert card.count('class="lead') == 6 and "stitched short-lead archive" in card.split("+1")[0] and "optimistic" in card
    faded = sum(bool((e or {}).get("low_power")) for e in fig["m.out"]["lead"])
    assert faded and card.count('opacity=".55"') == faded + sum(bool(e["low_power"]) for e in (o, c))


def test_s3_chained_is_scored_on_the_oracles_rows():
    """Fix A5: S3's chained pill is lead 1 on the unit-days the oracle scored (the Westside split on overflow days),
    against the oracle's reference, so the two are paired and comparable; lead 1 on every day is in the tooltip."""
    b = _b()
    sc = b.scores
    f = sc["figures"]["T1-holdout"]["m.s3"]                    # lead 1 exists from Jan 2024, inside the holdout
    r = b.rows["s3"]
    r = r[(r["tier"] == "T1-holdout") & (r["excl"] == "")]
    o, c = (r[r["entry"] == e].set_index(["unit", "date"]) for e in ("oracle", "L1"))
    common = o.index.intersection(c.index)
    assert 0 < f["chained"]["n"] == len(common) == sc["s3_on_oracle_rows"]["pooled"]["L1"]["T1-holdout"]["n"] < len(c)
    y = o.loc[common, "y"].to_numpy()
    bss = 1 - ((c.loc[common, "p"].to_numpy() - y) ** 2).sum() / ((o.loc[common, "ref"].to_numpy() - y) ** 2).sum()
    assert abs(f["chained"]["v"] - bss) < 1e-12
    every = sc["s3"]["pooled"]["L1"]["T1-holdout"]
    assert f["chained"]["also"][0]["what"] == "lead 1 on every day" and (f["chained"]["also"][0]["v"], f["chained"]["also"][0]["n"]) == (every["bss"], every["n"])
    assert f["chained"]["v"] != every["bss"] and "on the oracle's own days" in f["chained"]["window"]
    tip = re.search(r"<title>(chained: BSS [^<]*on the oracle's own days[^<]*)</title>", F.render("geo_v1", sc["figures"]["T1-holdout"])[0]).group(1)
    assert "lead 1 on every day: BSS" in tip, tip


def test_s4_truth_reads_stardb_in_its_window():
    """Fix B in the build: S4 and OUT are graded on design D10's three records, so the T2 season 2019-20, which DataSF
    (from 2020-07-27) and Poo Bot (to 2017-01) never sampled, has scored S4 rows from STARDB."""
    b = _b()
    r = b.rows["s4"]
    t2 = r[(r["tier"] == "T2") & (r["excl"] == "")]
    assert len(t2) > 100 and pd.DatetimeIndex(t2["date"]).min() >= pd.Timestamp("2019-07-01"), len(t2)
    assert b.scores["s4"]["pooled"]["oracle"]["T2"]["n"] == int((t2["entry"] == "oracle").sum())
    assert b.scores["figure_counts"]["exclusions"]["X-S4-UNSAMPLED"] == 11950          # as of 2026-08-17 (DataSF + Poo Bot: 13,120)
    for name in ("stardb", "datasf", "poobot"):
        assert str(B.SMP.SOURCE_FILES[name].relative_to(ROOT)) in b.manifest["inputs"], name


def test_s1_scores_are_pinned_without_their_clock():
    """Fix D: s1_scores.json's built_at never enters the sha256 a stage manifest pins, so rebuilding S1 on unchanged
    data stales nothing; any other change to it does. Every other input, and the code, are pinned by their bytes."""
    doc = json.loads(B.S1_SCORES.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "s1_scores.json"
        old = B.UNSTAMPED
        try:
            B.UNSTAMPED = {a: ("built_at",)}
            a.write_text(json.dumps(doc))
            h = B.input_sha(a)
            a.write_text(json.dumps({**doc, "built_at": "2099-01-01T00:00:00+00:00"}, indent=1))   # a new clock, other bytes
            assert B.input_sha(a) == h and hashlib.sha256(a.read_bytes()).hexdigest() != h
            a.write_text(json.dumps({**doc, "data_end": "2099-01-01"}))
            assert B.input_sha(a) != h, "a change in what the scores say makes them stale"
        finally:
            B.UNSTAMPED = old
    m = _b().manifest
    rel = str(B.S1_SCORES.relative_to(ROOT))
    assert m["inputs"][rel] == B.input_sha(B.S1_SCORES) != hashlib.sha256(B.S1_SCORES.read_bytes()).hexdigest()
    assert m["inputs_unstamped"] == {rel: ["built_at"]} and B.stale_files({"inputs": {rel: m["inputs"][rel]}}) == []
    for f, h in m["inputs"].items():
        if f != rel:
            assert hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == h, f
    code = "features/forecast/src/models/stages_build.py"
    assert m["code"][code] == hashlib.sha256((ROOT / code).read_bytes()).hexdigest()
    assert B.stale_files({"inputs": {}, "code": {code: "0" * 64}}) == [code], "the code-sha guard stays"


def test_the_protocol_stamp_is_the_files_sha():
    text = X.PROTOCOL.read_text()
    sha = re.search(r"^protocol sha256: ([0-9a-f]{64})$", text, re.M).group(1)
    body = text.replace(f"protocol sha256: {sha}", "protocol sha256: <filled at commit>", 1)
    assert hashlib.sha256(body.encode()).hexdigest() == sha
    m = _b().manifest
    assert m["protocol"] == f"{SP.PROTOCOL_VERSION}@{sha}" == _b().scores["protocol"]
    assert m["schema"] == "bwtf.stages/1" and m["spec_version"] == SP.SPEC_VERSION and m["geography"] == "geo_v1"
    assert m["components"]["s5"] == "live_v2" and m["components"]["s3"] == "split_v2" and m["components"]["s1"] == E.served_weather_model()
    for f, h in m["inputs"].items():
        assert B.input_sha(ROOT / f) == h, f


def test_a_rebuild_gives_the_same_scores():
    kw = dict(entries=("oracle", "rain", "L1"), tiers=("T1",), feeds=("oracle",), n_boot=50, log=lambda *a: None)
    a, b = B.build("served", **kw), B.build("served", **kw)
    assert json.dumps(a.scores) == json.dumps(b.scores)
    assert a.scores["s2"]["pooled"]["oracle"]["T1"] == _b().scores["s2"]["pooled"]["oracle"]["T1"]


def test_writing_stays_under_the_sets_directory_and_needs_the_protocols_bootstrap():
    b = _b()
    assert _raises(lambda: B.write(b), ValueError)                      # B = 50 is not the protocol's 2,000
    with tempfile.TemporaryDirectory() as tmp:
        old = B.STAGES_DIR
        try:
            B.STAGES_DIR = Path(tmp)
            paths = B.write(type(b)(**{**b.__dict__, "n_boot": B.B_PROTOCOL}))
        finally:
            B.STAGES_DIR = old
        assert sorted(p.name for p in paths) == ["manifest.json", "rows.csv.gz", "scores.json"]
        assert all(p.parent == Path(tmp) / b.bundle.name for p in paths)
        rows = pd.read_csv(Path(tmp) / b.bundle.name / "rows.csv.gz", keep_default_na=False, na_values=[""], low_memory=False)
        assert list(rows.columns) == list(X.COLUMNS) and len(rows) == sum(len(r) for r in b.rows.values())
        assert json.loads((Path(tmp) / b.bundle.name / "scores.json").read_text()) == json.loads(json.dumps(b.scores))
    assert _raises(lambda: B.load_set("not_a_set", "candidates"), KeyError, ValueError)
    assert _raises(lambda: B.build("served", entries=("L9",)), KeyError)
    assert _raises(lambda: B.build("served", steps=("s7",)), KeyError)


def test_a_candidate_is_compared_with_the_served_set_on_identical_rows():
    """A GEO_V1 candidate against the served slice written by this code (the committed artifacts' currency is
    test_the_committed_artifacts_are_current's): Δ on identical rows, and the primaries and §9 block stated."""
    d = _served_dir()
    old = B.STAGES_DIR
    try:
        B.STAGES_DIR = d
        c = B.build("icon-w38-nosplit-lt1-bflags", "candidates", entries=("oracle", "rain"), tiers=("T1",), feeds=("oracle",), n_boot=50,
                    log=lambda *a: None)
    finally:
        B.STAGES_DIR = old
    vs = c.scores["paired"]["vs_served"]
    assert "skipped" not in vs, vs.get("skipped")
    assert vs["served"] == B.served_name() and c.manifest["components"]["s3"] == "basin_v1"
    # logit_v1 is the served set's stage 1: S2 is the same to the last written digit, so Δ is exactly 0
    for u, cell in vs["s2"].items():
        d = cell["oracle"]["T1"]
        assert d["delta"] == 0 and d["lo"] == d["hi"] == 0 and d["verdict"] == "no clear difference", u
    # …while its S3 has no split, so the Westside zones' oracle moves (and the identity zones do not)
    assert vs["s3"]["pooled"]["oracle"]["T1"]["delta"] > 0
    assert set(vs["s3"]) >= {"ocean", "baker_china"} and vs["out"]["pooled"]["rain"]["T1"]["mcb"]["metric"] == "_mcb"
    assert vs["geography"] == {"candidate": "geo_v1", "served": "geo_v1", "invariant_only": False}
    if B.out_dir("icon-w38-nosplit-lt1-bflags").exists():                 # a candidate's directory holds no rows
        assert not (B.out_dir("icon-w38-nosplit-lt1-bflags") / "rows.csv.gz").exists()
    # the primaries of a GEO_V1 candidate: S2 against the served component itself (no T2 in this slice), no
    # geography or South row, S5 from its own build (basin_swap exists on GEO_V1); a changed S3 that no §8 row
    # scores (no split, no size share) leaves criterion 1 not met
    pr = {r["id"]: r for r in c.scores["primaries"]["rows"]}
    assert pr["S2"]["parts"][0]["status"] == "not yet computable" and pr["S2"]["parts"][1]["delta"] == 0
    assert all(pr[k]["status"] == "not applicable" for k in ("S1", "S2-south-floor", "S2-volume", "S3a", "S3b"))
    assert "this build" in pr["S5"]["reason"] and pr["S5"]["parts"][0]["status"] in ("pass", "fail")
    assert c.scores["primaries"]["changed"] == {"s1": False, "s2": False, "s3": True, "s4": True, "s5": False}
    c1 = c.scores["promotion"]["criteria"][0]
    assert c1["status"] == "not met" and "s3: no §8 primary scores this change" in c1["reason"], c1
    # §8's S4 row tests S4 v3: logit_v1's changed S4 (stage 2 v1's raw table) has no row, so it decides nothing
    assert c.manifest["components"]["s4"] == "impact_v1" and pr["S4"]["status"] == "not applicable", pr["S4"]
    assert "S4 v3" in pr["S4"]["reason"] and "s4: no §8 primary scores this change" in c1["reason"], (pr["S4"], c1)


def test_the_committed_artifacts_are_current():
    d = B.STAGES_DIR / B.served_name()
    if not (d / "scores.json").exists():
        print("SKIP no committed stages artifacts yet")
        return
    m = json.loads((d / "manifest.json").read_text())
    sc = json.loads((d / "scores.json").read_text())
    assert m["schema"] == B.SCHEMA and m["set"] == B.served_name() and m["bootstrap"]["n"] == B.B_PROTOCOL
    assert "features/forecast/src/models/stages_build.py" in m["code"] and not any(f.startswith("tests/") for f in m["code"])
    stale = B.stale_files(m)
    assert not stale, f"{stale} changed since the build: rerun stages_build --set served --write (then the candidates)"
    assert sc["protocol"] == m["protocol"] and set(sc["figure"]) - {"caption"} == {s["node"] for s in SP.STAGES}
    assert isinstance(sc["figure"].get("caption"), str) and sc["figure"]["caption"].startswith("Pills: "), sc["figure"].get("caption")
    # the served set is refit per season: its figure shows every stage's powered window, never a fallback
    assert sc["figure_window"] == {"m.s1": B.S1_WINDOW, **B.FIGURE_WINDOWS}, sc["figure_window"]
    assert (d / "rows.csv.gz").stat().st_size < 6.5e6
    assert not any((d / n).exists() for n in ("alert_lines.json",))
    # every other set's artifacts too: current, compared with the served set on its rows, rows only for a stage
    # candidate (design §7), the §8 primaries and the §9 criteria stated, never a word to promote
    for c in sorted(p for p in B.STAGES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_") and p != d):
        mc = json.loads((c / "manifest.json").read_text())
        scc = json.loads((c / "scores.json").read_text())
        assert mc["set"] == c.name and mc["bootstrap"]["n"] == B.B_PROTOCOL and not B.stale_files(mc), c.name
        assert scc["protocol"] == m["protocol"] and (c / "rows.csv.gz").exists() == (mc["root"] == B.STAGE_ROOT), c.name
        assert "skipped" not in scc["paired"]["vs_served"], (c.name, scc["paired"]["vs_served"].get("skipped"))
        assert [r["id"] for r in scc["primaries"]["rows"]] == list(B.protocol_rules()["rows"]), c.name
        assert len(scc["promotion"]["criteria"]) == 5 and "promote" not in json.dumps(scc["promotion"]).lower(), c.name


# ── a stage candidate (P8: sfpuc4_v1 under stages_candidates/) ──────────────
# A stub saved through stages_candidates into a temporary root, with a stub bake-off directory: logistic S2 with
# weights ≥ 0 on three rain terms (finals, holdout siblings, the 2019-20 outer fold as rows), log-linear heads
# (the declared fallback where a basin is under the floor), a logit_logvol Westside split and a zone_v3 S4 with a
# rain background, one record per fold. The served slice above is written to a temporary stages directory, so
# the comparisons read rows built on this very code.

# A complete set is stored under its lineup's id (Part B 36): the stub's own parts get codes, in this process only.
for _col, _cid, _code in (("s2", "stub_s2", "stubs2"), ("s3", "stub_links", "stublinks"), ("s4", "stub_zone_v3", "stubzone")):
    LU.CODES[_col].setdefault(_cid, _code)
STUB = LU.set_id({"geography": "sfpuc4_v1", "s1": E.served_weather_model(), "s2": "stub_s2", "s3": "stub_links",
                  "s4": "stub_zone_v3", "s5": "link_zone_swap"})
STUB_FEATURES = ["precip_avg", "rain_3d_cum", "rain_max3h"]
RECIPE_FEATURES = ["precip_avg", "rain_2d_cum"]
STUB_HEAD = ["precip_avg", "rain_max3h"]
STUB_SEASON = 2019
STUB_SLICE = dict(entries=("oracle", "rain", "L1"), tiers=("T1", "T1-holdout", "T2"), seasons=(STUB_SEASON,),
                  feeds=("oracle", "degraded:1"), s5_tiers=("T1",), n_boot=50, log=lambda *a: None)
STUB_S3_S2 = "dev:stub_stand_in"          # the S2 the stub's shares say they were fit on: a development stand-in
STUB_VS_FALLBACK = {"metric": "log_mae", "n": 41, "n_blocks": 17, "a": 0.81, "b": 0.84, "delta": -0.03, "lo": -0.07,
                    "hi": 0.01, "se": 0.02, "p_neg": 0.88, "mde": 0.05, "mde_pct": 0.06, "level": 0.9,
                    "verdict": "no clear difference"}
_TMP = tempfile.TemporaryDirectory()


def _stub_folds():
    """(tier, fold, training-row filter, fit seasons, fit span) of the stub's three folds."""
    seasons = [s for s in S2.T2_SEASONS if s != STUB_SEASON]
    return (("T1", "final", lambda f: f["date"] <= S2.TRAINED_THROUGH, list(range(2015, 2026)), ["2016-03-01", "2025-10-31"]),
            ("T1-holdout", "pre_holdout", lambda f: f["date"] < S2.HOLDOUT_START, list(range(2015, 2023)), ["2016-03-01", "2023-06-30"]),
            ("T2", S2.season_label(STUB_SEASON), lambda f: f["season"].isin(seasons), seasons, ["2016-07-01", "2025-06-30"]))


@functools.lru_cache(maxsize=1)
def _stub_data():
    from shared import geography as G
    geo = G.SFPUC4_V1
    src = {b.key: G.RAIN_OF_FACILITY[b.facility] for b in geo.basins}
    frames, _ = T4.build_dataset(sources=sorted(set(src.values())), input_rules=["gauge_outage_v1"], geo=geo)
    return geo, src, frames


def _stub_fit(keep, features=STUB_FEATURES, C_=1.0):
    """{basin: model dict}, {basin: head dict} on the rows ``keep`` holds: weights clipped at 0 (A5), a basin under
    20 known-volume events taking the pooled Bay-side fallback with a basin offset (Part B 7)."""
    import stages_s2_sfpuc4 as S2C
    from sklearn.linear_model import LinearRegression, LogisticRegression
    geo, src, frames = _stub_data()
    w, h, ev_of = {}, {}, {}
    for b in geo.basins:
        sub = T4.target_frame(frames[src[b.key]], b.key, geo)
        sub = sub[(sub[f"{b.name}_label_source"] != "poobot") & keep(sub)]
        lr = LogisticRegression(C=C_, max_iter=2000).fit(sub[features], sub["y"].astype(int))
        lr.coef_ = np.maximum(lr.coef_, 0.0)
        w[b.key] = {"model": lr, "features": list(features), "rain_source": src[b.key], "calibration_offset": 0.0, "family": "logit"}
        ev_of[b.key] = sub[(sub["y"] == 1) & (sub[f"{b.name}_volume_mg"] > 0)]
    bay = [b.key for b in geo.basins if b.facility == S2C.FALLBACK_FACILITY]
    for b in geo.basins:
        ev = ev_of[b.key]
        yv = np.log1p(ev[f"{b.name}_volume_mg"])
        m = LinearRegression(positive=True).fit(ev[STUB_HEAD], yv)
        kind = "loglinear"
        if len(ev) < S2.HEAD_MIN_EVENTS:                      # the declared fallback: pooled Bay-side slopes, a basin offset
            Xc = pd.concat([ev_of[k][STUB_HEAD] - ev_of[k][STUB_HEAD].mean() for k in bay])
            yc = np.concatenate([np.log1p(ev_of[k][f"{geo.basin(k).name}_volume_mg"]) - np.log1p(ev_of[k][f"{geo.basin(k).name}_volume_mg"]).mean()
                                 for k in bay])
            pooled = LinearRegression(positive=True, fit_intercept=False).fit(Xc, yc)
            m.coef_ = pooled.coef_.copy()
            m.intercept_ = float(yv.mean() - ev[STUB_HEAD].mean().to_numpy() @ pooled.coef_)
            kind = S2C.FALLBACK_KIND
        h[b.key] = {"model": m, "features": list(STUB_HEAD), "rain_source": src[b.key], "target": "log1p_volume_mg",
                    "n_events": int(len(ev)), "kind": kind}
    return w, h


def _window_rows(w, h, tier, fold, arm, lo, hi):
    """One arm's rows as stages_s2_sfpuc4 writes them, with the labels the bake-off read (truth.basin_onsets: NaN
    where the ledger does not know the day)."""
    geo, src, _ = _stub_data()
    fr = {s: f.set_index("date") for s, f in E.frames("oracle", sorted(set(src.values()))).items()}
    y = T.basin_onsets(geo).set_index(["basin", "date"])["y"]
    parts = []
    for k in geo.keys:
        d = fr[src[k]].index[(fr[src[k]].index >= pd.Timestamp(lo)) & (fr[src[k]].index <= pd.Timestamp(hi))]
        Xk = fr[src[k]].loc[d]
        parts.append(pd.DataFrame({"arm": arm, "contender": "stub", "date": d, "unit": k, "tier": tier, "fold": fold,
                                   "p": T4.calibrated(w[k], Xk), "v_hat": T4.predicted_volume(h[k], Xk) if h else np.nan,
                                   "y": y.reindex(pd.MultiIndex.from_arrays([[k] * len(d), d])).to_numpy(dtype=float),
                                   "excl": ""}))
    return pd.concat(parts, ignore_index=True)


def _s3_block(geo, tier, fold, seasons, span, shift):
    links = {}
    for lk in geo.links:
        if lk.identity:
            links[lk.id] = {"share": {"kind": "identity"}, "vol_share": 1.0}
        else:
            a = -0.4 + shift if lk.zone == "ocean" else 0.9 + shift
            links[lk.id] = {"share": {"kind": "logit_logvol", "coef": {"a": a, "b": 0.5}, "basin_median_mg": 5.0},
                            "constant": 0.6 if lk.zone == "ocean" else 0.8, "vol_share": 0.5}
    return {"tier": tier, "fold": fold, "train_seasons": seasons, "share_fit_span": span, "links": links,
            "basin_median_mg": {"westside": 5.0}, "cofire": {}, "union": {"east": {"rule": "noisy_or"}}}


S4_FEATURES = ("hinge_rain3_0.1", "hinge_rain3_0.5", "wet_season")


def _s4_record(tier, fold, seasons, span, shift):
    """One fold's S4 record, fit (as stages_s4_v3 says, fit.folds[].vol_share) at the stub S3's link shares φ."""
    from shared.zones import ZONES
    geo = _stub_data()[0]
    coef = {z: {"intercept": -2.6 + shift, "hinge_rain3_0.1": 1.1, "hinge_rain3_0.5": 0.4, "wet_season": 0.3} for z in ZONES}
    bk = {z: {**{f"{b}_small": round(0.5 * 0.6 ** i, 6) for i, b in enumerate(C.BUCKET_ORDER)},
              **{f"{b}_large": round(0.8 * 0.6 ** i, 6) for i, b in enumerate(C.BUCKET_ORDER)}} for z in ZONES}
    return {"tier": tier, "fold": fold, "fit_span": span, "fit_seasons": seasons, "background": coef, "buckets": bk,
            "zone_median_mg": {z: 5.0 for z in ZONES}, "vol_share": {lk.id: 1.0 if lk.identity else 0.5 for lk in geo.links}}


@functools.lru_cache(maxsize=1)
def _stub_root() -> Path:
    """The stub saved under a temporary stages_candidates root, its bake-off beside it; returns the root."""
    import stages_candidates as SC
    import stages_s2_sfpuc4 as S2C
    import stages_s3_links as S3L
    geo, src, _ = _stub_data()
    root = Path(_TMP.name) / "stages_candidates"
    bake = root / "_bakeoff"
    fits = {(t, fo): (_stub_fit(keep), _stub_fit(keep, RECIPE_FEATURES, 0.05), seasons, span)
            for t, fo, keep, seasons, span in _stub_folds()}
    windows = {("T1", "final"): (S2.POST_START, AS_OF), ("T1-holdout", "pre_holdout"): (S2.HOLDOUT_START, S2.TRAINED_THROUGH),
               ("T2", S2.season_label(STUB_SEASON)): (pd.Timestamp(STUB_SEASON, 7, 1), pd.Timestamp(STUB_SEASON + 1, 6, 30))}
    rows = []
    for (t, fo), ((w, h), (rw, _), _, _) in fits.items():
        lo, hi = windows[(t, fo)]
        if t == "T2":
            rows.append(_window_rows(w, h, t, fo, B.T2_ARM, lo, hi))
        else:                                                 # the winner's own finals and siblings (fidelity)
            rows.append(_window_rows(w, None, t, fo, {"T1": "t1:stub", "T1-holdout": "t1h:stub"}[t], lo, hi))
        rows.append(_window_rows(rw, None, t, fo, B.RECIPE_ARMS[t], lo, hi))
    # each outer fold's head of the picked recipe, as stages_s2_sfpuc4.volume records it (kind, n_events per fold)
    (_, h2), _, _, _ = fits[("T2", S2.season_label(STUB_SEASON))]
    per_basin = {k: {"picked": "loglinear", "recipes": {"loglinear": {"folds": {S2.season_label(STUB_SEASON): {
        "kind": h2[k]["kind"], "n_events": h2[k]["n_events"]}}}}} for k in geo.keys}
    per_basin["westside"]["vs_fallback"] = {"delta": None, "why": "no declared fallback for a Oceanside basin (Part B 7)"}
    per_basin["central"]["vs_fallback"] = {"delta": STUB_VS_FALLBACK, "head_is_fallback_in": []}
    results = {"winner": {"contender": "stub"}, "volume": {"per_basin": per_basin}, "south_floor": None,
               "nested": {"picked_by_fold": {S2.season_label(STUB_SEASON): "stub"}}}
    S2C.write_results(results, pd.concat(rows, ignore_index=True), bake)
    (w1, h1), _, _, _ = fits[("T1", "final")]
    (wh, hh), _, _, _ = fits[("T1-holdout", "pre_holdout")]
    SC.save_component(STUB, "s2", {"geography": geo.version, "component": "stub_s2", "models": w1, "volume": h1,
                                   "holdout_models": wh, "holdout_volume": hh,
                                   "spec": {"bakeoff": str(bake / "results.json"), "trained_through": "2025-10-31",
                                            "holdout_start": "2023-07-01", "t2": S2C.T2_NESTED}}, root=root)
    blocks = [_s3_block(geo, t, fo, seasons, span, 0.1 * i) for i, (t, fo, _, seasons, span) in enumerate(_stub_folds())]
    top = S3L.fold_spec({"geography": geo.version, "fit": {"folds": blocks}}, "T1", "final")
    SC.save_component(STUB, "s3_links", {**top, "component": "stub_links", "fit": {"folds": blocks},
                                         "sources": {"s2": {"name": STUB_S3_S2}}}, root=root)
    recs = [_s4_record(t, fo, seasons, span, 0.2 * i) for i, (t, fo, _, seasons, span) in enumerate(_stub_folds())]
    fin = recs[0]
    s4 = {"geography": geo.version, "pipeline": "stages_v1", "component": "stub_zone_v3", "kind": "zone_v3", "unit": "zone",
          "background": {"kind": "logistic", "features": list(S4_FEATURES), "coef": fin["background"]},
          "buckets": fin["buckets"], "zone_median_mg": fin["zone_median_mg"], "monotone": True,
          "sources": {"truth": "stub"}, "fit": {"folds": recs}}
    SC.save_component(STUB, "s4_quality", s4, root=root)
    SC.save_component(STUB, "s1", {"geography": geo.version, "component": E.served_weather_model()}, root=root)
    SC.save_component(STUB, "s5", {"geography": geo.version, "component": "link_zone_swap"}, root=root)
    return root


@functools.lru_cache(maxsize=1)
def _served_dir() -> Path:
    """The served slice written to a temporary stages directory (so vs_served reads rows built on this code)."""
    d = Path(_TMP.name) / "stages"
    b = _b()
    old = B.STAGES_DIR
    try:
        B.STAGES_DIR = d
        B.write(type(b)(**{**b.__dict__, "n_boot": B.B_PROTOCOL}))
    finally:
        B.STAGES_DIR = old
    return d


@functools.lru_cache(maxsize=1)
def _cand():
    return B.load_stage_candidate(STUB, root=_stub_root())


@functools.lru_cache(maxsize=1)
def _sb():
    d = _served_dir()
    old = B.STAGES_DIR
    try:
        B.STAGES_DIR = d
        return B.build(STUB, B.STAGE_ROOT, stage_set=_cand(), **STUB_SLICE)
    finally:
        B.STAGES_DIR = old


def test_a_stub_sfpuc4_candidate_builds_on_a_slice():
    """Read through stages_candidates and scored by its own folds: SFPUC4's four basins at S2 (no GEO_V1 adapter),
    T1 its finals, T1-holdout its siblings, T2 the bake-off's outer-fold rows (rain known only, no warm-up: S4 /
    OUT start a week into the season, counted), each fold's S3 / S4 from its own record; no T3 row; the counts
    partition; S5 has no basin_swap (live_v2 is GEO_V1's)."""
    b, cand = _sb(), _cand()
    assert b.bundle.geo.version == "sfpuc4_v1" and b.manifest["root"] == B.STAGE_ROOT and b.manifest["pipeline"] == "stages_v1"
    assert b.manifest["components"] == {"s1": E.served_weather_model(), "s2": "stub_s2", "s3": "stub_links", "s4": "stub_zone_v3",
                                        "s5": "link_zone_swap"}
    assert set(b.rows["s2"]["unit"]) == {"westside", "north_shore", "central", "south"}
    assert {(f.tier, f.fold) for f in cand.folds} == {("T1", "final"), ("T1-holdout", "pre_holdout"), ("T2", "2019-20"),
                                                      ("T0", "final")}
    assert [cand.is_rows_fold(f) for f in cand.folds] == [False, False, True, False] and cand.nested
    # T0 is T1's fold after the freeze: the finals, their heads and specs, not refit (protocol §2)
    f1, f0 = (next(f for f in cand.folds if f.tier == t) for t in ("T1", "T0"))
    assert f0.weights is f1.weights and f0.heads is f1.heads and f0.start == X.freeze_date() + pd.Timedelta(days=1)
    assert cand.specs[("T0", "final")] is cand.specs[("T1", "final")] and cand.s3b[("T0", "final")] is cand.s3b[("T1", "final")]
    # T2's S2 is the bake-off's rows to the digit; T1 its finals through stages_s2's predict path
    s2 = b.rows["s2"]
    t2 = s2[(s2["tier"] == "T2") & (s2["entry"] == "oracle")].set_index(["unit", "date"])["p"].sort_index()
    want = cand.t2.set_index(["basin", "date"])["p"].sort_index()
    assert np.array_equal(t2.to_numpy(), want.loc[t2.index].to_numpy()) and len(t2) == len(want)
    assert set(s2.loc[s2["tier"] == "T2", "entry"]) == {"oracle"}                     # no lead entry from rows
    # each fold composed with its own record: the T2 S4 background is T2's, not the finals'
    assert b.specs[("T2", "2019-20")]["s4"]["background"]["coef"]["east"]["intercept"] != \
        b.specs[("T1", "final")]["s4"]["background"]["coef"]["east"]["intercept"]
    assert b.specs[("T2", "2019-20")]["s3"]["fit"]["seasons"] == [s for s in S2.T2_SEASONS if s != STUB_SEASON]
    for st, r in b.rows.items():
        assert len(r) and set(r["tier"]) <= {"T1", "T1-holdout", "T2"}, st
        parts = [r[r["variant"] == v] for v in sorted(set(r["variant"]))] if st == "s5" else [r]
        for g in parts:
            X.counts(g)
    assert b.scores["integrity"]["t3_rows"] == 0 and b.scores["integrity"]["s3_identity_mismatches"] == 0
    # the build's S2 reproduces the bake-off's own rows of the finals and siblings (same inputs, Part B 16)
    fid = b.scores["integrity"]["s2_bakeoff_fidelity"]
    assert fid["n"] == int(s2[(s2["entry"] == "oracle") & s2["tier"].isin(["T1", "T1-holdout"])].shape[0]) > 1000
    assert fid["max_abs_diff"] <= B.FIDELITY_TOL, fid
    moved = dataclasses.replace(cand, own_rows=cand.own_rows.assign(p=cand.own_rows["p"] * 0.9))
    pred = s2.rename(columns={"unit": "basin"})[list(S2.COLUMNS)]
    assert _raises(lambda: B.bakeoff_fidelity(moved, pred), AssertionError)
    assert "basin_swap" not in set(b.rows["s5"]["variant"]) and "link_zone_swap" in set(b.rows["s5"]["variant"])
    # a rows fold has no warm-up: its S4 / OUT start with a full week in the season, the days before counted
    o = b.rows["out"]
    first = pd.DatetimeIndex(o.loc[(o["tier"] == "T2") & (o["entry"] == "oracle"), "date"]).min()
    assert first == pd.Timestamp(STUB_SEASON, 7, 8) and b.scores["dropped"]["no_history_days"]["oracle T2"] == 7
    # the heads in use, every fold (T2's as the bake-off records each outer fold's head): the declared fallback
    # wherever a basin is under the 20-event floor
    assert set(cand.heads_used) == set(cand.head_records) == {(f.tier, f.fold) for f in cand.folds if f.tier != "T0"}  # T0's: T1's
    for key, used in cand.heads_used.items():
        f = next(x for x in cand.folds if (x.tier, x.fold) == key)
        for k, kind in used.items():
            n = cand.head_records[key][k]["n_events"]
            assert n == (f.heads[k]["n_events"] if f.heads else n) and kind == cand.head_records[key][k]["kind"], (key, k)
            assert (kind == "pooled_bayside_loglinear") == (n < 20), (key, k)
    assert any(k == "pooled_bayside_loglinear" for u in cand.heads_used.values() for k in u.values())


def test_a_lead_entrys_s4_with_a_rain_background_reads_the_issue_days_rain():
    """zone_v3's background on a lead entry reads D−2…D as the issue day knew it (the entry frame's precip_avg and
    lags: gauges before I, the forecast from I): equal to compose_v2 on the real 8-day window with that rain."""
    b, cand = _sb(), _cand()
    geo = b.bundle.geo
    fold = next(f for f in cand.folds if f.tier == "T1")
    spec = b.specs[("T1", "final")]
    assert spec["s4"]["background"]["kind"] == "logistic"
    src = list(b.bundle.s2.sources)
    raw = {e: E.frames(e, src, model=E.served_weather_model()) for e in ("oracle", "L0", "L1")}
    ef = {e: S2._entry_frames(b.bundle.s2, e, raw[e]) for e in raw}
    s4 = b.rows["s4"]
    q1 = s4[(s4["entry"] == "L1") & (s4["tier"] == "T1")]
    targets = pd.DatetimeIndex(sorted(set(q1.nlargest(60, "p")["date"])))[:6]
    a1 = raw["L1"]["avg"].set_index("date")
    for D in targets:
        win = pd.date_range(D - pd.Timedelta(days=7), D)
        parts = [B._s2_values(b.bundle.s2, fold, ef["oracle"], win[:-2]), B._s2_values(b.bundle.s2, fold, ef["L0"], win[-2:-1]),
                 B._s2_values(b.bundle.s2, fold, ef["L1"], win[-1:])]
        p, v = pd.concat([x[0] for x in parts]), pd.concat([x[1] for x in parts])
        rain = pd.Series(0.0, index=pd.date_range(D - pd.Timedelta(days=9), D))     # only D−2…D enter b(D)
        rain.loc[D - pd.Timedelta(days=2):D] = a1.loc[D, ["rain_lag2d", "rain_lag1d", "precip_avg"]].to_numpy(dtype=float)
        comp = C.compose(geo, spec, C.BasinInputs(p, v, rain))
        for zk in ("ocean", "baker_china", "north", "east"):
            q = q1[(q1["unit"] == zk) & (q1["date"] == D)]["p"]
            assert len(q) == 1 and abs(q.iloc[0] - comp.s4.zone.loc[D, zk]) < TOL, (D, zk)
    # the issue day's rain is not rain known's: on some target it differs, and so does b(D)
    rk = raw["oracle"]["avg"].set_index("date")
    diff = (np.abs(a1.loc[targets, "precip_avg"] - rk.loc[targets, "precip_avg"]) > 1e-9).sum()
    assert diff >= 1, "the slice's lead-1 targets all forecast their rain exactly"


def test_the_comparisons_are_paired_on_identical_rows():
    """vs_served pairs the candidate's and the served set's scored rows on the unit-days both scored (their
    intersection), geography-invariant targets only across geographies; s2_vs_recipe pairs the candidate's S2 with
    the bake-off's served-recipe rows on (tier, fold, basin, day); S3b pairs each oracle row with its own
    constant-share row; recomputed here from the rows."""
    b = _sb()
    sc = b.scores
    vs = sc["paired"]["vs_served"]
    assert "skipped" not in vs, vs.get("skipped")
    assert vs["geography"] == {"candidate": "sfpuc4_v1", "served": "geo_v1", "invariant_only": True,
                               "s2": vs["geography"]["s2"]} and "s2" not in vs and {"s3", "s4", "out"} <= set(vs)
    srv = pd.read_csv(_served_dir() / B.served_name() / "rows.csv.gz", parse_dates=["date"], keep_default_na=False,
                      na_values=[""], low_memory=False)
    import verify as V
    blocks = T.blocks(end=E.data_end()).set_index("date")

    def ci_is(d: dict, y, pa, pb, dates, level=B.LEVEL, lo_hi=None):
        """The cell's CI is the storm-block bootstrap's (truth.blocks, seed 0, the build's B) on exactly these rows."""
        want = V.paired_delta(y, pa, pb, B._storm_blocks(dates, blocks)[0], n=STUB_SLICE["n_boot"], seed=B.SEED, level=level)
        got = lo_hi or (d["lo"], d["hi"])
        assert max(abs(got[0] - want["lo"]), abs(got[1] - want["hi"])) < 1e-12, (got, want["lo"], want["hi"])
        assert d["n_blocks"] == want["n_blocks"] < d["n"], (d["n_blocks"], d["n"])     # blocks, never rows
    for st, e, t in (("out", "rain", "T1"), ("out", "L1", "T1"), ("s4", "oracle", "T2"), ("s3", "rain", "T2")):
        mine = b.rows[st]
        a = mine[(mine["excl"] == "") & (mine["entry"] == e) & (mine["tier"] == t)].set_index(["unit", "date"])
        s = srv[(srv["stage"] == st) & (srv["excl"].fillna("") == "") & (srv["entry"] == e) & (srv["tier"] == t)].set_index(["unit", "date"])
        common = a.index.intersection(s.index)
        d = vs[st]["pooled"][e][t]
        assert 0 < d["n"] == len(common) < len(a) + len(s), (st, e, t)
        pa = B._as_written(a.loc[common, "p"].to_numpy(dtype=float))
        ya, pb = a.loc[common, "y"].to_numpy(dtype=float), s.loc[common, "p"].to_numpy(dtype=float)
        bs = lambda p_: float(((p_ - ya) ** 2).mean())  # noqa: E731
        assert abs(d["delta"] - (bs(pa) - bs(pb))) < 1e-12, (st, e, t)
        ci_is(d, ya, pa, pb, common.get_level_values("date"))
    assert "mcb" in vs["out"]["pooled"]["rain"]["T1"] and "mcb" in vs["out"]["pooled"]["L1"]["T1"]
    # S2 within the geography: the candidate vs the served recipe refit on its labels, every scored row paired,
    # both arms at the bake-off file's precision
    rec = sc["paired"]["s2_vs_recipe"]
    s2 = b.rows["s2"]
    cand = _cand()
    ref = cand.reference.set_index(["tier", "fold", "basin", "date"])["p"]
    for t in ("T1", "T1-holdout", "T2"):
        g = s2[(s2["excl"] == "") & (s2["entry"] == "oracle") & (s2["tier"] == t)]
        pb = ref.reindex(pd.MultiIndex.from_arrays([g["tier"], g["fold"], g["unit"], pd.DatetimeIndex(g["date"])])).to_numpy()
        assert np.isfinite(pb).all() and rec["pooled"][t]["n"] == len(g) == rec["pooled"][t]["n_candidate_rows"], t
        pa = B._as_written(g["p"].to_numpy(dtype=float))
        want = float(((pa - g["y"]) ** 2).mean() - ((pb - g["y"]) ** 2).mean())
        assert abs(rec["pooled"][t]["delta"] - want) < 1e-12 and rec["pooled"][t]["delta"] != 0, t
        ci_is(rec["pooled"][t], g["y"].to_numpy(dtype=float), pa, pb, g["date"])
    # the recipe's labels are the build's (the stub's rows carry truth.basin_onsets'); one flipped label on a paired
    # scored basin-day raises, and so does a recipe arm the candidate's own rows cannot vouch for
    assert cand.reference["y"].notna().sum() > 1000
    g = s2[(s2["excl"] == "") & (s2["entry"] == "oracle") & (s2["tier"] == "T1")].iloc[0]
    hit = ((cand.reference["tier"] == "T1") & (cand.reference["basin"] == g["unit"]) & (cand.reference["date"] == g["date"])).to_numpy()
    assert hit.sum() == 1
    flipped = cand.reference.assign(y=np.where(hit, 1.0 - g["y"], cand.reference["y"]))
    assert _raises(lambda: B.s2_vs_recipe(s2, flipped, blocks, 2), AssertionError)
    pred = s2.rename(columns={"unit": "basin"})[list(S2.COLUMNS)]
    assert _raises(lambda: B.bakeoff_fidelity(dataclasses.replace(cand, own_rows=None), pred), AssertionError)
    no_t1h = dataclasses.replace(cand, own_rows=cand.own_rows[cand.own_rows["tier"] != "T1-holdout"])
    assert _raises(lambda: B.bakeoff_fidelity(no_t1h, pred), AssertionError)
    assert B.bakeoff_fidelity(dataclasses.replace(cand, own_rows=None, reference=None), pred)["n"] == 0
    # S3b: the oracle's Westside rows against the constant share of the same fold, its CIs at 90% and Holm's 95%
    s3b = sc["paired"]["s3b_vs_constant"]
    s3 = b.rows["s3"]
    g = s3[(s3["excl"] == "") & (s3["entry"] == "oracle") & (s3["tier"] == "T2")]
    assert set(g["unit"]) == {"ocean", "baker_china"} == set(s3b["zones"]) and s3b["pooled"]["T2"]["n"] == len(g)
    # the benchmark recomposed here: the fold's constant-share spec on the true occurrence with v̂ from rain (the
    # bake-off's T2 rows), Part B 2's oracle input
    geo = b.bundle.geo
    key = ("T2", S2.season_label(STUB_SEASON))
    v = cand.t2[cand.t2["fold"] == key[1]].pivot(index="date", columns="basin", values="v_hat")[list(geo.keys)]
    occ = B.truths(geo, E.data_end()).basin_y.reindex(v.index)[list(geo.keys)].fillna(0.0)
    zp = C.s3(geo, cand.s3b[key], occ, v).zone_p
    pb = np.array([zp.loc[d, u] for u, d in zip(g["unit"], pd.DatetimeIndex(g["date"]))], dtype=float)
    yb, pa = g["y"].to_numpy(dtype=float), g["p"].to_numpy(dtype=float)
    assert abs(s3b["pooled"]["T2"]["delta"] - (float(((pa - yb) ** 2).mean()) - float(((pb - yb) ** 2).mean()))) < 1e-12
    ci_is(s3b["pooled"]["T2"], yb, pa, pb, g["date"])
    ci_is(s3b["pooled"]["T2"], yb, pa, pb, g["date"], level=HOLM_95, lo_hi=s3b["pooled"]["T2"]["ci95"])
    # the stub's few Westside overflow days tie the 90% and 95% bounds, so S3b's 95% bound is pinned on synthetic
    # rows too, where the two levels differ
    rng = np.random.default_rng(0)
    syn = pd.DataFrame([(d, z) for d in pd.date_range("2019-10-01", "2020-03-31") for z in s3b["zones"]], columns=["date", "unit"])
    syn = syn.assign(stage="s3", entry="oracle", tier="T2", excl="", y=rng.binomial(1, 0.3, len(syn)).astype(float),
                     p=rng.uniform(0, 1, len(syn)))
    sb = syn[["tier", "unit", "date"]].assign(p=rng.uniform(0, 1, len(syn)))
    got = B.s3b_vs_constant(syn, sb, geo, blocks, 200)["pooled"]["T2"]
    want = V.paired_delta(syn["y"], syn["p"], sb["p"], B._storm_blocks(syn["date"], blocks)[0], n=200, seed=B.SEED, level=HOLM_95)
    assert np.allclose(got["ci95"], [want["lo"], want["hi"]], rtol=0, atol=1e-12) and got["ci95"][1] > got["hi"], (got, want)
    # S3a in the primaries is East's chained rain-known T2 rows, on the same intersection vs_served holds, its 95%
    # bound Holm's
    pr = {r["id"]: r for r in sc["primaries"]["rows"]}
    assert pr["S3a"]["n"] == vs["s3"]["east"]["rain"]["T2"]["n"] and pr["S3a"]["delta"] == vs["s3"]["east"]["rain"]["T2"]["delta"]
    mine = b.rows["s3"]
    a = mine[(mine["excl"] == "") & (mine["entry"] == "rain") & (mine["tier"] == "T2") & (mine["unit"] == "east")].set_index(["unit", "date"])
    s = srv[(srv["stage"] == "s3") & (srv["excl"].fillna("") == "") & (srv["entry"] == "rain") & (srv["tier"] == "T2")
            & (srv["unit"] == "east")].set_index(["unit", "date"])
    common = a.index.intersection(s.index)
    ci_is(pr["S3a"], a.loc[common, "y"].to_numpy(dtype=float), B._as_written(a.loc[common, "p"].to_numpy(dtype=float)),
          s.loc[common, "p"].to_numpy(dtype=float), common.get_level_values("date"), level=HOLM_95, lo_hi=pr["S3a"]["ci95"])


def test_the_primaries_state_every_protocol_row():
    """One row per protocol §8 row in its words (read from the frozen file), with Δ, its 90% storm-block CI, MDE,
    margin and a status from the four; S3a and S3b one Holm family; S2 within the geography (nested T2, T1
    non-inferiority); OUT says T0 holds no day yet; S5 read from the served set's build."""
    sc = _sb().scores
    prim = sc["primaries"]
    rules = B.protocol_rules()
    assert [r["id"] for r in prim["rows"]] == list(rules["rows"]) == ["S1", "S2", "S2-south-floor", "S2-volume", "S3a", "S3b",
                                                                      "S4", "S5", "OUT"]
    text = X.PROTOCOL.read_text()
    for r in prim["rows"]:
        assert r["status"] in prim["statuses"] and r["reason"] is not None, r["id"]
        assert f"| {r['label']} | {r['comparison']} | {r['entry']} | {r['window']} | {r['rule']} |" in text, r["id"]
    pr = {r["id"]: r for r in prim["rows"]}
    assert pr["S1"]["status"] == "not applicable" and not pr["S1"]["changed"]
    assert prim["changed"] == {"s1": False, "s2": True, "s3": True, "s4": True, "s5": True}
    s2 = pr["S2"]
    assert [p["window"] for p in s2["parts"]] == ["T2", "T1"] and s2["parts"][0]["nested"] is True
    assert s2["parts"][0]["delta"] == sc["paired"]["s2_vs_recipe"]["pooled"]["T2"]["delta"]
    assert s2["parts"][1]["margin"] == 0.05 * sc["paired"]["s2_vs_recipe"]["pooled"]["T1"]["b"]
    assert s2["parts"][0]["margin"] == 0.0 and s2["parts"][0]["part"] == "superiority"     # protocol §8: T2 superiority
    for p in s2["parts"]:
        hi = p["ci"][1]
        assert p["status"] == ("pass" if hi < p["margin"] else "fail"), p
    assert {"delta", "ci", "mde", "p_neg", "n", "n_blocks"} <= set(s2), s2
    sf = pr["S2-south-floor"]
    assert sf["ci"] == sc["s2"]["south"]["oracle"]["T2"]["ci"]["bss"] and sf["status"] == ("pass" if sf["ci"][0] > 0 else "fail")
    vol = pr["S2-volume"]
    assert vol["status"] == "pass" and vol["fallback_used"] and vol["decided_by"] == vol["parts"][0]["part"]
    # the floor rule was checked on every fold scored, the T2 outer fold's heads included (as the bake-off records them)
    assert vol["parts"][0]["folds_checked"] == ["T1 final", "T1-holdout pre_holdout", "T2 2019-20"], vol["parts"][0]
    assert s2["parts"][0]["picked_by_fold"] == {"2019-20": "stub"}
    # the head − fallback Δ is the bake-off's, per basin, reported beside the rule (never deciding it)
    per = vol["parts"][1]["per_basin"]
    assert vol["parts"][1]["status"] == "not applicable" and set(per) == {"westside", "north_shore", "central", "south"}
    assert per["central"]["delta"] == STUB_VS_FALLBACK["delta"] and per["central"]["ci"] == [STUB_VS_FALLBACK["lo"], STUB_VS_FALLBACK["hi"]]
    assert (per["central"]["head"], per["central"]["fallback"], per["central"]["metric"]) == (0.81, 0.84, "log_mae")
    assert "Oceanside" in per["westside"]["why"] and per["south"]["why"]
    for k in ("S3a", "S3b"):
        assert pr[k]["holm"]["family"] == ["S3a", "S3b"] and pr[k]["status"] in ("pass", "fail"), pr[k]
        # the shares say they were fit on a development stand-in S2: stated on S3's rows, never silently scored
        assert pr[k]["s3_fit_with_s2"] == STUB_S3_S2 and STUB_S3_S2 in pr[k]["caveat"] and "Part B 2" in pr[k]["caveat"], pr[k]
    assert sc["integrity"]["s3_fit_with_s2"] == {"name": STUB_S3_S2, "stand_in": True}
    # S5 is read from the served set's chain (basin_swap exists on GEO_V1 only): said on the row, carried to §9.1
    assert "served set's own chain" in pr["S5"]["caveat"] and "geo_v1" in pr["S5"]["caveat"], pr["S5"]
    assert not any("caveat" in r for r in prim["rows"] if r["id"] not in ("S3a", "S3b", "S4", "S5"))
    assert B.s3_fit_s2({"sources": {"s2": {"name": "sfpuc-icon-t8s-ssplit-rain-lzflags"}}}) == {"name": "sfpuc-icon-t8s-ssplit-rain-lzflags", "stand_in": False}
    assert B.s3_fit_s2({"links": {}})["name"] is None
    # an S3 spec fit on the candidate's own S2 carries no caveat; a stand-in, an unstated S2 or another S2 does
    own = B.s3_fit_s2({"sources": {"s2": {"name": "sfpuc4_x_v1"}}})
    assert B.s3_fit_caveat(own, "x_s2", "sfpuc4_x_v1") is None
    assert B.s3_fit_caveat(B.s3_fit_s2({"sources": {"s2": {"name": "x_s2"}}}), "x_s2", "sfpuc4_x_v1") is None
    assert "stand-in" in B.s3_fit_caveat(B.s3_fit_s2({"sources": {"s2": {"name": "dev:a"}}}), "x_s2", "sfpuc4_x_v1")
    assert "does not say" in B.s3_fit_caveat(B.s3_fit_s2({"links": {}}), "x_s2", "sfpuc4_x_v1")
    assert "sfpuc4_y_v1" in B.s3_fit_caveat(B.s3_fit_s2({"sources": {"s2": {"name": "sfpuc4_y_v1"}}}), "x_s2", "sfpuc4_x_v1")
    # S4 fit = S4 use (Part B 6): the stub's S4 says it was fit at its S3's link shares in every fold, so no caveat;
    # S4 fit at φ = 1 beside fitted shares (a φ = 1 study next to links_v1), or records that do not say, carry one
    assert sc["integrity"]["s4_fit_sizes"] == {"match": True, "folds_off": [], "folds_unstated": []}
    cand = _cand()
    fitted = {k: v for k, v in cand.specs.items() if k[0] != "T0"}           # T0 composes with T1's specs: no fit of its own
    assert all(s["match"] for s in cand.s4_sizes.values()) and set(cand.s4_sizes) == set(fitted)
    assert B.s4_size_caveat(cand.s4_sizes) is None and "Part B 6" not in pr["S4"].get("caveat", "") and "caveat" not in pr["OUT"]
    # S4's fix clause is tested only where the served table shows the oracle-worse-than-chained defect (its chained −
    # oracle CI wholly below 0), both S4s' Δ on the same rows: the T2 unit-days the candidate's oracle and chained rows
    # and the served set's oracle and chained rows all scored, recomputed here
    d4 = pr["S4"]["defect"]
    srv_rows = pd.read_csv(_served_dir() / B.served_name() / "rows.csv.gz", parse_dates=["date"], keep_default_na=False,
                           na_values=[""], low_memory=False)

    def t2(f, e):
        return f[(f["stage"] == "s4") & (f["excl"].fillna("") == "") & (f["entry"] == e) & (f["tier"] == "T2")].set_index(["unit", "date"])
    arms = [t2(_sb().rows["s4"], "oracle"), t2(_sb().rows["s4"], "rain"), t2(srv_rows, "oracle"), t2(srv_rows, "rain")]
    common = functools.reduce(lambda a, b: a.intersection(b), [a.index for a in arms])
    assert d4["n"] == len(common) > 0 and len(common) < min(len(a) for a in arms[1:]) + 1, (d4["n"], [len(a) for a in arms])
    y4 = arms[0].loc[common, "y"].to_numpy(dtype=float)
    bs4 = [float(((p_ - y4) ** 2).mean()) for p_ in (B._as_written(arms[1].loc[common, "p"].to_numpy(dtype=float)),
                                                       B._as_written(arms[0].loc[common, "p"].to_numpy(dtype=float)),
                                                       arms[3].loc[common, "p"].to_numpy(dtype=float),
                                                       arms[2].loc[common, "p"].to_numpy(dtype=float))]
    assert abs(d4["chained_minus_oracle"]["delta"] - (bs4[0] - bs4[1])) < 1e-12, d4
    assert abs(d4["served_chained_minus_oracle"]["delta"] - (bs4[2] - bs4[3])) < 1e-12, d4
    assert d4["chained_minus_oracle"]["n"] == d4["served_chained_minus_oracle"]["n"] == d4["n"]
    assert d4["served_shows_defect"] == d4["fix_tested"] == bool(d4["served_chained_minus_oracle"]["ci"][1] < 0), d4
    by_ni = pr["S4"]["status"] == "pass" and pr["S4"]["superior"] != "pass"
    assert ("caveat" in pr["S4"]) == (by_ni and d4.get("fix_tested") is not True), pr["S4"]
    if "caveat" in pr["S4"]:
        assert ("not tested" if d4.get("fix_tested") is False else "unknown") in pr["S4"]["caveat"] in pr["S4"]["reason"]
    assert B.s4_defect({}, {"rows": srv_rows}, None, 2) is None and B.s4_defect(_sb().rows, {"skipped": "x"}, None, 2) is None
    import stages_candidates as SC
    saved = SC.load_set(STUB, root=_stub_root())
    s4 = json.loads(json.dumps(saved.s4_quality))
    for r in s4["fit"]["folds"]:
        r["vol_share"] = {lid: 1.0 for lid in r["vol_share"]}
    off = B.s4_fit_sizes(s4, fitted)
    assert [k for k, s in sorted(off.items()) if s["match"] is False] == sorted(fitted)
    cv = B.s4_size_caveat(off)
    assert "Part B 6" in cv and "westside>ocean 1.0 vs 0.5" in cv and "3 of 3 folds" in cv, cv
    for r in s4["fit"]["folds"]:
        r.pop("vol_share")
    assert "does not say" in B.s4_size_caveat(B.s4_fit_sizes(s4, fitted))
    # S3b tests a size term: the stub's logit_logvol shares have one; constant shares would leave S3b nothing to test
    cand = _cand()
    assert B.size_shares(cand)
    flat = {k: {**sp, "s3": {**sp["s3"], "links": {lid: ({**ln, "share": {"kind": "constant", "p": 0.6}} if lid.startswith("westside>")
                                                         else ln) for lid, ln in sp["s3"]["links"].items()}}}
            for k, sp in cand.specs.items()}
    assert not B.size_shares(dataclasses.replace(cand, specs=flat))
    # a row whose component is the served set's decides nothing (§9.1 reads changed components): its numbers stay
    rows = [{"id": "S2", "stage": "s2", "changed": False, "status": "fail", "reason": "Δ 0", "delta": 0.0},
            {"id": "S4", "stage": "s4", "changed": True, "status": "pass", "reason": "superior"}]
    got = B.settle_unchanged(rows, {"s2": "logit_v1", "s4": "zone_v3"})
    assert [r["status"] for r in got] == ["not applicable", "pass"] and got[0]["computed_status"] == "fail"
    assert got[0]["delta"] == 0.0 and "logit_v1" in got[0]["reason"] and rows[0]["status"] == "fail" and got[1] is rows[1]
    assert pr["S3a"]["margin"] == 0.05 * pr["S3a"]["bs_b"]
    assert pr["S4"]["status"] in ("pass", "fail") and "fixed" in pr["S4"]["defect"] and pr["S4"]["candlestick"]
    assert _sb().bundle.t1_specs["s4"]["kind"] == B.S4_V3_KIND == "zone_v3"          # the stub's S4 is the row's v3
    assert pr["S3b"]["margin"] == 0.0 and pr["S3a"]["margin"] > 0                     # S3b superiority, S3a +5%
    # the family's statuses are Holm's on the rows' own bounds and the protocol's margins (S3b superiority: 0; S3a
    # +5% of the GEO_V1 BS), so the margins Holm reads are the ones the rows state
    fam = {"S3a": {"hi90": pr["S3a"]["ci"][1], "hi95": pr["S3a"]["ci95"][1], "margin": 0.05 * pr["S3a"]["bs_b"]},
           "S3b": {"hi90": pr["S3b"]["ci"][1], "hi95": pr["S3b"]["ci95"][1], "margin": 0.0}}
    assert {k: pr[k]["status"] for k in fam} == B.holm(fam), ({k: pr[k]["status"] for k in fam}, fam)
    seen, real_holm = {}, B.holm
    _primaries_again(holm=lambda parts: seen.update(parts) or real_holm(parts))   # what Holm is handed, whatever the numbers
    assert {k: (v["hi90"], v["hi95"]) for k, v in seen.items()} == {k: (v["hi90"], v["hi95"]) for k, v in fam.items()}, seen
    assert seen["S3b"]["margin"] == 0.0 and abs(seen["S3a"]["margin"] - fam["S3a"]["margin"]) < 1e-15, seen
    s5 = pr["S5"]
    assert "the served set's build" in s5["reason"] and [p["part"].split(" (")[0] for p in s5["parts"]] == ["perfect feed", "degraded feed"]
    out = pr["OUT"]
    assert "T0" in out["reason"] and "holds no scored day" in out["reason"] and out["t0"] == "empty"
    # T0 starts the day after the freeze: empty at this data end, so T1 stands alone; once the data run past it, T0 is
    # shadow-run like T1 and the parts read T1 post ∪ T0 as one window (tests/test_t0.py builds one)
    t0_first = X.freeze_date() + pd.Timedelta(days=1)
    assert B.t0_words(AS_OF)[0] == "empty" and B.t0_words(t0_first - pd.Timedelta(days=1))[0] == "empty"
    assert B.t0_words(t0_first)[0] == "scored" and "T1 post ∪ T0" in B.t0_words(t0_first)[1]
    assert [p["part"] for p in out["parts"]] == ["non-inferiority at +5%, rain known", "pooled MCB, rain known",
                                                 "non-inferiority at +5%, lead 1", "pooled MCB, lead 1"]
    assert {p["window"] for p in out["parts"]} == {"T1"} and not any("t1_status" in p for p in out["parts"])
    assert out["parts"][0]["delta"] == sc["paired"]["vs_served"]["out"]["pooled"]["rain"]["T1"]["delta"]
    # Holm on CIs: the smaller p clears at 95%, then the other at 90%; one member: its 90% bound
    assert B.holm({"a": {"hi90": -0.1, "hi95": -0.05, "margin": 0.0}, "b": {"hi90": -0.01, "hi95": 0.01, "margin": 0.0}}) == {"a": "pass", "b": "pass"}
    assert B.holm({"a": {"hi90": -0.1, "hi95": 0.02, "margin": 0.0}, "b": {"hi90": -0.01, "hi95": 0.01, "margin": 0.0}}) == {"a": "fail", "b": "fail"}
    assert B.holm({"a": {"hi90": -0.1, "hi95": -0.05, "margin": 0.0}, "b": {"hi90": 0.01, "hi95": 0.02, "margin": 0.0}}) == {"a": "pass", "b": "fail"}
    assert B.holm({"a": {"hi90": 0.04, "hi95": 0.09, "margin": 0.05}}) == {"a": "pass"}
    assert B.holm({"a": {"hi90": None, "hi95": None, "margin": 0.0}, "b": {"hi90": -1, "hi95": -1, "margin": 0.0}}) == {"a": "not yet computable", "b": "not yet computable"}


def test_the_promotion_block_states_each_criterion_and_never_says_promote():
    """Protocol §9's five criteria in its words, each met / not met / not yet computable with its reason; 4 and 5
    are the owner's; nothing in the block says to promote or recommends anything."""
    sc = _sb().scores
    pm = sc["promotion"]
    crit = B.protocol_rules()["criteria"]
    assert [c["criterion"] for c in pm["criteria"]] == crit and [c["n"] for c in pm["criteria"]] == [1, 2, 3, 4, 5]
    for c in pm["criteria"]:
        assert c["status"] in ("met", "not met", "not yet computable") and c["reason"], c
    assert [c["status"] for c in pm["criteria"][3:]] == ["not yet computable"] * 2
    words = json.dumps(pm, ensure_ascii=False).lower()
    assert "promote" not in words and "recommend" not in words, words
    rows = {r["id"]: r for r in sc["primaries"]["rows"]}
    for n, key in ((2, "non-inferiority"), (3, "pooled MCB")):                # OUT's parts decide criteria 2 and 3
        sts = [p["status"] for p in rows["OUT"]["parts"] if p["part"].startswith(key)]
        want = "not met" if "fail" in sts else ("not yet computable" if "not yet computable" in sts else "met")
        assert len(sts) == 2 and pm["criteria"][n - 1]["status"] == want and "T0" in pm["criteria"][n - 1]["reason"], n
        why = pm["criteria"][n - 1]["reason"]
        assert why.endswith(".") and ".." not in why and not re.search(r"[^.] Caveat:", why), why
    assert set(pm["criteria"][0]["changed"]) == {"s2", "s3", "s4", "s5"}
    first = [r["status"] for r in sc["primaries"]["rows"] if r["stage"] in ("s2", "s3", "s4", "s5") and r["status"] != "not applicable"]
    assert pm["criteria"][0]["status"] == ("not met" if "fail" in first else "not yet computable" if "not yet computable" in first else "met")
    assert "Caveats: S3a: " in pm["criteria"][0]["reason"] and STUB_S3_S2 in pm["criteria"][0]["reason"]
    assert "S5: computed on the served set's own chain" in pm["criteria"][0]["reason"]
    # with no changed component criterion 1 holds vacuously, and still nothing is decided: 4 and 5 stay open
    idle = B.promotion({"changed": {c: False for c in B.COMPONENTS}, "rows": [dict(r, status="pass") for r in sc["primaries"]["rows"]]})
    assert [c["status"] for c in idle["criteria"]][0] == "met" and [c["status"] for c in idle["criteria"]][3:] == ["not yet computable"] * 2


def _cell(d, lo, hi, b=0.1):
    """A paired Δ cell as stages_build writes one: Δ, its 90% CI, arm b's BS, the verdict words."""
    import verify as V
    return {"delta": d, "lo": lo, "hi": hi, "b": b, "verdict": V.verdict(lo, hi)}


def test_s4_status_reads_its_cells_and_its_caveat_is_s4s():
    """Protocol §8's S4 rule on constructed cells: superiority passes; non-inferiority passes only with the fix (the
    candidate's chained − oracle Δ ≥ 0 and its CI not wholly below 0); the fix is tested only where the served
    table's Δ has its CI wholly below 0 (never its point estimate), else a pass carries a caveat; no cell, or no
    defect check, is not yet computable."""
    sup, ni, neither = _cell(-0.01, -0.02, -0.001), _cell(0.0, -0.002, 0.003), _cell(0.004, -0.001, 0.009)
    fixed, worse_pt, worse_ci = _cell(0.001, -0.002, 0.004), _cell(-0.001, -0.004, 0.002), _cell(-0.006, -0.01, -0.003)
    shows, none_, pt_only = _cell(-0.006, -0.01, -0.003), _cell(0.006, 0.003, 0.01), _cell(-0.002, -0.006, 0.001)
    r = B.s4_status(sup, worse_ci, none_)
    assert (r["status"], r["caveat"], r["superior"]) == ("pass", None, "pass"), r          # superiority needs no fix
    r = B.s4_status(ni, fixed, shows)
    assert (r["status"], r["caveat"], r["fixed"], r["shown"]) == ("pass", None, True, True), r
    assert abs(r["margin"] - 0.05 * 0.1) < 1e-15 and r["noninferior"] == "pass" and r["superior"] == "fail"
    for sfx in (none_, pt_only):                    # no defect shown: a negative point estimate alone shows nothing
        r = B.s4_status(ni, fixed, sfx)
        assert r["status"] == "pass" and r["shown"] is False and "not tested" in r["caveat"] and r["caveat"] in r["why"], r
    r = B.s4_status(ni, fixed, None)
    assert r["status"] == "pass" and r["shown"] is None and "unknown" in r["caveat"], r
    for fx in (worse_pt, worse_ci):
        r = B.s4_status(ni, fx, shows)
        assert r["status"] == "fail" and r["fixed"] is False and r["caveat"] is None, r
    assert B.s4_status(ni, None, shows)["status"] == "not yet computable"
    assert B.s4_status(neither, fixed, shows)["status"] == "fail" and B.s4_status(None, fixed, shows)["status"] == "not yet computable"
    no_ci = {"delta": 0.0, "lo": None, "hi": None, "b": 0.1, "verdict": "not scored"}
    assert B.s4_status(no_ci, fixed, shows)["status"] == "not yet computable"


def _primaries_again(bundle=None, **patch):
    """The stub's primaries recomputed with stages_build names patched (candlestick off: it needs the context)."""
    b = _sb()
    patch = {"candlestick_sensitivity": lambda *a, **k: None, **patch}
    held, old_dir = {k: getattr(B, k) for k in patch}, B.STAGES_DIR
    try:
        B.STAGES_DIR = _served_dir()
        for k, v in patch.items():
            setattr(B, k, v)
        return B.primaries(bundle or b.bundle, b.scores, b.rows, B.served_artifacts(), None,
                           T.blocks(end=E.data_end()).set_index("date"), STUB_SLICE["n_boot"], E.data_end())
    finally:
        for k, v in held.items():
            setattr(B, k, v)
        B.STAGES_DIR = old_dir


def test_s4s_fix_caveat_never_reaches_out_and_s5_decides_only_its_component():
    """OUT reads S4's table, so it carries S4's size caveat (Part B 6), never the fix-not-tested caveat, which is
    the S4 row's reading alone; §9's criteria 2 and 3 read OUT's. The §8 S5 row tests link_zone_swap: a candidate
    whose S5 is another variant has no S5 primary, so criterion 1 says no §8 row scores that change."""
    real = B.s4_status

    def untested(*a):
        return {**real(*a), "status": "pass", "caveat": "FIX-NOT-TESTED", "why": "forced"}
    pr = {r["id"]: r for r in _primaries_again(s4_status=untested)["rows"]}
    assert pr["S4"]["caveat"] == "FIX-NOT-TESTED" and "caveat" not in pr["OUT"], (pr["S4"].get("caveat"), pr["OUT"].get("caveat"))
    prim = _primaries_again(s4_status=untested, s4_size_caveat=lambda *a: "SIZE")
    pr = {r["id"]: r for r in prim["rows"]}
    assert pr["S4"]["caveat"] == "SIZE; FIX-NOT-TESTED" and pr["OUT"]["caveat"] == "SIZE", (pr["S4"]["caveat"], pr["OUT"]["caveat"])
    pm = B.promotion(prim)["criteria"]
    assert all("FIX-NOT-TESTED" not in c["reason"] for c in pm[1:3]) and "FIX-NOT-TESTED" in pm[0]["reason"]
    assert all(c["reason"].endswith("Caveat: SIZE.") for c in pm[1:3]), [c["reason"] for c in pm[1:3]]
    # S5: the stub's link_zone_swap is the row's arm; another variant leaves the row deciding nothing
    assert pr["S5"]["status"] in ("pass", "fail")
    b = _sb()
    man = json.loads(json.dumps(b.bundle.stage.manifest))
    man["components"]["s5"] = "downgrade"
    other = dataclasses.replace(b.bundle, stage=dataclasses.replace(b.bundle.stage, manifest=man))
    prim = _primaries_again(bundle=other)
    s5 = next(r for r in prim["rows"] if r["id"] == "S5")
    assert s5["status"] == "not applicable" and "downgrade" in s5["reason"] and "link_zone_swap" in s5["reason"], s5
    c1 = B.promotion(prim)["criteria"][0]
    assert c1["status"] == "not met" and "s5: no §8 primary scores this change" in c1["reason"], c1


def test_a_nested_candidates_s2_t2_says_so():
    """A stage candidate whose S2 choices were made nested (the A5 procedure's outer-fold rows) is labelled
    'development (nested)' wherever its S2 T2 is shown: the windows block, the figure's S2 pill, the caption and
    the manifest; S3 / S4 / OUT stay 'development'; an existing set's S2 'development (selection-contaminated)'."""
    g4 = B.G.SFPUC4_V1
    assert B.t2_label("s2", g4, nested=True) == B.T2_NESTED_LABEL == "development (nested)"
    assert B.t2_label("s2", g4) == B.t2_label("s4", g4, nested=True) == B.t2_label("out", B.G.GEO_V1) == "development"
    assert B.t2_label("s2", g4, nested=False) == B.t2_label("s2", B.G.GEO_V1, nested=True) == S2.T2_LABEL
    sc = _sb().scores
    assert sc["windows"]["T2"]["label"] == B.T2_NESTED_LABEL and "(nested)" in sc["windows"]["T2"]["weights"]
    fig = sc["figure"]
    assert fig["m.s2"]["oracle"]["window"].endswith(f": {B.T2_NESTED_LABEL}"), fig["m.s2"]["oracle"]["window"]
    assert all(fig[n]["oracle"]["window"].endswith(": development") for n in ("m.s4", "m.out")), fig
    assert "S2's nested" in fig["caption"], fig["caption"]


def test_a_post_seen_candidates_post_training_scores_are_tagged():
    """Protocol §2: a set designed after its post-training scores were seen (stages_candidates.tag 'post_seen') has
    the tag on those scores: the post-training words of every pill and the caption, and a caveat on each primary
    with a part decided on post-training days (joined to any other), never on one decided elsewhere; an untagged set
    says nothing. The committed sfpuc-icon-t8s-osplits-lt2zone-lzflags build carries it on windows['T1'], OUT and S2."""
    cand = _cand()
    assert cand.post_seen is None
    tagged = dataclasses.replace(cand, manifest={**cand.manifest, "tags": {"post_seen": "why"}})
    assert tagged.post_seen == "why"
    cell = {"span": ["2025-11-01", AS_OF], "n_seasons": 1}
    assert B.window_words("T1", cell, "out", cand.geo, post_seen=True).endswith(f"; {B.POST_SEEN_WORDS}")
    assert B.POST_SEEN_WORDS not in B.window_words("T1", cell, "out", cand.geo) + B.window_words("T2", cell, "out", cand.geo, post_seen=True)
    sc = _sb().scores
    fig = B.figure(sc, B.FIGURE_WINDOWS, None, cand.geo, fallback=True, nested=True, post_seen=True)
    assert fig["m.out"]["oracle"]["post"]["window"].endswith(B.POST_SEEN_WORDS) and B.POST_SEEN_WORDS in fig["caption"]
    assert B.POST_SEEN_WORDS not in json.dumps(sc["figure"]) and "tag" not in sc["windows"]["T1"]
    rows = [{"id": "OUT", "parts": [{"window": "T1"}]}, {"id": "S3a"}, {"id": "S2", "caveat": "x", "parts": [{"window": "T2"}, {"window": "T1"}]},
            {"id": "S5", "parts": [{"window": "S5"}]}]
    got = [B.post_seen_caveat(r, "why") for r in rows]
    assert got[0]["caveat"] == "post_seen (protocol §2): why" and got[2]["caveat"] == "x; post_seen (protocol §2): why"
    assert got[1] == rows[1] and got[3] == rows[3]
    d = B.STAGES_DIR / "sfpuc-icon-t8s-osplits-lt2zone-lzflags"
    if (d / "scores.json").exists():
        v2 = json.loads((d / "scores.json").read_text())
        assert v2["windows"]["T1"]["tag"] == "post_seen" and B.POST_SEEN_WORDS in v2["figure"]["caption"]
        pr = {r["id"]: r for r in v2["primaries"]["rows"]}
        assert all("post_seen (protocol §2)" in pr[k]["caveat"] for k in ("OUT", "S2")), {k: pr[k].get("caveat") for k in ("OUT", "S2")}


def test_a_stage_candidate_is_refused_when_its_parts_could_have_seen_its_days():
    """from_saved raises on a spec fit on its own fold's days, a head under the floor that is not the declared
    fallback, a negative weight (stages_candidates' own check), a fold record that is missing, and rows that do
    not cover every basin; a stage candidate writes rows under data/models/stages/<name>/."""
    import stages_candidates as SC
    saved = SC.load_set(STUB, root=_stub_root())
    cand = B.from_saved(saved)
    f2 = next(f for f in cand.folds if f.tier == "T2")
    sp = dict(cand.specs[("T2", "2019-20")]["s4"], fit={"seasons": [2018, 2019]})
    assert _raises(lambda: B.check_fit_span(sp, "s4", f2, STUB), ValueError)
    f1 = next(f for f in cand.folds if f.tier == "T1")
    assert _raises(lambda: B.check_fit_span(dict(sp, fit={"span": ["2016-03-01", "2025-11-30"]}), "s4", f1, STUB), ValueError)
    assert _raises(lambda: B.check_fit_span({k: v for k, v in sp.items() if k != "fit"}, "s4", f1, STUB), ValueError)
    bad = {k: dict(h, kind="loglinear", n_events=12) for k, h in saved.volume.items()}
    assert _raises(lambda: B.check_heads(STUB, ("T1", "final"), bad), ValueError)
    # T2's heads are the bake-off's: a record missing, or a head under the floor that is not the fallback, refuses
    # the candidate (Part B 7 on every fold scored)
    import shutil
    res = json.loads((_stub_root() / "_bakeoff" / "results.json").read_text())
    fold = S2.season_label(STUB_SEASON)
    assert B.t2_head_records(res, fold, cand.geo, STUB) == cand.head_records[("T2", fold)]
    assert _raises(lambda: B.t2_head_records(res, "2016-17", cand.geo, STUB), KeyError)
    assert _raises(lambda: B.t2_head_records({"volume": {}}, fold, cand.geo, STUB), KeyError)
    with tempfile.TemporaryDirectory() as tmp:
        for what, edit in (("no record", lambda r: r["volume"]["per_basin"]["central"]["recipes"]["loglinear"]["folds"].pop(fold)),
                           ("under the floor", lambda r: r["volume"]["per_basin"]["central"]["recipes"]["loglinear"]["folds"][fold]
                            .update(kind="loglinear", n_events=12))):
            d = Path(tmp) / what.replace(" ", "_")
            shutil.copytree(_stub_root() / "_bakeoff", d)
            r = json.loads((d / "results.json").read_text())
            edit(r)
            (d / "results.json").write_text(json.dumps(r))
            man = json.loads(json.dumps(saved.manifest))
            man["s2"]["bakeoff"] = str(d / "results.json")
            try:
                B.from_saved(dataclasses.replace(saved, manifest=man))
                raise AssertionError(f"{what}: not refused")
            except (KeyError, ValueError) as e:
                assert "Part B 7" in str(e) and "central" in str(e), (what, e)
    s4 = json.loads(json.dumps(saved.s4_quality))
    s4["fit"]["folds"] = [r for r in s4["fit"]["folds"] if r["tier"] != "T2"]
    assert _raises(lambda: B.from_saved(dataclasses.replace(saved, s4_quality=s4)), KeyError)
    t2 = cand.t2[cand.t2["basin"] != "south"]
    assert _raises(lambda: B._check_rows(t2, "T2", cand.geo, v_hat=True), ValueError)
    from sklearn.linear_model import LogisticRegression
    neg = LogisticRegression().fit(np.c_[np.arange(10.0)], np.r_[np.zeros(5), np.ones(5)].astype(int))
    neg.coef_ = -np.abs(neg.coef_)
    assert _raises(lambda: SC.check_nonnegative(neg, "stub"), ValueError)
    assert _raises(lambda: B.load_set(STUB, "served", stage_set=cand), ValueError)
    b = _sb()
    with tempfile.TemporaryDirectory() as tmp:
        old = B.STAGES_DIR
        try:
            B.STAGES_DIR = Path(tmp)
            paths = B.write(type(b)(**{**b.__dict__, "n_boot": B.B_PROTOCOL}))
        finally:
            B.STAGES_DIR = old
        assert sorted(p.name for p in paths) == ["manifest.json", "rows.csv.gz", "scores.json"]
        man = json.loads((Path(tmp) / STUB / "manifest.json").read_text())
        assert man["stage_candidate"]["t2_selection"] == "nested" and man["windows"]["t2_label"] == B.T2_NESTED_LABEL
        assert any(k.endswith("rows.csv.gz") and "_bakeoff" in k for k in man["inputs"])
    # the served set's written build the comparisons read is pinned, so a served rebuild on other inputs stales them
    served = _served_dir() / B.served_name()
    for n in B.SERVED_BUILD_FILES:
        assert b.manifest["inputs"][str(served / n)] == B.input_sha(served / n), n
    real = str((B.STAGES_DIR / B.served_name()).relative_to(ROOT))      # the build read the temporary served build only
    assert not any(k.startswith(real + "/") for k in b.manifest["inputs"])
    assert not any(k.endswith(f"{B.served_name()}/rows.csv.gz") for k in _b().manifest["inputs"])   # never its own


def test_stage_candidates_load_through_stages_candidates():
    """The build reads a stage candidate through stages_candidates.load_set, which refuses what the stub shows
    tampered: a file changed after it was saved, an unlisted file, a missing stamp, a negative weight."""
    import pickle
    import shutil
    import stages_candidates as SC
    root = _stub_root()
    assert B.saved_loader() is SC.load_set

    def rehash(d: Path, fname: str) -> None:
        man = json.loads((d / "manifest.json").read_text())
        man["files"][fname] = hashlib.sha256((d / fname).read_bytes()).hexdigest()
        (d / "manifest.json").write_text(json.dumps(man))

    def unstamped(d: Path) -> None:
        s4 = json.loads((d / "s4_quality.json").read_text())
        s4.pop("set")
        (d / "s4_quality.json").write_text(json.dumps(s4))
        rehash(d, "s4_quality.json")

    def negative(d: Path) -> None:
        f = d / "central_model.pkl"
        obj = pickle.loads(f.read_bytes())
        obj["model"].coef_ = -np.abs(obj["model"].coef_) - 0.1
        f.write_bytes(pickle.dumps(obj))
        rehash(d, f.name)

    tampers = {"a changed file": lambda d: (d / "s4_quality.json").write_bytes((d / "s4_quality.json").read_bytes() + b" "),
               "an unlisted file": lambda d: (d / "extra.json").write_text("{}"),
               "a missing stamp": unstamped, "a negative weight": negative}
    with tempfile.TemporaryDirectory() as tmp:
        for what, tamper in tampers.items():
            r = Path(tmp) / what.replace(" ", "_")
            shutil.copytree(root / STUB, r / STUB)
            tamper(r / STUB)
            assert _raises(lambda: SC.load_set(STUB, root=r)), what

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
