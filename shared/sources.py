"""Every source the site reads, with its date coverage — one registry.

The "How it's built" page (/architecture) draws the sources as tiles; clicking
a tile opens the entries registered here for it, served by ``/api/sources``.
Nothing is typed by hand that a file already knows: spans come from the
record CSVs and their manifests, DataSF's newest day from the Supabase mirror,
the Supabase tables' first and last rows from the tables themselves, the build
from ``app/build_info``. Anything unreachable degrades to ``None`` rather than
breaking the page. Supabase lookups are cached for a quarter of an hour per
process; the endpoint is only called when a tile is opened.

An entry::

    {"name": ..., "what": ..., "kind": "live API" | "repo file" | "Supabase table" | "service",
     "first": "YYYY-MM-DD" | None, "last": "YYYY-MM-DD" | "live" | None,
     "cadence": ..., "refreshed": "YYYY-MM-DD" | None, "next_due": "YYYY-MM-DD" | None,
     "stored": ..., "used_by": [page, ...], "note": ... | None}

Tiles (the keys the template puts on each ``<g class="node" data-tile>``):
sfpuc, datasf, bwtf, weather, state, supabase, vercel, github, brevo,
healthchecks, subscribers, visitors.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "features" / "forecast" / "data"

_CACHE: dict = {"at": 0.0, "supabase": None}
_CACHE_TTL_SECONDS = 15 * 60

# Constants that describe live inputs (mirrors of the serving code's settings;
# features/forecast/live_dashboard.py METEO_PARAMS / ACIS_GAUGES / NWS).
ECMWF_FORECAST_DAYS = 6          # forecast_days of the Open-Meteo call (name kept; the model is ICON since 2026-09-30)
KSFO_PAST_DAYS = 7
BWTF_SF_SINCE = "2023-09-01"     # the chapter's first SF collections in the BWTF database (features/comparison/samples.py)
WATCHER_SINCE = "2026-08-20"     # features/forecast/live_dashboard.LiveData.WATCHER_SINCE — the fallback when alert_log is unreachable

TILES = {
    "sfpuc": "SFPUC beach map", "datasf": "City lab results", "bwtf": "Surfrider BWTF", "weather": "Weather & tides",
    "state": "State records", "supabase": "Supabase", "vercel": "Vercel", "github": "GitHub", "brevo": "Brevo",
    "healthchecks": "Healthchecks", "subscribers": "Subscribers", "visitors": "Visitors",
}

# Supabase tables worth a line, with the column that dates a row (db/migrations).
SUPABASE_TABLES = [
    ("subscribers", "created_at", "people signed up for zone alerts"),
    ("alert_log", "created_at", "every posting / sewage transition the watcher acted on"),
    ("alert_deliveries", "sent_at", "what each subscriber was sent, and whether Brevo took it"),
    ("feed_station_days", "day", "the feed's flags per station and day, as the watcher saw them"),
    ("feed_sample_dates", "sample_date", "the sample dates the feed showed per station"),
    ("samples", "sample_date", "mirror of DataSF's lab results, with the day we first saw each"),
    ("forecast_history", "forecast_date", "first and last forecast of every day"),
    ("forecast_changes", "first_at", "every change of the forecast's numbers"),
    ("watcher_errors", "at", "watcher tick errors (90-day retention)"),
]


def _entry(name, what, kind, first=None, last=None, cadence=None, refreshed=None, next_due=None,
           stored=None, used_by=(), note=None) -> dict:
    return {"name": name, "what": what, "kind": kind, "first": first, "last": last, "cadence": cadence,
            "refreshed": refreshed, "next_due": next_due, "stored": stored, "used_by": list(used_by), "note": note}


def _csv_span(path: Path, column: str) -> tuple[Optional[str], Optional[str]]:
    """(min, max) of a date-like column, as YYYY-MM-DD; (None, None) if unreadable."""
    try:
        lo = hi = None
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                v = (r.get(column) or "")[:10]
                if len(v) == 10:
                    lo = v if lo is None or v < lo else lo
                    hi = v if hi is None or v > hi else hi
        return lo, hi
    except Exception:  # noqa: BLE001
        return None, None


def _json_file(path: Path) -> dict:
    try:
        return json.load(open(path))
    except Exception:  # noqa: BLE001
        return {}


# ── Supabase spans (cached) ───────────────────────────────────────────────────

def _supabase_spans() -> dict:
    """{table: {"first", "last", "count_hint"}} for SUPABASE_TABLES, plus
    watcher_runtime's last tick; {} when Supabase is not configured."""
    now = time.monotonic()
    if _CACHE["supabase"] is not None and now - _CACHE["at"] < _CACHE_TTL_SECONDS:
        return _CACHE["supabase"]
    out: dict = {}
    try:
        from shared import supabase as sb
        if not sb.is_configured():
            return {}
        for table, col, _ in SUPABASE_TABLES:
            try:
                lo = sb.select(table, {"select": col, "order": f"{col}.asc", "limit": "1"})
                hi = sb.select(table, {"select": col, "order": f"{col}.desc", "limit": "1"})
                out[table] = {"first": (lo[0][col] or "")[:10] if lo else None, "last": (hi[0][col] or "")[:10] if hi else None}
            except Exception:  # noqa: BLE001
                out[table] = {"first": None, "last": None}
        try:
            rt = sb.select("watcher_runtime", {"select": "last_processed_at", "order": "last_processed_at.desc", "limit": "1"})
            out["watcher_runtime"] = {"last_tick": rt[0]["last_processed_at"] if rt else None}
        except Exception:  # noqa: BLE001
            out["watcher_runtime"] = {"last_tick": None}
    except Exception:  # noqa: BLE001
        out = {}
    _CACHE.update(at=now, supabase=out)
    return out


# ── the registry ─────────────────────────────────────────────────────────────

def registry(include_supabase: bool = True) -> dict:
    """{"generated_at", "tiles": {tile key: [entries]}, "labels": TILES}."""
    from shared import city_history
    from shared.datasf import DATASET_FLOOR, DATASET_PAGE_URL
    sup = _supabase_spans() if include_supabase else {}
    tiles: dict[str, list] = {k: [] for k in TILES}

    # SFPUC beach feed
    watcher_first = (sup.get("alert_log") or {}).get("first") or WATCHER_SINCE
    last_tick = (sup.get("watcher_runtime") or {}).get("last_tick")
    tiles["sfpuc"].append(_entry(
        "SFPUC beach status feed (getBeaches)", "which beaches are posted, and the sewage (CSO) flags, as the public map shows them",
        "live API", first=watcher_first, last="live", cadence="read every minute by the watcher; on each visit by the pages",
        refreshed=(last_tick or "")[:16].replace("T", " ") or None,
        stored="Supabase alert_log (transitions since the watcher went live) and feed_station_days (per station and day since 2026-09-27)",
        used_by=["Dashboard", "Sewage Alert System", "Online Postings Timeline", "CSO Forecast (live corrections)"]))

    # DataSF + the older years + the Poo Bot archive
    newest = (sup.get("samples") or {}).get("last") or _csv_span(DATA / "raw" / "historical_bacteria.csv", "sample_date")[1]
    tiles["datasf"].append(_entry(
        "DataSF — Beach Water Quality Monitoring", "the city's lab results per station, sample date and indicator",
        "live API", first=DATASET_FLOOR, last=newest, cadence="the city publishes nightly; read live; mirrored into Supabase every 30 minutes",
        stored="Supabase samples (with the day each result first appeared) and data/raw/historical_bacteria.csv (the training copy)",
        used_by=["Samples", "Graphs", "Site Report Card", "Sewage Alert System", "CSO Forecast (samples ruler, live corrections)", "Online Postings Timeline"],
        note=DATASET_PAGE_URL))
    prov = city_history.provenance()
    if prov.get("available"):
        tiles["datasf"].append(_entry(
            "SFPUC lab export (STARDB)", "the same lab's results for the years before DataSF, from a one-off export obtained by the chapter",
            "repo file", first=prov["floor"], last=prov["end"], cadence="one-off (Sep 2026)",
            stored="features/forecast/data/sfpuc_stardb_2000_2020/ (60,574 results); the PDF in the chapter's data archive",
            used_by=["Samples", "Graphs", "Site Report Card"],
            note=f"Enterococcus and E. coli from {prov['entero_from']} at the bay stations and {prov['entero_from_ocean']} on the ocean beaches; E. coli in place of fecal coliform until Mar 2021"))
    fs_lo, fs_hi = _csv_span(DATA / "poobot" / "feed_status.csv", "snapshot")
    ps_lo, ps_hi = _csv_span(DATA / "poobot" / "samples.csv", "sample_date")
    if fs_lo:
        tiles["datasf"].append(_entry(
            "Poo Bot feed archive", "552 snapshots of SFPUC's feed committed by a Twitter bot in 2016–17: postings, CSO flags and lab results",
            "repo file", first=fs_lo, last=fs_hi, cadence="one-off (historical)",
            stored="features/forecast/data/poobot/ (feed_status.csv, discharge_onsets.csv, samples.csv)",
            used_by=["CSO Forecast (live-corrections replay, training gates)"],
            note=f"samples {ps_lo} → {ps_hi}, identical to the lab export for those dates" if ps_lo else None))

    # Surfrider
    tiles["bwtf"].append(_entry(
        "Surfrider BWTF lab database (GraphQL)", "the chapter's volunteer Enterococcus results, with field notes",
        "live API", first=BWTF_SF_SINCE, last="live", cadence="read on each visit; volunteers sample on their own schedule",
        stored="not mirrored", used_by=["Samples", "Graphs", "Source Comparison", "Dashboard"]))

    # Weather & tides
    rain_lo, rain_hi = _csv_span(DATA / "raw" / "historical_rain.csv", "date")
    hr_lo, hr_hi = _csv_span(DATA / "raw" / "hourly_rain_openmeteo.csv", "timestamp")
    cc_lo, cc_hi = _csv_span(DATA / "raw" / "historical_rain_cocorahs.csv", "date")
    tiles["weather"] += [
        _entry("NOAA rain gauges via ACIS (Downtown 047772, Oceanside 047767)", "daily rain totals — the models' training rain and the past days of every forecast",
               "live API", first=rain_lo, last="live", cadence="daily; the training copy is refreshed at each retrain",
               stored=f"data/raw/historical_rain.csv (to {rain_hi})", used_by=["CSO Forecast"]),
        _entry("Open-Meteo ERA5 hourly rain", "hourly intensity for the peak-intensity features",
               "live API", first=hr_lo, last=hr_hi, cadence="training only; refreshed at each retrain",
               stored="data/raw/hourly_rain_openmeteo.csv", used_by=["CSO Forecast (training)"]),
        _entry("Open-Meteo ICON forecast", "hourly rain for the coming days (ICON, DWD; ECMWF IFS 2026-09-04 → 09-30)",
               "live API", first="today", last=f"next {ECMWF_FORECAST_DAYS} days", cadence="every forecast refresh (30 min)",
               stored="inside each forecast snapshot", used_by=["CSO Forecast"]),
        _entry("NWS hourly observations at SFO (KSFO)", "observed rain for the past days, overriding the model's hindcast",
               "live API", first=f"past {KSFO_PAST_DAYS} days", last="live", cadence="every forecast refresh",
               stored="inside each forecast snapshot", used_by=["CSO Forecast"]),
        _entry("NOAA CO-OPS tides and NWS forecast", "tide predictions and the rain outlook on the dashboard",
               "live API", first="today", last="live", cadence="on each visit", stored="not stored", used_by=["Dashboard"]),
    ]
    if cc_lo:
        tiles["weather"].append(_entry("CoCoRaHS Potrero Hill gauge", "a third gauge, tried as a rain source and found worse",
                                       "repo file", first=cc_lo, last=cc_hi, cadence="research only", stored="data/raw/historical_rain_cocorahs.csv",
                                       used_by=["model leaderboard (not served)"]))

    # State records
    try:
        import features.discharges.page as dp
        ref, cov = dp._refresh(), dp._coverage()
        ev_lo, ev_hi = _csv_span(dp.WEST_DAILY, "event_date")[0], _csv_span(dp._CSV, "event_date")[1]
        tiles["state"].append(_entry(
            "CIWQS — SFPUC's monthly self-monitoring reports", "every reported combined sewer discharge: outfall, date, duration, volume",
            "repo file", first=ev_lo, last=f"{cov['through']} (last month filed)", cadence="quarterly, by script (see /records)",
            refreshed=ref.get("refreshed_at"), next_due=ref.get("next_due"),
            stored="features/forecast/data/csd/ (events + monthly coverage grid; daily totals 2011-2017 in pre2018/); the source PDFs in the chapter's data archive",
            used_by=["Discharge Ledger", "CSO Forecast (training labels, Model check: events only)", "analysis reports"],
            note="per-event tables from Oct 2016 (Bayside) and Jan 2018 (Oceanside); daily totals before that, back to Mar 2011"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import features.postings.page as pp
        p = pp._load()
        tiles["state"].append(_entry(
            "BeachWatch — State Water Board beach advisories", "every advisory San Francisco filed with the State: station, cause, posted and reopened days",
            "repo file", first=p.get("first"), last=p.get("known_through"), cadence="quarterly, by script; the State's file lags months",
            refreshed=(p.get("refresh") or {}).get("refreshed_at"), next_due=(p.get("refresh") or {}).get("next_due"),
            stored="features/forecast/data/beachwatch/", used_by=["Beach Postings", "CSO Forecast (postings ruler)", "analysis reports"]))
    except Exception:  # noqa: BLE001
        pass

    # Supabase
    for table, col, what in SUPABASE_TABLES:
        span = sup.get(table) or {}
        tiles["supabase"].append(_entry(table, what, "Supabase table", first=span.get("first"), last=span.get("last"),
                                        cadence={"samples": "every forecast refresh (30 min)", "forecast_history": "every forecast refresh",
                                                 "forecast_changes": "every forecast refresh", "feed_station_days": "every watcher tick (1 min)",
                                                 "feed_sample_dates": "every watcher tick", "alert_log": "on each transition",
                                                 "alert_deliveries": "on each send; Brevo status polled", "watcher_errors": "on each error",
                                                 "subscribers": "on signup / unsubscribe"}.get(table),
                                        stored="Supabase (Postgres)", used_by=[]))
    if last_tick:
        tiles["supabase"].append(_entry("watcher_runtime", "the watcher's last tick", "Supabase table", last=last_tick[:16].replace("T", " "),
                                        cadence="every minute", stored="Supabase (Postgres)"))

    # Vercel / GitHub / Brevo / Healthchecks / people
    try:
        from app.build_info import build_info
        b = build_info()
        tiles["vercel"].append(_entry("The web app (Flask on Vercel)", "every page and API; main auto-deploys", "service",
                                      first=None, last="live", cadence="deploys on each push to main",
                                      refreshed=(b.get("process_started") or "")[:16].replace("T", " ") or None,
                                      stored="—", used_by=[], note=b.get("label")))
    except Exception:  # noqa: BLE001
        pass
    tiles["github"].append(_entry("ciniper/BWTF_SF", "the code, the committed record CSVs and the analysis reports", "service",
                                  last="live", cadence="a push to main deploys", stored="github.com/ciniper/BWTF_SF", used_by=[]))
    d = sup.get("alert_deliveries") or {}
    tiles["brevo"].append(_entry("Brevo (transactional email)", "sends each alert; delivery events are read back into alert_deliveries", "service",
                                 first=d.get("first"), last=d.get("last") or "live", cadence="on each alert", stored="Supabase alert_deliveries", used_by=[]))
    tiles["healthchecks"].append(_entry("healthchecks.io", "pinged by every watcher run; silence raises an alarm", "service",
                                        last="live", cadence="every minute", stored="—", used_by=[]))
    s = sup.get("subscribers") or {}
    tiles["subscribers"].append(_entry("Subscribers", "people signed up at /signup for their beach zones", "service",
                                       first=s.get("first"), last="live", cadence="alerts only when a beach turns bad", stored="Supabase subscribers", used_by=[]))
    tiles["visitors"].append(_entry("Visitors", "anyone, any browser — no accounts, no tracking of our own", "service", last="live",
                                    cadence="—", stored="—", used_by=[]))

    return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "labels": TILES, "tiles": tiles,
            "supabase_reachable": bool(sup)}
