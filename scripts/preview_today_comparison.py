#!/usr/bin/env python3
"""Compare real Today templates using one shared, explicitly illustrative board.

Run with the app's Python: python scripts/preview_today_comparison.py
Open http://127.0.0.1:8088/compare-today/proposal
The main and Brian views come from local Git refs; proposal uses the worktree.
The map, controls, static assets and layer API routes are the application's own.
"""
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import abort, redirect, request
from jinja2 import ChoiceLoader, DictLoader
from app.wsgi import app
from app.landing import today_board
from shared.zones import ZONES

REFS = {"main": "origin/main", "brian": "origin/design/today-map-controls", "proposal": None}
TEMPLATES = ("today/page.html", "_today_board.html")
ENVIRONMENTS = {}
for name, ref in REFS.items():
    templates = {}
    for template in TEMPLATES:
        path = "app/templates/" + template
        templates[template] = subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True) if ref else (ROOT / path).read_text()
    ENVIRONMENTS[name] = app.jinja_env.overlay(loader=ChoiceLoader([DictLoader(templates), app.jinja_env.loader]))

# All three templates receive the same board so data changes cannot skew the comparison.
stations = [SimpleNamespace(station_id=sid, station_name=st.name,
                           status=SimpleNamespace(value="safe"), has_cso=False,
                           sample_date=datetime(2026, 11, 2))
            for zone in ZONES.values() for sid, st in zip(zone.station_ids, zone.stations)]
board = today_board(stations, {"zones": {key: 0 for key in ZONES}}, "city 11/2 · Surfrider 10/22", now=datetime(2026, 11, 9, 9, 0))


@app.get("/compare-today/<variant>")
def compare_today(variant):
    if variant not in REFS:
        abort(404)
    context = {"board": board, "generated": "Nov 9, 2026 · comparison sample", "canonical": None}
    app.update_template_context(context)
    html = ENVIRONMENTS[variant].get_template("today/page.html").render(context)
    links = " ".join(f'<a href="/compare-today/{key}" aria-current="{str(key == variant).lower()}" style="color:inherit;padding:5px 9px;font-weight:{700 if key == variant else 400}">{label}</a>' for key, label in [("main", "Main"), ("brian", "Brian"), ("proposal", "Proposal")])
    bar = '<nav aria-label="Local comparison" style="background:#edf3f7;color:#263742;padding:7px 14px;font:13px/1.5 sans-serif;display:flex;align-items:center;flex-wrap:wrap;gap:4px">' + links + '<span>Local comparison · identical sample data</span></nav>'
    return html.replace("<body>", "<body>" + bar, 1)


@app.get("/compare-today")
def compare_index():
    return redirect("/compare-today/proposal" + ("?" + request.query_string.decode() if request.query_string else ""))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8088, debug=False)
