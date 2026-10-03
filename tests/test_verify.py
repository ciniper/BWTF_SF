"""The verification library (P3, 2026-10-01): every stage score has one implementation, checked here.

Anchors: the Finley tornado table as worked in the WWRP/JWGFVR verification
pages (POD 0.549, FAR 0.720, CSI 0.228, ETS 0.216, PSS 0.523, HSS 0.355); the
CORP identity BS = MCB − DSC + UNC to 1e-12; BSS 1 for a perfect forecast and
0 for the reference itself; Hamill–Juras pooling from stratum sums; a seeded
bootstrap that repeats exactly; storm blocks on a toy series; the sign of a
paired difference.
Run: venv/bin/python tests/test_verify.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(MODELS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import verify as V  # noqa: E402


def _probs(n=4000, seed=0):
    """A miscalibrated forecast with ties (2 dp) and its outcomes."""
    rng = np.random.default_rng(seed)
    p = np.round(rng.beta(0.6, 3.0, n), 2)
    y = (rng.random(n) < np.clip(1.3 * p ** 1.2, 0, 1)).astype(float)
    return y, p


# ── 2×2 ────────────────────────────────────────────────────────────────────

def test_finley_tornado_table_matches_the_published_scores():
    c = V.contingency_table(28, 72, 23, 2680)
    got = {k: round(c[k], 3) for k in ("pod", "far", "csi", "ets", "pss", "hss")}
    assert got == {"pod": 0.549, "far": 0.720, "csi": 0.228, "ets": 0.216, "pss": 0.523, "hss": 0.355}, got
    assert round(c["fbi"], 2) == 1.96 and round(c["pofd"], 3) == 0.026 and round(c["acc"], 3) == 0.966
    assert c["n"] == 2803 and math.isclose(c["spec"], 1 - c["pofd"])
    # the same table from yes/no arrays and from a threshold on probabilities
    fc = np.r_[np.ones(28), np.ones(72), np.zeros(23), np.zeros(2680)]
    ob = np.r_[np.ones(28), np.zeros(72), np.ones(23), np.zeros(2680)]
    assert V.contingency(fc, ob) == c
    assert V.contingency_at(np.where(fc > 0, 0.6, 0.1), ob, 0.505) == c
    # a NaN row is dropped, not counted as a no
    assert V.contingency(np.r_[fc, np.nan], np.r_[ob, 1.0])["n"] == 2803


def test_contingency_edge_cases_are_nan_not_zero():
    c = V.contingency_table(0, 0, 0, 50)                  # no events, no alerts
    for k in ("pod", "far", "csi", "ets", "pss", "fbi", "hss", "sedi"):
        assert math.isnan(c[k]), (k, c[k])
    assert c["pofd"] == 0.0 and c["acc"] == 1.0
    assert math.isnan(V.contingency_table(0, 0, 0, 0)["acc"])
    # random forecast: H = F → PSS 0, SEDI 0
    r = V.contingency_table(10, 40, 10, 40)
    assert abs(r["pss"]) < 1e-15 and abs(r["sedi"]) < 1e-15 and abs(r["hss"]) < 1e-15 and abs(r["ets"]) < 1e-15
    # SEDI rises with skill, is NaN when H or F hits 0 or 1
    assert 0 < V.contingency_table(28, 72, 23, 2680)["sedi"] < 1
    assert math.isnan(V.contingency_table(5, 3, 0, 90)["sedi"])
    # the S1 rain case: forecast and gauge both thresholded at the same depth
    s1 = V.contingency_at([0.0, 0.6, 0.4, 1.2], [0.1, 0.5, 0.7, 0.2], 0.5)
    assert (s1["a"], s1["b"], s1["c"], s1["d"]) == (1, 1, 1, 1)


# ── continuous ─────────────────────────────────────────────────────────────

def test_continuous_scores_and_the_either_wet_subset():
    c = V.continuous([1.0, 2.0, 3.0, np.nan], [1.0, 1.0, 1.0, 5.0])
    assert c["n"] == 3 and c["me"] == 1.0 and c["mae"] == 1.0 and math.isclose(c["rmse"], math.sqrt(5 / 3))
    assert c["mbias"] == 2.0 and math.isnan(c["r"])       # constant obs → no correlation
    fc, ob = np.array([0.0, 0.05, 0.3, 0.0, 1.0]), np.array([0.0, 0.0, 0.0, 0.4, 0.8])
    w = V.continuous(fc, ob, wet=0.1)                       # either-wet: rows 2, 3, 4
    assert w["n"] == 3 and math.isclose(w["mae"], (0.3 + 0.4 + 0.2) / 3)
    m = V.wet_masks(fc, ob, 0.1)
    assert m["obs_wet"].sum() == 2 and m["fc_wet"].sum() == 2 and m["either_wet"].sum() == 3 and m["all"].sum() == 5
    assert V.continuous(fc, ob, m["obs_wet"])["n"] == 2
    rng = np.random.default_rng(3)
    a, b = rng.gamma(0.5, 0.4, 300), rng.gamma(0.5, 0.4, 300)
    assert math.isclose(V.continuous(a, b)["r"], np.corrcoef(a, b)[0, 1])


# ── probability scores ─────────────────────────────────────────────────────

def test_corp_identity_holds_to_1e12_on_random_data():
    for seed in range(5):
        y, p = _probs(seed=seed)
        c = V.corp(y, p)
        assert abs(c["bs"] - (c["mcb"] - c["dsc"] + c["unc"])) < 1e-12, seed
        assert abs(c["bs"] - V.brier(y, p)) < 1e-15
        assert c["mcb"] > -1e-12 and c["dsc"] > -1e-12
        assert math.isclose(c["unc"], y.mean() * (1 - y.mean()), rel_tol=1e-12)
        xs = [pt[0] for pt in c["curve"]]
        vs = [pt[1] for pt in c["curve"]]
        assert xs == sorted(xs) and vs == sorted(vs) and 0 <= vs[0] and vs[-1] <= 1
    # a calibrated forecast on a lot of data has almost no miscalibration
    rng = np.random.default_rng(9)
    p = rng.random(20000)
    y = (rng.random(20000) < p).astype(float)
    assert V.corp(y, p)["mcb"] < 0.002
    # worked by hand: PAV pools the out-of-order pair (0.2 → 1, 0.3 → 0) to 0.5 each,
    # so BS 0.275, BS(recalibrated) 0.125, UNC 0.25 → MCB 0.15, DSC 0.125
    h = V.corp([0, 1, 0, 1], [0.1, 0.2, 0.3, 0.4])
    assert np.allclose([h["bs"], h["mcb"], h["dsc"], h["unc"]], [0.275, 0.15, 0.125, 0.25], atol=1e-15)
    assert h["curve"] == [[0.1, 0.0], [0.2, 0.5], [0.3, 0.5], [0.4, 1.0]]
    # degenerate inputs stay finite where they can
    c = V.corp([0, 0, 1, 1], [0.3, 0.3, 0.3, 0.3])
    assert abs(c["bs"] - (c["mcb"] - c["dsc"] + c["unc"])) < 1e-12 and abs(c["dsc"]) < 1e-15
    assert V.corp([], [])["n"] == 0


def test_corp_bands_are_seeded():
    _, p = _probs(800)
    a, b = V.corp_bands(p, n=40, seed=1), V.corp_bands(p, n=40, seed=1)
    assert a == b and all(lo <= hi for lo, hi in zip(a["lo"], a["hi"]))


def test_bss_perfect_is_one_and_climatology_is_zero():
    y, p = _probs()
    ref = np.full(len(y), y.mean())
    assert V.bss(y, y, ref) == 1.0
    assert V.bss(y, ref, ref) == 0.0
    assert V.bss(y, p, 0.2) == V.bss(y, p, np.full(len(y), 0.2))      # a scalar reference is broadcast
    assert math.isnan(V.bss([0, 0, 0], [0.1, 0.2, 0.0], [0, 0, 0]))  # a perfect reference has no skill to beat
    assert V.brier([1, 0], [1, 0]) == 0.0 and V.brier([1, 0, np.nan], [0, 1, 0.5]) == 1.0
    # a NaN reference on a scored row raises (dropping it would score skill on fewer rows than BS);
    # on a row already dropped for a NaN outcome it is fine
    try:
        V.bss([1, 0, 1], [0.9, 0.1, 0.2], [0.5, np.nan, 0.5])
        raise AssertionError("a NaN reference on a scored row must raise")
    except ValueError:
        pass
    assert V.bss([1, np.nan, 1], [0.9, 0.1, 0.2], [0.5, np.nan, 0.5]) == V.bss([1, 1], [0.9, 0.2], [0.5, 0.5])


def test_climatology_ref_is_unit_by_month_smoothed_over_neighbours():
    # unit A: Jan 1 of 10 positive, Feb 4 of 10, Dec 0 of 5; unit B: 1 of 2 in Jul only
    units = ["A"] * 25 + ["B"] * 2
    months = [1] * 10 + [2] * 10 + [12] * 5 + [7, 7]
    y = [1] + [0] * 9 + [1] * 4 + [0] * 6 + [0] * 5 + [1, 0]
    score = (np.array(["A", "A", "A", "A", "B", "B", "C"]), np.array([1, 12, 6, 3, 8, 1, 5]))
    r = V.climatology_ref(y, (units, months), score)
    assert math.isclose(r[0], 5 / 25)          # Jan window = Dec + Jan + Feb
    assert math.isclose(r[1], 1 / 15)          # Dec window = Nov + Dec + Jan (wraps)
    assert math.isclose(r[2], 5 / 25)          # June: no A rows in May–Jul → the unit's rate
    assert math.isclose(r[3], 4 / 10)          # Mar window = Feb + Mar + Apr
    assert math.isclose(r[4], 0.5)             # Aug window reaches Jul
    assert math.isclose(r[5], 0.5)             # B in Jan: empty window → B's rate
    assert math.isclose(r[6], 6 / 27)          # unseen unit → overall rate
    # smooth=0 is the raw unit-month rate; dates work as the month column; rows of pairs work
    r0 = V.climatology_ref(y, (units, months), (["A"], [1]), smooth=0)
    assert math.isclose(r0[0], 0.1)
    dates = pd.to_datetime([f"2020-{m:02d}-15" for m in months])
    assert np.allclose(V.climatology_ref(y, (units, dates), score), r)
    assert np.allclose(V.climatology_ref(y, list(zip(units, months)), list(zip(*score))), r)
    rs = V.climatology_ref(y, (units, months), (["A"], [1]), shrink=25)
    assert math.isclose(rs[0], (5 + 25 * 5 / 25) / 50)
    # ±6 months would count the opposite month twice
    try:
        V.climatology_ref(y, (units, months), score, smooth=6)
        raise AssertionError("smooth=6 must raise")
    except ValueError:
        pass


def test_hamill_juras_pooling_uses_stratum_sums_not_a_pooled_base_rate():
    rng = np.random.default_rng(4)
    s = np.r_[np.zeros(1000), np.ones(1000)]
    base = np.where(s == 0, 0.02, 0.6)
    y = (rng.random(2000) < base).astype(float)
    ref = base.copy()                                        # each stratum's own climatology
    p = np.clip(base + rng.normal(0, 0.05, 2000), 0, 1)      # knows the stratum, little else
    by = [(int((s == k).sum()), V.brier(y[s == k], p[s == k]), V.brier(y[s == k], ref[s == k])) for k in (0, 1)]
    by_hand = 1 - sum(n * b for n, b, _ in by) / sum(n * r for n, _, r in by)
    assert math.isclose(V.bss(y, p, ref), by_hand, rel_tol=1e-12)        # a per-row stratified ref pools by stratum sums
    pooled_base = V.bss(y, p, V.sample_ref(y))
    assert pooled_base > 0.25 and abs(by_hand) < 0.1, (pooled_base, by_hand)   # the base-rate trap
    assert np.allclose(V.sample_ref(y, s), np.where(s == 0, y[s == 0].mean(), y[s == 1].mean()))


def test_log_score_in_bits_with_clip_count():
    assert V.log_score([1, 0], [0.5, 0.5]) == (1.0, 0)
    m, k = V.log_score([1, 0, 1], [1.0, 0.0, 0.0005])
    assert k == 3 and math.isclose(m, (-math.log2(0.999) * 2 - math.log2(0.001)) / 3)
    assert V.log_score([1, np.nan], [0.25, 0.5]) == (2.0, 0)


def test_roc_matches_sklearn_with_ties_and_pr_reports_prevalence():
    from sklearn.metrics import roc_auc_score
    y, p = _probs(3000, seed=2)
    assert math.isclose(V.roc_auc(y, p), roc_auc_score(y, p), rel_tol=1e-12)
    ap, prev = V.pr_auc(y, p)
    assert math.isclose(prev, y.mean()) and ap > prev
    assert math.isnan(V.roc_auc([0, 0], [0.1, 0.2])) and math.isnan(V.pr_auc([0, 0], [0.1, 0.2])[0])


def test_murphy_integrates_to_the_brier_score():
    y, p = _probs(2000, seed=5)
    th = (np.arange(20000) + 0.5) / 20000
    assert abs(2 * V.murphy(y, p, th).mean() - V.brier(y, p)) < 1e-4
    # at θ an alert is p ≥ θ: a miss costs 1 − θ, a false alarm θ
    assert np.allclose(V.murphy([1, 0], [0.2, 0.6], [0.5]), [(0.5 + 0.5) / 2])
    assert np.allclose(V.murphy([1, 0], [0.5, 0.4], [0.5]), [0.0])


# ── blocks and the bootstrap ───────────────────────────────────────────────

def test_storm_blocks_on_a_toy_series():
    days = pd.date_range("2024-01-01", periods=31)          # Mon 1 Jan 2024 = ISO week 1
    rain = np.zeros(31)
    rain[[4, 5, 8]] = [0.3, 0.1, 0.5]                        # Jan 5, 6, 9: two dry days apart → one storm
    rain[19] = 0.2                                           # Jan 20
    rain[25] = 0.05                                          # below 0.1": not wet
    assert [(s.day, e.day) for s, e in V.storm_spans(days, rain)] == [(5, 9), (20, 20)]
    b = V.storm_blocks(days, rain)
    # quiet 1–3 | storm 4 → 16 Jan | quiet 17–18 (wk 3) | storm 19 → 27 | quiet 28 (Sun, wk 4) | 29–31 (wk 5)
    want = [0] * 3 + [1] * 13 + [2] * 2 + [3] * 9 + [4] * 1 + [5] * 3
    assert b.tolist() == want, b.tolist()
    # gap 1: Jan 6 → 9 is two dry days, so two storms, whose padded windows still overlap
    assert V.storm_blocks(days, rain, gap=1).tolist() == want
    assert len(V.storm_spans(days, rain, gap=1)) == 3
    # far-apart storms stay separate; a long gap joins them
    r2 = np.zeros(31)
    r2[[4, 14]] = 1.0
    assert len(set(V.storm_blocks(days, r2).tolist())) > len(set(V.storm_blocks(days, r2, gap=9).tolist()))
    # stacked rows (several units per day) share their day's block; NaN rain is dry
    stacked = V.storm_blocks(np.r_[days, days], np.r_[rain, np.full(31, np.nan)])
    assert stacked.tolist() == want + want
    # a dry month is ISO weeks only, and so is a month with no rain readings at all
    weeks = [0] * 7 + [1] * 7 + [2] * 7 + [3] * 7 + [4] * 3
    assert V.storm_blocks(days, np.zeros(31)).tolist() == weeks
    assert V.storm_blocks(days, np.full(31, np.nan)).tolist() == weeks


def test_block_bootstrap_is_deterministic_given_the_seed():
    y, p = _probs(1200, seed=7)
    blocks = np.arange(len(y)) // 10
    a = V.block_bootstrap(V.brier, (y, p), blocks, n=300, seed=11)
    b = V.block_bootstrap(V.brier, (y, p), blocks, n=300, seed=11)
    c = V.block_bootstrap(V.brier, (y, p), blocks, n=300, seed=12)
    assert a == b and a != c and a[1] <= a[0] <= a[2]
    assert a[0] == V.brier(y, p)
    # one block cannot be resampled
    est, lo, hi = V.block_bootstrap(V.brier, (y, p), np.zeros(len(y)), n=50)
    assert est == V.brier(y, p) and math.isnan(lo) and math.isnan(hi)
    # vector statistics get vector bounds
    e, lo, hi = V.block_bootstrap(lambda yy, pp: [V.brier(yy, pp), pp.mean()], (y, p), blocks, n=300)
    assert e.shape == lo.shape == hi.shape == (2,) and (lo <= e).all() and (e <= hi).all()
    # errors shared within a block (one storm, one bias) widen the interval; row resampling would hide that
    rng = np.random.default_rng(13)
    blk = np.arange(1200) // 60
    yy = (rng.random(1200) < rng.uniform(0, 0.6, 20)[blk]).astype(float)   # each block its own storm
    pp = np.full(1200, 0.3)
    narrow = V.block_bootstrap(V.brier, (yy, pp), None, n=400, seed=0)
    wide = V.block_bootstrap(V.brier, (yy, pp), blk, n=400, seed=0)
    assert wide[2] - wide[1] > 1.5 * (narrow[2] - narrow[1]), (wide, narrow)


def test_paired_delta_sign_and_mirror():
    rng = np.random.default_rng(8)
    n = 2000
    truth = rng.beta(0.5, 3, n)
    y = (rng.random(n) < truth).astype(float)
    good, bad = truth, np.clip(truth + rng.normal(0, 0.15, n), 0, 1)
    blocks = np.arange(n) // 7
    d = V.paired_delta(y, good, bad, blocks, n=500, seed=3)
    assert d["delta"] < 0 and d["hi"] < 0 and d["verdict"] == "better" and d["p_neg"] > 0.95
    assert math.isclose(d["delta"], V.brier(y, good) - V.brier(y, bad))
    r = V.paired_delta(y, bad, good, blocks, n=500, seed=3)
    assert math.isclose(r["delta"], -d["delta"]) and math.isclose(r["lo"], -d["hi"], abs_tol=1e-12)
    assert r["verdict"] == "worse" and not V.noninferior(r) and V.noninferior(d)
    # the same forecast in both arms: Δ 0, no clear difference
    z = V.paired_delta(y, good, good, blocks, n=100)
    assert z["delta"] == 0 and z["verdict"] == "no clear difference"
    # the log metric and a NaN in one arm drop that row from both
    lg = V.paired_delta(np.r_[y, 1], np.r_[good, np.nan], np.r_[bad, 0.5], np.r_[blocks, 999], metric="log", n=100)
    assert lg["n"] == n and lg["delta"] < 0
    # MDE ≈ 2.49 × SE (90% CI, 80% power), and the closed-form SE is the same size as the bootstrap's
    assert math.isclose(V.mde(1.0), 1.6448536 + 0.8416212, rel_tol=1e-6)
    assert math.isclose(d["mde"], V.mde(d["se"])) and math.isclose(d["mde_pct"], d["mde"] / d["b"])
    se = V.paired_se(y, good, bad, blocks)
    assert 0.7 < se / d["se"] < 1.4, (se, d["se"])
    # blocks must be given (None = rows, asked for explicitly): a forgotten one would narrow the CI silently
    try:
        V.paired_delta(y, good, bad)
        raise AssertionError("paired_delta without blocks must fail")
    except TypeError:
        pass


# ── the bundle ─────────────────────────────────────────────────────────────

def test_scores_bundle_has_every_field_and_is_json_safe():
    rng = np.random.default_rng(10)
    days = pd.date_range("2020-10-01", periods=900)
    rain = np.where(rng.random(900) < 0.12, rng.gamma(1, 0.4, 900), 0.0)
    p = np.clip(0.02 + 0.6 * np.tanh(rain), 0, 1)
    y = (rng.random(900) < p).astype(float)
    ref = V.climatology_ref(y, (np.array(["u"] * 900), days), (np.array(["u"] * 900), days))
    blocks = V.storm_blocks(days, rain)
    b = V.scores_bundle(y, p, ref, blocks, n=200)
    json.dumps(b, allow_nan=False)
    for k in ("n", "n_pos", "n_blocks", "n_pos_blocks", "prev", "bs", "bs_ref", "bss", "logs", "clipped", "roc",
              "pr", "lift", "corp", "ci", "contingency"):
        assert k in b, k
    assert set(b["corp"]) == {"mcb", "dsc", "unc", "curve"} and set(b["ci"]) == {"level", "bs", "bss"}
    assert list(b["contingency"]) == ["0.205", "0.505", "0.805"]
    assert b["n"] == 900 and b["n_pos"] == int(y.sum()) and b["n_blocks"] == len(set(blocks))
    assert math.isclose(b["bs"], V.brier(y, p)) and math.isclose(b["bss"], V.bss(y, p, ref))
    assert b["ci"]["bss"][0] <= b["bss"] <= b["ci"]["bss"][1]
    assert abs(b["bs"] - (b["corp"]["mcb"] - b["corp"]["dsc"] + b["corp"]["unc"])) < 1e-12
    assert b["contingency"]["0.205"] == V.clean(V.contingency_at(p, y, 0.205))
    assert b == V.scores_bundle(y, p, ref, blocks, n=200)              # seeded
    # no reference → BSS unscored (None), the rest still there; NaN outcome rows dropped
    nb = V.scores_bundle(np.r_[y, np.nan], np.r_[p, 0.3], None, np.r_[blocks, -1], n=50)
    assert nb["bss"] is None and nb["ci"]["bss"] == [None, None] and nb["n"] == 900
    try:
        V.scores_bundle(y, p, np.r_[ref[:-1], np.nan], blocks, n=10)
        raise AssertionError("a NaN reference on a scored row must raise")
    except ValueError:
        pass
    e = V.scores_bundle([], [], None, None, n=10)
    assert e["n"] == 0 and e["bs"] is None
    json.dumps(e, allow_nan=False)
    try:
        V.scores_bundle(y, p)
        raise AssertionError("scores_bundle without ref and blocks must fail")
    except TypeError:
        pass
    assert V.clean({"a": np.float64(np.nan), "b": np.array(2.5), "c": np.array([1, np.inf])}) == \
        {"a": None, "b": 2.5, "c": [1.0, None]}


def test_default_edges_mirror_the_public_risk_levels():
    from shared import risk_levels           # the home of the levels (A3); verify.py may not import it
    assert V.EDGES == tuple(risk_levels.edges()), (V.EDGES, risk_levels.edges())


def test_module_imports_nothing_from_the_repo():
    src = (MODELS / "verify.py").read_text()
    imports = [ln.split()[1] for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    allowed = ("__future__", "math", "numpy", "pandas", "scipy", "sklearn")
    assert all(i.split(".")[0] in allowed for i in imports), imports


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
