"""Learn — how San Francisco's sewers meet the shore, the bacteria the labs count, and the record.

Chase's idea (2026-10-07), built on design/learn-prototype and live since 2026-10-08: its own top tab since
2026-10-18 (with the Collect and Lab SOP pages, blank for now) and still linked from Today's page; kept out of
search until the facts in docs/learn_facts_to_check.md are checked. Leaflet loads only for the map; nothing else uses a library:

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
  2c. How the lab counts   a scroll-pinned side-scroller: one bottle through Enterolert in a Quanti-Tray.
  4. A century and a half  a scrolling timeline, 1850s → today, from SFPUC's Sewer System Master Plan
                           (2010) and this site's own records, with the plan's own photos. Parked in Admin's
                           UI overflow tab since 2026-11-04 (app/templates/learn/_timeline.html): TIMELINE
                           and PHOTO_CREDIT stay here, its content, for when it comes back.

Everything here is read from the repo's own registries and record files; nothing calls a live API.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict

from datetime import datetime
from math import exp
from random import Random

from flask import render_template

from features.discharges.page import _CSV as CSD_CSV
from features.learn.collect import GET_ROUTES as COLLECT_ROUTES
from features.postings.page import _CSV as POSTINGS_CSV
from shared.outfalls import OUTFALLS
from shared.standards import ENTERO_CAUTION, STANDARDS
from shared.text import title_case


def _plants() -> dict:
    """Outfalls per treatment plant, from the registry (Oceanside 7, Bayside 27 today)."""
    n = Counter(o.facility for o in OUTFALLS.values())
    return {"oceanside": n.get("Oceanside", 0), "bayside": n.get("Bayside", 0), "total": len(OUTFALLS)}


def _indicators() -> list[dict]:
    """The four indicators, their single-sample limits from the shared standards, and who counts them here."""
    s = STANDARDS
    return [
        {"code": "ENTERO", "name": "Enterococcus", "shape": "cocci", "color": "#2f8f83",
         "what": "Round bacteria in pairs and short chains. They live in the guts of people and animals.",
         "why": "The indicator for ocean and bay beaches. It stays alive longer in salt water than the coliforms.",
         "limit": f"{s['ENTERO']['single_sample_max']:,}", "limit_note": f"Surfrider uses {ENTERO_CAUTION} and more as caution",
         "who": "The city lab (from July 2002 at the bay stations and October 2003 at the ocean beaches), and the Surfrider volunteers."},
        {"code": "COLI_E", "name": "E. coli", "shape": "flagella", "color": "#7a63a8",
         "what": "A rod-shaped gut bacterium. It is the best-known coliform. It moves with flagella that look like whips.",
         "why": "A sign of fresh fecal contamination. Most types do not cause illness. Thus, it is a good indicator.",
         "limit": f"{s['COLI_E']['single_sample_max']:,}", "limit_note": "",
         "who": "The city lab, from July 2002 at the bay stations and October 2003 at the ocean beaches, until March 2021. Then fecal coliform replaced it."},
        {"code": "COLI_FECAL", "name": "Fecal Coliform", "shape": "rods", "color": "#b07a3a",
         "what": "The coliforms that grow at body temperature. This group lives in the gut and includes E. coli.",
         "why": "One of the three California beach indicators. When it is a large part of total coliform, the total coliform limit decreases.",
         "limit": f"{s['COLI_FECAL']['single_sample_max']:,}", "limit_note": "",
         "who": "The city lab, since March 2021, in place of E. coli."},
        {"code": "COLI_TOTAL", "name": "Total Coliform", "shape": "mixed", "color": "#5b7f99",
         "what": "The largest group: rod-shaped bacteria from guts, and also from soil and plants.",
         "why": "The longest city record on this site. It is the only indicator that the city measured before 2002.",
         "limit": f"{s['COLI_TOTAL']['single_sample_max']:,}",
         "limit_note": f"{s['COLI_TOTAL']['single_sample_max_ratio']:,} when fecal coliform is more than one tenth of total coliform",
         "who": "The city lab, from 2000 in the record of this site."},
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
    "oceanside": {"name": "Oceanside Plant", "short": "Oceanside Plant", "lat": 37.7297, "lon": -122.5047, "note": "Westside: secondary treatment; opened 1993",
                  "photo": "learn/oceanside_aerial.jpg"},
    "southeast": {"name": "Southeast Plant", "short": "Southeast Plant", "lat": 37.7426, "lon": -122.3884, "note": "Bayside: secondary treatment; opened 1952",
                  "photo": "learn/southeast_aerial.jpg"},
    "northpoint": {"name": "North Point Wet-Weather Facility", "short": "North Point · Storms Only", "lat": 37.8063, "lon": -122.4057, "note": "Bayside, storms only: primary treatment; built 1951",
                   "photo": "learn/plant_north_point_1951.jpg"},
}
DEEP_OUTFALLS = [   # treated water: approximate routes
    {"name": "Southwest Ocean Outfall, about four miles offshore (1986)", "pts": [(37.7297, -122.5047), (37.7300, -122.5110), (37.7150, -122.5790)]},
    {"name": "Southeast Bay Outfall, off Pier 80", "pts": [(37.7426, -122.3884), (37.7495, -122.3800), (37.7495, -122.3768)]},
    {"name": "Quint Street Outfall, Islais Creek", "pts": [(37.7426, -122.3884), (37.7462, -122.3908)]},
    {"name": "North Point outfalls, under Piers 33 and 35", "pts": [(37.8063, -122.4057), (37.8088, -122.4048), (37.8112, -122.4036)]},
]
# runs of the storage ring, by outfall id, each ending where it drains; "main" = a force main or tunnel to a plant
RING = [
    {"kind": "storage", "to": "oceanside", "ids": ["CSD-007", "CSD-006", "CSD-005", "CSD-004", "CSD-003", "CSD-002"], "end": "oceanside",
     "name": "Westside: the Richmond Tunnel and the Westside storage under the Great Highway, to the Westside Pump Station"},
    {"kind": "storage", "to": "oceanside", "ids": ["CSD-001"], "end": "oceanside", "name": "The Lake Merced Tunnel, to the Westside Pump Station"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-009", "CSD-010", "CSD-011", "CSD-013", "CSD-015", "CSD-017", "CSD-018", "CSD-022"],
     "name": "North Shore: storage along the Marina and the Embarcadero; the North Shore Pump Station and Force Main carry it on to the Channel storage"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-027", "CSD-026", "CSD-025", "CSD-024", "CSD-023", "CSD-022"], "name": "Channel: storage along Mission Creek"},
    {"kind": "main", "to": "southeast", "ids": ["CSD-022"], "via": [(37.7650, -122.3905), (37.7540, -122.3895)], "end": "southeast",
     "name": "The Channel Force Main, from the Channel Pump Station to the Southeast plant: a major artery with no backup, the master plan says"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-029", "CSD-030", "CSD-030A", "CSD-031"], "end": "southeast", "name": "Storage along the Central Basin"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-033", "CSD-032", "CSD-031A", "CSD-035"], "end": "southeast", "name": "Islais Creek storage"},
    {"kind": "main", "to": "southeast", "ids": ["CSD-037"], "end": "southeast", "name": "From Evans Avenue to the Southeast plant"},
    {"kind": "storage", "to": "southeast", "ids": ["CSD-042", "CSD-040", "CSD-041"], "name": "Storage along Yosemite Creek and South Basin"},
    {"kind": "main", "to": "southeast", "ids": ["CSD-041"], "via": [(37.7300, -122.3900)], "end": "southeast", "name": "From Yosemite Creek to the Southeast plant"},
    {"kind": "main", "to": "southeast", "ids": ["CSD-043"], "via": [(37.7200, -122.3960), (37.7330, -122.3930)], "end": "southeast", "name": "From Sunnydale to the Southeast plant"},
]
# The main collecting sewers, schematic: each drawn from a neighborhood down the valley it drains to the storage
# ring (the master plan: the system "utilizes natural watershed areas wherever possible to take advantage of
# gravity flow"). The real pipes run under streets; these show which way the water goes, not where the pipes are.
ARTERIES = [
    {"name": "The Richmond", "pts": [(37.7815, -122.4600), (37.7800, -122.4850), (37.7830, -122.5020)], "to": "CSD-004"},
    {"name": "Sea Cliff", "pts": [(37.7840, -122.4750), (37.7878, -122.4862)], "to": "CSD-007"},
    {"name": "The Sunset, north of Golden Gate Heights", "pts": [(37.7620, -122.4650), (37.7625, -122.4900), (37.7635, -122.5080)], "to": "CSD-003"},
    {"name": "West Portal and the Parkside", "pts": [(37.7450, -122.4620), (37.7410, -122.4850), (37.7385, -122.5060)], "to": "CSD-002"},
    {"name": "Pacific Heights and the Marina", "pts": [(37.7905, -122.4400), (37.7980, -122.4405), (37.8060, -122.4400)], "to": "CSD-010"},
    {"name": "Russian Hill and North Beach", "pts": [(37.7980, -122.4150), (37.8030, -122.4100), (37.8075, -122.4070)], "to": "CSD-013"},
    {"name": "Nob Hill and Downtown", "pts": [(37.7895, -122.4100), (37.7950, -122.4000), (37.7978, -122.3952)], "to": "CSD-017"},
    {"name": "Hayes Valley, Civic Center and Market Street", "pts": [(37.7740, -122.4300), (37.7810, -122.4100), (37.7880, -122.3980), (37.7925, -122.3910)], "to": "CSD-018"},
    {"name": "The Mission, down Mission Creek's old valley", "pts": [(37.7520, -122.4200), (37.7620, -122.4150), (37.7680, -122.4050), (37.7700, -122.3985)], "to": "CSD-026"},
    {"name": "Potrero Hill", "pts": [(37.7590, -122.4020), (37.7630, -122.3920), (37.7645, -122.3860)], "to": "CSD-029"},
    {"name": "Glen Park and Bernal, down the Islais Creek valley", "pts": [(37.7330, -122.4350), (37.7330, -122.4200), (37.7390, -122.4070), (37.7460, -122.3960), (37.7476, -122.3912)], "to": "CSD-033"},
    {"name": "Bayview and Hunters Point", "pts": [(37.7300, -122.3900), (37.7340, -122.3800), (37.7356, -122.3745)], "to": "CSD-037"},
    {"name": "The Excelsior and Portola, to Yosemite Creek", "pts": [(37.7200, -122.4300), (37.7180, -122.4100), (37.7230, -122.3950), (37.7238, -122.3860)], "to": "CSD-041"},
    {"name": "Visitacion Valley, to Sunnydale", "pts": [(37.7140, -122.4100), (37.7110, -122.3980), (37.7098, -122.3905)], "to": "CSD-043"},
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
            ring.append({"kind": run["kind"], "pts": pts, "name": run["name"]})
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
    arteries = [{"name": a["name"], "pts": a["pts"] + [pos[a["to"]]]} for a in ARTERIES]   # each ends on its outfall's storage
    return {"outfalls": outfalls, "ring": ring, "arteries": arteries, "plants": PLANTS, "deep": DEEP_OUTFALLS, "storms": storms}


# ── 4. How the lab counts: one sample, followed through IDEXX's Enterolert test in a Quanti-Tray ─────────
# The water holds QT_CONC Enterococcus per 100 mL; a 1:10 dilution puts about a tenth of them in the tray;
# each one lands in a well with odds by volume (49 big wells of 1.86 mL, 48 small of 0.186 mL); every well
# that catches one or more glows; the most probable number of the glowing wells, times ten, is the result.
QT_CONC, QT_VL, QT_VS = 300, 1.86, 0.186


def mpn_per_100ml(big: int, small: int) -> float | None:
    """The Most Probable Number per 100 mL for big + small glowing wells: the maximum-likelihood
    concentration, the estimate MPN tables are built on. None when every well glows (above the range)."""
    if big == 0 and small == 0:
        return 0.0
    if big == 49 and small == 48:
        return None
    total = 49 * QT_VL + 48 * QT_VS
    f = lambda lam: (big * QT_VL / (1 - exp(-lam * QT_VL)) if big else 0) + (small * QT_VS / (1 - exp(-lam * QT_VS)) if small else 0) - total
    lo, hi = 1e-6, 60.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return lo * 100


def _quanti_story() -> dict:
    """The side-scroller's one sample, the same on every visit: the first seed whose draw is typical
    (a few big wells catch two, a small well or two catch one) and reads back within 20 of the truth."""
    cells = QT_CONC // 10
    vols = [QT_VL] * 49 + [QT_VS] * 48
    for seed in range(1, 500):
        rng = Random(seed)
        wells = rng.choices(range(97), weights=vols, k=cells)
        hits = Counter(wells)
        doubles, small = sum(1 for w, n in hits.items() if n >= 2 and w < 49), sum(1 for w in hits if w >= 49)
        big_on, small_on = sum(1 for w in hits if w < 49), sum(1 for w in hits if w >= 49)
        tray = mpn_per_100ml(big_on, small_on)
        if 3 <= doubles <= 5 and small in (1, 2) and abs(tray * 10 - QT_CONC) <= 20:
            break
    else:
        raise RuntimeError("no typical draw in 500 seeds")
    return {"conc": QT_CONC, "cells": cells, "wells": wells, "big": big_on, "small": small_on, "doubles": doubles,
            "tray": round(tray, 1), "result": round(tray * 10), "vl": QT_VL, "vs": QT_VS}


def _quanti_steps(q: dict) -> list[dict]:
    lim = STANDARDS["ENTERO"]["single_sample_max"]
    return [
        {"title": "Fill a Bottle", "alt": "A sample bottle fills at the shore. The drawing shows each bacterium.",
         "text": f"A volunteer fills a sterile bottle in the surf and keeps it cold on the way to the lab. For example, this water has {q['conc']} Enterococcus per 100 mL. That is approximately three times the state limit of {lim}. But it is only {q['conc']} bacteria in the full bottle. The drawing shows each one."},
        {"title": "Dilute It One in Ten", "alt": "A pipette moves 10 mL of the sample into 90 mL of sterile water",
         "text": f"Put 10 mL of the sample into 90 mL of sterile water. For this test, you must dilute seawater. Thus, the lowest result is \"under 10\". The new bottle has approximately {q['cells']} Enterococcus."},
        {"title": "Add the Food", "alt": "A packet of reagent powder goes into the bottle and dissolves",
         "text": "A packet of Enterolert powder dissolves in the water. It is a nutrient that only enterococci can break down. When they break it down, it releases a compound. This compound glows blue under ultraviolet light."},
        {"title": "Pour It into the Tray", "alt": "The water goes from the bottle into a vertical tray of wells",
         "text": "Put all 100 mL into a Quanti-Tray. This is a plastic sheet with 97 wells: 49 large wells and 48 small wells."},
        {"title": "Seal It", "alt": "The tray goes through a sealer. The water is then divided among its wells.",
         "text": f"A sealer closes the tray and divides the water among the wells. Each bacterium goes into the well where its drop goes. Most wells get no bacteria. Many wells get one. In this example, {q['doubles']} got two or more."},
        {"title": "Keep It Warm Overnight", "alt": "The tray in a 41 degree incubator. A clock goes to 24 hours while one well fills with bacteria.",
         "text": "24 hours at 41 °C. In each well with one or more bacteria, the bacteria multiply: two, four, eight, and more. Then the well is full of bacteria and the glowing compound. In the morning, a well that started with one bacterium looks the same as a well that started with two."},
        {"title": "Turn On the UV Lamp", "alt": "Under ultraviolet light, the wells with bacteria glow blue",
         "text": f"Under long-wave ultraviolet light, each well with Enterococcus glows blue. A well gives only yes or no. In this example, {q['big']} large wells and {q['small']} small {'well glows' if q['small'] == 1 else 'wells glow'}."},
        {"title": "Look Up the Number", "alt": "The number of glowing wells gives a result per 100 mL",
         "text": f"Find the number of glowing wells in the IDEXX Most Probable Number table. {q['big']} large and {q['small']} small wells give {q['tray']:g} per 100 mL. Multiply by ten for the dilution: {q['result']:,}. The water had {q['conc']}. The method gives an estimate. It does not count each bacterium. The table includes wells that got two or more bacteria. Thus, the result is a little more than the number of glowing wells."},
    ]


# ── 5. A century and a half ───────────────────────────────────────────────────────────────────────
# Each stop's source: SFPUC's Sewer System Master Plan, Summary Report (Final Draft, March 2010), or this
# site's own record files. photos = the plan's own photos (Chase cleared their use, 2026-10-08), saved under
# app/static/learn/; the plant photos are cut from its Figure 1-1. todo = a placeholder frame for a photo to find.
_SSMP, _SITE = "SFPUC Sewer System Master Plan (2010)", "this site's records"
PHOTO_CREDIT = ("Photos: SFPUC, from its Sewer System Master Plan (2010); photography by Ben Chan, Randall Homan, "
                "Tim Ormond, William John Smith and Cliff Wong. Used with permission.")


def _ph(file: str, cap: str, small: bool = False) -> dict:
    return {"file": "learn/" + file, "cap": title_case(cap), "small": small}   # captions are title case (2026-11-22)   # small: the original is under 400 px, shown near its size

TIMELINE = [
    {"when": "1850s", "title": "The First Sewers", "src": _SSMP,
     "text": "During the Gold Rush, San Francisco starts to install sewers. They carry sewage and rain in the same pipe, down to the shoreline. One tenth of the smaller city sewers (36 inches or less) that are still in use are from before 1900."},
    {"when": "1899", "title": "The First Master Plan", "src": _SSMP, "todo": "A brick sewer under construction",
     "text": "The city already has more than 300 miles of combined sewers. Its first coordinated sewer plan gives 700 miles of sewers and four pump stations. Sewage no longer spills onto land."},
    {"when": "1901–1940", "title": "Half of Today's Pipes", "src": _SSMP, "photos": [_ph("brick_sewer.jpg", "Inside One of the Old Brick Sewers", small=True)],
     "text": "The city installs almost half of the smaller sewers that are in use today (49%) in these four decades."},
    {"when": "1932", "title": "The McQueen Plant", "src": _SSMP, "photos": [_ph("plant_mcqueen_1932.jpg", "The McQueen Plant", small=True)],
     "text": "The first treatment plant on the SFPUC timeline of the system. It is three years before the second master plan."},
    {"when": "1935", "title": "A Plan for Treatment", "src": _SSMP,
     "text": "The second master plan gives the city its first treatment plants and deep-water outfalls, and a total of 900 miles of sewers. Sewage spills in dry weather stop."},
    {"when": "1938–1952", "title": "Three Plants", "src": _SSMP,
     "photos": [_ph("plant_richmond_sunset_1938.jpg", "Richmond-Sunset"), _ph("plant_north_point_1951.jpg", "North Point"), _ph("plant_southeast_early.jpg", "Southeast")],
     "text": "The Richmond-Sunset plant opens in 1938, North Point in 1951 and Southeast in 1952."},
    {"when": "1972", "title": "The Clean Water Act", "src": _SSMP,
     "text": "Congress passes the Clean Water Act. Two years later, the city makes its next master plan as a reply."},
    {"when": "1974", "title": "The Plan That Built the Moat", "src": _SSMP, "todo": "Building a transport/storage structure",
     "text": "The city builds these in approximately 25 years: secondary treatment for all dry-weather flow, the Southwest Ocean Outfall, and 17 miles of transport/storage structures around the shore. The structures hold 197 million gallons. Discharges decrease to an average of 4.4% of the flow for the year."},
    {"when": "Early 1980s", "title": "Southeast Goes Secondary", "src": _SSMP, "photos": [_ph("southeast_aerial.jpg", "The Southeast Plant and its Bayview neighbors")],
     "text": "In 1982, the city upgrades the Southeast plant to secondary treatment. The plant treats all the dry-weather flow of the Bayside. North Point becomes a wet-weather facility. It operates only during storms."},
    {"when": "1986", "title": "Four Miles Out", "src": _SSMP, "todo": "Laying the ocean outfall",
     "text": "The Southwest Ocean Outfall opens. It is a 12-foot pipe under the seabed. It goes approximately four miles offshore to 85 diffusers, approximately 80 feet deep."},
    {"when": "1993", "title": "The Oceanside Plant", "src": _SSMP, "photos": [_ph("oceanside_aerial.jpg", "The Oceanside plant, the Great Highway at left and Lake Merced at lower right")],
     "text": "The new Westside plant opens on the Great Highway, between Lake Merced and the Zoo. It replaces Richmond-Sunset."},
    {"when": "1999", "title": "Beach Postings on the Record", "src": "State Water Board BeachWatch", "photos": [_ph("ocean_surf.jpg", "Surf on the ocean side")],
     "text": "The State record of San Francisco beach advisories starts. The Beach Postings page uses it."},
    {"when": "2002–2003", "title": "Counting Enterococcus", "src": _SITE,
     "text": "The city lab adds Enterococcus and E. coli to total coliform: at the bay stations in July 2002, and at the ocean beaches in October 2003."},
    {"when": "2010", "title": "A Plan for the Next Century", "src": _SSMP, "photos": [_ph("sunnydale_outfall.jpg", "The Sunnydale outfall at Candlestick Cove, the plan's example for a backflow valve at a weir")],
     "text": "976 miles of sewers, 27 pump stations, 36 discharge sites. The plan gives a warning: if the sea level increases as the higher estimates show, the daily high tide will go over the lowest discharge weirs by 2050."},
    {"when": "2016", "title": "Every Discharge, Outfall by Outfall", "src": _SITE,
     "text": "The discharge reports for each outfall on this site start in October 2016. The Discharge Ledger and the storms on the map above use these reports."},
    {"when": "2023", "title": "Volunteers' Results", "src": _SITE, "todo": "The Blue Water Task Force lab",
     "text": "From September 2023, the record on this site includes the Surfrider SF Blue Water Task Force results with the city results."},
    {"when": "2026", "title": "Watching the Map", "src": _SITE,
     "text": "From August 2026, this site examines the SFPUC beach map each minute and keeps the Online Postings Timeline."},
]


def learn_context() -> dict:
    ctx = {"plants": _plants(), "indicators": _indicators(), "caution": ENTERO_CAUTION,
           "entero_limit": STANDARDS["ENTERO"]["single_sample_max"]}
    q = _quanti_story()
    ctx.update(qt=q, qt_steps=_quanti_steps(q), entero_color=next(b["color"] for b in ctx["indicators"] if b["code"] == "ENTERO"))
    for key, fn in (("postings", _postings), ("discharges", _discharges), ("system", _system)):
        try:
            ctx[key] = fn()
        except Exception as e:  # noqa: BLE001 — a missing record file leaves its chart out, never the page
            ctx[key] = None; ctx[key + "_error"] = str(e)
    return ctx


def handle_page(query, body):
    return 200, "text/html; charset=utf-8", render_template("learn/page.html", **learn_context()).encode()


# The hub's two other pages (Chase, 2026-10-18) live in their own modules: Collect (features/learn/collect.py) and the
# The Lab SOP moved to an Admin tab on 2026-11-04 (features/alerts/lab_sop.py); /learn/lab-sop redirects there.
GET_ROUTES = {"/learn": handle_page, **COLLECT_ROUTES}
