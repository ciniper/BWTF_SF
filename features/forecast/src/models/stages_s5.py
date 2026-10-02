"""stages_s5 — S5 live corrections, replayed through the stage composition (P7c;
STAGES_DESIGN.md Part C §3.5, §2.5, §5.5, §7 step 6, as amended by Part B 1, 9
and 16; STAGES_PROTOCOL.md §1 "S5", §3 "S5 oracle", §4.1, §7's S5 rules and
§8's S5 primary).

S5 asks whether what was seen improves what comes after. An observation
replaces a prediction at the stage it measures (compose_v2.Inject): a named
outfall or a station's CSO flag at S3, after the split; a lab result at S4.
Every variant is replayed on the same S2 inputs (Part B 16) and graded against
OUT's label on the observation-conditional set, which the feed defines, so it
is the same set for every variant (protocol §4.1):

    feeds(geo)     the S5 feeds, one observation frame each, in the shape
                   exclusions.Context.with_inputs(feeds=...) checks (date, zone,
                   basin) plus outfall, link, stations and the filed day:
        oracle        the perfect feed: every CIWQS event on its filed day, at
                      outfall resolution (the ledger itself, so sibling zone-days
                      are never scored on it: X-S5-PERFECT-SIBLING, Part B 9)
        archive       the 2016-17 Poo Bot feed (truth.archive_onsets, multi-basin
                      structures split, Part B 22)
        degraded:<s>  replay_live.synthetic_feed for seed s (13% of onsets never
                      shown, 60% of the rest a day late), expanded to the filed
                      outfalls of each basin-day it kept
        watcher       our watcher's alert_log, 2026-08-20 →: EMPTY until a wet
                      season exists (it started after the hindcast's last day,
                      2026-08-17, and has watched no wet season yet). Its rows will
                      be station CSO transitions (stations, no outfall, until the
                      feed's structure field is verified); X-S5-HEALTH reads its
                      health from watcher_runtime.
    correct(...)   one variant's corrected S3 p_z, S4 q_z and OUT r_z per day
    s5_rows(...)   rows.csv.gz rows for one feed: p = the corrected r_z, b = the
                   plain r_z (the paired ΔBrier is p vs b on one row), q = the
                   corrected q_z, y = truth.out_label, y2 = the S4 sample truth,
                   'variant' naming the correction (exclusions.COLUMNS' additive
                   column), excl / tags / stratum from exclusions.apply
    sample_rates   the next-sample transition rates sample_swap reads, fit on
                   training days only
    served_inputs  development inputs (P7c): the served set's stored per-day p and
                   v̂, exactly as replay_live replays them; stages_build passes S2
                   engine outputs instead

Variants (§3.5):
    plain           no observation
    basin_swap      live_v2 exactly (the incumbent): p_b = 1 on an onset, then the
                    split, with every other live_v2 rule (the previous-day anchor,
                    the no-flag downgrade, the large-event volume, the sample floor
                    and cap, the flag hold), replayed day by day through compose_v2's
                    GEO_V1 adapter. tests/test_stages_s5.py pins it to
                    replay_live.per_day_risks day by day, on the archive feed and
                    over a degraded feed's whole era.
                    live_v2 is defined on geo_v1's basins and legacy groups, so
                    another geography raises (its incumbent is a P8d question).
    link_swap       a named outfall: its link p = 1, the basin's other links at
                    least their co-firing share (compose_v2.cofire on the fold's
                    ledger events, never all-time when a fold is scored)
    zone_swap       a flagged station: its zone p_z = 1; its basin only through
                    geography.station_basin (§2.5). On the oracle, archive and
                    degraded feeds a row's station flags are the stations its outfall
                    posts (what the map flags for that structure).
    link_zone_swap  the §8 primary's candidate: a row naming an outfall goes in at
                    its link (a structure name overrides the station, §2.5), a row
                    with only a station flag at its zone
    sample_swap     a lab result at S4: q_z(D) = the result is the replaced unit-day;
                    q_z(D+1…D+3) take the next-sample transition rate P(over standard
                    at the next sample | this result, in a tail or dry), fit on
                    training days. A result reaches the forecast known_lag_days (1)
                    after sampling (live_rules), so an issue-day row never holds its
                    own day's result: a row carries the newest result known by its
                    day, within the 3-day horizon, its regime (tail or dry) read as
                    live_v2's in_tail reads it on that issue day. The data support it with thin
                    cells (Baker & China's tail: 17 elevated and 7 clean pairs to
                    2025-10-31); a cell under 10 pairs (protocol §6's power floor)
                    has no rate and leaves q as the model's. OUT never reads a lab
                    result (A1), so sample_swap's p equals its b.

How the replay reads time. Every row is the forecast at the end of its day: a
CSO observation is known the day it is shown (the map is real-time), so on the
observation's own zone-day the corrected value is the replaced one (X-S5-SELF),
and its siblings' same day is a real nowcast; lab results lag a day. For the
injection variants this is one composition over the run (an injection on d moves
nothing before d); basin_swap is composed issue day by issue day, because live_v2
reads the day (quiet days, the next day's onset), as replay_live does at
cutoff="end". live_v2 and sample_swap's tail test read the whole feed, not just the
run's days (live_v2's in_tail looks back to D−10), so a run that starts mid-era
gives the same rows as the full run.

The rows' set (protocol §4.1, §7): zone-days D…D+7 after any observation in the
same zone or in a basin feeding it (exclusions' own | near), on scored days only:
inside the tier's window (T1 2025-11-01 → the freeze; T1-holdout 2023-07-01 →
2025-10-31; T0 after the freeze; T2 one held-out season), inside the feed's era,
and with the 8 days before them in the inputs (live_v2's 9-day window). A day the
inputs' weights saw is never a row (T3): T0 / T1 / T1-holdout rows say what the
inputs were fit through (trained_through), T2 rows which season they were held out
of (held_out_season), and the S5 parameters (co-firing shares, sample rates) are
fit on the days before the window, or on the other T2 seasons for T2; a fold event
off those days raises. The feed is scored under its own name only (the perfect
feed's sibling rule reads it). X-S5-INSAMPLE tags the archive years, where the
served S2 was in-sample.

No module-level IO; nothing is written. The served set's name is read from
served.json, never typed.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, HERE, HERE.parent / "collectors"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import compose_v2 as C  # noqa: E402
import csd_labels  # noqa: E402  (the ledger the co-firing shares count)
import exclusions as X  # noqa: E402
import live_rules as LR  # noqa: E402  (read only: live_v2's rules)
import posting_label as PL  # noqa: E402
import replay_live as RL  # noqa: E402
import samples as SMP  # noqa: E402
import train_v4  # noqa: E402  (APP_BASINS: the scorecard's basin order, which synthetic_feed draws in)
import truth as T  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.outfalls import OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONE_OF_STATION, ZONES  # noqa: E402

SERVE_DIR = FORECAST / "data" / "models"
VARIANTS = ("plain", "basin_swap", "link_swap", "zone_swap", "link_zone_swap", "sample_swap")
PRIMARY = ("link_zone_swap", "basin_swap")        # protocol §8: candidate vs incumbent (= live_v2)
SEEDS = RL.SYN_SEEDS                              # the degraded feed's draws (replay_live --synthetic)
OBS_DAYS = X.OBS_DAYS                             # zone-days D…D+7 after an observation
HISTORY_DAYS = 8                                  # a row needs D−8…D in the inputs (live_v2's 9-day window)
FEED_COLUMNS = ("date", "zone", "basin", "link", "outfall", "stations", "filed")
SCOPES = ("conditional", "all_days")              # the primary set, or every scored day (S5's all_days table)
RATE_MIN_PAIRS = X.POWER_MIN_POSITIVES            # a transition cell needs 10 pairs (protocol §6's power floor)
T2_SPAN = (pd.Timestamp("2016-07-01"), pd.Timestamp("2025-06-30"))   # protocol §2: seasons 2016-17 … 2024-25
_SAMPLE = LR.RULES["samples"]
LAG, HORIZON, TAIL_DAYS, TAIL_MIN_P = _SAMPLE["known_lag_days"], _SAMPLE["horizon_days"], _SAMPLE["tail_days"], _SAMPLE["tail_min_p"]


def _ts(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def _date(d) -> dt.date:
    return _ts(d).date()


@functools.lru_cache(maxsize=1)
def _geo1() -> G.Geography:
    """geo_v1, whose basins the scorecard, the synthetic feed and live_v2 are keyed by."""
    g = G.get("geo_v1")
    want = tuple(RL.BASIN_KEYS[b] for b in train_v4.APP_BASINS)
    if g.keys != want:
        raise AssertionError(f"geo_v1 basins {g.keys} are not the scorecard's order {want}: synthetic_feed draws in that order")
    return g


# ── feeds ──────────────────────────────────────────────────────────────────

def observations(geo, obs, era=None, flags=None, name: str = "") -> pd.DataFrame:
    """An S5 feed frame from observations: each a dict with ``date`` (the day it was shown) and
    exactly one of ``outfall`` (a named structure's CSD id) or ``station`` (an SFPUC station id with a
    CSO flag), and optionally ``filed`` (the ledger day it stands for).

    Columns FEED_COLUMNS: an outfall row takes its zone, basin and link from the geography and its
    station flags from the registry (the stations it posts); a station row has no outfall or link, its
    zone from shared/zones.py and its basin from geography.station_basin (None: the zone alone, §2.5).
    ``attrs``: feed (name), geography, era ((first, last) day the feed was watched; None = never) and
    flags ({datetime.date: frozenset of geo_v1 basin keys}, the days live_v2 saw a CSO flag up). An
    unknown outfall, station or basin raises."""
    geo = T._geo(geo)
    rows = []
    for o in obs:
        has_o, has_s = o.get("outfall") is not None, o.get("station") is not None
        if has_o == has_s:
            raise ValueError(f"an observation names an outfall or a station, exactly one: {o}")
        d = _ts(o["date"])
        filed = _ts(o["filed"]) if o.get("filed") is not None else pd.NaT
        if has_o:
            oid = o["outfall"]
            if oid not in OUTFALLS:
                raise KeyError(f"{oid!r} is not an outfall in shared/outfalls.py")
            lk = geo.link_of_outfall(oid)
            rows.append({"date": d, "zone": lk.zone, "basin": lk.basin, "link": lk.id, "outfall": oid,
                         "stations": ";".join(OUTFALLS[oid].stations), "filed": filed})
        else:
            sid = str(o["station"])
            basin = geo.station_basin(sid)                    # KeyError for an id that is no station
            rows.append({"date": d, "zone": ZONE_OF_STATION[sid], "basin": basin, "link": None, "outfall": None,
                         "stations": sid, "filed": filed})
    f = pd.DataFrame(rows, columns=list(FEED_COLUMNS))
    f["date"] = pd.to_datetime(f["date"])
    f["filed"] = pd.to_datetime(f["filed"])
    f = f.sort_values(["date", "zone", "outfall", "stations"], kind="stable", na_position="last").reset_index(drop=True)
    fl = {}
    keys1 = set(_geo1().keys)
    for d, bks in dict(flags or {}).items():
        bad = set(bks) - keys1
        if bad:
            raise KeyError(f"flag basins {sorted(bad)} are not geo_v1's (live_v2's) {sorted(keys1)}")
        if bks:
            fl[_date(d)] = frozenset(bks)
    f.attrs = {"feed": name, "geography": geo.version, "era": None if era is None else (_date(era[0]), _date(era[1])),
               "flags": dict(sorted(fl.items()))}
    return f


def _check_feed_frame(feed: pd.DataFrame, geo) -> None:
    if not isinstance(feed, pd.DataFrame) or list(feed.columns) != list(FEED_COLUMNS):
        raise ValueError(f"an S5 feed is a frame with columns {FEED_COLUMNS} (stages_s5.feeds / observations)")
    for k in ("era", "flags", "geography"):
        if k not in feed.attrs:
            raise ValueError(f"the feed frame lost its attrs[{k!r}]: build it with stages_s5.feeds or observations")
    if feed.attrs["geography"] != geo.version:
        raise ValueError(f"the feed was placed in {feed.attrs['geography']}, the run is {geo.version}")


def _ledger_days(end) -> list:
    """The ledger's geo_v1 basin onsets as scorecard days ({date, basins: {key: {y}}}, the scorecard's basin
    order): what synthetic_feed reads. On 2023-07-01 → 2026-08-17 it is the served scorecard's y exactly."""
    g = _geo1()
    bo = T.basin_onsets(g, end=end)
    y = bo.pivot(index="date", columns="basin", values="y")[list(g.keys)]
    return [{"date": str(d.date()), "basins": {bk: {"y": None if pd.isna(v) else int(v)} for bk, v in zip(g.keys, row)}}
            for d, row in zip(y.index, y.astype(object).to_numpy())]


def _draws(days: list, era: tuple, seed) -> dict:
    """{(filed day, basin): shown day, or None if dropped}: synthetic_feed's two draws per true onset, in
    its order (days, then basins; a miss draw, then a lag draw for each one kept). synthetic_feed's output
    keys onsets by the day shown, which cannot say which filed day a shown onset stands for; this is that
    attribution, and ``_degraded`` checks it reproduces synthetic_feed's onsets exactly on every call."""
    rng = random.Random(seed) if seed is not None else None
    out = {}
    for d in days:
        D = dt.date.fromisoformat(d["date"])
        if D < era[0] or D > era[1]:
            continue
        for bk, b in d["basins"].items():
            if not b.get("y"):
                continue
            if rng is not None and rng.random() < RL.SYN_MISS_RATE:
                out[(D, bk)] = None
                continue
            lag = rng is not None and rng.random() < RL.SYN_LAG_RATE
            out[(D, bk)] = D + dt.timedelta(days=1) if lag else D
    return out


def _events_by_basin_day(end) -> dict:
    ev = T.ledger_events(_geo1())
    ev = ev[ev["date"] <= _ts(end)]
    return {(b, d.date()): sorted(g) for (b, d), g in ev.groupby(["basin", "date"])["outfall"]}


def _degraded(geo, days: list, events: dict, label, seed, era: tuple) -> pd.DataFrame:
    onsets, flags, _ = RL.synthetic_feed(days, label, era, seed)
    draws = _draws(days, era, seed)
    agg: dict = {}
    for (_, bk), shown in draws.items():
        if shown is not None:
            agg.setdefault(shown, set()).add(bk)
    if agg != onsets:
        raise AssertionError(f"replay_live.synthetic_feed draws differently from stages_s5._draws (seed {seed}): "
                             "the outfall expansion would not be its feed")
    obs = []
    for (D, bk), shown in draws.items():
        if shown is None:
            continue
        if (bk, D) not in events:
            raise ValueError(f"the ledger has no {bk} event on {D} although its basin onset is 1")
        obs += [{"date": shown, "outfall": o, "filed": D} for o in events[(bk, D)]]
    return observations(geo, obs, era=era, flags=flags, name="degraded" if seed is None else f"degraded:{seed}")


def _v1_onsets(feed: pd.DataFrame) -> dict:
    """{datetime.date: {geo_v1 basin}}: the basin onsets live_v2 reads from a feed's rows (an outfall's
    geo_v1 basin; a station-only row's geo_v1 station_basin, as live_dashboard maps alert_log stations)."""
    g = _geo1()
    out: dict = {}
    for d, o, s in zip(feed["date"], feed["outfall"], feed["stations"]):
        b = g.basin_of_outfall(o) if isinstance(o, str) else g.station_basin(s)
        if b is not None:
            out.setdefault(d.date(), set()).add(b)
    return out


def feeds(geo, end=None, seeds=SEEDS) -> dict:
    """{'oracle', 'archive', 'degraded:<seed>' for each seed, 'watcher': feed frame} (see ``observations``),
    placed in ``geo``. ``end`` defaults to the data end (exclusions: the ledger grid, gauges and samples).

    oracle      every ledger event on its filed day, 2016-10-01 (the first continuous ledger, Bayside) →
                end; flags: synthetic_feed's for the perfect feed (BeachWatch's CSO postings, else 2 days)
    archive     truth.archive_onsets, one row per outfall; era and flags from replay_live.archive_flags
                (2016-03-19 → 2017-01-10); its geo_v1 basin onsets are archive_flags' exactly (checked)
    degraded:s  replay_live.synthetic_feed(seed s) over 2023-07-01 → end (run_synthetic's era), each kept
                basin onset expanded to that basin-day's filed outfalls, shown on the day the draw put it
    watcher     empty, era None (see the module notes)
    """
    geo = T._geo(geo)
    end = X._data_end(tuple(SMP.DEFAULT_SOURCES)) if end is None else _ts(end)
    days = _ledger_days(end)
    events = _events_by_basin_day(end)
    label = PL.from_beachwatch()
    out = {}
    # the perfect feed: the ledger itself
    lo = min(T.ledger_start(f) for f in sorted({b.facility for b in geo.basins}))
    era = (lo.date(), end.date())
    onsets, flags, _ = RL.synthetic_feed(days, label, era, None)
    obs = [{"date": D, "outfall": o, "filed": D} for (bk, D), oids in sorted(events.items(), key=lambda kv: (kv[0][1], kv[0][0]))
           if era[0] <= D <= era[1] for o in oids]
    out["oracle"] = observations(geo, obs, era=era, flags=flags, name="oracle")
    if _v1_onsets(out["oracle"]) != onsets:
        raise AssertionError("the perfect feed's basin onsets differ from synthetic_feed's perfect feed")
    # the 2016-17 archive
    ao = T.archive_onsets(geo)
    a_on, a_fl, a_era = RL.archive_flags()
    obs = [{"date": d, "outfall": o} for d, ids in zip(ao["date"], ao["outfall_ids"]) for o in str(ids).split("|")]
    out["archive"] = observations(geo, obs, era=a_era, flags=a_fl, name="archive")
    if _v1_onsets(out["archive"]) != a_on:
        raise AssertionError("truth.archive_onsets and replay_live.archive_flags disagree on the archive's basin onsets "
                             "(archive_flags drops multi-basin structures, Part B 22)")
    era = (train_v4.HOLDOUT_START.date(), end.date())
    for s in seeds:
        out[f"degraded:{s}"] = _degraded(geo, days, events, label, s, era)
    out["watcher"] = observations(geo, [], era=None, flags={}, name="watcher")
    return out


# ── the conditional set ────────────────────────────────────────────────────

def observed(geo, feed: pd.DataFrame, days) -> pd.DataFrame:
    """date × zone: True where the feed holds an observation in the zone, or in a basin feeding it, on
    D−7…D — exclusions' own | near, the observation-conditional set (X-S5-QUIET is its complement).
    It reads the feed alone, so it is the same for every variant."""
    geo = T._geo(geo)
    days = pd.DatetimeIndex(days)
    span = pd.date_range(days.min() - pd.Timedelta(days=OBS_DAYS), days.max())
    out = {}
    for z in ZONES:
        feeding = set(T.feeding_basins(geo, z))
        hit = feed[(feed["zone"] == z) | feed["basin"].isin(feeding)]
        n = pd.Series(1, index=pd.DatetimeIndex(hit["date"])).groupby(level=0).sum().reindex(span, fill_value=0)
        out[z] = n.rolling(OBS_DAYS + 1, min_periods=1).sum().reindex(days).to_numpy() > 0
    return pd.DataFrame(out, index=days)[list(ZONES)]


# ── the corrections ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Corrected:
    """One variant's corrected frames over the days asked for (date × zone): S3's p_z, S4's q_z, OUT's r_z."""
    variant: str
    p: pd.DataFrame
    q: pd.DataFrame
    r: pd.DataFrame


def _frames(variant, comp: C.Composition, days) -> Corrected:
    return Corrected(variant, comp.s3.zone_p.loc[days], comp.s4.zone.loc[days], comp.out.zone.loc[days])


def _in_run(feed: pd.DataFrame, run: pd.DatetimeIndex) -> pd.DataFrame:
    """The feed's rows on the run's days (an observation before or after the inputs moves no scored day)."""
    out = feed[feed["date"].isin(run)].copy()
    out.attrs = dict(feed.attrs)
    return out


def _by_day(dates, values) -> dict:
    out: dict = {}
    for d, v in zip(dates, values):
        out.setdefault(_ts(d), set()).update(v)
    return out


def _injections(feed: pd.DataFrame, variant: str) -> C.Inject:
    named = feed["outfall"].map(lambda o: isinstance(o, str)).to_numpy(dtype=bool)
    sids = feed["stations"].map(lambda s: [x for x in str(s).split(";") if x])
    if variant == "link_swap":
        return C.Inject(link=_by_day(feed["date"][named], [[o] for o in feed["outfall"][named]]))
    if variant == "zone_swap":
        return C.Inject(zone=_by_day(feed["date"], sids))
    if variant == "link_zone_swap":                     # a structure name overrides the station (§2.5)
        return C.Inject(link=_by_day(feed["date"][named], [[o] for o in feed["outfall"][named]]),
                        zone=_by_day(feed["date"][~named], sids[~named]))
    raise KeyError(variant)


def group_samples(samples: pd.DataFrame) -> dict:
    """{(geo_v1 legacy group, datetime.date): over standard} — live_v2's sample record: any result of a group's
    stations over standard that day (equal to the served scorecard's groups[*].elevated on its span)."""
    if not len(samples):
        return {}
    g1 = _geo1()
    group_of = {sid: lk.legacy_group for lk in g1.links for sid in lk.stations}
    unknown = sorted(set(samples["station"]) - set(STATIONS))
    if unknown:
        raise KeyError(f"sample stations not in shared/stations.py: {unknown[:5]}")
    grp = samples["station"].map(lambda k: group_of[STATIONS[k].sfpuc_id])
    el = samples.assign(_g=grp).groupby(["_g", "date"])["exceeds"].max()
    return {(g, _date(d)): bool(e) for (g, d), e in el.items()}


def _live_v2(geo, specs: dict, inputs: C.BasinInputs, feed: pd.DataFrame, days: pd.DatetimeIndex, smp: dict) -> Corrected:
    """basin_swap = live_v2, issue day by issue day, exactly as replay_live.replay at cutoff='end' with the
    same inputs, but composed by compose_v2's GEO_V1 adapter: stage-1 rules (live_rules.adjust_stage1) on the
    9-day window, S3 → S4 → OUT, then the group rules (adjust_groups) and the zone max. live_v2 has no S4 of
    its own: q is S4 on its adjusted basin inputs (the group rules move OUT only)."""
    if geo.version != "geo_v1" or specs["s4"]["unit"] != "link":
        raise ValueError("basin_swap is live_v2, which is defined on geo_v1's basins and legacy groups (the GEO_V1 "
                         f"adapter); {geo.version} has no incumbent yet (P8d)")
    if inputs.rain is None:
        raise ValueError("basin_swap needs the day's rain: live_v2 anchors the day before an onset on it")
    rules = LR.RULES
    run = pd.DatetimeIndex(inputs.p.index)
    rain = pd.Series(inputs.rain, dtype=float)
    rain.index = pd.DatetimeIndex(rain.index).normalize()
    rain = rain.reindex(run)
    keys = list(geo.keys)
    P, V = inputs.p[keys].to_numpy(dtype=float), inputs.v_hat[keys].to_numpy(dtype=float)
    dates = [d.date() for d in run]
    pos = {d: i for i, d in enumerate(run)}
    era = feed.attrs["era"]
    onsets, flags = _v1_onsets(feed), feed.attrs["flags"]
    units = list(C.s4_units(geo, specs["s4"]))
    by_id = {lk.id: lk for lk in geo.links}
    group = {u: by_id[u].legacy_group for u in units}
    zone_of_group = {group[u]: by_id[u].zone for u in units}
    basin_of_group = {group[u]: by_id[u].basin for u in units}
    xl = C.x_curves(specs["s4"], units, np.full((1, len(units)), rules["cso"]["large_volume_mg"]))
    ucol = {group[u]: i for i, u in enumerate(units)}
    large = lambda g, k: float(xl[k, 0, ucol[g]])  # noqa: E731
    zone_groups = {zk: [group[lk.id] for lk in geo.links_into(zk)] for zk in ZONES}
    out_p, out_q, out_r = [], [], []
    for D in days:
        i = pos[D]
        lo = i - HISTORY_DAYS
        if lo < 0:
            raise ValueError(f"{D.date()} needs the {HISTORY_DAYS} days before it in the inputs")
        win, wdx = dates[lo:i + 1], run[lo:i + 1]
        r_win = rain.iloc[lo:i + 1]
        if r_win.isna().any():
            raise ValueError(f"no rain on {r_win[r_win.isna()].index[0].date()}: live_v2's anchor would read it as dry")
        today = win[-1]
        on_k = {d: s for d, s in onsets.items() if d <= today}          # cutoff 'end': the day's own observations count
        fl_k = {d: s for d, s in flags.items() if d <= today}
        probs = [dict(zip(keys, map(float, row))) for row in P[lo:i + 1]]
        vols = [dict(zip(keys, map(float, row))) for row in V[lo:i + 1]]
        p2, v2, _ = LR.adjust_stage1(probs, vols, win, on_k, fl_k, dict(zip(win, map(float, r_win))), today, rules=rules,
                                     watcher_from=era[0] if era else None, watcher_ok=True)
        P2 = pd.DataFrame(p2, index=wdx)[keys]
        V2 = pd.DataFrame(v2, index=wdx)[keys]
        comp = C.compose(geo, specs, C.BasinInputs(P2, V2))
        groups = {group[u]: float(comp.out.unit.iat[-1, k]) for k, u in enumerate(units)}
        P0 = P2.copy()
        P0.iloc[-1] = 0.0                                                # persistence: the day's own term removed
        persist_u = C.compose(geo, specs, C.BasinInputs(P0, V2)).out.unit
        persist = {group[u]: float(persist_u.iat[-1, k]) for k, u in enumerate(units)}
        near = {k: v for k, v in smp.items() if today - dt.timedelta(days=HORIZON) <= k[1] <= today}
        adjusted, _ = LR.adjust_groups(groups, persist, p2[-1], today, near, dict(zip(win, p2)), on_k, fl_k,
                                       zone_of_group, basin_of_group, large, rules=rules)
        out_r.append({zk: max(adjusted[g] for g in gs) for zk, gs in zone_groups.items()})
        out_p.append(comp.s3.zone_p.iloc[-1])
        out_q.append(comp.s4.zone.iloc[-1])
    zs = list(ZONES)
    mk = lambda rows: pd.DataFrame(list(rows), index=days, columns=zs) if len(days) else pd.DataFrame(columns=zs, index=days, dtype=float)  # noqa: E731
    return Corrected("basin_swap", mk(out_p), mk(out_q), mk(out_r))


def sample_rates(geo, fit_days, sources=SMP.DEFAULT_SOURCES, min_pairs: int = RATE_MIN_PAIRS) -> dict:
    """{zone: {"tail" | "dry": {"elevated": rate | None, "clean": rate | None}, "n": {...: {EE, EC, CE, CC}}}}:
    P(over standard at the next sample | this sample's result) over consecutive sampled days at most 3 days
    apart (live_rules' horizon), both on ``fit_days`` (training days only), from truth.zone_elevated. "tail":
    a zone overflow on record in d−7…d (truth.zone_overflow); a pair whose first day's history the ledger
    does not know is skipped. A cell with fewer than ``min_pairs`` pairs has no rate (None): sample_swap
    leaves q as the model's there. live_rules.fit_sample_rates' arithmetic, on the ledger's truth."""
    geo = T._geo(geo)
    fit = pd.DatetimeIndex(fit_days)
    el = T.zone_elevated(geo, sources)
    zo = T.zone_overflow(geo)
    out = {}
    for z in ZONES:
        e = el[el["zone"] == z].set_index("date")["y"]
        g = zo[zo["zone"] == z].set_index("date")
        fired = g["y"].eq(1).fillna(False).astype(int)
        tail = fired.rolling(TAIL_DAYS + 1, min_periods=1).max().astype(bool)
        n = {"tail": {"EE": 0, "EC": 0, "CE": 0, "CC": 0}, "dry": {"EE": 0, "EC": 0, "CE": 0, "CC": 0}}
        dd = list(e.index)
        for a, b in zip(dd, dd[1:]):
            if (b - a).days > HORIZON or a not in fit or b not in fit or not bool(g["hist_known"].get(a, False)):
                continue
            n["tail" if tail[a] else "dry"][("E" if e[a] else "C") + ("E" if e[b] else "C")] += 1
        rate = lambda hit, miss: hit / (hit + miss) if hit + miss >= min_pairs else None  # noqa: E731
        out[z] = {k: {"elevated": rate(c["EE"], c["EC"]), "clean": rate(c["CE"], c["CC"])} for k, c in n.items()}
        out[z]["n"] = n
    return out


def _sample_swap(geo, specs, inputs, feed, plain: C.Composition, zsmp: pd.DataFrame, rates: dict, days) -> Corrected:
    """S4 only: the newest result known by D (sampled on d, d + 1 ≤ D ≤ d + 3) sets q_z(D) to its
    transition rate; OUT is plain (A1: the public number reads no lab result).

    The rate's regime is live_v2's in_tail as read on issue day D: "tail" if the feed holds an observation
    in the zone or a basin feeding it on d−7…d (the whole feed), or the model's own S3 p_z reached 0.5 on a
    day of d−7…d inside D's 9-day window (D−8…D: live_v2's probs_by_date), else "dry". Reading the model
    only inside the issue day's window makes the row the same whatever day the run starts on."""
    run = pd.DatetimeIndex(inputs.p.index)
    obs = observed(geo, feed, run)                           # the whole feed: d−7 may precede the run
    hot = plain.s3.zone_p >= TAIL_MIN_P
    newest: dict = {}
    for z, d, e in zip(zsmp["zone"], zsmp["date"], zsmp["any_exceedance"]):
        d = _ts(d)
        if d not in obs.index:                               # a result outside the run moves nothing
            continue
        for k in range(LAG, HORIZON + 1):
            D = d + pd.Timedelta(days=k)
            if D in obs.index and ((z, D) not in newest or newest[(z, D)][0] < d):
                newest[(z, D)] = (d, bool(e))                # the newest result known by D decides, rate or not
    inj: dict = {}
    for (z, D), (d, e) in newest.items():
        lo = max(d - pd.Timedelta(days=TAIL_DAYS), D - pd.Timedelta(days=HISTORY_DAYS))
        regime = "tail" if (obs.at[d, z] or bool(hot[z].loc[lo:d].any())) else "dry"
        rate = rates[z][regime]["elevated" if e else "clean"]
        if rate is not None:
            inj.setdefault(D, {})[z] = rate
    comp = C.compose(geo, specs, inputs, C.Inject(sample=inj))
    return _frames("sample_swap", comp, days)


def correct(geo, specs: dict, inputs: C.BasinInputs, feed: pd.DataFrame, variant: str, days=None,
            samples: pd.DataFrame | None = None, rates: dict | None = None) -> Corrected:
    """One variant's corrected frames on ``days`` (default: every inputs day with the 8 before it, inside the
    feed's era when it has one). ``specs`` = {"s3", "s4"} carrying the co-firing shares sibling injections
    read; ``samples`` = the lab record the corrections read (samples.load_samples' shape; default the served
    sources; an empty frame reads none); ``rates`` = ``sample_rates`` (sample_swap only). An unknown variant raises."""
    geo = T._geo(geo)
    if variant not in VARIANTS:
        raise KeyError(f"unknown S5 variant {variant!r}; known: {VARIANTS}")
    _check_feed_frame(feed, geo)
    run = pd.DatetimeIndex(inputs.p.index)
    if days is None:
        days = run[HISTORY_DAYS:]
        era = feed.attrs["era"]
        if era is not None:
            days = days[(days >= _ts(era[0])) & (days <= _ts(era[1]))]
    days = pd.DatetimeIndex(days)
    if not days.isin(run[HISTORY_DAYS:]).all():
        raise ValueError(f"every day asked for needs the {HISTORY_DAYS} days before it in the inputs")
    smp = T._samples(tuple(SMP.DEFAULT_SOURCES)) if samples is None else samples
    if variant == "plain":
        return _frames(variant, C.compose(geo, specs, inputs), days)
    # live_v2 and sample_swap's tail test read observations before the run (live_v2's in_tail looks 7 days back
    # from a sample up to 3 days old, so to D−10): they get the whole feed, so a run that starts mid-era (a T2
    # season with its 8-day lead-in) gives the full run's values on its days. Injections act on the run's days only.
    if variant == "basin_swap":
        return _live_v2(geo, specs, inputs, feed, days, group_samples(smp))
    if variant == "sample_swap":
        if rates is None:
            raise ValueError("sample_swap needs its transition rates (stages_s5.sample_rates on training days)")
        zsmp = SMP.zone_sample_days(smp) if len(smp) else pd.DataFrame(columns=["zone", "date", "any_exceedance"])
        return _sample_swap(geo, specs, inputs, feed, C.compose(geo, specs, inputs), zsmp, rates, days)
    return _frames(variant, C.compose(geo, specs, inputs, _injections(_in_run(feed, run), variant)), days)


# ── rows ───────────────────────────────────────────────────────────────────

def _season(d: pd.Timestamp) -> int:
    return d.year if d.month >= 7 else d.year - 1


def _window(tier: str, trained_through, run: pd.DatetimeIndex, held_out_season: int | None = None) -> tuple:
    """(scored days, fit days, held-out season or None) for a tier (protocol §2). Raises on T3, an unknown
    tier, weights that saw the window, a T2 run without the season its weights were held out of, and a T2
    run whose scored days fall outside that season. T1 stops at the freeze date: a later day is T0's (a day
    is scored in exactly one tier)."""
    if tier == X.IN_SAMPLE:
        raise ValueError("X-ALL-INSAMPLE: T3 rows (the scored weights saw the day) are never emitted")
    if tier not in X.WINDOWS:
        raise ValueError(f"unknown tier {tier!r}; known: {X.WINDOWS}")
    days = run[HISTORY_DAYS:]
    if tier == "T2":
        if trained_through is not None:
            raise ValueError("T2 refits every component per held-out season; trained_through does not apply")
        if held_out_season is None:
            raise ValueError("say which season the inputs' and specs' weights were held out of: a T2 row needs "
                             "held_out_season (the July year), as a T1 row needs trained_through")
        s = int(held_out_season)
        if s not in range(_season(T2_SPAN[0]), _season(T2_SPAN[1]) + 1):
            raise ValueError(f"season {s} is not a T2 season ({_season(T2_SPAN[0])} … {_season(T2_SPAN[1])})")
        days = days[(days >= T2_SPAN[0]) & (days <= T2_SPAN[1])]
        seasons = sorted({_season(d) for d in days})
        if seasons and seasons != [s]:
            raise ValueError(f"a T2 run scores its held-out season {s} only; these inputs' scored days span {seasons}")
        fit = pd.date_range(*T2_SPAN)                          # the other T2 seasons, nothing else
        fit = fit[[_season(d) != s for d in fit]]
        return days, fit, s
    if held_out_season is not None:
        raise ValueError(f"held_out_season is for T2 rows; {tier} says what the inputs were fit through")
    lo = {"T1": X.POST_START, "T1-holdout": X.HOLDOUT_START, "T0": X.freeze_date() + pd.Timedelta(days=1)}[tier]
    hi = {"T1": X.freeze_date(), "T1-holdout": X.POST_START - pd.Timedelta(days=1), "T0": pd.Timestamp.max}[tier]
    if trained_through is None:
        raise ValueError(f"say what the inputs were fit through: a {tier} row needs weights fit before {lo.date()}")
    if _ts(trained_through) >= lo:
        raise ValueError(f"the inputs were fit through {_ts(trained_through).date()}, inside {tier} (from {lo.date()}): "
                         "those rows would be T3")
    return days[(days >= lo) & (days <= hi)], pd.date_range(T.TRUTH_START, lo - pd.Timedelta(days=1)), None


def _fold_events(fold_events, fit: pd.DatetimeIndex, season) -> pd.DataFrame:
    """The ledger events the S5 parameters may count: the given fold's, which must all lie on the fit days
    (before the window; T2: the other T2 seasons), or the ledger's on the fit days."""
    if fold_events is None:
        ev = csd_labels.load_events()
        d = pd.to_datetime(ev["event_date"]).dt.normalize()
        return ev[d.isin(fit)]
    if not {"event_date", "outfall_id"} <= set(fold_events.columns):
        raise ValueError("fold_events are ledger rows (csd_labels.load_events): event_date, outfall_id")
    d = pd.to_datetime(fold_events["event_date"]).dt.normalize()
    if d.isna().any():
        raise ValueError(f"{int(d.isna().sum())} fold events have no event_date")
    if season is not None:
        leak = d[[_season(x) == season for x in d]]
        if len(leak):
            raise ValueError(f"fold_events hold {len(leak)} events in the held-out season {season} (first "
                             f"{leak.min().date()}): the co-firing shares would have seen the scored days")
    off = d[~d.isin(fit)]
    if len(off):
        raise ValueError(f"fold_events hold {len(off)} events off the fit days {fit.min().date()} → {fit.max().date()} "
                         f"(first {off.min().date()}): the S5 parameters are fit before the window"
                         + (", on the other T2 seasons only" if season is not None else ""))
    return fold_events


def _empty_rows() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object if c in ("stage", "unit_type", "unit", "entry", "tier", "sel", "stratum",
                                                             "excl", "tags", "variant") else float)
                         for c in X.COLUMNS}).assign(date=pd.Series(dtype="datetime64[ns]"))[list(X.COLUMNS)]


def s5_rows(geo, specs: dict, inputs: C.BasinInputs, feed_name: str, feed: pd.DataFrame, variants=VARIANTS,
            tier: str = "T1", fold_events: pd.DataFrame | None = None, *, trained_through=None,
            held_out_season: int | None = None, samples: pd.DataFrame | None = None, ctx: X.Context | None = None,
            scope: str = "conditional") -> pd.DataFrame:
    """S5 rows (exclusions.COLUMNS, 'variant' included) for one feed: one row per variant × zone-day.

    The days: ``scope='conditional'`` (the primary) is the observation-conditional set — zone-days D…D+7
    after an observation in the zone or a basin feeding it (``observed``), the observation's own zone-day
    included and marked X-S5-SELF by exclusions.apply; ``'all_days'`` is every scored day (S5's all_days
    table, X-S5-QUIET not applied). Either way only scored days of ``tier`` (``_window``) inside the feed's
    era, so the set is the feed's and the same for every variant.

    Per row: p = the variant's corrected OUT r_z, b = the plain r_z on the same inputs (the paired ΔBrier is
    p against b), q = the corrected S4 q_z (the plain variant's rows hold the plain q), y = truth.out_label's
    y, y2 = the S4 sample truth (zone_elevated's y; NaN unsampled), n_sampled = stations sampled, lead NaN
    (S5's entry is the feed), excl / tags / stratum / sel from exclusions.apply with this feed in the context.

    ``fold_events``: the ledger events the co-firing shares are counted on; default the ledger's on the
    fit days (before the window; T2: the other T2 seasons); an event off the fit days raises. The sample
    rates use the same fit days. ``trained_through``: the last day the inputs' and specs' weights saw
    (required for T0, T1 and T1-holdout); ``held_out_season``: the season (July year) they were held out of
    (required for T2). ``feed_name`` must be the frame's own (attrs['feed']): the perfect feed's sibling rule
    is gated on the name (Part B 9). ``ctx``: an exclusions context covering the rows (built if None)."""
    geo = T._geo(geo)
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, not {scope!r}")
    if X._feed_of(str(feed_name)) not in X.S5_FEEDS:
        raise KeyError(f"unknown S5 feed {feed_name!r}; known: {X.S5_FEEDS} (a seed may follow a colon)")
    _check_feed_frame(feed, geo)
    if feed.attrs.get("feed") != feed_name:
        raise ValueError(f"the frame is feed {feed.attrs.get('feed')!r}, scored as {feed_name!r}: X-S5-PERFECT-SIBLING "
                         "reads the name, so a perfect feed under another name would score its sibling rows")
    variants = tuple(variants)
    if not variants or set(variants) - set(VARIANTS):
        raise KeyError(f"variants must be some of {VARIANTS}, got {variants}")
    if not isinstance(inputs, C.BasinInputs):
        raise TypeError("inputs must be compose_v2.BasinInputs (S2's p and v̂, and the day's rain)")
    run = pd.DatetimeIndex(inputs.p.index)
    scored, fit, season = _window(tier, trained_through, run, held_out_season)
    era = feed.attrs["era"]
    if era is None:
        if len(feed):
            raise ValueError(f"feed {feed_name!r} holds {len(feed)} observations but no era: say when it was watched")
        return _empty_rows()                                 # nobody watched: no observation, no row
    scored = scored[(scored >= _ts(era[0])) & (scored <= _ts(era[1]))]
    if not len(scored):
        return _empty_rows()
    seen = observed(geo, _in_run(feed, run), scored)
    if scope == "conditional" and not seen.to_numpy().any():
        return _empty_rows()
    ev = _fold_events(fold_events, fit, season)
    specs = {"s3": C.check_s3_spec({**specs["s3"], "cofire": C.cofire(geo, ev)}, geo), "s4": specs["s4"]}
    rates = sample_rates(geo, fit) if "sample_swap" in variants else None
    plain = correct(geo, specs, inputs, feed, "plain", days=scored, samples=samples)
    frames = {v: plain if v == "plain" else correct(geo, specs, inputs, feed, v, days=scored, samples=samples, rates=rates)
              for v in variants}
    if ctx is None:
        ctx = X.context(geo, start=scored.min(), end=scored.max(), feeds={feed_name: feed})
    else:
        if ctx.geo.version != geo.version:
            raise ValueError(f"the context is {ctx.geo.version}, the run {geo.version}")
        ctx = ctx.with_inputs(feeds={feed_name: feed})
    zf = ctx.frames["zone"]
    el = T.zone_elevated(geo, ctx.sources).set_index(["zone", "date"])["n_stations_sampled"]   # the context's samples
    parts = []
    for z in ZONES:
        days = scored[seen[z].to_numpy()] if scope == "conditional" else scored
        if not len(days):
            continue
        key = pd.MultiIndex.from_arrays([[z] * len(days), days])
        y = zf["out_y"].reindex(key).to_numpy(dtype=float)
        y2 = zf["s4_y"].reindex(key).to_numpy(dtype=float)
        n_s = el.reindex(key).fillna(0).to_numpy(dtype=float)
        for v in variants:
            f = frames[v]
            parts.append(pd.DataFrame({
                "date": days, "stage": "s5", "unit_type": "zone", "unit": z, "entry": feed_name, "lead": np.nan,
                "tier": tier, "sel": "", "p": f.r.loc[days, z].to_numpy(dtype=float), "q": f.q.loc[days, z].to_numpy(dtype=float),
                "b": plain.r.loc[days, z].to_numpy(dtype=float), "v_hat": np.nan, "y": y, "y2": y2, "n_sampled": n_s,
                "stratum": "", "excl": "", "tags": "", "variant": v}))
    if not parts:
        return _empty_rows()
    rows = pd.concat(parts, ignore_index=True)[list(X.COLUMNS)]
    for c in ("p", "q", "b"):
        if rows[c].isna().any():
            raise ValueError(f"S5 rows: {c} is NaN on {int(rows[c].isna().sum())} rows; a correction left a day uncomposed")
    order = {v: i for i, v in enumerate(VARIANTS)}
    zorder = {z: i for i, z in enumerate(ZONES)}
    rows = rows.sort_values(["variant", "unit", "date"], key=lambda c: c.map(order) if c.name == "variant" else
                            (c.map(zorder) if c.name == "unit" else c), kind="stable").reset_index(drop=True)
    rows = X.apply(rows, "s5", ctx, table="score" if scope == "conditional" else "all_days")
    if scope == "conditional" and (rows["excl"] == "X-S5-QUIET").any():
        raise AssertionError("a conditional row is X-S5-QUIET: stages_s5.observed and exclusions disagree on the set")
    return rows


# ── development inputs ─────────────────────────────────────────────────────

def served_name() -> str:
    """The served set's name, from served.json (never typed)."""
    return json.loads((SERVE_DIR / "served.json").read_text())["name"]


def served_inputs(holdout_fit: bool = False) -> tuple[C.BasinInputs, pd.Timestamp]:
    """(inputs, trained_through) from the served set's stored scorecard, for development until stages_build
    passes S2 engine outputs: p = the stored per-day basin probability (``holdout_fit``: the holdout-fit one
    where the scorecard has it, as replay_live grades, else the served model's), v̂ = replay_live.
    predicted_volumes (the served heads on the training frames), rain = the scorecard's day rain (what
    live_v2's anchor reads in the replay). trained_through is the scorecard's: with the served table, split
    and heads fit through it, only T1 days (from 2025-11-01) are clean (protocol §2)."""
    sc, days, _ = RL.load_artifact()
    keys = list(_geo1().keys)
    if [list(d["basins"]) for d in days[:1]] != [keys]:
        raise ValueError(f"the scorecard's basins are not geo_v1's {keys}")
    vol = RL.predicted_volumes(days, sc)
    idx = pd.DatetimeIndex([d["date"] for d in days])
    pick = (lambda b: RL._p_used(b)) if holdout_fit else (lambda b: float(b["p"]))  # noqa: E731
    p = pd.DataFrame([{k: pick(d["basins"][k]) for k in keys} for d in days], index=idx)
    v = pd.DataFrame([{k: float(vol[d["date"]][k]) for k in keys} for d in days], index=idx)
    rain = pd.Series([float(d["rain"]) for d in days], index=idx)
    return C.BasinInputs(p, v, rain), _ts(sc["trained_through"])


if __name__ == "__main__":
    geo = G.get("geo_v1")
    inputs, tt = served_inputs()
    fd = feeds(geo, end=inputs.p.index.max())
    print(f"served set {served_name()} (trained through {tt.date()}); feeds:",
          {k: (len(v), v.attrs["era"]) for k, v in fd.items()})
    specs = C.geo_v1_adapter_specs(cofire_shares=C.cofire(geo, csd_labels.load_events(), end=tt))
    for name in ("oracle", "degraded:1"):
        rows = s5_rows(geo, specs, inputs, name, fd[name], tier="T1", trained_through=tt)
        sc = rows[rows["excl"] == ""]
        print(f"── {name}: {len(rows)} rows, scored per variant {len(sc) // len(VARIANTS)}")
        for v, g in sc.groupby("variant", sort=False):
            print(f"   {v:15} ΔBS (corrected − plain) {((g.p - g.y) ** 2 - (g.b - g.y) ** 2).mean():+.5f} on {len(g)} zone-days")
