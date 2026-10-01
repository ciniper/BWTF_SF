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

ONE worker on purpose: the alert watcher runs as a single background thread,
so multiple workers would each spawn their own. (The forecast refresh thread
is gone — predictions are compute-on-visit, cached in Supabase.) One worker
with threads serves this dashboard's load fine.

This is the single web entrypoint; the old stdlib ``app/server.py`` has been
retired. The per-request client construction + the ``_send_json`` /
``_read_request_data`` helpers below are what the alert/comparison mixins call
on their handler — they previously lived on the stdlib ``UnifiedHandler``.
"""
import hmac
import io
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, Response, abort, redirect, render_template, request, send_from_directory, session

import features.about.page as about_page
import features.today.page as today_page
import features.cso_history.page as cso_history_page
import features.discharges.page as discharges_page
import features.forecast.page as forecast_page
import features.signup.page as signup_page
import features.unsubscribe.page as unsubscribe_page
import features.manage.page as manage_page
import features.site_analysis.page as site_analysis_page
import features.postings.page as postings_page
import features.alerts.costs as costs_page
from features.alerts.page import AlertsRoutes
from features.comparison.page import ComparisonRoutes
from shared.datasf import DATASET_PAGE_URL
from shared.basemap import basemap
from features.alerts.cso_alerts import SURFRIDER_LOGO_URL
from features.alerts.monitoring import CombinedWaterQualityMonitor
from features.alerts.subscriptions import SubscriptionStore
from features.alerts.cso_alerts import SimulatedCSOStore
from shared.sfpuc_api import SFPUCRealTimeAPI
from app.assets import asset, cache_stamped_static
from app.landing import canonical_path, nav_model
from app.build_info import build_info

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
    "/graphs": "send_graphs_page",
    "/samples": "send_samples_page",
    "/compare": "send_comparison_page",       # Source Comparison: the head to head for the dual sites
    "/bwtf": "send_bwtf_redirect",            # retired BWTF Sample Log → /samples?source=bwtf&notes=columns
    "/api/compare": "send_api_compare",
    "/api/site-history": "send_api_site_history",
    "/api/site-series": "send_api_site_series",
    "/api/samples": "send_api_samples",
    "/api/sample-day": "send_api_sample_day",
    "/api/sample-dates": "send_api_sample_dates",
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
    """Wrap a ``(query, body) -> (status, content_type, bytes)`` route fn
    (the functional contract the forecast and cso-history features use)."""
    def view(**kwargs):
        params = parse_qs(urlparse(request.full_path).query)
        status, content_type, body = handler(params, request.get_data())
        return Response(body, status=status, content_type=content_type)
    return view


_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


def _report_view(name: str):
    """Static analyses under reports/ (e.g. the model explorer). HTML only,
    plain file names only — nothing else in the repo is reachable here."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.html", name) or not (_REPORTS_DIR / name).is_file():
        abort(404)
    return send_from_directory(_REPORTS_DIR, name, mimetype="text/html")


def create_app():
    app = Flask(__name__)
    app.jinja_env.globals["DATASF_DATASET_URL"] = DATASET_PAGE_URL  # shared/datasf.py
    app.jinja_env.globals["SURFRIDER_LOGO_URL"] = SURFRIDER_LOGO_URL
    app.jinja_env.globals["BASEMAP"] = basemap()  # shared/basemap.py: the one tile layer every map draws
    app.jinja_env.globals["NAV"] = nav_model()   # app/landing.py: the three hubs, for the shared top bar (_frame.html)
    app.jinja_env.globals["canonical_path"] = canonical_path   # /today and /index.html light the home page's tabs
    app.jinja_env.globals["asset"] = asset                     # app/assets.py: /static links stamped with their contents
    app.after_request(cache_stamped_static)                    # …and a year-long cache only for a current stamp
    # Signs the session cookie that remembers an unlocked alerts gate. Without
    # FLASK_SECRET_KEY set, a random key is generated per boot — everything
    # works, but everyone re-enters the passphrase after each deploy/restart.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    # Home: the root IS the Today page (2026-10-01) — served directly, no redirect; /today and
    # /index.html serve the same page (alert emails link /today), and its canonical tag names /
    app.add_url_rule("/", "home", _forecast_view(today_page.handle_page), methods=["GET"])
    app.add_url_rule("/index.html", "home_index", _forecast_view(today_page.handle_page), methods=["GET"])
    # Browsers ask for /favicon.ico regardless of <link> tags; the BWTF icon
    # (Surfrider's own favicon + manifest PNGs) lives with the brand assets.
    app.add_url_rule("/favicon.ico", "favicon",
                     lambda: redirect("/static/brand/favicon.ico", code=302), methods=["GET"])

    # BWTF Sample Log

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
    # Running costs: the alerts dashboard's third tab (features/alerts/costs.py), behind the same passphrase
    for path, handler in costs_page.GET_ROUTES.items():
        gate = _gated_api if path.startswith("/alerts/api/") else _gated_page
        app.add_url_rule(path, f"costs-get:{path}", gate(_forecast_view(handler)), methods=["GET"])

    # Comparison page + its GET APIs
    for path, method_name in _COMPARE_GET.items():
        app.add_url_rule(path, f"compare-get:{path}", _mixin_view(method_name), methods=["GET"])

    # Forecast (functional routes; ML engine is lazy + degrades gracefully)
    for path, handler in forecast_page.GET_ROUTES.items():
        app.add_url_rule(path, f"forecast-get:{path}", _forecast_view(handler), methods=["GET"])
    for path, handler in forecast_page.POST_ROUTES.items():
        app.add_url_rule(path, f"forecast-post:{path}", _forecast_view(handler), methods=["POST"])

    # CSO event timeline (public read-only; recipient data never leaves the DB)
    for path, handler in cso_history_page.GET_ROUTES.items():
        app.add_url_rule(path, f"cso-history-get:{path}", _forecast_view(handler), methods=["GET"])

    # Site report card (public read-only; DataSF lab-data analysis)
    for path, handler in site_analysis_page.GET_ROUTES.items():
        app.add_url_rule(path, f"site-analysis-get:{path}", _forecast_view(handler), methods=["GET"])

    # Discharge ledger (public read-only; static CSD dataset from CIWQS SMRs)
    for path, handler in discharges_page.GET_ROUTES.items():
        app.add_url_rule(path, f"discharges-get:{path}", _forecast_view(handler), methods=["GET"])

    # Beach postings (public read-only; the State's BeachWatch record, static repo file)
    for path, handler in postings_page.GET_ROUTES.items():
        app.add_url_rule(path, f"postings-get:{path}", _forecast_view(handler), methods=["GET"])

    # About: how the records were obtained (/records) and the infrastructure diagram (/architecture)
    for path, handler in about_page.GET_ROUTES.items():
        app.add_url_rule(path, f"about-get:{path}", _forecast_view(handler), methods=["GET"])

    # Today: the home page (also at /) — the board, its layers (/api/today/*), the page directory below it
    for path, handler in today_page.GET_ROUTES.items():
        app.add_url_rule(path, f"today-get:{path}", _forecast_view(handler), methods=["GET"])

    # Reports: static HTML analyses committed under reports/ (model explorers, training report …)
    app.add_url_rule("/reports/<name>", "reports", _report_view, methods=["GET"])
    # Which build is this? Every template gets `build` (footers show it); /api/build returns it as JSON.
    app.context_processor(lambda: {"build": build_info()})
    app.add_url_rule("/api/build", "build", lambda: Response(json.dumps(build_info()), mimetype="application/json"), methods=["GET"])

    # Alert signup — deliberately UNGATED, including its POST: this is the one
    # alerts surface meant for the public (zone subscribe, email only; the
    # endpoint does its own validation + honeypot — hardening list in TODO B10).
    for path, handler in signup_page.GET_ROUTES.items():
        app.add_url_rule(path, f"signup-get:{path}", _forecast_view(handler), methods=["GET"])
    for path, handler in signup_page.POST_ROUTES.items():
        app.add_url_rule(path, f"signup-post:{path}", _forecast_view(handler), methods=["POST"])

    # One-click unsubscribe (017) — UNGATED by design: the per-subscriber token
    # in the link is the credential. GET confirms, POST (button or a mail
    # client's RFC 8058 one-click) deactivates.
    for path, handler in unsubscribe_page.GET_ROUTES.items():
        app.add_url_rule(path, f"unsubscribe-get:{path}", _forecast_view(handler), methods=["GET"])
    for path, handler in unsubscribe_page.POST_ROUTES.items():
        app.add_url_rule(path, f"unsubscribe-post:{path}", _forecast_view(handler), methods=["POST"])
    # "Change your sites" (same token): the signup zones, pre-checked, saved
    # through the signup store; saving on an unsubscribed row turns alerts back on.
    for path, handler in manage_page.GET_ROUTES.items():
        app.add_url_rule(path, f"manage-get:{path}", _forecast_view(handler), methods=["GET"])
    for path, handler in manage_page.POST_ROUTES.items():
        app.add_url_rule(path, f"manage-post:{path}", _forecast_view(handler), methods=["POST"])

    # No forecast refresh thread: predictions are compute-on-visit, cached in
    # Supabase (forecast_predictions) — /forecast/api/data refreshes on staleness.

    # Start the automatic alert watcher (poll → edge-triggered dispatch).
    from features.alerts.watcher import start_watcher
    start_watcher()

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
