#!/usr/bin/env python3
"""
Compare SF beach water-quality results from two independent sources.

  • Surfrider Blue Water Task Force (BWTF) volunteer lab
  • Public city data -> SF Gov Open Data (Socrata) + SFPUC real-time status

The bacteria analyte is selectable. BWTF measures only **Enterococcus**; the
city (SF Gov) feed also publishes Total and Fecal coliform for these marine/bay
sites (E. coli is a freshwater indicator and isn't sampled here). For
Enterococcus the two sources compare head-to-head; for the coliforms only the
city has data, so BWTF shows "not measured". Each analyte is graded against its
own California single-sample maximum.
"""
from __future__ import annotations

import sys
from collections import defaultdict
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
from shared.standards import ENTERO_CAUTION, exceeds, parse_result as std_parse, single_sample_max
from shared import city_history
from shared.datasf import DATASET_FLOOR
from shared.stations import STATIONS
from shared.zones import ZONES, ZONE_OF_SOURCE
from shared.clock import now_pacific_naive, utc_iso

# The BWTF lab's sites with no city counterpart (the lab's site list, 2026-09)
# and the zone each sits in (shared/zones.py keys). The samples viewer
# discovers these from the history at run time; the graphs page's site list
# is rendered offline from this.
BWTF_ONLY_SITES = {"Bayview Hunters Point": "east"}

# The history graph's default window (Chase, 2026-09-27: a year, selectable back
# to the dataset floor). The sample viewer shares it.
DEFAULT_DAYS = 365

# Selectable analytes for these sites. dict key = SF Gov `analyte` code; each maps
# to the BWTF substance name, a display label, and the CA single-sample maximum.
# BWTF only reports Enterococcus, so the coliforms are city-only here. E. coli is
# the indicator the city ran in place of fecal coliform from Jul 2002 to Jul 2020
# (shared/city_history.py) and in a few hundred rows since.
ANALYTES = {
    "ENTERO":     {"label": "Enterococcus",   "bwtf_substance": "Enterococcus",   "limit": STANDARDS["ENTERO"]["single_sample_max"]},
    "COLI_FECAL": {"label": "Fecal coliform", "bwtf_substance": "Fecal Coliform", "limit": STANDARDS["COLI_FECAL"]["single_sample_max"]},
    "COLI_E":     {"label": "E. coli",        "bwtf_substance": "E. coli",        "limit": STANDARDS["COLI_E"]["single_sample_max"]},
    "COLI_TOTAL": {"label": "Total coliform", "bwtf_substance": "Total Coliform", "limit": STANDARDS["COLI_TOTAL"]["single_sample_max"]},
}
DEFAULT_ANALYTE = "ENTERO"


def resolve_analyte(code: Optional[str]) -> str:
    """Return a valid analyte code, falling back to the default."""
    return code if code in ANALYTES else DEFAULT_ANALYTE


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


def parse_range(start: str = "", end: str = "") -> tuple[datetime, datetime]:
    """YYYY-MM-DD strings -> (start, end) datetimes; the default is the last
    DEFAULT_DAYS ending today. Bad or reversed input falls back the same way."""
    today = now_pacific_naive().replace(hour=0, minute=0, second=0, microsecond=0)

    def parse(s):
        try:
            return datetime.strptime((s or "").strip(), "%Y-%m-%d")
        except ValueError:
            return None
    e = parse(end) or today
    s = parse(start) or (e - timedelta(days=DEFAULT_DAYS))
    if s > e:
        s = e - timedelta(days=DEFAULT_DAYS)
    return s, e


def resolve_site(site: str) -> dict:
    """A site key or name -> {key, name, bwtf_name, sources, dual}. Accepts a
    DataSF station id ("OCEAN#15_SL"), "bwtf:<BWTF site name>" for a BWTF-only
    site, or a bare BWTF site name (the comparison table's original key), so
    the history graph works for every station, not only the six dual ones."""
    sfpuc_to_bwtf = {v: k for k, v in BWTF_TO_SFPUC_NAME.items()}
    if site in STATIONS:
        st = STATIONS[site]
        bw = sfpuc_to_bwtf.get(st.sfpuc_name)
        return {"key": site, "name": st.name, "bwtf_name": bw, "sources": [site], "dual": bw is not None}
    name = site[5:] if site.startswith("bwtf:") else site
    sfpuc_name = BWTF_TO_SFPUC_NAME.get(name)
    sources = SFPUC_TO_SFGOV_SOURCES.get(sfpuc_name, []) if sfpuc_name else []
    if sources:
        return {"key": sources[0], "name": STATIONS[sources[0]].name, "bwtf_name": name, "sources": sources, "dual": True}
    return {"key": "bwtf:" + name, "name": name, "bwtf_name": name, "sources": [], "dual": False}


@dataclass
class ComparisonRow:
    site_name: str
    latitude: Optional[float]
    longitude: Optional[float]

    # Surfrider BWTF (volunteer lab)
    bwtf_date: Optional[str]
    bwtf_time: Optional[str]
    bwtf_raw: Optional[str]
    bwtf_value: Optional[float]
    bwtf_exceeds: Optional[bool]

    # City — SF Gov Open Data lab result. On a double-sampled day (DataSF keeps
    # the date but not the time, so a routine sample and a storm resample share
    # it) city_raws lists every result, city_raw joins them, city_value is the
    # worse one and grades the row (Chase, 2026-09-27).
    city_source: Optional[str]
    city_date: Optional[str]
    city_raw: Optional[str]
    city_value: Optional[float]
    city_exceeds: Optional[bool]
    city_raws: list
    city_n: int

    # City — SFPUC official posted status (context)
    sfpuc_status: Optional[str]

    # Comparison
    both_have: bool
    agree: Optional[bool]          # do the two sources agree on pass/fail vs the standard?
    value_delta: Optional[float]   # |bwtf - city|
    day_gap: Optional[int]         # days between the two samples

    # Which kind of site: key = DataSF station id (or "bwtf:<name>"); dual =
    # both programs sample it; bwtf_site = a BWTF lab site (dual or BWTF-only).
    # City-only stations join the table behind the "All sites" toggle.
    site_key: str = ""
    dual: bool = False
    bwtf_site: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _exceeds(value: Optional[float], limit: float) -> Optional[bool]:
    if value is None:
        return None
    return value > limit


def _bwtf_sample_for(site, substance: str):
    """Latest BWTF (raw, value, datetime) for a substance on this site, else (None, None, None)."""
    for sample in site.samples:
        if sample.substance == substance:
            return sample.result_raw, sample.result_value, sample.collection_time
    return None, None, None


def _fetch_city_latest(monitor: SFWaterQualityMonitor, sources: list[str], days: int, analyte: str) -> dict:
    """Latest city result for `analyte` per SF Gov source id (raw + parsed)."""
    if not sources:
        return {}
    start = now_pacific_naive() - timedelta(days=days)
    source_filter = " OR ".join(f"source='{s}'" for s in sources)
    params = {
        "$select": "source,sample_date,analyte,data,data_as_of",
        "$where": (
            f"analyte='{analyte}' AND sample_date >= '{start.strftime('%Y-%m-%dT00:00:00')}' "
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
        print(f"Error fetching city {analyte} data: {exc}")
        return {}

    return _latest_by_source(records)


def _latest_by_source(records: list[dict]) -> dict:
    """Newest-first records -> {source: latest}. Every record sharing the
    source's newest sample date is kept: ``raws`` lists them in the order the
    city published them, ``raw`` joins them for display, ``value`` is the
    worst (max) and grades the row, ``n`` counts them."""
    latest: dict[str, dict] = {}
    for record in records:
        source = record.get("source")
        when = parse_datetime(record.get("sample_date"))
        if not source or when is None:
            continue
        raw, value = parse_result(record.get("data"))
        cur = latest.get(source)
        if cur is None:
            latest[source] = {"date": when, "raw": raw, "value": value, "raws": [raw], "values": [value], "n": 1}
        elif when.date() == cur["date"].date():   # same latest day: a second sample
            cur["raws"].append(raw)
            cur["values"].append(value)
            cur["n"] += 1
            cur["raw"] = " / ".join(cur["raws"])
            vals = [v for v in cur["values"] if v is not None]
            cur["value"] = max(vals) if vals else None
    return latest


def _fetch_city_series(monitor: SFWaterQualityMonitor, sources: list[str], start: datetime, end: datetime,
                       analyte: str) -> dict:
    """All city results for `analyte` per source in [start, end], ascending by date."""
    if not sources:
        return {}
    records = city_history.records(sources, start, end, [analyte]) if city_history.covers(start) else []
    api_start = max(start, _datasf_floor())
    source_filter = " OR ".join(f"source='{s}'" for s in sources)
    params = {
        "$select": "source,sample_date,data",
        "$where": (
            f"analyte='{analyte}' AND sample_date >= '{api_start.strftime('%Y-%m-%dT00:00:00')}' "
            f"AND sample_date <= '{end.strftime('%Y-%m-%dT23:59:59')}' AND ({source_filter})"
        ),
        "$order": "sample_date ASC",
        "$limit": 5000,
    }
    if api_start <= end:
        try:
            response = monitor.session.get(monitor.API_URL, params=params, timeout=30)
            response.raise_for_status()
            records = records + response.json()
        except requests.RequestException as exc:
            print(f"Error fetching city {analyte} series: {exc}")
            if not records:
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


def _datasf_floor() -> datetime:
    return datetime.strptime(DATASET_FLOOR, "%Y-%m-%d")


def fetch_city_records(monitor, sources: list[str], start: datetime, end: datetime) -> list[dict]:
    """Every city row for these stations and the four indicators in [start, end]
    (raw Socrata dicts: source, sample_date, analyte, data): DataSF from its
    floor, and for the days before it SFPUC's lab export (shared/city_history.py,
    rows flagged ``history``)."""
    if not sources:
        return []
    out = city_history.records(sources, start, end, list(ANALYTES)) if city_history.covers(start) else []
    api_start = max(start, _datasf_floor())
    if api_start > end:
        return out
    where = (f"sample_date >= '{api_start:%Y-%m-%dT00:00:00}' AND sample_date <= '{end:%Y-%m-%dT23:59:59}' "
             f"AND analyte in ({','.join(repr(a) for a in ANALYTES)}) "
             f"AND source in ({','.join(repr(s) for s in sources)})")
    response = monitor.session.get(monitor.API_URL, params={
        "$select": "source,sample_date,analyte,data", "$where": where,
        "$order": "sample_date DESC", "$limit": 50000,
    }, timeout=30)
    response.raise_for_status()
    return out + response.json()


def graph_sites() -> list[dict]:
    """Every site the graphs page can plot, in zone order (shared/zones.py):
    the 20 stations (``city``; ``bwtf`` too when Surfrider samples the beach)
    with the Surfrider-only sites placed in their zones. ``group`` is the zone label."""
    sfpuc_to_bwtf = {v: k for k, v in BWTF_TO_SFPUC_NAME.items()}
    out = []
    for zone in ZONES.values():
        for sid, st in STATIONS.items():
            if ZONE_OF_SOURCE.get(sid) == zone.key:
                dual = st.sfpuc_name in sfpuc_to_bwtf
                out.append({"key": sid, "name": st.name, "group": zone.label, "zone": zone.key, "dual": dual, "city": True, "bwtf": dual})
        out += [{"key": "bwtf:" + n, "name": n, "group": zone.label, "zone": zone.key, "dual": False, "city": False, "bwtf": True}
                for n, zk in BWTF_ONLY_SITES.items() if zk == zone.key]
    return out


def site_series_payload(site: dict, city_records: list[dict], bwtf_history: list[dict], since: datetime, until: datetime) -> dict:
    """Pure: one site's results over time for the graphs page. City: per day and
    indicator the worst value the city published (a double-sampled day keeps
    both counted in ``n``), graded by the shared rule with the ratio from the
    same day's fecal and total; every point carries the limit that applied
    and ``pct`` = value as % of it. Surfrider: every Enterococcus collection.
    ``paired``: days both programs sampled (worst per day). ``worst``: per day
    and source, the one indicator with the highest ``pct`` (city: across the
    three indicators; Surfrider: its Enterococcus)."""
    by_day: dict = defaultdict(lambda: defaultdict(list))
    for r in city_records:
        if r.get("source") not in site["sources"] or r.get("analyte") not in ANALYTES or not r.get("sample_date"):
            continue
        raw = str(r.get("data") if r.get("data") is not None else "").strip()
        v = std_parse(raw)
        if v is not None:
            by_day[r["sample_date"][:10]][r["analyte"]].append((v, raw))
    city = {code: [] for code in ANALYTES}
    worst_city = []   # per day, the indicator furthest over (or nearest to) its own limit — the "All % threshold" view
    for day in sorted(by_day):
        vals = by_day[day]
        fecal = max((v for v, _ in vals.get("COLI_FECAL", [])), default=None)
        total = max((v for v, _ in vals.get("COLI_TOTAL", [])), default=None)
        ts = int(datetime.strptime(day, "%Y-%m-%d").replace(hour=12).timestamp() * 1000)
        day_points = []
        for code in ANALYTES:                       # registry order, so a tie on % goes to Enterococcus
            pairs = vals.get(code)
            if not pairs:
                continue
            v, raw = max(pairs)
            limit = single_sample_max(code, fecal, total)
            point = {"date": day, "ts": ts, "value": v, "raw": raw, "n": len(pairs), "limit": limit, "pct": _pct_of_limit(v, limit),
                     "over": exceeds(code, v, fecal, total),
                     "ratio": code == "COLI_TOTAL" and limit != STANDARDS["COLI_TOTAL"]["single_sample_max"],
                     "station": site["sources"][0] if site["sources"] else None, "name": site["name"]}
            city[code].append(point)
            day_points.append(dict(point, code=code, label=ANALYTES[code]["label"]))
        worst_city.append(max(day_points, key=lambda q: q["pct"]))
    entero_limit = STANDARDS["ENTERO"]["single_sample_max"]
    bwtf = []
    for rec in bwtf_history:
        if rec.get("site_name") != site["bwtf_name"] or rec.get("substance") != "Enterococcus":
            continue
        when, value = rec.get("collection_time"), rec.get("result_value")
        if when is None or value is None or when < since or when > until + timedelta(days=1):
            continue
        bwtf.append({"date": when.strftime("%Y-%m-%d"), "ts": int(when.timestamp() * 1000), "value": value,
                     "raw": rec.get("result_raw"), "limit": entero_limit, "pct": _pct_of_limit(value, entero_limit),
                     "over": exceeds("ENTERO", value), "name": site["name"]})
    bwtf.sort(key=lambda p: p["ts"])
    c_by_day = {p["date"]: p for p in city["ENTERO"]}
    b_by_day = _by_day_max(bwtf)
    worst_bwtf = [dict(b_by_day[d], code="ENTERO", label=ANALYTES["ENTERO"]["label"]) for d in sorted(b_by_day)]
    paired = [{"date": d, "bwtf": b_by_day[d]["value"], "bwtf_raw": b_by_day[d]["raw"], "city": c_by_day[d]["value"], "city_raw": c_by_day[d]["raw"]}
              for d in sorted(set(c_by_day) & set(b_by_day))]
    return {
        "site": site["name"], "site_key": site["key"], "bwtf_name": site["bwtf_name"], "bwtf_sampled": site["bwtf_name"] is not None,
        "dual": site["dual"], "start": since.strftime("%Y-%m-%d"), "end": until.strftime("%Y-%m-%d"),
        "analytes": [{"code": c, "label": m["label"]} for c, m in ANALYTES.items()],
        "limits": {c: STANDARDS[c]["single_sample_max"] for c in ANALYTES}, "caution": ENTERO_CAUTION, "units": "MPN/100mL",
        "city": city, "bwtf": bwtf, "paired": paired,
        "worst": {"city": worst_city, "bwtf": worst_bwtf},
        "counts": {"city_days": len(by_day), "bwtf": len(bwtf), "paired": len(paired)},
    }


def build_site_series(site_name: str, bwtf_client: Optional[SFBWTFClient] = None,
                      sf_gov_monitor: Optional[SFWaterQualityMonitor] = None, start: str = "", end: str = "") -> dict:
    """Fetch both programs for one site and window and build the graphs payload."""
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    site = resolve_site(site_name)
    since, until = parse_range(start, end)
    records = fetch_city_records(sf_gov_monitor, site["sources"], since, until) if site["sources"] else []
    history = bwtf_client.fetch_history(since=since, max_pages=40) if site["bwtf_name"] else []
    return site_series_payload(site, records, history, since, until)


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
    analyte: str = DEFAULT_ANALYTE,
) -> dict:
    """Build the BWTF-vs-city comparison payload for one analyte (JSON-serializable)."""
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    sfpuc_api = sfpuc_api or SFPUCRealTimeAPI()

    analyte = resolve_analyte(analyte)
    cfg = ANALYTES[analyte]
    limit = cfg["limit"]

    lab = bwtf_client.fetch_lab()

    # Every city station takes part (one query); the ones BWTF also samples
    # are "dual" and lead the table, the rest sit behind the All sites toggle.
    city_latest = _fetch_city_latest(sf_gov_monitor, sorted(STATIONS), city_days, analyte)
    sfpuc_status = _sfpuc_status_by_name(sfpuc_api)

    def row_for(site_name, lat, lon, sources, sfpuc_name, bwtf_sample, site_key, dual, bwtf_site) -> ComparisonRow:
        # Freshest city result among the candidate sources for this site.
        city = None
        for src in sources:
            candidate = city_latest.get(src)
            if not candidate or candidate["date"] is None:
                continue
            if city is None or candidate["date"] > city["date"]:
                city = {**candidate, "source": src}

        bwtf_raw, bwtf_value, bwtf_time = bwtf_sample
        bwtf_exceeds = _exceeds(bwtf_value, limit)
        city_value = city["value"] if city else None
        city_exceeds = _exceeds(city_value, limit)
        both_have = bwtf_value is not None and city_value is not None

        agree = value_delta = day_gap = None
        if both_have:
            agree = bwtf_exceeds == city_exceeds
            value_delta = round(abs(bwtf_value - city_value), 1)
            if bwtf_time and city["date"]:
                day_gap = abs((bwtf_time.date() - city["date"].date()).days)

        return ComparisonRow(
            site_name=site_name,
            latitude=lat,
            longitude=lon,
            bwtf_date=bwtf_time.strftime("%Y-%m-%d") if bwtf_time else None,
            bwtf_time=bwtf_time.strftime("%-I:%M %p") if bwtf_time else None,
            bwtf_raw=bwtf_raw,
            bwtf_value=bwtf_value,
            bwtf_exceeds=bwtf_exceeds,
            city_source=city["source"] if city else None,
            city_date=city["date"].strftime("%Y-%m-%d") if city and city["date"] else None,
            city_raw=city["raw"] if city else None,
            city_value=city_value,
            city_exceeds=city_exceeds,
            city_raws=list(city["raws"]) if city else [],
            city_n=city["n"] if city else 0,
            sfpuc_status=sfpuc_status.get(sfpuc_name) if sfpuc_name else None,
            both_have=both_have,
            agree=agree,
            value_delta=value_delta,
            day_gap=day_gap,
            site_key=site_key,
            dual=dual,
            bwtf_site=bwtf_site,
        )

    rows: list[ComparisonRow] = []
    bwtf_sites = lab.sites if lab else []
    for site in bwtf_sites:
        sfpuc_name = BWTF_TO_SFPUC_NAME.get(site.name)
        sources = SFPUC_TO_SFGOV_SOURCES.get(sfpuc_name, []) if sfpuc_name else []
        # BWTF value for the selected analyte (only Enterococcus is reported here).
        rows.append(row_for(site.name, site.latitude, site.longitude, sources, sfpuc_name,
                            _bwtf_sample_for(site, cfg["bwtf_substance"]),
                            site_key=sources[0] if sources else "bwtf:" + site.name,
                            dual=bool(sources), bwtf_site=True))
    covered = {r.site_key for r in rows}
    for sid, st in sorted(STATIONS.items(), key=lambda kv: (kv[1].group, kv[1].name)):
        if sid in covered:
            continue
        rows.append(row_for(st.name, st.lat, st.lon, [sid], st.sfpuc_name, (None, None, None),
                            site_key=sid, dual=False, bwtf_site=False))

    # The head-to-head summary is about the BWTF lab's sites, as it always was.
    lab_rows = [r for r in rows if r.bwtf_site]
    comparable = [r for r in lab_rows if r.both_have]
    summary = {
        "site_count": len(lab_rows),
        "all_site_count": len(rows),
        "comparable_count": len(comparable),
        "agree_count": sum(1 for r in comparable if r.agree),
        "disagree_count": sum(1 for r in comparable if r.agree is False),
        "bwtf_exceed_count": sum(1 for r in lab_rows if r.bwtf_exceeds),
        "city_exceed_count": sum(1 for r in lab_rows if r.city_exceeds),
        "max_day_gap": max((r.day_gap for r in comparable if r.day_gap is not None), default=None),
    }

    return {
        "generated_at": utc_iso(),
        "standard": {"analyte": cfg["label"], "code": analyte, "single_sample_max": limit, "units": "MPN/100mL"},
        "analytes": [{"code": code, "label": meta["label"]} for code, meta in ANALYTES.items()],
        "bwtf_measures": analyte == "ENTERO",   # whether BWTF has data for this analyte
        "bwtf_available": lab is not None,
        "lab_name": lab.name if lab else None,
        "summary": summary,
        "rows": [r.to_dict() for r in rows],
    }


def _pct_of_limit(value: float, limit: Optional[float]) -> Optional[float]:
    """A result as a percentage of the limit that applies to it (100 = at the limit)."""
    return round(value / limit * 100, 1) if limit else None


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
    days: Optional[int] = None,
    analyte: str = DEFAULT_ANALYTE,
    start: str = "",
    end: str = "",
) -> dict:
    """Per-site time series for one analyte from both sources (JSON-serializable).

    ``site_name`` is anything resolve_site accepts (a station id, "bwtf:<name>",
    or a BWTF site name), so city-only stations graph too — with an empty BWTF
    series, as the coliforms have for every site. The window is [start, end]
    (default the last DEFAULT_DAYS; ``days`` is the old caller's shorthand).
    Point: {date, ts, value, raw}.
    """
    bwtf_client = bwtf_client or SFBWTFClient()
    sf_gov_monitor = sf_gov_monitor or SFWaterQualityMonitor()
    analyte = resolve_analyte(analyte)
    cfg = ANALYTES[analyte]
    site = resolve_site(site_name)
    if days and not start:
        since, until = now_pacific_naive() - timedelta(days=days), now_pacific_naive()
    else:
        since, until = parse_range(start, end)
    until_end = until + timedelta(days=1)

    bwtf_series = []
    for rec in (bwtf_client.fetch_history(since=since, max_pages=40) if site["bwtf_name"] else []):
        if rec["site_name"] != site["bwtf_name"] or rec["substance"] != cfg["bwtf_substance"]:
            continue
        when, value = rec["collection_time"], rec["result_value"]
        if when is None or value is None or when > until_end:
            continue
        bwtf_series.append({
            "date": when.strftime("%Y-%m-%d"),
            "ts": int(when.timestamp() * 1000),
            "value": value,
            "raw": rec["result_raw"],
        })
    bwtf_series.sort(key=lambda p: p["ts"])

    by_source = _fetch_city_series(sf_gov_monitor, site["sources"], since, until, analyte)
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
        "site": site["name"],
        "site_key": site["key"],
        "bwtf_name": site["bwtf_name"],
        "bwtf_sampled": site["bwtf_name"] is not None,
        "dual": site["dual"],
        "analyte": cfg["label"],
        "code": analyte,
        "standard": cfg["limit"],
        "units": "MPN/100mL",
        "start": since.strftime("%Y-%m-%d"),
        "end": until.strftime("%Y-%m-%d"),
        "days": (until - since).days,
        "city_source": city_source,
        "bwtf": bwtf_series,
        "city": city_series,
        "paired": paired,
    }


def main():
    """Print the comparison as a text table."""
    import argparse
    parser = argparse.ArgumentParser(description="BWTF vs city comparison")
    parser.add_argument("--analyte", default=DEFAULT_ANALYTE, choices=list(ANALYTES))
    args = parser.parse_args()

    data = build_comparison(analyte=args.analyte)
    std = data["standard"]
    s = data["summary"]
    print(f"BWTF ({data['lab_name']}) vs City — {std['analyte']} (limit {std['single_sample_max']} MPN/100mL)")
    print(f"Generated {data['generated_at'][:19]} | BWTF measures this: {data['bwtf_measures']}\n")
    print(f"{'Site':32s} {'BWTF':>14s} {'City (SFPUC)':>16s} {'Posting':>8s}  Agree")
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
