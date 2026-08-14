#!/usr/bin/env python3
"""Simulation and targeted dispatch helpers for CSO subscription alerts."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.alerts.notifiers import (
    EmailNotifier,
    EmailToSMSNotifier,
    TwilioSMSNotifier,
    email_transport_configured,
)
from shared.sfpuc_api import SFPUCStation
from features.alerts.subscriptions import SiteSubscription


from shared.paths import DATA_DIR

SIMULATED_CSO_PATH = DATA_DIR / "simulated_cso_events.json"
BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"


class SimulatedCSOStore:
    def __init__(self, path: Path | None = None):
        self.path = path or SIMULATED_CSO_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_station_ids(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return []
        return sorted(set(payload.get("station_ids", [])))

    def set_station_ids(self, station_ids: list[str]) -> list[str]:
        normalized = sorted({station_id for station_id in station_ids if station_id})
        payload = {
            "station_ids": normalized,
            "updated_at": datetime.utcnow().isoformat(),
        }
        self.path.write_text(json.dumps(payload, indent=2))
        return normalized

    def clear(self) -> None:
        self.set_station_ids([])


def get_cso_eligible_stations(stations: list[SFPUCStation]) -> list[SFPUCStation]:
    return sorted(
        [station for station in stations if station.cso_outfalls],
        key=lambda station: station.station_name,
    )


def apply_simulated_cso(stations: list[SFPUCStation], simulated_station_ids: list[str]) -> list[SFPUCStation]:
    if not simulated_station_ids:
        return stations

    simulated_set = set(simulated_station_ids)
    updated = []
    for station in stations:
        if station.station_id in simulated_set:
            updated.append(replace(
                station,
                has_cso=True,
                cso_station_id=station.cso_station_id or f"SIM-{station.station_id}",
            ))
        else:
            updated.append(station)
    return updated


def format_cso_subscription_message(stations: list[SFPUCStation], simulated: bool = False) -> str:
    site_names = [station.station_name for station in stations]
    prefix = "TEST CSO alert" if simulated else "CSO alert"
    message = f"🚨 SF Beach {prefix}: " + ", ".join(site_names)
    message += ". Avoid water contact. Map: https://webapps.sfpuc.org/sapps/beachesandbay.html"
    if len(message) > 320:
        message = message[:317] + "..."
    return message


def format_cso_email_subject(stations: list[SFPUCStation], simulated: bool = False) -> str:
    prefix = "TEST " if simulated else ""
    if len(stations) == 1:
        return f"{prefix}SF Beach CSO Alert: {stations[0].station_name}"
    return f"{prefix}SF Beach CSO Alert: {len(stations)} selected sites"


def format_cso_email_body(stations: list[SFPUCStation], simulated: bool = False) -> tuple[str, str]:
    site_lines = "\n".join(f"- {station.station_name}" for station in stations)
    prefix = "TEST " if simulated else ""
    text = (
        f"{prefix}SF Beach CSO Alert\n\n"
        "Combined sewer overflow conditions are active for your selected sites:\n"
        f"{site_lines}\n\n"
        "Avoid water contact and check the SFPUC map for the latest status:\n"
        "https://webapps.sfpuc.org/sapps/beachesandbay.html\n"
    )
    html_items = "".join(f"<li>{station.station_name}</li>" for station in stations)
    html = (
        "<html>"
        "<body style=\"margin:0; padding:24px; background:#f5f6f7; font-family:'Avenir Next','Trebuchet MS','Segoe UI',sans-serif; color:#26272a;\">"
        "<div style=\"max-width:680px; margin:0 auto; background:#ffffff; border-radius:28px; overflow:hidden; box-shadow:0 18px 40px rgba(38,39,42,0.12);\">"
        "<div style=\"background:linear-gradient(135deg, #26272a 0%, #317fb2 100%); padding:24px;\">"
        "<div style=\"display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between;\">"
        "<div style=\"display:inline-block; background:rgba(255,255,255,0.12); color:#ffffff; border:1px solid rgba(255,255,255,0.16); border-radius:999px; padding:8px 12px; font-size:12px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase;\">"
        "Surfrider SF Blue Water Task Force"
        "</div>"
        f"<img src=\"{BWTF_LOGO_URL}\" alt=\"Blue Water Task Force\" style=\"display:block; width:180px; max-width:100%; height:auto;\">"
        "</div>"
        f"<h1 style=\"margin:18px 0 8px; color:#ffffff; font-size:30px; line-height:1; text-transform:uppercase; letter-spacing:0.03em;\">{prefix}SF Beach CSO Alert</h1>"
        "<p style=\"margin:0; color:rgba(255,255,255,0.84); font-size:15px;\">Combined sewer overflow conditions are active for one or more of your selected sites.</p>"
        "</div>"
        "<div style=\"padding:24px;\">"
        f"<img src=\"{SURFRIDER_LOGO_URL}\" alt=\"Surfrider Foundation\" style=\"display:block; width:240px; max-width:100%; height:auto; margin-bottom:16px;\">"
        "<div style=\"background:rgba(255,65,0,0.10); border-radius:18px; padding:16px 18px; margin-bottom:18px;\">"
        "<p style=\"margin:0 0 10px; color:#26272a; font-weight:700;\">Affected sites</p>"
        f"<ul style=\"margin:0; padding-left:18px; color:#26272a; line-height:1.6;\">{html_items}</ul>"
        "</div>"
        "<div style=\"background:#f7fafb; border:1px solid #d9e4e8; border-radius:18px; padding:16px 18px;\">"
        "<p style=\"margin:0 0 10px; color:#26272a; font-weight:700;\">What to do</p>"
        "<p style=\"margin:0; color:#5e6a71; line-height:1.6;\">Avoid water contact and check the live map before heading out.</p>"
        "</div>"
        "<div style=\"margin-top:22px;\">"
        "<a href=\"https://webapps.sfpuc.org/sapps/beachesandbay.html\" style=\"display:inline-block; background:#317fb2; color:#ffffff; text-decoration:none; padding:12px 18px; border-radius:999px; font-weight:700;\">View SFPUC Beach Map</a>"
        "</div>"
        "</div>"
        "</div>"
        "</body>"
        "</html>"
    )
    return text, html


def dispatch_subscription_alerts(
    subscriptions: list[SiteSubscription],
    stations: list[SFPUCStation],
    simulated_station_ids: list[str],
    channel: str = "email",
) -> list[dict]:
    channel = (channel or "email").strip().lower()
    if channel not in {"email", "sms"}:
        raise ValueError("Channel must be 'email' or 'sms'.")

    active_cso_by_station_id = {
        station.station_id: station for station in stations if station.has_cso
    }
    simulated_station_id_set = set(simulated_station_ids)

    results = []
    # Brevo HTTP API or SMTP creds — either enables the email-based channels.
    smtp_email_configured = email_transport_configured()
    smtp_gateway_configured = email_transport_configured()
    twilio_configured = all(
        os.environ.get(key)
        for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    )

    for subscription in subscriptions:
        matching_stations = [
            active_cso_by_station_id[station_id]
            for station_id in subscription.station_ids
            if station_id in active_cso_by_station_id
        ]
        if not matching_stations:
            continue

        station_ids = [station.station_id for station in matching_stations]
        is_simulated = any(station_id in simulated_station_id_set for station_id in station_ids)
        message = format_cso_subscription_message(matching_stations, simulated=is_simulated)

        delivery = "preview"
        delivered = False
        error = None

        if channel == "email":
            if smtp_email_configured and subscription.email:
                try:
                    subject = format_cso_email_subject(matching_stations, simulated=is_simulated)
                    text_body, html_body = format_cso_email_body(matching_stations, simulated=is_simulated)
                    notifier = EmailNotifier(to_emails=[subscription.email])
                    delivered = notifier.send_message(subject, text_body, [subscription.email], html_body)
                    delivery = "email" if delivered else "failed"
                    if not delivered:
                        error = notifier.last_error
                except Exception as exc:  # pragma: no cover
                    delivery = "failed"
                    error = str(exc)
            elif not subscription.email:
                delivery = "preview_no_email"
            else:
                delivery = "preview_email_not_configured"
        elif channel == "sms":
            if smtp_gateway_configured and subscription.carrier and subscription.phone_number:
                try:
                    gateway_email = EmailToSMSNotifier.phone_to_gateway(subscription.phone_number, subscription.carrier)
                    notifier = EmailToSMSNotifier(to_sms_emails=[gateway_email])
                    delivered = notifier.send_message(message)
                    delivery = "email_to_sms" if delivered else "failed"
                except Exception as exc:  # pragma: no cover - defensive around network
                    delivery = "failed"
                    error = str(exc)
            elif twilio_configured and subscription.phone_number:
                notifier = TwilioSMSNotifier(to_numbers=[subscription.phone_number])
                try:
                    delivered = notifier.send_message(message)
                    delivery = "twilio" if delivered else "failed"
                except Exception as exc:  # pragma: no cover - defensive around network
                    delivery = "failed"
                    error = str(exc)
            elif not subscription.phone_number:
                delivery = "preview_no_phone"
            elif not subscription.carrier:
                delivery = "preview_no_carrier"
            else:
                delivery = "preview_sms_not_configured"

        results.append({
            "email": subscription.email,
            "phone_number": subscription.phone_number,
            "carrier": subscription.carrier,
            "station_ids": station_ids,
            "station_names": [station.station_name for station in matching_stations],
            "message": message,
            "channel": channel,
            "delivery": delivery,
            "delivered": delivered,
            "simulated": is_simulated,
            "error": error,
        })

    return results
