#!/usr/bin/env python3
"""Behavioral tests for migration 010 — simulations carry a kind (cso | posted).
Run AFTER pasting 010 (009 must be in place). Shadow mode; synthetic TEST-010
stations; cleans up after itself, including the simulated_cso rows.

    venv/bin/python db/scripts/test_010_simulation_kind.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb  # noqa: E402

A = "TEST-010-A"
results: list[bool] = []


def check(label: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))


def tick(*stations: dict) -> dict:
    return sb._request("POST", "rpc/bwtf_process_payload", json_body={"p_payload": list(stations)}).json()


def st(sid: str, *, cso: str | None = None, s_color: str | None = "G") -> dict:
    return {"stationid": sid, "stationname": "Test 010 A", "cso": cso, "s_color": s_color, "posted": None, "p_color": None}


def last(sid: str) -> dict:
    rows = sb.select("alert_log", {"select": "*", "source": "eq.pg_shadow", "station_ids": "cs.{" + sid + "}", "order": "id.desc", "limit": 1})
    return rows[0] if rows else {}


def tr(row: dict) -> dict:
    return ((row.get("results") or {}).get("transitions") or [{}])[0]


def state(sid: str) -> dict | None:
    rows = sb.select("watcher_state_shadow", {"select": "status,sim_active,sim_kind", "station_id": f"eq.{sid}"})
    return rows[0] if rows else None


def sim(kind: str | None) -> None:
    sb.delete("simulated_cso", {"station_id": f"eq.{A}"})
    if kind:
        sb.insert("simulated_cso", [{"station_id": A, "kind": kind}])


def cleanup() -> None:
    sb.delete("simulated_cso", {"station_id": "like.TEST-010*"})
    sb.delete("watcher_state_shadow", {"station_id": "like.TEST-010*"})
    sb.delete("alert_log", {"source": "eq.pg_shadow", "station_ids": "cs.{" + A + "}"})


def main() -> int:
    assert sb.is_configured()
    cleanup()
    try:
        tick(st(A))
        check("seed: no simulation", state(A) == {"status": "ok", "sim_active": False, "sim_kind": None}, str(state(A)))

        # 1. posting simulation starts on a clean station
        sim("posted"); r = tick(st(A)); row = last(A)
        check("1 posted-sim start: posted row, simulated=TRUE, ok→posted",
              row.get("event_type") == "posted" and row.get("simulated") is True and tr(row)["from"] == "ok" and tr(row)["to"] == "posted", str(tr(row)))
        check("1 state: real ok, sim_kind posted", state(A) == {"status": "ok", "sim_active": True, "sim_kind": "posted"}, str(state(A)))

        # 2. real CSO during a posting simulation → real row
        r = tick(st(A, cso="CSO#REAL")); row = last(A)
        check("2 real CSO during posted-sim: cso row simulated=FALSE", row.get("event_type") == "cso" and row.get("simulated") is False and tr(row)["from"] == "ok", str(tr(row)))
        r = tick(st(A)); row = last(A)
        check("2 real clear: cleared simulated=FALSE", row.get("event_type") == "cleared" and row.get("simulated") is False, str(tr(row)))

        # 3. kind change posted → cso: one simulated escalation posted→cso
        sim("cso"); r = tick(st(A)); row = last(A)
        check("3 kind posted→cso: cso row simulated=TRUE, from posted", row.get("event_type") == "cso" and row.get("simulated") is True and tr(row)["from"] == "posted" and tr(row)["to"] == "cso", str(tr(row)))
        check("3 state sim_kind cso", (state(A) or {}).get("sim_kind") == "cso", str(state(A)))

        # 4. kind change cso → posted: one simulated downgrade cso→posted
        sim("posted"); r = tick(st(A)); row = last(A)
        check("4 kind cso→posted: cleared row simulated=TRUE, cso→posted", row.get("event_type") == "cleared" and row.get("simulated") is True and tr(row)["from"] == "cso" and tr(row)["to"] == "posted", str(tr(row)))

        # 5. simulation ends: cleared posted→ok, simulated
        sim(None); r = tick(st(A)); row = last(A)
        check("5 sim end: cleared simulated=TRUE, posted→ok", row.get("event_type") == "cleared" and row.get("simulated") is True and tr(row)["from"] == "posted" and tr(row)["to"] == "ok", str(tr(row)))
        check("5 state clean", state(A) == {"status": "ok", "sim_active": False, "sim_kind": None}, str(state(A)))

        # 6. posting simulation on a station that is REALLY posted: nothing visible, nothing logged
        tick(st(A, s_color="R")); before = last(A)["id"]
        sim("posted"); r = tick(st(A, s_color="R"))
        check("6 posted-sim on a really-posted station logs nothing", last(A)["id"] == before and r["simulated_transitions"] == 0, str(r))
        sim(None); tick(st(A, s_color="R"))
        check("6 ending it logs nothing either", last(A)["id"] == before, str(last(A)["id"]))
        tick(st(A))  # real clear
    finally:
        cleanup()
    print("\n" + ("ALL PASS" if all(results) else f"{results.count(False)} FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
