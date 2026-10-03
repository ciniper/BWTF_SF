"""stages_s2 (P7a): S2 out of fold — every scored window refits every S2 component
without the scored days (STAGES_DESIGN.md Part B 1, §8 P7; STAGES_PROTOCOL.md §2).

Fidelity first, on committed data, no network:
  - the additive return_oof hooks (leaderboard.season_cv, shared_logit.season_cv):
    the default return is exactly what the pre-hook code recorded, and the pooled OOF
    reproduces that record exactly;
  - the training record is each set's own (a clone fit on set_rows is the final, exactly),
    and a flipped archive rule, masked inputs or a missing month raise;
  - refits refuse the GBM and a fitted dry-day offset; the protocol stamp is checked
    against the protocol's text;
  - T1 is the served set's own predict path: its p equals the served scorecard's
    stored post-training p on the inputs that scorecard used (gauge_outage_v1);
  - T1-holdout's refit equals stage2_variants._refit_holdouts and lands within 0.002
    of the stored holdout p on the same unmasked inputs (a fidelity check only);
  - the volume heads refit with the bundle's own design (a full-span refit is the
    bundle's head; a fold's is train_v4.fit_volume_heads on the fold's rows);
  - a T2 fold equals a refit by hand; no fold trains on its scored season, no row is
    T3, and check_out_of_fold catches a leak; candidates logit_v1 and gb_v1 load
    (gb_v1: T1 only, its dry-day offset included).
Run: venv/bin/python tests/test_stages_s2.py
"""
from __future__ import annotations

import functools
import gzip
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "features" / "forecast" / "src" / "models"
for p in (str(ROOT), str(ROOT / "features" / "forecast"), str(MODELS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import candidates as C  # noqa: E402
import exclusions as X  # noqa: E402
import leaderboard as L  # noqa: E402
import shared_logit as SL  # noqa: E402
import stages_s2 as S  # noqa: E402
import stages_spec as SP  # noqa: E402
import train_v4 as T  # noqa: E402
import truth as TR  # noqa: E402
from shared import geography as G  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")     # the committed data's end: every count below is as of this day
POST_DAYS, HOLDOUT_DAYS = 290, 854     # protocol §2: T1 2025-11-01 → AS_OF, T1-holdout 2023-07-01 → 2025-10-31
T2_DAYS = 3287                         # 2016-07-01 → 2025-06-30, the nine T2 seasons (no data end in it)
KEYS = G.get("geo_v1").keys            # the served set's basins (its geography; asserted in the stamp test)


def _served() -> str:
    return C.served_info()["name"]     # never hard-coded


@functools.lru_cache(maxsize=None)
def _sources() -> tuple:
    """Every rain source the served set and its heads read (S2Set.sources)."""
    return S.load_set(_served()).sources


@functools.lru_cache(maxsize=None)
def _frames(masked: bool) -> dict:
    """The 'rain' entry: the training record's features through AS_OF, outage-masked or raw."""
    return T.build_dataset(end=AS_OF, sources=list(_sources()), input_rules=["gauge_outage_v1"] if masked else None)[0]


@functools.lru_cache(maxsize=None)
def _train() -> dict:
    """The raw training record through 2025-10-31 (stages_s2.training_frames)."""
    return S.training_frames(S.load_set(_served()))


@functools.lru_cache(maxsize=None)
def _fit(name: str, root: str = "served", tiers=S.TIERS):
    return S.fit(name, root, tiers)


@functools.lru_cache(maxsize=None)
def _rows(name: str, root: str = "served", tiers=S.TIERS, masked: bool = True) -> pd.DataFrame:
    return _fit(name, root, tiers).predict({"rain": _frames(masked)})


def _scorecard(path: Path) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def _stored(sc: dict, field: str) -> dict:
    """{(date, basin key): stored value} for every day the value is not None."""
    return {(pd.Timestamp(d["date"]), k): b[field] for d in sc["days"] for k, b in d["basins"].items() if b.get(field) is not None}


def _set_rows(s2set, frames, key):
    name = s2set.geo.basin(key).name
    sub = T.target_frame(frames[s2set.models[key]["rain_source"]], name)
    return sub if s2set.use_archive.get(name, True) else sub[sub[f"{name}_label_source"] != "poobot"]


# ── the additive hooks (§8 P7a) ─────────────────────────────────────────────

def _check_oof(oof: pd.DataFrame, want: dict, what) -> None:
    """The pooled out-of-fold frame is the recorded season CV, exactly: its scores are the
    recorded dict, one row per pre-holdout day, each tagged with the season that held it out."""
    assert list(oof.columns) == list(L.OOF_COLUMNS) and not oof["date"].duplicated().any(), what
    assert (T.wet_season(oof["date"]) == oof["season"]).all() and (oof["date"] < T.HOLDOUT_START).all(), what
    assert L.scores(oof["y"], oof["p"]) == want, what


def test_leaderboard_season_cv_default_is_the_recorded_one_and_its_oof_reproduces_it():
    """The default return equals what the pre-hook code wrote to leaderboard.json (pre-holdout rows,
    unchanged by a later data refresh), so it stays byte-identical; with return_oof the pooled OOF
    gives the same dict exactly. Every logit row the record's sources reach, and one GBM row."""
    board = json.loads(L.OUT_JSON.read_text())
    frames = _train()
    checked = {"logit": 0, "gb": 0}
    for key, bd in board["basins"].items():
        for r in bd["rows"]:
            fam, want = r["family"], r["season_cv_pre_holdout"]
            if r["source"] not in frames or not want or (fam == "gb" and checked["gb"]):
                continue
            sub = T.target_frame(frames[r["source"]], bd["name"])
            if not board["stage1_archive_labels"][bd["name"]]:
                sub = sub[sub[f"{bd['name']}_label_source"] != "poobot"]
            C_ = r["C"] if fam == "logit" else 0.3                         # make_model ignores C for gb
            assert L.season_cv(sub, fam, C_) == want, (key, fam, r["source"], r["C"])   # the default, exactly
            got, oof = L.season_cv(sub, fam, C_, return_oof=True)
            assert got == want, (key, fam, r["source"], r["C"])
            _check_oof(oof, want, (key, fam, r["source"], r["C"]))
            checked[fam] += 1
    assert checked["logit"] >= 7 and checked["gb"] == 1, checked    # 7 logit rows read avg or SF Downtown


def test_shared_logit_season_cv_default_is_the_recorded_one_and_its_oof_reproduces_it():
    frames = _train()
    use_archive = json.loads((T.SERVE_DIR / "eval_report.json").read_text())["stage1_archive_labels"]
    for name, dk in (("logit_v2_four", "four"), ("logit_v2_shared5", "shared5")):
        man = json.loads((C.candidate_dir(name) / "manifest.json").read_text())
        for key, want in man["per_basin"].items():
            basin = "citywide" if key == "citywide" else G.get("geo_v1").basin(key).name
            sub = SL.basin_rows(basin, frames, {basin: man["rain_sources"][key]}, use_archive)
            assert SL.season_cv(sub, L.SHARED_DESIGNS[dk], want["C"]) == want["season_cv_pre_holdout"], (name, key)
            got, oof = SL.season_cv(sub, L.SHARED_DESIGNS[dk], want["C"], return_oof=True)
            assert got == want["season_cv_pre_holdout"], (name, key)
            _check_oof(oof, want["season_cv_pre_holdout"], (name, key))


# ── fidelity: T1 and T1-holdout ─────────────────────────────────────────────

def test_t1_is_the_served_predict_path_on_the_scorecards_post_training_inputs():
    sc = _scorecard(T.SERVE_DIR / "scorecard.json.gz")
    assert sc["input_rules_post"] == ["gauge_outage_v1"] and sc["trained_through"] == "2025-10-31"
    rows = _rows(_served(), tiers=("T1",))
    assert len(rows) == POST_DAYS * len(KEYS) and set(rows["fold"]) == {S.FOLD_FINAL}
    stored = _stored(sc, "p")
    worst = max(abs(round(p, 3) - stored[(d, k)]) for d, k, p in zip(rows["date"], rows["basin"], rows["p"]))
    assert worst < 1e-9, worst                                  # the artifact holds 3 decimals: exact at its precision
    for key in KEYS:                                            # code vs code, unrounded
        with open(T.SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            m = pickle.load(f)
        fr = _frames(True)[m["rain_source"]]
        fr = fr[fr["date"] >= S.POST_START]
        got = rows[rows["basin"] == key]
        assert (got["date"].to_numpy() == fr["date"].to_numpy()).all()
        assert np.abs(got["p"].to_numpy() - T.calibrated(m, fr)).max() < 1e-12, key


def test_t1_holdout_refits_like_refit_holdouts_and_reproduces_the_stored_holdout_p():
    import stage2_variants as SV
    fitted = _fit(_served())
    s2set = fitted.set
    rows = _rows(_served(), masked=False)                       # the unmasked inputs the scorecard's holdout used
    ho = rows[rows["tier"] == "T1-holdout"]
    assert len(ho) == HOLDOUT_DAYS * len(KEYS) and set(ho["fold"]) == {S.FOLD_HOLDOUT}
    stored = _stored(_scorecard(T.SERVE_DIR / "scorecard.json.gz"), "ph")
    worst = max(abs(p - stored[(d, k)]) for d, k, p in zip(ho["date"], ho["basin"], ho["p"]))
    assert worst <= 0.002, worst
    # the same weights stage2_variants._refit_holdouts fits
    models = dict(s2set.models)
    with open(T.SERVE_DIR / "citywide_model.pkl", "rb") as f:
        models["citywide"] = pickle.load(f)
    chosen = {s2set.geo.basin(k).name: s2set.models[k]["rain_source"] for k in KEYS} | {"citywide": "avg"}
    train = _train()
    theirs = SV._refit_holdouts(models, chosen, train, T.get_feature_columns_v21())
    fold = next(f for f in fitted.folds if f.tier == "T1-holdout")
    X0 = _frames(False)["avg"][T.get_feature_columns_v21()]
    for key in KEYS:
        a = fold.weights[key]["model"].predict_proba(X0)[:, 1]
        assert np.abs(a - theirs[key].predict_proba(X0)[:, 1]).max() < 1e-12, key


def test_volume_heads_are_refit_with_the_bundles_own_design():
    fitted = _fit(_served())
    s2set = fitted.set
    train = _train()
    full = S.fit_fold(s2set, train, "T1-holdout", "check", S.HOLDOUT_START, S.TRAINED_THROUGH, None,
                      lambda f: f["date"] <= S.TRAINED_THROUGH)
    X0 = _frames(True)
    for key in KEYS:                                            # all training events → the bundle's head, exactly
        h = s2set.heads[key]
        assert full.heads[key]["n_events"] == h["n_events"], key
        assert np.abs(T.predicted_volume(full.heads[key], X0[h["rain_source"]]) - T.predicted_volume(h, X0[h["rain_source"]])).max() == 0.0
    # T1-holdout's v_hat = train_v4.fit_volume_heads on the pre-holdout rows
    pre = {s: f[f["date"] < S.HOLDOUT_START] for s, f in train.items()}
    chosen = {s2set.geo.basin(k).name: s2set.heads[k]["rain_source"] for k in KEYS}
    theirs = T.fit_volume_heads(pre, chosen, s2set.heads["westside"]["features"])
    ho = _rows(_served())
    ho = ho[ho["tier"] == "T1-holdout"]
    for key in KEYS:
        name = s2set.geo.basin(key).name
        fr = X0[s2set.heads[key]["rain_source"]].set_index("date").loc[ho.loc[ho["basin"] == key, "date"]]
        assert np.abs(ho.loc[ho["basin"] == key, "v_hat"].to_numpy() - T.predicted_volume(theirs[name], fr)).max() < 1e-12, key


def _raises(fn, words, kinds=(ValueError, KeyError, TypeError, FileNotFoundError)):
    try:
        fn()
    except kinds as e:
        assert words in str(e), str(e)
        return
    raise AssertionError(f"no raise for {words!r}")


def test_the_training_record_is_the_sets_own():
    """Every refit starts from set_rows, so they must be the set's rows: a clone fit on all of them is
    the pickled final exactly, and the record's counts are the set's. A flipped archive-label decision,
    masked training inputs or a missing month all raise before any fold is fit."""
    import dataclasses
    from sklearn.base import clone
    s2set, train = S.load_set(_served()), _train()
    S.check_training_record(s2set, train)
    for key in KEYS:
        m, rows = s2set.models[key], S.set_rows(s2set, train, key)
        again = clone(m["model"]).fit(rows[m["features"]], rows["y"])
        Xr = rows[m["features"]]
        assert np.abs(again.predict_proba(Xr)[:, 1] - m["model"].predict_proba(Xr)[:, 1]).max() == 0.0, key
    flipped = dataclasses.replace(s2set, use_archive={**s2set.use_archive, "Westside": True})
    _raises(lambda: S.check_training_record(flipped, train), "the set recorded")
    masked = T.build_dataset(sources=list(s2set.sources), input_rules=["gauge_outage_v1"])[0]
    _raises(lambda: S.check_training_record(s2set, masked), "misses the final")
    gap = {s: f[(f["date"] < pd.Timestamp("2019-01-01")) | (f["date"] > pd.Timestamp("2019-01-31"))] for s, f in train.items()}
    _raises(lambda: S.check_training_record(s2set, gap), "the set recorded")
    _raises(lambda: S.fit(_served(), tiers=("T1",), train_frames=masked), "misses the final")
    longer = T.build_dataset(end=AS_OF, sources=list(s2set.sources))[0]      # days past the training end are cut
    S.check_training_record(s2set, longer)


def test_refits_refuse_the_gbm_and_a_fitted_offset():
    """No LOSO or holdout refit for a GBM, through fit or fit_fold; a refit never carries a dry-day
    offset fit on the final's span (every logit set's is 0 by construction); a pickle of another
    family than its descriptor's raises at load."""
    import dataclasses
    train = _train()
    gb = S.load_set("gb_v1", "candidates")
    assert all(m["calibration_offset"] > 0 for m in gb.models.values())     # gb_v1's offsets were fit
    plan = {p[1]: p for p in S._plan(S.TIERS)}
    _raises(lambda: S.fit_fold(gb, train, *plan["2019-20"]), "no refit")
    _raises(lambda: S.fit_fold(gb, train, *plan[S.FOLD_HOLDOUT]), "no refit")
    S.fit_fold(gb, train, *plan[S.FOLD_FINAL])                              # T1: its own finals, offset and all
    served = S.load_set(_served())
    assert all(m["calibration_offset"] == 0.0 for m in served.models.values())
    shifted = dataclasses.replace(served, models={**served.models,
                                                  "central": {**served.models["central"], "calibration_offset": 0.001}})
    _raises(lambda: S.fit_fold(shifted, train, *plan["2019-20"]), "dry-day offset")
    _raises(lambda: S.fit_fold(shifted, train, *plan[S.FOLD_HOLDOUT]), "dry-day offset")
    S.fit_fold(shifted, train, *plan[S.FOLD_FINAL])
    real = C.load_models
    try:                                                                    # GBM pickles under a logit manifest
        C.load_models = lambda name: real("gb_v1") if name == "logit_v1" else real(name)
        _raises(lambda: S.load_set("logit_v1", "candidates"), "in a logit set")
    finally:
        C.load_models = real


def test_the_protocol_stamp_is_checked_against_the_protocol_text():
    import hashlib
    import re
    import tempfile
    text = X.PROTOCOL.read_text()
    sha = re.search(r"^protocol sha256: ([0-9a-f]{64})$", text, re.M).group(1)
    assert X.protocol_stamp() == f"{SP.PROTOCOL_VERSION}@{sha}"
    assert hashlib.sha256(text.replace(sha, "<filled at commit>", 1).encode()).hexdigest() == sha
    real = X.PROTOCOL
    with tempfile.TemporaryDirectory() as d:
        try:
            X.PROTOCOL = Path(d) / real.name
            X.PROTOCOL.write_text(text.replace("never pooled with T1", "never pooled with T1 ", 1))
            _raises(X.protocol_stamp, "edited after its freeze")
        finally:
            X.PROTOCOL = real


# ── T2 and the out-of-fold guarantee ────────────────────────────────────────

def test_a_t2_fold_equals_a_refit_by_hand():
    from sklearn.base import clone
    s2set = _fit(_served()).set
    rows = _rows(_served())
    train = _train()
    season, others = 2019, [s for s in S.T2_SEASONS if s != 2019]
    for key in KEYS:
        m, h = s2set.models[key], s2set.heads[key]
        name = s2set.geo.basin(key).name
        sub = _set_rows(s2set, train, key)
        tr = sub[sub["season"].isin(others)]
        model = clone(m["model"]).fit(tr[m["features"]], tr["y"])
        hs = T.target_frame(train[h["rain_source"]], name)
        ev = hs[(hs["y"] == 1) & (hs[f"{name}_volume_known"] == 1) & hs["season"].isin(others)]
        head = {**h, "model": clone(h["model"]).fit(ev[h["features"]], np.log1p(ev[f"{name}_volume_mg"]))}
        fr = _frames(True)[m["rain_source"]]
        fr = fr[T.wet_season(fr["date"]) == season]
        frv = _frames(True)[h["rain_source"]].set_index("date").loc[fr["date"]]
        got = rows[(rows["tier"] == "T2") & (rows["fold"] == "2019-20") & (rows["basin"] == key)]
        assert (got["date"].to_numpy() == fr["date"].to_numpy()).all() and len(got) == 366
        assert np.abs(got["p"].to_numpy() - T.calibrated({**m, "model": model}, fr)).max() < 1e-12, key
        assert np.abs(got["v_hat"].to_numpy() - T.predicted_volume(head, frv)).max() < 1e-12, key


def test_no_t3_row_and_no_fold_trains_on_what_it_scores():
    fitted = _fit(_served())
    rows = _rows(_served())
    assert set(rows["tier"]) == set(S.TIERS) and list(rows.columns) == list(S.COLUMNS)
    assert rows["p"].between(0, 1).all() and (rows["v_hat"] >= 0).all() and rows[list(S.COLUMNS)].notna().all().all()
    assert not rows.duplicated(["date", "basin", "entry", "tier"]).any()
    t2rows = rows[rows["tier"] == "T2"]                         # every T2 day once per basin, in its own season's fold
    assert (t2rows.groupby("basin").size() == T2_DAYS).all() and len(t2rows) == T2_DAYS * len(KEYS)
    assert (t2rows["fold"] == T.wet_season(t2rows["date"]).map(S.season_label)).all()
    stamp = rows.attrs["stamp"]
    t2 = [f for f in stamp["folds"] if f["tier"] == "T2"]
    assert [f["fold"] for f in t2] == ["2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    for f in t2:
        s = int(f["fold"][:4])
        for key, info in f["train"].items():
            assert s not in info["seasons"] and set(info["seasons"]) <= set(S.T2_SEASONS) - {s}, (f["fold"], key)   # never 2015, never 2025
    ho = next(f for f in stamp["folds"] if f["tier"] == "T1-holdout")
    assert all(pd.Timestamp(i["span"][1]) < S.HOLDOUT_START for i in ho["train"].values())
    t1 = next(f for f in stamp["folds"] if f["tier"] == "T1")
    assert all(pd.Timestamp(i["span"][1]) <= S.TRAINED_THROUGH for i in t1["train"].values())
    for f in fitted.folds:                                      # the guarantee check_out_of_fold enforces
        g = rows[(rows["tier"] == f.tier) & (rows["fold"] == f.fold)]
        for key in KEYS:
            assert not pd.DatetimeIndex(g.loc[g["basin"] == key, "date"]).isin(f.seen[key]).any()
    S.check_out_of_fold(rows, fitted)

    def raises(bad, words):
        try:
            S.check_out_of_fold(bad, fitted)
        except (ValueError, KeyError) as e:
            assert words in str(e), str(e)
            return
        raise AssertionError(f"no raise for {words!r}")

    raises(rows.assign(tier=np.where(rows.index == 0, "T3", rows["tier"])), "X-ALL-INSAMPLE")
    leak = rows.copy()                                          # a 2019-20 row relabelled as the 2018-19 fold, which trained on it
    i = leak.index[(leak["tier"] == "T2") & (leak["fold"] == "2019-20")][0]
    leak.loc[i, "fold"] = "2018-19"
    raises(leak, "outside")
    seen = rows.copy()                                          # the 2018-19 fold's window, one of its training days in it
    f18 = next(f for f in fitted.folds if f.fold == "2018-19")
    j = seen.index[(seen["tier"] == "T2") & (seen["fold"] == "2018-19") & (seen["basin"] == "central")][0]
    fake = S.Fold(f18.tier, f18.fold, f18.start, f18.end, f18.season, f18.weights, f18.heads,
                  {**f18.seen, "central": f18.seen["central"].union([seen.loc[j, "date"]])}, f18.info)
    raises_with = S.Fitted(fitted.set, fitted.tiers, tuple(fake if f is f18 else f for f in fitted.folds), fitted.stamp)
    try:
        S.check_out_of_fold(seen, raises_with)
        raise AssertionError("a fold scoring a day it was fit on did not raise")
    except ValueError as e:
        assert "fit on" in str(e)
    early = rows.copy()
    k = early.index[early["tier"] == "T1-holdout"][0]
    early.loc[k, "date"] = S.HOLDOUT_START - pd.Timedelta(days=1)
    raises(early, "outside")
    raises(rows.assign(sel=""), "X-SEL")
    raises(pd.concat([rows, rows.iloc[[0]]], ignore_index=True), "emitted twice")


def test_westside_first_scored_season_sel_and_the_development_stamp():
    rows = _rows(_served())
    stamp = rows.attrs["stamp"]
    assert stamp["first_scored_season"] == {"westside": "2017-18", "north_shore": "2016-17", "central": "2016-17", "southeast": "2016-17"}
    assert stamp["tiers"]["T2"]["label"] == S.T2_LABEL == "development (selection-contaminated)"
    assert stamp["set"] == _served() and stamp["geography"] == "geo_v1" and stamp["protocol"].startswith(f"{SP.PROTOCOL_VERSION}@")
    assert stamp["basins"] == list(KEYS) and "citywide" not in set(rows["basin"])
    # Westside's 2016-17 fold is emitted for composition; none of its days is ledger_known (X-S2-ARCHIVE / UNCOV)
    ws = rows[(rows["basin"] == "westside") & (rows["fold"] == "2016-17")]
    lk = TR.ledger_known("geo_v1", end=AS_OF)
    lk = lk[lk["basin"] == "westside"].set_index("date")["known"]
    assert len(ws) == 365 and not lk.reindex(ws["date"]).fillna(False).astype(bool).any()
    assert lk.reindex(rows.loc[(rows["basin"] == "westside") & (rows["fold"] == "2017-18"), "date"]).astype(bool).any()
    assert (rows.loc[rows["tier"] == "T1", "sel"] == "post_selected").all()
    assert (rows.loc[rows["tier"] == "T1-holdout", "sel"] == "holdout_selected").all()
    t2 = rows[rows["tier"] == "T2"]                             # X-SEL is by date: the holdout seasons inside T2 carry it too
    assert ((t2["sel"] == "holdout_selected") == (t2["date"] >= S.HOLDOUT_START)).all()


def test_every_entry_is_predicted_by_the_same_fitted_folds():
    fitted = _fit(_served())
    rain = _frames(True)
    wet = {s: f.assign(**{c: f[c] * 1.5 for c in ("precip_avg", "rain_2d_cum", "rain_3d_cum")}) for s, f in rain.items()}
    late = {s: f[f["date"] >= pd.Timestamp("2024-01-20")] for s, f in wet.items()}   # a lead archive's reach
    both = fitted.predict({"rain": rain, "L0": {s: f.copy() for s, f in rain.items()}, "L1": wet, "L2": late})
    one = fitted.predict({"rain": rain})
    a, b = both[both["entry"] == "rain"].reset_index(drop=True), one.reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
    l0 = both[both["entry"] == "L0"].reset_index(drop=True)
    assert np.array_equal(l0["p"], a["p"]) and np.array_equal(l0["v_hat"], a["v_hat"])
    l1 = both[both["entry"] == "L1"].reset_index(drop=True)
    assert (np.abs(l1["p"] - a["p"]) > 1e-6).any() and (l1[["date", "basin", "tier", "fold"]] == a[["date", "basin", "tier", "fold"]]).all().all()
    l2 = both[both["entry"] == "L2"]
    assert l2["date"].min() == pd.Timestamp("2024-01-20") and set(l2["tier"]) == set(S.TIERS)
    assert set(l2.loc[l2["tier"] == "T2", "fold"]) == {"2023-24", "2024-25"}
    m = l1.merge(l2, on=["date", "basin", "tier", "fold"], suffixes=("", "_l2"))   # a shorter batch: 1 ulp of BLAS noise at most
    assert len(m) == len(l2) and np.abs(m["p"] - m["p_l2"]).max() < 1e-12 and np.abs(m["v_hat"] - m["v_hat_l2"]).max() < 1e-12


def test_bad_inputs_raise():
    fitted = _fit(_served())
    rain = _frames(True)

    def raises(fn, words):
        try:
            fn()
        except (ValueError, KeyError, TypeError, FileNotFoundError) as e:
            assert words in str(e), str(e)
            return
        raise AssertionError(f"no raise for {words!r}")

    raises(lambda: fitted.predict({"lead_one": rain}), "unknown entry")
    raises(lambda: fitted.predict({"rain": {"avg": rain["avg"]}}), "SF Downtown")
    raises(lambda: fitted.predict({}), "at least one entry")
    raises(lambda: fitted.predict({"rain": {s: f.drop(columns=["rain_max6h"]) for s, f in rain.items()}}), "lacks")
    holed = {s: f.copy() for s, f in rain.items()}
    holed["avg"].loc[holed["avg"]["date"] == pd.Timestamp("2026-01-05"), "rain_7d_cum"] = np.nan
    raises(lambda: fitted.predict({"rain": holed}), "missing on 2026-01-05")
    short = {"avg": rain["avg"], "SF Downtown": rain["SF Downtown"].iloc[:-1]}
    raises(lambda: fitted.predict({"rain": short}), "different days")
    raises(lambda: fitted.predict({"rain": {s: f[f["date"] < pd.Timestamp("2016-07-01")] for s, f in rain.items()}}), "holds no day")
    raises(lambda: S.fit(_served(), tiers=("T3",)), "X-ALL-INSAMPLE")
    raises(lambda: S.fit(_served(), tiers=("T4",)), "unknown tiers")
    raises(lambda: S.load_set(_served(), root="stages_candidates"), "unknown root")
    raises(lambda: S.load_set("logit_v1", root="served"), "the served set is")
    raises(lambda: S.load_set("no_such_set", root="candidates"), "no candidate")
    raises(lambda: S.fit("gb_v1", "candidates", tiers=("T1", "T2")), "its finals only")
    # T0 is T1's fold after the freeze, never refit: the finals themselves (a gb set has it too)
    t0 = S.fit(_served(), tiers=("T0",))
    assert [(f.tier, f.fold, f.start) for f in t0.folds] == [("T0", S.FOLD_FINAL, X.freeze_date() + pd.Timedelta(days=1))]
    assert all(t0.folds[0].weights[k] is t0.set.models[k] and t0.folds[0].heads[k] is t0.set.heads[k] for k in KEYS)
    assert [f.tier for f in S.fit("gb_v1", "candidates", tiers=S.FINAL_TIERS).folds] == ["T1", "T0"]


def test_candidates_logit_v1_and_gb_v1_load_and_reproduce_their_post_training_p():
    for name, tiers in (("logit_v1", S.TIERS), ("gb_v1", ("T1",))):
        rows = _rows(name, "candidates", tiers)
        assert set(rows["tier"]) == set(tiers) and rows.attrs["stamp"]["family"] == ("gb" if name == "gb_v1" else "logit")
        sc = C.load_scorecard(name)
        assert sc["input_rules_post"] == ["gauge_outage_v1"]
        stored = _stored(sc, "p")
        t1 = rows[rows["tier"] == "T1"]
        assert len(t1) == POST_DAYS * len(KEYS)
        worst = max(abs(round(p, 3) - stored[(d, k)]) for d, k, p in zip(t1["date"], t1["basin"], t1["p"]))
        assert worst < 1e-9, (name, worst)
    gb = _fit("gb_v1", "candidates", ("T1",)).set
    assert any(m["calibration_offset"] > 0 for m in gb.models.values())   # the dry-day offset is on the path


def test_the_served_t2_refit_takes_seconds_not_minutes():
    t0 = time.time()
    fitted = S.fit(_served(), tiers=("T2",))
    dt = time.time() - t0
    assert len(fitted.folds) == 9 and all(len(f.weights) == len(f.heads) == 4 for f in fitted.folds)
    print(f"   served T2: 9 folds x 4 basins + volume heads fit in {dt:.1f}s")
    assert dt < 120, dt


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
