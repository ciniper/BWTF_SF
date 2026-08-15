#!/usr/bin/env python3
"""B7 delivery log — record every dispatch attempt in Supabase's alert_log.

Write-only from the app's perspective. Logging must never break dispatch:
``record_dispatch`` swallows (and prints) every failure, and is a silent no-op
when Supabase isn't configured.
"""
from __future__ import annotations

from shared import supabase as sb


def record_dispatch(*, source: str, event_type: str, station_ids: list[str],
                    station_names: list[str], recipient_count: int, channel: str,
                    simulated: bool, results: list[dict]) -> None:
    if not sb.is_configured():
        return
    try:
        sb.insert("alert_log", [{
            "source": source,
            "event_type": event_type,
            "station_ids": station_ids,
            "station_names": station_names,
            "recipient_count": recipient_count,
            "channel": channel,
            "simulated": simulated,
            "results": results,
        }])
    except Exception as exc:  # never let logging break the dispatch path
        print(f"[alert_log] failed to record dispatch: {exc}")
