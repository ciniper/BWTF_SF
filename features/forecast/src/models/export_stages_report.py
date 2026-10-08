#!/usr/bin/env python3
"""The stages report, public at /reports/2026-10_forecast_stages.html: the forecast scored
stage by stage, the live forecast (the set served.json names, wearing a LIVE badge) beside every
challenger the stages build has scored, each named by its lineup (STAGES_DESIGN.md A8; Part C §8 P9).
Every part goes by its one fixed name (shared/lineup.py WORDS); nothing is "today's" (Chase, 2026-10-04).

    venv/bin/python features/forecast/src/models/export_stages_report.py   # writes the page, prints its path

It reads committed artifacts only and never recomputes a score:

    data/models/served.json                               the served set's name
    data/models/stages/<set>/manifest.json, scores.json   every set the stages build scored (the served set's
                                                          t0_as_served: the live season as the page showed it)
    data/models/stages/_s1/s1_scores.json                 S1 by lead (no set owns it)
    data/models/stages_candidates/<name>/manifest.json    a stage candidate's S2 design
    data/models/stages_candidates/_bakeoff/results.json   the S2 term-set bake-off (A5)
    STAGES_PROTOCOL.md, protocols/stages_v*.md            the rules versions and their dates

Every number on the page is one field of one of those files, printed inside
<span data-v="KEY:PATH|FMT">: KEY names the file (the page's #sources block maps it to its
repo path), PATH is the field ("/"-joined keys, list indices as numbers) and FMT how it
prints (``FMT``), so tests/test_stages_report.py checks each one against its file. Counting
fields (how many seeds say "worse", how many seasons picked a term set) is the only arithmetic. The words come from
stages_spec (stages, exclusions, claims), the figure from stages_flowchart, the lineup's
plain words from shared/lineup.py, the one hand-typed map (component ids to words), and the LIVE badge
the live forecast wears from export_reports_index (one look on every report). The output is
a pure function of those inputs, with no clock: rebuilding unchanged artifacts gives the
same bytes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (str(REPO), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stages_flowchart as F  # noqa: E402
import stages_spec as SP  # noqa: E402
from export_reports_index import LIVE_BADGE, LIVE_CSS  # noqa: E402  (the LIVE badge, one look on every report)
from shared import geography as G  # noqa: E402
from shared import lineup as LU  # noqa: E402  (the lineup's plain words, A8: one map for this page and the Model check)
from shared import lineup_titles as LT  # noqa: E402  (the column titles, display only)
from shared.risk_levels import LEVELS  # noqa: E402  (the public levels and their colours, A3)
from shared.zones import ZONES  # noqa: E402

MODELS = FORECAST / "data" / "models"
SERVED = MODELS / "served.json"
STAGES_DIR = MODELS / "stages"
S1_SCORES = STAGES_DIR / "_s1" / "s1_scores.json"
CANDIDATES_DIR = MODELS / "stages_candidates"
BAKEOFF = CANDIDATES_DIR / "_bakeoff" / "results.json"
PROTOCOL = FORECAST / "STAGES_PROTOCOL.md"
PROTOCOLS_DIR = FORECAST / "protocols"
ICONS = REPO / "app" / "templates" / "_icons.html"
OUT = REPO / "reports" / "2026-10_forecast_stages.html"
HOW_IT_WORKS = "/reports/2026-09_forecast_how_it_works.html"
INDEX = "/reports/"                                         # every report, current and archived (export_reports_index.py)
ROOTS = ("served", "candidates", "stages_candidates")       # lineup order after the live forecast: BWTF basins first

LABELS, LINEUP_COLS = LU.WORDS, LT.COLUMNS

# A rules version's one line (from STAGES_PROTOCOL.md's "Changes from" blocks; the dates are read from the
# files). Rule ids go in the tooltip. A version the files name with no line here raises.
VERSION_NOTES = {
    "stages_v1": ("The first rules.", "Replaced the same day, before any score was computed.", ()),
    "stages_v2": ("A month counts as filed only inside the plant's unbroken run of reports; “ledger likely incomplete” "
                  "tests the zone, and only in wet weather.", "", ("X-LEDGER-SUSPECT",)),
    "stages_v3": ("“Ledger likely incomplete” days also leave the water-quality score and the public number; "
                  "“dead gauge in the inputs” is worded exactly.",
                  "Chase's decision, made after the live forecast's stage scores had been seen; a re-check is on the list.",
                  ("X-LEDGER-SUSPECT", "X-S2-OUTAGEIN")),
}

FEATURE_WORDS = {"precip_avg": "today's rain", "rain_2d_cum": "last 2 days", "rain_3d_cum": "last 3 days",
                 "rain_5d_cum": "last 5 days", "rain_7d_cum": "last 7 days", "rain_14d_cum": "last 14 days",
                 "rain_30d_cum": "last 30 days", "rain_lag1d": "yesterday's rain", "rain_lag2d": "rain 2 days ago",
                 "rain_lag3d": "rain 3 days ago", "rain_lag5d": "rain 5 days ago", "rain_lag7d": "rain 7 days ago",
                 "antecedent_moisture": "how wet the ground is", "wet_prior_3d": "wet in the 3 days before",
                 "peak_3d": "wettest of the last 3 days", "dry_spell_days": "days since rain", "rain_max1h": "wettest hour",
                 "rain_max3h": "wettest 3 hours", "rain_max6h": "wettest 6 hours"}
SERIES_WORDS = {"SF Downtown": "Downtown gauge", "SF Oceanside": "Oceanside gauge", "avg": "two-gauge mean"}
WINDOW_WORDS = {"T2": "nine seasons", "T1": "post-training", "T1-holdout": "holdout"}
VERDICT_CLASS = {"better": "good", "worse": "bad", "no clear difference": "na"}
STATUS_CLASS = {"pass": "good", "fail": "bad", "not yet computable": "na", "not applicable": "na",
                "met": "good", "not met": "bad"}
KIND_WORDS = {"exclude": "left out", "tag": "marked, kept", "stratum": "scored apart", "fit": "fit only"}

# What each pre-registered test asks, in plain words (protocol §8's rows by id; the artifact's own wording is the
# tooltip). A row id with no words prints the artifact's comparison. Parts go by their fixed names (shared/lineup.py):
# S3a compares the two geographies, S3b the size split, S5 the primary pair (stages_s5.PRIMARY: link_zone_swap
# against basin_swap, the live forecast's rule).
PRIMARY_WORDS = {
    "S1": "S1 · another weather model's rain closer to the gauges, one day ahead",
    "S2": "S2 · overflow model: fewer errors than the live forecast's, basins pooled",
    "S2-south-floor": "S2 · the South basin: better than its usual rate",
    "S2-volume": "S2 · overflow size: a stand-in where a basin has too few measured sizes",
    "S3a": f"S3 · {LU.words('geography', 'sfpuc4_v1')}: East Beaches no worse than on {LU.words('geography', 'geo_v1')}",
    "S3b": f"S3 · the Westside's {LU.words('s3', 'links_v1')} beats a fixed share",
    "S4": "S4 · lingering table: water quality no worse than the live forecast's table",
    "S5": f"S5 · {LU.words('s5', 'link_zone_swap')} beat {LU.words('s5', 'basin_swap')}",
    "OUT": "OUT · the public number: no worse than the live forecast's",
}
PART_WORDS = {
    "superiority": "better, nine seasons",
    "non-inferiority at +5%": "not worse beyond the margin, post-training",
    "perfect feed (no sibling rows)": "perfect feed",
    "degraded feed (5 seeds)": "realistic feeds: the mean of all five draws, the range across them",
    "non-inferiority at +5%, rain known": "not worse beyond the margin, measured rain",
    "pooled MCB, rain known": "calibration not worse, measured rain",
    "non-inferiority at +5%, lead 1": "not worse beyond the margin, one day ahead",
    "pooled MCB, lead 1": "calibration not worse, one day ahead",
    "a declared fallback exists and stands in under the floor": "the stand-in exists and is used where needed",
    "declared head − fallback, Δ log-MAE on event days": "size error against the stand-in (described, not tested)",
}
# A "not applicable" row's plain reason, used only when the artifact's reason says the same thing (the pattern; a list
# holds one (pattern, words) per reason a row can give).
NA_WORDS = {
    "S1": (r"served weather model", "same weather model as the live forecast"),
    "S2": (r"its S2 is the served set's", "same overflow model as the live forecast"),
    "S2-south-floor": (r"has no South basin", f"{LU.words('geography', 'geo_v1')} have no South basin"),
    "S2-volume": (r"shares the served bundle's volume heads", "uses the live forecast's size estimates"),
    "S3a": (r"is the served set's geography", "same basins as the live forecast: this test compares basins"),
    "S3b": (r"no Westside share model with a size term", "no size-based split to test"),
    "S4": [(r"which no §8 row scores", "no test is set for this lingering table"),
           (r"its S4 is the served set's", "same lingering table as the live forecast")],
    "S5": (r"its S5 is the served set's", "same correction rule as the live forecast"),
}
CRITERIA_WORDS = {1: "Every changed stage passes its own pre-registered test",
                  2: "The public number is no worse than the live forecast's beyond the margin, post-training and the "
                     "live season, measured rain and one day ahead",
                  3: "Its percentages are no worse calibrated than the live forecast's",
                  4: "Chase has seen the zone-by-zone shadow-run report",
                  5: "Chase approves"}
# S5's replayed rules, each by its fixed name (shared/lineup.py), with a line on what it is: basin_swap is live_v2,
# the live forecast's rule; downgrade is link_zone_swap plus the no-flag downgrade (stages_s5.VARIANTS)
S5_RULES = (("basin_swap", "the live forecast's rule"), ("link_zone_swap", ""),
            ("downgrade", f"{LU.words('s5', 'link_zone_swap')}, plus a downgrade when no flag comes"),
            ("all_floors", f"{LU.words('s5', 'basin_swap')} with every sample floor"), ("sample_swap", ""))
LADDER_WORDS = {"truth_at_s4": ("True overflows", "the true overflow history: every stage before OUT perfect"),
                "truth_at_s3": ("True basin overflows", "S3 splits the true basin overflows into zones"),
                "rain": ("Measured rain", "S2 predicts the overflows from the gauges' rain"),
                "L1": ("One day ahead", "S1's forecast stands in for the gauges")}
STEP_WORDS = {"truth_at_s3 − truth_at_s4": "the zone split (S3)", "rain − truth_at_s3": "predicting overflows from rain (S2)",
              "L1 − rain": "the rain forecast (S1)"}
BUDGET_WINDOWS = {"T2": "the nine seasons' forecast days", "T1": "post-training days"}


# ── numbers: every one is a field, printed by one of these formats ──────────

MINUS = "−"


def _m(s: str) -> str:
    return s.replace("-", MINUS)


def _date(v) -> dt.date:
    return dt.date.fromisoformat(str(v)[:10])


def _whole(v) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not v.is_integer()):
        raise TypeError(f"a count must be a whole number, not {v!r}")
    return int(v)


FMT = {
    "2": lambda v: _m(f"{v:.2f}"), "3": lambda v: _m(f"{v:.3f}"), "4": lambda v: _m(f"{v:.4f}"),
    "2s": lambda v: _m(f"{v:+.2f}"), "3s": lambda v: _m(f"{v:+.3f}"), "4s": lambda v: _m(f"{v:+.4f}"),
    "5": lambda v: _m(f"{v:.5f}"), "5s": lambda v: _m(f"{v:+.5f}"),
    "i": lambda v: f"{_whole(v):,}", "in": lambda v: _m(f"{v:.2f}") + "″", "ins": lambda v: _m(f"{v:+.3f}") + "″",
    "pct": lambda v: f"{int(math.floor(100 * v + 0.5))}%", "km": lambda v: f"{v:.1f} km",
    "mon": lambda v: _date(v).strftime("%b %Y"), "day": lambda v: f"{_date(v).day} {_date(v).strftime('%b %Y')}",
    "len": lambda v: f"{len(v):,}", "t": lambda v: str(v),
}


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class Art:
    """One artifact: its data, and the key its numbers carry (data-v="KEY:PATH|FMT")."""

    def __init__(self, key: str, rel: str, data):
        self.key, self.rel, self.data = key, rel, data

    @classmethod
    def read(cls, key: str, path: Path) -> "Art":
        return cls(key, str(path.relative_to(REPO)), json.loads(path.read_text()))

    def get(self, *path):
        v = self.data
        for k in path:
            v = v[int(k)] if isinstance(v, list) else v[k]
        return v

    def opt(self, *path):
        try:
            return self.get(*path)
        except (KeyError, IndexError, TypeError):
            return None

    def n(self, *path, f: str = "2") -> str:
        """The field at ``path`` as the page prints it, tagged with where it comes from."""
        text = FMT[f](self.get(*path))
        return f'<span class="n" data-v="{esc(self.key)}:{esc("/".join(str(p) for p in path))}|{f}">{esc(text)}</span>'

    def rng(self, *path, f: str = "2") -> str:
        """'[lo, hi]' from a two-item field."""
        return f'<span class="ci">[{self.n(*path, 0, f=f)}, {self.n(*path, 1, f=f)}]</span>'


# ── loading ──────────────────────────────────────────────────────────────────

def served_name() -> str:
    return json.loads(SERVED.read_text())["name"]


def protocol_info() -> dict:
    """The rules versions, oldest first, with their freeze dates, and the current one's stamp and live-season start,
    read from STAGES_PROTOCOL.md and protocols/stages_v*.md."""
    def head(text: str, where: str) -> dict:
        v = re.search(r"scoring protocol `(stages_v\d+)`", text)
        fz = re.search(r"^Freeze date: (\d{4}-\d{2}-\d{2})", text, re.M)
        if not (v and fz):
            raise ValueError(f"{where}: no version or freeze date in its header")
        return {"version": v.group(1), "freeze": fz.group(1)}

    cur_text = PROTOCOL.read_text()
    cur = head(cur_text, PROTOCOL.name)
    t0 = re.search(r"T0 starts the next day, (\d{4}-\d{2}-\d{2})", cur_text)
    sha = re.search(r"^protocol sha256: ([0-9a-f]{64})$", cur_text, re.M)
    if not (t0 and sha):
        raise ValueError(f"{PROTOCOL.name}: no live-season start or digest in its header")
    versions = [head(p.read_text(), p.name) for p in sorted(PROTOCOLS_DIR.glob("stages_v*.md"))] + [cur]
    versions.sort(key=lambda v: int(v["version"].rsplit("v", 1)[1]))
    named = set(re.findall(r"\*\*Changes from `(stages_v\d+)`\*\*", cur_text)) | {v["version"] for v in versions}
    missing = sorted(named - set(VERSION_NOTES))
    if missing:
        raise KeyError(f"rules versions with no line in VERSION_NOTES: {missing}")
    return {"version": cur["version"], "stamp": f"{cur['version']}@{sha.group(1)}", "freeze": cur["freeze"],
            "t0": t0.group(1), "versions": versions}


def load() -> dict:
    """Every artifact the page reads, checked: each set's manifest and scores name it, every set was scored
    under the current rules (scores under different versions are never compared), the served set is there."""
    served = served_name()
    proto = protocol_info()
    sets = []
    for d in sorted(p for p in STAGES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if not (d / "scores.json").exists():
            continue
        sc, man = Art.read(d.name, d / "scores.json"), Art.read(f"{d.name}/manifest", d / "manifest.json")
        if sc.get("set") != d.name or man.get("set") != d.name:
            raise ValueError(f"{d.name}: its manifest or scores name another set")
        if sc.get("protocol") != proto["stamp"] or man.get("protocol") != proto["stamp"]:
            raise ValueError(f"{d.name}: scored under {sc.get('protocol')}, not the current rules {proto['stamp']}")
        if man.get("root") not in ROOTS:
            raise ValueError(f"{d.name}: unknown root {man.get('root')!r}")
        design = None
        if man.get("root") == "stages_candidates":
            design = Art.read(f"{d.name}/design", CANDIDATES_DIR / d.name / "manifest.json")
        sets.append({"name": d.name, "scores": sc, "manifest": man, "design": design})
    names = [s["name"] for s in sets]
    if served not in names:
        raise KeyError(f"the served set {served!r} has no stages scores (sets: {names})")
    sets.sort(key=lambda s: (s["name"] != served, ROOTS.index(s["manifest"].get("root")), s["name"]))
    srv = sets[0]["scores"]
    if [round(e, 3) for e in srv.get("edges")] != [round(e, 3) for e in SP.LEVEL_EDGES]:
        raise ValueError(f"the scores' level edges {srv.get('edges')} are not the risk levels' {SP.LEVEL_EDGES}")
    return {"served": served, "sets": sets, "protocol": proto,
            "served_json": Art.read("served", SERVED), "s1": Art.read("s1", S1_SCORES),
            "bakeoff": Art.read("bakeoff", BAKEOFF)}


def sources(D: dict) -> dict:
    """{key: Art} for every file the page reads numbers from."""
    out = {a.key: a for a in (D["served_json"], D["s1"], D["bakeoff"])}
    for s in D["sets"]:
        for a in (s["scores"], s["manifest"], s["design"]):
            if a is not None:
                out[a.key] = a
    return out


# ── the lineup (A8) ──────────────────────────────────────────────────────────

def lineup_of(man: Art) -> dict:
    """{column: component id} for one set: its geography, then S1–S5, from its manifest."""
    comps = man.get("components")
    return {"geography": man.get("geography"), **{c: comps[c] for c in ("s1", "s2", "s3", "s4", "s5")}}


label = LU.words


def lineup_words(man: Art) -> str:
    return " · ".join(label(c, v) for c, v in lineup_of(man).items())


def set_name(man: Art) -> str:
    """A set's name: its S2 · S3 · S4 in their fixed words ("38-weight · Outfall split · Linger table 2")."""
    parts = lineup_of(man)
    return " · ".join(label(c, parts[c]) for c in ("s2", "s3", "s4"))


def lineup_diff(D: dict) -> dict:
    """{set: {column: True when it differs from the live forecast}}."""
    base = lineup_of(D["sets"][0]["manifest"])
    return {s["name"]: {c: v != base[c] for c, v in lineup_of(s["manifest"]).items()} for s in D["sets"]}


# ── small builders ───────────────────────────────────────────────────────────

def chip(word: str, cls: str, tip: str = "") -> str:
    t = f' title="{esc(tip)}"' if tip else ""
    return f'<span class="v {cls}"{t}>{esc(word)}</span>'


def verdict(word: str, tip: str = "") -> str:
    return chip(word, VERDICT_CLASS.get(word, "na"), tip)


def span_words(A: Art, *path) -> str:
    """'Oct 2016 – Jun 2025' from a [first, last] field."""
    return f'{A.n(*path, 0, f="mon")} – {A.n(*path, 1, f="mon")}'


def cap(s: str) -> str:
    """First letter up, the rest as written ("archive Westside days" keeps its capital)."""
    return s[:1].upper() + s[1:]


def zone_label(z: str) -> str:
    return ZONES[z].label


def unit_label(geo: str, unit: str) -> str:
    if unit == "pooled":
        return "All together"
    if unit in ZONES:
        return zone_label(unit)
    for b in G.get(geo).basins:
        if b.key == unit:
            return b.name
    return SERIES_WORDS.get(unit, unit)


def tw(table: str, cls: str = "") -> str:
    """A table in its own sideways scroller, so a wide table never widens the page."""
    return f'<div class="tw{(" " + cls) if cls else ""}">{table}</div>'


def clean(s: str) -> str:
    """An artifact sentence for a tooltip-free spot: section signs and code marks out."""
    s = re.sub(r"\s*\((?:protocol )?§[^)]*\)", "", str(s))
    s = re.sub(r"\s*\([a-z0-9_]+\)", "", s)
    return re.sub(r"§\s*[\d.]+", "", s).replace("`", "").strip()


# ── score cells ──────────────────────────────────────────────────────────────

STAGE_WORDS = {   # (unit-days, positives) per stage block
    "s2": ("basin-days", "overflow days"), "s3": ("zone-days", "zone overflow days"),
    "s3_on_oracle_rows": ("zone-days", "zone overflow days"), "s4": ("sampled zone-days", "over the standard"),
    "out": ("zone-days", "bad beach days"),
}


def score_cell(A: Art, path: tuple, words: tuple, head_span=None, missing: str = "not scored") -> str:
    """BSS [range], days, positives, storms; faded, with the words, when too few storms decide nothing. A cell
    whose days differ from its column's (one day ahead starts with the forecast archive) prints its own."""
    c = A.opt(*path)
    if not isinstance(c, dict) or c.get("bss") is None:
        return f'<td class="sc none">{esc(missing)}</td>'
    unit, pos = words
    bits = [f'{A.n(*path, "n", f="i")} {unit}', f'{A.n(*path, "n_pos", f="i")} {pos}']
    if "n_storm_blocks" in c:
        bits.append(f'{A.n(*path, "n_storm_blocks", f="i")} storms')
    lp = bool(c.get("low_power"))
    tip = f"{c['span'][0]} → {c['span'][1]}" if c.get("span") else ""
    if c.get("n_seasons"):
        tip += f" · {c['n_seasons']} season{'s' if c['n_seasons'] != 1 else ''}"
    own = ""
    if c.get("span") and head_span is not None and [FMT["mon"](d) for d in c["span"]] != [FMT["mon"](d) for d in head_span]:
        own = f'<div class="sm">{span_words(A, *path, "span")}</div>'
    note = '<div class="lpw">too few storms to decide</div>' if lp else ""
    return (f'<td class="sc{" lp" if lp else ""}" title="{esc(tip)}"><b>{A.n(*path, "bss")}</b> {A.rng(*path, "ci", "bss")}'
            f'<div class="sm">{" · ".join(bits)}</div>{own}{note}</td>')


def _head_span(A: Art, rows: list, unit: str, w: str):
    """The column's days: the first row's that has a span."""
    for _, blk, e in rows:
        c = A.opt(blk, unit, e, w)
        if isinstance(c, dict) and c.get("span"):
            return (blk, unit, e, w), c["span"]
    return None, None


def stage_table(A: Art, rows: list, windows=("T2", "T1"), unit="pooled", missing: dict | None = None) -> str:
    """rows: [(label html, block, entry)]; one row per entry, one column per window, headed by the first row's days."""
    head, spans = ["<tr><th></th>"], {}
    for w in windows:
        p, spans[w] = _head_span(A, rows, unit, w)
        sub = span_words(A, *p, "span") if p else ""
        head.append(f'<th>{esc(cap(WINDOW_WORDS[w]))}<div class="sm">{sub}</div></th>')
    head.append("</tr>")
    body = "".join(f'<tr><th class="rl">{lab}</th>' + "".join(score_cell(A, (blk, unit, e, w), STAGE_WORDS[blk], spans[w],
                                                                         (missing or {}).get(e, "not scored")) for w in windows)
                   + "</tr>" for lab, blk, e in rows)
    return tw(f'<table class="st">{"".join(head)}{body}</table>')


def unit_table(A: Art, geo: str, cols: list, units: list, windows=("T2", "T1"), missing: dict | None = None) -> str:
    """By unit: cols = [(label, block, entry)] × windows, each headed by the pooled cell's days; a unit whose days
    differ prints its own."""
    head, spans = ["<tr><th></th>"], {}
    for lab, blk, e in cols:
        for w in windows:
            c = A.opt(blk, "pooled", e, w)
            spans[(blk, e, w)] = c.get("span") if isinstance(c, dict) else None
            sub = span_words(A, blk, "pooled", e, w, "span") if spans[(blk, e, w)] else ""
            head.append(f'<th>{esc(lab)} · {esc(WINDOW_WORDS[w])}<div class="sm">{sub}</div></th>')
    head.append("</tr>")
    body = []
    for u in units:
        cells = "".join(score_cell(A, (blk, u, e, w), STAGE_WORDS[blk], spans[(blk, e, w)], (missing or {}).get(e, "not scored"))
                        for _, blk, e in cols for w in windows)
        body.append(f'<tr><th class="rl">{esc(unit_label(geo, u))}</th>{cells}</tr>')
    return tw(f'<table class="st wide">{"".join(head)}{"".join(body)}</table>')


# ── sections ─────────────────────────────────────────────────────────────────

def live_started(D: dict) -> bool:
    """Whether the data reach the live season (the rules' T0, from the day after the freeze)."""
    return _date(D["sets"][0]["scores"].get("as_of")) >= _date(D["protocol"]["t0"])


def section_what(D: dict) -> str:
    A = D["sets"][0]["scores"]
    M = D["sets"][0]["manifest"]
    P = D["protocol"]
    power = SP.EXCLUSIONS["X-POWER"]["plain"]
    live = live_started(D)
    post_end = M.n("windows", "freeze" if live else "data_end", f="mon")       # post-training stops at the freeze
    live_words = (f'Every day after the rules froze on {esc(FMT["day"](P["freeze"]))}, scored to the data end on '
                  f'{A.n("as_of", f="day")}: the public number as the page showed it, below, and each challenger on the same days.'
                  if live else f'Every day after the rules froze on {esc(FMT["day"](P["freeze"]))}. Nothing is scored there yet: '
                  f'the data end on {A.n("as_of", f="day")}.')
    return f'''<section id="what"><h2>What this is</h2>
<p class="lead">The percentage on the forecast page is the end of a chain of five stages. <b>S1</b> forecasts the rain. <b>S2</b> turns rain into the chance that each sewer basin overflows. <b>S3</b> works out which beach zones an overflow reaches. <b>S4</b> asks whether the water is still over the state standard in the days after. <b>S5</b> corrects the chain when SFPUC's beach map or a lab result shows what happened. <b>OUT</b> is the number people see. Each stage is scored twice: on its true input (<b>oracle</b>, the stage on its own) and on the real output of the stage before it (<b>chained</b>, the stage as it runs).</p>
<div class="card wide defs">
<div><b>Skill (BSS)</b><span>1 is perfect; 0 is no better than the usual rate for that place and month, taken from the days the fit learned on; below 0 is worse than that usual rate.</span></div>
<div><b>{A.n("bootstrap", "level", f="pct")} ranges</b><span>Every score is recomputed on {A.n("bootstrap", "n", f="i")} resamples of whole storms; the range holds the middle {A.n("bootstrap", "level", f="pct")}.</span></div>
<div><b>Too few storms to decide</b><span>{esc(power)} Such a cell is drawn faded.</span></div>
<div><b>Better, worse, no clear difference</b><span>A difference counts only when its whole range sits on one side of 0.</span></div>
</div>
<h3>Three windows</h3>
<div class="grid3">
<div class="card"><b>{A.n("s2", "pooled", "oracle", "T2", "n_seasons", f="i")} seasons</b><div class="mute">{span_words(A, "s2", "pooled", "oracle", "T2", "span")}</div><p class="fine">Each July–June season is scored by a fit that never saw it: every fitted part is refit without that season. These are development scores: the live forecast's design was chosen with these seasons in view.</p></div>
<div class="card"><b>Post-training</b><div class="mute">{M.n("windows", "post_start", f="mon")} – {post_end} · {A.n("out", "pooled", "oracle", "T1", "n_storm_blocks", f="i")} storms</div><p class="fine">Every fitted part was fit on days before {M.n("windows", "post_start", f="mon")}. With so few storms most cells here say too few storms to decide. The live forecast was also picked with these days in view, which favours it.</p></div>
<div class="card"><b>The live season</b><div class="mute">from {esc(FMT["day"](P["t0"]))}</div><p class="fine">{live_words}</p></div>
</div>
<p class="fine">The artifacts also hold a holdout window ({span_words(A, "s2", "pooled", "oracle", "T1-holdout", "span")}), kept for development only; this page shows the other two.</p>
</section>'''


def section_figure(D: dict) -> str:
    A = D["sets"][0]["scores"]
    svg, phone = F.render(A.get("geography"), A.get("figure"), A.get("figure_counts"))
    return f'''<section id="figure"><h2>The chain, scored</h2>
<p class="lead">The live forecast drawn as its stages, each graded on its own truth. Every box links to its stage below; hover or tap a pill for its {A.n("bootstrap", "level", f="pct")} range, its days and its post-training score.</p>
<p class="figcap">{A.n("figure", "caption", f="t")}</p>
<div class="panel">{svg}{phone}</div></section>'''


def section_lineups(D: dict) -> str:
    diff = lineup_diff(D)
    sv = D["served_json"]
    rows = []
    for i, s in enumerate(D["sets"]):
        man, name = s["manifest"], s["name"]
        until = f'<div class="sm">served until {sv.n("promoted_at", f="day")}</div>' if name == sv.get("replaced") else ""
        who = f'<b>{esc(set_name(man))}</b>{" " + LIVE_BADGE if i == 0 else ""}{until}'
        cells = []
        for col, _ in LINEUP_COLS:
            cid = lineup_of(man)[col]
            d = diff[name][col]
            mark = '<span class="vh"> (differs from the live forecast)</span>' if d else ""
            cells.append(f'<td data-col="{col}" class="{"diff" if d else "same"}" title="{esc(cid)}">{esc(label(col, cid))}{mark}</td>')
        rows.append(f'<tr data-set="{esc(name)}"{" class=live" if i == 0 else ""}><th class="rl">{who}<code>{esc(name)}</code></th>{"".join(cells)}</tr>')
    head = "<tr><th>Forecast</th>" + "".join(f"<th>{esc(h)}</th>" for _, h in LINEUP_COLS) + "</tr>"
    return f'''<section id="lineups"><h2>Lineups</h2>
<p class="lead">A forecast is a lineup: one way of dividing the city into sewer basins, then one part per stage. It goes by its S2 · S3 · S4. Here is the live forecast and every challenger the stages have scored; a shaded cell differs from the live forecast.</p>
{tw(f'<table class="lineup">{head}{"".join(rows)}</table>')}
<p class="fine"><span class="key diff"></span> differs from the live forecast. {LIVE_BADGE} marks the set the forecast page runs now. The grey names are the stored identifiers, kept so the record never breaks. They are labels, not descriptions: in <code>{esc(D["served"])}</code>, “s2” is the old two-stage pipeline's stage 2, which is S3 and S4 on this page, not this page's S2.</p>
</section>'''


def _stage_head(sid: str) -> str:
    st = SP.STAGE[sid]
    return f'<h3><span class="code">{esc(st["code"])}</span> {esc(st["name"])} <span class="q">{esc(st["question"])}</span></h3>'


def _graded(sid: str) -> str:
    st = SP.STAGE[sid]
    if sid == "s1":                                   # nothing comes before rain: its oracle is the floor
        return (f'<p class="fine"><b>Graded on</b> {esc(st["truth"])}. <b>Oracle:</b> {esc(st["oracle"])}. '
                f'<b>Chained:</b> {esc(st["chained"])}.</p>')
    return (f'<p class="fine"><b>Graded on</b> {esc(st["truth"])}. <b>Oracle:</b> fed {esc(st["oracle"])}. '
            f'<b>Chained:</b> fed {esc(st["chained"])}.</p>')


def card_s1(D: dict) -> str:
    S = D["s1"]
    served = S.get("served_model")
    models = [served] + [m for m in S.get("models") if m != served]
    leads = [k for k in S.get("by_lead")]
    live = "<div class=sm>the live forecast's</div>"
    head = "<tr><th>Lead</th>" + "".join(f'<th>{esc(label("s1", m))}{live if m == served else ""}</th>' for m in models) + "</tr>"
    rows = []
    for L in leads:
        day = SP.LEAD_DAYS[int(L)] + (" (optimistic)" if S.get("leads", L, "kind") == "short_lead_optimistic" else "")
        cells = []
        for m in models:
            base = ("by_lead", L, "avg", "models", m)
            if S.opt(*base) is None:
                cells.append('<td class="sc none">—</td>')
                continue
            rng = S.rng(*base, "ci", "continuous", "either_wet", "mae", f="in") if m == served else ""
            cells.append(f'<td class="sc"><b>{S.n(*base, "continuous", "either_wet", "mae", f="in")}</b> {rng}'
                         f'<div class="sm">{S.n(*base, "n_either_wet", f="i")} wet days</div></td>')
        tip = SP.LEAD_TIPS[int(L)]
        rows.append(f'<tr><th class="rl" title="{esc(tip)}">{esc(day)}</th>{"".join(cells)}</tr>')
    # the floor: one gauge as a perfect forecast of the other (both directions give the same miss on the same days;
    # the figure's oracle pill is the first)
    floor = next(iter(S.get("floor")))
    base = ("floor", floor)
    a, b = floor.split(" as ")
    floors = [f'<tr class="floor"><th class="rl">Floor<div class="sm">the {esc(SERIES_WORDS.get(a, a))} as a forecast of the '
              f'{esc(SERIES_WORDS.get(b, b))}</div></th><td class="sc" colspan="{len(models)}"><b>{S.n(*base, "continuous", "either_wet", "mae", f="in")}</b> '
              f'{S.rng(*base, "ci", "continuous", "either_wet", "mae", f="in")}<div class="sm">{S.n(*base, "n_either_wet", f="i")} wet days</div></td></tr>']
    tests = []
    for m in S.get("primary", "vs_served"):
        base = ("primary", "vs_served", m)
        adj, raw = S.get(*base, "verdict"), S.get(*base, "verdict_unadjusted")
        extra = f" ({raw} before allowing for testing {len(S.get('primary', 'vs_served'))} models at once)" if adj != raw else ""
        tests.append(f'{esc(label("s1", m))} − {esc(label("s1", served))}: {S.n(*base, "mae_either_wet", "delta", f="ins")} '
                     f'<span class="ci">[{S.n(*base, "mae_either_wet", "lo", f="ins")}, {S.n(*base, "mae_either_wet", "hi", f="ins")}]</span> '
                     f'{verdict(adj)}{esc(extra)}')
    return f'''<div class="card stage" id="s1">{_stage_head("s1")}
{_graded("s1")}
<p class="fine">Wet-day error: the average miss in inches on days the gauges or the forecast had at least {S.n("wet_day_in", f="2")}″, against the two-gauge mean, {S.n("windows", "previous_runs", 0, f="mon")} – {S.n("windows", "previous_runs", 1, f="mon")} (lower is better). One day ahead, {esc(label("s1", served))} misses by {S.n("by_lead", "1", "avg", "models", served, "continuous", "either_wet", "mae", f="in")}; the two gauges, a few miles apart, miss each other by {S.n("floor", floor, "continuous", "either_wet", "mae", f="in")}: the floor for a forecast made at one point. Five days ahead the miss is {S.n("by_lead", leads[-1], "avg", "models", served, "continuous", "either_wet", "mae", f="in")}.</p>
{tw(f'<table class="st">{head}{"".join(rows)}{"".join(floors)}</table>')}
<p class="fine"><b>The weather-model test</b> (fixed in advance; one day ahead, the two-gauge mean): {" · ".join(tests)}. A weather model is chosen on this stage alone.</p>
</div>'''


def card_s2(D: dict) -> str:
    A, M = D["sets"][0]["scores"], D["sets"][0]["manifest"]
    geo = A.get("geography")
    rows = [("Oracle<div class=sm>the gauges' rain</div>", "s2", "oracle"),
            ("Chained, one day ahead<div class=sm>S1's forecast</div>", "s2", "L1")]
    units = [u for u in A.get("s2") if u != "pooled"]
    contaminated = "selection-contaminated" in str(M.get("windows", "t2_label"))
    note = (" Nine-season scores here are development scores, selection-contaminated: this model's settings were "
            "chosen with those seasons in view.") if contaminated else ""
    return f'''<div class="card stage" id="s2">{_stage_head("s2")}
{_graded("s2")}<p class="fine">Basins pooled.{note}</p>
{stage_table(A, rows)}
<details class="more"><summary>By basin</summary>{unit_table(A, geo, [("oracle", "s2", "oracle"), ("one day ahead", "s2", "L1")], units)}</details>
</div>'''


def card_s3(D: dict) -> str:
    A = D["sets"][0]["scores"]
    geo = A.get("geography")
    rows = [("Oracle<div class=sm>the Westside split, fed the true basin overflows</div>", "s3", "oracle"),
            ("Chained, one day ahead<div class=sm>on the oracle's days</div>", "s3_on_oracle_rows", "L1"),
            ("Measured rain<div class=sm>every zone-day</div>", "s3", "rain"),
            ("One day ahead<div class=sm>every zone-day</div>", "s3", "L1")]
    zones = [u for u in A.get("s3") if u != "pooled"]
    cols = [("oracle", "s3", "oracle"), ("measured rain", "s3", "rain"), ("one day ahead", "s3", "L1")]
    return f'''<div class="card stage" id="s3">{_stage_head("s3")}
{_graded("s3")}
<p class="fine">Only the Westside reaches two zones, so its split is the only part scored on true overflows. A basin that reaches a single zone passes its overflow straight through: {A.n("integrity", "s3_identity_checked", f="i")} such zone-days checked, {A.n("integrity", "s3_identity_mismatches", f="i")} mismatches.</p>
{stage_table(A, rows)}
<details class="more"><summary>By zone</summary>{unit_table(A, geo, cols, zones, missing={"oracle": "checked only"})}</details>
</div>'''


def card_s4(D: dict) -> str:
    A = D["sets"][0]["scores"]
    geo = A.get("geography")
    rows = [("Oracle<div class=sm>the true zone overflows and the gauges' rain</div>", "s4", "oracle"),
            ("Measured rain<div class=sm>the chain from the gauges' rain</div>", "s4", "rain"),
            ("Chained, one day ahead<div class=sm>the chain from S1's forecast</div>", "s4", "L1")]
    zones = [u for u in A.get("s4") if u != "pooled"]
    cols = [("oracle", "s4", "oracle"), ("measured rain", "s4", "rain"), ("one day ahead", "s4", "L1")]
    return f'''<div class="card stage" id="s4">{_stage_head("s4")}
{_graded("s4")}
<p class="fine">Scored on first-look samples only: a sample taken because the zone was over the standard a day or two before is left out, since SFPUC goes back after nearly every one.</p>
{stage_table(A, rows)}
<details class="more"><summary>By zone</summary>{unit_table(A, geo, cols, zones)}</details>
</div>'''


def card_s5(D: dict) -> str:
    A = D["sets"][0]["scores"]
    feeds = [f for f in A.get("s5", "pooled") if f == "oracle" or f.startswith("degraded:")]
    seeds = [f for f in feeds if f.startswith("degraded:")]
    real = [f for f in A.get("s5", "pooled") if f == "archive"]

    def window_of(f):
        return "S5" if A.opt("s5", "pooled", f, "S5") is not None else next(iter(A.get("s5", "pooled", f)))

    def head(f):
        w = window_of(f)
        base = ("s5", "pooled", f, w, "plain")
        name = "Perfect feed" if f == "oracle" else ("2016-17 feed (real)" if f == "archive" else f"Realistic feed {f.split(':')[1]}")
        return (f'<th>{esc(name)}<div class="sm">{A.n(*base, "n", f="i")} zone-days · {A.n(*base, "n_blocks", f="i")} observation events'
                f'<br>{span_words(A, *base, "span")}</div></th>')

    head_row = "<tr><th>Rule</th>" + "".join(head(f) for f in feeds + real) + "</tr>"
    body = []
    for v, what in S5_RULES:
        cells = []
        for f in feeds + real:
            w = window_of(f)
            base = ("s5", "pooled", f, w, v)
            if A.opt(*base) is None:
                cells.append('<td class="sc none">—</td>')
                continue
            d = ("s5", "pooled", f, w, v, "delta_vs_plain")
            if v == "sample_swap":                          # OUT never reads a lab result (A1): 0 by construction
                cells.append(f'<td class="sc none"><b>{A.n(*d, "delta", f="3s")}</b> by construction</td>')
                continue
            lp = bool(A.get(*base, "low_power"))
            cells.append(f'<td class="sc{" lp" if lp else ""}"><b>{A.n(*d, "delta", f="3s")}</b> '
                         f'<span class="ci">[{A.n(*d, "lo", f="3s")}, {A.n(*d, "hi", f="3s")}]</span>'
                         f'<div>{verdict(A.get(*d, "verdict"))}</div>{"<div class=lpw>too few storms to decide</div>" if lp else ""}</td>')
        tip = ' title="its change on the public number is 0 by construction: the public number never reads a lab result"' if v == "sample_swap" else ""
        what = f'<div class="sm">{esc(what)}</div>' if what else ""
        body.append(f'<tr><th class="rl"{tip}>{esc(label("s5", v))}{what}</th>{"".join(cells)}</tr>')
    # the live forecast's rule against no correction, on the realistic feeds (the artifact's verdicts, counted)
    live, lzn = esc(label("s5", "basin_swap")), esc(label("s5", "link_zone_swap"))
    worse = [f for f in seeds if A.get("s5", "pooled", f, "S5", "basin_swap", "delta_vs_plain", "verdict") == "worse"]
    pf = A.get("s5", "pooled", "oracle", "S5", "basin_swap", "delta_vs_plain", "verdict")
    if seeds and len(worse) == len(seeds):
        verdict_live = (f"On every one of the {len(seeds)} realistic feeds, <b>the live forecast's rule, {live}, makes the days after "
                        f"an observation worse than no correction at all</b>. On the perfect feed: {esc(pf)}.")
    elif worse:
        verdict_live = (f"On {len(worse)} of the {len(seeds)} realistic feeds, the live forecast's rule, {live}, makes the days after an "
                        f"observation worse than no correction. On the perfect feed: {esc(pf)}.")
    else:
        verdict_live = (f"On none of the {len(seeds)} realistic feeds is the live forecast's rule, {live}, clearly worse than no "
                        f"correction. On the perfect feed: {esc(pf)}.")
    lz = [A.get("s5", "pooled", f, "S5", "link_zone_swap", "delta_vs_plain", "verdict") for f in ["oracle"] + seeds]
    lz_words = (f"the {lzn} rule is not clearly different from no correction on any of them" if all(x == "no clear difference" for x in lz)
                else f"the {lzn} rule: " + ", ".join(lz))
    prim = A.get("paired", "s5_primary")
    pv = [prim[f]["S5"]["pooled"]["verdict"] for f in ["oracle"] + seeds if f in prim]
    beats = (f"Against the {live} rule, the {lzn} rule is better on the perfect feed ({A.n('paired', 's5_primary', 'oracle', 'S5', 'pooled', 'delta', f='3s')} "
             f"<span class=ci>[{A.n('paired', 's5_primary', 'oracle', 'S5', 'pooled', 'lo', f='3s')}, {A.n('paired', 's5_primary', 'oracle', 'S5', 'pooled', 'hi', f='3s')}]</span>) "
             f"and on every realistic feed" if all(x == "better" for x in pv) else
             f"Against the {live} rule, the {lzn} rule: " + ", ".join(pv))
    return f'''<div class="card stage" id="s5">{_stage_head("s5")}
<p class="fine">When SFPUC's beach map flags an overflow, a correction rule moves the forecast for that day and the week after. Each rule is scored by the change in error on the days after an observation, against no correction at all: below 0 helps, above 0 hurts (lower is better). The perfect feed is every filed overflow, on time; the realistic feeds miss some and show others a day late, as the beach map has (five random draws); the 2016-17 feed is the real thing, on one season.</p>
{tw(f'<table class="st wide">{head_row}{"".join(body)}</table>')}
<p class="fine">{verdict_live} By contrast {lz_words}. {beats}: that is the pre-registered test of this stage. The {esc(label("s5", "sample_swap"))} only moves the water-quality stage, which the public number never reads, so its change here is 0 by construction.</p>
</div>'''


def lead_chart(A: Art) -> str:
    """BSS by lead with its 90% range: nine seasons and post-training. Numbers are in the table beside it."""
    leads = [f"L{i}" for i in range(6)]
    series = (("T2", "#0072BC"), ("T1", "#d4763a"))
    pts = []
    for i, e in enumerate(leads):
        for j, (w, col) in enumerate(series):
            c = A.opt("out", "pooled", e, w)
            if isinstance(c, dict) and c.get("bss") is not None:
                pts.append((i, j, c["bss"], c["ci"]["bss"][0], c["ci"]["bss"][1], bool(c.get("low_power")), col, w))
    ymin = math.floor(min([0.0] + [p[3] for p in pts]) * 4) / 4
    W, H, L, R, T, B = 560, 220, 40, 12, 12, 30
    xw = (W - L - R) / len(leads)

    def x(i, j):
        return L + xw * (i + 0.5) + (j - 0.5) * 16

    def y(v):
        return T + (H - T - B) * (1 - (v - ymin) / (1 - ymin))

    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img"><title>Skill of the public number by lead</title>']
    k = ymin
    while k <= 1.0001:
        o.append(f'<line class="grid{" zero" if abs(k) < 1e-9 else ""}" x1="{L}" y1="{y(k):.1f}" x2="{W - R}" y2="{y(k):.1f}"/>')
        if abs(k) < 1e-9 or abs(k - 0.5) < 1e-9 or abs(k - 1) < 1e-9:
            o.append(f'<text class="ax" x="{L - 6}" y="{y(k) + 4:.1f}" text-anchor="end">{k:g}</text>')
        k += 0.25
    for i in range(len(leads)):
        o.append(f'<text class="ax" x="{L + xw * (i + 0.5):.1f}" y="{H - 8}" text-anchor="middle">{esc(SP.LEAD_DAYS[i])}</text>')
    for i, j, v, lo, hi, lp, col, w in pts:
        cx = x(i, j)
        tip = f"{SP.LEAD_DAYS[i]}, {WINDOW_WORDS[w]}: BSS {FMT['2'](v)} [{FMT['2'](lo)}, {FMT['2'](hi)}]" + ("; too few storms to decide" if lp else "")
        fill = "#fff" if lp else col
        o.append(f'<g><title>{esc(tip)}</title><line x1="{cx:.1f}" y1="{y(hi):.1f}" x2="{cx:.1f}" y2="{y(lo):.1f}" stroke="{col}" stroke-width="2"/>'
                 f'<circle cx="{cx:.1f}" cy="{y(v):.1f}" r="4.5" fill="{fill}" stroke="{col}" stroke-width="2"/></g>')
    o.append("</svg>")
    legend = ('<div class="legend"><span><i style="background:#0072BC"></i>nine seasons</span><span><i style="background:#d4763a"></i>post-training</span>'
              '<span><i class="hollow"></i>too few storms to decide</span></div>')
    return f'<div class="chartbox">{"".join(o)}{legend}</div>'


def section_out(D: dict) -> str:
    A = D["sets"][0]["scores"]
    geo = A.get("geography")
    rows = [("Oracle<div class=sm>the true overflow history</div>", "out", "oracle"),
            ("Measured rain<div class=sm>the chain from the gauges' rain</div>", "out", "rain"),
            ("Chained, one day ahead<div class=sm>the chain from S1's forecast</div>", "out", "L1")]
    lead_rows = []
    for i in range(6):
        e = f"L{i}"
        lab = SP.LEAD_DAYS[i] + (" (optimistic)" if i == 0 else "")
        lead_rows.append((f'<span title="{esc(SP.LEAD_TIPS[i])}">{esc(lab)}</span>', "out", e))
    lead_rows += [("today, as served<div class=sm>with the live page's shorter rain history</div>", "out", "L0s"),
                  ("+1, as served", "out", "L1s")]
    sv = {k: A.get("paired", "as_served", "out", k, "pooled", "T2", "verdict") for k in A.get("paired", "as_served", "out")}
    if all(v == "no clear difference" for v in sv.values()):
        served_words = "shows no clear difference from the full history on the nine seasons"
    else:
        served_words = "against the full history, on the nine seasons: " + "; ".join(f"{k.split(' ')[0]} {v}" for k, v in sv.items())
    zones = [u for u in A.get("out") if u != "pooled"]
    cols = [("oracle", "out", "oracle"), ("measured rain", "out", "rain"), ("one day ahead", "out", "L1")]
    # the levels and the threshold scores at their edges, one day ahead
    lv = "".join(f'<span class="lvl" style="color:{lv.color};background:{lv.tint}">{esc(lv.label)} {lv.lo}–{lv.hi}%</span>' for lv in LEVELS)
    edge_rows = []
    for k, e in enumerate(A.get("edges")):
        lvl = LEVELS[k + 1]
        cells = []
        for w in ("T2", "T1"):
            base = ("out", "pooled", "L1", w, "contingency", str(e))
            cells.append(f'<td class="num">{A.n(*base, "pod", f="pct")}</td><td class="num">{A.n(*base, "far", f="pct")}</td>')
        edge_rows.append(f'<tr><th class="rl"><span style="color:{lvl.color}">{esc(lvl.label)}</span> or above</th>{"".join(cells)}</tr>')
    edge_head = ('<tr><th>One day ahead</th><th class="num">caught · nine seasons</th><th class="num">not bad · nine seasons</th>'
                 '<th class="num">caught · post-training</th><th class="num">not bad · post-training</th></tr>')
    return f'''<section id="out"><h2>The public number</h2>
<p class="lead">What people see: the chance a zone's beaches are affected by a sewer overflow, today and each of the next five days. It is graded on {esc(SP.STAGE["out"]["truth"])}. Zones pooled.</p>
{stage_table(A, rows)}
<details class="more"><summary>By zone</summary>{unit_table(A, geo, cols, zones)}</details>
<h3>By lead: today to five days ahead</h3>
<div class="grid2 lead2"><div class="card">{lead_chart(A)}<p class="fine">The score for today reads a stitched short-lead archive, not forecasts stored as they were issued, so it flatters. “As served” runs the live page's shorter rain history, and {esc(served_words)}.</p></div>
<div>{stage_table(A, lead_rows)}</div></div>
{section_live(D)}
<div class="card" id="levels"><b>Risk levels</b><div class="levels">{lv}</div>
<p class="fine">Fixed bands on the whole percent the page shows, the same on every page; nothing chooses them from data. Of the bad beach days, the share the forecast put at each level or above one day ahead (caught), and of the days it put there, the share that were not bad:</p>
{tw(f'<table class="st">{edge_head}{"".join(edge_rows)}</table>')}</div>
{section_budget(A)}
</section>'''


def section_live(D: dict) -> str:
    """The live season (the rules' T0) as the page showed it: the live forecast's own stored forecasts, graded by lead
    (scores['t0_as_served']), pooled and by zone; one plain line until a data refresh covers the season."""
    A, P = D["sets"][0]["scores"], D["protocol"]
    head = '<h3 id="live">The live season, as the page showed it</h3>'
    if A.opt("t0_as_served", "state") != "graded":
        return (f'{head}<p class="fine">Each day\'s forecast as the page showed it, from {esc(FMT["day"](P["t0"]))}, is graded '
                f'here once a data refresh covers {esc(_date(P["t0"]).strftime("%B %Y"))} or later.</p>')
    T, words = ("t0_as_served", "out"), STAGE_WORDS["out"]
    leads = [f"L{i}" for i in range(6)]
    rows = "".join(f'<tr><th class="rl">{esc(SP.LEAD_DAYS[i])}</th>{score_cell(A, (*T, "pooled", e, "T0"), words, missing="none yet")}</tr>'
                   for i, e in enumerate(leads))
    zones = [u for u in A.get(*T) if u != "pooled"]
    zhead = "<tr><th></th>" + "".join(f"<th>{esc(d)}</th>" for d in SP.LEAD_DAYS) + "</tr>"
    zrows = "".join(f'<tr><th class="rl">{esc(zone_label(z))}</th>'
                    + "".join(score_cell(A, (*T, z, e, "T0"), words, missing="none yet") for e in leads) + "</tr>" for z in zones)
    c = ("t0_as_served", "counts")
    return f'''{head}
<p class="fine">The percentages the page showed each day from {esc(FMT["day"](P["t0"]))}, stored as they were issued, graded on the same truth as above: {A.n(*c, "issue_days", f="i")} days of forecasts; {A.n(*c, "graded", f="i")} zone forecasts graded, {A.n(*c, "waiting", f="i")} waiting for the records that grade them, {A.n(*c, "other_model", f="i")} made by another lineup. These are the forecasts people saw; the challengers' tests run every lineup on the same inputs instead.</p>
{tw(f'<table class="st"><tr><th>All zones together</th><th>The live season</th></tr>{rows}</table>')}
<details class="more"><summary>By zone</summary>{tw(f'<table class="st wide">{zhead}{zrows}</table>')}</details>'''


def section_budget(A: Art) -> str:
    lad = ("paired", "ladder", "pooled")
    windows = [w for w in ("T2", "T1") if A.opt(*lad, w) is not None]
    rungs = list(LADDER_WORDS)
    drops = list(A.get(*lad, windows[0], "drops"))
    head = ["<tr><th></th>"]
    for w in windows:
        sub = ("the days with a forecast archive, from " + A.n("out", "pooled", "L1", w, "span", 0, f="mon")) if w == "T2" else span_words(A, "out", "pooled", "L1", w, "span")
        head.append(f'<th>{esc(cap(WINDOW_WORDS[w]))}<div class="sm">{sub}<br>{A.n(*lad, w, "n", f="i")} zone-days · '
                    f'{A.n(*lad, w, "n_pos", f="i")} bad beach days</div></th>')
    head.append("</tr>")
    body = []
    for i, r in enumerate(rungs):
        name, words = LADDER_WORDS[r]
        body.append(f'<tr class="rung"><th class="rl">{esc(name)}<div class="sm">{esc(words)}</div></th>'
                    + "".join(f'<td class="sc">error {A.n(*lad, w, "bs", r, f="4")}</td>' for w in windows) + "</tr>")
        if i < len(drops):
            k = drops[i]
            cells = []
            for w in windows:
                d = (*lad, w, "drops", k)
                v = A.get(*d, "verdict")
                word = {"worse": "adds error", "better": "removes error"}.get(v, v)
                cells.append(f'<td class="sc step"><b>{A.n(*d, "delta", f="4s")}</b> <span class="ci">[{A.n(*d, "lo", f="4s")}, '
                             f'{A.n(*d, "hi", f="4s")}]</span> {chip(word, VERDICT_CLASS.get(v, "na"))}</td>')
            body.append(f'<tr class="drop"><th class="rl">↓ {esc(STEP_WORDS.get(k, k))}</th>{"".join(cells)}</tr>')
    # the reading: which steps clearly add error, and the largest, per window (artifact verdicts and deltas, compared)
    never = [k for k in drops if all(A.get(*lad, w, "drops", k, "verdict") != "worse" for w in windows)]
    always = [k for k in drops if all(A.get(*lad, w, "drops", k, "verdict") == "worse" for w in windows)]
    big = {w: max(drops, key=lambda k: A.get(*lad, w, "drops", k, "delta")) for w in windows}
    parts = []
    if never:
        parts.append(f"Once the true basin overflows are known, little is lost: {' and '.join(STEP_WORDS[k] for k in never)} never clearly adds error.")
    if always:
        parts.append(f"{cap(' and '.join(STEP_WORDS[k] for k in always))} clearly adds error in every window.")
    parts.append("The largest step: " + "; ".join(f"{STEP_WORDS[big[w]]} on {BUDGET_WINDOWS.get(w, WINDOW_WORDS[w])}" for w in windows) + ".")
    parts.append("So most of the skill is lost before the beaches: predicting whether the sewer overflows from rain, and forecasting the rain itself.")
    if set(big.values()) - {"rain − truth_at_s3", "L1 − rain"}:
        parts.pop()
    return f'''<h3 id="budget">Where skill is lost</h3>
<p class="fine">The same days scored four ways, from every stage perfect down to the real chain one day ahead. The error is the Brier score, the average squared miss of the percentage (lower is better); each step down adds the error of one stage.</p>
{tw(f'<table class="st ladder">{"".join(head)}{"".join(body)}</table>')}
<p class="reading">{" ".join(parts)}</p>'''


# ── challengers ──────────────────────────────────────────────────────────────

def _test_value(A: Art, base: tuple, row: dict) -> str:
    """A test's difference with its range (more places when it is small), or a skill with its range."""
    if row.get("delta") is not None and row.get("ci") is not None:
        f = "3s" if max(abs(row["delta"]), *(abs(x) for x in row["ci"])) >= 0.01 else "4s"
        return (f'<b>{A.n(*base, "delta", f=f)}</b> <span class="ci">[{A.n(*base, "ci", 0, f=f)}, {A.n(*base, "ci", 1, f=f)}]</span>')
    if row.get("bss") is not None:
        return f'skill <b>{A.n(*base, "bss")}</b> {A.rng(*base, "ci")}'
    return '<span class="mute">—</span>'


def window_words(w) -> str:
    """A test's window in words: the tier names, or the month a dated window starts."""
    w = str(w or "")
    if w in WINDOW_WORDS:
        return WINDOW_WORDS[w]
    d = re.search(r"\d{4}-\d{2}-\d{2}", w)
    if w == "S5" or d:
        return "the days after observations" + (f", from {FMT['mon'](d.group(0))}" if d else "")
    return clean(w)


def _status_cell(status: str, tip: str) -> str:
    return f'<td>{chip(status, STATUS_CLASS.get(status, "na"), tip)}</td>'


def _na_words(row: dict) -> str:
    reason = str(row.get("reason", ""))
    pairs = NA_WORDS.get(row["id"], [])
    for pat, words in ([pairs] if isinstance(pairs, tuple) else pairs):
        if re.search(pat, reason):
            return words
    return clean(reason)


def _nyc_words(row: dict) -> str:
    """A "not yet computable" row's plain reason (the artifact's own is the tooltip)."""
    reason = str(row.get("reason", ""))
    if re.search(r"rebuild", reason, re.I):
        return "waiting for the live forecast to be scored again"
    if re.search(r"T0|prospective|live season", reason):
        return "waiting for the live season's days"
    return "not computed yet"


def challenger_card(D: dict, s: dict) -> str:
    A, name = s["scores"], s["name"]
    P = A.get("primaries")
    diff = lineup_diff(D)[name]
    live = lineup_of(D["sets"][0]["manifest"])                      # the live forecast's parts, by their fixed names
    live_s4, live_s5 = esc(label("s4", live["s4"])), esc(label("s5", live["s5"]))
    changed = [f'{esc(h.split(" · ")[0])}: {esc(label(c, lineup_of(s["manifest"])[c]))}' for c, h in LINEUP_COLS if diff[c]]
    rows = []
    for i, r in enumerate(P["rows"]):
        base = ("primaries", "rows", i)
        test = esc(PRIMARY_WORDS.get(r["id"], clean(r.get("comparison", r["id"]))))
        tip = f'{r["id"]} · {r.get("comparison", "")} · {r.get("rule", "")} · {r.get("reason", "")}'
        parts = r.get("parts") or []
        if r["status"] == "not applicable" or (r["status"] == "not yet computable" and not parts and r.get("delta") is None):
            why = _na_words(r) if r["status"] == "not applicable" else _nyc_words(r)
            rows.append(f'<tr class="na"><th class="rl" title="{esc(tip)}">{test}</th><td colspan="2" class="mute">{esc(why)}</td>'
                        f'{_status_cell(r["status"], r.get("reason", ""))}</tr>')
            continue
        if not parts:
            note = ""
            if r.get("caveat") and r["id"] == "S4":
                note = (f'<div class="sm">its other half, fixing {live_s4}, could not be tested here: {live_s4} shows no such problem '
                        f'on these samples; Chase\'s reading</div>')
            rows.append(f'<tr><th class="rl" title="{esc(tip)}">{test}{note}</th><td class="mute">{esc(window_words(r.get("window")))}</td>'
                        f'<td class="sc">{_test_value(A, base, r)} {verdict(r["verdict"]) if r.get("verdict") else ""}</td>{_status_cell(r["status"], r.get("reason", ""))}</tr>')
            continue
        note = ""
        if r["id"] == "S5" and r.get("caveat"):
            note = f'<div class="sm">tested on the live forecast\'s chain, where the {live_s5} rule runs</div>'
        if r["id"] == "OUT" and r.get("t0") == "empty":
            note = '<div class="sm">the live season holds no scored day yet</div>'
        elif r["id"] == "OUT" and r.get("t0") == "scored":
            note = '<div class="sm">post-training and the live season, judged together</div>'
        for j, p in enumerate(parts):
            pb = (*base, "parts", j)
            first = f'<th class="rl" rowspan="{len(parts)}" title="{esc(tip)}">{test}{note}<div class="sm">overall: {chip(r["status"], STATUS_CLASS.get(r["status"], "na"))}</div></th>' if j == 0 else ""
            what = esc(PART_WORDS.get(p["part"], clean(p["part"])))
            rows.append(f'<tr>{first}<td class="mute">{what}</td><td class="sc">{_test_value(A, pb, p)} {verdict(p["verdict"]) if p.get("verdict") else ""}</td>'
                        f'{_status_cell(p["status"], p.get("reason", ""))}</tr>')
    head = "<tr><th>What was tested</th><th>Judged on</th><th>Challenger − live forecast</th><th>Result</th></tr>"
    crit = A.get("promotion", "criteria")
    met = sum(1 for c in crit if c["status"] == "met")
    m = re.match(r"(\d+) of (\d+) met", str(A.get("promotion", "summary")))
    if not m or int(m.group(1)) != met or int(m.group(2)) != len(crit):
        raise ValueError(f"{name}: the promotion summary {A.get('promotion', 'summary')!r} disagrees with its criteria")
    checklist = "".join(f'<li title="{esc(c.get("criterion", ""))} · {esc(c.get("reason", ""))}">{chip(c["status"], STATUS_CLASS.get(c["status"], "na"))} '
                        f'{esc(CRITERIA_WORDS.get(c["n"], clean(c.get("criterion", ""))))}</li>' for c in crit)
    seen = A.opt("windows", "T1", "tag") == "post_seen"
    seen_note = ('<p class="fine warn">This lineup was designed after an earlier challenger\'s post-training scores had been seen, '
                 'so its post-training scores cannot confirm it; only the live season can.</p>') if seen else ""
    return f'''<div class="card chal" id="c-{esc(name)}"><div class="bh"><b>{esc(set_name(s["manifest"]))}</b><code>{esc(name)}</code></div>
<p class="fine">Differs from the live forecast in {"; ".join(changed) if changed else "nothing"}. Differences are in Brier error, challenger minus the live forecast: below 0 means the challenger erred less.</p>{seen_note}
{tw(f'<table class="tests">{head}{"".join(rows)}</table>')}
<div class="check"><b>{m.group(1)} of {m.group(2)} conditions met</b><ol>{checklist}</ol></div>
</div>'''


def section_bakeoff(D: dict) -> str:
    B = D["bakeoff"]
    cons = list(B.get("grid", "contenders"))
    picks = B.get("nested", "picked_by_fold")
    rows = []
    for c in cons:
        bends = B.get("development", "choices", c, "bends")
        terms = B.get("grid", "contenders", c, "terms", bends)
        feats = list(dict.fromkeys(re.split(r"[:>]", t)[0] for t in terms))
        if B.get("grid", "contenders", c, "kind") == "hinges":
            inputs = f"all {len(feats)} rain inputs, plus {sum(1 for t in terms if '>' in t)} bends"
        else:
            per = {f: sum(1 for t in terms if t.split(":")[0] == f) for f in feats}
            inputs = ", ".join(FEATURE_WORDS.get(f, f) + (f" in {k} bands" if k > 1 else "") for f, k in per.items())
        nb = ("nested", "by_contender", c, "pooled")
        t1 = ("T1", "scores", c, "pooled")
        lp = bool(B.get(*t1, "low_power"))
        won = sum(1 for v in picks.values() if v == c)
        rows.append(f'<tr{" class=winner" if c == B.get("winner", "contender") else ""}><th class="rl"><code>{esc(c)}</code></th>'
                    f'<td class="num">{B.n("grid", "contenders", c, "n_terms", f="i")}</td><td>{esc(inputs)}</td>'
                    f'<td class="sc"><b>{B.n(*nb, "bss")}</b> {B.rng(*nb, "ci", "bss")}<div class="sm">error {B.n(*nb, "bs", f="5")}</div></td>'
                    f'<td class="sc{" lp" if lp else ""}"><b>{B.n(*t1, "bss")}</b> {B.rng(*t1, "ci", "bss")}'
                    f'{"<div class=lpw>too few storms to decide</div>" if lp else ""}</td>'
                    f'<td class="num">{won} of {len(picks)}</td></tr>')
    head = ("<tr><th>Term set</th><th class=num>Terms</th><th>Inputs</th><th>Nine seasons, nested</th>"
            "<th>Post-training</th><th class=num>Picked in</th></tr>")
    fp = ("development", "final_pick")
    rk = B.get(*fp, "ranking")
    low, nxt = rk[0]["contender"], rk[1]["contender"]
    vs = ("nested", "vs_served_recipe", "procedure", "pooled")
    sf = ("south_floor",)
    return f'''<h3 id="bakeoff">The S2 bake-off: four term sets on the city's basins</h3>
<p class="fine">Chase's rule, fixed before it ran: the same terms in every basin, each basin its own weights, none below 0. For each of the {len(picks)} seasons the term set was picked on the other seasons and then scored on the held-out one (nested), so these scores never saw their own season. The rule: the lowest error wins, unless the {B.n("grid", "bootstrap", "level", f="pct")} range of its lead over the runner-up includes 0; then the one with fewer terms wins.</p>
{tw(f'<table class="st">{head}{"".join(rows)}</table>')}
<p class="fine"><b>The pick, on all {len(picks)} seasons together:</b> <code>{esc(low)}</code> has the lowest error ({B.n(*fp, "ranking", 0, "brier", f="5")}), <code>{esc(nxt)}</code> is next ({B.n(*fp, "ranking", 1, "brier", f="5")}); the range of the gap, {B.n(*fp, "delta", "delta", f="5s")} <span class="ci">[{B.n(*fp, "delta", "lo", f="5s")}, {B.n(*fp, "delta", "hi", f="5s")}]</span>, {"includes" if B.get(*fp, "ci_includes_0") else "excludes"} 0, so <b><code>{esc(B.get(*fp, "winner"))}</code> wins</b> ({B.n(*fp, "ranking", 0, "n_terms", f="i")} terms against {B.n(*fp, "ranking", 1, "n_terms", f="i")}). It is the overflow model of both city-basin challengers.</p>
<p class="fine">The nested pick against the live forecast's recipe refit on the same basins: {B.n(*vs, "delta", f="5s")} <span class="ci">[{B.n(*vs, "lo", f="5s")}, {B.n(*vs, "hi", f="5s")}]</span> {verdict(B.get(*vs, "verdict"))}. The South basin, with few overflows, still beats its usual rate: skill {B.n(*sf, "bss")} {B.rng(*sf, "ci")} on {B.n(*sf, "n_pos", f="i")} overflow days. The term sets themselves were drawn up before the bake-off, partly on these seasons, and post-training days had been looked at too, so only the live season is clean of every choice.</p>'''


def section_challengers(D: dict) -> str:
    cards = "".join(challenger_card(D, s) for s in D["sets"][1:] if s["scores"].opt("primaries") is not None)
    return f'''<section id="challengers"><h2>Challengers against the live forecast</h2>
<p class="lead">Each challenger is tested against the live forecast with comparisons written down before any was run, stage by stage on the stage it changes, then on the public number. A challenger replaces the live forecast only when all five conditions hold, and the last two are Chase's: whether to switch is his call. This page lists the tests; it recommends nothing.</p>
{cards}
{section_bakeoff(D)}
</section>'''


# ── left out, and not claimed ────────────────────────────────────────────────

def _units_for(stage: str, A: Art) -> list:
    cat = A.get("catalog", "exclusions")
    seen = []
    for rules in cat.get(stage, {}).values():
        for u in rules:
            if u not in seen:
                seen.append(u)
    if stage == "s2":
        return [b.key for b in G.get(A.get("geography")).basins]
    if stage == "s1":
        return [u for u in seen if u in SERIES_WORDS]
    return list(ZONES)


S1_BUILD_RULES = ("X-S1-NWPGAP", "X-S1-NOLEAD")      # need the weather model's archive: counted by S1's own scores


def _s1_count(S1: Art, rule: str, series: str) -> str:
    """A rule's count for the served weather model, one day ahead, from S1's partition (absent = none left out)."""
    path = ("exclusions", S1.get("served_model"), "previous_runs", series, "L1", "T1", "excluded")
    got = S1.opt(*path)
    if not isinstance(got, dict):
        return '<td class="num mute">—</td>'
    return f'<td class="num">{S1.n(*path, rule, f="i")}</td>' if rule in got else '<td class="num mute">0</td>'


def ledger_table(A: Art, S1: Art, sid: str) -> str:
    st = SP.STAGE[sid]
    geo = A.get("geography")
    units = _units_for(sid, A)
    cat = ("catalog", "exclusions", sid)
    head = "<tr><th>Left out of the score</th><th>How</th>" + "".join(f"<th class=num>{esc(unit_label(geo, u))}</th>" for u in units) + "</tr>"
    body = []
    for x in st["exclusions"]:
        e = SP.EXCLUSIONS[x]
        how = KIND_WORDS.get(e["kind"], e["kind"])
        counts = A.opt(*cat, x)
        extra = ""
        if isinstance(counts, dict) and set(counts) <= set(units) and counts:
            cells = "".join(f'<td class="num">{A.n(*cat, x, u, f="i")}</td>' if u in counts else '<td class="num mute">—</td>' for u in units)
            if sid == "s5":                                  # the catalog counts S5's rules on the 2016-17 archive's days
                how += " · the 2016-17 archive's days"
        elif isinstance(counts, dict) and counts:            # by station (the follow-up stations)
            extra = "<div class=sm>" + " · ".join(f'{esc(k.split("_")[0])}: {A.n(*cat, x, k, f="i")} station-days' for k in counts) + "</div>"
            cells = f'<td colspan="{len(units)}" class="mute">by station, above</td>'
        elif sid == "s1" and x in S1_BUILD_RULES:          # the weather model's archive: S1's own scores count it
            cells = "".join(_s1_count(S1, x, u) for u in units)
            how += f" · {label('s1', S1.get('served_model'))}, one day ahead"
        else:
            fc = A.opt("figure_counts", "stages", sid, x)
            if fc is not None:
                scope = (A.n("figure_counts", "feeds", sid, "chip", f="t") if A.opt("figure_counts", "feeds", sid, "chip")
                         else "in the scores")
                cells = f'<td colspan="{len(units)}" class="sc">{A.n("figure_counts", "stages", sid, x, f="i")} <span class="sm">{scope}</span></td>'
            else:
                words = "none, by construction" if x == "X-ALL-INSAMPLE" else ("marked on each score" if e["kind"] == "tag" else "not scored yet")
                cells = f'<td colspan="{len(units)}" class="mute">{esc(words)}</td>'
        body.append(f'<tr title="{esc(x)}"><th class="rl">{esc(cap(e["chip"]))}<div class="sm">{esc(e["plain"])}</div>{extra}</th>'
                    f'<td class="mute">{esc(how)}</td>{cells}</tr>')
    return f'<h4>{esc(st["code"])} · {esc(st["name"])}</h4>' + tw(f'<table class="st ledger">{head}{"".join(body)}</table>')


def section_left_out(D: dict) -> str:
    A = D["sets"][0]["scores"]
    P = D["protocol"]
    cat = ("catalog",)
    ledgers = "".join(ledger_table(A, D["s1"], s["id"]) for s in SP.STAGES)
    # what the forecast does not claim, per zone
    zones = list(ZONES)
    head = "<tr><th>The forecast does not claim</th>" + "".join(f"<th class=num>{esc(zone_label(z))}</th>" for z in zones) + "</tr>"
    body = []
    for c, w in SP.CLAIMS.items():
        base = ("claims", c)
        cl = A.get(*base)
        if "days" in cl and "episodes" in cl:
            cells = "".join(f'<td class="num">{A.n(*base, "days", z, f="i")} days<div class="sm">{A.n(*base, "episodes", z, f="i")} episodes</div></td>' for z in zones)
            sub = ""
        elif c == "C-UNMON":
            cells = "".join(f'<td class="num">{A.n(*base, "days", z, f="i")} overflow days</td>' for z in zones)
            sub = " · ".join(f'{esc(o)} {A.n(*base, "outfalls", o, "km", f="km")} from a station' for o in cl["outfalls"])
        elif "days" in cl:
            cells = "".join(f'<td class="num">{A.n(*base, "days", z, f="i")} days</td>' for z in zones)
            sub = span_words(A, *base, "span") if cl.get("span") else ""
        else:
            note = f'{A.n(*base, "events_crossing_midnight", f="i")} overflows ran past midnight' if "events_crossing_midnight" in cl else "not counted: outside what a daily zone forecast can say"
            cells = f'<td colspan="{len(zones)}" class="mute">{note}</td>'
            sub = ""
        body.append(f'<tr title="{esc(c)}"><th class="rl">{esc(cap(w["pill"]))}<div class="sm">{esc(w["plain"])}</div>'
                    f'{"<div class=sm>" + sub + "</div>" if sub else ""}</th>{cells}</tr>')
    eps = A.get(*cat, "ledger_suspect")
    ep_rows = "".join(f'<li>{esc(e["basin_name"])} · {A.n(*cat, "ledger_suspect", i, "start", f="day")} – {A.n(*cat, "ledger_suspect", i, "end", f="day")} · '
                      f'{A.n(*cat, "ledger_suspect", i, "n_days", f="i")} days · {esc(", ".join(zone_label(z) for z in e["zones"]))}</li>'
                      for i, e in enumerate(eps))
    versions = []
    for v in P["versions"]:
        line, extra, ids = VERSION_NOTES[v["version"]]
        tip = f' title="{esc(", ".join(ids))}"' if ids else ""
        versions.append(f'<li{tip}><b>{esc(v["version"])}</b>, frozen {esc(FMT["day"](v["freeze"]))}{" (in force)" if v["version"] == P["version"] else ""}: '
                        f'{esc(line)}{(" " + esc(extra)) if extra else ""}</li>')
    return f'''<section id="left-out"><h2>What is left out, and what is not claimed</h2>
<p class="lead">A score is only as honest as the list of what it leaves out. Each stage's rules run in a fixed order, and each left-out day is counted once, under the first rule it meets. Counts are for the live forecast, {A.n("catalog", "start", f="mon")} – {A.n("catalog", "as_of", f="mon")}.</p>
{ledgers}
<h3 id="claims">What the forecast does not claim</h3>
<p class="fine">Counted, never hidden: a sample over the standard with no overflow behind it stays in the public number's score as a day the forecast was right to call quiet.</p>
{tw(f'<table class="st ledger">{head}{"".join(body)}</table>')}
<h3 id="suspect">Where the ledger likely missed an overflow</h3>
<p class="fine">{esc(SP.EXCLUSIONS["X-LEDGER-SUSPECT"]["plain"])} There are {A.n(*cat, "ledger_suspect", f="len")} such basin windows, {A.n("figure_counts", "stages", "s2", "X-LEDGER-SUSPECT", f="i")} basin-days.</p>
<details class="more"><summary>All {A.n(*cat, "ledger_suspect", f="len")}</summary><ol class="eps">{ep_rows}</ol></details>
<h3 id="rules">Rules versions</h3>
<ul class="versions">{"".join(versions)}</ul>
<p class="fine">Scores made under different rules are never compared; every score on this page is under {esc(P["version"])}.</p>
</section>'''


# ── the page ────────────────────────────────────────────────────────────────

CSS = """
.wrap{max-width:1440px;margin:0 auto;padding:24px}
.rh{display:flex;gap:18px;align-items:flex-start;margin-bottom:8px}.rh .mark{width:56px;height:56px;border-radius:14px}
h1{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:40px;letter-spacing:.04em;margin:0 0 6px;color:#26272a}
h2{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:28px;letter-spacing:.05em;margin:30px 0 8px;color:#26272a}
h3{font-size:16px;margin:20px 0 6px;color:#26272a}h4{margin:16px 0 6px;font-size:14px;color:#26272a}
.sub{font-size:16px;color:#54576F;margin:0 0 8px;max-width:900px}.lead{font-size:15px;color:#54576F;max-width:960px;margin:6px 0 12px}
.meta{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:#8a949b}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin:10px 0 6px;font-size:14px;font-weight:600}nav a{color:#0072BC;text-decoration:none}nav a:hover{text-decoration:underline}
.figcap{font-size:13px;color:#54576F;margin:-2px 0 10px;max-width:960px}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.grid2.lead2{grid-template-columns:minmax(0,5fr) minmax(0,6fr);align-items:start}.grid2>*,.grid3>*,.defs>*{min-width:0}
@media(max-width:1000px){.grid3{grid-template-columns:repeat(2,minmax(0,1fr))}.grid2.lead2{grid-template-columns:minmax(0,1fr)}}
@media(max-width:640px){.grid2,.grid3{grid-template-columns:minmax(0,1fr)}.rh{flex-direction:column}.wrap{padding:16px 14px}h1{font-size:34px}}
.card{background:#fff;border:1px solid #d9e4e8;border-radius:18px;padding:14px 16px;font-size:14px;color:#26272a;min-width:0}.card.wide{grid-column:1/-1}
.card > b, .card > div > b:first-child{display:block;font-size:15px;margin-bottom:4px}.card p b{display:inline;font-size:inherit;margin:0}
.card .bh{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:4px 12px}.card .bh b{margin:0;font-size:15px}
.card.stage{margin:12px 0}.card.chal{margin:14px 0}
.defs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px 18px}.defs b{display:block;margin-bottom:2px}.defs span{color:#54576F;font-size:13.5px}
@media(max-width:1000px){.defs{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:640px){.defs{grid-template-columns:minmax(0,1fr)}}
.mute{color:#8a949b}.fine{font-size:12.5px;color:#54576F;margin:6px 0 0;max-width:1000px}.fine.warn{color:#b5310a}
.reading{font-size:14px;color:#26272a;margin:8px 0 0;max-width:1000px}
h3 .code{display:inline-block;background:#0072BC;color:#fff;border-radius:8px;padding:1px 8px;font-size:13px;margin-right:4px;vertical-align:1px}
h3 .q{font-weight:400;font-style:italic;color:#54576F;font-size:14px;margin-left:4px}
code{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#54576F;overflow-wrap:anywhere}
th.rl code{display:block;margin-top:2px;font-weight:400}.card .bh code{color:#8a949b}
.tw{position:relative;overflow-x:auto;-webkit-overflow-scrolling:touch;margin:8px 0;border:1px solid #d9e4e8;border-radius:14px;background:#fff}
.tw table{width:100%;border-collapse:collapse;font-size:13.5px}
.tw table.wide{min-width:880px}.tw table.lineup{min-width:900px}.tw table.tests{min-width:720px}
th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#54576F;padding:8px 10px;border-bottom:1px solid #d9e4e8;background:#f7fafc;vertical-align:bottom}
th .sm{text-transform:none;letter-spacing:0;font-weight:400}
td{padding:7px 10px;border-bottom:1px solid #eef2f5;vertical-align:top}
th.rl{text-transform:none;letter-spacing:0;font-size:13px;color:#26272a;background:#fff;vertical-align:top;font-weight:600;min-width:150px}
th.rl .sm{font-weight:400}
@media(max-width:640px){th.rl{min-width:96px}th,td{padding:6px 7px}.card{padding:12px 12px}}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.sc{font-variant-numeric:tabular-nums;white-space:nowrap}td.sc .sm,td.sc .lpw{white-space:normal}
td.sc.none{color:#8a949b;font-style:italic}
.sm{font-size:11.5px;color:#8a949b;margin-top:2px}.ci{color:#54576F;font-size:12px}
td.lp>b,td.lp>.ci{opacity:.55}.lpw{font-size:11px;color:#8a949b;font-style:italic}
tr.floor th.rl,tr.floor td{background:#f7fafc}
tr.drop th.rl{font-weight:400;color:#54576F;padding-left:18px}tr.drop td{background:#fbfcfd}
tr.winner th.rl code{color:#0072BC;font-weight:700}
table.lineup td{font-size:13px}table.lineup tr.live th.rl,table.lineup tr.live td{background:#f3f8fc}
table.lineup td.diff,.key.diff{background:#fbe9d9;box-shadow:inset 3px 0 #d4763a}
.key{display:inline-block;width:14px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:4px}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.v{display:inline-block;font-size:11px;font-weight:700;border-radius:999px;padding:1px 8px;margin:1px 0;white-space:nowrap}
.v.good{background:#e0f0ea;color:#237059}.v.bad{background:#f8dcd6;color:#b5310a}.v.na{background:#f3f6f9;color:#54576F}
.check{margin-top:8px}.check b{font-size:14px}.check ol{margin:6px 0 0;padding-left:20px;font-size:13.5px}.check li{margin:4px 0}
.levels{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}.lvl{font-size:13px;font-weight:700;border-radius:999px;padding:3px 12px}
details.more{margin-top:8px}details.more summary{cursor:pointer;font-weight:700;color:#0072BC;font-size:14px}
.chartbox{margin-top:4px}svg.chart{display:block;width:100%;max-width:560px;height:auto;font-family:Roboto,Arial,sans-serif}
svg.chart .grid{stroke:#eef2f5}svg.chart .grid.zero{stroke:#b9c7cf}svg.chart .ax{font-size:11px;fill:#8a949b}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#54576F;margin-top:4px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}.legend i.hollow{border:2px solid #8a949b;background:#fff;width:6px;height:6px}
ol.eps{font-size:13px;color:#54576F;columns:2;column-gap:28px;margin:8px 0}@media(max-width:640px){ol.eps{columns:1}}
ul.versions{font-size:14px;color:#26272a;padding-left:20px;max-width:1000px}ul.versions li{margin:6px 0}
footer.srcs{margin-top:28px;border-top:1px solid #d9e4e8;padding-top:10px}footer.srcs code{font-size:11.5px}
"""

FIT_SCRIPT = """<script>
/* fit every figure label to its box in this browser's font: start at full size, shrink only as far as it must */
(function () {
  function fit() {
    document.querySelectorAll('svg text[data-maxw]').forEach(function (t) {
      var max = +t.dataset.maxw, full = +t.dataset.fs || parseFloat(getComputedStyle(t).fontSize);
      t.style.fontSize = full + 'px';
      var len = t.getComputedTextLength();
      if (len > max) t.style.fontSize = (full * max / len * 0.98).toFixed(2) + 'px';
    });
  }
  fit();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
})();
</script>"""


def sprite() -> str:
    """The app's icon sprite, minus its Jinja comment (the figure and the phone list draw from it)."""
    return re.sub(r"\{#-.*?-#\}", "", ICONS.read_text(), flags=re.S)


def render(D: dict | None = None) -> str:
    """The whole page from the artifacts."""
    D = load() if D is None else D
    S = D["sets"][0]
    A, M, P = S["scores"], S["manifest"], D["protocol"]
    srcs = sources(D)
    body = [
        f'''<header class="rh"><img src="/static/brand/bwtf_144x144.png" alt="" class="mark"><div><h1>The forecast, stage by stage</h1>
<p class="sub">Five stages turn rain into the beach percentage. Each is scored on its own and as part of the chain, on days its fit never saw. The live forecast: <b>{esc(lineup_words(M))}</b> {LIVE_BADGE}</p>
<div class="meta"><span>rules {esc(P["version"])}, frozen {esc(FMT["day"](P["freeze"]))}</span><span>data through {A.n("as_of", f="day")}</span><span>scored {M.n("built_at", f="day")}</span><span><a href="{HOW_IT_WORKS}">how it works →</a></span><span><a href="{INDEX}">every report →</a></span></div></div></header>''',
        '<nav><a href="#what">What this is</a><a href="#figure">The chain, scored</a><a href="#lineups">Lineups</a><a href="#s1">Each stage</a>'
        '<a href="#out">The public number</a><a href="#challengers">Challengers</a><a href="#left-out">Left out</a></nav>',
        section_what(D), section_figure(D), section_lineups(D),
        f'<section id="stages"><h2>Each stage of the live forecast</h2><p class="lead">Skill on the {A.n("s2", "pooled", "oracle", "T2", "n_seasons", f="i")} seasons and on post-training days, oracle and chained. Each value is skill with its {A.n("bootstrap", "level", f="pct")} range; below it, the days scored, the days that count as positive, and the storms they fall in.</p>'
        + card_s1(D) + card_s2(D) + card_s3(D) + card_s4(D) + card_s5(D) + "</section>",
        section_out(D), section_challengers(D), section_left_out(D),
        '<footer class="srcs"><p class="fine">Every number on this page is a field of one of these files, built by the stages build '
        'and read here without recomputing: ' + ", ".join(f"<code>{esc(a.rel)}</code>" for a in srcs.values()) +
        f'; the rules: <code>{esc(str(PROTOCOL.relative_to(REPO)))}</code>. Page: <code>{esc(str(Path(__file__).relative_to(REPO)))}</code>.</p></footer>',
    ]
    src_json = json.dumps({k: a.rel for k, a in srcs.items()}, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>The forecast, stage by stage</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/brand.css"><style>{CSS}{LIVE_CSS}{F.CSS}</style></head><body>{sprite()}
<div class="wrap">
{"".join(body)}
</div><script type="application/json" id="sources">{src_json}</script>{FIT_SCRIPT}</body></html>
'''


def write(path: Path = OUT) -> Path:
    path.write_text(render(), encoding="utf-8")
    return path


def main(argv=None) -> None:
    """``export_stages_report.py [PATH]``: write the page (to the report by default) and print where."""
    argv = sys.argv[1:] if argv is None else argv
    print(write(Path(argv[0]) if argv else OUT))


if __name__ == "__main__":
    main()
