"""UI overflow (/alerts/ui-overflow, an Admin tab since 2026-11-04): what came off the Today page for the beta,
kept as Today drew it until it finds a home — the hub cards (the page directory, with their live facts), the Learn
bar and the rain and tide pills — and the Learn page's "A century and a half" timeline (app/templates/learn/_timeline.html,
its content still features/learn/page.py's). Behind the coordinators' passphrase with the rest of Admin (app/wsgi.py).

Chase, 2026-11-04: "get rid of all the clickable links under the 'statuses from SFPUC beach map' and the three pills
at the bottom and put it into a 'ui overflow' tab", then "move the 'A century and a half' section to UI overflow in
admin for now". Built by app/landing.directory_context.
"""
from __future__ import annotations

from flask import render_template

from app.landing import directory_context
from features.learn.page import PHOTO_CREDIT, TIMELINE

try:
    from shared.weather_tides import EnvironmentalContext
except Exception:  # noqa: BLE001 — weather/tides are optional, as in features/today/page.py
    EnvironmentalContext = None


def render_page(ctx: dict) -> str:
    return render_template("alerts/ui_overflow.html", hubs=ctx["hubs"], conditions=ctx["conditions"], generated=ctx["generated"],
                           timeline=TIMELINE, photo_credit=PHOTO_CREDIT)


def handle_page(query, body):
    env = EnvironmentalContext() if EnvironmentalContext else None
    return 200, "text/html; charset=utf-8", render_page(directory_context(env)).encode()


GET_ROUTES = {"/alerts/ui-overflow": handle_page}
