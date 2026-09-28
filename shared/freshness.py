"""When is a hand-refreshed record due again? One rule for every page that
shows a "last refreshed" line (Discharge Ledger, Beach Postings).

The external records we mirror by hand — SFPUC's discharge reports on CIWQS,
the State's BeachWatch postings — are re-harvested on the quarterly run
(~Mar / Jun / Sep / Dec 15, see TODO "Regular CIWQS re-run"). A page shows the
last refresh date, the next due date and turns amber once DUE_DAYS have
passed, so the reminder lives where the data is looked at.
"""
from __future__ import annotations

from datetime import date, datetime

DUE_DAYS = 90            # a quarterly cadence; SFPUC files a month 4–6 weeks late, SF files postings months late
QUARTER_MONTHS = (3, 6, 9, 12)
RUN_DAY = 15
MIN_GAP_DAYS = 60        # a run less than this after a refresh is skipped — it would find nothing new


def parse_day(value) -> date | None:
    """'2026-09-24' or '2026-09-26T18:15:48+00:00' → date, else None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def next_quarterly_due(refreshed_at, due_days: int = DUE_DAYS) -> str | None:
    """The first quarterly run day (Mar/Jun/Sep/Dec 15) at least MIN_GAP_DAYS
    after the refresh — the run about a quarter later, never the one a
    fortnight away (a Sep 24 refresh → Dec 15; a Mar 1 refresh → Jun 15)."""
    d = parse_day(refreshed_at)
    if d is None:
        return None
    y, mo = d.year, d.month
    for _ in range(8):
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
        if mo in QUARTER_MONTHS and (date(y, mo, RUN_DAY) - d).days >= MIN_GAP_DAYS:
            return f"{y:04d}-{mo:02d}-{RUN_DAY:02d}"
    return None


def refresh_block(refreshed_at, due_days: int = DUE_DAYS, **extra) -> dict:
    """The payload block a page renders: refreshed_at (date), next_due, due_days, plus whatever the caller adds."""
    d = parse_day(refreshed_at)
    return {"refreshed_at": d.isoformat() if d else None, "next_due": next_quarterly_due(refreshed_at, due_days),
            "due_days": due_days, **extra}
