"""The exclusions engine (features/forecast/src/models/exclusions.py; STAGES_DESIGN.md Part C §4,
Part B 3, 4, 8, 9; STAGES_PROTOCOL.md §7, the authority for ids, kinds and first-match order).

Pins that:
  - the rules are exactly protocol §7's 36 ids, of §7's kinds, and STAGE_ORDER is §7's first-match
    table (parsed from the frozen protocol, not copied);
  - every exclude, tag and stratum rule fires on a hand-built fixture row and not on its near-miss
    twin, and when several match, the first in the protocol's order wins;
  - counts partition the table on a synthetic multi-stage frame and on the committed data, and a T3
    row raises;
  - C-DRY + C-RUNOFF per zone are the sampled exceedance zone-days with no overflow on D−7…D and the
    history known (Part B 3: negatives of the claim, never excluded), counted independently here;
  - X-LEDGER-SUSPECT lists Bayside's February 2026 (Part B 8), and is exactly protocol §7's sentence
    (stages_v2: a trigger in zone z counts when its window holds a wet day and no basin feeding z filed an
    event in it), recomputed here; a dry window and an event at the zone's other basin flag nothing, and no
    zone overflow day is ever suspect;
  - a context's facts on a day do not depend on where it starts (a build over part of the record);
  - oracle and chained rows stay paired (X-S4-HISTUNK on every entry);
  - the catalog counts for both geographies, as of 2026-08-17 (Part B 23). Where a pinned count
    differs from the design's §4 figure, the figure and the reason sit beside the pin.

The fixture context is built by hand (every fact clean, then one set), so each rule is checked as a
pure function of (row, context); the data tests read the committed data under features/forecast/data/.

    venv/bin/python tests/test_exclusions.py
"""
from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "features" / "forecast"
MODELS = FORECAST / "src" / "models"
for p in (ROOT, MODELS, FORECAST / "src" / "collectors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import exclusions as X  # noqa: E402
import stages_spec as SP  # noqa: E402
import truth as T  # noqa: E402
from shared import geography as G  # noqa: E402
from shared.zones import ZONES  # noqa: E402

AS_OF = pd.Timestamp("2026-08-17")             # the committed data's end: every pin below is as of this day
PROTOCOL = FORECAST / "STAGES_PROTOCOL.md"
POSTINGS_PULL = "2026-09-26"                   # the committed BeachWatch pull (its last filing 2026-02-28)

# ── the catalog snapshot (catalog_counts, TRUTH_START → AS_OF), first-match counts, units in order ──
# s1 series (Downtown, Oceanside, mean); s2 basins in the geography's order; s3–out zones (ocean,
# baker_china, north, east); X-S4-FOLLOWUP by station (OCEAN#20, 21, 22). Design §4.3 beside each.
CATALOG_COMMON = {
    # gauge-days 2016-03-01 → AS_OF. Design "all time": 37 / 430 missing and 378 outage days (37 + 341, the same).
    # From the record's start (2016-01-01) to 2026-09-02 the record holds 38 / 431 missing; 2016-01-01 → 02-29
    # holds 1 / 16 of them, so 37 / 415 here.
    ("s1", "X-S1-MISSING"): [37, 415, 0],
    ("s1", "X-S1-OUTAGE"): [37, 341, 0],
    # design: Westside 283 + 374, each Bay basin 183 + 31 (same): every day before the continuous ledger
    # (protocol §1; truth.ledger_start, tests/test_truth.py UNKNOWN_BASIN_DAYS) is unknown
    ("s2", "X-S2-UNCOV"): [374, 31, 31, 31],
    ("s2", "X-S2-ARCHIVE"): [283, 183, 183, 183],
    # design: Ocean / Baker & China 657, North / East 214 (same)
    ("s3", "X-S3-UNCOV"): [657, 657, 214, 214],
    # design: 10 of 20 Baker & China–only days to 2026-02-28 (same: 22 such days to AS_OF, 2 after the last filing)
    ("s3", "X-S3-NOTCLEAN"): [5, 10, 0, 0],
    ("s3", "X-PL-END"): [0, 2, 0, 1],
    # design: 3,371 / 3,339 / 3,349 / 3,061 (same)
    ("s4", "X-S4-UNSAMPLED"): [3371, 3339, 3349, 3061],
    # design: 61 / 65 / 34 / 49 (same; sampled − SAMPLED_HIST_KNOWN in test_truth)
    ("s4", "X-S4-HISTUNK"): [61, 65, 34, 49],
    # Part B 4's rule; no design figure. East resamples half its covered sample-days.
    ("s4", "X-S4-RESAMPLE"): [61, 84, 90, 387],
    # strata on the first-look rows left scored. Design 28 / 24 / 26 / 58 day-of and 36/14/9/8 % few were counted
    # on every sampled day with the history known, before X-S4-RESAMPLE existed (and with feed onsets, East +4).
    ("s4", "X-S4-DAYOF"): [23, 18, 20, 20],
    ("s4", "X-S4-FEW"): [5, 6, 4, 18],
    # tag on the scored zone-days of 2020-07 → 2021; the design's ≈ 848 counts results, not zone-days
    ("s4", "X-S4-ANALYTE"): [78, 81, 80, 75],
    # station-days 2016-03-01 → AS_OF, DataSF + Poo Bot. The design's 46–54 are DataSF alone (46 / 54 / 53); the
    # default sources add 8 Poo Bot station-days each from 2016-03-01 (12 / 12 / 11 from its 2016-01 start)
    ("s4", "X-S4-FOLLOWUP"): [54, 62, 61],
    # design 283 days: the protocol's words are the date range 2016-03-19 → 2017-01-10 (298 days); 283 are the
    # feed's snapshot days in it
    ("s5", "X-S5-CIRC"): [298, 298, 0, 0],
    ("s5", "X-S5-INSAMPLE"): [298, 298, 298, 298],
    # design 664 / 664 / 220 / 220. North / East: the 214 days before 2016-10 and the ledger's first 7 days, whose
    # week reaches back before it (2016-10-01 → 07, none an overflow day), as Ocean's 664 is 657 + Westside's first 7
    ("out", "X-E2E-UNCOV"): [664, 664, 221, 221],
    # design 172 / 224 / 203 / 248: the design counted feed-onset tails (Part C fix 22); the truth is ledger-only
    ("out", "X-E2E-UNK"): [172, 224, 197, 242],
    # the posting ruler after OUT's own rules: 170 days after 2026-02-28 (design), less the uncovered and
    # unsampled-tail days among them
    ("out", "X-PL-END"): [153, 142, 170, 166],
}
# X-LEDGER-SUSPECT (Part B 8; no design figure) as protocol §7 words it under stages_v2: a trigger in zone z counts
# when D−3…D+1 holds a wet day and no basin feeding z filed an event in it; then every feeding basin's known days
# in the window are suspect. So East's two basins are flagged together, on the same 81 days, and the geographies
# agree (20 / 55 / 81 / 81 basin-days, 45 episodes each). No zone overflow day is suspect: S3 East checks all 100
# of its overflow days as identity days. (stages_v1 tested each basin alone and counted dry windows: South 438 /
# Southeast 210 basin-days, and S3 East lost 64 / 14 of its 100 overflow days.)
CATALOG_BY_GEO = {
    "geo_v1": {   # basins westside, north_shore, central (Mission Creek), southeast (Islais + Candlestick)
        ("s2", "X-S2-CARRY"): [3, 4, 16, 14],
        ("s2", "X-LEDGER-SUSPECT"): [20, 55, 81, 81],
        ("s2", "X-S2-OUTAGEIN"): [661, 153, 139, 141],
        ("s2", "X-S2-VOLQ"): [16, 9, 9, 6],
        # design: "all covered days except Westside 65, North 42, East 100" — first match also takes the suspect
        # and carry days; Ocean keeps 63 of Westside's 65 (2 are Ocean Beach carry-overs while Baker & China fired)
        ("s3", "X-S3-QUIET"): [3077, 3080, 3507, 3411],
        ("s3", "X-S3-CARRY"): [5, 0, 4, 16],
        ("s3", "X-LEDGER-SUSPECT"): [20, 20, 55, 81],
        # North Shore's cell is two links in GEO_V1, neither the basin's only one, so no zone-day is identity here.
        # (The served adapter's Crissy Field share is 1 at both sizes, so its North oracle is exact all the same:
        # an open question for the build, not for the truth.)
        ("s3", "X-S3-ID"): [0, 0, 0, 100],
        # design: Baker & China 7 of 54, East 6 of 100 (same; counted on the chained entry's scored rows)
        ("s3", "X-S3-GEO"): [0, 7, 0, 6],
    },
    "sfpuc4_v1": {   # basins westside, north_shore, central, south
        ("s2", "X-S2-CARRY"): [3, 4, 17, 3],     # design §4.3: Central 17, North Shore 4, South 3, Westside 3 (same)
        ("s2", "X-LEDGER-SUSPECT"): [20, 55, 81, 81],
        ("s2", "X-S2-OUTAGEIN"): [661, 153, 140, 141],
        ("s2", "X-S2-VOLQ"): [16, 9, 15, 2],     # basin event days with a blank or '<' volume (5 null + 45 '<' events)
        ("s3", "X-S3-QUIET"): [3077, 3080, 3507, 3411],
        ("s3", "X-S3-CARRY"): [5, 0, 4, 16],
        ("s3", "X-LEDGER-SUSPECT"): [20, 20, 55, 81],
        # design: North Shore 42, Central 97, South 23 link-days checked (zone-days here: North's 42, East's 100)
        ("s3", "X-S3-ID"): [0, 0, 42, 100],
        ("s3", "X-S3-GEO"): [0, 7, 0, 6],        # design: Baker & China 7, East 6 (same)
    },
}
SUSPECT_EPISODES = {"geo_v1": 45, "sfpuc4_v1": 45}
SUSPECT_BASIN_DAYS = {"westside": 20, "north_shore": 55, "central": 81, "south": 81}   # SFPUC4; GEO_V1's the same
# What became of every trigger whose window reaches TRUTH_START, to AS_OF (the same in both geographies): it counted,
# its window was dry (no wet day), or a basin feeding its zone filed an event in the window.
TRIGGER_OUTCOMES = {"cso_posting_onset": {"counted": 39, "dry": 1, "explained": 150},
                    "sample_10x": {"counted": 33, "dry": 29, "explained": 152}}
# Rules whose counts read BeachWatch (X-PL-END, X-LEDGER-SUSPECT's posting triggers) or follow X-LEDGER-SUSPECT
# in first-match order: a BeachWatch pull after POSTINGS_PULL may move them, so they are pinned for that pull only.
POSTING_BOUND = {("s2", "X-S2-CARRY"), ("s2", "X-LEDGER-SUSPECT"), ("s2", "X-S2-OUTAGEIN"), ("s3", "X-S3-QUIET"),
                 ("s3", "X-S3-CARRY"), ("s3", "X-LEDGER-SUSPECT"), ("s3", "X-S3-ID"), ("s3", "X-S3-GEO"),
                 ("s3", "X-S3-NOTCLEAN"), ("s3", "X-PL-END"), ("out", "X-PL-END")}

# claims (TRUTH_START → AS_OF), per zone. Design §4.2: C-DRY 10 / 45 / 38 / 121, C-RUNOFF 4 / 10 / 17 / 49
# (294 = 14 / 55 / 55 / 170). East +4 (1 dry, 3 runoff) are exceedances in the tails of the feed onsets the design
# counted as overflows (2016-10-24, 12-23, 12-24, 12-25). Baker & China 2023-05-08 had exactly 0.10" on D−2…D
# (0.08 + 0 + 0.02): runoff by the rule (≥ 0.1"), dry in the design, whose rolling sum read 0.0999…. (stages_v1
# also counted Bayside's 2016 "no discharge" months: North 39 / 17, East 126 / 53.)
C_DRY = {"days": {"ocean": 10, "baker_china": 44, "north": 38, "east": 122}, "episodes": {"ocean": 9, "baker_china": 38, "north": 32, "east": 87}}
C_RUNOFF = {"days": {"ocean": 4, "baker_china": 11, "north": 17, "east": 52}, "episodes": {"ocean": 4, "baker_china": 9, "north": 15, "east": 33}}
C_OTHER = {"ocean": 17, "baker_china": 129, "north": 135, "east": 417}            # design 698 (same), 2016-10-16 → 2026-02-28
C_UNMON = {"CSD-004": 1.7, "CSD-017": 2.6, "CSD-018": 2.5, "CSD-037": 1.8}        # design's km; 70 event-days (same)


def _pull_is_committed() -> bool:
    import beachwatch as BW
    return json.loads(BW.MANIFEST.read_text())["fetched_at"].startswith(POSTINGS_PULL)


def _s7() -> str:
    return PROTOCOL.read_text().split("## 7.", 1)[1].split("\n## ", 1)[0]


def _ids(text: str) -> list:
    return re.findall(r"X-[A-Z0-9]+(?:-[A-Z0-9]+)*", text)


# ── the fixture: a hand-built context, every fact clean ─────────────────────

FX_START, FX_END = pd.Timestamp("2016-03-01"), pd.Timestamp("2026-01-31")
D = pd.Timestamp("2020-01-15")
A = pd.Timestamp("2016-06-01")              # inside the feed archive window
TRUTH_COL = {"S1": ("series", "rain"), "S2": ("basin", "y"), "S3": ("zone", "y"), "S4": ("zone", "s4_y"),
             "S5": ("zone", "out_y"), "OUT": ("zone", "out_y")}


def fixture(geo="sfpuc4_v1", facts=(), outage=(), postings_end=None, nwp=None, feeds=None, watcher=None, peak_truth=False) -> X.Context:
    """A context where nothing fires, then ``facts`` [(unit_type, unit, day, column, value)] set on top.
    ``outage`` [(gauge, day)] are gauge_outage_v1 days; nwp holds every (day, lead 0–5) at 24 hours unless given."""
    geo = G.get(geo)
    days = pd.date_range(FX_START, FX_END, name="date")

    def frame(units, cols):
        idx = pd.MultiIndex.from_product([list(units), days], names=["unit", "date"])
        return pd.DataFrame({c: np.full(len(idx), v, dtype=type(v) if not isinstance(v, str) else object) for c, v in cols.items()}, index=idx)
    frames = {
        "series": frame(T.SERIES, {"status": "ok", "other": "ok", "rain": 0.0}),
        "basin": frame(geo.keys, {"known": True, "archive": False, "y": 0.0, "volq": False, "carry": False, "suspect": False}),
        "link": frame([lk.id for lk in geo.links], {"known": True, "y": 0.0, "carry": False, "suspect": False, "quiet": False,
                                                    "identity": False, "geo_only": False, "notclean": False}),
        "zone": frame(ZONES, {"known": True, "hist_known": True, "y": 0.0, "carry": False, "suspect": False, "quiet": False,
                              "identity": False, "geo_only": False, "notclean": False, "sampled": True, "s4_y": 0.0,
                              "first_look": 1.0, "few": 0.0, "dayof": False, "out_y": 0.0, "out_why": "clean_sample",
                              "overflow": 0.0}),
    }
    frames["series"].loc[T.MEAN_SERIES, "other"] = np.nan
    for ut, unit, day, col, val in facts:
        frames[ut].loc[(unit, pd.Timestamp(day)), col] = val
    gdays = pd.date_range(FX_START - pd.Timedelta(days=60), FX_END)
    out = pd.DataFrame(0, index=gdays, columns=list(T.GAUGE_SERIES))
    for g, day in outage:
        out.loc[pd.Timestamp(day), g] = 1
    if nwp is None:
        nwp = pd.DataFrame([(d, lead, 24) for d in pd.date_range(D - pd.Timedelta(days=40), D + pd.Timedelta(days=5)) for lead in range(6)],
                           columns=["date", "lead", "n_hours"])
    ctx = X.Context(geo=geo, start=FX_START, end=FX_END, sources=(), frames=frames,
                    blocks=pd.DataFrame({"block": np.arange(len(days)), "block_kind": "quiet"}, index=days), outage=out.cumsum(),
                    rain_series={b.key: b.rain_series for b in geo.basins}, postings_end=postings_end or FX_END, suspect=(),
                    circ_zones=("ocean", "baker_china"), rain3=pd.Series(0.0, index=days), peak_truth=peak_truth)
    return ctx.with_inputs(nwp=nwp, feeds=feeds, watcher=watcher)


def row(ctx: X.Context, stage: str, unit: str, day=D, entry="oracle", tier="T2", unit_type=None, y=None) -> pd.DataFrame:
    """One rows.csv.gz row, its y read off the context's truth (what a build writes)."""
    code = X._code(stage)
    r = X.skeleton(code, [unit], day, day, entry=entry, tier=tier, unit_type=unit_type)
    ut, col = TRUTH_COL[code]
    ut = unit_type or ut
    r["y"] = ctx.frames[ut].loc[(unit, pd.Timestamp(day)), "y" if ut == "link" else col] if y is None else y
    return r


def _obs(*events) -> pd.DataFrame:
    return pd.DataFrame(list(events), columns=["date", "zone", "basin"])


ALL_FEEDS = ("oracle", "archive", "degraded", "degraded:0", "watcher")


# Each case: a rule, its stage, unit, entry and table, and two fixtures — fixture() kwargs, the row's own changes
# under 'row' — for the row it fires on and its near-miss twin.
def _cases() -> list:
    b, z = "basin", "zone"
    one = pd.Timedelta(days=1)
    near = _obs((D - one, "ocean", "westside"))                    # an observation in the row's own zone the day before
    feeds = lambda ev: {f: ev for f in ALL_FEEDS}  # noqa: E731
    C = []

    def case(rule, stage, unit, hit, twin, entry="oracle", table="score", unit_type=None):
        C.append(dict(rule=rule, stage=stage, unit=unit, hit=hit, twin=twin, entry=entry, table=table, unit_type=unit_type))

    # S1 (a forecast row at lead 1; the floor is the oracle row)
    case("X-S1-MISSING", "s1", "SF Downtown", dict(facts=[("series", "SF Downtown", D, "status", "missing")]), {}, entry="L1")
    case("X-S1-MISSING", "s1", "SF Downtown", dict(facts=[("series", "SF Downtown", D, "other", "missing")]),
         dict(facts=[("series", "SF Downtown", D, "other", "missing")], row=dict(entry="L1")))    # the floor reads the other gauge
    case("X-S1-OUTAGE", "s1", "SF Oceanside", dict(facts=[("series", "SF Oceanside", D, "status", "outage")]), {}, entry="L1")
    gap = pd.DataFrame([(D, 1, 23)], columns=["date", "lead", "n_hours"])
    case("X-S1-NWPGAP", "s1", T.MEAN_SERIES, dict(nwp=gap), dict(nwp=gap.assign(n_hours=24)), entry="L1")
    case("X-S1-NOLEAD", "s1", T.MEAN_SERIES, dict(nwp=gap.assign(lead=2, n_hours=24)), dict(nwp=gap.assign(n_hours=24)), entry="L1")
    case("X-S1-PEAK", "s1", "SF Downtown", {}, dict(peak_truth=True), entry="L1", table="peak")
    # S2
    case("X-S2-ARCHIVE", "s2", "westside", dict(facts=[(b, "westside", D, "known", False), (b, "westside", D, "archive", True)]),
         dict(facts=[(b, "westside", D, "known", False)]))                                         # unknown, not the feed: X-S2-UNCOV
    case("X-S2-UNCOV", "s2", "central", dict(facts=[(b, "central", D, "known", False)]), {})
    case("X-LEDGER-SUSPECT", "s2", "south", dict(facts=[(b, "south", D, "suspect", True)]), dict(facts=[(b, "south", D - one, "suspect", True)]))
    case("X-S2-CARRY", "s2", "north_shore", dict(facts=[(b, "north_shore", D, "carry", True)]), dict(facts=[(b, "north_shore", D + one, "carry", True)]))
    case("X-S2-VOLQ", "s2", "central", dict(facts=[(b, "central", D, "y", 1.0), (b, "central", D, "volq", True)]),
         dict(facts=[(b, "central", D, "y", 1.0)]), table="volume")
    case("X-S2-OUTAGEIN", "s2", "westside", dict(outage=[("SF Oceanside", D - pd.Timedelta(days=29))]),
         dict(outage=[("SF Oceanside", D - pd.Timedelta(days=30))]))                               # one day before the 30-day window
    case("X-S2-OUTAGEIN", "s2", "central", dict(outage=[("SF Downtown", D)]),
         dict(outage=[("SF Downtown", D)], row=dict(entry="L1")))                                  # at lead 1 day D is the forecast's
    case("X-S2-OUTAGEIN", "s2", "central", dict(outage=[("SF Downtown", D - pd.Timedelta(days=6))]),
         dict(outage=[("SF Downtown", D - pd.Timedelta(days=7))], row=dict(entry="L1s")), entry="L1s")   # as served: 7 days
    case("X-S2-OUTAGEIN", "s2", "westside", dict(outage=[("SF Oceanside", D)]),
         dict(outage=[("SF Oceanside", D)], row=dict(unit="central")))                              # Bayside reads Downtown only
    # S3
    case("X-S3-UNCOV", "s3", "east", dict(facts=[(z, "east", D, "known", False)]), {})
    case("X-LEDGER-SUSPECT", "s3", "north", dict(facts=[(z, "north", D, "suspect", True)]), {})
    case("X-S3-CARRY", "s3", "ocean", dict(facts=[(z, "ocean", D, "carry", True)]), {})
    case("X-S3-CARRY", "s3", "westside>ocean", dict(facts=[("link", "westside>ocean", D, "carry", True)]), {}, unit_type="link")
    case("X-S3-QUIET", "s3", "baker_china", dict(facts=[(z, "baker_china", D, "quiet", True)]),
         dict(facts=[(z, "baker_china", D, "quiet", True)], row=dict(entry="rain")))                # oracle only
    case("X-S3-ID", "s3", "north", dict(facts=[(z, "north", D, "identity", True)]),
         dict(facts=[(z, "north", D, "identity", True)], row=dict(entry="L1")))                     # oracle skill only
    case("X-S3-GEO", "s3", "baker_china", dict(facts=[(z, "baker_china", D, "geo_only", True), (z, "baker_china", D, "y", 1.0)]),
         dict(facts=[(z, "baker_china", D, "y", 1.0)]), entry="rain")
    case("X-S3-NOTCLEAN", "s3", "baker_china", dict(facts=[(z, "baker_china", D, "notclean", True)]), {}, entry="rain", table="posting")
    case("X-PL-END", "s3", "ocean", dict(postings_end=D - one), dict(postings_end=D), entry="rain", table="posting")
    # S4
    case("X-S4-UNSAMPLED", "s4", "east", dict(facts=[(z, "east", D, "sampled", False)]), {})
    case("X-S4-HISTUNK", "s4", "north", dict(facts=[(z, "north", D, "hist_known", False)]), {})
    for e in ("rain", "L1", "L0s"):                                                                # every entry (§7)
        case("X-S4-HISTUNK", "s4", "north", dict(facts=[(z, "north", D, "hist_known", False)]), {}, entry=e)
    case("X-S4-RESAMPLE", "s4", "ocean", dict(facts=[(z, "ocean", D, "first_look", 0.0)]), {})
    case("X-S4-DAYOF", "s4", "east", dict(facts=[(z, "east", D, "dayof", True)]), dict(facts=[(z, "east", D - one, "dayof", True)]))
    case("X-S4-FEW", "s4", "east", dict(facts=[(z, "east", D, "few", 1.0)]), {})
    case("X-S4-ANALYTE", "s4", "ocean", dict(row=dict(day="2020-07-01")), dict(row=dict(day="2020-06-30")))
    # S5 (the entry is the feed; an observation the day before keeps a row in the conditional set)
    case("X-S5-HEALTH", "s5", "ocean", dict(feeds=feeds(near), watcher=pd.Series(False, index=[D])),
         dict(feeds=feeds(near), watcher=pd.Series(True, index=[D])), entry="watcher")
    case("X-S5-CIRC", "s5", "ocean", dict(feeds=feeds(_obs((A - one, "ocean", "westside")))),
         dict(feeds=feeds(_obs((A - one, "north", "north_shore"))), row=dict(unit="north")), entry="archive")
    case("X-S5-PERFECT-SIBLING", "s5", "ocean", dict(feeds=feeds(_obs((D - one, "baker_china", "westside")))),
         dict(feeds=feeds(_obs((D - one, "baker_china", "westside"))), row=dict(entry="degraded:0")))
    case("X-S5-PERFECT-SIBLING", "s5", "ocean", dict(feeds=feeds(_obs((D - one, "baker_china", "westside")))),
         dict(feeds=feeds(_obs((D - one, "baker_china", "westside"), (D - 2 * one, "ocean", "westside")))))   # its own zone was seen too
    case("X-S5-SELF", "s5", "ocean", dict(feeds=feeds(_obs((D, "ocean", "westside")))), dict(feeds=feeds(near)))
    case("X-S5-QUIET", "s5", "ocean", dict(feeds=feeds(_obs((D - pd.Timedelta(days=8), "ocean", "westside")))),
         dict(feeds=feeds(_obs((D - pd.Timedelta(days=7), "ocean", "westside")))))                 # D−7 is in, D−8 is not
    case("X-S5-INSAMPLE", "s5", "north", dict(feeds=feeds(_obs((T.ARCHIVE_END - one, "north", "north_shore"))), row=dict(day=T.ARCHIVE_END)),
         dict(feeds=feeds(_obs((T.ARCHIVE_END, "north", "north_shore"))), row=dict(day=T.ARCHIVE_END + one)), entry="archive")
    # OUT
    case("X-E2E-UNCOV", "out", "north", dict(facts=[(z, "north", D, "known", False)]), {})
    case("X-E2E-UNCOV", "out", "east", dict(facts=[(z, "east", D, "hist_known", False)]),                 # a quiet day's week unknown …
         dict(facts=[(z, "east", D, "hist_known", False), (z, "east", D, "overflow", 1.0), (z, "east", D, "out_why", "overflow"),
                     (z, "east", D, "out_y", 1.0)]))                                                   # … an overflow day needs only D
    case("X-E2E-UNK", "out", "ocean", dict(facts=[(z, "ocean", D, "out_why", "tail_unsampled"), (z, "ocean", D, "out_y", np.nan)]),
         dict(facts=[(z, "ocean", D, "out_why", "quiet")]))
    case("X-PL-END", "out", "east", dict(postings_end=D - one), dict(postings_end=D), table="posting")
    return C


def _run(c: dict, side: str) -> pd.Series:
    spec = dict(c[side])
    rkw = spec.pop("row", {})
    ctx = fixture(**spec)
    day = pd.Timestamp(rkw.get("day", A if c["entry"] == "archive" else D))
    r = row(ctx, c["stage"], rkw.get("unit", c["unit"]), day=day, entry=rkw.get("entry", c["entry"]), unit_type=c["unit_type"])
    return X.apply(r, c["stage"], ctx, table=c["table"]).iloc[0]


def _fired(rule: str, out: pd.Series) -> bool:
    kind = X.RULES[rule].kind
    col = {"exclude": "excl", "tag": "tags", "stratum": "stratum"}[kind]
    return rule in out[col].split(";") if col != "excl" else out["excl"] == rule


# ── the rules are the protocol's ────────────────────────────────────────────

def test_import_reads_no_data():
    """No module-level IO: importing exclusions opens nothing under features/forecast/data (nor the protocol)."""
    code = ("import builtins, io, sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
            "real = io.open\n"
            "def spy(f, *a, **k):\n"
            "    if '/features/forecast/' in str(f) and not str(f).endswith('.py'): raise AssertionError(f'opened {f} at import')\n"
            "    return real(f, *a, **k)\n"
            "builtins.open = io.open = spy\n"
            "import exclusions\n"
            "assert exclusions._truth_context.cache_info().currsize == 0\n"
            "print('inert')") % (str(MODELS), str(ROOT))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("inert"), r.stderr[-800:]


def test_rules_are_the_protocols_36_with_its_kinds():
    table = {m.group(1): m.group(2) for m in re.finditer(r"^\| (X-[A-Z0-9-]+)([^|]*)\|", _s7(), re.M)}
    assert set(table) == set(X.RULES) and len(X.RULES) == 36, set(table) ^ set(X.RULES)
    marker = {"(tag)": "tag", "(stratum)": "stratum", "(fit only)": "fit_only"}
    for x, rest in table.items():
        want = next((k for m, k in marker.items() if m in rest), "exclude")
        assert X.RULES[x].kind == want, (x, X.RULES[x].kind, want)
    for x, r in X.RULES.items():                                     # the words are stages_spec's, read not copied
        assert r.chip == SP.EXCLUSIONS[x]["chip"] and r.plain == SP.EXCLUSIONS[x]["plain"], x
    assert "X-E2E-SCOPE" not in X.RULES                              # Part B 3


def test_first_match_order_is_the_protocol_table():
    """§7's first-match table, parsed from the frozen protocol, is STAGE_ORDER; its 'also' column is the
    secondary tables, tags, strata and fit-only rules of the same stage; order() appends OUT's rules to S5."""
    rows = {m.group(1): (_ids(m.group(2)), _ids(m.group(3))) for m in re.finditer(r"^\| (S[1-5]|OUT) \| ([^|]*)\| ([^|]*)\|", _s7(), re.M)}
    assert {c: tuple(first) for c, (first, _) in rows.items()} == X.STAGE_ORDER
    for code, (first, also) in rows.items():
        secondary = {x for t, ids in X.TABLES[code].items() for x in ids}
        for x in also:
            assert code in X.RULES[x].stages, (code, x)
            assert x in secondary or X.RULES[x].kind in ("tag", "stratum", "fit_only"), (code, x)
        assert secondary <= set(also), (code, secondary - set(also))
        assert X.order(code) == tuple(first) + (tuple(y for y in X.STAGE_ORDER["OUT"] if y != "X-ALL-INSAMPLE") if code == "S5" else ())
    assert "X-S5-QUIET" not in X.order("S5", "all_days") and X.order("S3", "posting")[-2:] == ("X-S3-NOTCLEAN", "X-PL-END")
    # "(oracle only)", "(oracle skill only)": gated to the oracle entry
    assert X.RULES["X-S3-QUIET"].entries == X.RULES["X-S3-ID"].entries == ("oracle",)


# ── every rule on a fixture row and its near-miss twin ──────────────────────

def test_every_rule_fires_on_its_fixture_and_not_on_its_twin():
    covered = set()
    for c in _cases():
        hit, twin = _run(c, "hit"), _run(c, "twin")
        assert _fired(c["rule"], hit), (c["rule"], "did not fire", hit[["excl", "tags", "stratum"]].to_dict())
        assert not _fired(c["rule"], twin), (c["rule"], "fired on its twin", twin[["excl", "tags", "stratum"]].to_dict())
        if X.RULES[c["rule"]].kind == "exclude":
            assert c["rule"] not in twin["tags"] + twin["stratum"]
        covered.add(c["rule"])
    # X-POWER and X-SEL have their own tests below, X-ALL-INSAMPLE raises, X-S4-FOLLOWUP is fit-only
    want = {x for x, r in X.RULES.items() if r.kind in ("exclude", "tag", "stratum")} - {"X-POWER", "X-SEL", "X-ALL-INSAMPLE"}
    assert covered == want, want ^ covered


def test_the_first_match_wins_in_protocol_order():
    """On one row every rule of a stage's order from k on is true: the k-th is written (S2, S3, S4, OUT)."""
    unit = {"S2": ("basin", "central"), "S3": ("zone", "north"), "S4": ("zone", "east"), "OUT": ("zone", "ocean")}
    fact = {"X-S2-ARCHIVE": [("archive", True), ("known", False)], "X-S2-UNCOV": [("known", False)], "X-LEDGER-SUSPECT": [("suspect", True)],
            "X-S2-CARRY": [("carry", True)], "X-S3-UNCOV": [("known", False)], "X-S3-CARRY": [("carry", True)],
            "X-S3-QUIET": [("quiet", True)], "X-S3-ID": [("identity", True)], "X-S4-UNSAMPLED": [("sampled", False)],
            "X-S4-HISTUNK": [("hist_known", False)], "X-S4-RESAMPLE": [("first_look", 0.0)],
            "X-E2E-UNCOV": [("known", False)], "X-E2E-UNK": [("out_why", "tail_unsampled")]}
    for code, (ut, u) in unit.items():
        seq = [x for x in X.STAGE_ORDER[code] if x != "X-ALL-INSAMPLE"]
        for k, first in enumerate(seq):
            facts = [(ut, u, D, col, val) for x in seq[k:] for col, val in fact[x]]
            ctx = fixture(facts=facts)
            got = X.apply(row(ctx, code, u, y=np.nan), code, ctx).iloc[0]["excl"]
            assert got == first, (code, seq[k:], got)
    # S1: missing before outage (the floor reads both gauges), and either before the weather model's rules
    gap = pd.DataFrame([(D, 1, 3)], columns=["date", "lead", "n_hours"])
    ctx = fixture(facts=[("series", "SF Downtown", D, "status", "outage"), ("series", "SF Downtown", D, "other", "missing")], nwp=gap)
    assert X.apply(row(ctx, "s1", "SF Downtown", y=np.nan), "s1", ctx).iloc[0]["excl"] == "X-S1-MISSING"
    assert X.apply(row(ctx, "s1", "SF Downtown", entry="L1", y=np.nan), "s1", ctx).iloc[0]["excl"] == "X-S1-OUTAGE"
    ctx = fixture(nwp=gap)
    assert X.apply(row(ctx, "s1", "SF Downtown", entry="L1", y=np.nan), "s1", ctx).iloc[0]["excl"] == "X-S1-NWPGAP"
    # S5: the feed's own rules come before OUT's coverage rules; HEALTH and CIRC before SELF
    unknown = [("zone", "ocean", D, "known", False), ("zone", "ocean", A, "known", False)]
    seen = {f: _obs((D, "ocean", "westside"), (A, "ocean", "westside")) for f in ALL_FEEDS}
    ctx = fixture(facts=unknown, feeds=seen, watcher=pd.Series(False, index=[D]))
    for entry, day, want in (("watcher", D, "X-S5-HEALTH"), ("archive", A, "X-S5-CIRC"), ("oracle", D, "X-S5-SELF")):
        assert X.apply(row(ctx, "s5", "ocean", day=day, entry=entry, y=np.nan), "s5", ctx).iloc[0]["excl"] == want, entry
    ctx = fixture(facts=unknown, feeds={f: _obs((D - pd.Timedelta(days=2), "ocean", "westside")) for f in ALL_FEEDS})
    assert X.apply(row(ctx, "s5", "ocean", entry="degraded", y=np.nan), "s5", ctx).iloc[0]["excl"] == "X-E2E-UNCOV"


def test_power_and_selection_tags():
    """X-POWER: fewer than 10 scored positives or 8 storm blocks in the unit × window (× entry) tags every
    row of it. X-SEL: a GEO_V1 set's days in the holdout (from 2023-07-01) and post-training windows."""
    days = pd.date_range("2020-01-01", "2020-01-31")
    pos = days[:10]
    ctx = fixture(facts=[("basin", "central", d, "y", 1.0) for d in pos])
    blocks = ctx.blocks.copy()
    blocks.loc[pos, "block_kind"] = "storm"
    blocks.loc[pos, "block"] = np.arange(10) + 10 ** 6         # 10 storm blocks, one per positive day
    ctx = dataclasses.replace(ctx, blocks=blocks)
    rows = X.skeleton("s2", ["central"], days[0], days[-1])
    rows["y"] = ctx.frames["basin"].loc["central"].loc[days, "y"].to_numpy()
    out = X.apply(rows, "s2", ctx)
    assert not out["tags"].str.contains("X-POWER").any()
    rows2 = rows.copy()
    rows2.loc[rows2.index[-1], "tier"] = "T1"                     # another window: one row, no positive
    two = X.apply(rows2, "s2", ctx)
    assert two["tags"].str.contains("X-POWER").tolist() == [False] * 30 + [True]
    sub = dataclasses.replace(ctx, blocks=blocks.assign(block=np.where(blocks["block_kind"] == "storm", blocks["block"] % 7, blocks["block"])))
    assert X.apply(rows, "s2", sub)["tags"].str.contains("X-POWER").all(), "7 storm blocks"
    carry = fixture(facts=[("basin", "central", d, "y", 1.0) for d in pos] + [("basin", "central", pos[0], "carry", True)])
    carry = dataclasses.replace(carry, blocks=blocks)                # a positive the score leaves out does not count
    assert X.apply(rows, "s2", carry)["tags"].str.contains("X-POWER").all(), "9 scored positives"
    # X-SEL
    edge = [pd.Timestamp(d) for d in ("2023-06-30", "2023-07-01", "2025-10-31", "2025-11-01", "2026-01-31")]
    for geo, want in (("geo_v1", ["", "holdout_selected", "holdout_selected", "post_selected", "post_selected"]), ("sfpuc4_v1", [""] * 5)):
        ctx = fixture(geo)
        r = pd.concat([row(ctx, "out", "east", day=d) for d in edge], ignore_index=True)
        out = X.apply(r, "out", ctx)
        assert out["sel"].tolist() == want, (geo, out["sel"].tolist())
        assert out["tags"].str.contains("X-SEL").tolist() == [bool(w) for w in want]
    assert X.freeze_date() == pd.Timestamp("2026-10-01")
    for geo, sel in (("sfpuc4_v1", "post_selected"), ("geo_v1", "holdout_selected")):
        ctx = fixture(geo)
        r = row(ctx, "s2", "central", day="2025-12-01")
        r["sel"] = sel
        _raises(ValueError, X.apply, r, "s2", ctx)
    ctx = fixture("geo_v1")
    s1 = X.apply(row(ctx, "s1", "SF Downtown", day="2025-12-01", entry="L1"), "s1", ctx).iloc[0]
    assert "X-SEL" not in s1["tags"] and s1["sel"] == "", "S1 is set-independent"


# ── it raises instead of guessing ───────────────────────────────────────────

def _raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


def test_a_t3_row_raises_and_unknowns_raise():
    ctx = fixture()
    r = row(ctx, "s2", "central")
    _raises(ValueError, X.apply, r.assign(tier="T3"), "s2", ctx)                    # X-ALL-INSAMPLE: zero by construction
    _raises(AssertionError, X.counts, X.apply(r, "s2", ctx).assign(tier="T3"))
    _raises(ValueError, X.apply, r.assign(tier="T9"), "s2", ctx)
    _raises(KeyError, X.apply, r, "s9", ctx)
    _raises(ValueError, X.apply, r, "s3", ctx)                                       # rows of another stage
    _raises(KeyError, X.apply, r.assign(unit="southeast"), "s2", ctx)                # a GEO_V1 key under SFPUC4
    _raises(ValueError, X.apply, r.assign(entry="L7"), "s2", ctx)
    _raises(ValueError, X.apply, r.assign(entry="L2", lead=1.0), "s2", ctx)
    _raises(ValueError, X.apply, r.assign(date=FX_END + pd.Timedelta(days=1)), "s2", ctx)   # outside the context
    _raises(KeyError, X.apply, r, "s2", ctx, table="posting")
    _raises(ValueError, X.apply, r.drop(columns="tags"), "s2", ctx)
    _raises(ValueError, X.apply, r.assign(y=np.nan), "s2", ctx)                      # a scored row with no truth
    _raises(ValueError, X.apply, r.assign(y=1.0), "s2", ctx)                         # … or the wrong one
    _raises(ValueError, X.apply, r, "s2", ctx, table="volume")                       # not an event day
    _raises(ValueError, X.apply, row(ctx, "s1", T.MEAN_SERIES), "s1", ctx)           # the floor is per gauge
    _raises(ValueError, X.apply, row(ctx, "s1", "SF Downtown", entry="rain"), "s1", ctx)
    _raises(ValueError, X.apply, row(ctx, "s1", "SF Downtown", entry="L1"), "s1", dataclasses.replace(ctx, nwp=None))
    _raises(ValueError, X.apply, row(ctx, "s5", "ocean"), "s5", ctx)                 # no feeds
    ctx5 = fixture(feeds={f: _obs() for f in ("oracle", "archive")})
    _raises(KeyError, X.apply, row(ctx5, "s5", "ocean", entry="degraded:3"), "s5", ctx5)
    _raises(ValueError, X.apply, row(ctx5, "s5", "ocean", entry="sideways"), "s5", ctx5)
    ctx5 = fixture(feeds={"watcher": _obs()})
    _raises(ValueError, X.apply, row(ctx5, "s5", "ocean", entry="watcher"), "s5", ctx5)   # health unknown
    _raises(KeyError, fixture, feeds={"oracle": _obs((D, "mars", None))})
    _raises(KeyError, X.order, "S4", "volume")
    _raises(KeyError, X.context, "geo_v2")
    _raises(TypeError, X.context, None)
    _raises(ValueError, X.context, "sfpuc4_v1", end="2030-01-01")                    # past the data end
    bad = X.apply(r, "s2", ctx).assign(excl="X-S4-UNSAMPLED")
    _raises(KeyError, X.counts, bad)
    _raises(KeyError, X.counts, X.apply(r, "s2", ctx).assign(tags="X-S3-GEO"))
    vol = fixture(facts=[("basin", "central", D, "y", 1.0), ("basin", "central", D, "volq", True)])
    vq = X.apply(row(vol, "s2", "central"), "s2", vol, table="volume")
    assert X.counts(vq, table="volume")["exclusions"]["s2"]["X-S2-VOLQ"] == {"central": 1}
    _raises(KeyError, X.counts, vq)                                                   # a volume-table rule among score rows
    _raises(KeyError, X.counts, vq, table="posting")                                  # S2 has no posting table
    for col, blank in (("tier", np.nan), ("unit", None), ("entry", "")):              # a row no cell would hold
        two = pd.concat([X.apply(r, "s2", ctx), X.apply(r, "s2", ctx).assign(**{col: blank})], ignore_index=True)
        _raises(ValueError, X.counts, two)


def test_apply_returns_the_same_rows():
    ctx = fixture(facts=[("zone", "east", D, "known", False)])
    r = pd.concat([row(ctx, "out", z) for z in ZONES], ignore_index=True)
    r.index = [10, 11, 12, 13]
    r["p"] = [0.1, 0.2, 0.3, 0.4]
    r["date"] = r["date"].dt.strftime("%Y-%m-%d")                 # as read back from rows.csv.gz
    out = X.apply(r, "out", ctx)
    assert list(out.index) == [10, 11, 12, 13] and list(out.columns) == list(r.columns)
    pd.testing.assert_frame_equal(out.drop(columns=["excl", "tags", "stratum", "sel"]), r.drop(columns=["excl", "tags", "stratum", "sel"]))
    assert out["excl"].tolist() == ["", "", "", "X-E2E-UNCOV"]


# ── counts partition the table ──────────────────────────────────────────────

def test_counts_partition_a_synthetic_multi_stage_frame():
    """Random facts on the fixture, every stage, several entries and windows: each row lands in exactly
    one cell (scored, or its first rule), counts() asserts n_total = n_scored + Σ n_rule, and its numbers
    match a tally made here."""
    rng = np.random.default_rng(0)
    days = pd.date_range("2020-01-01", "2020-03-31")
    facts = []
    flip = {"known": False, "hist_known": False, "sampled": False}          # the clean value is True; every other fact's is False
    for ut, units, cols in (("basin", G.SFPUC4_V1.keys, ("known", "archive", "carry", "suspect")),
                            ("zone", list(ZONES), ("known", "hist_known", "carry", "suspect", "quiet", "identity", "sampled", "geo_only"))):
        for u in units:
            for col in cols:
                for d in days[rng.random(len(days)) < 0.15]:
                    facts.append((ut, u, d, col, flip.get(col, True)))
    for d in days[rng.random(len(days)) < 0.1]:
        facts.append(("zone", "east", d, "first_look", 0.0))
        facts.append(("zone", "north", d, "out_why", "tail_unsampled"))
    for d in days[rng.random(len(days)) < 0.1]:
        facts.append(("series", "SF Oceanside", d, "status", "missing"))
    ctx = fixture(facts=facts, feeds={"oracle": _obs((days[5], "ocean", "westside"), (days[40], "east", "central")),
                                      "degraded:1": _obs((days[6], "baker_china", "westside"))})
    frames = []
    for code, units, entries in (("s1", T.GAUGE_SERIES, ("oracle", "L1")), ("s2", G.SFPUC4_V1.keys, ("oracle", "L1", "L1s")),
                                 ("s3", list(ZONES), ("oracle", "rain")), ("s4", list(ZONES), ("oracle", "rain")),
                                 ("s5", list(ZONES), ("oracle", "degraded:1")), ("out", list(ZONES), ("rain", "L1"))):
        for entry in entries:
            r = X.skeleton(code, units, days[0], days[-1], entry=entry)
            r["tier"] = np.where(r["date"] < days[45], "T2", "T1")
            ut, col = TRUTH_COL[X._code(code)]
            r["y"] = ctx.frames[ut][col].reindex(pd.MultiIndex.from_arrays([r["unit"], r["date"]])).to_numpy()
            frames.append(X.apply(r, code, ctx))
    rows = pd.concat(frames, ignore_index=True)
    c = X.counts(rows)
    seen = 0
    for (stage, unit, entry, tier), g in rows.groupby(["stage", "unit", "entry", "tier"]):
        cell = c["partition"][stage][unit][entry][tier]
        assert cell["n_total"] == len(g) == cell["n_scored"] + sum(cell["excluded"].values())
        assert cell["n_scored"] == int((g["excl"] == "").sum())
        assert cell["excluded"] == {k: int(v) for k, v in g.loc[g["excl"] != "", "excl"].value_counts().items()}
        seen += len(g)
    assert seen == len(rows)
    tally = rows[rows["excl"] != ""].groupby(["stage", "excl", "unit"]).size()
    for (stage, x, unit), n in tally.items():
        assert c["exclusions"][stage][x][unit] == n
    fired = set(rows["excl"]) - {""}
    assert {"X-S1-MISSING", "X-S2-ARCHIVE", "X-S2-UNCOV", "X-LEDGER-SUSPECT", "X-S2-CARRY", "X-S3-UNCOV", "X-S3-QUIET", "X-S3-ID",
            "X-S4-UNSAMPLED", "X-S4-HISTUNK", "X-S4-RESAMPLE", "X-S5-SELF", "X-S5-QUIET", "X-S5-PERFECT-SIBLING", "X-E2E-UNCOV",
            "X-E2E-UNK"} <= fired, fired
    # paired comparisons use the intersection: oracle and chained S3 rows differ only by the oracle-only rules
    s3 = rows[rows["stage"] == "s3"].pivot_table(index=["unit", "date", "tier"], columns="entry", values="excl", aggfunc="first")
    differ = s3[s3["oracle"] != s3["rain"]]
    assert set(differ["oracle"]) <= {"X-S3-QUIET", "X-S3-ID"} and set(differ["rain"]) == {""}


def test_counts_partition_the_committed_data():
    """Every stage of the served geography on the committed data, y from truth.py as a build writes it:
    the partition holds and the truth check passes on every scored row."""
    ctx = X.context("geo_v1", end=AS_OF)
    frames = []
    for code, units, col, ut in (("s2", ctx.geo.keys, "y", "basin"), ("s3", list(ZONES), "y", "zone"),
                                 ("s4", list(ZONES), "s4_y", "zone"), ("out", list(ZONES), "out_y", "zone")):
        for entry in ("oracle", "L1"):
            r = X.skeleton(code, units, ctx.start, ctx.end, entry=entry)
            r["tier"] = np.where(r["date"] >= X.POST_START, "T1", np.where(r["date"] >= X.HOLDOUT_START, "T1-holdout", "T2"))
            r["y"] = ctx.frames[ut][col].reindex(pd.MultiIndex.from_arrays([r["unit"], r["date"]])).to_numpy()
            frames.append(X.apply(r, code, ctx))
    rows = pd.concat(frames, ignore_index=True)
    c = X.counts(rows)
    n = sum(cell["n_total"] for st in c["partition"].values() for u in st.values() for e in u.values() for cell in e.values())
    assert n == len(rows)
    sel = rows.drop_duplicates(["date"]).set_index("date")["sel"]
    assert (sel[sel.index < X.HOLDOUT_START] == "").all() and (sel[sel.index >= X.POST_START] == "post_selected").all()
    # oracle and chained stay paired: S2, S4 (X-S4-HISTUNK on every entry) and OUT leave out the same rows on
    # every entry; S3 differs only by its oracle-only rules
    for stage in ("s2", "s4", "out", "s3"):
        p = rows[rows["stage"] == stage].pivot_table(index=["unit", "date"], columns="entry", values="excl", aggfunc="first")
        differ = p[p["oracle"] != p["L1"]]
        if stage == "s3":
            assert set(differ["oracle"]) <= {"X-S3-QUIET", "X-S3-ID"} and set(differ["L1"]) == {""}, stage
        else:
            assert differ.empty, (stage, differ.head())
    assert (rows.loc[rows["stage"] == "s4", "excl"] == "X-S4-HISTUNK").groupby(rows["entry"]).sum().nunique() == 1
    # the catalog is the same first-match code: the oracle entry's S2 exclusions agree with it
    cat = X.catalog_counts("geo_v1")
    s2 = rows[(rows["stage"] == "s2") & (rows["entry"] == "oracle")]
    for x, units in cat["exclusions"]["s2"].items():
        if X.RULES[x].kind == "exclude" and x != "X-S2-VOLQ":
            assert units == {u: int(((s2["unit"] == u) & (s2["excl"] == x)).sum()) for u in ctx.geo.keys}, x


# ── what the public % does not claim (Part B 3) ─────────────────────────────

def test_dry_and_runoff_are_the_exceedances_with_no_overflow_in_the_week():
    """C-DRY + C-RUNOFF per zone = sampled exceedance zone-days with every feeding basin known on D−7…D and
    no zone overflow on D−7…D, counted here from zone_elevated and zone_overflow; the split is the two-gauge
    rain on D−2…D. They stay in OUT's score as good days (negatives of the claim): no rule excludes them."""
    geo = G.SFPUC4_V1
    cl = X.claims(geo)
    el = T.zone_elevated(geo, end=AS_OF)
    zo = T.zone_overflow(geo, start=T.TRUTH_START - pd.Timedelta(days=7), end=AS_OF)
    on = zo["y"].eq(1).fillna(False).to_numpy(dtype=bool)
    over = set(zip(zo.loc[on, "zone"], zo.loc[on, "date"]))
    rain = T.gauge_rain(T.TRUTH_START - pd.Timedelta(days=2), AS_OF)["avg"]
    dry, runoff = {z: 0 for z in ZONES}, {z: 0 for z in ZONES}
    for z, d, y, hk in zip(el["zone"], el["date"], el["y"], el["hist_known"]):
        if y and hk and not any((z, d - pd.Timedelta(days=k)) in over for k in range(8)):
            (dry if round(rain.loc[d - pd.Timedelta(days=2):d].sum(), 6) < 0.1 else runoff)[z] += 1
    assert cl["C-DRY"]["days"] == dry and cl["C-RUNOFF"]["days"] == runoff, (cl["C-DRY"]["days"], dry, cl["C-RUNOFF"]["days"], runoff)
    assert cl["C-DRY"] == C_DRY and cl["C-RUNOFF"] == C_RUNOFF, (cl["C-DRY"], cl["C-RUNOFF"])
    # in OUT's score: the out label is good there, and OUT's rules leave them scored
    ctx = X.context(geo, end=AS_OF)
    zf = ctx.frames["zone"].reset_index()
    neg = zf[zf["out_why"] == "exceedance_no_overflow"]
    assert len(neg) == sum(dry.values()) + sum(runoff.values()) and (neg["out_y"] == 0).all()
    r = X.skeleton("out", list(ZONES), ctx.start, ctx.end, entry="rain")
    r["y"] = ctx.frames["zone"]["out_y"].reindex(pd.MultiIndex.from_arrays([r["unit"], r["date"]])).to_numpy()
    out = X.apply(r, "out", ctx).set_index(["unit", "date"])
    assert (out.loc[list(zip(neg["unit"], neg["date"])), "excl"] == "").all()
    for c in ("C-DRY", "C-RUNOFF"):                     # an episode is an exceedance and its resamples (≤ 2 days apart)
        assert all(cl[c]["episodes"][z] <= cl[c]["days"][z] for z in ZONES)


def test_the_other_claims():
    for version in G.VERSIONS:
        cl = X.claims(version)
        assert set(cl) == set(SP.CLAIMS)
        u = cl["C-UNMON"]
        assert {o: round(v["km"], 1) for o, v in u["outfalls"].items()} == C_UNMON, u["outfalls"]
        assert u["event_days"] == 70 and {o: v["zone"] for o, v in u["outfalls"].items()} == {
            "CSD-004": "baker_china", "CSD-017": "north", "CSD-018": "east", "CSD-037": "east"}
        assert cl["C-POSTING"]["structural"] and cl["C-TIME"]["structural"] and cl["C-TIME"]["events_crossing_midnight"] == 122
        if _pull_is_committed():
            assert cl["C-OTHER"]["days"] == C_OTHER and cl["C-OTHER"]["span"] == ["2016-10-16", "2026-02-28"], cl["C-OTHER"]
    assert set(X.unmonitored()) == set(C_UNMON)        # from the registry's coordinates; observed outfalls never count


# ── X-LEDGER-SUSPECT (Part B 8) ─────────────────────────────────────────────

def test_bayside_february_2026_is_suspect():
    """Bayside filed no event in February 2026 while it rained, East and North were posted for CSO on the 17th and
    East read 10× the standard from the 16th to the 20th: every Bay-side basin is listed, East's two together —
    Central and South (Central and Southeast under GEO_V1) 13 → 21 February, North Shore 14 → 18 February."""
    want = {"east": ("2026-02-13", "2026-02-21"), "north": ("2026-02-14", "2026-02-18")}
    for version, bay in (("sfpuc4_v1", ("north_shore", "central", "south")), ("geo_v1", ("north_shore", "central", "southeast"))):
        eps = X.ledger_suspect(version, end=AS_OF)
        feb = [e for e in eps if e["start"] <= "2026-02-17" <= e["end"]]
        assert sorted(e["basin"] for e in feb) == sorted(bay), (version, feb)
        for e in feb:
            zone = "north" if e["basin"] == "north_shore" else "east"
            assert e["zones"] == [zone] and (e["start"], e["end"]) == want[zone], (version, e)
            whys = {(r["zone"], r["why"]) for r in e["reasons"]}
            assert (zone, "sample_10x") in whys or zone == "north", (version, e)
            if _pull_is_committed():
                assert (zone, "cso_posting_onset") in whys, (version, e)
        assert not any(e["basin"] == "westside" and e["start"] <= "2026-02-17" <= e["end"] for e in eps), "Westside filed events that week"
        # every flagged day is ledger_known, has no event for its basin, and has a reason
        b = T.basin_onsets(version, end=AS_OF).set_index(["basin", "date"])
        for e in eps:
            assert e["reasons"] and {r["why"] for r in e["reasons"]} <= {"cso_posting_onset", "sample_10x"}
            for d in pd.date_range(e["start"], e["end"]):
                assert b.loc[(e["basin"], d), "known"] and b.loc[(e["basin"], d), "y"] == 0, (version, e["basin"], d)
    # one rule, read on the zone: East's two basins are flagged on the same days, and the geographies agree
    days = lambda eps: {(e["basin"], d) for e in eps for d in pd.date_range(e["start"], e["end"])}  # noqa: E731
    s4, v1 = days(X.ledger_suspect("sfpuc4_v1", end=AS_OF)), days(X.ledger_suspect("geo_v1", end=AS_OF))
    assert {d for bk, d in s4 if bk == "central"} == {d for bk, d in s4 if bk == "south"}
    assert {({"south": "southeast"}.get(bk, bk), d) for bk, d in s4} == v1
    if _pull_is_committed():
        assert {k: sum(1 for bk, _ in s4 if bk == k) for k in G.SFPUC4_V1.keys} == SUSPECT_BASIN_DAYS


def test_no_zone_overflow_day_is_ever_suspect():
    """Under stages_v2 an event at any basin feeding the zone explains a trigger for all of them, and zones that share
    a basin share all their feeding basins, so neither a basin event day nor a zone overflow day is suspect, in either
    geography: S3 keeps every East overflow day (stages_v1 left out 64 under SFPUC4, 14 under GEO_V1)."""
    for version in G.VERSIONS:
        geo = G.get(version)
        feeding = {z: set(T.feeding_basins(geo, z)) for z in ZONES}
        assert all(feeding[a] == feeding[b] for a in ZONES for b in ZONES if feeding[a] & feeding[b]), version
        ctx = X.context(geo, end=AS_OF)
        z, b = ctx.frames["zone"], ctx.frames["basin"]
        assert z["suspect"].any() and b["suspect"].any(), version            # not empty by accident
        lost = z[z["suspect"].to_numpy(dtype=bool) & (z["y"] == 1).to_numpy()]
        assert lost.empty, (version, list(lost.index[:5]))
        assert not (b["suspect"].to_numpy(dtype=bool) & (b["y"] == 1).to_numpy()).any(), version
        r = X.skeleton("s3", ["east"], ctx.start, ctx.end, entry="rain")
        r["y"] = z.loc["east", "y"].reindex(pd.DatetimeIndex(r["date"])).to_numpy()
        out = X.apply(r, "s3", ctx)
        over = (r["y"] == 1).to_numpy()
        assert over.sum() == 100 and (out.loc[over, "excl"] == "").all(), (version, out.loc[over, "excl"].value_counts().to_dict())


def _suspect_on(trigger_rows, events=(), wet=(), unrecorded=(), geo="sfpuc4_v1"):
    """X._suspect's flagged (basin, day) set on a hand-built January 2020: triggers [(day, zone, why)], ledger events
    [(basin, day)], wet days (0.5" two-gauge mean, else 0.0) and days with no rain record; every basin is known."""
    geo = G.get(geo)
    lo, hi = pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-31")
    rain = pd.Series(0.0, index=pd.date_range("2019-12-01", hi, name="date"))
    rain[pd.DatetimeIndex(list(wet))] = 0.5
    rain[pd.DatetimeIndex(list(unrecorded))] = np.nan
    fired = {(bk, pd.Timestamp(d)) for bk, d in events}

    def onsets(g, start, end):
        dd = pd.date_range(start, end, name="date")
        return pd.concat([pd.DataFrame({"date": dd, "basin": bk, "known": True,
                                        "y": pd.array([int((bk, d) in fired) for d in dd], dtype="Int8")}) for bk in g.keys],
                         ignore_index=True)

    def blocks(start=None, end=None):
        r = rain.loc[:end]
        return pd.DataFrame({"date": r.index, "rain": r.to_numpy(), "wet": (r >= 0.1).to_numpy()})

    def triggers(g, sources, start, end):
        t = pd.DataFrame([(pd.Timestamp(d), z, w, "fixture") for d, z, w in trigger_rows], columns=["date", "zone", "why", "detail"])
        return t[(t["date"] >= start) & (t["date"] <= end)].reset_index(drop=True)
    real = X._triggers, T.basin_onsets, T.blocks
    try:
        X._triggers, T.basin_onsets, T.blocks = triggers, onsets, blocks
        return X._suspect(geo, (), lo, hi)[1]
    finally:
        X._triggers, T.basin_onsets, T.blocks = real


def test_a_dry_window_and_an_explained_trigger_flag_nothing():
    """The rule's two conditions on a fixture: a 10× sample with no wet day in D−3…D+1 is a dry-weather exceedance and
    flags nothing; one wet day on D−3 or D+1 makes it count (D−4 and D+2 do not); a day with no rain record in an
    otherwise dry window raises. A CSO posting in East that Central's event explains flags neither East basin, even
    South, which filed nothing; an event outside the window, or at a basin that does not feed East, explains nothing."""
    D = pd.Timestamp("2020-01-15")
    day = lambda k: D + pd.Timedelta(days=k)  # noqa: E731
    win = {(bk, day(k)) for bk in ("central", "south") for k in range(-3, 2)}
    tenx = [(D, "east", "sample_10x")]
    assert _suspect_on(tenx) == set(), "a dry-window 10× sample is C-DRY, not a missed overflow"
    assert _suspect_on(tenx, wet=[day(-3)]) == _suspect_on(tenx, wet=[day(1)]) == win
    assert _suspect_on(tenx, wet=[day(-4), day(2)]) == set()
    try:
        _suspect_on(tenx, unrecorded=[day(-1)])
        raise AssertionError("an unrecorded day in a dry window must raise: unknown is not dry")
    except ValueError:
        pass
    assert _suspect_on(tenx, wet=[D], unrecorded=[day(-1)]) == win, "a wet day decides it whatever the other days hold"
    post = [(D, "east", "cso_posting_onset")]
    assert _suspect_on(post, wet=[D]) == win
    assert _suspect_on(post, wet=[D], events=[("central", day(-2))]) == set(), "Central's event explains East: South is not flagged"
    assert _suspect_on(post, wet=[D], events=[("south", day(1))]) == set()
    assert _suspect_on(post, wet=[D], events=[("central", day(2))]) == win, "D+2 is outside the window"
    assert _suspect_on(post, wet=[D], events=[("westside", D), ("north_shore", D)]) == win, "basins that do not feed East"
    # a trigger whose window reaches back before the context's start still flags the days inside it
    assert _suspect_on([(pd.Timestamp("2020-01-01"), "north", "sample_10x")], wet=["2019-12-29"]) == \
        {("north_shore", pd.Timestamp("2020-01-01")), ("north_shore", pd.Timestamp("2020-01-02"))}
    assert _suspect_on(post, wet=[D], geo="geo_v1") == {({"south": "southeast"}.get(bk, bk), d) for bk, d in win}


def test_ledger_suspect_is_the_protocols_sentence():
    """X-LEDGER-SUSPECT recomputed here from protocol §7's words (stages_v2), on the committed data: a trigger (a
    CSO-cause posting onset, or a sample ≥ 10× the standard) in zone z on day D counts when D−3…D+1 holds a wet day
    (the masked two-gauge mean ≥ 0.10") and no basin feeding z has a ledger event in D−3…D+1 (all read as of AS_OF);
    then every feeding basin's ledger_known days in D−3…D+1 are suspect. The context's basin facts are exactly that
    set, and a zone row is suspect when any of its feeding basins' days is."""
    lo = T.TRUTH_START - pd.Timedelta(days=1)                     # the first trigger whose window reaches TRUTH_START
    rain = T.gauge_rain(lo - pd.Timedelta(days=3), AS_OF)[T.MEAN_SERIES]
    assert rain.notna().all()
    wet = set(rain.index[rain >= 0.1])
    for version in G.VERSIONS:
        geo = G.get(version)
        b = T.basin_onsets(geo, lo - pd.Timedelta(days=3), AS_OF)
        on = b["y"].eq(1).fillna(False).to_numpy(dtype=bool)
        fired = set(zip(b.loc[on, "basin"], b.loc[on, "date"]))
        known = set(zip(b.loc[b["known"], "basin"], b.loc[b["known"], "date"]))
        p = T.postings()
        p = p[(p["cause_class"] == "cso") & p["class_onset"] & (p["date"] >= lo) & (p["date"] <= AS_OF)]
        el = T.zone_elevated(geo, start=lo, end=AS_OF)
        el = el[el["max_ratio"] >= 10]
        want, tally = set(), {}
        for why, z, day in [("cso_posting_onset", z, d) for z, d in zip(p["zone"], p["date"])] + \
                           [("sample_10x", z, d) for z, d in zip(el["zone"], el["date"])]:
            win = [d for d in pd.date_range(day - pd.Timedelta(days=3), day + pd.Timedelta(days=1)) if d <= AS_OF]
            feeding = {lk.basin for lk in geo.links if lk.zone == z}
            if not wet & set(win):
                outcome = "dry"
            elif any((bk, d) in fired for bk in feeding for d in win):
                outcome = "explained"
            else:
                outcome = "counted"
                want |= {(bk, d) for bk in feeding for d in win if d >= T.TRUTH_START and (bk, d) in known}
            tally.setdefault(why, {}).setdefault(outcome, 0)
            tally[why][outcome] += 1
        ctx = X.context(geo, end=AS_OF)
        f = ctx.frames["basin"]
        assert set(f.index[f["suspect"].to_numpy(dtype=bool)]) == want, version
        zf = ctx.frames["zone"]
        for z in ZONES:
            feeding = T.feeding_basins(geo, z)
            got = set(zf.loc[z].index[zf.loc[z, "suspect"].to_numpy(dtype=bool)])
            assert got == {d for bk, d in want if bk in feeding}, (version, z)
        if _pull_is_committed():
            assert tally == TRIGGER_OUTCOMES, (version, tally)


def test_notclean_reads_two_days_either_side():
    """X-S3-NOTCLEAN on the committed data, recomputed: a link of another zone fed by the same basin fired on
    D−2…D+2 (read as of AS_OF). North and East have no such sibling in either geography."""
    for version in G.VERSIONS:
        geo = G.get(version)
        ctx = X.context(geo, end=AS_OF)
        lo = T.link_onsets(geo, T.TRUTH_START - pd.Timedelta(days=2), AS_OF)
        on = lo["y"].eq(1).fillna(False).to_numpy(dtype=bool)
        fired = set(zip(lo.loc[on, "link"], lo.loc[on, "date"]))
        zf = ctx.frames["zone"]
        for z in ZONES:
            sib = [lk.id for lk in geo.links if lk.zone != z and lk.basin in T.feeding_basins(geo, z)]
            want = [any((s, d + pd.Timedelta(days=k)) in fired for s in sib for k in range(-2, 3)) for d in zf.loc[z].index]
            assert zf.loc[z, "notclean"].tolist() == want, (version, z)
            assert bool(sib) == (z in ("ocean", "baker_china")), (version, z, sib)


def test_a_contexts_facts_do_not_depend_on_its_start():
    """A context built from a later start holds, day for day, the facts of the full record's context: a build
    whose rows start mid-record (a holdout refit, one season) leaves out exactly what the full record would.
    The starts sit where X-LEDGER-SUSPECT reads its window before the start: on 2016-10-29 an East event the day
    before explains an East sample after it (a clipped window would flag Central and South 29 → 31 October), and
    on 2026-02-20 the wet days before it make Bayside's February triggers count (a clipped window would drop
    Central and South on 20 and 21 February)."""
    for version in G.VERSIONS:
        full = X.context(version, end=AS_OF)
        for start in ("2016-10-29", "2026-02-20"):
            part = X.context(version, start=start, end=AS_OF)
            for ut, f in part.frames.items():
                g = full.frames[ut].loc[f.index]
                for c in f.columns:
                    same = (f[c] == g[c]) | (f[c].isna() & g[c].isna())
                    assert same.all(), (version, start, ut, c, list(f.index[~same.to_numpy()][:3]))
            assert part.blocks.equals(full.blocks.loc[part.blocks.index]) and part.rain3.equals(full.rain3.loc[part.rain3.index])


# ── the catalog snapshot, both geographies, as of AS_OF ─────────────────────

def test_catalog_counts_as_of():
    committed = _pull_is_committed()
    for version in G.VERSIONS:
        cat = X.catalog_counts(version, as_of=AS_OF)
        assert cat["as_of"] == "2026-08-17" and cat["start"] == "2016-03-01" and cat["geography"] == version
        got = {(st, x): list(units.values()) for st, rules in cat["exclusions"].items() for x, units in rules.items()}
        want = {**CATALOG_COMMON, **CATALOG_BY_GEO[version]}
        assert set(got) == set(want), (version, set(got) ^ set(want))
        for k, v in want.items():
            if committed or k not in POSTING_BOUND:
                assert got[k] == v, (version, k, got[k], v)
        assert list(cat["exclusions"]["s2"]["X-S2-UNCOV"]) == list(G.get(version).keys)
        assert list(cat["exclusions"]["s3"]["X-S3-UNCOV"]) == list(ZONES)
        if committed:
            assert cat["posting_check_days"] == {"ocean": 11, "baker_china": 22, "north": 42, "east": 100}
            assert len(cat["ledger_suspect"]) == SUSPECT_EPISODES[version]
        # the chips bind: figure_counts feeds the figure's NOT SCORED and DOES NOT CLAIM rows
        import stages_flowchart as F
        fc = X.figure_counts(cat, X.claims(version))
        svg = F.render(version, None, fc)[0]
        for words in ("no filing 467", "feed archive 832", "days nobody sampled 13,120", "resamples 622", "overflow history unknown 209",
                      "unsampled days after one 835", "dry-weather exceedances 214", "rain runoff, no overflow 84",
                      "shore far from a station 4"):
            assert words in svg, (version, words)


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
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
