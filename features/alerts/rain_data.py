"""Admin rainfall source explorer: a reproducible, dated research snapshot.

No remote reads, model changes, or background polling on page load.
"""
import json
from functools import lru_cache
from pathlib import Path

from flask import render_template


@lru_cache(maxsize=1)
def snapshot():
    return json.loads((Path(__file__).parent / 'data' / 'rain_snapshot.json').read_text())


def handle_page(query, body):
    html = render_template('alerts/rain_data.html', rain=snapshot())
    return 200, 'text/html; charset=utf-8', html.encode()


GET_ROUTES = {'/alerts/rain-data': handle_page}
