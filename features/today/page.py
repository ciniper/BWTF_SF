"""Today — the home page, served at ``/`` and at ``/today`` (alert emails link there): the board,
a Layers strip above it, and the page directory below it. Born 2026-09-29 as the Main page's
board with room to experiment; the Main page was deleted 2026-09-30 (Chase: "wholesale delete
the main page") and Today took the root 2026-10-01, with a canonical tag naming ``/``.

``/today``                the Today board (app/landing.board_context → the shared
                          _today_board.html macros), the Layers strip above it, and the
                          hub cards + Under the hood strip below it (render_page).
``/api/today/surfrider``  Surfrider's latest Enterococcus result at every site the
                          volunteers sample, with coordinates, graded by the shared
                          rule — drawn on the map as diamonds when toggled on.
``/api/today/rain``       the last three daily totals at the forecast's two NOAA gauges
                          (Downtown 047772, Oceanside 047767) via ACIS, and each zone's
                          gauge — the "why" behind postings.
``/api/today/outfalls``   the 34 permitted CSO outfalls with what each reported in the
                          trailing twelve months of public records (features/discharges),
                          and the beaches SFPUC posts when it fires (shared/outfalls).
``/api/today/replay``     what SFPUC's map showed at the last check of each of the past
                          seven days, per station (feed_station_days, migration 012).

Every layer is Experimental: the city does not post on any of it. Each route handler
returns ``(status, content_type, body_bytes)``; the layer builders are pure functions
over fetched rows so the tests run offline.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import requests
from flask import render_template

from app.landing import HOME_URL, UNDER_THE_HOOD, board_context, hubs_with_facts
from features.comparison.bwtf_api import SFBWTFClient
from features.comparison.comparison import BWTF_TO_SFPUC_NAME, resolve_site
from shared import supabase as sb
from shared.sfpuc_api import SFPUCRealTimeAPI
from shared.standards import ENTERO_CAUTION, STANDARDS, exceeds
from shared.zones import ZONES

try:
    from shared.weather_tides import EnvironmentalContext
except Exception:  # noqa: BLE001 — weather/tides are optional, as in app/wsgi.py
    EnvironmentalContext = None

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


# ── Surfrider results ──────────────────────────────────────────────────────────────────

def surfrider_layer(lab) -> dict:
    """Pure: a BWTFLab (fetch_lab) → the map layer payload. One entry per site the
    volunteers sample: name, the city's name for the same beach when there is one,
    the site key the other pages use, coordinates, the latest Enterococcus result
    graded by the shared single-sample rule (caution = at or above ENTERO_CAUTION
    but not over), the collection date and the public report link."""
    limit = STANDARDS["ENTERO"]["single_sample_max"]
    sites, newest = [], ""
    for s in (lab.sites if lab else []):
        v = s.entero_value
        over = exceeds("ENTERO", v)
        try:
            key = resolve_site(s.name)["key"]
        except Exception:  # noqa: BLE001
            key = f"bwtf:{s.name}"
        zone = zone_for_site(key, s.latitude, s.longitude)
        date_ = s.latest_time.strftime("%Y-%m-%d") if s.latest_time else ""
        newest = max(newest, date_)
        sites.append({
            "name": s.name, "city_name": BWTF_TO_SFPUC_NAME.get(s.name), "key": key, "zone": zone,
            "lat": s.latitude, "lon": s.longitude,
            "value": v, "raw": s.entero_raw, "date": date_,
            "time": s.latest_time.strftime("%H:%M") if s.latest_time else "",
            "over": bool(over), "caution": bool(v is not None and not over and v >= ENTERO_CAUTION),
            "report_url": s.report_url,
        })
    sites.sort(key=lambda x: x["name"])
    return {"ok": True, "sites": sites, "newest": newest or None, "limit": limit, "caution": ENTERO_CAUTION,
            "note": "Experimental: the volunteers' latest result per site, on their own schedule; the city does not post on these."}


def zone_for_site(key: str, lat, lon) -> Optional[str]:
    """The zone a Surfrider site belongs to: the zone of the city station it shares a beach
    with, else the zone of the nearest registry station (Bayview Hunters Point has no city
    twin). None without coordinates. The Today chips list the site under that zone."""
    for z in ZONES.values():
        if key in z.source_ids:
            return z.key
    if lat is None or lon is None:
        return None
    return min(((st.lat - lat) ** 2 + (st.lon - lon) ** 2, z.key) for z in ZONES.values() for st in z.stations)[1]


def build_surfrider_layer(client: Optional[SFBWTFClient] = None) -> dict:
    client = client or SFBWTFClient(timeout=8)
    return surfrider_layer(client.fetch_lab())


# ── Rain, the last three daily totals ──────────────────────────────────────────────────

ACIS_URL = "https://data.rcc-acis.org/StnData"
# The forecast's two NOAA daily gauges (features/forecast/src/collectors/historical.py
# RAIN_STATIONS); coordinates from the ACIS station metadata the model explorer records.
GAUGES = {
    "SF Downtown": {"sid": "047772", "lat": 37.7705, "lon": -122.4269},
    "SF Oceanside": {"sid": "047767", "lat": 37.728, "lon": -122.5052},
}
_BASIN_GAUGE = {"Westside": "SF Oceanside", "North Shore": "SF Downtown", "Central": "SF Downtown", "Southeast": "SF Downtown"}
ZONE_GAUGE = {z.key: _BASIN_GAUGE.get(z.basins[0], "SF Downtown") for z in ZONES.values()}   # as the forecast page's zone cards
RAIN_WINDOW_DAYS = 3          # "the last 72 hours": three daily totals
RAIN_ADVISORY_IN = 0.1        # shared/weather_tides.RAIN_THRESHOLD_INCHES — the amount SFPUC's 72-hour advice keys on


def _acis_value(v) -> Optional[float]:
    """ACIS daily precipitation: 'M' missing → None, 'T' trace → 0.0, a trailing 'A'
    marks an accumulated total (kept as its number)."""
    if v in (None, "", "M"):
        return None
    if v == "T":
        return 0.0
    try:
        return float(str(v).rstrip("A"))
    except ValueError:
        return None


def fetch_acis_daily(sid: str, sdate: str, edate: str, timeout: int = 8) -> list[tuple[str, Optional[float]]]:
    """[(ISO day, inches | None)] ascending for one gauge — the same request the
    historical collector makes, over a few days."""
    r = requests.post(ACIS_URL, json={"sid": sid, "sdate": sdate, "edate": edate,
                                      "elems": [{"name": "pcpn", "interval": "dly", "duration": "dly"}], "output": "json"}, timeout=timeout)
    r.raise_for_status()
    return [(str(day), _acis_value(val)) for day, val in (r.json().get("data") or [])]


def rain_layer(readings: dict[str, list[tuple[str, Optional[float]]]], today: date) -> dict:
    """Pure. ``readings``: gauge name → [(ISO day, inches | None)] up to today. Per gauge,
    the window is the three most recent daily totals the gauge reported — today's when
    ACIS already has it, otherwise the three days ending yesterday — so a gauge that has
    not reported today still shows a full three days. Each zone reads its own gauge."""
    gauges = {}
    for name, meta in GAUGES.items():
        rows = sorted((d, v) for d, v in (readings.get(name) or []) if d <= today.isoformat())
        window = [(d, v) for d, v in rows if v is not None][-RAIN_WINDOW_DAYS:]
        total = round(sum(v for _, v in window), 2) if window else None
        gauges[name] = {
            "sid": meta["sid"], "lat": meta["lat"], "lon": meta["lon"],
            "days": [{"date": d, "in": v} for d, v in rows[-(RAIN_WINDOW_DAYS + 1):]],
            "total_in": total, "n_days": len(window),
            "window": [window[0][0], window[-1][0]] if window else None,
        }
    zones = {zk: {"gauge": g, "total_in": gauges[g]["total_in"]} for zk, g in ZONE_GAUGE.items()}
    wettest = max((g["total_in"] or 0.0 for g in gauges.values()), default=0.0)
    return {"ok": True, "gauges": gauges, "zones": zones, "wettest_in": wettest, "advisory_in": RAIN_ADVISORY_IN,
            "note": "Experimental: NOAA daily gauge totals via ACIS, the rain the forecast reads. "
                    "A gauge that reports 0.00 through a wet day may be down, so the two gauges are shown separately."}


def build_rain_layer(fetch: Callable = fetch_acis_daily, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(_PACIFIC)
    today = now.date()
    sdate = (today - timedelta(days=10)).isoformat()   # a gauge can lag a week (Oceanside, Sep 2026); the window is still its three newest totals
    readings = {name: fetch(meta["sid"], sdate, today.isoformat()) for name, meta in GAUGES.items()}
    return rain_layer(readings, today)


# ── Outfalls, sized by the trailing year of reported discharge ─────────────────────────

def outfalls_layer(payload: dict, months: int = 12) -> dict:
    """Pure over the Discharge Ledger's payload (features/discharges/page._load):
    ``events`` rows are [event_date, facility, outfall_id, outfall_name, receiving_water,
    duration_min, volume_MG]; ``locations`` is the registry keyed by outfall id. The
    window is the trailing ``months`` ending at the newest public record, so the layer
    says the same thing in January as in August. Every registry outfall is listed, at
    zero when quiet, with the stations SFPUC posts when it fires."""
    events = payload.get("events") or []
    locations = payload.get("locations") or {}
    dates = sorted(str(e[0])[:10] for e in events if e and e[0])
    if not dates:
        return {"ok": False, "error": "no discharge records"}
    to = date.fromisoformat(dates[-1])
    frm = to - timedelta(days=int(round(365.25 * months / 12)))
    agg: dict[str, dict] = {}
    for e in events:
        d = str(e[0])[:10]
        if d <= frm.isoformat():
            continue
        a = agg.setdefault(str(e[2]), {"n": 0, "volume_mg": 0.0, "last": d, "unreported": 0})
        a["n"] += 1
        if e[6] is None:
            a["unreported"] += 1
        else:
            a["volume_mg"] += float(e[6])
        a["last"] = max(a["last"], d)
    sites = []
    for oid, loc in locations.items():
        a = agg.get(oid, {"n": 0, "volume_mg": 0.0, "last": None, "unreported": 0})
        sites.append({"id": oid, "name": loc.get("name"), "lat": loc.get("lat"), "lon": loc.get("lon"),
                      "basin": loc.get("basin"), "water": loc.get("water"), "stations": list(loc.get("stations") or []),
                      "n": a["n"], "volume_mg": round(a["volume_mg"], 2), "last": a["last"], "unreported": a["unreported"]})
    sites.sort(key=lambda s: (-s["volume_mg"], -s["n"], s["name"] or ""))
    return {"ok": True, "sites": sites, "window": {"from": frm.isoformat(), "to": to.isoformat(), "months": months},
            "max_volume_mg": max((s["volume_mg"] for s in sites), default=0.0),
            "coverage": (payload.get("coverage") or {}).get("note", ""), "source": payload.get("source"),
            "note": "Experimental: what each outfall reported to regulators in the trailing twelve months of public records. "
                    "Circle area follows volume; a ring means discharges with no volume reported."}


def build_outfalls_layer() -> dict:
    from features.discharges import page as discharges
    return outfalls_layer(discharges._load())


# ── Replay, the past week of the city's map ────────────────────────────────────────────

_REPLAY_STATUS = {"ok": "safe", "posted": "posted", "cso": "discharge"}
REPLAY_DAYS = 7
# The feed record keys stations by SFPUC's numeric id; the board's stations carry the lab/DataSF
# source id (zone.source_ids). Translate so the client can match rows to its markers.
FEED_TO_SOURCE = {reg.sfpuc_id: source for z in ZONES.values() for source, reg in zip(z.source_ids, z.stations)}


def replay_days(today: date, days: int = REPLAY_DAYS) -> list[str]:
    """ISO days oldest → today, ``days`` back."""
    return [(today - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]


def replay_layer(rows: list[dict], days: list[str], id_map: Optional[dict] = None) -> dict:
    """Pure over feed_station_days rows (station_id, day, status_last): per station, the
    status SFPUC's map showed at the last check of each day, in the board's vocabulary
    (safe / posted / discharge), keyed by the board's source id (``id_map``, default
    FEED_TO_SOURCE; ids it does not know pass through). Days with no row for a station
    are left out; the client shows them as not recorded."""
    id_map = FEED_TO_SOURCE if id_map is None else id_map
    stations: dict[str, dict[str, str]] = {}
    for r in rows:
        sid, day = str(r.get("station_id") or ""), str(r.get("day") or "")[:10]
        if not sid or day not in days:
            continue
        stations.setdefault(id_map.get(sid, sid), {})[day] = _REPLAY_STATUS.get(str(r.get("status_last") or "ok"), "safe")
    covered = [d for d in days if any(d in v for v in stations.values())]
    return {"ok": True, "days": days, "covered": covered, "stations": stations,
            "note": "Experimental: what SFPUC's map showed at the last check of each day, from this site's own "
                    "minute-by-minute record — nothing before the record began."}


def build_replay_layer(now: Optional[datetime] = None, days: int = REPLAY_DAYS) -> dict:
    if not sb.is_configured():
        return {"ok": False, "error": "the map's history is unavailable in this environment"}
    now = now or datetime.now(_PACIFIC)
    span = replay_days(now.date(), days)
    rows = sb.select("feed_station_days", {"select": "station_id,day,status_last", "day": f"gte.{span[0]}",
                                           "order": "day.asc", "limit": "5000"})
    return replay_layer(rows, span)


# ── routes ─────────────────────────────────────────────────────────────────────────────

def render_page(ctx: dict) -> str:
    """The home page from a board_context: the board, then the page directory that used to sit
    under the Main page's board — the three hub cards (live facts where they answered; no card
    row for Today itself) and the Under the hood strip."""
    return render_template("today/page.html", board=ctx["board"], conditions=ctx["conditions"], generated=ctx["generated"],
                           hubs=hubs_with_facts(ctx.get("facts"), skip=("/",)), hood=UNDER_THE_HOOD, canonical=HOME_URL)


def handle_page(query, body):
    sfpuc = SFPUCRealTimeAPI()
    env = EnvironmentalContext() if EnvironmentalContext else None
    try:
        ctx = board_context(sfpuc, env)
    except Exception as e:  # noqa: BLE001 — never blank the page on a flaky upstream
        return 500, "text/html; charset=utf-8", f"<!doctype html><meta charset='utf-8'><h1>Today</h1><pre>{e}</pre>".encode()
    return 200, "text/html; charset=utf-8", render_page(ctx).encode()


def _layer_route(build: Callable[[], dict]):
    def handler(query, body):
        try:
            out = build()
            return _json(out, status=200 if out.get("ok", True) else 502)
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "error": str(e)}, status=502)
    return handler


GET_ROUTES = {
    "/today": handle_page,
    "/api/today/surfrider": _layer_route(build_surfrider_layer),
    "/api/today/rain": _layer_route(build_rain_layer),
    "/api/today/outfalls": _layer_route(build_outfalls_layer),
    "/api/today/replay": _layer_route(build_replay_layer),
}
