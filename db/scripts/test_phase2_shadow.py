#!/usr/bin/env python3
"""Behavioral tests for the pg shadow watcher. Run after migration 002.

Exercises bwtf_process_payload via RPC with synthetic TEST- stations and a
TEST subscriber (cleaned up after), then asserts live parity: the pg path's
watcher_state_shadow must classify the 20 real stations identically to the
in-process thread's watcher_state.

    venv/bin/python db/scripts/test_phase2_shadow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb

TEST_EMAIL = "test+pgshadow@example.org"
results = []


def check(label: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))


def rpc_process(payload: list[dict]) -> dict:
    response = sb._request("POST", "rpc/bwtf_process_payload",
                           json_body={"p_payload": payload})
    return response.json()


def station(sid: str, name: str, *, cso: str | None = None, s_color: str | None = "G",
            posted: str | None = None, p_color: str | None = None) -> dict:
    return {"stationid": sid, "stationname": name, "cso": cso,
            "s_color": s_color, "posted": posted, "p_color": p_color}


def cleanup() -> None:
    sb.delete("watcher_state_shadow", {"station_id": "like.TEST-*"})
    sb.delete("simulated_cso", {"station_id": "like.TEST-*"})
    sb.delete("subscribers", {"email": f"eq.{TEST_EMAIL}"})
    sb.delete("alert_log", {"source": "eq.pg_shadow", "station_ids": "cs.{TEST-B}"})
    sb.delete("alert_log", {"source": "eq.pg_shadow", "station_ids": "cs.{TEST-A}"})


def main() -> int:
    assert sb.is_configured()
    cleanup()
    sb.insert("subscribers", [{"email": TEST_EMAIL, "station_ids": ["TEST-B"], "active": True}])

    base = [station("TEST-A", "Test Alpha"), station("TEST-B", "Test Bravo")]

    # 1. unseen stations seed silently
    r = rpc_process(base)
    check("unseen stations: no transitions", r["transitions"] == 0 and r["stations"] == 2, str(r))

    # 2. ok -> posted fires, matches the TEST subscriber
    posted = [station("TEST-A", "Test Alpha"),
              station("TEST-B", "Test Bravo", s_color="R")]
    r = rpc_process(posted)
    check("posted transition fires", r["transitions"] == 1 and r["recipients"] == 1, str(r))
    logs = sb.select("alert_log", {"select": "*", "source": "eq.pg_shadow",
                                   "station_ids": "cs.{TEST-B}", "order": "id.desc", "limit": 1})
    ok = (logs and logs[0]["event_type"] == "posted" and logs[0]["recipient_count"] == 1
          and TEST_EMAIL in str(logs[0]["results"]) and logs[0]["channel"] == "shadow")
    check("shadow log row: would-send recorded, nothing sent", bool(ok))

    # 3. same state again -> dedup
    r = rpc_process(posted)
    check("no re-fire on unchanged state", r["transitions"] == 0, str(r))

    # 4. simulated overlay -> cso, flagged simulated
    sb.insert("simulated_cso", [{"station_id": "TEST-A"}])
    r = rpc_process(posted)
    check("simulated CSO fires as cso", r["transitions"] == 1, str(r))
    logs = sb.select("alert_log", {"select": "event_type,simulated", "source": "eq.pg_shadow",
                                   "station_ids": "cs.{TEST-A}", "order": "id.desc", "limit": 1})
    check("sim row flagged simulated + cso", bool(logs) and logs[0]["event_type"] == "cso"
          and logs[0]["simulated"] is True)
    sb.delete("simulated_cso", {"station_id": "eq.TEST-A"})

    # 5. recovery is silent
    r = rpc_process(base)
    check("recovery silent", r["transitions"] == 0, str(r))

    # 6. posted-fallback classification (posted + p_color R, s_color G)
    r = rpc_process([station("TEST-B", "Test Bravo", posted="OCEAN#X_SL", p_color="R")])
    check("posted-fallback (p_color) fires", r["transitions"] == 1, str(r))

    cleanup()

    # 7. live parity: pg shadow state vs thread state for the 20 real stations
    shadow = {r["station_id"]: r["status"] for r in
              sb.select("watcher_state_shadow", {"select": "station_id,status",
                                                 "station_id": "not.like.TEST-*"})}
    thread = {r["station_id"]: r["status"] for r in
              sb.select("watcher_state", {"select": "station_id,status",
                                          "station_id": "not.like.TEST-*"})}
    if shadow and thread:
        diffs = {k: (thread.get(k), shadow.get(k))
                 for k in set(thread) | set(shadow)
                 if thread.get(k) != shadow.get(k)}
        check(f"LIVE PARITY thread vs pg ({len(thread)} vs {len(shadow)} stations)",
              not diffs, str(diffs) if diffs else "")
    else:
        check("live parity (skipped — pg hasn't processed a real tick yet)", True,
              f"shadow={len(shadow)} thread={len(thread)}")

    print("\n" + ("ALL PASS ✅" if all(results) else "SOME FAILED ❌"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
