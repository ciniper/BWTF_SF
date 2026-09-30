"""Sample viewer for the Source Comparison page — every published result,
one row per sample, from both programs (Chase, 2026-09-27).

The comparison table and the Site Report Card both reduce a station-day to
one number (the worse reading). This is the place the raw record stays
visible: each city row is one station and sample date with EVERY value the
city published for each indicator that day (a double-sampled storm day shows
both), each BWTF row is one volunteer collection with its time. The
    one-day payloads at the bottom feed the sample popover: the city's
    (``sample_day_payload``) and Surfrider's (``bwtf_sample_day_payload``).

  * Range: any window; the default is the last year. City results run from
    2000-01-03: SFPUC's lab export until DataSF begins (DATASET_FLOOR,
    2020-07-27), DataSF from then on (shared/city_history.py; rows before the
    floor carry ``history``). BWTF's SF record starts Sep 2023.
  * Source, as on Graphs: ``all`` (default) = every city station plus
    BWTF-only sites, both labs; ``both`` = only the six beaches both programs
    sample (BWTF_TO_SFPUC_NAME); ``city`` / ``bwtf`` = one lab's sites and rows.
  * Grading: shared/standards.exceeds — over its single-sample limit, the
    total-coliform limit falling to 1,000 when fecal is over 10% of total. On
    a double-sampled day the ratio uses the day's highest fecal and total
    (which value pairs with which is not recoverable from the dataset).
    Enterococcus between ENTERO_CAUTION and the limit is marked caution.

Stateless: fetch on demand, no background threads, no Supabase.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from features.comparison.bwtf_api import BWTF_REPORT_URL, ENTERO_SUBSTANCE, SF_LAB_ID, SFBWTFClient
from features.comparison.comparison import ANALYTES, BWTF_TO_SFPUC_NAME, DEFAULT_DAYS, fetch_city_records, parse_range, resolve_site  # noqa: F401
from urllib.parse import quote, urlencode

from shared import city_history
from shared.datasf import BEACH_SAMPLES_URL, DATASET_FLOOR
from shared.standards import ENTERO_CAUTION, STANDARDS, exceeds, parse_result, single_sample_max
from shared.stations import STATIONS
from shared.clock import now_pacific_naive

SOURCES = ("city", "bwtf", "both", "all")   # as on Graphs: City / Surfrider / Both = only the beaches both programs sample / All


def normalise_source(source: str = "", scope: str = "") -> str:
    """The Source choice; old links carried ``scope=dual|all`` instead (dual = today's Both)."""
    if source in SOURCES:
        return source
    return "both" if scope == "dual" else "all"


def site_fits(site: dict, source: str) -> bool:
    return source == "all" or (source == "both" and site["dual"]) or (source == "city" and site["city"]) or (source == "bwtf" and site["bwtf_name"] is not None)
SUBSTANCE_TO_CODE = {meta["bwtf_substance"]: code for code, meta in ANALYTES.items()}


def viewer_sites(bwtf_history: Optional[list] = None) -> list[dict]:
    """The 20 city stations (``dual`` when BWTF samples the same beach), then
    any BWTF-only site seen in the history (e.g. Bayview Hunters Point)."""
    sfpuc_to_bwtf = {v: k for k, v in BWTF_TO_SFPUC_NAME.items()}
    sites = []
    for sid, st in STATIONS.items():
        bw = sfpuc_to_bwtf.get(st.sfpuc_name)
        sites.append({"key": sid, "name": st.name, "group": st.group, "bwtf_name": bw,
                      "dual": bw is not None, "city": True})
    known = {s["bwtf_name"] for s in sites if s["bwtf_name"]}
    for name in sorted({h["site_name"] for h in (bwtf_history or [])} - known):
        sites.append({"key": "bwtf:" + name, "name": name, "group": "BWTF only", "bwtf_name": name,
                      "dual": False, "city": False})
    return sites


def _fmt_temp(value) -> str:
    return f"{value:.0f}°F" if value is not None else ""


def field_notes(event: dict) -> dict:
    """The volunteer's field record for one collection, formatted for display
    (what the old BWTF Sample Log page showed): tester, air, water, sky, wind,
    tide, waves, rain, comments. Empty strings where nothing was recorded."""
    w = event.get("weather") or {}
    wind = " ".join(x for x in (w.get("wind_direction") or "", f"{w['wind_speed']:.0f} mph" if w.get("wind_speed") is not None else "") if x)
    rain = w.get("precipitation")
    return {
        "tested_by": event.get("tested_by") or "", "air": _fmt_temp(w.get("air_temp")), "water": _fmt_temp(w.get("water_temp")),
        "sky": w.get("current_weather") or "", "wind": wind, "tide": (w.get("tide") or "").title(), "waves": w.get("wave_height") or "",
        "rain": "Yes" if rain else ("No" if rain is False else ""), "comments": event.get("comments") or "",
    }


FIELD_COLUMNS = [("tested_by", "Tested by"), ("air", "Air"), ("water", "Water"), ("sky", "Sky"), ("wind", "Wind"),
                 ("tide", "Tide"), ("waves", "Waves"), ("rain", "Rain"), ("comments", "Comments")]


def _bwtf_rows(history_or_events: list[dict]) -> list[dict]:
    """Accept either fetch_history rows (site_name, collection_time, substance,
    result_value, result_raw) or fetch_event_history events (with ``samples``
    and the field record) and return one row per substance result, each
    carrying ``field`` (empty for history-shaped input)."""
    out = []
    for item in history_or_events:
        if "samples" in item:
            notes = field_notes(item)
            for smp in item.get("samples") or []:
                out.append({"site_name": item.get("site_name"), "collection_time": item.get("collection_time"),
                            "substance": smp.get("substance"), "result_value": smp.get("result_value"),
                            "result_raw": smp.get("result_display") or smp.get("result_raw"), "field": notes})
        else:
            out.append({**item, "field": item.get("field") or {}})
    return out


def _row(site: dict, day: str, time: Optional[str], source: str, cells: dict, field: Optional[dict] = None) -> dict:
    fecal_max = max((c["value"] for c in cells.get("COLI_FECAL", []) if c["value"] is not None), default=None)
    total_max = max((c["value"] for c in cells.get("COLI_TOTAL", []) if c["value"] is not None), default=None)
    out, n, over_any = {}, 0, False
    for code in ANALYTES:
        vals = []
        for c in cells.get(code, []):
            over = exceeds(code, c["value"], fecal_max, total_max)
            caution = (code == "ENTERO" and c["value"] is not None and not over and c["value"] >= ENTERO_CAUTION)
            vals.append({"raw": c["raw"], "value": c["value"], "over": over, "caution": caution})
            over_any = over_any or over
        out[code] = vals
        n = max(n, len(vals))
    return {"date": day, "time": time, "site": site["name"], "site_key": site["key"], "dual": site["dual"],
            "source": source, "cells": out, "over": over_any, "n_samples": n, "field": field or None}


def build_samples(city_records: list[dict], bwtf_history: list[dict], start: datetime, end: datetime,
                  source: str = "", site: str = "", scope: str = "") -> dict:
    """Pure: records + history -> the viewer payload (JSON-serializable).
    ``sites`` lists the sites the Source choice admits; ``rows`` only that
    source's results (``scope`` is the old parameter, see normalise_source)."""
    source = normalise_source(source, scope)
    sites = [s for s in viewer_sites(bwtf_history) if site_fits(s, source)]
    in_scope = [s for s in sites if s["key"] == site] if site else sites
    by_key = {s["key"]: s for s in in_scope} if source != "bwtf" else {}
    by_bwtf = {s["bwtf_name"]: s for s in in_scope if s["bwtf_name"]} if source != "city" else {}

    rows = []
    city = defaultdict(lambda: defaultdict(list))
    for r in city_records:
        s, a = by_key.get(r.get("source")), r.get("analyte")
        if not s or a not in ANALYTES or not r.get("sample_date"):
            continue
        raw = str(r.get("data") if r.get("data") is not None else "").strip()
        city[(s["key"], r["sample_date"][:10])][a].append({"raw": raw, "value": parse_result(raw)})
    for (key, day), cells in city.items():
        rows.append(dict(_row(by_key[key], day, None, "SFPUC", cells), history=day < DATASET_FLOOR))

    events = defaultdict(lambda: defaultdict(list)); fields = {}
    for h in _bwtf_rows(bwtf_history):
        s, when, code = by_bwtf.get(h.get("site_name")), h.get("collection_time"), SUBSTANCE_TO_CODE.get(h.get("substance"))
        if not s or when is None or not code or when < start or when > end + timedelta(days=1):
            continue
        events[(s["key"], when)][code].append({"raw": h.get("result_raw") or "", "value": h.get("result_value")})
        if h.get("field"):
            fields[(s["key"], when)] = h["field"]
    site_of = {s["key"]: s for s in in_scope}
    for (key, when), cells in events.items():
        rows.append(_row(site_of[key], when.strftime("%Y-%m-%d"), when.strftime("%-I:%M %p"), "BWTF", cells, fields.get((key, when))))

    # newest day first; within a day by site, the city's sample before the volunteers', then by time
    rows.sort(key=lambda r: (r["site"], 0 if r["source"] == "SFPUC" else 1, r["time"] or ""))
    rows.sort(key=lambda r: r["date"], reverse=True)
    return {
        "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "source": source, "site": site,
        "floor": city_history.city_record_floor(), "datasf_from": DATASET_FLOOR, "history": city_history.provenance(),
        "default_days": DEFAULT_DAYS, "caution": ENTERO_CAUTION,
        "sites": [dict({k: s[k] for k in ("key", "name", "group", "dual", "city")}, bwtf=s["bwtf_name"] is not None) for s in sites],
        "analytes": [{"code": c, "label": m["label"], "limit": m["limit"]} for c, m in ANALYTES.items()],
        "field_columns": [{"key": k, "label": l} for k, l in FIELD_COLUMNS],
        "rows": rows,
        "counts": {
            "rows": len(rows),
            "city": sum(r["source"] == "SFPUC" for r in rows),
            "bwtf": sum(r["source"] == "BWTF" for r in rows),
            "double_sampled_days": sum(r["n_samples"] > 1 for r in rows),
            "over": sum(r["over"] for r in rows),
        },
    }


def build_sample_viewer(start: str = "", end: str = "", source: str = "", site: str = "", scope: str = "",
                        bwtf_client: Optional[SFBWTFClient] = None, sf_gov_monitor=None) -> dict:
    """Fetch what the Source choice needs for the window and build the payload
    (Surfrider only: no DataSF call; City only: the history still names the
    Surfrider-only sites, so it is fetched either way)."""
    from features.alerts.monitoring import SFWaterQualityMonitor  # local: avoids the import cycle at module load
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    s, e = parse_range(start, end)
    source = normalise_source(source, scope)
    history = bwtf_client.fetch_event_history(since=s, max_pages=20)   # events carry the field notes
    wanted = [x["key"] for x in viewer_sites(history) if x["city"] and source != "bwtf" and site_fits(x, source) and (not site or x["key"] == site)]
    city_start = max(s, datetime.strptime(city_history.city_record_floor(), "%Y-%m-%d"))
    records = fetch_city_records(sf_gov_monitor, wanted, city_start, e) if wanted and city_start <= e else []
    return build_samples(records, history, s, e, source, site)


# ── one station-day: the mini bar graphs behind the "latest sample" chips ────

def results_url(station: str, date: str) -> str:
    """The raw DataSF rows for one station and day (the link the alerts page has always offered)."""
    params = {"$select": "source,sample_date,analyte,data,data_as_of,data_loaded_at",
              "$where": f"source='{station}' AND sample_date = '{date}T00:00:00'", "$order": "source, analyte"}
    return f"{BEACH_SAMPLES_URL}?{urlencode(params)}"


def sample_day_payload(city_records: list[dict], station: str, date: str = "") -> dict:
    """Pure: DataSF rows -> one station's results on one day, every value the
    city published per indicator (a double-sampled day has two), each graded by
    the shared rule, with the limits the graphs draw. No ``date`` = the newest
    published day among the records. Feeds /api/sample-day and the popover on
    the alerts and forecast pages (Chase, 2026-09-27)."""
    st = STATIONS.get(station)
    if st is None:
        raise ValueError(f"unknown station {station!r}")
    recs = [r for r in city_records if r.get("source") == station and r.get("analyte") in ANALYTES and r.get("sample_date")]
    date = date or max((r["sample_date"][:10] for r in recs), default="")
    day = [r for r in recs if r["sample_date"][:10] == date]
    cells = defaultdict(list)
    for r in day:
        raw = str(r.get("data") if r.get("data") is not None else "").strip()
        cells[r["analyte"]].append({"raw": raw, "value": parse_result(raw)})
    site = {"key": station, "name": st.name, "dual": False}
    row = _row(site, date, None, "SFPUC", cells) if day else None
    fecal_max = max((c["value"] for c in cells.get("COLI_FECAL", []) if c["value"] is not None), default=None)
    total_max = max((c["value"] for c in cells.get("COLI_TOTAL", []) if c["value"] is not None), default=None)
    return {
        "station": station, "name": st.name, "group": st.group, "date": date or None, "found": bool(day),
        "cells": row["cells"] if row else {c: [] for c in ANALYTES},
        "over": row["over"] if row else False, "n_samples": row["n_samples"] if row else 0,
        "limits": {c: single_sample_max(c, fecal_max, total_max) for c in ANALYTES},   # total coliform's is the ratio-adjusted one
        "ratio_applied": (fecal_max is not None and total_max is not None and total_max > 0
                          and fecal_max / total_max > STANDARDS["COLI_TOTAL"]["ratio_threshold"]),
        "ratio_note": f"fecal over {STANDARDS['COLI_TOTAL']['ratio_threshold']:.0%} of total, so the limit drops to {STANDARDS['COLI_TOTAL']['single_sample_max_ratio']:,}",
        "caution": ENTERO_CAUTION, "units": "MPN/100mL",
        "analytes": [{"code": c, "label": m["label"]} for c, m in ANALYTES.items()],
        "results_url": results_url(station, date) if date and date >= DATASET_FLOOR else None,   # the export's days have no DataSF page
        "history": bool(date) and date < DATASET_FLOOR,
        "viewer_url": f"/samples?site={quote(station, safe='')}",
        "graph_url": f"/graphs?site={quote(station, safe='')}",
    }


def bwtf_sample_day_payload(events: list[dict], bwtf_name: str, date: str = "") -> dict:
    """Pure: Surfrider's collections at one site on one day (no ``date`` = the
    newest), every Enterococcus result graded by the shared rule, with the
    volunteer's field notes. Same shape as ``sample_day_payload`` so the popover
    draws it the same way — one mini bar graph, since Surfrider measures one
    indicator (Chase, 2026-09-28: the mini graph for BWTF points too).
    ``bwtf_name`` may be any site key ``resolve_site`` accepts (a station id of
    a beach Surfrider samples, ``bwtf:<name>`` or the Surfrider name)."""
    site = resolve_site(bwtf_name)
    if site.get("bwtf_name") is None:
        raise ValueError(f"Surfrider does not sample {bwtf_name!r}")
    rows = [r for r in _bwtf_rows(events) if r.get("site_name") == site["bwtf_name"] and r.get("collection_time") is not None]
    date = date or max((r["collection_time"].strftime("%Y-%m-%d") for r in rows), default="")
    day = sorted((r for r in rows if r["collection_time"].strftime("%Y-%m-%d") == date), key=lambda r: r["collection_time"])
    cells = defaultdict(list)
    for r in day:
        if r.get("substance") == ENTERO_SUBSTANCE:
            cells["ENTERO"].append({"raw": r.get("result_raw"), "value": r.get("result_value")})
    field = next((r["field"] for r in day if any((r.get("field") or {}).values())), None)
    row = _row(site, date, day[0]["collection_time"].strftime("%H:%M") if day else None, "Surfrider", cells, field) if day else None
    site_id = next((e.get("site_id") for e in events if e.get("site_name") == site["bwtf_name"] and e.get("site_id")), "")
    key = quote(site["key"], safe="")
    return {
        "source": "bwtf", "station": None, "bwtf": site["bwtf_name"], "name": site["name"], "date": date or None, "found": bool(day),
        "times": [r["collection_time"].strftime("%H:%M") for r in day],
        "cells": row["cells"] if row else {c: [] for c in ANALYTES},
        "over": row["over"] if row else False, "n_samples": row["n_samples"] if row else 0,
        "limits": {"ENTERO": single_sample_max("ENTERO")}, "ratio_applied": False, "ratio_note": "",
        "caution": ENTERO_CAUTION, "units": "MPN/100mL",
        "analytes": [{"code": "ENTERO", "label": ANALYTES["ENTERO"]["label"]}],
        "field": field,
        "results_url": BWTF_REPORT_URL.format(lab_id=SF_LAB_ID, site_id=site_id) if site_id else None,
        "viewer_url": f"/samples?site={key}&source=bwtf",
        "graph_url": f"/graphs?site={key}",
    }


def build_bwtf_sample_day(bwtf_name: str, date: str = "", bwtf_client: Optional[SFBWTFClient] = None) -> dict:
    """Fetch Surfrider's events back to that day (or the last 60 then 400 days
    for the newest collection at the site) and build the payload."""
    bwtf_client = bwtf_client or SFBWTFClient()
    if date:
        events = bwtf_client.fetch_event_history(since=datetime.strptime(date, "%Y-%m-%d"), max_pages=20)
    else:
        today = now_pacific_naive()
        events = bwtf_client.fetch_event_history(since=today - timedelta(days=60), max_pages=5)
        if not any(e.get("site_name") == bwtf_name for e in events):
            events = bwtf_client.fetch_event_history(since=today - timedelta(days=400), max_pages=20)
    return bwtf_sample_day_payload(events, bwtf_name, date)


def build_sample_day(station: str, date: str = "", sf_gov_monitor=None) -> dict:
    """Fetch one station's rows (that day, or the last 60 then 400 days for the
    newest published day) and build the payload."""
    from features.alerts.monitoring import SFWaterQualityMonitor  # local: avoids the import cycle at module load
    if station not in STATIONS:
        raise ValueError(f"unknown station {station!r}")
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    if date:
        d = datetime.strptime(date, "%Y-%m-%d")
        records = fetch_city_records(sf_gov_monitor, [station], d, d)
    else:
        today = now_pacific_naive()
        records = fetch_city_records(sf_gov_monitor, [station], today - timedelta(days=60), today)
        if not records:
            records = fetch_city_records(sf_gov_monitor, [station], today - timedelta(days=400), today)
    return sample_day_payload(records, station, date)


# ── every day a site was sampled: the popover's ‹ › arrows step through these ──────────
_DATES_CACHE: dict[str, tuple[float, dict]] = {}
_DATES_TTL_S = 600


def city_sample_dates(sources: list[str], monitor) -> list[str]:
    """Every day the city sampled any of these stations, ascending: the lab export before
    DataSF's floor (shared/city_history), then DataSF grouped by day — one light request."""
    out: set[str] = set()
    try:
        if city_history.available():
            out.update(str(r.get("sample_date"))[:10] for r in city_history.records(sources) if r.get("sample_date"))
    except Exception:  # noqa: BLE001 — the export is a bonus; DataSF is the record
        pass
    where = (f"analyte in ({','.join(repr(a) for a in ANALYTES)}) AND source in ({','.join(repr(s) for s in sources)})")
    r = monitor.session.get(monitor.API_URL, params={"$select": "sample_date", "$group": "sample_date", "$where": where,
                                                     "$order": "sample_date", "$limit": 50000}, timeout=30)
    r.raise_for_status()
    out.update(str(row.get("sample_date"))[:10] for row in r.json() if row.get("sample_date"))
    return sorted(d for d in out if d and d != "None")


def bwtf_sample_dates(events: list[dict], bwtf_name: str) -> list[str]:
    """Pure: every day the volunteers collected at this site, ascending."""
    return sorted({r["collection_time"].strftime("%Y-%m-%d") for r in _bwtf_rows(events)
                   if r.get("site_name") == bwtf_name and r.get("collection_time") is not None})


def build_sample_dates(station: str = "", bwtf: str = "", sf_gov_monitor=None, bwtf_client: Optional[SFBWTFClient] = None) -> dict:
    """{ok, source, site, key, dates, latest} — cached ten minutes per site. ``station`` is a
    DataSF source id; ``bwtf`` a Surfrider site name or site key (resolve_site either way)."""
    cache_key = f"bwtf:{bwtf}" if bwtf else f"city:{station}"
    hit = _DATES_CACHE.get(cache_key)
    if hit and hit[0] > time.time():
        return hit[1]
    site = resolve_site(bwtf or station)
    if bwtf:
        if not site["bwtf_name"]:
            raise ValueError(f"Surfrider does not sample {site['name']}")
        client = bwtf_client or SFBWTFClient()
        dates = bwtf_sample_dates(client.fetch_event_history(since=datetime(2023, 1, 1), max_pages=80), site["bwtf_name"])
        out = {"ok": True, "source": "bwtf", "site": site["name"], "key": site["key"], "dates": dates, "latest": dates[-1] if dates else None}
    else:
        if not site["sources"]:
            raise ValueError(f"unknown station {station!r}")   # a 404, not an empty "source in ()" sent to DataSF
        from features.alerts.monitoring import SFWaterQualityMonitor  # local: avoids the import cycle at module load
        monitor = sf_gov_monitor or SFWaterQualityMonitor()
        dates = city_sample_dates(list(site["sources"]), monitor)
        out = {"ok": True, "source": "city", "site": site["name"], "key": site["key"], "dates": dates, "latest": dates[-1] if dates else None}
    _DATES_CACHE[cache_key] = (time.time() + _DATES_TTL_S, out)
    return out
