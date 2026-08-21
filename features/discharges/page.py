"""Discharge Ledger — every reported combined-sewer discharge into SF waters.

Public read-only page over the ground-truth CSD event dataset
(``features/forecast/data/csd/sf_csd_events.csv`` — 1,007 events, Oct 2016 –
Oct 2025, extracted from SFPUC's monthly Self-Monitoring Reports on CIWQS; see
NOTES.md there for schema, provenance, and gaps). Unlike the Site Report Card
(bacteria *samples*) or the CSO Event Timeline (our real-time *flags*), this is
the official record of what was actually discharged: outfall, start, duration,
and volume in million gallons.

Coverage caveats the page must surface (from NOTES.md):
  * Bayside per-event data begins Oct 2016; **Oceanside/Westside begins
    Jan 2018** — citywide totals for 2016–2017 exclude the Pacific side.
  * The record ends Oct 2025 (later SMR attachments not yet public on CIWQS).

The CSV is a static repo file (refreshed ~yearly by re-running
``src/collectors/csd_ciwqs/``), so it's loaded once per process and the full
compact event list ships to the client (~90 KB) — all filtering/aggregation is
client-side and instant. Serverless-safe: no threads, no external calls.

Each route handler returns ``(status, content_type, body_bytes)`` — the
contract ``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from flask import render_template

_CSV = Path(__file__).resolve().parents[1] / "forecast" / "data" / "csd" / "sf_csd_events.csv"

# Compact wire format: list of rows in this column order (keeps the payload
# ~half the size of a dict-per-event).
COLUMNS = ["event_date", "facility", "outfall_id", "outfall_name",
           "receiving_water", "duration_min", "volume_MG"]

_payload_cache: dict | None = None


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
    _payload_cache = {
        "columns": COLUMNS,
        "events": events,
        "coverage": {
            "bayside_from": "2016-10",
            "oceanside_from": "2018-01",
            "through": "2025-10",
            "note": ("Oceanside (Pacific side) per-event records begin Jan 2018 — "
                     "citywide totals for 2016–2017 exclude it. Records after "
                     "Oct 2025 aren't yet public on CIWQS."),
        },
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
