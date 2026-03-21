#!/usr/bin/env python3
"""
Simple Web Dashboard for SF Beach Water Quality

Run with: python dashboard.py
Then open http://localhost:8080 in your browser

This dashboard combines real-time SFPUC data (CSO events, posted status)
with SF Gov API bacteria data for a comprehensive view.
"""

import http.server
import socketserver
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from core.cso_alerts import (
    SimulatedCSOStore,
    apply_simulated_cso,
    dispatch_subscription_alerts,
    get_cso_eligible_stations,
)
from core.monitoring import CombinedWaterQualityMonitor, STANDARDS
from core.sfpuc_api import SFPUCRealTimeAPI
from core.subscriptions import SubscriptionStore

# Optional weather/tides integration
try:
    from core.weather_tides import EnvironmentalContext
    HAS_WEATHER = True
except ImportError:
    HAS_WEATHER = False

PORT = 8080
CARRIER_OPTIONS = [
    ("verizon", "Verizon"),
    ("att", "AT&T"),
    ("tmobile", "T-Mobile"),
    ("sprint", "Sprint"),
    ("cricket", "Cricket"),
    ("metropcs", "MetroPCS"),
    ("uscellular", "US Cellular"),
]
BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"
SURFRIDER_HERO_IMAGE_URL = "https://sf.surfrider.org/hubfs/IMG_8857.jpg"


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class WaterQualityHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for water quality dashboard"""
    
    def __init__(self, *args, **kwargs):
        self.combined_monitor = CombinedWaterQualityMonitor()
        self.sfpuc_api = SFPUCRealTimeAPI()
        self.subscription_store = SubscriptionStore()
        self.simulated_cso_store = SimulatedCSOStore()
        self.env_context = EnvironmentalContext() if HAS_WEATHER else None
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_dashboard()
        elif parsed.path == "/api/status":
            self.send_api_status()
        elif parsed.path == "/api/alerts":
            self.send_api_alerts()
        elif parsed.path == "/api/realtime":
            self.send_api_realtime()
        elif parsed.path == "/api/weather":
            self.send_api_weather()
        elif parsed.path == "/api/subscriptions":
            self.send_api_subscriptions()
        elif parsed.path == "/api/simulations/cso":
            self.send_api_simulated_cso()
        elif parsed.path == "/api/debug/sfpuc":
            self.send_api_debug_sfpuc()
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/subscriptions":
            self.save_subscription()
        elif parsed.path == "/api/subscriptions/delete":
            self.delete_subscription()
        elif parsed.path == "/api/simulations/cso":
            self.save_simulated_cso()
        elif parsed.path == "/api/simulations/cso/clear":
            self.clear_simulated_cso()
        elif parsed.path == "/api/dispatch-cso-alerts":
            self.dispatch_cso_site_alerts()
        else:
            self.send_error(404, "Not Found")

    def _send_json(self, payload, status=200):
        data = json.dumps(payload, default=str)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data.encode()))
        self.end_headers()
        self.wfile.write(data.encode())

    def _read_request_data(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode() if content_length else ""
        content_type = self.headers.get("Content-Type", "")

        if "application/json" in content_type:
            return json.loads(raw_body or "{}")

        form_data = parse_qs(raw_body, keep_blank_values=True)
        return {
            key: values if len(values) > 1 else values[0]
            for key, values in form_data.items()
        }

    def _get_dashboard_stations(self):
        live_stations = self.sfpuc_api.fetch_stations()
        simulated_station_ids = self.simulated_cso_store.get_station_ids()
        stations = apply_simulated_cso(live_stations, simulated_station_ids)
        return stations, simulated_station_ids
    
    def send_dashboard(self):
        """Send the main dashboard HTML page"""
        html = self.generate_dashboard_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html.encode()))
        self.end_headers()
        self.wfile.write(html.encode())
    
    def send_api_status(self):
        """Send JSON status data"""
        try:
            stations, simulated_station_ids = self._get_dashboard_stations()
            cso_events = [s for s in stations if s.has_cso]
            status = {
                "stations": [{
                    "id": s.station_id,
                    "name": s.station_name,
                    "status": s.status.value,
                    "has_cso": s.has_cso,
                    "simulated_cso": s.station_id in set(simulated_station_ids),
                    "sample_date": s.sample_date.isoformat() if s.sample_date else None,
                    "latitude": s.latitude,
                    "longitude": s.longitude
                } for s in stations],
                "summary": {
                    "timestamp": datetime.now().isoformat(),
                    "total_stations": len(stations),
                    "safe_count": len([s for s in stations if s.status.value == "safe"]),
                    "posted_count": len([s for s in stations if s.status.value == "posted"]),
                    "not_sampled_count": len([s for s in stations if s.status.value in ("not_sampled", "not_routinely_sampled")]),
                    "cso_active_count": len(cso_events),
                    "simulated_station_ids": simulated_station_ids,
                }
            }
        except Exception as e:
            status = {"error": str(e)}
        self._send_json(status)
    
    def send_api_alerts(self):
        """Send JSON alerts data"""
        try:
            alerts = self.combined_monitor.get_combined_alerts()
            payload = [{
                "type": a.alert_type,
                "station_id": a.station_id,
                "station_name": a.station_name,
                "message": a.message,
                "severity": a.severity,
                "sample_date": a.sample_date.isoformat() if a.sample_date else None,
                "details": a.details
            } for a in alerts]
        except Exception as e:
            payload = {"error": str(e)}
        self._send_json(payload)
    
    def send_api_realtime(self):
        """Send real-time SFPUC data"""
        try:
            stations, simulated_station_ids = self._get_dashboard_stations()
            cso_events = [s for s in stations if s.has_cso]

            payload = {
                "cso_events": [{
                    "station_id": station.station_id,
                    "station_name": station.station_name,
                    "cso_station_id": station.cso_station_id,
                    "latitude": station.latitude,
                    "longitude": station.longitude,
                    "simulated": station.station_id in set(simulated_station_ids),
                } for station in cso_events],
                "stations": [{
                    "id": s.station_id,
                    "name": s.station_name,
                    "status": s.status.value,
                    "has_cso": s.has_cso,
                    "simulated_cso": s.station_id in set(simulated_station_ids),
                    "sample_date": s.sample_date.isoformat() if s.sample_date else None
                } for s in stations]
            }
        except Exception as e:
            payload = {"error": str(e)}
        self._send_json(payload)
    
    def send_api_weather(self):
        """Send weather and tide data as JSON"""
        if not self.env_context:
            payload = {"error": "Weather module not available"}
        else:
            try:
                context = self.env_context.get_full_context()
                payload = context
            except Exception as e:
                payload = {"error": str(e)}
        self._send_json(payload)

    def send_api_subscriptions(self):
        try:
            subscriptions = self.subscription_store.list_subscriptions()
            payload = [{
                "email": subscription.email,
                "phone_number": subscription.phone_number,
                "carrier": subscription.carrier,
                "station_ids": subscription.station_ids,
                "created_at": subscription.created_at,
                "updated_at": subscription.updated_at,
            } for subscription in subscriptions]
        except Exception as e:
            payload = {"error": str(e)}
        self._send_json(payload)

    def send_api_simulated_cso(self):
        try:
            payload = {
                "station_ids": self.simulated_cso_store.get_station_ids(),
            }
        except Exception as e:
            payload = {"error": str(e)}
        self._send_json(payload)

    def send_api_debug_sfpuc(self):
        """Return the raw current SFPUC getBeaches payload for browser inspection."""
        try:
            response = self.sfpuc_api.session.get(self.sfpuc_api.API_URL, timeout=30)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            raw_text = root.text or "[]"
            raw_rows = json.loads(raw_text)
            payload = {
                "source_url": self.sfpuc_api.API_URL,
                "fetched_at": datetime.now().isoformat(),
                "count": len(raw_rows),
                "rows": raw_rows,
            }
        except Exception as e:
            payload = {
                "source_url": self.sfpuc_api.API_URL,
                "error": str(e),
            }
        self._send_json(payload)

    def save_subscription(self):
        try:
            data = self._read_request_data()
            email = data.get("email", "")
            phone_number = data.get("phone_number", "")
            carrier = data.get("carrier", "")
            station_ids = data.get("station_ids", [])
            if isinstance(station_ids, str):
                station_ids = [station_ids]

            stations, _ = self._get_dashboard_stations()
            eligible_station_ids = {station.station_id for station in get_cso_eligible_stations(stations)}
            selected_station_ids = [station_id for station_id in station_ids if station_id in eligible_station_ids]
            subscription = self.subscription_store.upsert_subscription(
                email=email,
                station_ids=selected_station_ids,
                phone_number=phone_number,
                carrier=carrier,
            )
            self._send_json({
                "ok": True,
                "subscription": {
                    "email": subscription.email,
                    "phone_number": subscription.phone_number,
                    "carrier": subscription.carrier,
                    "station_ids": subscription.station_ids,
                }
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)

    def delete_subscription(self):
        try:
            data = self._read_request_data()
            removed = self.subscription_store.delete_subscription(
                email=data.get("email", ""),
                phone_number=data.get("phone_number", ""),
            )
            if not removed:
                self._send_json({"ok": False, "error": "Subscription not found."}, status=404)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)

    def save_simulated_cso(self):
        try:
            data = self._read_request_data()
            station_ids = data.get("station_ids", [])
            if isinstance(station_ids, str):
                station_ids = [station_ids]

            stations, _ = self._get_dashboard_stations()
            available_station_ids = {station.station_id for station in get_cso_eligible_stations(stations)}
            selected_station_ids = [station_id for station_id in station_ids if station_id in available_station_ids]
            saved_station_ids = self.simulated_cso_store.set_station_ids(selected_station_ids)
            self._send_json({"ok": True, "station_ids": saved_station_ids})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)

    def clear_simulated_cso(self):
        self.simulated_cso_store.clear()
        self._send_json({"ok": True, "station_ids": []})

    def dispatch_cso_site_alerts(self):
        try:
            data = self._read_request_data()
            channel = data.get("channel", "email")
            stations, simulated_station_ids = self._get_dashboard_stations()
            subscriptions = self.subscription_store.list_subscriptions()
            results = dispatch_subscription_alerts(
                subscriptions=subscriptions,
                stations=stations,
                simulated_station_ids=simulated_station_ids,
                channel=channel,
            )
            self._send_json({
                "ok": True,
                "channel": channel,
                "results": results,
                "twilio_configured": bool(
                    os.environ.get("TWILIO_ACCOUNT_SID")
                    and os.environ.get("TWILIO_AUTH_TOKEN")
                    and os.environ.get("TWILIO_FROM_NUMBER")
                ),
                "smtp_configured": bool(
                    os.environ.get("SMTP_USERNAME")
                    and os.environ.get("SMTP_PASSWORD")
                ),
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)
    
    def generate_dashboard_html(self):
        """Generate the dashboard HTML with combined data"""
        
        # Fetch weather/tide data
        rain_html = ""
        tide_html = ""
        if self.env_context:
            try:
                rain_advisory = self.env_context.weather.get_rain_advisory()
                if rain_advisory.is_active:
                    rain_html = f"""
                    <div class="cso-banner" style="background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);">
                        <div class="cso-icon">🌧️</div>
                        <div class="cso-content">
                            <div class="cso-title">RAIN ADVISORY ACTIVE</div>
                            <div class="cso-message">{rain_advisory.message}</div>
                            <div class="cso-warning">
                                CSO Risk: <strong>{rain_advisory.cso_risk.upper()}</strong> |
                                Total rainfall: {rain_advisory.total_recent_inches:.2f} inches |
                                Advisory until: {rain_advisory.advisory_until.strftime('%m/%d %I:%M %p') if rain_advisory.advisory_until else 'N/A'}
                            </div>
                        </div>
                    </div>
                    """
                elif rain_advisory.upcoming_rain:
                    forecasts = "<br>".join(f"• {e.description}" for e in rain_advisory.upcoming_rain[:3])
                    rain_html = f"""
                    <div class="error-banner" style="border-left-color: #3498db; background: #e8f4f8;">
                        🌦️ <strong>Rain in Forecast:</strong><br>{forecasts}
                        <br><small>SFPUC advises avoiding water contact during and 72 hours after rain.</small>
                    </div>
                    """
            except Exception as e:
                rain_html = f'<div class="error-banner">⚠️ Weather data unavailable: {e}</div>'
            
            try:
                tide_info = self.env_context.tides.get_tide_info()
                if tide_info:
                    trend_icon = "📈" if tide_info.current_trend == "rising" else "📉"
                    trend_label = tide_info.current_trend.capitalize()
                    next_high_str = f"{tide_info.next_high.time.strftime('%I:%M %p')} ({tide_info.next_high.height_ft:.1f} ft)" if tide_info.next_high else "N/A"
                    next_low_str = f"{tide_info.next_low.time.strftime('%I:%M %p')} ({tide_info.next_low.height_ft:.1f} ft)" if tide_info.next_low else "N/A"
                    
                    tide_html = f"""
                    <div class="summary-card total" style="border-top-color: #1abc9c; grid-column: span 1;">
                        <div class="summary-number" style="font-size: 1.5em;">{trend_icon} {trend_label}</div>
                        <div class="summary-label">🌊 Tide</div>
                        <div style="font-size: 0.8em; color: #666; margin-top: 8px;">
                            Next High: {next_high_str}<br>
                            Next Low: {next_low_str}
                        </div>
                    </div>
                    """
            except Exception:
                pass
        
        subscriptions = self.subscription_store.list_subscriptions()

        # Fetch real-time SFPUC data
        try:
            stations, simulated_station_ids = self._get_dashboard_stations()
            simulated_station_id_set = set(simulated_station_ids)
            cso_events = [station for station in stations if station.has_cso]
            posted_stations = [station for station in stations if station.status.value == "posted"]
            safe_stations = [station for station in stations if station.status.value == "safe"]
            not_sampled_stations = [
                station for station in stations
                if station.status.value in ("not_sampled", "not_routinely_sampled")
            ]
            eligible_cso_stations = get_cso_eligible_stations(stations)
            sfpuc_error = None
        except Exception as e:
            stations = []
            cso_events = []
            posted_stations = []
            safe_stations = []
            not_sampled_stations = []
            eligible_cso_stations = []
            simulated_station_ids = []
            simulated_station_id_set = set()
            sfpuc_error = str(e)
        
        # Fetch combined alerts
        try:
            alerts = self.combined_monitor.get_combined_alerts()
        except Exception as e:
            alerts = []

        try:
            lab_results_lookup = self.combined_monitor.sf_gov_monitor.get_latest_lab_results_for_sfpuc_stations()
        except Exception:
            lab_results_lookup = {}
        
        # Count statistics
        cso_count = len(cso_events)
        posted_count = len(posted_stations)
        safe_count = len(safe_stations)
        not_sampled_count = len(not_sampled_stations)
        total_stations = len(stations)
        
        # Generate CSO alert banner
        cso_banner = ""
        if cso_events:
            cso_locations = ", ".join([e.station_name for e in cso_events])
            cso_banner = f"""
            <div class="cso-banner">
                <div class="cso-icon">🚨</div>
                <div class="cso-content">
                    <div class="cso-title">ACTIVE COMBINED SEWER OVERFLOW</div>
                    <div class="cso-message">
                        CSO detected at: <strong>{cso_locations}</strong>
                    </div>
                    <div class="cso-warning">
                        Sewage discharge has occurred within the last 24-72 hours. Avoid water contact at affected beaches.
                    </div>
                </div>
            </div>
            """
        
        # Generate station cards - sorted by severity
        station_cards = ""
        
        # Track which stations we've already added
        added_station_ids = set()
        
        # CSO stations (highest priority) - use has_cso flag directly
        for station in stations:
            if station.has_cso:
                station_cards += self._generate_station_card(
                    station,
                    "cso",
                    station.station_id in simulated_station_id_set,
                    lab_results_lookup.get(station.station_name),
                )
                added_station_ids.add(station.station_id)
        
        # Posted stations (without CSO)
        for station in posted_stations:
            if station.station_id not in added_station_ids:
                station_cards += self._generate_station_card(
                    station,
                    "posted",
                    station.station_id in simulated_station_id_set,
                    lab_results_lookup.get(station.station_name),
                )
                added_station_ids.add(station.station_id)
        
        # Safe stations
        for station in safe_stations:
            if station.station_id not in added_station_ids:
                station_cards += self._generate_station_card(
                    station,
                    "safe",
                    station.station_id in simulated_station_id_set,
                    lab_results_lookup.get(station.station_name),
                )
                added_station_ids.add(station.station_id)
        
        # Not sampled stations
        for station in not_sampled_stations:
            if station.station_id not in added_station_ids:
                station_cards += self._generate_station_card(
                    station,
                    "not_sampled",
                    station.station_id in simulated_station_id_set,
                    lab_results_lookup.get(station.station_name),
                )
                added_station_ids.add(station.station_id)
        
        # Generate alerts section
        alerts_html = ""
        if alerts:
            for alert in alerts:
                severity_class = "critical" if alert.severity == "critical" else "warning" if alert.severity == "warning" else "advisory"
                icon = "🚨" if alert.severity == "critical" else "⚠️" if alert.severity == "warning" else "ℹ️"
                alerts_html += f"""
                    <div class="alert {severity_class}">
                        <span class="alert-icon">{icon}</span>
                        <div class="alert-content">
                            <div class="alert-message">{alert.message}</div>
                            <div class="alert-meta">
                                <span class="alert-station">{alert.station_name}</span>
                                {f'<span class="alert-date">Sample: {alert.sample_date.strftime("%m/%d/%Y")}</span>' if alert.sample_date else ''}
                            </div>
                        </div>
                    </div>
                """
        else:
            alerts_html = '<div class="no-alerts">✅ No active alerts</div>'
        
        # Error message if SFPUC API failed
        error_html = ""
        if sfpuc_error:
            error_html = f"""
            <div class="error-banner">
                ⚠️ Could not fetch real-time data: {sfpuc_error}
            </div>
            """

        subscription_site_options = "".join(
            f"""
            <label class="checkbox-option">
                <input type="checkbox" name="station_ids" value="{station.station_id}">
                <span>{station.station_name}</span>
            </label>
            """
            for station in eligible_cso_stations
        ) or '<div class="empty-state">No CSO-eligible sites are available right now.</div>'

        simulation_site_options = "".join(
            f"""
            <label class="checkbox-option">
                <input type="checkbox" name="station_ids" value="{station.station_id}" {'checked' if station.station_id in simulated_station_id_set else ''}>
                <span>{station.station_name}</span>
            </label>
            """
            for station in eligible_cso_stations
        ) or '<div class="empty-state">No CSO-eligible sites are available right now.</div>'

        station_name_by_id = {
            station.station_id: station.station_name
            for station in eligible_cso_stations
        }

        current_subscription_html = "".join(
            f"""
            <div class="subscription-item">
                <div class="subscription-phone">{subscription.email or 'No email set'}</div>
                <div class="subscription-sites">Email: {subscription.email or 'not configured'}</div>
                <div class="subscription-sites">SMS: {subscription.phone_number or 'not configured'}</div>
                <div class="subscription-sites">Carrier: {dict(CARRIER_OPTIONS).get(subscription.carrier, subscription.carrier or 'not configured')}</div>
                <div class="subscription-sites">{", ".join(station_name_by_id.get(station_id, station_id) for station_id in subscription.station_ids)}</div>
                <div class="subscription-actions">
                    <button class="action-btn danger small" type="button" onclick='deleteSubscription({json.dumps(subscription.email)}, {json.dumps(subscription.phone_number)})'>Clear Subscriber</button>
                </div>
            </div>
            """
            for subscription in subscriptions
        ) or '<div class="empty-state">No subscriptions saved yet.</div>'

        carrier_options_html = "".join(
            f'<option value="{value}">{label}</option>'
            for value, label in CARRIER_OPTIONS
        )

        simulated_site_names = [
            station.station_name
            for station in eligible_cso_stations
            if station.station_id in simulated_station_id_set
        ]
        simulated_sites_html = (
            ", ".join(simulated_site_names)
            if simulated_site_names else
            "None"
        )
        
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="300">
    <title>SF Beach Water Quality Dashboard</title>
    <style>
        :root {{
            --surf-blue: #317fb2;
            --surf-blue-bright: #3d9fdf;
            --surf-aqua: #88c8d2;
            --surf-ink: #26272a;
            --surf-sand: #f5f6f7;
            --surf-paper: #ffffff;
            --surf-muted: #5e6a71;
            --surf-border: #d9e4e8;
            --surf-danger: #ff4100;
            --surf-warning: #fbc02d;
            --surf-success: #25d670;
            --surf-shadow: 0 18px 40px rgba(38, 39, 42, 0.12);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            font-family: "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at top left, rgba(136, 200, 210, 0.38), transparent 28%),
                radial-gradient(circle at top right, rgba(61, 159, 223, 0.20), transparent 22%),
                linear-gradient(180deg, #f5f6f7 0%, #e8f0f2 100%);
            min-height: 100vh;
            color: var(--surf-ink);
        }}
        
        .container {{
            max-width: 1280px;
            margin: 0 auto;
            padding: 24px;
        }}
        
        header {{
            position: relative;
            overflow: hidden;
            border-radius: 28px;
            padding: 32px;
            margin-bottom: 24px;
            box-shadow: var(--surf-shadow);
            background:
                linear-gradient(115deg, rgba(38, 39, 42, 0.92), rgba(49, 127, 178, 0.84)),
                url('{SURFRIDER_HERO_IMAGE_URL}') center/cover;
            color: white;
            border: 1px solid rgba(255,255,255,0.16);
        }}

        header::after {{
            content: "";
            position: absolute;
            inset: auto -80px -100px auto;
            width: 320px;
            height: 320px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(136, 200, 210, 0.34) 0%, rgba(136, 200, 210, 0) 72%);
        }}
        
        header h1 {{
            color: white;
            font-size: clamp(2rem, 4vw, 3.3rem);
            line-height: 0.95;
            letter-spacing: 0.02em;
            text-transform: uppercase;
            max-width: 10ch;
        }}
        
        header .subtitle {{
            color: rgba(255,255,255,0.82);
            font-size: 0.98em;
            max-width: 48rem;
        }}
        
        .header-row {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 24px;
            position: relative;
            z-index: 1;
        }}

        .hero-copy {{
            display: grid;
            gap: 12px;
            max-width: 720px;
        }}

        .hero-kicker {{
            display: inline-flex;
            align-items: center;
            gap: 10px;
            width: fit-content;
            padding: 8px 14px;
            border-radius: 999px;
            background: rgba(255,255,255,0.14);
            border: 1px solid rgba(255,255,255,0.18);
            color: white;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
        }}

        .hero-logos {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            justify-content: flex-end;
            max-width: 360px;
        }}

        .hero-logo {{
            border-radius: 18px;
            padding: 12px 14px;
            backdrop-filter: blur(4px);
            box-shadow: 0 12px 24px rgba(0,0,0,0.16);
        }}

        .hero-logo img {{
            display: block;
            max-width: 100%;
            height: auto;
        }}

        .hero-logo.bwtf {{
            background: rgba(49, 127, 178, 0.90);
            border: 1px solid rgba(255,255,255,0.18);
            width: 220px;
        }}

        .hero-logo.surfrider {{
            background: rgba(255,255,255,0.96);
            width: 280px;
        }}

        .hero-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 6px;
        }}
        
        .data-source {{
            padding: 8px 14px;
            border-radius: 999px;
            font-size: 0.8em;
            color: white;
            background: rgba(255,255,255,0.12);
            border: 1px solid rgba(255,255,255,0.16);
        }}
        
        .data-source.live {{
            background: rgba(136, 200, 210, 0.24);
            color: white;
        }}
        
        .data-source.live::before {{
            content: "●";
            margin-right: 5px;
            animation: pulse 2s infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        
        /* CSO Banner */
        .cso-banner {{
            background: linear-gradient(135deg, #c0392b 0%, #e74c3c 100%);
            color: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            display: flex;
            align-items: flex-start;
            gap: 15px;
            box-shadow: 0 4px 15px rgba(192, 57, 43, 0.4);
            animation: cso-pulse 3s infinite;
        }}
        
        @keyframes cso-pulse {{
            0%, 100% {{ box-shadow: 0 4px 15px rgba(192, 57, 43, 0.4); }}
            50% {{ box-shadow: 0 4px 25px rgba(192, 57, 43, 0.6); }}
        }}
        
        .cso-icon {{
            font-size: 2.5em;
        }}
        
        .cso-title {{
            font-size: 1.3em;
            font-weight: bold;
            margin-bottom: 8px;
        }}
        
        .cso-message {{
            margin-bottom: 8px;
        }}
        
        .cso-warning {{
            font-size: 0.9em;
            opacity: 0.9;
        }}
        
        /* Error Banner */
        .error-banner {{
            background: #fff3cd;
            color: #856404;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
            border-left: 4px solid #ffc107;
        }}
        
        /* Summary Cards */
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        
        .summary-card {{
            background: rgba(255,255,255,0.94);
            border-radius: 24px;
            padding: 22px 20px;
            text-align: center;
            box-shadow: var(--surf-shadow);
            border: 1px solid rgba(38, 39, 42, 0.06);
        }}
        
        .summary-card.safe {{
            border-top: 4px solid #27ae60;
        }}
        
        .summary-card.posted {{
            border-top: 4px solid #f39c12;
        }}
        
        .summary-card.cso {{
            border-top: 4px solid #e74c3c;
        }}
        
        .summary-card.total {{
            border-top: 4px solid #3498db;
        }}
        
        .summary-number {{
            font-size: 2.5em;
            font-weight: bold;
            color: var(--surf-ink);
        }}
        
        .summary-card.cso .summary-number {{
            color: #e74c3c;
        }}
        
        .summary-label {{
            color: var(--surf-muted);
            margin-top: 5px;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}
        
        /* Alerts Section */
        .alerts-section {{
            background: rgba(255,255,255,0.95);
            border-radius: 24px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: var(--surf-shadow);
            border: 1px solid rgba(38, 39, 42, 0.06);
        }}
        
        .section-title {{
            color: var(--surf-ink);
            margin-bottom: 15px;
            font-size: 1.1em;
            display: flex;
            align-items: center;
            gap: 8px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        
        .alert {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 10px;
        }}
        
        .alert:last-child {{
            margin-bottom: 0;
        }}
        
        .alert.critical {{
            background: rgba(255, 65, 0, 0.10);
            border-left: 4px solid var(--surf-danger);
        }}
        
        .alert.warning {{
            background: rgba(251, 192, 45, 0.14);
            border-left: 4px solid var(--surf-warning);
        }}
        
        .alert.advisory {{
            background: rgba(136, 200, 210, 0.18);
            border-left: 4px solid var(--surf-blue-bright);
        }}
        
        .alert-icon {{
            font-size: 1.3em;
        }}
        
        .alert-message {{
            font-weight: 500;
        }}
        
        .alert-meta {{
            display: flex;
            gap: 15px;
            color: #666;
            font-size: 0.85em;
            margin-top: 5px;
        }}
        
        .no-alerts {{
            color: #27ae60;
            padding: 20px;
            text-align: center;
            font-size: 1.1em;
        }}

        .control-panels {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}

        .panel {{
            background: rgba(255,255,255,0.96);
            border-radius: 24px;
            padding: 22px;
            box-shadow: var(--surf-shadow);
            border: 1px solid rgba(38, 39, 42, 0.06);
        }}

        .panel h3 {{
            color: var(--surf-ink);
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.98rem;
        }}

        .panel p {{
            color: var(--surf-muted);
            margin-bottom: 12px;
            line-height: 1.4;
        }}

        .stack {{
            display: grid;
            gap: 10px;
        }}

        .form-input {{
            width: 100%;
            padding: 12px;
            border: 1px solid var(--surf-border);
            border-radius: 14px;
            font-size: 0.95em;
            background: rgba(255,255,255,0.96);
            color: var(--surf-ink);
        }}

        .checkbox-grid {{
            display: grid;
            gap: 8px;
            max-height: 220px;
            overflow-y: auto;
            padding: 10px;
            border: 1px solid var(--surf-border);
            border-radius: 18px;
            background: linear-gradient(180deg, #f8fbfd 0%, #eef5f6 100%);
        }}

        .checkbox-option {{
            display: flex;
            gap: 8px;
            align-items: flex-start;
            font-size: 0.92em;
        }}

        .button-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}

        .action-btn {{
            border: none;
            border-radius: 999px;
            padding: 11px 16px;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: 600;
            transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
            box-shadow: 0 10px 18px rgba(38, 39, 42, 0.12);
        }}

        .action-btn:hover {{
            transform: translateY(-1px);
        }}

        .action-btn.primary {{
            background: var(--surf-blue);
            color: white;
        }}

        .action-btn.secondary {{
            background: rgba(136, 200, 210, 0.26);
            color: var(--surf-ink);
        }}

        .action-btn.danger {{
            background: rgba(255, 65, 0, 0.14);
            color: #a93226;
        }}

        .action-btn.small {{
            padding: 8px 10px;
            font-size: 0.82em;
        }}

        .subscription-list {{
            display: grid;
            gap: 8px;
        }}

        .subscription-item {{
            border: 1px solid var(--surf-border);
            border-radius: 18px;
            padding: 14px;
            background: linear-gradient(180deg, #ffffff 0%, #f7fafb 100%);
        }}

        .subscription-actions {{
            margin-top: 10px;
        }}

        .subscription-phone {{
            font-weight: 700;
            color: var(--surf-ink);
            margin-bottom: 4px;
        }}

        .subscription-sites {{
            font-size: 0.85em;
            color: var(--surf-muted);
        }}

        .helper-note {{
            font-size: 0.85em;
            color: var(--surf-muted);
        }}

        .action-result {{
            margin-top: 12px;
            border-radius: 8px;
            padding: 12px;
            display: none;
            font-size: 0.9em;
            white-space: pre-wrap;
        }}

        .action-result.success {{
            display: block;
            background: rgba(37, 214, 112, 0.12);
            color: #146b37;
        }}

        .action-result.error {{
            display: block;
            background: rgba(255, 65, 0, 0.10);
            color: #a93226;
        }}

        .empty-state {{
            color: #667;
            font-size: 0.9em;
        }}

        .simulation-pill {{
            display: inline-block;
            margin-top: 8px;
            padding: 4px 8px;
            border-radius: 999px;
            background: #fff3cd;
            color: #856404;
            font-size: 0.75em;
            font-weight: 700;
            text-transform: uppercase;
        }}
        
        /* Stations Section */
        .stations-section {{
            margin-bottom: 20px;
        }}
        
        .stations-section h2 {{
            color: var(--surf-ink);
            margin-bottom: 15px;
            font-size: 1.1em;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        
        .stations-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 15px;
        }}
        
        .station-card {{
            background: rgba(255,255,255,0.96);
            border-radius: 22px;
            padding: 18px;
            box-shadow: var(--surf-shadow);
            transition: transform 0.2s, box-shadow 0.2s;
            border: 1px solid rgba(38, 39, 42, 0.06);
        }}
        
        .station-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }}
        
        .station-card.safe {{
            border-left: 4px solid #27ae60;
        }}
        
        .station-card.posted {{
            border-left: 4px solid #f39c12;
        }}
        
        .station-card.cso {{
            border-left: 4px solid var(--surf-danger);
            background: linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(255, 238, 229, 0.96) 100%);
        }}
        
        .station-card.not_sampled {{
            border-left: 4px solid #95a5a6;
            opacity: 0.8;
        }}
        
        .station-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }}
        
        .station-header h3 {{
            font-size: 1em;
            color: var(--surf-ink);
            flex: 1;
        }}
        
        .status-badge {{
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
        }}
        
        .status-badge.safe {{
            background: #d4edda;
            color: #155724;
        }}
        
        .status-badge.posted {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .status-badge.cso {{
            background: #f8d7da;
            color: #721c24;
            animation: badge-pulse 2s infinite;
        }}
        
        .status-badge.not_sampled {{
            background: #e9ecef;
            color: #6c757d;
        }}
        
        @keyframes badge-pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.7; }}
        }}
        
        .station-meta {{
            display: flex;
            justify-content: space-between;
            color: #888;
            font-size: 0.85em;
            gap: 10px;
        }}

        .station-meta + .station-meta {{
            margin-top: 6px;
        }}

        .lab-link {{
            color: var(--surf-blue);
            text-decoration: none;
            font-weight: 600;
        }}

        .lab-link:hover {{
            text-decoration: underline;
        }}
        
        .station-warning {{
            color: #e74c3c;
            font-size: 0.85em;
            margin-top: 8px;
            padding: 8px;
            background: #fdecea;
            border-radius: 6px;
        }}
        
        /* Footer */
        footer {{
            background: linear-gradient(180deg, rgba(38, 39, 42, 0.98) 0%, rgba(38, 39, 42, 0.94) 100%);
            border-radius: 24px;
            padding: 24px;
            text-align: center;
            box-shadow: var(--surf-shadow);
        }}
        
        footer p {{
            margin-bottom: 8px;
            color: rgba(255,255,255,0.76);
        }}
        
        footer a {{
            color: white;
            text-decoration: none;
        }}
        
        footer a:hover {{
            text-decoration: underline;
        }}
        
        .hotline {{
            font-size: 1.1em;
            color: white;
            font-weight: 500;
        }}
        
        .refresh-btn {{
            background: rgba(255,255,255,0.14);
            color: white;
            border: none;
            padding: 12px 18px;
            border-radius: 999px;
            cursor: pointer;
            font-size: 0.9em;
            border: 1px solid rgba(255,255,255,0.18);
        }}
        
        .refresh-btn:hover {{
            background: rgba(255,255,255,0.22);
        }}
        
        @media (max-width: 600px) {{
            header {{
                padding: 24px 20px;
            }}

            .header-row {{
                flex-direction: column;
                align-items: flex-start;
            }}

            .hero-logos {{
                justify-content: flex-start;
            }}

            .hero-logo.bwtf,
            .hero-logo.surfrider {{
                width: min(100%, 280px);
            }}
            
            .summary {{
                grid-template-columns: repeat(2, 1fr);
            }}
            
            .stations-grid {{
                grid-template-columns: 1fr;
            }}
            
            .cso-banner {{
                flex-direction: column;
                text-align: center;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-row">
                <div class="hero-copy">
                    <div class="hero-kicker">Surfrider Foundation • Blue Water Task Force</div>
                    <h1>SF Beach Water Quality Dashboard</h1>
                    <div class="subtitle">
                        Live SFPUC status, SF Gov lab results, and subscriber alert testing for the Surfrider San Francisco Blue Water Task Force.
                    </div>
                    <div class="hero-meta">
                        <span class="data-source live">Real-time SFPUC Data</span>
                        <span class="data-source">Updated {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</span>
                    </div>
                </div>
                <div class="hero-logos">
                    <div class="hero-logo bwtf">
                        <img src="{BWTF_LOGO_URL}" alt="Blue Water Task Force logo">
                    </div>
                    <div class="hero-logo surfrider">
                        <img src="{SURFRIDER_LOGO_URL}" alt="Surfrider Foundation logo">
                    </div>
                    <button class="refresh-btn" onclick="location.reload()">🔄 Refresh</button>
                </div>
            </div>
        </header>
        
        {error_html}
        {rain_html}
        {cso_banner}
        
        <div class="summary">
            <div class="summary-card cso">
                <div class="summary-number">{cso_count}</div>
                <div class="summary-label">🚨 Active CSO</div>
            </div>
            <div class="summary-card posted">
                <div class="summary-number">{posted_count}</div>
                <div class="summary-label">⚠️ Posted</div>
            </div>
            <div class="summary-card safe">
                <div class="summary-number">{safe_count}</div>
                <div class="summary-label">✅ Safe</div>
            </div>
            <div class="summary-card total">
                <div class="summary-number">{total_stations}</div>
                <div class="summary-label">📍 Total Stations</div>
            </div>
            {tide_html}
        </div>

        <div class="control-panels">
            <section class="panel">
                <h3>Email + Optional SMS Alerts</h3>
                <p>Choose the CSO-eligible sites you care about. Email is the primary free alert path, and SMS via carrier gateway is optional.</p>
                <form id="subscription-form" class="stack">
                    <input class="form-input" type="email" name="email" placeholder="Email address" required>
                    <input class="form-input" type="tel" name="phone_number" placeholder="Phone number for optional SMS (e.g. 4155551234)">
                    <select class="form-input" name="carrier">
                        <option value="">Skip SMS / email only</option>
                        {carrier_options_html}
                    </select>
                    <div class="checkbox-grid">
                        {subscription_site_options}
                    </div>
                    <div class="button-row">
                        <button class="action-btn primary" type="submit">Save Subscription</button>
                    </div>
                </form>
                <div class="helper-note">Only sites mapped to CSO outfalls are available for selection. Leave carrier blank if you only want email alerts.</div>
                <div class="action-result" id="subscription-result"></div>
            </section>

            <section class="panel">
                <h3>Simulator</h3>
                <p>Inject a simulated CSO event, then send a test dispatch through the same matching logic the real alert flow uses.</p>
                <form id="simulation-form" class="stack">
                    <div class="checkbox-grid">
                        {simulation_site_options}
                    </div>
                    <div class="button-row">
                        <button class="action-btn secondary" type="submit">Save Simulated CSO</button>
                        <button class="action-btn danger" type="button" onclick="clearSimulation()">Clear Simulation</button>
                    </div>
                </form>
                <div class="helper-note">Currently simulated sites: {simulated_sites_html}</div>
                <div class="button-row" style="margin-top: 12px;">
                    <button class="action-btn primary" type="button" onclick="dispatchSubscriptionAlerts('email')">Send Email Alerts</button>
                    <button class="action-btn secondary" type="button" onclick="dispatchSubscriptionAlerts('sms')">Send SMS Alerts</button>
                </div>
                <div class="helper-note">Email uses SMTP. SMS uses carrier gateway if phone + carrier are set, otherwise it falls back to preview. These buttons send for all currently matching subscriptions.</div>
                <div class="action-result" id="simulation-result"></div>
            </section>

            <section class="panel">
                <h3>Saved Subscribers</h3>
                <p>Current email/SMS subscriptions stored on disk for the dashboard.</p>
                <div class="subscription-list">
                    {current_subscription_html}
                </div>
            </section>
        </div>
        
        <div class="alerts-section">
            <h2 class="section-title">⚠️ Active Alerts</h2>
            {alerts_html}
        </div>
        
        <div class="stations-section">
            <h2>📍 Beach Stations</h2>
            <div class="stations-grid">
                {station_cards}
            </div>
        </div>
        
        <footer>
            <p class="hotline">📞 Beach Hotline: 1-877-SFBEACH (1-877-732-3224) or 415-242-2214</p>
            <p style="color: #e74c3c; font-weight: 500;">⚠️ Avoid water contact during and 72 hours after rain events</p>
            <p>
                <a href="https://webapps.sfpuc.org/sapps/beachesandbay.html" target="_blank">SFPUC Beach Map</a> | 
                <a href="https://sf.surfrider.org/blue-water-task-force/" target="_blank">Surfrider BWTF</a> |
                <a href="https://data.sfgov.org/Energy-and-Environment/Beach-Water-Quality-Monitoring/v3fv-x3ux" target="_blank">SF Gov Data</a> |
                <a href="https://www.sfpuc.gov/programs/ocean-and-beach-monitoring" target="_blank">SFPUC Monitoring Program</a> |
                <a href="/api/debug/sfpuc" target="_blank">Debug SFPUC Payload</a>
            </p>
            <p style="margin-top: 15px; font-size: 0.85em; color: #888;">
                Data refreshes automatically every 5 minutes. Sources: SFPUC LIMS API, SF Gov Open Data, NWS Weather, NOAA Tides.
            </p>
        </footer>
    </div>
    <script>
        function setResult(id, message, isError = false) {{
            const element = document.getElementById(id);
            element.className = 'action-result ' + (isError ? 'error' : 'success');
            element.textContent = message;
        }}

        async function postJson(url, payload) {{
            const response = await fetch(url, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }});
            const data = await response.json();
            if (!response.ok || data.ok === false) {{
                throw new Error(data.error || 'Request failed');
            }}
            return data;
        }}

        document.getElementById('subscription-form').addEventListener('submit', async (event) => {{
            event.preventDefault();
            const form = event.currentTarget;
            const payload = {{
                email: form.email.value,
                phone_number: form.phone_number.value,
                carrier: form.carrier.value,
                station_ids: Array.from(form.querySelectorAll('input[name="station_ids"]:checked')).map((input) => input.value)
            }};
            try {{
                const data = await postJson('/api/subscriptions', payload);
                const smsSummary = data.subscription.phone_number
                    ? ` SMS: ${{data.subscription.phone_number}} (${{data.subscription.carrier || 'no carrier'}}).`
                    : ' Email only.';
                setResult('subscription-result', `Saved ${{data.subscription.email}} for ${{data.subscription.station_ids.length}} site(s).${{smsSummary}}`);
                window.setTimeout(() => window.location.reload(), 700);
            }} catch (error) {{
                setResult('subscription-result', error.message, true);
            }}
        }});

        async function deleteSubscription(email, phoneNumber) {{
            const target = email || phoneNumber;
            if (!target) {{
                setResult('subscription-result', 'This subscriber record is missing both email and phone.', true);
                return;
            }}
            if (!window.confirm(`Clear subscriber ${{target}}?`)) {{
                return;
            }}
            try {{
                await postJson('/api/subscriptions/delete', {{
                    email: email || '',
                    phone_number: phoneNumber || ''
                }});
                setResult('subscription-result', `Cleared subscriber ${{target}}.`);
                window.setTimeout(() => window.location.reload(), 500);
            }} catch (error) {{
                setResult('subscription-result', error.message, true);
            }}
        }}

        document.getElementById('simulation-form').addEventListener('submit', async (event) => {{
            event.preventDefault();
            const form = event.currentTarget;
            const payload = {{
                station_ids: Array.from(form.querySelectorAll('input[name="station_ids"]:checked')).map((input) => input.value)
            }};
            try {{
                const data = await postJson('/api/simulations/cso', payload);
                setResult('simulation-result', `Saved ${{data.station_ids.length}} simulated CSO site(s).`);
                window.setTimeout(() => window.location.reload(), 700);
            }} catch (error) {{
                setResult('simulation-result', error.message, true);
            }}
        }});

        async function clearSimulation() {{
            try {{
                await postJson('/api/simulations/cso/clear', {{}});
                setResult('simulation-result', 'Cleared simulated CSO events.');
                window.setTimeout(() => window.location.reload(), 700);
            }} catch (error) {{
                setResult('simulation-result', error.message, true);
            }}
        }}

        async function dispatchSubscriptionAlerts(channel) {{
            try {{
                const data = await postJson('/api/dispatch-cso-alerts', {{ channel }});
                const summary = data.results.length
                    ? data.results.map((result) => {{
                        const destination = channel === 'email'
                            ? (result.email || 'no email')
                            : (result.phone_number || result.email || 'no destination');
                        return `${{destination}}: ${{result.station_names.join(', ')}} [${{result.delivery}}]`;
                    }}).join('\\n')
                    : `No matching subscriptions for current/simulated CSO sites on ${{channel}}.`;
                setResult('simulation-result', summary);
            }} catch (error) {{
                setResult('simulation-result', error.message, true);
            }}
        }}
    </script>
</body>
</html>
"""
    
    def _generate_station_card(self, station, status_type, is_simulated=False, lab_result=None):
        """Generate HTML for a single station card"""
        status_icon = {
            "cso": "🚨",
            "posted": "⚠️",
            "safe": "✅",
            "not_sampled": "⚪"
        }.get(status_type, "ℹ️")
        
        status_label = {
            "cso": "CSO ALERT",
            "posted": "Posted",
            "safe": "Safe",
            "not_sampled": "No Data"
        }.get(status_type, "Unknown")
        
        status_feed_date = station.sample_date.strftime('%m/%d/%Y') if station.sample_date else "N/A"
        if lab_result:
            lab_sample_date = lab_result["sample_date"].strftime('%m/%d/%Y')
            lab_details_html = f"""
                <div class="station-meta">
                    <span>Latest SF Gov lab sample: {lab_sample_date}</span>
                    <a class="lab-link" href="{lab_result['results_url']}" target="_blank" rel="noopener noreferrer">View results</a>
                </div>
            """
        else:
            lab_details_html = """
                <div class="station-meta">
                    <span>Latest SF Gov lab sample: N/A</span>
                    <span></span>
                </div>
            """
        
        warning_html = ""
        if status_type == "cso":
            warning_html = """
                <div class="station-warning">
                    ⚠️ Combined sewer discharge detected. Avoid water contact for 72 hours.
                </div>
            """
        elif status_type == "posted":
            warning_html = """
                <div class="station-warning" style="background: #fff3cd; color: #856404;">
                    ⚠️ Elevated bacteria levels. Water contact not recommended.
                </div>
            """
        elif status_type == "not_sampled":
            warning_html = """
                <div class="station-warning" style="background: #f5f5f5; color: #666;">
                    ℹ️ This location is not routinely sampled. No data available.
                </div>
            """

        if is_simulated:
            warning_html += """
                <div class="simulation-pill">
                    Simulated for testing
                </div>
            """
        
        # Add CSS class for not_sampled cards
        card_class = status_type
        
        return f"""
            <div class="station-card {card_class}">
                <div class="station-header">
                    <h3>{station.station_name}</h3>
                    <span class="status-badge {status_type}">{status_icon} {status_label}</span>
                </div>
                <div class="station-meta">
                    <span>SFPUC status feed date: {status_feed_date}</span>
                    <span>ID: {station.station_id}</span>
                </div>
                {lab_details_html}
                {warning_html}
            </div>
        """


def main():
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SF Beach Water Quality Dashboard                            ║
║  Surfrider SF Blue Water Task Force                          ║
╠══════════════════════════════════════════════════════════════╣
║  🌐 Open in browser: http://localhost:{PORT}                   ║
║  📡 Real-time data from SFPUC LIMS API                       ║
║  🔄 Auto-refresh every 5 minutes                             ║
║                                                              ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    with ReusableTCPServer(("", PORT), WaterQualityHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Shutting down dashboard...")


if __name__ == "__main__":
    main()
