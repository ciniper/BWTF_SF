#!/usr/bin/env python3
"""Flask / WSGI entrypoint — the production-deployable front for the app.

This is a thin ADAPTER layer. It reuses the exact page code already in
``features/*/page.py`` and ``app.landing`` — no page logic is copied:

  * alerts + comparison pages are the ``AlertsRoutes`` / ``ComparisonRoutes``
    mixins (the same classes the retired stdlib server composed). The
    adapter below subclasses them, so every page/handler/helper method
    (``send_dashboard``, ``send_comparison_page``, ``generate_dashboard_html``,
    ``_get_dashboard_stations`` …) runs unchanged.
  * the forecast feature already exposes plain ``(query, body) -> (status,
    content_type, bytes)`` functions in ``GET_ROUTES`` / ``POST_ROUTES``; those
    wrap trivially.
  * the path -> handler-method-name routing tables live here (defined below).

The only new code is the glue that turns a Flask request into the
``BaseHTTPRequestHandler``-style ``self.*`` surface those mixins were written
against, and turns what they write back into a Flask ``Response``.

Run locally (Flask dev server):
    python -m app.wsgi
Run in production (what a host runs via the Procfile):
    gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT app.wsgi:app

ONE worker on purpose: the forecast engine runs a single background refresh
thread, so multiple workers would each spawn their own (N weather pulls + N
model loads). One worker with threads serves this dashboard's load fine.

This is the single web entrypoint; the old stdlib ``app/server.py`` has been
retired. The per-request client construction + the ``_send_json`` /
``_read_request_data`` helpers below are what the alert/comparison mixins call
on their handler — they previously lived on the stdlib ``UnifiedHandler``.
"""
import hmac
import io
import json
import os
import secrets
from urllib.parse import parse_qs, urlparse

from flask import Flask, Response, redirect, render_template, request, session

import features.forecast.page as forecast_page
from features.alerts.page import AlertsRoutes
from features.comparison.page import ComparisonRoutes
from features.alerts.monitoring import CombinedWaterQualityMonitor
from features.alerts.subscriptions import SubscriptionStore
from features.alerts.cso_alerts import SimulatedCSOStore
from shared.sfpuc_api import SFPUCRealTimeAPI
from app.landing import render_landing
from features.bwtf_history.page import render_bwtf_history

try:
    from shared.weather_tides import EnvironmentalContext
    HAS_WEATHER = True
except ImportError:
    HAS_WEATHER = False

# Path -> mixin-method-name maps: the single source of truth for routing.
# Alert-page GET endpoints -> AlertsRoutes method names.
_ALERT_GET = {
    "/api/status": "send_api_status",
    "/api/alerts": "send_api_alerts",
    "/api/realtime": "send_api_realtime",
    "/api/weather": "send_api_weather",
    "/api/subscriptions": "send_api_subscriptions",
    "/api/simulations/cso": "send_api_simulated_cso",
    "/api/debug/sfpuc": "send_api_debug_sfpuc",
    "/api/watcher": "send_api_watcher",
}
# Alert-page POST endpoints -> AlertsRoutes method names.
_ALERT_POST = {
    "/api/subscriptions": "save_subscription",
    "/api/subscriptions/delete": "delete_subscription",
    "/api/simulations/cso": "save_simulated_cso",
    "/api/simulations/cso/clear": "clear_simulated_cso",
    "/api/dispatch-cso-alerts": "dispatch_cso_site_alerts",
}
# Comparison-page GET endpoints -> ComparisonRoutes method names.
_COMPARE_GET = {
    "/compare": "send_comparison_page",
    "/api/compare": "send_api_compare",
    "/api/site-history": "send_api_site_history",
}

# ── Alerts access gate ────────────────────────────────────────────────────────
# The alerts page manages live subscriptions and its simulator can send real
# email, so it sits behind a shared passphrase. Besides the page itself, every
# state-changing action and the endpoints that expose subscriber contact info
# are gated; public-data reads (/api/status, /api/debug/sfpuc, …) stay open so
# other pages and the landing footer keep working.
ALERTS_PASSPHRASE = os.environ.get("ALERTS_PASSPHRASE", "snowy plover")
_GATED_API_GET = {"/api/subscriptions", "/api/watcher"}


def _normalize_passphrase(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def _alerts_unlocked() -> bool:
    return session.get("alerts_unlocked") is True


def _gated_page(inner_view):
    """Show the unlock page instead of the wrapped page until the session is unlocked."""
    def view(**kwargs):
        if _alerts_unlocked():
            return inner_view(**kwargs)
        return Response(render_template("alerts/unlock.html", error=None),
                        status=200, content_type="text/html; charset=utf-8")
    return view


def _gated_api(inner_view):
    """401-JSON instead of the wrapped API until the session is unlocked."""
    def view(**kwargs):
        if _alerts_unlocked():
            return inner_view(**kwargs)
        return Response(json.dumps({"ok": False, "error": "Locked — open /alerts and enter the passphrase."}),
                        status=401, content_type="application/json")
    return view


def _unlock_submit():
    supplied = _normalize_passphrase(request.form.get("passphrase", ""))
    if hmac.compare_digest(supplied, _normalize_passphrase(ALERTS_PASSPHRASE)):
        session["alerts_unlocked"] = True
        return redirect("/alerts")
    return Response(render_template("alerts/unlock.html", error="That's not it — check with the coordinator and try again."),
                    status=401, content_type="text/html; charset=utf-8")


class _RequestAdapter(AlertsRoutes, ComparisonRoutes):
    """Backs the alert/comparison mixin methods with a Flask request.

    The mixins call ``self.send_response`` / ``send_header`` / ``end_headers`` /
    ``wfile.write`` (and ``self._send_json``, which does the same); we capture
    those and expose the result as a Flask ``Response`` via :meth:`to_response`.
    """

    def __init__(self):
        # Same per-request clients the stdlib UnifiedHandler constructs.
        self.combined_monitor = CombinedWaterQualityMonitor()
        self.sfpuc_api = SFPUCRealTimeAPI()
        self.subscription_store = SubscriptionStore()
        self.simulated_cso_store = SimulatedCSOStore()
        self.env_context = EnvironmentalContext() if HAS_WEATHER else None
        # Request surface the mixins read.
        self.path = request.full_path            # includes "?query"; mixins urlparse this
        self.headers = request.headers           # werkzeug headers; .get() is case-insensitive
        self.rfile = io.BytesIO(request.get_data())
        # Response capture.
        self.wfile = io.BytesIO()
        self._status = 200
        self._out_headers = {}

    # ── captured BaseHTTPRequestHandler response API ──
    def send_response(self, code, message=None):
        self._status = code

    def send_header(self, key, value):
        # Let Flask recompute Content-Length from the final body.
        if key.lower() != "content-length":
            self._out_headers[key] = value

    def end_headers(self):
        pass

    # ── helpers copied from UnifiedHandler (dedupe when server.py is retired) ──
    def _send_json(self, payload, status=200):
        data = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def _read_request_data(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode() if content_length else ""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw_body or "{}")
        form_data = parse_qs(raw_body, keep_blank_values=True)
        return {k: v if len(v) > 1 else v[0] for k, v in form_data.items()}

    # ── run one mixin method by name, package what it wrote ──
    def run(self, method_name):
        getattr(self, method_name)()
        return self.to_response()

    def to_response(self):
        headers = dict(self._out_headers)
        content_type = headers.pop("Content-Type", "text/html; charset=utf-8")
        return Response(self.wfile.getvalue(), status=self._status,
                        headers=headers, content_type=content_type)


def _mixin_view(method_name):
    """A Flask view that runs one mixin method on a fresh adapter."""
    def view(**kwargs):
        return _RequestAdapter().run(method_name)
    return view


def _forecast_view(handler):
    """Wrap a forecast ``(query, body) -> (status, content_type, bytes)`` fn."""
    def view(**kwargs):
        params = parse_qs(urlparse(request.full_path).query)
        status, content_type, body = handler(params, None)
        return Response(body, status=status, content_type=content_type)
    return view


def _landing_view():
    sfpuc = SFPUCRealTimeAPI()
    env = EnvironmentalContext() if HAS_WEATHER else None
    try:
        html = render_landing(sfpuc, env)
    except Exception as e:  # never blank the hub page on a flaky upstream
        html = f"<!doctype html><meta charset='utf-8'><h1>Dashboard</h1><pre>{e}</pre>"
    return Response(html, content_type="text/html; charset=utf-8")


def _bwtf_view():
    try:
        html = render_bwtf_history()
    except Exception as e:  # never blank the page on a flaky upstream
        html = f"<!doctype html><meta charset='utf-8'><h1>BWTF Sample Log unavailable</h1><pre>{e}</pre>"
    return Response(html, content_type="text/html; charset=utf-8")


def create_app():
    app = Flask(__name__)
    # Signs the session cookie that remembers an unlocked alerts gate. Without
    # FLASK_SECRET_KEY set, a random key is generated per boot — everything
    # works, but everyone re-enters the passphrase after each deploy/restart.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    # Landing
    app.add_url_rule("/", "landing", _landing_view, methods=["GET"])
    app.add_url_rule("/index.html", "landing_index", _landing_view, methods=["GET"])

    # BWTF Sample Log
    app.add_url_rule("/bwtf", "bwtf", _bwtf_view, methods=["GET"])

    # Alerts page (passphrase-gated) + its GET/POST APIs
    app.add_url_rule("/alerts", "alerts", _gated_page(_mixin_view("send_dashboard")), methods=["GET"])
    app.add_url_rule("/alerts/unlock", "alerts-unlock", _unlock_submit, methods=["POST"])
    for path, method_name in _ALERT_GET.items():
        view = _mixin_view(method_name)
        if path in _GATED_API_GET:
            view = _gated_api(view)
        app.add_url_rule(path, f"alert-get:{path}", view, methods=["GET"])
    for path, method_name in _ALERT_POST.items():
        app.add_url_rule(path, f"alert-post:{path}", _gated_api(_mixin_view(method_name)), methods=["POST"])

    # Comparison page + its GET APIs
    for path, method_name in _COMPARE_GET.items():
        app.add_url_rule(path, f"compare-get:{path}", _mixin_view(method_name), methods=["GET"])

    # Forecast (functional routes; ML engine is lazy + degrades gracefully)
    for path, handler in forecast_page.GET_ROUTES.items():
        app.add_url_rule(path, f"forecast-get:{path}", _forecast_view(handler), methods=["GET"])
    for path, handler in forecast_page.POST_ROUTES.items():
        app.add_url_rule(path, f"forecast-post:{path}", _forecast_view(handler), methods=["POST"])

    # Kick the forecast background refresh once (no-op if ML deps are missing).
    if forecast_page.is_available():
        forecast_page.start_refresh()

    # Start the automatic alert watcher (poll → edge-triggered dispatch).
    from features.alerts.watcher import start_watcher
    start_watcher()

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
