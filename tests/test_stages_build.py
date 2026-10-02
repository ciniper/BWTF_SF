"""stages_build: every stage of one set scored on days no fitted component saw (P7; STAGES_DESIGN.md Part C §7,
Part B 1, 2, 9, 16; STAGES_PROTOCOL.md stages_v2 §2–§8).

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
  - the protocol stamp is the protocol file's sha; a rebuild gives the same scores.json;
  - writing goes only under data/models/stages/<set>/, rows only for the served set, never with B < 2,000.

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
import stages_flowchart as F  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_spec as SP  # noqa: E402
import train_v4 as T4  # noqa: E402
import truth as T  # noqa: E402

SLICE = dict(entries=("oracle", "rain", "L1", "L1s"), tiers=("T1", "T1-holdout", "T2"), seasons=(2019,),
             feeds=("oracle", "degraded:1"), s5_tiers=("T1",), n_boot=50, log=lambda *a: None)
AS_OF = "2026-08-17"
TOL = 1e-9          # one model's predictions in different batch sizes differ by ~1e-16 (stages_build.EPS)


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
    fit = B.fit_s3_s4(train, bundle.chosen, heads, samples, events, "v2")
    served = json.loads((T4.SERVE_DIR / "stage2.json").read_text())
    assert fit["raw"] == raw_json
    for k in ("variant", "kind", "group_outfalls", "shares", "median_event_volume_mg", "impact_table"):
        assert fit["stage2"][k] == served[k], k
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
    sc = _b().scores
    fig = sc["figure"]
    nodes = {s["node"] for s in SP.STAGES}
    assert set(fig) == nodes, sorted(set(fig) ^ nodes)
    for node in nodes:
        for k in ("oracle", "chained"):
            assert fig[node][k] and fig[node][k]["v"] is not None, (node, k)
    assert len(fig["m.out"]["lead"]) == 6 and fig["m.out"]["lead"][1]["v"] == fig["m.out"]["chained"]["v"]
    svg, phone = F.render("geo_v1", fig, sc["figure_counts"])
    for node in nodes - {"m.out"}:
        unit = SP.STAGE_OF_CODE[SP.NODE[node]["stage"]]["unit_fmt"]
        for k in ("oracle", "chained"):
            assert F.fmt_score(fig[node][k]["v"], unit) in svg, (node, k)
    assert F.fmt_count(F.flat_counts(sc["figure_counts"]), "X-S4-UNSAMPLED") in svg
    assert "{n_city_days}" not in svg and F.fmt_count(sc["figure_counts"]["slots"], "n_city_days") in svg
    assert sc["figure_window"] == "T1" and set(sc["figures"]) == {"T1", "T1-holdout", "T2"}


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
        assert hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == h, f


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
    if not (B.STAGES_DIR / B.served_name() / "rows.csv.gz").exists():
        print("SKIP the served set's rows are not written yet")
        return
    c = B.build("logit_v1", "candidates", entries=("oracle", "rain"), tiers=("T1",), feeds=("oracle",), n_boot=50,
                log=lambda *a: None)
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
    if B.out_dir("logit_v1").exists():                                  # a candidate's directory holds no rows
        assert not (B.out_dir("logit_v1") / "rows.csv.gz").exists()


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
    assert sc["protocol"] == m["protocol"] and set(sc["figure"]) == {s["node"] for s in SP.STAGES}
    assert (d / "rows.csv.gz").stat().st_size < 6.5e6
    assert not any((d / n).exists() for n in ("alert_lines.json",))
    # every other set's artifacts too: current, scores only, compared with the served set on its rows
    for c in sorted(p for p in B.STAGES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_") and p != d):
        mc = json.loads((c / "manifest.json").read_text())
        scc = json.loads((c / "scores.json").read_text())
        assert mc["set"] == c.name and mc["bootstrap"]["n"] == B.B_PROTOCOL and not B.stale_files(mc), c.name
        assert scc["protocol"] == m["protocol"] and not (c / "rows.csv.gz").exists(), c.name
        assert "skipped" not in scc["paired"]["vs_served"], (c.name, scc["paired"]["vs_served"].get("skipped"))


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
