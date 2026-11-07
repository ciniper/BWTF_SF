"""Data (/data): the index the top bar's Data tab opens on (design/nav, 2026-11-06).

Two columns, one per group of the Data section (app/landing.py NAV_SECTIONS): lab results, and the
city's records. Each leads with its featured page (landing.DATA_FEATURED: the Site Report Card and
the Discharge Ledger) as a large card with a headline number, and lists the group's other pages under it.

The Ledger's number is counted here from the committed record (no lookups). The Report Card's comes
from DataSF, so the page asks ``/analysis/api/summary`` for it after it has drawn and shows the card
without a number if that fails: the index itself always answers at once. The route handler returns
``(status, content_type, body_bytes)`` — the contract ``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

from datetime import date, datetime

from flask import render_template

from app.landing import data_index
from shared.clock import today_pacific


def _volume(mg: float) -> str:
    """Million gallons in words, rounded as the Ledger's tiles round."""
    if mg >= 1000:
        return f"{mg / 1000:.2f} billion gallons"
    if mg >= 1:
        return f"{round(mg):,} million gallons"
    return f"{mg:.2f} million gallons"


def ledger_stat(events: list | None = None, through: str | None = None, today: date | None = None) -> dict | None:
    """The Discharge Ledger's headline: discharges and volume reported so far this calendar year, the
    window the Ledger itself opens on (its "1 year" preset: January 1 to the newest record).

    ``events`` are the Ledger's rows ([date, facility, outfall id, name, water, minutes, MG]) and
    ``through`` its coverage's last filed month ("YYYY-MM"); both default to the committed record.
    While no report for this year has been filed yet, the year of the last filed month stands in, named,
    so the card never reads "0 discharges" for a year nobody has reported on. None when there is no record."""
    if events is None or through is None:
        from features.discharges.page import _load
        payload = _load()
        events = payload["events"] if events is None else events
        through = payload["coverage"].get("through") if through is None else through
    if not through:
        return None
    year = (today or today_pacific()).year
    if through < f"{year}-01":
        year = int(through[:4])
    rows = [e for e in events if e[0].startswith(f"{year}-")]
    return {"year": year, "count": len(rows), "volume": _volume(sum(e[6] or 0 for e in rows)),
            "through": f"{datetime.strptime(through, '%Y-%m'):%b %Y}"}


def handle_page(query, body):
    try:
        ledger = ledger_stat()
    except Exception:  # noqa: BLE001 — the number is a garnish: a broken record file must not take the index down
        ledger = None
    html = render_template("data/index.html", groups=data_index(), ledger=ledger)
    return 200, "text/html; charset=utf-8", html.encode()


GET_ROUTES = {"/data": handle_page}
