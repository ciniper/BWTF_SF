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
    # the age of each result, in days and in words, and stale past STALE_AFTER_DAYS (14 to start, 2026-11-06): drawn hollow and grey
    assert d["newest_text"] == "8 days ago" and d["stale_after_days"] == T.STALE_AFTER_DAYS == 14
    assert (by["Aquatic Park"]["age_days"], by["Aquatic Park"]["age_text"], by["Aquatic Park"]["stale"]) == (8, "8 days ago", False)
    assert (by["Crissy Field Beach East"]["age_days"], by["Crissy Field Beach East"]["age_text"], by["Crissy Field Beach East"]["stale"]) == (15, "15 days ago", True)
    assert (by["Bayview Hunters Point"]["age_days"], by["Bayview Hunters Point"]["age_text"], by["Bayview Hunters Point"]["stale"]) == (None, "", False)
    edge = T.surfrider_layer(types.SimpleNamespace(sites=[_site("Aquatic Park", 20.0, "20", datetime(2026, 9, 24, 18, 30))]), today=date(2026, 10, 8))["sites"][0]
    assert (edge["age_days"], edge["stale"]) == (14, False)                                                   # exactly the threshold is still fresh
    board = (ROOT / "app/templates/_today_board.html").read_text()
    assert ".bwtf-dot.stale{background:#fff;border:1.5px dashed var(--faint)" in board and '(s.stale ? " stale" : "")' in board and 'dia("stale", "over " + d.stale_after_days + " days old")' in board
    assert '" · " + s.age_text + " (" + s.date + ")"' in board and 'newest sampled " + esc(newest.age_text)' in board and '" · " + s.sampled_text + " ("' in board   # ages in words on the map, the tiles and the city dots
    # the tile says when the city has sampled the zone since Surfrider did — beside the age, deciding nothing (Chase, 2026-11-07)
    assert 'newest.age_days > cityNewest.sampled_age_days ? " · the city\'s newest is " + esc(cityNewest.sampled_text.replace(/^sampled /, ""))' in board
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
    assert 'id="today-map"' in h and 'id="zones"' in h and 'id="layers"' in h and 'data-layer="replay"' in h and 'id="replay-ctl" hidden' in h and "/api/today/" in h
    assert h.index('id="layers"') < h.index('class="board') and 'class="extras"' not in h                                            # the strip sits above the board; the old row is gone
    # The strip above the board holds Replay alone; what the map shows is chosen on the map, in one "Map shows" bar of chips
    # that each change the map and nothing else (design/today-map-controls, 2026-11-06: the Sources and Layers boxes and the
    # phone fold went; Risk view went too, the tiles always show today's risk)
    strip = h.split('id="layers"')[1].split('class="board')[0]
    assert '<span class="ctl-label" id="lbl-replay">Past days</span>' in strip and strip.count("<button") == 1 and "Experimental" not in strip and "Sources" not in strip
    assert 'id="lbl-sources"' not in h and 'id="layers-toggle"' not in h and 'id="layer-group"' not in h and 'id="tg-city"' not in h and 'id="tg-bwtf"' not in h and 'data-layer="risk"' not in h
    bar = h.split('class="map-ctl"')[1].split('class="map-stack"')[0]
    assert '<span class="ctl-label" id="lbl-map">Map shows</span>' in bar and [b.split('"')[0] for b in bar.split('data-layer="')[1:]] == ["city", "bwtf", "rain", "outfalls", "labels"]
    assert 'class="preset layer active" data-layer="city" aria-pressed="true"' in bar and bar.count('aria-pressed="false"') == 4 and ">Names</button>" in bar and ">Labels<" not in h
    assert h.index('class="map-col"') < h.index('class="map-ctl"') < h.index('class="map-stack"') < h.index('id="today-map"') < h.index('id="map-legend"') < h.index('id="layer-status"')   # bar, map, legend, the layers' line
    board = (ROOT / "app/templates/_today_board.html").read_text()
    assert "RISK_VIEW" not in board and "show-risk" not in board and "openLayers" not in board and ".switch" not in board   # no risk toggle, no fold, no switches
    assert '.map-stack{position:relative' in board and ".map-col{display:flex" in board and "position:relative" not in board.split(".map-col{")[1].split("}")[0]   # the legend floats over the map, not over the bar
    assert 'if (key === "city") { setCity(!CITY_ON, btn); return; }' in board and 'btn.setAttribute("aria-pressed", on ? "true" : "false")' in board
    assert 'el.className = "bwtfline"' in board and '.board.replaying .zone .bwtfline' in board and 'querySelectorAll(".zone .bwtfline").forEach(e => e.remove())' in board   # Surfrider's line on each tile, gone with the layer and on a replayed day
    # the forecast's risk left the tiles for its own Beta panel under them (2026-11-09); the rain and Surfrider lines now follow the zone's name
    assert '.zone .risk' not in board and 'class="risk">' not in board and "overflow risk today{% endif %}" not in board
    assert '{% if board.risk_text %}' in board and '<section class="risk-panel" aria-labelledby="risk-h">' in board and '<span class="beta-tag">Beta</span>' in board and ".board.replaying .risk-panel{display:none}" in board
    assert board.index('class="zones-hint"') < board.index('class="risk-panel"') < board.index('class="map-col"')   # under the tiles, before the map column
    assert 'el.className = "rainline"; t.b.querySelector(".nm").after(el); }' in board and '(t.b.querySelector(".rainline") || t.b.querySelector(".nm")).after(el); }' in board
    # the chosen zone: a brand ring outside its grade border, the others dimmed
    assert ".zone.focus{box-shadow:0 0 0 2px #fff,0 0 0 5px var(--brand)}" in board and ".zones.has-focus .zone:not(.focus){opacity:.5}" in board and 'classList.toggle("has-focus", !!ACTIVE)' in board
    # the legend: over the map on wider screens, under it on phones (it covered the north shore)
    col = h.split('class="map-col"')[1].split('id="zone-sites"')[0]
    assert col.index('class="map-wrap"') < col.index('id="today-map"') < col.index('id="map-legend"') and col.split('id="map-legend"')[0].count("</div>") >= 2   # legend outside the map frame
    assert ".map-legend{position:static;" in board
    # The words say what each colour reports (2026-11-08). The city's: its posting, never "safe". With Surfrider on, each source
    # keys its own colours under its name; one note under the map says what the colours are not; the key under the board spells
    # out all three systems with the numbers the layer and the grades actually use.
    legend = h.split('id="map-legend"')[1].split("</div>")[0]
    assert [t.split("</span>")[0] for t in legend.split("</i>")[1:]] == ["no warning", "warning posted", "sewage discharge", "not sampled"] and "safe<" not in legend and "Surfrider latest" not in h
    assert '<p class="legend-note" id="legend-note">Colours are the city\'s posted status at the last check, not a measurement of today\'s water.</p>' in h
    assert "coloured by the city's posted status" in h and "coloured by today's status" not in h
    assert 'dot("safe", REPLAY_OFFSET ? "not posted" : "no warning") + dot("posted", "warning posted") + dot("discharge", "sewage discharge")' in board
    assert '<span class="src">Surfrider</span>\' + dia("safe", "below limit") + dia("caution", "from " + (d ? d.caution : "") + " MPN") + dia("posted", "over limit") + dia("unknown", "no result")' in board
    assert 'if (CITY_ON) h += (bw ? \'<span class="src">City</span>\' : "")' in board and "Diamonds grade one volunteer Enterococcus sample per site, taken about every two weeks; not an advisory." in board
    key = h.split('<details class="colour-key">')[1].split("</details>")[0]
    assert "<summary>What the colours mean</summary>" in key and key.count("<dt>") == 3 and h.index('<details class="colour-key">') > h.index('id="legend-note"') and h.index('<details class="colour-key">') < h.index('id="zone-sites"')
    assert "Tile borders and city dots" in key and "<b>Green</b> means no warning sign is up — not that the water was tested clean today" in key
    assert "Surfrider diamonds" in key and "limit of 104 MPN/100 mL" in key and "<b>yellow</b> from 36" in key and "over 14 days old is drawn hollow" in key and "not a health advisory" in key and "Where the two sources differ, both are shown." in key
    assert "The overflow-risk percentages" in key and "In the Beta panel under the tiles" in key and 'href="/forecast"' in key and "A prediction, driven by rain, not a measurement of the water." in key
    assert ".board.replaying .colour-key{display:none}" in board and "All clear" not in h and '"No warning" if status == "safe"' in (ROOT / "app/landing.py").read_text()   # the tiles read "No warning", not "All clear"
    assert h.count('class="site" data-zone=') == 20 and 'id="zone-sites"' in h and 'data-sample-station=' in h and "{{" not in h        # a chip per station under the zone tiles, each opening the popover
    assert ' hidden>' in h.split('id="zone-sites"')[1][:200] and ".site-chips[hidden]{display:none}" in (ROOT / "app/static/brand.css").read_text()   # chips wait for a zone tap — and the attribute must win over the strip's display:flex
    assert 'data-layer="bwtf" aria-pressed="false"' in h and h.index('data-layer="bwtf"') < h.index('data-layer="rain"')   # the two sources lead the bar: city on, Surfrider off (Chase, 2026-09-30)
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
    with a zone chosen, an overlapping name tried on the dot's other side and then hidden; posted labels win."""
    from app.wsgi import app
    with app.test_client() as c:
        h = c.get("/").data.decode()
    chip = h.split('data-layer="labels"')[0][-80:] + h.split('data-layer="labels"')[1][:200]
    assert 'aria-pressed="false"' in chip and ">Names</button>" in chip and "active" not in chip.split("<button")[-1].split(">")[0]   # "Names" since 2026-11-06 (it was "Labels": nobody could tell what it did)
    bar = h.split('class="map-ctl"')[1].split('class="map-stack"')[0]
    assert bar.index('data-layer="outfalls"') < bar.index('data-layer="labels"') and 'data-layer="risk"' not in h              # last in the Map shows bar; no Risk view chip
    board = (ROOT / "app/templates/_today_board.html").read_text()
    assert "labels: {}" in board and 'if (key === "labels")' in board and "LABEL_ZOOM = 13" in board        # no fetch; ?layers=labels deep-links like the rest
    assert 'MAP.createPane("lbl").style.zIndex = 445' in board and 'MAP.on("zoomend moveend", refreshLabels)' in board
    assert board.count("refreshLabels()") >= 4 and 'classList.add("lft")' in board and 'classList.add("off")' in board


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
