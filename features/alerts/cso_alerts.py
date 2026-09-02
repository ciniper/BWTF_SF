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

from features.alerts.render import render_alert
from features.alerts.notifiers import (
    EmailNotifier,
    EmailToSMSNotifier,
    TwilioSMSNotifier,
    email_transport_configured,
)
from shared import supabase as sb
from shared.sfpuc_api import SFPUCStation
from features.alerts.subscriptions import SiteSubscription


from shared.paths import DATA_DIR

SIMULATED_CSO_PATH = DATA_DIR / "simulated_cso_events.json"
BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"


class SimulatedCSOStore:
    """Simulated-CSO station ids. Supabase-backed (``simulated_cso`` table) so
    the in-process watcher AND the Phase 2 pg_cron shadow path see the same
    simulations; falls back to the legacy JSON file when Supabase env is
    absent. An explicit ``path`` forces JSON mode (tests)."""

    def __init__(self, path: Path | None = None):
        self._remote = path is None and sb.is_configured()
        self.path = path or SIMULATED_CSO_PATH
        if not self._remote:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_station_ids(self) -> list[str]:
        if self._remote:
            rows = sb.select("simulated_cso", {"select": "station_id"})
            return sorted({row["station_id"] for row in rows})
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return []
        return sorted(set(payload.get("station_ids", [])))

    def set_station_ids(self, station_ids: list[str]) -> list[str]:
        normalized = sorted({station_id for station_id in station_ids if station_id})
        if self._remote:
            sb.delete("simulated_cso", {"station_id": "neq."})  # all rows
            if normalized:
                sb.insert("simulated_cso", [{"station_id": sid} for sid in normalized])
            return normalized
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
        # Shared renderer (same output as the production pg sender) — manual
        # dispatches are indistinguishable from automatic ones in the inbox.
        rendered = render_alert(
            [{"station_id": s.station_id, "station_name": s.station_name, "to": "cso"}
             for s in matching_stations],
            is_simulated,
        )
        message = rendered["sms_text"]

        delivery = "preview"
        delivered = False
        error = None

        if channel == "email":
            if smtp_email_configured and subscription.email:
                try:
                    notifier = EmailNotifier(to_emails=[subscription.email])
                    delivered = notifier.send_message(rendered["subject"], rendered["text_body"],
                                                      [subscription.email], rendered["html_body"])
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
