"""Today — the Main page's board as a tab of its own, with room to experiment
(Chase, 2026-09-29: "a mimic of the main page hero with some extra features").

``/today``                the Today board (app/landing.board_context → the shared
                          _today_board.html macros) plus an experimental-layers row.
``/api/today/surfrider``  the first extra: Surfrider's latest Enterococcus result at
                          every site the volunteers sample, with coordinates, graded by
                          the shared rule — drawn on the map as diamonds when toggled on.
                          Experimental because the city does not post on these; they are
                          sampled on the volunteers' own schedule.

Each route handler returns ``(status, content_type, body_bytes)``.
"""
from __future__ import annotations

import json
from typing import Optional

from flask import render_template

from app.landing import board_context
from features.comparison.bwtf_api import SFBWTFClient
from features.comparison.comparison import BWTF_TO_SFPUC_NAME, resolve_site
from shared.sfpuc_api import SFPUCRealTimeAPI
from shared.standards import ENTERO_CAUTION, STANDARDS, exceeds

try:
    from shared.weather_tides import EnvironmentalContext
except Exception:  # noqa: BLE001 — weather/tides are optional, as in app/wsgi.py
    EnvironmentalContext = None


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


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
        date = s.latest_time.strftime("%Y-%m-%d") if s.latest_time else ""
        newest = max(newest, date)
        sites.append({
            "name": s.name, "city_name": BWTF_TO_SFPUC_NAME.get(s.name), "key": key,
            "lat": s.latitude, "lon": s.longitude,
            "value": v, "raw": s.entero_raw, "date": date,
            "time": s.latest_time.strftime("%H:%M") if s.latest_time else "",
            "over": bool(over), "caution": bool(v is not None and not over and v >= ENTERO_CAUTION),
            "report_url": s.report_url,
        })
    sites.sort(key=lambda x: x["name"])
    return {"ok": True, "sites": sites, "newest": newest or None, "limit": limit, "caution": ENTERO_CAUTION,
            "note": "Experimental: the volunteers' latest result per site, on their own schedule; the city does not post on these."}


def build_surfrider_layer(client: Optional[SFBWTFClient] = None) -> dict:
    client = client or SFBWTFClient(timeout=8)
    return surfrider_layer(client.fetch_lab())


def handle_page(query, body):
    sfpuc = SFPUCRealTimeAPI()
    env = EnvironmentalContext() if EnvironmentalContext else None
    try:
        ctx = board_context(sfpuc, env)
    except Exception as e:  # noqa: BLE001 — never blank the page on a flaky upstream
        return 500, "text/html; charset=utf-8", f"<!doctype html><meta charset='utf-8'><h1>Today</h1><pre>{e}</pre>".encode()
    html = render_template("today/page.html", board=ctx["board"], conditions=ctx["conditions"], generated=ctx["generated"])
    return 200, "text/html; charset=utf-8", html.encode()


def handle_api_surfrider(query, body):
    try:
        return _json(build_surfrider_layer())
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, status=502)


GET_ROUTES = {
    "/today": handle_page,
    "/api/today/surfrider": handle_api_surfrider,
}
