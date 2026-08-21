#!/usr/bin/env python3
"""Behavioral tests for the CSO event timeline (/cso-history).

Two layers, neither needs migration 006 applied:
  * pure window-building on canned alert_log rows (escalate/segment/clear,
    parallel-source dedup, watcher fallback rows, dangling clears, open-window
    resolution against the live roster);
  * the API handler with a faked Supabase layer — asserting the PostgREST
    select never asks for raw `results` (recipient data stays in the DB) and
    the response carries no contact fields even when the backing rows do.

    venv/bin/python db/scripts/test_cso_history.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.cso_history import page

results = []


def check(label: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))


def row(ts: str, source: str, event_type: str, transitions, ids=None, names=None) -> dict:
    return {"id": 0, "created_at": ts, "source": source, "event_type": event_type,
            "station_ids": ids or [], "station_names": names or [],
            "transitions": transitions}


def t(sid, name, frm, to):
    return {"station_id": sid, "station_name": name, "from": frm, "to": to,
            "simulated": False}


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def main() -> int:
    # 1. open -> escalate -> partial clear -> full clear = one 3-segment window
    rows = [
        row("2026-08-17T10:00:00+00:00", "pg_live", "posted", [t("S1", "Aquatic Park", "ok", "posted")]),
        row("2026-08-17T11:00:00+00:00", "pg_live", "cso", [t("S1", "Aquatic Park", "posted", "cso")]),
        row("2026-08-18T09:00:00+00:00", "pg_live", "cleared", [t("S1", "Aquatic Park", "cso", "posted")]),
        row("2026-08-18T15:00:00+00:00", "pg_live", "cleared", [t("S1", "Aquatic Park", "posted", "ok")]),
    ]
    st = page.build_station_windows(rows, {}, NOW)
    w = st[0]["windows"][0]
    check("one closed window", len(st) == 1 and len(st[0]["windows"]) == 1
          and w["end"] == "2026-08-18T15:00:00Z" and w["end_recorded"] and not w["ongoing"], str(w))
    check("segments posted→cso→posted",
          [s["status"] for s in w["segments"]] == ["posted", "cso", "posted"], str(w["segments"]))

    # 2. parallel-run duplicates coalesce (watcher logs the same event ~1 min later)
    rows = [
        row("2026-08-17T10:00:00+00:00", "pg_shadow", "posted", [t("S1", "Aquatic Park", "ok", "posted")]),
        row("2026-08-17T10:01:00+00:00", "watcher", "posted", None, ids=["S1"], names=["Aquatic Park"]),
        row("2026-08-17T12:00:00+00:00", "pg_shadow", "cleared", [t("S1", "Aquatic Park", "posted", "ok")]),
    ]
    st = page.build_station_windows(rows, {}, NOW)
    check("duplicate source rows -> one window, earliest start",
          len(st[0]["windows"]) == 1 and st[0]["windows"][0]["start"] == "2026-08-17T10:00:00Z",
          str(st[0]["windows"]))

    # 3. watcher fallback rows (no transitions payload) still open windows
    rows = [row("2026-08-14T10:00:00+00:00", "watcher", "cso", None,
                ids=["S2"], names=["Ocean Beach"])]
    st = page.build_station_windows(rows, {"S2": {"station_name": "Ocean Beach", "status": "cso"}}, NOW)
    w = st[0]["windows"][0]
    check("watcher fallback opens window; roster says still ongoing",
          w["segments"][0]["status"] == "cso" and w["ongoing"] and not w["end_recorded"], str(w))

    # 4. open window + roster ok = end not recorded (pre-006 event)
    st = page.build_station_windows(rows, {"S2": {"station_name": "Ocean Beach", "status": "ok"}}, NOW)
    w = st[0]["windows"][0]
    check("roster-cleared open window -> end not recorded",
          not w["ongoing"] and not w["end_recorded"] and w["end"] is None, str(w))

    # 5. dangling clear (simulated teardown) is ignored; quiet roster stations listed
    rows = [row("2026-08-19T10:00:00+00:00", "pg_live", "cleared",
                [t("S3", "Crissy Field", "cso", "ok")])]
    st = page.build_station_windows(rows, {"S4": {"station_name": "Baker Beach", "status": "ok"}}, NOW)
    check("dangling clear ignored, roster station shown eventless",
          all(not s["windows"] for s in st)
          and {s["station_id"] for s in st} == {"S3", "S4"}, str(st))

    # 6. eventful stations sort first, most recent on top
    rows = [
        row("2026-08-16T10:00:00+00:00", "pg_shadow", "posted", [t("S5", "China Beach", "ok", "posted")]),
        row("2026-08-16T11:00:00+00:00", "pg_shadow", "cleared", [t("S5", "China Beach", "posted", "ok")]),
        row("2026-08-19T10:00:00+00:00", "pg_live", "posted", [t("S6", "Candlestick", "ok", "posted")]),
        row("2026-08-19T11:00:00+00:00", "pg_live", "cleared", [t("S6", "Candlestick", "posted", "ok")]),
    ]
    st = page.build_station_windows(rows, {"S0": {"station_name": "Alcatraz", "status": "ok"}}, NOW)
    check("sort: recent-eventful first, quiet last",
          [s["station_id"] for s in st] == ["S6", "S5", "S0"], str([s["station_id"] for s in st]))

    # 7. API handler: select shape + response sanitization (faked sb layer)
    captured = []
    canned = [dict(row("2026-08-17T10:00:00+00:00", "pg_shadow", "posted",
                       [t("S1", "Aquatic Park", "ok", "posted")]))]

    def fake_select(table, params):
        captured.append((table, dict(params)))
        if params.get("limit") == "1":
            return [{"created_at": "2026-08-13T08:00:00+00:00"}]
        return canned

    sb_real = (page.sb.is_configured, page.sb.select)
    page.sb.is_configured = lambda: True
    page.sb.select = fake_select
    roster_real = page._fetch_roster
    page._fetch_roster = lambda: {}
    try:
        status, ctype, body = page.handle_events({}, None)
    finally:
        page.sb.is_configured, page.sb.select = sb_real
        page._fetch_roster = roster_real

    payload = json.loads(body)
    event_select = captured[0][1]
    check("API 200 JSON", status == 200 and ctype == "application/json")
    check("select pulls only results->transitions (never raw results)",
          "transitions:results->transitions" in event_select["select"]
          and "results," not in event_select["select"]
          and not event_select["select"].rstrip().endswith("results"), event_select["select"])
    check("select excludes simulated + non-detection sources",
          event_select.get("simulated") == "eq.false"
          and event_select.get("source") == "in.(watcher,pg_shadow,pg_live)", str(event_select))
    body_text = body.decode()
    check("response carries no recipient-ish fields",
          all(k not in body_text for k in ("email", "phone", "carrier", "recipient", "would")),
          body_text[:200])
    check("response shape", payload["monitoring_since"] == "2026-08-13T08:00:00Z"
          and payload["event_count"] == 1
          and payload["stations"][0]["station_id"] == "S1", str(payload)[:200])

    # 8. unconfigured Supabase degrades to an empty, noted payload
    page.sb.is_configured = lambda: False
    try:
        status, _, body = page.handle_events({}, None)
    finally:
        page.sb.is_configured = sb_real[0]
    payload = json.loads(body)
    check("unconfigured -> empty + note", status == 200 and payload["stations"] == []
          and "note" in payload, str(payload))

    print("\n" + ("ALL PASS ✅" if all(results) else "SOME FAILED ❌"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
