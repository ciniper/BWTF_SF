#!/usr/bin/env python3
"""
Compare SF beach water-quality results from two independent sources.

  • Surfrider Blue Water Task Force (BWTF) volunteer lab  -> core/bwtf_api.py
  • Public city data -> SF Gov Open Data (Socrata) + SFPUC real-time status

Both programs measure **Enterococcus** (MPN/100mL) and grade it against the
California single-sample maximum of 104 MPN/100mL, which is what makes the two
directly comparable. They sample independently — on different days and at
slightly different points — so this module pairs each BWTF site with its
nearest city station and lays the two latest results side by side, flagging
where they agree or disagree on whether the water meets the standard.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from features.comparison.bwtf_api import SFBWTFClient, parse_datetime, parse_result
from features.alerts.monitoring import STANDARDS, SFPUC_TO_SFGOV_SOURCES, SFWaterQualityMonitor
from shared.sfpuc_api import SFPUCRealTimeAPI

# California single-sample maximum for Enterococcus (MPN/100mL) — same line both
# sources are graded against.
ENTERO_SINGLE_SAMPLE_MAX = STANDARDS["ENTERO"]["single_sample_max"]  # 104

# BWTF site name (lab 76) -> the SFPUC station name used as the city counterpart.
# All but one match verbatim; SFPUC spells out "Street".
BWTF_TO_SFPUC_NAME = {
    "Aquatic Park": "Aquatic Park",
    "Baker Beach at Lobos Creek": "Baker Beach at Lobos Creek",
    "China Beach": "China Beach",
    "Crissy Field Beach East": "Crissy Field Beach East",
    "Ocean Beach at Lincoln Way": "Ocean Beach at Lincoln Way",
    "Ocean Beach at Vicente St": "Ocean Beach at Vicente Street",
}


@dataclass
class ComparisonRow:
    site_name: str
    latitude: Optional[float]
    longitude: Optional[float]

    # Surfrider BWTF (volunteer lab)
    bwtf_date: Optional[str]
    bwtf_raw: Optional[str]
    bwtf_value: Optional[float]
    bwtf_exceeds: Optional[bool]

    # City — SF Gov Open Data lab result
    city_source: Optional[str]
    city_date: Optional[str]
    city_raw: Optional[str]
    city_value: Optional[float]
    city_exceeds: Optional[bool]

    # City — SFPUC official posted status (context)
    sfpuc_status: Optional[str]

    # Comparison
    both_have: bool
    agree: Optional[bool]          # do the two sources agree on pass/fail vs the standard?
    value_delta: Optional[float]   # |bwtf - city|
    day_gap: Optional[int]         # days between the two samples

    def to_dict(self) -> dict:
        return asdict(self)


def _exceeds(value: Optional[float]) -> Optional[bool]:
    if value is None:
        return None
    return value > ENTERO_SINGLE_SAMPLE_MAX


def _fetch_city_entero(monitor: SFWaterQualityMonitor, sources: list[str], days: int) -> dict:
    """Latest city Enterococcus result per SF Gov source id (raw + parsed)."""
    if not sources:
        return {}
    start = datetime.now() - timedelta(days=days)
    source_filter = " OR ".join(f"source='{s}'" for s in sources)
    params = {
        "$select": "source,sample_date,analyte,data,data_as_of",
        "$where": (
            f"analyte='ENTERO' AND sample_date >= '{start.strftime('%Y-%m-%dT00:00:00')}' "
            f"AND ({source_filter})"
        ),
        "$order": "sample_date DESC",
        "$limit": 2000,
    }
    try:
        response = monitor.session.get(monitor.API_URL, params=params, timeout=30)
        response.raise_for_status()
        records = response.json()
    except requests.RequestException as exc:
        print(f"Error fetching city Enterococcus data: {exc}")
        return {}

    latest: dict[str, dict] = {}
    for record in records:
        source = record.get("source")
        if not source or source in latest:  # records are newest-first, keep the first
            continue
        raw, value = parse_result(record.get("data"))
        latest[source] = {
            "date": parse_datetime(record.get("sample_date")),
            "raw": raw,
            "value": value,
        }
    return latest


def _fetch_city_entero_series(monitor: SFWaterQualityMonitor, sources: list[str], days: int) -> dict:
    """All city Enterococcus results per source over `days`, ascending by date."""
    if not sources:
        return {}
    start = datetime.now() - timedelta(days=days)
    source_filter = " OR ".join(f"source='{s}'" for s in sources)
    params = {
        "$select": "source,sample_date,data",
        "$where": (
            f"analyte='ENTERO' AND sample_date >= '{start.strftime('%Y-%m-%dT00:00:00')}' "
            f"AND ({source_filter})"
        ),
        "$order": "sample_date ASC",
        "$limit": 5000,
    }
    try:
        response = monitor.session.get(monitor.API_URL, params=params, timeout=30)
        response.raise_for_status()
        records = response.json()
    except requests.RequestException as exc:
        print(f"Error fetching city Enterococcus series: {exc}")
        return {}

    by_source: dict[str, list] = {}
    for record in records:
        source = record.get("source")
        when = parse_datetime(record.get("sample_date"))
        if not source or when is None:
            continue
        raw, value = parse_result(record.get("data"))
        if value is None:
            continue
        by_source.setdefault(source, []).append({
            "date": when.strftime("%Y-%m-%d"),
            "ts": int(when.timestamp() * 1000),
            "value": value,
            "raw": raw,
        })
    return by_source


def _sfpuc_status_by_name(sfpuc_api: SFPUCRealTimeAPI) -> dict:
    """Map SFPUC station name -> official status string (cso / posted / safe / ...)."""
    status: dict[str, str] = {}
    try:
        for station in sfpuc_api.fetch_stations():
            label = "cso" if station.has_cso else station.status.value
            status[station.station_name] = label
    except Exception as exc:  # defensive: never let live status break the comparison
        print(f"Could not fetch SFPUC status: {exc}")
    return status


def build_comparison(
    bwtf_client: Optional[SFBWTFClient] = None,
    sf_gov_monitor: Optional[SFWaterQualityMonitor] = None,
    sfpuc_api: Optional[SFPUCRealTimeAPI] = None,
    city_days: int = 60,
) -> dict:
    """Build the BWTF-vs-city comparison payload (JSON-serializable)."""
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    sfpuc_api = sfpuc_api or SFPUCRealTimeAPI()

    lab = bwtf_client.fetch_lab()

    # Gather every city source id we might need across the mapped sites.
    needed_sources = sorted({
        src
        for sfpuc_name in BWTF_TO_SFPUC_NAME.values()
        for src in SFPUC_TO_SFGOV_SOURCES.get(sfpuc_name, [])
    })
    city_latest = _fetch_city_entero(sf_gov_monitor, needed_sources, city_days)
    sfpuc_status = _sfpuc_status_by_name(sfpuc_api)

    rows: list[ComparisonRow] = []
    bwtf_sites = lab.sites if lab else []
    for site in bwtf_sites:
        sfpuc_name = BWTF_TO_SFPUC_NAME.get(site.name)
        sources = SFPUC_TO_SFGOV_SOURCES.get(sfpuc_name, []) if sfpuc_name else []

        # Freshest city result among the candidate sources for this site.
        city = None
        for src in sources:
            candidate = city_latest.get(src)
            if not candidate or candidate["date"] is None:
                continue
            if city is None or candidate["date"] > city["date"]:
                city = {**candidate, "source": src}

        bwtf_exceeds = _exceeds(site.entero_value)
        city_value = city["value"] if city else None
        city_exceeds = _exceeds(city_value)
        both_have = site.entero_value is not None and city_value is not None

        agree = value_delta = day_gap = None
        if both_have:
            agree = bwtf_exceeds == city_exceeds
            value_delta = round(abs(site.entero_value - city_value), 1)
            if site.latest_time and city["date"]:
                day_gap = abs((site.latest_time.date() - city["date"].date()).days)

        rows.append(ComparisonRow(
            site_name=site.name,
            latitude=site.latitude,
            longitude=site.longitude,
            bwtf_date=site.latest_time.strftime("%Y-%m-%d") if site.latest_time else None,
            bwtf_raw=site.entero_raw,
            bwtf_value=site.entero_value,
            bwtf_exceeds=bwtf_exceeds,
            city_source=city["source"] if city else None,
            city_date=city["date"].strftime("%Y-%m-%d") if city and city["date"] else None,
            city_raw=city["raw"] if city else None,
            city_value=city_value,
            city_exceeds=city_exceeds,
            sfpuc_status=sfpuc_status.get(sfpuc_name) if sfpuc_name else None,
            both_have=both_have,
            agree=agree,
            value_delta=value_delta,
            day_gap=day_gap,
        ))

    comparable = [r for r in rows if r.both_have]
    summary = {
        "site_count": len(rows),
        "comparable_count": len(comparable),
        "agree_count": sum(1 for r in comparable if r.agree),
        "disagree_count": sum(1 for r in comparable if r.agree is False),
        "bwtf_exceed_count": sum(1 for r in rows if r.bwtf_exceeds),
        "city_exceed_count": sum(1 for r in rows if r.city_exceeds),
        "max_day_gap": max((r.day_gap for r in comparable if r.day_gap is not None), default=None),
    }

    return {
        "generated_at": datetime.now().isoformat(),
        "standard": {"analyte": "Enterococcus", "single_sample_max": ENTERO_SINGLE_SAMPLE_MAX, "units": "MPN/100mL"},
        "bwtf_available": lab is not None,
        "lab_name": lab.name if lab else None,
        "summary": summary,
        "rows": [r.to_dict() for r in rows],
    }


def _by_day_max(series: list[dict]) -> dict:
    """Collapse a point list to one entry per calendar day — the day's worst (max) reading."""
    best: dict[str, dict] = {}
    for p in series:
        cur = best.get(p["date"])
        if cur is None or p["value"] > cur["value"]:
            best[p["date"]] = p
    return best


def build_site_history(
    site_name: str,
    bwtf_client: Optional[SFBWTFClient] = None,
    sf_gov_monitor: Optional[SFWaterQualityMonitor] = None,
    days: int = 540,
) -> dict:
    """Per-site Enterococcus time series for both sources (JSON-serializable).

    Returns ascending-by-date point lists for the BWTF volunteer lab and the
    city's published lab results (SF Gov Open Data). Point: {date, ts, value, raw}.
    """
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    since = datetime.now() - timedelta(days=days)

    bwtf_series = []
    for rec in bwtf_client.fetch_history(since=since):
        if rec["site_name"] != site_name or rec["substance"] != "Enterococcus":
            continue
        when, value = rec["collection_time"], rec["result_value"]
        if when is None or value is None:
            continue
        bwtf_series.append({
            "date": when.strftime("%Y-%m-%d"),
            "ts": int(when.timestamp() * 1000),
            "value": value,
            "raw": rec["result_raw"],
        })
    bwtf_series.sort(key=lambda p: p["ts"])

    sfpuc_name = BWTF_TO_SFPUC_NAME.get(site_name)
    sources = SFPUC_TO_SFGOV_SOURCES.get(sfpuc_name, []) if sfpuc_name else []
    by_source = _fetch_city_entero_series(sf_gov_monitor, sources, days)
    # Use the single city station with the most data so the line stays continuous.
    city_source = max(by_source, key=lambda s: len(by_source[s]), default=None)
    city_series = by_source.get(city_source, []) if city_source else []

    # Same-day paired samples: only dates where BOTH programs sampled this site.
    b_by_day = _by_day_max(bwtf_series)
    c_by_day = _by_day_max(city_series)
    paired = []
    for day in sorted(set(b_by_day) & set(c_by_day)):
        bp, cp = b_by_day[day], c_by_day[day]
        paired.append({
            "date": day,
            "ts": bp["ts"],
            "bwtf": bp["value"], "bwtf_raw": bp["raw"],
            "city": cp["value"], "city_raw": cp["raw"],
        })

    return {
        "site": site_name,
        "standard": ENTERO_SINGLE_SAMPLE_MAX,
        "units": "MPN/100mL",
        "days": days,
        "city_source": city_source,
        "bwtf": bwtf_series,
        "city": city_series,
        "paired": paired,
    }


def main():
    """Print the comparison as a text table."""
    data = build_comparison()
    s = data["summary"]
    print(f"BWTF ({data['lab_name']}) vs City — Enterococcus (limit {data['standard']['single_sample_max']} MPN/100mL)")
    print(f"Generated {data['generated_at'][:19]} | BWTF available: {data['bwtf_available']}\n")
    print(f"{'Site':32s} {'BWTF':>14s} {'City (SF Gov)':>16s} {'SFPUC':>8s}  Agree")
    print("-" * 82)
    for r in data["rows"]:
        bwtf = f"{r['bwtf_raw'] or '—'} ({r['bwtf_date'] or '—'})"
        city = f"{r['city_raw'] or '—'} ({r['city_date'] or '—'})"
        agree = "—" if r["agree"] is None else ("yes" if r["agree"] else "NO")
        print(f"{r['site_name']:32s} {bwtf:>14s} {city:>16s} {str(r['sfpuc_status'] or '—'):>8s}  {agree}")
    print("-" * 82)
    print(f"{s['comparable_count']} comparable | agree {s['agree_count']} / disagree {s['disagree_count']} "
          f"| BWTF exceed {s['bwtf_exceed_count']} | city exceed {s['city_exceed_count']} | max gap {s['max_day_gap']}d")


if __name__ == "__main__":
    main()
