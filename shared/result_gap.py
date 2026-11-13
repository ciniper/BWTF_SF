"""The morning gap between a posting change and the sample behind it.

SFPUC's beach feed (getBeaches) carries two records per station that move on
their own: the *posting* (``posted`` + ``p_color``, the sign) and the *sample*
(``sample_date`` + ``s_color``, the newest graded sample). On a result day the
posting goes up or comes down at about 8:10–8:40 AM, the new sample date reaches
the map between about 10:30 AM and 3 PM, and the numbers reach DataSF around
11 PM (Online Postings Timeline, Oct–Nov 2026: every posting and clear since
October followed that order, gaps of 2h41 to 6h36). In that first window the
feed pairs today's posting with last week's sample, so a posted beach reads
"sampled Nov 2" over a sample that passed.

``pending_results`` names the stations in that window: their latest status
change was between "safe" and "posted" (a discharge flag is not sample-driven),
it happened within ``MAX_HOURS``, and it came after the feed first showed the
sample date it still shows (``feed_sample_dates``, migration 018). Without that
table it falls back to the dates alone: a change made more than a day after the
shown collection day was not caused by it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_HOURS = 36   # the map has always caught up the same day; past this we stop claiming a sample is unpublished
SAMPLE_CHANGES = {("ok", "posted"): "posted", ("posted", "ok"): "cleared"}   # the transitions a lab result makes


def _ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def latest_changes(rows: list[dict]) -> dict[str, dict]:
    """{SFPUC station id: {"at", "from", "to"}} — each station's newest transition in
    alert_log rows (``created_at`` + ``transitions``, the timeline's shape)."""
    from features.cso_history.page import _row_transitions
    out: dict[str, dict] = {}
    for row in rows:
        at = _ts(row.get("created_at"))
        if at is None:
            continue
        for t in _row_transitions(row):
            sid = str(t.get("station_id") or "")
            if sid and (sid not in out or at >= out[sid]["at"]):
                out[sid] = {"at": at, "from": t.get("from"), "to": t.get("to")}
    return out


def candidates(changes: dict[str, dict], now: datetime) -> dict[str, dict]:
    """The changes a lab result makes (safe ↔ posted) that are recent enough to still be waiting."""
    cutoff = now - timedelta(hours=MAX_HOURS)
    return {sid: c for sid, c in changes.items() if (c.get("from"), c.get("to")) in SAMPLE_CHANGES and c["at"] >= cutoff}


def pending_results(changes: dict[str, dict], shown_dates: dict[str, str], first_seen: dict[tuple[str, str], datetime] | None,
                    now: datetime) -> dict[str, dict]:
    """{SFPUC station id: {"at": ISO UTC, "change": "posted" | "cleared"}} for every station whose
    latest posting change is not explained by the sample the feed still shows.

    ``changes``: latest_changes(); ``shown_dates``: {station id: ISO sample date the feed shows};
    ``first_seen``: {(station id, ISO sample date): when the feed first showed it}, None when unknown."""
    out: dict[str, dict] = {}
    for sid, c in candidates(changes, now).items():
        shown = shown_dates.get(sid)
        if not shown:
            continue
        seen = (first_seen or {}).get((sid, shown))
        if seen is not None:
            waiting = c["at"] > seen
        else:   # no tick for the shown date: a change more than a day after collection was not that sample's doing
            waiting = c["at"].astimezone(PACIFIC).date() > date.fromisoformat(shown) + timedelta(days=1)
        if waiting:
            out[sid] = {"at": c["at"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "change": SAMPLE_CHANGES[(c["from"], c["to"])]}
    return out


# ─── Supabase reads (the board calls these inside its time budget) ───────────

def fetch_changes(now: datetime | None = None) -> dict[str, dict]:
    """Latest transition per station over the last MAX_HOURS, from the watcher's alert_log."""
    from shared import supabase as sb
    from shared.alert_log import REALTIME_SOURCES
    if not sb.is_configured():
        return {}
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=MAX_HOURS)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = sb.select("alert_log", {
        "select": "created_at,event_type,station_ids,station_names,transitions:results->transitions",
        "source": f"in.({','.join(REALTIME_SOURCES)})",
        "simulated": "eq.false",
        "created_at": f"gte.{since}",
        "order": "created_at.asc",
        "limit": "500",
    })
    return latest_changes(rows or [])


def fetch_first_seen(station_ids: list[str]) -> dict[tuple[str, str], datetime] | None:
    """{(station id, ISO sample date): first tick that showed it} for these stations;
    None when the table is not there (the date rule then decides)."""
    from shared import supabase as sb
    if not station_ids or not sb.is_configured():
        return None
    try:
        rows = sb.select("feed_sample_dates", {
            "select": "station_id,sample_date,first_seen_at,approx",
            "station_id": f"in.({','.join(sorted(station_ids))})",
            "order": "sample_date.desc",
            "limit": "200",
        })
    except Exception:
        return None
    out = {}
    for r in rows or []:
        fs = _ts(r.get("first_seen_at"))
        if fs is not None and not r.get("approx"):   # a backfilled row knows the day, not the tick
            out[(str(r["station_id"]), str(r["sample_date"])[:10])] = fs
    return out
