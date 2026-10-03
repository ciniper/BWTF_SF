"""stages_s4_v3: S4 v3, zone-level water quality with a rain background (P8c; STAGES_DESIGN.md Part C §3.4,
§6 change 3, §7 s4_quality.json; Part B 4, 5, 6; STAGES_PROTOCOL.md stages_v3 §7 S4 rules, §8 S4 row).

On a slice of the folds, so the file runs in about a minute: T1 (the finals), T1-holdout and the T2 seasons
2019-20 and 2023-24, with λ fixed (the nested choice is exercised on one fold with a two-value grid) and a
50-resample bootstrap (the CLI and ``write`` without a test directory use the protocol's 2,000). The pins:
  - every fitted fold is monotone non-increasing in k and large ≥ small in every bucket, x ≤ 1, the rain slopes
    never negative, also with the pooling all but off (λ = 0.1); a spec breaking large ≥ small is refused;
  - the fit uses the function the use calls: the objective's q is compose_v2.lingering's, and compose_v2.s4 on
    the spec reproduces it on every fit row, for the fitted parameters and for random feasible ones; the
    recorded log-likelihood is compose_v2.s4's; the analytic gradient matches finite differences;
  - no fold sees its season: its fit rows, their read span D−11…D+4 (the history D−7…D widened by
    X-LEDGER-SUSPECT's reach, derived from exclusions' constants) and its medians miss the scored window, and
    rewriting every truth, overflow, size and rain of the held-out season leaves the fold's spec unchanged;
    the climatology reference of a season's rows never reads that season;
  - no n_sampled feature: the background is FEATURES exactly, a spec naming n_sampled is refused, the fitter
    takes no such input, and a row's n_sampled never moves the fit;
  - resamples are out of the fit and of the primary score: every fit row and every primary row is a first
    look, flipping the resamples' truth leaves the fit as it was, and they are scored as their own stratum;
  - X-S4-FOLLOWUP: on fit rows with no zone overflow in the week, OCEAN#20 / 21 / 22 are out of the truth;
  - the primary is v3 − the served table on T2's first-look oracle rows, its rule's parts reported, "the
    defect" decided by a CI and the verdict saying when the fix was not tested; the Candlestick stations are
    4615–4617 (derived from SFPUC4's station_basin); the strata (§3.4, source and analyte era included)
    partition the scored rows;
  - the served table keeps its recipe: refit on DataSF + Poo Bot (never STARDB), T1 on the served artifacts;
    STARDB joins v3's truth only inside 2016-10 → 2020-07; persistence reads D−7…D−1, never D;
  - sizes follow the S3 spec (fit = use across stages): at a fitted Westside φ the oracle sizes are
    stages_build.true_history's under sfpuc4_v1, the chained sizes φ·v̂, and ``write`` refuses a set whose
    S3 sizes zones with other φ (and a φ ≠ 1 spec into a set with no S3);
  - so does the v̂ (fit = use, Part B 6): given a stage candidate's own fold v̂, the oracle history is read
    under sfpuc4_v1 as the build composes the candidate (stages_build.true_history, every zone), the 0/1
    history unchanged, only overflows with no measured volume sized differently; the folds record whose v̂
    and which geography; ``write`` refuses a set holding its own S2 unless the study read that S2; the
    challenger's committed s4_quality.json is its own fit (its finals' fold refit at the recorded λ on its own
    S3's φ and S2's v̂ gives the recorded buckets, background and medians);
  - s4_quality.json round-trips through stages_candidates and compose_v2 (kind zone_v3, unit zone, logistic
    background, monotone, sources, fit with every fold, each fold's φ) and through stages_build.s4_fold_spec
    (the reader that scores a stage candidate); a write outside stages_candidates/ is refused, and so is a
    bootstrap below 2,000 anywhere under it;
  - today's lingering curve at zone level (sfpuc4_shared8_v2): on geo_v1, zone_table's ocean is
    train_v4.fit_impact_table's Ocean Beach on the same inputs and window, and the served frames as they are
    differ from those inputs only on X-S2-VOLQ days; zone_curves reads a table as compose_v2's GEO_V1 adapter
    does, takes the other size's x where impact.impact_fraction would and raises where neither size reaches; the
    candidate's committed s4_quality.json is served_recipe's own fit on its own S2's v̂.

Counts are as of the committed data's end, 2026-08-17 (Part B 23).

    venv/bin/python tests/test_stages_s4_v3.py
"""
from __future__ import annotations

import dataclasses
import functools
import inspect
import json
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
import samples as SMP  # noqa: E402
import stage2 as STG2  # noqa: E402
import stages_build as SB  # noqa: E402
import stages_entries as E  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_s4_v3 as M  # noqa: E402
import train_v4 as T4  # noqa: E402
import truth as T  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402

LAM = 10.0
SEASONS = (2019, 2023)
N_BOOT = 50
AS_OF = "2026-08-17"
PHI = {"westside>ocean": 0.8, "westside>baker_china": 0.3}     # a fitted-φ S3's Westside links (stages_s3_links' kind)


@functools.lru_cache(maxsize=1)
def _inputs() -> M.Inputs:
    return M.load_inputs(log=lambda *a: None)


@functools.lru_cache(maxsize=1)
def _study() -> M.Study:
    return M.run(tiers=M.TIERS, seasons=SEASONS, lam=LAM, n_boot=N_BOOT, inputs=_inputs(), log=lambda *a: None)


@functools.lru_cache(maxsize=1)
def _scores() -> dict:
    return M.scores(_study())


@functools.lru_cache(maxsize=1)
def _phi_study() -> M.Study:
    """T1 and the T2 season 2019-20, zones sized at PHI (an S3 candidate's Westside φ)."""
    keys = [("T1", "final"), ("T2", "2019-20")]
    return M.run(tiers=("T1", "T2"), seasons=(2019,), lam=LAM, n_boot=N_BOOT, inputs=_inputs(), log=lambda *a: None,
                 phis={k: PHI for k in keys})


def _fold(tier: str, season: int | None = None):
    return next(f for f in _inputs().fitted.folds if f.tier == tier and (season is None or f.season == season))


def _fit_parts(fold):
    inp = _inputs()
    ch = M.served_chain(inp, fold)
    return ch, M.fit_rows(inp, fold, ch["hist"]), M.train_days(inp, fold)


def _same_spec(a: dict, b: dict) -> bool:
    return (a["buckets"] == b["buckets"] and a["background"]["coef"] == b["background"]["coef"]
            and a["zone_median_mg"] == b["zone_median_mg"])


def _curve_ok(bk: dict) -> None:
    for s in C.SIZES:
        xs = [bk[f"{b}_{s}"] for b in M.BUCKET_ORDER]
        assert all(0.0 <= x <= 1.0 for x in xs), xs
        assert all(b <= a + 1e-12 for a, b in zip(xs, xs[1:])), (s, xs)        # non-increasing in k
    for b in M.BUCKET_ORDER:
        assert bk[f"{b}_large"] >= bk[f"{b}_small"] - 1e-12, (b, bk)            # a bigger overflow never lowers it


# ── monotone, large ≥ small ────────────────────────────────────────────────

def test_monotone_and_large_ge_small_every_fold():
    st = _study()
    assert set(st.folds) == {("T1", "final"), ("T1-holdout", "pre_holdout"), ("T2", "2019-20"), ("T2", "2023-24")}
    for key, f in st.folds.items():
        for spec in (f["spec"], f["background"]):
            M.check_v3(spec)
            assert spec["kind"] == "zone_v3" and spec["unit"] == "zone" and spec["monotone"] is True
            for z in ZONES:
                _curve_ok(spec["buckets"][z])
                c = spec["background"]["coef"][z]
                assert c["hinge_rain3_0.1"] >= 0 and c["hinge_rain3_0.1"] + c["hinge_rain3_0.5"] >= -1e-12, (key, c)
        assert all(v == 0.0 for bk in f["background"]["buckets"].values() for v in bk.values())   # x ≡ 0
    # with the pooling all but off the constraints still hold (they are the fit's, not the penalty's)
    ch, rows, td = _fit_parts(_fold("T2", 2019))
    for lam in (0.1, 0.0):
        r = M.fit(ch["hist"], _inputs().rain, rows, td, lam)
        for z in ZONES:
            _curve_ok(r.spec["buckets"][z])
    # the served artifacts' table breaks large ≥ small somewhere in five of its six links; v3 refuses that
    adapter = C.geo_v1_adapter_specs()["s4"]["buckets"]
    broken = [u for u, bk in adapter.items()
              if any(bk[f"{b}_large"] is not None and bk[f"{b}_small"] is not None and bk[f"{b}_large"] < bk[f"{b}_small"]
                     for b in M.BUCKET_ORDER)]
    assert len(broken) == 5, broken
    bad = json.loads(json.dumps(st.folds[M.FINAL]["spec"]))
    bad["buckets"]["ocean"]["1_large"] = bad["buckets"]["ocean"]["1_small"] - 0.01
    bad["buckets"]["ocean"]["2_large"] = min(bad["buckets"]["ocean"]["2_large"], bad["buckets"]["ocean"]["1_large"])
    bad["buckets"]["ocean"]["3_large"] = min(bad["buckets"]["ocean"]["3_large"], bad["buckets"]["ocean"]["2_large"])
    bad["buckets"]["ocean"]["4-5_large"] = min(bad["buckets"]["ocean"]["4-5_large"], bad["buckets"]["ocean"]["3_large"])
    bad["buckets"]["ocean"]["6-7_large"] = min(bad["buckets"]["ocean"]["6-7_large"], bad["buckets"]["ocean"]["4-5_large"])
    try:
        M.check_v3(bad)
        raise AssertionError("a spec with large < small was accepted")
    except ValueError as e:
        assert "large" in str(e)


# ── fit = use ──────────────────────────────────────────────────────────────

def test_fit_is_use():
    st = _study()
    for key, f in st.folds.items():
        assert f["gap"] <= M.FIT_USE_TOL, (key, f["gap"])
    inp = _inputs()
    fold = _fold("T2", 2019)
    ch, rows, td = _fit_parts(fold)
    calls = {"lingering": 0, "s4": 0}
    real_l, real_s4 = C.lingering, C.s4

    def lingering(*a, **k):
        calls["lingering"] += 1
        return real_l(*a, **k)

    def s4(*a, **k):
        calls["s4"] += 1
        return real_s4(*a, **k)

    C.lingering, C.s4 = lingering, s4
    try:
        r = M.fit(ch["hist"], inp.rain, rows, td, LAM)
    finally:
        C.lingering, C.s4 = real_l, real_s4
    assert calls["lingering"] > 10 and calls["s4"] >= 1, calls          # the objective's algebra and the use check
    # the recorded log-likelihood is compose_v2.s4's own q on the fit rows
    q = M.q_use(r.spec, ch["hist"], inp.rain)
    qr = q.stack().reindex(pd.MultiIndex.from_arrays([pd.DatetimeIndex(rows["date"]), rows["zone"]])).to_numpy()
    y = rows["y_fit"].to_numpy(dtype=float)
    nll = -np.mean(y * np.log(qr) + (1 - y) * np.log(1 - qr))
    assert abs(nll - r.spec["fit"]["neg_loglik_per_row"]) < 1e-9, (nll, r.spec["fit"]["neg_loglik_per_row"])
    # for random feasible parameters too: the objective's q is compose_v2.s4's on every row
    L = M.layout(ch["hist"], inp.rain, rows, M.zone_medians(ch["hist"], td))
    rng = np.random.default_rng(7)
    Z = len(ZONES)
    for _ in range(3):
        th = M.start(L, "zone")
        th[:Z] += rng.normal(0, 0.5, Z)
        th[Z:Z + 2] = rng.uniform(0, 2, 2)
        th[Z + 2] = rng.normal(0, 0.5)
        d = rng.uniform(0, 0.08, (Z, 2, M.NB))
        d[:, 1] += d[:, 0]                                                  # large ≥ small in every bucket
        th[Z + 3:] = d.ravel()
        spec = M.spec_from(th, "zone", L.medians)
        assert M.check_fit_is_use(th, "zone", L, spec) <= 1e-12


def test_gradient_matches_finite_differences():
    inp = _inputs()
    ch, rows, td = _fit_parts(_fold("T2", 2023))
    L = M.layout(ch["hist"], inp.rain, rows, M.zone_medians(ch["hist"], td))
    rng = np.random.default_rng(3)
    xbar = M._curves(M.start(L, "pooled"), "pooled", len(ZONES))
    for mode in M.MODES:
        th = M.start(L, mode)
        th[len(ZONES) + 3:] += rng.uniform(0, 0.02, len(th) - len(ZONES) - 3)
        th[:len(ZONES)] += rng.normal(0, 0.3, len(ZONES))
        _, g = M.objective(th, L, mode, LAM, xbar)
        h = 1e-6
        for i in range(len(th)):
            e = np.zeros_like(th)
            e[i] = h
            num = (M.objective(th + e, L, mode, LAM, xbar, grad=False) - M.objective(th - e, L, mode, LAM, xbar, grad=False)) / (2 * h)
            assert abs(num - g[i]) < 1e-7, (mode, i, num, g[i])


def test_fit_is_deterministic():
    inp = _inputs()
    ch, rows, td = _fit_parts(_fold("T2", 2023))
    a, b = (M.fit(ch["hist"], inp.rain, rows, td, LAM) for _ in range(2))
    assert _same_spec(a.spec, b.spec)


# ── no fold sees its season ────────────────────────────────────────────────

def test_no_fold_sees_its_season():
    st, inp = _study(), _inputs()
    # a fit row reads D−7…D, and its S4 rules read X-LEDGER-SUSPECT's triggers on D−8…D+3, each over its D−3…D+1
    before = len(C.LAGS) - 1 + X.SUSPECT_BEFORE + X.SUSPECT_AFTER
    after = X.SUSPECT_BEFORE + X.SUSPECT_AFTER
    assert M.EXCL_REACH == (before, after) == (11, 4)
    near = 0
    for (tier, fold), f in st.folds.items():
        fo = next(x for x in inp.fitted.folds if (x.tier, x.fold) == (tier, fold))
        win = M.scored_window(inp, fo)
        assert not f["train_days"].isin(win).any(), (tier, fold)
        r = f["fit_rows"]
        assert not r["date"].isin(win).any() and M.window_clear(r["date"], win[0], win[-1]).all(), (tier, fold)
        d = pd.DatetimeIndex(r["date"])
        assert ((d + pd.Timedelta(days=after) < win[0]) | (d - pd.Timedelta(days=before) > win[-1])).all(), (tier, fold)
        # the training pool holds first looks inside that reach (so the guard is doing something)
        pdd = pd.DatetimeIndex(inp.pool.loc[(inp.pool["excl"] == "") & inp.pool["date"].isin(f["train_days"]), "date"])
        near += int((((pdd + pd.Timedelta(days=after) >= win[0]) & (pdd < win[0]))
                     | ((pdd - pd.Timedelta(days=before) <= win[-1]) & (pdd > win[-1]))).sum())
        rows = st.rows[(st.rows["tier"] == tier) & (st.rows["fold"] == fold)]
        assert pd.DatetimeIndex(rows["date"]).isin(win).all()               # every emitted row is the fold's scored window
        if tier == "T2":
            assert fo.season not in f["spec"]["fit"]["fit_seasons"]
    assert near > 0, near
    # rewrite every truth, overflow, size and rain of the held-out season: the fold's spec does not move
    fold = _fold("T2", 2019)
    ch, rows, td = _fit_parts(fold)
    r0 = M.fit(ch["hist"], inp.rain, rows, td, LAM)
    lo, hi = fold.start, fold.end
    rng = np.random.default_rng(11)
    pool = inp.pool.copy()
    ins = ((pool["date"] >= lo) & (pool["date"] <= hi)).to_numpy()
    pool.loc[ins, "y"] = 1.0 - pool.loc[ins, "y"]
    pool.loc[ins, "y_bg"] = 1.0 - pool.loc[ins, "y_bg"]
    pool.loc[ins, "excl"] = ""                                              # even its resamples read as first looks
    p, v = ch["hist"].p.copy(), ch["hist"].v.copy()
    days = (p.index >= lo) & (p.index <= hi)
    p.loc[days] = rng.integers(0, 2, size=p.loc[days].shape).astype(float)
    v.loc[days] = np.where(p.loc[days] == 1, rng.uniform(0.1, 500, size=v.loc[days].shape), 0.0)
    hist2 = C.History(p, v)
    inp2 = dataclasses.replace(inp, pool=pool, cache={})
    rows2 = M.fit_rows(inp2, fold, hist2)
    pd.testing.assert_frame_equal(rows.reset_index(drop=True), rows2.reset_index(drop=True))
    r1 = M.fit(hist2, inp.rain, rows2, td, LAM)
    assert _same_spec(r0.spec, r1.spec)                                    # bit for bit
    # … and the nested λ choice reads the same rows (two-value grid, one fold)
    lam_a, br_a = M.choose_lambda(ch["hist"], inp.rain, rows, td, grid=(1.0, 100.0))
    lam_b, br_b = M.choose_lambda(hist2, inp.rain, rows2, td, grid=(1.0, 100.0))
    assert lam_a == lam_b and br_a == br_b, (br_a, br_b)
    # the season's rain: rain3 is a pandas rolling sum (compose_v2.background's), whose running total carries
    # ~1e−16 of float residue past any day it changed, so the spec may move by that much and no more
    rain = inp.rain.copy()
    rain.loc[(rain.index >= lo) & (rain.index <= hi)] *= 3.0
    r2 = M.fit(hist2, rain, rows2, td, LAM)
    gap = max(max(abs(r0.spec["buckets"][z][k] - r2.spec["buckets"][z][k]) for k in r0.spec["buckets"][z]) for z in ZONES)
    gap = max(gap, max(abs(r0.spec["background"]["coef"][z][k] - r2.spec["background"]["coef"][z][k])
                       for z in ZONES for k in r0.spec["background"]["coef"][z]))
    assert gap < 1e-12, gap
    # the T1 finals are fit through 2025-10-31; T1-holdout before 2023-07-01
    assert st.folds[M.FINAL]["train_days"].max() == pd.Timestamp("2025-10-31")
    assert st.folds[("T1-holdout", "pre_holdout")]["train_days"].max() == pd.Timestamp("2023-06-30")
    # the climatology reference (BSS's) of a season's rows never reads that season: flip its truth, same reference
    t2 = st.rows[(st.rows["tier"] == "T2") & (st.rows["fold"] == "2019-20")].reset_index(drop=True)
    pool = SB._pool("s4", inp.ctx, G.get(M.GEOGRAPHY), "oracle")
    flip = pool.copy()
    ins = ((flip["date"] >= lo) & (flip["date"] <= hi)).to_numpy()
    assert ins.any()
    flip.loc[ins, "y"] = 1.0 - flip.loc[ins, "y"]
    assert np.array_equal(SB.references(t2, pool), SB.references(t2, flip))
    assert np.allclose(SB.references(t2, pool), t2["ref"].to_numpy())


# ── no n_sampled ───────────────────────────────────────────────────────────

def test_no_n_sampled_feature():
    st = _study()
    assert not any("n_sampled" in f for f in M.FEATURES) and M.FEATURES == ("hinge_rain3_0.1", "hinge_rain3_0.5", "wet_season")
    for f in st.folds.values():
        assert tuple(f["spec"]["background"]["features"]) == M.FEATURES
    params = set(inspect.signature(M.fit).parameters) | set(inspect.signature(M.layout).parameters)
    assert not any("sampled" in p for p in params), params
    assert not any("sampled" in fld.name for fld in dataclasses.fields(M.Layout))
    bad = json.loads(json.dumps(st.folds[M.FINAL]["spec"]))
    bad["background"]["features"].append("log_n_sampled")
    for z in ZONES:
        bad["background"]["coef"][z]["log_n_sampled"] = 0.1
    for check in (M.check_v3, lambda s: C.check_s4_spec(s, G.get(M.GEOGRAPHY))):
        try:
            check(bad)
            raise AssertionError("an n_sampled predictor was accepted")
        except ValueError as e:
            assert "n_sampled" in str(e) or "issue time" in str(e)
    # a row's n_sampled (a stratum) never moves the fit
    inp = _inputs()
    ch, rows, td = _fit_parts(_fold("T2", 2023))
    r0 = M.fit(ch["hist"], inp.rain, rows, td, LAM)
    r1 = M.fit(ch["hist"], inp.rain, rows.assign(n_sampled=np.random.default_rng(1).integers(1, 7, len(rows))), td, LAM)
    assert _same_spec(r0.spec, r1.spec)


# ── resamples out of the fit and of the primary ────────────────────────────

def test_resamples_out_of_fit_and_primary():
    st, inp = _study(), _inputs()
    zf = inp.ctx.frames["zone"]
    for f in st.folds.values():
        r = f["fit_rows"]
        assert (r["excl"] == "").all()
        fl = zf["first_look"].reindex(pd.MultiIndex.from_arrays([r["zone"], pd.DatetimeIndex(r["date"])]))
        assert (fl == 1).all()
    t2 = st.rows[st.rows["tier"] == "T2"]
    scored = t2[t2["excl"] == ""]
    fl = zf["first_look"].reindex(pd.MultiIndex.from_arrays([scored["unit"], pd.DatetimeIndex(scored["date"])]))
    assert (fl == 1).all() and not (scored["excl"] == "X-S4-RESAMPLE").any()
    sc = _scores()
    assert sc["primary"]["delta"]["pooled"]["n"] == len(scored)
    n_res = int((t2["excl"] == "X-S4-RESAMPLE").sum())
    assert n_res > 0 and sc["tiers"]["T2"]["resamples"]["n"] == n_res
    assert sc["tiers"]["T2"]["excluded"]["X-S4-RESAMPLE"] == n_res
    # flipping the resamples' truth leaves the fit as it was
    fold = _fold("T2", 2019)
    ch, rows, td = _fit_parts(fold)
    r0 = M.fit(ch["hist"], inp.rain, rows, td, LAM)
    pool = inp.pool.copy()
    res = (pool["excl"] == "X-S4-RESAMPLE").to_numpy()
    pool.loc[res, ["y", "y_bg"]] = 1.0 - pool.loc[res, ["y", "y_bg"]]
    rows2 = M.fit_rows(dataclasses.replace(inp, pool=pool, cache={}), fold, ch["hist"])
    assert _same_spec(r0.spec, M.fit(ch["hist"], inp.rain, rows2, td, LAM).spec)


def test_followup_out_of_background_fit():
    inp = _inputs()
    fu = X.followup_stations()
    assert fu == ("OCEAN#20_SL", "OCEAN#21_SL", "OCEAN#22_SL")
    free = M.followup_free_truth(inp.ctx.sources)
    smp = X.T._samples(tuple(inp.ctx.sources))
    only_fu = smp.groupby(["zone", "date"])["station"].agg(lambda s: set(s) <= set(fu))
    only_fu = only_fu[only_fu]
    assert len(only_fu) and not free.index.isin(only_fu.index).any()      # a day sampled only there has no such truth
    ch, rows, td = _fit_parts(_fold("T1"))
    quiet, loud = rows[rows["quiet"]], rows[~rows["quiet"]]
    assert len(quiet) and len(loud)
    key = pd.MultiIndex.from_arrays([quiet["zone"], pd.DatetimeIndex(quiet["date"])])
    assert np.array_equal(quiet["y_fit"].to_numpy(), free.reindex(key).to_numpy())
    assert np.array_equal(loud["y_fit"].to_numpy(), loud["y"].to_numpy())
    # a first look sampled only at the follow-up stations: out of the fit with no overflow in the week, in with one
    only = inp.pool[(inp.pool["excl"] == "") & inp.pool["y_bg"].isna() & inp.pool["date"].isin(td)]
    week = ch["hist"].p.rolling(8, min_periods=1).max().stack()
    had = week.reindex(pd.MultiIndex.from_arrays([pd.DatetimeIndex(only["date"]), only["zone"]])).to_numpy() > 0
    in_fit = only.set_index(["zone", "date"]).index.isin(rows.set_index(["zone", "date"]).index)
    assert len(only) and not in_fit[~had].any() and in_fit[had].all(), (len(only), int(had.sum()))


def test_wet_season_recorded():
    spec = _study().folds[M.FINAL]["spec"]
    assert spec["background"]["wet_season_months"] == [10, 11, 12, 1, 2, 3, 4] == list(C.WET_SEASON_MONTHS)
    idx = pd.date_range("2024-01-01", "2024-12-31")
    F = M.rain_features(pd.Series(0.0, index=pd.date_range("2023-12-30", "2024-12-31")), idx)
    assert np.array_equal(F[:, 2], np.isin(idx.month, (10, 11, 12, 1, 2, 3, 4)).astype(float))


# ── the primary, benchmarks, Candlestick ───────────────────────────────────

def test_primary_benchmarks_and_candlestick():
    sc, st = _scores(), _study()
    p = sc["primary"]
    d = p["delta"]["pooled"]
    t2 = st.rows[(st.rows["tier"] == "T2") & (st.rows["excl"] == "")]
    want = float(np.mean((t2["v3"] - t2["y"]) ** 2) - np.mean((t2["served"] - t2["y"]) ** 2))
    assert abs(d["delta"] - want) < 1e-12
    assert p["superior"] == (d["hi"] < 0)
    assert p["noninferior_5pct"] == (d["hi"] < 0.05 * d["b"])
    assert p["passes"] == (p["superior"] or (p["noninferior_5pct"] and p["fixed"]))
    assert set(p["fix"]) == {"T2", "T1"} and "served_shows_defect" in p["fix"]["T2"]
    for t, fx in p["fix"].items():                                          # "shows the defect" is the CI's call
        assert fx["served_shows_defect"] == (fx["served_oracle_minus_chained"]["verdict"] == "worse"), t
    assert p["fix_tested"] == p["fix"]["T2"]["served_shows_defect"]
    if p["passes"] and not p["superior"]:
        assert ("fixed" in p["verdict"]) == p["fix_tested"] and ("not tested" in p["verdict"]) == (not p["fix_tested"])
    # §3.4's strata: dry / wet / day-of / tail, source and analyte era each partition the scored rows
    cell = sc["tiers"]["T2"]
    assert sum(cell["strata"][s]["n"] for s in ("dry", "wet_no_overflow", "dayof", "tail") if s in cell["strata"]) == len(t2)
    for name in ("source", "analyte_era"):
        assert sum(g["n"] for g in cell["strata"][name].values()) == len(t2), name
    assert set(cell["strata"]["source"]) <= {s for s, _, _ in SMP.D10_SOURCES}
    st2 = M.strata(t2, _inputs().ctx)
    zf = _inputs().ctx.frames["zone"]
    key = pd.MultiIndex.from_arrays([t2["unit"], pd.DatetimeIndex(t2["date"])])
    assert (zf["y"].reindex(key).to_numpy()[st2 == "dayof"] == 1).all()
    assert (zf["y"].reindex(key).to_numpy()[st2 != "dayof"] != 1).all()
    era = t2["tags"].str.contains("X-S4-ANALYTE").to_numpy()
    d2 = pd.DatetimeIndex(t2["date"])
    assert np.array_equal(era, np.asarray((d2 >= "2020-07-01") & (d2 <= "2021-12-31")))
    arms = sc["tiers"]["T2"]["arms"]
    assert set(arms) == set(M.ARMS) and abs(arms["climatology"]["pooled"]["bss"]) < 1e-12
    for a in ("v3", "served", "background", "rain_logit", "persistence"):
        assert arms[a]["pooled"]["n"] == len(t2)
    assert set(sc["tiers"]) == {"T1", "T1-holdout", "T2"}
    # Candlestick (A2): the three East stations SFPUC4 puts in South, derived
    assert M.candlestick_stations() == ("BAY#301.2_SL", "BAY#301.1_SL", "BAY#300.1_SL")
    cs = sc["candlestick"]
    assert cs["stations"] == ["4615", "4616", "4617"] and cs["T2"]["n"] > 0
    east_days = st.rows[(st.rows["tier"] == "T2") & (st.rows["unit"] == "east")]
    assert cs["T2"]["n"] <= len(east_days)                                 # Candlestick sample-days are East sample-days
    assert sc["as_of"] == str(E.data_end().date())                         # 2026-08-17 at the freeze (AS_OF)


def test_rows_are_out_of_fold_and_paired():
    st = _study()
    key = ["unit", "date", "tier", "excl", "y"]
    assert st.rows[key].equals(st.chained[key])                            # oracle and chained on identical rows
    assert not (st.rows["tier"] == "T3").any() and st.rows["stage"].eq("s4").all()
    for arm in ("v3", "served", "background", "rain_logit", "persistence", "climatology", "ref"):
        assert np.isfinite(st.rows[arm]).all(), arm
    for (tier, fold), f in st.folds.items():
        dates = pd.DatetimeIndex(st.rows.loc[(st.rows["tier"] == tier) & (st.rows["fold"] == fold), "date"])
        assert not dates.isin(f["train_days"]).any(), (tier, fold)          # never a row whose fit saw its day


def test_primary_rule_on_constructed_rows():
    """Protocol §8's S4 rule on rows built to sit either side of its edges: "the table shows the defect" is its
    oracle − chained CI wholly above 0, never a positive point estimate; the verdict says when the fix was not
    tested; v3's own oracle worse than its chained fails the non-inferiority route."""
    import types
    blk = _inputs().blocks
    days = blk.index[(blk.index >= "2019-07-01") & (blk.index <= "2024-06-30")]

    def study(seed, k, v3_chained_better=False):
        rng = np.random.default_rng(seed)
        d = pd.DatetimeIndex(rng.choice(days, 600, replace=False)).sort_values()
        y = (rng.random(600) < 0.25).astype(float)
        so = np.clip(0.25 + rng.normal(0, 0.12, 600), 0.01, 0.99)
        sch = np.clip(so + k * (y - so) + rng.normal(0, 0.03, 600), 0.01, 0.99)   # chained: a little sharper
        rows = pd.DataFrame({"unit": "east", "date": d, "tier": "T2", "excl": "", "y": y, "v3": so, "served": so})
        ch = rows.assign(served=sch, v3=sch if v3_chained_better else so)
        return M.Study(rows, ch, {}, types.SimpleNamespace(blocks=blk), 200)

    # the table's oracle − chained point estimate is above 0, its CI is not: no defect shown, the fix not tested
    p = M.primary(study(9, 0.0035))
    srv = p["fix"]["T2"]["served_oracle_minus_chained"]
    assert srv["delta"] > 0 and srv["verdict"] == "no clear difference", srv
    assert not p["fix"]["T2"]["served_shows_defect"] and not p["fix_tested"]
    assert p["passes"] and not p["superior"] and "not tested" in p["verdict"] and "fixed" not in p["verdict"]
    # the CI wholly above 0: the defect is shown, and v3 (oracle = chained) fixes it
    p = M.primary(study(0, 0.012))
    assert p["fix"]["T2"]["served_oracle_minus_chained"]["verdict"] == "worse" and p["fix_tested"]
    assert p["passes"] and p["verdict"] == "passes: non-inferior and the defect fixed"
    # v3's own oracle worse than its chained: not fixed, so non-inferiority alone does not pass
    p = M.primary(study(0, 0.012, v3_chained_better=True))
    assert p["noninferior_5pct"] and not p["fixed"] and not p["passes"] and p["verdict"] == "does not pass"


def test_served_table_keeps_its_recipe():
    inp = _inputs()
    # the benchmark is the served recipe: DataSF + Poo Bot (samples.DEFAULT_SOURCES), never STARDB ...
    assert set(inp.samples_v1["source"]) == set(SMP.DEFAULT_SOURCES) == {"datasf", "poobot"}
    seen = []
    real = SB.fold_specs

    def spy(bundle, fold, keep, train, events, samples):
        seen.append(set(samples["source"]))
        return real(bundle, fold, keep, train, events, samples)

    SB.fold_specs = spy
    try:
        M.served_chain(dataclasses.replace(inp, cache={}), _fold("T2", 2019))
    finally:
        SB.fold_specs = real
    assert seen == [{"datasf", "poobot"}], seen
    # ... and T1 reads the served artifacts' own table
    t1 = M.served_chain(inp, _fold("T1"))
    assert t1["specs"]["s4"]["buckets"] == C.geo_v1_adapter_specs()["s4"]["buckets"]
    # v3's truth is D10's: STARDB joins it only inside 2016-10 → 2020-07, Poo Bot only to 2017-01
    smp = T._samples(tuple(inp.ctx.sources))
    win = {s: (pd.Timestamp(lo), pd.Timestamp(hi) if hi else None) for s, lo, hi in SMP.D10_SOURCES}
    for src, g in smp.groupby("source"):
        lo, hi = win[src]
        assert g["date"].min() >= lo and (hi is None or g["date"].max() <= hi), (src, g["date"].min(), g["date"].max())
    assert {"stardb", "datasf", "poobot"} == set(smp["source"])
    srcs = set(";".join(inp.samples_zone["sources"]).split(";"))
    assert "stardb" in srcs


def test_persistence_reads_before_the_day():
    d = pd.Timestamp("2024-01-10")
    sz = pd.DataFrame({"zone": ["ocean"] * 3, "date": [d, d - pd.Timedelta(days=1), d - pd.Timedelta(days=8)],
                       "y": [1, 0, 1]})
    p, seen = M.persistence(sz, ["ocean", "ocean", "north"], [d, d + pd.Timedelta(days=1), d], [0.3, 0.3, 0.2])
    # D's own sample is not visible on D: its newest earlier one (D−1, clean) is; D+1 sees D's (over); no sample → fallback
    assert list(p) == [0.0, 1.0, 0.2] and list(seen) == [True, True, False]
    p2, seen2 = M.persistence(sz[sz["date"] != d - pd.Timedelta(days=1)], ["ocean"], [d], [0.3])
    assert list(p2) == [0.3] and not seen2[0]                              # D−8 is outside D−7…D−1


def test_size_rule_follows_s3():
    inp, st0, st = _inputs(), _study(), _phi_study()
    g4 = G.get(M.GEOGRAPHY)
    phi = M.unit_phi(PHI)
    assert phi["westside>ocean"] == 0.8 and phi["north_shore>north"] == 1.0
    tr4 = SB.truths(g4, inp.end)
    for key, f in st.folds.items():
        assert f["vol_share"] == phi and f["gap"] <= M.FIT_USE_TOL and f["spec"]["fit"]["vol_share"] == phi
        fo = next(x for x in inp.fitted.folds if (x.tier, x.fold) == key)
        ch = M.served_chain(inp, fo, phi)
        # the oracle sizes are stages_build.true_history's under sfpuc4_v1 with an S3 spec at these φ (the link's
        # own volume where φ ≠ 1); East reads the same outfalls, only the v̂ fill of an unmeasured day differs
        h4, _ = SB.true_history(g4, {"s3": C.benchmark_s3_spec(g4, "identity", vol_share=phi), "s4": {"unit": "zone"}},
                                tr4, inp.days, ch["v"].rename(columns={k: "south" for k in ch["v"] if k not in g4.keys}))
        for z in ("ocean", "baker_china", "north"):
            assert np.allclose(h4.v[z].to_numpy(), ch["hist"].v[z].to_numpy(), atol=1e-12), (key, z)
        # chained: the served S3's zone p, its Westside zones sized φ·v̂
        base = M.served_chain(inp, fo)
        assert ch["chain_hist"].p.equals(base["chain_hist"].p)
        for z, lid in (("ocean", "westside>ocean"), ("baker_china", "westside>baker_china")):
            assert np.allclose(ch["chain_hist"].v[z], phi[lid] * ch["v"]["westside"]), (key, z)
        for z in ("north", "east"):
            assert ch["chain_hist"].v[z].equals(base["chain_hist"].v[z])
        # the medians move where the sizes do, and only there
        m0, m1 = st0.folds[key]["spec"]["zone_median_mg"], f["spec"]["zone_median_mg"]
        assert m1["ocean"] < m0["ocean"] and m1["baker_china"] < m0["baker_china"]
        assert m1["north"] == m0["north"] and m1["east"] == m0["east"]
    # a φ on a link the served geography cannot read raises
    try:
        M.phi_on(inp.geo_hist, {"central>east": 0.5})
        raise AssertionError("a φ on central>east was read through geo_v1")
    except ValueError as e:
        assert "central>east" in str(e)
    # write: the sizes must be the set's S3's
    import stages_candidates as SC
    import stages_s3_links as S3L
    with tempfile.TemporaryDirectory() as tmp:
        try:                                                                # a φ ≠ 1 spec into a set with no S3
            M.write(st, name="sfpuc4_s4_phi_test", root=tmp)
            raise AssertionError("a φ ≠ 1 S4 was saved into a set with no S3 to pin it")
        except ValueError as e:
            assert "s3_links" in str(e)

        def blocks(phis):
            out = []
            for k in dict.fromkeys([*st0.folds, *st.folds]):
                links = {}
                for lk in g4.links:
                    if lk.identity:
                        links[lk.id] = {"share": {"kind": "identity"}, "vol_share": 1.0}
                    else:
                        links[lk.id] = {"share": {"kind": "logit_logvol", "coef": {"a": 0.5, "b": 0.5}, "basin_median_mg": 5.0},
                                        "constant": 0.7, "vol_share": phis[lk.id]}
                out.append({"tier": k[0], "fold": k[1], "train_seasons": [2016], "share_fit_span": ["2016-10-01", "2016-12-31"],
                            "links": links, "basin_median_mg": {"westside": 5.0}, "cofire": {}, "union": {"east": {"rule": "max"}}})
            return out

        for name, phis in (("sfpuc4_s3_phi_test", phi), ("sfpuc4_s3_one_test", M.unit_phi())):
            b = blocks(phis)
            top = S3L.fold_spec({"geography": g4.version, "fit": {"folds": b}}, "T1", "final")
            SC.save_component(name, "s3_links", {**top, "component": "toy_links", "fit": {"folds": b}}, root=tmp)
        assert M.size_rules(SC.load_set("sfpuc4_s3_phi_test", root=tmp).s3_links, list(st.folds)) == {k: phi for k in st.folds}
        for study, name in ((st0, "sfpuc4_s3_phi_test"), (st, "sfpuc4_s3_one_test")):
            try:
                M.write(study, name=name, root=tmp)
                raise AssertionError(f"an S4 sized at other φ joined {name}")
            except ValueError as e:
                assert "refit with run(s3_set=" in str(e), e
        path = M.write(st, name="sfpuc4_s3_phi_test", root=tmp)               # the matching set takes it
        saved = SC.load_set("sfpuc4_s3_phi_test", root=tmp)
        assert saved.s3_links is not None and Path(path).name == "s4_quality.json"
        assert all(r["vol_share"] == phi for r in saved.s4_quality["fit"]["folds"])


class _Cand:
    """A stage candidate's S2 as S4 v3 reads it (stages_s3_links.CandidateS2's ``name`` and ``values``): here the
    served heads' v̂ under sfpuc4_v1's keys, doubled, so every fill an unmeasured overflow takes moves."""
    name = "sfpuc4_toy_cand"

    def values(self, key, what, days):
        assert what == "v_hat"
        fo = next(x for x in _inputs().fitted.folds if (x.tier, x.fold) == tuple(key))
        v = M.served_chain(_inputs(), fo)["v"]
        g4 = G.get(M.GEOGRAPHY)
        v = v.rename(columns={k: "south" for k in v if k not in g4.keys})[list(g4.keys)]
        return 2.0 * v.reindex(pd.DatetimeIndex(days))


def test_candidate_v_hat_sizes_the_oracle_as_the_build_composes():
    inp, cand = _inputs(), _Cand()
    g4 = G.get(M.GEOGRAPHY)
    phi = M.unit_phi(PHI)
    fo = _fold("T1")
    base = M.served_chain(inp, fo, phi)
    vc = cand.values((fo.tier, fo.fold), "v_hat", inp.days)
    ch = M.served_chain(inp, fo, phi, vc, cand.name)
    assert ch["hist"].p.equals(base["hist"].p)                              # the 0/1 history is geography-invariant
    want, _ = SB.true_history(g4, M.zone_size_spec(g4, phi), SB.truths(g4, inp.end), inp.days, vc)
    for z in ZONES:
        assert np.allclose(ch["hist"].v[z].to_numpy(), want.v[z].to_numpy(), atol=1e-12), z
    moved = (np.abs(ch["hist"].v - base["hist"].v) > 1e-9) & (base["hist"].p > 0)
    assert moved.to_numpy().any() and not ((np.abs(ch["hist"].v - base["hist"].v) > 1e-9) & (base["hist"].p == 0)).to_numpy().any()
    # only overflow zone-days with no measured volume move: 2017-02-03 East (Central's volume filed as '<'), as of 2026-08-17
    assert bool(moved.loc[pd.Timestamp("2017-02-03"), "east"])
    assert ch["chain_hist"].p.equals(base["chain_hist"].p) and ch["served_oracle"].equals(base["served_oracle"])
    for bad in (vc.drop(columns=["south"]), vc.iloc[1:]):
        try:
            M.served_chain(inp, fo, phi, bad, "bad_cand")
            raise AssertionError("a candidate v̂ missing a basin or a day was read")
        except KeyError as e:
            assert "every day" in str(e)
    st = M.run(tiers=("T1",), lam=LAM, n_boot=N_BOOT, inputs=inp, log=lambda *a: None, phis={("T1", "final"): PHI},
               s2_source=cand)
    rec = st.folds[("T1", "final")]["spec"]["fit"]
    assert st.s2_set == cand.name and rec["v_hat_from"] == cand.name and rec["history_geography"] == M.GEOGRAPHY
    assert _phi_study().folds[("T1", "final")]["spec"]["fit"]["v_hat_from"] == inp.bundle.name
    # write: a set holding its own S2 takes only a study that read it
    import stages_candidates as SC
    name = "sfpuc4_shared8_v1"
    if not (SC.ROOT / name / "manifest.json").exists():
        return
    saved = SC.load_set(name)
    with tempfile.TemporaryDirectory() as tmp:
        SC.save_component(name, "s2", {"geography": g4.version, "component": saved.components["s2"], "models": saved.models,
                                       "volume": saved.volume, "holdout_models": saved.holdout_models,
                                       "holdout_volume": saved.holdout_volume, "spec": saved.manifest["s2"]}, root=tmp)
        for study, text in ((_study(), "holds its own S2"), (dataclasses.replace(_study(), s2_set="sfpuc4_other"), "holds its own S2")):
            try:
                M.check_s2_rule(study, name, root=tmp)
                raise AssertionError("an S4 fit on another S2's v̂ joined a set holding its own")
            except ValueError as e:
                assert text in str(e), e
        M.check_s2_rule(dataclasses.replace(_study(), s2_set=name), name, root=tmp)      # its own S2: accepted
        try:
            M.check_s2_rule(dataclasses.replace(_study(), s2_set=name), "sfpuc4_elsewhere", root=tmp)
            raise AssertionError("an S4 fit on one candidate's v̂ joined another set")
        except ValueError as e:
            assert "save it into that set" in str(e)


def test_the_candidates_committed_s4_is_its_own_fit():
    """The challenger's committed s4_quality.json (stages_candidates.assemble) holds its own fit: the finals' fold
    refit here at its recorded λ, on its own S3's φ and its own S2's v̂ (stages_s3_links.candidate_s2), gives the
    recorded buckets, background and zone medians, and the record says which S3 and S2 it read (Part B 6)."""
    import stages_candidates as SC
    import stages_s3_links as S3L
    name = "sfpuc4_shared8_v1"
    if not (SC.ROOT / name / "manifest.json").exists() or SC.load_set(name).s4_quality is None:
        print(f"  (no s4_quality.json in {name} yet)")
        return
    saved = SC.load_set(name).s4_quality
    rec = next(r for r in saved["fit"]["folds"] if (r["tier"], r["fold"]) == M.FINAL)
    assert saved["fit"]["s3_set"] == saved["fit"]["s2_set"] == rec["v_hat_from"] == name, saved["fit"]
    cand = S3L.candidate_s2(name, check=False)
    st = M.run(tiers=("T1",), lam=rec["lambda"], n_boot=N_BOOT, inputs=_inputs(), log=lambda *a: None, s3_set=name,
               s2_source=cand)
    got = json.loads(json.dumps(st.folds[M.FINAL]["spec"]))
    assert got["fit"]["vol_share"] == rec["vol_share"] and got["fit"]["v_hat_from"] == name
    for part in ("buckets", "zone_median_mg"):
        for z in ZONES:
            a, b = got[part][z], rec[part][z]
            if isinstance(a, dict):
                assert set(a) == set(b) and max(abs(a[k] - b[k]) for k in a) < 1e-9, (part, z)
            else:
                assert abs(a - b) < 1e-9, (part, z)
    for z in ZONES:
        a, b = got["background"]["coef"][z], rec["background"][z]
        assert set(a) == set(b) and max(abs(a[k] - b[k]) for k in a) < 1e-9, z


# ── the artifact ───────────────────────────────────────────────────────────

def test_write_round_trip():
    st = _study()
    sc = _scores()
    with tempfile.TemporaryDirectory() as tmp:
        path = M.write(st, name="sfpuc4_s4_v3_test", root=tmp, sc=sc)
        saved = json.loads(Path(path).read_text())
        spec = M.for_compose(saved)
        C.check_s4_spec(spec, G.get("sfpuc4_v1"))
        assert spec["kind"] == "zone_v3" and spec["unit"] == "zone" and spec["background"]["kind"] == "logistic"
        assert spec["monotone"] is True and set(spec["zone_median_mg"]) == set(ZONES) and spec["sources"]
        assert {(f["tier"], f["fold"]) for f in spec["fit"]["folds"]} == set(st.folds)
        assert spec["fit"]["scores"]["primary"]["verdict"] == sc["primary"]["verdict"]
        assert spec["buckets"] == st.folds[M.FINAL]["spec"]["buckets"]
        assert all(r["vol_share"] == M.unit_phi() for r in spec["fit"]["folds"])
        # stages_build reads every fold's spec back from these records when it scores a stage candidate
        g4 = G.get("sfpuc4_v1")
        for key, f in st.folds.items():
            back = SB.s4_fold_spec(saved, key, g4)
            assert back["buckets"] == f["spec"]["buckets"] and back["background"]["coef"] == f["spec"]["background"]["coef"]
            assert back["zone_median_mg"] == f["spec"]["zone_median_mg"]
            fo = next(x for x in _inputs().fitted.folds if (x.tier, x.fold) == key)
            SB.check_fit_span(back, "s4", fo, "test")
    for kw in ({}, {"root": M.CANDIDATES_ROOT}):                          # not under stages_candidates/, however named
        try:
            M.write(st, "sfpuc4_s4_v3_test", sc=sc, **kw)
            raise AssertionError(f"a 50-resample artifact was written to the candidates' directory ({kw})")
        except ValueError as e:
            assert "2000" in str(e) or "2,000" in str(e), (kw, e)
    try:
        M.write(st, "sfpuc4_s4_v3_test", root=FORECAST / "data" / "models" / "nope", sc=sc)
        raise AssertionError("a write outside stages_candidates/ was accepted")
    except ValueError as e:
        assert "stages_candidates" in str(e)


# ── today's lingering curve at zone level (sfpuc4_shared8_v2) ──────────────

SERVED_PARTS = "sfpuc4_shared8_v2"      # the stage candidate that takes it (stages_candidates.assemble_served_parts)


def test_the_zone_recipe_is_the_served_tables_on_the_same_inputs():
    """Parity: on geo_v1, zone_table's ocean (fed by group Ocean Beach alone) is train_v4.fit_impact_table's Ocean
    Beach with stage 2 v2's event days (the group's own outfalls), on the same inputs and window: the served training
    frames on Westside's ledger (2017-12-01 → 2025-10-31), holding the ledger's measured volume (truth's Σ of measured
    volume_MG; a day with none measured takes the served head's v̂, as both recipes then do), the same samples and
    heads. Why that and not the served frames as they are: before the window the frames count the Poo Bot archive
    days as covered, which the ledger rule never does (protocol §1), and inside it they read a CIWQS day's volume as
    Σ volume_MG with a blank as 0 and a '<' bound at its bound, every CIWQS day measured; the zone recipe reads the
    ledger's measured volume, which the build sizes its oracle with (fit = use). The two differ exactly on the
    X-S2-VOLQ days (2018-11-21's '<0.01' only reads 0.01 MG measured there), which moves the median (as of the data
    end, 13.93 MG on the frames vs 14.08) and some days' size class."""
    inp = _inputs()
    g1 = G.get("geo_v1")
    b = inp.bundle
    hi = S2.TRAINED_THROUGH
    win = pd.date_range(T.ledger_start("Oceanside"), hi)
    tr = SB.truths(g1, hi)
    days = pd.date_range(T.TRUTH_START, hi)
    vol = tr.basin_vol["westside"]
    frames = {s: f[f["date"].isin(win)].reset_index(drop=True) for s, f in inp.train.items()}
    assert (inp.train[b.chosen["Westside"]].query("Westside_label_source == 'poobot'")["date"] < win[0]).all()
    same = {}
    for s, f in frames.items():
        v = vol.reindex(pd.DatetimeIndex(f["date"])).to_numpy()
        same[s] = f.assign(Westside_volume_known=np.where(np.isfinite(v), f["Westside_volume_known"], 0),
                           Westside_volume_mg=np.where(np.isfinite(v), v, 0.0))
    heads = {g1.basin(k).name: h for k, h in b.s2.heads.items()}
    v_hat = pd.DataFrame({k: T4.predicted_volume(h, inp.train[h["rain_source"]].set_index("date").reindex(days))
                          for k, h in b.s2.heads.items()}, index=days)
    served = T4.fit_impact_table(same, b.chosen, heads, inp.samples_v1,
                                 event_days=STG2.group_event_days(same, b.chosen, inp.events))[0]["Ocean Beach"]
    mine = M.zone_table(g1, tr, days, v_hat, inp.samples_v1, win)["ocean"]
    assert served["buckets"] == mine["buckets"], (served["buckets"], mine["buckets"])
    assert served["n_sample_days"] == mine["n_sample_days"] and served["median_event_volume_mg"] == round(mine["median_event_volume_mg"], 2)
    # the served frames as they are: the same Ocean Beach event days, another size exactly on the X-S2-VOLQ days
    f = frames[b.chosen["Westside"]].set_index("date")
    ob = sorted(STG2.group_event_days(frames, b.chosen, inp.events)["Ocean Beach"])
    zo = T.zone_overflow(g1, win[0], hi)
    assert ob == sorted(zo.loc[(zo["zone"] == "ocean") & (zo["y"] == 1), "date"])
    bo = T.basin_onsets(g1, win[0], hi).set_index(["basin", "date"]).loc["westside"]
    off = [d for d in ob if not np.isclose(f.at[d, "Westside_volume_mg"], vol.get(d, np.nan))]
    assert off and set(off) <= set(bo.index[bo["volq"]]), off
    assert pd.Timestamp("2018-11-21") in off and np.isnan(vol[pd.Timestamp("2018-11-21")])


def test_zone_curves_read_a_table_as_the_geo_v1_adapter_does():
    """zone_curves is compose_v2.geo_v1_adapter_specs' reading of a served table (impact.smooth_table, then
    compose_v2._attributable): on the served impact_table.json, each zone given a group's table, every x the adapter
    holds is equal and the background is its baseline; a size with no bucket at or before k takes the other size's x
    there (impact.impact_fraction's fallback), a bucket neither size reaches raises, and so does a baseline of 0."""
    raw = json.loads((T4.SERVE_DIR / "impact_table.json").read_text())
    group = {"ocean": "Ocean Beach", "baker_china": "Baker-China", "north": "Aquatic Park", "east": "Mission Creek"}
    bk, coef, filled = M.zone_curves({z: raw[g] for z, g in group.items()})
    ad = C.geo_v1_adapter_specs(impact_table=raw)["s4"]
    link = {lk.legacy_group: lk.id for lk in G.get("geo_v1").links}
    for z, g in group.items():
        for k, x in ad["buckets"][link[g]].items():
            assert (f"{z} {k}" in filled) if x is None else bk[z][k] == x, (z, k)
        assert abs(1.0 / (1.0 + np.exp(-coef[z]["intercept"])) - ad["background"]["p"][link[g]]) < 1e-12, z
    C.check_s4_spec({"geography": "sfpuc4_v1", "kind": "zone_v3", "unit": "zone", "monotone": True, "buckets": bk,
                     "background": {"kind": "logistic", "features": [], "coef": coef},
                     "zone_median_mg": {z: 1.0 for z in ZONES}}, G.get("sfpuc4_v1"))           # a zone_v3 spec as is
    # fills and refusals on a toy table: no small bucket on the day of → the large x; neither → raise
    toy = {z: {"buckets": {"baseline_no_recent_discharge": {"p_elevated": 0.1, "n": 50},
                           "d0_large": {"p_elevated": 0.9, "n": 5}, "d1_small": {"p_elevated": 0.4, "n": 4},
                           "d1_large": {"p_elevated": 0.5, "n": 4}}} for z in ZONES}
    bk, _, filled = M.zone_curves(toy)
    assert filled == [f"{z} 0_small" for z in ZONES] and bk["ocean"]["0_small"] == bk["ocean"]["0_large"] == (0.9 - 0.1) / 0.9
    assert bk["ocean"]["6-7_small"] == (0.4 - 0.1) / 0.9                                   # the nearest bucket before
    toy["east"]["buckets"].pop("d0_large")
    for t, text in ((toy, "either size"), ({**toy, "east": {"buckets": {"baseline_no_recent_discharge": {"p_elevated": 0.0, "n": 9},
                                                                       "d0_large": {"p_elevated": 0.5, "n": 3}}}}, "finite logit")):
        try:
            M.zone_curves(t)
            raise AssertionError(f"accepted ({text})")
        except ValueError as e:
            assert text in str(e), e


def test_the_served_parts_candidates_committed_s4_is_its_own_fit():
    """sfpuc4_shared8_v2's s4_quality.json (stages_candidates.assemble_served_parts) is served_recipe's own fit: the
    finals' fold and the T2 season 2019-20 refit here (zone_table on the fold's training days with the set's own fold
    v̂, zone_curves) give the recorded buckets, background and zone medians exactly; every fold is at φ 1 with the
    set's v̂ and reads back through stages_build.s4_fold_spec; the note names the recipe and Part B 6."""
    import stages_candidates as SC
    import stages_s3_links as S3L
    if not (SC.ROOT / SERVED_PARTS / "manifest.json").exists() or SC.load_set(SERVED_PARTS).s4_quality is None:
        print(f"  (no s4_quality.json in {SERVED_PARTS} yet)")
        return
    saved = SC.load_set(SERVED_PARTS).s4_quality
    assert saved["component"] == M.RECIPE_KIND and saved["fit"]["s2_set"] == saved["fit"]["s3_set"] == SERVED_PARTS
    assert "fit_impact_table" in saved["fit"]["recipe"] and "Part B 6" in saved["note"]
    assert saved["background"]["features"] == [] and saved["monotone"] is True
    inp, g4 = _inputs(), G.get(M.GEOGRAPHY)
    recs = {(r["tier"], r["fold"]): r for r in saved["fit"]["folds"]}
    assert set(recs) == {(f.tier, f.fold) for f in inp.fitted.folds}
    for key, r in recs.items():
        assert r["vol_share"] == M.unit_phi() and r["v_hat_from"] == SERVED_PARTS and r["history_geography"] == M.GEOGRAPHY
        back = SB.s4_fold_spec(saved, key, g4)
        assert back["buckets"] == r["buckets"] and back["zone_median_mg"] == r["zone_median_mg"]
    src = S3L.candidate_s2(SERVED_PARTS, check=False)
    for fold in (_fold("T1"), _fold("T2", 2019)):
        key = (fold.tier, fold.fold)
        tab = M.zone_table(g4, M.candidate_truths(inp), inp.days, src.values(key, "v_hat", inp.days), inp.samples_v1,
                           M.train_days(inp, fold))
        bk, coef, filled = M.zone_curves(tab)
        r = recs[key]
        assert bk == r["buckets"] and coef == r["background"] and filled == r["filled"], key
        assert {z: t["median_event_volume_mg"] for z, t in tab.items()} == r["zone_median_mg"], key


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
