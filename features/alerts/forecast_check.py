"""Forecast check (/alerts/forecast-check, an Admin tab since 2026-11-11): what each morning's forecast said next to
what then happened, day by day, from the record migration 022 keeps (features/forecast/outcomes.py).

Three things on the page: is the record healthy (an issue day for every calendar day since it began — a gap is a
refresh that never ran, which nothing else surfaces); how each target day's forecast developed from five days out to
the morning of, with the rain it was fed, beside the SFPUC map's flags, the city's samples over standard and the
measured gauge rain; and the season's tally by lead at the Medium edge on the three fast rulers. The rulers are
interim and the tab says so: the protocol's T0 grade is grade_prospective.py → stages_build.t0_as_served against the
CIWQS ledger (STAGES_PROTOCOL.md §1–2). Behind the coordinators' passphrase with the rest of Admin (app/wsgi.py).
"""
from __future__ import annotations

import json

from features.forecast import outcomes as O

DAYS_DEFAULT, DAYS_MIN, DAYS_MAX = 60, 7, 180


def _days(query: dict) -> int:
    try:
        return min(DAYS_MAX, max(DAYS_MIN, int((query.get("days") or [str(DAYS_DEFAULT)])[0])))
    except ValueError:
        return DAYS_DEFAULT


def handle_api(query, body):
    try:
        return 200, "application/json", json.dumps(O.build(_days(query)), default=str).encode()
    except Exception as e:  # noqa: BLE001
        return 500, "application/json", json.dumps({"ok": False, "error": str(e)}).encode()


def render_page(data: dict, days: int) -> str:
    from flask import render_template
    return render_template("alerts/forecast_check.html", data=data, days=days, level=O.level, edges=O.EDGES)


def handle_page(query, body):
    days = _days(query)
    return 200, "text/html; charset=utf-8", render_page(O.build(days), days).encode()


GET_ROUTES = {
    "/alerts/forecast-check": handle_page,
    "/alerts/api/forecast-check": handle_api,
}
