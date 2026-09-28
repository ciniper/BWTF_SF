"""Sample viewer for the Source Comparison page — every published result,
one row per sample, from both programs (Chase, 2026-09-27).

The comparison table and the Site Report Card both reduce a station-day to
one number (the worse reading). This is the place the raw record stays
visible: each city row is one station and sample date with EVERY value the
city published for each indicator that day (a double-sampled storm day shows
both), each BWTF row is one volunteer collection with its time.

  * Range: any window; the default is the last year. The city's dataset
    starts at DATASET_FLOOR (2020-07-27); BWTF's SF record starts Sep 2023.
  * Sites: ``dual`` (default) = the six beaches both programs sample, keyed by
    BWTF_TO_SFPUC_NAME; ``all`` = every city station plus BWTF-only sites.
  * Grading: shared/standards.exceeds — over its single-sample limit, the
    total-coliform limit falling to 1,000 when fecal is over 10% of total. On
    a double-sampled day the ratio uses the day's highest fecal and total
    (which value pairs with which is not recoverable from the dataset).
    Enterococcus between ENTERO_CAUTION and the limit is marked caution.

Stateless: fetch on demand, no background threads, no Supabase.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from features.comparison.bwtf_api import SFBWTFClient
from features.comparison.comparison import ANALYTES, BWTF_TO_SFPUC_NAME, DEFAULT_DAYS, parse_range  # noqa: F401
from urllib.parse import quote, urlencode

from shared.datasf import BEACH_SAMPLES_URL, DATASET_FLOOR
from shared.standards import ENTERO_CAUTION, STANDARDS, exceeds, parse_result, single_sample_max
from shared.stations import STATIONS

SCOPES = ("dual", "all")
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


def fetch_city_records(monitor, sources: list[str], start: datetime, end: datetime) -> list[dict]:
    """Every DataSF row for these stations, all three indicators, in the window."""
    if not sources:
        return []
    where = (f"sample_date >= '{start:%Y-%m-%dT00:00:00}' AND sample_date <= '{end:%Y-%m-%dT23:59:59}' "
             f"AND analyte in ({','.join(repr(a) for a in ANALYTES)}) "
             f"AND source in ({','.join(repr(s) for s in sources)})")
    response = monitor.session.get(monitor.API_URL, params={
        "$select": "source,sample_date,analyte,data", "$where": where,
        "$order": "sample_date DESC", "$limit": 50000,
    }, timeout=30)
    response.raise_for_status()
    return response.json()


def _row(site: dict, day: str, time: Optional[str], source: str, cells: dict) -> dict:
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
            "source": source, "cells": out, "over": over_any, "n_samples": n}


def build_samples(city_records: list[dict], bwtf_history: list[dict], start: datetime, end: datetime,
                  scope: str = "dual", site: str = "") -> dict:
    """Pure: records + history -> the viewer payload (JSON-serializable)."""
    scope = scope if scope in SCOPES else "dual"
    sites = viewer_sites(bwtf_history)
    in_scope = [s for s in sites if scope == "all" or s["dual"]]
    if site:
        in_scope = [s for s in in_scope if s["key"] == site]
    by_key = {s["key"]: s for s in in_scope}
    by_bwtf = {s["bwtf_name"]: s for s in in_scope if s["bwtf_name"]}

    rows = []
    city = defaultdict(lambda: defaultdict(list))
    for r in city_records:
        s, a = by_key.get(r.get("source")), r.get("analyte")
        if not s or a not in ANALYTES or not r.get("sample_date"):
            continue
        raw = str(r.get("data") if r.get("data") is not None else "").strip()
        city[(s["key"], r["sample_date"][:10])][a].append({"raw": raw, "value": parse_result(raw)})
    for (key, day), cells in city.items():
        rows.append(_row(by_key[key], day, None, "SFPUC", cells))

    events = defaultdict(lambda: defaultdict(list))
    for h in bwtf_history:
        s, when, code = by_bwtf.get(h.get("site_name")), h.get("collection_time"), SUBSTANCE_TO_CODE.get(h.get("substance"))
        if not s or when is None or not code or when < start or when > end + timedelta(days=1):
            continue
        events[(s["key"], when)][code].append({"raw": h.get("result_raw") or "", "value": h.get("result_value")})
    for (key, when), cells in events.items():
        rows.append(_row(by_key[key], when.strftime("%Y-%m-%d"), when.strftime("%-I:%M %p"), "BWTF", cells))

    # newest day first; within a day by site, the city's sample before the volunteers', then by time
    rows.sort(key=lambda r: (r["site"], 0 if r["source"] == "SFPUC" else 1, r["time"] or ""))
    rows.sort(key=lambda r: r["date"], reverse=True)
    return {
        "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "scope": scope, "site": site,
        "floor": DATASET_FLOOR, "default_days": DEFAULT_DAYS, "caution": ENTERO_CAUTION,
        "sites": [{k: s[k] for k in ("key", "name", "group", "dual", "city")} for s in sites],
        "analytes": [{"code": c, "label": m["label"], "limit": m["limit"]} for c, m in ANALYTES.items()],
        "rows": rows,
        "counts": {
            "rows": len(rows),
            "city": sum(r["source"] == "SFPUC" for r in rows),
            "bwtf": sum(r["source"] == "BWTF" for r in rows),
            "double_sampled_days": sum(r["n_samples"] > 1 for r in rows),
            "over": sum(r["over"] for r in rows),
        },
    }


def build_sample_viewer(start: str = "", end: str = "", scope: str = "dual", site: str = "",
                        bwtf_client: Optional[SFBWTFClient] = None, sf_gov_monitor=None) -> dict:
    """Fetch both programs for the window and build the payload."""
    from features.alerts.monitoring import SFWaterQualityMonitor  # local: avoids the import cycle at module load
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    s, e = parse_range(start, end)
    scope = scope if scope in SCOPES else "dual"
    history = bwtf_client.fetch_history(since=s, max_pages=40)
    sites = viewer_sites(history)
    wanted = [x["key"] for x in sites if x["city"] and (scope == "all" or x["dual"]) and (not site or x["key"] == site)]
    city_start = max(s, datetime.strptime(DATASET_FLOOR, "%Y-%m-%d"))
    records = fetch_city_records(sf_gov_monitor, wanted, city_start, e) if city_start <= e else []
    return build_samples(records, history, s, e, scope, site)


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
        "results_url": results_url(station, date) if date else None,
        "viewer_url": f"/compare?scope=all&vsite={quote(station, safe='')}#viewer",
        "graph_url": f"/compare?scope=all&graph={quote(station, safe='')}",
    }


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
        today = datetime.now()
        records = fetch_city_records(sf_gov_monitor, [station], today - timedelta(days=60), today)
        if not records:
            records = fetch_city_records(sf_gov_monitor, [station], today - timedelta(days=400), today)
    return sample_day_payload(records, station, date)
