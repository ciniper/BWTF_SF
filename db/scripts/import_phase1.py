#!/usr/bin/env python3
"""One-time Phase 1 import: live prod state → Supabase. Idempotent.

- Subscribers: pulled from the deployed app's gated API (the live truth — the
  local ``data/`` files may be stale), upserted through the new Supabase-backed
  SubscriptionStore so the import exercises the production code path.
- watcher_state: seeded from the live public /api/status (same classification
  the watcher itself writes), so the first Supabase-mode poll sees continuous
  state instead of re-baselining.

Needs ``.env`` (SUPABASE_URL/SUPABASE_SERVICE_KEY) and the alerts passphrase
(ALERTS_PASSPHRASE env, defaulting to the app's default).

Run from the repo root:  venv/bin/python db/scripts/import_phase1.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from shared import supabase as sb
from features.alerts.subscriptions import SubscriptionStore
from features.alerts import watcher

PROD = os.environ.get("BWTF_PROD_URL", "https://bwtf-sf.up.railway.app")
PASSPHRASE = os.environ.get("ALERTS_PASSPHRASE", "snowy plover")


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1

    session = requests.Session()
    r = session.post(f"{PROD}/alerts/unlock", data={"passphrase": PASSPHRASE},
                     timeout=30, allow_redirects=False)
    if r.status_code not in (301, 302):
        print(f"Could not unlock prod alerts gate ({r.status_code}) — check passphrase."); return 1

    subs = session.get(f"{PROD}/api/subscriptions", timeout=30).json()
    print(f"prod subscribers: {len(subs)}")
    store = SubscriptionStore()
    assert store._remote, "store must be in Supabase mode"
    for s in subs:
        saved = store.upsert_subscription(
            email=s.get("email", ""),
            station_ids=s.get("station_ids", []),
            phone_number=s.get("phone_number", "") or "",
            carrier=s.get("carrier", "") or "",
        )
        print(f"  upserted {saved.email or saved.phone_number} ({len(saved.station_ids)} stations)")

    status = session.get(f"{PROD}/api/status", timeout=30).json()
    stations = status.get("stations", [])
    statuses, names = {}, {}
    for st in stations:
        sid = st["id"]
        if st.get("has_cso"):
            statuses[sid] = "cso"
        elif st.get("status") == "posted":
            statuses[sid] = "posted"
        else:
            statuses[sid] = "ok"
        names[sid] = st.get("name", "")
    if statuses:
        watcher.save_state(statuses, names=names)
    print(f"watcher_state seeded: {len(statuses)} stations "
          f"({sum(1 for v in statuses.values() if v != 'ok')} non-ok)")

    final = store.list_subscriptions()
    print(f"supabase now holds {len(final)} active subscriber(s) — import complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
