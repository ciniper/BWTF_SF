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

from features.alerts.cso_alerts import SimulatedCSOStore, apply_simulated_cso
from features.alerts.notifiers import EmailNotifier, EmailToSMSNotifier, TwilioSMSNotifier
from features.alerts.subscriptions import SubscriptionStore
from shared.paths import DATA_DIR
from shared.sfpuc_api import SFPUCRealTimeAPI

STATE_PATH = DATA_DIR / "alert_watcher_state.json"
DEFAULT_INTERVAL_SECONDS = 120
SFPUC_MAP_URL = "https://webapps.sfpuc.org/sapps/beachesandbay.html"

_SEVERITY = {"ok": 0, "posted": 1, "cso": 2}
_EVENT_LABEL = {"posted": "bacteria posting", "cso": "CSO discharge"}
_EVENT_ADVICE = {
    "posted": "Elevated bacteria levels — water contact not recommended.",
    "cso": "Combined sewer overflow — avoid water contact for 72 hours.",
}

_started = False
_run_lock = threading.Lock()
_last_run: dict = {
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
    if not STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text() or "{}")
    except json.JSONDecodeError:
        return {}
    return payload.get("statuses", {}) or {}


def save_state(statuses: dict) -> None:
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
    """(subject, sms_text, text_body, html_body) for one subscriber's events."""
    prefix = "TEST " if simulated else ""
    names = [t["station"].station_name for t in matching]
    if len(matching) == 1:
        t = matching[0]
        subject = f"{prefix}SF Beach Alert: {_EVENT_LABEL[t['to']]} at {t['station'].station_name}"
    else:
        subject = f"{prefix}SF Beach Alert: {len(matching)} sites affected"

    lines = [f"- {t['station'].station_name}: {_EVENT_LABEL[t['to']]}" for t in matching]
    sms_text = f"🚨 {prefix}SF Beach alert: " + "; ".join(
        f"{t['station'].station_name} ({_EVENT_LABEL[t['to']]})" for t in matching
    ) + f". Avoid water contact. Map: {SFPUC_MAP_URL}"
    if len(sms_text) > 320:
        sms_text = sms_text[:317] + "..."

    advice = sorted({_EVENT_ADVICE[t["to"]] for t in matching})
    text_body = (
        f"{prefix}SF Beach Water Quality Alert\n\n"
        "New events at your selected sites:\n" + "\n".join(lines) + "\n\n"
        + "\n".join(advice) + "\n\n"
        f"Live map: {SFPUC_MAP_URL}\n\n"
        "You are receiving this because you subscribed on the Surfrider SF BWTF dashboard."
    )

    html_items = "".join(
        f"<li><b>{t['station'].station_name}</b> — {_EVENT_LABEL[t['to']]}</li>" for t in matching
    )
    html_advice = "".join(f"<p style=\"margin:0 0 6px; color:#5e6a71; line-height:1.6;\">{a}</p>" for a in advice)
    html_body = (
        "<html><body style=\"margin:0; padding:24px; background:#e2e8ee; "
        "font-family:'Avenir Next','Trebuchet MS','Segoe UI',sans-serif; color:#26272a;\">"
        "<div style=\"max-width:640px; margin:0 auto; background:#ffffff; border-radius:24px; overflow:hidden;\">"
        "<div style=\"background:#1f6fb0; padding:22px 24px;\">"
        "<div style=\"color:rgba(255,255,255,0.85); font-size:12px; font-weight:700; "
        "letter-spacing:0.12em; text-transform:uppercase;\">Blue Water Task Force • Surfrider SF</div>"
        f"<h1 style=\"margin:10px 0 0; color:#ffffff; font-size:26px;\">{prefix}Beach Water Quality Alert</h1>"
        "</div>"
        "<div style=\"padding:22px 24px;\">"
        "<div style=\"background:rgba(209,92,92,0.10); border-radius:16px; padding:14px 16px; margin-bottom:16px;\">"
        "<p style=\"margin:0 0 8px; font-weight:700;\">New events at your sites</p>"
        f"<ul style=\"margin:0; padding-left:18px; line-height:1.6;\">{html_items}</ul>"
        "</div>"
        f"{html_advice}"
        f"<p style=\"margin:16px 0 0;\"><a href=\"{SFPUC_MAP_URL}\" "
        "style=\"display:inline-block; background:#317fb2; color:#ffffff; text-decoration:none; "
        "padding:11px 16px; border-radius:999px; font-weight:700;\">View SFPUC Beach Map</a></p>"
        "</div></div></body></html>"
    )
    return subject, sms_text, text_body, html_body


def dispatch_transition_alerts(subscriptions, transitions: list[dict]) -> list[dict]:
    """Alert each subscriber whose stations transitioned — email and SMS both,
    whichever the subscriber configured. Falls back to preview when the
    sending credentials aren't set (recorded, not raised)."""
    by_station_id = {t["station"].station_id: t for t in transitions}
    smtp_configured = all(os.environ.get(k) for k in ("SMTP_USERNAME", "SMTP_PASSWORD"))
    twilio_configured = all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))

    results = []
    for sub in subscriptions:
        matching = [by_station_id[sid] for sid in sub.station_ids if sid in by_station_id]
        if not matching:
            continue
        simulated = any(t.get("simulated") for t in matching)
        subject, sms_text, text_body, html_body = _format_messages(matching, simulated)

        deliveries = []
        if sub.email:
            if smtp_configured:
                try:
                    ok = EmailNotifier(to_emails=[sub.email]).send_message(subject, text_body, [sub.email], html_body)
                    deliveries.append({"channel": "email", "delivery": "email" if ok else "failed"})
                except Exception as exc:
                    deliveries.append({"channel": "email", "delivery": "failed", "error": str(exc)})
            else:
                deliveries.append({"channel": "email", "delivery": "preview_email_not_configured"})
        if sub.phone_number:
            if sub.carrier and smtp_configured:
                try:
                    gateway = EmailToSMSNotifier.phone_to_gateway(sub.phone_number, sub.carrier)
                    ok = EmailToSMSNotifier(to_sms_emails=[gateway]).send_message(sms_text)
                    deliveries.append({"channel": "sms", "delivery": "email_to_sms" if ok else "failed"})
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
        subscriptions = SubscriptionStore().list_subscriptions()
        info["dispatch_results"] = dispatch_transition_alerts(subscriptions, transitions)

    save_state({station.station_id: classify(station) for station in stations})

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
    raw = os.environ.get("ALERT_WATCHER_INTERVAL", "").strip().lower()
    if raw in {"0", "off", "false", "disabled"}:
        print("[watcher] disabled via ALERT_WATCHER_INTERVAL")
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
