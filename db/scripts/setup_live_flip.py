#!/usr/bin/env python3
"""Seed the pg live-dispatch config + report flip readiness. Does NOT flip.

Reads BREVO_API_KEY, ALERT_FROM_EMAIL (and optional ALERT_FROM_NAME) from
.env and upserts them into watcher_config, then prints the flip checklist
state. The actual flip (mode='live') is a deliberate separate step.

    venv/bin/python db/scripts/setup_live_flip.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from shared import supabase as sb


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1

    seeded = []
    for env_key, cfg_key in (("BREVO_API_KEY", "brevo_api_key"),
                             ("ALERT_FROM_EMAIL", "alert_from_email"),
                             ("ALERT_FROM_NAME", "alert_from_name")):
        value = os.environ.get(env_key, "").strip()
        if value:
            sb.upsert("watcher_config", [{"key": cfg_key, "value": value}], on_conflict="key")
            seeded.append(cfg_key)
    print(f"seeded into watcher_config: {seeded or 'nothing (add BREVO_API_KEY + ALERT_FROM_EMAIL to .env)'}")

    cfg = {r["key"]: bool(r["value"]) for r in sb.select("watcher_config", {"select": "key,value"})}
    mode = [r["value"] for r in sb.select("watcher_config", {"select": "value", "key": "eq.mode"})]

    print("\nflip checklist:")
    print(f"  [{'x' if cfg.get('brevo_api_key') else ' '}] brevo_api_key in watcher_config")
    print(f"  [{'x' if cfg.get('alert_from_email') else ' '}] alert_from_email in watcher_config")
    print(f"  [{'x' if cfg.get('healthchecks_ping_url') else ' '}] dead-man's switch URL present")
    print(f"  mode: {mode[0] if mode else 'MISSING'}")

    try:
        s = requests.Session()
        s.post("https://bwtf-sf.up.railway.app/alerts/unlock",
               data={"passphrase": os.environ.get("ALERTS_PASSPHRASE", "snowy plover")},
               timeout=15, allow_redirects=False)
        w = s.get("https://bwtf-sf.up.railway.app/api/watcher", timeout=15).json()
        print(f"  thread mode: {w.get('mode', 'send (pre-observer build)')} | poll #{w.get('poll_count')}")
    except Exception as exc:
        print(f"  thread mode: unreachable ({type(exc).__name__})")

    ready = cfg.get("brevo_api_key") and cfg.get("alert_from_email")
    print("\nREADY to flip (mode='live') once the thread is in observe mode." if ready
          else "\nNOT ready — seed the Brevo config first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
