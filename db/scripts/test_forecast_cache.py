#!/usr/bin/env python3
"""Behavioral tests for the compute-on-visit forecast cache (migration 005).

Calls the /forecast/api/data handler directly (no HTTP server needed) against
the real Supabase `forecast_predictions` row, exercising: cold-start populate,
warm instant serve, stale recompute, held-guard stale serve, abandoned-guard
recovery, engine-less degradation, and the force-refresh endpoint.

Prereq: migration 005 pasted into the Supabase SQL editor. Safe to run against
the prod project — it only touches the single forecast_predictions cache row.

    venv/bin/python db/scripts/test_forecast_cache.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared import supabase as sb
import features.forecast.page as page

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def call_data() -> tuple[dict, float]:
    t0 = time.monotonic()
    status, _, body = page.handle_data({}, b"")
    took = time.monotonic() - t0
    assert status == 200, f"status {status}: {body[:200]}"
    return json.loads(body), took


def set_row(**patch):
    sb.update(page._TABLE, {"id": "eq.1"}, patch)


def get_row() -> dict:
    return sb.select(page._TABLE, {"select": "*", "id": "eq.1"})[0]


def main() -> int:
    if not sb.is_configured():
        print("Supabase env missing — aborting."); return 1
    try:
        get_row()
    except Exception as exc:
        print(f"forecast_predictions row unreadable ({exc}) — paste db/migrations/005 first.")
        return 1
    if not page.is_available():
        print("forecast engine unavailable in this venv — aborting."); return 1

    print("— cold start —")
    set_row(snapshot=None, generated_at=None, refresh_started_at=None)
    data, took = call_data()
    row = get_row()
    check("cold visit computes + stores", bool(row["snapshot"]) and bool(row["generated_at"]),
          f"{took:.1f}s, {len(data.get('predictions') or {})} prediction days")
    check("cold visit releases the guard", row["refresh_started_at"] is None)
    check("response carries generated_at", bool(data.get("generated_at")))
    gen1 = row["generated_at"]

    print("— warm —")
    data, took = call_data()
    check("warm visit is instant (no recompute)", took < 2.0, f"{took:.2f}s")
    check("warm visit serves the stored snapshot", get_row()["generated_at"] == gen1)

    print("— stale —")
    stale_ts = page._iso(page._utcnow() - timedelta(seconds=page.FRESH_SECONDS + 60))
    set_row(generated_at=stale_ts)
    data, took = call_data()
    check("stale visit recomputes", get_row()["generated_at"] != stale_ts, f"{took:.1f}s")
    check("recompute is fresh in the response", data.get("refreshing") is False)

    print("— guard held by a peer —")
    set_row(generated_at=stale_ts, refresh_started_at=page._iso(page._utcnow()))
    data, took = call_data()
    check("loser serves stale instantly", took < 2.0, f"{took:.2f}s")
    check("loser flags refreshing=true", data.get("refreshing") is True)
    check("loser did not recompute", get_row()["generated_at"] == stale_ts)

    print("— abandoned guard —")
    set_row(generated_at=stale_ts,
            refresh_started_at=page._iso(page._utcnow() - timedelta(seconds=page.GUARD_SECONDS + 60)))
    data, took = call_data()
    check("abandoned claim is stolen + recomputed", get_row()["generated_at"] != stale_ts,
          f"{took:.1f}s")

    print("— engine-less host with a stored snapshot —")
    set_row(generated_at=stale_ts)  # stale, so step 1 can't satisfy it
    saved_engine, saved_err = page._engine, page._engine_error
    page._engine, page._engine_error = None, "simulated: no ML deps on this host"
    try:
        data, took = call_data()
    finally:
        page._engine, page._engine_error = saved_engine, saved_err
    check("engine-less host serves stored snapshot", bool(data.get("predictions")),
          data.get("stale_note", ""))
    check("…with a stale_note", "unavailable" in (data.get("stale_note") or ""))

    print("— force refresh —")
    before = get_row()["generated_at"]
    status, _, body = page.handle_refresh({}, b"")
    check("refresh endpoint recomputes even when fresh",
          status == 200 and get_row()["generated_at"] != before)
    check("refresh releases the guard", get_row()["refresh_started_at"] is None)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
