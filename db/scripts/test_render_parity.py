#!/usr/bin/env python3
"""Parity test: the pg renderer (bwtf_render_alert, migration 020) must match
the Python fallback byte-for-byte — subject, sms_text, text_body, html_body.

If this fails after a format change, update whichever side lagged (format
changes belong in migration SQL first; the Python port follows).

    venv/bin/python db/scripts/test_render_parity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.alerts.render import _fallback
from shared import supabase as sb

CASES = [
    ("single cso", [{"station_id": "4619", "station_name": "Islais Creek", "to": "cso"}], False, None),
    ("single cso + zone", [{"station_id": "4619", "station_name": "Islais Creek", "to": "cso"}], False, "East Beaches"),
    ("single posted", [{"station_id": "4616", "station_name": "Windsurfer Circle", "to": "posted"}], False, None),
    ("single cso simulated + zone", [{"station_id": "4618", "station_name": "Mission Creek", "to": "cso"}], True, "East Beaches"),
    ("multi mixed", [
        {"station_id": "4605", "station_name": "Ocean Beach at Lincoln Way", "to": "posted"},
        {"station_id": "4610", "station_name": "Baker Beach at Lobos Creek", "to": "cso"},
        {"station_id": "4613", "station_name": "Aquatic Park", "to": "posted"},
    ], True, None),
    ("multi + zone", [
        {"station_id": "4601", "station_name": "Fort Funston", "to": "posted"},
        {"station_id": "4602", "station_name": "Ocean Beach at Sloat", "to": "cso"},
    ], False, "Ocean Beach"),
    ("blank zone equals none", [{"station_id": "4613", "station_name": "Aquatic Park", "to": "posted"}], False, "  "),
]
UNSUB = "https://bwtf-sf.vercel.app/unsubscribe?t=0f3b2c9e-1d2a-4e5f-8a9b-0c1d2e3f4a5b"
# every case twice: without the link (phone-only / pre-017 rows) and with it
CASES = [(label, tr, sim, zone, None) for label, tr, sim, zone in CASES] + \
        [(label + " + unsubscribe", tr, sim, zone, UNSUB) for label, tr, sim, zone in CASES]

WHEN = "Tue Sep 30, 7:12 AM PDT"   # 020: the dispatcher passes the moment in; both sides get this one
FIELDS = ("subject", "sms_text", "text_body", "html_body")


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1
    try:
        sb.rpc("bwtf_render_alert", {"p_transitions": CASES[0][1],
                                     "p_simulated": False, "p_zone": None, "p_unsubscribe_url": None, "p_when": WHEN})
    except Exception as exc:
        print(f"bwtf_render_alert RPC unavailable ({exc}) — paste db/migrations/020 first.")
        return 1

    failures = 0
    for label, transitions, simulated, zone, unsub in CASES:
        pg = sb.rpc("bwtf_render_alert", {"p_transitions": transitions,
                                          "p_simulated": simulated, "p_zone": zone, "p_unsubscribe_url": unsub, "p_when": WHEN})
        py = _fallback(transitions, simulated, zone, unsub, WHEN)
        for f in FIELDS:
            if pg.get(f) == py[f]:
                print(f"PASS {label} · {f}")
            else:
                failures += 1
                print(f"FAIL {label} · {f}")
                print(f"  pg: {pg.get(f)!r}"[:220])
                print(f"  py: {py[f]!r}"[:220])

    print(f"\n{'ALL PARITY CHECKS PASS' if failures == 0 else f'{failures} MISMATCHES'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
