"""stages_s5 — S5 live corrections, replayed through the stage composition (P7c,
and P8d's candidates; STAGES_DESIGN.md Part C §3.5, §2.5, §5.5, §5.8, §7 step 6,
as amended by Part B 1, 4, 9 and 16; STAGES_PROTOCOL.md §1 "S5", §3 "S5 oracle",
§4.1, §6, §7's S4 and S5 rules and §8's S5 primary).

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
    sample_rows(...)  S5's sample rows (P8d): every variant's q on the zone-days a
                   lab result reaches (D+1…D+3), graded by S5's feed rules and
                   S4's first-match rules on first-look samples (X-S4-RESAMPLE),
                   the table sample_swap's value shows in
    s5_tables(...) both tables of one feed and fold with the corrections composed
                   once (what stages_build would call to add the sample rows)
    window_apply   the exclusions re-run per window over several folds (X-POWER)
    sample_rates   the next-sample transition rates sample_swap reads, fit on
                   training days only
    feed_recall    the per-basin feed recall the downgrade reads, fit on training
                   days only
    feed_counts    per-feed exclusion counts for the report: each feed (every
                   degraded seed) and window on its own, never summed across seeds
    verdicts       the S5 candidate verdict table: every variant against no
                   correction and against basin_swap, ΔBrier with block CIs
    served_inputs  development inputs (P7c): the served set's stored per-day p and
                   v̂, exactly as replay_live replays them; stages_build passes S2
                   engine outputs instead
    stage_rows     the verdict table's rows on stages_build's own S2 folds (T2,
                   T1-holdout, T1), through stages_build's loaders (the CLI)

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
                    result (A1), so sample_swap's p equals its b: its effect on the
                    OUT label is 0 by construction, and its ΔBrier on the conditional
                    rows says nothing. Its value is scored on S5's sample rows
                    (``sample_rows``): q against first-look samples on D+1…D+3.
    downgrade       (P8d) link_zone_swap with the no-flag Bayes downgrade, before the
                    split: at the end of D, a basin's S2 p on a watched day d of
                    D−7…D with no observation of the basin on d…D becomes
                    live_rules.bayes_downgrade(p, R_b[D − d]), where R_b[k] = P(the
                    feed shows the basin on d…d+k | a ledger onset on d) is the
                    feed's recall per basin (``feed_recall``), fit on the training
                    days only. South uses the pooled Bay-side recall (§3.5), as does
                    any Bay-side basin under 10 training onsets (protocol §6's power
                    floor); a basin whose feed holds too few training onsets, its own
                    or pooled (a degraded feed's holdout: it starts 2023-07-01),
                    borrows the real feed's, the 2016-17 archive, whose measured
                    imperfections the degraded feed copies; a basin with no measured
                    recall (the archive's Westside, whose truth there is the feed
                    itself) is not downgraded. Unlike
                    live_v2's downgrade: the recall is fit, not set (0.60 / 0.87 /
                    0.95), every day the model called is updated (no min_p), and the
                    observations go in after the split. bayes_downgrade treats a
                    silent day as certain when nothing started (a show of a
                    neighbouring day's onset is not modelled), as live_v2 does.
                    On the perfect feed it is circular against OUT's label: that
                    feed is the ledger, its recall is 1, so a silent day goes to 0
                    exactly when the ledger filed nothing (41 of its 44 scored T1
                    zone-days carry the ledger's own day-of answer, as of
                    2026-08-17), the reason Part B 9 leaves sibling zone-days off
                    that feed. Its rows stay (the set is the feed's, the same for
                    every variant), but the verdict table marks those comparisons
                    circular and claims nothing from them (``circular_on_perfect``).
                    live_v2's own no-flag downgrade reads the same silence, more
                    weakly (recall 0.60 / 0.87 / 0.95, days at p ≥ 0.15): on the
                    perfect feed it moved 3 of the 44 scored T1 zone-days and 6 of the
                    124 holdout ones (−0.001 and −0.004 of Brier, served inputs, as of
                    2026-08-17), so basin_swap and all_floors are marked circular there
                    too against a reference without it. Against first-look samples it
                    is S4 on the true history, not circular.
    all_floors      (P8d) live_v2 with every empirical sample floor applied, those
                    under live_rules.FLOOR_MIN = 0.5 included: replay_live.variants()
                    ['all_floors'], what live_v2 did before 2026-09-27. §5.8's
                    FLOOR_MIN re-test is all_floors − basin_swap by ΔBrier. It maps
                    onto compose_v2 as basin_swap does (the GEO_V1 adapter, issue day
                    by issue day), and tests pin it to replay_live.per_day_risks
                    ('all_floors') day by day; like basin_swap it is geo_v1's only.
                    Both arms use live_rules' rates (fit 2026-09-26 on the whole
                    record), so the difference isolates FLOOR_MIN; neither arm's rates
                    are clean (protocol §2's decision log: live_v2, FLOOR_MIN saw S5).

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

S5's sample rows (``sample_rows``, P8d): the zone-days D of the same scored days
on which a lab result sampled on D−3…D−1 is known (live_rules' lag and horizon),
the same for every variant (the lab record defines them, as the feed defines the
conditional set). Each row is an S4 row (stage 's4', entry 'rain': S5 replays the
rain-known S2 output) whose p = q is the variant's corrected q_z(D), b the plain
q_z(D), y the S4 sample truth and y2 the newest result known by D, put through
S5's feed rules (X-S5-HEALTH, X-S5-CIRC, X-S5-PERFECT-SIBLING: a sibling zone-day
is never scored on the perfect feed, Part B 9, protocol §3) and then exclusions'
S4 order (X-S4-UNSAMPLED → X-S4-HISTUNK → X-LEDGER-SUSPECT → X-S4-RESAMPLE): the
score is on first-look samples, so a day after an exceedance on D−1 or D−2 is
left out. 'variant' and 'feed' name the correction and the CSO feed its tail test
read; they are not rows.csv.gz rows (exclusions forbids a variant on an S4 row,
so ``window_apply`` re-runs the rules variant-free).

The verdict table (``verdicts``, design §3.5's benchmarks, protocol §6/§8): per
feed and window (each tier, and 'S5' = T1-holdout ∪ T1, protocol §8's window,
only when the rows hold both), every variant's paired ΔBrier against no
correction (plain) and against basin_swap (live_v2): on the conditional set
against OUT's label with observation-event blocks (stages_build.s5_blocks), and
on the sample rows against first-look samples with storm blocks (truth.blocks);
90% CIs, B = 2,000, seed 0; the verdict words of verify. The degraded feeds are
summarised over the seeds (a verdict only when every seed gives it), never
pooled. X-POWER is protocol §6's (fewer than 10 positives or 8 rain storm blocks,
in both tables, whatever the bootstrap resamples); an X-POWER cell is shown with
its CI and claims nothing, nor does a comparison circular on the perfect feed.
The table is descriptive: protocol §8's S5 primary (link_zone_swap against
basin_swap on both feeds) is stages_build's. ``stage_rows`` builds the rows on
stages_build's folds and feeds (the CLI), as stages_build.s5_build does: the
perfect feed and the archive have T2, the degraded feeds only S5's window (§8).

No module-level IO; nothing is written. The served set's name is read from
served.json, never typed.

    venv/bin/python features/forecast/src/models/stages_s5.py              # development summary (served inputs, T1)
    venv/bin/python features/forecast/src/models/stages_s5.py --verdicts [--n-boot N] [--seasons 2019,2020] [--json]
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
import verify as V  # noqa: E402  (the verdict table's paired Brier differences)
from shared import geography as G  # noqa: E402
from shared.outfalls import OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONE_OF_STATION, ZONES  # noqa: E402

SERVE_DIR = FORECAST / "data" / "models"
VARIANTS = ("plain", "basin_swap", "link_swap", "zone_swap", "link_zone_swap", "sample_swap", "downgrade", "all_floors")
PRIMARY = ("link_zone_swap", "basin_swap")        # protocol §8: candidate vs incumbent (= live_v2)
LIVE_V2 = ("basin_swap", "all_floors")            # live_v2's rule sets, replayed through the GEO_V1 adapter (geo_v1 only)
NO_CORRECTION, INCUMBENT = "plain", "basin_swap"  # the verdict table's two references (design §3.5's benchmarks)
BUILT_ON = {"downgrade": "link_zone_swap"}        # a variant that adds one rule to another: its increment is scored too
SILENCE = {"basin_swap": "live_v2", "all_floors": "live_v2", "downgrade": "feed_recall"}   # the no-flag downgrade each applies
CIRCULAR_ON_PERFECT = tuple(SILENCE)              # Part B 9's reason, on OUT's label: the perfect feed is the ledger (circular_on_perfect)
SEED, LEVEL = 0, 0.9                              # protocol §6: seed 0 (verify's default), 90% percentile CIs
RECALL_DAYS = X.OBS_DAYS                          # the downgrade's R[k], k = D − d = 0…7 (the days OUT reads)
POOLED_BASINS = ("south",)                        # design §3.5: South uses the pooled Bay-side recall
BAY_SIDE = "Bayside"                              # Basin.facility of the Bay-side basins (the Southeast plant's permit)
SAMPLE_STAGE, SAMPLE_ENTRY = "s4", "rain"         # S5's sample rows: S4's truth and rules, on the rain-known S2 output
S5_WINDOW = ("T1-holdout", "T1")                  # protocol §8: S5's window, 2023-07-01 → the data end (disjoint tiers)
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


@functools.lru_cache(maxsize=4)
def _archive(version: str) -> pd.DataFrame:
    geo = G.get(version)
    ao = T.archive_onsets(geo)
    a_on, a_fl, a_era = RL.archive_flags()
    obs = [{"date": d, "outfall": o} for d, ids in zip(ao["date"], ao["outfall_ids"]) for o in str(ids).split("|")]
    f = observations(geo, obs, era=a_era, flags=a_fl, name="archive")
    if _v1_onsets(f) != a_on:
        raise AssertionError("truth.archive_onsets and replay_live.archive_flags disagree on the archive's basin onsets "
                             "(archive_flags drops multi-basin structures, Part B 22)")
    return f


def archive_feed(geo) -> pd.DataFrame:
    """The 2016-17 Poo Bot feed placed in ``geo`` (see ``feeds``): a fresh copy each call, its attrs included."""
    f = _archive(T._geo(geo).version)
    out = f.copy()
    out.attrs = dict(f.attrs)
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
    out["archive"] = archive_feed(geo)
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


def live_rules(variant: str) -> dict:
    """The live_v2 rule set a LIVE_V2 variant replays: basin_swap live_rules.RULES itself, all_floors
    replay_live.variants()['all_floors'] (every empirical floor, FLOOR_MIN off). KeyError otherwise."""
    if variant == "basin_swap":
        return LR.RULES
    if variant == "all_floors":
        return RL.variants()["all_floors"]
    raise KeyError(f"{variant!r} is not a live_v2 rule set; those are {LIVE_V2}")


def _live_v2(geo, specs: dict, inputs: C.BasinInputs, feed: pd.DataFrame, days: pd.DatetimeIndex, smp: dict,
             variant: str = "basin_swap") -> Corrected:
    """basin_swap = live_v2 (all_floors: its rule set with every sample floor), issue day by issue day,
    exactly as replay_live.replay at cutoff='end' with the same inputs, but composed by compose_v2's GEO_V1
    adapter: stage-1 rules (live_rules.adjust_stage1) on the 9-day window, S3 → S4 → OUT, then the group
    rules (adjust_groups) and the zone max. live_v2 has no S4 of its own: q is S4 on its adjusted basin
    inputs (the group rules move OUT only)."""
    if geo.version != "geo_v1" or specs["s4"]["unit"] != "link":
        raise ValueError(f"{variant} is live_v2, which is defined on geo_v1's basins and legacy groups (the GEO_V1 "
                         f"adapter); {geo.version} has no live_v2 incumbent: its sets are compared with the served "
                         "set's on the zones (protocol §8)")
    if inputs.rain is None:
        raise ValueError(f"{variant} needs the day's rain: live_v2 anchors the day before an onset on it")
    rules = live_rules(variant)
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
    return Corrected(variant, mk(out_p), mk(out_q), mk(out_r))


def sample_rates(geo, fit_days, sources=SMP.DEFAULT_SOURCES, min_pairs: int = RATE_MIN_PAIRS) -> dict:
    """{zone: {"tail" | "dry": {"elevated": rate | None, "clean": rate | None}, "n": {...: {EE, EC, CE, CC}}}}:
    P(over standard at the next sample | this sample's result) over consecutive sampled days at most 3 days
    apart (live_rules' horizon), both on ``fit_days`` (training days only), from truth.zone_elevated. "tail":
    a zone overflow on record in d−7…d (truth.zone_overflow); a pair whose first day's history the ledger
    does not know is skipped, and so is one whose tail test d−7…d leaves the fit days (a T2 fold's first week
    after its held-out season would read that season's ledger). A cell with fewer than ``min_pairs`` pairs has
    no rate (None): sample_swap leaves q as the model's there. live_rules.fit_sample_rates' arithmetic, on the
    ledger's truth."""
    geo = T._geo(geo)
    fit = pd.DatetimeIndex(fit_days)
    fit_set = set(fit.normalize())
    look = [pd.Timedelta(days=k) for k in range(TAIL_DAYS + 1)]
    el = T.zone_elevated(geo, sources)
    zo = T.zone_overflow(geo)
    out = {}
    for z in ZONES:
        e = el[el["zone"] == z].set_index("date")["y"]
        g = zo[zo["zone"] == z].set_index("date")
        fired = g["y"].eq(1).fillna(False).astype(int)   # NaN (unknown) only on days hist_known skips below
        tail = fired.rolling(TAIL_DAYS + 1, min_periods=1).max().astype(bool)
        n = {"tail": {"EE": 0, "EC": 0, "CE": 0, "CC": 0}, "dry": {"EE": 0, "EC": 0, "CE": 0, "CC": 0}}
        dd = list(e.index)
        for a, b in zip(dd, dd[1:]):
            if (b - a).days > HORIZON or b not in fit_set or any(a - j not in fit_set for j in look) \
                    or not bool(g["hist_known"].get(a, False)):
                continue
            n["tail" if tail[a] else "dry"][("E" if e[a] else "C") + ("E" if e[b] else "C")] += 1
        rate = lambda hit, miss: hit / (hit + miss) if hit + miss >= min_pairs else None  # noqa: E731
        out[z] = {k: {"elevated": rate(c["EE"], c["EC"]), "clean": rate(c["CE"], c["CC"])} for k, c in n.items()}
        out[z]["n"] = n
    return out


def _newest_results(zsmp: pd.DataFrame, index) -> dict:
    """{(zone, D): (d, over standard)}: the newest lab result known by D — sampled on d with d + 1 ≤ D ≤ d + 3
    (live_rules' known lag and horizon) — for D and d on ``index`` (a result outside the run moves nothing).
    The newest decides, whether or not its cell has a rate; sample_swap and S5's sample rows read the same."""
    on = set(pd.DatetimeIndex(index))
    newest: dict = {}
    for z, d, e in zip(zsmp["zone"], zsmp["date"], zsmp["any_exceedance"]):
        d = _ts(d)
        if d not in on:
            continue
        for k in range(LAG, HORIZON + 1):
            D = d + pd.Timedelta(days=k)
            if D in on and ((z, D) not in newest or newest[(z, D)][0] < d):
                newest[(z, D)] = (d, bool(e))
    return newest


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
    newest = _newest_results(zsmp, run)
    inj: dict = {}
    for (z, D), (d, e) in newest.items():
        lo = max(d - pd.Timedelta(days=TAIL_DAYS), D - pd.Timedelta(days=HISTORY_DAYS))
        regime = "tail" if (obs.at[d, z] or bool(hot[z].loc[lo:d].any())) else "dry"
        rate = rates[z][regime]["elevated" if e else "clean"]
        if rate is not None:
            inj.setdefault(D, {})[z] = rate
    comp = C.compose(geo, specs, inputs, C.Inject(sample=inj))
    return _frames("sample_swap", comp, days)


# ── the no-flag downgrade (P8d) ────────────────────────────────────────────

def _ledger_onsets(geo) -> pd.DataFrame:
    """The ledger's basin onset days (truth.basin_onsets: y = 1 on a known day): basin, date."""
    bo = T.basin_onsets(geo)
    on = bo["y"].eq(1).fillna(False).astype(bool).to_numpy() & bo["known"].astype(bool).to_numpy()
    return bo.loc[on, ["basin", "date"]].reset_index(drop=True)


def _recall_counts(geo, feed: pd.DataFrame, fit_days) -> dict:
    """{basin: (n, hits)}: the ledger onset basin-days d whose whole look d…d+7 lies on ``fit_days`` inside the
    feed's era, and, for k = 0…7, how many of them the feed showed (an observation placed in the basin) on some
    day of d…d+k. One set of onsets for every k, so R_k = hits_k / n never falls as k grows."""
    out = {b: [0, np.zeros(RECALL_DAYS + 1, dtype=int)] for b in geo.keys}
    era = feed.attrs["era"]
    if era is None:
        return {b: (n, h) for b, (n, h) in out.items()}
    fit = set(pd.DatetimeIndex(fit_days).normalize())
    lo, hi = _ts(era[0]), _ts(era[1])
    shows = {b: set(pd.DatetimeIndex(feed.loc[feed["basin"] == b, "date"])) for b in geo.keys}
    look = [pd.Timedelta(days=k) for k in range(RECALL_DAYS + 1)]
    on = _ledger_onsets(geo)
    for b, d in zip(on["basin"], pd.DatetimeIndex(on["date"])):
        if d < lo or d + look[-1] > hi or any(d + j not in fit for j in look):
            continue
        out[b][0] += 1
        first = next((k for k, j in enumerate(look) if d + j in shows[b]), None)
        if first is not None:
            out[b][1][first:] += 1
    return {b: (n, h) for b, (n, h) in out.items()}


def feed_recall(geo, feed: pd.DataFrame, fit_days, fallback: pd.DataFrame | None = None,
                min_onsets: int = RATE_MIN_PAIRS) -> dict:
    """{basin: {"r": (R_0 … R_7) | None, "n": onsets counted, "source": where R comes from}}: the feed's
    recall per basin, R_k = P(the feed shows the basin on d…d+k | a ledger onset on d), on the training days
    ``fit_days`` only (``_recall_counts``) — live_rules.bayes_downgrade's recall, fit rather than set.

    Per basin, the first that holds ``min_onsets`` onsets (protocol §6's power floor, 10): the feed's own on
    the basin (never for POOLED_BASINS: design §3.5, South uses the pooled Bay-side recall); the feed's own
    pooled over the Bay-side basins (a Bay-side basin only); then the same two on ``fallback`` (the real feed,
    the 2016-17 archive, for a feed whose era holds no training onsets); else None, and the basin is not
    downgraded (the archive's Westside: its truth there is the feed itself). Both feeds must be placed in
    ``geo``."""
    geo = T._geo(geo)
    _check_feed_frame(feed, geo)
    named = [(feed.attrs.get("feed") or "the feed", feed)]
    if fallback is not None:
        _check_feed_frame(fallback, geo)
        if fallback.attrs.get("feed") != feed.attrs.get("feed"):
            named.append((fallback.attrs.get("feed") or "the fallback feed", fallback))
    counted = [(name, _recall_counts(geo, f, fit_days)) for name, f in named]
    bay = [b.key for b in geo.basins if b.facility == BAY_SIDE]
    stray = [b for b in POOLED_BASINS if b in geo.keys and b not in bay]
    if not bay or stray:                 # a pooled basin with no Bay side to pool would be left undowngraded, silently
        raise ValueError(f"{geo.version}: Bay-side basins (facility {BAY_SIDE!r}) {bay}; pooled basins outside them {stray}")
    out = {}
    for b in geo.keys:
        pick = None
        for name, c in counted:
            if b not in POOLED_BASINS and c[b][0] >= min_onsets:
                pick = (c[b][0], c[b][1], f"{name}, {geo.basin(b).name}")
            elif b in bay and sum(c[k][0] for k in bay) >= min_onsets:
                pick = (sum(c[k][0] for k in bay), sum(c[k][1] for k in bay), f"{name}, pooled Bay side")
            if pick:
                break
        if pick is None:
            out[b] = {"r": None, "n": int(max(c[b][0] for _, c in counted)), "source": "unmeasured: not downgraded"}
        else:
            n, h, src = pick
            out[b] = {"r": tuple(float(x) for x in h / n), "n": int(n), "source": src}
    return out


def _downgrade(geo, specs: dict, inputs: C.BasinInputs, feed: pd.DataFrame, days: pd.DatetimeIndex, recall: dict) -> Corrected:
    """downgrade = link_zone_swap + the no-flag Bayes downgrade, issue day by issue day (the downgrade of day d
    depends on how long the feed has been silent since, D − d): at the end of D, every basin's S2 p on a day d of
    D−7…D inside the feed's era (watched, through D) with no observation placed in the basin on d…D becomes
    live_rules.bayes_downgrade(p, R_b[D − d]) (a basin whose recall is None keeps its p); then compose_v2 on
    that 8-day window (OUT and S4 read D−7…D) with the window's link and zone injections, and D's row is kept.
    The window's arithmetic is the full run's (lags are row offsets), so with no recall this is link_zone_swap."""
    keys = list(geo.keys)
    missing = sorted(set(keys) - set(recall))
    if missing:
        raise KeyError(f"the recall has no basin {missing}: feed_recall(geo, ...) gives every basin, None where unmeasured")
    run = pd.DatetimeIndex(inputs.p.index)
    P, V = inputs.p[keys].to_numpy(dtype=float), inputs.v_hat[keys]
    R = [recall[b]["r"] for b in keys]
    era = feed.attrs["era"]
    lo_w, hi_w = (_ts(era[0]), _ts(era[1])) if era is not None else (pd.Timestamp.max, pd.Timestamp.min)
    shown = np.zeros((len(run) + 1, len(keys)), dtype=int)       # cumulative shows per basin over the run
    on_run = feed[feed["date"].isin(run)]
    pos = {d: i for i, d in enumerate(run)}
    col = {b: c for c, b in enumerate(keys)}
    for d, b in zip(on_run["date"], on_run["basin"]):
        if isinstance(b, str):
            shown[pos[_ts(d)] + 1, col[b]] += 1
    shown = shown.cumsum(axis=0)
    inj = _injections(_in_run(feed, run), "link_zone_swap")
    span = len(C.LAGS) - 1                                       # 7: D−7…D
    rain = None
    if inputs.rain is not None:
        rain = pd.Series(inputs.rain, dtype=float)
        rain.index = pd.DatetimeIndex(rain.index).normalize()
    out_p, out_q, out_r = [], [], []
    for D in days:
        i = pos[D]
        lo = i - span
        if lo < 0:
            raise ValueError(f"{D.date()} needs the {span} days before it in the inputs")
        Pw = P[lo:i + 1].copy()
        if lo_w <= D <= hi_w:
            for j in range(lo, i + 1):
                if run[j] < lo_w:
                    continue
                for c in range(len(keys)):
                    if R[c] is not None and shown[i + 1, c] - shown[j, c] == 0:
                        Pw[j - lo, c] = LR.bayes_downgrade(float(Pw[j - lo, c]), R[c][i - j])
        wdx = run[lo:i + 1]
        sub = C.Inject(link={d: s for d, s in inj.link.items() if d in wdx}, zone={d: s for d, s in inj.zone.items() if d in wdx})
        r_w = None if rain is None else rain.loc[wdx[0] - pd.Timedelta(days=2):D]
        comp = C.compose(geo, specs, C.BasinInputs(pd.DataFrame(Pw, wdx, keys), V.iloc[lo:i + 1], r_w), sub)
        out_p.append(comp.s3.zone_p.iloc[-1])
        out_q.append(comp.s4.zone.iloc[-1])
        out_r.append(comp.out.zone.iloc[-1])
    zs = list(ZONES)
    mk = lambda rows: pd.DataFrame(list(rows), index=days, columns=zs) if len(days) else pd.DataFrame(columns=zs, index=days, dtype=float)  # noqa: E731
    return Corrected("downgrade", mk(out_p), mk(out_q), mk(out_r))


def correct(geo, specs: dict, inputs: C.BasinInputs, feed: pd.DataFrame, variant: str, days=None,
            samples: pd.DataFrame | None = None, rates: dict | None = None, recall: dict | None = None) -> Corrected:
    """One variant's corrected frames on ``days`` (default: every inputs day with the 8 before it, inside the
    feed's era when it has one). ``specs`` = {"s3", "s4"} carrying the co-firing shares sibling injections
    read; ``samples`` = the lab record the corrections read (samples.load_samples' shape; default the served
    sources; an empty frame reads none); ``rates`` = ``sample_rates`` (sample_swap only); ``recall`` =
    ``feed_recall`` (downgrade only). An unknown variant raises."""
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
    if variant in LIVE_V2:
        return _live_v2(geo, specs, inputs, feed, days, group_samples(smp), variant)
    if variant == "sample_swap":
        if rates is None:
            raise ValueError("sample_swap needs its transition rates (stages_s5.sample_rates on training days)")
        return _sample_swap(geo, specs, inputs, feed, C.compose(geo, specs, inputs), _zone_samples(smp), rates, days)
    if variant == "downgrade":
        if recall is None:
            raise ValueError("downgrade needs the feed's recall per basin (stages_s5.feed_recall on training days)")
        return _downgrade(geo, specs, inputs, feed, days, recall)
    return _frames(variant, C.compose(geo, specs, inputs, _injections(_in_run(feed, run), variant)), days)


def _zone_samples(smp: pd.DataFrame) -> pd.DataFrame:
    """samples.zone_sample_days of the corrections' lab record (an empty record: no zone-day)."""
    return SMP.zone_sample_days(smp) if len(smp) else pd.DataFrame(columns=["zone", "date", "any_exceedance"])


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


def _empty_rows(extra=()) -> pd.DataFrame:
    cols = list(X.COLUMNS) + list(extra)
    return pd.DataFrame({c: pd.Series(dtype=object if c in ("stage", "unit_type", "unit", "entry", "tier", "sel", "stratum",
                                                             "excl", "tags", "variant", "feed") else float)
                         for c in cols}).assign(date=pd.Series(dtype="datetime64[ns]"))[cols]


def _scored(geo, inputs, feed_name: str, feed: pd.DataFrame, variants, tier: str, trained_through, held_out_season) -> tuple:
    """(variants, run, scored days, fit days, held-out season) after the checks every S5 table shares: the feed
    by name and frame (its name the frame's own), the variants, the inputs, the tier's window (``_window``: no
    T3 row); scored = the window's days inside the feed's era (none when nobody watched)."""
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
        return variants, run, scored[:0], fit, season
    return variants, run, scored[(scored >= _ts(era[0])) & (scored <= _ts(era[1]))], fit, season


def _parameters(geo, specs: dict, feed: pd.DataFrame, variants: tuple, fit, season, fold_events) -> tuple:
    """(specs with the fold's co-firing shares, sample_swap's rates or None, the downgrade's feed recall or
    None): every S5 parameter, fit on the fit days only (before the window; T2: the other T2 seasons)."""
    ev = _fold_events(fold_events, fit, season)
    specs = {"s3": C.check_s3_spec({**specs["s3"], "cofire": C.cofire(geo, ev)}, geo), "s4": specs["s4"]}
    rates = sample_rates(geo, fit) if "sample_swap" in variants else None
    recall = feed_recall(geo, feed, fit, fallback=archive_feed(geo)) if "downgrade" in variants else None
    return specs, rates, recall


def _corrected(geo, specs, inputs, feed, variants, scored, samples, rates, recall) -> tuple:
    """(plain, {variant: Corrected}) on the scored days: plain always (every row's b), each variant once."""
    plain = correct(geo, specs, inputs, feed, "plain", days=scored, samples=samples)
    frames = {v: plain if v == "plain" else correct(geo, specs, inputs, feed, v, days=scored, samples=samples, rates=rates,
                                                    recall=recall)
              for v in variants}
    return plain, frames


def _rows_context(geo, ctx: X.Context | None, scored, feed_name: str, feed: pd.DataFrame) -> X.Context:
    if ctx is None:
        return X.context(geo, start=scored.min(), end=scored.max(), feeds={feed_name: feed})
    if ctx.geo.version != geo.version:
        raise ValueError(f"the context is {ctx.geo.version}, the run {geo.version}")
    return ctx.with_inputs(feeds={feed_name: feed})


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
    rates and the downgrade's feed recall use the same fit days (``_parameters``). ``trained_through``: the
    last day the inputs' and specs' weights saw (required for T0, T1 and T1-holdout); ``held_out_season``: the
    season (July year) they were held out of (required for T2). ``feed_name`` must be the frame's own
    (attrs['feed']): the perfect feed's sibling rule is gated on the name (Part B 9). ``ctx``: an exclusions
    context covering the rows (built if None)."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, not {scope!r}")
    return s5_tables(geo, specs, inputs, feed_name, feed, variants, tier, fold_events, trained_through=trained_through,
                     held_out_season=held_out_season, samples=samples, ctx=ctx, tables=(scope,))[scope]


SAMPLE_COLUMNS = tuple(X.COLUMNS) + ("feed",)
SAMPLE_S5_RULES = ("X-S5-HEALTH", "X-S5-CIRC", "X-S5-PERFECT-SIBLING")   # S5's feed rules, first on its sample rows (_sample_rules)
TABLES = SCOPES + ("samples",)                      # s5_tables: the conditional set, every scored day, S5's sample rows


def sample_rows(geo, specs: dict, inputs: C.BasinInputs, feed_name: str, feed: pd.DataFrame, variants=VARIANTS,
                tier: str = "T1", fold_events: pd.DataFrame | None = None, *, trained_through=None,
                held_out_season: int | None = None, samples: pd.DataFrame | None = None,
                ctx: X.Context | None = None) -> pd.DataFrame:
    """S5's sample rows (SAMPLE_COLUMNS) for one feed: one row per variant × zone-day a lab result reaches.

    The days: the scored days of ``tier`` inside the feed's era (as ``s5_rows``) on which the zone has a
    result known by D, sampled on D−3…D−1 (``_newest_results``: live_rules' lag and horizon) in the lab record
    the corrections read (``samples``; default the served sources). The record defines the set, so it is the
    same for every variant, and it is sample_swap's: every day its q could move.

    Per row (stage 's4', entry 'rain', unit_type 'zone'): p = q = the variant's corrected S4 q_z(D), b = the
    plain q_z(D), y = the S4 sample truth (zone_elevated's y on the context's record; NaN unsampled), y2 = the
    newest result known by D (1 over standard, 0 clean), n_sampled, lead NaN; excl / tags / stratum / sel from
    ``_sample_rules``: S5's feed rules first (a sibling zone-day on the perfect feed is X-S5-PERFECT-SIBLING, Part
    B 9), then exclusions.apply in S4's order (X-S4-UNSAMPLED → X-S4-HISTUNK → X-LEDGER-SUSPECT →
    X-S4-RESAMPLE), run once, variant-free, and shared by every variant: the score is on first-look samples (Part B 4), so a day
    after an exceedance on D−1 or D−2 is left out (X-S4-RESAMPLE) and a resample is never the truth a result
    is credited with. 'variant' names the correction and 'feed' the CSO feed its tail test read. Arguments
    as ``s5_rows``; nothing is fit on the scored days."""
    return s5_tables(geo, specs, inputs, feed_name, feed, variants, tier, fold_events, trained_through=trained_through,
                     held_out_season=held_out_season, samples=samples, ctx=ctx, tables=("samples",))["samples"]


def s5_tables(geo, specs: dict, inputs: C.BasinInputs, feed_name: str, feed: pd.DataFrame, variants=VARIANTS,
              tier: str = "T1", fold_events: pd.DataFrame | None = None, *, trained_through=None,
              held_out_season: int | None = None, samples: pd.DataFrame | None = None, ctx: X.Context | None = None,
              tables=("conditional", "samples")) -> dict:
    """{table: rows} for one feed and fold, the corrections composed once: 'conditional' and 'all_days' as
    ``s5_rows``' scopes, 'samples' as ``sample_rows``. A table with no day is an empty frame."""
    geo = T._geo(geo)
    tables = tuple(tables)
    if not tables or set(tables) - set(TABLES):
        raise ValueError(f"tables must be some of {TABLES}, not {tables}")
    empty = {t: _empty_rows(("feed",) if t == "samples" else ()) for t in tables}
    variants, run, scored, fit, season = _scored(geo, inputs, feed_name, feed, variants, tier, trained_through, held_out_season)
    if not len(scored):
        return empty                                        # nobody watched these days: no observation, no row
    seen = observed(geo, _in_run(feed, run), scored)
    smp = T._samples(tuple(SMP.DEFAULT_SOURCES)) if samples is None else samples
    on = set(scored)
    reach = {k: r for k, r in _newest_results(_zone_samples(smp), run).items() if k[1] in on} if "samples" in tables else {}
    need = [t for t in tables if (t == "conditional" and seen.to_numpy().any()) or t == "all_days" or (t == "samples" and reach)]
    if not need:
        return empty
    specs, rates, recall = _parameters(geo, specs, feed, variants, fit, season, fold_events)
    plain, frames = _corrected(geo, specs, inputs, feed, variants, scored, smp, rates, recall)
    ctx = _rows_context(geo, ctx, scored, feed_name, feed)
    el = T.zone_elevated(geo, ctx.sources).set_index(["zone", "date"])["n_stations_sampled"]   # the context's samples
    out = dict(empty)
    for t in need:
        out[t] = (_sample_table(ctx, el, plain, frames, variants, reach, feed_name, tier) if t == "samples" else
                  _s5_table(ctx, el, plain, frames, variants, scored, seen, feed_name, tier, t))
    return out


def _s5_table(ctx, el, plain, frames, variants, scored, seen, feed_name, tier, scope) -> pd.DataFrame:
    zf = ctx.frames["zone"]
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


def _sample_table(ctx, el, plain, frames, variants, reach, feed_name, tier) -> pd.DataFrame:
    zf = ctx.frames["zone"]
    parts = []
    for z in ZONES:
        days = pd.DatetimeIndex(sorted(D for (zk, D) in reach if zk == z))
        if not len(days):
            continue
        key = pd.MultiIndex.from_arrays([[z] * len(days), days])
        parts.append(pd.DataFrame({
            "date": days, "stage": SAMPLE_STAGE, "unit_type": "zone", "unit": z, "entry": SAMPLE_ENTRY, "lead": np.nan,
            "tier": tier, "sel": "", "p": np.nan, "q": np.nan, "b": plain.q.loc[days, z].to_numpy(dtype=float),
            "v_hat": np.nan, "y": zf["s4_y"].reindex(key).to_numpy(dtype=float),
            "y2": [float(reach[(z, D)][1]) for D in days], "n_sampled": el.reindex(key).fillna(0).to_numpy(dtype=float),
            "stratum": "", "excl": "", "tags": ""}))
    base = _sample_rules(pd.concat(parts, ignore_index=True), ctx, feed_name)
    out = []
    for v in variants:
        q = frames[v].q.stack().reindex(pd.MultiIndex.from_arrays([base["date"], base["unit"]])).to_numpy(dtype=float)
        out.append(base.assign(p=q, q=q, variant=v, feed=feed_name))
    rows = pd.concat(out, ignore_index=True)[list(SAMPLE_COLUMNS)]
    for c in ("p", "q", "b"):
        if rows[c].isna().any():
            raise ValueError(f"S5 sample rows: {c} is NaN on {int(rows[c].isna().sum())} rows; a correction left a day uncomposed")
    return rows


def _sample_rules(rows: pd.DataFrame, ctx: X.Context, feed_name: str) -> pd.DataFrame:
    """excl / tags / stratum / sel of one feed's sample rows (S4-shaped, variant-free), first match first: S5's feed
    rules (SAMPLE_S5_RULES: X-S5-HEALTH, X-S5-CIRC, X-S5-PERFECT-SIBLING, exclusions' own, run on the same zone-days
    as S5 rows of this feed — Part B 9 and protocol §3: a sibling zone-day is never scored on the perfect feed,
    whatever its truth), then S4's order on the rest (X-S4-UNSAMPLED → X-S4-HISTUNK → X-LEDGER-SUSPECT →
    X-S4-RESAMPLE), so S4's X-POWER counts the rows it scores. X-S5-SELF and X-S5-QUIET belong to the conditional
    set (the value an observation replaced is S3's p, not q); an S5-ruled row has no tag or stratum."""
    s5 = rows.assign(stage="s5", entry=feed_name, variant="")[list(X.COLUMNS)]
    s5["y"] = ctx.frames["zone"]["out_y"].reindex(pd.MultiIndex.from_arrays([s5["unit"], s5["date"]])).to_numpy(dtype=float)
    s5 = X.apply(s5, "s5", ctx, table="all_days")
    hit = s5["excl"].isin(SAMPLE_S5_RULES).to_numpy()
    out = rows.copy()
    if (~hit).any():
        rest = X.apply(rows[~hit], SAMPLE_STAGE, ctx)
        for c in ("excl", "tags", "stratum", "sel"):
            out.loc[~hit, c] = rest[c].to_numpy()
    if hit.any():
        out.loc[hit, "excl"] = s5["excl"].to_numpy()[hit]
        out.loc[hit, "tags"] = ""
        out.loc[hit, "stratum"] = ""
        out.loc[hit, "sel"] = s5["sel"].to_numpy()[hit]
    return out


def window_apply(rows: pd.DataFrame, ctx: X.Context) -> pd.DataFrame:
    """Rows of several folds put through exclusions again, one window at a time, so X-POWER counts the whole
    window (exclusions.apply's own rule): S5 rows per (feed, tier), as stages_build.s5_build does; S5's sample
    rows per (feed, tier) on one variant's rows, variant-free (exclusions allows a variant on S5 rows only) and
    through ``_sample_rules`` (S5's feed rules, then S4's), the result shared by every variant. Either way every
    variant must come out with the same excl (one set)."""
    if not len(rows):
        return rows
    stages = set(rows["stage"])
    if stages == {"s5"}:
        parts = [X.apply(g, "s5", ctx) for _, g in rows.groupby(["entry", "tier"], sort=False)]
    elif stages == {SAMPLE_STAGE} and "feed" in rows.columns:
        parts = []
        for (feed, _), g in rows.groupby(["feed", "tier"], sort=False):
            v0 = g["variant"].iloc[0]
            one = _sample_rules(g[g["variant"] == v0].drop(columns=["variant", "feed"]), ctx, feed)
            got = one.set_index(["unit", "date"])[["excl", "tags", "stratum", "sel"]]
            key = pd.MultiIndex.from_arrays([g["unit"], g["date"]])
            parts.append(g.assign(**{c: got[c].reindex(key).to_numpy() for c in got.columns}))
    else:
        raise ValueError(f"window_apply takes S5 rows or S5's sample rows, not stages {sorted(stages)}")
    out = pd.concat(parts, ignore_index=True)
    key = ["entry", "tier", "unit", "date"] + (["feed"] if "feed" in out.columns else [])
    piv = out.pivot_table(index=key, columns="variant", values="excl", aggfunc="first")
    if not (piv.nunique(axis=1) == 1).all():
        raise AssertionError("S5 rows: the variants disagree on what is scored")
    return out


# ── the report's tables: per-feed counts and the verdicts ──────────────────

TIER_ORDER = ("T2", "T1-holdout", "T1", "T0")
WINDOW_WORDS = {"T2": "cross-season (T2, development)", "T1-holdout": "holdout (T1-holdout, development)",
                "T1": "post-training (T1)", "T0": "prospective (T0)",
                "S5": "S5's window (T1-holdout ∪ T1, 2023-07-01 → the data end)"}


def _feed_col(rows: pd.DataFrame) -> str:
    """The column naming a row's CSO feed: 'feed' on S5's sample rows, the entry on S5 rows."""
    return "feed" if "feed" in rows.columns else "entry"


def _windows(tiers) -> list:
    """The windows a set of tiers is reported on: each tier in TIER_ORDER, then 'S5' (T1-holdout ∪ T1) when both
    are there — protocol §8's window is the two together, so rows of one alone are reported under that tier, never
    as S5's window. T2 is never pooled with another tier (it shares 2023-24 and 2024-25 with the holdout)."""
    t = set(tiers)
    unknown = t - set(TIER_ORDER)
    if unknown:
        raise ValueError(f"unknown tiers {sorted(unknown)}")
    return [x for x in TIER_ORDER if x in t] + (["S5"] if set(S5_WINDOW) <= t else [])


def _in_window(rows: pd.DataFrame, w: str) -> pd.DataFrame:
    return rows[rows["tier"].isin(S5_WINDOW)] if w == "S5" else rows[rows["tier"] == w]


def feed_counts(rows: pd.DataFrame, variant: str = NO_CORRECTION) -> dict:
    """Per-feed exclusion counts for the report, from one variant's rows (every variant shares the set):
    {feed: {window: {"n_total", "n_scored", "excluded": {rule: n}, "zones": {zone: {...}}}}}, S5 rows or S5's
    sample rows. Every feed is its own — each degraded seed separately, never summed across seeds, which see
    the same zone-days — and so is every tier; 'S5' adds T1-holdout and T1 (only for a feed with both), whose
    days never overlap, so each of its counts is still distinct zone-days. T2 is never added to the holdout
    (2023-24 and 2024-25 are in both). The partition holds in every cell (exclusions.counts asserts it; on S5's
    sample rows for the rows S4's order ruled, and the rows S5's feed rules took first are added by rule)."""
    one = rows[rows["variant"] == variant]
    if not len(one):
        raise ValueError(f"no rows of variant {variant!r} (variants here: {sorted(set(rows['variant']))})")
    out = {}
    for feed, g in one.groupby(_feed_col(one), sort=False):
        g = g.drop(columns=["feed"], errors="ignore")
        if set(g["stage"]) == {SAMPLE_STAGE}:                # S5's feed rules on sample rows: S5 ids exclusions' S4 table lacks
            hit = g["excl"].isin(SAMPLE_S5_RULES).to_numpy()
        else:
            hit = np.zeros(len(g), dtype=bool)
        found = []                                           # (unit, tier, n_total, n_scored, excluded)
        if (~hit).any():
            part = X.counts(g[~hit])["partition"]
            if len(part) != 1:
                raise ValueError(f"feed {feed}: rows of stages {sorted(part)}; count one table at a time")
            found += [(unit, tier, c["n_total"], c["n_scored"], c["excluded"])
                      for unit, by_entry in next(iter(part.values())).items()
                      for by_tier in by_entry.values() for tier, c in by_tier.items()]
        found += [(unit, tier, int(n), 0, {x: int(n)}) for (unit, tier, x), n in
                  g[hit].groupby(["unit", "tier", "excl"], sort=False).size().items()]
        cells: dict = {}
        for unit, tier, n_total, n_scored, excluded in found:
            for w in [tier] + (["S5"] if tier in S5_WINDOW else []):
                cell = cells.setdefault(w, {"n_total": 0, "n_scored": 0, "excluded": {}, "zones": {}})
                z = cell["zones"].setdefault(unit, {"n_total": 0, "n_scored": 0, "excluded": {}})
                for d in (cell, z):
                    d["n_total"] += n_total
                    d["n_scored"] += n_scored
                    for x, n in excluded.items():
                        d["excluded"][x] = d["excluded"].get(x, 0) + n
        for cell in cells.values():
            for d in [cell] + list(cell["zones"].values()):
                if d["n_total"] != d["n_scored"] + sum(d["excluded"].values()):
                    raise AssertionError(f"feed {feed}: a count cell does not partition its rows: {d}")
        if sum(c["n_total"] for w, c in cells.items() if w != "S5") != len(g):
            raise AssertionError(f"feed {feed}: the counts hold {sum(c['n_total'] for w, c in cells.items() if w != 'S5')} "
                                 f"rows of {len(g)}")
        out[feed] = {w: cells[w] for w in _windows(set(g["tier"])) if w in cells}
    return out


def _cell(y, arms: dict, blk, n_boot: int, storm=None) -> dict:
    """One feed × window: rows, positives, bootstrap blocks, storm blocks, X-POWER, and every variant's paired
    ΔBrier against no correction and against basin_swap (verify.paired_delta, Δ = BS(variant) − BS(reference),
    90% CI); a variant built on another (BUILT_ON: the downgrade on link_zone_swap) also against that one, its own
    rule's increment. ``storm``: each row's rain storm block (truth.blocks; NaN off storms). X-POWER is protocol
    §6's words: fewer than 10 positives or fewer than 8 *storm* blocks, whatever the bootstrap resamples (S5's
    conditional rows resample observation events); a low-power cell is shown with its CI and decides nothing."""
    y = np.asarray(y, dtype=float)
    k = int(len(np.unique(blk)))
    st = np.asarray(blk if storm is None else storm, dtype=float)
    n_storm = int(len(np.unique(st[np.isfinite(st)])))
    out = {"n": int(len(y)), "n_pos": int((y == 1).sum()), "n_blocks": k, "n_storm_blocks": n_storm,
           "low_power": bool((y == 1).sum() < X.POWER_MIN_POSITIVES or n_storm < X.POWER_MIN_STORM_BLOCKS),
           "bs": {v: V.clean(V.brier(y, p)) for v, p in arms.items()}, "variants": {}}
    for v, p in arms.items():
        cmp = {}
        for ref in (NO_CORRECTION, INCUMBENT) + ((BUILT_ON[v],) if v in BUILT_ON else ()):
            if v != ref and ref in arms:
                cmp[f"vs_{ref}"] = V.clean(V.paired_delta(y, p, arms[ref], blk, n=n_boot, seed=SEED, level=LEVEL))
        if cmp:
            out["variants"][v] = cmp
    return out


def circular_on_perfect(variant: str, ref: str) -> bool:
    """Part B 9's reason, on OUT's label: the perfect feed is the ledger, so a day it is silent on is a day the
    ledger filed nothing, and a variant that downgrades silent days (SILENCE) reads the label. A comparison there is
    circular when the variant reads that silence and the reference does not read it the same way: the downgrade
    against anything; basin_swap and all_floors (live_v2's own no-flag downgrade) against no correction or an
    injection. all_floors against basin_swap is not (one downgrade: the difference is FLOOR_MIN's). A reference that
    reads the silence while the variant does not (link_zone_swap against basin_swap, protocol §8's primary) favours
    the reference, so it is conservative for the variant and not marked."""
    s = SILENCE.get(variant)
    return s is not None and s != SILENCE.get(ref)


def _table(rows: pd.DataFrame, block_of, n_boot: int, storm_of=None, circular: bool = False) -> dict:
    """{feed: {window: _cell}} on the scored rows (excl == ''); ``block_of(feed, dates)`` → each row's bootstrap
    block, ``storm_of(dates)`` → its storm block (X-POWER; default the bootstrap's). ``circular``: mark the perfect
    feed's circular comparisons (``circular_on_perfect``): each carries circular = True (shown, never claimed) and
    the cell's 'circular' names the variants that have one."""
    sc = rows[rows["excl"] == ""]
    out: dict = {}
    for feed, g in sc.groupby(_feed_col(sc), sort=False):
        for w in _windows(set(g["tier"])):
            gw = _in_window(g, w)
            by_v = {v: gv.set_index(["tier", "unit", "date"]).sort_index() for v, gv in gw.groupby("variant", sort=False)}
            if NO_CORRECTION not in by_v:
                raise ValueError(f"{feed} {w}: no plain rows, so no variant can be compared with no correction")
            ref = by_v[NO_CORRECTION]
            for v, gv in by_v.items():
                if not gv.index.equals(ref.index):
                    raise AssertionError(f"{feed} {w}: variant {v} is scored on other zone-days than plain")
            dates = ref.index.get_level_values("date")
            blk = np.asarray(block_of(feed, dates), dtype=float)
            if not np.isfinite(blk).all() or (blk < 0).any():
                raise AssertionError(f"{feed} {w}: a scored row lies in no block")
            arms = {v: by_v[v]["p"].to_numpy(dtype=float) for v in VARIANTS if v in by_v}
            cell = _cell(ref["y"].to_numpy(dtype=float), arms, blk, n_boot, None if storm_of is None else storm_of(dates))
            if circular and X._feed_of(feed) == "oracle":
                marked = set()
                for v, cmp in cell["variants"].items():
                    for name, d in cmp.items():
                        if circular_on_perfect(v, name[len("vs_"):]):
                            d["circular"] = True
                            marked.add(v)
                cell["circular"] = [v for v in VARIANTS if v in marked]
            out.setdefault(feed, {})[w] = cell
    return out


def _seed_summary(table: dict) -> dict:
    """The degraded feeds over their seeds, never pooled: {window: {variant: {cmp: {"seeds", "of", "mean",
    "min", "max", "verdicts": {feed: words}, "low_power", "verdict"}}}}. The verdict is the seeds' when every
    degraded seed of the table scored the window and all give the same words; 'seeds disagree' when they differ,
    'not every seed scored' when a seed has no such cell. low_power: some seed's cell is X-POWER (it decides
    nothing)."""
    deg = sorted((f for f in table if X._feed_of(f) == "degraded"), key=lambda f: (len(f), f))
    out: dict = {}
    for w in WINDOW_WORDS:
        cells = [(f, table[f][w]) for f in deg if w in table[f]]
        if not cells:
            continue
        for v in VARIANTS:
            for cmp in (f"vs_{NO_CORRECTION}", f"vs_{INCUMBENT}") + ((f"vs_{BUILT_ON[v]}",) if v in BUILT_ON else ()):
                got = [(f, c["variants"][v][cmp]) for f, c in cells if cmp in c["variants"].get(v, {})]
                if not got:
                    continue
                ds = [d["delta"] for _, d in got]
                words = {f: d["verdict"] for f, d in got}
                verdict = ("not every seed scored" if len(got) < len(deg) else
                           next(iter(words.values())) if len(set(words.values())) == 1 else "seeds disagree")
                out.setdefault(w, {}).setdefault(v, {})[cmp] = {
                    "seeds": len(got), "of": len(deg), "mean": float(np.mean(ds)), "min": float(min(ds)),
                    "max": float(max(ds)), "verdicts": words,
                    "low_power": any(c["low_power"] for f, c in cells if f in words), "verdict": verdict}
    return out


def _words(vs: list) -> str:
    return ", ".join(vs) if vs else "none"


def _statement(res: dict) -> tuple[dict, list]:
    """({table: {window: {"perfect": [...], "degraded": [...], "archive": [...], "both": [...], "x_power": [...],
    "circular": [...]}}}, plain lines): the variants better than no correction (the 90% CI wholly below 0) on the
    perfect feed, on every degraded seed, on the archive, and on both the perfect and every degraded feed; the
    variants worse than no correction are named too. Nothing is claimed from an X-POWER cell (protocol §6: shown
    with its CI, decides nothing; for the degraded feeds, a cell X-POWER in any seed): 'x_power' names those
    feeds. Nor from a comparison circular on the perfect feed (``circular_on_perfect``): 'circular' names the
    variants whose comparison with no correction is."""
    beats: dict = {}
    lines = []
    what = {"conditional": "against OUT's label, on the zone-days after an observation",
            "samples": "against first-look samples, on D+1…D+3 after a lab result"}
    key = f"vs_{NO_CORRECTION}"

    def pick(cell, word):
        if not cell or cell["low_power"]:
            return []
        return [v for v, c in cell["variants"].items() if not c.get(key, {}).get("circular") and c.get(key, {}).get("verdict") == word]

    def power(cell) -> str:
        return f"X-POWER ({cell['n_pos']} positives, {cell['n_storm_blocks']} storm blocks): shown, decides nothing"

    for t, words in what.items():
        tab = res.get(t) or {}
        if not tab:
            continue
        for w in WINDOW_WORDS:
            perfect = (tab.get("oracle") or {}).get(w)
            deg = res["degraded"].get(t, {}).get(w)
            arch = (tab.get("archive") or {}).get(w)
            if not (perfect or deg or arch):
                continue
            deg_low = bool(deg) and any(c[key]["low_power"] for c in deg.values() if key in c)
            dpick = lambda word: [] if deg_low else [v for v, c in (deg or {}).items() if key in c and c[key]["verdict"] == word]  # noqa: E731
            b = {"perfect": pick(perfect, "better"), "degraded": dpick("better"), "archive": pick(arch, "better")}
            b["both"] = [v for v in b["perfect"] if v in b["degraded"]]
            b["x_power"] = [n for n, low in (("perfect", bool(perfect) and perfect["low_power"]), ("degraded", deg_low),
                                             ("archive", bool(arch) and arch["low_power"])) if low]
            b["circular"] = [v for v, c in (perfect or {}).get("variants", {}).items() if c.get(key, {}).get("circular")]
            beats.setdefault(t, {})[w] = b
            parts = []
            if perfect:
                circ = f"; circular on the perfect feed, not claimed: {_words(b['circular'])}" if b["circular"] else ""
                parts.append(f"perfect feed: {power(perfect)}" if perfect["low_power"] else
                             f"perfect feed: better {_words(b['perfect'])}; worse {_words(pick(perfect, 'worse'))}{circ}")
            if deg:
                n = max(c[key]["seeds"] for c in deg.values() if key in c)
                parts.append(f"degraded feeds ({n} seeds): X-POWER in some seed: shown, decides nothing" if deg_low else
                             f"degraded feeds ({n} seeds, each must agree): better {_words(b['degraded'])}; worse {_words(dpick('worse'))}")
            if arch:
                parts.append(f"2016-17 archive: {power(arch)}" if arch["low_power"] else
                             f"2016-17 archive: better {_words(b['archive'])}; worse {_words(pick(arch, 'worse'))}")
            lines.append(f"{WINDOW_WORDS[w]}, {words}, than no correction — " + " · ".join(parts))
    head = []
    for t, words in (("conditional", "against OUT's label"), ("samples", "against first-look samples")):
        s5 = (beats.get(t) or {}).get("S5")
        if s5 is not None:
            head.append(f"Beats doing nothing on both the perfect and every degraded feed in S5's window (protocol §8), "
                        f"{words}: {_words(s5['both'])}.")
    return beats, head + lines


def verdicts(rows: pd.DataFrame, feeds: dict, samples: pd.DataFrame | None = None, n_boot: int = 2000) -> dict:
    """The S5 candidate verdict table (design §3.5's benchmarks; protocol §6, §8): for every feed and window
    (each tier, and 'S5' = T1-holdout ∪ T1), every variant's paired ΔBrier against no correction ('vs_plain')
    and against basin_swap = live_v2 ('vs_basin_swap'), and the downgrade against link_zone_swap, the variant it
    adds its rule to ('vs_link_zone_swap', BUILT_ON), each with its 90% block-bootstrap CI (B = ``n_boot``, the
    protocol's 2,000 by default; seed 0) and verify's verdict words; Δ < 0 means the variant is better.
    all_floors − basin_swap is §5.8's FLOOR_MIN re-test.

        conditional  S5 rows (``s5_rows``, scored rows only) against OUT's label, blocks = observation events
                     (stages_build.s5_blocks on the row's feed). sample_swap's Δ here is 0 by A1. On the perfect
                     feed a comparison whose variant reads the ledger's silence is circular (``circular_on_perfect``:
                     the downgrade's, and live_v2's against no correction or an injection): shown, never claimed.
        samples      S5's sample rows (``sample_rows``), q against first-look samples, blocks = storm blocks
                     (truth.blocks): where sample_swap's value shows.
        degraded     each table's degraded feeds summarised over the seeds (``_seed_summary``), never pooled.
        beats_nothing, statement  the variants better than no correction, per table and window, in words.

    Every cell carries X-POWER in protocol §6's words (fewer than 10 positives or 8 storm blocks, the storms of
    truth.blocks, in both tables); an X-POWER cell decides nothing. Rows are put through ``window_apply`` first by
    the caller when they come from several folds."""
    import stages_build as B                    # deferred: stages_build imports this module
    res = {"bootstrap": {"n": int(n_boot), "seed": SEED, "level": LEVEL, "protocol": n_boot == 2000}}
    obs_block = lambda feed, dates: B.s5_blocks(feeds[feed], dates)  # noqa: E731
    tb = T.blocks().set_index("date")
    storm_blocks = tb["block"].where(tb["block_kind"] == "storm")                 # NaN off storms
    storm_of = lambda dates: storm_blocks.reindex(pd.DatetimeIndex(dates)).to_numpy(dtype=float)  # noqa: E731
    res["conditional"] = (_table(rows, obs_block, n_boot, storm_of, circular=True)
                          if rows is not None and len(rows) else {})
    if samples is not None and len(samples):
        res["samples"] = _table(samples, lambda feed, dates: tb["block"].reindex(pd.DatetimeIndex(dates)).to_numpy(), n_boot,
                                storm_of)
    else:
        res["samples"] = {}
    res["degraded"] = {t: _seed_summary(res[t]) for t in ("conditional", "samples")}
    res["beats_nothing"], res["statement"] = _statement(res)
    return res


def verdict_lines(res: dict) -> list:
    """The verdict table as plain text lines: per table and window, each variant's Δ against no correction and
    against basin_swap (the downgrade also against link_zone_swap) with the 90% CI and the words; the degraded
    feeds as the seeds' mean, range and words. X-POWER cells and circular comparisons are marked: they decide
    nothing."""
    f = lambda d: "—" if d is None or d.get("delta") is None else (  # noqa: E731
        (f"{d['delta']:+.5f} [{d['lo']:+.5f}, {d['hi']:+.5f}] {d['verdict']}" if d.get("lo") is not None else f"{d['delta']:+.5f} (no CI)")
        + (" (circular on the perfect feed: not claimed)" if d.get("circular") else ""))
    g = lambda s: "—" if s is None else f"mean {s['mean']:+.5f} (seeds {s['min']:+.5f} … {s['max']:+.5f}) {s['verdict']}"  # noqa: E731
    out = list(res["statement"]) + [""]
    for t in ("conditional", "samples"):
        for feed, by_w in (res.get(t) or {}).items():
            if X._feed_of(feed) == "degraded":
                continue
            for w, c in by_w.items():
                out.append(f"[{t}] {feed} · {WINDOW_WORDS[w]}: n {c['n']}, positives {c['n_pos']}, blocks {c['n_blocks']}, "
                           f"storm blocks {c['n_storm_blocks']}" + (" (X-POWER: decides nothing)" if c["low_power"] else ""))
                for v, cmp in c["variants"].items():
                    out.append(f"    {v:15} vs plain {f(cmp.get('vs_plain'))} · vs basin_swap {f(cmp.get('vs_basin_swap'))}"
                               + (f" · vs {BUILT_ON[v]} {f(cmp.get('vs_' + BUILT_ON[v]))}" if v in BUILT_ON else ""))
        for w, by_v in (res["degraded"].get(t) or {}).items():
            low = any(s["low_power"] for cmp in by_v.values() for s in cmp.values())
            out.append(f"[{t}] degraded feeds · {WINDOW_WORDS[w]}" + (" (X-POWER in some seed: decides nothing)" if low else ""))
            for v, cmp in by_v.items():
                out.append(f"    {v:15} vs plain {g(cmp.get('vs_plain'))} · vs basin_swap {g(cmp.get('vs_basin_swap'))}"
                           + (f" · vs {BUILT_ON[v]} {g(cmp.get('vs_' + BUILT_ON[v]))}" if v in BUILT_ON else ""))
    return out


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


def stage_rows(set_name: str = "served", root: str = "served", tiers=("T1", "T1-holdout", "T2"), seasons=None,
               feed_names=None, variants=None, log=print) -> tuple:
    """(S5 rows, S5's sample rows, the feeds, the context) on stages_build's own S2 folds, for the verdict
    table: the set and its stamps (stages_build.load_set), S2 refit per fold (stages_s2.fit), the fold's S3 / S4
    specs (stages_build.fold_specs, fit on the fold's training days) and its rain-known run with the 8-day
    warm-up (stages_build.runs_for), the two-gauge rain over the whole record (an S4 rain background reads D−2
    before a run) — the inputs stages_build.s5_build hands ``s5_rows`` — then both tables per fold and feed
    (``s5_tables``: the conditional set and the sample rows), and ``window_apply`` per window. As in s5_build, the
    degraded feeds run on S5's window only (protocol §8; their era also reaches T2's 2023-24 and 2024-25, which
    would be reported as T2 though they are two of its nine seasons), and ``variants`` defaults to every
    variant the geography replays (live_v2's rule sets on GEO_V1 only). A stage candidate (stages_candidates) brings
    its own folds: its S5 rows are stages_build's, so it raises here. ``seasons`` keeps those T2 seasons,
    ``feed_names`` those feeds (development knobs). Reads the committed data; writes nothing."""
    import stages_build as B                    # deferred: stages_build imports this module
    import stages_entries as E
    import stages_s2 as S2
    bundle = B.load_set(set_name, root)
    if getattr(bundle, "stage", None) is not None:
        raise ValueError(f"{set_name} is a stage candidate with its own folds and specs: its S5 rows are "
                         "stages_build.build's (s5_build), not these refits")
    geo, s2set = bundle.geo, bundle.s2
    if variants is None:                        # live_v2's rule sets are GEO_V1's only (_live_v2 raises elsewhere)
        variants = VARIANTS if geo.version == "geo_v1" else tuple(v for v in VARIANTS if v not in LIVE_V2)
    variants = tuple(variants)
    end = E.data_end()
    fd = feeds(geo, end=end)
    fd = {k: v for k, v in fd.items() if k != "watcher" and (feed_names is None or k in set(feed_names))}
    ctx = X.context(geo, end=end).with_inputs(feeds=fd)
    need = sorted(set(s2set.sources) | set(bundle.chosen.values()) | {"avg"})
    train, _ = B.T4.build_dataset(sources=need)
    fitted = S2.fit(bundle.name, root, tuple(tiers), train_frames=train)
    plan = {(p[0], p[1]): p for p in S2._plan(tuple(tiers))}
    frames = E.frames("oracle", need, model=E.served_weather_model(), end=end)
    rain = frames["avg"].set_index("date")["precip_avg"].astype(float)          # stages_build's rain_known
    pred = fitted.predict({"oracle": frames})
    events, samples = B.T4.load_events(), B.T4.load_samples()
    rows, srows = [], []
    for f in fitted.folds:
        if f.tier == "T2" and seasons is not None and f.season not in set(seasons):
            continue
        specs, _ = B.fold_specs(bundle, f, plan[(f.tier, f.fold)][5], train, events, samples)
        mine = pred[(pred["tier"] == f.tier) & (pred["fold"] == f.fold) & (pred["entry"] == "oracle")]
        rs = B.runs_for(s2set, f, "oracle", frames, pd.DatetimeIndex(sorted(mine["date"].unique())))
        if len(rs) != 1:
            raise AssertionError(f"rain known breaks into {len(rs)} runs in {f.tier} {f.fold}: a gauge day is missing")
        inputs = C.BasinInputs(rs[0].p, rs[0].v, rain)
        kw = ({"held_out_season": f.season} if f.tier == "T2"
              else {"trained_through": s2set.trained_through if f.tier == "T1" else S2.HOLDOUT_START - pd.Timedelta(days=1)})
        for name, feed in fd.items():
            if X._feed_of(name) == "degraded" and f.tier not in S5_WINDOW:
                continue                                   # built for S5's window (§8), as stages_build.s5_build
            got = s5_tables(geo, specs, inputs, name, feed, variants, f.tier, ctx=ctx, **kw)
            rows += [got["conditional"].assign(fold=f.fold)] if len(got["conditional"]) else []
            srows += [got["samples"].assign(fold=f.fold)] if len(got["samples"]) else []
        log(f"  {f.tier} {f.fold}: S5 rows so far {sum(map(len, rows))}, sample rows {sum(map(len, srows))}")
    cat = lambda parts, extra: pd.concat(parts, ignore_index=True) if parts else _empty_rows(extra)  # noqa: E731
    return window_apply(cat(rows, ("fold",)), ctx), window_apply(cat(srows, ("feed", "fold")), ctx), fd, ctx


def _main_verdicts(argv: list) -> None:
    """--verdicts [--set NAME] [--root served|candidates] [--n-boot N] [--seasons 2019,2020] [--feeds oracle,degraded:1]
    [--json]: the verdict table and the per-feed counts on stages_build's folds, printed (JSON with --json)."""
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    n_boot = int(opt("--n-boot", 2000))
    seasons = [int(s) for s in opt("--seasons").split(",")] if opt("--seasons") else None
    feed_names = opt("--feeds").split(",") if opt("--feeds") else None
    quiet = "--json" in argv
    rows, srows, fd, _ = stage_rows(opt("--set", "served"), opt("--root", "served"), seasons=seasons, feed_names=feed_names,
                                    log=(lambda *a: None) if quiet else print)
    res = verdicts(rows, fd, srows, n_boot=n_boot)
    res["counts"] = {"conditional": feed_counts(rows), "samples": feed_counts(srows) if len(srows) else {}}
    res["recall_note"] = "downgrade: feed_recall per fold on its training days (see the module notes)"
    if quiet:
        print(json.dumps(V.clean(res), indent=1, default=str))
        return
    print("\n".join(verdict_lines(res)))
    for t, by_feed in res["counts"].items():
        for feed, by_w in by_feed.items():
            for w, c in by_w.items():
                print(f"[counts {t}] {feed} · {w}: {c['n_total']} zone-days, {c['n_scored']} scored, left out {c['excluded']}")


if __name__ == "__main__":
    if "--verdicts" in sys.argv:
        _main_verdicts(sys.argv[1:])
        sys.exit(0)
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
