"""Forecast page — wraps the self-contained sf_sewage_forecast engine.

The forecaster (``live_dashboard.py`` + ``src/``) was moved here intact. Its
modules use absolute ``src.*`` imports and locate ``data/``/``config/`` relative
to ``__file__``, so we put this directory on ``sys.path`` and import it as-is —
no edits to the forecaster internals.

The ML engine (pandas/scikit-learn + the pickled models) is imported lazily so
the rest of the app runs even when those heavy deps aren't installed; the
forecast page then degrades to an "unavailable" notice.

Each route handler returns ``(status, content_type, body_bytes)`` — the simple
response contract ``app/server.py`` dispatches.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

# Treat this directory as the forecaster's project root (preserves its
# `from src.models...` imports and __file__-relative data/config paths).
_FORECAST_ROOT = Path(__file__).resolve().parent
if str(_FORECAST_ROOT) not in sys.path:
    sys.path.insert(0, str(_FORECAST_ROOT))

_engine = None            # the imported live_dashboard module (holds LIVE + HTML_TEMPLATE)
_engine_error = None      # human-readable reason the engine couldn't load
_refresh_started = False


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


def start_refresh(interval_seconds: int = 1800):
    """Start the engine's background refresh loop once (called at server startup)."""
    global _refresh_started
    if _refresh_started:
        return
    _load_engine()
    if _engine is None:
        return
    thread = threading.Thread(
        target=_engine.refresh_loop, args=(_engine.LIVE, interval_seconds), daemon=True
    )
    thread.start()
    _refresh_started = True


def is_available() -> bool:
    _load_engine()
    return _engine is not None


# ─── response helpers ────────────────────────────────────────────────────────

def _html(body: str, status: int = 200):
    return status, "text/html; charset=utf-8", body.encode()


def _json(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, default=str).encode()


def _unavailable_html() -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
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
    if _engine is None:
        return _unavailable_html()
    today = datetime.now().strftime("%Y-%m-%d")
    html = (
        _engine.HTML_TEMPLATE
        .replace("__MIN_DATE__", "2020-07-27")   # earliest SF Gov bacteria data
        .replace("__MAX_DATE__", today)
        .replace("__DEFAULT_DATE__", today)
    )
    # Namespace the page's API calls under /forecast/api/ (all fetches are single-quoted).
    html = html.replace("'/api/", "'/forecast/api/")
    # Inject the unified back-to-dashboard button (kept here so live_dashboard.py stays untouched).
    back_css = (
        "<style>.back-to-dash{display:inline-block;margin:0 0 14px;padding:8px 14px;"
        "background:#343b44;color:#fff;border-radius:999px;text-decoration:none;font-weight:700;"
        "font-size:13px}.back-to-dash:hover{background:#26272a}</style>"
    )
    html = html.replace("</head>", back_css + "</head>")
    html = html.replace('<div class="container">', '<div class="container"><a class="back-to-dash" href="/">← Dashboard</a>', 1)
    return html


# ─── route handlers ──────────────────────────────────────────────────────────

def handle_page(query, body):
    return _html(_render_page())


def _require_engine():
    _load_engine()
    if _engine is None:
        return _json({"error": _engine_error or "forecast engine unavailable"}, status=503)
    return None


def handle_data(query, body):
    err = _require_engine()
    return err or _json(_engine.LIVE.get_snapshot())


def handle_refresh(query, body):
    err = _require_engine()
    if err:
        return err
    _engine.LIVE.refresh()
    return _json(_engine.LIVE.get_snapshot())


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


GET_ROUTES = {
    "/forecast": handle_page,
    "/forecast/api/data": handle_data,
    "/forecast/api/refresh": handle_refresh,
    "/forecast/api/historical": handle_historical,
    "/forecast/api/bacteria": handle_bacteria,
}
POST_ROUTES = {}
