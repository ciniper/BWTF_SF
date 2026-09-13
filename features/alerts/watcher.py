#!/usr/bin/env python3
"""Automatic alert watcher — the detection→dispatch loop (roadmap B5/B6).

A daemon thread polls the SFPUC real-time feed every couple of minutes and
compares each station's state against the last state it saw (persisted in
``data/alert_watcher_state.json``). When a station *transitions into* a more
severe state — safe→posted, safe→CSO, or posted→CSO — it emails/texts every
subscriber of that station, once per event. Postings and CSO discharges are
both alerted, labeled by type.

Edge-trigger semantics (B6):
  * Only transitions to a MORE severe state alert; recoveries just update state.
  * A station the watcher has never seen (first boot, or a fresh deploy — the
    state file is ephemeral on PaaS hosts) is baselined silently, so a deploy
    never re-alerts existing conditions.
  * State is saved after every successful poll regardless of delivery outcome,
    so a failed send is logged but never retried into spam.

Simulated CSO events (set from the dashboard) flow through this path too, with
the TEST prefix — that is the end-to-end test story for the auto-dispatcher.
Without SMTP/Twilio env vars, deliveries fall back to preview and are recorded
in the run log (visible at /api/watcher).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.alerts.alert_log import record_dispatch
from features.alerts.cso_alerts import SimulatedCSOStore, apply_simulated_cso
from features.alerts.notifiers import (
    EmailNotifier,
    EmailToSMSNotifier,
    TwilioSMSNotifier,
    email_transport_configured,
)
from features.alerts.subscriptions import SubscriptionStore
from shared import supabase as sb
from shared.paths import DATA_DIR
from shared.sfpuc_api import SFPUCRealTimeAPI
from features.alerts.render import render_alert

STATE_PATH = DATA_DIR / "alert_watcher_state.json"
DEFAULT_INTERVAL_SECONDS = 120

_SEVERITY = {"ok": 0, "posted": 1, "cso": 2}

_started = False
_run_lock = threading.Lock()
_last_run: dict = {
    "mode": os.environ.get("ALERT_WATCHER_MODE", "send").strip().lower() or "send",
    "enabled": False,
    "interval_seconds": None,
    "poll_count": 0,
    "checked_at": None,
    "baselined": False,
    "station_count": 0,
    "transitions": [],
    "dispatch_results": [],
    "error": None,
}


def classify(station) -> str:
    """Collapse a station's feed state into ok / posted / cso."""
    if station.has_cso:
        return "cso"
    if station.status.value == "posted":
        return "posted"
    return "ok"


def load_state() -> dict:
    """Last observed status per station — Supabase when configured, else JSON."""
    if sb.is_configured():
        rows = sb.select("watcher_state", {"select": "station_id,status"})
        return {row["station_id"]: row["status"] for row in rows}
    if not STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text() or "{}")
    except json.JSONDecodeError:
        return {}
    return payload.get("statuses", {}) or {}


def save_state(statuses: dict, names: dict | None = None) -> None:
    if sb.is_configured():
        names = names or {}
        sb.upsert("watcher_state", [
            {"station_id": station_id, "status": status,
             "station_name": names.get(station_id, "")}
            for station_id, status in statuses.items()
        ], on_conflict="station_id")
        return
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "statuses": statuses,
        "updated_at": datetime.now().isoformat(),
    }, indent=2))


def detect_transitions(previous: dict, stations) -> list[dict]:
    """Stations that moved to a MORE severe state than last observed.

    Stations without a previous record are baselined silently (no alert).
    """
    transitions = []
    for station in stations:
        now = classify(station)
        before = previous.get(station.station_id)
        if before is None:
            continue
        if _SEVERITY[now] > _SEVERITY.get(before, 0):
            transitions.append({"station": station, "from": before, "to": now})
    return transitions


def _format_messages(matching: list[dict], simulated: bool) -> tuple[str, str, str, str]:
    """(subject, sms_text, text_body, html_body) for one subscriber's events.

    Rendering is delegated to the shared renderer (bwtf_render_alert via RPC,
    with a byte-identical local fallback) so this path can never drift from
    the production pg sender's format — see features/alerts/render.py."""
    payload = [{"station_id": t["station"].station_id,
                "station_name": t["station"].station_name,
                "to": t["to"]} for t in matching]
    r = render_alert(payload, simulated)
    return r["subject"], r["sms_text"], r["text_body"], r["html_body"]


def dispatch_transition_alerts(subscriptions, transitions: list[dict],
                               dry_run: bool = False) -> list[dict]:
    """Alert each subscriber whose stations transitioned — email and SMS both,
    whichever the subscriber configured. Falls back to preview when the
    sending credentials aren't set (recorded, not raised).

    ``dry_run=True`` (observer mode): identical matching and message build,
    but nothing is sent — deliveries are recorded as 'would_send'. Used while
    the pg path is the live sender and this thread is the reference shadow."""
    by_station_id = {t["station"].station_id: t for t in transitions}
    smtp_configured = email_transport_configured()  # Brevo HTTP API or SMTP creds
    twilio_configured = all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))

    results = []
    for sub in subscriptions:
        matching = [by_station_id[sid] for sid in sub.station_ids if sid in by_station_id]
        if not matching:
            continue
        simulated = any(t.get("simulated") for t in matching)
        subject, sms_text, text_body, html_body = _format_messages(matching, simulated)

        if dry_run:
            deliveries = []
            if sub.email:
                deliveries.append({"channel": "email", "delivery": "would_send"})
            if sub.phone_number:
                deliveries.append({"channel": "sms", "delivery": "would_send"})
            results.append({
                "email": sub.email,
                "phone_number": sub.phone_number,
                "station_names": [t["station"].station_name for t in matching],
                "events": [{"station": t["station"].station_name, "from": t["from"], "to": t["to"]} for t in matching],
                "message": sms_text,
                "simulated": simulated,
                "deliveries": deliveries,
            })
            continue

        deliveries = []
        if sub.email:
            if smtp_configured:
                try:
                    notifier = EmailNotifier(to_emails=[sub.email])
                    ok = notifier.send_message(subject, text_body, [sub.email], html_body)
                    entry = {"channel": "email", "delivery": "email" if ok else "failed"}
                    if not ok and notifier.last_error:
                        entry["error"] = notifier.last_error
                    deliveries.append(entry)
                except Exception as exc:
                    deliveries.append({"channel": "email", "delivery": "failed", "error": str(exc)})
            else:
                deliveries.append({"channel": "email", "delivery": "preview_email_not_configured"})
        if sub.phone_number:
            if sub.carrier and smtp_configured:
                try:
                    gateway = EmailToSMSNotifier.phone_to_gateway(sub.phone_number, sub.carrier)
                    gw_notifier = EmailToSMSNotifier(to_sms_emails=[gateway])
                    ok = gw_notifier.send_message(sms_text)
                    entry = {"channel": "sms", "delivery": "email_to_sms" if ok else "failed"}
                    if not ok and gw_notifier.last_error:
                        entry["error"] = gw_notifier.last_error
                    deliveries.append(entry)
                except Exception as exc:
                    deliveries.append({"channel": "sms", "delivery": "failed", "error": str(exc)})
            elif twilio_configured:
                try:
                    ok = TwilioSMSNotifier(to_numbers=[sub.phone_number]).send_message(sms_text)
                    deliveries.append({"channel": "sms", "delivery": "twilio" if ok else "failed"})
                except Exception as exc:
                    deliveries.append({"channel": "sms", "delivery": "failed", "error": str(exc)})
            else:
                deliveries.append({"channel": "sms", "delivery": "preview_sms_not_configured"})

        results.append({
            "email": sub.email,
            "phone_number": sub.phone_number,
            "station_names": [t["station"].station_name for t in matching],
            "events": [{"station": t["station"].station_name, "from": t["from"], "to": t["to"]} for t in matching],
            "message": sms_text,
            "simulated": simulated,
            "deliveries": deliveries,
        })
    return results


def check_and_dispatch(api=None) -> dict:
    """One poll: fetch → diff against saved state → dispatch → persist state."""
    api = api or SFPUCRealTimeAPI()
    stations = api.fetch_stations()
    info = {
        "checked_at": datetime.now().isoformat(),
        "baselined": False,
        "station_count": len(stations),
        "transitions": [],
        "dispatch_results": [],
        "error": None,
    }
    if not stations:
        # Feed hiccup: keep the previous state so we don't re-baseline
        # (and miss nothing — no transition can be observed in an empty poll).
        info["error"] = "SFPUC feed returned no stations; state kept"
        return _record_run(info)

    simulated_ids = SimulatedCSOStore().get_station_ids()
    stations = apply_simulated_cso(stations, simulated_ids)
    simulated_set = set(simulated_ids)

    previous = load_state()
    if not previous:
        info["baselined"] = True
        transitions = []
    else:
        transitions = detect_transitions(previous, stations)
        for t in transitions:
            t["simulated"] = t["station"].station_id in simulated_set

    if transitions:
        # observer mode: the pg path is the live sender; this thread detects
        # and logs identical would-send decisions as the reference shadow.
        observe = os.environ.get("ALERT_WATCHER_MODE", "send").strip().lower() == "observe"
        subscriptions = SubscriptionStore().list_subscriptions()
        info["dispatch_results"] = dispatch_transition_alerts(subscriptions, transitions, dry_run=observe)
        record_dispatch(
            source="thread_shadow" if observe else "watcher",
            event_type="cso" if any(t["to"] == "cso" for t in transitions) else "posted",
            station_ids=[t["station"].station_id for t in transitions],
            station_names=[t["station"].station_name for t in transitions],
            recipient_count=len(info["dispatch_results"]),
            channel="shadow" if observe else "mixed",
            simulated=any(t.get("simulated") for t in transitions),
            results=info["dispatch_results"],
        )

    save_state(
        {station.station_id: classify(station) for station in stations},
        names={station.station_id: station.station_name for station in stations},
    )

    info["transitions"] = [{
        "station_id": t["station"].station_id,
        "station_name": t["station"].station_name,
        "from": t["from"],
        "to": t["to"],
        "simulated": t.get("simulated", False),
    } for t in transitions]

    if info["baselined"]:
        print(f"[watcher] baselined {len(stations)} stations (no alerts on first sight)")
    for t in info["transitions"]:
        print(f"[watcher] TRANSITION {t['station_name']}: {t['from']} → {t['to']}"
              + (" (simulated)" if t["simulated"] else ""))
    for r in info["dispatch_results"]:
        summary = ", ".join(f"{d['channel']}:{d['delivery']}" for d in r["deliveries"]) or "no channels"
        print(f"[watcher] dispatched to {r['email'] or r['phone_number']}: {summary}")

    return _record_run(info)


def _record_run(info: dict) -> dict:
    with _run_lock:
        _last_run.update(info)
        _last_run["poll_count"] += 1
    return info


def get_last_run() -> dict:
    with _run_lock:
        return dict(_last_run)


def _watch_loop(interval_seconds: int) -> None:
    while True:
        try:
            check_and_dispatch()
        except Exception as exc:  # never let the watcher die
            _record_run({"checked_at": datetime.now().isoformat(), "error": str(exc),
                         "baselined": False, "transitions": [], "dispatch_results": []})
            print(f"[watcher] poll failed: {exc}")
        time.sleep(interval_seconds)


def start_watcher(interval_seconds: int | None = None) -> bool:
    """Start the watcher thread once. ALERT_WATCHER_INTERVAL env overrides the
    interval (seconds); '0' / 'off' disables the watcher entirely."""
    global _started
    if _started:
        return False
    if os.environ.get("VERCEL"):
        # Serverless: no persistent process to host the thread — and an
        # unconfigured instance would default to SEND mode beside pg_live.
        # Postgres (pg_cron) is the sender; nothing here to replace.
        print("[watcher] disabled on Vercel (pg_cron is the sender)")
        return False
    raw = os.environ.get("ALERT_WATCHER_INTERVAL", "").strip().lower()
    if not raw or raw in {"0", "off", "false", "disabled"}:
        # Opt-in only since 2026-09-12: Postgres (pg_cron) is the sender
        # everywhere, and a local checkout running this thread in shadow mode
        # double-logged the 2026-09-01 Windsurfer posting into alert_log
        # (source='thread_shadow'). Set ALERT_WATCHER_INTERVAL=<seconds> to run
        # it deliberately — it remains the emergency fallback sender we own.
        print("[watcher] not started (opt-in: set ALERT_WATCHER_INTERVAL=<seconds>)")
        return False
    interval = interval_seconds or (int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_INTERVAL_SECONDS)
    thread = threading.Thread(target=_watch_loop, args=(interval,), daemon=True, name="alert-watcher")
    thread.start()
    _started = True
    with _run_lock:
        _last_run["enabled"] = True
        _last_run["interval_seconds"] = interval
    print(f"[watcher] started — polling every {interval}s")
    return True
