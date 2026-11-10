"""The morning gap (shared/result_gap.py): SFPUC moves a posting at ~8:30 AM and the
sample behind it reaches their map hours later. The cases are real ones from the Online
Postings Timeline (Oct–Nov 2026). Offline: no Supabase."""
import pathlib
import sys
import types
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import result_gap as G  # noqa: E402

P = ZoneInfo("America/Los_Angeles")


def pt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=P)


def log_row(at: str, sid: str, frm: str, to: str, name: str = "") -> dict:
    return {"created_at": pt(at).astimezone(timezone.utc).isoformat(), "event_type": to if to != "ok" else "clear",
            "transitions": [{"station_id": sid, "station_name": name, "from": frm, "to": to}]}


def test_latest_change_per_station_and_only_safe_posted_moves_count():
    rows = [log_row("2026-10-27T08:16", "4616", "ok", "posted"), log_row("2026-10-28T08:07", "4616", "posted", "ok"),
            log_row("2026-10-28T09:00", "4619", "ok", "cso"), log_row("2026-10-28T09:30", "4605", "cso", "posted")]
    ch = G.latest_changes(rows)
    assert ch["4616"]["from"] == "posted" and ch["4616"]["to"] == "ok"            # the newest one wins
    recent = G.candidates(ch, pt("2026-10-28T10:00"))
    assert set(recent) == {"4616"}                                               # a discharge flag up or down is not a lab result
    assert G.candidates(ch, pt("2026-10-29T21:00")) == {}                        # 36 h on, nothing is claimed


def test_windsurfer_oct_27_posting_waits_until_the_map_shows_the_oct_26_sample():
    ch = G.latest_changes([log_row("2026-10-27T08:16", "4616", "ok", "posted")])
    seen = {("4616", "2026-10-19"): pt("2026-10-20T13:00"), ("4616", "2026-10-26"): pt("2026-10-27T14:52")}
    before = G.pending_results(ch, {"4616": "2026-10-19"}, seen, pt("2026-10-27T10:00"))
    assert before == {"4616": {"at": "2026-10-27T15:16:00Z", "change": "posted"}}
    after = G.pending_results(ch, {"4616": "2026-10-26"}, seen, pt("2026-10-27T15:00"))
    assert after == {}                                                           # the map caught up at 2:52 PM


def test_clear_mornings_wait_too():
    ch = G.latest_changes([log_row("2026-10-15T08:13", "4610", "posted", "ok")])
    seen = {("4610", "2026-10-13"): pt("2026-10-14T13:41")}
    assert G.pending_results(ch, {"4610": "2026-10-13"}, seen, pt("2026-10-15T09:00"))["4610"]["change"] == "cleared"


def test_without_feed_ticks_the_dates_decide():
    ch = G.latest_changes([log_row("2026-11-10T08:32", "4605", "ok", "posted")])
    assert G.pending_results(ch, {"4605": "2026-11-02"}, None, pt("2026-11-10T09:46")) == {"4605": {"at": "2026-11-10T16:32:00Z", "change": "posted"}}
    assert G.pending_results(ch, {"4605": "2026-11-09"}, None, pt("2026-11-10T13:00")) == {}   # the Nov 9 sample explains it
    assert G.pending_results(ch, {}, None, pt("2026-11-10T09:46")) == {}                       # no sample date shown: nothing to contradict


def test_board_row_reads_result_on_the_way_and_the_tile_follows():
    from app import landing as L
    S = lambda sid, name, status, d: types.SimpleNamespace(station_id=sid, station_name=name, status=types.SimpleNamespace(value=status),  # noqa: E731
                                                           has_cso=False, sample_date=datetime.fromisoformat(d))
    sts = [S("4605", "Ocean Beach at Lincoln Way", "posted", "2026-11-02"), S("4602", "Ocean Beach at Sloat Boulevard", "safe", "2026-11-02"),
           S("4612", "Crissy Field Beach East", "safe", "2026-11-02")]
    pending = {"4605": {"at": "2026-11-10T16:32:00Z", "change": "posted"}}
    b = L.today_board(sts, {}, "", now=datetime(2026, 11, 10, 9, 46), pending=pending)
    z = {t["key"]: t for t in b["zones"]}
    lincoln = next(s for s in z["ocean"]["stations"] if s["full_name"].startswith("Ocean Beach at Lincoln"))
    assert lincoln["sampled_text"] == "posted 8:32 AM · new result on the way" and lincoln["pending"] == pending["4605"]
    assert lincoln["sampled"] == "2026-11-02"                                    # the popover still knows which sample the map shows
    assert z["ocean"]["meta"].endswith(" · new result on the way")
    assert z["north"]["meta"].endswith("sampled Nov 2")                          # other zones untouched
    stale = L.today_board([S("4605", "Ocean Beach at Lincoln Way", "safe", "2026-11-02")], {}, "", now=datetime(2026, 11, 10, 9, 46), pending=pending)
    row = next(s for t in stale["zones"] for s in t["stations"] if s["full_name"].startswith("Ocean Beach at Lincoln"))
    assert row["pending"] is None and row["sampled_text"] == "sampled Nov 2"     # the feed no longer matches the log row
    ystd = L.today_board(sts, {}, "", now=datetime(2026, 11, 11, 7, 0), pending=pending)
    row = next(s for t in ystd["zones"] for s in t["stations"] if s["full_name"].startswith("Ocean Beach at Lincoln"))
    assert row["sampled_text"] == "posted Tue 8:32 AM · new result on the way"


def test_board_template_hands_the_change_to_the_popover():
    html = (ROOT / "app/templates/_today_board.html").read_text()
    assert 'data-sample-changed="{{ s.pending.change }}" data-sample-changed-at="{{ s.pending.at }}"' in html
    assert 'changedAt: s.pending ? s.pending.at : ""' in html
    js = (ROOT / "app/static/sample_popover.js").read_text()
    assert "changedAt: a.dataset.sampleChangedAt" in js and "before the sample behind it reached their map" in js


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
