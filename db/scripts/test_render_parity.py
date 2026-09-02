#!/usr/bin/env python3
"""Parity test: the pg renderer (bwtf_render_alert, migration 007) must match
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
    ("single cso", [{"station_id": "4619", "station_name": "Islais Creek", "to": "cso"}], False),
    ("single posted", [{"station_id": "4616", "station_name": "Windsurfer Circle", "to": "posted"}], False),
    ("single cso simulated", [{"station_id": "4618", "station_name": "Mission Creek", "to": "cso"}], True),
    ("multi mixed", [
        {"station_id": "4605", "station_name": "Ocean Beach at Lincoln Way", "to": "posted"},
        {"station_id": "4610", "station_name": "Baker Beach at Lobos Creek", "to": "cso"},
        {"station_id": "4613", "station_name": "Aquatic Park", "to": "posted"},
    ], True),
]

FIELDS = ("subject", "sms_text", "text_body", "html_body")


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1
    try:
        sb.rpc("bwtf_render_alert", {"p_transitions": CASES[0][1], "p_simulated": False})
    except Exception as exc:
        print(f"bwtf_render_alert RPC unavailable ({exc}) — paste db/migrations/007 first.")
        return 1

    failures = 0
    for label, transitions, simulated in CASES:
        pg = sb.rpc("bwtf_render_alert", {"p_transitions": transitions, "p_simulated": simulated})
        py = _fallback(transitions, simulated)
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
