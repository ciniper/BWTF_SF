"""One clock for the site.

Vercel runs in UTC; the beaches are in San Francisco. A bare ``datetime.now()`` on the
server is therefore seven or eight hours ahead of the beach — from 4 or 5 in the
afternoon Pacific it is already tomorrow — which is how the board came to say
"Wed Sep 30 · SFPUC map checked 5:40 AM" at 10:40 on a Tuesday night (Chase,
2026-09-29). Every server-rendered date or time, and every "today" in page logic,
reads the clock through here. ``zoneinfo`` follows daylight saving on its own.

- ``now_pacific()``        aware, for anything shown to a person
- ``today_pacific()``      the beach's calendar day
- ``now_pacific_naive()``  for code that compares with naive local timestamps
                            (NOAA tide predictions requested in lst_ldt, Open-Meteo hours)
- ``now_utc()`` / ``utc_iso()``  aware UTC for stored stamps and JSON — an offset in the
                            string means a browser's ``new Date()`` converts it correctly

tests/test_clock.py scans the served modules for bare clock reads.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def days_ago_text(then: date, today: date) -> str:
    """A past day in words a reader can weigh at once: "today", "yesterday", "15 days ago" (design/today-sample-age,
    2026-11-06: a date alone read as current — a map all green from results five months old). Always days, never
    weeks or months, so the number stays comparable with the sampling schedule and the stale threshold."""
    n = (today - then).days
    if n <= 0:
        return "today"
    if n == 1:
        return "yesterday"
    return f"{n} days ago"


def now_pacific() -> datetime:
    return datetime.now(PACIFIC)


def now_pacific_naive() -> datetime:
    return now_pacific().replace(tzinfo=None)


def today_pacific() -> date:
    return now_pacific().date()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return now_utc().isoformat(timespec="seconds")
