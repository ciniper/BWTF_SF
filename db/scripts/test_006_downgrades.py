#!/usr/bin/env python3
"""Behavioral tests for downgrade/clear logging. Run AFTER migration 006.

Exercises bwtf_process_payload via RPC (default p_mode='shadow' — can never
send) with synthetic TEST-006 stations, asserting that severity decreases now
land in alert_log as event_type='cleared' rows (recipient_count=0,
channel='log', per-station from/to in results.transitions) while the
escalation path behaves exactly as before. Cleans up after itself.

    venv/bin/python db/scripts/test_006_downgrades.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb

results = []


def check(label: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))


def rpc_process(payload: list[dict]) -> dict:
    response = sb._request("POST", "rpc/bwtf_process_payload",
                           json_body={"p_payload": payload})
    return response.json()


def station(sid: str, name: str, *, cso: str | None = None,
            s_color: str | None = "G") -> dict:
    return {"stationid": sid, "stationname": name, "cso": cso,
            "s_color": s_color, "posted": None, "p_color": None}


def last_log(station_id: str) -> dict | None:
    rows = sb.select("alert_log", {"select": "*", "source": "eq.pg_shadow",
                                   "station_ids": "cs.{" + station_id + "}",
                                   "order": "id.desc", "limit": 1})
    return rows[0] if rows else None


def cleanup() -> None:
    sb.delete("watcher_state_shadow", {"station_id": "like.TEST-006*"})
    sb.delete("alert_log", {"source": "eq.pg_shadow", "station_ids": "cs.{TEST-006-A}"})


def main() -> int:
    assert sb.is_configured()
    cleanup()

    ok_state = [station("TEST-006-A", "Test 006 Alpha")]
    posted = [station("TEST-006-A", "Test 006 Alpha", s_color="R")]
    cso = [station("TEST-006-A", "Test 006 Alpha", cso="CSO#X")]

    # 1. seed silently, then escalate ok -> cso (the pre-006 path, unchanged)
    r = rpc_process(ok_state)
    check("unseen station seeds silently", r["transitions"] == 0
          and r.get("downgrades") == 0, str(r))
    r = rpc_process(cso)
    check("escalation still fires", r["transitions"] == 1 and r.get("downgrades") == 0, str(r))

    # 2. cso -> posted: a downgrade row with the exact from/to
    r = rpc_process(posted)
    check("downgrade counted in summary", r["transitions"] == 0 and r.get("downgrades") == 1, str(r))
    row = last_log("TEST-006-A")
    t = (row or {}).get("results", {}).get("transitions", [{}])[0]
    check("cleared row: type/count/channel", bool(row)
          and row["event_type"] == "cleared" and row["recipient_count"] == 0
          and row["channel"] == "log", str({k: row.get(k) for k in
          ("event_type", "recipient_count", "channel")} if row else None))
    check("cleared row: from=cso to=posted", t.get("from") == "cso" and t.get("to") == "posted", str(t))
    check("cleared row: log-only (no recipient data)",
          bool(row) and set(row["results"].keys()) == {"transitions"}, str(row and list(row["results"].keys())))

    # 3. posted -> ok: the window-closing clear
    r = rpc_process(ok_state)
    check("clear to ok counted", r.get("downgrades") == 1, str(r))
    row = last_log("TEST-006-A")
    t = (row or {}).get("results", {}).get("transitions", [{}])[0]
    check("cleared row: from=posted to=ok", t.get("from") == "posted" and t.get("to") == "ok", str(t))

    # 4. unchanged state logs nothing
    before = last_log("TEST-006-A")["id"]
    r = rpc_process(ok_state)
    after = last_log("TEST-006-A")["id"]
    check("no re-fire on unchanged state", r["transitions"] == 0
          and r.get("downgrades") == 0 and before == after, str(r))

    cleanup()
    print("\n" + ("ALL PASS ✅" if all(results) else "SOME FAILED ❌"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
