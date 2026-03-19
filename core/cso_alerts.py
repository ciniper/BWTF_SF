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
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.notifiers import EmailNotifier, EmailToSMSNotifier, TwilioSMSNotifier
from core.sfpuc_api import SFPUCStation
from core.subscriptions import SiteSubscription


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SIMULATED_CSO_PATH = DATA_DIR / "simulated_cso_events.json"


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
        "<html><body style=\"font-family: Arial, sans-serif;\">"
        f"<h2>{prefix}SF Beach CSO Alert</h2>"
        "<p>Combined sewer overflow conditions are active for your selected sites:</p>"
        f"<ul>{html_items}</ul>"
        "<p>Avoid water contact and check the latest map status here: "
        "<a href=\"https://webapps.sfpuc.org/sapps/beachesandbay.html\">SFPUC Beach Map</a></p>"
        "</body></html>"
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
    smtp_email_configured = all(
        os.environ.get(key)
        for key in ("SMTP_USERNAME", "SMTP_PASSWORD")
    )
    smtp_gateway_configured = all(
        os.environ.get(key)
        for key in ("SMTP_USERNAME", "SMTP_PASSWORD")
    )
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
