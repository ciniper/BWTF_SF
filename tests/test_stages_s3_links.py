"""stages_s3_links (P8b): stage S3 under SFPUC4_V1 — Westside's link shares fit on v̂, identity links, the
co-firing matrix and the East union rule chosen nested (STAGES_DESIGN.md §3.3, §2.4, §7; Part B 1, 2;
STAGES_PROTOCOL.md §2, §8 S3a / S3b).

On committed data, no network (counts as of the data end 2026-08-17):
  - the folds are stages_s2's, as whole training seasons; the development heads refit per fold are
    stages_s2's fold heads, prediction for prediction;
  - the shares are fit on out-of-sample v̂, never the filed volume: every fit day's v̂ comes from a head
    without the day's season, the spec's coefficients are a fit on that v̂ alone, a fit on the filed
    volume differs, and scrambling the filed volumes moves the size medians but never a share;
  - a fold never sees its season (shares, φ, medians, events, heads, S2, every inner fit), and a planted
    leak raises; every fitted part (constant share, φ, size medians, co-firing shares) is recomputed here
    from the truth frames on the fold's training days alone, so a part read from all seasons fails;
  - the S3 oracle reads a known occurrence for every basin feeding a scored zone (no unknown read as 0);
  - identity links are exact (share 1, φ 1, 0 mismatches on every X-S3-ID row under every rule);
  - every union rule stays inside the Fréchet bounds; the rule is chosen by inner leave-one-season-out,
    max (the default) unless a challenger is better on the inner rows' storm-block CI;
  - S3b is verify.paired_delta on storm blocks with the p-value and CIs Holm needs; Holm's steps;
  - the spec passes compose_v2 (file and every fold), and saves and loads through stages_candidates;
  - the assemble step: the stage candidate's own S2 (candidate_s2) keeps each fold's design, reproduces what
    the build scores and refits inner folds on that design only; a season whose inner Westside head would be
    under the 20-event floor is left out of the share fit and recorded; the candidate's committed spec is a
    fresh fit on its own S2;
  - today's split on the city map (sfpuc4_shared8_v2): fold_spec takes a recorded size_blend share as recorded
    (composed as stage2.group_share), its benchmark the record's constant; the served groups Ocean Beach and
    Baker-China are read as the SFPUC4 links with the same outfalls, and a crossed map raises; the committed
    spec keeps sfpuc4_shared8_v1's union rule, co-firing shares and constant benchmark in every fold, its
    Westside links the served split at φ 1 (T1 the served stage2.json, T1-holdout refit here by the served
    recipe through stages_build.fold_specs).
Run: venv/bin/python tests/test_stages_s3_links.py
"""
from __future__ import annotations

import dataclasses
import functools
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(ROOT / "features" / "forecast"), str(MODELS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import candidates as CAND  # noqa: E402
import compose_v2 as C  # noqa: E402
import stage2 as STG2  # noqa: E402
import stages_build as SB  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_s3_links as S  # noqa: E402
import train_v4 as T4  # noqa: E402
import verify as V  # noqa: E402
from shared import geography as G  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")     # the committed data's end: every count below is as of this day
N_BOOT = 200                           # the tests' bootstrap (the written spec uses the protocol's 2,000)
GEO = G.get("sfpuc4_v1")
WEST_DAYS_T2 = 98                      # T2 oracle rows of the two Westside zones (48 Ocean Beach + 50 Baker & China), as of AS_OF
EAST_ROWS_T2 = 3088                    # T2 East rain-known rows scored, as of AS_OF (the served set's GEO_V1 East: the same 3088)


@functools.lru_cache(maxsize=None)
def _s2():
    return S.dev_s2()


@functools.lru_cache(maxsize=None)
def _tr():
    return S.truth(AS_OF)


@functools.lru_cache(maxsize=None)
def _res():
    return S.run(_s2(), _tr(), n_boot=N_BOOT, log=lambda *a: None)


def _final():
    return _res().fits[("T1", S2.FOLD_FINAL)]


def raises(fn, text: str):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        assert text in str(e), f"raised {type(e).__name__}: {e!r}, expected text {text!r}"
        return
    raise AssertionError(f"did not raise (expected {text!r})")


# ── folds and the development S2 ────────────────────────────────────────────

def test_the_folds_are_stages_s2s_as_whole_training_seasons():
    folds = S.plan()
    want = [(t, f) for t, f, *_ in S2._plan(S2.TIERS)]
    assert [f.key for f in folds] == want
    cal = S._calendar()
    for f, (_, _, start, end, season, keep) in zip(folds, S2._plan(S2.TIERS)):
        kept = cal if keep is None else cal[keep(cal)]
        assert S.train_days(f.seasons).equals(pd.DatetimeIndex(kept["date"])), f.key
        assert (f.start, f.end, f.season) == (start, end, season)
        if f.tier == "T2":
            assert f.season not in f.seasons and len(f.seasons & set(S2.T2_SEASONS)) == 8
        if f.tier == "T1-holdout":
            assert S.train_days(f.seasons).max() < S2.HOLDOUT_START
    final = next(f for f in folds if f.tier == "T1")
    assert S.train_days(final.seasons).max() == S.TRAIN_END
    assert S.inner_seasons(final) == S2.T2_SEASONS          # the final union pick reads all nine T2 seasons


def test_the_dev_heads_are_stages_s2s_fold_heads():
    s2 = _s2()
    g1 = G.get("geo_v1")
    assert {lk.outfalls for lk in g1.links_from("westside")} == {lk.outfalls for lk in GEO.links_from("westside")}
    fitted = S2.fit(CAND.served_info()["name"])                # the served set's folds: T1, T1-holdout, T2
    frame = s2.frames[fitted.set.heads["westside"]["rain_source"]]
    days = frame.index[(frame.index >= "2016-07-01") & (frame.index <= AS_OF)]
    by = {f.key: f for f in S.plan()}
    for f in fitted.folds:
        theirs = T4.predicted_volume(f.heads["westside"], frame.loc[days])
        mine = s2.predict("westside", S.VHAT, by[(f.tier, f.fold)].seasons, days)
        assert np.abs(mine - theirs).max() < 1e-9, (f.tier, f.fold, float(np.abs(mine - theirs).max()))
        assert s2.seen("westside", S.VHAT, by[(f.tier, f.fold)].seasons).isin(pd.DatetimeIndex(f.seen["westside"])).all()


def test_the_dev_s2_keeps_every_weight_nonnegative():
    s2 = _s2()
    for b in ("central", "south"):
        for f in S.plan():
            m = s2.fitted(b, S.P, f.seasons)["model"]
            assert (m.named_steps["lr"].coef_ >= -1e-12).all(), (b, f.key)
    assert s2.designs[("central", S.P)]["rain_source"] == "SF Downtown"


# ── shares on v̂ ────────────────────────────────────────────────────────────

def test_shares_are_fit_on_out_of_sample_v_hat_never_the_filed_volume():
    s2, tr, ff = _s2(), _tr(), _final()
    t = ff.table
    assert not any("vol" in c for c in t.columns)            # the fit table holds no filed volume at all
    # every fit day's v̂ is the fold's head design refit without that day's season
    for s, g in t.groupby("season"):
        inner = ff.fold.seasons - {int(s)}
        assert np.array_equal(g["v_hat"].to_numpy(), s2.predict("westside", S.VHAT, inner, g["date"]))
        assert str(int(s)) not in g["head_seasons"].iloc[0].split(",")
    insample = s2.predict("westside", S.VHAT, ff.fold.seasons, t["date"])
    assert (np.abs(insample - t["v_hat"].to_numpy()) > 1e-6).mean() > 0.9   # not the fold head's in-sample v̂
    # why (as of AS_OF): the GBR head's in-sample v̂ is nearly the filed volume, the out-of-fold v̂ is not
    d = t.drop_duplicates("date")
    fv = tr.basin[tr.basin["basin"] == "westside"].set_index("date")["volume_mg"].reindex(d["date"]).to_numpy(dtype=float)
    ok = np.isfinite(fv)
    r = lambda v: np.corrcoef(np.log1p(fv[ok]), np.log1p(v[ok]))[0, 1]  # noqa: E731
    assert (len(d), int(ok.sum())) == (51, 49)
    assert r(s2.predict("westside", S.VHAT, ff.fold.seasons, d["date"])) > 0.96 and r(d["v_hat"].to_numpy()) < 0.85
    # the spec's coefficients are the fit on that v̂ alone …
    again = S.fit_shares(t)
    top = _res().spec["links"]
    for lid in ("westside>ocean", "westside>baker_china"):
        assert top[lid]["share"]["coef"] == {"a": again[lid][S.SHARE]["a"], "b": again[lid][S.SHARE]["b"]}
    # … and a fit on the filed volume is a different share: code that read the true volume fails the line above
    bv = tr.basin[tr.basin["basin"] == "westside"].set_index("date")["volume_mg"]
    filed = bv.reindex(t["date"]).to_numpy(dtype=float)
    assert np.isfinite(filed).mean() > 0.9
    try:
        true_fit = S.fit_shares(t.assign(v_hat=np.where(np.isfinite(filed), filed, t["v_hat"])))
        assert abs(true_fit["westside>ocean"][S.SHARE]["b"] - again["westside>ocean"][S.SHARE]["b"]) > 0.5
    except ValueError as e:                                   # the filed volume all but decides Ocean Beach
        assert "separates" in str(e)
    # scrambling every filed volume moves the size medians, never a share (the measured volumes trade places;
    # blanks, '<' days and the no-event 0s stay where they are, so every day keeps its X-S2-VOLQ status)
    rng = np.random.default_rng(0)

    def scr(f):
        v = f["volume_mg"].to_numpy(dtype=float).copy()
        ok = np.isfinite(v) & (v > 0)
        v[ok] = rng.permutation(v[ok])
        return f.assign(volume_mg=v)
    tr2 = dataclasses.replace(tr, basin=scr(tr.basin), link=scr(tr.link))
    assert S.fit_shares(S.fit_table(ff.fold, s2, tr2)) == ff.shares
    assert S.basin_medians(ff.fold, tr2) != ff.medians


def test_fit_logit_recovers_known_coefficients_and_refuses_bad_fits():
    rng = np.random.default_rng(1)
    x = rng.gamma(1.5, 1.2, 20000)
    y = (rng.random(len(x)) < 1 / (1 + np.exp(-(-0.5 + 1.2 * x)))).astype(float)
    c = S.fit_logit(x, y)
    assert abs(c["a"] + 0.5) < 0.08 and abs(c["b"] - 1.2) < 0.08, c
    from sklearn.linear_model import LogisticRegression
    sk = LogisticRegression(C=1e12, max_iter=10000, tol=1e-12).fit(x[:, None], y)
    assert abs(sk.intercept_[0] - c["a"]) < 1e-4 and abs(sk.coef_[0, 0] - c["b"]) < 1e-4
    raises(lambda: S.fit_logit([1, 2, 3], [1, 1, 1]), "no share can be fit")
    raises(lambda: S.fit_logit([1, 2, 3, 4], [0, 0, 1, 1]), "separat")
    raises(lambda: S.fit_logit([1, np.nan, 3], [0, 1, 1]), "finite")
    raises(lambda: S.fit_logit([2, 2, 2], [0, 1, 1]), "not identified")


def test_shares_vol_shares_and_medians_read_as_of_the_data_end():
    ff = _final()
    n = ff.table.groupby("link").size().to_dict()
    assert n == {"westside>baker_china": 51, "westside>ocean": 49}      # 51 Westside days ≤ 2025-10-31; Ocean loses 2 carry-over days
    assert ff.shares["westside>ocean"][S.SHARE]["b"] > 0                # a bigger Westside overflow reaches Ocean Beach more often
    assert abs(ff.phi["westside>ocean"] - 0.87) < 0.01                  # §3.3: Ocean Beach outfalls are a median 87% of Westside volume
    assert 0 < ff.phi["westside>baker_china"] < 1
    assert all(ff.phi[lk.id] == 1.0 for lk in GEO.links if lk.identity)
    assert set(ff.medians) == set(GEO.keys) and all(v > 0 for v in ff.medians.values())
    for ff_ in _res().fits.values():
        for lid, s in ff_.shares.items():
            assert 0 < s["constant"] < 1 and s["n"] >= 35, (ff_.fold.key, lid)


# ── folds never see their season ────────────────────────────────────────────

def test_a_fold_never_sees_its_season():
    res, tr = _res(), _tr()
    for key, ff in res.fits.items():
        sd = S.scored_days(ff.fold, tr.end)
        for name, seen in ff.seen.items():
            assert not pd.DatetimeIndex(seen).isin(sd).any(), (key, name)
        if ff.fold.tier == "T2":
            s = ff.fold.season
            assert s not in set(ff.table["season"]), key
            assert s not in set(T4.wet_season(pd.Series(ff.seen["events"]))), key
            for inner in ff.union["inner"].values():
                assert s not in inner["train_seasons"], key
            assert str(s) not in ff.union["inner"]                     # never scored inside its own fold's choice
        if ff.fold.tier == "T1-holdout":
            assert max(pd.DatetimeIndex(v).max() for v in ff.seen.values()) < S2.HOLDOUT_START
    for name, rows in (("oracle", res.oracle), ("union", res.union)):
        for (tier, fold), g in rows.groupby(["tier", "fold"]):
            f = res.fits[(tier, fold)].fold
            assert g["date"].between(f.start, f.end).all(), (name, tier, fold)
            if tier == "T2":
                assert (T4.wet_season(g["date"]) == f.season).all()
    # a planted leak raises
    key = ("T2", "2018-19")
    ff = res.fits[key]
    leaky = dataclasses.replace(ff, seen={**ff.seen, "shares": ff.seen["shares"].append(S.scored_days(ff.fold, tr.end)[:1])})
    raises(lambda: S.check_out_of_fold({**res.fits, key: leaky}, {"oracle": res.oracle}, tr.end), "X-ALL-INSAMPLE")
    bad = ff.table.copy()
    bad.loc[0, "head_seasons"] = bad.loc[0, "head_seasons"] + f",{int(bad.loc[0, 'season'])}"
    raises(lambda: S.check_out_of_fold({**res.fits, key: dataclasses.replace(ff, table=bad)}, {}, tr.end), "saw its season")
    # … and so does an inner union fit that saw the inner season it is scored on
    for k, ff_ in res.fits.items():
        for s in ff_.union["inner"]:
            for b in ("central", "south"):
                assert not pd.DatetimeIndex(ff_.seen[f"union_p_{b}_{s}"]).isin(S.train_days({int(s)})).any(), (k, s, b)
    s = sorted(ff.union["inner"])[0]
    inner_leak = {**ff.seen, f"union_p_central_{s}": ff.seen[f"union_p_central_{s}"].append(S.train_days({int(s)})[:1])}
    raises(lambda: S.check_out_of_fold({**res.fits, key: dataclasses.replace(ff, seen=inner_leak)}, {}, tr.end), "inner season")


def test_every_fitted_part_is_refit_per_fold_on_its_training_days():
    """Each fold's constant share, φ, size medians and co-firing shares, recomputed here straight from the truth
    frames on the fold's training days alone (not through the module's own ``seen`` record, which a part read
    from all seasons would not change)."""
    res, tr = _res(), _tr()
    yes = lambda s: s.fillna(False).astype(bool).to_numpy()  # noqa: E731  (a nullable flag: NA, unknown, is no)
    for key, ff in res.fits.items():
        days = S.train_days(ff.fold.seasons)
        b = tr.basin[tr.basin["date"].isin(days).to_numpy() & yes(tr.basin["y"] == 1) & ~yes(tr.basin["volq"])]
        for k in GEO.keys:                                                  # one size median per basin
            assert ff.medians[k] == float(np.median(b.loc[b["basin"] == k, "volume_mg"].to_numpy(dtype=float))), (key, k)
        bv = b.set_index(["basin", "date"])["volume_mg"]
        o = tr.oracle[(tr.oracle["excl"] == "") & tr.oracle["date"].isin(days)]
        for lk in GEO.links:
            if lk.identity:
                continue
            ln = tr.link[(tr.link["link"] == lk.id).to_numpy() & tr.link["date"].isin(days).to_numpy() & yes(tr.link["y"] == 1)]
            den = bv.reindex(pd.MultiIndex.from_arrays([ln["basin"], ln["date"]])).to_numpy(dtype=float)
            ok = np.isfinite(den) & (den > 0)
            assert abs(ff.phi[lk.id] - float(np.median(ln["volume_mg"].to_numpy(dtype=float)[ok] / den[ok]))) < 1e-12, (key, lk.id)
            yz = o.loc[o["unit"] == lk.zone, "y"].to_numpy(dtype=float)     # the constant benchmark: the fit rows' rate
            assert abs(ff.shares[lk.id]["constant"] - yz.mean()) < 1e-12 and ff.shares[lk.id]["n"] == len(yz), (key, lk.id)
        ev = tr.events[pd.to_datetime(tr.events["event_date"]).dt.normalize().isin(days)]
        assert ff.cofire == C.cofire(GEO, ev), key
    # and none of them is one all-time fit shared by every fold
    fin, ho = _final(), res.fits[("T1-holdout", S2.FOLD_HOLDOUT)]
    assert fin.medians["westside"] != ho.medians["westside"] and fin.phi != ho.phi and fin.cofire != ho.cofire
    t2 = [ff for k, ff in res.fits.items() if k[0] == "T2"]
    assert len({json.dumps(ff.cofire, sort_keys=True) for ff in t2}) > 1 and len({ff.medians["central"] for ff in t2}) > 1


def test_the_oracle_never_reads_an_unknown_basin_as_no_overflow():
    tr = _tr()
    run = pd.date_range("2017-01-14", periods=3)          # Bayside's ledger is open (2016-10), Oceanside's is not (2017-12)
    rows = pd.DataFrame({"date": run, "unit": "north"})
    y = S.occurrence(tr, rows, run)
    assert np.isfinite(y.to_numpy()).all() and (y["westside"] == 0).all()   # Westside feeds no row here: set to 0
    assert np.isin(y["north_shore"].to_numpy(), (0.0, 1.0)).all()
    raises(lambda: S.occurrence(tr, rows.assign(unit="ocean"), run), "no known occurrence")


# ── identity links and the union ────────────────────────────────────────────

def test_identity_links_are_exact():
    res = _res()
    specs = [res.spec] + [S.fold_spec(res.spec, *k) for k in res.fits]
    for sp in specs:
        for lk in GEO.links:
            if lk.identity:
                assert sp["links"][lk.id]["share"] == {"kind": "identity"} and sp["links"][lk.id]["vol_share"] == 1.0
    ig = res.scores["integrity"]
    assert ig["s3_identity_mismatches"] == 0 and ig["rules"] == list(S.RULES)
    assert set(ig["by_zone"]) == {"north", "east"} and all(v["n_checked"] > 0 for v in ig["by_zone"].values())
    # by hand: the true basin occurrence through the final spec is the zone truth on North and East
    tr = _tr()
    rows = tr.identity[(tr.identity["excl"] == "X-S3-ID") & (tr.identity["date"] >= "2024-11-01")]
    days = pd.date_range(rows["date"].min(), rows["date"].max())
    yb = tr.basin.pivot(index="date", columns="basin", values="y").astype(float).reindex(days).fillna(0.0)[list(GEO.keys)]
    zp = C.s3(GEO, C.check_s3_spec({k: v for k, v in res.spec.items()}, GEO), yb, yb * 0.0).zone_p
    got = zp.stack().reindex(pd.MultiIndex.from_arrays([rows["date"], rows["unit"]])).to_numpy()
    assert len(rows) > 10 and np.array_equal(got, rows["y"].to_numpy(dtype=float))


def test_every_union_rule_stays_inside_the_frechet_bounds():
    u = _res().union
    assert len(u[u["tier"] == "T2"]) == EAST_ROWS_T2
    lo = u[["p_central", "p_south"]].max(axis=1).to_numpy()
    hi = np.minimum(1.0, u["p_central"] + u["p_south"]).to_numpy()
    for rule in S.RULES + ("nested",):
        p = u[rule].to_numpy()
        assert ((p >= lo - 1e-15) & (p <= hi + 1e-15)).all(), rule
    assert np.array_equal(u["max"].to_numpy(), lo)
    assert (u["noisy_or"].to_numpy() >= lo).all()
    # any p and any π through compose_v2.s3: still inside the bounds (cofire is clipped to the bound it would break)
    rng = np.random.default_rng(2)
    days = pd.date_range("2020-01-01", periods=400)
    pc, ps = rng.random(len(days)), rng.random(len(days))
    for pi_c in (0.0, 0.5, 0.95):
        cof = {**_final().cofire, "central>east|south>east": pi_c}
        for rule in S.RULES:
            e = S.east_p(GEO, rule, cof, {"central": pc, "south": ps}, days)["east"].to_numpy()
            assert ((e >= np.maximum(pc, ps) - 1e-15) & (e <= np.minimum(1, pc + ps) + 1e-15)).all(), (rule, pi_c)


PICKS = {("T1-holdout", S2.FOLD_HOLDOUT): "noisy_or"}   # as of AS_OF: noisy_or is better than max on the pre-holdout
                                                          # inner rows; every other fold keeps the default max


def test_the_union_rule_is_chosen_by_inner_leave_one_season_out():
    res, s2, tr = _res(), _s2(), _tr()
    for key, ff in res.fits.items():
        u = ff.union
        assert set(u["inner"]) == {str(s) for s in S.inner_seasons(ff.fold)}, key
        n = sum(v["n"] for v in u["inner"].values())
        assert n == u["n"]
        for rule in S.RULES:
            assert abs(u["brier"][rule] - sum(v["sse"][rule] for v in u["inner"].values()) / n) < 1e-15
        # §2.4: max stays unless a challenger is better (its Δ to max has a 90% CI wholly below 0)
        vs = u["pick"]["vs_default"]
        assert set(vs) == set(S.RULES) - {S.UNION_DEFAULT} and all(d["n"] == n for d in vs.values()), key
        better = {r for r, d in vs.items() if d["hi"] < 0}
        assert {r for r, d in vs.items() if d["verdict"] == "better"} == better, key
        assert (u["rule"] == S.UNION_DEFAULT) == (not better) and (u["rule"] == S.UNION_DEFAULT or u["rule"] in better), key
        assert u["pick"]["lowest"] == min(S.RULES, key=lambda r: (u["brier"][r], S.RULES.index(r))), key
        assert u["rule"] == PICKS.get(key, S.UNION_DEFAULT), (key, u["rule"], u["pick"]["why"])
        g = res.union[(res.union["tier"] == key[0]) & (res.union["fold"] == key[1])]
        assert np.array_equal(g["nested"].to_numpy(), g[u["rule"]].to_numpy()), key
        for b in ("central", "south"):                    # the scored rows' p: S2 fit on the fold's seasons only
            assert np.abs(g[f"p_{b}"].to_numpy() - s2.predict(b, S.P, ff.fold.seasons, g["date"])).max() < 1e-12, (key, b)
    # a lower Brier score alone never moves the rule: some fold's lowest is a challenger that is not better
    assert any(ff.union["pick"]["lowest"] != ff.union["rule"] for ff in res.fits.values())
    # one inner season recomputed by hand: S2 and π refit without it (and without the fold's own season)
    ff = res.fits[("T2", "2020-21")]
    ins = ff.fold.seasons - {2018}
    cof = C.cofire(GEO, S.fold_events(tr, ins))
    rows = S.chained_rows(tr, s2, ins, cof, S.train_days({2018}))
    for rule in S.RULES:
        assert abs(float(((rows[rule] - rows["y"]) ** 2).sum()) - ff.union["inner"]["2018"]["sse"][rule]) < 1e-12
    assert ff.union["inner"]["2018"]["train_seasons"] == sorted(ins)
    # the spec carries the final fold's choice, with its π from the final fold's co-firing shares
    top = res.spec["union"]["east"]
    assert top["rule"] == _final().union["rule"]
    assert top == C.union_block(GEO, top["rule"], _final().cofire)["east"]
    assert res.spec["cofire"] == _final().cofire == C.cofire(GEO, S.fold_events(tr, _final().fold.seasons))


def test_pick_union_keeps_the_default_unless_a_challenger_is_better():
    tr = _tr()
    days = pd.date_range("2018-07-01", "2020-06-30")
    rng = np.random.default_rng(4)
    y = (rng.random(len(days)) < 0.15).astype(float)
    base = np.clip(0.15 + 0.2 * rng.standard_normal(len(days)), 0.0, 1.0)
    good = np.clip(0.8 * y + 0.1, 0.0, 1.0)                   # far closer to y on every row
    once = base.copy()
    once[int(np.flatnonzero(y)[0])] = 1.0                     # better than base on one row only
    params = {"max": 0, "noisy_or": 0, "cofire": 1}
    pick = lambda arms: S.pick_union(y, arms, days, tr.blocks, params, n_boot=500)  # noqa: E731
    # lowest by one row: its CI reaches 0, so max stays (and ``lowest`` says who was lowest)
    p = pick({"max": base, "noisy_or": once, "cofire": base})
    assert p["rule"] == "max" and p["lowest"] == "noisy_or" and p["vs_default"]["noisy_or"]["verdict"] != "better"
    # a challenger that is better replaces it
    p = pick({"max": base, "noisy_or": base, "cofire": good})
    assert p["rule"] == "cofire" and p["vs_default"]["cofire"]["verdict"] == "better"
    # two better challengers apart by noise: the one with fewer parameters (noisy_or), though cofire is lower
    cof = good.copy()
    cof[int(np.flatnonzero(y)[0])] = 1.0
    p = pick({"max": base, "noisy_or": good, "cofire": cof})
    assert p["lowest"] == "cofire" and p["rule"] == "noisy_or", p["why"]
    # … and apart beyond noise: the lower one, parameters or not
    p = pick({"max": base, "noisy_or": np.clip(0.5 * good + 0.5 * base, 0, 1), "cofire": good})
    assert p["rule"] == "cofire", p["why"]
    raises(lambda: pick({"noisy_or": base, "cofire": base}), "including the default")
    raises(lambda: pick({"max": base, "noisy_or": base[:-1]}), "one set of rows")


# ── S3b and Holm ────────────────────────────────────────────────────────────

def test_s3b_is_the_paired_brier_difference_on_storm_blocks_with_holms_inputs():
    res, tr = _res(), _tr()
    o = res.oracle[res.oracle["tier"] == "T2"]
    assert len(o) == WEST_DAYS_T2 and set(o["unit"]) == {"ocean", "baker_china"}
    s = res.scores["s3b"]
    bs = lambda p: float(((o[p] - o["y"]) ** 2).mean())  # noqa: E731
    assert abs(s["delta"] - (bs(S.SHARE) - bs("constant"))) < 1e-12 and s["n"] == len(o)
    blk, _ = S.SB._storm_blocks(o["date"], tr.blocks)
    d = V.paired_delta(o["y"], o[S.SHARE], o["constant"], blk, n=N_BOOT, seed=0, level=0.9)
    assert s["ci90"] == [d["lo"], d["hi"]] and abs(s["p_one_sided"] - (1 - d["p_neg"])) < 1e-12
    assert s["ci95"][0] <= s["ci90"][0] and s["ci95"][1] >= s["ci90"][1]
    assert s["superior_unadjusted"] == (s["ci90"][1] < 0) and s["verdict"] == V.verdict(*s["ci90"])
    assert s["family"] == ["S3a", "S3b"] and s["alpha"] == 0.05 and s["n_blocks"] == len(np.unique(blk))
    # every oracle row: the true occurrence (Westside overflowed) times the share at the fold's v̂ — the fold head's
    # prediction from rain, never the filed volume (Part B 2), which it differs from on every measured day
    assert (o["identity"] == 1.0).all()
    filed = tr.basin[tr.basin["basin"] == "westside"].set_index("date")["volume_mg"].astype(float)
    for key, g in res.oracle.groupby(["tier", "fold"]):
        ff = res.fits[key]
        head = _s2().predict("westside", S.VHAT, ff.fold.seasons, g["date"])
        assert np.abs(g["v_hat"].to_numpy() - head).max() < 1e-9, key
        fv = filed.reindex(g["date"]).to_numpy()
        assert (np.abs(g["v_hat"].to_numpy() - fv)[np.isfinite(fv)] > 1e-6).all(), key
        for lid, z in (("westside>ocean", "ocean"), ("westside>baker_china", "baker_china")):
            gz = g[g["unit"] == z]
            c = ff.shares[lid][S.SHARE]
            want = 1 / (1 + np.exp(-(c["a"] + c["b"] * np.log1p(gz["v_hat"].to_numpy()))))
            assert np.abs(gz[S.SHARE].to_numpy() - want).max() < 1e-12, key
            assert (gz["constant"] == ff.shares[lid]["constant"]).all()


def test_holm_steps_down():
    h = S.holm({"S3a": 0.01, "S3b": 0.04})
    assert h["S3a"] == {"p": 0.01, "rank": 1, "level": 0.025, "reject": True}
    assert h["S3b"]["level"] == 0.05 and h["S3b"]["reject"]
    h = S.holm({"S3a": 0.03, "S3b": 0.001})
    assert h["S3b"]["level"] == 0.025 and h["S3b"]["reject"] and h["S3a"]["level"] == 0.05 and h["S3a"]["reject"]
    h = S.holm({"S3a": 0.06, "S3b": 0.001})
    assert h["S3b"]["reject"] and not h["S3a"]["reject"]
    h = S.holm({"S3a": 0.03, "S3b": 0.04})
    assert not h["S3a"]["reject"] and not h["S3b"]["reject"]          # 0.03 ≥ 0.025 stops the steps
    h = S.holm({"S3a": float("nan"), "S3b": 0.001})
    assert h["S3b"]["reject"] and not h["S3a"]["reject"]


# ── the spec ────────────────────────────────────────────────────────────────

def test_the_spec_loads_in_compose_v2_with_every_fold_and_arm():
    res = _res()
    spec = res.spec
    assert (spec["geography"], spec["pipeline"], spec["kind"]) == ("sfpuc4_v1", "stages_v1", S.KIND)
    assert spec["fit"]["protocol"] == S2.protocol_stamp() and spec["fit"]["as_of"] == str(AS_OF.date())
    with tempfile.TemporaryDirectory() as d:
        path = S.write(spec, out_dir=Path(d))
        loaded = C.load_s3_spec(path, GEO)
        assert loaded == json.loads(json.dumps(spec))
    for key in res.fits:
        for arm in S.ARMS:
            sp = S.fold_spec(spec, *key, arm=arm)
            assert sp == C.check_s3_spec(sp, GEO)
        assert S.fold_spec(spec, *key)["links"] == S.fold_spec_of(res.fits[key], GEO)["links"]
    # compose_v2.s3 on a week with the loaded spec: a Westside link is the basin p times its share at v̂
    days = pd.date_range("2025-12-01", periods=7)
    pb = pd.DataFrame(0.4, index=days, columns=list(GEO.keys))
    vb = pd.DataFrame(5.0, index=days, columns=list(GEO.keys))
    out = C.s3(GEO, loaded, pb, vb)
    c = loaded["links"]["westside>ocean"]["share"]["coef"]
    assert np.allclose(out.link_p["westside>ocean"], 0.4 / (1 + np.exp(-(c["a"] + c["b"] * np.log1p(5.0)))))
    assert np.allclose(out.link_v["westside>ocean"], 5.0 * loaded["links"]["westside>ocean"]["vol_share"])
    assert np.allclose(out.zone_p["north"], 0.4)
    assert len(json.dumps(spec)) < 400_000                               # compact scores: no CORP curves or threshold tables
    assert "curve" not in json.dumps(spec["fit"]["scores"])


def test_the_spec_saves_and_loads_through_stages_candidates():
    SC = S._saver()
    if SC is None:
        assert not (MODELS / "stages_candidates.py").exists(), "stages_candidates.py exists but the saver did not load"
        print("  (stages_candidates.save_component not present: the spec falls back to _s3_links/)")
        return
    res = _res()
    with tempfile.TemporaryDirectory() as d:
        path = S.write(res.spec, name="sfpuc4_test", root=d)
        saved = json.loads(path.read_text())
        assert (saved["set"], saved["component"], saved["geography"], saved["pipeline"]) == ("sfpuc4_test", S.KIND, "sfpuc4_v1", "stages_v1")
        st = SC.load_set("sfpuc4_test", root=d)
        assert st.components == {"s3": S.KIND}
        spec = S.load("sfpuc4_test", root=d)
        assert spec == json.loads(json.dumps(res.spec))
        raises(lambda: S.for_compose({k: v for k, v in saved.items() if k != "set"}), "lacks ['set']")


def test_writing_refuses_unprotocol_scores_and_paths_outside_stages_candidates():
    res = _res()
    raises(lambda: S.write(res.spec), "protocol's 2000")                 # N_BOOT = 200 never reaches the real directory
    raises(lambda: S.write(res.spec, out_dir=S.FORECAST / "data" / "models" / "candidates"), "stages_candidates/ only")
    raises(lambda: S.write(res.spec, out_dir=S.REPO / "app"), "stages_candidates/ only")        # nowhere else in the repo either
    raises(lambda: S.write(res.spec, root=S.FORECAST / "data" / "models"), "stages_candidates/ only")
    bad = json.loads(json.dumps(res.spec))
    bad["links"]["westside>ocean"]["outfalls"] = ["CSD-001"]
    raises(lambda: S.write(bad, out_dir=Path(tempfile.gettempdir())), "westside>ocean")


def test_bad_inputs_raise():
    s2, tr = _s2(), _tr()
    raises(lambda: s2.predict("westside", S.VHAT, {2016}, pd.date_range("2018-01-01", periods=3)), "floor")
    raises(lambda: s2.predict("north_shore", S.P, {2016, 2017}, pd.date_range("2018-01-01", periods=3)), "no p for 'north_shore'")
    raises(lambda: s2.predict("central", S.P, _final().fold.seasons, pd.date_range("2030-01-01", periods=2)), "frame lacks")
    raises(lambda: S.fit_table(dataclasses.replace(_final().fold, seasons=frozenset({2015})), s2, tr), "no Westside overflow day")
    raises(lambda: S.build_spec(GEO, {}, "size_blend", {}, 1.0, {}, "max"), "unknown share arm")
    raises(lambda: S.for_compose(json.loads(json.dumps(_res().spec))), "lacks")
    raises(lambda: S.fold_spec(_res().spec, "T2", "2030-31"), "holds 0 folds")


def test_the_committed_spec_is_current():
    """The committed development spec (stage candidate DEV_SET) holds what a fresh fit gives: links, union, co-firing
    shares and every fold's coefficients (scores aside: the tests' bootstrap is smaller)."""
    SC = S._saver()
    path = (S.CANDIDATES_ROOT / S.DEV_SET / S.SPEC_FILE) if SC is not None else (S.OUT_DIR / S.SPEC_FILE)
    if not path.exists():
        print(f"  (no committed {path.relative_to(ROOT)} yet)")
        return
    got = S.load() if SC is not None else C.load_s3_spec(path, GEO)
    want = json.loads(json.dumps(_res().spec))
    for k in ("geography", "pipeline", "kind", "links", "union", "cofire"):
        assert got[k] == want[k], k
    assert got["fit"]["folds"] == want["fit"]["folds"]
    assert got["fit"]["bootstrap"]["protocol"] is True


# ── the assemble step: the stage candidate's own S2 (Part B 2) ──────────────

CANDIDATE = "sfpuc4_shared8_v1"        # the A5 winner's stage candidate (stages_s2_sfpuc4's committed bake-off)
# as of AS_OF: the folds whose Westside head refit without one season reads under 20 known-volume events (35 in
# all through 2025-10-31; 2018-19 holds 7, 2022-23 holds 9), so those seasons' fit days are left out
DROPPED_AS_OF = {("T1-holdout", S2.FOLD_HOLDOUT): {"2018": 19, "2022": 17}, ("T2", "2018-19"): {"2022": 19},
                 ("T2", "2022-23"): {"2018": 19}}


@functools.lru_cache(maxsize=None)
def _cand():
    SC = S._saver()
    if SC is None or not (S.CANDIDATES_ROOT / CANDIDATE / "manifest.json").exists():
        return None
    return S.candidate_s2(CANDIDATE)


@functools.lru_cache(maxsize=None)
def _cres():
    return S.run(_cand(), _tr(), n_boot=N_BOOT, log=lambda *a: None)


def _results() -> dict:
    return json.loads((S.CANDIDATES_ROOT / "_bakeoff" / "results.json").read_text())


def test_the_candidates_s2_is_each_folds_own_design():
    """candidate_s2: each fold keeps the design its S2 was made with (T1 / T1-holdout the final pick's, a T2 season
    its outer fold's A5 pick, as results.json records them); refit on the fold's own seasons it is what the build
    scores (the saved finals and siblings exactly, the bake-off's T2 rows to its solver's 1e-5, re-derived here);
    an inner refit reads the fold's design and never the inner season or the fold's own; every weight ≥ 0."""
    c = _cand()
    if c is None:
        print(f"  (no stage candidate {CANDIDATE} yet)")
        return
    import stages_s2_sfpuc4 as S2C
    r = _results()
    fc = S2C.fold_choices(r)
    assert c.name == CANDIDATE and c.component == S._saver().load_set(CANDIDATE).components["s2"]
    for f in S.plan():
        assert c.for_fold(f).choice == fc[f.key], f.key
        fid = c.fidelity[f"{f.tier} {f.fold}"]
        tol = S.P_FIDELITY_TOL if f.tier == "T2" else S.EXACT_TOL
        assert fid["p_max_abs"] <= tol and fid["v_hat_max_rel"] <= (S.V_FIDELITY_RTOL if f.tier == "T2" else S.EXACT_TOL), (f.key, fid)
    assert {c.choices[k].contender for k in c.choices if k[0] == "T2"} == set(r["nested"]["picked_by_fold"].values())
    # the bake-off's T2 rows of one fold, re-derived here: p to the C-path solver's 1e-5, v̂ to 6 significant digits
    rows = SB.bakeoff_rows(S.CANDIDATES_ROOT / "_bakeoff" / "rows.csv.gz", GEO)
    f = next(x for x in S.plan() if x.key == ("T2", "2021-22"))
    g = rows[(rows["arm"] == "procedure") & (rows["fold"] == "2021-22") & (rows["basin"] == "central")].sort_values("date")
    fs = c.for_fold(f)
    assert np.abs(fs.predict("central", S.P, f.seasons, g["date"]) - g["p"].to_numpy()).max() < 1e-5
    v = fs.predict("westside", S.VHAT, f.seasons, g["date"])
    gw = rows[(rows["arm"] == "procedure") & (rows["fold"] == "2021-22") & (rows["basin"] == "westside")].sort_values("date")
    assert np.allclose(v, gw["v_hat"].to_numpy(), rtol=1e-5)
    # an inner refit: the fold's design (contender, bends, C), weights ≥ 0, and it never reads the inner season
    # or the fold's own
    inner = f.seasons - {2018}
    m = fs.fitted("central", S.P, inner)
    assert (m["contender"], m["bends"], m["C"]) == (fc[f.key].contender, fc[f.key].knots, fc[f.key].C["central"])
    S._saver().check_nonnegative(m["model"], "inner refit")
    seen_seasons = set(T4.wet_season(pd.Series(fs.seen("central", S.P, inner))).astype(int))
    assert seen_seasons <= set(inner) and not seen_seasons & {2018, 2021}
    assert fs.n_fit("westside", S.VHAT, inner) == int(S._s2c()._vol_rows(c.data, "westside", seasons=inner).sum())
    raises(lambda: c.for_fold(dataclasses.replace(f, fold="2030-31")), "no S2 design")


def test_seasons_whose_inner_head_is_under_the_floor_are_not_fit_on():
    """fit_table: a season whose inner Westside head would read under 20 known-volume events has no out-of-sample v̂
    (no declared Westside fallback, Part B 7), so its fit days are left out and recorded; the candidate's folds do
    this exactly where n_fit says (as of AS_OF: DROPPED_AS_OF); no other season is touched; a fold left with no fit
    day raises. The development stand-in drops nothing."""
    assert not any(ff.dropped for ff in _res().fits.values())
    tr = _tr()
    s2 = _s2()

    class Floored:                                   # the dev S2 with one season's inner head under the floor
        name = s2.name

        def n_fit(self, basin, what, seasons):
            return 0 if 2018 not in seasons else s2.n_fit(basin, what, seasons)

        def predict(self, *a):
            return s2.predict(*a)

    fold = _final().fold
    full, dropped = S.fit_table(fold, s2, tr), {}
    cut = S.fit_table(fold, Floored(), tr, dropped)
    assert set(dropped) == {"2018"} and dropped["2018"]["n_rows"] == int((full["season"] == 2018).sum()) > 0
    assert 2018 not in set(cut["season"]) and len(cut) == len(full) - dropped["2018"]["n_rows"]
    assert cut.set_index(["link", "date"])["v_hat"].equals(full[full["season"] != 2018].set_index(["link", "date"])["v_hat"])

    class AllFloored(Floored):
        def n_fit(self, basin, what, seasons):
            return 0

    raises(lambda: S.fit_table(fold, AllFloored(), tr, {}), "under the floor")
    if _cand() is None:
        return
    res = _cres()
    for key, ff in res.fits.items():
        fs = S.fold_s2(_cand(), ff.fold)
        with_rows = set(ff.table["season"].astype(int)) | {int(s) for s in ff.dropped}
        n = {s: fs.n_fit("westside", S.VHAT, ff.fold.seasons - {s}) for s in with_rows}
        want = {str(s): k for s, k in n.items() if k < S.HEAD_MIN_EVENTS}
        assert {s: d["n_events"] for s, d in ff.dropped.items()} == want == DROPPED_AS_OF.get(key, {}), key
        assert not set(ff.table["season"].astype(str)) & set(ff.dropped), key
        block = next(b for b in res.spec["fit"]["folds"] if (b["tier"], b["fold"]) == key)
        assert block.get("fit_days_dropped", {}) == ff.dropped, key


def test_the_candidates_committed_spec_is_current():
    """The candidate's s3_links.json (stages_candidates.assemble) holds what a fresh fit on its own S2 gives, and
    says so (sources.s2.name: the candidate itself, never the development stand-in)."""
    c = _cand()
    if c is None or S._saver().load_set(CANDIDATE).s3_links is None:
        print(f"  (no s3_links.json in {CANDIDATE} yet)")
        return
    got = S.load(CANDIDATE)
    want = json.loads(json.dumps(_cres().spec))
    for k in ("geography", "pipeline", "kind", "links", "union", "cofire"):
        assert got[k] == want[k], k
    assert got["fit"]["folds"] == want["fit"]["folds"]
    assert got["sources"]["s2"]["name"] == CANDIDATE and not got["sources"]["s2"]["name"].startswith("dev:")
    assert got["fit"]["bootstrap"]["protocol"] is True


# ── today's split on the city map (sfpuc4_shared8_v2) ──────────────────────

SPLIT_SET = "sfpuc4_shared8_v2"        # the stage candidate that takes it (stages_candidates.assemble_served_parts)


def test_a_recorded_size_blend_share_is_taken_as_recorded():
    blend = {"kind": "size_blend", "large": 1.0, "small": 0.46, "median_mg": 3.7, "n_basin_days": 51}
    links = {lk.id: ({"share": {"kind": "identity"}, "vol_share": 1.0} if lk.identity else
                     {"share": dict(blend), "constant": 0.7, "vol_share": 1.0}) for lk in GEO.links}
    rec = {"tier": "T1", "fold": "final", "links": links, "basin_median_mg": {"westside": 4.0}, "cofire": {},
           "union": {"east": {"rule": "max"}}}
    spec = {"geography": GEO.version, "kind": S.SPLIT_KIND, "fit": {"folds": [rec]}}
    a, c = S.fold_spec(spec, "T1", "final"), S.fold_spec(spec, "T1", "final", arm="constant")
    assert a["kind"] == S.SPLIT_KIND and c["kind"] == f"{S.SPLIT_KIND}_benchmark_constant"
    assert all(a["links"][lid]["share"] == blend and c["links"][lid]["share"] == {"kind": "constant", "p": 0.7}
               for lid in S.SPLIT_GROUPS)
    # compose_v2 applies it as the served split does: p_basin · stage2.group_share(v̂)
    days = pd.date_range("2024-01-01", periods=3)
    v = pd.DataFrame(np.repeat([[1.0], [3.7], [20.0]], len(GEO.keys), axis=1), index=days, columns=list(GEO.keys))
    zp = C.s3(GEO, a, pd.DataFrame(0.5, index=days, columns=list(GEO.keys)), v).zone_p["ocean"]
    served = {"shares": {"Ocean Beach": {"large": {"p": 1.0}, "small": {"p": 0.46}}}, "median_event_volume_mg": {"Ocean Beach": 3.7}}
    assert np.allclose(zp.to_numpy(), [0.5 * STG2.group_share(served, "Ocean Beach", x) for x in (1.0, 3.7, 20.0)], atol=1e-15)


def test_todays_split_reads_the_served_groups_as_the_city_links():
    pairs = S.split_pairs(GEO)
    g1 = G.get("geo_v1")
    assert set(pairs) == {lk.id for lk in S.split_links(GEO)} == set(S.SPLIT_GROUPS)
    for lid, l1 in pairs.items():
        a, b = next(x for x in GEO.links if x.id == lid), next(x for x in g1.links if x.id == l1)
        assert b.legacy_group == S.SPLIT_GROUPS[lid] and set(a.outfalls) == set(b.outfalls) and (a.basin, a.zone) == (b.basin, b.zone)
    old = dict(S.SPLIT_GROUPS)
    try:
        S.SPLIT_GROUPS.update({"westside>ocean": "Baker-China", "westside>baker_china": "Ocean Beach"})
        raises(lambda: S.split_pairs(GEO), "cannot be the link's")
    finally:
        S.SPLIT_GROUPS.clear()
        S.SPLIT_GROUPS.update(old)


def test_the_split_candidates_committed_spec():
    SC = S._saver()
    if SC is None or not (S.CANDIDATES_ROOT / SPLIT_SET / "manifest.json").exists():
        print(f"  (no stage candidate {SPLIT_SET} yet)")
        return
    got, base = SC.load_set(SPLIT_SET).s3_links, SC.load_set(CANDIDATE).s3_links
    assert got["kind"] == got["component"] == S.SPLIT_KIND
    assert got["sources"]["s2"]["name"] == SPLIT_SET and got["sources"]["s2"]["records_from"] == CANDIDATE
    keep = ("tier", "fold", "scores", "train_seasons", "basin_median_mg", "cofire", "union")
    assert [(r["tier"], r["fold"]) for r in got["fit"]["folds"]] == [(r["tier"], r["fold"]) for r in base["fit"]["folds"]]
    for a, b in zip(got["fit"]["folds"], base["fit"]["folds"]):
        assert {k: a[k] for k in keep} == {k: b[k] for k in keep}, a["fold"]
        for lid, v in a["links"].items():
            if lid in S.SPLIT_GROUPS:
                assert v["share"]["kind"] == "size_blend" and v["vol_share"] == 1.0 and v["constant"] == b["links"][lid]["constant"]
            else:
                assert v == b["links"][lid], (a["fold"], lid)
    pairs = S.split_pairs(GEO)
    served = C.geo_v1_adapter_specs()["s3"]["links"]                     # T1: the served stage2.json itself
    assert all(got["links"][lid]["share"] == served[g]["share"] for lid, g in pairs.items())
    bundle = SB.load_set("served")                                       # T1-holdout: the served recipe refit before 2023-07-01
    need = sorted(set(bundle.s2.sources) | set(bundle.chosen.values()) | {"avg"})
    train = T4.build_dataset(sources=need)[0]
    fitted = S2.fit(bundle.name, "served", ("T1-holdout",), train_frames=train)
    plan = {(p[0], p[1]): p for p in S2._plan(("T1-holdout",))}
    refit = S.served_split(bundle, fitted.folds, plan, train, T4.load_events(), T4.load_samples())[("T1-holdout", S2.FOLD_HOLDOUT)]
    rec = next(r for r in got["fit"]["folds"] if r["tier"] == "T1-holdout")
    assert all(rec["links"][lid]["share"] == refit["shares"][lid] for lid in pairs) and rec["share_fit_span"] == refit["fit"]["fit_span"]
    assert pd.Timestamp(rec["share_fit_span"][1]) < S2.HOLDOUT_START


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
