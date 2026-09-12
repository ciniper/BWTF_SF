#!/usr/bin/env python3
"""Behavioral tests for migration 009 — simulations stay out of the real
watcher state. Run AFTER pasting 009.

Drives bwtf_process_payload via RPC in shadow mode (can never send) with
synthetic TEST-009 stations and a real simulated_cso row for one of them,
asserting per scenario what lands in alert_log and watcher_state_shadow:

  1. plain real escalation / clear-down, no simulation      (006 regression)
  2. simulation starts        → cso row, simulated=TRUE, state = real ok + sim_active
  3. real posting DURING sim  → posted row, simulated=FALSE
  4. real CSO DURING sim      → cso row,    simulated=FALSE   (006 lost this one)
  5. real clear DURING sim    → cleared row, simulated=FALSE
  6. simulation ends          → cleared row, simulated=TRUE, sim_active off
  7. sim starts + real posting in ONE tick → two rows, one real, one simulated
  8. the 2026-09-05 shape: sim start, sim end → the teardown is simulated

Cleans up after itself (state rows, log rows, and the simulated_cso row —
the alerts page shows simulated stations, so never leave one behind).

    venv/bin/python db/scripts/test_009_simulation_provenance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb  # noqa: E402

A, B = "TEST-009-A", "TEST-009-B"
results: list[bool] = []


def check(label: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))


def tick(*stations: dict) -> dict:
    return sb._request("POST", "rpc/bwtf_process_payload", json_body={"p_payload": list(stations)}).json()


def st(sid: str, *, cso: str | None = None, s_color: str | None = "G") -> dict:
    return {"stationid": sid, "stationname": f"Test 009 {sid[-1]}", "cso": cso,
            "s_color": s_color, "posted": None, "p_color": None}


def logs(sid: str, n: int = 3) -> list[dict]:
    return sb.select("alert_log", {"select": "*", "source": "eq.pg_shadow",
                                   "station_ids": "cs.{" + sid + "}", "order": "id.desc", "limit": n})


def state(sid: str) -> dict | None:
    rows = sb.select("watcher_state_shadow", {"select": "station_id,status,sim_active", "station_id": f"eq.{sid}"})
    return rows[0] if rows else None


def sim(on: bool, sid: str = A) -> None:
    if on:
        sb.insert("simulated_cso", [{"station_id": sid}])
    else:
        sb.delete("simulated_cso", {"station_id": f"eq.{sid}"})


def cleanup() -> None:
    sb.delete("simulated_cso", {"station_id": "like.TEST-009*"})
    sb.delete("watcher_state_shadow", {"station_id": "like.TEST-009*"})
    for sid in (A, B):
        sb.delete("alert_log", {"source": "eq.pg_shadow", "station_ids": "cs.{" + sid + "}"})


def tr(row: dict | None, i: int = 0) -> dict:
    return ((row or {}).get("results") or {}).get("transitions", [{}])[i] if row else {}


def main() -> int:
    assert sb.is_configured()
    cleanup()
    try:
        # seed both stations silently (baseline is per-table; the real stations exist, so this just inserts)
        r = tick(st(A), st(B))
        check("seed logs nothing", r["transitions"] == 0 and r["downgrades"] == 0, str(r))
        check("state carries sim_active", state(A) == {"station_id": A, "status": "ok", "sim_active": False}, str(state(A)))

        # 1. plain real path — the 006 regression
        r = tick(st(A, cso="CSO#X"), st(B))
        row = logs(A, 1)[0]
        check("1 real cso: row simulated=false, from ok to cso",
              row["event_type"] == "cso" and row["simulated"] is False and tr(row) == {**tr(row), "from": "ok", "to": "cso", "simulated": False}, str(tr(row)))
        r = tick(st(A), st(B))
        row = logs(A, 1)[0]
        check("1 real clear: cleared row simulated=false", row["event_type"] == "cleared" and row["simulated"] is False and tr(row)["from"] == "cso", str(tr(row)))
        check("1 summary has no simulated counts", r.get("simulated_transitions") == 0 and r.get("simulated_downgrades") == 0, str(r))

        # 2. simulation starts on A
        sim(True)
        r = tick(st(A), st(B))
        row = logs(A, 1)[0]
        check("2 sim start: cso row simulated=TRUE, from real ok to cso",
              row["event_type"] == "cso" and row["simulated"] is True and tr(row)["from"] == "ok" and tr(row)["to"] == "cso" and tr(row)["simulated"] is True, str(tr(row)))
        check("2 state: status is the REAL ok, sim_active on", state(A) == {"station_id": A, "status": "ok", "sim_active": True}, str(state(A)))
        check("2 summary counts it as simulated", r["simulated_transitions"] == 1 and r["transitions"] == 1, str(r))

        # 3. real posting while simulated
        r = tick(st(A, s_color="R"), st(B))
        row = logs(A, 1)[0]
        check("3 real posting during sim: posted row simulated=FALSE, ok→posted",
              row["event_type"] == "posted" and row["simulated"] is False and tr(row)["from"] == "ok" and tr(row)["to"] == "posted", str(tr(row)))
        check("3 state: real posted, sim still active", state(A) == {"station_id": A, "status": "posted", "sim_active": True}, str(state(A)))

        # 4. real CSO while simulated — 006 produced nothing here
        r = tick(st(A, cso="CSO#REAL"), st(B))
        row = logs(A, 1)[0]
        check("4 real CSO during sim: cso row simulated=FALSE, posted→cso",
              row["event_type"] == "cso" and row["simulated"] is False and tr(row)["from"] == "posted" and tr(row)["to"] == "cso", str(tr(row)))

        # 5. real clear while simulated
        r = tick(st(A), st(B))
        row = logs(A, 1)[0]
        check("5 real clear during sim: cleared row simulated=FALSE, cso→ok",
              row["event_type"] == "cleared" and row["simulated"] is False and tr(row)["from"] == "cso" and tr(row)["to"] == "ok", str(tr(row)))
        check("5 state: real ok, sim still active", state(A) == {"station_id": A, "status": "ok", "sim_active": True}, str(state(A)))

        # 6. simulation ends
        sim(False)
        r = tick(st(A), st(B))
        row = logs(A, 1)[0]
        check("6 sim end: cleared row simulated=TRUE, cso→ok",
              row["event_type"] == "cleared" and row["simulated"] is True and tr(row)["from"] == "cso" and tr(row)["to"] == "ok" and tr(row)["simulated"] is True, str(tr(row)))
        check("6 state: sim_active off, status ok", state(A) == {"station_id": A, "status": "ok", "sim_active": False}, str(state(A)))
        check("6 summary: simulated downgrade, no real one", r["simulated_downgrades"] == 1 and r["downgrades"] == 1 and r["transitions"] == 0, str(r))

        # 7. sim start on A and a real posting on B in the same tick → two rows
        before = logs(B, 1)
        sim(True)
        r = tick(st(A), st(B, s_color="R"))
        ra, rb = logs(A, 1)[0], logs(B, 1)[0]
        check("7 two rows: A simulated cso, B real posted",
              ra["event_type"] == "cso" and ra["simulated"] is True and rb["event_type"] == "posted" and rb["simulated"] is False and ra["id"] != rb["id"], f"A={ra['simulated']} B={rb['simulated']}")
        check("7 rows carry only their own stations", ra["station_ids"] == [A] and rb["station_ids"] == [B], f"{ra['station_ids']} {rb['station_ids']}")
        sim(False)
        tick(st(A), st(B))

        # 8. the 2026-09-05 shape end to end
        sim(True); tick(st(A), st(B))
        sim(False); tick(st(A), st(B))
        rows = logs(A, 2)
        check("8 start+end: both rows simulated", all(x["simulated"] for x in rows) and {x["event_type"] for x in rows} == {"cso", "cleared"}, str([(x["event_type"], x["simulated"]) for x in rows]))

        # 9. unchanged state logs nothing
        last = logs(A, 1)[0]["id"]
        r = tick(st(A), st(B))
        check("9 no re-fire on unchanged state", r["transitions"] == 0 and r["downgrades"] == 0 and logs(A, 1)[0]["id"] == last, str(r))
    finally:
        cleanup()
    print("\n" + ("ALL PASS" if all(results) else f"{results.count(False)} FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
