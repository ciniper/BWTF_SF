"""Online Postings Timeline (/cso-history): the lab-samples row (collection day + publish lag, 2026-09-27) and the
map-feed sample dates (when SFPUC's map first showed each sample; migration 018, 2026-09-29)."""
import re
from datetime import datetime, timezone
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.cso_history import page as P  # noqa: E402
from shared.stations import STATIONS  # noqa: E402


def test_sample_days_collapse_analytes_and_measure_the_lag_in_pacific_days():
    sid = "OCEAN#21.1_SL"; sfpuc = STATIONS[sid].sfpuc_id
    rows = [
        {"station_id": sid, "sample_date": "2026-09-21", "exceeds": False, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"},
        {"station_id": sid, "sample_date": "2026-09-21", "exceeds": True,  "first_seen_at": "2026-09-27T05:00:00+00:00", "source": "refresh"},  # 22:00 Pacific on the 26th
        {"station_id": sid, "sample_date": "2026-09-14", "exceeds": False, "first_seen_at": "2026-09-27T18:27:00+00:00", "source": "backfill"},
        {"station_id": sid, "sample_date": "2026-09-14", "exceeds": False, "first_seen_at": "2026-09-27T18:27:00+00:00", "source": "refresh"},   # mixed day → no honest lag
        {"station_id": "NOT_A_STATION", "sample_date": "2026-09-21", "exceeds": True, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"},
        {"station_id": sid, "sample_date": None, "exceeds": True, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"},
    ]
    out = P.build_sample_days(rows)
    assert set(out) == {sfpuc}
    d14, d21 = out[sfpuc]
    assert d21 == {"date": "2026-09-21", "elevated": True, "n": 2, "source": "refresh", "first_seen": "2026-09-26T23:30:00Z", "lag_days": 5, "lane": 0,
                   "map_seen": None, "map_lag_days": None, "map_approx": False, "map_censored": False}
    assert d14["source"] == "backfill" and d14["first_seen"] is None and d14["lag_days"] is None and d14["elevated"] is False and d14["n"] == 2 and d14["lane"] is None
    # the Pacific day matters: 05:00Z on the 27th is still the 26th in SF → lag 5, not 6
    late = P.build_sample_days([rows[1]])[sfpuc][0]
    assert late["lag_days"] == 5 and late["first_seen"] == "2026-09-27T05:00:00Z"
    assert P._median([]) is None and P._median([3]) == 3 and P._median([1, 4]) == 2.5 and P._median([5, 1, 3]) == 3


def test_lanes_stack_overlapping_publish_bars_and_skip_short_lags():
    mk = lambda date, fs, lag: {"date": date, "first_seen": fs, "lag_days": lag}
    # a storm week: Mon/Tue/Wed resamples all published Fri → three lanes; the following Monday's, published Thu, reuses lane 0
    entries = [mk("2026-11-02", "2026-11-06T22:00:00Z", 4), mk("2026-11-03", "2026-11-06T22:00:00Z", 3), mk("2026-11-04", "2026-11-06T22:00:00Z", 2),
               mk("2026-11-09", "2026-11-12T22:00:00Z", 3),
               mk("2026-11-16", None, None),                     # history: no bar
               mk("2026-11-23", "2026-11-24T18:00:00Z", 1)]      # published next day: tooltip only, no bar
    assert P.assign_lanes(entries) == 3
    assert [e["lane"] for e in entries] == [0, 1, 2, 0, None, None]
    # non-overlapping weekly bars share lane 0
    weekly = [mk("2026-10-05", "2026-10-09T22:00:00Z", 4), mk("2026-10-12", "2026-10-16T22:00:00Z", 4)]
    assert P.assign_lanes(weekly) == 1 and [e["lane"] for e in weekly] == [0, 0]
    assert P.assign_lanes([]) == 0
    # build_sample_days assigns lanes itself
    sid = "BAY#300.1_SL"
    rows = [{"station_id": sid, "sample_date": d, "exceeds": False, "first_seen_at": "2026-11-06T22:00:00+00:00", "source": "refresh"} for d in ("2026-11-02", "2026-11-03")]
    out = P.build_sample_days(rows)[STATIONS[sid].sfpuc_id]
    assert [e["lane"] for e in out] == [0, 1]


def test_feed_sample_dates_say_when_the_map_first_showed_each_sample():
    # the feed's MM/DD/YY (and the odd MM/DD/YYYY); anything else is None
    assert P.parse_feed_date("09/28/26") == "2026-09-28" and P.parse_feed_date("9/3/2026") == "2026-09-03"
    assert P.parse_feed_date(None) is None and P.parse_feed_date("") is None and P.parse_feed_date("2026-09-28") is None and P.parse_feed_date("n/a") is None
    # before 018: derived from station-day rows — the first day whose last tick showed the date, flagged approx
    days = [
        {"station_id": "4617", "day": "2026-09-27", "first_seen_at": "2026-09-27T18:26:00+00:00", "status_last": "posted", "posting_color": "R", "feed_sample_date": "09/23/26"},
        {"station_id": "4617", "day": "2026-09-28", "first_seen_at": "2026-09-28T07:00:00+00:00", "status_last": "posted", "posting_color": None, "feed_sample_date": "09/23/26"},
        {"station_id": "4617", "day": "2026-09-29", "first_seen_at": "2026-09-29T07:00:00+00:00", "status_last": "ok", "posting_color": None, "feed_sample_date": "09/28/26"},
        {"station_id": "4617", "day": "2026-09-30", "first_seen_at": "2026-09-30T07:00:00+00:00", "status_last": "ok", "posting_color": None, "feed_sample_date": "09/28/26"},
        {"station_id": "9999", "day": "2026-09-29", "first_seen_at": "2026-09-29T07:00:00+00:00", "status_last": "ok", "posting_color": None, "feed_sample_date": "09/28/26"},   # not a station we know
        {"station_id": "4619", "day": "2026-09-29", "first_seen_at": "2026-09-29T07:00:00+00:00", "status_last": "ok", "posting_color": None, "feed_sample_date": None},
    ]
    derived = P.derive_feed_sample_rows(days)
    assert [(r["station_id"], r["sample_date"], r["first_seen_at"][:10], r["approx"], r["status_first"]) for r in derived] == [
        ("4617", "2026-09-23", "2026-09-27", True, "posted"), ("4617", "2026-09-28", "2026-09-29", True, "ok"), ("9999", "2026-09-28", "2026-09-29", True, "ok")]
    feed = P.build_feed_dates(derived + [
        {"station_id": "4613", "sample_date": "2026-10-05", "first_seen_at": "2026-10-06T16:31:00+00:00", "approx": False, "status_first": "ok"}])   # 018's exact tick
    assert set(feed) == {("4617", "2026-09-23"), ("4617", "2026-09-28"), ("4613", "2026-10-05")}   # 9999 dropped
    # already on the map the first day we read the feed → censored: "by Sep 27", no lag
    assert feed[("4617", "2026-09-23")] == {"seen": "2026-09-27T19:00:00Z", "seen_day": "2026-09-27", "approx": True, "censored": True, "status": "posted"}
    # approx rows sit at noon Pacific of their day; exact rows keep the tick
    assert feed[("4617", "2026-09-28")]["seen"] == "2026-09-29T19:00:00Z" and feed[("4617", "2026-09-28")]["censored"] is False
    assert feed[("4613", "2026-10-05")] == {"seen": "2026-10-06T16:31:00Z", "seen_day": "2026-10-06", "approx": False, "censored": False, "status": "ok"}

    # merged into the sample days: map fields on published days, a pending entry for a date the lab dataset lacks
    sid = "BAY#300.1_SL"; assert STATIONS[sid].sfpuc_id == "4617"
    rows = [{"station_id": sid, "sample_date": "2026-09-23", "exceeds": True, "first_seen_at": "2026-09-26T23:30:00+00:00", "source": "refresh"}]
    now = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)   # Oct 1, 11:00 Pacific
    out = P.build_sample_days(rows, feed, now)
    d23, d28 = out["4617"]
    assert d23["map_seen"] == "2026-09-27T19:00:00Z" and d23["map_censored"] is True and d23["map_lag_days"] is None and d23["lag_days"] == 3
    assert d28 == {"date": "2026-09-28", "elevated": None, "n": 0, "source": "feed", "first_seen": None, "lag_days": None, "pending": True, "pending_days": 3,
                   "map_seen": "2026-09-29T19:00:00Z", "map_lag_days": 1, "map_approx": True, "map_censored": False, "lane": 0}
    # Aquatic Park's exact tick: 1 day after collection; a future-dated feed row is ignored
    ap = P.build_sample_days([], {("4613", "2026-10-05"): feed[("4613", "2026-10-05")], ("4613", "2026-10-09"): dict(feed[("4613", "2026-10-05")], seen_day="2026-10-10")},
                             datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc))["4613"]
    assert [e["date"] for e in ap] == ["2026-10-05"] and ap[0]["map_lag_days"] == 1 and ap[0]["pending_days"] == 3
    # a pending bar ends at now and takes a lane like any other; under MIN_BAR_LAG_DAYS it has none
    e1 = {"date": "2026-09-28", "first_seen": None, "lag_days": None, "pending": True, "pending_days": 3}
    e2 = {"date": "2026-09-29", "first_seen": "2026-10-01T16:00:00Z", "lag_days": 2}
    e3 = {"date": "2026-09-30", "first_seen": None, "lag_days": None, "pending": True, "pending_days": 1}
    assert P.assign_lanes([e1, e2, e3], now) == 2 and [e["lane"] for e in (e1, e2, e3)] == [0, 1, None]
    assert P.assign_lanes([dict(e1)]) == 0   # no ``now`` → a pending entry cannot draw a bar


def test_migration_018_adds_only_marked_blocks_to_the_feed_day_function():
    sql018 = (ROOT / "db/migrations/018_feed_sample_dates.sql").read_text()
    sql012 = (ROOT / "db/migrations/012_history_tables.sql").read_text()
    grab = lambda sql: re.search(r"create or replace function public\.bwtf_record_feed_day\(p_payload jsonb\).*?\nend \$\$;\n", sql, re.S).group(0)
    fn018, fn012 = grab(sql018), grab(sql012)
    blocks = re.findall(r"^[ \t]*-- 018:.*?^[ \t]*-- /018\n", fn018, re.S | re.M)
    assert len(blocks) == 2 and "insert into public.feed_sample_dates" in blocks[1] and "on conflict (station_id, sample_date) do nothing" in blocks[1]
    stripped = fn018
    for b in blocks:
        stripped = stripped.replace(b, "")
    assert stripped == fn012
    for needle in ("create table if not exists public.feed_sample_dates", "primary key (station_id, sample_date)",
                   "create or replace function public.bwtf_feed_sample_date(p text)", "exception when others then",
                   "from public.feed_station_days f", "revoke all on public.feed_sample_dates from anon, authenticated",
                   "grant execute on function public.bwtf_record_feed_day(jsonb) to service_role"):
        assert needle in sql018, needle


def test_page_carries_the_samples_row_and_the_events_payload_has_the_keys():
    html = (ROOT / "app/templates/cso_history/page.html").read_text()
    for needle in ("className = 'row srow'", "showSampleTip", "smark", "swatch lag", "DATA.samples", "sample_lag", "Numbers online", "d.lane", "window.__timeline",
                   "swatch pending", "swatch mapmark", "className = 'mapmark'", "d.pending ? 'pending'", "lagbar.pending", "d.map_censored", "sample_lag || {}).map"):
        assert needle in html, needle
    # the unconfigured path keeps the old shape; the configured path adds samples + sample_lag (exercised against Supabase when available)
    from shared import supabase as sb
    import json
    status, ctype, body = P.handle_events({}, b"")
    d = json.loads(body)
    assert status == 200 and "stations" in d and "event_count" in d
    if sb.is_configured() and not d.get("note"):
        assert "samples" in d and "sample_lag" in d and set(d["sample_lag"]) == {"n", "median_days", "measured_since", "map"}
        assert set(d["sample_lag"]["map"]) == {"n", "median_days", "after_map_n", "after_map_median_days", "pending", "measured_since"}
        for sid, days in d["samples"].items():
            assert any(s["station_id"] == sid for s in d["stations"]), sid    # every sampled station has a row


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
