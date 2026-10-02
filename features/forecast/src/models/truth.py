"""The truths every stage is scored against, built once per geography (P4;
STAGES_DESIGN.md Part C §3.0–§3.6, §4, §8 "P4 · Truth and exclusions", as
amended by Part B 2, 3, 4, 8, 10 and 22; STAGES_PROTOCOL.md §1, §6, §7).

A stage score is only as good as what it is graded on, so the truths live in
one module and every stage reads the same frames:

    S1   gauges()              ACIS daily totals per gauge and their two-gauge mean
    S2   basin_onsets(geo)     an overflow starts at an outfall of the basin on D
    S3   zone_overflow(geo)    an outfall of a link into the zone started one on D
         zone_overflow_storm   the same, per rain storm (§3.3 secondary a)
    S4   zone_elevated(geo)    any sampled station of the zone over standard on D
    OUT  out_label(geo)        the public claim's bad / good / unknown day
    second ruler  postings()   BeachWatch zone-days, to its last filing
    blocks        storms()     rain storms and bootstrap blocks (protocol §6)

and the context the exclusion rules read (src/models/exclusions.py, P4b):
ledger_known (X-S2-UNCOV, X-S3-UNCOV, X-S4-HISTUNK, X-E2E-UNCOV), the Poo Bot
archive days (X-S2-ARCHIVE), carry_days (X-S2-CARRY, X-S3-CARRY), the volume
qualifiers (X-S2-VOLQ), the gauge status (X-S1-MISSING, X-S1-OUTAGE), the
first-look and few-stations flags (X-S4-RESAMPLE, X-S4-FEW) and the posting end
(X-PL-END). This module labels; it never excludes. A frame says what is true
and what is known, and the exclusion rules decide what is scored.

**One coverage rule (protocol §1).** ``ledger_known(basin, D)``: D's month lies
in the facility's continuous ledger and the basin's facility filed it with
status events_parsed, table_present_zero_events or
no_table_stated_no_discharge. The continuous ledger is the unbroken run of
those months that ends at the facility's last grid month, opened by its first
month with a filed CSD table (``ledger_start``: Bayside 2016-10, Oceanside
2017-12 on the committed grid, derived, never typed). Statuses before it are
not trusted: Bayside's 2016 "no discharge" statements (Feb, Apr–Jul, Sep) are
not known. All of it is csd_labels' coverage grid read per facility
(``facility_covered_dates``), never re-derived. Poo Bot archive days
(2016-03-19 → 2017-01-10) the continuous ledger does not cover are not known
either: the feed's onsets are S5's real archive feed, never truth
(``archive_onsets``). So the served scorecard's labels differ from these
inside that window, where it took Westside onsets and Bay-side extra onsets
from the feed (Part C fix 22); outside it they agree (tests/test_truth.py).
The served frames start the Bay-side ledger at the same month
(build_daily_labels, 2016-10), and Westside's at the same grid month.

**Geography.** Every ledger frame takes a ``shared.geography.Geography``
(``geography.get('geo_v1' | 'sfpuc4_v1')``, or its version string). An event's
basin, link and zone come from its outfall through the geography; an outfall
the geography lacks raises. The zone truths are geography-invariant (every
outfall posts one zone's stations, and a zone's feeding basins share one
facility), which is why the S3 oracle cannot decide the remap (§3.3).

**Missing is never zero.** A gauge day nobody recorded is NaN, a basin-day the
ledger does not cover has ``y`` = <NA>, a ledger event on a day its facility
did not file raises, and so does an outfall the registry or the geography
lacks, or a zone shared/zones.py lacks.

The day frames are long (one row per unit × day, sorted by unit then date) with
plain column names. Loaders are cached; every public function returns a new
frame the caller may change. No module-level IO: files are read on first use.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, HERE, HERE.parent / "collectors"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import csd_labels  # noqa: E402  (the ledger and its facility-month coverage grid)
import samples as SMP  # noqa: E402
import train_v4  # noqa: E402  (span start, the archive window and the Poo Bot feed tables)
import verify  # noqa: E402
from rain_features import mask_gauge_outages  # noqa: E402  (gauge_outage_v1, the one implementation)
from shared import geography as G  # noqa: E402
from shared.outfalls import OBSERVED, OUTFALLS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

RAIN_CSV = train_v4.RAW_DIR / "historical_rain.csv"     # ACIS daily totals, both gauges (collectors/historical.py)
TRUTH_START = train_v4.TRAIN_START                     # 2016-03-01: the first day of every scored frame (the scorecard's)
ARCHIVE_START, ARCHIVE_END = train_v4.ARCHIVE_START, train_v4.ARCHIVE_END   # the Poo Bot feed archive: never ledger truth
GAUGE_SERIES = ("SF Downtown", "SF Oceanside")         # ACIS 047772, 047767 (historical_rain.csv rain_station_name)
MEAN_SERIES = "avg"                                    # the two-gauge mean, train_v4.rain_series' name for it
SERIES = GAUGE_SERIES + (MEAN_SERIES,)
TRACE_IN = 0.001                                       # collectors/historical.py stores ACIS 'T' as 0.001; scored as 0
TAIL_DAYS = 7                                          # OUT's tail k = 1…7 (impact.compose, scorecard.TAIL_DAYS)
HISTORY_DAYS = 7                                       # "the history known": every feeding basin known on D−7…D
LEVELS = ("basin", "link", "zone")
GRID_STATUSES = ("events_parsed", "table_present_zero_events", "no_table_stated_no_discharge", "no_event_table_found")
TABLE_STATUSES = ("events_parsed", "table_present_zero_events")   # a CSD table was filed: only these open the continuous ledger


# ── plumbing ───────────────────────────────────────────────────────────────

def _geo(geo) -> G.Geography:
    """A Geography, or its version string (KeyError on an unknown one: there is no default)."""
    if isinstance(geo, G.Geography):
        return geo
    if isinstance(geo, str):
        return G.get(geo)
    raise TypeError(f"geo must be a shared.geography.Geography or a version string, not {type(geo).__name__}")


def _ts(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def _days(start, end) -> pd.DatetimeIndex:
    lo, hi = _ts(start), _ts(end)
    if lo > hi:
        raise ValueError(f"start {lo.date()} is after end {hi.date()}")
    return pd.date_range(lo, hi, name="date")


def _zone_order(z: pd.Series) -> pd.Series:
    order = {k: i for i, k in enumerate(ZONES)}
    unknown = set(z) - set(order)
    if unknown:
        raise KeyError(f"zones not in shared/zones.py: {sorted(unknown)}")
    return z.map(order)


def _nullable(y, known) -> pd.Series:
    """0/1 where known, <NA> where not: an unknown day is never a negative."""
    y, known = np.asarray(y, dtype=bool), np.asarray(known, dtype=bool)
    return pd.Series(y.astype("int8"), dtype="Int8").mask(~known)


# ── S1 · gauges ─────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _gauge_record() -> tuple:
    rain = pd.read_csv(RAIN_CSV, parse_dates=["date"])
    other = set(rain["rain_station_name"]) - set(GAUGE_SERIES)
    if other:
        raise ValueError(f"historical_rain.csv names gauges outside {GAUGE_SERIES}: {sorted(other)}")
    dup = rain.duplicated(["date", "rain_station_name"])
    if dup.any():
        raise ValueError(f"historical_rain.csv has {int(dup.sum())} repeated gauge-days, e.g. {rain[dup].iloc[0].to_dict()}")
    rp = rain.pivot(index="date", columns="rain_station_name", values="precip_inches")
    days = pd.date_range(rp.index.min(), rp.index.max(), name="date")
    raw = rp.reindex(days)[list(GAUGE_SERIES)]           # a day absent from the file is missing, never 0
    tmp, runs = mask_gauge_outages(raw.reset_index(), gauges=GAUGE_SERIES)   # on the raw record, every day, as serving runs it
    masked = tmp.set_index("date")[list(GAUGE_SERIES)]
    status = pd.DataFrame("ok", index=days, columns=list(GAUGE_SERIES))
    status[raw.isna()] = "missing"
    status[masked.isna() & raw.notna()] = "outage"
    return raw, masked.mask(masked == TRACE_IN, 0.0), status, tuple(runs)


def outage_runs() -> list[dict]:
    """The gauge_outage_v1 runs on the whole record: {gauge, start, end, days, other_total}."""
    return [dict(r) for r in _gauge_record()[3]]


def gauges(start=None, end=None) -> pd.DataFrame:
    """S1 truth: one row per (series, day) for SF Downtown, SF Oceanside and their mean.

    Columns: date, series, rain (inches; NaN = no usable reading), raw (the ACIS
    value as filed), status, n_gauges (usable gauges behind the row).

    ``gauge_outage_v1`` masks a dead gauge's 0.00 run on every day, then a trace
    ('T') scores 0. A gauge row's status is 'ok', 'missing' (null or 'M':
    X-S1-MISSING) or 'outage' (inside a run: X-S1-OUTAGE); its rain is NaN
    unless ok, never filled. The mean (series 'avg') averages the usable gauges,
    so a missing or masked gauge falls back to the other; it is NaN, with the
    status of the first rule that applies (missing before outage), only when
    neither gauge is usable. The record must cover [start, end]: asking past it
    raises rather than invent missing days.
    """
    raw, rain, status, _ = _gauge_record()
    lo = raw.index.min() if start is None else _ts(start)
    hi = raw.index.max() if end is None else _ts(end)
    if lo < raw.index.min() or hi > raw.index.max():
        raise ValueError(f"the gauge record runs {raw.index.min().date()} → {raw.index.max().date()}; asked {lo.date()} → {hi.date()}")
    days = _days(lo, hi)
    raw, rain, status = raw.loc[days], rain.loc[days], status.loc[days]
    out = []
    for g in GAUGE_SERIES:
        ok = status[g] == "ok"
        out.append(pd.DataFrame({"date": days, "series": g, "rain": rain[g].where(ok).to_numpy(), "raw": raw[g].to_numpy(),
                                 "status": status[g].to_numpy(), "n_gauges": ok.astype(int).to_numpy()}))
    n = (status == "ok").sum(axis=1)
    worst = np.where((status == "missing").any(axis=1), "missing", "outage")
    out.append(pd.DataFrame({"date": days, "series": MEAN_SERIES, "rain": rain.mean(axis=1).to_numpy(),
                             "raw": raw.mean(axis=1).to_numpy(), "status": np.where(n > 0, "ok", worst), "n_gauges": n.to_numpy()}))
    return pd.concat(out, ignore_index=True)


def gauge_rain(start=None, end=None) -> pd.DataFrame:
    """date × {SF Downtown, SF Oceanside, avg}: ``gauges()``'s rain, wide (NaN kept)."""
    g = gauges(start, end)
    return g.pivot(index="date", columns="series", values="rain")[list(SERIES)]


# ── storms and blocks (protocol §6, Part B 10) ─────────────────────────────

def blocks(start=None, end=None) -> pd.DataFrame:
    """One row per day: rain (the two-gauge masked mean), wet, storm, block, block_kind.

    The bootstrap blocks of every stage but S1 and S5, built from rain, never
    from the ledger (Part B 10), by ``verify.storm_spans`` and
    ``verify.storm_blocks`` at their defaults (wet ≥ 0.1", at most 2 dry days
    inside a storm, a storm block = [first wet day − 1, last wet day + 7],
    overlaps merged, quiet days in ISO-week blocks between the same two
    storms). A day neither gauge recorded is NaN rain and counts as dry.
    ``storm`` is the storm id whose span holds the day, else −1.
    """
    rain = gauge_rain(start, end)[MEAN_SERIES]
    dates = rain.index
    spans = verify.storm_spans(dates, rain.to_numpy())        # verify's defaults are the protocol's numbers
    blk = verify.storm_blocks(dates, rain.to_numpy())
    pad = storm_rule()["pad"]
    storm = np.full(len(dates), -1)
    padded = np.zeros(len(dates), bool)
    for i, (s, e) in enumerate(spans):
        storm[(dates >= s) & (dates <= e)] = i
        padded |= (dates >= s - pd.Timedelta(days=pad[0])) & (dates <= e + pd.Timedelta(days=pad[1]))
    return pd.DataFrame({"date": dates, "rain": rain.to_numpy(), "wet": (rain >= storm_rule()["wet"]).to_numpy(),
                         "storm": storm, "block": blk, "block_kind": np.where(padded, "storm", "quiet")})


def storm_rule() -> dict:
    """{wet, gap, pad}: the defaults of verify.storm_spans / storm_blocks, which ``blocks`` and ``storms`` use."""
    import inspect
    spans, blks = inspect.signature(verify.storm_spans).parameters, inspect.signature(verify.storm_blocks).parameters
    if (spans["wet"].default, spans["gap"].default) != (blks["wet"].default, blks["gap"].default):
        raise AssertionError("verify.storm_spans and storm_blocks disagree on their defaults")
    return {"wet": spans["wet"].default, "gap": spans["gap"].default, "pad": blks["pad"].default}


def storms(start=None, end=None) -> pd.DataFrame:
    """One row per rain storm: storm, start, end (first and last wet day), n_days, n_wet_days, rain_in, block.

    ``block`` is the bootstrap block holding the storm (several storms can share
    one, when their padded blocks overlap). See ``blocks``.
    """
    b = blocks(start, end)
    rows = []
    for sid, g in b[b["storm"] >= 0].groupby("storm"):
        blk = g["block"].unique()
        if len(blk) != 1:
            raise AssertionError(f"storm {sid} spans blocks {blk}")
        rows.append({"storm": int(sid), "start": g["date"].min(), "end": g["date"].max(), "n_days": len(g),
                     "n_wet_days": int(g["wet"].sum()), "rain_in": round(float(g["rain"].sum()), 2), "block": int(blk[0])})
    return pd.DataFrame(rows, columns=["storm", "start", "end", "n_days", "n_wet_days", "rain_in", "block"])


# ── the ledger ─────────────────────────────────────────────────────────────

def _start_time(ev: pd.DataFrame) -> pd.Series:
    """Event start as a timestamp: '2:09 AM' or the manual 24 h '15:00'; NaT when unparseable."""
    day = ev["event_date"].dt.strftime("%Y-%m-%d")
    t = ev["start_time"].astype(str).str.strip()
    a = pd.to_datetime(day + " " + t, format="%Y-%m-%d %I:%M %p", errors="coerce")
    b = pd.to_datetime(day + " " + t, format="%Y-%m-%d %H:%M", errors="coerce")
    return a.fillna(b)


@functools.lru_cache(maxsize=None)
def _events(geo: G.Geography) -> pd.DataFrame:
    ev = csd_labels.load_events()
    multi = ev["outfall_id"].astype(str).str.contains(r"\|")
    if multi.any():
        raise ValueError("a CIWQS row names several outfalls; the ledger is one outfall per row "
                         f"(aggregate.py): {ev.loc[multi, 'outfall_id'].tolist()[:5]}")
    unknown = sorted(set(ev["outfall_id"]) - set(OUTFALLS))
    if unknown:
        raise KeyError(f"ledger outfalls not in shared/outfalls.py: {unknown}")
    link = {o: geo.link_of_outfall(o) for o in ev["outfall_id"].unique()}   # KeyError for one the geography lacks
    start = _start_time(ev)
    parsed = start.notna()
    start = start.fillna(ev["event_date"])                # no parseable start time: counts as starting on event_date (§4.3)
    dur = pd.to_numeric(ev["duration_min"], errors="coerce")
    end = start + pd.to_timedelta(dur.fillna(0.0), unit="m")   # unknown duration: no carry-over is claimed
    qual = ev["volume_qualifier"].fillna("").astype(str)
    return pd.DataFrame({
        "date": ev["event_date"].dt.normalize(), "outfall": ev["outfall_id"],
        "basin": ev["outfall_id"].map(lambda o: link[o].basin), "link": ev["outfall_id"].map(lambda o: link[o].id),
        "zone": ev["outfall_id"].map(lambda o: link[o].zone),
        "observed": ev["outfall_id"].map(lambda o: OUTFALLS[o].evidence == OBSERVED),
        "start": start, "start_parsed": parsed, "end": end, "duration_known": dur.notna(),
        "volume_mg": pd.to_numeric(ev["volume_MG"], errors="coerce"),
        "vol_null": pd.to_numeric(ev["volume_MG"], errors="coerce").isna(), "vol_lt": qual == "<",
    }).sort_values(["date", "outfall", "start"], kind="stable").reset_index(drop=True)


def ledger_events(geo) -> pd.DataFrame:
    """The CIWQS ledger, one row per filed event, placed in the geography.

    Columns: date (the start day CIWQS files it under), outfall, basin, link,
    zone, observed (the outfall's station mapping was seen in the 2016-17 feed),
    start, start_parsed, end, duration_known, volume_mg, vol_null, vol_lt.
    A start time that does not parse (3 rows on the committed data) counts as
    the event_date's midnight; an unknown duration ends the event at its start.
    volume_mg is the row as filed: a '<0.01' reads 0.01 with vol_lt set, and the
    day sums (basin_onsets, link_onsets) leave it out.
    """
    return _events(_geo(geo)).copy()


def ledger_end() -> pd.Timestamp:
    """The last day of the CIWQS coverage grid (any facility): the default end of the ledger frames."""
    cov = csd_labels.load_coverage()
    last = cov.sort_values(["year", "month"]).iloc[-1]
    return pd.Timestamp(int(last["year"]), int(last["month"]), 1) + pd.offsets.MonthEnd(0)


@functools.lru_cache(maxsize=None)
def _continuous_ledger(facility: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(first day, last day) of the facility's continuous ledger, read from csd_labels' coverage grid.

    Walk back from the facility's last grid month over covered months (csd_labels' three statuses)
    until one is not covered; the run opens at its first month with a filed CSD table
    (TABLE_STATUSES), so a "no discharge" statement can sit inside the ledger but never start it.
    The grid must hold every month from its first to its last, each with a status it knows, and its
    last month must be covered, and no filed event of the facility may fall before the start (an
    unfiled month a refresh puts inside the ledger would cut the run there and drop every event before
    it): anything else raises rather than move the start silently.
    """
    cov = csd_labels.load_coverage()
    name = csd_labels.facility_grid_name(facility)
    g = cov[cov["facility"] == name]
    other = sorted(set(g["status"]) - set(GRID_STATUSES))
    if other:
        raise ValueError(f"{name}: coverage statuses {other} are not in {GRID_STATUSES}")
    g = g.assign(_m=g["year"].astype(int) * 12 + g["month"].astype(int) - 1).sort_values("_m")
    m, covered = g["_m"].to_numpy(), g["covered"].to_numpy(dtype=bool)
    if (np.diff(m) != 1).any():
        raise ValueError(f"{name}: the coverage grid skips or repeats a month")
    month = lambda k: pd.Timestamp(int(k) // 12, int(k) % 12 + 1, 1)  # noqa: E731
    if not covered[-1]:
        raise ValueError(f"{name}: the last grid month {month(m[-1]):%Y-%m} is {g['status'].iloc[-1]!r}; "
                         "no continuous ledger ends there")
    i = len(m) - 1
    while i > 0 and covered[i - 1]:
        i -= 1
    table = np.flatnonzero(g["status"].isin(TABLE_STATUSES).to_numpy()[i:])
    if not len(table):
        raise ValueError(f"{name}: no filed CSD table in the run from {month(m[i]):%Y-%m}")
    start = month(m[i + table[0]])
    ev = csd_labels.load_events()
    early = ev.loc[ev["outfall_id"].map(lambda o: OUTFALLS[o].facility).eq(facility) & (ev["event_date"] < start), "event_date"]
    if len(early):
        cut = f"the grid's {month(m[i - 1]):%Y-%m} is {g['status'].iloc[i - 1]!r} and breaks the run" if i else "the grid starts after them"
        raise ValueError(f"{name}: {len(early)} filed events fall before the continuous ledger's start {start:%Y-%m} "
                         f"(the first on {early.min():%Y-%m-%d}): {cut}")
    return start, month(m[-1]) + pd.offsets.MonthEnd(0)


def ledger_start(facility: str) -> pd.Timestamp:
    """The first day of the facility's continuous ledger (Basin.facility: 'Bayside' | 'Oceanside'), derived
    from the coverage grid: 2016-10-01 and 2017-12-01 on the committed grid (tests/test_truth.py). No day
    before it is ledger_known."""
    return _continuous_ledger(facility)[0]


@functools.lru_cache(maxsize=None)
def _facility_known(facility: str) -> pd.DatetimeIndex:
    """The facility's covered days inside its continuous ledger (protocol §1's two conditions)."""
    lo, hi = _continuous_ledger(facility)
    d = pd.DatetimeIndex(csd_labels.facility_covered_dates(facility)).normalize().unique()
    return d[(d >= lo) & (d <= hi)]


@functools.lru_cache(maxsize=1)
def _archive_days() -> pd.DatetimeIndex:
    """Days the Poo Bot feed archive holds a snapshot (train_v4.archive_tables), inside its window."""
    d = pd.DatetimeIndex(train_v4.archive_tables()["covered_dates"]).normalize().unique()
    return d[(d >= ARCHIVE_START) & (d <= ARCHIVE_END)]


def _known_wide(geo: G.Geography, days: pd.DatetimeIndex) -> pd.DataFrame:
    """date × basin key → ledger_known (bool)."""
    return pd.DataFrame({b.key: days.isin(_facility_known(b.facility)) for b in geo.basins}, index=days)


def _check_events_known(geo: G.Geography) -> None:
    ev = _events(geo)
    for b in geo.basins:
        d = ev.loc[ev["basin"] == b.key, "date"]
        bad = d[~d.isin(_facility_known(b.facility))]
        if len(bad):
            raise ValueError(f"{geo.version} {b.key}: ledger events on days its facility ({b.facility}) did not file "
                             f"inside its continuous ledger: {sorted(str(x.date()) for x in bad.unique())[:5]}")


def ledger_known(geo, start=TRUTH_START, end=None) -> pd.DataFrame:
    """One row per (basin, day): date, basin, known, archive.

    ``known`` is the one coverage rule (protocol §1): D's month lies in the
    facility's continuous ledger (from ``ledger_start``) and the facility filed
    it (csd_labels' grid); the feed never makes a day known. ``archive`` marks a
    Poo Bot feed snapshot day the continuous ledger does not cover
    (X-S2-ARCHIVE, applied before X-S2-UNCOV); it is not known either. So
    Bayside's 2016 snapshot days before 2016-10 are archive, as in the served
    frames (label_source 'poobot'). See the module notes.
    """
    geo = _geo(geo)
    days = _days(start, ledger_end() if end is None else end)
    kw = _known_wide(geo, days)
    arch = days.isin(_archive_days())
    out = [pd.DataFrame({"date": days, "basin": b.key, "known": kw[b.key].to_numpy(),
                         "archive": arch & ~kw[b.key].to_numpy()}) for b in geo.basins]
    return pd.concat(out, ignore_index=True)


def _daily(ev: pd.DataFrame, unit: str) -> pd.DataFrame:
    """Per (unit, day) aggregates of the events.

    volume_mg sums the measured volumes only (protocol §1: "Σ volume_MG over
    known values"). A blank volume and a '<' bound are not measured magnitudes
    (X-S2-VOLQ), so neither enters the sum: NaN when the day has no measured one.
    """
    g = ev.assign(_measured=ev["volume_mg"].where(~ev["vol_lt"])).groupby([unit, "date"])
    return pd.DataFrame({
        "n_events": g.size(), "n_outfalls": g["outfall"].nunique(),
        "volume_mg": g["_measured"].sum(min_count=1),
        "n_vol_null": g["vol_null"].sum(), "n_vol_lt": g["vol_lt"].sum(),
        "n_observed": g["observed"].sum(),
    })


def _onset_frame(geo: G.Geography, unit: str, units: list, known: dict, days: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per (unit, day): known, y, and the day's event aggregates (zeros on a day with none)."""
    _check_events_known(geo)
    agg = _daily(_events(geo), unit)
    have = set(agg.index.get_level_values(0))
    out = []
    for u in units:
        a = agg.loc[u].reindex(days) if u in have else pd.DataFrame(np.nan, index=days, columns=agg.columns)
        f = pd.DataFrame({"date": days, unit: u, "known": np.asarray(known[u], dtype=bool)})
        for c in ("n_events", "n_outfalls", "n_vol_null", "n_vol_lt", "n_observed"):
            f[c] = a[c].fillna(0).astype(int).to_numpy()
        fired = f["n_events"] > 0
        f["volume_mg"] = np.where(fired, a["volume_mg"].astype(float).to_numpy(), 0.0)
        f.loc[~f["known"], "volume_mg"] = np.nan
        f.insert(3, "y", _nullable(fired, f["known"]))
        out.append(f)
    return pd.concat(out, ignore_index=True)


def basin_onsets(geo, start=TRUTH_START, end=None) -> pd.DataFrame:
    """S2 truth, one row per (basin, day).

    Columns: date, basin, known, archive, y (1 if ≥ 1 CIWQS event starts on D at
    an outfall of the basin; <NA> where not known), n_events, n_outfalls,
    volume_mg (Σ of the measured volumes: a blank or '<' volume is left out;
    NaN on an event day with no measured volume, and where not known; 0 on a
    known day with no event), n_vol_null, n_vol_lt, volq (an event day with a
    blank or '<' volume: X-S2-VOLQ).

    Every CIWQS row is one outfall, so a storm that fires several basins counts
    once in each through the geography; the feed's multi-basin structure strings
    are split in ``archive_onsets`` (Part B 22). Nothing is truncated or dropped.
    """
    geo = _geo(geo)
    days = _days(start, ledger_end() if end is None else end)
    kw = _known_wide(geo, days)
    f = _onset_frame(geo, "basin", list(geo.keys), {k: kw[k].to_numpy() for k in geo.keys}, days)
    arch = np.tile(days.isin(_archive_days()), len(geo.keys))
    f.insert(3, "archive", arch & ~f["known"].to_numpy())
    f["volq"] = (f["n_vol_null"] + f["n_vol_lt"]) > 0
    return f.drop(columns="n_observed")


def link_onsets(geo, start=TRUTH_START, end=None) -> pd.DataFrame:
    """One row per (link, day): date, link, basin, zone, known (the link's basin), y, n_events, n_outfalls,
    volume_mg, n_observed (events at observed-evidence outfalls). Links are internal: S3's parameters."""
    geo = _geo(geo)
    days = _days(start, ledger_end() if end is None else end)
    kw = _known_wide(geo, days)
    f = _onset_frame(geo, "link", [lk.id for lk in geo.links], {lk.id: kw[lk.basin].to_numpy() for lk in geo.links}, days)
    by = {lk.id: lk for lk in geo.links}
    f.insert(2, "basin", f["link"].map(lambda i: by[i].basin))
    f.insert(3, "zone", f["link"].map(lambda i: by[i].zone))
    return f.drop(columns=["n_vol_null", "n_vol_lt"])


def feeding_basins(geo, zone: str) -> tuple:
    """Basin keys with a link into `zone`, in the geography's order (KeyError if none: an unknown zone)."""
    geo = _geo(geo)
    keys = tuple(dict.fromkeys(lk.basin for lk in geo.links_into(zone)))
    if not keys:
        raise KeyError(f"no link of {geo.version} reaches zone {zone!r}")
    return keys


def _zone_known(geo: G.Geography, days: pd.DatetimeIndex) -> tuple[dict, dict]:
    """zone → (known on D, known on every day of D−HISTORY_DAYS…D) over `days`."""
    pad = _days(days.min() - pd.Timedelta(days=HISTORY_DAYS), days.max())
    kw = _known_wide(geo, pad)
    known, hist = {}, {}
    for z in ZONES:
        k = kw[list(feeding_basins(geo, z))].all(axis=1)
        h = k.astype(int).rolling(HISTORY_DAYS + 1, min_periods=HISTORY_DAYS + 1).min().fillna(0).astype(bool)
        known[z], hist[z] = k.loc[days].to_numpy(), h.loc[days].to_numpy()
    return known, hist


def zone_overflow(geo, start=TRUTH_START, end=None) -> pd.DataFrame:
    """S3 truth, one row per (zone, day).

    Columns: date, zone, known (every feeding basin ledger_known on D), hist_known
    (… on every day of D−7…D: X-S4-HISTUNK, X-E2E-UNCOV), y (an outfall of a link
    into the zone started an event on D; <NA> where not known), n_events,
    n_outfalls, links (fired link ids, ';'-joined), only_geography (it fired, and
    only through geography-evidence outfalls: X-S3-GEO).

    Identical under GEO_V1 and SFPUC4_V1: the zone is the union of the same
    outfalls either way (§3.3 geography invariance).
    """
    geo = _geo(geo)
    days = _days(start, ledger_end() if end is None else end)
    known, hist = _zone_known(geo, days)
    f = _onset_frame(geo, "zone", list(ZONES), known, days)
    ev = _events(geo)
    fired = ev.groupby(["zone", "date"])["link"].agg(lambda s: ";".join(dict.fromkeys(sorted(s, key=_link_rank(geo)))))
    f["links"] = [fired.get((z, d), "") for z, d in zip(f["zone"], f["date"])]
    f.insert(3, "hist_known", np.concatenate([hist[z] for z in ZONES]))
    f["only_geography"] = (f["n_events"] > 0) & (f["n_observed"] == 0)
    return f.drop(columns=["n_vol_null", "n_vol_lt", "volume_mg", "n_observed"])


def _link_rank(geo: G.Geography):
    rank = {lk.id: i for i, lk in enumerate(geo.links)}
    return lambda i: rank[i]


def carry_days(geo, level: str = "basin", start=TRUTH_START, end=None) -> pd.DataFrame:
    """One row per (unit, day) at ``level`` ('basin' | 'link' | 'zone'): date, <level>, carry, n_active.

    ``carry``: an event of the unit that started before D is still running on D
    (it ends after D's midnight) and none starts on D (X-S2-CARRY at the basin,
    X-S3-CARRY at the zone). ``n_active`` counts the earlier events still
    running. Facts only: a carry day the ledger does not cover is still a carry
    day; the exclusion order puts the coverage rule first.
    """
    geo = _geo(geo)
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}, not {level!r}")
    units = {"basin": list(geo.keys), "link": [lk.id for lk in geo.links], "zone": list(ZONES)}[level]
    days = _days(start, ledger_end() if end is None else end)
    ev = _events(geo)
    onset = set(zip(ev[level], ev["date"]))
    active: dict = {}
    for u, d0, e in zip(ev[level], ev["date"], ev["end"]):
        d = d0 + pd.Timedelta(days=1)
        while e > d:
            active[(u, d)] = active.get((u, d), 0) + 1
            d += pd.Timedelta(days=1)
    out = []
    for u in units:
        n = np.array([active.get((u, d), 0) for d in days])
        starts = np.array([(u, d) in onset for d in days])
        out.append(pd.DataFrame({"date": days, level: u, "carry": (n > 0) & ~starts, "n_active": n}))
    return pd.concat(out, ignore_index=True)


def zone_overflow_storm(geo, start=TRUTH_START, end=None) -> pd.DataFrame:
    """S3's storm-level truth (§3.3 secondary a, Part B 10), one row per (rain storm, zone).

    Columns: storm, start, end, zone, n_days, n_days_known, y (any zone overflow
    on a day of the storm's span; <NA> when none is on record and some day is
    not known), n_overflow_days. Storms come from ``storms()`` (rain, never the
    ledger), clipped to [start, end]. ``attrs['overflow_days_outside_storms']``
    counts zone overflow days in no storm span, per zone.
    """
    geo = _geo(geo)
    hi = ledger_end() if end is None else _ts(end)
    zo = zone_overflow(geo, start, hi)
    st = storms(end=min(hi, _gauge_record()[0].index.max()))
    st = st[(st["end"] >= _ts(start)) & (st["start"] <= hi)]
    rows, covered = [], {z: set() for z in ZONES}
    by_zone = {z: g.set_index("date") for z, g in zo.groupby("zone")}
    for r in st.itertuples(index=False):
        span = pd.date_range(max(r.start, _ts(start)), min(r.end, hi))
        for z in ZONES:
            g = by_zone[z].reindex(span)
            n_known = int(g["known"].fillna(False).astype(bool).sum())
            n_over = int((g["y"] == 1).sum())
            covered[z].update(span)
            y = 1 if n_over else (0 if n_known == len(span) else pd.NA)
            rows.append({"storm": r.storm, "start": r.start, "end": r.end, "zone": z, "n_days": len(span),
                         "n_days_known": n_known, "y": y, "n_overflow_days": n_over})
    out = pd.DataFrame(rows, columns=["storm", "start", "end", "zone", "n_days", "n_days_known", "y", "n_overflow_days"])
    out["y"] = out["y"].astype("Int8")
    out.attrs["overflow_days_outside_storms"] = {
        z: int(((zo["zone"] == z) & (zo["y"] == 1) & ~zo["date"].isin(covered[z])).sum()) for z in ZONES}
    return out


def archive_onsets(geo) -> pd.DataFrame:
    """The Poo Bot feed's discharge onsets (2016-03-19 → 2017-01-10) placed in the geography:
    S5's real archive feed, never truth. A structure string whose outfalls span basins becomes one row per
    basin with its own outfalls (Part B 22, train_v4.geo_onsets). Columns: date, snapshot, structure,
    outfall_ids, basin (key), zone."""
    geo = _geo(geo)
    on = train_v4.geo_onsets(train_v4.archive_tables()["onsets"], geo)
    key = {b.name: b.key for b in geo.basins}
    out = on[["date", "snapshot", "structure", "outfall_ids"]].copy()
    out["basin"] = on["basin"].map(key)
    zones = [{geo.zone_of_outfall(o) for o in s.split("|")} for s in on["outfall_ids"]]
    if any(len(z) != 1 for z in zones):
        raise ValueError("an archive onset row reaches several zones after the basin split")
    out["zone"] = [z.pop() for z in zones]
    return out.reset_index(drop=True)


# ── S4 · samples ───────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _samples(sources: tuple) -> pd.DataFrame:
    return SMP.load_samples(sources)


def zone_elevated(geo, sources=SMP.DEFAULT_SOURCES, start=TRUTH_START, end=None) -> pd.DataFrame:
    """S4 truth, one row per (zone, sampled day).

    Columns: date, zone, y (any station × analyte over the AB411 single-sample
    standard), n_stations (the zone's stations), n_stations_sampled,
    n_stations_exceeding, few (fewer than half sampled: X-S4-FEW), first_look
    (no exceedance in the zone on D−1 or D−2; False = a resample, X-S4-RESAMPLE),
    max_ratio (the highest result ÷ its standard: ≥ 10 feeds X-LEDGER-SUSPECT),
    sources, known and hist_known (the zone's feeding basins on D and on D−7…D:
    X-S4-HISTUNK), dayof (a zone overflow on D: X-S4-DAYOF).

    ``sources`` as samples.load_samples (DataSF + Poo Bot by default, what the
    served models were fit on; add 'stardb' for 2016-10 → 2020-07, design D10).
    The first-look flag is computed on the whole record before clipping, so a
    D−1 exceedance just before ``start`` still counts. ``end`` defaults to the
    last sample.
    """
    geo = _geo(geo)
    s = _samples(tuple(sources))
    z = SMP.zone_sample_days(s)
    ratio = (s["value"] / s["standard"]).groupby([s["zone"], s["date"]]).max()
    z["max_ratio"] = [ratio.get((zk, d), np.nan) for zk, d in zip(z["zone"], z["date"])]
    hi = z["date"].max() if end is None else _ts(end)
    z = z[(z["date"] >= _ts(start)) & (z["date"] <= hi)].copy()
    zo = zone_overflow(geo, start, hi).set_index(["zone", "date"])
    idx = list(zip(z["zone"], z["date"]))
    z["known"] = zo["known"].reindex(idx).to_numpy()
    z["hist_known"] = zo["hist_known"].reindex(idx).to_numpy()
    z["dayof"] = (zo["y"].reindex(idx) == 1).fillna(False).to_numpy(dtype=bool)
    z["y"] = z["any_exceedance"].astype(int)
    z["few"] = z["n_stations_sampled"] * 2 < z["n_stations"]
    cols = ["date", "zone", "y", "n_stations", "n_stations_sampled", "n_stations_exceeding", "few", "first_look",
            "max_ratio", "sources", "known", "hist_known", "dayof"]
    z = z.assign(_z=_zone_order(z["zone"])).sort_values(["_z", "date"], kind="stable")
    return z[cols].reset_index(drop=True)


# ── the posting ruler ──────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _beachwatch() -> tuple:
    import json
    import beachwatch as BW   # collectors/beachwatch.py (reads its committed CSV; fetches only on --refresh)
    man = json.loads(BW.MANIFEST.read_text())
    lo, hi = (pd.Timestamp(x) for x in man["span"])
    return BW.load_posted_zone_days(), lo, hi


def postings_span() -> tuple[pd.Timestamp, pd.Timestamp]:
    """(first, last) day BeachWatch's SF filings cover. After the last: X-PL-END (2026-02-28 at the 2026-09-26 pull)."""
    _, lo, hi = _beachwatch()
    return lo, hi


def postings() -> pd.DataFrame:
    """BeachWatch zone-days: zone, date, cause_class ('cso' | 'rain' | 'other'), n_advisories, stations,
    onset (the zone was not posted the day before), class_onset (the day's cause class is new: a CSO posting
    that starts while another cause is up counts). The second ruler, never truth; ``postings_span`` bounds it."""
    pz, lo, hi = _beachwatch()
    pz = pz.copy()
    pz["date"] = pd.to_datetime(pz["date"]).dt.normalize()
    if pz.duplicated(["zone", "date"]).any():
        raise ValueError("sf_posted_zone_days.csv repeats a zone-day")
    if pz["date"].max() > hi:
        raise ValueError(f"postings after the manifest span end {hi.date()}")
    pz = pz.assign(_z=_zone_order(pz["zone"])).sort_values(["_z", "date"], kind="stable").drop(columns="_z")
    prev = {(z, d) for z, d in zip(pz["zone"], pz["date"])}
    cls = {(z, d): c for z, d, c in zip(pz["zone"], pz["date"], pz["cause_class"])}
    one = pd.Timedelta(days=1)
    pz["onset"] = [(z, d - one) not in prev for z, d in zip(pz["zone"], pz["date"])]
    pz["class_onset"] = [cls.get((z, d - one)) != c for z, d, c in zip(pz["zone"], pz["date"], pz["cause_class"])]
    return pz.reset_index(drop=True)


# ── OUT · the public claim ─────────────────────────────────────────────────

# why → (label, plain words). The order is the decision order in out_label.
OUT_WHY = {
    "overflow": ("bad", "the zone overflowed that day (x(0) = 1 by policy)"),
    "sample_after_overflow": ("bad", "a sample over standard 1–7 days after a zone overflow"),
    "clean_sample": ("good", "every sample that day was within standard"),
    "exceedance_no_overflow": ("good", "over standard with no overflow on D−7…D and the history known: a negative of the claim (Part B 3)"),
    "quiet": ("good", "nobody sampled, no overflow on D−7…D, and the history known"),
    "tail_unsampled": ("unknown", "nobody sampled in the 7 days after an overflow (X-E2E-UNK)"),
    "uncovered": ("unknown", "the ledger does not cover the day or the week before it (X-E2E-UNCOV)"),
}


def out_label(geo, sources=SMP.DEFAULT_SOURCES, start=TRUTH_START, end=None) -> pd.DataFrame:
    """The OUT label (§3.6, Part B 3; STAGES_PROTOCOL.md §1), one row per (zone, day).

    Columns: date, zone, label ('bad' | 'good' | 'unknown'), y (1 bad, 0 good,
    <NA> unknown), why (a key of OUT_WHY), overflow (the zone overflowed on D;
    <NA> where not known), tail (a zone overflow on record on D−7…D−1), sampled,
    exceed, known, hist_known (zone_overflow's).

        bad      a zone overflow day; or a sample over standard on D with an
                 overflow 1–7 days before
        good     a clean sample; an exceedance with no overflow on D−7…D and the
                 history known (the claim is "affected by a sewer overflow", so
                 dry-weather and runoff exceedances are its negatives, never
                 excluded); a quiet unsampled day with the history known
        unknown  an unsampled day in the 7 after an overflow, or a day whose
                 week the ledger does not cover

    The label says what is true. Whether a row is scored is the exclusion rules'
    call: X-E2E-UNCOV reads ``known`` / ``hist_known`` (a clean sample on a day
    the ledger does not cover is good, and is still left out of the score).
    For GEO_V1 it is scorecard.combined_label read on the ledger, with Part B 3's
    change; tests/test_truth.py pins every difference with its reason.
    """
    geo = _geo(geo)
    lo, hi = _ts(start), ledger_end() if end is None else _ts(end)
    zo = zone_overflow(geo, lo - pd.Timedelta(days=TAIL_DAYS), hi)
    el = zone_elevated(geo, sources, lo, hi)
    out = []
    for z in ZONES:
        g = zo[zo["zone"] == z].set_index("date")
        on = (g["y"] == 1).fillna(False).astype(bool)
        tail = on.astype(int).shift(1).rolling(TAIL_DAYS, min_periods=1).max().fillna(0).astype(bool)
        g, on, tail = g.loc[lo:hi], on.loc[lo:hi].to_numpy(), tail.loc[lo:hi].to_numpy()
        e = el[el["zone"] == z].set_index("date")["y"].reindex(g.index)
        sampled = e.notna().to_numpy()
        exceed = (e == 1).fillna(False).to_numpy(dtype=bool)
        hk = g["hist_known"].to_numpy(dtype=bool)
        why = np.select(
            [on, exceed & tail, sampled & ~exceed, exceed & hk, exceed, tail, hk],
            ["overflow", "sample_after_overflow", "clean_sample", "exceedance_no_overflow", "uncovered",
             "tail_unsampled", "quiet"], default="uncovered")
        out.append(pd.DataFrame({"date": g.index, "zone": z, "label": [OUT_WHY[w][0] for w in why], "why": why,
                                 "overflow": g["y"].array, "tail": tail, "sampled": sampled, "exceed": exceed,
                                 "known": g["known"].to_numpy(dtype=bool), "hist_known": hk}))
    f = pd.concat(out, ignore_index=True)
    f.insert(3, "y", _nullable(f["label"] == "bad", f["label"] != "unknown"))
    return f
