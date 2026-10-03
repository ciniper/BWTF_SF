"""stages_s2_sfpuc4 (P8a; STAGES_DESIGN.md Part A A5, Part B 1, 7, 13; STAGES_PROTOCOL.md stages_v3 §2, §8):
the stage 2 term-set bake-off on the city's four basins.

On committed data (as of the data end 2026-08-17) and a small slice of the grid:
  - the declared grid: A5's four contenders with 38 / 19 / 8 / 9 terms in every bend option, the
    recorded bends equal to leaderboard's, the hinge transform equal to leaderboard.add_hinges;
  - the fast solver is leaderboard.NonNegLogit's objective (p within 1e-5 of the pipeline's);
  - rows and labels: the served rain sources mapped to the city's basins (South → the served
    'southeast' basin's SF Downtown), the archive never a label, labels equal to truth.py's;
  - every weight ≥ 0: the bake-off's fits, the finals (South pooled included), the volume heads;
  - no fold sees its season, inner or outer, and check_out_of_fold / check_fit_spans catch a leak;
  - bends, C and the South mode are chosen inside the folds: flipping the held-out season's labels
    changes no fit an inner fold or the outer refit makes (South's pooled variant included), so no
    choice made for it moves;
  - the procedure is nested end to end: every outer fold's recorded choices are choose() on its inner
    folds, its pick is A5 on their rows, its rows are the pick refit without the season, and none of
    it moves when that season's labels flip;
  - protocol §8's S2 primary and the South floor read the right rows and arms (the procedure's and
    the winner's against the served recipe's), with the rule's own inequality;
  - A5's tie rule on synthetic arms, with storm-block (not row) CIs;
  - the declared volume fallback triggers under 20 known-volume events, and a basin with no
    declared fallback raises; heads fit measured volumes only; §6 change 7's recipe rule;
  - the winner round-trips through stages_candidates;
  - fit_final refits a design on any season set (on every season, the finals exactly) and basins, and
    fold_choices reads each fold's own design from results.json (the assemble step's S2 per fold);
  - the committed _bakeoff results agree with the code (grid sha, every contender scored, the
    candidate's pickles reproduce the T1 rows, the procedure's rows are the picked contenders',
    the final pick re-derived from the development rows, the primary's arms re-derived from the
    rows, the volume rule, the caveats stated, no set left from an older bake-off).
Run: venv/bin/python tests/test_stages_s2_sfpuc4.py
"""
from __future__ import annotations

import dataclasses
import functools
import gzip
import json
import shutil
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

import leaderboard as L  # noqa: E402
import stages_candidates as SC  # noqa: E402
import stages_s2 as S2  # noqa: E402
import stages_s2_sfpuc4 as M  # noqa: E402
import train_v4 as T  # noqa: E402
import truth as TR  # noqa: E402
from shared import geography as G  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")          # the committed data's end: every count below is as of this day
SMALL = M.Grid(contenders=("four", "shared8"), knots=("recorded", "low"), Cs=(0.1, 1.0),
               seasons=(2017, 2018, 2021, 2022, 2023))   # Westside keeps ≥ 20 known-volume events in every fold
QUIET = lambda *a, **k: None  # noqa: E731


@functools.lru_cache(maxsize=None)
def _data() -> M.Data:
    return M.load_data(AS_OF)


@functools.lru_cache(maxsize=None)
def _run() -> dict:
    return M.run(grid=SMALL, data=_data(), n_boot=200, log=QUIET)


FLIP = 2022                                 # the outer season whose labels the leak tests flip


@functools.lru_cache(maxsize=None)
def _flipped() -> tuple:
    """(data, store) with every known label of season FLIP flipped, the store fit on SMALL as _run's is."""
    data = _data()
    flipped = {}
    for k, b in data.basins.items():
        y = b.y.copy()
        m = (data.season == FLIP) & b.known
        y[m] = 1 - y[m]
        flipped[k] = dataclasses.replace(b, y=y)
    data2 = dataclasses.replace(data, basins=flipped)
    return data2, M.fit_store(data2, SMALL, log=QUIET)


def _raises(fn) -> str:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return str(e)
    raise AssertionError(f"{fn} did not raise")


def test_the_declared_grid_is_a5s():
    assert M.CONTENDERS == ("logit_v1", "half", "shared8", "four")
    assert M.check_grid_terms() == {"logit_v1": 38, "half": 19, "shared8": 8, "four": 9}
    assert M.KNOT_GRID["logit_v1"]["recorded"] == {f: tuple(k) for f, k in L.HINGES.items()}
    for c in ("half", "shared8", "four"):
        assert M.KNOT_GRID[c]["recorded"] == L.SHARED_DESIGNS[c], c
    assert list(M.C_GRID) == sorted(M.C_GRID) and M.KNOT_OPTIONS[0] == "recorded" and M.SOUTH_MODES[0] == "standalone"
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.gamma(0.4, 0.6, size=(300, len(L.FEATS))), columns=L.FEATS)
    assert np.array_equal(M.add_hinges_at(X, L.HINGES), L.add_hinges(X))
    assert np.array_equal(M.add_hinges_at(X.values, L.HINGES), L.add_hinges(X.values))
    y = (X["precip_avg"] > 0.4).astype(int)
    for c in M.CONTENDERS:
        for o in M.KNOT_OPTIONS:
            assert M.design_matrix(c, o, X).shape == (300, M.n_terms(c)), (c, o)
            m = M.make_pipeline(c, o, 1.0).fit(X, y)
            assert np.allclose(m[:-1].transform(X), (M.design_matrix(c, o, X) - m.named_steps["scale"].mean_)
                               / m.named_steps["scale"].scale_)
    d = M.Grid().declared()
    assert json.loads(json.dumps(d)) == d and d["C_grid"] == list(M.C_GRID) and len(d["seasons"]) == 9
    assert M._sha_json(d) == M._sha_json(M.Grid().declared())
    assert "fewer terms" in d["rule"]
    for bad in (M.Grid(contenders=("five",)), M.Grid(Cs=(1.0, 0.1)), M.Grid(seasons=(2016, 2017)),
                M.Grid(south_modes=("pooled",)), M.Grid(knots=("middle",))):
        _raises(bad.check)


def test_the_fast_solver_is_nonneglogit():
    data = _data()
    b = data.basins["central"]
    m = M.train_mask(data, "central", seasons=[2016, 2018, 2022])
    for c in ("four", "logit_v1"):
        Xd = M.design_matrix(c, "recorded", b.F)
        mu, sd = M._scale(Xd[m])
        th = M._nonneg_path((Xd[m] - mu) / sd, b.y[m], (0.1, 1.0))
        for i, C in enumerate((0.1, 1.0)):
            ref = M.make_pipeline(c, "recorded", C).fit(b.F[m], b.y[m].astype(int))
            got = 1 / (1 + np.exp(-(((Xd - mu) / sd) @ th[i, :-1] + th[i, -1])))
            gap = np.abs(got - ref.predict_proba(b.F)[:, 1]).max()
            assert gap < 1e-5, (c, C, gap)


def test_rows_labels_and_rain_sources_as_of_the_data_end():
    data = _data()
    assert data.keys == G.get("sfpuc4_v1").keys == ("westside", "north_shore", "central", "south")
    assert data.sources == {"westside": "avg", "north_shore": "SF Downtown", "central": "SF Downtown", "south": "SF Downtown"}
    assert data.source_rule["south"]["served_key"] == "southeast" and data.source_rule["south"]["outfalls_were_in"] == ["southeast"]
    assert data.source_rule["central"]["served_key"] == "central"
    assert data.source_rule["central"]["outfalls_were_in"] == ["central", "southeast"]      # Islais came from GEO_V1 'southeast'
    events = {k: int(np.nansum(b.y)) for k, b in data.basins.items()}
    assert events == {"westside": 65, "north_shore": 42, "central": 97, "south": 23}, events   # design §2.3, as of AS_OF
    assert M.first_scored_season(data, "westside") == 2017 and M.first_scored_season(data, "south") == 2016
    known = TR.ledger_known("sfpuc4_v1", end=AS_OF)
    for k, b in data.basins.items():
        kn = known[known["basin"] == k].set_index("date").reindex(data.days)
        assert np.array_equal(b.known, kn["known"].to_numpy(dtype=bool)), k
        assert not (b.known & kn["archive"].to_numpy(dtype=bool)).any(), k    # the feed archive is never a label
        assert not (b.scored & ~b.known).any(), k                            # a scored day is a known day
        assert np.isfinite(b.y[b.known]).all() and np.isnan(b.y[~b.known]).all()
        assert (np.isfinite(b.vol) <= (b.y == 1)).all(), k                    # volumes on event days only
    vol = {k: int((np.isfinite(b.vol) & (data.days <= M.TRAIN_END)).sum()) for k, b in data.basins.items()}
    assert vol["south"] == 20 and min(vol.values()) == 20, vol                # South sits on the 20-event floor


def test_every_weight_is_nonnegative():
    out = _run()
    for k, m in out["finals"].items():
        assert (m["model"].named_steps["lr"].coef_ >= 0).all(), k
        assert all(v >= 0 for v in m["slope_per_unit"].values()), k
    for k, m in out["holdouts"].items():
        assert (m["model"].named_steps["lr"].coef_ >= 0).all(), k
    for heads in (out["heads"], out["holdout_heads"]):
        for k, h in heads.items():
            SC.check_nonnegative(h["model"], k)
    for coef in out["store"].pooled_coef.values():
        for ab in coef.values():
            assert ((ab[:, 1] >= 0) & (ab[:, 1] <= M.POOLED_SLOPE_MAX)).all()
    rng = np.random.default_rng(3)
    Z = rng.normal(size=(4000, 3))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(Z @ np.array([1.5, -1.0, 0.5]) - 2)))).astype(float)
    th = M._nonneg_path(Z, y, (0.1, 1.0))
    assert (th[:, :3] >= 0).all() and np.allclose(th[:, 1], 0.0) and (th[:, 0] > 0.5).all()


def test_no_fold_sees_its_season():
    pl = M.plan(SMALL.seasons)
    assert len(pl) == len(SMALL.seasons) ** 2                    # 5 nine-season-style folds + 5 × 4 inner folds
    data = _data()
    for (o, s), tr in pl.items():
        assert s not in tr and o not in tr and len(tr) == len(SMALL.seasons) - (1 if o is None else 2)
        for k in data.keys:
            m = M.train_mask(data, k, seasons=tr)
            assert set(np.unique(data.season[m])) <= set(tr) and (data.days[m] <= M.TRAIN_END).all()
    out = _run()
    rows, store = out["rows"], out["store"]
    M.check_out_of_fold(rows, store, data)
    t2 = rows[rows["tier"] == "T2"]
    assert set(t2["fold"]) == {"2017-18", "2018-19", "2021-22", "2022-23", "2023-24"}
    assert set(rows["tier"]) == {"T2", "T1", "T1-holdout"}
    bad = rows.copy()
    i = bad.index[(bad["tier"] == "T2") & (bad["fold"] == "2021-22")][0]
    bad.loc[i, "fold"] = "2022-23"                               # a 2021-22 day scored by the fold that trained on it
    assert "outside its season" in _raises(lambda: M.check_out_of_fold(bad, store, data))
    bad = rows.copy()
    bad.loc[bad.index[bad["tier"] == "T2"][0], "fold"] = "2019-20"
    assert "never fit" in _raises(lambda: M.check_out_of_fold(bad, store, data))
    bad = rows.copy()
    bad.loc[bad.index[bad["tier"] == "T1"][0], "date"] = M.TRAIN_END
    assert "training end" in _raises(lambda: M.check_out_of_fold(bad, store, data))
    # the finals, siblings and heads behind the T1 / T1-holdout rows: fit spans end before the days they score
    M.check_fit_spans({"w": out["finals"], "h": out["heads"]}, {"w": out["holdouts"], "h": out["holdout_heads"]})
    late = {"w": {"central": {**out["finals"]["central"], "span": ["2016-10-01", "2025-11-30"]}}}
    assert "X-ALL-INSAMPLE" in _raises(lambda: M.check_fit_spans(late, {}))
    late = {"h": {"south": {**out["holdout_heads"]["south"], "span": ["2016-10-01", "2023-07-01"]}}}
    assert "X-ALL-INSAMPLE" in _raises(lambda: M.check_fit_spans({}, late))
    assert all(h["fit_seasons"] is None and pd.Timestamp(h["span"][1]) <= M.TRAIN_END for h in out["heads"].values())


def test_choices_are_made_inside_the_folds():
    """Flip every label of the outer season 2022-23: no fit an inner fold of 2022-23 makes reads it, so every
    contender's bends, C and South mode for that fold, and the A5 pick, stay the same."""
    data, o = _data(), FLIP
    data2, s2 = _flipped()
    s1 = _run()["store"]
    terms = {c: M.n_terms(c) for c in SMALL.contenders}
    picks = []
    for d, s in ((data, s1), (data2, s2)):
        ch = {c: M.choose(d, s, o, c) for c in SMALL.contenders}
        rows = M.inner_rows(d, s, o)
        assert o not in set(rows["season"])
        arms = {c: M.inner_p(s, rows, ch[c]) for c in SMALL.contenders}
        picks.append((ch, M.a5_pick(rows["y"].to_numpy(), arms, rows["date"], d.blocks, terms, 200)["winner"]))
    (a, wa), (b, wb) = picks
    assert wa == wb
    for c in SMALL.contenders:
        assert (a[c].knots, a[c].C, a[c].south) == (b[c].knots, b[c].C, b[c].south), c
        assert abs(a[c].brier - b[c].brier) < 1e-12
    # every fit the choice reads — each inner fold (2022, s), every basin, contender, bends and C, South's pooled
    # variant (Central's weights of the same fold, South's intercept and slope) — and the outer refit (None, 2022)
    folds = [(o, s) for s in SMALL.seasons if s != o] + [(None, o)]
    for key in s1.p:
        for f in folds:
            assert np.array_equal(s1.p[key][f], s2.p[key][f]), (key, f)
    assert s1.pool and set(s1.pool) == set(s2.pool)
    for key in s1.pool:
        for f in folds:
            assert np.array_equal(s1.pool[key][f], s2.pool[key][f]), ("pooled", key, f)
            assert np.array_equal(s1.pooled_coef[key][f], s2.pooled_coef[key][f]), ("pooled coef", key, f)
    moved = {(key, f) for key in s1.p for f in s1.p[key] if not np.array_equal(s1.p[key][f], s2.p[key][f])}
    assert moved == {(key, f) for key in s1.p for f in s1.p[key] if o not in f}   # exactly the fits that train on 2022-23


def test_the_procedure_is_nested():
    """End to end through nested() and run(): each outer fold's recorded choice per contender is choose() on that
    fold's inner folds, its pick is A5 on their inner rows, and the procedure's rows for the season are the pick's
    fold (None, season) predictions; with the season's labels flipped, none of it moves (a choice that read the
    held-out season, e.g. the nine-season one, would)."""
    data, out = _data(), _run()
    store, rows = out["store"], out["rows"]
    terms = {c: M.n_terms(c) for c in SMALL.contenders}
    folds = out["results"]["nested"]["folds"]
    assert [f["season"] for f in folds] == [S2.season_label(s) for s in SMALL.seasons]
    for f in folds:
        o = S2._season_of(f["season"])
        ch = {c: M.choose(data, store, o, c) for c in SMALL.contenders}
        for c in SMALL.contenders:
            rec = f["inner"][c]
            assert (rec["bends"], rec["C"], rec["south"]) == (ch[c].knots, ch[c].C, ch[c].south), (f["season"], c)
            assert abs(rec["inner_brier"] - ch[c].brier) < 1e-15, (f["season"], c)
        inner = M.inner_rows(data, store, o)
        assert o not in set(inner["season"])
        arms = {c: M.inner_p(store, inner, ch[c]) for c in SMALL.contenders}
        pick = M.a5_pick(inner["y"].to_numpy(), arms, inner["date"], data.blocks, terms, 200)
        w = pick["winner"]
        assert f["picked"] == w and f["rule"]["n"] == len(inner) == pick["n"], f["season"]   # the inner rows, not all
        assert [(x["contender"], x["brier"]) for x in f["rule"]["ranking"]] == [(x["contender"], x["brier"]) for x in pick["ranking"]]
        assert f["rule"]["delta"]["lo"] == pick["delta"]["lo"] and f["rule"]["delta"]["hi"] == pick["delta"]["hi"]
        g = rows[(rows["arm"] == "procedure") & (rows["fold"] == f["season"])]
        assert set(g["contender"]) == {w} and len(g) == len(data.keys) * len(store.days_of[o])
        for k in data.keys:
            assert np.array_equal(g[g["unit"] == k]["p"].to_numpy(), M._pred(store, ch[w], k, (None, o))), (f["season"], k)
    # the held-out season's labels flipped: its fold record and its procedure rows stay exactly as they were
    data2, store2 = _flipped()
    nest2 = M.nested(data2, store2, 200, log=QUIET)
    label = S2.season_label(FLIP)
    a = next(f for f in folds if f["season"] == label)
    b = next(f for f in nest2["folds"] if f["season"] == label)
    assert a["picked"] == b["picked"] and a["choice"]["C"] == b["choice"]["C"] and a["choice"]["bends"] == b["choice"]["bends"]
    pa = rows[(rows["arm"] == "procedure") & (rows["fold"] == label)]["p"].to_numpy()
    pb = nest2["procedure"][nest2["procedure"]["fold"] == label]["p"].to_numpy()
    assert np.array_equal(pa, pb)
    moved = [f["season"] for f, f2 in zip(folds, nest2["folds"]) if f["season"] != label and
             not np.array_equal(rows[(rows["arm"] == "procedure") & (rows["fold"] == f["season"])]["p"].to_numpy(),
                                nest2["procedure"][nest2["procedure"]["fold"] == f["season"]]["p"].to_numpy())]
    assert moved, "the flip moved no other season's rows: the check above could not have failed"


def test_the_s2_primary_and_the_south_floor_read_the_right_rows():
    """Protocol §8's S2 primary: T2 = the procedure's rows − the served recipe's leave-one-season-out rows
    (superior when the CI's upper bound is below 0), T1 = the winner's finals − the served recipe's refit
    (non-inferior when it is below 5% of the served recipe's Brier score); the South floor on the procedure's
    South rows (the CI's lower bound above 0). Each arm's Brier score re-derived from the rows."""
    out, data = _run(), _data()
    r, rows, win = out["results"], out["rows"], out["winner"]

    def bs(arm, tier):
        g = rows[(rows["arm"] == arm) & (rows["tier"] == tier) & (rows["excl"] == "")]
        return float(np.mean((g["p"] - g["y"]) ** 2)), len(g)
    pr = r["s2_primary"]
    for window, arm_a, arm_b in (("T2", "procedure", "served_recipe"), ("T1", f"t1:{win}", "t1:served_recipe")):
        d = pr[window]["delta"]
        (a, na), (b, nb) = bs(arm_a, window), bs(arm_b, window)
        assert abs(d["a"] - a) < 1e-12 and abs(d["b"] - b) < 1e-12 and d["n"] == na == nb, window
        assert abs(d["delta"] - (a - b)) < 1e-12, window
    assert pr["T2"]["superior"] == (pr["T2"]["delta"]["hi"] < 0)
    assert pr["T1"]["noninferior_5pct"] == (pr["T1"]["delta"]["hi"] < M.NONINFERIOR_MARGIN * pr["T1"]["delta"]["b"])
    assert r["T1"]["check"]["noninferior_5pct"] == pr["T1"]["noninferior_5pct"]
    assert r["T1"]["check"]["delta"] == r["T1"]["vs_served_recipe"][win]["pooled"] == pr["T1"]["delta"]
    assert pr["passes"] == (pr["T2"]["superior"] and pr["T1"]["noninferior_5pct"])
    sf, south = r["south_floor"], M.score(rows[rows["arm"] == "procedure"], data, 200)["south"]
    assert sf["bss"] == south["bss"] and sf["ci"] == south["ci"]["bss"] and sf["n"] == south["n"]
    assert sf["pass"] == (south["ci"]["bss"][0] > 0)
    assert r["nested"]["caveat"] == M.T2_CAVEAT and r["T1"]["caveat"] == M.T1_CAVEAT
    assert {c: v["provenance"] for c, v in r["contenders"].items()} == {c: M.TERM_SET_PROVENANCE[c] for c in SMALL.contenders}
    edges = r["development"]["grid_edges"][win]
    assert edges["n"] == 1 and sum(edges["bends"].values()) == 1


def _blocks(dates, per=7) -> pd.DataFrame:
    d = pd.DatetimeIndex(dates)
    return pd.DataFrame({"block": np.arange(len(d)) // per, "block_kind": "storm"}, index=d)


def test_the_tie_rule():
    rng = np.random.default_rng(0)
    n = 2000
    dates = pd.date_range("2020-01-01", periods=n)
    blocks = _blocks(dates)
    y = (rng.random(n) < 0.1).astype(float)
    base = np.clip(0.1 + 0.6 * y + rng.normal(0, 0.05, n), 0.001, 0.999)
    terms = {"logit_v1": 38, "half": 19, "shared8": 8, "four": 9}
    a, b = (np.clip(base + rng.normal(0, 0.05, n), 0.001, 0.999) for _ in range(2))         # equally good arms
    a, b = sorted((a, b), key=lambda p: np.mean((p - y) ** 2))
    near = {"four": a, "shared8": b}                              # the 9-term one a hair lower
    r = M.a5_pick(y, near, dates, blocks, terms, 500)
    assert r["lowest"] == "four" and r["next"] == "shared8" and r["ci_includes_0"] and r["winner"] == "shared8"
    assert r["delta"]["lo"] <= 0 <= r["delta"]["hi"] and "fewer terms" in r["why"]
    far = {"logit_v1": base, "four": np.full(n, 0.1)}              # far worse, fewer terms: the lowest wins
    r = M.a5_pick(y, far, dates, blocks, terms, 500)
    assert r["lowest"] == "logit_v1" and not r["ci_includes_0"] and r["winner"] == "logit_v1"
    assert r["delta"]["hi"] < 0 and set(r["vs_lowest"]) == {"four"}
    three = {"logit_v1": base, "half": np.clip(base + 0.002, 0, 1), "four": np.full(n, 0.1)}
    r = M.a5_pick(y, three, dates, blocks, terms, 500)
    assert r["next"] == "half" and r["winner"] in ("half", "logit_v1")   # only the next contender is compared
    assert r["winner"] == ("half" if r["ci_includes_0"] else "logit_v1")
    one = M.a5_pick(y, {"four": base}, dates, blocks, terms, 500)
    assert one["winner"] == "four" and one["next"] is None
    r = M.a5_pick(y, near, dates, _blocks(dates, per=n), terms, 500)   # one block: no CI can be drawn → a tie
    assert r["ci_includes_0"] and r["winner"] == "shared8"
    _raises(lambda: M.a5_pick(np.array([]), {}, [], blocks, terms, 10))
    # storm blocks, not rows: 40 blocks of 50 days, the 9-term arm better by 1e-4 on average with a block-to-block
    # spread of 1e-3. Resampling rows would see 2,000 independent days and a CI excluding 0; resampling the
    # blocks sees 40 and a CI including it, so the 8-term arm wins
    k, per = 40, 50
    z = rng.normal(size=k)
    d = -1e-4 + 1e-3 * (z - z.mean()) / z.std()
    dd = pd.date_range("2018-01-01", periods=k * per)
    y0, pb = np.zeros(k * per), np.full(k * per, 0.1)
    pa = np.sqrt(0.01 + np.repeat(d, per))
    blocked = M.a5_pick(y0, {"four": pa, "shared8": pb}, dd, _blocks(dd, per=per), terms, 2000)
    assert blocked["lowest"] == "four" and blocked["ci_includes_0"] and blocked["winner"] == "shared8"
    rowwise = M.a5_pick(y0, {"four": pa, "shared8": pb}, dd, _blocks(dd, per=1), terms, 2000)
    assert not rowwise["ci_includes_0"] and rowwise["winner"] == "four"           # what a row-level CI would decide


def test_the_volume_fallback_triggers_below_20_events():
    data = _data()
    served = M.served_head_recipe()
    no2018 = [s for s in M.T2_SEASONS if s != 2018]
    h = M.fit_head(data, "south", "gbr", served, seasons=no2018)        # South: 15 known-volume events
    assert h["n_events"] < 20 and h["kind"] == M.FALLBACK_KIND and h["recipe"] == "gbr"
    assert h["pooled_basins"] == ["central", "north_shore", "south"] and h["pooled_events"] > 60
    pooled_days = np.concatenate([data.days[M._vol_rows(data, k, seasons=no2018)] for k in h["pooled_basins"]])
    assert h["span"] == [str(pd.Timestamp(pooled_days.min()).date()), str(pd.Timestamp(pooled_days.max()).date())]
    assert h["own_span"][1] <= h["span"][1]                           # the span is every pooled basin's, not South's alone
    SC.check_nonnegative(h["model"], "fallback")
    F = data.basins["south"].F
    assert np.isfinite(M.head_log1p(h, F[h["features"]])).all()
    own = M.fit_head(data, "south", "loglinear", served)                # 20 events through 2025-10-31: its own head
    assert own["n_events"] == 20 and own["kind"] == "loglinear"
    sib = M.fit_head(data, "south", "loglinear", served, before=M.HOLDOUT_START)
    assert sib["kind"] == M.FALLBACK_KIND and sib["n_events"] < 20
    msg = _raises(lambda: M.fit_head(data, "westside", "loglinear", served, seasons=[2017, 2018]))
    assert "no declared fallback" in msg and "Oceanside" in msg
    # the fallback is least squares with free basin offsets: one slope set ≥ 0 on within-basin centred rows
    from sklearn.linear_model import LinearRegression
    m = {k: M._vol_rows(data, k, seasons=no2018) for k in ("north_shore", "central", "south")}
    lx = {k: np.log1p(data.basins[k].F[list(M.VOLUME_TERMS)][m[k]].to_numpy()) for k in m}
    ly = {k: data.basins[k].vol[m[k]] for k in m}
    w = LinearRegression(positive=True, fit_intercept=False).fit(
        np.vstack([lx[k] - lx[k].mean(axis=0) for k in m]), np.concatenate([ly[k] - ly[k].mean() for k in m])).coef_
    assert np.allclose(h["model"].named_steps["ols"].coef_, w)
    assert abs(h["basin_offset"] - (ly["south"].mean() - lx["south"].mean(axis=0) @ w)) < 1e-9
    assert np.allclose(M.head_log1p(h, F[h["features"]][m["south"]]),
                       np.maximum(0.0, h["basin_offset"] + lx["south"] @ w))   # train_v4.predicted_volume clips at 0
    forced = M.fit_head(data, "central", "loglinear", served, force_fallback=True)   # 75 events, the fallback asked for
    assert forced["kind"] == M.FALLBACK_KIND and forced["n_events"] >= 20
    assert "no declared fallback" in _raises(lambda: M.fit_head(data, "westside", "gbr", served, force_fallback=True))


def test_volume_rows_and_the_recipe_rule():
    """A head fits only measured volumes (X-S2-VOLQ: a blank or '<' volume on any of the day's events leaves the
    day out); design §6 change 7: log-linear replaces the GBR recipe unless the paired storm-block CI says it is
    worse; protocol §8's head-vs-fallback comparison is reported for every Bay-side basin."""
    data = _data()
    ons = TR.basin_onsets(G.get("sfpuc4_v1"), end=AS_OF).set_index(["basin", "date"])
    n_volq = 0
    for k, b in data.basins.items():
        o = ons.loc[k].reindex(data.days)
        volq = (o["volq"].to_numpy() == True) & (b.y == 1)                   # noqa: E712  (NaN off the ledger)
        n_volq += int(volq.sum())
        assert not (np.isfinite(b.vol) & volq).any(), k
        assert np.array_equal(np.isfinite(b.vol), (b.y == 1) & ~volq & np.isfinite(o["volume_mg"].to_numpy(dtype=float))), k
        assert np.allclose(b.vol[np.isfinite(b.vol)], np.log1p(o["volume_mg"].to_numpy(dtype=float)[np.isfinite(b.vol)]))
    assert n_volq > 0                                                        # the rule has days to leave out
    for lo, hi, want in ((0.01, 0.2, "gbr"), (-0.1, 0.2, "loglinear"), (0.0, 0.1, "loglinear"),
                         (-0.3, -0.1, "loglinear"), (None, None, "loglinear")):
        assert M.pick_recipe({"lo": lo, "hi": hi})[0] == want, (lo, hi)
    out = _run()
    vol = out["results"]["volume"]
    for k, v in vol["per_basin"].items():
        d = v["loglinear_vs_gbr"]
        assert v["picked"] == vol["picked"][k] == M.pick_recipe(d)[0], k
        assert abs(d["delta"] - (v["recipes"]["loglinear"]["log_mae"] - v["recipes"]["gbr"]["log_mae"])) < 1e-12, k
        assert out["heads"][k]["recipe"] == v["picked"]
        fb = v["vs_fallback"]
        if data.basins[k].facility == M.FALLBACK_FACILITY:
            assert fb["delta"]["n"] == v["recipes"]["loglinear"]["n"] and np.isfinite(fb["delta"]["delta"]), k
        else:
            assert fb["delta"] is None and "no declared fallback" in fb["why"], k


def test_the_winner_round_trips_through_the_saver():
    out, data = _run(), _data()
    tmp = Path(tempfile.mkdtemp())
    try:
        info = M.save_candidate(M.candidate_name(out["winner"]), out, out["results"], root=tmp, out_dir=tmp / "_bakeoff")
        s = SC.load_set(info["name"], root=tmp)
        assert s.geo.version == "sfpuc4_v1" and s.manifest["s2"]["contender"] == out["winner"]
        assert s.manifest["s2"]["rain_sources"] == data.sources and s.manifest["s2"]["grid_sha256"]
        for k in data.keys:
            F = data.basins[k].F
            assert np.array_equal(s.models[k]["model"].predict_proba(F)[:, 1], out["finals"][k]["model"].predict_proba(F)[:, 1])
            assert np.array_equal(s.holdout_models[k]["model"].predict_proba(F)[:, 1],
                                  out["holdouts"][k]["model"].predict_proba(F)[:, 1])
            h = out["heads"][k]
            assert np.array_equal(T.predicted_volume(s.volume[k], F), T.predicted_volume(h, F))
            assert s.models[k]["span"][1] <= str(M.TRAIN_END.date()) and s.holdout_models[k]["span"][1] < str(M.HOLDOUT_START.date())
        if out["choice"].south == "pooled":
            assert s.models["south"]["pooled_with"] == "central" and s.models["south"]["pooled_slope"] >= 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fit_final_refits_a_design_on_any_season_set():
    """fit_final's seasons / keys (a fold's design refit on the season sets its S3 asks for): on every season through
    the training end it is the finals exactly; a season set reads only its seasons' known days; keys fit those basins
    only; pooled South without Central, and an unknown basin, raise."""
    out, data = _run(), _data()
    ch = out["choice"]
    every = set(int(s) for s in np.unique(data.season[data.days <= M.TRAIN_END]))
    a, b = M.fit_final(data, ch), M.fit_final(data, ch, seasons=every)
    for k in data.keys:
        F = data.basins[k].F
        assert np.array_equal(a[k]["model"].predict_proba(F)[:, 1], b[k]["model"].predict_proba(F)[:, 1]), k
        assert a[k]["span"] == b[k]["span"] and a[k]["n_rows"] == b[k]["n_rows"], k
    some = {2017, 2018, 2021}
    c = M.fit_final(data, ch, seasons=some, keys=("central",))
    assert list(c) == ["central"]
    m = M.train_mask(data, "central", seasons=some)
    assert c["central"]["n_rows"] == int(m.sum()) and set(np.unique(data.season[m])) <= some
    assert c["central"]["span"] == [str(data.days[m].min().date()), str(data.days[m].max().date())]
    pooled = dataclasses.replace(ch, south="pooled")
    assert "fit both" in _raises(lambda: M.fit_final(data, pooled, keys=("south",)))
    assert set(M.fit_final(data, pooled, seasons=some, keys=("south", "central"))) == {"central", "south"}
    assert "are not" in _raises(lambda: M.fit_final(data, ch, keys=("southeast",)))


def test_fold_choices_are_each_folds_own_design():
    """fold_choices reads results.json: T1 and T1-holdout the final pick's choice (the finals' and the siblings'),
    each T2 season its outer fold's A5 pick; a winner whose choice names another contender, a bend option off the
    grid, or a missing season raises."""
    r = json.loads((M.OUT_DIR / "results.json").read_text())       # the committed bake-off's (nine outer folds)
    fc = M.fold_choices(r)
    assert set(fc) == {("T1", S2.FOLD_FINAL), ("T1-holdout", S2.FOLD_HOLDOUT)} | {("T2", S2.season_label(s)) for s in M.T2_SEASONS}
    assert fc[("T1", S2.FOLD_FINAL)].as_dict() == r["winner"]["choice"] == fc[("T1-holdout", S2.FOLD_HOLDOUT)].as_dict()
    for f in r["nested"]["folds"]:
        c = fc[("T2", f["season"])]
        assert c.contender == f["picked"] and c.as_dict() == f["choice"] and c.outer == S2._season_of(f["season"])
    bad = json.loads(json.dumps(r))
    bad["winner"]["choice"]["contender"] = "half"
    assert "different contenders" in _raises(lambda: M.fold_choices(bad))
    short = json.loads(json.dumps(r))
    short["nested"]["folds"] = short["nested"]["folds"][:-1]
    assert "nine T2 seasons" in _raises(lambda: M.fold_choices(short))
    odd = json.loads(json.dumps(r))
    odd["winner"]["choice"]["bends"] = "lower"
    assert "A5's grid" in _raises(lambda: M.fold_choices(odd))


def test_the_served_recipe_is_the_served_pickles_refit_on_the_city_basins():
    data = _data()
    rec = M.served_recipe(data)
    assert {k: v["served_key"] for k, v in rec.items()} == {"westside": "westside", "north_shore": "north_shore",
                                                            "central": "central", "south": "southeast"}
    fit = M.served_fit(data, rec)
    for k in data.keys:
        lr = fit[k].named_steps["lr"]
        assert type(lr).__name__ == "LogisticRegression" and lr.C == rec[k]["model"].named_steps["lr"].C   # unconstrained, as served
        assert fit[k] is not rec[k]["model"]


def test_writing_refuses_a_partial_grid():
    assert "full grid" in _raises(lambda: M.run(grid=SMALL, data=_data(), n_boot=10, write=True, log=QUIET))
    assert "full grid" in _raises(lambda: M.run(data=_data(), n_boot=10, save=True, log=QUIET))
    # the real directories however they are spelled
    assert "full grid" in _raises(lambda: M.run(grid=SMALL, data=_data(), n_boot=10, save=True, root=SC.ROOT, log=QUIET))
    assert "full grid" in _raises(lambda: M.run(grid=SMALL, data=_data(), n_boot=10, write=True, save=False,
                                                out_dir=M.OUT_DIR / ".." / M.OUT_DIR.name, log=QUIET))
    # inside the repository the bake-off writes to _bakeoff/ only, refused before anything is written (write_grid,
    # the first write, is stubbed to raise: a broken guard fails this test without writing into the repository)
    def no_write(*a, **k):
        raise AssertionError("run() went on to write")
    real_write = M.write_grid
    M.write_grid = no_write
    try:
        for bad in (SC.ROOT / "_bakeoff_scratch", SC.ROOT.parent / "candidates" / "_bakeoff"):
            assert "_bakeoff/ only" in _raises(lambda: M.run(grid=SMALL, data=_data(), n_boot=10, write=True, save=False,
                                                             out_dir=bad, log=QUIET))
            assert not bad.exists()
    finally:
        M.write_grid = real_write


def test_the_committed_bakeoff_results_agree_with_the_code():
    p = M.OUT_DIR / "results.json"
    assert p.exists(), "run stages_s2_sfpuc4.py --write"
    r = json.loads(p.read_text())
    g = json.loads((M.OUT_DIR / "grid.json").read_text())
    declared = M.Grid().declared()
    assert r["schema"] == M.SCHEMA and r["geography"] == "sfpuc4_v1" and r["as_of"] == str(AS_OF.date())
    assert g["sha256"] == M._sha_json(declared) == r["grid"]["sha256"] and g["grid"] == declared
    assert g["declared_at"] <= r["built_at"]                       # the grid was written before the fits
    assert r["protocol"].startswith("stages_v3@") and r["timing"]["total_seconds"] < 1800
    seasons = [f"{s}-{(s + 1) % 100:02d}" for s in M.T2_SEASONS]
    assert list(r["nested"]["picked_by_fold"]) == seasons and set(r["nested"]["picked_by_fold"].values()) <= set(M.CONTENDERS)
    keys = {"pooled", "westside", "north_shore", "central", "south"}
    for c in M.CONTENDERS:
        assert set(r["nested"]["by_contender"][c]) == keys and set(r["development"]["scores"][c]) == keys, c
        assert set(r["T1"]["scores"][c]) == keys and set(r["T1_holdout"]["scores"][c]) == keys, c
    assert set(r["T1"]["scores"]) == set(M.CONTENDERS) | {"served_recipe"}
    for f in r["nested"]["folds"]:
        assert f["season"] not in f["inner_seasons"] and len(f["inner_seasons"]) == 8
    win = r["winner"]["contender"]
    assert win == r["development"]["final_pick"]["winner"] and r["winner"]["candidate"] == M.candidate_name(win)
    assert isinstance(r["T1"]["check"]["noninferior_5pct"], bool) and isinstance(r["south_floor"]["pass"], bool)
    assert set(r["volume"]["picked"]) == keys - {"pooled"}
    s = SC.load_set(r["winner"]["candidate"])
    assert s.manifest["s2"]["contender"] == win and s.manifest["s2"]["grid_sha256"] == g["sha256"]
    with gzip.open(M.OUT_DIR / "rows.csv.gz", "rt") as f:
        rows = pd.read_csv(f, parse_dates=["date"])
    t1 = rows[rows["arm"] == f"t1:{win}"]
    data = _data()
    for k in data.keys:
        g1 = t1[t1["basin"] == k]
        p = s.models[k]["model"].predict_proba(data.basins[k].F.loc[pd.DatetimeIndex(g1["date"])])[:, 1]
        assert np.allclose(p, g1["p"].to_numpy(), rtol=1e-5, atol=1e-9), k      # rows hold 6 significant digits
    proc = rows[rows["arm"] == "procedure"]
    assert set(proc["tier"]) == {"T2"} and proc["v_hat"].notna().all() and len(proc) == 4 * len(pd.date_range("2016-07-01", "2025-06-30"))
    # the procedure's rows in each outer fold are the picked contender's own nested rows of that fold
    for season, picked in r["nested"]["picked_by_fold"].items():
        a = proc[proc["fold"] == season].set_index(["basin", "date"])["p"]
        b = rows[(rows["arm"] == f"nested:{picked}") & (rows["fold"] == season)].set_index(["basin", "date"])["p"]
        assert len(a) and a.sort_index().equals(b.sort_index()), season
    # the final pick, again from the development rows (excl '' reads back as NaN): same ranking, same winner
    dev = rows[rows["arm"].str.startswith("dev:") & rows["excl"].isna()]
    arms = {c: g.set_index(["basin", "date"])["p"].sort_index() for c, g in dev.groupby("contender")}
    y = dev[dev["contender"] == win].set_index(["basin", "date"])["y"].sort_index()
    assert all(a.index.equals(y.index) for a in arms.values()) and len(y) == r["development"]["final_pick"]["n"]
    bs = {c: float(np.mean((a.to_numpy() - y.to_numpy()) ** 2)) for c, a in arms.items()}
    for x in r["development"]["final_pick"]["ranking"]:
        assert abs(bs[x["contender"]] - x["brier"]) < 1e-8, x
    again = M.a5_pick(y.to_numpy(), {c: a.to_numpy() for c, a in arms.items()}, y.index.get_level_values("date"),
                      data.blocks, {c: M.n_terms(c) for c in M.CONTENDERS}, M.N_BOOT)
    assert again["winner"] == win and again["lowest"] == r["development"]["final_pick"]["lowest"]
    # protocol §8's S2 primary: each arm's Brier score again from the rows (excl '' reads back as NaN)
    sc = rows[rows["excl"].isna()]
    for window, arm_a, arm_b in (("T2", "procedure", "served_recipe"), ("T1", f"t1:{win}", "t1:served_recipe")):
        d = r["s2_primary"][window]["delta"]
        ga, gb = sc[(sc["arm"] == arm_a) & (sc["tier"] == window)], sc[(sc["arm"] == arm_b) & (sc["tier"] == window)]
        assert d["n"] == len(ga) == len(gb), window
        assert abs(d["a"] - np.mean((ga["p"] - ga["y"]) ** 2)) < 1e-7 and abs(d["b"] - np.mean((gb["p"] - gb["y"]) ** 2)) < 1e-7
    assert r["s2_primary"]["T1"]["noninferior_5pct"] == (r["s2_primary"]["T1"]["delta"]["hi"] < 0.05 * r["s2_primary"]["T1"]["delta"]["b"])
    # what the scores cannot clean is stated in the results and the candidate
    assert r["nested"]["caveat"] == M.T2_CAVEAT == s.manifest["s2"]["t2_caveat"]
    assert r["T1"]["caveat"] == M.T1_CAVEAT == s.manifest["s2"]["t1_caveat"]
    assert s.manifest["s2"]["term_set_provenance"] == M.TERM_SET_PROVENANCE[win] and s.manifest["s2"]["t2"] == M.T2_NESTED
    # no set's S2 is left from an older bake-off: the one these results name is its winner's
    here = M._rel(M.OUT_DIR / "results.json")
    from_here = [m["name"] for m in SC.list_sets() if "s2" in m["components"] and (m.get("s2") or {}).get("bakeoff") == here]
    assert from_here == [r["winner"]["candidate"]], from_here
    # the volume rule as declared, and the candidate's heads are the picked recipes
    for k, v in r["volume"]["per_basin"].items():
        assert v["picked"] == M.pick_recipe(v["loglinear_vs_gbr"])[0], k
        assert s.volume[k]["recipe"] == v["picked"] and s.manifest["s2"]["volume"][k]["recipe"] == v["picked"], k
        assert (v["vs_fallback"]["delta"] is None) == (data.basins[k].facility != M.FALLBACK_FACILITY), k


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
