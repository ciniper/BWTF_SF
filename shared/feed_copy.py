"""The board's minute-fresh copy of SFPUC's beach feed, read from Supabase (Chase, 2026-10-02: "I'm ok
with testing out the reading from supabase storage for the sfpuc feed").

The pg_cron watcher fetches SFPUC's getBeaches feed every minute and stores each station's entry, exactly
as the feed sent it, in ``feed_station_days.raw_last`` with ``last_seen_at`` (migrations 012/018). The
Today board reads that instead of calling SFPUC on every visit: SFPUC's feed takes ~0.8 s to answer and
was four-fifths of the page's server time; one Supabase query is a fraction of that.

The copy is only used when it is fresh — the watcher saw the feed within FRESH_SECONDS — and only the
stations seen at that newest tick count (a station the feed stopped sending drops out, as it would live).
Anything else (no Supabase, an error, no rows, a stale watcher) returns None and the board reads SFPUC
live exactly as before. This departs from "viewers read the APIs live" for the board alone, on purpose.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from shared import supabase as sb
from shared.clock import PACIFIC, now_utc

FRESH_SECONDS = 300       # the watcher ticks every minute; five minutes covers a few missed ticks
SAME_TICK_SECONDS = 180   # stations whose last sighting is this close to the newest tick were in it


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def latest_feed(now: Optional[datetime] = None, select: Optional[Callable] = None) -> Optional[dict]:
    """{"items": [the feed's station entries], "checked_at": the newest tick (aware)} or None."""
    if select is None:
        if not sb.is_configured():
            return None
        select = sb.select
    now = now or now_utc()
    since = (now.astimezone(PACIFIC).date() - timedelta(days=1)).isoformat()   # yesterday too: the first tick after midnight
    try:
        rows = select("feed_station_days", {"select": "station_id,last_seen_at,raw_last", "day": f"gte.{since}",
                                            "order": "last_seen_at.desc", "limit": "100"})
    except Exception:  # noqa: BLE001 — any trouble here just means reading SFPUC live
        return None
    latest: dict = {}
    for r in rows or []:
        if r.get("station_id") and r.get("last_seen_at") and isinstance(r.get("raw_last"), dict):
            latest.setdefault(r["station_id"], r)   # newest first: the first row per station is its latest
    if not latest:
        return None
    seen = {sid: _when(r["last_seen_at"]) for sid, r in latest.items()}
    newest = max(seen.values())
    if (now - newest).total_seconds() > FRESH_SECONDS:
        return None
    items = [r["raw_last"] for sid, r in latest.items() if (newest - seen[sid]).total_seconds() <= SAME_TICK_SECONDS]
    return {"items": items, "checked_at": newest} if items else None
