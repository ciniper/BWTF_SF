"""About pages — how the records were obtained, and how the site is built.

``/records``       the standard operating procedure behind the three record
                   pages (Discharge Ledger, Beach Postings, Online Postings
                   Timeline): who publishes each record, the steps to get it
                   by hand, the script that automates it, cadence and gaps.
                   Freshness comes from the same manifests the record pages
                   read, so a refresh moves this page too.
``/architecture``  one diagram of the infrastructure — sources, Supabase,
                   Vercel, GitHub, Brevo, the people on the other end.

Both are static apart from the freshness lines. Serverless-safe: no threads,
no external calls. Each route handler returns ``(status, content_type,
body_bytes)`` — the contract ``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

from datetime import date, datetime

from flask import render_template

import features.discharges.page as discharges_page
import features.postings.page as postings_page


def _freshness() -> dict:
    """Last refresh / next due for the two hand-refreshed records; a missing
    or unreadable manifest just leaves the line out."""
    out = {}
    try:
        out["discharges"] = discharges_page._refresh()
        through = discharges_page._coverage()["through"]
        out["discharges"]["through"] = datetime.strptime(through, "%Y-%m").strftime("%b %Y")
    except Exception:  # noqa: BLE001
        pass
    try:
        p = postings_page._load()
        out["postings"] = dict(p["refresh"], first=p["first"])
        if p["known_through"]:
            out["postings"]["through"] = datetime.strptime(p["known_through"], "%Y-%m-%d").strftime("%b %Y")
    except Exception:  # noqa: BLE001
        pass
    today = date.today().isoformat()
    for block in out.values():
        block["overdue"] = bool(block.get("next_due")) and block["next_due"] < today
    return out


def handle_records(query, body):
    html = render_template("about/records.html", fresh=_freshness())
    return 200, "text/html; charset=utf-8", html.encode()


def handle_architecture(query, body):
    return 200, "text/html; charset=utf-8", render_template("about/architecture.html").encode()


GET_ROUTES = {
    "/records": handle_records,
    "/architecture": handle_architecture,
}
POST_ROUTES: dict = {}
