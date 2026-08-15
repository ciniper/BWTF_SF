#!/usr/bin/env python3
"""Phase 2 post-migration setup + liveness verification.

Run AFTER pasting db/migrations/002_phase2_shadow_watcher.sql, with
HEALTHCHECKS_PING_URL added to .env:

    venv/bin/python db/scripts/setup_phase2.py

Seeds watcher_config with the healthchecks ping URL, then watches the pg
pipeline for a few minutes to confirm the cron is ticking: runtime row
advancing, shadow state populated, and (once data flows) the dead-man's
switch pinging.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1

    ping = os.environ.get("HEALTHCHECKS_PING_URL", "").strip()
    if ping:
        sb.upsert("watcher_config", [{"key": "healthchecks_ping_url", "value": ping}],
                  on_conflict="key")
        print("healthchecks_ping_url seeded into watcher_config")
    else:
        print("⚠️  HEALTHCHECKS_PING_URL not in .env — dead-man's switch stays dark until seeded")

    mode = sb.select("watcher_config", {"select": "value", "key": "eq.mode"})
    print(f"mode: {mode[0]['value'] if mode else 'MISSING — migration not applied?'}")

    print("\nwatching the pg pipeline (up to 4 min)…")
    deadline = time.time() + 240
    last_processed = None
    while time.time() < deadline:
        rt = sb.select("watcher_runtime", {"select": "*", "id": "eq.1"})
        if rt:
            row = rt[0]
            processed = (row.get("last_processed_at") or "")[:19]
            if processed and processed != last_processed:
                last_processed = processed
                shadow = sb.select("watcher_state_shadow", {"select": "station_id"})
                print(f"  tick processed @ {processed} | fetch={row.get('last_fetch_status')} "
                      f"| shadow stations={len(shadow)} | summary={row.get('last_summary')}")
                if len(shadow) >= 20:
                    print("\npg shadow watcher is LIVE ✅ — cron ticking, payload parsed, state populated.")
                    if ping:
                        print("check healthchecks.io — the check should now be receiving pings.")
                    return 0
        time.sleep(20)

    print("\nno processed tick observed in 4 min — check: migration pasted? extensions enabled? "
          "cron job listed in Dashboard → Integrations → Cron?")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
