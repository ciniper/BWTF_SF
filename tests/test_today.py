"""The Today page (/today, the home page): the board with its Layers, a Surfrider layer
(features/today/page.py). Offline: stubbed clients."""
import pathlib
import sys
import types
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.today import page as T  # noqa: E402
from shared import standards  # noqa: E402


def _site(name, value, raw, when, lat=37.8, lon=-122.4):
    return types.SimpleNamespace(site_id="9", name=name, latitude=lat, longitude=lon, latest_time=when, entero_raw=raw, entero_value=value,
                                 report_url=f"https://bwtf.surfrider.org/report/76/9")


def test_surfrider_layer_grades_each_site_and_maps_it_to_the_city_beach():
    lab = types.SimpleNamespace(sites=[
        _site("Ocean Beach at Vicente St", 20.0, "20", datetime(2026, 9, 24, 16, 45)),
        _site("Aquatic Park", 41.0, "41", datetime(2026, 9, 24, 18, 30)),
        _site("Crissy Field Beach East", 208.0, "208", datetime(2026, 9, 17, 9, 0)),
        _site("Bayview Hunters Point", None, None, None, lat=37.73, lon=-122.38),
    ])
    d = T.surfrider_layer(lab)
    by = {s["name"]: s for s in d["sites"]}
    assert d["ok"] and d["newest"] == "2026-09-24" and d["limit"] == standards.STANDARDS["ENTERO"]["single_sample_max"] and d["caution"] == standards.ENTERO_CAUTION
    assert [s["name"] for s in d["sites"]] == sorted(by)                                                      # stable order
    v = by["Ocean Beach at Vicente St"]
    assert (v["city_name"], v["key"], v["over"], v["caution"], v["date"], v["time"]) == ("Ocean Beach at Vicente Street", "OCEAN#21_SL", False, False, "2026-09-24", "16:45")
    assert (by["Aquatic Park"]["over"], by["Aquatic Park"]["caution"], by["Aquatic Park"]["key"]) == (False, True, "BAY#211_SL")   # 41: caution band
    assert by["Crissy Field Beach East"]["over"] is True and by["Crissy Field Beach East"]["caution"] is False               # 208: over
    bv = by["Bayview Hunters Point"]
    assert bv["city_name"] is None and bv["key"] == "bwtf:Bayview Hunters Point" and bv["value"] is None and bv["over"] is False and bv["date"] == ""
    assert v["zone"] == "ocean" and by["Aquatic Park"]["zone"] == "north" and bv["zone"] == "east"                        # the shared beach's zone, else the nearest station's
    assert T.zone_for_site("bwtf:nowhere", None, None) is None
    assert (bv["lat"], bv["lon"]) == (37.73, -122.38) and "Experimental" in d["note"]
    assert T.surfrider_layer(None) == {**T.surfrider_layer(types.SimpleNamespace(sites=[])), "sites": []}


def test_today_page_is_the_board_plus_the_experimental_row_and_the_api_route_exists():
    from app.wsgi import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/today" in rules and "/api/today/surfrider" in rules
    orig = T.SFBWTFClient
    T.SFBWTFClient = lambda **kw: types.SimpleNamespace(fetch_lab=lambda: types.SimpleNamespace(sites=[_site("Aquatic Park", 20.0, "20", datetime(2026, 9, 24, 18, 30))]))  # noqa: E731
    try:
        with app.test_client() as c:
            j = c.get("/api/today/surfrider").get_json()
            assert j["ok"] and j["sites"][0]["name"] == "Aquatic Park" and j["sites"][0]["key"] == "BAY#211_SL"
            h = c.get("/today").data.decode()
    finally:
        T.SFBWTFClient = orig
    assert 'id="today-map"' in h and 'id="zones"' in h and 'id="layers"' in h and "Experimental" in h and 'id="tg-bwtf"' in h and 'data-layer="replay"' in h and 'id="replay-ctl" hidden' in h and "/api/today/" in h
    assert h.index('id="layers"') < h.index('class="board') and 'class="extras"' not in h                                            # the strip sits above the board; the old row is gone
    assert h.count('class="site" data-zone=') == 20 and 'id="zone-sites"' in h and 'data-sample-station=' in h and "{{" not in h        # a chip per station under the zone tiles, each opening the popover
    assert ' hidden>' in h.split('id="zone-sites"')[1][:200] and ".site-chips[hidden]{display:none}" in (ROOT / "app/static/brand.css").read_text()   # chips wait for a zone tap — and the attribute must win over the strip's display:flex
    assert 'id="tg-city" checked' in h and 'id="tg-bwtf">' in h and 'data-layer="bwtf"' not in h and h.index('id="tg-bwtf"') < h.index('data-layer="rain"')   # two source switches: city on, Surfrider off (Chase, 2026-09-30)
    assert 'if (!q.has("layers")) want.add("bwtf")' not in (ROOT / "app/templates/_today_board.html").read_text()
    assert 'class="topbar"' in h and 'href="/today" class="on" aria-current="page">Today' in h                                  # the home page, lit in its hub's tabs
    assert 'class="hubs"' in h and 'class="hood"' in h and h.index('id="zone-sites"') < h.index('class="hubs"')                   # the Main page's directory, below the board (Chase, 2026-09-30)
    assert ".hub .cta{" in h and "\n  .cta{" not in h                                                                            # the card's button style must not reach the frame's Get beach alerts
    with app.test_client() as c:
        assert c.get("/").headers["Location"] == "/today"                                                                         # the Main page is gone
    assert "board_section" in (ROOT / "app/templates/today/page.html").read_text()


def test_rain_layer_takes_the_three_most_recent_reported_totals_per_gauge_and_each_zone_reads_its_gauge():
    from datetime import date
    readings = {"SF Downtown": [("2026-09-25", 0.3), ("2026-09-26", 0.0), ("2026-09-27", 0.12), ("2026-09-28", None), ("2026-09-29", 0.4)],   # a missing day is skipped, not zeroed
                "SF Oceanside": [("2026-09-26", 0.0), ("2026-09-27", 0.05), ("2026-09-28", 0.0)]}                                            # nothing for today yet → the three days ending yesterday
    r = T.rain_layer(readings, date(2026, 9, 29))
    dt, oc = r["gauges"]["SF Downtown"], r["gauges"]["SF Oceanside"]
    assert (dt["total_in"], dt["window"], dt["n_days"], dt["sid"]) == (0.52, ["2026-09-26", "2026-09-29"], 3, "047772")
    assert (oc["total_in"], oc["window"], oc["n_days"]) == (0.05, ["2026-09-26", "2026-09-28"], 3)
    assert r["zones"]["ocean"] == {"gauge": "SF Oceanside", "total_in": 0.05} and r["zones"]["east"]["gauge"] == "SF Downtown" and r["zones"]["north"]["total_in"] == 0.52
    assert r["wettest_in"] == 0.52 and r["advisory_in"] == 0.1 and "Experimental" in r["note"]
    assert T._acis_value("M") is None and T._acis_value("T") == 0.0 and T._acis_value("0.25A") == 0.25 and T._acis_value("") is None
    empty = T.rain_layer({}, date(2026, 9, 29))
    assert empty["gauges"]["SF Downtown"]["total_in"] is None and empty["zones"]["north"]["total_in"] is None and empty["wettest_in"] == 0.0


def test_outfalls_layer_sums_the_trailing_year_per_outfall_and_lists_quiet_ones_at_zero():
    payload = {"events": [["2026-04-22", "Bayside", "CSD-001", "Division Street", "Mission Creek", 120.0, 10.5],
                          ["2025-06-01", "Bayside", "CSD-001", "Division Street", "Mission Creek", 60.0, None],       # in the window, volume not reported
                          ["2025-04-22", "Bayside", "CSD-001", "Division Street", "Mission Creek", 60.0, 99.0],       # exactly a year before the newest record: outside
                          ["2026-01-05", "Oceanside", "CSD-007", "Sea Cliff #2", "Pacific", 30.0, 2.25]],
               "locations": {"CSD-001": {"name": "Division Street", "lat": 37.77, "lon": -122.39, "basin": "Central", "water": "Mission Creek", "stations": ["Mission Creek"]},
                             "CSD-007": {"name": "Sea Cliff #2", "lat": 37.79, "lon": -122.49, "basin": "Westside", "water": "Pacific", "stations": ["Baker Beach East", "China Beach"]},
                             "CSD-030": {"name": "Quiet", "lat": 37.7, "lon": -122.4, "basin": "Southeast", "water": "Bay", "stations": ["Islais Creek"]}},
               "coverage": {"note": "Records after Apr 2026 aren't public yet."}, "source": "CIWQS"}
    o = T.outfalls_layer(payload)
    assert o["ok"] and o["window"] == {"from": "2025-04-22", "to": "2026-04-22", "months": 12} and o["max_volume_mg"] == 10.5
    by = {s["id"]: s for s in o["sites"]}
    assert (by["CSD-001"]["n"], by["CSD-001"]["volume_mg"], by["CSD-001"]["unreported"], by["CSD-001"]["last"]) == (2, 10.5, 1, "2026-04-22")
    assert (by["CSD-007"]["n"], by["CSD-007"]["volume_mg"], by["CSD-007"]["stations"]) == (1, 2.25, ["Baker Beach East", "China Beach"])
    assert (by["CSD-030"]["n"], by["CSD-030"]["volume_mg"], by["CSD-030"]["last"]) == (0, 0.0, None)
    assert [s["id"] for s in o["sites"]] == ["CSD-001", "CSD-007", "CSD-030"] and "Apr 2026" in o["coverage"]          # biggest first, quiet last
    assert T.outfalls_layer({"events": [], "locations": {}}) == {"ok": False, "error": "no discharge records"}


def test_replay_layer_maps_each_station_day_to_the_boards_vocabulary_over_the_past_week():
    from datetime import date
    days = T.replay_days(date(2026, 9, 29))
    assert days[0] == "2026-09-22" and days[-1] == "2026-09-29" and len(days) == 8
    rows = [{"station_id": "OCEAN#21_SL", "day": "2026-09-27", "status_last": "posted"},
            {"station_id": "OCEAN#21_SL", "day": "2026-09-29", "status_last": "ok"},
            {"station_id": "BAY#220", "day": "2026-09-29", "status_last": "cso"},
            {"station_id": "BAY#220", "day": "2026-09-01", "status_last": "ok"},                                     # before the window: dropped
            {"station_id": "", "day": "2026-09-29", "status_last": "ok"}]
    r = T.replay_layer(rows, days)
    assert r["stations"] == {"OCEAN#21_SL": {"2026-09-27": "posted", "2026-09-29": "safe"}, "BAY#220": {"2026-09-29": "discharge"}}
    assert r["covered"] == ["2026-09-27", "2026-09-29"] and r["days"] == days and "Experimental" in r["note"]
    from shared.zones import ZONES
    z = next(iter(ZONES.values())); source, reg = z.source_ids[0], z.stations[0]                                        # the feed keys by SFPUC's numeric id …
    assert T.FEED_TO_SOURCE[reg.sfpuc_id] == source and len(T.FEED_TO_SOURCE) == 20
    r2 = T.replay_layer([{"station_id": reg.sfpuc_id, "day": "2026-09-29", "status_last": "posted"}], days)
    assert r2["stations"] == {source: {"2026-09-29": "posted"}}                                                          # … the layer answers in the board's ids


def test_layer_routes_exist_and_answer_json():
    from app.wsgi import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert {"/api/today/rain", "/api/today/outfalls", "/api/today/replay"} <= rules
    with app.test_client() as c:
        j = c.get("/api/today/outfalls").get_json()                       # the committed CSD record: offline
        assert j["ok"] and len(j["sites"]) == 34 and j["sites"][0]["volume_mg"] >= j["sites"][-1]["volume_mg"]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
