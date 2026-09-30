"""live_v2 (2026-09-29): the sample rules read SFPUC's beach map first.

`live_dashboard._feed_sample_flags` turns the watcher's per-station-day record
of the map (Supabase `feed_station_days`) into {(group, sample date): elevated}:
posted for bacteria → over standard, clear all day → clean, a clearing day or a
precautionary CSO posting → nothing. `_cso_flag_days` reads the watcher's live
state from `watcher_state_shadow` (migration 012 dropped `watcher_state`) and
ignores simulated rows. Run: venv/bin/python tests/test_live_v2.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "features" / "forecast"))
from features.forecast import live_dashboard as ld  # noqa: E402
from features.forecast.src.models import live_rules as LR  # noqa: E402


class FakeSupabase:
    """Stands in for shared.supabase: fixed rows per table, a 404 for the rest."""

    def __init__(self, tables: dict):
        self.tables, self.calls = tables, []

    def is_configured(self) -> bool:
        return True

    def select(self, table: str, params: dict) -> list:
        self.calls.append((table, dict(params)))
        if table not in self.tables:
            raise RuntimeError(f"GET {table} -> 404: could not find the table")
        return [dict(r) for r in self.tables[table]]


def _with_fake(tables: dict):
    fake = FakeSupabase(tables)
    real = ld._supabase
    ld._supabase = fake
    return fake, real


def _group_of(sfpuc_id: str) -> str:
    return ld.GROUP_OF_STATION[ld.STATION_BY_SFPUC_ID[sfpuc_id]]


def test_version_and_sources():
    assert LR.VERSION == "live_v2"
    assert tuple(LR.RULES["samples"]["sources"]) == ("feed", "datasf")
    # the arithmetic constants are live_v1's: same replays, same grades
    assert LR.RULES["samples"]["known_lag_days"] == 1 and LR.RULES["samples"]["horizon_days"] == 3
    assert LR.RULES["samples"]["floor_elevated_tail"]["east"] == 0.80


def test_parse_feed_date():
    assert ld._parse_feed_date("09/21/26") == date(2026, 9, 21)
    assert ld._parse_feed_date("09/21/2026") == date(2026, 9, 21)
    assert ld._parse_feed_date("2026-09-21T00:00:00") == date(2026, 9, 21)
    assert ld._parse_feed_date("") is None and ld._parse_feed_date(None) is None and ld._parse_feed_date("n/a") is None


def test_feed_sample_flags_reads_the_map_as_results():
    sunnydale, aquatic, lobos = "4617", "4613", "4610"     # Sunnydale Cove, Aquatic Park, Baker Beach at Lobos Creek
    se, ap = _group_of(sunnydale), _group_of(aquatic)
    rows = [
        # posted for bacteria on the 23rd for the Monday sample: that sample was over standard
        {"station_id": sunnydale, "day": "2026-09-23", "status_max": "posted", "status_last": "posted", "raw_last": {"sample_date": "09/21/26"}},
        # still posted on the 27th, the map now names the resample: over standard too
        {"station_id": sunnydale, "day": "2026-09-27", "status_max": "posted", "status_last": "posted", "raw_last": {"sample_date": "09/23/26"}},
        # the sign came down on the 28th with the same sample date: ambiguous, says nothing (and never overrides the elevated result)
        {"station_id": sunnydale, "day": "2026-09-28", "status_max": "posted", "status_last": "ok", "raw_last": {"sample_date": "09/23/26"}},
        # clear all day with a sample date: that sample was clean
        {"station_id": aquatic, "day": "2026-09-27", "status_max": "ok", "status_last": "ok", "raw_last": {"sample_date": "09/21/26"}},
        # a precautionary CSO posting is not a lab result
        {"station_id": lobos, "day": "2026-09-22", "status_max": "cso", "status_last": "cso", "raw_last": {"sample_date": "09/21/26"}},
        # unknown station, missing dates, a sample outside the window: all ignored
        {"station_id": "9999", "day": "2026-09-27", "status_max": "ok", "status_last": "ok", "raw_last": {"sample_date": "09/21/26"}},
        {"station_id": aquatic, "day": "2026-09-27", "status_max": "ok", "status_last": "ok", "raw_last": {}},
        {"station_id": aquatic, "day": "2026-09-27", "status_max": "ok", "status_last": "ok", "raw_last": {"sample_date": "09/01/26"}},
    ]
    fake, real = _with_fake({"feed_station_days": rows})
    try:
        eng = ld.LiveData.__new__(ld.LiveData)
        out = eng._feed_sample_flags(date(2026, 9, 20), date(2026, 9, 29))
    finally:
        ld._supabase = real
    assert out == {(se, date(2026, 9, 21)): True, (se, date(2026, 9, 23)): True, (ap, date(2026, 9, 21)): False}, out
    assert all(k[0] != _group_of(lobos) for k in out)
    assert fake.calls and fake.calls[0][0] == "feed_station_days" and fake.calls[0][1]["day"] == "gte.2026-09-20"

    # an elevated sibling in the same beach group outranks a clean one on the same sample date, whatever the row order
    siblings = [sid for sid, code in ld.STATION_BY_SFPUC_ID.items() if ld.GROUP_OF_STATION.get(code) == se and sid != sunnydale]
    assert siblings, "the Southeast group has more than one station"
    rows2 = [
        {"station_id": siblings[0], "day": "2026-09-24", "status_max": "ok", "status_last": "ok", "raw_last": {"sample_date": "09/22/26"}},
        {"station_id": sunnydale, "day": "2026-09-24", "status_max": "posted", "status_last": "posted", "raw_last": {"sample_date": "09/22/26"}},
    ]
    for order in (rows2, rows2[::-1]):
        fake, real = _with_fake({"feed_station_days": order})
        try:
            out2 = ld.LiveData.__new__(ld.LiveData)._feed_sample_flags(date(2026, 9, 20), date(2026, 9, 29))
        finally:
            ld._supabase = real
        assert out2 == {(se, date(2026, 9, 22)): True}, out2

    # the table missing (or Supabase down) → nothing observed, never an exception
    fake, real = _with_fake({})
    try:
        assert ld.LiveData.__new__(ld.LiveData)._feed_sample_flags(date(2026, 9, 20), date(2026, 9, 29)) == {}
    finally:
        ld._supabase = real
    print(f"   map → results: {out}")


def test_cso_flag_days_reads_the_shadow_state_and_skips_simulations():
    today = date(2026, 9, 29)
    shadow = [
        {"station_id": "4617", "status": "cso", "sim_active": False},   # Sunnydale Cove: a real flag up right now → Southeast today
        {"station_id": "4613", "status": "cso", "sim_active": True},    # Aquatic Park: a simulation → ignored
        {"station_id": "4610", "status": "posted", "sim_active": False},
    ]
    fake, real = _with_fake({"alert_log": [], "watcher_state_shadow": shadow})
    try:
        eng = ld.LiveData.__new__(ld.LiveData)
        flags = eng._cso_flag_days(today - ld.timedelta(days=7), today, today)
    finally:
        ld._supabase = real
    assert flags == {today: {"southeast"}}, flags
    assert [c[0] for c in fake.calls] == ["alert_log", "watcher_state_shadow"]
    assert "watcher_state" not in [c[0] for c in fake.calls]

    # before the fix the reader asked for the dropped table and every call came back empty
    fake, real = _with_fake({"alert_log": [], "watcher_state": shadow})
    try:
        assert ld.LiveData.__new__(ld.LiveData)._cso_flag_days(today - ld.timedelta(days=7), today, today) == {}
    finally:
        ld._supabase = real


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
