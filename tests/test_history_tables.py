"""Migration 012 (history tables) and its two Python writers.

Run: venv/bin/python tests/test_history_tables.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.forecast import page  # noqa: E402
from shared import samples_mirror  # noqa: E402


def test_migration_012_drops_the_legacy_table_and_copies_the_tick_from_004():
    sql = (ROOT / "db/migrations/012_history_tables.sql").read_text()
    assert "drop table if exists public.watcher_state;" in sql
    for t in ("forecast_history", "feed_station_days", "samples"):
        assert f"create table if not exists public.{t} (" in sql, t
        assert f"alter table public.{t} enable row level security;" in sql, t
        assert f"revoke all on public.{t} from anon, authenticated;" in sql, t
    assert "create or replace function public.bwtf_record_forecast(p_date date, p_snapshot jsonb, p_generated_at timestamptz)" in sql
    assert "create or replace function public.bwtf_record_feed_day(p_payload jsonb)" in sql
    # the tick is 004's body plus one guarded hook — nothing else may differ
    grab = lambda s: re.search(r"create or replace function public\.bwtf_shadow_tick\(\).*?\nend \$\$;\n", s, re.S).group(0)
    tick012 = grab(sql)
    tick004 = grab((ROOT / "db/migrations/004_pg_live_dispatch.sql").read_text())
    hook = re.search(r"        -- 012: one row per station-day.*?        end;\n", tick012, re.S)
    assert hook and "perform bwtf_record_feed_day(payload);" in hook.group(0)
    assert "feed_day_error" in hook.group(0) and "last_error" not in hook.group(0)   # never touches the health gate's column
    assert tick012.replace(hook.group(0), "") == tick004
    # the feed-day function reads the same station keys the classifier does (011)
    for key in ("stationid", "stationname", "cso", "s_color", "posted", "p_color"):
        assert f"station->>'{key}'" in sql, key


def test_record_history_uses_the_engine_day_and_never_raises(monkeypatch=None):
    calls = []
    orig = page.sb.rpc
    page.sb.rpc = lambda fn, payload: calls.append((fn, payload)) or {}
    try:
        now = datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc)   # 20:00 Pacific on the 27th
        snap = {"predictions": {"2026-09-27": {"date": "2026-09-27", "is_today": True, "zones": {"east": 0.1}},
                                "2026-09-28": {"date": "2026-09-28", "is_today": False}}, "generated_at": "x"}
        assert page._record_history(snap, now) is True
        fn, payload = calls[-1]
        assert fn == "bwtf_record_forecast" and payload["p_date"] == "2026-09-27" and payload["p_generated_at"] == "2026-09-28T03:00:00Z"
        assert payload["p_snapshot"]["predictions"]["2026-09-27"]["zones"] == {"east": 0.1}
        # no is_today day → the Pacific calendar day of now
        assert page._forecast_date({"predictions": {}}, now) == "2026-09-27"
        # a failing rpc is logged, not raised
        def boom(fn, payload):
            raise RuntimeError("relation forecast_history does not exist")
        page.sb.rpc = boom
        assert page._record_history(snap, now) is False
    finally:
        page.sb.rpc = orig


def test_samples_mirror_shapes_rows_and_inserts_do_nothing_in_batches():
    from shared.stations import STATIONS
    st = sorted(STATIONS)[0]
    samples = [
        {"date": "2026-09-21", "station": st, "analyte": "ENTERO", "value": 41.0, "value_raw": "41", "exceeds": False},
        {"date": "2026-09-21", "station": st, "analyte": "ENTERO", "value": 41.0, "value_raw": "41", "exceeds": False},   # duplicate collapses
        {"date": "2026-09-21", "station": "NOT_A_STATION", "analyte": "ENTERO", "value": 5.0, "value_raw": "<10"},      # unknown station dropped
        {"date": "2026-09-21", "station": st, "analyte": "COLI_TOTAL", "value": 24196.0, "value_raw": ">24196", "exceeds": True},
        {"date": "2026-09-21", "station": st, "analyte": "COLI_E", "value": None, "value_raw": ""},                    # blank raw dropped
    ]
    rows = samples_mirror.to_rows(samples)
    assert [r["analyte"] for r in rows] == ["ENTERO", "COLI_TOTAL"]
    assert rows[1] == {"station_id": st, "sample_date": "2026-09-21", "analyte": "COLI_TOTAL", "value_raw": ">24196", "value": 24196.0, "exceeds": True}
    calls = []
    orig = samples_mirror.sb.upsert
    samples_mirror.sb.upsert = lambda table, rows, on_conflict, resolution="merge-duplicates": calls.append((table, len(rows), on_conflict, resolution))
    try:
        many = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "station": st, "analyte": "ENTERO", "value": 1.0, "value_raw": str(i)} for i in range(1200)]
        assert samples_mirror.mirror(many) == 1200
        assert [c[1] for c in calls] == [500, 500, 200]
        assert all(c[0] == "samples" and c[2] == samples_mirror.ON_CONFLICT and c[3] == "ignore-duplicates" for c in calls)
    finally:
        samples_mirror.sb.upsert = orig


def test_engine_keeps_the_live_samples_and_the_refresh_writes_only_after_storing():
    from features.forecast import live_dashboard as ld
    eng_cls = type(ld.LIVE)
    obj = object.__new__(eng_cls)
    fetched = [{"date": "2026-09-21", "station": "S", "analyte": "ENTERO", "value": 200.0, "value_raw": "200", "exceeds": True}]
    obj._samples_window = lambda s, e: fetched
    eng_cls._sample_flags(obj, "2026-09-20", "2026-09-27")
    assert obj.last_live_samples is fetched

    class FakeLive:
        last_live_samples = fetched
        def refresh(self): pass
        def get_snapshot(self): return {"predictions": {"2026-09-27": {"date": "2026-09-27", "is_today": True}}}
    class FakeEngine:
        LIVE = FakeLive()
    seen = []
    saved = (page._engine, page._store_snapshot, page._record_history, page._mirror_samples)
    page._engine = FakeEngine
    page._record_history = lambda snap, now: seen.append("history")
    page._mirror_samples = lambda: seen.append("mirror")
    now = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
    try:
        page._store_snapshot = lambda snap, now: True
        page._compute_and_store({"snapshot": {}, "generated_at": "2026-09-27T17:00:00Z"}, now)
        assert seen == ["history", "mirror"]
        page._store_snapshot = lambda snap, now: False      # store failed → nothing recorded
        page._compute_and_store({"snapshot": {}, "generated_at": "2026-09-27T17:00:00Z"}, now)
        assert seen == ["history", "mirror"]
        page._compute_and_store(None, now)                  # no cache row (Supabase-less host) → nothing recorded
        assert seen == ["history", "mirror"]
    finally:
        page._engine, page._store_snapshot, page._record_history, page._mirror_samples = saved


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
