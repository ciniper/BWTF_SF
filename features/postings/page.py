"""Beach Postings — every advisory San Francisco filed with the State, 1999 → today.

Public read-only page over the BeachWatch record (California State Water
Resources Control Board, data.ca.gov "Beach Water Quality Postings and
Closures", the SF rows stored at ``features/forecast/data/beachwatch/`` by
``src/collectors/beachwatch.py --refresh``). Where the Discharge Ledger is what
SFPUC *discharged* and the Site Report Card is what the *water* measured, this
is what the public was *told*: the signs — when each beach was posted, for how
long, and why.

Cause classes come from the collector (``cause_class``): ``cso`` = a sewage
discharge named as the source, ``rain`` = a rain advisory, ``other`` = a
posting the record attributes to neither (dry-weather bacteria, unknown). A
posting runs from ``posted_on`` through ``reopened_on`` inclusive; the two
filings left open for over a year are shown but excluded from day totals
(``MAX_DURATION_DAYS``, same rule as the forecast's posting label).

Static repo file → loaded once per process, compact rows shipped to the client
(~110 KB), all filtering client-side. Serverless-safe: no threads, no calls.
Each route handler returns ``(status, content_type, body_bytes)``.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from flask import render_template

from shared.freshness import DUE_DAYS, refresh_block
from shared.stations import STATIONS
from shared.zones import ZONES

_ROOT = Path(__file__).resolve().parents[2]
_DIR = _ROOT / "features" / "forecast" / "data" / "beachwatch"
_CSV = _DIR / "sf_beach_advisories.csv"
_MANIFEST = _DIR / "manifest.json"
MAX_DURATION_DAYS = 60   # src/collectors/beachwatch.MAX_DURATION_DAYS — longer = a filing left open

COLUMNS = ["posted_on", "reopened_on", "duration_days", "station", "zone", "cause_class", "advisory_type", "cause", "source"]
CAUSE_LABELS = {"cso": "Sewage discharge", "rain": "Rain advisory", "other": "Other / unknown"}

_payload_cache: dict | None = None


def _station_name(sid: str, description: str) -> str:
    st = STATIONS.get(sid)
    return st.name if st else (description or sid)


def _load() -> dict:
    global _payload_cache
    if _payload_cache is not None:
        return _payload_cache
    rows, stations = [], {}
    with open(_CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            sid = r["station"] or r["station_name"]
            stations.setdefault(sid, {"name": _station_name(sid, r.get("station_description", "")),
                                      "zone": r["zone"], "beach": r.get("beach_name", "")})
            rows.append([r["posted_on"], r["reopened_on"], int(r["duration_days"] or 0), sid, r["zone"],
                         r["cause_class"], r["advisory_type"], r["cause"], r["source"]])
    rows.sort(key=lambda x: (x[0], x[3]), reverse=True)
    try:
        man = json.load(open(_MANIFEST))
    except Exception:  # noqa: BLE001
        man = {}
    zones = {k: z.label for k, z in ZONES.items()}
    for z in {x[4] for x in rows}:
        zones.setdefault(z, z.replace("_", " ").title())
    _payload_cache = {
        "columns": COLUMNS,
        "advisories": rows,
        "stations": stations,
        "zones": zones,
        "cause_labels": CAUSE_LABELS,
        "max_duration_days": MAX_DURATION_DAYS,
        "first": min((x[0] for x in rows), default=None),
        "known_through": max((x[1] or x[0] for x in rows), default=None),
        "fetched_at": man.get("fetched_at"),
        # the freshness line: when WE last fetched the State's file and when the quarterly run is due;
        # the record's own end (known_through) is the State's side — SF files postings months late
        "refresh": refresh_block(man.get("fetched_at"), DUE_DAYS, statewide_rows=man.get("statewide_rows"), sf_rows=man.get("sf_rows")),
        "excluded_long": len(man.get("excluded_long_advisories") or []),
        "source": "California State Water Resources Control Board — BeachWatch, \"Beach Posting and Closures – Advisories\" (data.ca.gov), San Francisco rows",
    }
    return _payload_cache


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj).encode()


def handle_advisories(query, body):
    return _json(_load())


def handle_csv(query, body):
    return 200, "text/csv; charset=utf-8", _CSV.read_bytes()


def handle_page(query, body):
    return 200, "text/html; charset=utf-8", render_template("postings/page.html").encode()


GET_ROUTES = {
    "/postings": handle_page,
    "/postings/api/advisories": handle_advisories,
    "/postings/api/csv": handle_csv,
}
POST_ROUTES: dict = {}
