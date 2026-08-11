"""Alert dashboard page — live SFPUC status, bacteria, subscriptions, simulations.

Owns /alerts and the alert APIs/POST actions. Implemented as a mixin on the
request handler in the Flask app (app/wsgi.py), which supplies the HTTP helpers
(self._send_json, self._read_request_data) and shared clients
(self.combined_monitor, self.sfpuc_api, self.subscription_store,
self.simulated_cso_store, self.env_context).
"""
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime

from flask import render_template

from features.alerts.cso_alerts import (
    apply_simulated_cso,
    dispatch_subscription_alerts,
    get_cso_eligible_stations,
)

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


class AlertsRoutes:
    """Alert-dashboard routes, mixed into the unified server handler."""

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
        
        return render_template(
            "alerts/dashboard.html",
            generated=datetime.now().strftime('%B %d, %Y at %I:%M %p'),
            bwtf_logo=BWTF_LOGO_URL,
            surfrider_logo=SURFRIDER_LOGO_URL,
            error_html=error_html,
            rain_html=rain_html,
            cso_banner=cso_banner,
            tide_html=tide_html,
            cso_count=cso_count,
            posted_count=posted_count,
            safe_count=safe_count,
            total_stations=total_stations,
            carrier_options_html=carrier_options_html,
            subscription_site_options=subscription_site_options,
            simulation_site_options=simulation_site_options,
            simulated_sites_html=simulated_sites_html,
            current_subscription_html=current_subscription_html,
            alerts_html=alerts_html,
            station_cards=station_cards,
        )
    
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
            # The status feed reports a newer sample than SF Gov has published:
            # lab numbers take ~24h incubation + a nightly load, so postings can
            # lead the published results by days. Say so instead of confusing.
            pending_html = ""
            if station.sample_date and station.sample_date.date() > lab_result["sample_date"].date():
                pending_html = f"""
                <div class="station-meta">
                    <span class="lab-pending">⏳ Lab numbers for the {status_feed_date} sample not yet published</span>
                </div>
                """
            lab_details_html = f"""
                <div class="station-meta">
                    <span>Latest SF Gov lab sample: {lab_sample_date}</span>
                    <a class="lab-link" href="{lab_result['results_url']}" target="_blank" rel="noopener noreferrer">View results</a>
                </div>
                {pending_html}
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


