#!/usr/bin/env python3
"""B7 delivery log — record every dispatch attempt in Supabase's alert_log.

Write-only from the app's perspective. Logging must never break dispatch:
``record_dispatch`` swallows (and prints) every failure, and is a silent no-op
when Supabase isn't configured.

Since 015 each result may carry ``deliveries`` — one complete row per message
the Python path attempted, with the rendered message and Brevo's synchronous
reply. Those go to ``alert_deliveries`` (linked to the new alert_log row) and
are stripped from ``results`` first, so the bodies are stored once.
"""
from __future__ import annotations

from shared import supabase as sb


def record_dispatch(*, source: str, event_type: str, station_ids: list[str],
                    station_names: list[str], recipient_count: int, channel: str,
                    simulated: bool, results: list[dict]) -> None:
    if not sb.is_configured():
        return
    deliveries: list[dict] = []
    slim: list[dict] = []
    for r in results or []:
        r = dict(r)
        deliveries.extend(r.pop("deliveries", None) or [])
        slim.append(r)
    try:
        rows = sb.insert("alert_log", [{
            "source": source,
            "event_type": event_type,
            "station_ids": station_ids,
            "station_names": station_names,
            "recipient_count": recipient_count,
            "channel": channel,
            "simulated": simulated,
            "results": slim,
        }], returning=bool(deliveries))
    except Exception as exc:  # never let logging break the dispatch path
        print(f"[alert_log] failed to record dispatch: {exc}")
        return
    if deliveries:
        from features.alerts.deliveries import record_manual
        log_id = rows[0].get("id") if rows and isinstance(rows[0], dict) else None
        record_manual(log_id, deliveries)
