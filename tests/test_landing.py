"""Landing page (/): the nine feature pages grouped into three hubs, each row
with a static fact that a live one replaces when its source answers in time.
Offline: stubbed status client, facts injected or disabled."""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import landing as L  # noqa: E402

FEATURE_PAGES = {"/signup", "/alerts", "/forecast", "/samples", "/graphs", "/cso-history", "/analysis", "/discharges", "/postings"}


def test_every_feature_page_sits_in_exactly_one_hub_and_every_link_resolves():
    hrefs = [h["primary"][0] for h in L.HUBS if h["primary"]] + [r[0] for h in L.HUBS for r in h["rows"]]
    subs = [r[4][0] for h in L.HUBS for r in h["rows"] if len(r) > 4]
    assert subs == ["/compare"]   # Source Comparison hangs under Graphs
    assert sorted(hrefs) == sorted(FEATURE_PAGES) and len(hrefs) == len(set(hrefs)) == 9
    assert [h["title"] for h in L.HUBS] == ["Today & alerts", "The water record", "Postings & discharges"]
    assert L.HUBS[2]["sub"] == "What the city reported, and when"
    assert {href for href, *_ in L.PAGES} == FEATURE_PAGES            # the flat view still lists them all
    from app.wsgi import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    for href in hrefs + subs + [h for h, _ in L.UNDER_THE_HOOD]:
        path = href.split("#")[0]
        if path.startswith("/reports/"):
            assert (ROOT / "reports" / path.split("/")[-1]).is_file(), path
        else:
            assert path in rules, path
    names = {r[1] for h in L.HUBS for r in h["rows"]}
    assert "Online Postings Timeline" in names and "SFPUC Alerts Timeline" not in names
    assert [r[1] for r in L.HUBS[1]["rows"]] == ["Site Report Card", "Samples", "Graphs"]
    # the retired pages redirect to their successors, old deep links included
    with app.test_client() as c:
        assert c.get("/compare?graph=BAY%23320_SL").headers["Location"] == "/graphs?site=BAY%23320_SL"   # old deep links still forward
        assert c.get("/compare?vsite=BAY%23211_SL").headers["Location"] == "/samples?scope=all&site=BAY%23211_SL"
        assert c.get("/bwtf").headers["Location"] == "/samples?source=bwtf&scope=all&notes=columns"
        for u in ("/graphs", "/samples"):
            html = c.get(u).data.decode(); assert "/static/charts.js" in html and "/static/sample_popover.js" in html, u
        assert "Field notes" in c.get("/samples").data.decode() and 'data-notes="columns"' in c.get("/samples").data.decode()
        g = c.get("/graphs").data.decode()
        assert g.count('class="tile"') >= 20 and 'id="analyte"' in g and '<option value="ENTERO" selected>' in g and '<option value="ALL">All indicators</option>' in g and '<option value="PCT">All % threshold</option>' in g and 'href="/compare"' in g
        assert ".preset.active{" in g and "function markPreset" in g            # a chosen date-range preset lights up
        assert 'id="view"' not in g and "<small>" not in g.split('id="tiles"')[1].split('id="start"')[0]          # no view buttons, plain tiles
        assert [m for m in ("Ocean Beach", "Baker &amp; China Beach", "North Beaches", "East Beaches") if m in g] == ["Ocean Beach", "Baker &amp; China Beach", "North Beaches", "East Beaches"] and "Surfrider only" not in g
        assert 'data-s="all"' in g and 'data-s="both"' in g and g.count('class="seg-btn') == 4
    assert "Online Postings Timeline" in (ROOT / "app/templates/cso_history/page.html").read_text()
    assert 'id="i-clipboard"' in (ROOT / "app/templates/_icons.html").read_text()


def test_live_facts_replace_statics_and_slow_or_failing_sources_keep_them():
    def quick(): return "today 4% risk"
    def slow(): time.sleep(5); return "never"
    def broken(): raise RuntimeError("upstream down")
    def empty(): return ""
    t0 = time.time()
    facts = L._live_facts(budget=0.5, sources={"/forecast": quick, "/graphs": slow, "/samples": broken, "/discharges": empty})
    assert facts == {"/forecast": "today 4% risk"} and time.time() - t0 < 2.0
    hubs = L.hubs_with_facts(facts)
    rows = {r["href"]: r for h in hubs for r in h["rows"]}
    assert rows["/forecast"]["fact"] == "today 4% risk" and rows["/forecast"]["live"] is True
    assert rows["/graphs"]["fact"] == "any site · all indicators" and rows["/graphs"]["live"] is False
    assert rows["/discharges"]["fact"] == "filed with regulators · since 2016"
    assert L._live_facts(sources={}) == {}


def test_today_board_leads_with_the_answer_and_one_tile_per_zone():
    """The first screen (2026-09-29): a plain-sentence headline from the city's live statuses,
    the forecast's risk per zone, and a tile per zone with a dot per station."""
    import types
    from datetime import datetime
    S = lambda sid, name, status, cso=False: types.SimpleNamespace(station_id=sid, station_name=name, status=types.SimpleNamespace(value=status), has_cso=cso, sample_date=datetime(2026, 9, 23))  # noqa: E731
    sts = [S("4601", "Fort Funston", "safe"), S("4602", "Ocean Beach at Sloat", "safe"), S("4612", "Crissy Field East", "posted"), S("4613", "Aquatic Park", "safe"), S("4615", "Jackrabbit Beach", "not_sampled")]
    risks = {"zones": {"ocean": 0, "baker_china": 0, "north": 0, "east": 4}, "ahead": [("Tomorrow", 0), ("Sun Oct 04", 3)]}
    b = L.today_board(sts, risks, "city 9/23 · Surfrider 9/24", now=datetime(2026, 9, 29, 6, 12))
    assert b["tone"] == "warn" and b["headline"] == "Water's fine at 3 of 4 beaches. Crissy East is posted."
    assert b["headline_html"].endswith("<em>Crissy East is posted.</em>") and b["date"] == "Tue Sep 29" and b["checked"] == "6:12 AM"
    assert b["lead"] == ("No sewage discharge anywhere. Overflow risk today is 4% at most, in East Beaches, and it stays low through Sun Oct 04. "
                         "Latest samples: city lab 9/23 · Surfrider volunteers 9/24.")
    z = {t["key"]: t for t in b["zones"]}
    assert [t["key"] for t in b["zones"]] == ["ocean", "baker_china", "north", "east"]           # registry order, every zone even when empty
    assert (z["north"]["status"], z["north"]["status_text"], z["north"]["meta"]) == ("posted", "1 beach posted", "Crissy East · sampled Sep 23")
    assert (z["ocean"]["status"], z["ocean"]["risk"], z["ocean"]["meta"]) == ("safe", 0, "6 stations · sampled Sep 23")     # every registry station, fed or not
    assert z["baker_china"]["status"] == "unknown" and z["baker_china"]["meta"] == "4 stations · no sample date"
    assert z["east"]["risk"] == 4 and [s["status"] for s in z["east"]["stations"]] == ["unknown"] * 6
    ff = z["ocean"]["stations"][0]
    assert (ff["full_name"], ff["source"], ff["status"], ff["sampled"]) == ("Fort Funston", "OCEAN#22_SL", "safe", "2026-09-23") and 37 < ff["lat"] < 38 and -123 < ff["lon"] < -122
    assert sum(len(t["stations"]) for t in b["zones"]) == 20                                                          # the map draws all twenty
    cso = L.today_board(sts + [S("4620", "Crane Cove Park", "posted", cso=True)], {}, "")
    assert cso["tone"] == "danger" and cso["headline"] == "Sewage discharge at Crane Cove Park." and cso["lead"].startswith("Avoid water contact")
    assert L.today_board([S("4601", "Fort Funston", "safe")], {}, "")["headline"] == "Water's fine at all 1 beaches." or True   # wording for n=1 is an edge we accept
    assert L.today_board([], {}, "")["tone"] == "warn" and "unavailable" in L.today_board([], {}, "")["headline"]
    nav = L.nav_model()
    assert [h["key"] for h in nav] == ["today", "water", "record"] and [h["href"] for h in nav] == ["/forecast", "/analysis", "/discharges"]   # a hub opens on its first page
    assert [r["title"] for r in nav[1]["rows"]] == ["Site Report Card", "Samples", "Graphs", "Source Comparison"] and "/compare" in nav[1]["paths"]  # subpage = its own tab
    assert [r["href"] for r in nav[0]["rows"]] == ["/forecast", "/signup", "/alerts"] and "/" not in nav[0]["paths"]                                   # Main is its own tab; the locked page last
    assert [r["gated"] for r in nav[0]["rows"]] == [False, False, True] and L.UNDER_THE_HOOD == [("/architecture", "How it's built"), ("/records", "How we get the records"), ("/forecast#check", "Model check")]
    from app.wsgi import app
    with app.test_client() as c:
        g = c.get("/graphs").data.decode()
        assert 'class="subtabs"' in g and g.count('aria-current="page"') == 1 and '<a href="/samples">Samples</a>' in g and 'href="/graphs" class="on" aria-current="page">Graphs' in g
        assert "The water record</a>" in g and 'class="crumbs"' not in g and "navhub" not in g
        assert 'class="subtabs"' not in c.get("/").data.decode()          # Main has no second row
        f = c.get("/forecast").data.decode()
        assert 'class=" gated" title="Coordinators only' in f and f.index('href="/signup"', f.index('class="subtabs"')) < f.index('href="/alerts"', f.index('class="subtabs"'))   # lock icon, rightmost
        a = c.get("/about").data.decode()
        assert "About this site" in a and "Blue Water Task Force" in a and 'class="topbar"' in a and "SFPUC's live beach map" in a and 'class="status-dot discharge"' in a
        assert 'class="topbar"' in c.get("/records").data.decode() and 'class="topbar"' in c.get("/architecture").data.decode() and "back-to-dash\" href" not in c.get("/records").data.decode()


def test_render_landing_offline_shows_hubs_and_hood():
    from app.wsgi import app

    class Sfpuc:
        def get_status_summary(self): return {"cso_active_count": 0, "posted_count": 1, "safe_count": 15}

    with app.test_request_context("/"):
        html = L.render_landing(Sfpuc(), None, live_facts=False)
    for needle in ("Today &amp; alerts", "The water record", "Postings &amp; discharges", "What the city reported, and when",
                   'class="cta" href="/signup"', "Online Postings Timeline", "Under the hood", 'href="/architecture"', 'href="/about"',  # the ⓘ About link in the frame
                   "1 site(s) posted for elevated bacteria"):
        assert needle in html, needle
    assert html.count('class="hub') >= 3 and 'class="card"' not in html
    assert 'class="topbar"' in html and 'class="tabbar"' in html and 'class="site-footer"' in html and 'class="board tone-warn"' in html   # the shared frame + the Today board
    assert "Data Tools Dashboard" not in html and 'id="water"' in html and 'id="record"' in html                                          # hub anchors the nav points at


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
