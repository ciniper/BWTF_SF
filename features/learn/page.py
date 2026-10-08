"""Learn — a PROTOTYPE on branch design/learn-prototype (2026-10-07). Not linked from the site.

Chase's idea: a "Learn" tab with how San Francisco's sewers meet the shore, the bacteria the labs
count, historical photos, and moving graphics. One page, four sections, no libraries:

  1. Where the rain goes   an animated cross-section of a combined sewer: a rain slider and a
                           "Play a storm" button, from dry weather to an overflow at the shore.
  2. Meet the indicators   a card per indicator with its state limit (shared/standards.py), and a
                           97-well tray that lights like the lab's and reads its count back as a
                           Most Probable Number.
  3. The record, in motion advisories San Francisco filed with the State per year (BeachWatch) and
                           discharge days per wet season (CIWQS), from the files the site already ships.
  4. From the archive      a then-and-now slider on placeholder frames: no photos until rights are cleared.

Everything here is read from the repo's own registries and record files; nothing calls a live API.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict

from flask import render_template

from features.discharges.page import _CSV as CSD_CSV
from features.postings.page import _CSV as POSTINGS_CSV
from shared.outfalls import OUTFALLS
from shared.standards import ENTERO_CAUTION, STANDARDS


def _plants() -> dict:
    """Outfalls per treatment plant, from the registry (Oceanside 7, Bayside 27 today)."""
    n = Counter(o.facility for o in OUTFALLS.values())
    return {"oceanside": n.get("Oceanside", 0), "bayside": n.get("Bayside", 0), "total": len(OUTFALLS)}


def _indicators() -> list[dict]:
    """The four indicators, their single-sample limits from the shared standards, and who counts them here."""
    s = STANDARDS
    return [
        {"code": "ENTERO", "name": "Enterococcus", "shape": "cocci", "color": "#2f8f83",
         "what": "Round bacteria that sit in pairs and short chains. They live in the guts of people and animals.",
         "why": "The indicator for ocean and bay beaches: it lasts longer in salt water than the coliforms do.",
         "limit": f"{s['ENTERO']['single_sample_max']:,}", "limit_note": f"Surfrider marks {ENTERO_CAUTION} and up as caution",
         "who": "The city lab, since July 2002 at the bay stations and October 2003 on the ocean beaches, and Surfrider's volunteers."},
        {"code": "COLI_E", "name": "E. coli", "shape": "flagella", "color": "#7a63a8",
         "what": "A rod-shaped gut bacterium, the best-known coliform, that swims with whip-like flagella.",
         "why": "A sign of fresh fecal contamination; most strains are harmless, which is what makes it a good stand-in.",
         "limit": f"{s['COLI_E']['single_sample_max']:,}", "limit_note": "",
         "who": "The city lab, from July 2002 at the bay stations and October 2003 on the ocean beaches until March 2021, when fecal coliform took its place."},
        {"code": "COLI_FECAL", "name": "Fecal coliform", "shape": "rods", "color": "#b07a3a",
         "what": "The coliforms that grow at body temperature, the gut-dwelling group that includes E. coli.",
         "why": "One of California's three beach indicators; when it is a large share of total coliform, the total coliform limit tightens.",
         "limit": f"{s['COLI_FECAL']['single_sample_max']:,}", "limit_note": "",
         "who": "The city lab, since March 2021, in place of E. coli."},
        {"code": "COLI_TOTAL", "name": "Total coliform", "shape": "mixed", "color": "#5b7f99",
         "what": "The widest net: rod-shaped bacteria from guts, and also from soil and plants.",
         "why": "The city's longest record here: the only indicator it ran before 2002.",
         "limit": f"{s['COLI_TOTAL']['single_sample_max']:,}",
         "limit_note": f"{s['COLI_TOTAL']['single_sample_max_ratio']:,} when fecal coliform is more than a tenth of it",
         "who": "The city lab, since 2000 in this site's record."},
    ]


def _postings() -> dict:
    """Advisories filed with the State per year, split by cause class (cso / rain / other)."""
    rows = list(csv.DictReader(open(POSTINGS_CSV, newline="")))
    by = defaultdict(Counter)
    for r in rows:
        by[int(r["posted_on"][:4])][r["cause_class"]] += 1
    first, last = min(by), max(by)
    years = [{"year": y, "cso": by[y]["cso"], "rain": by[y]["rain"], "other": by[y]["other"]} for y in range(first, last + 1)]
    return {"years": years, "through": max(r["posted_on"] for r in rows)}


def _discharges() -> dict:
    """Days with a combined sewer discharge per wet season (October to September), and outfall-days."""
    ev = list(csv.DictReader(open(CSD_CSV, newline="")))
    season = lambda d: int(d[:4]) if int(d[5:7]) >= 10 else int(d[:4]) - 1  # noqa: E731
    days, outfall_days = defaultdict(set), defaultdict(set)
    for e in ev:
        s = season(e["event_date"])
        days[s].add(e["event_date"]); outfall_days[s].add((e["event_date"], e["outfall_id"]))
    seasons = [{"label": f"{s}–{str(s + 1)[2:]}", "days": len(days[s]), "outfall_days": len(outfall_days[s])} for s in sorted(days)]
    return {"seasons": seasons, "first": min(e["event_date"] for e in ev), "through": max(e["event_date"] for e in ev)}


def learn_context() -> dict:
    ctx = {"plants": _plants(), "indicators": _indicators(), "caution": ENTERO_CAUTION,
           "entero_limit": STANDARDS["ENTERO"]["single_sample_max"]}
    for key, fn in (("postings", _postings), ("discharges", _discharges)):
        try:
            ctx[key] = fn()
        except Exception as e:  # noqa: BLE001 — a missing record file leaves its chart out, never the page
            ctx[key] = None; ctx[key + "_error"] = str(e)
    return ctx


def handle_page(query, body):
    return 200, "text/html; charset=utf-8", render_template("learn/page.html", **learn_context()).encode()


GET_ROUTES = {"/learn": handle_page}
