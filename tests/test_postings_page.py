"""Beach Postings (/postings): the BeachWatch record served as a page, and the
Discharge Ledger's CIWQS refresh stamp."""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.postings import page as P  # noqa: E402
from features.discharges import page as L  # noqa: E402


def test_payload_is_the_whole_record_with_names_zones_and_span():
    status, ctype, body = P.handle_advisories({}, b"")
    assert status == 200 and ctype == "application/json"
    d = json.loads(body)
    rows = list(csv.DictReader(open(ROOT / "features/forecast/data/beachwatch/sf_beach_advisories.csv", newline="")))
    assert len(d["advisories"]) == len(rows) == 2142
    assert d["columns"] == P.COLUMNS and d["first"] == "1999-01-31" and d["known_through"] >= "2026-02-28"
    assert set(d["zones"]) >= {"ocean", "baker_china", "north", "east"} and d["zones"]["east"] == "East Beaches"
    assert set(r[5] for r in d["advisories"]) == {"cso", "rain", "other"}
    assert d["advisories"] == sorted(d["advisories"], key=lambda x: (x[0], x[3]), reverse=True)   # newest first
    st = d["stations"]; assert "OCEAN#21.1_SL" in st and st["OCEAN#21.1_SL"]["name"] == "Ocean Beach at Sloat" and st["OCEAN#21.1_SL"]["zone"] == "ocean"
    assert d["max_duration_days"] == 60 and d["excluded_long"] == 2 and d["fetched_at"]
    assert d["refresh"] == {"refreshed_at": "2026-09-26", "next_due": "2026-12-15", "due_days": 90, "statewide_rows": 35748, "sf_rows": 2142}
    assert 'id="freshNote"' in (ROOT / "app/templates/postings/page.html").read_text()
    assert sum(1 for r in d["advisories"] if r[2] > 60) == 2


def test_page_and_csv_routes():
    from app.wsgi import app
    with app.test_request_context():
        status, ctype, body = P.handle_page({}, b"")
    html = body.decode()
    assert status == 200 and "Beach Postings" in html and "/postings/api/advisories" in html and 'href="/discharges"' in html
    # the headline chart: days with any posting, as a count or a share of the year, with station-days the other option (Chase, 2026-10-01)
    assert 'id="metricToggle"' in html and 'data-metric="days"' in html and 'data-metric="pct"' in html and 'data-metric="stationdays"' in html and "function uniqueDays" in html
    assert [m for m in ("1", "3", "5", "") if f'data-years="{m}">' in html] == ["1", "3", "5", ""] and 'data-years="1">1 year<' in html and 'data-years="5">5 years<' in html and "Last 10 yrs" not in html
    status, ctype, body = P.handle_csv({}, b"")
    assert status == 200 and ctype.startswith("text/csv") and body.startswith(b"advisory_id,")
    assert set(P.GET_ROUTES) == {"/postings", "/postings/api/advisories", "/postings/api/csv"}
    with app.test_client() as c:   # registered in the app, linked from its neighbours
        assert c.get("/postings").status_code == 200 and c.get("/postings/api/advisories").status_code == 200
        assert 'href="/postings"' in c.get("/discharges").data.decode() and 'href="/postings"' in c.get("/analysis").data.decode()
        landing = c.get("/today").data.decode()   # a row in the "Postings & discharges" hub card under the Today board (app/landing.py HUBS)
        assert '<a href="/postings"' in landing and "Beach Postings" in landing


def test_ledger_carries_the_refresh_stamp_and_next_due():
    from shared import freshness
    assert freshness.next_quarterly_due("2026-09-24") == "2026-12-15" and freshness.next_quarterly_due("2026-09-26T18:15:48+00:00") == "2026-12-15"
    assert freshness.next_quarterly_due("2026-12-10") == "2027-03-15" and freshness.next_quarterly_due("2026-12-20") == "2027-03-15"
    assert freshness.next_quarterly_due("2026-01-05") == "2026-03-15" and freshness.next_quarterly_due("2026-03-01") == "2026-06-15" and freshness.next_quarterly_due(None) is None
    r = L._refresh()
    assert r["refreshed_at"] == "2026-09-24" and r["next_due"] == "2026-12-15" and r["due_days"] == 90
    status, ctype, body = L.handle_events({}, b"")
    d = json.loads(body); assert d["refresh"]["refreshed_at"] == "2026-09-24"
    html = (ROOT / "app/templates/discharges/page.html").read_text()
    assert 'id="refreshNote"' in html and "Record last refreshed from CIWQS" in html and "Compiled 2026-08" not in html


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED"); sys.exit(1 if failed else 0)
