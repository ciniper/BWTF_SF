"""The exclusions engine: what each stage's score leaves out, in the protocol's
first-match order, and what the public number does not claim (P4b;
STAGES_DESIGN.md Part C §4, as amended by Part B 3, 4, 8 and 9;
STAGES_PROTOCOL.md §7 is the authority for the ids, their kinds and the order).

A score is only as honest as the list of what it left out. Every rule is a pure
function of a stage row and a ``Context`` built once per geography from
truth.py (ledger coverage, onsets, carry days, gauge status, samples and
first looks, postings, storms), plus the inputs only a build has (a weather
model's archive, the S5 feeds, the watcher's health). ``apply`` writes the
first rule that matches a row into its ``excl`` column, so the counts
partition the table, and ``counts`` asserts it:

    n_total = n_scored + Σ n_rule      for every (stage, unit, entry, window)

    RULES         the 36 ids of protocol §7: kind (exclude | tag | stratum |
                  fit_only), stages, the table it leaves rows out of, the
                  entries it is gated to; the plain words live in
                  stages_spec.EXCLUSIONS and are read from there
    STAGE_ORDER   §7's first-match table, exactly
    apply         rows → the same rows with excl, tags, stratum (and sel)
    counts        {stage: {rule: {unit: n}}} and the partition, asserted
    ledger_suspect  X-LEDGER-SUSPECT's windows, with their reasons (Part B 8)
    claims        what the public % does not claim (§4.2, Part B 3)
    catalog_counts  every §4.3 count the truth alone decides, per unit

**Tables.** A row's ``excl`` is for its stage's primary score. §7's "also"
column names rules that leave rows out of a secondary table only: X-S1-PEAK
(peak-hour metrics), X-S2-VOLQ (the volume score), X-S3-NOTCLEAN and X-PL-END
(the posting checks and rulers). ``apply(..., table=...)`` runs the stage's
order and then the table's rules, so each table partitions on its own. S5 has
an ``all_days`` table: its order without X-S5-QUIET ("conditional score only").

**S5 is graded on OUT's label.** After S5's own five rules a row takes OUT's
coverage rules (X-E2E-UNCOV, X-E2E-UNK): a zone-day OUT cannot grade has no S5
truth either, and an unlabelled row may never sit in the scored set.

**What a build applies, and what the truth decides alone.** X-S1-NWPGAP and
X-S1-NOLEAD read the weather model's archive (``Context.nwp``); X-S5-HEALTH,
X-S5-PERFECT-SIBLING, X-S5-SELF and X-S5-QUIET read the feeds
(``Context.feeds``, ``Context.watcher``); X-POWER counts a unit × window's
scored positives and storm blocks; X-SEL reads the set's geography and the
dates. ``apply`` runs all of them on the build's rows; ``catalog_counts`` runs
the rest on a skeleton of every unit-day, which is what the stages report's
exclusions ledger and the figure's chips show before any model row exists.

**Readings the words leave open, and what this module does:**
- X-LEDGER-SUSPECT tests the zone, as protocol §7 words it: a trigger counts
  when D−3…D+1 holds a wet day (§6: ``truth.blocks``' wet) and no basin
  feeding the zone filed an event in it (East: Central's event explains a
  posting, so South is not flagged). A window with no wet day and a day no
  gauge recorded raises: unknown is not dry. A zone row (S3) is suspect when
  any feeding basin's day is, as X-S3-UNCOV reads coverage.
- X-S2-OUTAGEIN reads the gauges behind the basin's rain series over the
  input days that are gauge days, on the record the row's entry read: D−29…D
  for the oracle and rain known (the whole record's gauge_outage_v1 mask);
  D−29…D−L−1 for lead L, and as served the live frame's 7 past days
  D−L−7…D−L−1 (stages_entries.SERVED_PAST_DAYS), both on the issue day's
  record: a gauge-day the whole-record mask hides that the gauges through
  I − 1 could not yet call an outage (stages_entries.issue_time_unmasked) was
  read as filed, so it is not a masked day of that row's window. Lead rows
  need it as a build input (``Context.unmasked``); without it they raise.
- S4's truth and OUT's label read the stages' sample record,
  samples.D10_SOURCES (design §3.4, owner decision D10: DataSF 2020-07 →,
  Poo Bot 2015-12 → 2017-01, STARDB 2016-10 → 2020-07, de-duplicated), so do
  the sample triggers of X-LEDGER-SUSPECT and the claims.
- X-S3-NOTCLEAN: a zone's sibling is another zone its feeding basins reach.
- X-S5-CIRC: "Westside" is the basin whose ledger knows no day of the feed
  archive window, so its archive-era truth can only have been the feed.
- X-S4-ANALYTE: the protocol's window, 2020-07 → 2021, by date.

Missing is never zero, and nothing defaults: an unknown stage, unit, entry,
tier or rule raises, a T3 row raises (X-ALL-INSAMPLE is zero by
construction), a row outside the context's days raises, and so does a scored
row whose truth is missing or disagrees with truth.py. No module-level IO.

    venv/bin/python features/forecast/src/models/exclusions.py     # the catalog, both geographies
"""
from __future__ import annotations

import dataclasses
import functools
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (REPO, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import samples as SMP  # noqa: E402
import stages_spec as SP  # noqa: E402
import train_v4  # noqa: E402  (the holdout and training-end dates the served set was selected and fit on)
import truth as T  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.outfalls import GEOGRAPHY, OUTFALLS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

PROTOCOL = FORECAST / "STAGES_PROTOCOL.md"
AS_OF = pd.Timestamp("2026-08-17")                 # the committed data's end at the freeze (protocol §2, T1)
HOLDOUT_START = train_v4.HOLDOUT_START             # 2023-07-01: X-SEL holdout_selected from here
POST_START = train_v4.TRAIN_END + pd.Timedelta(days=1)   # 2025-11-01: post_selected from here to the freeze

# protocol §6 / §7 numbers
POWER_MIN_POSITIVES, POWER_MIN_STORM_BLOCKS = 10, 8   # X-POWER
SUSPECT_BEFORE, SUSPECT_AFTER = 3, 1                  # X-LEDGER-SUSPECT: D−3…D+1
SUSPECT_RATIO = 10.0                                  # … or a sample ≥ 10× the standard
NOTCLEAN_DAYS = 2                                     # X-S3-NOTCLEAN: a sibling zone's link within ±2 days
INPUT_DAYS = 30                                       # X-S2-OUTAGEIN: the 30-day input window (rain_30d_cum)
AS_SERVED_DAYS = 7                                    # as served: METEO_PARAMS past_days=7 (protocol §3)
NWP_DAY_HOURS = 24                                    # X-S1-NWPGAP: fewer than 24 archived hours
OBS_DAYS = T.TAIL_DAYS                                # S5: zone-days D…D+7 after an observation
ANALYTE_ERA = (pd.Timestamp("2020-07-01"), pd.Timestamp("2021-12-31"))   # X-S4-ANALYTE "2020-07 → 2021"
FOLLOWUP_STATIONS = ("OCEAN#20", "OCEAN#21", "OCEAN#22")   # X-S4-FOLLOWUP, as §7 names them
UNMONITORED_KM = 1.5                                  # C-UNMON
RUNOFF_RAIN_IN, RUNOFF_DAYS = 0.1, 3                  # C-DRY / C-RUNOFF: two-gauge rain D−2…D

COLUMNS = ("date", "stage", "unit_type", "unit", "entry", "lead", "tier", "sel", "p", "q", "b", "v_hat",
           "y", "y2", "n_sampled", "stratum", "excl", "tags", "variant")   # rows.csv.gz (design §7)
# 'variant' (added for S5, stages_s5): the live correction an S5 row carries; '' on every other stage. Rows
# built without it validate as variant ''. S5's variants share one conditional set (the feed defines it), so
# X-POWER is decided per variant and ``counts`` takes one variant's rows at a time.
OPTIONAL_COLUMNS = ("variant",)
WINDOWS = ("T0", "T1", "T1-holdout", "T2")             # protocol §2; T3 is never emitted
IN_SAMPLE = "T3"
ENTRIES = ("oracle", "rain", "L0", "L1", "L2", "L3", "L4", "L5", "L0s", "L1s")   # protocol §3
S5_FEEDS = ("oracle", "archive", "degraded", "watcher")  # S5's entry names the feed; oracle = the perfect feed
KINDS = ("exclude", "tag", "stratum", "fit_only")
SELECTED = ("holdout_selected", "post_selected")

STAGE_IDS = {s["code"]: s["id"] for s in SP.STAGES}   # 'S2' → 's2'
UNIT_TYPES = {"S1": ("series",), "S2": ("basin",), "S3": ("zone", "link"), "S4": ("zone",), "S5": ("zone",), "OUT": ("zone",)}

# STAGES_PROTOCOL.md §7, first-match order, exactly (tests/test_exclusions.py parses the table and compares).
STAGE_ORDER = {
    "S1": ("X-S1-MISSING", "X-S1-OUTAGE", "X-S1-NWPGAP", "X-S1-NOLEAD"),
    "S2": ("X-S2-ARCHIVE", "X-S2-UNCOV", "X-LEDGER-SUSPECT", "X-S2-CARRY", "X-ALL-INSAMPLE"),
    "S3": ("X-S3-UNCOV", "X-LEDGER-SUSPECT", "X-S3-CARRY", "X-S3-QUIET", "X-S3-ID", "X-ALL-INSAMPLE"),
    "S4": ("X-S4-UNSAMPLED", "X-S4-HISTUNK", "X-S4-RESAMPLE", "X-ALL-INSAMPLE"),
    "S5": ("X-S5-HEALTH", "X-S5-CIRC", "X-S5-PERFECT-SIBLING", "X-S5-SELF", "X-S5-QUIET"),
    "OUT": ("X-E2E-UNCOV", "X-E2E-UNK", "X-ALL-INSAMPLE"),
}
# Secondary tables (§7's "also" column): the stage's order, then these. 'score' is the primary.
TABLES = {
    "S1": {"score": (), "peak": ("X-S1-PEAK",)},
    "S2": {"score": (), "volume": ("X-S2-VOLQ",)},
    "S3": {"score": (), "posting": ("X-S3-NOTCLEAN", "X-PL-END")},
    "S4": {"score": ()},
    "S5": {"score": (), "all_days": ()},
    "OUT": {"score": (), "posting": ("X-PL-END",)},
}
GRADED_ON = {"S5": "OUT"}                              # S5 is scored against OUT's label (protocol §1)

# id → (table, entries, needs): the table it leaves rows out of; the entries it is gated to (S5: the
# feeds; None = every entry); 'truth' if the truth alone decides it, 'model' if it needs a build's inputs.
_SPEC = {
    "X-ALL-INSAMPLE": ("score", None, "truth"),
    "X-POWER": ("score", None, "model"),
    "X-SEL": ("score", None, "model"),
    "X-LEDGER-SUSPECT": ("score", None, "truth"),
    "X-PL-END": ("posting", None, "truth"),
    "X-S1-MISSING": ("score", None, "truth"),
    "X-S1-OUTAGE": ("score", None, "truth"),
    "X-S1-NWPGAP": ("score", None, "model"),
    "X-S1-NOLEAD": ("score", None, "model"),
    "X-S1-PEAK": ("peak", None, "truth"),
    "X-S2-ARCHIVE": ("score", None, "truth"),
    "X-S2-UNCOV": ("score", None, "truth"),
    "X-S2-CARRY": ("score", None, "truth"),
    "X-S2-VOLQ": ("volume", None, "truth"),
    "X-S2-OUTAGEIN": ("score", None, "truth"),
    "X-S3-UNCOV": ("score", None, "truth"),
    "X-S3-CARRY": ("score", None, "truth"),
    "X-S3-QUIET": ("score", ("oracle",), "truth"),
    "X-S3-ID": ("score", ("oracle",), "truth"),
    "X-S3-GEO": ("score", None, "truth"),
    "X-S3-NOTCLEAN": ("posting", None, "truth"),
    "X-S4-UNSAMPLED": ("score", None, "truth"),
    "X-S4-HISTUNK": ("score", None, "truth"),
    "X-S4-RESAMPLE": ("score", None, "truth"),
    "X-S4-DAYOF": ("score", None, "truth"),
    "X-S4-FEW": ("score", None, "truth"),
    "X-S4-FOLLOWUP": ("fit", None, "truth"),
    "X-S4-ANALYTE": ("score", None, "truth"),
    "X-S5-HEALTH": ("score", ("watcher",), "model"),
    "X-S5-CIRC": ("score", ("archive",), "truth"),
    "X-S5-PERFECT-SIBLING": ("score", ("oracle",), "model"),
    "X-S5-SELF": ("score", None, "model"),
    "X-S5-QUIET": ("score", None, "model"),
    "X-S5-INSAMPLE": ("score", None, "truth"),
    "X-E2E-UNCOV": ("score", None, "truth"),
    "X-E2E-UNK": ("score", None, "truth"),
}


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str          # exclude | tag | stratum | fit_only
    stages: tuple      # stage codes whose rows it applies to (stages_spec's lists)
    table: str         # 'score', a secondary table ('peak' | 'volume' | 'posting'), or 'fit'
    entries: tuple | None
    needs: str         # 'truth' | 'model'

    @property
    def chip(self) -> str:
        return SP.EXCLUSIONS[self.id]["chip"]

    @property
    def plain(self) -> str:
        return SP.EXCLUSIONS[self.id]["plain"]


def _rules() -> dict:
    if set(_SPEC) != set(SP.EXCLUSIONS):
        raise AssertionError(f"exclusions._SPEC and stages_spec.EXCLUSIONS differ: {sorted(set(_SPEC) ^ set(SP.EXCLUSIONS))}")
    stages = {x: tuple(c for c, ids in SP.EXCLUSIONS_BY_STAGE.items() if x in ids) for x in SP.EXCLUSIONS}
    kind = {"fit": "fit_only"}
    out = {}
    for x, (table, entries, needs) in _SPEC.items():
        out[x] = Rule(x, kind.get(SP.EXCLUSIONS[x]["kind"], SP.EXCLUSIONS[x]["kind"]), stages[x], table, entries, needs)
        if out[x].kind not in KINDS or not out[x].stages:
            raise AssertionError(f"{x}: kind {out[x].kind!r}, stages {out[x].stages}")
    for code, seq in STAGE_ORDER.items():
        for x in seq:
            if code not in out[x].stages or out[x].kind != "exclude":
                raise AssertionError(f"{x} is in {code}'s first-match order but stages_spec does not list it there as an exclusion")
    return out


RULES = _rules()


# ── plumbing ───────────────────────────────────────────────────────────────

def _code(stage: str) -> str:
    """'s2' or 'S2' → 'S2'; KeyError on anything else."""
    s = str(stage)
    if s.upper() in STAGE_ORDER:
        return s.upper()
    raise KeyError(f"unknown stage {stage!r}; known: {tuple(STAGE_IDS.values())}")


def _ts(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def _days(lo, hi) -> pd.DatetimeIndex:
    return pd.date_range(_ts(lo), _ts(hi), name="date")


def _lead_of(entry: str) -> int | None:
    m = re.fullmatch(r"L(\d)s?", entry)
    return int(m.group(1)) if m else None


def _feed_of(entry: str) -> str:
    """S5's entry is its feed, optionally with a seed after a colon ('degraded:3')."""
    return entry.split(":", 1)[0]


@functools.lru_cache(maxsize=1)
def freeze_date() -> pd.Timestamp:
    """The protocol's freeze date (its first lines): T0 starts the next day, and X-SEL post_selected ends here.
    The file must be the protocol these rules implement (stages_spec.PROTOCOL_VERSION): a newer version
    raises until the code follows it."""
    text = PROTOCOL.read_text()
    title = re.search(r"^# .*scoring protocol `([^`]+)`", text, re.M)
    if not title or title.group(1) != SP.PROTOCOL_VERSION:
        raise ValueError(f"{PROTOCOL.name} is protocol {title.group(1) if title else '(no title)'}, "
                         f"these rules implement {SP.PROTOCOL_VERSION}")
    m = re.search(r"^Freeze date: (\d{4}-\d{2}-\d{2})", text, re.M)
    if not m:
        raise ValueError(f"{PROTOCOL.name} states no 'Freeze date:'")
    return pd.Timestamp(m.group(1))


def selection(dates, geo) -> np.ndarray:
    """X-SEL per date: 'holdout_selected' (2023-07-01 → 2025-10-31), 'post_selected' (2025-11-01 → the
    freeze) or '' — only for a GEO_V1 set, whose choices were made on those days (protocol §2)."""
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    if _geo(geo).version != "geo_v1":
        return np.full(len(d), "", dtype=object)
    return np.select([(d >= HOLDOUT_START) & (d < POST_START), (d >= POST_START) & (d <= freeze_date())],
                     list(SELECTED), default="").astype(object)


def _geo(geo) -> G.Geography:
    if isinstance(geo, G.Geography):
        return geo
    if isinstance(geo, str):
        return G.get(geo)
    raise TypeError(f"geo must be a shared.geography.Geography or a version string, not {type(geo).__name__}")


def _km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (haversine, mean Earth radius)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


# ── X-LEDGER-SUSPECT (Part B 8) ─────────────────────────────────────────────

def _triggers(geo: G.Geography, sources: tuple, start, end) -> pd.DataFrame:
    """Every CSO-cause posting onset and every zone sample-day ≥ 10× its standard, D in [start, end]."""
    p = T.postings()
    p = p[(p["cause_class"] == "cso") & p["class_onset"] & (p["date"] >= start) & (p["date"] <= end)]
    hi = min(end, T._samples(sources)["date"].max())
    el = T.zone_elevated(geo, sources, start, hi)
    el = el[el["max_ratio"] >= SUSPECT_RATIO]
    return pd.concat([
        pd.DataFrame({"date": p["date"], "zone": p["zone"], "why": "cso_posting_onset",
                      "detail": [f"{n} advisories posted ({s})" for n, s in zip(p["n_advisories"], p["stations"])]}),
        pd.DataFrame({"date": el["date"], "zone": el["zone"], "why": "sample_10x",
                      "detail": [f"a sample {r:,.0f}× the standard" for r in el["max_ratio"]]}),
    ], ignore_index=True).sort_values(["date", "zone", "why"], kind="stable").reset_index(drop=True)


def _suspect(geo: G.Geography, sources: tuple, start, end) -> tuple[list, set]:
    """(episodes, {(basin, date)}): see ``ledger_suspect``. Uses the data through ``end`` only.

    A trigger's window (its wet days and the ledger's events) is read whole, as far as ``end``, never
    only the part inside [start, end]: a context starting mid-record must flag the same days as the
    full record does (an event or a wet day before ``start`` still decides a trigger after it)."""
    start, end = _ts(start), _ts(end)
    lo = start - pd.Timedelta(days=SUSPECT_AFTER)          # the first trigger whose window reaches `start`
    ons = T.basin_onsets(geo, lo - pd.Timedelta(days=SUSPECT_BEFORE), end)
    on = ons["y"].eq(1).fillna(False).to_numpy(dtype=bool)
    fired = set(zip(ons.loc[on, "basin"], ons.loc[on, "date"]))
    known = set(zip(ons.loc[ons["known"], "basin"], ons.loc[ons["known"], "date"]))
    rain = T.blocks(end=end).set_index("date")[["rain", "wet"]]   # protocol §6's wet day: masked two-gauge mean ≥ 0.10"
    flagged: dict = {}
    for tr in _triggers(geo, sources, lo, end).itertuples(index=False):
        win = _days(tr.date - pd.Timedelta(days=SUSPECT_BEFORE), tr.date + pd.Timedelta(days=SUSPECT_AFTER))
        win = win[win <= end]                               # the record as of `end`: nothing after it is read
        r = rain.reindex(win)
        if not r["wet"].fillna(False).astype(bool).any():
            if r["rain"].isna().any():
                raise ValueError(f"X-LEDGER-SUSPECT: no wet day in {win[0].date()} → {win[-1].date()} around a {tr.zone} "
                                 "trigger, and a day of it has no rain record: unknown is not dry")
            continue                                        # a dry window: a dry-weather exceedance, not a missed overflow
        feeding = T.feeding_basins(geo, tr.zone)
        if any((b, d) in fired for b in feeding for d in win):
            continue                                        # a basin feeding the zone filed an event: explained
        for b in feeding:
            for d in win[win >= start]:
                if (b, d) in known:
                    flagged.setdefault((b, d), []).append(tr)
    episodes = []
    for b in geo.keys:
        days = sorted(d for (bb, d) in flagged if bb == b)
        runs: list = []
        for d in days:
            if runs and (d - runs[-1][-1]).days == 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        for run in runs:
            seen = {}
            for d in run:
                for tr in flagged[(b, d)]:
                    seen[(tr.date, tr.zone, tr.why)] = tr
            reasons = [{"date": str(tr.date.date()), "zone": tr.zone, "why": tr.why, "detail": tr.detail}
                       for _, tr in sorted(seen.items(), key=lambda kv: kv[0])]
            episodes.append({"basin": b, "basin_name": geo.basin(b).name, "start": str(run[0].date()), "end": str(run[-1].date()),
                             "n_days": len(run), "zones": sorted({r["zone"] for r in reasons}, key=list(ZONES).index),
                             "reasons": reasons})
    return episodes, set(flagged)


def ledger_suspect(geo, start=T.TRUTH_START, end=None, sources=SMP.D10_SOURCES) -> list[dict]:
    """X-LEDGER-SUSPECT's windows (Part B 8; protocol §7), merged into episodes per basin, with reasons.

    A trigger is a CSO-cause BeachWatch posting onset (``truth.postings``: cause_class 'cso' and a new
    class that day) or a zone sample-day with a result ≥ 10× its standard (``zone_elevated.max_ratio``),
    in zone z on day D. It counts when D−3…D+1 holds a wet day (protocol §6: the masked two-gauge mean
    ≥ 0.10", ``truth.blocks``) and no basin feeding z has a ledger event in D−3…D+1. Then every feeding
    basin's ledger_known days in D−3…D+1 are suspect: listed here, left out of S2 and S3. One rule: a
    trigger in a dry window is a dry-weather exceedance (C-DRY), and an event at any feeding basin
    explains the trigger for all of them. In both geographies zones that share a basin share all their
    feeding basins, so no zone overflow day is suspect (tests/test_exclusions.py checks the data).

    Returns one dict per episode (consecutive suspect days of one basin): basin, basin_name, start, end
    (ISO dates), n_days, zones, reasons [{date, zone, why ('cso_posting_onset' | 'sample_10x'), detail}].
    Uses the data through ``end`` only (default: the context's data end), so a count as of a date stays put.
    """
    geo = _geo(geo)
    end = _data_end(tuple(sources)) if end is None else _ts(end)
    return _suspect(geo, tuple(sources), _ts(start), end)[0]


# ── the context ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Context:
    """Everything the rules read, for one geography over [start, end]. Built by ``context``.

    frames: unit_type → DataFrame indexed by (unit, date), every unit × day of [start, end]:
      series  status, other (the other gauge's status, for the S1 floor), rain
      basin   known, archive, y, volq, carry, suspect
      link    known, y, carry, suspect, quiet, identity, geo_only, notclean
      zone    known, hist_known, y, carry, suspect, quiet, identity, geo_only, notclean,
              sampled, s4_y, first_look, few, dayof, out_y, out_why, overflow
    blocks: date → (block, block_kind) from truth.blocks (X-POWER's storm blocks).
    outage: date × gauge → running count of gauge_outage_v1 days, the whole record (X-S2-OUTAGEIN).
    rain_series: basin → the S2 rain series its inputs read; rain3: two-gauge rain D−2…D (C-DRY,
    C-RUNOFF); postings_end: BeachWatch's last filing (X-PL-END); suspect: ``ledger_suspect``'s
    episodes; circ_zones: the zones X-S5-CIRC covers.
    The build adds what only it has: nwp (one weather model's archive, (date, lead) → archived hours),
    feeds ({feed: DataFrame[date, zone, basin]}, S5), watcher (date → healthy, S5 live era) and unmasked
    (DataFrame[issue, date, gauge]: stages_entries.issue_time_unmasked, the gauge-days the issue day's
    record had not yet masked; X-S2-OUTAGEIN on lead rows).
    """
    geo: G.Geography
    start: pd.Timestamp
    end: pd.Timestamp
    sources: tuple
    frames: dict
    blocks: pd.DataFrame
    outage: pd.DataFrame
    rain_series: dict
    postings_end: pd.Timestamp
    suspect: tuple
    circ_zones: tuple
    rain3: pd.Series
    nwp: pd.Series | None = None
    feeds: dict | None = None
    watcher: pd.Series | None = None
    peak_truth: bool = False      # X-S1-PEAK fires on every day until KSFO hourly truth is committed
    unmasked: pd.DataFrame | None = None

    def with_inputs(self, nwp=None, feeds=None, watcher=None, rain_series=None, unmasked=None) -> "Context":
        """A copy with a build's inputs added (checked); see ``context``."""
        kw = {}
        if unmasked is not None:
            kw["unmasked"] = _check_unmasked(unmasked)
        if nwp is not None:
            kw["nwp"] = _check_nwp(nwp)
        if feeds is not None:
            kw["feeds"] = {str(k): _check_feed(self.geo, k, v) for k, v in feeds.items()}
        if watcher is not None:
            w = pd.Series(watcher)
            if w.isna().any():
                raise ValueError("the watcher's health has unknown days: leave them out rather than guess")
            w = w.astype(bool)
            w.index = pd.DatetimeIndex(w.index).normalize()
            kw["watcher"] = w
        if rain_series is not None:
            kw["rain_series"] = _check_rain_series(self.geo, rain_series)
        return dataclasses.replace(self, **kw)


def _check_nwp(nwp: pd.DataFrame) -> pd.Series:
    need = {"date", "lead", "n_hours"}
    if not need <= set(nwp.columns):
        raise ValueError(f"nwp needs columns {sorted(need)}, has {sorted(nwp.columns)}")
    f = nwp[["date", "lead", "n_hours"]].copy()
    f["date"] = pd.to_datetime(f["date"]).dt.normalize()
    f["lead"] = f["lead"].astype(int)
    if f.duplicated(["date", "lead"]).any():
        raise ValueError("nwp repeats a (date, lead)")
    if f["n_hours"].isna().any() or (f["n_hours"] < 0).any():
        raise ValueError("nwp n_hours must be a count of archived hours, never NaN")
    return f.set_index(["date", "lead"])["n_hours"].sort_index()


def _check_feed(geo: G.Geography, name, f: pd.DataFrame) -> pd.DataFrame:
    if _feed_of(str(name)) not in S5_FEEDS:
        raise KeyError(f"unknown S5 feed {name!r}; known: {S5_FEEDS} (a seed may follow a colon)")
    if not {"date", "zone"} <= set(f.columns):
        raise ValueError(f"feed {name!r} needs columns date, zone (and basin, or None for a zone-only observation)")
    out = pd.DataFrame({"date": pd.to_datetime(f["date"]).dt.normalize(), "zone": f["zone"].astype(str),
                        "basin": f["basin"] if "basin" in f.columns else None})
    bad = set(out["zone"]) - set(ZONES)
    if bad:
        raise KeyError(f"feed {name!r} names zones not in shared/zones.py: {sorted(bad)}")
    basins = {b for b in out["basin"] if isinstance(b, str)}
    if basins - set(geo.keys):
        raise KeyError(f"feed {name!r} names basins {geo.version} lacks: {sorted(basins - set(geo.keys))}")
    return out.reset_index(drop=True)


def _check_unmasked(u: pd.DataFrame) -> pd.DataFrame:
    """stages_entries.issue_time_unmasked's frame, checked: (issue, date, gauge), each gauge-day before its issue day."""
    need = {"issue", "date", "gauge"}
    if not need <= set(u.columns):
        raise ValueError(f"unmasked needs columns {sorted(need)}, has {sorted(u.columns)}")
    f = pd.DataFrame({"issue": pd.to_datetime(u["issue"]).dt.normalize(), "date": pd.to_datetime(u["date"]).dt.normalize(),
                      "gauge": u["gauge"].astype(str)})
    bad = set(f["gauge"]) - set(T.GAUGE_SERIES)
    if bad:
        raise KeyError(f"unmasked names gauges that are not {T.GAUGE_SERIES}: {sorted(bad)}")
    if (f["date"] >= f["issue"]).any():
        raise ValueError("an unmasked gauge-day is not before its issue day: the issue day reads the gauges through I − 1")
    if f.duplicated().any():
        raise ValueError("unmasked repeats an (issue, date, gauge)")
    return f.reset_index(drop=True)


def _check_rain_series(geo: G.Geography, rs: dict) -> dict:
    if set(rs) != set(geo.keys):
        raise KeyError(f"rain_series must name every basin of {geo.version}: {geo.keys}, got {sorted(rs)}")
    bad = {b: s for b, s in rs.items() if s not in T.SERIES}
    if bad:
        raise ValueError(f"rain series not in {T.SERIES}: {bad}")
    return dict(rs)


@functools.lru_cache(maxsize=4)
def _data_end(sources: tuple) -> pd.Timestamp:
    """The last day every record covers: the ledger grid, the gauges and the samples."""
    gauge_end = T._gauge_record()[0].index.max()
    return min(T.ledger_end(), gauge_end, T._samples(sources)["date"].max())


def _index(f: pd.DataFrame, unit: str) -> pd.DataFrame:
    return f.rename(columns={unit: "unit"}).set_index(["unit", "date"]).sort_index()


def _window_any(fired: pd.DataFrame, back: int, ahead: int) -> pd.DataFrame:
    """date × unit bool (every day) → True where the unit fired on some day of D−back…D+ahead that the frame
    holds. The window ends at D+ahead before it is shifted back onto D, so a frame's first days still read
    the `back` days before them (a frame that starts `back` days before a context reads every D−back of
    it), and nothing after the frame's last day is read."""
    x = fired.astype(int)
    if ahead:
        tail = pd.date_range(x.index[-1] + pd.Timedelta(days=1), periods=ahead, name=x.index.name)
        x = pd.concat([x, pd.DataFrame(0, index=tail, columns=x.columns)])
    out = x.rolling(back + ahead + 1, min_periods=1).max()
    return (out.shift(-ahead).iloc[:len(fired)] if ahead else out).astype(bool)


@functools.lru_cache(maxsize=8)
def _truth_context(geo: G.Geography, start: pd.Timestamp, end: pd.Timestamp, sources: tuple) -> Context:
    days = _days(start, end)
    one = pd.Timedelta(days=1)
    feeding = {z: list(T.feeding_basins(geo, z)) for z in ZONES}
    # basins (S2)
    lk = T.ledger_known(geo, start, end)
    bo = T.basin_onsets(geo, start, end)
    bc = T.carry_days(geo, "basin", start, end)
    _aligned((lk, "basin"), (bc, "basin"), ref=(bo, "basin"))
    episodes, flagged = _suspect(geo, sources, start, end)
    basin = pd.DataFrame({"date": bo["date"], "basin": bo["basin"], "known": bo["known"], "archive": lk["archive"].to_numpy(),
                          "y": bo["y"].astype("float"), "volq": bo["volq"], "carry": bc["carry"].to_numpy(),
                          "suspect": [(b, d) in flagged for b, d in zip(bo["basin"], bo["date"])]})
    b_suspect = basin.pivot(index="date", columns="basin", values="suspect").astype(bool)
    b_fired = basin.pivot(index="date", columns="basin", values="y").eq(1)
    # links (S3's internal unit). The ±2-day lookahead stops at `end`: a context as of a date reads nothing later.
    lo = T.link_onsets(geo, start - NOTCLEAN_DAYS * one, end)
    l_fired = lo.assign(f=lo["y"].eq(1).fillna(False)).pivot(index="date", columns="link", values="f").astype(bool)
    l_near = _window_any(l_fired, NOTCLEAN_DAYS, NOTCLEAN_DAYS).loc[days]
    lo = lo[lo["date"] >= start].reset_index(drop=True)
    lc = T.carry_days(geo, "link", start, end)
    _aligned((lc, "link"), ref=(lo, "link"))
    parts = []
    for lk_ in geo.links:
        sib = [o.id for o in geo.links if o.basin == lk_.basin and o.zone != lk_.zone]
        parts.append(pd.DataFrame({"suspect": b_suspect[lk_.basin], "quiet": ~b_fired[lk_.basin], "identity": lk_.identity,
                                   "notclean": l_near[sib].any(axis=1) if sib else False}, index=days))
    per_link = pd.concat(parts, ignore_index=True)
    link = pd.DataFrame({"date": lo["date"], "link": lo["link"], "known": lo["known"], "y": lo["y"].astype("float"),
                         "carry": lc["carry"].to_numpy(), "geo_only": (lo["n_events"] > 0) & (lo["n_observed"] == 0),
                         **{c: per_link[c].to_numpy(dtype=bool) for c in per_link.columns}})
    # zones (S3, S4, OUT, S5)
    zo = T.zone_overflow(geo, start, end)
    zc = T.carry_days(geo, "zone", start, end)
    ol = T.out_label(geo, sources, start, end)
    _aligned((zc, "zone"), (ol, "zone"), ref=(zo, "zone"))
    el = T.zone_elevated(geo, sources, start, end).set_index(["zone", "date"])
    idx = pd.MultiIndex.from_arrays([zo["zone"], zo["date"]])
    parts = []
    for z in ZONES:
        sib = [lk_.id for lk_ in geo.links if lk_.zone != z and lk_.basin in feeding[z]]
        parts.append(pd.DataFrame({"suspect": b_suspect[feeding[z]].any(axis=1), "quiet": ~b_fired[feeding[z]].any(axis=1),
                                   "identity": all(lk_.identity for lk_ in geo.links_into(z)),
                                   "notclean": l_near[sib].any(axis=1) if sib else False}, index=days))
    per_zone = pd.concat(parts, ignore_index=True)
    zone = pd.DataFrame({
        "date": zo["date"], "zone": zo["zone"], "known": zo["known"], "hist_known": zo["hist_known"],
        "y": zo["y"].astype("float"), "carry": zc["carry"].to_numpy(), "geo_only": zo["only_geography"],
        **{c: per_zone[c].to_numpy(dtype=bool) for c in per_zone.columns},
        "sampled": pd.Series(True, index=el.index).reindex(idx, fill_value=False).to_numpy(dtype=bool),
        "s4_y": el["y"].reindex(idx).astype("float").to_numpy(),
        "first_look": el["first_look"].reindex(idx).astype("float").to_numpy(),
        "few": el["few"].reindex(idx).astype("float").to_numpy(),
        "dayof": zo["y"].eq(1).fillna(False).to_numpy(dtype=bool),
        "out_y": ol["y"].astype("float").to_numpy(), "out_why": ol["why"].to_numpy(),
        "overflow": ol["overflow"].astype("float").to_numpy(),
    })
    # gauges (S1 rows; X-S2-OUTAGEIN) over the whole record. The S1 floor reads the other gauge too.
    g = T.gauges()
    status = g.pivot(index="date", columns="series", values="status")
    g = g[(g["date"] >= start) & (g["date"] <= end)]
    if g["date"].nunique() != len(days):
        raise ValueError(f"the gauge record does not cover {start.date()} → {end.date()}")
    other = dict(zip(T.GAUGE_SERIES, reversed(T.GAUGE_SERIES)))
    other_status = pd.concat([status[other[s]].rename(s) if s in other else pd.Series(np.nan, index=status.index, name=s)
                              for s in T.SERIES], axis=1)
    series = pd.DataFrame({"date": g["date"], "series": g["series"], "status": g["status"], "rain": g["rain"],
                           "other": other_status.stack(future_stack=True).reindex(pd.MultiIndex.from_arrays([g["date"], g["series"]])).to_numpy()})
    outage = (status[list(T.GAUGE_SERIES)] == "outage").astype(int).cumsum()
    avg = T.gauge_rain(start - (RUNOFF_DAYS - 1) * one, end)[T.MEAN_SERIES]
    # gauges read hundredths of an inch: a rolling sum's float drift (0.08 + 0 + 0.02 → 0.0999…) must not flip 0.1"
    rain3 = avg.rolling(RUNOFF_DAYS, min_periods=RUNOFF_DAYS).sum().round(6).loc[days]
    blk = T.blocks(end=end).set_index("date").loc[days, ["block", "block_kind"]]
    # X-S5-CIRC: zones fed by a basin whose ledger knows no day of the feed archive (Westside, by the data)
    arch = T.ledger_known(geo, T.ARCHIVE_START, T.ARCHIVE_END)
    blind = {b for b in geo.keys if not arch.loc[arch["basin"] == b, "known"].any()}
    circ = tuple(z for z in ZONES if blind & set(feeding[z]))
    frames = {"series": _index(series, "series"), "basin": _index(basin, "basin"), "link": _index(link, "link"),
              "zone": _index(zone, "zone")}
    n_units = {"series": len(T.SERIES), "basin": len(geo.keys), "link": len(geo.links), "zone": len(ZONES)}
    for ut, f in frames.items():
        if len(f) != n_units[ut] * len(days) or f.index.duplicated().any():
            raise AssertionError(f"context {ut} frame covers {len(f)} unit-days, not {n_units[ut]} × {len(days)}")
    return Context(geo=geo, start=start, end=end, sources=sources, frames=frames, blocks=blk, outage=outage,
                   rain_series={b.key: b.rain_series for b in geo.basins}, postings_end=T.postings_span()[1],
                   suspect=tuple(episodes), circ_zones=circ, rain3=rain3)


def _aligned(*frames, ref) -> None:
    """truth.py's day frames are sorted by unit then date: check before reading them side by side."""
    r, col = ref
    for f, c in frames:
        if len(f) != len(r) or not ((f[c].to_numpy() == r[col].to_numpy()).all() and (f["date"].to_numpy() == r["date"].to_numpy()).all()):
            raise AssertionError(f"truth frames are not aligned by {col} and day")


def context(geo, start=T.TRUTH_START, end=None, sources=SMP.D10_SOURCES, nwp=None, feeds=None, watcher=None,
            rain_series=None, unmasked=None) -> Context:
    """The rules' context for ``geo`` over [start, end] (default end: the last day the ledger grid, the
    gauges and the samples all cover). ``sources``: the lab record S4 and OUT are graded on (default the
    stages' S4 truth, samples.D10_SOURCES). Optional build inputs: nwp (DataFrame date, lead, n_hours: one
    weather model's archive), feeds ({'oracle' | 'archive' | 'degraded[:seed]' | 'watcher': DataFrame
    date, zone, basin}), watcher (Series date → healthy), rain_series ({basin: series}, the set's own
    stamp; default the geography's) and unmasked (stages_entries.issue_time_unmasked(), for S2 lead rows).
    See ``perfect_feed`` and ``archive_feed`` for the two truth feeds."""
    geo = _geo(geo)
    sources = tuple(sources)
    hi = _data_end(sources)
    end = hi if end is None else _ts(end)
    start = _ts(start)
    if end > hi:
        raise ValueError(f"end {end.date()} is past the data end {hi.date()} (ledger grid, gauges and samples)")
    if start > end:
        raise ValueError(f"start {start.date()} is after end {end.date()}")
    ctx = _truth_context(geo, start, end, sources)
    return ctx.with_inputs(nwp=nwp, feeds=feeds, watcher=watcher, rain_series=rain_series, unmasked=unmasked)


def perfect_feed(geo, start=T.TRUTH_START, end=None) -> pd.DataFrame:
    """S5's oracle feed (§3.5, Part B 9): every filed overflow on its day — one row per link onset
    (date, zone, basin, link), the ledger itself, so sibling zone-days are never scored on it."""
    geo = _geo(geo)
    lo = T.link_onsets(geo, start, end)
    lo = lo[lo["y"] == 1]
    return lo[["date", "zone", "basin", "link"]].reset_index(drop=True)


def archive_feed(geo) -> pd.DataFrame:
    """S5's real archive feed: the Poo Bot onsets 2016-03-19 → 2017-01-10 placed in the geography
    (truth.archive_onsets; multi-basin strings already split, Part B 22): date, zone, basin."""
    return T.archive_onsets(_geo(geo))[["date", "zone", "basin"]].reset_index(drop=True)


# ── rows ───────────────────────────────────────────────────────────────────

def skeleton(stage: str, units, start, end, entry: str = "oracle", tier: str = "T2", unit_type: str | None = None) -> pd.DataFrame:
    """A rows frame with every rows.csv.gz column: one row per unit × day, no forecast, no truth.
    For the catalog counts and for tests; a build fills p, y and the rest."""
    code = _code(stage)
    unit_type = unit_type or UNIT_TYPES[code][0]
    days = _days(start, end)
    units = list(units)
    lead = _lead_of(entry)
    f = pd.DataFrame({"date": np.tile(days.to_numpy(), len(units)), "stage": STAGE_IDS[code], "unit_type": unit_type,
                      "unit": np.repeat(units, len(days)), "entry": entry, "lead": np.nan if lead is None else float(lead),
                      "tier": tier, "sel": ""})
    for c in ("p", "q", "b", "v_hat", "y", "y2", "n_sampled"):
        f[c] = np.nan
    f["stratum"] = f["excl"] = f["tags"] = f["variant"] = ""
    return f[list(COLUMNS)]


def _validate(rows: pd.DataFrame, code: str, ctx: Context, table: str) -> pd.DataFrame:
    if table not in TABLES[code]:
        raise KeyError(f"{code} has no table {table!r}; it has {tuple(TABLES[code])}")
    missing = [c for c in COLUMNS if c not in rows.columns and c not in OPTIONAL_COLUMNS]
    if missing:
        raise ValueError(f"rows lack columns {missing}")
    r = rows.copy()
    r["variant"] = r["variant"].fillna("").astype(str) if "variant" in r.columns else ""
    if code != "S5" and (r["variant"] != "").any():
        raise ValueError(f"{code} rows carry variants {sorted(set(r['variant']) - {''})}: only S5 rows have a variant")
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    if r["date"].isna().any():
        raise ValueError("a row has no date")
    stage = set(r["stage"].astype(str))
    if stage - {STAGE_IDS[code]}:
        raise ValueError(f"apply(..., {code!r}) got rows of stage {sorted(stage - {STAGE_IDS[code]})}")
    out = r[(r["date"] < ctx.start) | (r["date"] > ctx.end)]
    if len(out):
        raise ValueError(f"{len(out)} rows fall outside the context's days {ctx.start.date()} → {ctx.end.date()} "
                         f"(e.g. {out['date'].iloc[0].date()}): build the context over the rows' span")
    ut = set(r["unit_type"].astype(str))
    if ut - set(UNIT_TYPES[code]):
        raise ValueError(f"{code} rows are per {UNIT_TYPES[code]}, not {sorted(ut - set(UNIT_TYPES[code]))}")
    for t in ut:
        known = set(ctx.frames[t].index.get_level_values(0))
        bad = set(r.loc[r["unit_type"] == t, "unit"].astype(str)) - known
        if bad:
            raise KeyError(f"{code} {t} units not in {ctx.geo.version}: {sorted(bad)}")
    tiers = set(r["tier"].astype(str))
    if IN_SAMPLE in tiers:
        raise ValueError(f"X-ALL-INSAMPLE: {int((r['tier'] == IN_SAMPLE).sum())} rows are {IN_SAMPLE} (the scored weights saw "
                         "the day); T3 rows are never emitted (protocol §2, §7)")
    if tiers - set(WINDOWS):
        raise ValueError(f"unknown tiers {sorted(tiers - set(WINDOWS))}; known: {WINDOWS}")
    entries = r["entry"].astype(str)
    if code == "S5":
        bad = {e for e in entries if _feed_of(e) not in S5_FEEDS}
        if bad:
            raise ValueError(f"S5 entries name a feed {S5_FEEDS} (seed after a colon), not {sorted(bad)}")
    else:
        allowed = [e for e in ENTRIES if not (code == "S1" and e == "rain")]
        bad = set(entries) - set(allowed)
        if bad:
            raise ValueError(f"{code} entries must be in {tuple(allowed)}, not {sorted(bad)}")
        want = entries.map(_lead_of)
        lead = pd.to_numeric(r["lead"], errors="coerce")
        wrong = (want.isna() & lead.notna()) | (want.notna() & (lead != want.astype(float)))
        if wrong.any():
            i = wrong.idxmax()
            raise ValueError(f"entry {r.at[i, 'entry']!r} with lead {r.at[i, 'lead']!r}: an entry's lead is its number (oracle and rain have none)")
    if code == "S1":
        floor_avg = (entries == "oracle") & (r["unit"] == T.MEAN_SERIES)
        if floor_avg.any():
            raise ValueError("the S1 floor is per gauge (one gauge as a forecast of the other); there is no oracle row for the mean")
    for c in ("sel", "stratum", "excl", "tags"):
        r[c] = r[c].fillna("").astype(str)
    return r


class _View:
    """The rows' columns as arrays, and the context's unit-day values looked up for each row."""

    def __init__(self, r: pd.DataFrame, ctx: Context):
        self.ctx, self.n = ctx, len(r)
        self.date = pd.DatetimeIndex(r["date"])
        self.unit = r["unit"].astype(str).to_numpy()
        self.unit_type = r["unit_type"].astype(str).to_numpy()
        self.entry = r["entry"].astype(str).to_numpy()
        self.feed = np.array([_feed_of(e) for e in self.entry], dtype=object)
        self.lead = pd.to_numeric(r["lead"], errors="coerce").to_numpy()
        self._cache: dict = {}

    def val(self, name: str) -> np.ndarray:
        """The context's ``name`` at each row's (unit, date); _validate has checked every key exists."""
        if name not in self._cache:
            parts = []
            for ut in np.unique(self.unit_type):
                m = self.unit_type == ut
                f = self.ctx.frames[ut]
                if name not in f.columns:
                    raise KeyError(f"{name!r} is not a fact of {ut} rows")
                parts.append((m, f[name].reindex(pd.MultiIndex.from_arrays([self.unit[m], self.date[m]])).to_numpy()))
            if len(parts) == 1:
                out = parts[0][1]
            else:
                out = np.empty(self.n, dtype=object)
                for m, vals in parts:
                    out[m] = vals
            self._cache[name] = out
        return self._cache[name]

    def flag(self, name: str) -> np.ndarray:
        """True where the fact is true; NaN (unknown) is never true."""
        v = self.val(name)
        if v.dtype == bool:
            return v
        if v.dtype == object:
            return np.array([x is not None and not (isinstance(x, float) and math.isnan(x)) and bool(x == 1) for x in v], dtype=bool)
        return v == 1

    def num(self, name: str) -> np.ndarray:
        return pd.to_numeric(pd.Series(self.val(name)), errors="coerce").to_numpy(dtype=float)


# ── the rules: (view, ctx) → bool per row ──────────────────────────────────

def _never(v, ctx):
    return np.zeros(v.n, dtype=bool)


def _s1_status(which: str):
    def fn(v, ctx):
        st, oth = v.val("status"), v.val("other")
        return (st == which) | ((v.entry == "oracle") & (oth == which))
    return fn


def _nwp(gap: bool):
    def fn(v, ctx):
        fc = v.entry != "oracle"
        if not fc.any():
            return np.zeros(v.n, dtype=bool)
        if ctx.nwp is None:
            raise ValueError("S1 forecast rows need the weather model's archive: context(..., nwp=DataFrame[date, lead, n_hours])")
        lead = np.where(fc, v.lead, -1).astype(int)
        hours = ctx.nwp.reindex(pd.MultiIndex.from_arrays([v.date, lead])).to_numpy(dtype=float)
        absent = fc & np.isnan(hours)
        return fc & ~absent & (hours < NWP_DAY_HOURS) if gap else absent
    return fn


def _outage_in(v, ctx):
    """A masked gauge day among the gauge days of the row's input window, on the record its entry read.

    Window: D−29…D for the oracle and rain known; D−29…D−L−1 for lead L; as served, the live frame's 7 past
    days D−L−7…D−L−1. Rain known reads the whole record's gauge_outage_v1 mask (``ctx.outage``). A lead entry
    reads the gauges through its issue day I − 1 as I knew them: the gauge-days the whole record masks but I
    could not yet call an outage (``ctx.unmasked``, stages_entries.issue_time_unmasked) were read as filed, so
    they are not masked days of its window."""
    gauges_of = {T.MEAN_SERIES: T.GAUGE_SERIES, **{g: (g,) for g in T.GAUGE_SERIES}}
    has_lead = ~np.isnan(v.lead)
    lead = np.where(has_lead, v.lead, -1).astype(int)
    last = v.date - pd.to_timedelta(lead + 1, unit="D")                       # oracle / rain: lead −1 → D
    served = np.array([_lead_of(e) is not None and e.endswith("s") for e in v.entry])
    back = np.where(served, lead + AS_SERVED_DAYS, INPUT_DAYS - 1)            # as served: I − 7 = D − L − 7
    first = v.date - pd.to_timedelta(back, unit="D")
    cum = ctx.outage
    pos = lambda d: np.searchsorted(cum.index.to_numpy(), d.to_numpy(), side="right") - 1  # noqa: E731
    i_last, i_before = pos(last), pos(first - pd.Timedelta(days=1))
    n = np.zeros(v.n)
    for b in np.unique(v.unit):
        m = v.unit == b
        for g in gauges_of[ctx.rain_series[b]]:
            c = np.r_[0, cum[g].to_numpy()]                                   # c[i + 1] = outage days through index i
            n[m] += c[i_last[m] + 1] - c[i_before[m] + 1]
    if has_lead.any():
        if ctx.unmasked is None:
            raise ValueError("S2 lead rows read the gauges as their issue day knew them: "
                             "context(..., unmasked=stages_entries.issue_time_unmasked())")
        u = ctx.unmasked
        if len(u):
            issue = (v.date - pd.to_timedelta(lead, unit="D")).to_numpy()
            cells = {k: pd.DatetimeIndex(g["date"]).sort_values().to_numpy() for k, g in u.groupby(["issue", "gauge"])}
            for i in np.flatnonzero(has_lead):
                for g in gauges_of[ctx.rain_series[v.unit[i]]]:
                    d = cells.get((pd.Timestamp(issue[i]), g))
                    if d is not None:
                        n[i] -= np.count_nonzero((d >= first[i].to_datetime64()) & (d <= last[i].to_datetime64()))
        if (n < 0).any():
            raise AssertionError("an issue day's unmasked gauge-day is not a whole-record outage day: the mask and the record disagree")
    return n > 0


def _feed_counts(v, ctx) -> dict:
    """Per row: own (an observation in the row's zone on D−7…D), near (one in a basin feeding the zone,
    elsewhere, same days) and same_day (one in the zone on D), from the row's feed."""
    if "feeds" in v._cache:
        return v._cache["feeds"]
    if ctx.feeds is None:
        raise ValueError("S5 rows need their feeds: context(..., feeds={feed: DataFrame[date, zone, basin]})")
    missing = set(v.entry) - set(ctx.feeds)
    if missing:
        raise KeyError(f"S5 rows on feeds the context lacks: {sorted(missing)}")
    days = _days(ctx.start - pd.Timedelta(days=OBS_DAYS), ctx.end)
    own, near, same = np.zeros(v.n, bool), np.zeros(v.n, bool), np.zeros(v.n, bool)
    feeding = {z: set(T.feeding_basins(ctx.geo, z)) for z in ZONES}
    for name in np.unique(v.entry):
        ev = ctx.feeds[name]
        ev = ev[(ev["date"] >= days[0]) & (ev["date"] <= days[-1])]
        for z in np.unique(v.unit[v.entry == name]):
            m = (v.entry == name) & (v.unit == z)
            in_z = pd.Series(1, index=ev.loc[ev["zone"] == z, "date"]).groupby(level=0).sum().reindex(days, fill_value=0)
            nb = ev[(ev["zone"] != z) & ev["basin"].isin(feeding[z])]
            in_nb = pd.Series(1, index=nb["date"]).groupby(level=0).sum().reindex(days, fill_value=0)
            w = OBS_DAYS + 1
            own[m] = in_z.rolling(w, min_periods=1).sum().reindex(v.date[m]).to_numpy() > 0
            near[m] = in_nb.rolling(w, min_periods=1).sum().reindex(v.date[m]).to_numpy() > 0
            same[m] = in_z.reindex(v.date[m]).to_numpy() > 0
    v._cache["feeds"] = {"own": own, "near": near, "same": same}
    return v._cache["feeds"]


def _health(v, ctx):
    live = v.feed == "watcher"
    if not live.any():
        return np.zeros(v.n, dtype=bool)
    if ctx.watcher is None:
        raise ValueError("S5 watcher rows need the watcher's health: context(..., watcher=Series date → healthy)")
    ok = ctx.watcher.reindex(v.date[live])
    if ok.isna().any():
        raise ValueError(f"the watcher's health is unknown on {ok.index[ok.isna()][0].date()}: unknown is not healthy or unhealthy")
    out = np.zeros(v.n, dtype=bool)
    out[live] = ~ok.to_numpy(dtype=bool)
    return out


def _in(d: pd.DatetimeIndex, lo, hi) -> np.ndarray:
    return np.asarray((d >= lo) & (d <= hi))


FN = {
    "X-ALL-INSAMPLE": _never,                      # _validate raises on any T3 row first
    "X-LEDGER-SUSPECT": lambda v, c: v.flag("suspect"),
    "X-PL-END": lambda v, c: np.asarray(v.date > c.postings_end),
    "X-S1-MISSING": _s1_status("missing"),
    "X-S1-OUTAGE": _s1_status("outage"),
    "X-S1-NWPGAP": _nwp(gap=True),
    "X-S1-NOLEAD": _nwp(gap=False),
    "X-S1-PEAK": lambda v, c: np.full(v.n, not c.peak_truth),
    "X-S2-ARCHIVE": lambda v, c: v.flag("archive"),
    "X-S2-UNCOV": lambda v, c: ~v.flag("known"),
    "X-S2-CARRY": lambda v, c: v.flag("carry"),
    "X-S2-VOLQ": lambda v, c: v.flag("volq"),
    "X-S2-OUTAGEIN": _outage_in,
    "X-S3-UNCOV": lambda v, c: ~v.flag("known"),
    "X-S3-CARRY": lambda v, c: v.flag("carry"),
    "X-S3-QUIET": lambda v, c: v.flag("quiet"),
    "X-S3-ID": lambda v, c: v.flag("identity"),
    "X-S3-GEO": lambda v, c: v.flag("geo_only"),
    "X-S3-NOTCLEAN": lambda v, c: v.flag("notclean"),
    "X-S4-UNSAMPLED": lambda v, c: ~v.flag("sampled"),
    "X-S4-HISTUNK": lambda v, c: ~v.flag("hist_known"),
    "X-S4-RESAMPLE": lambda v, c: v.flag("sampled") & ~v.flag("first_look"),
    "X-S4-DAYOF": lambda v, c: v.flag("sampled") & v.flag("dayof"),
    "X-S4-FEW": lambda v, c: v.flag("sampled") & v.flag("few"),
    "X-S4-ANALYTE": lambda v, c: _in(v.date, *ANALYTE_ERA),
    "X-S5-HEALTH": _health,
    "X-S5-CIRC": lambda v, c: np.isin(v.unit, c.circ_zones) & _in(v.date, T.ARCHIVE_START, T.ARCHIVE_END),
    "X-S5-PERFECT-SIBLING": lambda v, c: ~_feed_counts(v, c)["own"] & _feed_counts(v, c)["near"],
    "X-S5-SELF": lambda v, c: _feed_counts(v, c)["same"],
    "X-S5-QUIET": lambda v, c: ~_feed_counts(v, c)["own"] & ~_feed_counts(v, c)["near"],
    "X-S5-INSAMPLE": lambda v, c: _in(v.date, T.ARCHIVE_START, T.ARCHIVE_END),
    # OUT: the label says what is true (truth.out_label); these say what OUT may grade
    "X-E2E-UNCOV": lambda v, c: ~v.flag("known") | ((v.num("overflow") != 1) & ~v.flag("hist_known")),
    "X-E2E-UNK": lambda v, c: v.val("out_why") == "tail_unsampled",
}
# X-POWER and X-SEL are decided over a unit × window and by the set (in apply); X-S4-FOLLOWUP is for the
# background fit, not for rows (followup_mask).
if set(FN) | {"X-POWER", "X-SEL", "X-S4-FOLLOWUP"} != set(RULES):
    raise AssertionError(f"rules without a function: {sorted(set(RULES) - set(FN) - {'X-POWER', 'X-SEL', 'X-S4-FOLLOWUP'})}")


def order(stage: str, table: str = "score") -> tuple:
    """The first-match order ``apply`` runs for a stage's table: §7's order, S5 then OUT's coverage
    rules (S5 is graded on OUT's label), then the table's own rules."""
    code = _code(stage)
    if table not in TABLES[code]:
        raise KeyError(f"{code} has no table {table!r}")
    seq = list(STAGE_ORDER[code])
    if table == "all_days":
        seq.remove("X-S5-QUIET")
    if code in GRADED_ON:
        seq += [x for x in STAGE_ORDER[GRADED_ON[code]] if x != "X-ALL-INSAMPLE"]
    return tuple(seq) + TABLES[code][table]


def _hits(v: _View, ids, ctx: Context, truth_only: bool) -> dict:
    out = {}
    for x in ids:
        rule = RULES[x]
        if truth_only and rule.needs == "model":
            continue
        m = np.array(FN[x](v, ctx), dtype=bool)          # a copy: the view's cached facts stay as read
        if rule.entries is not None:
            m &= np.isin(v.feed, rule.entries)
        out[x] = m
    return out


def _resolve(rows: pd.DataFrame, code: str, ctx: Context, table: str, truth_only: bool, v: "_View | None" = None) -> pd.DataFrame:
    """excl, tags, stratum (and sel) for validated rows. truth_only skips the rules that need a build's
    rows or inputs (``catalog_counts``) and fills no sel."""
    v = v or _View(rows, ctx)
    seq = order(code, table)
    hits = _hits(v, seq, ctx, truth_only)
    excl = np.full(v.n, "", dtype=object)
    for x in seq:
        if x in hits:
            excl[(excl == "") & hits[x]] = x
    also = [x for x, r in RULES.items() if code in r.stages and r.kind in ("tag", "stratum") and x in FN]
    marks = _hits(v, also, ctx, truth_only)
    rows = rows.copy()
    rows["excl"] = excl
    if not truth_only and code in RULES["X-SEL"].stages:
        given = rows["sel"].to_numpy(dtype=object)
        if ctx.geo.version == "geo_v1":
            sel = selection(v.date, ctx.geo)
            clash = (given != "") & (given != sel)
            if clash.any():
                raise ValueError(f"row sel {given[clash][0]!r} disagrees with X-SEL's {sel[clash][0]!r} on {v.date[clash][0].date()}")
            rows["sel"] = sel
        elif np.isin(given, SELECTED).any():
            raise ValueError(f"X-SEL is for GEO_V1 sets; a {ctx.geo.version} row says {given[np.isin(given, SELECTED)][0]!r}")
        marks["X-SEL"] = np.isin(rows["sel"].to_numpy(dtype=object), SELECTED)
    if not truth_only and code in RULES["X-POWER"].stages:
        marks["X-POWER"] = _power(rows, code, ctx)
    rows["tags"] = _joined([x for x in RULES if RULES[x].kind == "tag" and x in marks], marks, v.n)
    rows["stratum"] = _joined([x for x in RULES if RULES[x].kind == "stratum" and x in marks], marks, v.n)
    return rows


def _joined(ids, marks: dict, n: int) -> np.ndarray:
    """';'-joined ids per row, in catalog order."""
    out = np.full(n, "", dtype=object)
    for x in ids:
        m = marks[x]
        out[m] = np.where(out[m] == "", x, out[m] + ";" + x)
    return out


def _positive(code: str, y: np.ndarray) -> np.ndarray:
    """X-POWER's positives: event days, or wet gauge days (≥ 0.1") for S1."""
    return (y >= T.storm_rule()["wet"]) if code == "S1" else (y == 1)


def _power(rows: pd.DataFrame, code: str, ctx: Context) -> np.ndarray:
    """Fewer than 10 scored positives or 8 storm blocks in the unit × window (× entry): every row of it."""
    scored = rows["excl"].to_numpy() == ""
    y = pd.to_numeric(rows["y"], errors="coerce").to_numpy(dtype=float)
    blk = ctx.blocks.reindex(pd.DatetimeIndex(rows["date"]))
    storm_block = np.where(blk["block_kind"].to_numpy() == "storm", blk["block"].to_numpy(dtype=float), np.nan)
    f = pd.DataFrame({"unit": rows["unit"].to_numpy(), "entry": rows["entry"].to_numpy(), "tier": rows["tier"].to_numpy(),
                      "variant": rows["variant"].to_numpy() if "variant" in rows.columns else "",
                      "pos": scored & _positive(code, y), "blk": np.where(scored, storm_block, np.nan)})
    keys = ["unit", "entry", "tier", "variant"]       # S5's variants each count their own (identical) set
    n_pos = f.groupby(keys)["pos"].transform("sum")
    n_blk = f.groupby(keys)["blk"].transform("nunique")
    return ((n_pos < POWER_MIN_POSITIVES) | (n_blk < POWER_MIN_STORM_BLOCKS)).to_numpy()


def _truth_of(code: str, v: _View) -> np.ndarray:
    col = {"S1": "rain", "S2": "y", "S3": "y", "S4": "s4_y", "S5": "out_y", "OUT": "out_y"}[code]
    return v.num(col)


def apply(rows: pd.DataFrame, stage: str, ctx: Context, table: str = "score") -> pd.DataFrame:
    """The same rows (same order and index) with ``excl`` (the first rule of ``order(stage, table)`` that
    matches, or ''), ``tags`` (';'-joined tag ids), ``stratum`` (';'-joined stratum ids) and, for a GEO_V1
    set, ``sel``. Raises on a T3 row, an unknown stage / unit / entry / tier / table, a row outside the
    context's days, a build input a rule needs and the context lacks, and a scored row whose ``y`` is
    missing or differs from truth.py's (protocol §1) — an unlabelled scored row is a rule gone missing.
    A table='volume' row must be a basin event day. X-POWER is decided over the rows given, per unit ×
    entry × window: pass every row of a window in one call (all nine T2 seasons together, never a fold
    at a time), or a whole window would be tagged by one season's count."""
    code = _code(stage)
    r = _validate(rows, code, ctx, table)
    v = _View(r, ctx)
    out = _resolve(r, code, ctx, table, truth_only=False, v=v)
    scored = out["excl"].to_numpy() == ""
    y = pd.to_numeric(out["y"], errors="coerce").to_numpy(dtype=float)
    want = _truth_of(code, v)
    if table == "volume":
        not_event = want != 1
        if not_event.any():
            raise ValueError(f"the volume table is basin event days; {int(not_event.sum())} rows are not (e.g. "
                             f"{v.unit[not_event][0]} {v.date[not_event][0].date()})")
    lost = scored & np.isnan(y)
    if lost.any():
        raise ValueError(f"{int(lost.sum())} scored {code} rows have no truth (e.g. {v.unit[lost][0]} {v.date[lost][0].date()} "
                         f"{v.entry[lost][0]}): every row a stage cannot grade must carry a rule")
    differs = scored & (np.isnan(want) | (np.abs(y - np.nan_to_num(want, nan=np.inf)) > 1e-9))
    if differs.any():
        i = np.flatnonzero(differs)[0]
        raise ValueError(f"{int(differs.sum())} scored {code} rows disagree with truth.py (e.g. {v.unit[i]} {v.date[i].date()}: "
                         f"row y {y[i]}, truth {want[i]})")
    result = rows.copy()                       # every other column exactly as given
    for c in ("sel", "stratum", "excl", "tags"):
        result[c] = out[c].to_numpy()
    return result


# ── counts ─────────────────────────────────────────────────────────────────

def counts(rows: pd.DataFrame, table: str = "score") -> dict:
    """{'exclusions': {stage: {rule: {unit: n}}}, 'partition': {stage: {unit: {entry: {window: {n_total,
    n_scored, excluded: {rule: n}}}}}}}. Exclusions count each row's first match (``excl``); tags and
    strata are counted on the scored rows, the subsets the score is split into. Asserts the partition
    n_total = n_scored + Σ n_rule for every (stage, unit, entry, window) and that it holds every row,
    that every id is a rule of its kind for its stage and of ``table`` (rows.csv.gz's excl is the primary
    score's: a secondary table's rule there means two tables' rows were mixed), and that no T3 row was
    emitted. A row with no stage, unit, entry or tier raises: no cell would count it."""
    missing = [c for c in ("stage", "unit", "entry", "tier", "excl", "tags", "stratum") if c not in rows.columns]
    if missing:
        raise ValueError(f"rows lack columns {missing}")
    r = rows.copy()
    for c in ("excl", "tags", "stratum"):
        r[c] = r[c].fillna("").astype(str)
    if "variant" in r.columns:
        per_stage = r.assign(_v=r["variant"].fillna("").astype(str)).groupby("stage")["_v"].unique()
        mixed = {s: sorted(v) for s, v in per_stage.items() if len(v) > 1}
        if mixed:
            raise ValueError(f"rows hold several S5 variants {mixed}: count one variant at a time (they share one "
                             "conditional set, so each would be counted once per variant)")
    keys = ["stage", "unit", "entry", "tier"]
    blank = r[keys].isna().any(axis=1) | (r[keys].astype(str) == "").any(axis=1)
    if blank.any():                                    # groupby would drop such a row from every cell, silently
        raise ValueError(f"{int(blank.sum())} rows have no stage, unit, entry or tier (e.g. {r.loc[blank, keys].iloc[0].to_dict()})")
    if (r["tier"].astype(str) == IN_SAMPLE).any():
        raise AssertionError("X-ALL-INSAMPLE: a T3 row was emitted")
    by_rule: dict = {}
    part: dict = {}
    for (stage, unit, entry, tier), g in r.groupby(keys, sort=False):
        code = _code(stage)
        ex = g["excl"].value_counts().to_dict()
        n_scored = int(ex.pop("", 0))
        can = set(order(code, table))                   # KeyError if the stage has no such table
        for x in ex:
            if x not in can:
                raise KeyError(f"{x!r} is not an exclusion {code}'s {table!r} table can carry")
        if n_scored + sum(ex.values()) != len(g):
            raise AssertionError(f"{stage} {unit} {entry} {tier}: {len(g)} rows ≠ {n_scored} scored + {sum(ex.values())} excluded")
        part.setdefault(stage, {}).setdefault(unit, {}).setdefault(entry, {})[tier] = {
            "n_total": int(len(g)), "n_scored": n_scored, "excluded": {x: int(n) for x, n in ex.items()}}
        for x, n in ex.items():
            d = by_rule.setdefault(stage, {}).setdefault(x, {})
            d[unit] = d.get(unit, 0) + int(n)
        scored = g[g["excl"] == ""]
        for col, kinds in (("tags", ("tag",)), ("stratum", ("stratum",))):
            for cell in scored[col]:
                for x in filter(None, cell.split(";")):
                    if x not in RULES or RULES[x].kind not in kinds or code not in RULES[x].stages:
                        raise KeyError(f"{x!r} is not a {kinds[0]} of {code}")
                    d = by_rule.setdefault(stage, {}).setdefault(x, {})
                    d[unit] = d.get(unit, 0) + 1
    n = sum(cell["n_total"] for st in part.values() for u in st.values() for e in u.values() for cell in e.values())
    if n != len(r):
        raise AssertionError(f"the partition holds {n} rows of {len(r)}")
    return {"exclusions": by_rule, "partition": part}


# ── what the public % does not claim (§4.2, Part B 3) ──────────────────────

def _episodes(days: pd.Series) -> int:
    """Runs of days at most samples.RESAMPLE_DAYS apart: an exceedance and the resamples that follow it."""
    d = pd.DatetimeIndex(sorted(days))
    return int(len(d) and 1 + (np.diff(d.to_numpy()).astype("timedelta64[D]").astype(int) > max(SMP.RESAMPLE_DAYS)).sum())


def unmonitored() -> dict:
    """C-UNMON's outfalls: geography-evidence outfalls whose nearest posted station is more than 1.5 km
    away, from the registries' coordinates. {outfall: km to the nearest posted station}."""
    by_id = {s.sfpuc_id: s for s in STATIONS.values()}
    out = {}
    for o in OUTFALLS.values():
        km = min(_km(o.lat, o.lon, by_id[s].lat, by_id[s].lon) for s in o.stations)
        if o.evidence == GEOGRAPHY and km > UNMONITORED_KM:
            out[o.id] = round(km, 2)
    return out


def claims(geo, as_of=AS_OF, sources=SMP.D10_SOURCES) -> dict:
    """What the forecast does not claim, from TRUTH_START to ``as_of`` (§4.2; protocol §7, Part B 3).

      C-DRY     a sampled exceedance with every feeding basin known on D−7…D, no zone overflow on
                D−7…D, and two-gauge rain D−2…D < 0.1" — truth.out_label's 'exceedance_no_overflow'
      C-RUNOFF  the same with rain ≥ 0.1"
                Both stay in OUT's score as negatives of the claim; they are counted, never excluded.
                Days and episodes per zone (an episode: days at most 2 apart, an exceedance and its
                resamples).
      C-OTHER   BeachWatch zone-days filed under another cause, from the ledger's first event to the
                last filing
      C-UNMON   ``unmonitored()`` outfalls and their event days
      C-POSTING, C-TIME  structural (C-TIME notes the events that cross midnight)
    """
    geo = _geo(geo)
    ctx = context(geo, end=as_of, sources=sources)
    z = ctx.frames["zone"].reset_index()
    neg = z[z["out_why"] == "exceedance_no_overflow"]
    rain = ctx.rain3.reindex(pd.DatetimeIndex(neg["date"])).to_numpy()
    if np.isnan(rain).any():
        raise ValueError("two-gauge rain is missing on a C-DRY / C-RUNOFF day: unknown is not dry")
    out = {}
    for cid, m in (("C-DRY", rain < RUNOFF_RAIN_IN), ("C-RUNOFF", rain >= RUNOFF_RAIN_IN)):
        sub = neg[m]
        out[cid] = {"days": {k: int((sub["unit"] == k).sum()) for k in ZONES},
                    "episodes": {k: _episodes(sub.loc[sub["unit"] == k, "date"]) for k in ZONES}}
    ev = T.ledger_events(geo)
    ev = ev[ev["date"] <= ctx.end]
    p = T.postings()
    lo, hi = ev["date"].min(), min(ctx.postings_end, ctx.end)
    oth = p[(p["cause_class"] == "other") & (p["date"] >= lo) & (p["date"] <= hi)]
    out["C-OTHER"] = {"days": {k: int((oth["zone"] == k).sum()) for k in ZONES}, "span": [str(lo.date()), str(hi.date())]}
    far = unmonitored()
    u = ev[ev["outfall"].isin(far)]
    out["C-UNMON"] = {"outfalls": {o: {"km": km, "zone": geo.zone_of_outfall(o), "event_days": int(u.loc[u["outfall"] == o, "date"].nunique())}
                                   for o, km in far.items()},
                      "event_days": int(u["date"].nunique()),
                      "days": {k: int(u.loc[u["zone"] == k, "date"].nunique()) for k in ZONES}}
    out["C-POSTING"] = {"structural": True}
    out["C-TIME"] = {"structural": True, "events_crossing_midnight": int((ev["end"] > ev["date"] + pd.Timedelta(days=1)).sum())}
    if set(out) != set(SP.CLAIMS):
        raise AssertionError(f"claims and stages_spec.CLAIMS differ: {sorted(set(out) ^ set(SP.CLAIMS))}")
    return out


# ── the catalog: every count the truth alone decides (§4.3) ────────────────

def followup_mask(samples: pd.DataFrame) -> np.ndarray:
    """X-S4-FOLLOWUP (fit only): the sample rows of OCEAN#20 / 21 / 22, left out of the background fit."""
    keys = followup_stations()
    return samples["station"].isin(keys).to_numpy()


def followup_stations() -> tuple:
    """The registry keys of the stations protocol §7 names for X-S4-FOLLOWUP."""
    keys = tuple(f"{s}_SL" for s in FOLLOWUP_STATIONS)
    missing = [k for k in keys if k not in STATIONS]
    if missing:
        raise KeyError(f"X-S4-FOLLOWUP stations not in shared/stations.py: {missing}")
    return keys


def _tally(rows: pd.DataFrame, col: str = "excl") -> dict:
    out: dict = {}
    for unit, cell in zip(rows["unit"], rows[col]):
        for x in filter(None, str(cell).split(";")):
            out.setdefault(x, {})
            out[x][unit] = out[x].get(unit, 0) + 1
    return out


def _with_units(d: dict, units) -> dict:
    """Every unit in order, zeros included, so a pinned snapshot shows a zero rather than a gap."""
    return {x: {u: int(n.get(u, 0)) for u in units} for x, n in d.items()}


def catalog_counts(geo, as_of=AS_OF, sources=SMP.D10_SOURCES) -> dict:
    """Every §4.3 count the truth decides alone, per unit, from TRUTH_START to ``as_of``.

    Each stage's skeleton (every unit × day, the oracle entry) goes through the same first-match code as
    a build's rows, without the rules that need model rows. So these are first-match counts — what the
    stage's score would leave out — and tags and strata are counted on the rows left scored:
      s1   X-S1-MISSING, X-S1-OUTAGE per gauge series
      s2   X-S2-ARCHIVE, X-S2-UNCOV, X-LEDGER-SUSPECT, X-S2-CARRY; X-S2-OUTAGEIN (oracle); X-S2-VOLQ in the
           volume table (basin event days)
      s3   zones: X-S3-UNCOV, X-LEDGER-SUSPECT, X-S3-CARRY, X-S3-QUIET, X-S3-ID (oracle); X-S3-GEO on the
           chained entry's scored rows; and in the posting check (zone-days only the zone's own link
           fired, chained entry) X-S3-NOTCLEAN, X-PL-END
      s4   X-S4-UNSAMPLED, X-S4-HISTUNK, X-S4-RESAMPLE; X-S4-DAYOF, X-S4-FEW, X-S4-ANALYTE; X-S4-FOLLOWUP
           (station-days by station)
      s5   X-S5-CIRC on the archive feed's window, and its X-S5-INSAMPLE tag
      out  X-E2E-UNCOV, X-E2E-UNK; X-PL-END in the posting ruler
    X-S1-NWPGAP / NOLEAD, X-S5-HEALTH / PERFECT-SIBLING / SELF / QUIET, X-POWER and X-SEL need a build's
    rows and inputs: ``apply`` decides them there. X-ALL-INSAMPLE is 0 by construction.
    """
    geo = _geo(geo)
    ctx = context(geo, end=as_of, sources=sources)
    lo, hi = ctx.start, ctx.end
    zones = list(ZONES)
    ex: dict = {}

    def run(stage, units, entry="oracle", table="score", keep=None, lo=lo, hi=hi):
        r = skeleton(stage, units, lo, hi, entry=entry)
        if keep is not None:
            r = r[keep(r)].reset_index(drop=True)
        return _resolve(_validate(r, _code(stage), ctx, table), _code(stage), ctx, table, truth_only=True)

    s1 = run("s1", T.SERIES, entry="L1")
    ex["s1"] = _with_units(_tally(s1), T.SERIES)
    s2 = run("s2", geo.keys)
    vol = run("s2", geo.keys, table="volume", keep=lambda r: _at(ctx, "basin", r, "y") == 1)
    ex["s2"] = _with_units({**_tally(s2), **_tally(s2[s2["excl"] == ""], "tags"), **_only(_tally(vol), "X-S2-VOLQ")}, geo.keys)
    s3 = run("s3", zones)
    s3c = run("s3", zones, entry="rain")            # chained: the oracle's identity zones are checked, not scored
    post = run("s3", zones, entry="rain", table="posting", keep=lambda r: _single_zone_days(ctx, r))
    ex["s3"] = _with_units({**_tally(s3), **_tally(s3c[s3c["excl"] == ""], "tags"), **_only(_tally(post), *TABLES["S3"]["posting"])}, zones)
    s4 = run("s4", zones)
    sc4 = s4[s4["excl"] == ""]
    ex["s4"] = _with_units({**_tally(s4), **_tally(sc4, "tags"), **_tally(sc4, "stratum")}, zones)
    smp = T._samples(tuple(sources))                    # truth's cached sample table
    fu = smp[followup_mask(smp) & (smp["date"] >= lo) & (smp["date"] <= hi)]
    ex["s4"]["X-S4-FOLLOWUP"] = {k: int(fu.loc[fu["station"] == k, "date"].nunique()) for k in followup_stations()}
    s5 = run("s5", zones, entry="archive", lo=max(lo, T.ARCHIVE_START), hi=min(hi, T.ARCHIVE_END))
    ex["s5"] = _with_units(_only({**_tally(s5), **_tally(s5, "tags")}, "X-S5-CIRC", "X-S5-INSAMPLE"), zones)
    o = run("out", zones)
    ex["out"] = _with_units({**_tally(o), **_only(_tally(run("out", zones, table="posting")), "X-PL-END")}, zones)
    return {"as_of": str(hi.date()), "start": str(lo.date()), "geography": geo.version, "exclusions": ex,
            "posting_check_days": {z: int((post["unit"] == z).sum()) for z in zones},
            "volume_event_days": {b: int((vol["unit"] == b).sum()) for b in geo.keys},
            "ledger_suspect": [dict(e, reasons=len(e["reasons"])) for e in ctx.suspect]}


def _at(ctx: Context, unit_type: str, rows: pd.DataFrame, col: str) -> np.ndarray:
    return ctx.frames[unit_type][col].reindex(pd.MultiIndex.from_arrays([rows["unit"], pd.DatetimeIndex(rows["date"])])).to_numpy()


def _only(d: dict, *ids) -> dict:
    return {k: v for k, v in d.items() if k in ids}


def _single_zone_days(ctx: Context, rows: pd.DataFrame) -> np.ndarray:
    """S3's posting check rows (§3.3 b): zone-days the zone's own link fired and no sibling zone's link did."""
    lf = ctx.frames["link"]["y"].unstack(0)
    sib_today = np.zeros(len(rows), dtype=bool)
    for z in ZONES:
        m = (rows["unit"] == z).to_numpy()
        sib = [lk.id for lk in ctx.geo.links if lk.zone != z and lk.basin in T.feeding_basins(ctx.geo, z)]
        if sib and m.any():
            sib_today[m] = lf[sib].eq(1).any(axis=1).reindex(pd.DatetimeIndex(rows.loc[m, "date"])).to_numpy(dtype=bool)
    return (_at(ctx, "zone", rows, "y") == 1) & ~sib_today


def figure_counts(catalog: dict, claim: dict | None = None) -> dict:
    """{'exclusions': {id: n}, 'stages': {stage: {id: n}}, 'claims': {id: n}}: the totals the figure's chips
    print (stages_flowchart takes this as ``counts``). An exclusion's total is summed over its units. A chip
    reads its own stage's total ('stages': X-LEDGER-SUSPECT is basin-days in S2's chip, zone-days in S3's);
    the flat 'exclusions' gives a rule two stages share (X-LEDGER-SUSPECT, X-PL-END) its first stage's.
    C-DRY, C-RUNOFF and C-OTHER are days, C-UNMON the outfalls."""
    tot: dict = {}
    by_stage: dict = {}
    for stage, rules in catalog["exclusions"].items():
        for x, units in rules.items():
            if RULES[x].kind != "fit_only":
                by_stage.setdefault(stage, {})[x] = int(sum(units.values()))
                tot.setdefault(x, by_stage[stage][x])
    out = {"exclusions": tot, "stages": by_stage, "claims": {}}
    if claim:
        for cid in ("C-DRY", "C-RUNOFF", "C-OTHER"):
            out["claims"][cid] = int(sum(claim[cid]["days"].values()))
        out["claims"]["C-UNMON"] = len(claim["C-UNMON"]["outfalls"])
    return out


if __name__ == "__main__":
    import json
    for version in G.VERSIONS:
        cat = catalog_counts(version)
        print(f"── {version}, {cat['start']} → {cat['as_of']}")
        for stage, rules in cat["exclusions"].items():
            for x, units in rules.items():
                print(f"  {stage:4} {x:22} {json.dumps(units)}")
        print("  X-LEDGER-SUSPECT episodes:")
        for e in cat["ledger_suspect"]:
            print(f"    {e['basin']:12} {e['start']} → {e['end']} ({e['n_days']} days; {', '.join(e['zones'])}; {e['reasons']} triggers)")
        print("  claims:", json.dumps(claims(version), default=str))
