"""stages_candidates (P8; STAGES_DESIGN.md Part B 13, §7 "SFPUC4 stage candidates", §2.6 stamps): the
geography-aware saver and loader of stage candidates.

  - an S2 component round-trips: same predictions, every pickle stamped with the set's geography,
    pipeline, name, component, fold and basin; the manifest lists every file with its sha256;
  - S3 and S4 spec files round-trip and are checked against design §7's schema and the geography;
  - bad payloads raise before anything is written: a missing or foreign basin key (GEO_V1's
    'southeast'), a negative weight (Chase: "no odd weights"), a fitted dry-day offset, a head with
    another target, a component of another geography, a bad name, an unknown kind, an S2 section
    with no training window, a fit whose span runs past it (Part B 1), a head under the 20-event
    floor that is not the declared fallback (Part B 7);
  - the loader catches an edited, missing or unlisted file and a pickle stamped for another set;
  - a re-saved component replaces its old files;
  - the directory is not data/models/candidates/, so candidates.list_candidates never lists a stage
    candidate, and list_sets skips the '_bakeoff' working directory; a root inside the repository
    other than stages_candidates/ itself (candidates/, the served bundle's directory, a set or a '_…'
    working directory inside it) raises before anything is written;
  - the assemble step: S5's choice is the variant better than no correction on the perfect feed (never
    a comparison that reads the feed's own silence) and every degraded seed, else link_zone_swap, read on
    S5's window before post-training only (T1 and the whole S5 window never), from a current served build;
    assembling needs the set's own S2; an assembled candidate's S1, S3, S4 and S5 record that they
    were fit on its own parts.
Writes only to temporary directories. Run: venv/bin/python tests/test_stages_candidates.py
"""
from __future__ import annotations

import hashlib
import json
import pickle
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

import candidates as C  # noqa: E402
import compose_v2 as C2  # noqa: E402
import leaderboard as L  # noqa: E402
import stages_candidates as SC  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402
from sklearn.linear_model import LinearRegression, LogisticRegression  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import FunctionTransformer, StandardScaler  # noqa: E402

GEO = G.get("sfpuc4_v1")


def _toy(n=500, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.gamma(0.4, 0.6, size=(n, len(L.FEATS))), columns=L.FEATS)
    y = (X["precip_avg"] + 0.5 * X["rain_lag1d"] + rng.normal(0, 0.3, n) > 1.1).astype(int)
    return X, y


def _model(seed=0) -> dict:
    X, y = _toy(seed=seed)
    m = L.make_shared_model(1.0, L.SHARED_DESIGNS["four"]).fit(X, y)
    return {"model": m, "features": list(L.FEATS), "rain_source": "SF Downtown", "calibration_offset": 0.0, "family": "logit"}


def _head(seed=0) -> dict:
    X, _ = _toy(seed=seed)
    v = np.log1p(3 * X["precip_avg"] + X["rain_max3h"])
    m = Pipeline([("log1p", FunctionTransformer(np.log1p, validate=True)), ("ols", LinearRegression(positive=True))])
    m.fit(X[["precip_avg", "rain_max3h"]], v)
    return {"model": m, "features": ["precip_avg", "rain_max3h"], "rain_source": "SF Downtown",
            "target": "log1p_volume_mg", "n_events": 25, "kind": "loglinear", "span": ["2016-10-01", "2025-10-31"]}


def _s2(holdouts=True) -> dict:
    p = {"geography": GEO.version, "component": "four_nonneg_sfpuc4",
         "models": {k: _model(i) for i, k in enumerate(GEO.keys)}, "volume": {k: _head(i) for i, k in enumerate(GEO.keys)},
         "spec": {"contender": "four", "C": {k: 1.0 for k in GEO.keys}, "trained_through": "2025-10-31"}}
    if holdouts:
        p["holdout_models"] = {k: _model(i + 10) for i, k in enumerate(GEO.keys)}
        p["holdout_volume"] = {k: {**_head(i + 10), "span": ["2016-10-01", "2023-06-30"]} for i, k in enumerate(GEO.keys)}
        p["spec"]["holdout_start"] = "2023-07-01"
    return p


def _s3() -> dict:
    """A minimal s3_links spec compose_v2 accepts: identity links at 1, Westside's at a constant share."""
    links = {lk.id: {"basin": lk.basin, "zone": lk.zone, "outfalls": list(lk.outfalls), "evidence": C2.link_evidence(lk),
                     "identity": lk.identity, "share": {"kind": "identity"} if lk.identity else {"kind": "constant", "p": 0.8},
                     "vol_share": 1.0 if lk.identity else 0.5}
             for lk in GEO.links}
    union = {z: {"rule": "max"} for z in ZONES if len(GEO.links_into(z)) > 1}
    return {"geography": GEO.version, "component": "split_v3", "kind": "links", "links": links, "union": union,
            "cofire": {}, "fit": {}}


def _s4() -> dict:
    """A minimal zone_v3 spec compose_v2 accepts: flat buckets, a constant background."""
    return {"geography": GEO.version, "component": "zone_v3", "kind": "zone_v3", "unit": "zone",
            "background": {"kind": "constant", "p": {z: 0.02 for z in ZONES}},
            "buckets": {z: {k: 0.1 for k in C2.BUCKET_KEYS} for z in ZONES}, "zone_median_mg": {z: 1.0 for z in ZONES},
            "monotone": True, "sources": ["datasf"], "fit": {}}


def _raises(fn) -> str:
    """The message of what ``fn`` raised; AssertionError if it raised nothing."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return str(e)
    raise AssertionError(f"{fn} did not raise")


class _Tmp:
    def __enter__(self):
        self.path = Path(tempfile.mkdtemp())
        return self.path

    def __exit__(self, *a):
        shutil.rmtree(self.path, ignore_errors=True)


def test_an_s2_component_round_trips_with_its_stamps():
    X, _ = _toy(seed=7)
    with _Tmp() as root:
        pay = _s2()
        d = SC.save_component("sfpuc4_toy_v1", "s2", pay, root=root)
        s = SC.load_set("sfpuc4_toy_v1", root=root)
        assert s.path == d and s.geo.version == "sfpuc4_v1" and s.keys == GEO.keys
        assert s.components == {"s2": "four_nonneg_sfpuc4"} and s.manifest["pipeline"] == SC.PIPELINE
        assert s.manifest["s2"] == pay["spec"] and s.manifest["basin_names"]["south"] == "South"
        for part, got in (("models", s.models), ("holdout_models", s.holdout_models)):
            for k in GEO.keys:
                want = pay[part][k]["model"].predict_proba(X)[:, 1]
                assert np.array_equal(got[k]["model"].predict_proba(X)[:, 1], want), (part, k)
                fold = "final" if part == "models" else "pre_holdout"
                assert {got[k][f] for f in ("geography", "pipeline", "set", "component", "fold", "basin")} == \
                    {"sfpuc4_v1", "stages_v1", "sfpuc4_toy_v1", "four_nonneg_sfpuc4", fold, k}
        for k in GEO.keys:
            assert np.array_equal(s.volume[k]["model"].predict(X[["precip_avg", "rain_max3h"]]),
                                  pay["volume"][k]["model"].predict(X[["precip_avg", "rain_max3h"]]))
        names = {f"{k}_{w}{x}.pkl" for k in GEO.keys for w in ("model", "volume") for x in ("", ".pre_holdout")}
        assert set(s.manifest["files"]) == names == set(s.manifest["stamps"]["s2"]["files"])
        assert s.manifest["stamps"]["s2"]["protocol"].startswith("stages_v3@")
        assert [m["name"] for m in SC.list_sets(root=root)] == ["sfpuc4_toy_v1"]


def test_spec_files_round_trip_and_follow_the_design_schema():
    with _Tmp() as root:
        SC.save_component("sfpuc4_toy_v1", "s2", _s2(holdouts=False), root=root)
        SC.save_component("sfpuc4_toy_v1", "s3_links", _s3(), root=root)
        SC.save_component("sfpuc4_toy_v1", "s4_quality", _s4(), root=root)
        SC.save_component("sfpuc4_toy_v1", "s1", {"geography": GEO.version, "component": "icon_seamless"}, root=root)
        s = SC.load_set("sfpuc4_toy_v1", root=root)
        assert s.components == {"s2": "four_nonneg_sfpuc4", "s3": "split_v3", "s4": "zone_v3", "s1": "icon_seamless"}
        assert set(s.s3_links["links"]) == {lk.id for lk in GEO.links} and s.s3_links["pipeline"] == "stages_v1"
        C2.check_s3_spec(SC._compose_view(s.s3_links), GEO)                # what compose_v2 reads back
        C2.check_s4_spec(SC._compose_view(s.s4_quality), GEO)
        assert s.s4_quality["set"] == "sfpuc4_toy_v1" and s.holdout_models == {} and s.citywide is None
        bad = _s3()
        bad["links"]["south>east"]["outfalls"] = ["CSD-040"]
        assert "south>east" in _raises(lambda: SC.save_component("sfpuc4_toy_v1", "s3_links", bad, root=root))
        bad = _s3()
        bad["union"] = {}
        assert "union" in _raises(lambda: SC.save_component("sfpuc4_toy_v1", "s3_links", bad, root=root))
        bad = _s4()
        del bad["buckets"]["east"]
        assert "buckets" in _raises(lambda: SC.save_component("sfpuc4_toy_v1", "s4_quality", bad, root=root))
        bad = _s4()
        del bad["sources"]
        assert "sources" in _raises(lambda: SC.save_component("sfpuc4_toy_v1", "s4_quality", bad, root=root))
        bad = _s4()
        bad["monotone"] = False
        assert "monotone" in _raises(lambda: SC.save_component("sfpuc4_toy_v1", "s4_quality", bad, root=root))
        # the failed saves wrote nothing: the set still loads as it was
        assert SC.load_set("sfpuc4_toy_v1", root=root).s3_links["component"] == "split_v3"


def test_bad_payloads_raise_before_anything_is_written():
    with _Tmp() as root:
        def save(p, name="sfpuc4_toy_v1", kind="s2"):
            return lambda: SC.save_component(name, kind, p, root=root)
        p = _s2()
        del p["models"]["south"]
        assert "basins" in _raises(save(p))
        p = _s2()
        p["models"]["southeast"] = p["models"].pop("south")             # a GEO_V1 key in an SFPUC4 set
        assert "basins" in _raises(save(p))
        p = _s2()
        X, y = _toy()
        X["rain_lag1d"] *= -1                                            # an unconstrained fit takes a negative weight
        p["models"]["central"]["model"] = Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression())]).fit(X, y)
        assert "negative weight" in _raises(save(p))
        p = _s2()
        p["models"]["westside"]["calibration_offset"] = 0.01
        assert "offset" in _raises(save(p))
        p = _s2()
        p["volume"]["north_shore"]["target"] = "volume_mg"
        assert "target" in _raises(save(p))
        p = _s2()
        del p["holdout_volume"]
        assert "holdout" in _raises(save(p))
        p = _s2()
        del p["component"]
        assert "component" in _raises(save(p))
        p = _s2()
        del p["spec"]
        assert "s2 section" in _raises(save(p))                          # the S2 section is never defaulted
        p = _s2()
        del p["spec"]["trained_through"]
        assert "trained_through" in _raises(save(p))
        p = _s2()
        del p["spec"]["holdout_start"]
        assert "holdout_start" in _raises(save(p))
        p = _s2()
        p["volume"]["central"]["span"] = ["2016-10-01", "2025-11-01"]   # a final head that read a post-training day
        assert "X-ALL-INSAMPLE" in _raises(save(p))
        p = _s2()
        p["holdout_models"]["south"]["span"] = ["2016-10-01", "2023-07-01"]   # a sibling that read the holdout's first day
        assert "X-ALL-INSAMPLE" in _raises(save(p))
        p = _s2()
        p["volume"]["south"]["n_events"] = 15                           # Part B 7: under the floor, only the fallback
        assert "fallback" in _raises(save(p))
        assert "unknown geography" in _raises(save({**_s2(), "geography": "sfpuc5"}))
        assert "kind" in _raises(save(_s2(), kind="s6"))
        for name in ("_bakeoff", "Bad Name", "x"):
            assert "name" in _raises(save(_s2(), name=name))
        assert not any(root.iterdir()), "a refused save wrote files"
        p = _s2()
        p["volume"]["south"] = {**p["volume"]["south"], "n_events": 15, "kind": SC.FALLBACK_KIND}
        SC.save_component("sfpuc4_toy_v1", "s2", p, root=root)       # the declared fallback may sit under the floor
        assert SC.load_set("sfpuc4_toy_v1", root=root).volume["south"]["kind"] == SC.FALLBACK_KIND
        v1 = {"geography": "geo_v1", "component": "x", "kind": "zone_v3"}
        assert "geo_v1" in _raises(save(v1, kind="s4_quality"))     # one set, one geography


def test_the_loader_catches_edits_missing_and_unlisted_files_and_foreign_stamps():
    with _Tmp() as root:
        d = SC.save_component("sfpuc4_toy_v1", "s2", _s2(), root=root)
        f = d / "central_model.pkl"
        raw = f.read_bytes()
        f.write_bytes(raw + b"\0")
        assert "changed" in _raises(lambda: SC.load_set("sfpuc4_toy_v1", root=root))
        f.write_bytes(raw)
        (d / "extra_model.pkl").write_bytes(raw)
        assert "does not list" in _raises(lambda: SC.load_set("sfpuc4_toy_v1", root=root))
        (d / "extra_model.pkl").unlink()
        obj = pickle.loads(raw)
        obj["geography"] = "geo_v1"                                      # stamped for another geography …
        f.write_bytes(pickle.dumps(obj))
        man = json.loads((d / "manifest.json").read_text())
        man["files"]["central_model.pkl"] = SC._sha(f)                   # … with the manifest's sha made to agree
        (d / "manifest.json").write_text(json.dumps(man))
        assert "geography" in _raises(lambda: SC.load_set("sfpuc4_toy_v1", root=root))
        f.unlink()
        assert "missing" in _raises(lambda: SC.load_set("sfpuc4_toy_v1", root=root))
        assert "no stage candidate" in _raises(lambda: SC.load_set("sfpuc4_none_v1", root=root))


def test_a_resaved_component_replaces_its_files():
    with _Tmp() as root:
        SC.save_component("sfpuc4_toy_v1", "s2", _s2(holdouts=True), root=root)
        d = SC.save_component("sfpuc4_toy_v1", "s2", _s2(holdouts=False), root=root)
        s = SC.load_set("sfpuc4_toy_v1", root=root)
        assert s.holdout_models == {} and not list(d.glob("*.pre_holdout.pkl"))
        assert set(s.manifest["files"]) == {f"{k}_{w}.pkl" for k in GEO.keys for w in ("model", "volume")}


def test_check_nonnegative_reads_every_linear_step():
    X, y = _toy()
    SC.check_nonnegative(_model()["model"], "toy")
    SC.check_nonnegative(_head()["model"], "toy head")
    X2 = X.copy()
    X2["precip_avg"] *= -1
    bad = Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression())]).fit(X2, y)
    assert "negative" in _raises(lambda: SC.check_nonnegative(bad, "toy"))


def test_stage_candidates_are_not_model_check_candidates():
    assert SC.ROOT == ROOT / "features" / "forecast" / "data" / "models" / "stages_candidates"
    assert SC.ROOT != C.CANDIDATES_DIR and C.CANDIDATES_DIR not in SC.ROOT.parents and SC.ROOT not in C.CANDIDATES_DIR.parents
    listed = {m.get("name") for m in C.list_candidates()}
    assert not listed & {m["name"] for m in SC.list_sets()}
    assert not any(n.startswith("sfpuc4_") for n in listed)
    with _Tmp() as root:
        (root / "_bakeoff").mkdir()
        (root / "_bakeoff" / "manifest.json").write_text("{}")
        assert SC.list_sets(root=root) == []
    # Part B 13: inside the repository the saver writes under stages_candidates/ only
    pay = _s2(holdouts=False)
    bads = (C.CANDIDATES_DIR, C.SERVE_DIR, ROOT, SC.ROOT / ".." / "stages", SC.ROOT / "_scratch", SC.ROOT / "sfpuc4_shared8_v1")
    for bad in bads:                         # the guard alone first (pure): if it is broken, nothing below writes
        assert "stages_candidates" in _raises(lambda: SC._root(bad)), bad
    for bad in bads:
        assert "stages_candidates" in _raises(lambda: SC.save_component("sfpuc4_toy_v1", "s2", pay, root=bad))
        assert not (Path(bad) / "sfpuc4_toy_v1").exists()                 # refused before anything is written
    assert SC._root(SC.ROOT / "." ) == SC.ROOT / "."                        # stages_candidates/ itself, however spelled


# ── the assemble step: S5's choice, and what assembling needs ──────────────

NONE = (0.01, -0.02, 0.04, "no clear difference")
S5_WINDOW_BEFORE_POST = "T1-holdout"     # S5's window (2023-07-01 →) less post-training: typed here, never read from SC
BETTER = (-0.03, -0.05, -0.01, "better")


def _s5_scores(cells: dict, window: str | None = None, others: dict | None = None) -> dict:
    """A served scores.json's S5 block: cells {variant: {feed | '*': (Δ, lo, hi, verdict)}} against no correction, on
    the perfect feed ('oracle') and every degraded seed, in ``window`` (default the choice's, S5's window before
    post-training); ``others`` {window: cells} fills other windows (T1, 'S5'), which the choice must never read."""
    feeds = ["oracle"] + [f"degraded:{i}" for i in range(1, SC.S5_DEGRADED + 1)]
    pooled = {f: {} for f in feeds}
    for w, cs in {**(others or {}), window or S5_WINDOW_BEFORE_POST: cells}.items():
        for f in feeds:
            pooled[f][w] = {}
            for v, by in cs.items():
                d, lo, hi, verdict = by.get(f, by.get("*"))
                pooled[f][w][v] = {"delta_vs_plain": {"delta": d, "lo": lo, "hi": hi, "verdict": verdict, "n": 100}}
    return {"s5": {"pooled": pooled}}


def test_s5_is_the_variant_that_beats_no_correction_else_link_zone_swap():
    """s5_choice: a variant the geography replays that is better than no correction on the perfect feed and on every
    degraded seed (the lower mean Δ of two such), never one whose perfect-feed comparison reads the feed's own
    silence (Part B 9: the downgrade, whatever its cells say); none → link_zone_swap, protocol §8's S5 winner.
    live_v2's GEO_V1 rule sets are not SFPUC4's to take; a missing cell or feed raises."""
    import stages_build as SB
    variants = [v for v in SB.s5_variants(GEO) if v != "plain"]
    assert "basin_swap" not in variants and "all_floors" not in variants and "link_zone_swap" in variants
    base = {v: {"*": NONE} for v in variants}
    got = SC.s5_choice(_s5_scores(base), GEO, "served_set")
    assert got["component"] == SC.S5_DEFAULT == "link_zone_swap" and "no correction variant beats" in got["why"]
    assert set(got["table"]) == set(variants) and not any(t["beats_no_correction"] for t in got["table"].values())
    assert got["served_set"] == "served_set" and got["table"]["zone_swap"]["perfect"]["verdict"] == NONE[3]
    assert SC.s5_choice(_s5_scores({**base, "zone_swap": {"*": BETTER}}), GEO)["component"] == "zone_swap"
    assert SC.s5_choice(_s5_scores({**base, "zone_swap": {"*": BETTER, "degraded:3": NONE}}), GEO)["component"] == "link_zone_swap"
    assert SC.s5_choice(_s5_scores({**base, "zone_swap": {"*": NONE, "oracle": BETTER}}), GEO)["component"] == "link_zone_swap"
    two = {**base, "zone_swap": {"*": BETTER}, "link_swap": {"*": (-0.04, -0.06, -0.02, "better")}}
    t = SC.s5_choice(_s5_scores(two), GEO)
    assert t["component"] == "link_swap" and "lowest mean" in t["why"]
    # identical rows (every observation names an outfall): the §8 primary's name wins the tie, else VARIANTS order
    same = {**base, **{v: {"*": BETTER} for v in ("link_swap", "zone_swap", "link_zone_swap")}}
    assert SC.s5_choice(_s5_scores(same), GEO)["component"] == "link_zone_swap"
    pair = {**base, **{v: {"*": BETTER} for v in ("zone_swap", "link_swap")}}
    assert SC.s5_choice(_s5_scores(pair), GEO)["component"] == "link_swap"
    circ = SC.s5_choice(_s5_scores({**base, "downgrade": {"*": BETTER}}), GEO)
    assert circ["component"] == "link_zone_swap" and circ["table"]["downgrade"]["perfect_circular"] is True
    assert not circ["table"]["downgrade"]["beats_no_correction"]
    nod = SC.s5_choice(_s5_scores({v: c for v, c in base.items() if v != "downgrade"}), GEO)   # a build without its cells
    assert nod["table"]["downgrade"]["perfect"] is None and nod["component"] == "link_zone_swap"
    assert "no zone_swap" in _raises(lambda: SC.s5_choice(_s5_scores({v: c for v, c in base.items() if v != "zone_swap"}), GEO))
    short = _s5_scores(base)
    del short["s5"]["pooled"]["degraded:5"]
    assert "rebuild it first" in _raises(lambda: SC.s5_choice(short, GEO))
    # protocol §2: the choice reads S5's window before post-training only (2023-07-01 → 2025-10-31), never T1 or the
    # whole S5 window, which hold the post-training days that confirm a design
    assert SC.S5_CHOICE_WINDOW == "T1-holdout" and got["window"] == "T1-holdout" and "2025-10-31" in got["rule"]
    later = {w: {**base, "zone_swap": {"*": BETTER}} for w in ("T1", "S5")}
    assert SC.s5_choice(_s5_scores(base, others=later), GEO)["component"] == "link_zone_swap"
    assert SC.s5_choice(_s5_scores({**base, "zone_swap": {"*": BETTER}}, others={w: base for w in later}), GEO)["component"] == "zone_swap"
    assert "rebuild it first" in _raises(lambda: SC.s5_choice(_s5_scores(base, window="S5"), GEO))


def test_s5_is_chosen_only_on_a_current_served_build():
    """served_s5_scores reads the served set's written scores only while its build is current (inputs, code and
    protocol as the manifest pins them); a stale or other-protocol build refuses, so a stale table never picks S5."""
    import stages_build as SB
    import stages_s2 as S2
    with _Tmp() as tmp:
        d = Path(tmp) / SB.served_name()
        d.mkdir()
        (d / "scores.json").write_text(json.dumps(_s5_scores({})))
        good = {"inputs": {}, "code": {"features/forecast/src/models/stages_build.py": hashlib.sha256(
            (ROOT / "features/forecast/src/models/stages_build.py").read_bytes()).hexdigest()}, "protocol": S2.protocol_stamp()}
        old = SB.STAGES_DIR
        try:
            SB.STAGES_DIR = Path(tmp)
            for what, man in (("current", good), ("stale code", {**good, "code": {"features/forecast/src/models/stages_build.py": "0" * 64}}),
                              ("other protocol", {**good, "protocol": "stages_v2@0"})):
                (d / "manifest.json").write_text(json.dumps(man))
                if what == "current":
                    assert SC.served_s5_scores() == _s5_scores({}), what
                else:
                    assert "stale" in _raises(lambda: SC.served_s5_scores()), what
            assert SC.served_s5_scores(d / "scores.json") == _s5_scores({})        # an explicit file is read as given
        finally:
            SB.STAGES_DIR = old


def test_assemble_needs_the_sets_own_s2():
    with _Tmp() as root:
        SC.save_component("sfpuc4_nos2_v1", "s3_links", _s3(), root=root)
        assert "holds no S2" in _raises(lambda: SC.assemble("sfpuc4_nos2_v1", root=root))


def test_the_assembled_candidates_components():
    """Every stage candidate holding all five components was assembled consistently: S1 the served weather model, S3
    fit on the set's own S2 (stages_s3_links.candidate_s2), S4 v3 at its S3's φ with its own v̂, S5 the choice its
    own recorded table gives."""
    import stages_entries as E
    import stages_s4_v3 as S4V3
    for m in SC.list_sets():
        if set(m["components"]) != {"s1", "s2", "s3", "s4", "s5"}:
            continue
        st = SC.load_set(m["name"])
        assert st.components["s1"] == E.served_weather_model() == m["s1"]["weather_model"], m["name"]
        assert st.s3_links["sources"]["s2"]["name"] == m["name"], m["name"]
        assert st.s4_quality["fit"]["s2_set"] == m["name"] == st.s4_quality["fit"]["s3_set"], m["name"]
        assert all(r["v_hat_from"] == m["name"] and r["history_geography"] == S4V3.GEOGRAPHY for r in st.s4_quality["fit"]["folds"])
        s5 = m["s5"]
        winners = [v for v, t in s5["table"].items() if t["beats_no_correction"]]
        assert st.components["s5"] == s5["component"] and (s5["component"] in winners if winners else s5["component"] == SC.S5_DEFAULT)
        assert not any(t["beats_no_correction"] and t["perfect_circular"] for t in s5["table"].values())


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
