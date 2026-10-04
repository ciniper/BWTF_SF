#!/usr/bin/env python3
"""The reports index, public at /reports/ (reports/index.html): what to read, in order, with every page
under reports/ listed exactly once; and the one stamper that marks an archived page.

    venv/bin/python features/forecast/src/models/export_reports_index.py   # stamps the archived pages, writes the index

Current pages, in reading order (the five stages, STAGES_DESIGN.md A8; 2026-10-03):

    2026-10_forecast_stages.html                 The forecast, stage by stage: how good each stage is. Start here.
    2026-09_forecast_how_it_works.html           How the forecast works: what each stage does.
    2026-09_forecast_<set>_model_explorer.html   Inside the overflow model (S2), one page per model set.
    2026-09_forecast_stage2_explorer.html        From an overflow to beach risk (S3–S4).

Archived pages keep their files and URLs and are never regenerated: ARCHIVED says when, what replaced
them and why, and ``stamp`` puts that on the page as a banner, right after <body>, between two HTML
comments, so stamping again rewrites the banner in place and never adds a second one. Every other page
is listed under "Other reports" with its line from OTHER (a page with no line yet shows its heading
alone, so the index never drops a file).

The index is a pure function of reports/ and the model sets on disk (served.json, the candidates'
manifests), with no clock: rebuilding it unchanged gives the same bytes (tests/test_reports_index.py).
A set goes by its name, its S2 · S3 · S4 in their fixed words (shared/lineup.py), its stored name in small
print; the set served.json names wears a LIVE badge, a status beside the name. Nothing is "today's"
(Chase, 2026-10-04): the served set is "the live forecast" wherever its status matters.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORECAST = HERE.parents[1]
REPO = FORECAST.parents[1]
for _p in (str(REPO), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import candidates as C  # noqa: E402
from shared import lineup as LU  # noqa: E402

REPORTS = REPO / "reports"
OUT = REPORTS / "index.html"
URL = "/reports/"

STAGES = "2026-10_forecast_stages.html"
WORKS = "2026-09_forecast_how_it_works.html"
STAGE2 = "2026-09_forecast_stage2_explorer.html"
TITLES = {STAGES: "The forecast, stage by stage", WORKS: "How the forecast works",
          STAGE2: "From an overflow to beach risk (S3–S4)"}
EXPLORER_TITLE = "Inside the overflow model (S2)"
SET_COLS = ("s2", "s3", "s4")       # a set's name: its overflow model, beach split and lingering table
# The LIVE badge: the set served.json names wears it beside its name, wherever a page shows that set (a status, not
# part of the name). One look on every page: a small green pill, white capitals. LIVE_CSS for a page's stylesheet;
# LIVE_STYLE inline where a page's stylesheet is not ours to change (the S3–S4 explorer's, which replay_live reads).
LIVE_TIP = "the set the forecast page runs now"
LIVE_STYLE = ("display:inline-block;font-family:Roboto,'Segoe UI',Arial,sans-serif;font-size:10.5px;font-weight:700;"
              "letter-spacing:.08em;line-height:1.5;color:#fff;background:#237059;border-radius:999px;padding:0 7px;"
              "vertical-align:2px;text-transform:none")
LIVE_CSS = ".live-badge{" + LIVE_STYLE + "}"
LIVE_BADGE = f'<span class="live-badge" title="{LIVE_TIP}">LIVE</span>'
LIVE_BADGE_INLINE = f'<span class="live-badge" title="{LIVE_TIP}" style="{LIVE_STYLE}">LIVE</span>'

# Archived pages: {file: (date archived, replacement file[#section], why it is the replacement)}. The stamper
# and the index both read this table; nothing regenerates these pages.
ARCHIVED = {
    "2026-09_forecast_how_it_is_graded.html": (
        "2026-10-03", STAGES, "each stage scored on its own and in the chain, on days its fit never saw, under the frozen scoring rules"),
    "2026-09_live_replay.html": (
        "2026-10-03", f"{STAGES}#s5", "its S5 scores the live corrections on the 2016-17 archive feed and on realistic feeds"),
    "2026-09_live_replay_synthetic.html": (
        "2026-10-03", f"{STAGES}#s5", "its S5 scores the live corrections on a perfect feed and on realistic, degraded ones"),
    "2026-09_weather_models.html": (
        "2026-10-03", f"{STAGES}#s1", "its S1 grades the weather model against the two gauges, by lead"),
}

# Pages outside the forecast's reading list: one line each on what they are.
OTHER = {
    "2026-08_eastside_site_analysis.html":
        "The city's lab results on the east shore, Jul 2020 – Aug 2026: which sites fail the state standard most often, "
        "in which season, and the unsampled gap at India Basin.",
    "2026-09_outfall_station_mapping.html":
        "Outfall by outfall, from SFPUC's 2016-17 feed, the permits and the map: the evidence behind the registry "
        "that the forecast's beach split and the alerts read (Sep 2026).",
}

BANNER_START, BANNER_END = "<!-- archived-banner -->", "<!-- /archived-banner -->"
BANNER_TEXT = ("Kept for the record and not updated: it describes the forecast as it was graded before the "
               "five-stage rebuild.")


def esc(s) -> str:
    """Text for HTML, in text or in a double-quoted attribute (apostrophes stay as typed)."""
    return html.escape(str(s), quote=False).replace('"', "&quot;")


def day_words(day: str) -> str:
    """'2026-10-03' → '3 Oct 2026'."""
    d = dt.date.fromisoformat(day)
    return f"{d.day} {d:%b %Y}"


def href(file: str) -> str:
    return URL + file


def heading(file: str) -> str:
    """A page's own name: its first <h1>, else its <title>, as plain text."""
    text = (REPORTS / file).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", text, re.S) or re.search(r"<title>(.*?)</title>", text, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split()) if m else file


# ── model sets, by their lineup (A8) ────────────────────────────────────────

def explorer_file(name: str) -> str:
    return f"2026-09_forecast_{name}_model_explorer.html"


def model_sets() -> list[dict]:
    """The live forecast (the set served.json names), then every candidate set on disk by stored name: {name,
    served, parts, words, differs, file}. ``parts`` are its S2–S4 components (the overflow model, the beach
    split, the lingering table, read from its two stored halves as the Model check reads them); ``words`` its
    name, those parts' fixed words joined by " · "; ``differs`` marks the parts the live forecast does not share."""
    sv = C.served_info()
    base = LU.geo_v1_parts(sv["stage1"], sv["stage2"])
    rows = [{"name": sv["name"], "served": True, "parts": base}]
    for man in sorted(C.list_candidates(), key=lambda m: m["name"]):
        if man["name"] == sv["name"]:          # a promoted set's old candidate directory: it is the live forecast
            continue
        stage1 = (man.get("stage1") or {}).get("name", man["name"])
        variant = (man.get("stage2") or {}).get("variant", "v1")
        rows.append({"name": man["name"], "served": False, "parts": LU.geo_v1_parts(stage1, variant)})
    for r in rows:
        r["words"] = " · ".join(LU.words(c, r["parts"][c]) for c in SET_COLS)
        r["differs"] = {c: r["parts"][c] != base[c] for c in SET_COLS}
        r["file"] = explorer_file(r["name"])
    return rows


def model_set(name: str) -> dict:
    return next(r for r in model_sets() if r["name"] == name)


def set_title(row: dict) -> str:
    """How a page names a set: its S2 · S3 · S4 words, the same for the served set as for any other (its status is
    the LIVE badge, beside the name)."""
    return row["words"]


def explorer_title(row: dict) -> str:
    """A set's S2 page's title: "Inside the overflow model (S2): 38-weight · Outfall split · Linger table 2"."""
    return f"{EXPLORER_TITLE}: {set_title(row)}"


def current_pages() -> list[str]:
    """The pages this index recommends, in reading order: the stages report, how it works, the S2 explorer of
    every model set (the live forecast's first) and the S3–S4 explorer. None of them carries the archived banner."""
    return [STAGES, WORKS] + [r["file"] for r in model_sets()] + [STAGE2]


# ── the stamper ─────────────────────────────────────────────────────────────

def banner(file: str) -> str:
    """The archived banner for one page in ARCHIVED, styled inline (each page has its own stylesheet)."""
    day, repl, why = ARCHIVED[file]
    page = repl.split("#")[0]
    link = (f'<a href="{esc(href(repl))}" style="color:#0072BC;font-weight:700">{esc(TITLES.get(page, page))}</a>')
    return (f'{BANNER_START}<div id="archived-banner" role="note" style="display:block;background:#fbf3dc;'
            f'border-bottom:2px solid #e2c46b;color:#26272a;font-family:Roboto,\'Segoe UI\',Arial,sans-serif;font-size:15px;'
            f'line-height:1.5;text-align:left"><div style="max-width:1180px;margin:0 auto;padding:12px 22px;overflow-wrap:anywhere">'
            f'<b>Archived {esc(day_words(day))}.</b> {esc(BANNER_TEXT)} Read instead: {link} ({esc(why)}). '
            f'<a href="{URL}" style="color:#0072BC">Every report</a> is listed in one place.</div></div>{BANNER_END}')


def stamped(text: str, file: str) -> str:
    """A page's HTML with ARCHIVED's banner for ``file`` right after <body>. Idempotent: a page that has one gets
    it rewritten in place, between the markers, so stamping twice is stamping once."""
    b = banner(file)
    if BANNER_START in text:
        return re.sub(re.escape(BANNER_START) + r".*?" + re.escape(BANNER_END), lambda _: b, text, count=1, flags=re.S)
    m = re.search(r"<body\b[^>]*>", text)
    if m is None:
        raise ValueError(f"{file}: no <body> to put the archived banner after")
    return text[:m.end()] + b + text[m.end():]


def stamp(path: Path) -> bool:
    """Stamp one archived page in place (``stamped``). True when the file changed."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    new = stamped(text, path.name)
    if new != text:
        path.write_text(new, encoding="utf-8")
    return new != text


def stamp_all() -> list[str]:
    """Stamp every page in ARCHIVED; the names of the pages that changed."""
    return [f for f in ARCHIVED if stamp(REPORTS / f)]


# ── the index ───────────────────────────────────────────────────────────────

CSS = """
.wrap{max-width:1180px;margin:0 auto;padding:24px}
.rh{display:flex;gap:18px;align-items:flex-start;margin-bottom:8px}.rh .mark{width:56px;height:56px;border-radius:14px;flex:0 0 auto}
h1{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:40px;letter-spacing:.04em;line-height:1.1;margin:0 0 6px;color:#26272a}
h2{font-family:'Bebas Neue',sans-serif;font-weight:400;font-size:28px;letter-spacing:.05em;margin:28px 0 6px;color:#26272a}
.sub{font-size:16px;color:#54576F;margin:0 0 8px;max-width:900px;line-height:1.5}
.lead{font-size:15px;color:#54576F;max-width:900px;margin:4px 0 10px;line-height:1.5}
.meta{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:#8a949b}.meta a{color:#0072BC}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin:10px 0 6px;font-size:14px;font-weight:600}nav a{color:#0072BC;text-decoration:none}nav a:hover{text-decoration:underline}
.list{display:grid;grid-template-columns:minmax(0,1fr);gap:10px;max-width:900px}
.item{display:block;background:#fff;border:1px solid #d9e4e8;border-radius:18px;padding:14px 16px;color:#26272a;text-decoration:none;min-width:0;overflow-wrap:anywhere}
a.item:hover{border-color:#0072BC}a.item.first{border:2px solid #0072BC}
.item b{display:block;font-size:16px;color:#0072BC;margin-bottom:2px}
.item>span{display:block;font-size:14px;color:#54576F;line-height:1.45}.item>span a{color:#0072BC;font-weight:600}
.item.arch{background:#fbfcfd}.item.arch>a{text-decoration:none}.item .when{font-weight:700;color:#26272a}
details.sets{max-width:900px;background:#fff;border:1px solid #d9e4e8;border-radius:18px;padding:12px 16px}
details.sets summary{cursor:pointer;font-weight:700;color:#0072BC;font-size:15px}
.rows{display:grid;grid-template-columns:minmax(0,1fr);gap:0;margin-top:8px}
a.row{display:block;padding:9px 2px;border-top:1px solid #eef2f5;color:#26272a;text-decoration:none;font-size:14px;line-height:1.45;overflow-wrap:anywhere}
a.row:hover .w{color:#0072BC;text-decoration:underline}
.d{background:#fbe9d9;box-shadow:inset 0 -2px #d4763a;border-radius:3px;padding:0 2px}
small{display:block;font-size:12px;color:#8a949b}code{font-family:ui-monospace,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}
.fine{font-size:12.5px;color:#54576F;margin:6px 0 0;max-width:900px;line-height:1.5}
footer{margin-top:28px;border-top:1px solid #d9e4e8;padding-top:10px}
@media(max-width:640px){.rh{flex-direction:column;gap:10px}.wrap{padding:16px 14px}h1{font-size:34px}.item{padding:12px 13px}}
"""


def item(file: str, title: str, line: str, cls: str = "item", live: bool = False) -> str:
    """One page as a card: the whole card is the link. ``live``: the page opens the live forecast, whose name
    wears the LIVE badge."""
    return (f'<a class="{cls}" data-file="{esc(file)}" href="{esc(href(file))}"><b>{esc(title)}{" " + LIVE_BADGE if live else ""}</b>'
            f'<span>{esc(line)}</span></a>')


def set_row(r: dict, retired: dict) -> str:
    """A model set in the fold: its name (the parts that differ from the live forecast tinted), its stored name in
    small print, and until when it served if it once did."""
    words = " · ".join(f'<span class="d">{esc(LU.words(c, r["parts"][c]))}</span>' if r["differs"][c]
                       else esc(LU.words(c, r["parts"][c])) for c in SET_COLS)
    note = f' · served until {esc(day_words(retired["until"]))}' if r["name"] == retired.get("name") else ""
    return (f'<a class="row" data-file="{esc(r["file"])}" href="{esc(href(r["file"]))}"><span class="w">{words}</span>'
            f'<small>stored as <code>{esc(r["name"])}</code>{note}</small></a>')


def render() -> str:
    """The whole index from reports/ and the model sets on disk."""
    files = sorted(p.name for p in REPORTS.glob("*.html") if p.name != OUT.name)
    sets = model_sets()
    served = sets[0]
    for f in (STAGES, WORKS, STAGE2, served["file"]):
        if f not in files:
            raise FileNotFoundError(f"reports/{f} is missing: run its exporter first")
    others_sets = [r for r in sets[1:] if r["file"] in files]
    sv = C.served_info()
    retired = {"name": sv.get("replaced"), "until": (sv.get("promoted_at") or "")[:10]} if sv.get("replaced") and sv.get("promoted_at") else {}

    start = item(STAGES, TITLES[STAGES],
                 "How good each stage is, from S1 rain to S5 live corrections and the percentage people see: each one "
                 "scored on its own and in the chain, on days its fit never saw, under the frozen scoring rules. "
                 "The live forecast beside every challenger.", "item first")
    works = item(WORKS, TITLES[WORKS],
                 "What each stage does, in pictures: the rain that comes in (S1), will the sewers overflow (S2), which "
                 "beaches (S3), for how long (S4), the live corrections (S5) and the percentage that goes out.")
    deeper = (item(served["file"], explorer_title(served),
                   "The live forecast's overflow model opened up: the rain it reads, its weights, a what-if editor that "
                   "runs the real model in your browser, and a self-check against the stored scores.", live=True)
              + item(STAGE2, TITLES[STAGE2],
                     "Which beaches an overflow reaches (S3) and how long it lingers there (S4): the split and the "
                     "lingering table, any day of the record walked through, every model set side by side."))
    fold = (f'<details class="sets"><summary>{EXPLORER_TITLE}, for each of the other {len(others_sets)} model sets</summary>'
            '<p class="fine">Each set goes by its parts: the overflow model (S2) · the beach split (S3) · the lingering '
            'table (S4). Tinted: the parts that differ from the live forecast. None of these runs live; the Model check '
            'grades them all on the same days. The live forecast is under Go deeper.</p>'
            f'<div class="rows">{"".join(set_row(r, retired) for r in others_sets)}</div></details>')

    arch = []
    for f, (day, repl, why) in ARCHIVED.items():
        if f not in files:
            continue
        page = repl.split("#")[0]
        arch.append(f'<div class="item arch" data-file="{esc(f)}"><a href="{esc(href(f))}"><b>{esc(heading(f))}</b></a>'
                    f'<span><span class="when">Archived {esc(day_words(day))}</span>, kept for the record and not updated. '
                    f'Read instead: <a href="{esc(href(repl))}">{esc(TITLES.get(page, page))}</a> ({esc(why)}).</span></div>')

    listed = set(current_pages()) | set(ARCHIVED)
    other = [item(f, heading(f), OTHER.get(f, "")) for f in files if f not in listed]

    body = f'''<header class="rh"><img src="/static/brand/bwtf_144x144.png" alt="" class="mark"><div><h1>Forecast reports: what to read</h1>
<p class="sub">The forecast runs in five stages: S1 how much rain, S2 will the sewers overflow, S3 which beaches, S4 for how long, S5 live corrections, then the percentage people see. Read the first report first; the others go deeper. Archived pages stay at their addresses, marked, and are not updated.</p>
<div class="meta"><span><a href="/forecast">← the forecast</a></span></div></div></header>
<nav><a href="#start">Start here</a><a href="#works">How it works</a><a href="#deeper">Go deeper</a><a href="#sets">Every model set</a><a href="#archived">Archived</a><a href="#other">Other reports</a></nav>
<section id="start"><h2>Start here</h2><div class="list">{start}</div></section>
<section id="works"><h2>How it works</h2><div class="list">{works}</div></section>
<section id="deeper"><h2>Go deeper</h2><div class="list">{deeper}</div></section>
<section id="sets"><h2>Every model set</h2>{fold}</section>
<section id="archived"><h2>Archived</h2><p class="lead">Replaced by the stage-by-stage report when the forecast was rebuilt in five stages. Each page carries a banner saying so.</p><div class="list">{"".join(arch)}</div></section>
<section id="other"><h2>Other reports</h2><div class="list">{"".join(other)}</div></section>
<footer><p class="fine">Written by <code>features/forecast/src/models/export_reports_index.py</code>, which also marks the archived pages.</p></footer>'''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forecast reports: what to read</title><link rel="icon" href="/static/brand/favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/brand.css"><style>{CSS}{LIVE_CSS}</style></head><body>
<div class="wrap">
{body}
</div></body></html>
'''


def write(path: Path = OUT) -> Path:
    path.write_text(render(), encoding="utf-8")
    return path


def main(argv=None) -> None:
    """``export_reports_index.py``: stamp the archived pages, then write the index."""
    argv = sys.argv[1:] if argv is None else argv
    changed = stamp_all()
    out = write(Path(argv[0]) if argv else OUT)
    print(f"stamped {len(ARCHIVED)} archived pages ({len(changed)} changed); wrote {out}")


if __name__ == "__main__":
    main()
