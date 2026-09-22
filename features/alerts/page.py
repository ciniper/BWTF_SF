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

from shared.outfalls import OBSERVED, OUTFALLS, STATION_OUTFALLS

from features.alerts.cso_alerts import (
    SIM_KINDS,
    apply_simulated_cso,
    dispatch_subscription_alerts,
    get_cso_eligible_stations,
)
from features.alerts.notifiers import email_transport_configured

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
        """(stations with the simulation overlay applied, simulated station ids)."""
        live_stations = self.sfpuc_api.fetch_stations()
        simulations = self.simulated_cso_store.get_simulations()
        stations = apply_simulated_cso(live_stations, simulations)
        return stations, sorted(simulations)

    def _status_view(self, stations, simulations, lab_results_lookup):
        """Counts, CSO banner and station cards for one view of the stations —
        the real feed, or the feed with simulations overlaid. Since migration
        009 the watcher keeps those two apart; the dashboard shows them as
        two tabs so a simulation never reads as a real event."""
        cso_events = [st for st in stations if st.has_cso]
        posted = [st for st in stations if st.status.value == "posted"]
        safe = [st for st in stations if st.status.value == "safe"]
        not_sampled = [st for st in stations if st.status.value in ("not_sampled", "not_routinely_sampled")]
        banner = ""
        if cso_events:
            names = ", ".join(e.station_name for e in cso_events)
            simulated_note = " (simulated)" if all(e.station_id in simulations for e in cso_events) else ""
            banner = f"""
            <div class="cso-banner">
                <div class="cso-icon"><svg class="ic" style="width:48px;height:48px"><use href="#i-octagon-alert"/></svg></div>
                <div class="cso-content">
                    <div class="cso-title">ACTIVE COMBINED SEWER OVERFLOW{simulated_note.upper()}</div>
                    <div class="cso-message">CSO detected at: <strong>{names}</strong></div>
                    <div class="cso-warning">Sewage discharge has occurred within the last 24-72 hours. Avoid water contact at affected beaches.</div>
                </div>
            </div>
            """
        cards, added = "", set()
        for group, kind in ((cso_events, "cso"), (posted, "posted"), (safe, "safe"), (not_sampled, "not_sampled")):
            for st in group:
                if st.station_id in added:
                    continue
                cards += self._generate_station_card(st, kind, st.station_id in simulations,
                                                     lab_results_lookup.get(st.station_name))
                added.add(st.station_id)
        return {"cso_count": len(cso_events), "posted_count": len(posted), "safe_count": len(safe),
                "not_sampled_count": len(not_sampled), "total_stations": len(stations),
                "cso_banner": banner, "station_cards": cards, "cso_events": cso_events,
                "posted_stations": posted, "safe_stations": safe, "not_sampled_stations": not_sampled}
    
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

    def send_api_simulated_cso(self):  # ids + kinds of the active simulations
        try:
            simulations = self.simulated_cso_store.get_simulations()
            payload = {"station_ids": sorted(simulations), "simulations": simulations,
                       "recipients": self.simulated_cso_store.get_recipients()}
        except Exception as e:
            payload = {"error": str(e)}
        self._send_json(payload)

    def send_api_watcher(self):
        """Report the auto-alert watcher's last poll: transitions + dispatches."""
        from features.alerts.watcher import get_last_run
        self._send_json(get_last_run())

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
            valid_station_ids = {station.station_id for station in stations}
            selected_station_ids = [station_id for station_id in station_ids if station_id in valid_station_ids]
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

            kind = data.get("kind") or "cso"
            if kind not in SIM_KINDS:
                raise ValueError(f"kind must be one of {SIM_KINDS}")
            stations, _ = self._get_dashboard_stations()
            available_station_ids = {station.station_id for station in get_cso_eligible_stations(stations)}
            selected_station_ids = [station_id for station_id in station_ids if station_id in available_station_ids]
            recipients = data.get("recipients") or []
            if isinstance(recipients, str):
                recipients = [recipients]
            saved = self.simulated_cso_store.set_simulations({sid: kind for sid in selected_station_ids}, recipients)
            self._send_json({"ok": True, "station_ids": sorted(saved), "kind": kind, "simulations": saved,
                             "recipients": self.simulated_cso_store.get_recipients()})
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
            from features.alerts.alert_log import record_dispatch
            affected = sorted({sid for r in results for sid in r["station_ids"]})
            affected_names = sorted({n for r in results for n in r["station_names"]})
            record_dispatch(
                source="manual",
                event_type="manual_dispatch",
                station_ids=affected,
                station_names=affected_names,
                recipient_count=len(results),
                channel=channel,
                simulated=any(r.get("simulated") for r in results),
                results=results,
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
                "smtp_configured": email_transport_configured(),
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)
    
    def generate_dashboard_html(self):
        """Generate the dashboard HTML with combined data"""
        
        # Rain advisory banner (tide tile retired 2026-09-22)
        rain_html = ""
        if self.env_context:
            try:
                rain_advisory = self.env_context.weather.get_rain_advisory()
                if rain_advisory.is_active:
                    rain_html = f"""
                    <div class="cso-banner" style="background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);">
                        <div class="cso-icon"><img src="/static/brand/droplet.png" alt="" style="width:56px;height:56px;background:#fff;border-radius:12px;padding:4px;display:block;"></div>
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
                        <svg class="ic brand"><use href="#i-cloud-sun-rain"/></svg> <strong>Rain in Forecast:</strong><br>{forecasts}
                        <br><small>SFPUC advises avoiding water contact during and 72 hours after rain.</small>
                    </div>
                    """
            except Exception as e:
                rain_html = f'<div class="error-banner"><svg class="ic warn"><use href="#i-triangle-alert"/></svg> Weather data unavailable: {e}</div>'
            
        
        subscriptions = self.subscription_store.list_subscriptions()

        # Fetch real-time SFPUC data — the real feed and the simulation overlay
        # are rendered as two views (tabs)
        try:
            live_stations = self.sfpuc_api.fetch_stations()
            simulations = self.simulated_cso_store.get_simulations()
            sim_stations = apply_simulated_cso(live_stations, simulations)
            stations = live_stations
            simulated_station_ids = sorted(simulations)
            simulated_station_id_set = set(simulations)
            eligible_cso_stations = get_cso_eligible_stations(live_stations)
            sfpuc_error = None
        except Exception as e:
            live_stations, sim_stations, stations = [], [], []
            simulations = {}
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
        
        real = self._status_view(live_stations, {}, lab_results_lookup)
        sim = self._status_view(sim_stations, simulations, lab_results_lookup)

        # One feed date for the whole station grid instead of one per card.
        # Stations are sampled on different days, so show the newest and, when
        # they differ, the oldest too.
        feed_dates = sorted({st.sample_date.date() for st in live_stations if st.sample_date})
        if not feed_dates:
            feed_date_html = "SFPUC status feed · no sample dates published"
        elif feed_dates[0] == feed_dates[-1]:
            feed_date_html = f"SFPUC status feed · samples {feed_dates[-1].strftime('%m/%d/%Y')}"
        else:
            feed_date_html = (f"SFPUC status feed · latest samples {feed_dates[-1].strftime('%m/%d/%Y')}"
                              f" <small>(oldest {feed_dates[0].strftime('%m/%d/%Y')})</small>")

        # Generate alerts section
        alerts_html = ""
        if alerts:
            for alert in alerts:
                severity_class = "critical" if alert.severity == "critical" else "warning" if alert.severity == "warning" else "advisory"
                icon = '<svg class="ic danger"><use href="#i-octagon-alert"/></svg>' if alert.severity == "critical" else '<svg class="ic warn"><use href="#i-triangle-alert"/></svg>' if alert.severity == "warning" else '<svg class="ic brand"><use href="#i-info"/></svg>'
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
            alerts_html = '<div class="no-alerts"><svg class="ic ok"><use href="#i-circle-check"/></svg> No active alerts</div>'
        
        # Error message if SFPUC API failed
        error_html = ""
        if sfpuc_error:
            error_html = f"""
            <div class="error-banner">
                <svg class="ic warn"><use href="#i-triangle-alert"/></svg> Could not fetch real-time data: {sfpuc_error}
            </div>
            """

        # Subscriptions cover every monitored site: the watcher alerts on
        # bacteria postings as well as CSO discharges, so no site is excluded.
        subscribable_stations = sorted(stations, key=lambda s: s.station_name)
        subscription_site_options = "".join(
            f"""
            <label class="checkbox-option">
                <input type="checkbox" name="station_ids" value="{station.station_id}">
                <span>{station.station_name}</span>
            </label>
            """
            for station in subscribable_stations
        ) or '<div class="empty-state">No sites are available right now.</div>'

        # Every station has at least one registry outfall, but SFPUC's own feed
        # only ever POSTED 18 of 20 during the 2016-17 discharges (never China
        # Beach or Crane Cove) — say so on the form rather than hard-exclude.
        observed_cso_stations = {
            sid for sid, oids in STATION_OUTFALLS.items()
            if any(OUTFALLS[o].evidence == OBSERVED for o in oids)
        }
        simulation_site_options = "".join(
            f"""
            <label class="checkbox-option">
                <input type="checkbox" name="station_ids" value="{station.station_id}" {'checked' if station.station_id in simulated_station_id_set else ''}>
                <span>{station.station_name}{'' if station.station_id in observed_cso_stations else ' <small class="mute">(never seen posted for a discharge)</small>'}</span>
            </label>
            """
            for station in eligible_cso_stations
        ) or '<div class="empty-state">No sites are available right now.</div>'

        station_name_by_id = {
            station.station_id: station.station_name
            for station in subscribable_stations
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

        kind_label = {"cso": "CSO", "posted": "bacteria posting"}
        simulated_site_names = [
            f"{station.station_name} ({kind_label.get(simulations.get(station.station_id), 'CSO')})"
            for station in eligible_cso_stations
            if station.station_id in simulated_station_id_set
        ]
        simulated_sites_html = ", ".join(simulated_site_names) if simulated_site_names else "None"
        active_kind = next(iter(simulations.values()), "cso") if simulations else "cso"
        try:
            sim_recipients = self.simulated_cso_store.get_recipients() or []
        except Exception:
            sim_recipients = []
        if simulations:
            simulated_sites_html += (" — TEST alerts limited to " + ", ".join(sim_recipients)) if sim_recipients \
                else " — TEST alerts go to every subscriber of those sites"
        # Who a simulation may reach. Default (nothing ticked) = every subscriber
        # of the simulated sites, the real path; ticking names narrows it.
        recipient_options_html = "".join(
            f"""
            <label class="checkbox-option">
                <input type="checkbox" name="recipients" value="{(sub.email or sub.phone_number).lower()}" {'checked' if (sub.email or sub.phone_number).lower() in sim_recipients else ''}>
                <span>{sub.email or sub.phone_number} <small class="mute">· {len(sub.station_ids)} site{'s' if len(sub.station_ids) != 1 else ''}</small></span>
            </label>
            """
            for sub in subscriptions if (sub.email or sub.phone_number)
        ) or '<div class="empty-state">No subscribers yet.</div>'
        
        return render_template(
            "alerts/dashboard.html",
            generated=datetime.now().strftime('%B %d, %Y at %I:%M %p'),
            bwtf_logo=BWTF_LOGO_URL,
            error_html=error_html,
            rain_html=rain_html,
            real=real,
            sim=sim,
            simulation_count=len(simulations),
            active_kind=active_kind,
            carrier_options_html=carrier_options_html,
            subscription_site_options=subscription_site_options,
            simulation_site_options=simulation_site_options,
            simulated_sites_html=simulated_sites_html,
            recipient_options_html=recipient_options_html,
            subscriber_count=len(subscriptions),
            current_subscription_html=current_subscription_html,
            alerts_html=alerts_html,
            feed_date_html=feed_date_html,
        )
    
    def _generate_station_card(self, station, status_type, is_simulated=False, lab_result=None):
        """Generate HTML for a single station card"""
        status_icon = {
            "cso": '<svg class="ic"><use href="#i-octagon-alert"/></svg>',
            "posted": '<svg class="ic"><use href="#i-triangle-alert"/></svg>',
            "safe": '<svg class="ic"><use href="#i-circle-check"/></svg>',
            "not_sampled": '<svg class="ic"><use href="#i-circle-dashed"/></svg>'
        }.get(status_type, '<svg class="ic"><use href="#i-info"/></svg>')
        
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
                    <span class="lab-pending"><svg class="ic warn"><use href="#i-hourglass"/></svg> Lab numbers for the {status_feed_date} sample not yet published</span>
                </div>
                """
            lab_details_html = f"""
                <div class="station-meta">
                    <span>Latest sample data: <a class="lab-link" href="{lab_result['results_url']}" target="_blank" rel="noopener noreferrer" title="Open the lab results on SF Gov Open Data">{lab_sample_date}</a></span>
                    <span>ID: {station.station_id}</span>
                </div>
                {pending_html}
            """
        else:
            lab_details_html = f"""
                <div class="station-meta">
                    <span>Latest sample data: N/A</span>
                    <span>ID: {station.station_id}</span>
                </div>
            """
        
        warning_html = ""
        if status_type == "cso":
            warning_html = """
                <div class="station-warning">
                    <svg class="ic"><use href="#i-octagon-alert"/></svg> Combined sewer discharge detected. Avoid water contact for 72 hours.
                </div>
            """
        elif status_type == "posted":
            warning_html = """
                <div class="station-warning" style="background: #fff3cd; color: #856404;">
                    <svg class="ic"><use href="#i-triangle-alert"/></svg> Elevated bacteria levels. Water contact not recommended.
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
                {lab_details_html}
                {warning_html}
            </div>
        """


