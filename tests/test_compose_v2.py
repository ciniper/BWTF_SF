"""compose_v2: S3 → S4 → OUT with injection hooks (STAGES_DESIGN.md Part C §3.3–§3.6,
§6 changes 1–2, §7, §8 P6; Part A A1–A2; Part B 2, 5, 6, 9).

Pins that the GEO_V1 adapter is the served composition bit for bit (impact.compose +
groups.zone_risks on every served-scorecard day, inputs rebuilt the way the scorecard
was) and within rounding of the stored artifact; that nothing injected is the identity;
that observations enter after the split (a Sea Cliff outfall sets Baker & China and lifts
Ocean Beach to the ledger's co-firing share; an Islais Creek flag sets East under either
geography); that every East union rule stays inside the Fréchet bounds; that raising any
input never lowers anything downstream; and that S4's oracle and chained entries are one
algebra. Counts read the committed data and carry an as-of date (Part B 23). The
scorecard and the served files are read only.

    venv/bin/python tests/test_compose_v2.py
"""
from __future__ import annotations

import ast
import dataclasses
import functools
import gzip
import json
import pickle
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
MODELS = FORECAST / "src" / "models"
for p in (ROOT, FORECAST, MODELS, FORECAST / "src" / "collectors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compose_v2 as C  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

SERVE_DIR = FORECAST / "data" / "models"
LEDGER_AS_OF = pd.Timestamp("2026-04-22")   # the committed ledger's last event day (tests/test_geography.py AS_OF)
DATA_END = "2026-08-17"                     # the served scorecard's span end at this writing
GEO1, GEO4 = G.get("geo_v1"), G.get("sfpuc4_v1")
STORM = ("2022-12-20", "2023-01-31")        # the New Year 2023 atmospheric rivers: every basin overflowed


# ── fixtures (read-only) ───────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _scorecard() -> dict:
    with gzip.open(SERVE_DIR / "scorecard.json.gz", "rt") as f:
        return json.load(f)


@functools.lru_cache(maxsize=None)
def _served_inputs() -> dict:
    """{"pre" | "post": (p, v̂)} — the served stage-1 probabilities and volume-head sizes,
    unrounded, rebuilt as the scorecard was: train_v4.build_scorecard over
    build_dataset frames from the served pickles; pre-training days on the raw record,
    post-training days on the artifact's input_rules_post (candidates.rescore_post)."""
    import train_v4 as T
    import leaderboard  # noqa: F401  (logit pickles reference leaderboard.add_hinges)
    from groups import BASIN_KEYS
    sc = _scorecard()
    finals, chosen = {}, {}
    for basin in T.APP_BASINS:
        key = BASIN_KEYS[basin]
        with open(SERVE_DIR / f"{key}_model.pkl", "rb") as f:
            finals[key] = pickle.load(f)
        chosen[basin] = finals[key].get("rain_source", "avg")
    heads, _ = T.stage2_from_served()
    sources = sorted(set(chosen.values()) | set(T.RAIN_SOURCES) | {h.get("rain_source", "avg") for h in heads.values()})
    out = {}
    for tag, rules in (("pre", None), ("post", sc["input_rules_post"])):
        fr = T.build_dataset(end=pd.Timestamp(sc["span"][1]), sources=sources, input_rules=rules, wind=True)[0]   # a set may read the wind
        idx = pd.DatetimeIndex(fr["avg"]["date"])
        p = pd.DataFrame({BASIN_KEYS[b]: T.calibrated(finals[BASIN_KEYS[b]], fr[chosen[b]]) for b in T.APP_BASINS}, index=idx)
        v = pd.DataFrame({BASIN_KEYS[b]: T.predicted_volume(heads[b], fr[heads[b].get("rain_source", chosen[b])])
                          for b in T.APP_BASINS}, index=idx)
        out[tag] = (p, v)
    return out


@functools.lru_cache(maxsize=None)
def _ledger() -> pd.DataFrame:
    import csd_labels
    return csd_labels.load_events()


@functools.lru_cache(maxsize=None)
def _shares(version: str) -> dict:
    return C.cofire(G.get(version), _ledger(), end=LEDGER_AS_OF)


@functools.lru_cache(maxsize=None)
def _rain() -> pd.Series:
    import train_v4 as T
    return T.rain_series("avg", ["gauge_outage_v1"])[0]   # two-gauge mean, dead-gauge runs masked


def _served_impact():
    """(smoothed table, split) exactly as serving loads them (live_dashboard)."""
    import stage2 as S2
    from impact import smooth_table
    spec = json.loads((SERVE_DIR / "stage2.json").read_text())
    table = smooth_table(spec["impact_table"]) if spec.get("impact_table") else \
        smooth_table(json.loads((SERVE_DIR / "impact_table.json").read_text()))
    return spec, table, S2.make_split(spec)


@functools.lru_cache(maxsize=None)
def _adapter(round_out: bool = True) -> dict:
    return C.geo_v1_adapter_specs(cofire_shares=_shares("geo_v1"), round_out=round_out)


def _zone_v3_fixture(geo) -> dict:
    """A zone_v3-shaped S4 spec for exercising SFPUC4 runs (a fixture, not a fit): each
    zone's tails are the max of its geo_v1 groups' served tails, made non-increasing;
    its size median the largest of theirs; a rain background whose fixture slopes only
    exercise the features."""
    ad = _adapter()["s4"]
    buckets, med, coef = {}, {}, {}
    for zk in ZONES:
        lids = [lk.id for lk in GEO1.links_into(zk)]
        bk = {}
        for s in C.SIZES:
            other = "large" if s == "small" else "small"
            vals = []
            for b in C.BUCKET_ORDER:
                xs = [ad["buckets"][l][f"{b}_{s}"] if ad["buckets"][l][f"{b}_{s}"] is not None else ad["buckets"][l][f"{b}_{other}"]
                      for l in lids]
                vals.append(max((x for x in xs if x is not None), default=0.0))
            for b, x in zip(C.BUCKET_ORDER, np.minimum.accumulate(vals)):
                bk[f"{b}_{s}"] = float(x)
        buckets[zk] = bk
        med[zk] = max(ad["median_mg"][l] for l in lids)
        b0 = max(ad["background"]["p"][l] for l in lids)
        coef[zk] = {"intercept": float(np.log(b0 / (1 - b0))), "hinge_rain3_0.1": 1.0, "hinge_rain3_0.5": 0.5, "wet_season": 0.2}
    spec = {"geography": geo.version, "kind": "zone_v3", "unit": "zone", "buckets": buckets, "zone_median_mg": med,
            "background": {"kind": "logistic", "features": ["hinge_rain3_0.1", "hinge_rain3_0.5", "wet_season"], "coef": coef},
            "monotone": True}
    return C.check_s4_spec(spec, geo)


def _sfpuc4_inputs(tag: str = "pre", window=None) -> C.BasinInputs:
    """SFPUC4 basin inputs for exercising the algebra: the served GEO_V1 S2 output with
    Southeast standing in for South (until P8a fits SFPUC4's S2), plus the masked rain."""
    p, v = _served_inputs()[tag]
    ren = {"southeast": "south"}
    p, v = p.rename(columns=ren)[list(GEO4.keys)], v.rename(columns=ren)[list(GEO4.keys)]
    if window:
        p, v = p.loc[window[0]:window[1]], v.loc[window[0]:window[1]]
    return C.BasinInputs(p, v, _rain())


def _geo1_inputs(tag: str = "pre", window=None) -> C.BasinInputs:
    p, v = _served_inputs()[tag]
    if window:
        p, v = p.loc[window[0]:window[1]], v.loc[window[0]:window[1]]
    return C.BasinInputs(p, v)


def _sfpuc4_specs(union: str = "max") -> dict:
    return {"s3": C.benchmark_s3_spec(GEO4, "identity", union=union, shares=_shares("sfpuc4_v1")), "s4": _zone_v3_fixture(GEO4)}


def _frames_equal(a: C.Composition, b: C.Composition) -> None:
    for name, fa in a.frames().items():
        pd.testing.assert_frame_equal(fa, b.frames()[name], check_exact=True, obj=name)


def _legacy_lists(p: pd.DataFrame, v: pd.DataFrame):
    return ([dict(zip(p.columns, map(float, r))) for r in p.to_numpy()],
            [dict(zip(v.columns, map(float, r))) for r in v.to_numpy()])


# ── (1) the adapter is impact.compose + zone_risks, exactly ────────────────

def test_adapter_equals_impact_compose_on_every_scorecard_day():
    """With the policy (x(0) = 1, b = 0) the GEO_V1 adapter equals impact.compose +
    groups.zone_risks with max |Δ| = 0 — per legacy group and per zone — on all of the
    served scorecard's days, under both input treatments the artifact used. Unrounded
    it sits within impact.compose's 3-dp rounding (≤ 0.0005)."""
    from groups import GROUPS_BY_BASIN, zone_risks
    from impact import compose
    _, table, split = _served_impact()
    n_days = len(_scorecard()["days"])
    for tag, (p, v) in _served_inputs().items():
        assert len(p) == n_days, (tag, len(p), n_days)
        comp = C.compose(GEO1, _adapter(), C.BasinInputs(p, v))
        raw = C.compose(GEO1, _adapter(round_out=False), C.BasinInputs(p, v))
        probs, vols = _legacy_lists(p, v)
        G_ = comp.out.unit.to_numpy()
        Z_ = comp.out.zone.to_numpy()
        gcol = {lk.legacy_group: comp.out.unit.columns.get_loc(lk.id) for lk in GEO1.links}
        zcol = {zk: comp.out.zone.columns.get_loc(zk) for zk in ZONES}
        worst, worst_raw = 0.0, 0.0
        for i in range(len(p)):
            _, per_group = compose(table, GROUPS_BY_BASIN, probs, vols, i, split=split)
            for g, r in per_group.items():
                worst = max(worst, abs(G_[i, gcol[g]] - r))
                worst_raw = max(worst_raw, abs(raw.out.unit.iat[i, gcol[g]] - r))
            for zk, r in zone_risks(per_group).items():
                worst = max(worst, abs(Z_[i, zcol[zk]] - r))
        assert worst == 0.0, (tag, worst)
        assert worst_raw <= 0.0005 + 1e-12, (tag, worst_raw)
        print(f"   {tag}: {len(p)} days × {len(gcol)} groups + 4 zones, max |Δ| {worst} (unrounded {worst_raw:.6f})")


# ── (2) within rounding of the stored artifact ─────────────────────────────

def test_adapter_within_rounding_of_the_stored_scorecard():
    """The adapter on the rebuilt inputs lands ≤ 0.0025 from the stored zones.risk and
    groups.risk on every day (pre-training days from the raw record, post-training days
    from the artifact's input_rules_post). Measured at this writing: 0 on all 3,822 days
    (span 2016-03-01 → 2026-08-17)."""
    sc = _scorecard()
    tt = sc["trained_through"]
    comps = {tag: C.compose(GEO1, _adapter(), C.BasinInputs(p, v)) for tag, (p, v) in _served_inputs().items()}
    worst, at = 0.0, None
    for d in sc["days"]:
        comp = comps["post" if d["date"] > tt else "pre"]
        day = pd.Timestamp(d["date"])
        for zk, z in d["zones"].items():
            dz = abs(comp.out.zone.at[day, zk] - z["risk"])
            if dz > worst:
                worst, at = dz, (d["date"], zk)
        for lk in GEO1.links:
            dg = abs(comp.out.unit.at[day, lk.id] - d["groups"][lk.legacy_group]["risk"])
            if dg > worst:
                worst, at = dg, (d["date"], lk.legacy_group)
    assert sc["span"][1] >= DATA_END and len(sc["days"]) >= 3822, sc["span"]
    assert worst <= 0.0025, (worst, at)
    print(f"   stored vs adapter over {len(sc['days'])} days: worst |Δ| {worst} {at or ''}")


# ── (3) nothing injected → identity ────────────────────────────────────────

def test_nothing_injected_is_the_identity():
    empty = C.Inject(basin={}, link={}, zone={}, sample={})
    assert empty.empty and C.Inject().empty
    for geo, specs, inputs in ((GEO1, _adapter(), _geo1_inputs()), (GEO4, _sfpuc4_specs(), _sfpuc4_inputs(window=("2016-03-03", DATA_END)))):
        a = C.compose(geo, specs, inputs)
        _frames_equal(a, C.compose(geo, specs, inputs, C.Inject()))
        _frames_equal(a, C.compose(geo, specs, inputs, empty))
        # empty day entries are dropped, not applied
        _frames_equal(a, C.compose(geo, specs, inputs, C.Inject(basin={"2020-01-01": set()}, zone={"2020-01-01": []})))


# ── (4) a Sea Cliff link swap ──────────────────────────────────────────────

def test_sea_cliff_link_swap_sets_baker_china_and_lifts_ocean_to_the_cofire_share():
    """A named Sea Cliff outfall (the feed's SEA CLIFF II) is the Baker & China link:
    Baker & China = 1, Ocean Beach = max(its prediction, P(Ocean Beach link fires |
    Baker & China link fires, same day)). The share is measured from the committed
    ledger through 2026-04-22: 32 of 54 days = 0.593 (the design quotes 0.59; same
    count). P(Baker & China | Ocean Beach) = 32/43 and P(Central | South) = 20/23
    under SFPUC4."""
    days = C.link_fire_days(GEO4, _ledger(), end=LEDGER_AS_OF)
    both = len(days["westside>ocean"] & days["westside>baker_china"])
    assert (both, len(days["westside>baker_china"]), len(days["westside>ocean"])) == (32, 54, 43)
    assert (len(days["central>east"] & days["south>east"]), len(days["south>east"])) == (20, 23)
    share = _shares("sfpuc4_v1")["westside>ocean|westside>baker_china"]
    assert share == 32 / 54 and round(share, 2) == 0.59
    assert _shares("geo_v1")["westside>ocean|westside>baker_china"] == share   # same outfalls in both geographies

    sea = FEED_NAME_TO_OUTFALLS["SEA CLIFF II"]
    for geo, specs, inputs in ((GEO1, _adapter(), _geo1_inputs(window=STORM)), (GEO4, _sfpuc4_specs(), _sfpuc4_inputs(window=STORM))):
        assert {geo.link_of_outfall(o).id for o in sea} == {"westside>baker_china"}
        base = C.compose(geo, specs, inputs)
        ob = base.s3.link_p["westside>ocean"]
        hi_day, lo_day = ob.idxmax(), ob.idxmin()
        assert ob[hi_day] > share > ob[lo_day], (geo.version, ob[hi_day], ob[lo_day])   # one day each side of the share
        inj = C.compose(geo, specs, inputs, C.Inject(link={hi_day: sea, lo_day: sea}))
        for d in (hi_day, lo_day):
            assert inj.s3.zone_p.at[d, "baker_china"] == 1.0 and inj.out.zone.at[d, "baker_china"] == 1.0
            assert inj.s3.zone_p.at[d, "ocean"] == max(ob[d], share), (geo.version, d, inj.s3.zone_p.at[d, "ocean"])
        assert inj.s3.zone_p.at[hi_day, "ocean"] == ob[hi_day]                     # the share never lowers a prediction
        others = inj.s3.zone_p.index.difference([hi_day, lo_day])
        pd.testing.assert_frame_equal(inj.s3.zone_p.loc[others], base.s3.zone_p.loc[others], check_exact=True)
        pd.testing.assert_frame_equal(inj.s3.zone_p[["north", "east"]], base.s3.zone_p[["north", "east"]], check_exact=True)
        reach = set()
        for d in (hi_day, lo_day):
            reach |= set(pd.date_range(d, d + pd.Timedelta(days=7)))
        still = inj.out.zone.index.difference(sorted(reach))
        pd.testing.assert_frame_equal(inj.out.zone.loc[still], base.out.zone.loc[still], check_exact=True)
    # a station flag with a known basin lifts its siblings the same way: an Ocean Beach station under SFPUC4
    ob_station = ZONES["ocean"].station_ids[0]
    assert GEO4.station_basin(ob_station) == "westside"
    inputs = _sfpuc4_inputs(window=STORM)
    base = C.compose(GEO4, _sfpuc4_specs(), inputs)
    d = base.s3.link_p["westside>baker_china"].idxmin()
    inj = C.compose(GEO4, _sfpuc4_specs(), inputs, C.Inject(zone={d: {ob_station}}))
    assert inj.s3.zone_p.at[d, "ocean"] == 1.0
    assert inj.s3.zone_p.at[d, "baker_china"] == max(base.s3.link_p.at[d, "westside>baker_china"], 32 / 43)
    print(f"   P(Ocean Beach link | Baker & China link) = {both}/{len(days['westside>baker_china'])} = {share:.4f} "
          f"(ledger through {LEDGER_AS_OF.date()})")


# ── (5) an Islais Creek flag → East = 1 whatever the basin ─────────────────

def test_islais_flag_sets_east_under_both_geographies():
    islais = next(s.sfpuc_id for s in STATIONS.values() if s.name == "Islais Creek")
    assert (GEO1.station_basin(islais), GEO4.station_basin(islais)) == ("southeast", "central")   # the basins differ
    for geo, specs, inputs in ((GEO1, _adapter(), _geo1_inputs(window=STORM)), (GEO4, _sfpuc4_specs(), _sfpuc4_inputs(window=STORM))):
        base = C.compose(geo, specs, inputs)
        dry = base.s3.zone_p["east"].idxmin()
        assert base.s3.zone_p.at[dry, "east"] < 0.5
        inj = C.compose(geo, specs, inputs, C.Inject(zone={dry: {islais}}))
        assert inj.s3.zone_p.at[dry, "east"] == 1.0, geo.version
        assert inj.out.zone.at[dry, "east"] == 1.0, geo.version
        assert inj.s4.zone.at[dry, "east"] >= base.s4.zone.at[dry, "east"]
        # East's other basin is untouched: no sibling link crosses basins
        pd.testing.assert_frame_equal(inj.s3.zone_p.drop(columns="east"), base.s3.zone_p.drop(columns="east"), check_exact=True)


# ── (6) the East union inside the Fréchet bounds ───────────────────────────

def test_east_union_inside_the_frechet_bounds_for_every_rule():
    """max(p_C, p_S) ≤ U ≤ min(1, p_C + p_S) for max, noisy_or and cofire (π = P(no
    Central | South) = 3/23 from the ledger through 2026-04-22), on a grid with both
    ends; the cofire clip binds where p_S outruns p_C."""
    grid = np.array([0.0, 0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99, 1.0])
    pc, ps = (a.ravel() for a in np.meshgrid(grid, grid))
    idx = pd.date_range("2024-01-01", periods=len(pc))
    p = pd.DataFrame({"westside": 0.2, "north_shore": 0.2, "central": pc, "south": ps}, index=idx)
    v = pd.DataFrame(1.0, index=idx, columns=list(GEO4.keys))
    lo, hi = np.maximum(pc, ps), np.minimum(1.0, pc + ps)
    pi = C.union_block(GEO4, "cofire", _shares("sfpuc4_v1"))["east"]["pi"]["south>east"]
    assert abs(pi - 3 / 23) < 1e-15
    for rule in C.UNION_RULES:
        spec = C.benchmark_s3_spec(GEO4, "identity", union=rule, shares=_shares("sfpuc4_v1"))
        u = C.s3(GEO4, spec, p, v).zone_p["east"].to_numpy()
        assert (u >= lo).all() and (u <= hi).all(), rule
        inside = (pc > 0) & (ps > 0) & (pc < 1) & (ps < 1)
        if rule == "noisy_or":
            assert (u[inside] > lo[inside]).all(), rule        # adds something wherever both are uncertain
        if rule == "cofire":                                   # … and cofire wherever Central leads
            assert (u[inside & (pc >= ps)] > lo[inside & (pc >= ps)]).all(), rule
    spec = C.benchmark_s3_spec(GEO4, "identity", union="cofire", shares=_shares("sfpuc4_v1"))
    one = pd.DataFrame({"westside": [0.0], "north_shore": [0.0], "central": [0.0], "south": [0.9]}, index=idx[:1])
    assert C.s3(GEO4, spec, one, v.iloc[:1]).zone_p.at[idx[0], "east"] == 0.9   # p_C + π·p_S = 0.117 < max → clipped up
    # geo_v1's two-link zones take the max of their links
    a = C.compose(GEO1, _adapter(), _geo1_inputs(window=STORM))
    for zk in ("north", "east"):
        cols = [lk.id for lk in GEO1.links_into(zk)]
        assert (a.s3.zone_p[zk] == a.s3.link_p[cols].max(axis=1)).all()


# ── (7) monotone ───────────────────────────────────────────────────────────

def _ge(a: C.Composition, b: C.Composition, what: str) -> None:
    for name, fa in a.frames().items():
        if name.endswith("_v"):
            continue
        d = (fa.to_numpy() - b.frames()[name].to_numpy()).min()
        assert d >= 0.0, (what, name, d)


def test_monotone_raising_any_input_never_lowers_anything_downstream():
    window = ("2023-01-01", "2023-01-24")
    cases = [(GEO1, _adapter(), _geo1_inputs(window=window))]
    cases += [(GEO4, _sfpuc4_specs(rule), _sfpuc4_inputs(window=window)) for rule in C.UNION_RULES]
    for geo, specs, inputs in cases:
        base = C.compose(geo, specs, inputs)
        n = 0
        for r in range(0, len(inputs.p), 3):
            for b in geo.keys:
                for bump in (0.15, 1.0):
                    p2 = inputs.p.copy()
                    p2.iloc[r, p2.columns.get_loc(b)] = min(1.0, p2.iloc[r, p2.columns.get_loc(b)] + bump)
                    _ge(C.compose(geo, specs, dataclasses.replace(inputs, p=p2)), base, (geo.version, r, b, bump))
                    n += 1
        # observations only ever raise S3, S4 and OUT (a lab result may lower q; it is not an input p)
        d0, d1 = inputs.p.index[2], inputs.p.index[9]
        any_station = {zk: ZONES[zk].station_ids[-1] for zk in ZONES}
        injections = [C.Inject(basin={d0: set(geo.keys)}), C.Inject(zone={d1: set(any_station.values())}),
                      C.Inject(link={d0: {lk.outfalls[0] for lk in geo.links}}),
                      C.Inject(basin={d1: {geo.keys[0]}}, link={d0: {geo.links[0].outfalls[0]}}, zone={d0: {any_station["east"]}})]
        for inj in injections:
            _ge(C.compose(geo, specs, inputs, inj), base, (geo.version, "inject"))
        assert n >= 60, n


# ── (8) S4 oracle and chained: one algebra ─────────────────────────────────

def _reference_q(p: np.ndarray, x_of, b: np.ndarray | None) -> np.ndarray:
    """An independent slow loop: q(D) = 1 − (1 − b) · Π_{k=0..7, D−k in run} (1 − p(D−k) · x(k, D−k))."""
    T, U = p.shape
    q = np.empty((T, U))
    for t in range(T):
        for u in range(U):
            keep = 1.0
            for k in range(8):
                j = t - k
                if j >= 0:
                    keep *= 1.0 - p[j, u] * x_of(u, k, j)
            q[t, u] = 1.0 - (1.0 if b is None else 1.0 - b[t, u]) * keep
    return q


def test_s4_oracle_and_chained_are_one_algebra():
    """Oracle (true 0/1 overflow history) and chained (S3's p) go through the same
    function: each equals ``lingering`` on its own history and an independent slow
    loop; under the adapter that loop reads impact.impact_fraction (x(0) = the measured
    d0 bucket, b = the group's baseline). OUT is the same algebra with x(0) = 1, b = 0."""
    from impact import impact_fraction
    _, table, _ = _served_impact()
    window = ("2022-11-01", "2023-02-28")
    inputs = _geo1_inputs(window=window)
    spec4 = _adapter()["s4"]
    chained = C.compose(GEO1, _adapter(), inputs).s3.history(spec4)
    fired = C.link_fire_days(GEO1, _ledger())
    truth = pd.DataFrame({lid: chained.p.index.isin(sorted(days)).astype(float) for lid, days in fired.items()},
                         index=chained.p.index)[list(chained.p.columns)]
    assert truth.to_numpy().sum() >= 20, "the window must hold real overflows"
    oracle = C.History(truth, chained.v)
    units = C.s4_units(GEO1, spec4)
    groups = [spec4["legacy_group"][u] for u in units]
    b = C.background(spec4, units, truth.index, None)
    for hist in (oracle, chained):
        q = C.s4(GEO1, spec4, hist).unit.to_numpy()
        same = C.lingering(hist.p.to_numpy(), C.x_curves(spec4, units, hist.v.to_numpy()), b)
        assert np.array_equal(q, same)
        v = hist.v.to_numpy()
        ref = _reference_q(hist.p.to_numpy(), lambda u, k, j: impact_fraction(table, groups[u], k, v[j, u]), b)
        assert np.abs(q - ref).max() <= 1e-15, np.abs(q - ref).max()
        r = C.out(GEO1, spec4, hist).unit.to_numpy()
        ref_r = _reference_q(hist.p.to_numpy(), lambda u, k, j: 1.0 if k == 0 else impact_fraction(table, groups[u], k, v[j, u]), None)
        assert np.abs(r - ref_r).max() <= 0.0005 + 1e-12          # OUT rounds to 3 dp as impact.compose does
    # zone units: the same function with the rain background
    spec4z = _zone_v3_fixture(GEO4)
    inp4 = _sfpuc4_inputs(window=window)
    hist4 = C.compose(GEO4, _sfpuc4_specs(), inp4).s3.history(spec4z)
    zunits = C.s4_units(GEO4, spec4z)
    bz = C.background(spec4z, zunits, hist4.p.index, inp4.rain)
    xz = C.x_curves(spec4z, zunits, hist4.v.to_numpy())
    q4 = C.s4(GEO4, spec4z, hist4, inp4.rain).unit.to_numpy()
    assert np.array_equal(q4, C.lingering(hist4.p.to_numpy(), xz, bz))
    assert np.abs(q4 - _reference_q(hist4.p.to_numpy(), lambda u, k, j: xz[k, j, u], bz)).max() <= 1e-15
    assert (bz.max(axis=0) > bz.min(axis=0)).all()                # the rain background moves with the rain


# ── more: basin_swap, the adapter's pieces, specs, oracle API, speed ───────

def test_basin_injection_is_live_v2_basin_swap():
    """Inject.basin = impact.compose's ``observed`` (p_b = 1, then the split), exactly,
    with the ledger's GEO_V1 onsets of the 2022-23 season as the observations."""
    from groups import GROUPS_BY_BASIN
    from impact import compose
    _, table, split = _served_impact()
    window = ("2022-07-01", "2023-06-30")
    inputs = _geo1_inputs(window=window)
    ev = _ledger()
    ev = ev[(ev["event_date"] >= window[0]) & (ev["event_date"] <= window[1])]
    observed: dict = {}
    for d, oid in zip(ev["event_date"], ev["outfall_id"]):
        observed.setdefault(pd.Timestamp(d).normalize(), set()).add(GEO1.basin_of_outfall(oid))
    assert sum(map(len, observed.values())) >= 30
    comp = C.compose(GEO1, _adapter(), inputs, C.Inject(basin=observed))
    probs, vols = _legacy_lists(inputs.p, inputs.v_hat)
    dates = list(inputs.p.index)
    gcol = {lk.legacy_group: comp.out.unit.columns.get_loc(lk.id) for lk in GEO1.links}
    for i in range(len(dates)):
        _, per_group = compose(table, GROUPS_BY_BASIN, probs, vols, i, dates, observed, split=split)
        for g, r in per_group.items():
            assert comp.out.unit.iat[i, gcol[g]] == r, (dates[i], g, comp.out.unit.iat[i, gcol[g]], r)


def test_adapter_reproduces_the_live_payload_golden():
    """On the served-golden fixture's live inputs (tests/fixtures/stages_golden_payload.json:
    LiveData's own p and v̂ for 2026-01-08 → 02-22), the adapter's payload blocks are the
    plain block live_dashboard._day_payload built for the six pinned days, exactly."""
    gold = json.loads((ROOT / "tests" / "fixtures" / "stages_golden_payload.json").read_text())
    days = sorted(gold["discharge_probs_by_day"])
    idx = pd.DatetimeIndex(days)
    p = pd.DataFrame([{k: gold["discharge_probs_by_day"][d][k] for k in GEO1.keys} for d in days], index=idx)
    v = pd.DataFrame([{k: gold["volumes_by_day"][d][k] for k in GEO1.keys} for d in days], index=idx)
    comp = C.compose(GEO1, _adapter(), C.BasinInputs(p, v))
    assert len(gold["plain"]) == 6
    for d, want in gold["plain"].items():
        got = C.payload_blocks(GEO1, _adapter()["s4"], comp, d)
        for key in ("zones", "impact_groups", "predictions"):
            assert got[key] == want["plain"][key] == want[key], (d, key, got[key], want[key])


def test_adapter_pieces_are_stage2_and_impact_bit_for_bit():
    import stage2 as S2
    from impact import impact_fraction
    spec2, table, _ = _served_impact()
    ad = _adapter()
    vgrid = np.array([0.0, 0.01, 0.5, 3.7, 7.25, 13.93, 26.77, 55.27, 120.0, 1e6])
    for lk in GEO1.links:
        g = lk.legacy_group
        got = C._apply_share(ad["s3"]["links"][lk.id]["share"], np.ones_like(vgrid), vgrid)
        assert np.array_equal(got, [1.0 * S2.group_share(spec2, g, float(x)) for x in vgrid]), g
    units = C.s4_units(GEO1, ad["s4"])
    X = C.x_curves(ad["s4"], units, np.tile(vgrid[:, None], (1, len(units))))
    for ui, u in enumerate(units):
        g = ad["s4"]["legacy_group"][u]
        for k in C.LAGS:
            assert np.array_equal(X[k, :, ui], [impact_fraction(table, g, k, float(x)) for x in vgrid]), (g, k)
        assert ad["s4"]["background"]["p"][u] == table[g]["buckets"]["baseline_no_recent_discharge"]["p_elevated"]
    # the double median the served set carries (stage 2 shares vs the impact table), kept as served
    assert ad["s3"]["links"]["westside>ocean"]["share"]["median_mg"] == spec2["median_event_volume_mg"]["Ocean Beach"]
    assert ad["s4"]["median_mg"]["westside>ocean"] == table["Ocean Beach"]["median_event_volume_mg"]
    want = "stage2.json impact_table" if spec2.get("impact_table") else "impact_table.json"
    assert ad["s3"]["sources"]["impact_table"] == want   # serving's rule (live_dashboard): a refit table wins


def test_spec_files_round_trip_and_bad_specs_raise():
    inputs = _geo1_inputs(window=STORM)
    with tempfile.TemporaryDirectory() as tmp:
        for name, spec in _adapter().items():
            (Path(tmp) / f"{name}.json").write_text(json.dumps(spec))
        loaded = {"s3": C.check_s3_spec(json.loads((Path(tmp) / "s3.json").read_text()), GEO1),
                  "s4": C.check_s4_spec(json.loads((Path(tmp) / "s4.json").read_text()), GEO1)}
    _frames_equal(C.compose(GEO1, loaded, inputs), C.compose(GEO1, _adapter(), inputs))

    def raises(exc, fn, *a, **kw):
        try:
            fn(*a, **kw)
        except exc:
            return
        raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")

    s3a, s4a = _adapter()["s3"], _adapter()["s4"]
    raises(ValueError, C.check_s3_spec, s3a, GEO4)                                   # stamp ≠ geography
    raises(ValueError, C.check_s3_spec, {k: v for k, v in s3a.items() if k != "geography"}, GEO4)   # no stamp = geo_v1
    raises(KeyError, C.check_s3_spec, {**s3a, "links": {k: v for k, v in s3a["links"].items() if k != "central>east"}}, GEO1)
    bad = json.loads(json.dumps(s3a))
    bad["links"]["westside>ocean"]["share"] = {"kind": "magic"}
    raises(ValueError, C.check_s3_spec, bad, GEO1)
    bad = json.loads(json.dumps(s3a))
    bad["links"]["westside>ocean"]["outfalls"] = ["CSD-001"]
    raises(ValueError, C.check_s3_spec, bad, GEO1)
    raises(ValueError, C.check_s3_spec, {**s3a, "union": {"east": {"rule": "noisy_or"}}}, GEO1)   # geo_v1 takes the max
    raises(KeyError, C.check_s3_spec, {**s3a, "unoin": {}}, GEO1)
    s3_4 = C.benchmark_s3_spec(GEO4, "identity")
    raises(KeyError, C.check_s3_spec, {**s3_4, "union": {}}, GEO4)                   # East needs a rule
    raises(ValueError, C.check_s3_spec, {**s3_4, "union": {**s3_4["union"], "ocean": {"rule": "max"}}}, GEO4)
    z = _zone_v3_fixture(GEO4)
    raises(ValueError, C.check_s4_spec, {**z, "background": {**z["background"], "features": ["n_sampled"],
                                                            "coef": {u: {"intercept": 0.0, "n_sampled": 0.1} for u in ZONES}}}, GEO4)
    rising = json.loads(json.dumps(z))
    rising["buckets"]["east"]["6-7_small"] = 0.99
    raises(ValueError, C.check_s4_spec, rising, GEO4)
    raises(ValueError, C.check_s4_spec, s4a, GEO4)
    raises(ValueError, C.check_s4_spec, {**z, "geography": "geo_v1"}, GEO1)          # zone units under geo_v1
    raises(KeyError, C.cofire, GEO4, pd.DataFrame({"event_date": ["2020-01-01"], "outfall_id": ["CSD-999"]}))
    # inputs and injections fail loudly
    raises(KeyError, C.compose, GEO1, _adapter(), C.BasinInputs(inputs.p.assign(citywide=0.1), inputs.v_hat))
    raises(ValueError, C.compose, GEO1, _adapter(), C.BasinInputs(inputs.p.where(inputs.p > 0.5), inputs.v_hat))
    raises(ValueError, C.compose, GEO1, _adapter(), C.BasinInputs(inputs.p.iloc[::2], inputs.v_hat.iloc[::2]))
    d = inputs.p.index[3]
    raises(KeyError, C.compose, GEO1, _adapter(), inputs, C.Inject(basin={d: {"south"}}))
    raises(KeyError, C.compose, GEO1, _adapter(), inputs, C.Inject(link={d: {"CSD-999"}}))
    raises(KeyError, C.compose, GEO1, _adapter(), inputs, C.Inject(zone={d: {"BAY#320_SL"}}))
    raises(KeyError, C.compose, GEO1, _adapter(), inputs, C.Inject(sample={d: {"marina": 1}}))
    raises(KeyError, C.compose, GEO1, _adapter(), inputs, C.Inject(basin={"2001-01-01": {"central"}}))
    raises(KeyError, C.compose, GEO1, C.geo_v1_adapter_specs(), inputs, C.Inject(link={d: {"CSD-007"}}))   # no cofire shares
    raises(TypeError, C.Inject, basin={d: "central"})
    raises(ValueError, C.out, GEO1, s4a, C.compose(GEO1, _adapter(), inputs).s3.history(s4a), C.Inject(sample={d: {"east": 1}}))
    raises(ValueError, C.s4, GEO4, z, C.compose(GEO4, _sfpuc4_specs(), _sfpuc4_inputs(window=STORM)).s3.history(z))   # no rain
    # a sample result is q_z(D) at S4 and nothing else
    base = C.compose(GEO1, _adapter(), inputs)
    smp = C.compose(GEO1, _adapter(), inputs, C.Inject(sample={d: {"east": 0, "ocean": 1}}))
    assert smp.s4.zone.at[d, "east"] == 0.0 and smp.s4.zone.at[d, "ocean"] == 1.0
    pd.testing.assert_frame_equal(smp.out.zone, base.out.zone, check_exact=True)
    pd.testing.assert_frame_equal(smp.s3.zone_p, base.s3.zone_p, check_exact=True)
    pd.testing.assert_frame_equal(smp.s4.zone.drop(index=d), base.s4.zone.drop(index=d), check_exact=True)


def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return
    raise AssertionError(f"{getattr(fn, '__name__', fn)} did not raise {exc.__name__}")


def test_gaps_that_would_default_silently_raise():
    """Each of these used to pass without a word: an infinite size (a volume head that
    overflowed) reaching zone_v3 as "large"; an identity link carrying a share below 1;
    a link with no evidence tags; a benchmark φ map missing links; an S4 legacy_group map
    that is not the geography's; two results for one zone-day; and an adapter source
    whose missing group, baseline or median serving would fill with 1, 0 or 1 MG."""
    inputs = _sfpuc4_inputs(window=STORM)
    specs = _sfpuc4_specs()
    v_inf = inputs.v_hat.copy()
    v_inf.iloc[4, v_inf.columns.get_loc("central")] = np.inf
    _raises(ValueError, C.compose, GEO4, specs, dataclasses.replace(inputs, v_hat=v_inf))
    hist = C.compose(GEO4, specs, inputs).s3.history(specs["s4"])
    hv = hist.v.copy()
    hv.iloc[4, hv.columns.get_loc("east")] = np.inf
    _raises(ValueError, C.s4, GEO4, specs["s4"], C.History(hist.p, hv), inputs.rain)
    _raises(ValueError, C.out, GEO4, specs["s4"], C.History(hist.p, hv))
    s3_4 = C.benchmark_s3_spec(GEO4, "identity")
    for sh in ({"kind": "constant", "p": 0.5}, {"kind": "logit_logvol", "coef": {"a": 5.0, "b": 0.0}},
               {"kind": "size_blend", "large": 1.0, "small": 0.9, "median_mg": 10.0}):
        bad = json.loads(json.dumps(s3_4))
        bad["links"]["central>east"]["share"] = sh                        # central is its basin's only link
        _raises(ValueError, C.check_s3_spec, bad, GEO4)
    ok = json.loads(json.dumps(s3_4))
    ok["links"]["central>east"]["share"] = {"kind": "size_blend", "large": 1.0, "small": 1.0, "median_mg": 10.0}
    C.check_s3_spec(ok, GEO4)                                              # the served v2 form of an identity share
    bad = json.loads(json.dumps(s3_4))
    del bad["links"]["westside>ocean"]["evidence"]
    _raises(KeyError, C.check_s3_spec, bad, GEO4)
    _raises(KeyError, C.benchmark_s3_spec, GEO4, "identity", vol_share={"westside>ocean": 0.9})
    phis = {lk.id: (1.0 if lk.identity else 0.6) for lk in GEO4.links}
    assert C.benchmark_s3_spec(GEO4, "identity", vol_share=phis)["links"]["westside>ocean"]["vol_share"] == 0.6
    for lid, phi in (("central>east", 0.8), ("westside>ocean", 1.5), ("westside>ocean", 0.0)):
        _raises(ValueError, C.benchmark_s3_spec, GEO4, "identity", vol_share={**phis, lid: phi})
    s4a = _adapter()["s4"]
    lg = dict(s4a["legacy_group"])
    lg["westside>ocean"], lg["westside>baker_china"] = lg["westside>baker_china"], lg["westside>ocean"]
    _raises(ValueError, C.check_s4_spec, {**s4a, "legacy_group": lg}, GEO1)
    _raises(ValueError, C.check_s4_spec, {**_zone_v3_fixture(GEO4), "legacy_group": {}}, GEO4)
    _raises(ValueError, C.Inject, sample={"2023-01-05": {"east": 1}, pd.Timestamp("2023-01-05 09:00"): {"east": 0}})
    assert C.Inject(sample={"2023-01-05": {"east": 1}, pd.Timestamp("2023-01-05 09:00"): {"east": 1}}).sample \
        == {pd.Timestamp("2023-01-05"): {"east": 1.0}}                     # the same result twice is one result
    spec2, _, _ = _served_impact()
    bad2 = json.loads(json.dumps(spec2))
    bad2["shares"]["Baker China"] = bad2["shares"].pop("Baker-China")     # a misspelt group: group_share would go identity
    _raises(KeyError, C.geo_v1_adapter_specs, stage2=bad2)
    bad2 = json.loads(json.dumps(spec2))
    del bad2["median_event_volume_mg"]["Ocean Beach"]
    _raises(KeyError, C.geo_v1_adapter_specs, stage2=bad2)
    for drop in ("baseline", "median"):
        bad2 = json.loads(json.dumps(spec2))
        gt = bad2["impact_table"]["Aquatic Park"]
        if drop == "baseline":
            del gt["buckets"]["baseline_no_recent_discharge"]["p_elevated"]
        else:
            del gt["median_event_volume_mg"]
        _raises(ValueError, C.geo_v1_adapter_specs, stage2=bad2)
    # Part B 14: a directory stamped for a stages bundle is not read as GEO_V1; unstamped = geo_v1 / two_stage_v1
    served = json.loads((SERVE_DIR / "served.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "stage2.json").write_text(json.dumps(spec2))
        (Path(tmp) / "served.json").write_text(json.dumps(served))
        assert C.geo_v1_adapter_specs(serve_dir=tmp)["s4"]["buckets"] == C.geo_v1_adapter_specs()["s4"]["buckets"]
        (Path(tmp) / "served.json").write_text(json.dumps({**served, "geography": "sfpuc4_v1", "pipeline": "stages_v1"}))
        _raises(ValueError, C.geo_v1_adapter_specs, serve_dir=tmp)


def test_a_station_with_no_basin_speaks_for_its_zone_alone():
    """§2.5: when station_basin names no basin the flag sets its zone and nothing else.
    No registry station is in that case today (every one has a basin under both
    geographies), so a geography whose station_basin answers None stands in. Under
    sfpuc4_v1 the zone is 1 and every link stays as predicted; under geo_v1 (link units)
    the station's own link carries the flag and its basin's other link is not lifted."""
    @dataclasses.dataclass(frozen=True)
    class NoStationBasin(G.Geography):
        def station_basin(self, sfpuc_id):
            G.Geography.station_basin(self, sfpuc_id)   # a non-station id still raises
            return None

    ob_station = ZONES["ocean"].station_ids[0]
    for real, specs, inputs in ((GEO4, _sfpuc4_specs(), _sfpuc4_inputs(window=STORM)),
                                (GEO1, _adapter(), _geo1_inputs(window=STORM))):
        geo = NoStationBasin(**{f.name: getattr(real, f.name) for f in dataclasses.fields(real)})
        assert geo.station_basin(ob_station) is None and real.station_basin(ob_station) == "westside"
        base = C.compose(geo, specs, inputs)
        d = base.s3.link_p["westside>baker_china"].idxmin()
        assert base.s3.link_p.at[d, "westside>baker_china"] < 32 / 43   # the real geography would lift it
        inj = C.compose(geo, specs, inputs, C.Inject(zone={d: {ob_station}}))
        assert inj.s3.zone_p.at[d, "ocean"] == 1.0 and inj.out.zone.at[d, "ocean"] == 1.0
        pd.testing.assert_frame_equal(inj.s3.zone_p.drop(columns="ocean"), base.s3.zone_p.drop(columns="ocean"), check_exact=True)
        changed = (inj.s3.link_p != base.s3.link_p).any()
        assert set(changed[changed].index) == (set() if real is GEO4 else {"westside>ocean"}), (real.version, changed)
        lifted = C.compose(real, specs, inputs, C.Inject(zone={d: {ob_station}}))
        assert lifted.s3.link_p.at[d, "westside>baker_china"] > inj.s3.link_p.at[d, "westside>baker_china"]


def test_full_history_marks_the_days_a_run_start_cuts_short():
    """S4 and OUT on a run's first 7 days leave out the overflows before the run (as
    impact.compose does). full_history() is the days after them: there a run started a
    week earlier gives the same numbers bit for bit, and before them it does not."""
    long_w, short_w = ("2022-12-24", "2023-01-31"), ("2022-12-31", "2023-01-31")
    for geo, specs, mk in ((GEO1, _adapter(), _geo1_inputs), (GEO4, _sfpuc4_specs(), _sfpuc4_inputs)):
        short = C.compose(geo, specs, mk(window=short_w))
        long_ = C.compose(geo, specs, mk(window=long_w))
        full = short.full_history()
        assert len(full) == len(short.out.zone) - 7 and full[0] == pd.Timestamp(short_w[0]) + pd.Timedelta(days=7)
        for name, fs in short.frames().items():
            pd.testing.assert_frame_equal(fs.loc[full], long_.frames()[name].loc[full], check_exact=True, obj=name)
        cut = short.out.zone.index[:7]
        assert (short.out.zone.loc[cut] < long_.out.zone.loc[cut]).to_numpy().any(), geo.version


def test_s3_oracle_takes_the_true_occurrence_and_keeps_v_hat():
    """Part B 2: the S3 oracle input is y_b with v̂ from rain; the API has no field for a
    filed volume, and a non-0/1 occurrence raises."""
    assert [f.name for f in dataclasses.fields(C.BasinInputs)] == ["p", "v_hat", "rain"]
    inputs = _sfpuc4_inputs(window=STORM)
    y = (inputs.p > 0.5).astype(int)
    orc = inputs.oracle(y)
    assert orc.v_hat is inputs.v_hat and orc.rain is inputs.rain
    try:
        inputs.oracle(inputs.p)
        raise AssertionError("a probability passed as the oracle occurrence must raise")
    except ValueError:
        pass
    comp = C.compose(GEO4, _sfpuc4_specs(), orc)
    for lk in GEO4.links:   # identity shares: each link carries its basin's truth, sized from rain
        assert (comp.s3.link_p[lk.id] == y[lk.basin]).all(), lk.id
        assert (comp.s3.link_v[lk.id] == inputs.v_hat[lk.basin]).all(), lk.id
    east = [lk.basin for lk in GEO4.links_into("east")]
    assert (comp.s3.zone_p["east"] == y[east].max(axis=1)).all()                  # the max union of the true occurrences
    assert (comp.s3.zone_v["east"] == inputs.v_hat[east].sum(axis=1)).all()       # the zone's size: its basins' sizes summed
    for zk in ("ocean", "baker_china", "north"):
        assert (comp.s3.zone_p[zk] == y[GEO4.links_into(zk)[0].basin]).all(), zk


def test_module_does_no_io_at_import_and_never_writes():
    src = (MODELS / "compose_v2.py").read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "attr", getattr(sub.func, "id", ""))
                assert name not in {"open", "read_text", "read_csv", "load", "loads", "get"}, (name, ast.dump(node)[:120])
    for word in ("write_text", "write_bytes", ".dump(", "to_csv", "'w'", '"w"'):
        assert word not in src, word


def test_fast_enough_for_the_full_span():
    p, v = _served_inputs()["pre"]
    t0 = time.perf_counter()
    for _ in range(3):
        C.compose(GEO1, _adapter(), C.BasinInputs(p, v))
    t_geo1 = (time.perf_counter() - t0) / 3
    inp4 = _sfpuc4_inputs(window=("2016-03-03", DATA_END))
    t0 = time.perf_counter()
    C.compose(GEO4, _sfpuc4_specs("cofire"), inp4)
    t_geo4 = time.perf_counter() - t0
    assert t_geo1 < 1.0 and t_geo4 < 1.0, (t_geo1, t_geo4)
    print(f"   full span ({len(p)} days): geo_v1 adapter {t_geo1 * 1000:.0f} ms, sfpuc4_v1 {t_geo4 * 1000:.0f} ms")


if __name__ == "__main__":
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
