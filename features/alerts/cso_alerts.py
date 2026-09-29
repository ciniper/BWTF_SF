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
from features.alerts.deliveries import sends_to_deliveries
from features.alerts.notifiers import (
    EmailNotifier,
    EmailToSMSNotifier,
    TwilioSMSNotifier,
    email_transport_configured,
)
from shared import supabase as sb
from shared.sfpuc_api import StationStatus, SFPUCStation
from features.alerts.subscriptions import SiteSubscription


from shared.paths import DATA_DIR

SIMULATED_CSO_PATH = DATA_DIR / "simulated_cso_events.json"
BWTF_LOGO_URL = "https://bwtf.surfrider.org/images/BWTF-Logo_White.png"
SURFRIDER_LOGO_URL = "https://f.hubspotusercontent20.net/hubfs/20811975/SF-Horizontal-Logo_RGB_Black_crop_small.png"


SIM_KINDS = ("cso", "posted")


class SimulatedCSOStore:
    """Simulations: station id → kind ('cso' | 'posted'). Supabase-backed
    (``simulated_cso`` table, ``kind`` column since migration 010) so the
    dashboard and the pg_cron watcher see the same simulations; falls back to
    the legacy JSON file when Supabase env is absent. An explicit ``path``
    forces JSON mode (tests). ``get_station_ids``/``set_station_ids`` remain
    for callers that only know about CSO simulations."""

    def __init__(self, path: Path | None = None):
        self._remote = path is None and sb.is_configured()
        self.path = path or SIMULATED_CSO_PATH
        if not self._remote:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rows(self) -> list[dict]:
        """Live simulation rows, tolerating a table that predates 010 (kind)
        or 011 (recipients)."""
        for select in ("station_id,kind,recipients", "station_id,kind", "station_id"):
            try:
                return sb.select("simulated_cso", {"select": select})
            except sb.SupabaseError as exc:
                if not any(col in str(exc) for col in ("kind", "recipients")):
                    raise
        return []

    def get_simulations(self) -> dict[str, str]:
        if self._remote:
            return {row["station_id"]: (row.get("kind") or "cso") for row in self._rows()}
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}
        kinds = payload.get("kinds") or {}
        return {sid: kinds.get(sid, "cso") for sid in payload.get("station_ids", [])}

    def get_recipients(self) -> list[str] | None:
        """Emails / phone numbers the TEST alerts are limited to; None = every
        subscriber of the simulated sites (the pre-011 behaviour)."""
        if self._remote:
            for row in self._rows():
                if row.get("recipients"):
                    return sorted(row["recipients"])
            return None
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            return None
        return sorted(payload["recipients"]) if payload.get("recipients") else None

    def set_simulations(self, simulations: dict[str, str], recipients: list[str] | None = None) -> dict[str, str]:
        normalized = {sid: (kind if kind in SIM_KINDS else "cso")
                      for sid, kind in sorted(simulations.items()) if sid}
        targets = sorted({r.strip().lower() for r in (recipients or []) if r and r.strip()}) or None
        if self._remote:
            sb.delete("simulated_cso", {"station_id": "neq."})  # all rows
            if normalized:
                rows = [{"station_id": sid, "kind": kind, "recipients": targets} for sid, kind in normalized.items()]
                try:
                    sb.insert("simulated_cso", rows)
                except sb.SupabaseError as exc:
                    msg = str(exc)
                    if "recipients" in msg:
                        if targets:
                            raise RuntimeError("Targeting specific subscribers needs migration 011 (db/migrations/011_simulation_recipients.sql) applied first.") from exc
                        rows = [{"station_id": sid, "kind": kind} for sid, kind in normalized.items()]
                    elif "kind" in msg:
                        if any(kind != "cso" for kind in normalized.values()):
                            raise RuntimeError("Bacteria-posting simulations need migration 010 (db/migrations/010_simulation_kind.sql) applied first.") from exc
                        rows = [{"station_id": sid} for sid in normalized]
                    else:
                        raise
                    sb.insert("simulated_cso", rows)
            return normalized
        self.path.write_text(json.dumps({
            "station_ids": sorted(normalized), "kinds": normalized, "recipients": targets,
            "updated_at": datetime.utcnow().isoformat(),
        }, indent=2))
        return normalized

    def get_station_ids(self) -> list[str]:
        return sorted(self.get_simulations())

    def set_station_ids(self, station_ids: list[str], kind: str = "cso") -> list[str]:
        return sorted(self.set_simulations({sid: kind for sid in station_ids if sid}))

    def clear(self) -> None:
        self.set_simulations({})


def get_cso_eligible_stations(stations: list[SFPUCStation]) -> list[SFPUCStation]:
    return sorted(
        [station for station in stations if station.cso_outfalls],
        key=lambda station: station.station_name,
    )


def apply_simulated_cso(stations: list[SFPUCStation], simulations) -> list[SFPUCStation]:
    """Overlay simulations on the live stations. ``simulations`` is either a
    {station_id: kind} mapping or a plain list of ids (all treated as CSO).
    A 'cso' simulation sets the CSO flag; a 'posted' simulation raises the
    station to POSTED. Neither ever lowers a real status."""
    if not simulations:
        return stations
    kinds = simulations if isinstance(simulations, dict) else {sid: "cso" for sid in simulations}
    updated = []
    for station in stations:
        kind = kinds.get(station.station_id)
        if kind == "cso":
            updated.append(replace(
                station,
                has_cso=True,
                cso_station_id=station.cso_station_id or f"SIM-{station.station_id}",
            ))
        elif kind == "posted" and station.status.value in ("safe", "not_sampled", "not_routinely_sampled", "unknown"):
            updated.append(replace(station, status=StationStatus.POSTED))
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
        deliveries: list[dict] = []   # one row per message actually attempted (015)

        if channel == "email":
            if smtp_email_configured and subscription.email:
                try:
                    notifier = EmailNotifier(to_emails=[subscription.email])
                    delivered = notifier.send_message(rendered["subject"], rendered["text_body"],
                                                      [subscription.email], rendered["html_body"])
                    deliveries = sends_to_deliveries(notifier, "email", rendered, is_simulated)
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
                    deliveries = sends_to_deliveries(notifier, "sms", rendered, is_simulated)
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
            "deliveries": deliveries,
        })

    return results
