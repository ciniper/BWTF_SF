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
  2b. The system from above  a Leaflet map of the shoreline storage ring (schematic, through the outfalls),
                           the three treatment plants and their deep-water outfalls, with the six biggest
                           storms in the CIWQS record replayed outfall by outfall, hour by hour.
  4. A century and a half  a scrolling timeline, 1850s → today, from SFPUC's Sewer System Master Plan
                           (2010) and this site's own records.
  5. From the archive      a then-and-now slider on placeholder frames: no photos until rights are cleared.

Everything here is read from the repo's own registries and record files; nothing calls a live API.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict

from datetime import datetime
from math import cos, radians

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


# ── 2b. The system from above ─────────────────────────────────────────────────────────────────────
# Plant sites and deep-water outfalls are drawn from SFPUC's Sewer System Master Plan (2010, Figures 2-1 and
# 2-5) at approximate positions; the storage ring is schematic: it runs through the registry's outfalls in
# the shoreline order the master plan's map shows, each run toward the plant it drains to.
PLANTS = {
    "oceanside": {"name": "Oceanside plant", "short": "Oceanside plant", "lat": 37.7297, "lon": -122.5047, "note": "Westside: secondary treatment; opened 1993"},
    "southeast": {"name": "Southeast plant", "short": "Southeast plant", "lat": 37.7426, "lon": -122.3884, "note": "Bayside: secondary treatment; opened 1952"},
    "northpoint": {"name": "North Point wet-weather facility", "short": "North Point · storms only", "lat": 37.8063, "lon": -122.4057, "note": "Bayside, storms only: primary treatment; built 1951"},
}
DEEP_OUTFALLS = [   # treated water: approximate routes
    {"name": "Southwest Ocean Outfall, about four miles offshore (1986)", "pts": [(37.7297, -122.5047), (37.7300, -122.5110), (37.7150, -122.5790)]},
    {"name": "Southeast Bay Outfall, off Pier 80", "pts": [(37.7426, -122.3884), (37.7495, -122.3800), (37.7495, -122.3768)]},
    {"name": "Quint Street Outfall, Islais Creek", "pts": [(37.7426, -122.3884), (37.7462, -122.3908)]},
    {"name": "North Point outfalls, under Piers 33 and 35", "pts": [(37.8063, -122.4057), (37.8088, -122.4048), (37.8112, -122.4036)]},
]
# runs of the storage ring, by outfall id, each ending where it drains; "main" = a force main or tunnel to a plant
RING = [
    {"kind": "storage", "to": "oceanside", "ids": ["CSD-007", "CSD-006", "CSD-005", "CSD-004", "CSD-003", "CSD-002"], "end": "oceanside"},
    {"kind": "storage", "to": "oceanside", "ids": ["CSD-001"], "end": "oceanside"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-009", "CSD-010", "CSD-011", "CSD-013", "CSD-015", "CSD-017", "CSD-018", "CSD-022"]},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-027", "CSD-026", "CSD-025", "CSD-024", "CSD-023", "CSD-022"]},
    {"kind": "main", "to": "southeast", "ids": ["CSD-022"], "via": [(37.7650, -122.3905), (37.7540, -122.3895)], "end": "southeast"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-029", "CSD-030", "CSD-030A", "CSD-031"], "end": "southeast"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-033", "CSD-032", "CSD-031A", "CSD-035"], "end": "southeast"},
    {"kind": "main", "to": "southeast", "ids": ["CSD-037"], "end": "southeast"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-042", "CSD-040", "CSD-041"]},
    {"kind": "main", "to": "southeast", "ids": ["CSD-041"], "via": [(37.7300, -122.3900)], "end": "southeast"},
    {"kind": "main", "to": "southeast", "ids": ["CSD-043"], "via": [(37.7200, -122.3960), (37.7330, -122.3930)], "end": "southeast"},
]


def _minutes(t: str):
    """A CIWQS start time as minutes after midnight: "10:13 AM", "13:26", or Excel's day fraction "0.56"."""
    t = (t or "").strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
        try:
            d = datetime.strptime(t.upper(), fmt); return d.hour * 60 + d.minute
        except ValueError:
            pass
    try:
        f = float(t)
        return round(f * 1440) if 0 <= f < 1 else None
    except ValueError:
        return None


def _system(n_storms: int = 6) -> dict:
    ev = list(csv.DictReader(open(CSD_CSV, newline="")))
    days_by = defaultdict(set)
    for e in ev:
        days_by[e["outfall_id"]].add(e["event_date"])
    outfalls = [{"id": o.id, "name": o.name, "plant": "oceanside" if o.facility == "Oceanside" else "southeast", "lat": o.lat, "lon": o.lon,
                 "water": o.receiving_water, "days": len(days_by.get(o.id, ()))}
                for o in sorted(OUTFALLS.values(), key=lambda o: o.id) if o.id != "CSD-119"]   # CSD-119 shares CSD-009's structure
    pos = {o["id"]: (o["lat"], o["lon"]) for o in outfalls}
    ring = []
    for run in RING:
        pts = [pos[i] for i in run["ids"]] + list(run.get("via", []))
        if run.get("end"):
            pts.append((PLANTS[run["end"]]["lat"], PLANTS[run["end"]]["lon"]))
        if len(pts) > 1:
            ring.append({"kind": run["kind"], "pts": pts})
    by_day = defaultdict(list)
    for e in ev:
        m = _minutes(e["start_time"])
        try:
            dur, mg = float(e["duration_min"] or 0), float(e["volume_MG"] or 0)
        except ValueError:
            continue
        if m is None or dur <= 0 or e["outfall_id"] not in pos:
            continue
        by_day[e["event_date"]].append({"id": e["outfall_id"], "start": m, "dur": round(dur), "mg": round(mg, 2)})
    ranked = sorted(by_day.items(), key=lambda kv: (-len({x["id"] for x in kv[1]}), -sum(x["mg"] for x in kv[1])))[:n_storms]
    storms = []
    for day, events in ranked:
        d = datetime.strptime(day, "%Y-%m-%d")
        storms.append({"date": day, "label": f"{d:%b} {d.day}, {d.year}", "outfalls": len({x["id"] for x in events}),
                       "mg": round(sum(x["mg"] for x in events)), "events": sorted(events, key=lambda x: x["start"])})
    return {"outfalls": outfalls, "ring": ring, "plants": PLANTS, "deep": DEEP_OUTFALLS, "storms": storms}


# ── 4. A century and a half ───────────────────────────────────────────────────────────────────────
# Each stop's source: SFPUC's Sewer System Master Plan, Summary Report (Final Draft, March 2010), or this
# site's own record files. photo = a placeholder frame until a photo's rights are cleared.
_SSMP, _SITE = "SFPUC Sewer System Master Plan (2010)", "this site's records"
TIMELINE = [
    {"when": "1850s", "title": "The first sewers", "src": _SSMP,
     "text": "Gold Rush San Francisco starts laying sewers that carry sewage and rain in the same pipe, downhill to the shoreline. A tenth of the city's smaller sewers (36 inches or less) still in use went in before 1900."},
    {"when": "1899", "title": "The first master plan", "src": _SSMP, "photo": "A brick sewer under construction",
     "text": "Over 300 miles of combined sewers are already built. The city's first coordinated sewer plan leads to 700 miles of them, four pump stations, and an end to sewage spilling onto land."},
    {"when": "1901–1940", "title": "Half of today's pipes", "src": _SSMP,
     "text": "Nearly half of the smaller sewers in use today (49%) are laid in these four decades."},
    {"when": "1935", "title": "A plan for treatment", "src": _SSMP,
     "text": "The second master plan brings the city's first treatment plants and deep-water outfalls, 900 miles of sewers in all, and an end to sewage spills in dry weather."},
    {"when": "1938–1952", "title": "Three plants", "src": _SSMP,
     "text": "The Richmond-Sunset plant opens in 1938, North Point in 1951 and Southeast in 1952."},
    {"when": "1972", "title": "The Clean Water Act", "src": _SSMP,
     "text": "Congress passes the Clean Water Act. The city's next master plan, two years later, is its answer."},
    {"when": "1974", "title": "The plan that built the moat", "src": _SSMP, "photo": "Building a transport/storage structure",
     "text": "Built over about 25 years: secondary treatment for all dry-weather flow, the Southwest Ocean Outfall, and 17 miles of transport/storage structures around the shore holding 197 million gallons. Discharges fall to an average of 4.4% of the year's flow."},
    {"when": "Early 1980s", "title": "Southeast goes secondary", "src": _SSMP,
     "text": "The Southeast plant is upgraded to secondary treatment in 1982 and takes all of the Bayside's dry-weather flow; North Point becomes a wet-weather facility, running only in storms."},
    {"when": "1986", "title": "Four miles out", "src": _SSMP, "photo": "Laying the ocean outfall",
     "text": "The Southwest Ocean Outfall opens: a 12-foot pipe buried under the seabed, running about four miles offshore to 85 diffusers about 80 feet down."},
    {"when": "1993", "title": "The Oceanside plant", "src": _SSMP,
     "text": "The Westside's new plant opens on the Great Highway between Lake Merced and the Zoo, replacing Richmond-Sunset."},
    {"when": "1999", "title": "Beach postings on the record", "src": "State Water Board BeachWatch",
     "text": "The State's record of San Francisco's beach advisories begins. The Beach Postings page reads it."},
    {"when": "2002–2003", "title": "Counting Enterococcus", "src": _SITE,
     "text": "The city lab adds Enterococcus and E. coli to total coliform: at the bay stations in July 2002, the ocean beaches in October 2003."},
    {"when": "2010", "title": "A plan for the next century", "src": _SSMP,
     "text": "976 miles of sewers, 27 pump stations, 36 discharge sites. If seas rise at the higher estimates, it warns, the daily high tide will top the lowest discharge weirs by 2050."},
    {"when": "2016", "title": "Every discharge, outfall by outfall", "src": _SITE,
     "text": "The per-outfall discharge reports this site keeps begin in October 2016. The Discharge Ledger and the storms on the map above come from them."},
    {"when": "2023", "title": "Volunteers' results", "src": _SITE, "photo": "The Blue Water Task Force lab",
     "text": "Surfrider SF's Blue Water Task Force results join the city's on this site's record from September 2023."},
    {"when": "2026", "title": "Watching the map", "src": _SITE,
     "text": "From August 2026 this site checks SFPUC's beach map every minute and keeps the Online Postings Timeline."},
]


def learn_context() -> dict:
    ctx = {"plants": _plants(), "indicators": _indicators(), "timeline": TIMELINE, "caution": ENTERO_CAUTION,
           "entero_limit": STANDARDS["ENTERO"]["single_sample_max"]}
    for key, fn in (("postings", _postings), ("discharges", _discharges), ("system", _system)):
        try:
            ctx[key] = fn()
        except Exception as e:  # noqa: BLE001 — a missing record file leaves its chart out, never the page
            ctx[key] = None; ctx[key + "_error"] = str(e)
    return ctx


def handle_page(query, body):
    return 200, "text/html; charset=utf-8", render_template("learn/page.html", **learn_context()).encode()


GET_ROUTES = {"/learn": handle_page}
