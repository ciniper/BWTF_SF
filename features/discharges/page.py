"""Discharge Ledger — every reported combined-sewer discharge into SF waters.

Public read-only page over the ground-truth CSD event dataset
(``features/forecast/data/csd/sf_csd_events.csv`` — per-event records from Oct
2016 on, extracted from SFPUC's monthly Self-Monitoring Reports on CIWQS and
re-harvested quarterly; see NOTES.md there for schema, provenance, and gaps),
plus the older daily-total reports back to Mar 2011 (``data/csd/pre2018/``,
transcribed 2026-10-06) and the 2004–2010 reports from Kyle's records request
(``data/csd/pre2011/``, 2026-11-01), which have big gaps; neither is read by
the served forecast.
Unlike the Site Report Card
(bacteria *samples*) or the Online Postings Timeline (our real-time *flags*), this is
the official record of what was actually discharged: outfall, start, duration,
and volume in million gallons.

Coverage caveats the page must surface (from NOTES.md):
  * Per-event data begins Oct 2016 (Bayside) and Jan 2018 (Oceanside/Westside).
    Before that, back to Mar 2011, the city filed daily totals: Westside per
    outfall with volume, Bayside per outfall GROUP with hours but no volume.
    Those rows carry kind "D" (Westside day) or "G" (Bayside group-day); the
    page counts them as discharges but says what they are.
  * Jul 2004 – Feb 2011 come from a records request, not CIWQS: Bay Jul 2004 –
    Aug 2006, Pacific only some months of 2006–2010. Months with no table read
    are listed per system in ``coverage["missing"]`` so the page never shows a
    missing month as a dry one.
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
_PRE = _CSV.parent / "pre2018"                      # older daily-total reports (build_pre2018.py, qc_pre2018.py)
WEST_DAILY = _PRE / "westside_daily_2011-03_2017-12.csv"
WEST_COV = _PRE / "westside_monthly_coverage_2011-03_2017-12.csv"
BAY_LEGACY = _PRE / "bayside_legacy_2011-03_2016-09.csv"
BAY_COV = _PRE / "bayside_legacy_monthly_coverage_2011-03_2016-09.csv"
QC_JSON = _PRE / "qc_summary.json"
_PRE11 = _CSV.parent / "pre2011"                    # Kyle's records request, Jul 2004 – Feb 2011 (csd_pre2011/build_pre2011.py)
WEST_2008 = _PRE11 / "westside_daily_2008-01_2010-02.csv"
BAY_2004 = _PRE11 / "bayside_daily_2004-07_2006-08.csv"
PRE11_COV = _PRE11 / "monthly_coverage_2004-2011.csv"
FIRST_MONTH = "2004-07"                              # the earliest report in the records request
ONLINE_FROM = "2011-03"                              # SFPUC's first electronic filing on CIWQS
# The raw-record browser's datasets, each served verbatim with its own columns.
CSV_SETS = {"events": _CSV, "westside": WEST_DAILY, "bayside": BAY_LEGACY, "west2008": WEST_2008, "bay2004": BAY_2004}
_MANIFEST = _CSV.parent / "manifest.json"          # refreshed_at etc., written by csd_ciwqs/aggregate.py
REFRESH_DUE_DAYS = DUE_DAYS                          # shared/freshness.py — the quarterly rule every page uses
_COVERED = {"events_parsed", "table_present_zero_events", "no_table_stated_no_discharge"}
# Where the modern per-event format starts (NOTES.md): the grid also carries
# legacy-era months, which the events CSV does not hold.
_MODERN_FROM = {"Bayside": (2016, 10), "Oceanside": (2018, 1)}


def _fac(name: str) -> str:
    return "Oceanside" if name.startswith("Oceanside") else "Bayside"


def _label(ym: str) -> str:
    return datetime(int(ym[:4]), int(ym[5:7]), 1).strftime("%b %Y")


def _spans(months: list[str]) -> list[str]:
    """Consecutive "YYYY-MM" months as "Jul 2004 – Aug 2006" spans."""
    out: list[list[str]] = []
    for m in sorted(months):
        if out:
            y, mo = int(out[-1][1][:4]), int(out[-1][1][5:])
            nxt = f"{y + (mo == 12)}-{mo % 12 + 1:02d}"
            if m == nxt:
                out[-1][1] = m
                continue
        out.append([m, m])
    return [_label(a) if a == b else f"{_label(a)} – {_label(b)}" for a, b in out]


def _pre2011() -> dict:
    """Which months of the records-request era (Jul 2004 – Feb 2011) each system
    has. A month counts as known when every basin's table was read (``events`` /
    ``zero``) or SFPUC's own count says no discharges (``stated_only`` at 0);
    anything else is missing: no report, or discharges counted but no table."""
    known: dict[str, dict[str, bool]] = {"Bayside": {}, "Oceanside": {}}
    with open(PRE11_COV, newline="") as fh:
        for r in csv.DictReader(fh):
            ok = r["status"] in ("events", "zero") or (r["status"] == "stated_only" and r["stated_count"] == "0")
            cur = known[_fac(r["plant"])]
            cur[r["month"]] = cur.get(r["month"], True) and ok
    missing = {f: sorted(m for m, ok in k.items() if not ok) for f, k in known.items()}
    have = {f: sorted(m for m, ok in k.items() if ok) for f, k in known.items()}
    return {"missing": missing, "have": have, "months": len(known["Bayside"])}


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

    pre = _pre2011()
    have = {f: [s for s in _spans(pre["have"][f])] for f in pre["have"]}
    fall = [m for m in pre["missing"]["Oceanside"] if m[5:] in ("10", "11", "12")]
    every_fall = len(fall) == sum(1 for y in range(int(FIRST_MONTH[:4]), int(ONLINE_FROM[:4])) for _ in "abc")
    note = (f"Records start {_label(FIRST_MONTH)}. Before {_label(ONLINE_FROM)} they come from a public records request "
            f"and have big gaps: the Bay side has {', '.join(have['Bayside'])}, and the Pacific side "
            f"{len(pre['have']['Oceanside'])} of {pre['months']} months"
            + (", never October to December" if every_fall else "")
            + ". Years with missing months say how many. "
            f"From {_label(ONLINE_FROM)} SFPUC's reports are online. Until Oct 2016 (Bay) and Jan 2018 "
            "(Pacific) the city filed daily totals, not single events: Pacific outfalls give hours and volume "
            "per day, Bay outfalls give hours per day for groups of outfalls and no volume. Dec 2012 on the "
            "Pacific side has basin totals only.")
    for fac, ms in sorted(holes.items()):
        if ms:
            note += f" No public {fac} report yet for " + ", ".join(_label(m) for m in ms) + "."
    note += f" Records after {_label(f'{through[0]}-{through[1]:02d}')} aren't public on CIWQS yet."
    return {"first": FIRST_MONTH, "online_from": ONLINE_FROM, "bayside_from": FIRST_MONTH, "oceanside_from": FIRST_MONTH,
            "events_from": {"Bayside": "2016-10", "Oceanside": "2018-01"},
            # the chart plots volumes from here; Pacific volumes 2009–2010 exist but their years are mostly missing
            "volume_from": {"Bayside": "2016-10", "Oceanside": ONLINE_FROM},
            "missing": pre["missing"],
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
    older = []   # [date, facility, outfall id, name, water, minutes, MG, kind]; the records-request rows first (same columns)
    for path in (WEST_2008, WEST_DAILY):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                older.append([r["event_date"], "Oceanside", r["outfall_id"] or "Westside (not stated)", r["outfall_name"],
                              r["receiving_water"], round(float(r["duration_hours"]) * 60, 1) if r["duration_hours"] else None,
                              float(r["volume_MG"]) if r["volume_MG"] else None, "D"])
    for path in (BAY_2004, BAY_LEGACY):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                older.append([r["date"], "Bayside", r["outfall_id"], r["outfall_name"], r["receiving_water"],
                              round(float(r["discharge_hours"]) * 60, 1) if r["discharge_hours"] else None, None, "G"])
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
        "older": older,
        "older_columns": COLUMNS + ["kind"],
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
    """A raw dataset file, verbatim — feeds the in-page record browser and
    doubles as the download link. ``?set=westside|bayside`` serves the 2011–2017
    daily-total files, ``west2008|bay2004`` the records-request ones; the
    default is the per-event record."""
    path = CSV_SETS.get(((query or {}).get("set") or ["events"])[0])   # parse_qs: every value is a list
    if path is None:
        return _json({"error": "unknown set; use " + ", ".join(CSV_SETS)}, status=400)
    try:
        return 200, "text/csv; charset=utf-8", path.read_bytes()
    except Exception as exc:
        return _json({"error": f"CSD dataset unavailable: {exc}"}, status=500)


def handle_page(query, body):
    return 200, "text/html; charset=utf-8", render_template("discharges/page.html").encode()


def _rows(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def reporting_context() -> dict:
    """Everything the "How reporting changed" page shows that comes from data:
    the QC results (qc_pre2018.py) and the months that are missing or partial."""
    qc = json.load(open(QC_JSON))
    west = [m for m in _rows(WEST_COV) if m["status"] not in ("events", "zero")]
    bay = [m for m in _rows(BAY_COV) if m["status"] not in ("events", "zero")]
    label = lambda m: datetime(int(m["year"]), int(m["month"]), 1).strftime("%b %Y")
    pre = _pre2011()
    return {"qc": qc, "coverage": _coverage(),
            "pre2011": {f: {"have": _spans(pre["have"][f]), "missing": _spans(pre["missing"][f]),
                            "n_have": len(pre["have"][f]), "n": pre["months"]} for f in pre["have"]},
            "west_gaps": [dict(month=label(m), status=m["status"], note=m["note"]) for m in west],
            "bay_gaps": [dict(month=label(m), status=m["status"], note=m["note"]) for m in bay]}


def handle_reporting(query, body):
    return 200, "text/html; charset=utf-8", render_template("discharges/reporting.html", **reporting_context()).encode()


GET_ROUTES = {
    "/discharges": handle_page,
    "/discharges/api/events": handle_events,
    "/discharges/api/csv": handle_csv,
    "/discharges/reporting": handle_reporting,
}
POST_ROUTES = {}
