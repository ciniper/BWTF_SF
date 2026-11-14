"""The Today page (the home page, at / and /today): the board with its Layers, a Surfrider layer
(features/today/page.py). Offline: stubbed clients."""
import pathlib
import sys
import types
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.today import page as T  # noqa: E402
from shared import standards  # noqa: E402


def _site(name, value, raw, when, lat=37.8, lon=-122.4):
    return types.SimpleNamespace(site_id="9", name=name, latitude=lat, longitude=lon, latest_time=when, entero_raw=raw, entero_value=value,
                                 report_url=f"https://bwtf.surfrider.org/report/76/9")


def test_days_ago_text_reads_as_a_person_would_say_it():
    from shared.clock import days_ago_text
    t = date(2026, 11, 6)
    assert [days_ago_text(d, t) for d in (date(2026, 11, 6), date(2026, 11, 5), date(2026, 11, 2), date(2026, 10, 22), date(2026, 5, 28))] == ["today", "yesterday", "4 days ago", "15 days ago", "162 days ago"]
    assert days_ago_text(date(2026, 11, 7), t) == "today"   # a clock ahead of the beach never says "-1 days ago"


def test_sample_facts_start_with_sentence_case_without_changing_embedded_ages():
    from app.wsgi import app
    from app.landing import today_board

    def station(sid, day):
        return types.SimpleNamespace(station_id=sid, status="safe", has_cso=False, sample_date=datetime(2026, 11, day))

    with app.test_request_context("/"):
        for day, age in ((9, "Yesterday"), (10, "Today")):
            board = today_board([station("4601", day)], samples_fact=f"Surfrider 11/{day}", now=datetime(2026, 11, 10))
            h = T.render_page({"board": board, "generated": "Preview"})
            assert f"<dd>Samples were collected {age.lower()} (Nov {day})." in h
            assert f"sampled {age.lower()}" in h
        board = today_board([station("4601", 9), station("4602", 10)], now=datetime(2026, 11, 10))
        h = T.render_page({"board": board, "generated": "Preview"})
        assert "<dd>Samples were collected between yesterday and today (Nov 9–10)." in h

        first, last = station("4601", 9), station("4602", 10)
        first.sample_date = datetime(2026, 12, 31)
        last.sample_date = datetime(2027, 1, 1)
        now = datetime(2027, 1, 3)
        board = today_board([first, last], now=now)
        assert board["facts"][0]["text"] == "Samples were collected 2–3 days ago (Dec 31, 2026–Jan 1, 2027)."
        first.sample_date = datetime(2027, 1, 4)
        last.sample_date = None
        assert today_board([first, last], now=now)["facts"] == []


def test_surfrider_layer_grades_each_site_and_maps_it_to_the_city_beach():
    lab = types.SimpleNamespace(sites=[
        _site("Ocean Beach at Vicente St", 20.0, "20", datetime(2026, 9, 24, 16, 45)),
        _site("Aquatic Park", 41.0, "41", datetime(2026, 9, 24, 18, 30)),
        _site("Crissy Field Beach East", 208.0, "208", datetime(2026, 9, 17, 9, 0)),
        _site("Bayview Hunters Point", None, None, None, lat=37.73, lon=-122.38),
    ])
    d = T.surfrider_layer(lab, today=date(2026, 10, 2))
    by = {s["name"]: s for s in d["sites"]}
    assert d["ok"] and d["newest"] == "2026-09-24" and d["limit"] == standards.STANDARDS["ENTERO"]["single_sample_max"] and d["caution"] == standards.ENTERO_CAUTION
    # Results become stale after seven days, independent of the bacteria grade.
    assert d["newest_text"] == "8 days ago" and d["stale_after_days"] == T.STALE_AFTER_DAYS == 7
    assert (by["Aquatic Park"]["age_days"], by["Aquatic Park"]["age_text"], by["Aquatic Park"]["stale"]) == (8, "8 days ago", True)
    assert (by["Crissy Field Beach East"]["age_days"], by["Crissy Field Beach East"]["age_text"], by["Crissy Field Beach East"]["stale"]) == (15, "15 days ago", True)
    assert (by["Bayview Hunters Point"]["age_days"], by["Bayview Hunters Point"]["age_text"], by["Bayview Hunters Point"]["stale"]) == (None, "", False)
    edge = T.surfrider_layer(types.SimpleNamespace(sites=[_site("Aquatic Park", 20.0, "20", datetime(2026, 9, 24, 18, 30))]), today=date(2026, 10, 1))["sites"][0]
    assert (edge["age_days"], edge["stale"]) == (7, False)   # exactly the threshold is still fresh
    board = (ROOT / "app/templates/_today_board.html").read_text()
    assert ".bwtf-dot.stale{background:#fff;border:1.5px dashed var(--faint)" in board and '(s.stale ? " stale" : "")' in board and 'item("bwtf-dot", "stale", "Over " + data.stale_after_days + " days old")' in board
    assert '" · " + s.age_text + " (" + s.date + ")"' in board and '" · " + s.sampled_text + " ("' in board   # ages remain on the markers
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
    assert 'id="today-map"' in h and 'id="zones"' in h and 'id="layers"' in h and 'id="tg-bwtf"' in h and 'data-layer="replay"' in h and 'id="day-strip"' in h and 'type="range"' not in h and "/api/today/" in h
    assert h.index('class="board') < h.index('id="zones"') < h.index('id="layers"') < h.index('id="today-map"') and 'class="extras"' not in h                                            # map sources sit after zones and immediately before the map
    # Sources and map extras stay separate; forecast and time controls belong to the board.
    assert '<span class="ctl-label" id="lbl-sources">Map shows:</span>' in h and '>Show<' not in h and 'class="layer-note"' not in h
    assert h.index('id="tg-bwtf"') < h.index('id="layers-toggle"') < h.index('id="layer-group"') < h.index('data-layer="rain"') < h.index('id="layer-status"')
    assert 'aria-controls="layer-group"' in h and 'aria-expanded="false"' in h.split('id="layers-toggle"')[0][-80:] + h.split('id="layers-toggle"')[1][:120]
    board = (ROOT / "app/templates/_today_board.html").read_text()
    assert ".layers .layer-group{display:none}.layers .layer-group.open{display:flex}" in board and ".layers-toggle{display:inline-flex}" in board and "openLayers(true)" in board and 'querySelectorAll(".layer.active").length' in board
    # the chosen zone: a brand ring outside its grade border, the others dimmed
    assert ".zone.focus{box-shadow:0 0 0 2px #fff,0 0 0 5px var(--brand)}" in board and ".zones.has-focus .zone:not(.focus){opacity:.5}" in board and 'classList.toggle("has-focus", !!ACTIVE)' in board
    # The compact legend sits beneath the map and the disclosure explains both sources.
    assert h.index('id="today-map"') < h.index('id="map-legend"') < h.index('<details class="colour-key">')
    assert "bwtfline" not in board
    legend = h.split('id="map-legend"')[1].split("</div>")[0]
    assert [t.split("</span>")[0] for t in legend.split("</i>")[1:]] == ["No warning", "Warning posted", "Sewage discharge", "No data"]
    key = h.split('<details class="colour-key">')[1].split("</details>")[0]
    assert "<summary>What the colors mean</summary>" in key and key.count("<dt>") == 2
    assert "See SFPUC’s official map and legend" in key and "recent combined sewer discharge" in key
    assert "Below 36" in key and "36 through 104" in key and "Above 104" in key
    assert "Over 7 days old (hollow)" in key and "No result (solid gray)" in key
    assert ".board.replaying .colour-key{display:none}" in board and "All clear" not in h and '"No warning" if status == "safe"' in (ROOT / "app/landing.py").read_text()   # the tiles read "No warning", not "All clear"
    assert h.count('class="site" data-zone=') == 20 and 'id="zone-sites"' in h and 'data-sample-station=' in h and "{{" not in h        # a chip per station under the zone tiles, each opening the popover
    assert ' hidden>' in h.split('id="zone-sites"')[1][:200] and ".site-chips[hidden]{display:none}" in (ROOT / "app/static/brand.css").read_text()   # chips wait for a zone tap — and the attribute must win over the strip's display:flex
    assert 'id="tg-city" checked' in h and 'id="tg-bwtf">' in h and 'data-layer="bwtf"' not in h and h.index('id="tg-bwtf"') < h.index('data-layer="rain"')   # two source switches: city on, Surfrider off (Chase, 2026-09-30)
    assert 'if (!q.has("layers")) want.add("bwtf")' not in (ROOT / "app/templates/_today_board.html").read_text()
    assert 'class="topbar"' in h and 'href="/" class="on" aria-current="page">Today' in h                                       # /today is the home page: its tab, named /, lights
    assert 'class="hubs"' not in h and 'class="hood"' not in h and 'class="learn"' not in h and 'class="conds' not in h          # the directory, the Learn bar and the pills moved to Admin's UI overflow (2026-11-04); no Under the hood since 2026-10-20
    assert "\n  .cta{" not in h                                                                                                  # no page style may reach the frame's Get beach alerts
    with app.test_client() as c:
        root = c.get("/")
        assert root.status_code == 200 and "Location" not in root.headers and 'id="layers"' in root.data.decode()             # the root serves Today itself, no redirect (2026-10-01)
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
    assert o["ok"] and o["window"] == {"from": "2025-04-22", "to": "2026-04-22", "months": 12} and "max_volume_mg" not in o
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


def test_labels_layer_is_a_chip_off_by_default_drawn_from_the_stations():
    """Chase, 2026-10-21: a Labels layer, default off — zone names on the whole-city map, station names zoomed in or
    with a zone chosen; posted labels win. Since 2026-11-08 ("a bit messy") one placer draws every label, posted ones
    included: each takes the first spot round its dot clear of other labels, the dots and the map's controls, or none."""
    from app.wsgi import app
    with app.test_client() as c:
        h = c.get("/").data.decode()
    chip = h.split('data-layer="labels"')[0][-80:] + h.split('data-layer="labels"')[1][:200]
    assert 'aria-pressed="false"' in chip and ">Names</button>" in chip and "active" not in chip.split("<button")[-1].split(">")[0]
    group = h.split('id="layer-group"')[1].split('id="layer-status"')[0]
    assert group.index('data-layer="outfalls"') < group.index('data-layer="labels"')
    assert 'data-layer="risk"' not in group and 'data-layer="replay"' not in group
    assert h.index('id="today-h"') < h.index('id="zones"') < h.index('data-layer="replay"') < h.index('data-layer="risk"') < h.index('id="day-strip"')   # Past week sits under the tiles, beside the forecast
    assert h.split('id="day-strip"')[1].split(">")[0].rstrip().endswith("hidden")   # closed until Past week is pressed
    assert h.index('id="zones"') < h.index('data-layer="risk"') < h.index('id="layers"')
    board = (ROOT / "app/templates/_today_board.html").read_text()
    assert "labels: {}" in board and 'if (key === "labels")' in board and "LABEL_ZOOM = 13" in board        # no fetch; ?layers=labels deep-links like the rest
    assert 'MAP.createPane("lbl").style.zIndex = 445' in board and 'MAP.on("zoomend moveend resize", refreshLabels)' in board
    assert board.count("refreshLabels()") >= 4 and "function besideDot(" in board and "function besideZone(" in board
    assert "permanent: bad" not in board and "function mapControls()" in board                                # posted labels are placed too, not left to Leaflet
    assert 'MAP.on("tooltipopen", fitTooltip)' in board
    assert "LAYER.outfalls.group.eachLayer(m => { const p = at(m)" in board                                   # labels keep off the outfalls too
    assert "function openSample(s)" in board and 'm.on("click", () => openSample(s))' in board                # a dot and its label open the same sample
    assert "at the wetter gauge over three days" not in board and "at both gauges over three days" not in board   # nor the rain layer
    assert "if (missed.length && !tried.has(orderKey(next)))" in board   # a zone left without a label picks its spot first on the next try
    assert 'esc(short(z)) + "<br>" + count' in board   # and a zone label short of room goes on two rows before it drops its name
    assert "outfalls discharged in the year" not in board   # the outfalls layer adds no count line under the map
    assert "new ResizeObserver(" in board and "MAP.invalidateSize()" in board   # the map re-measures when its box changes size
    assert "tapZone(z)" in board and "tapCluster(c)" in board and ".map-name>span.tap{pointer-events:auto" in board   # labels take taps                                                     # a hover tooltip opens on the side that fits


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
