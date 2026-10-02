"""The Today board reads the watcher's stored copy of SFPUC's feed when it is fresh, and SFPUC live
otherwise (shared/feed_copy.py, 2026-10-02). Offline: injected rows, stub clients."""
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import feed_copy as F  # noqa: E402
from shared.sfpuc_api import SFPUCRealTimeAPI  # noqa: E402

NOW = datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)
ITEM = lambda sid, name, s="G", posted="", p="", cso="", d="09/28/26": {"stationid": sid, "stationname": name, "s_color": s, "posted": posted, "p_color": p,  # noqa: E731
                                                                        "cso": cso, "sample_date": d, "lat": "37.7", "lon": "-122.5"}


def rows(*specs):
    return [{"station_id": it["stationid"], "last_seen_at": (NOW - timedelta(seconds=age)).isoformat(), "raw_last": it} for it, age in specs]


def test_a_fresh_copy_gives_the_newest_ticks_stations():
    got = []
    def select(table, params):
        got.append((table, params)); return rows((ITEM("4602", "Ocean Beach at Sloat Boulevard"), 40), (ITEM("4619", "Islais Creek", s="R"), 41),
                                                 (ITEM("4602", "Ocean Beach at Sloat Boulevard", s="R"), 3700),   # the same station an hour earlier: ignored
                                                 (ITEM("4601", "Fort Funston"), 900))                             # not in the newest tick: dropped
    c = F.latest_feed(now=NOW, select=select)
    assert got[0][0] == "feed_station_days" and got[0][1]["day"] == "gte.2026-10-01" and got[0][1]["order"] == "last_seen_at.desc"
    assert sorted(i["stationid"] for i in c["items"]) == ["4602", "4619"] and c["checked_at"] == NOW - timedelta(seconds=40)
    assert [i["s_color"] for i in c["items"] if i["stationid"] == "4602"] == ["G"]                                     # the latest sighting wins


def test_a_stale_empty_or_broken_copy_means_reading_sfpuc_live():
    assert F.latest_feed(now=NOW, select=lambda t, p: rows((ITEM("4602", "x"), F.FRESH_SECONDS + 5))) is None          # the watcher stopped
    assert F.latest_feed(now=NOW, select=lambda t, p: []) is None
    assert F.latest_feed(now=NOW, select=lambda t, p: 1 / 0) is None
    assert F.latest_feed(now=NOW, select=lambda t, p: [{"station_id": "4602", "last_seen_at": NOW.isoformat(), "raw_last": None}]) is None


def test_the_board_uses_the_copy_and_never_calls_sfpuc_when_it_is_fresh():
    from app import landing as L

    class Live(SFPUCRealTimeAPI):
        calls = 0
        def fetch_stations(self):   # one station back, so the empty-feed summary fallback (a second fetch) stays out of the count
            Live.calls += 1; return self.stations_from_items([ITEM("4601", "Fort Funston")])
    copy = {"items": [ITEM("4602", "Ocean Beach at Sloat Boulevard"), ITEM("4619", "Islais Creek", s="R", posted="4619", p="R")],
            "checked_at": datetime(2026, 10, 2, 16, 29, tzinfo=timezone.utc)}
    ctx = L.board_context(Live(), None, live_facts=False, feed_copy=lambda: copy)
    b = ctx["board"]
    assert Live.calls == 0 and b["feed_source"] == "watcher" and b["checked"] == "9:29 AM" and b["date"] == "Fri Oct 2"   # the tick's Pacific time
    assert b["posted"] == ["Islais Creek"] and b["feed_ok"]
    stale = L.board_context(Live(), None, live_facts=False, feed_copy=lambda: None)
    assert Live.calls == 1 and stale["board"]["feed_source"] == "live" and stale["board"]["feed_age_s"] is None        # stale → SFPUC live, as before
    broken = L.board_context(Live(), None, live_facts=False, feed_copy=lambda: 1 / 0)
    assert Live.calls == 2 and broken["board"]["feed_source"] == "live"
    plain = L.board_context(Live(), None, live_facts=False)
    assert Live.calls == 3 and plain["board"]["feed_source"] == "live"                                                 # no feed_copy: the old path


def test_one_parser_for_both_paths_and_the_page_opts_in():
    api = SFPUCRealTimeAPI()
    st = api.stations_from_items([ITEM("4619", "Islais Creek", s="G", cso="4619")])[0]
    assert st.station_id == "4619" and st.has_cso and st.sample_date == datetime(2026, 9, 28)
    src = (ROOT / "shared/sfpuc_api.py").read_text()
    assert "return self.stations_from_items(data)" in src and src.count("SFPUCStation(") == 1   # one constructor call: fetch_stations goes through it
    page = (ROOT / "features/today/page.py").read_text()
    assert "feed_copy=None if live else latest_feed" in page and '.get("feed")' in page        # ?feed=live forces the old path, for A/B tests
    assert 'data-feed="{{ board.feed_source or \'live\' }}"' in (ROOT / "app/templates/_today_board.html").read_text()   # curl-checkable in production


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("  ok  ", name)
            except Exception as e:  # noqa: BLE001
                failures += 1; print("FAIL", name + ":", type(e).__name__ + ":", e)
    print("ALL PASS" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
