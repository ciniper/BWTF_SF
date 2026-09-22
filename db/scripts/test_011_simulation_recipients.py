#!/usr/bin/env python3
"""Behavioral tests for migration 011 — a simulation can target specific
subscribers. Run AFTER pasting 011 (010 + 009 in place). Shadow mode, so the
"dispatch" is the would-send list in alert_log; nothing is emailed.

Creates two throwaway subscribers (test-011-a/b@example.invalid) subscribed
to a synthetic TEST-011 station, then checks who each simulation would reach:

  1. untargeted simulation        → both subscribers (today's behaviour)
  2. targeted at A                → A only; row records only_recipients
  3. real escalation during 2.    → both (real events are never filtered)
  4. targeted at a non-subscriber → nobody (station match still applies)
  5. phone-number target          → the phone-only subscriber

Cleans up subscribers, simulations, state and log rows.

    venv/bin/python db/scripts/test_011_simulation_recipients.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb  # noqa: E402

S = "TEST-011-A"
EA, EB, PHONE = "test-011-a@example.invalid", "test-011-b@example.invalid", "4150000011"
results: list[bool] = []


def check(label: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))


def tick(*stations: dict) -> dict:
    return sb._request("POST", "rpc/bwtf_process_payload", json_body={"p_payload": list(stations)}).json()


def st(sid: str, *, cso: str | None = None, s_color: str | None = "G") -> dict:
    return {"stationid": sid, "stationname": "Test 011 A", "cso": cso, "s_color": s_color, "posted": None, "p_color": None}


def last(sid: str) -> dict:
    rows = sb.select("alert_log", {"select": "*", "source": "eq.pg_shadow", "station_ids": "cs.{" + sid + "}", "order": "id.desc", "limit": 1})
    return rows[0] if rows else {}


def reached(row: dict) -> list[str]:
    return sorted((r.get("email") or r.get("phone_number")) for r in ((row.get("results") or {}).get("recipients") or []))


def sim(kind: str | None, recipients: list[str] | None = None) -> None:
    sb.delete("simulated_cso", {"station_id": f"eq.{S}"})
    if kind:
        sb.insert("simulated_cso", [{"station_id": S, "kind": kind, "recipients": recipients}])


def cleanup() -> None:
    sb.delete("simulated_cso", {"station_id": "like.TEST-011*"})
    sb.delete("watcher_state_shadow", {"station_id": "like.TEST-011*"})
    sb.delete("alert_log", {"source": "eq.pg_shadow", "station_ids": "cs.{" + S + "}"})
    sb.delete("subscribers", {"email": "like.test-011-%@example.invalid"})
    sb.delete("subscribers", {"phone_number": f"eq.{PHONE}", "email": "eq."})


def main() -> int:
    assert sb.is_configured()
    cleanup()
    # PostgREST bulk inserts need identical keys on every row
    sb.insert("subscribers", [
        {"email": EA, "phone_number": "", "carrier": "", "station_ids": [S], "active": True},
        {"email": EB, "phone_number": "", "carrier": "", "station_ids": [S], "active": True},
        {"email": "", "phone_number": PHONE, "carrier": "tmobile", "station_ids": [S], "active": True},
    ])
    try:
        tick(st(S))

        # 1. untargeted → everyone subscribed to the site
        sim("cso"); r = tick(st(S)); row = last(S)
        check("1 untargeted sim reaches every matching subscriber", row.get("simulated") is True and reached(row) == sorted([EA, EB, PHONE]) and r["recipients"] == 3, str(reached(row)))
        check("1 row has no only_recipients", "only_recipients" not in (row.get("results") or {}), str(list((row.get("results") or {}).keys())))
        sim(None); tick(st(S))

        # 2. targeted at A
        sim("posted", [EA]); r = tick(st(S)); row = last(S)
        check("2 targeted sim reaches A only", row.get("simulated") is True and reached(row) == [EA] and r["recipients"] == 1, str(reached(row)))
        check("2 row records only_recipients", (row.get("results") or {}).get("only_recipients") == [EA], str((row.get("results") or {}).get("only_recipients")))

        # 3. a REAL escalation while the targeted sim is on → unfiltered
        r = tick(st(S, cso="CSO#REAL")); row = last(S)
        check("3 real CSO during targeted sim reaches everyone", row.get("simulated") is False and reached(row) == sorted([EA, EB, PHONE]), str(reached(row)))
        tick(st(S))  # real clear
        sim(None); tick(st(S))  # sim end

        # 4. targeted at someone not subscribed to the site → nobody
        sim("cso", ["nobody@example.invalid"]); r = tick(st(S)); row = last(S)
        check("4 target not subscribed to the site → no recipients", row.get("simulated") is True and reached(row) == [] and r["recipients"] == 0, str(reached(row)))
        sim(None); tick(st(S))

        # 5. phone-number target
        sim("cso", [PHONE]); r = tick(st(S)); row = last(S)
        check("5 phone target reaches the phone-only subscriber", reached(row) == [PHONE], str(reached(row)))
        sim(None); tick(st(S))
    finally:
        cleanup()
    print("\n" + ("ALL PASS" if all(results) else f"{results.count(False)} FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
