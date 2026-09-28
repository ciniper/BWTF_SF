"""Discharge Ledger — every reported combined-sewer discharge into SF waters.

Public read-only page over the ground-truth CSD event dataset
(``features/forecast/data/csd/sf_csd_events.csv`` — per-event records from Oct
2016 on, extracted from SFPUC's monthly Self-Monitoring Reports on CIWQS and
re-harvested quarterly; see NOTES.md there for schema, provenance, and gaps).
Unlike the Site Report Card
(bacteria *samples*) or the Online Postings Timeline (our real-time *flags*), this is
the official record of what was actually discharged: outfall, start, duration,
and volume in million gallons.

Coverage caveats the page must surface (from NOTES.md):
  * Bayside per-event data begins Oct 2016; **Oceanside/Westside begins
    Jan 2018** — citywide totals for 2016–2017 exclude the Pacific side.
  * The record ends at the last month in the coverage grid, and months SFPUC
    hasn't published are holes — both come from ``_coverage()``, not literals.

The CSV is a static repo file (refreshed ~quarterly by re-running
``src/collectors/csd_ciwqs/``), so it's loaded once per process and the full
compact event list ships to the client (~90 KB) — all filtering/aggregation is
client-side and instant. Serverless-safe: no threads, no external calls.

Each route handler returns ``(status, content_type, body_bytes)`` — the
contract ``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from flask import render_template

_CSV = Path(__file__).resolve().parents[1] / "forecast" / "data" / "csd" / "sf_csd_events.csv"
# Outfall coordinates, basin and the stations SFPUC posts per structure come
# from the canonical registry (shared/outfalls.py); the map popups show them.
from shared.freshness import DUE_DAYS, refresh_block
from shared.outfalls import OUTFALLS  # noqa: E402

# Compact wire format: list of rows in this column order (keeps the payload
# ~half the size of a dict-per-event).
COLUMNS = ["event_date", "facility", "outfall_id", "outfall_name",
           "receiving_water", "duration_min", "volume_MG"]

_payload_cache: dict | None = None

_COVERAGE_CSV = _CSV.parent / "sf_csd_monthly_coverage.csv"
_MANIFEST = _CSV.parent / "manifest.json"          # refreshed_at etc., written by csd_ciwqs/aggregate.py
REFRESH_DUE_DAYS = DUE_DAYS                          # shared/freshness.py — the quarterly rule every page uses
_COVERED = {"events_parsed", "table_present_zero_events", "no_table_stated_no_discharge"}
# Where the modern per-event format starts (NOTES.md): the grid also carries
# legacy-era months, which the events CSV does not hold.
_MODERN_FROM = {"Bayside": (2016, 10), "Oceanside": (2018, 1)}


def _coverage() -> dict:
    """Span and holes of the per-event record, read from the monthly coverage
    grid so a CIWQS refresh moves the ledger's caveats by itself."""
    months: dict[str, set[tuple[int, int]]] = {"Bayside": set(), "Oceanside": set()}
    with open(_COVERAGE_CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["status"] in _COVERED:
                fac = "Oceanside" if r["facility"].startswith("Oceanside") else "Bayside"
                months[fac].add((int(r["year"]), int(r["month"])))
    through = max(m for s in months.values() for m in s)
    holes: dict[str, list[str]] = {}
    for fac, s in months.items():
        cur, miss = _MODERN_FROM[fac], []
        while cur <= through:
            if cur not in s:
                miss.append(f"{cur[0]}-{cur[1]:02d}")
            cur = (cur[0] + (cur[1] == 12), cur[1] % 12 + 1)
        holes[fac] = miss

    def label(ym: tuple[int, int]) -> str:
        return datetime(ym[0], ym[1], 1).strftime("%b %Y")

    note = ("Oceanside (Pacific side) per-event records begin Jan 2018 — citywide totals "
            "for 2016–2017 exclude it.")
    for fac, ms in sorted(holes.items()):
        if ms:
            note += f" No public {fac} report yet for " + ", ".join(label((int(m[:4]), int(m[5:]))) for m in ms) + "."
    note += f" Records after {label(through)} aren't public on CIWQS yet."
    return {"bayside_from": "2016-10", "oceanside_from": "2018-01",
            "through": f"{through[0]}-{through[1]:02d}", "holes": holes, "note": note}


def _refresh() -> dict:
    """When the record was last re-harvested from CIWQS (data/csd/manifest.json)
    and when the next quarterly run is due (shared/freshness.py)."""
    try:
        m = json.load(open(_MANIFEST))
    except Exception:  # noqa: BLE001
        return refresh_block(None, REFRESH_DUE_DAYS)
    return refresh_block(m.get("refreshed_at"), REFRESH_DUE_DAYS,
                         documents=m.get("documents"), events_added=m.get("events_added"), note=m.get("note"))


def _load() -> dict:
    global _payload_cache
    if _payload_cache is not None:
        return _payload_cache
    events = []
    with open(_CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            events.append([
                r["event_date"],
                "Oceanside" if r["facility"].startswith("Oceanside") else "Bayside",
                r["outfall_id"],
                r["outfall_name"],
                r["receiving_water"],
                float(r["duration_min"]) if r["duration_min"] else None,
                float(r["volume_MG"]) if r["volume_MG"] else None,
            ])
    locations = {
        o.id: {
            "lat": o.lat, "lon": o.lon, "water": o.receiving_water, "note": o.note,
            "name": o.name, "basin": o.basin, "feed_name": o.feed_name,
            "stations": o.station_names, "evidence": o.evidence,
        }
        for o in OUTFALLS.values()
    }
    _payload_cache = {
        "columns": COLUMNS,
        "events": events,
        "locations": locations,
        "coverage": _coverage(),
        "refresh": _refresh(),
        "source": "SFPUC monthly Self-Monitoring Reports (NPDES CA0037664 & CA0037681) via CIWQS",
    }
    return _payload_cache


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def handle_events(query, body):
    try:
        return _json(_load())
    except Exception as exc:
        return _json({"error": f"CSD dataset unavailable: {exc}"}, status=500)


def handle_csv(query, body):
    """The raw dataset file, verbatim — feeds the in-page record browser and
    doubles as the download link."""
    try:
        return 200, "text/csv; charset=utf-8", _CSV.read_bytes()
    except Exception as exc:
        return _json({"error": f"CSD dataset unavailable: {exc}"}, status=500)


def handle_page(query, body):
    return 200, "text/html; charset=utf-8", render_template("discharges/page.html").encode()


GET_ROUTES = {
    "/discharges": handle_page,
    "/discharges/api/events": handle_events,
    "/discharges/api/csv": handle_csv,
}
POST_ROUTES = {}
