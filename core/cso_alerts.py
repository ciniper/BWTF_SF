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

from core.notifiers import EmailToSMSNotifier, TwilioSMSNotifier
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


def dispatch_subscription_alerts(
    subscriptions: list[SiteSubscription],
    stations: list[SFPUCStation],
    simulated_station_ids: list[str],
) -> list[dict]:
    active_cso_by_station_id = {
        station.station_id: station for station in stations if station.has_cso
    }

    results = []
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
        is_simulated = any(station_id in set(simulated_station_ids) for station_id in station_ids)
        message = format_cso_subscription_message(matching_stations, simulated=is_simulated)

        delivery = "preview"
        delivered = False
        error = None

        if smtp_gateway_configured and subscription.carrier:
            try:
                gateway_email = EmailToSMSNotifier.phone_to_gateway(subscription.phone_number, subscription.carrier)
                notifier = EmailToSMSNotifier(to_sms_emails=[gateway_email])
                delivered = notifier.send_message(message)
                delivery = "email_to_sms" if delivered else "failed"
            except Exception as exc:  # pragma: no cover - defensive around network
                delivery = "failed"
                error = str(exc)
        elif twilio_configured:
            notifier = TwilioSMSNotifier(to_numbers=[subscription.phone_number])
            try:
                delivered = notifier.send_message(message)
                delivery = "twilio" if delivered else "failed"
            except Exception as exc:  # pragma: no cover - defensive around network
                delivery = "failed"
                error = str(exc)

        results.append({
            "phone_number": subscription.phone_number,
            "carrier": subscription.carrier,
            "station_ids": station_ids,
            "station_names": [station.station_name for station in matching_stations],
            "message": message,
            "delivery": delivery,
            "delivered": delivered,
            "simulated": is_simulated,
            "error": error,
        })

    return results
