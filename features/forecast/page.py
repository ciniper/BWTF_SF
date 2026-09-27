"""Forecast page — wraps the self-contained sf_sewage_forecast engine.

The forecaster (``live_dashboard.py`` + ``src/``) was moved here intact. Its
modules use absolute ``src.*`` imports and locate ``data/``/``config/`` relative
to ``__file__``, so we put this directory on ``sys.path`` and import it as-is —
no edits to the forecaster internals.

The ML engine (pandas/scikit-learn + the pickled models) is imported lazily so
the rest of the app runs even when those heavy deps aren't installed; the
forecast page then degrades to an "unavailable" notice.

Predictions are compute-on-visit: the latest snapshot lives in Supabase
(``forecast_predictions``, single row) and ``/forecast/api/data`` serves it
directly while it's fresh (<30 min). A stale/missing snapshot is recomputed by
the visit that finds it stale — ``refresh_started_at`` is a claim guard so
concurrent visitors never double-compute. There is no background refresh
thread anymore, which is what lets this run on serverless hosts.

Each route handler returns ``(status, content_type, body_bytes)`` — the simple
response contract ``app/wsgi.py`` dispatches.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import render_template

from shared import supabase as sb

# Treat this directory as the forecaster's project root (preserves its
# `from src.models...` imports and __file__-relative data/config paths).
_FORECAST_ROOT = Path(__file__).resolve().parent
if str(_FORECAST_ROOT) not in sys.path:
    sys.path.insert(0, str(_FORECAST_ROOT))

_engine = None            # the imported live_dashboard module (holds LIVE + HTML_TEMPLATE)
_engine_error = None      # human-readable reason the engine couldn't load


def _load_engine():
    """Import the forecaster lazily. Sets _engine or _engine_error (never raises)."""
    global _engine, _engine_error
    if _engine is not None or _engine_error is not None:
        return
    try:
        import live_dashboard as ld  # constructs ld.LIVE = LiveData() (loads .pkl models)
        _engine = ld
    except Exception as exc:  # missing deps, missing models, etc.
        _engine_error = f"{type(exc).__name__}: {exc}"


def is_available() -> bool:
    _load_engine()
    return _engine is not None


# ─── Supabase-cached predictions (compute-on-visit) ──────────────────────────

_TABLE = "forecast_predictions"
FRESH_SECONDS = 30 * 60   # a snapshot younger than this is served as-is
GUARD_SECONDS = 3 * 60    # a refresh claim older than this is abandoned (crashed worker)
_COLD_WAIT_SECONDS = 24   # how long a guard-losing visitor waits when there's NO snapshot yet


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cache_writable() -> bool:
    """Only the PRODUCTION host may write the shared cache row.

    Any machine with the Supabase key can read it, but a laptop running
    older (or newer, unreleased) code must never publish its snapshot to real
    visitors — it happened 2026-09-09. Vercel sets VERCEL_ENV=production on
    the deployed site; FORECAST_CACHE_WRITE=1 forces it (the cache tests).
    Everyone else computes in memory and serves that, leaving the row alone.
    """
    return os.environ.get("FORECAST_CACHE_WRITE") == "1" or os.environ.get("VERCEL_ENV") == "production"


def _serve_from_memory():
    """Compute in-process (if the engine hasn't within the window) and serve
    it without touching the shared row."""
    if not _memory_snapshot_fresh():
        _engine.LIVE.refresh()
    return _json(_with_meta(_engine.LIVE.get_snapshot(), _engine.LIVE.last_refresh,
                            stale_note="computed on this host; the shared cache is only written from production"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_row():
    """The single cache row, or None (missing table / network hiccup / no row)."""
    if not sb.is_configured():
        return None
    try:
        rows = sb.select(_TABLE, {"select": "*", "id": "eq.1"})
        return rows[0] if rows else None
    except Exception:
        return None


def _claim_refresh(now: datetime) -> bool:
    """Atomically claim the right to recompute. False = someone else owns it."""
    abandoned = _iso(now - timedelta(seconds=GUARD_SECONDS))
    try:
        won = sb.update(_TABLE, {
            "id": "eq.1",
            "or": f"(refresh_started_at.is.null,refresh_started_at.lt.{abandoned})",
        }, {"refresh_started_at": _iso(now)})
        return bool(won)
    except Exception:
        return False


def _release_claim() -> None:
    try:
        sb.update(_TABLE, {"id": "eq.1"}, {"refresh_started_at": None})
    except Exception:
        pass


def _store_snapshot(snap: dict, now: datetime) -> bool:
    try:
        clean = json.loads(json.dumps(snap, default=str))
        sb.update(_TABLE, {"id": "eq.1"},
                  {"snapshot": clean, "generated_at": _iso(now), "refresh_started_at": None})
        return True
    except Exception:
        _release_claim()
        return False


_PACIFIC = ZoneInfo("America/Los_Angeles")


def _forecast_date(snap: dict, now: datetime) -> str:
    """The Pacific calendar day a snapshot belongs to: the engine's own
    ``is_today`` day when present, else ``now`` in Pacific time."""
    for d in (snap.get("predictions") or {}).values():
        if isinstance(d, dict) and d.get("is_today") and d.get("date"):
            return str(d["date"])[:10]
    return now.astimezone(_PACIFIC).strftime("%Y-%m-%d")


def _record_history(snap: dict, now: datetime) -> bool:
    """forecast_history (migration 012): the day's first snapshot is the
    start-of-day forecast the grading uses, the last is kept too — one rpc,
    never raises (a missing table or a hiccup must not fail the refresh)."""
    try:
        clean = json.loads(json.dumps(snap, default=str))
        sb.rpc("bwtf_record_forecast", {"p_date": _forecast_date(snap, now),
                                        "p_snapshot": clean, "p_generated_at": _iso(now)})
        return True
    except Exception as e:  # noqa: BLE001
        print(f"forecast_history not recorded: {e}")
        return False


def _mirror_samples() -> int:
    """samples (migration 012): the lab results the engine just fetched for its
    window, DO NOTHING on the ones already there. Never raises."""
    try:
        from shared import samples_mirror
        rows = getattr(_engine.LIVE, "last_live_samples", None) or []
        return samples_mirror.mirror(rows) if rows else 0
    except Exception as e:  # noqa: BLE001
        print(f"samples not mirrored: {e}")
        return -1


def _with_meta(snap: dict | None, generated_at: datetime | None,
               refreshing: bool = False, stale_note: str | None = None) -> dict:
    out = dict(snap or {"last_refresh": None, "predictions": {},
                        "beach_status": [], "error": None, "thresholds": {}})
    out["generated_at"] = _iso(generated_at) if generated_at else None
    out["refreshing"] = refreshing
    if stale_note:
        out["stale_note"] = stale_note
    return out


def _memory_snapshot_fresh() -> bool:
    """True when the in-process engine already computed within the window
    (covers Supabase-less checkouts and the just-computed case)."""
    last = _engine.LIVE.last_refresh  # naive local time, set by the engine
    return bool(last) and (datetime.now() - last).total_seconds() < FRESH_SECONDS


def _compute_and_store(row, now: datetime):
    """Run the engine once; persist only a successful result. Returns a response."""
    _engine.LIVE.refresh()  # network pulls + model inference (~5–20 s)
    snap = _engine.LIVE.get_snapshot()
    stored_snap = row.get("snapshot") if row else None
    stored_at = _parse_ts(row.get("generated_at")) if row else None

    if snap.get("error") and not snap.get("predictions"):
        # Failed refresh: keep the stored snapshot, free the claim for a retry.
        if row is not None:
            _release_claim()
        if stored_snap:
            return _json(_with_meta(stored_snap, stored_at,
                                    stale_note=f"refresh failed: {snap['error']}"))
        return _json(_with_meta(snap, None))

    if row is not None:
        if _store_snapshot(snap, now):
            _record_history(snap, now)   # the production host is the only writer (migration 012)
            _mirror_samples()
    return _json(_with_meta(snap, now))


# ─── response helpers ────────────────────────────────────────────────────────

def _html(body: str, status: int = 200):
    return status, "text/html; charset=utf-8", body.encode()


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def _unavailable_html() -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<link rel="icon" href="/static/brand/favicon.ico">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forecast unavailable</title>
<style>
  body{{margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,'Segoe UI',Roboto,sans-serif}}
  .wrap{{max-width:640px;margin:0 auto;padding:48px 24px}}
  a{{color:#38bdf8}} code{{background:#1e293b;padding:2px 6px;border-radius:6px;color:#e2e8f0}}
  .card{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:24px;margin-top:18px}}
  h1{{color:#38bdf8;font-size:1.5em;margin:0 0 8px}}
</style></head>
<body><div class="wrap">
  <a href="/">← Back to dashboard</a>
  <h1>CSO Forecast — temporarily unavailable</h1>
  <p>The forecast engine needs the machine-learning dependencies, which don't appear to be installed in this environment.</p>
  <div class="card">
    <p style="margin:0 0 10px">Install the forecast extras and restart the server:</p>
    <code>pip install -r requirements.txt</code>
    <p style="margin:12px 0 0;color:#94a3b8;font-size:.9em">Details: {_engine_error or "engine not loaded"}</p>
  </div>
  <p style="margin-top:18px;color:#94a3b8">The alert and comparison pages don't need these extras and work normally.</p>
</div></body></html>"""


def _render_page() -> str:
    _load_engine()
    if _engine is None and _read_row() is None:
        return _unavailable_html()
    today = datetime.now().strftime("%Y-%m-%d")
    # The page shell lives in app/templates/forecast/page.html (extracted from the
    # engine's HTML_TEMPLATE with the /forecast/api namespacing + back-to-dashboard
    # button baked in); live_dashboard.py is left untouched. Predictions load
    # client-side via the /forecast/api/* endpoints.
    return render_template(
        "forecast/page.html",
        min_date="2016-03-19",   # Poo Bot archive floor (discharges + samples); DataSF bacteria from 2020-07-27
        max_date=today,
        default_date=today,
        zones_json=json.dumps(_zones_for_template()),
        groups_json=json.dumps(_groups_for_template()),
    )


def _zones_for_template() -> list:
    """Zone cards' metadata from the shared registries (no hand-typed names)."""
    from shared.zones import ZONES
    gauge = {"Westside": "SF Oceanside", "North Shore": "SF Downtown",
             "Central": "SF Downtown", "Southeast": "SF Downtown"}
    return [{"key": z.key, "label": z.label,
             "stations": [s.name for s in z.stations],
             "basins": list(z.basins),
             "gauge": gauge[z.basins[0]]} for z in ZONES.values()]


def _groups_for_template() -> dict:
    """zone key → stage-2 group names, so old cached snapshots without a
    `zones` field can still be presented by zone."""
    try:
        from src.models.groups import ZONE_GROUPS
        return dict(ZONE_GROUPS)
    except Exception:
        return {"ocean": ["Ocean Beach"], "baker_china": ["Baker-China"],
                "north": ["Crissy Field", "Aquatic Park"], "east": ["Southeast", "Mission Creek"]}


# ─── route handlers ──────────────────────────────────────────────────────────

def handle_page(query, body):
    return _html(_render_page())


def _require_engine():
    _load_engine()
    if _engine is None:
        return _json({"error": _engine_error or "forecast engine unavailable"}, status=503)
    return None


def handle_data(query, body):
    now = _utcnow()
    row = _read_row()
    stored_snap = row.get("snapshot") if row else None
    stored_at = _parse_ts(row.get("generated_at")) if row else None

    # 1. Fresh stored snapshot → serve instantly, no engine work at all.
    if stored_snap and stored_at and (now - stored_at).total_seconds() < FRESH_SECONDS:
        return _json(_with_meta(stored_snap, stored_at))

    # 2. Stale or missing → the engine has to run. An engine-less host can
    #    still serve whatever snapshot another host stored.
    _load_engine()
    if _engine is None:
        if stored_snap:
            return _json(_with_meta(stored_snap, stored_at,
                                    stale_note="forecast engine unavailable on this host; "
                                               "showing the last stored forecast"))
        return _json({"error": _engine_error or "forecast engine unavailable"}, status=503)

    # 3. This process computed recently (Supabase-less checkout, or the row
    #    vanished mid-flight) → serve memory rather than recompute per visit.
    if row is None and _memory_snapshot_fresh():
        # _iso() treats naive datetimes as local time, matching how the engine
        # stamps last_refresh.
        return _json(_with_meta(_engine.LIVE.get_snapshot(), _engine.LIVE.last_refresh))

    # 3b. Not the production host → never claim, never store; memory only.
    if not _cache_writable():
        return _serve_from_memory()

    # 4. Claim the guard (only meaningful when the cache row exists).
    if row is not None and not _claim_refresh(now):
        if stored_snap:
            # Another visitor is computing; stale data now beats a spinner.
            return _json(_with_meta(stored_snap, stored_at, refreshing=True))
        # Cold start with a concurrent computer: briefly wait for their result.
        deadline = time.monotonic() + _COLD_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(2)
            retry = _read_row()
            if retry and retry.get("snapshot"):
                return _json(_with_meta(retry["snapshot"], _parse_ts(retry.get("generated_at"))))
        return _json(_with_meta(None, None, refreshing=True))

    # 5. We own the refresh: compute once, store, serve.
    return _compute_and_store(row, now)


def handle_refresh(query, body):
    """Force a recompute (manual button today; the pg_cron ping target later).
    Still honors the claim guard so a stampede can't double-compute."""
    err = _require_engine()
    if err:
        return err
    if not _cache_writable():
        _engine.LIVE.refresh()
        return _serve_from_memory()
    now = _utcnow()
    row = _read_row()
    if row is not None and not _claim_refresh(now):
        return _json(_with_meta(row.get("snapshot"), _parse_ts(row.get("generated_at")),
                                refreshing=True))
    return _compute_and_store(row, now)


def handle_historical(query, body):
    err = _require_engine()
    if err:
        return err
    date_str = (query.get("date") or [""])[0]
    if not date_str:
        return _json({"error": "Missing ?date=YYYY-MM-DD parameter"})
    return _json(_engine.LIVE.get_historical(date_str))


def handle_bacteria(query, body):
    err = _require_engine()
    if err:
        return err
    date_str = (query.get("date") or [""])[0]
    if not date_str:
        return _json({"error": "Missing ?date=YYYY-MM-DD parameter"})
    return _json(_engine.LIVE.get_bacteria_ground_truth(date_str))


def handle_actuals(query, body):
    """What happened around a date: gauge rain, reported discharges + the
    beaches they post, watcher postings, bacteria samples — per zone."""
    err = _require_engine()
    if err:
        return err
    date_str = (query.get("date") or [""])[0]
    if not date_str:
        return _json({"error": "Missing ?date=YYYY-MM-DD parameter"})
    return _json(_engine.LIVE.get_actuals(date_str))


def handle_scorecard(query, body):
    """Model check: training-time hindcast vs labels around a date, plus the
    scorecard over a window (train_v4 artifact). Optional ?from= / ?to=
    (YYYY-MM-DD) time-box the scorecard; default = the date's rain season."""
    err = _require_engine()
    if err:
        return err
    date_str = (query.get("date") or [""])[0]
    if not date_str:
        return _json({"error": "Missing ?date=YYYY-MM-DD parameter"})
    start = (query.get("from") or [""])[0] or None
    end = (query.get("to") or [""])[0] or None
    model = (query.get("model") or [""])[0] or None   # a candidate set's name; default = the served v4 artifact
    return _json(_engine.LIVE.get_scorecard(date_str, start, end, model))


def handle_models(query, body):
    """The served model set plus every candidate set on disk (scorecards only —
    candidates are never served live)."""
    err = _require_engine()
    if err:
        return err
    return _json({"models": _engine.LIVE.list_models()})


GET_ROUTES = {
    "/forecast": handle_page,
    "/forecast/api/data": handle_data,
    "/forecast/api/refresh": handle_refresh,
    "/forecast/api/historical": handle_historical,
    "/forecast/api/bacteria": handle_bacteria,
    "/forecast/api/actuals": handle_actuals,
    "/forecast/api/scorecard": handle_scorecard,
    "/forecast/api/models": handle_models,
}
POST_ROUTES = {}
