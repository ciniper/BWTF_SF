"""Collect (/learn/collect, 2026-10-18): the four sites Surfrider's volunteers sample, a short
collection procedure with its diagram, and the conditions for the field sheet, fetched for a
site and a time.

The sheet's fields and their choices are the ones the volunteers' entries in Surfrider's BWTF
database use (features/comparison/bwtf_api.py, the event history's ``weather`` block): air and
water temperature in °F, current weather (Sunny / Partly Cloudy / Overcast / Light Rain), a
precipitation yes/no, tide (high / low / incoming / outgoing), wave height in bins, and wind as an
8-point compass direction and mph. Each value comes from a public source and says which:

  tide        NOAA CO-OPS high/low predictions at the site's own prediction station; the level at the time
              (feet above MLLW, the average lower low) on NOAA's half-cosine between the turns
  air, sky,   Open-Meteo's forecast API at the site's pin (model hours, nearest hour)
  wind
  waves,      Open-Meteo's marine API, the nearest sea cell (it cannot see inside a breakwater,
  water temp  so a sheltered site's waves are left to the eye)
  rain        the forecast's NOAA daily gauge for the site's zone, via ACIS (features/today); the other
              gauge when the zone's has not reported (Oceanside reports a month at a time)

Pure helpers (pins, labels, tide state, the assembly) are tested offline; the fetchers are the
only network code, each fails on its own and leaves its fields marked unavailable.
"""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import requests
from flask import render_template

from features.today.page import GAUGES, ZONE_GAUGE, fetch_acis_daily
from shared.clock import now_pacific
from shared.zones import ZONES

# ── The sites ───────────────────────────────────────────────────────────────────────────
# Pins are Chase's (2026-10-18), as Google Maps writes them. ``station`` is the city's station at
# the same beach (shared/stations.py), which gives the zone and so the rain gauge. ``tide`` is the
# NOAA prediction station nearest the beach. Parking notes are drafts: docs/learn_facts_to_check.md.
SITES = [
    {"key": "ob-lincoln", "n": 1, "name": "Ocean Beach at Lincoln Way", "pin": "37°45'50.1\"N 122°30'42.0\"W", "station": "4605",
     "tide": ("9414275", "Ocean Beach"), "sheltered": False, "photo": None,
     "parking": "Street parking on Lincoln Way and the avenues just inland. The Great Highway south of Lincoln is now "
                "Sunset Dunes park, closed to cars."},
    {"key": "ob-vicente", "n": 2, "name": "Ocean Beach at Vicente St", "pin": "37°44'16.5\"N 122°30'29.6\"W", "station": "4603",
     "tide": ("9414275", "Ocean Beach"), "sheltered": False, "photo": None,
     "parking": "Street parking on Vicente St and the nearby avenues, then walk across Sunset Dunes (the old Great Highway) "
                "to the sand."},
    {"key": "china", "n": 3, "name": "China Beach", "pin": "37°47'17.4\"N 122°29'30.3\"W", "station": "4607",
     "tide": ("9414290", "the Golden Gate"), "sheltered": False, "photo": None,
     "parking": "A small free lot at the end of Sea Cliff Ave, above the beach; a path and stairs lead down."},
    {"key": "aquatic", "n": 4, "name": "Aquatic Park", "pin": "37°48'25.9\"N 122°25'22.3\"W", "station": "4613",
     "tide": ("9414305", "North Point"), "sheltered": True, "photo": None,
     "parking": "Metered street parking on Beach St, Van Ness Ave and around Ghirardelli Square; the Fort Mason lots "
                "are a short walk west."},
]
SITE = {s["key"]: s for s in SITES}

_DMS = re.compile(r"""(\d+)°(\d+)'([\d.]+)"([NSEW])""")


def pin_to_decimal(pin: str) -> tuple[float, float]:
    """'37°45'50.1"N 122°30'42.0"W' → (37.763917, -122.511667)."""
    parts = _DMS.findall(pin)
    if len(parts) != 2:
        raise ValueError(f"not a lat/lon pin: {pin!r}")
    out = []
    for d, m, s, hemi in parts:
        v = int(d) + int(m) / 60 + float(s) / 3600
        out.append(round(-v if hemi in "SW" else v, 6))
    return out[0], out[1]


def maps_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def zone_of(site: dict) -> str:
    return next(k for k, z in ZONES.items() if any(st.sfpuc_id == site["station"] for st in z.stations))


def sites_view() -> list[dict]:
    out = []
    for s in SITES:
        lat, lon = pin_to_decimal(s["pin"])
        out.append({**s, "lat": lat, "lon": lon, "maps": maps_url(lat, lon), "zone": ZONES[zone_of(s)].label})
    return out


# ── The sheet's vocabulary ──────────────────────────────────────────────────────────────

WAVE_BINS = [(1, "<1 ft"), (2, "1-2 ft"), (4, "2-4 ft"), (6, "4-6 ft"), (9, "6-9 ft"), (12, "9-12 ft")]
TIDE_TURN_MIN = 60   # within an hour of a high or low the sheet says "high" / "low"; between them, incoming / outgoing
RAIN_YES_IN = 0.01   # any measurable rain at the gauge


def wave_bin(ft: Optional[float]) -> Optional[str]:
    if ft is None:
        return None
    return next((label for top, label in WAVE_BINS if ft < top), "12+ ft")


def compass8(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg % 360) / 45 + 0.5) % 8]


def sky_label(code: Optional[int]) -> Optional[str]:
    """WMO weather code (Open-Meteo) → the sheet's current weather."""
    if code is None:
        return None
    if code in (0, 1):
        return "Sunny"
    if code == 2:
        return "Partly Cloudy"
    if code in (3, 45, 48):
        return "Overcast"
    if code in (51, 53, 55, 56, 57, 61, 66, 80):
        return "Light Rain"
    return "Rain"


def tide_state(hilo: list[dict], at: datetime) -> Optional[dict]:
    """``hilo``: [{"t": datetime, "type": "H"|"L", "ft": float}] ascending (naive Pacific). The state at
    ``at``: high/low within TIDE_TURN_MIN of a turn, otherwise incoming (low → high) or outgoing."""
    before = [h for h in hilo if h["t"] <= at]
    after = [h for h in hilo if h["t"] > at]
    if not before or not after:
        return None
    prev, nxt = before[-1], after[0]
    near = min((prev, nxt), key=lambda h: abs((h["t"] - at).total_seconds()))
    if abs((near["t"] - at).total_seconds()) <= TIDE_TURN_MIN * 60:
        state = "high" if near["type"] == "H" else "low"
    else:
        state = "incoming" if prev["type"] == "L" else "outgoing"
    return {"state": state, "prev": prev, "next": nxt}


def _clock(t: datetime) -> str:
    return t.strftime("%-I:%M %p")


def _day(d: date) -> str:
    return d.strftime("%b %-d")


def _nearest_hour(hourly: dict, at: datetime) -> Optional[int]:
    times = hourly.get("time") or []
    if not times:
        return None
    return min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i]) - at).total_seconds()))


def _pick(hourly: dict, key: str, i: Optional[int]):
    vals = hourly.get(key) or []
    return vals[i] if i is not None and i < len(vals) else None


def tide_height(hilo: list[dict], at: datetime) -> Optional[float]:
    """Feet above MLLW at ``at``, on a half-cosine between the turns either side: NOAA's own rule for the
    height between a high and a low (its tide tables' Table 3), the same at a subordinate station like Ocean
    Beach, which publishes only its highs and lows."""
    for a, b in zip(hilo, hilo[1:]):
        if a["t"] <= at <= b["t"]:
            k = (at - a["t"]).total_seconds() / max((b["t"] - a["t"]).total_seconds(), 1)
            return round(a["ft"] + (b["ft"] - a["ft"]) * (1 - math.cos(math.pi * k)) / 2, 2)
    return None


def assemble(site: dict, at: datetime, weather: Optional[dict], marine: Optional[dict],
             hilo: Optional[list], rain: Optional[dict]) -> dict:
    """Pure. The sheet's fields in the sheet's order, each {label, value, detail, source}; a source
    that failed (None) leaves its fields with value None."""
    rows = []

    def row(key, label, value, detail="", source="", blank=None, **extra):   # blank: what to show in place of a value left to the volunteer
        rows.append({"key": key, "label": label, "value": value, "detail": detail, "source": source, "blank": blank, **extra})

    wh = (weather or {}).get("hourly") or {}
    wi = _nearest_hour(wh, at)
    om = "Open-Meteo, the model hour nearest " + _clock(at)
    temp = _pick(wh, "temperature_2m", wi)
    row("air_temp", "Air temperature", None if temp is None else f"{round(temp)}°F", source=om)

    mh = (marine or {}).get("hourly") or {}
    mi = _nearest_hour(mh, at)
    sst = _pick(mh, "sea_surface_temperature", mi)
    row("water_temp", "Water temperature", None if sst is None else f"{round(sst * 9 / 5 + 32)}°F",
        "An estimate for the open water nearby: use your thermometer's reading if you have one.", "Open-Meteo marine model")

    row("current_weather", "Current weather", sky_label(_pick(wh, "weather_code", wi)),
        "" if _pick(wh, "cloud_cover", wi) is None else f"{_pick(wh, 'cloud_cover', wi)}% cloud cover", om)

    want = [at.date() - timedelta(days=k) for k in (3, 2, 1, 0)]   # the three days before and the day itself: a calendar view of "72 hours"
    own = ZONE_GAUGE[zone_of(site)]
    gauge, note = own, ""
    if rain is not None and not any((rain.get(own) or {}).get(d.isoformat()) is not None for d in want):
        other = next(g for g in GAUGES if g != own)   # Oceanside reports a month at a time (2026-10): read Downtown until it does
        if any((rain.get(other) or {}).get(d.isoformat()) is not None for d in want):
            gauge, note = other, f" The {own} gauge hasn't reported these days yet, so this is {other}'s."
    src = f"NOAA {gauge} gauge, daily totals via ACIS"
    got = (rain or {}).get(gauge) or {}
    known = [got[d.isoformat()] for d in want if got.get(d.isoformat()) is not None]
    if not known:
        row("precipitation", "Rain in the past 72 hours", None, "" if rain is None else "Neither gauge has reported these days yet.", src)
    else:
        total = round(sum(known), 2)
        detail = f"{total:.2f} in, {_day(want[0])} to {_day(want[-1])}"
        late = [_day(d) for d in want if got.get(d.isoformat()) is None]
        if late:
            detail += f" (not reported yet: {', '.join(late)})"
        row("precipitation", "Rain in the past 72 hours", "Yes" if total >= RAIN_YES_IN else "No", detail + "." + note, src)

    st, where = site["tide"]
    ts = tide_state(hilo, at) if hilo else None
    level = tide_height(hilo, at) if hilo else None
    day0 = datetime(at.year, at.month, at.day)
    mins = lambda t: round((t - day0).total_seconds() / 60)
    steps = [day0 + timedelta(minutes=30 * i) for i in range(49)]
    pts = [[mins(t), tide_height(hilo, t)] for t in steps] if hilo else []
    shape = None if level is None else {   # the day's curve for the card and the saved image: every 30 minutes, the turns, and the moment
        "pts": [p for p in pts if p[1] is not None],
        "turns": [{"m": mins(h["t"]), "ft": h["ft"], "type": h["type"]} for h in hilo if h["t"].date() == at.date()],
        "at": mins(at), "level": level}
    src = f"NOAA tide predictions, {where} ({st})"
    if ts:
        nm = lambda h: ("high" if h["type"] == "H" else "low") + f" {_clock(h['t'])} ({h['ft']:.1f} ft)"
        lead = "" if level is None else f"{level:.1f} ft above the lowest lows at {_clock(at)}. "
        first = nm(ts["prev"])
        row("tide", "Tide", ts["state"].capitalize(), lead + f"{first[0].upper() + first[1:]}, then {nm(ts['next'])}.", src,
            level=None if level is None else f"{level:.1f} ft", curve=shape)
    else:
        row("tide", "Tide", None, source=src, level=None if level is None else f"{level:.1f} ft", curve=shape)

    wv = _pick(mh, "wave_height", mi)
    if site["sheltered"]:
        row("wave_height", "Wave height", None,
            "Check by eye: the wave model can't see inside the breakwater. Volunteers here usually log <1 ft."
            + ("" if wv is None else f" Outside, in the Gate: {wave_bin(wv * 3.28084)}."), "Open-Meteo marine model", blank="Check by eye")
    else:
        row("wave_height", "Wave height", None if wv is None else wave_bin(wv * 3.28084),
            "" if wv is None else f"{wv * 3.28084:.1f} ft significant wave height", "Open-Meteo marine model")

    wd, ws = _pick(wh, "wind_direction_10m", wi), _pick(wh, "wind_speed_10m", wi)
    row("wind_direction", "Wind direction", compass8(wd), "" if wd is None else f"from {round(wd)}°", om)
    row("wind_speed", "Wind speed", None if ws is None else f"{round(ws)} mph", source=om)

    return {"ok": True, "site": site["key"], "name": site["name"], "at": at.strftime("%Y-%m-%dT%H:%M"),
            "when": at.strftime("%a %b %-d, ") + _clock(at), "rows": rows}


# ── Fetchers (the only network code) ────────────────────────────────────────────────────

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_MARINE = "https://marine-api.open-meteo.com/v1/marine"
NOAA_TIDES = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
TIMEOUT = 8
EARLIEST_DAYS = 90   # Open-Meteo's forecast API keeps about three months of past hours
LATEST_DAYS = 7      # and the marine forecast reaches about a week ahead


def fetch_weather(lat: float, lon: float, day: date) -> dict:
    r = requests.get(OPEN_METEO, params={"latitude": lat, "longitude": lon, "start_date": day.isoformat(), "end_date": day.isoformat(),
                                         "hourly": "temperature_2m,weather_code,cloud_cover,wind_speed_10m,wind_direction_10m",
                                         "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "America/Los_Angeles"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_marine(lat: float, lon: float, day: date) -> dict:
    r = requests.get(OPEN_METEO_MARINE, params={"latitude": lat, "longitude": lon, "start_date": day.isoformat(), "end_date": day.isoformat(),
                                                "hourly": "wave_height,sea_surface_temperature", "cell_selection": "sea",
                                                "timezone": "America/Los_Angeles"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_hilo(station: str, day: date) -> list[dict]:
    r = requests.get(NOAA_TIDES, params={"station": station, "product": "predictions", "interval": "hilo", "datum": "MLLW",
                                         "begin_date": (day - timedelta(days=1)).strftime("%Y%m%d"), "end_date": (day + timedelta(days=1)).strftime("%Y%m%d"),
                                         "time_zone": "lst_ldt", "units": "english", "format": "json", "application": "SFBeachWater"}, timeout=TIMEOUT)
    r.raise_for_status()
    preds = r.json().get("predictions") or []
    return [{"t": datetime.strptime(p["t"], "%Y-%m-%d %H:%M"), "type": p["type"], "ft": float(p["v"])} for p in preds]


def fetch_rain(site: dict, day: date) -> dict:
    """Both gauges, {name: {ISO day: inches | None}}: the site's own first, the other if it lags."""
    return {name: dict(fetch_acis_daily(g["sid"], (day - timedelta(days=3)).isoformat(), day.isoformat())) for name, g in GAUGES.items()}


def conditions(site: dict, at: datetime, fetchers: Optional[dict[str, Callable]] = None) -> dict:
    f = {"weather": fetch_weather, "marine": fetch_marine, "hilo": fetch_hilo, "rain": fetch_rain, **(fetchers or {})}
    lat, lon = pin_to_decimal(site["pin"])
    jobs = {"weather": (f["weather"], lat, lon, at.date()), "marine": (f["marine"], lat, lon, at.date()),
            "hilo": (f["hilo"], site["tide"][0], at.date()), "rain": (f["rain"], site, at.date())}
    got = {}
    with ThreadPoolExecutor(max_workers=4) as ex:   # every source at once: the slowest sets the wait, not the sum
        futs = {k: ex.submit(fn, *args) for k, (fn, *args) in jobs.items()}
        for k, fut in futs.items():
            try:
                got[k] = fut.result()
            except Exception:  # noqa: BLE001 — one source down leaves its fields blank, never the whole sheet
                got[k] = None
    return assemble(site, at, got["weather"], got["marine"], got["hilo"], got["rain"])


# ── Routes ──────────────────────────────────────────────────────────────────────────────

def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj).encode()


def parse_when(day: str, hm: str, now: Optional[datetime] = None) -> datetime:
    """'2026-10-18', '08:30' → naive Pacific datetime, inside the window the sources cover."""
    now = (now or now_pacific()).replace(tzinfo=None)
    at = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
    if not (now - timedelta(days=EARLIEST_DAYS) <= at <= now + timedelta(days=LATEST_DAYS)):
        raise ValueError(f"pick a time within the last {EARLIEST_DAYS} days or the next {LATEST_DAYS}")
    return at


def handle_conditions(query, body):
    site = SITE.get((query.get("site") or [""])[0])
    if not site:
        return _json({"ok": False, "error": "unknown site"}, 400)
    try:
        at = parse_when((query.get("date") or [""])[0], (query.get("time") or [""])[0])
    except ValueError as e:
        return _json({"ok": False, "error": str(e)}, 400)
    return _json(conditions(site, at))


def handle_page(query, body):
    now = now_pacific()
    html = render_template("learn/collect.html", sites=sites_view(), today=now.strftime("%Y-%m-%d"), now=now.strftime("%H:%M"),
                           earliest=(now - timedelta(days=EARLIEST_DAYS)).strftime("%Y-%m-%d"),
                           latest=(now + timedelta(days=LATEST_DAYS)).strftime("%Y-%m-%d"))
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {"/learn/collect": handle_page, "/api/learn/conditions": handle_conditions}
