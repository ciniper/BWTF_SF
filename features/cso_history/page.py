"""CSO Event Timeline — our own real-time detections, plotted per station.

A public read-only page (no passphrase — it exposes no subscriber data)
showing every posting / CSO event the watcher has detected since real-time
monitoring began: when each station escalated and (post-migration 006) when it
cleared. Data is the alert_log's transition records — source='watcher'
(2-minute thread, the first few days) and source='pg_shadow'/'pg_live'
(1-minute pg_cron path since 2026-08-16), simulated rows excluded.

Privacy: alert_log.results also carries recipient contact info for dispatch
rows. The PostgREST select here pulls ONLY ``results->transitions`` (station
id/name + from/to), so recipient data never even reaches this process, let
alone the response.

Event windows are assembled server-side (testable) from the transition
stream; the page renders them client-side like the other pages.

Since 2026-09-27 each station also carries a samples row: the day each lab
sample was collected (from the Supabase ``samples`` mirror of the city's
dataset) and, for results the production refresh picked up as the city
published them, how long until the result appeared online — the publish lag
the live-corrections rules assume is one day. Backfilled rows show the
collection day only. Together with the flags this makes the page the
"what did the public-facing sources show, and when" view. Each route
handler returns ``(status, content_type, body_bytes)`` — the contract
``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import render_template

from shared import supabase as sb
from shared.stations import STATIONS
from zoneinfo import ZoneInfo

from shared.alert_log import REALTIME_SOURCES as _SOURCES  # our real-time detections only
_SEVERITY = {"ok": 0, "posted": 1, "cso": 2}
_ROW_LIMIT = 5000
# An open window with no clear record and no live roster to consult is shown
# as "ongoing" only while young; older ones are "end not recorded".
_ASSUME_ONGOING_HOURS = 24
_PACIFIC = ZoneInfo("America/Los_Angeles")
_SAMPLE_LOOKBACK_DAYS = 21      # samples collected up to this long before monitoring began still show
_SFPUC_OF_SOURCE = {sid: s.sfpuc_id for sid, s in STATIONS.items()}    # DataSF station id → SFPUC feed id (the timeline's key)
_NAME_OF_SFPUC = {s.sfpuc_id: s.sfpuc_name for s in STATIONS.values()}


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _fetch_event_rows() -> list[dict]:
    """Real (non-simulated) transition rows, oldest first, recipient-free."""
    return sb.select("alert_log", {
        "select": "id,created_at,source,event_type,station_ids,station_names,"
                  "transitions:results->transitions",
        "source": f"in.({','.join(_SOURCES)})",
        "simulated": "eq.false",
        "order": "created_at.asc",
        "limit": str(_ROW_LIMIT),
    })


def _fetch_monitoring_since() -> str | None:
    """First real-time log row of any kind (simulated tests count — they prove
    the watch was on), i.e. when continuous detection began."""
    rows = sb.select("alert_log", {
        "select": "created_at",
        "source": f"in.({','.join(_SOURCES)})",
        "order": "created_at.asc",
        "limit": "1",
    })
    return _iso(_parse_ts(rows[0]["created_at"])) if rows else None


def _fetch_roster() -> dict[str, dict]:
    """Current per-station status from the live SFPUC feed (best-effort) —
    used to show event-less stations and to resolve open-ended windows."""
    try:
        from shared.sfpuc_api import SFPUCRealTimeAPI
        from features.alerts.watcher import classify
        return {s.station_id: {"station_name": s.station_name,
                               "status": classify(s)}
                for s in SFPUCRealTimeAPI().fetch_stations()}
    except Exception:
        return {}


def _fetch_sample_rows(since_day: str) -> list[dict]:
    """The samples mirror (migration 012/014) from ``since_day`` on."""
    return sb.select("samples", {
        "select": "station_id,sample_date,exceeds,first_seen_at,source",
        "sample_date": f"gte.{since_day}",
        "order": "sample_date.asc",
        "limit": str(_ROW_LIMIT),
    })


def build_sample_days(rows: list[dict]) -> dict[str, list[dict]]:
    """{SFPUC station id: [{date, elevated, n, source, first_seen, lag_days}]},
    one entry per station-day. ``elevated`` = any analyte over its limit that
    day; ``first_seen`` = when the mirror first saw the day's results (the
    earliest row); ``lag_days`` = the Pacific day first seen minus the
    collection day — only for days whose rows the production refresh picked up
    in real time (source ``refresh``); a day with any backfilled row has no
    honest lag and shows the collection day alone."""
    days: dict[tuple[str, str], dict] = {}
    for r in rows:
        sfpuc = _SFPUC_OF_SOURCE.get(str(r.get("station_id") or ""))
        day = str(r.get("sample_date") or "")[:10]
        if not sfpuc or len(day) != 10:
            continue
        e = days.setdefault((sfpuc, day), {"elevated": False, "first_seen": None, "source": "refresh", "n": 0})
        e["n"] += 1
        e["elevated"] = e["elevated"] or bool(r.get("exceeds"))
        fs = _parse_ts(r.get("first_seen_at"))
        if fs is not None and (e["first_seen"] is None or fs < e["first_seen"]):
            e["first_seen"] = fs
        if r.get("source") != "refresh":
            e["source"] = "backfill"
    out: dict[str, list[dict]] = {}
    for (sfpuc, day), e in sorted(days.items()):
        real = e["source"] == "refresh" and e["first_seen"] is not None
        lag = (e["first_seen"].astimezone(_PACIFIC).date() - datetime.strptime(day, "%Y-%m-%d").date()).days if real else None
        out.setdefault(sfpuc, []).append({"date": day, "elevated": e["elevated"], "n": e["n"], "source": e["source"],
                                          "first_seen": _iso(e["first_seen"]) if real else None, "lag_days": lag})
    return out


def _median(values: list) -> float | None:
    if not values:
        return None
    s = sorted(values)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _row_transitions(row: dict) -> list[dict]:
    """Per-station from/to for one log row. pg rows carry them in results;
    watcher rows (a list of dispatch results there) fall back to the row-level
    station arrays with to=event_type and an unknown 'from'."""
    transitions = row.get("transitions")
    if isinstance(transitions, list) and transitions:
        return transitions
    if row.get("event_type") not in ("posted", "cso"):
        return []
    ids = row.get("station_ids") or []
    names = row.get("station_names") or []
    return [{"station_id": sid,
             "station_name": names[i] if i < len(names) else "",
             "from": None, "to": row["event_type"]}
            for i, sid in enumerate(ids)]


def build_station_windows(rows: list[dict], roster: dict[str, dict],
                          now: datetime) -> list[dict]:
    """Fold the transition stream into per-station event windows.

    A window opens on a severity increase and closes on a clear to 'ok';
    intermediate moves (posted→cso, cso→posted) become segments. Duplicate
    detections from the parallel watcher/pg run coalesce (a same-or-lower
    escalation into an open window is a no-op), and a clear with no open
    window (e.g. a simulated event's teardown) is ignored.
    """
    stations: dict[str, dict] = {}

    def entry(sid: str, name: str) -> dict:
        st = stations.setdefault(sid, {"station_id": sid, "station_name": name,
                                       "windows": [], "_open": None})
        if name:
            st["station_name"] = name
        return st

    for row in sorted(rows, key=lambda r: str(r.get("created_at") or "")):
        ts = _parse_ts(row.get("created_at"))
        if ts is None:
            continue
        for t in _row_transitions(row):
            sid = t.get("station_id")
            if not sid:
                continue
            st = entry(sid, t.get("station_name") or "")
            to = t.get("to")
            frm = t.get("from")
            open_win = st["_open"]
            escalation = _SEVERITY.get(to, 0) > _SEVERITY.get(frm or "ok", 0)

            if escalation:
                if open_win is None:
                    st["_open"] = {"start": ts,
                                   "segments": [{"start": ts, "status": to}]}
                elif _SEVERITY.get(to, 0) > _SEVERITY.get(open_win["segments"][-1]["status"], 0):
                    open_win["segments"].append({"start": ts, "status": to})
                # same/lower severity into an open window: parallel-source dup
            elif open_win is not None:
                if to == "ok":
                    open_win["end"] = ts
                    st["windows"].append(open_win)
                    st["_open"] = None
                elif _SEVERITY.get(to, 0) < _SEVERITY.get(open_win["segments"][-1]["status"], 0):
                    open_win["segments"].append({"start": ts, "status": to})
            # clear with no open window: dangling end, ignore

    # Roster stations with no events still get a row (coverage is the story
    # during a clean dry season).
    for sid, info in roster.items():
        entry(sid, info.get("station_name") or "")

    out = []
    for st in stations.values():
        current = roster.get(st["station_id"], {}).get("status")
        windows = []
        for w in st["windows"] + ([st["_open"]] if st["_open"] else []):
            closed = "end" in w
            if closed:
                ongoing, end_recorded = False, True
            elif current in ("posted", "cso"):
                ongoing, end_recorded = True, False
            elif current == "ok":
                ongoing, end_recorded = False, False  # cleared before 006: end unknown
            else:  # no live roster to consult
                age = now - w["start"]
                ongoing = age < timedelta(hours=_ASSUME_ONGOING_HOURS)
                end_recorded = False
            windows.append({
                "start": _iso(w["start"]),
                "end": _iso(w.get("end")),
                "ongoing": ongoing,
                "end_recorded": end_recorded,
                "segments": [{"start": _iso(s["start"]), "status": s["status"]}
                             for s in w["segments"]],
            })
        out.append({"station_id": st["station_id"],
                    "station_name": st["station_name"] or st["station_id"],
                    "current_status": current,
                    "windows": windows})

    # Stations with events first (most recent event on top), then the quiet
    # ones alphabetically. ISO-Z strings sort chronologically.
    eventful = sorted((s for s in out if s["windows"]),
                      key=lambda s: max(w["start"] for w in s["windows"]),
                      reverse=True)
    quiet = sorted((s for s in out if not s["windows"]),
                   key=lambda s: s["station_name"].lower())
    return eventful + quiet


# ─── response helpers (same contract as the forecast routes) ─────────────────

def _html(body: str, status: int = 200):
    return status, "text/html; charset=utf-8", body.encode()


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


# ─── route handlers ──────────────────────────────────────────────────────────

def handle_page(query, body):
    return _html(render_template("cso_history/page.html"))


def handle_events(query, body):
    now = datetime.now(timezone.utc)
    if not sb.is_configured():
        return _json({"generated_at": _iso(now), "monitoring_since": None,
                      "stations": [], "event_count": 0,
                      "note": "event history is unavailable in this environment"})
    try:
        rows = _fetch_event_rows()
        monitoring_since = _fetch_monitoring_since()
    except Exception as exc:  # a flaky backend must not blank the page
        return _json({"generated_at": _iso(now), "monitoring_since": None,
                      "stations": [], "event_count": 0,
                      "note": f"event history temporarily unavailable ({type(exc).__name__})"})
    stations = build_station_windows(rows, _fetch_roster(), now)
    samples, sample_note = {}, None
    try:
        since_day = ((_parse_ts(monitoring_since) or now) - timedelta(days=_SAMPLE_LOOKBACK_DAYS)).date().isoformat()
        samples = build_sample_days(_fetch_sample_rows(since_day))
    except Exception as exc:  # the samples row is an addition; its absence must not blank the events
        sample_note = f"sample dates temporarily unavailable ({type(exc).__name__})"
    known = {s["station_id"] for s in stations}
    extra = [{"station_id": sid, "station_name": _NAME_OF_SFPUC.get(sid, sid), "current_status": None, "windows": []}
             for sid in samples if sid not in known]
    if extra:   # eventful stations stay on top; the quiet ones re-sort alphabetically
        eventful = [s for s in stations if s["windows"]]
        quiet = sorted([s for s in stations if not s["windows"]] + extra, key=lambda s: s["station_name"].lower())
        stations = eventful + quiet
    lags = [d["lag_days"] for v in samples.values() for d in v if d["lag_days"] is not None]
    return _json({
        "generated_at": _iso(now),
        "monitoring_since": monitoring_since,
        "stations": stations,
        "event_count": sum(len(s["windows"]) for s in stations),
        "samples": samples,
        "sample_lag": {"n": len(lags), "median_days": _median(lags),
                       "measured_since": "2026-09-27"},   # the mirror's first real-time pickup (migration 012)
        "sample_note": sample_note,
    })


GET_ROUTES = {
    "/cso-history": handle_page,
    "/cso-history/api/events": handle_events,
}
