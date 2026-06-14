#!/usr/bin/env python3
"""Unified web server for the SF Beach Water Quality app.

One threaded HTTP server fronts three cleanly separated feature pages:

    /            landing (current conditions + CSO status + links)
    /alerts      sewage alert system        -> features/alerts/page.py
    /forecast    ML CSO forecast            -> features/forecast/page.py  (lazy)
    /compare     BWTF vs. city comparison   -> features/comparison/page.py

Run from the repo root:  python -m app.server

The alert and comparison pages are mixed in as route classes; the forecast
feature is imported lazily and degrades gracefully if its ML dependencies
aren't installed, so the rest of the app always runs.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from features.alerts.page import AlertsRoutes
from features.comparison.page import ComparisonRoutes
import features.forecast.page as forecast_page

from features.alerts.monitoring import CombinedWaterQualityMonitor
from features.alerts.subscriptions import SubscriptionStore
from features.alerts.cso_alerts import SimulatedCSOStore
from shared.sfpuc_api import SFPUCRealTimeAPI
from app.landing import render_landing

try:
    from shared.weather_tides import EnvironmentalContext
    HAS_WEATHER = True
except ImportError:
    HAS_WEATHER = False

PORT = 8080

# Alert-page GET endpoints -> AlertsRoutes method names.
_ALERT_GET = {
    "/api/status": "send_api_status",
    "/api/alerts": "send_api_alerts",
    "/api/realtime": "send_api_realtime",
    "/api/weather": "send_api_weather",
    "/api/subscriptions": "send_api_subscriptions",
    "/api/simulations/cso": "send_api_simulated_cso",
    "/api/debug/sfpuc": "send_api_debug_sfpuc",
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


class UnifiedHandler(AlertsRoutes, ComparisonRoutes, BaseHTTPRequestHandler):
    """Single handler that owns the shared clients + HTTP helpers the feature
    route mixins rely on (self.combined_monitor, self.sfpuc_api,
    self.subscription_store, self.simulated_cso_store, self.env_context,
    self._send_json, self._read_request_data)."""

    def __init__(self, *args, **kwargs):
        self.combined_monitor = CombinedWaterQualityMonitor()
        self.sfpuc_api = SFPUCRealTimeAPI()
        self.subscription_store = SubscriptionStore()
        self.simulated_cso_store = SimulatedCSOStore()
        self.env_context = EnvironmentalContext() if HAS_WEATHER else None
        super().__init__(*args, **kwargs)

    # ── shared HTTP helpers (used by the feature mixins) ──
    def _send_json(self, payload, status=200):
        data = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
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

    def _respond(self, status, content_type, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    # ── routing ──
    def do_GET(self):
        parsed = urlparse(self.path)
        path, params = parsed.path, parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self.send_landing()
        elif path in forecast_page.GET_ROUTES:
            self._respond(*forecast_page.GET_ROUTES[path](params, None))
        elif path == "/alerts":
            self.send_dashboard()
        elif path in _ALERT_GET:
            getattr(self, _ALERT_GET[path])()
        elif path in _COMPARE_GET:
            getattr(self, _COMPARE_GET[path])()
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path in _ALERT_POST:
            getattr(self, _ALERT_POST[path])()
        elif path in forecast_page.POST_ROUTES:
            params = parse_qs(urlparse(self.path).query)
            self._respond(*forecast_page.POST_ROUTES[path](params, None))
        else:
            self.send_error(404, "Not Found")

    def send_landing(self):
        try:
            html = render_landing(self.sfpuc_api, self.env_context)
        except Exception as e:
            html = f"<!doctype html><meta charset='utf-8'><h1>Dashboard</h1><pre>{e}</pre>"
        self._respond(200, "text/html; charset=utf-8", html.encode())


def main():
    forecast_ok = forecast_page.is_available()
    if forecast_ok:
        forecast_page.start_refresh()
    forecast_note = "ready" if forecast_ok else "unavailable (install ML deps; other pages still work)"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SF Beach Water Quality — unified dashboard                   ║
║  Surfrider SF Blue Water Task Force                           ║
╠══════════════════════════════════════════════════════════════╣
║  🌐  http://localhost:{PORT}                                     ║
║      /          landing                                       ║
║      /alerts    sewage alert system                           ║
║      /forecast  CSO forecast  [{forecast_note[:28]:<28}]║
║      /compare   BWTF vs. city comparison                      ║
╚══════════════════════════════════════════════════════════════╝
""")
    server = ThreadingHTTPServer(("", PORT), UnifiedHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
        server.shutdown()


if __name__ == "__main__":
    main()
