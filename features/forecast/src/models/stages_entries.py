#!/usr/bin/env python3
"""The entries: where the chain starts, as the input frames S2 is fed (P7b).

STAGES_DESIGN.md Part C §3.0 "Entry points", §3.1 (S1's output), §3.2 (S2's
oracle and chained inputs), §6 items 8 (``history_full_v1``, a candidate
input rule), 10 (never a missing hour as a dry one) and 11 (the Previous Runs
leads); STAGES_PROTOCOL.md §3 (Entries) and §7 (S1's rules, X-S1-NWPGAP
before X-S1-NOLEAD in first-match order).

A stage is scored on its true input (oracle) and on what the stage before it
really produced (chained). S2 reads 19 numbers a day: 16 daily rain features
(rain_features.DAILY_FEATURES) and the day's peak 1/3/6-hour rain
(INTENSITY_FEATURES), and a set may read the south wind on the rainy hours
too (rain_features.WIND_FEATURES, after the 19: ERA5's wind on ERA5's rain for
rain known; lead L's archived wind on lead L's own hours of D, as served
included, for a lead entry). Each entry is one way of filling them for day D,
with I = D − L the issue day of lead L:

  entry       days before I                    days I … D                      D's peak hours
  oracle,     the gauges, every day (no        —                               ERA5, as training
  rain        forecast): the training frame
  L0 … L5     the gauges, the whole record     the forecast issued on I: day   that lead-L
              behind them                      d at lead d − I (lead 0 = the   forecast's hours
                                               stitched short lead, tagged
                                               optimistic; 1–5 = Previous Runs)
  L0s, L1s    as L0 / L1, but the history features read only the 7 gauge days the live page holds
              (under the history_full_v1 candidate rule, L0 / L1 exactly)

``oracle`` and ``rain`` are one input (rain known; protocol §3: the S2
oracle is the rain that fell). The gauge days are train_v4.rain_series —
outage-masked on every day by ``gauge_outage_v1`` (the default
``input_rules``), a missing gauge filled from the other, exactly as the
training frames. rain_series' last fill, 0 for a day neither gauge read,
never fires inside the span; a refresh that brings such a day raises.
``input_rules=()`` gives the raw record the served weights were fit on, for
fidelity checks only.

**The gauges as the issue day knew them.** A lead entry is a forecast issued
on I, so its gauge days are the record through I − 1 as it stood then
(protocol §3: no predictor uses what was unknown at issue time). The
whole-record mask decides a 0.00 run with the other gauge's rain over the
whole run, days after I included: on 64 issue days 2022-11-17 → 2026-08-17
(19 in the lead-1 span, 2026-01-30 → 02-11 among them) it hides dead-gauge
zeros that the gauges through I − 1 could not yet call an outage (a run
shorter than 2 days, or < 0.5" at the other gauge so far), and the lead-1
frames change on 4 days (2026-02-10 → 12 and 04-15, a 7-day sum by up to
0.61"). On those issue days the lead entries read the record through I − 1
masked on its own (``issue_time_unmasked`` lists the gauge-days;
tests/test_stages_entries.py checks them against train_v4.rain_series run on
the record cut at I − 1). Every other issue day reads the training frame's
gauges. Rain known reads the whole record,
with hindsight, as training did. Every lead entry, as served included, reads
these same gauges, so a served-vs-candidate comparison at one entry has
identical inputs in both arms (Part B 16).

**rain** equals train_v4.build_dataset(..., input_rules=['gauge_outage_v1'])
column for column on every shared day (tests/test_stages_entries.py): the
same features through the same code (train_v4.rain_features,
build_hourly_features), the same names, order and dtypes.

**Lead L** puts the forecast where the gauges would be: the composite series
is the gauge record through I − 1 (as I knew it) and then, for each day d of I … D, the
daily total archived at lead d − I. D's peak hours come from the lead-L
archive's own hours (the windows run across midnight, as training's ERA5
peaks do, so they read D − 1's last 5 hours at lead L too). A forecast value
is used only when it is complete: a model-day with fewer than 24 archived
hours drops the row (X-S1-NWPGAP), and so does a peak window holding a NaN
hour; a (model, lead) the archive does not reach on a day the row needs drops
it as X-S1-NOLEAD. In first-match order NWPGAP wins when a row has both.
Nothing is ever zero-filled.

**As served (L0s, L1s)** is what the page computes: its Open-Meteo frame holds
7 past days (METEO_PARAMS past_days=7) and the 6 forecast days, so on each
refresh it runs the shared add_daily_features over 13 rows, I − 7 … I + 5.
Seven past days truncate the four history features — the 14- and 30-day
sums, antecedent moisture (its weights renormalised over the days held) and
the dry spell (capped at the frame) — the known train/serve skew. Every other
feature reads at most D − 7, which that frame holds, so it is the same in
every lead entry: it is taken from the issue day's 13-row frame for all of
them, and as served differs from lead L in HISTORY_FEATURES only, bit for bit
(a test).

**history_full_v1** (``history='full'``; design §6 item 8) is the candidate
rule that ends the skew. The frame reaches FULL_PAST_DAYS = 35 days back, so
the 14- and 30-day sums and antecedent moisture read whole windows, and the
dry spell is a carried count: when the frame is dry from its first day to D,
the count adds the dry spell the gauges through I − 1 held on the frame's eve
(I − 36), exactly as lead L carries it. A frame alone cannot do this: dry
spells run to 188 days on the record (2019-11-25, as of 2026-08-17), and the
design's 45-day ACIS window would still cap 192 of L1's 936 avg rows (the
35-day frame capped 232). The carried count is one number the page can know
at issue time: it is read off the gauges through I − 1, masked as the record
through I − 1 masks them (``_issue_records``; no committed issue day needs
that override, so a test makes an outage run uncallable and sees the carry
follow it). The page must recompute it on each refresh from a gauge record
that reaches past the spell's last wet day and the first day of any outage
run touching the spell or the frame: up to 167 days before the issue day on
the rows that exist (L0 issued 2023-09-26: its spell's last wet day,
2023-05-06, lies inside the Oceanside run from 2023-04-12; for SF Oceanside a
record starting at that wet day reads the spell as 144 days, not 143), as of
2026-08-17, and never carry it over from an earlier refresh, whose record may
not yet have masked an outage that the record through I − 1 does. So L0s / L1s under
history_full_v1 ARE L0 / L1, every row, every column, bit for bit (a test):
no row needs what the page could not know at issue time. Its near features
stay the 13-row frame's, as in every lead entry, and the page must keep them
so: run once over the longer frame, pandas' rolling sums round by the longer
history, and wet_prior_3d (a 3-day sum above 0.1") flips to 1 on an exact
0.10" sum, where the 13-row frame reads 0 (SF Downtown on 2025-02-10 and
2026-01-31, at L0 and L1, every weather model, as of 2026-08-17; a test).

Not emulated in any lead entry, so that L and Ls read the same inputs: the
live page's KSFO hours for today; its outage mask, which runs on
the 7 ACIS days it fetches rather than the record through I − 1 (the two
masks differ on 119 of the 1,370 issue days 2022-11-17 → 2026-08-17, inside
long outages whose start the 7 days cannot see; the whole-record mask differs
from the page's on 183); and Open-Meteo's local day (the archive's day is a
fixed UTC−7, openmeteo_previous_runs).

Every lead entry runs off one table per (model, rain source, input rules):
for each issue day, the two frames (7 and 35 past days) through the shared
add_daily_features — one call per frame gives all six leads, the frame
holding I … I + 5 — cached in memory for the process. Nothing is written to
disk. The frames end at the data end (``data_end``: the last day ERA5 and
both gauges cover, train_v4._inputs_reach) and start at the training span
(2016-03-01); asking past either raises.

API (the build agent and the S2 engine call these):
    frames(entry, sources, model, start, end, input_rules, history) -> {source: DataFrame}
    drops(entry, model, start, end) -> DataFrame[date, reason, lead, day, what]
    spans(model, start, end) -> {entry: {first, last, n_days, n_dropped}}
    nwp_hours(model) -> DataFrame[date, lead, n_hours]   (exclusions.Context.nwp)
    model_days(model, lead), model_peaks(model, lead, reach)   (S1's forecast columns)
    issue_time_unmasked(input_rules) -> DataFrame[issue, date, gauge]   (what the issue day could not yet mask)
    served_weather_model() -> str   (METEO_PARAMS['models']; the default ``model``, 'icon_seamless' today)
    data_end() -> Timestamp
"""
from __future__ import annotations

import functools
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, FORECAST, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import exclusions as X  # noqa: E402  (the entry ids, the as-served window, the S1 rule ids)
import rain_features as RF  # noqa: E402  (the one feature implementation, training's and the page's)
import train_v4 as T4  # noqa: E402  (the gauge series, the training frame's features, its span)
import truth as T  # noqa: E402  (which gauge days were read at all)
from src.collectors import openmeteo_previous_runs as OMP  # noqa: E402  (the archives and the complete-day rule)

ENTRIES = X.ENTRIES                                    # oracle, rain, L0 … L5, L0s, L1s (protocol §3)
MODELS = tuple(OMP.MODELS)                             # icon_seamless, ecmwf_ifs025, gfs_seamless
LEADS = OMP.LEADS                                      # 0 (short lead, optimistic), 1 … 5
FEATURES = list(RF.DAILY_FEATURES) + list(RF.INTENSITY_FEATURES)   # the 19, build_dataset's order
WIND = list(RF.WIND_FEATURES)                          # after the 19: read only by a set whose features name them
COLUMNS = FEATURES + WIND                              # every entry frame's inputs
HISTORY_FEATURES = ("rain_14d_cum", "rain_30d_cum", "antecedent_moisture", "dry_spell_days")
INT_FEATURES = ("wet_prior_3d", "dry_spell_days")      # int64 in the training frames
SERVED_PAST_DAYS = X.AS_SERVED_DAYS                    # 7: METEO_PARAMS past_days (a test reads live_dashboard)
FULL_PAST_DAYS = 35                                    # history_full_v1: Open-Meteo past_days 35 (design §6 item 8)
HISTORY = {"served": SERVED_PAST_DAYS, "full": FULL_PAST_DAYS}
FC_DAYS = len(LEADS)                                   # the page's 6 forecast days: I … I + 5
PEAK_REACH_HOURS = 5                                   # the 6-hour peak window reaches 5 hours before D's midnight
START = T4.TRAIN_START                                 # 2016-03-01: build_dataset's first day
NWPGAP, NOLEAD = "X-S1-NWPGAP", "X-S1-NOLEAD"
OK, GAP, ABSENT = 0, 1, 2                              # a model-day's status: complete, inside the archive but short, outside it
_DAY = pd.Timedelta(days=1)


# ── ids ─────────────────────────────────────────────────────────────────────

def lead_of(entry: str) -> int | None:
    """The entry's lead (0 … 5), None for oracle and rain; KeyError on an id protocol §3 lacks."""
    if entry not in ENTRIES:
        raise KeyError(f"unknown entry {entry!r}; known: {ENTRIES}")
    return X._lead_of(entry)


def as_served(entry: str) -> bool:
    lead_of(entry)
    return entry.endswith("s")


def _model(model: str) -> str:
    if model not in MODELS:
        raise KeyError(f"unknown weather model {model!r}; the archive holds {MODELS}")
    return model


def _lead(lead) -> int:
    if lead not in LEADS:
        raise KeyError(f"unknown lead {lead!r}; the archive holds {LEADS}")
    return int(lead)


LIVE_DASHBOARD = FORECAST / "live_dashboard.py"


def served_weather_model() -> str:
    """The weather model the live forecast reads: METEO_PARAMS["models"] in live_dashboard.py, parsed from
    its source as stages_flowchart does (importing it would load the whole model stack). The lead entries'
    default model; tests/test_stages_s1.py fails if the two ever part. Unreadable or not archived: raise."""
    src = LIVE_DASHBOARD.read_text()
    m = re.search(r'^METEO_PARAMS\s*=\s*\{[^}]*?"models":\s*"([a-z0-9_]+)"', src, re.M | re.S)
    if not m:
        raise ValueError(f"{LIVE_DASHBOARD.name}: METEO_PARAMS names no weather model")
    return _model(m.group(1))


def _rules(input_rules) -> tuple:
    rules = tuple(input_rules or ())
    bad = [r for r in rules if r != RF.GAUGE_OUTAGE_RULE["name"]]
    if bad:
        raise KeyError(f"unknown input rules {bad}; known: {[RF.GAUGE_OUTAGE_RULE['name']]}")
    return rules


def _sources(sources) -> list:
    if isinstance(sources, str):
        sources = [sources]
    src = list(sources)
    if not src:
        raise ValueError("sources: at least one rain source")
    bad = [s for s in src if s not in T4.RAIN_SOURCES]
    if bad:
        raise KeyError(f"unknown rain sources {bad}; known: {T4.RAIN_SOURCES}")
    if len(set(src)) != len(src):
        raise ValueError(f"sources repeat: {src}")
    return src


# ── the span ────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def data_end() -> pd.Timestamp:
    """The last day the rain frames cover: ERA5's last complete day and the last day both gauges filed
    (train_v4._inputs_reach, the reach --rescore scores through), and no later than ERA5's wind. 2026-08-17 at
    the freeze."""
    reach = T4._inputs_reach()
    wind = pd.read_csv(T4.HOURLY_WIND_CSV, usecols=["timestamp"], parse_dates=["timestamp"])["timestamp"].max()
    wind_end = wind.normalize() if wind.hour == 23 else wind.normalize() - _DAY
    return min(reach["hourly_rain"], reach["daily_rain"], wind_end)


def _span(start, end) -> pd.DatetimeIndex:
    lo = START if start is None else pd.Timestamp(start).normalize()
    hi = data_end() if end is None else pd.Timestamp(end).normalize()
    if lo < START:
        raise ValueError(f"start {lo.date()} is before the training span's first day {START.date()}")
    if hi > data_end():
        raise ValueError(f"end {hi.date()} is past the data end {data_end().date()} (ERA5 and both gauges)")
    if lo > hi:
        raise ValueError(f"start {lo.date()} is after end {hi.date()}")
    return pd.date_range(lo, hi, name="date")


# ── the weather model's archive ─────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _hourly(model: str, lead: int) -> tuple:
    """(hourly inches on a gap-free grid, inside: the grid hour lies between the first and last archived hour).

    An hour the file lacks is NaN on the grid, never 0; the grid runs a day past the archive at each end."""
    h = OMP.load_hourly(_model(model), int(lead))
    if h["timestamp"].duplicated().any():
        raise ValueError(f"{model} lead {lead}: the archive repeats an hour")
    s = h.set_index("timestamp")["precip_inches"].astype(float)
    ok = s.index[s.notna()]
    if not len(ok):
        raise ValueError(f"{model} lead {lead}: no archived hour")
    lo, hi = ok.min(), ok.max()
    grid = pd.date_range(lo.normalize() - _DAY, hi.normalize() + 2 * _DAY - pd.Timedelta(hours=1), freq="h", name="timestamp")
    return s.reindex(grid), pd.Series((grid >= lo) & (grid <= hi), index=grid)


@functools.lru_cache(maxsize=None)
def _days(model: str, lead: int) -> pd.DataFrame:
    """date → total (inches; NaN unless all 24 hours are archived), n_hours, status (OK | GAP | ABSENT).

    OMP.daily_totals is the complete-day rule; a day inside the archive's span (OMP.archive_span) with
    fewer than 24 hours is a gap, a day outside it is not in the archive."""
    h = OMP.load_hourly(model, int(lead))
    lo, hi = OMP.archive_span(h)
    d = OMP.daily_totals(h)
    days = pd.date_range(lo, hi, name="date")
    d = d.reindex(days)
    d["hours"] = d["hours"].fillna(0).astype(int)                     # a day the file lacks: 0 archived hours
    d["status"] = np.where(d["hours"] >= OMP.HOURS_PER_DAY, OK, GAP)
    if d.loc[d["status"] == OK, "total"].isna().any() or d.loc[d["status"] == GAP, "total"].notna().any():
        raise AssertionError(f"{model} lead {lead}: complete days and totals disagree")
    return d


def _status(model: str, lead: int, dates) -> np.ndarray:
    """OK / GAP / ABSENT for each date at (model, lead)."""
    st = _days(model, lead)["status"].reindex(pd.DatetimeIndex(dates))
    return st.fillna(ABSENT).to_numpy(dtype=int)


def _total(model: str, lead: int, dates) -> np.ndarray:
    return _days(model, lead)["total"].reindex(pd.DatetimeIndex(dates)).to_numpy(dtype=float)


@functools.lru_cache(maxsize=None)
def _peaks(model: str, lead: int, reach: int = PEAK_REACH_HOURS) -> pd.DataFrame:
    """date → rain_max1h/3h/6h of the lead's hours (rain_features.hourly_intensity) and peak_status: OK when
    every hour of [D 00:00 − reach h, D 23:00] is archived, GAP when one inside the archive is NaN, else
    ABSENT. hourly_intensity reads a NaN hour as 0, so only OK days' peaks are ever used."""
    s, inside = _hourly(model, lead)
    win = OMP.HOURS_PER_DAY + reach
    gap = (inside & s.isna()).astype(int).rolling(win, min_periods=1).sum()
    out = (~inside).astype(int).rolling(win, min_periods=win).sum()   # a window off the grid's start is outside
    last = s.index[s.index.hour == 23]
    st = np.where(gap.loc[last].to_numpy() > 0, GAP, np.where(out.loc[last].fillna(1).to_numpy() > 0, ABSENT, OK))
    pk = RF.hourly_intensity(pd.DataFrame({"timestamp": s.index, "precip_inches": s.to_numpy()})).set_index("date")
    pk = pk.reindex(last.normalize())
    pk["peak_status"] = st
    pk.index.name = "date"
    return pk


def model_days(model: str, lead: int) -> pd.DataFrame:
    """DataFrame[date, total, n_hours, complete] over the (model, lead) archive's span (OMP.archive_span):
    the daily total in inches, NaN unless all 24 hours are archived. Days outside are not in the archive."""
    d = _days(_model(model), _lead(lead))
    return pd.DataFrame({"date": d.index, "total": d["total"].to_numpy(), "n_hours": d["hours"].to_numpy(),
                         "complete": (d["status"] == OK).to_numpy()})


def model_peaks(model: str, lead: int, reach: int = PEAK_REACH_HOURS) -> pd.DataFrame:
    """DataFrame[date, rain_max1h, rain_max3h, rain_max6h, complete]: each day's peak hours at (model, lead);
    ``complete`` when every hour from ``reach`` hours before the day's midnight to its last is archived
    (reach 0 for the 1-hour peak, 2 for the 3-hour, 5 for the 6-hour). Use a peak only where complete."""
    if not 0 <= int(reach) <= PEAK_REACH_HOURS:
        raise ValueError(f"reach {reach}: 0 … {PEAK_REACH_HOURS} hours")
    p = _peaks(_model(model), _lead(lead), int(reach))
    out = p[list(RF.INTENSITY_FEATURES)].reset_index()
    out["complete"] = (p["peak_status"] == OK).to_numpy()
    return out


def nwp_hours(model: str, leads=LEADS) -> pd.DataFrame:
    """DataFrame[date, lead, n_hours]: the archived hours of every day inside each lead's span, the
    weather-model input exclusions.Context.nwp takes (X-S1-NWPGAP: < 24 hours; a (date, lead) absent
    here: X-S1-NOLEAD)."""
    parts = []
    for L in leads:
        d = _days(_model(model), _lead(L))
        parts.append(pd.DataFrame({"date": d.index, "lead": int(L), "n_hours": d["hours"].to_numpy()}))
    return pd.concat(parts, ignore_index=True)


# ── row availability (no rain needed) ───────────────────────────────────────

def _row_status(model: str, lead: int, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Per date: reason ('' | X-S1-NWPGAP | X-S1-NOLEAD) and the first model-day behind it (lead, day, what).

    A lead-L row needs the daily total of every day I + j at lead j (j = 0 … L) and D's peak window at lead L."""
    n = len(dates)
    issue = dates - lead * _DAY
    need = [(j, issue + j * _DAY, _status(model, j, issue + j * _DAY), "daily total") for j in range(lead + 1)]
    pk = _peaks(model, lead)["peak_status"].reindex(dates).fillna(ABSENT).to_numpy(dtype=int)
    need.append((lead, dates - _DAY, pk, "peak hours"))
    reason = np.full(n, "", dtype=object)
    lead_c, day_c, what_c = np.full(n, -1), np.full(n, np.datetime64("NaT", "us")), np.full(n, "", dtype=object)
    for code, rule in ((GAP, NWPGAP), (ABSENT, NOLEAD)):              # protocol §7: NWPGAP before NOLEAD
        for j, days, st, what in need:
            hit = (reason == "") & (st == code)
            reason[hit], lead_c[hit], what_c[hit] = rule, j, what
            day_c[hit] = pd.DatetimeIndex(days)[hit].to_numpy(dtype="datetime64[us]")
    return pd.DataFrame({"date": dates, "reason": reason, "lead": lead_c, "day": day_c, "what": what_c})


def drops(entry: str, model: str = "icon_seamless", start=None, end=None) -> pd.DataFrame:
    """The days of [start, end] where ``entry`` has no row, and why: DataFrame[date, reason, lead, day, what].

    reason is X-S1-NWPGAP or X-S1-NOLEAD; (lead, day, what) name the first model-day behind it ('daily
    total' at lead j for day I + j, or 'peak hours' at lead L, the window that starts on ``day`` = D − 1).
    The rain-known entries drop nothing inside the span. Independent of the rain source and rules."""
    L = lead_of(entry)
    days = _span(start, end)
    if L is None:
        return pd.DataFrame({"date": pd.DatetimeIndex([], dtype="datetime64[us]"), "reason": pd.Series([], dtype=object),
                             "lead": pd.Series([], dtype=int), "day": pd.DatetimeIndex([], dtype="datetime64[us]"),
                             "what": pd.Series([], dtype=object)})
    st = _row_status(_model(model), L, days)
    return st[st["reason"] != ""].reset_index(drop=True)


def _runs(dates) -> list:
    """Consecutive days as [[first, last], …] (ISO dates): a compact listing for attrs."""
    d = pd.DatetimeIndex(sorted(dates))
    if not len(d):
        return []
    brk = np.flatnonzero(np.diff(d.to_numpy()).astype("timedelta64[D]").astype(int) != 1)
    starts, ends = np.r_[0, brk + 1], np.r_[brk, len(d) - 1]
    return [[str(d[a].date()), str(d[b].date())] for a, b in zip(starts, ends)]


def spans(model: str = "icon_seamless", start=None, end=None) -> dict:
    """{entry: {first, last, n_days, n_dropped}}: the first and last day each entry has a row for
    ``model`` over [start, end] (default the whole span), and how many days inside it it drops."""
    days = _span(start, end)
    out = {}
    for e in ENTRIES:
        dr = drops(e, model, days[0], days[-1])
        have = days.difference(pd.DatetimeIndex(dr["date"]))
        first, last = (have.min(), have.max()) if len(have) else (None, None)
        inner = int(((dr["date"] > first) & (dr["date"] < last)).sum()) if first is not None else 0
        out[e] = {"first": str(first.date()) if first is not None else None, "last": str(last.date()) if last is not None else None,
                  "n_days": int(len(have)), "n_dropped": inner}
    return out


# ── the gauges ──────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _gauge(source: str, rules: tuple) -> pd.Series:
    """The source's daily gauge rain as training reads it (train_v4.rain_series), on a gap-free day index.

    rain_series ends by filling a day neither gauge recorded with 0; the span must hold no such day (none
    does, 2016-01 → the data end), so a refresh that brings one raises here instead of reading it as dry."""
    s, _ = T4.rain_series(source, list(rules) or None)
    s = s.astype(float)
    full = pd.date_range(s.index.min(), s.index.max())
    if len(full) != len(s) or not (s.index == full).all():
        raise ValueError(f"the gauge record for {source!r} skips days: training's rolling sums would cross them")
    if s.isna().any():
        raise AssertionError(f"rain_series({source!r}) returned NaN")
    g = T.gauges(s.index.min(), data_end())
    usable = g["status"].eq("ok") if rules else g["raw"].notna()          # masked days count as unread under the rule
    read = usable.groupby(g["date"]).any()
    if not read.all():
        raise ValueError(f"no gauge reading on {[str(d.date()) for d in read.index[~read]][:5]}: rain_series would read 0")
    return s


@functools.lru_cache(maxsize=None)
def _training_features(source: str, rules: tuple) -> pd.DataFrame:
    """train_v4.rain_features over the whole record, indexed by date (the rain entry's daily features)."""
    return T4.rain_features(source, list(rules) or None).set_index("date")


@functools.lru_cache(maxsize=1)
def _era5_peaks() -> pd.DataFrame:
    return T4.build_hourly_features()


@functools.lru_cache(maxsize=1)
def _era5_wind() -> pd.DataFrame:
    return T4.wind_features()


@functools.lru_cache(maxsize=None)
def _wind(model: str, lead: int) -> pd.Series:
    """date → wind_v_rain at (model, lead): the lead's own archived hours of rain weighing its archived wind
    (rain_features.wind_rain_features), on the archive's fixed UTC−7 day. The forecast issued on I says how the
    wind will blow while it rains on D; NaN where a rainy hour has no archived wind (a row needing it raises)."""
    rain = OMP.load_hourly(model, int(lead))[["timestamp", "precip_inches"]]
    h = rain.merge(OMP.load_wind(model, int(lead)), on="timestamp", how="left")
    return RF.wind_rain_features(h).set_index("date")["wind_v_rain"]


# ── the gauges as each issue day knew them ──────────────────────────────────

@functools.lru_cache(maxsize=None)
def _hindsight(rules: tuple) -> pd.DataFrame:
    """DataFrame[issue, date, gauge]: the gauge-days the whole-record mask hides that the record through
    issue − 1 does not. Only a run reaching I − 1 can differ (a run that ends sooner reads the same in
    both), and only by being too young or too dry to qualify yet: its days s … I − 1 then stay as filed."""
    out = pd.DataFrame({"issue": pd.DatetimeIndex([]), "date": pd.DatetimeIndex([]), "gauge": pd.Series([], dtype=object)})
    if RF.GAUGE_OUTAGE_RULE["name"] not in rules:
        return out
    raw = _per_gauge()[0]
    rows = []
    for r in T.outage_runs():                                    # the whole record's runs, the rule's own reading
        s, e = pd.Timestamp(r["start"]), pd.Timestamp(r["end"])
        lo = max(raw.index.min(), s - _DAY)                      # the day before the run: its start reads as on the whole record
        for I in pd.date_range(s + _DAY, e + _DAY):
            seen = RF.find_gauge_outages(raw.loc[lo:I - _DAY].reset_index(), gauges=T.GAUGE_SERIES)
            if not any(x["gauge"] == r["gauge"] and pd.Timestamp(x["start"]) == s for x in seen):
                rows += [(I, d, r["gauge"]) for d in pd.date_range(s, I - _DAY)]
    return pd.DataFrame(rows, columns=list(out.columns)) if rows else out


def issue_time_unmasked(input_rules=(RF.GAUGE_OUTAGE_RULE["name"],)) -> pd.DataFrame:
    """DataFrame[issue, date, gauge]: for each issue day I, the gauge-days (date ≤ I − 1) the whole-record
    gauge_outage_v1 mask hides but the gauges through I − 1 cannot yet call an outage. The lead entries
    read them as filed (dead-gauge 0.00s); rain known reads them masked. Empty with no rules."""
    return _hindsight(_rules(input_rules)).copy()


@functools.lru_cache(maxsize=1)
def _per_gauge() -> tuple:
    """(raw, masked): date × gauge as filed, and with the whole-record gauge_outage_v1 days blanked
    (truth.gauges' status; a missing day is NaN in both). rain_series' reading, before the fills."""
    g = T.gauges()
    g = g[g["series"].isin(T.GAUGE_SERIES)]
    raw = g.pivot(index="date", columns="series", values="raw")[list(T.GAUGE_SERIES)]
    status = g.pivot(index="date", columns="series", values="status")[list(T.GAUGE_SERIES)]
    return raw, raw.where(status != "outage")


def _combine(vals: pd.DataFrame, source: str) -> pd.Series:
    """train_v4.rain_series' fills: the mean of the gauges read, or the source gauge falling back to the
    other; then 0. _issue_records checks it reproduces rain_series on the whole record, exactly."""
    if source == T.MEAN_SERIES:
        return vals[list(T.GAUGE_SERIES)].mean(axis=1).fillna(0.0)
    other = [g for g in T.GAUGE_SERIES if g != source]
    if len(other) != 1:
        raise KeyError(f"rain source {source!r} is neither a gauge nor their mean")
    return vals[source].fillna(vals[other[0]]).fillna(0.0)


@functools.lru_cache(maxsize=None)
def _issue_records(source: str, rules: tuple) -> dict:
    """{I: (the source's gauge series as the record through I − 1 reads it, its dry spell on the 35-day
    frame's eve)} for every issue day ``_hindsight`` names. The series spans the whole index; days from I
    on are never read."""
    cells = _hindsight(rules)
    if not len(cells):
        return {}
    whole = _gauge(source, rules)
    raw, masked = _per_gauge()
    if not np.array_equal(_combine(masked, source).reindex(whole.index).to_numpy(), whole.to_numpy()):
        raise AssertionError(f"the per-gauge record no longer reproduces train_v4.rain_series({source!r})")
    out = {}
    for I, c in cells.groupby("issue"):
        vals = masked.copy()
        for d, g in zip(c["date"], c["gauge"]):
            vals.at[d, g] = raw.at[d, g]
        series = _combine(vals, source).reindex(whole.index)
        eve = I - (FULL_PAST_DAYS + 1) * _DAY
        dry = RF.add_daily_features(pd.DataFrame({"precip_inches": series.loc[:eve].to_numpy()}))["dry_spell_days"].iloc[-1]
        out[pd.Timestamp(I)] = (series, float(dry))
    return out


# ── the lead tables (one per model × source × rules) ───────────────────────

def _issue_features(gauge: pd.Series, fc: np.ndarray, issues: pd.DatetimeIndex, past: int) -> np.ndarray:
    """[issue, lead, DAILY_FEATURES] from the frame I − past … I + 5 of each issue day: the gauges, then the
    forecast day I + j at lead j (fc[issue, j]; NaN where that model-day is not complete). One call of the
    shared add_daily_features per frame, exactly as the page runs it. A NaN forecast day only reaches the
    rows from that day on, which the row status drops; add_daily_features would read it as 0, so callers
    never keep such a row."""
    g = gauge.to_numpy(dtype=float)
    pos = gauge.index.get_indexer(issues)
    if (pos < past).any():
        raise ValueError(f"an issue day has fewer than {past} gauge days behind it (first {issues[pos < past][0].date()})")
    out = np.empty((len(issues), FC_DAYS, len(RF.DAILY_FEATURES)))
    for k, p in enumerate(pos):
        frame = pd.DataFrame({"precip_inches": np.concatenate([g[p - past:p], fc[k]])})
        out[k] = RF.add_daily_features(frame)[RF.DAILY_FEATURES].to_numpy(dtype=float)[past:]
    return out


@functools.lru_cache(maxsize=None)
def _lead_tables(model: str, source: str, rules: tuple) -> dict:
    """{issues, near (13-row frame), full (35-day frame), dry_before}: every lead entry's features for every
    issue day whose lead-0 day is complete (no other issue day can give a row), each read from the gauges
    as that issue day knew them (``_issue_records`` where the whole-record mask would differ)."""
    gauge = _gauge(source, rules)
    st0 = _days(model, 0)
    issues = st0.index[(st0["status"] == OK).to_numpy()]
    issues = issues[(issues <= data_end()) & (issues - (FULL_PAST_DAYS + 1) * _DAY >= gauge.index.min())]
    if (issues - _DAY > gauge.index.max()).any():
        raise ValueError(f"the gauge record ends {gauge.index.max().date()}, before an issue day's eve")
    fc = np.column_stack([_total(model, j, issues + j * _DAY) for j in range(FC_DAYS)])
    tf = _training_features(source, rules)
    near = _issue_features(gauge, fc, issues, SERVED_PAST_DAYS)
    full = _issue_features(gauge, fc, issues, FULL_PAST_DAYS)
    # the gauge record's dry spell ending the day before the 35-day frame: lead L's (and history_full_v1's) carry
    dry_before = tf["dry_spell_days"].reindex(issues - (FULL_PAST_DAYS + 1) * _DAY).to_numpy(dtype=float)
    for I, (series, dry) in _issue_records(source, rules).items():   # the record as the issue day knew it
        k = int(issues.get_indexer([I])[0])
        if k < 0:
            continue
        one = pd.DatetimeIndex([I])
        near[k] = _issue_features(series, fc[k:k + 1], one, SERVED_PAST_DAYS)[0]
        full[k] = _issue_features(series, fc[k:k + 1], one, FULL_PAST_DAYS)[0]
        dry_before[k] = dry
    return {"issues": issues, "near": near, "full": full, "dry_before": dry_before}


def _lead_values(model: str, source: str, rules: tuple, lead: int, served: bool, history: str,
                 dates: pd.DatetimeIndex) -> np.ndarray:
    """[date, DAILY_FEATURES] for rows that exist (row status ''), per the entry's window: as served today,
    every feature from the 13-row frame; lead L and history_full_v1, the history features from the 35-day
    frame with the dry spell carried across its start (one code path, so the two are the same numbers)."""
    if history not in HISTORY:
        raise KeyError(f"unknown history {history!r}; known: {tuple(HISTORY)}")
    t = _lead_tables(model, source, rules)
    k = t["issues"].get_indexer(dates - lead * _DAY)
    if (k < 0).any():
        raise AssertionError(f"{model} lead {lead}: a kept row's issue day has no table row ({dates[k < 0][0].date()})")
    col = {f: i for i, f in enumerate(RF.DAILY_FEATURES)}
    hist = [col[f] for f in HISTORY_FEATURES]
    vals = t["near"][k, lead, :].copy()                      # the near features: one frame for every lead entry
    if served and history == "served":
        return vals                                          # the page today: history truncated at 7 past days
    full = t["full"][k, lead, :]
    vals[:, hist] = full[:, hist]
    d = col["dry_spell_days"]
    whole = full[:, d] == FULL_PAST_DAYS + lead + 1          # dry from the frame's first day to D: carry the record's count
    before = t["dry_before"][k]
    if np.isnan(before[whole]).any():
        raise AssertionError("the gauge record's dry spell is missing before a frame")
    vals[whole, d] = full[whole, d] + before[whole]
    return vals


# ── frames ──────────────────────────────────────────────────────────────────

def _check(f: pd.DataFrame, what: str) -> pd.DataFrame:
    bad = [c for c in COLUMNS if f[c].isna().any()]
    if bad:
        raise ValueError(f"{what}: NaN in {bad} (a missing value is never a zero)")
    if f["date"].duplicated().any():
        raise AssertionError(f"{what}: a day repeats")
    return f


def _rain_frame(source: str, rules: tuple, days: pd.DatetimeIndex) -> pd.DataFrame:
    """build_dataset's feature columns for ``source`` on ``days``: the same merges, names and order."""
    _gauge(source, rules)                                   # every day was read by a gauge
    f = pd.DataFrame({"date": days}).merge(_training_features(source, rules).reset_index(), on="date", how="left")
    f = f.merge(_era5_peaks(), on="date", how="left").merge(_era5_wind(), on="date", how="left")
    _check(f, f"rain {source}")                             # inside the span nothing is filled: build_dataset's fillna(0) is a no-op
    return f[["date"] + COLUMNS].reset_index(drop=True)


def _ints(vals: np.ndarray, name: str) -> np.ndarray:
    if not np.allclose(vals, np.round(vals)):
        raise AssertionError(f"{name} is not a whole number")
    return np.round(vals).astype("int64")


def frames(entry: str, sources, model: str = "icon_seamless", start=None, end=None,
           input_rules=(RF.GAUGE_OUTAGE_RULE["name"],), history: str = "served") -> dict:
    """{source: DataFrame[date + the 19 features + WIND]} for one entry: one row per day of [start, end] the
    entry holds, the columns, order and dtypes of train_v4.build_dataset's frames (date datetime64[us]; floats;
    wet_prior_3d and dry_spell_days int64), sorted by date on a RangeIndex. Every source holds the same days.
    wind_v_rain: ERA5's for rain known, the lead's own archived wind on its own rain for a lead entry.

    ``model``: the weather model of the lead entries (ignored by oracle / rain). ``input_rules``: the gauge
    rules (default gauge_outage_v1, every day; () = the raw record). ``history``: as-served entries only —
    'served' (7 past days, the page today) or 'full' (history_full_v1: 35 days and the carried dry spell,
    so L0s / L1s read exactly L0 / L1's features; only attrs "entry" and "history" tell them apart). Each frame's attrs say
    what it is: entry, model, history, input_rules, lead_kind (None for rain known; 'short_lead_optimistic'
    for L0 / L0s, which protocol §3 tags optimistic; 'fixed_lead' for L1 … L5 / L1s), source, span [first,
    last], dropped {reason: [[first, last], …]} (``drops`` gives the days one by one, with the model-day
    behind each).
    """
    L = lead_of(entry)
    src = _sources(sources)
    rules = _rules(input_rules)
    if history not in HISTORY:
        raise KeyError(f"unknown history {history!r}; known: {tuple(HISTORY)}")
    served = as_served(entry)
    if history != "served" and not served:
        raise ValueError(f"history={history!r} is the as-served window; entry {entry!r} reads the whole record already")
    days = _span(start, end)
    if L is None:
        out, kept, dropped = {s: _rain_frame(s, rules, days) for s in src}, days, {}
        model_used = None
    else:
        model_used = _model(model)
        st = _row_status(model_used, L, days)
        keep = (st["reason"] == "").to_numpy()
        kept = days[keep]
        dropped = {r: _runs(st.loc[st["reason"] == r, "date"]) for r in (NWPGAP, NOLEAD) if (st["reason"] == r).any()}
        pk = _peaks(model_used, L).reindex(kept)
        if (pk["peak_status"].to_numpy() != OK).any():
            raise AssertionError("a kept row's peak window is not complete")
        out = {}
        for s in src:
            vals = _lead_values(model_used, s, rules, L, served, history, kept)
            f = pd.DataFrame({"date": kept})
            for i, c in enumerate(RF.DAILY_FEATURES):
                f[c] = _ints(vals[:, i], c) if c in INT_FEATURES else vals[:, i]
            for c in RF.INTENSITY_FEATURES:
                f[c] = pk[c].to_numpy(dtype=float)
            f["wind_v_rain"] = _wind(model_used, L).reindex(kept).to_numpy(dtype=float)   # as served too: D's hours at lead L
            out[s] = _check(f, f"{entry} {model_used} {s}")
    for s, f in out.items():
        f.attrs = {"entry": entry, "model": model_used, "history": history if served else None, "input_rules": list(rules),
                   "lead_kind": None if L is None else OMP.LEAD_KIND[L],     # lead 0: short_lead_optimistic (protocol §3 tag)
                   "source": s, "span": [str(kept.min().date()), str(kept.max().date())] if len(kept) else None,
                   "dropped": dropped}
    return out
