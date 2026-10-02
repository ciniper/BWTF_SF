"""The five-stage forecast as plain data: the stages, what each is graded on and
what each score leaves out, and figure 1 (the flowchart) as nodes and edges on a
grid. The single source for the figure, its phone list, the stages report and
the Model check tabs (STAGES_DESIGN.md §1, as amended by Parts A and B).

    S1 rain → S2 basin overflow → S3 zone overflow → S4 water quality → OUT,
    with S5 live corrections under S3–S4 and the risk levels under OUT.

Every stage is scored twice: on its true input (oracle) and on the real output
of the stage before it (chained). Text is public copy: plain, short, no rule
ids (they go in tooltips only). Two geographies draw the same figure with
their own words (Part B 15): ``sfpuc4_v1`` (SFPUC's four basins, observations
after the split) and ``geo_v1`` (the served set: our four basins, a flag enters
at the basin). Any field may be ``by_geo(sfpuc4, geo_v1)``; ``nodes(geo)`` and
``edges(geo)`` resolve them.

No IO, and one import: the risk bands come from shared/risk_levels.py, the
one table every page uses. The renderer (stages_flowchart.py) reads the
geography and the zones; this file only says.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[4]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from shared import risk_levels as _RL  # noqa: E402

GEOS = ("sfpuc4_v1", "geo_v1")

# The scoring protocol this code implements: the version STAGES_PROTOCOL.md's
# title names, and what a score's manifest records (manifest.protocol =
# "<version>@<sha>"). Not the pipeline name ("stages_v1") that served.json
# stamps carry.
PROTOCOL_VERSION = "stages_v2"


def by_geo(sfpuc4, geo_v1) -> dict:
    return {"sfpuc4_v1": sfpuc4, "geo_v1": geo_v1}


def pick(v, geo: str):
    """A by_geo value resolved for `geo`; anything else passes through."""
    if isinstance(v, dict) and set(v) == set(GEOS):
        return v[geo]
    return v


# Fixed public risk levels on the whole percent the page shows (Chase,
# 2026-10-01, A3): (word, lowest %, highest %, colour). Threshold scores are
# reported at each band edge (0.205, 0.505, 0.805).
RISK_LEVELS = tuple((lv.label, lv.lo, lv.hi, lv.color) for lv in _RL.LEVELS)
LEVEL_EDGES = _RL.edges()


# ── the exclusion catalog (§4.3 as amended by Part B: X-E2E-SCOPE dropped;
#    X-S4-RESAMPLE, X-LEDGER-SUSPECT and X-S5-PERFECT-SIBLING added). Each
#    stage's list starts with STAGES_PROTOCOL.md §7's first-match order (frozen
#    at P0); tests/test_stages_spec.py pins it. ─────────────────────────────
# kind: exclude = left out of the score; tag = a sensitivity subset, rows stay;
# stratum = scored apart; fit = affects fitting only. counted = the figure and
# the report show a count beside it (False: structural, or 0 by construction).
# chip = the figure's words; plain = one line for the report and the tooltip.

def _x(group, kind, chip, plain, counted=True):
    return dict(group=group, kind=kind, chip=chip, plain=plain, counted=counted)


EXCLUSIONS = {
    # every stage
    "X-ALL-INSAMPLE": _x("ALL", "exclude", "days the fit saw", "Days the scored weights were fitted on are never scored, so the count is 0 by construction.", False),
    "X-POWER": _x("ALL", "tag", "too few to decide", "Fewer than 10 positive days or 8 storms: the score is shown with its range and never decides anything.", False),
    "X-SEL": _x("ALL", "tag", "days used to pick the set", "Days in a window that was used to pick the served set; scores there favour it.", False),
    "X-LEDGER-SUSPECT": _x("ALL", "exclude", "ledger likely incomplete", "A zone was posted for an overflow, or a sample there read 10× the standard, in wet weather (rain on a day from 3 days before to the day after), yet no basin draining to it filed an overflow in those days: listed, not scored."),
    "X-PL-END": _x("ALL", "exclude", "after the last posting record", "Days after 28 Feb 2026, the last BeachWatch filing: the posting ruler cannot grade them."),
    # S1
    "X-S1-MISSING": _x("S1", "exclude", "missing", "The gauge reported nothing that day; the two-gauge mean uses the other gauge."),
    "X-S1-OUTAGE": _x("S1", "exclude", "dead-gauge days", "A dead gauge reads 0.00, so days inside a known outage are masked."),
    "X-S1-NWPGAP": _x("S1", "exclude", "model gaps", "The weather model's archive is missing hours that day; never filled with zeros."),
    "X-S1-NOLEAD": _x("S1", "exclude", "no forecast at that lead", "No archived forecast for that model and lead (leads 1–5 start in early 2024)."),
    "X-S1-PEAK": _x("S1", "exclude", "peak hours", "Peak-hour rain is not scored until the airport's hourly record is kept.", False),
    # S2
    "X-S2-ARCHIVE": _x("S2", "exclude", "feed archive", "Days labelled from the 2016-17 beach-map archive: late, and the same data S5 corrects with."),
    "X-S2-UNCOV": _x("S2", "exclude", "no filing", "The month is outside the plant's unbroken run of filed reports, and unknown is not “no overflow”."),
    "X-S2-CARRY": _x("S2", "exclude", "carry-over", "An overflow from the day before was still running and none started that day."),
    "X-S2-VOLQ": _x("S2", "exclude", "size not measured", "The filed volume is blank or “less than”: left out of the size score."),
    "X-S2-OUTAGEIN": _x("S2", "tag", "dead gauge in the inputs", "A masked gauge day sits in the 30 days of rain the day's inputs read."),
    # S3
    "X-S3-UNCOV": _x("S3", "exclude", "no filing", "A basin feeding the zone filed no report for that month."),
    "X-S3-CARRY": _x("S3", "exclude", "carry-over", "An overflow into the zone carried over from the day before and none started that day."),
    "X-S3-QUIET": _x("S3", "exclude", "no basin overflow (oracle)", "Fed the true overflows, a day with none gives exactly 0; those days would flatter the score."),
    "X-S3-ID": _x("S3", "exclude", "identity links: checked only", "A basin that reaches one zone passes its overflow straight through: checked for mismatches (there must be none), not scored.", False),
    "X-S3-GEO": _x("S3", "tag", "mapped from the permit only", "The zone was reached only through outfalls placed by the permit and the map, never seen in a feed."),
    "X-S3-NOTCLEAN": _x("S3", "exclude", "storm spilled next door", "Posting check only: a neighbouring zone overflowed within 2 days, so a posting can't be pinned to one zone."),
    # S4
    "X-S4-UNSAMPLED": _x("S4", "exclude", "days nobody sampled", "No sample at any station in the zone that day."),
    "X-S4-HISTUNK": _x("S4", "exclude", "overflow history unknown", "A feeding basin's month was not filed on that day or one of the 7 before."),
    "X-S4-RESAMPLE": _x("S4", "exclude", "resamples", "The zone was over standard 1–2 days before, so SFPUC went back: scored apart from the first look."),
    "X-S4-DAYOF": _x("S4", "stratum", "overflow that day", "The zone overflowed that same day: that is S3's event, so it is reported apart."),
    "X-S4-FEW": _x("S4", "stratum", "few stations sampled", "Fewer than half the zone's stations were sampled: reported apart."),
    "X-S4-FOLLOWUP": _x("S4", "fit", "follow-up-only stations", "Three Ocean Beach stations sampled only after overflows: left out of fitting the no-overflow background."),
    "X-S4-ANALYTE": _x("S4", "tag", "E. coli era", "Samples from mid-2020 through 2021, when the indicator changed."),
    # S5
    "X-S5-HEALTH": _x("S5", "exclude", "watcher down", "The beach-map watcher was unhealthy, so no correction was made."),
    "X-S5-CIRC": _x("S5", "exclude", "archive Westside days", "Westside in the 2016-17 archive: the truth there is the feed itself."),
    "X-S5-PERFECT-SIBLING": _x("S5", "exclude", "neighbours on the perfect feed", "A neighbouring zone's day on the perfect feed: that feed is the ledger, so it would grade itself."),
    "X-S5-SELF": _x("S5", "exclude", "the replaced day", "The day whose number the observation replaced: right by construction."),
    "X-S5-QUIET": _x("S5", "exclude", "nothing seen nearby", "No observation in the basin or zone in the week before, so the correction changes nothing."),
    "X-S5-INSAMPLE": _x("S5", "tag", "2016-17 archive", "The archive years, which S2 was fitted on."),
    # OUT
    "X-E2E-UNCOV": _x("OUT", "exclude", "overflow history unknown", "A feeding basin's month was not filed: that day, or the week before a quiet day."),
    "X-E2E-UNK": _x("OUT", "exclude", "unsampled days after one", "Nobody sampled the water in the 7 days after an overflow."),
}

# What the public % does not claim (§4.2; Part B 3: dry-weather and runoff
# exceedances are negatives of the claim, counted here, never excluded).
CLAIMS = {
    "C-DRY": dict(pill="dry-weather exceedances", counted=True, plain="A sample over standard with no overflow in the week before and under 0.1″ of rain in 3 days: not overflow-related. Counted as episodes and days."),
    "C-RUNOFF": dict(pill="rain runoff, no overflow", counted=True, plain="The same after 0.1″ or more of rain: stormwater, not the sewer. S4 models it; the public % does not count it. Counted as episodes and days."),
    "C-OTHER": dict(pill="other causes posted", counted=True, plain="Postings the state files under another cause: sewage spills, wildlife, unexplained."),
    "C-UNMON": dict(pill="shore far from a station", counted=True, plain="Four outfalls more than 1.5 km from any station: their overflows count for the mapped zone; nothing is said about the shore at the outfall."),
    "C-POSTING": dict(pill="SFPUC's posting decisions", counted=False, plain="Whether SFPUC puts up a sign is a policy decision; postings are a second ruler, never the truth."),
    "C-TIME": dict(pill="the hour, the size, past day 5", counted=False, plain="The hour of an overflow, its size, any single station, and days past the fifth."),
}


# ── the stages ───────────────────────────────────────────────────────────────
# Report sections and Model check tabs follow this order; `anchor` is both.

STAGES = (
    dict(id="s1", code="S1", name="Rain", question="how much rain, and when?", unit="per rain gauge · day · lead 0–5",
         input="hourly rain at one city point from the weather model the forecast reads, today to five days ahead",
         output="daily rain and peak hours for Downtown, Oceanside and their mean, by lead",
         truth="the two NOAA gauges, Downtown and Oceanside, with dead-gauge days masked",
         oracle="nothing comes before rain: one gauge as a perfect forecast of the other (the floor)",
         chained="the forecast one day ahead",
         metric="wet-day error in inches, on days either the gauges or the forecast were wet, one day ahead",
         unit_fmt="inches", pills=("floor", "lead 1"),
         benchmarks=("yesterday's gauge", "monthly climatology", "ECMWF, GFS and the mean of three", "the floor"),
         exclusions=("X-S1-MISSING", "X-S1-OUTAGE", "X-S1-NWPGAP", "X-S1-NOLEAD", "X-S1-PEAK", "X-POWER"),
         node="m.s1", truth_node="t.s1", chip="x.s1", anchor="s1"),
    dict(id="s2", code="S2", name="Basin overflow", question="does the sewer overflow?", unit="per basin · day",
         input="the 19 daily rain numbers and peak hours from each basin's gauges",
         output="the chance an overflow starts that day, and its size, per basin",
         truth="the overflow ledger (CIWQS): an overflow filed as starting that day, in months that were filed",
         oracle="rain from the gauges, dead-gauge days masked",
         chained="S1's forecast one day ahead",
         metric="skill vs climatology (BSS)", unit_fmt="bss", pills=("oracle", "chained"),
         benchmarks=("climatology by basin and month", "a one-number rule on the day's rain", "yesterday's ledger"),
         exclusions=("X-S2-ARCHIVE", "X-S2-UNCOV", "X-LEDGER-SUSPECT", "X-S2-CARRY", "X-ALL-INSAMPLE",
                     "X-S2-VOLQ", "X-S2-OUTAGEIN", "X-POWER", "X-SEL"),
         node="m.s2", truth_node="t.s2", chip="x.s2", anchor="s2"),
    dict(id="s3", code="S3", name="Zone overflow", question="which zones does it reach?", unit="per zone · day",
         input="each basin's chance and size from S2",
         output="the chance each zone is reached that day",
         truth="the ledger by zone: an outfall that posts the zone's stations overflowed that day",
         oracle="the true basin overflows, with the size predicted from rain (never the true size, which holds the answer)",
         chained="S2's output",
         metric="skill (BSS), with the oracle on the Westside split", unit_fmt="bss", pills=("oracle", "chained"),
         benchmarks=("a constant share", "every overflow reaches every zone of its basin", "East: the larger of its basins"),
         exclusions=("X-S3-UNCOV", "X-LEDGER-SUSPECT", "X-S3-CARRY", "X-S3-QUIET", "X-S3-ID", "X-ALL-INSAMPLE",
                     "X-S3-GEO", "X-S3-NOTCLEAN", "X-PL-END", "X-POWER", "X-SEL"),
         node="m.s3", truth_node="t.s3", chip="x.s3", anchor="s3"),
    dict(id="s4", code="S4", name="Water quality", question="is the water over standard?", unit="per zone · sampled day",
         input="the zone's overflows in the last 7 days, their size, and rain",
         output="the chance a sample in the zone is over the state standard",
         truth="lab samples, first look: any station in the zone over the state standard",
         oracle="the true zone overflows and the gauges' rain",
         chained="S3's output",
         metric="skill vs climatology (BSS)", unit_fmt="bss", pills=("oracle", "chained"),
         benchmarks=("climatology by zone and month", "the zone's last sample within 7 days", "rain alone, no overflow history", "the served table"),
         exclusions=("X-S4-UNSAMPLED", "X-S4-HISTUNK", "X-S4-RESAMPLE", "X-ALL-INSAMPLE",
                     "X-S4-DAYOF", "X-S4-FEW", "X-S4-FOLLOWUP", "X-S4-ANALYTE", "X-POWER", "X-SEL"),
         node="m.s4", truth_node="t.s4", chip="x.s4", anchor="s4"),
    dict(id="s5", code="S5", name="Live corrections", question="does what was seen improve what comes after?", unit="per zone · the days after an observation",
         input="CSO flags and outfall names on SFPUC's beach map; new lab results (map 1–2 days, DataSF about 5)",
         output="corrected chances for that day and the week after",
         truth="the later stages' truths, on the days after an observation",
         oracle="a perfect feed: every filed overflow, on time (sibling zones are scored on real or degraded feeds only)",
         chained="the real feeds: the 2016-17 archive, a degraded feed, the watcher",
         metric="change in Brier on the days after", unit_fmt="bss", pills=("oracle", "chained"),
         benchmarks=("no correction", "the served live corrections"),
         exclusions=("X-S5-HEALTH", "X-S5-CIRC", "X-S5-PERFECT-SIBLING", "X-S5-SELF", "X-S5-QUIET", "X-S5-INSAMPLE", "X-POWER"),
         node="m.s5", truth_node=None, chip=None, anchor="s5"),
    dict(id="out", code="OUT", name="Overflow risk", question="what people see", unit="per zone · 6 days",
         input="S3's same-day chance and S4's overflow tail, with no rain background",
         output="the % per zone, today and five days ahead, every 30 minutes",
         truth="bad beach days: an overflow day, or a sample over standard in the 7 days after one; BeachWatch postings are a second ruler",
         oracle="each stage's truth in turn (the error budget)",
         chained="the full chain from the weather model, by lead",
         metric="skill by lead, chained", unit_fmt="bss", pills=(),
         benchmarks=("climatology", "yesterday's state", "the served set", "the retired gradient-boosted set"),
         exclusions=("X-E2E-UNCOV", "X-E2E-UNK", "X-ALL-INSAMPLE", "X-PL-END", "X-POWER", "X-SEL"),
         node="m.out", truth_node="t.out", chip="x.out", anchor="out"),
)
STAGE = {s["id"]: s for s in STAGES}
STAGE_OF_CODE = {s["code"]: s for s in STAGES}
EXCLUSIONS_BY_STAGE = {s["code"]: tuple(s["exclusions"]) for s in STAGES}

# The phone list reads in the figure's order: the chain, then S5, the levels, the claims.
PHONE_ORDER = ("s1", "s2", "s3", "s4", "out", "s5", "levels", "claims")


# ── figure 1: canvas and grid (§1.2) ─────────────────────────────────────────

VIEW = (1290, 820)
COLX = (44, 296, 548, 800, 1052)
CW = 222
ROWS = {0: (50, 80),      # TRUTH
        1: (220, 152),    # MODEL
        2: (416, 56),     # LIVE a
        3: (480, 56),     # LIVE b
        4: (544, 56),     # LIVE c
        5: (632, 54),     # NOT SCORED chips
        6: (700, 62)}     # DOES NOT CLAIM strip
BANDS = ((42, 96, "TRUTH"), (212, 168, "MODEL"), (408, 200, "LIVE"))
HEAD = tuple((f"{s['code']} · {s['name'].upper()}", s["unit"]) for s in STAGES if s["id"] != "s5")
TITLE = "Five stages from rain to a beach percentage, each scored twice"

T_BOT = ROWS[0][0] + ROWS[0][1]      # 130
M_TOP = ROWS[1][0]                   # 220
M_BOT = ROWS[1][0] + ROWS[1][1]      # 372
CHAIN_Y = M_TOP + 56                 # 276
L_TOP = ROWS[2][0]                   # 416
LIVE_LABEL_Y = 398                   # labels between the model row and the live row


def box(n: dict) -> tuple:
    """(x, y, w, h) of a node from its grid cell, column span and row span."""
    x = COLX[n["col"]]
    w = COLX[n["col"] + n.get("span", 1) - 1] + CW - x
    y, h = ROWS[n["row"]]
    last = n["row"] + n.get("rows", 1) - 1
    return x, y, w, ROWS[last][0] + ROWS[last][1] - y


# ── nodes (§1.3; kinds: truth, stage, output, live, input, decision, exclusion, claim) ──
# lines may hold {slot}s, filled from the render's counts ("—" without); {weather_model}
# defaults to the model the live forecast reads, so the S1 box follows a switch.
# items (exclusion chips) are lines of (words, rule id); the count follows the words.

_LEVEL_LINES = (" · ".join(f"{n} {a}–{b}" for n, a, b, _ in RISK_LEVELS[:2]),
                " · ".join(f"{n} {a}–{b}" for n, a, b, _ in RISK_LEVELS[2:]))

NODES = (
    # TRUTH row
    dict(id="t.s1", kind="truth", stage="S1", col=0, row=0, logo="noaa.svg", title="Rain gauges",
         lines=("Downtown · Oceanside", "dead-gauge days masked"), tip="NOAA ACIS gauges 047772 (Downtown) and 047767 (Oceanside); gauge_outage_v1 mask"),
    dict(id="t.s2", kind="truth", stage="S2", col=1, row=0, logo="water-boards.png", title="Overflow ledger",
         lines=("CIWQS, by basin", "{n_city_days} overflow days"), tip="CIWQS self-monitoring reports; days in filed months only"),
    dict(id="t.s3", kind="truth", stage="S3", col=2, row=0, logo="water-boards.png", title="Ledger, by zone",
         lines=("outfall → its stations", "same day, covered days"), tip="ledger + outfall registry; every feeding basin filed"),
    dict(id="t.s4", kind="truth", stage="S4", col=3, row=0, logo="sf-city-seal.png", title="Lab samples",
         lines=("any station over", "the state standard"), tip="AB411 single-sample standard; first-look samples"),
    dict(id="t.out", kind="truth", stage="OUT", col=4, row=0, icon="circle-alert", title="Bad beach days",
         lines=("overflow day, or over", "standard within 7 days"), tip="second ruler: BeachWatch postings to 2026-02-28"),
    # MODEL row
    dict(id="m.s1", kind="stage", stage="S1", col=0, row=1, icon="cloud-rain", title="Rain",
         lines=("{weather_model} via Open-Meteo, one point", "today → 5 days ahead"), metric="wet-day error, inches"),
    dict(id="m.s2", kind="stage", stage="S2", col=1, row=1, icon="gauge", title="Basin overflow",
         lines=by_geo(("chance + size, per basin", "SFPUC's four basins"), ("chance + size, per basin", "our four basins")),
         metric="skill vs climatology (BSS)"),
    dict(id="m.s3", kind="stage", stage="S3", col=2, row=1, icon="git-branch", title="Zone overflow", inset=True,
         lines=(), metric="skill (BSS) · oracle on the Westside split"),
    dict(id="m.s4", kind="stage", stage="S4", col=3, row=1, icon="flask", title="Water quality",
         lines=by_geo(("overflow history + rain", "per zone, days 0–7 after"), ("overflow history only", "per zone, days 0–7 after")),
         metric="skill vs climatology (BSS)"),
    dict(id="m.out", kind="output", stage="OUT", col=4, row=1, icon="map-pin", title="Overflow risk",
         lines=("% per zone · every 30 min",), metric="skill by lead, chained", lead=True),
    # LIVE rows
    dict(id="l.rain", kind="live", stage="S1", col=0, row=2, logo="noaa.svg", title="Rain so far", lines=("gauges, then SFO today",)),
    dict(id="l.map", kind="live", stage="S5", col=1, row=2, logo="sfpuc.png", title="SFPUC beach map",
         lines=by_geo(("CSO flags · outfall names",), ("CSO flags at stations",))),
    dict(id="l.lab", kind="live", stage="S5", col=1, row=3, logo="sf-city-seal.png", title="Lab results, new", lines=("map 1–2 days · DataSF ~5",)),
    dict(id="l.perfect", kind="input", stage="S5", col=1, row=4, icon="circle-check", title="Perfect feed", lines=("filed overflows, on time",)),
    dict(id="m.s5", kind="stage", stage="S5", col=2, row=2, span=2, rows=2, icon="satellite-dish", title="Live corrections", wide=True,
         lines=by_geo(("a CSO flag or named outfall → its zone, after the split", "a lab result → that day’s water quality"),
                      ("a CSO flag → its basin, before the split", "a lab result → that zone’s next few days")),
         metric="change in Brier on the days after"),
    dict(id="o.levels", kind="decision", stage="LEVELS", col=4, row=2, rows=2, icon="triangle-alert", title="Risk levels",
         lines=_LEVEL_LINES + ("the same bands on every page",),
         tip="fixed bands on the whole % shown (Chase, 2026-10-01); threshold scores at each edge: " + ", ".join(f"{100 * e:g}%" for e in LEVEL_EDGES)),
    # NOT SCORED chips
    dict(id="x.s1", kind="exclusion", stage="S1", col=0, row=5, title="not scored",
         items=((("dead-gauge days", "X-S1-OUTAGE"), ("missing", "X-S1-MISSING")), (("model gaps", "X-S1-NWPGAP"), ("peak hours", "X-S1-PEAK")))),
    dict(id="x.s2", kind="exclusion", stage="S2", col=1, row=5, title="not scored",
         items=((("no filing", "X-S2-UNCOV"), ("feed archive", "X-S2-ARCHIVE")), (("carry-over", "X-S2-CARRY"), ("days the fit saw", "X-ALL-INSAMPLE")))),
    dict(id="x.s3", kind="exclusion", stage="S3", col=2, row=5, title="not scored",
         items=((("identity links: checked only", "X-S3-ID"),), (("no basin overflow (oracle)", "X-S3-QUIET"),))),
    dict(id="x.s4", kind="exclusion", stage="S4", col=3, row=5, title="not scored",
         items=((("days nobody sampled", "X-S4-UNSAMPLED"), ("resamples", "X-S4-RESAMPLE")), (("overflow history unknown", "X-S4-HISTUNK"),))),
    dict(id="x.out", kind="exclusion", stage="OUT", col=4, row=5, title="not scored",
         items=((("unsampled days after one", "X-E2E-UNK"),), (("overflow history unknown", "X-E2E-UNCOV"),))),
    dict(id="x.claim", kind="claim", stage="CLAIM", col=0, row=6, span=5, title="the forecast does not claim", claims=tuple(CLAIMS)),
)
for _n in NODES:                                    # stage boxes carry their stage's question and pills
    _s = STAGE_OF_CODE.get(_n["stage"])
    if _n["kind"] in ("stage", "output") and _s:
        _n.setdefault("q", _s["question"])
        _n.setdefault("pills", _s["pills"])
    _n.setdefault("anchor", {"LEVELS": "levels", "CLAIM": "claims"}.get(_n["stage"]) or STAGE_OF_CODE[_n["stage"]]["anchor"])
NODE = {n["id"]: n for n in NODES}


# ── edges (§1.4; styles: data, fit, score, oracle, live, decision) ───────────
# pts are absolute; lab = (x, y, anchor) for the label's baseline.

STYLES = ("data", "fit", "score", "oracle", "live", "decision")


def _edges() -> tuple:
    E = []

    def e(id_, frm, to, style, pts, label=None, lab=None, tip=""):
        E.append(dict(id=id_, frm=frm, to=to, style=style, pts=pts, label=label, lab=lab, tip=tip))

    chain_tips = ("inches by day and lead", "chance + size per basin", "chance + size per zone", "the overflow tail, no rain background")
    for i, (a, b) in enumerate((("m.s1", "m.s2"), ("m.s2", "m.s3"), ("m.s3", "m.s4"), ("m.s4", "m.out"))):
        e(f"c{i + 1}{i + 2}", a, b, "data", ((COLX[i] + CW, CHAIN_Y), (COLX[i + 1], CHAIN_Y)), tip=chain_tips[i])
    for k, c in ((2, 1), (3, 2), (4, 3)):        # fit, down from the truth (S1's weather model is external: no fit arrow)
        x = COLX[c] + 64
        e(f"f{k}", f"t.s{k}", f"m.s{k}", "fit", ((x, T_BOT), (x, M_TOP)), *(("fit", (x - 6, 168, "end")) if k == 2 else (None, None)),
          "fitted on its own truth")
    for k, (c, a, b) in enumerate(((0, "m.s1", "t.s1"), (1, "m.s2", "t.s2"), (2, "m.s3", "t.s3"), (3, "m.s4", "t.s4"), (4, "m.out", "t.out"))):
        x = COLX[c] + 104                         # scored, up to the truth
        e(f"s{k + 1}", a, b, "score", ((x, M_TOP), (x, T_BOT)), *(("scored", (x + 6, 168, "start")) if k < 2 else (None, None)),
          "scored on days the fit never saw")
    for k, (c, lab) in enumerate(((0, "true rain"), (1, "true basin overflows"), (2, "true zone overflows"))):
        x0, x1 = COLX[c] + CW - 14, COLX[c + 1] + 30   # oracle: the truth to the left feeds the next stage
        e(f"o{k + 2}", f"t.s{k + 1}", f"m.s{k + 2}", "oracle", ((x0, T_BOT), (x1, M_TOP)), lab, (x0 + 35, 186, "end"))
    e("l1", "l.rain", "m.s1", "live", ((155, L_TOP), (155, M_BOT)), "observed rain", (161, LIVE_LABEL_Y, "start"))
    e("l2", "l.map", "m.s5", "live", ((COLX[1] + CW, 444), (COLX[2], 444)))
    e("l3", "l.lab", "m.s5", "live", ((COLX[1] + CW, 508), (COLX[2], 508)))
    e("o5", "l.perfect", "m.s5", "oracle", ((COLX[1] + CW, 572), (584, 572), (584, ROWS[2][0] + 120)), tip="the oracle feed")
    # a flag enters after the split (SFPUC4) or at the basin, before it (the served set's live_v2)
    e("l4", "m.s5", by_geo("m.s3", "m.s2"), "live",
      by_geo(((659, L_TOP), (659, M_BOT)), ((572, L_TOP), (572, 394), (460, 394), (460, M_BOT))),
      by_geo("a flag or a named outfall", "a flag → its basin"),
      by_geo((665, LIVE_LABEL_Y, "start"), (578, 405, "start")))
    e("l5", "m.s5", "m.s4", "live", ((911, L_TOP), (911, M_BOT)), "a lab result", (917, LIVE_LABEL_Y, "start"))
    e("d1", "m.out", "o.levels", "decision", ((1163, M_BOT), (1163, L_TOP)), "the % shown", (1169, LIVE_LABEL_Y, "start"))
    return tuple(E)


EDGES = _edges()

# The legend: arrows (style, words) then the chips row.
LEGEND_ARROWS = (("data", "flows every 30 minutes"), ("oracle", "oracle: fed the true input"), ("fit", "fits the stage"),
                 ("score", "scored on days the fit never saw"), ("live", "an observation replaces a prediction"),
                 ("decision", "becomes a risk level"))
LEGEND_CHAINED = "every stage is scored twice: on its true input (oracle) and on the real output of the stage before it (chained)"
LEGEND_NOT_SCORED = "each rule has a count; ids in tooltips"
LEGEND_Y = (772, 806)            # chip row, arrow row


def legend_layout(per_char: float = 5.9, gap: float = 18) -> tuple:
    """x of each legend arrow, packed left to right from the first column."""
    xs, x = [], COLX[0]
    for _, words in LEGEND_ARROWS:
        xs.append(x)
        x += 42 + len(words) * per_char + gap
    return tuple(xs)


def nodes(geo: str) -> tuple:
    """NODES resolved for one geography, each with its box."""
    if geo not in GEOS:
        raise KeyError(geo)
    return tuple({**{k: pick(v, geo) for k, v in n.items()}, "box": box(n)} for n in NODES)


def edges(geo: str) -> tuple:
    if geo not in GEOS:
        raise KeyError(geo)
    return tuple({k: pick(v, geo) for k, v in e.items()} for e in EDGES)


def spec_dict() -> dict:
    """Everything the figure, the phone list and the report read: SPEC_VERSION hashes all of it."""
    return dict(geos=GEOS, title=TITLE, stages=STAGES, exclusions=EXCLUSIONS, claims=CLAIMS, risk_levels=RISK_LEVELS,
                level_edges=LEVEL_EDGES, view=VIEW, colx=COLX, cw=CW, rows=ROWS, bands=BANDS, head=HEAD, nodes=NODES,
                edges=EDGES, legend=LEGEND_ARROWS, legend_chained=LEGEND_CHAINED, legend_not_scored=LEGEND_NOT_SCORED,
                legend_y=LEGEND_Y, phone=PHONE_ORDER)


SPEC_VERSION = hashlib.sha1(json.dumps(spec_dict(), sort_keys=True, ensure_ascii=False, default=list).encode()).hexdigest()[:10]
