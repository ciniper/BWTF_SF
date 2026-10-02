"""stages_s5: S5 live corrections replayed through the stage composition (STAGES_DESIGN.md
Part C §3.5, §2.5; Part B 9, 16; STAGES_PROTOCOL.md §4.1, §7's S5 rules, §8's S5 primary).

Pins that basin_swap is live_v2 day for day (replay_live.per_day_risks on the archive feed,
and on a degraded feed's whole era, 2023-07-01 → 2026-08-17); that a run starting mid-era
gives the full run's values; that nothing injected is the plain composition; that a Baker &
China outfall sets its zone to 1 and lifts Ocean Beach to max(prediction, co-firing share);
that an Islais Creek station flag sets East to 1 under both geographies; that X-S5-SELF
leaves out exactly the replaced zone-days, sibling rows on the perfect feed are left out
(X-S5-PERFECT-SIBLING) and scored on a degraded one; that the degraded feeds are
replay_live's synthetic feeds draw for draw; that the conditional set is exclusions' own
(the complement of X-S5-QUIET), identical across variants; that no T3 row, no leaking fold
and no feed under another feed's name gets through (each raise checked for its reason); and
that exclusions' 'variant' column is additive. Counts read the committed data and carry an
as-of date (Part B 23). Nothing is written.

    venv/bin/python tests/test_stages_s5.py
"""
from __future__ import annotations

import ast
import datetime as dt
import functools
import re
import sys
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
import csd_labels  # noqa: E402
import exclusions as X  # noqa: E402
import posting_label as PL  # noqa: E402
import replay_live as RL  # noqa: E402
import samples as SMP  # noqa: E402
import stages_s5 as S5  # noqa: E402
import truth as T  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.outfalls import FEED_NAME_TO_OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")          # the served scorecard's last day: the data end these counts are pinned at
LEDGER_AS_OF = pd.Timestamp("2026-04-22")   # the committed ledger's last event day
GEO1, GEO4 = G.get("geo_v1"), G.get("sfpuc4_v1")
STORM = ("2022-12-12", "2023-01-31")        # the New Year 2023 atmospheric rivers, with the 8 days a row needs before them
SEA_CLIFF, = FEED_NAME_TO_OUTFALLS["SEA CLIFF II"]   # the Baker & China link's observed outfall (CSD-007)


# ── fixtures (read-only) ───────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _inputs(holdout_fit: bool = False):
    return S5.served_inputs(holdout_fit=holdout_fit)


@functools.lru_cache(maxsize=None)
def _feeds(version: str = "geo_v1"):
    return S5.feeds(version, end=AS_OF)


@functools.lru_cache(maxsize=None)
def _shares(version: str) -> dict:
    return C.cofire(G.get(version), csd_labels.load_events(), end=LEDGER_AS_OF)


@functools.lru_cache(maxsize=None)
def _adapter() -> dict:
    return C.geo_v1_adapter_specs(cofire_shares=_shares("geo_v1"))


def _zone_v3(geo) -> dict:
    """A zone_v3-shaped S4 spec for exercising SFPUC4 runs (a fixture, not a fit): flat non-increasing tails
    and a constant background, so no rain is read."""
    xs = dict(zip(C.BUCKET_ORDER, (0.9, 0.6, 0.4, 0.3, 0.2, 0.1)))
    buckets = {zk: {f"{b}_{s}": xs[b] * (1.0 if s == "large" else 0.8) for s in C.SIZES for b in C.BUCKET_ORDER} for zk in ZONES}
    spec = {"geography": geo.version, "kind": "zone_v3", "unit": "zone", "buckets": buckets,
            "zone_median_mg": {zk: 5.0 for zk in ZONES}, "background": {"kind": "constant", "p": {zk: 0.05 for zk in ZONES}},
            "monotone": True}
    return C.check_s4_spec(spec, geo)


@functools.lru_cache(maxsize=None)
def _sfpuc4_specs() -> dict:
    return {"s3": C.benchmark_s3_spec(GEO4, "identity", union="max", shares=_shares("sfpuc4_v1")), "s4": _zone_v3(GEO4)}


def _window(inputs: C.BasinInputs, window, geo=GEO1) -> C.BasinInputs:
    lo, hi = window
    p, v, r = inputs.p.loc[lo:hi], inputs.v_hat.loc[lo:hi], inputs.rain.loc[lo:hi]
    if geo is GEO4:   # the served GEO_V1 S2 output, geo_v1's southeast key standing in for south, until P8a fits SFPUC4's S2
        ren = {"southeast": "south"}
        p, v = p.rename(columns=ren)[list(GEO4.keys)], v.rename(columns=ren)[list(GEO4.keys)]
    return C.BasinInputs(p, v, r)


def _no_samples() -> pd.DataFrame:
    return T._samples(("datasf", "poobot")).iloc[:0]


def _raises(exc, fn, *a, match: str | None = None, **kw):
    """fn raises exc; with ``match``, for that reason (a regex on the message), not another one."""
    try:
        fn(*a, **kw)
    except exc as e:
        if match is not None and not re.search(match, str(e)):
            raise AssertionError(f"{getattr(fn, '__name__', fn)} raised {exc.__name__} for another reason: {e}") from e
        return
    raise AssertionError(f"{getattr(fn, '__name__', fn)} did not raise {exc.__name__}")


@functools.lru_cache(maxsize=None)
def _rows(feed: str, variants=S5.VARIANTS):
    inputs, tt = _inputs()
    return S5.s5_rows(GEO1, _adapter(), inputs, feed, _feeds()[feed], variants=variants, tier="T1", trained_through=tt)


# ── (1) basin_swap = live_v2, day by day ───────────────────────────────────

def test_basin_swap_is_live_v2_day_by_day():
    """On the archive feed (2016-03-19 → 2017-01-10) basin_swap through compose_v2's GEO_V1 adapter equals
    replay_live's live_v2 replay (per_day_risks, cutoff 'end') on every day and zone, max |Δ| = 0, with the
    feed's onsets and flag days exactly archive_flags'. The same on the first degraded feed's whole era
    (2023-07-01 → 2026-08-17, the protocol's S5 window: holdout-fit p, then the served p), where the no-flag
    downgrade is live (the synthetic feed's watcher era starts 2023-07-01)."""
    inputs, _ = _inputs(holdout_fit=True)          # replay_live grades the holdout-fit p where the scorecard has one
    arch = _feeds()["archive"]
    on, fl, era = RL.archive_flags()
    assert S5._v1_onsets(arch) == on and arch.attrs["flags"] == fl and arch.attrs["era"] == era
    got = S5.correct(GEO1, _adapter(), inputs, arch, "basin_swap")
    plain = S5.correct(GEO1, _adapter(), inputs, arch, "plain")
    want = RL.per_day_risks(on, fl, era, cutoff="end")
    assert len(want) == len(got.r) == 298, (len(want), len(got.r))
    worst = max(abs(got.r.at[pd.Timestamp(d), z] - r) for d, w in want.items() for z, r in w["zones"].items())
    assert worst == 0.0, worst
    moved = int((got.r != plain.r).to_numpy().sum())
    assert moved >= 40, moved                       # live_v2 did something: the check is not plain == plain
    # a degraded feed over its whole era (the anchor, the downgrade, the sample rules and the flag hold all fire)
    deg = _feeds()["degraded:1"]
    era = deg.attrs["era"]
    days = inputs.p.index[(inputs.p.index >= pd.Timestamp(era[0])) & (inputs.p.index <= pd.Timestamp(era[1]))]
    assert len(days) == 1144 and days[-1] == AS_OF, len(days)
    got_d = S5.correct(GEO1, _adapter(), inputs, deg, "basin_swap", days=days)
    want_d = RL.per_day_risks(S5._v1_onsets(deg), deg.attrs["flags"], era, cutoff="end")
    worst = max(abs(got_d.r.at[d, z] - want_d[str(d.date())]["zones"][z]) for d in days for z in ZONES)
    assert worst == 0.0, worst
    moved_d = int((got_d.r != S5.correct(GEO1, _adapter(), inputs, deg, "plain", days=days).r).to_numpy().sum())
    assert moved_d >= 100, moved_d
    print(f"   archive: {len(want)} days × 4 zones equal, {moved} zone-days moved by live_v2; degraded:1 "
          f"{len(days)} days equal, {moved_d} zone-days moved")


def test_a_run_that_starts_mid_era_gives_the_full_runs_values():
    """A run whose inputs start mid-era (a T2 season and its 8-day lead-in) gives every variant the same S3 / S4 /
    OUT values on its days as the full run: live_v2's in_tail reads onsets up to D−10, before such a run's first
    day, so basin_swap reads the whole feed, not the run's slice (on the degraded:1 feed, runs starting the day
    after an observation moved basin_swap by up to 0.014 when it read the slice)."""
    inputs, _ = _inputs(holdout_fit=True)
    deg = _feeds()["degraded:1"]
    rates = S5.sample_rates(GEO1, pd.date_range(T.TRUTH_START, "2025-10-31"))
    full_days = inputs.p.index[inputs.p.index >= pd.Timestamp(deg.attrs["era"][0])]
    vs = ("plain", "basin_swap", "link_zone_swap", "sample_swap")
    full = {v: S5.correct(GEO1, _adapter(), inputs, deg, v, days=full_days, rates=rates) for v in vs}
    starts = sorted({d + pd.Timedelta(days=k) for d in deg["date"].unique() for k in (1,)
                     if d + pd.Timedelta(days=k + 20) <= AS_OF})
    for lo in starts:
        sub = _window(inputs, (lo, lo + pd.Timedelta(days=20)))
        days = sub.p.index[S5.HISTORY_DAYS:]
        for v in vs:
            got = S5.correct(GEO1, _adapter(), sub, deg, v, days=days, rates=rates)
            for name in ("p", "q", "r"):
                pd.testing.assert_frame_equal(getattr(got, name), getattr(full[v], name).loc[days], check_exact=True,
                                              obj=f"{v} {name}, run from {lo.date()}")
    print(f"   {len(starts)} runs from the day after an observation, {len(vs)} variants: equal to the full run")


# ── (2) nothing injected → plain ───────────────────────────────────────────

def test_nothing_injected_is_plain():
    """An empty feed (never watched, no flags) and no lab results: every variant's S3, S4 and OUT frames are the
    plain composition bit for bit, under both geographies (basin_swap is geo_v1's only). s5_rows has no row."""
    inputs, tt = _inputs()
    rates = S5.sample_rates(GEO1, pd.date_range(T.TRUTH_START, "2025-10-31"))
    for geo, specs, inp in ((GEO1, _adapter(), _window(inputs, STORM)), (GEO4, _sfpuc4_specs(), _window(inputs, STORM, GEO4))):
        empty = S5.observations(geo, [], era=None, flags={})
        plain = S5.correct(geo, specs, inp, empty, "plain")
        for v in S5.VARIANTS:
            if v == "basin_swap" and geo is GEO4:
                _raises(ValueError, S5.correct, geo, specs, inp, empty, v, samples=_no_samples())
                continue
            got = S5.correct(geo, specs, inp, empty, v, samples=_no_samples(), rates=rates)
            for name in ("p", "q", "r"):
                pd.testing.assert_frame_equal(getattr(got, name), getattr(plain, name), check_exact=True, obj=f"{geo.version} {v} {name}")
    assert S5.s5_rows(GEO1, _adapter(), inputs, "watcher", _feeds()["watcher"], tier="T1", trained_through=tt).empty


# ── (3) a Baker & China outfall ────────────────────────────────────────────

def test_baker_china_outfall_sets_its_zone_and_lifts_ocean_to_the_cofire_share():
    """A named Sea Cliff outfall (the Baker & China link): link_swap and link_zone_swap set Baker & China to 1
    at S3 and OUT, and Ocean Beach to max(its prediction, P(Ocean Beach link | Baker & China link) = 32/54 on the
    ledger through 2026-04-22), on a day either side of the share; nothing else moves on those days. zone_swap,
    reading the outfall's stations, does the same."""
    share = _shares("sfpuc4_v1")["westside>ocean|westside>baker_china"]
    assert share == 32 / 54 and _shares("geo_v1")["westside>ocean|westside>baker_china"] == share
    inputs, _ = _inputs()
    for geo, specs, inp in ((GEO1, _adapter(), _window(inputs, STORM)), (GEO4, _sfpuc4_specs(), _window(inputs, STORM, GEO4))):
        empty = S5.observations(geo, [], era=None)
        base = S5.correct(geo, specs, inp, empty, "plain")
        ob = base.p["ocean"]
        hi_day, lo_day = ob.idxmax(), ob.idxmin()
        assert ob[hi_day] > share > ob[lo_day], (geo.version, ob[hi_day], ob[lo_day])
        feed = S5.observations(geo, [{"date": hi_day, "outfall": SEA_CLIFF}, {"date": lo_day, "outfall": SEA_CLIFF}],
                               era=(inp.p.index[0], inp.p.index[-1]))
        assert set(feed["zone"]) == {"baker_china"} and set(feed["basin"]) == {"westside"} and set(feed["link"]) == {"westside>baker_china"}
        for v in ("link_swap", "link_zone_swap", "zone_swap"):
            got = S5.correct(geo, specs, inp, feed, v)
            for d in (hi_day, lo_day):
                assert got.p.at[d, "baker_china"] == 1.0 and got.r.at[d, "baker_china"] == 1.0, (geo.version, v, d)
                assert got.p.at[d, "ocean"] == max(ob[d], share), (geo.version, v, d, got.p.at[d, "ocean"])
                pd.testing.assert_series_equal(got.p.loc[d, ["north", "east"]], base.p.loc[d, ["north", "east"]], check_exact=True)
            assert got.p.at[hi_day, "ocean"] == ob[hi_day]           # the share never lowers a prediction


# ── (4) an Islais Creek station flag ───────────────────────────────────────

def test_islais_station_flag_sets_east_to_one():
    """A station-only CSO flag at Islais Creek: zone_swap and link_zone_swap (no structure named, so the zone
    takes it) set East to 1 at S3 and OUT on a dry day, under geo_v1 (its southeast key) and sfpuc4_v1 (central);
    link_swap, which reads named outfalls only, leaves it plain; no other zone moves."""
    islais = next(s.sfpuc_id for s in STATIONS.values() if s.name == "Islais Creek")
    inputs, _ = _inputs()
    for geo, specs, inp, basin in ((GEO1, _adapter(), _window(inputs, STORM), "southeast"),
                                   (GEO4, _sfpuc4_specs(), _window(inputs, STORM, GEO4), "central")):
        base = S5.correct(geo, specs, inp, S5.observations(geo, [], era=None), "plain")
        dry = base.p["east"].idxmin()
        assert base.p.at[dry, "east"] < 0.5
        feed = S5.observations(geo, [{"date": dry, "station": islais}], era=(inp.p.index[0], inp.p.index[-1]))
        assert feed.iloc[0][["zone", "basin", "outfall", "link"]].tolist() == ["east", basin, None, None], feed.iloc[0].to_dict()
        for v in ("zone_swap", "link_zone_swap"):
            got = S5.correct(geo, specs, inp, feed, v)
            assert got.p.at[dry, "east"] == 1.0 and got.r.at[dry, "east"] == 1.0, (geo.version, v)
            pd.testing.assert_frame_equal(got.p.drop(columns="east"), base.p.drop(columns="east"), check_exact=True)
        pd.testing.assert_frame_equal(S5.correct(geo, specs, inp, feed, "link_swap").r, base.r, check_exact=True)


# ── (5) sibling rows on the perfect feed ───────────────────────────────────

def test_sibling_rows_on_the_oracle_feed_are_left_out_and_scored_on_a_degraded_one():
    """T1 (post-training, through 2026-08-17): on the perfect feed every observation's own zone-day is X-S5-SELF
    (ledger link days: Ocean Beach 6, Baker & China 13, North 2, East 8) and a sibling zone-day — Ocean Beach after a
    Baker & China-only observation — is X-S5-PERFECT-SIBLING (Part B 9: that feed is the ledger). On a degraded feed
    the rule does not apply: its sibling zone-days stay in the scored set."""
    rows = _rows("oracle")
    one = rows[rows["variant"] == "link_zone_swap"]
    fd = _feeds()["oracle"]
    post = fd[(fd["date"] >= X.POST_START) & (fd["date"] <= AS_OF)]
    own = set(zip(post["date"], post["zone"]))
    self_ = one[one["excl"] == "X-S5-SELF"]
    assert set(zip(self_["date"], self_["unit"])) == own
    assert self_["unit"].value_counts().to_dict() == {"baker_china": 13, "ocean": 6, "east": 8, "north": 2}
    for feed, rows_ in (("oracle", rows), ("degraded:1", _rows("degraded:1"))):
        lz = rows_[rows_["variant"] == "link_zone_swap"]
        # the rule leaves out exactly the zone-days the observation replaced (shown day, also when a day late)
        assert (lz.loc[lz["excl"] == "X-S5-SELF", "p"] == 1.0).all(), feed
        assert set(lz.loc[lz["p"] == 1.0, "excl"]) <= {"X-S5-SELF"}, (feed, set(lz.loc[lz["p"] == 1.0, "excl"]))
    sib = one[one["excl"] == "X-S5-PERFECT-SIBLING"]
    assert sib["unit"].value_counts().to_dict() == {"ocean": 23, "baker_china": 2}, sib["unit"].value_counts().to_dict()
    for d, z in zip(sib["date"], sib["unit"]):
        win = post[(post["date"] >= d - pd.Timedelta(days=7)) & (post["date"] <= d)]
        assert z not in set(win["zone"]) and "westside" in set(win["basin"]), (d, z)
    assert (one.loc[sib.index, "y"].notna()).all()                   # they have a truth; the rule, not a gap, leaves them out
    deg = _rows("degraded:1")
    assert not (deg["excl"] == "X-S5-PERFECT-SIBLING").any()
    dfd = _feeds()["degraded:1"]
    d_one = deg[deg["variant"] == "link_zone_swap"]
    sibling_scored = [(d, z) for d, z, e in zip(d_one["date"], d_one["unit"], d_one["excl"]) if e == ""
                      and z not in set(dfd[(dfd["date"] >= d - pd.Timedelta(days=7)) & (dfd["date"] <= d)]["zone"])]
    assert sibling_scored, "a degraded feed scores its sibling zone-days"
    print(f"   oracle T1: {len(self_)} replaced zone-days, {len(sib)} sibling zone-days left out; degraded:1 scores "
          f"{len(sibling_scored)} sibling zone-days (as of {AS_OF.date()})")


# ── (6) the degraded feeds are replay_live's ───────────────────────────────

def test_degraded_seeds_reproduce_replay_lives_synthetic_feeds():
    """Each degraded:<seed> feed's geo_v1 basin onsets (on the day shown) and flag days are replay_live.
    synthetic_feed's for that seed, run on the served scorecard over run_synthetic's era, exactly; and the perfect
    feed over that era is synthetic_feed's perfect feed (seed None). Every kept onset is the ledger's outfalls of
    that basin-day, shown on the filed day or the next; drops and lags are synthetic_feed's own tallies."""
    sc, days, _ = RL.load_artifact()
    label = PL.from_beachwatch()
    era = (dt.date.fromisoformat(sc["holdout_start"]), dt.date.fromisoformat(sc["span"][1]))
    assert sc["span"][1] == str(AS_OF.date())
    for seed in S5.SEEDS:
        on, fl, st = RL.synthetic_feed(days, label, era, seed)
        f = _feeds()[f"degraded:{seed}"]
        assert f.attrs["era"] == era and f.attrs["flags"] == fl and S5._v1_onsets(f) == on, seed
        lag = (f["date"] - f["filed"]).dt.days
        assert set(lag) <= {0, 1}, seed
        kept = f.assign(b=[GEO1.basin_of_outfall(o) for o in f["outfall"]]).drop_duplicates(["filed", "b"])
        assert len(kept) == st["true_onset_days"] - st["dropped"] and int((kept["date"] != kept["filed"]).sum()) == st["lagged"], seed
    on, fl, st = RL.synthetic_feed(days, label, era, None)
    o = _feeds()["oracle"]
    o = o[(o["date"] >= pd.Timestamp(era[0])) & (o["date"] <= pd.Timestamp(era[1]))]
    o.attrs = dict(_feeds()["oracle"].attrs)
    assert S5._v1_onsets(o) == on and st["dropped"] == st["lagged"] == 0
    assert st["true_onset_days"] == 88, st                      # design §3.5: 88 true onset basin-days 2023-07 → 2026-08
    # the same observations placed in sfpuc4_v1: same rows and days, their basins read through the new geography
    f4 = _feeds("sfpuc4_v1")["degraded:1"]
    f1 = _feeds()["degraded:1"]
    assert f4[["date", "outfall", "filed", "zone"]].equals(f1[["date", "outfall", "filed", "zone"]])
    assert set(f4["basin"]) <= set(GEO4.keys) and "south" in set(f4["basin"])


# ── (7) one conditional set for every variant ──────────────────────────────

def test_the_conditional_set_is_the_feeds_and_identical_across_variants():
    """For one feed every variant's rows cover the same zone-days with the same exclusions, tags, truth and
    plain value; the set is ``observed`` (own zone or a feeding basin on D−7…D), so no row is X-S5-QUIET; the
    plain variant's p is its b; sample_swap's p is its b (OUT reads no lab result, A1) while its q moves."""
    for feed in ("degraded:1", "oracle"):
        rows = _rows(feed)
        assert list(rows.columns) == list(X.COLUMNS)
        assert set(rows["variant"]) == set(S5.VARIANTS) and not (rows["excl"] == "X-S5-QUIET").any()
        assert (rows["stage"] == "s5").all() and (rows["tier"] == "T1").all() and (rows["entry"] == feed).all()
        assert rows["lead"].isna().all() and rows[["p", "q", "b"]].notna().all().all()
        assert (rows["date"] >= X.POST_START).all() and (rows["date"] <= AS_OF).all()
        ref = rows[rows["variant"] == "plain"].reset_index(drop=True)
        assert (ref["p"] == ref["b"]).all()
        for v in S5.VARIANTS:
            g = rows[rows["variant"] == v].reset_index(drop=True)
            for c in ("date", "unit", "excl", "tags", "stratum", "y", "y2", "b", "n_sampled"):
                pd.testing.assert_series_equal(g[c], ref[c], check_exact=True, obj=f"{feed} {v} {c}")
        ss = rows[rows["variant"] == "sample_swap"].reset_index(drop=True)
        assert (ss["p"] == ss["b"]).all() and (ss["q"] != ref["q"]).any()
        lz = rows[rows["variant"] == "link_zone_swap"]
        assert (lz["p"] != lz["b"]).any()
        fd = _feeds()[feed]
        days = pd.DatetimeIndex(sorted(set(ref["date"])))
        seen = S5.observed(GEO1, fd, pd.date_range(X.POST_START, AS_OF))
        want = {(d, z) for z in ZONES for d in seen.index[seen[z].to_numpy()]}
        assert set(zip(ref["date"], ref["unit"])) == want, len(want)
        # independently of ``observed``: exclusions' own X-S5-QUIET, run on every zone-day of the window, leaves
        # exactly the conditional set (no X-S5-HEALTH / X-S5-CIRC on these feeds, which would come before it)
        every = X.skeleton("s5", list(ZONES), X.POST_START, AS_OF, entry=feed, tier="T1")
        ctx = X.context(GEO1, start=X.POST_START, end=AS_OF, feeds={feed: fd})
        every["y"] = ctx.frames["zone"]["out_y"].reindex(pd.MultiIndex.from_arrays([every["unit"], every["date"]])).to_numpy()
        every = X.apply(every, "s5", ctx)
        assert set(zip(every.loc[every["excl"] != "X-S5-QUIET", "date"], every.loc[every["excl"] != "X-S5-QUIET", "unit"])) == want
        pd.testing.assert_series_equal(every.set_index(["date", "unit"]).loc[list(zip(ref["date"], ref["unit"])), "excl"].reset_index(drop=True),
                                       ref["excl"], check_names=False)
        # exclusions counts one variant at a time, and the partition holds
        cnt = X.counts(rows[rows["variant"] == "link_zone_swap"])
        assert sum(c["n_total"] for u in cnt["partition"]["s5"].values() for e in u.values() for c in e.values()) == len(ref)
        _raises(ValueError, X.counts, rows)
        print(f"   {feed}: {len(ref)} zone-days per variant on {len(days)} days, "
              f"{int((ref['excl'] == '').sum())} scored")


def test_all_days_scope_holds_every_scored_day():
    """scope='all_days' (S5's all_days table, X-S5-QUIET not applied): every zone-day of the T1 window; on the
    conditional set its rows are the conditional rows, and off it an injection variant is the plain value."""
    inputs, tt = _inputs()
    rows = S5.s5_rows(GEO1, _adapter(), inputs, "degraded:1", _feeds()["degraded:1"], variants=("plain", "link_zone_swap"),
                      tier="T1", trained_through=tt, scope="all_days")
    one = rows[rows["variant"] == "link_zone_swap"].set_index(["date", "unit"])
    assert len(one) == len(ZONES) * len(pd.date_range(X.POST_START, AS_OF)) and not (rows["excl"] == "X-S5-QUIET").any()
    cond = _rows("degraded:1")
    c = cond[cond["variant"] == "link_zone_swap"]
    key = list(zip(c["date"], c["unit"]))
    cols = ["p", "b", "q", "y", "y2", "excl"]
    pd.testing.assert_frame_equal(one.loc[key, cols].reset_index(drop=True), c[cols].reset_index(drop=True), check_exact=True)
    off = one[~one.index.isin(key)]
    assert len(off) > len(c) and (off["p"] == off["b"]).all()


# ── (8) tiers, windows and folds ───────────────────────────────────────────

def _no_weights(window, geo=GEO4) -> C.BasinInputs:
    """Inputs that saw no day: constant p and v̂ (a fixture, not a model), so the T2 rows built on them, with
    the fixture specs (identity S3, flat zone_v3 S4), are honest out-of-fold rows."""
    idx = pd.date_range(*window)
    return C.BasinInputs(pd.DataFrame(0.1, idx, list(geo.keys)), pd.DataFrame(5.0, idx, list(geo.keys)))


def test_tier_window_and_fold_leaks_raise():
    """No T3 row: T0 / T1 / T1-holdout rows say what the inputs were fit through (the served table, split and
    heads saw every day to 2025-10-31, so the holdout window is in-sample for them), T2 rows which season they
    were held out of; T1 stops at the freeze (a later day is T0's); co-firing shares from events off the fit
    days raise (a T2 fold's own season above all); a feed is scored under its own name only; unknown names
    raise. Each raise is checked for its reason."""
    inputs, tt = _inputs()
    fd = _feeds()
    args = (GEO1, _adapter(), inputs, "oracle", fd["oracle"])
    _raises(ValueError, S5.s5_rows, *args, tier="T3", trained_through=tt, match="X-ALL-INSAMPLE")
    _raises(ValueError, S5.s5_rows, *args, tier="T1", match="fit through")
    _raises(ValueError, S5.s5_rows, *args, tier="T1-holdout", trained_through=tt, match="would be T3")
    _raises(ValueError, S5.s5_rows, *args, tier="T2", match="held_out_season")
    _raises(ValueError, S5.s5_rows, *args, tier="T2", trained_through=tt, match="trained_through does not apply")
    _raises(ValueError, S5.s5_rows, *args, tier="T1", trained_through=tt, held_out_season=2022, match="for T2 rows")
    _raises(ValueError, S5.s5_rows, *args, tier="T9", trained_through=tt, match="unknown tier")
    # T1 runs to the freeze date, T0 from the day after (a day is scored in one tier)
    run = pd.date_range("2025-10-01", "2026-12-31")
    t1, _, _ = S5._window("T1", tt, run)
    t0, _, _ = S5._window("T0", tt, run)
    assert t1.min() == X.POST_START and t1.max() == X.freeze_date() and t0.min() == X.freeze_date() + pd.Timedelta(days=1)
    ev = csd_labels.load_events()
    _raises(ValueError, S5.s5_rows, *args, tier="T1", trained_through=tt, fold_events=ev, match="off the fit days")
    ok = ev[pd.to_datetime(ev["event_date"]) <= tt]
    assert not S5.s5_rows(*args, variants=("link_swap",), tier="T1", trained_through=tt, fold_events=ok).empty
    # the feed's name is the frame's: the perfect feed under a degraded name would score its sibling rows (Part B 9)
    _raises(ValueError, S5.s5_rows, GEO1, _adapter(), inputs, "degraded:1", fd["oracle"], tier="T1", trained_through=tt,
            match="scored as")
    seen = S5.observations(GEO1, [{"date": "2025-12-01", "outfall": SEA_CLIFF}], era=None, name="watcher")
    _raises(ValueError, S5.s5_rows, GEO1, _adapter(), inputs, "watcher", seen, tier="T1", trained_through=tt, match="no era")
    # T2: one held-out season (2022-23) and its 8-day lead-in, on inputs and specs that saw no day
    f4 = _feeds("sfpuc4_v1")["oracle"]
    season = _no_weights(("2022-06-23", "2023-06-30"))
    t2 = dict(variants=("link_swap",), tier="T2")
    _raises(ValueError, S5.s5_rows, GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, match="held_out_season")
    _raises(ValueError, S5.s5_rows, GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, held_out_season=2021, match="2021 only")
    _raises(ValueError, S5.s5_rows, GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, held_out_season=2025, match="not a T2 season")
    d_ev = pd.to_datetime(ev["event_date"])
    in_t2 = ev[(d_ev >= S5.T2_SPAN[0]) & (d_ev <= S5.T2_SPAN[1])]
    _raises(ValueError, S5.s5_rows, GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, held_out_season=2022, fold_events=in_t2,
            match="held-out season 2022")                                            # the fold counted its own season
    not_2022 = ev[[S5._season(pd.Timestamp(d)) != 2022 for d in ev["event_date"]]]
    _raises(ValueError, S5.s5_rows, GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, held_out_season=2022, fold_events=not_2022,
            match="off the fit days")                                                # 2025-26 is no T2 season
    fold = in_t2[[S5._season(pd.Timestamp(d)) != 2022 for d in in_t2["event_date"]]]
    rows = S5.s5_rows(GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, held_out_season=2022, fold_events=fold)
    dflt = S5.s5_rows(GEO4, _sfpuc4_specs(), season, "oracle", f4, **t2, held_out_season=2022)
    pd.testing.assert_frame_equal(rows, dflt, check_exact=True)                       # the default fold is that one
    assert not rows.empty and {S5._season(d) for d in rows["date"]} == {2022} and (rows["tier"] == "T2").all()
    # T1-holdout on inputs fit before 2023-07-01 (here: none): its rows fall in 2023-07-01 → 2025-10-31
    hold = S5.s5_rows(GEO4, _sfpuc4_specs(), _no_weights(("2023-06-20", "2024-03-31")), "degraded:1", _feeds("sfpuc4_v1")["degraded:1"],
                      variants=("link_swap",), tier="T1-holdout", trained_through="2023-06-30")
    assert not hold.empty and (hold["tier"] == "T1-holdout").all() and hold["date"].min() >= X.HOLDOUT_START
    two = _no_weights(("2022-06-23", "2023-08-31"))
    _raises(ValueError, S5.s5_rows, GEO4, _sfpuc4_specs(), two, "oracle", f4, **t2, held_out_season=2022, fold_events=fold,
            match="span")
    _raises(KeyError, S5.s5_rows, *args, variants=("magic",), tier="T1", trained_through=tt, match="variants must be")
    _raises(KeyError, S5.s5_rows, GEO1, _adapter(), inputs, "sideways", fd["oracle"], tier="T1", trained_through=tt,
            match="unknown S5 feed")
    _raises(ValueError, S5.s5_rows, GEO1, _adapter(), inputs, "oracle", fd["oracle"].drop(columns="filed"), tier="T1",
            trained_through=tt, match="columns")
    lost = fd["oracle"].copy()
    lost.attrs = {}
    _raises(ValueError, S5.correct, GEO1, _adapter(), inputs, lost, "link_swap", match="lost its attrs")
    _raises(ValueError, S5.correct, GEO4, _sfpuc4_specs(), _window(inputs, STORM, GEO4), _feeds("sfpuc4_v1")["oracle"], "basin_swap",
            match="basin_swap is live_v2")
    _raises(ValueError, S5.correct, GEO1, _adapter(), _window(inputs, STORM), fd["oracle"], "link_swap", days=[pd.Timestamp(STORM[0])],
            match="8 days before it")
    _raises(KeyError, S5.observations, GEO1, [{"date": "2023-01-01", "outfall": "CSD-999"}], match="not an outfall")
    _raises(KeyError, S5.observations, GEO1, [{"date": "2023-01-01", "station": "BAY#320_SL"}])   # a registry key, not a station id
    _raises(ValueError, S5.observations, GEO1, [{"date": "2023-01-01", "outfall": SEA_CLIFF, "station": "4619"}], match="exactly one")
    _raises(KeyError, S5.observations, GEO1, [], flags={"2023-01-01": {"south"}}, match="geo_v1")   # live_v2's flags are geo_v1 basins


# ── (9) the next-sample rates ──────────────────────────────────────────────

def test_sample_rates_are_fit_on_training_days_and_drive_d_plus_1_to_3():
    """sample_rates counts consecutive sampled zone-days ≤ 3 days apart on the fit days only (East in a tail to
    2025-10-31: 148 elevated → elevated, 35 → clean); a cell under 10 pairs has no rate. In sample_swap a result
    sampled on d sets q on d+1…d+3 to its rate and leaves q(d) to the model (it is known a day later)."""
    fit = pd.date_range(T.TRUTH_START, "2025-10-31")
    r = S5.sample_rates(GEO1, fit)
    assert r["east"]["n"]["tail"] == {"EE": 148, "EC": 35, "CE": 13, "CC": 6}, r["east"]["n"]
    assert r["east"]["tail"]["elevated"] == 148 / 183 and r["east"]["tail"]["clean"] == 13 / 19
    assert r["baker_china"]["tail"]["clean"] is None                       # 2 + 5 pairs: too thin to fit
    early = S5.sample_rates(GEO1, pd.date_range(T.TRUTH_START, "2023-06-30"))
    assert sum(early["east"]["n"]["tail"].values()) < sum(r["east"]["n"]["tail"].values())
    inputs, _ = _inputs()
    inp = _window(inputs, STORM)
    empty = S5.observations(GEO1, [], era=None)
    plain = S5.correct(GEO1, _adapter(), inp, empty, "plain")
    smp = T._samples(("datasf", "poobot"))
    zs = SMP.zone_sample_days(smp)
    east = zs[(zs["zone"] == "east") & (zs["date"] >= plain.q.index[0] + pd.Timedelta(days=7)) & (zs["date"] <= plain.q.index[-1] - pd.Timedelta(days=3))]
    d = east["date"].iloc[0]
    one = smp[(smp["date"] == d) & (smp["zone"] == "east")]
    got = S5.correct(GEO1, _adapter(), inp, empty, "sample_swap", samples=one, rates=r)
    e = bool(east["any_exceedance"].iloc[0])
    for k in (1, 2, 3):        # the regime as live_v2 reads it on issue day d + k: the model's p on d−7…d inside d+k−8…
        lo = max(d - pd.Timedelta(days=7), d + pd.Timedelta(days=k - 8))
        regime = "tail" if (plain.p["east"].loc[lo:d] >= 0.5).any() else "dry"
        assert got.q.at[d + pd.Timedelta(days=k), "east"] == r["east"][regime]["elevated" if e else "clean"], k
    assert got.q.at[d, "east"] == plain.q.at[d, "east"]
    pd.testing.assert_frame_equal(got.r, plain.r, check_exact=True)
    pd.testing.assert_frame_equal(got.q.drop(columns="east"), plain.q.drop(columns="east"), check_exact=True)


# ── (10) exclusions' variant column is additive ────────────────────────────

def test_exclusions_variant_column_is_additive():
    """'variant' is appended to exclusions.COLUMNS (the 18 before it unchanged); a row frame without it still
    validates (as variant ''); a non-S5 row carrying one raises; X-POWER is decided per variant, so two variants
    of the same rows are tagged as one alone; counts takes one variant at a time. tests/test_exclusions.py runs
    unchanged in the full suite."""
    assert X.COLUMNS[:-1] == ("date", "stage", "unit_type", "unit", "entry", "lead", "tier", "sel", "p", "q", "b", "v_hat",
                              "y", "y2", "n_sampled", "stratum", "excl", "tags") and X.COLUMNS[-1] == "variant"
    sk = X.skeleton("out", list(ZONES), "2025-11-01", "2025-11-30", entry="rain", tier="T1")
    assert list(sk.columns) == list(X.COLUMNS) and (sk["variant"] == "").all()
    ctx = X.context(GEO1, start="2025-11-01", end="2025-11-30")
    sk["y"] = ctx.frames["zone"]["out_y"].reindex(pd.MultiIndex.from_arrays([sk["unit"], sk["date"]])).to_numpy()
    a = X.apply(sk, "out", ctx)
    b = X.apply(sk.drop(columns="variant"), "out", ctx)
    assert "variant" not in b.columns
    pd.testing.assert_frame_equal(a.drop(columns="variant"), b, check_exact=True)
    _raises(ValueError, X.apply, sk.assign(variant="link_swap"), "out", ctx)
    rows = _rows("degraded:1")
    one = rows[rows["variant"] == "link_swap"]
    ctx5 = X.context(GEO1, start=rows["date"].min(), end=rows["date"].max(), feeds={"degraded:1": _feeds()["degraded:1"]})
    alone = X.apply(one, "s5", ctx5)
    both = X.apply(pd.concat([one, one.assign(variant="zone_swap")], ignore_index=True), "s5", ctx5)
    assert (both["tags"].iloc[:len(one)].to_numpy() == alone["tags"].to_numpy()).all()
    assert (alone["tags"].str.contains("X-POWER")).any()
    mixed = X.counts(pd.concat([a, alone], ignore_index=True))                  # one variant per stage: fine
    assert set(mixed["partition"]) == {"out", "s5"}
    _raises(ValueError, X.counts, both)


# ── (11) the module ────────────────────────────────────────────────────────

def test_module_does_no_io_at_import_and_reads_the_served_name():
    src = (MODELS / "stages_s5.py").read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) or (isinstance(node, ast.If) and "__main__" in ast.dump(node.test)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "attr", getattr(sub.func, "id", ""))
                assert name not in {"open", "read_text", "read_csv", "load", "loads", "load_events", "feeds"}, (name, ast.dump(node)[:120])
    for word in ("write_text", "write_bytes", ".dump(", "to_csv", "'w'", '"w"'):
        assert word not in src, word
    served = (FORECAST / "data" / "models" / "served.json").read_text()
    assert S5.served_name() in served and S5.served_name() not in src     # read, never typed
    # replay_live's additive function: its old entry points are all still there
    for name in ("main", "run_synthetic", "replay", "plain_recomposition", "synthetic_feed", "archive_flags", "per_day_risks"):
        assert callable(getattr(RL, name)), name


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
