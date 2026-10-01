"""The page directory (app/landing.py HUBS): the feature pages grouped into three hubs, each row
with a static fact that a live one replaces when its source answers in time — the shared tabs and
the hub cards under the Today board. The Main page at / that first showed the cards was deleted
2026-09-30; since 2026-10-01 Today IS / (and still answers at /today). Offline: stubbed status client, facts injected or disabled."""
import pathlib
import re, sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import landing as L  # noqa: E402

FEATURE_PAGES = {"/", "/signup", "/alerts", "/forecast", "/samples", "/graphs", "/compare", "/cso-history", "/analysis", "/discharges", "/postings"}


def test_every_feature_page_sits_in_exactly_one_hub_and_every_link_resolves():
    hrefs = [h["primary"][0] for h in L.HUBS if h["primary"]] + [r[0] for h in L.HUBS for r in h["rows"]]
    subs = [r[4][0] for h in L.HUBS for r in h["rows"] if len(r) > 4]
    assert subs == []             # Source Comparison is a row like the others (Chase, 2026-09-29)
    assert sorted(hrefs) == sorted(FEATURE_PAGES) and len(hrefs) == len(set(hrefs)) == 11
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
    assert [r[1] for r in L.HUBS[1]["rows"]] == ["Site Report Card", "Samples", "Graphs", "Source Comparison"]
    # the retired pages redirect to their successors, old deep links included
    with app.test_client() as c:
        assert c.get("/compare?graph=BAY%23320_SL").headers["Location"] == "/graphs?site=BAY%23320_SL"   # old deep links still forward
        assert c.get("/compare?vsite=BAY%23211_SL").headers["Location"] == "/samples?site=BAY%23211_SL"
        assert c.get("/bwtf").headers["Location"] == "/samples?source=bwtf&notes=columns"
        for u in ("/graphs", "/samples"):
            html = c.get(u).data.decode(); assert "/static/charts.js" in html and "/static/sample_popover.js" in html, u
        # every page with a table carries tables.js: 50 rows first, "Show all N rows" for the rest (Chase, 2026-09-29)
        for tpl in ROOT.glob("app/templates/**/*.html"):
            body = tpl.read_text()
            if "<table" in body or "<tbody" in body:
                assert "/static/tables.js" in body, tpl
        tables_js = (ROOT / "app/static/tables.js").read_text()
        assert "Show all " in tables_js and "MutationObserver" in tables_js and "var N = 50" in tables_js and 'classList.contains("notes")' in tables_js
        assert "slice(0, 40)" not in (ROOT / "app/templates/postings/page.html").read_text() and "slice(0, 300)" not in (ROOT / "app/templates/cso_history/page.html").read_text()
        smp = c.get("/samples").data.decode()
        assert "Field notes" in smp and 'data-notes="columns"' in smp
        assert 'id="v-scope"' not in smp and "Dual sites" not in smp and [m for m in ("city", "bwtf", "both", "all") if 'data-source="%s"' % m in smp] == ["city", "bwtf", "both", "all"]   # Source as on Graphs, no Sites toggle
        g = c.get("/graphs").data.decode()
        assert g.count('class="tile"') >= 20 and 'id="analyte"' in g and '<option value="ENTERO" selected>' in g and '<option value="ALL">All indicators</option>' in g and '<option value="PCT">All % threshold</option>' in g and 'href="/compare"' in g
        assert ".preset.active" in (ROOT / "app/static/brand.css").read_text() and "function markPreset" in g            # a chosen date-range preset lights up (kit CSS lives in brand.css)
        assert 'id="kind"' in g and 'data-k="line"' in g and 'data-k="bar"' in g and "CH.barSeries : CH.seriesChart" in g   # the Chart choice: lines or bars
        assert 'querySelectorAll(".preset[data-days]")' in g and 'querySelectorAll(".preset")' not in g   # the Change site pill must not act as a range preset
        kit = (ROOT / "app/static/brand.css").read_text()
        assert 'id="siteBar"' in g and 'id="siteChange"' in g and ".tiles{display:none;" in kit and ".tiles.open{display:flex}" in kit and "@media (max-width:640px)" not in g   # tiles collapse to the chosen site at every width (picker styles live in the kit)
        assert 'id="view"' not in g and "<small>" not in g.split('id="tiles"')[1].split('id="start"')[0]          # no view buttons, plain tiles
        assert [m for m in ("Ocean Beach", "Baker &amp; China Beach", "North Beaches", "East Beaches") if m in g] == ["Ocean Beach", "Baker &amp; China Beach", "North Beaches", "East Beaches"] and "Surfrider only" not in g
        seg = lambda i: g.split('id="' + i + '"')[1].split("</div>")[0].count('class="seg-btn')  # noqa: E731
        assert 'data-s="all"' in g and 'data-s="both"' in g and seg("src") == 4 and seg("kind") == 2      # Source: City/Surfrider/Both/All; Chart: Lines/Bars
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
    assert b["tone"] == "warn" and b["headline"] == "Posted at 1 beach. 3 others meet state standards."
    assert b["headline_html"] == '<em class="posted">Posted at 1 beach.</em> 3 others meet state standards.' and b["date"] == "Tue Sep 29" and b["checked"] == "6:12 AM" and b["feed_ok"]
    assert b["lead"] == ("Posted: Crissy East. SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution. No active sewage discharge. Sewer-overflow risk today: 4% at most (East Beaches), staying low through Sun Oct 04. "
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
    assert cso["tone"] == "danger" and cso["headline"] == "Sewage discharge at 1 beach. Posted at 1 beach. 3 meet state standards."
    assert cso["headline_html"] == '<em class="discharge">Sewage discharge at 1 beach.</em> <em class="posted">Posted at 1 beach.</em> 3 meet state standards.'
    assert cso["lead"] == "Discharging: Crane Cove Park. Posted: Crissy East. SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution."                                   # facts only, no advice
    ok = L.today_board([S("4601", "Fort Funston", "safe"), S("4602", "Ocean Beach at Sloat", "safe")], {}, "")
    assert ok["headline"] == "All 2 monitored beaches meet state standards today." and ok["headline_html"] == 'All 2 monitored beaches <em class="good">meet state standards</em> today.'
    assert ok["lead"] == "No active sewage discharge." and L.today_board([], {}, "")["feed_ok"] is False
    one = L.today_board([S("4612", "Crissy Field East", "posted"), S("4613", "Aquatic Park", "safe")], {}, "")
    assert one["headline"] == "Posted at 1 beach. 1 other meets state standards." and one["lead"].startswith("Posted: Crissy East.")
    assert L.today_board([S("4612", "Crissy Field East", "posted")], {}, "")["headline"] == "Posted at 1 beach."
    from shared.zones import ZONES as Z
    storm = [S(sid, st.name, "posted", cso=True) for sid, st in zip(Z["ocean"].station_ids, Z["ocean"].stations)] + [S("4619", "Islais Creek", "posted", cso=True), S("4620", "Crane Cove Park", "posted", cso=True),
             S("4612", "Crissy Field East", "posted"), S("4613", "Aquatic Park", "posted"), S("4611", "Crissy Field West", "safe")]
    st = L.today_board(storm, {}, "")
    assert st["headline"] == "Sewage discharge at 8 beaches. Posted at 2 beaches. 1 meets state standards."
    assert st["lead"] == "Discharging: all of Ocean Beach, Islais Creek and Crane Cove Park. Posted: Crissy East and Aquatic Park. SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution."   # a whole zone collapses; no advice
    many = L.today_board([S(sid, st.name, "posted") for sid, st in zip(Z["east"].station_ids[:5], Z["east"].stations[:5])], {}, "")
    assert many["lead"].startswith("Posted: Jackrabbit Beach, Windsurfer Circle, Sunnydale Cove and 2 more.")                                       # the list stops at three
    down = L.today_board([], {}, "")
    assert down["tone"] == "warn" and down["headline_html"] == '<em class="warn">Beach status is unavailable right now.</em>'    # orange, like a posting
    nav = L.nav_model()
    assert [h["key"] for h in nav] == ["today", "water", "record"] and [h["href"] for h in nav] == ["/", "/analysis", "/discharges"]   # a hub opens on its first page; Today's is the root
    assert [r["title"] for r in nav[1]["rows"]] == ["Site Report Card", "Samples", "Graphs", "Source Comparison"] and "/compare" in nav[1]["paths"]  # subpage = its own tab
    assert [r["href"] for r in nav[0]["rows"]] == ["/", "/forecast", "/signup", "/alerts"] and "/" in nav[0]["paths"]                                  # the hub opens on Today, at the root; the locked page last
    assert L.canonical_path("/today") == L.canonical_path("/index.html") == "/" and L.canonical_path("/graphs") == "/graphs"
    assert [r["gated"] for r in nav[0]["rows"]] == [False, False, False, True] and L.UNDER_THE_HOOD == [("/architecture", "How it's built"), ("/records", "How we get the records"), ("/forecast#check", "Model check")]
    from app.wsgi import app
    with app.test_client() as c:
        g = c.get("/graphs").data.decode()
        assert 'class="subtabs"' in g and g.count('aria-current="page"') == 1 and '<a href="/samples">Samples</a>' in g and 'href="/graphs" class="on" aria-current="page">Graphs' in g
        assert "The water record</a>" in g and 'class="crumbs"' not in g and "navhub" not in g
        for u in ("/", "/today"):   # Today IS the home page: served at the root, and at /today for the alert emails — never a redirect (2026-10-01)
            r = c.get(u); body = r.data.decode()
            assert r.status_code == 200 and "Location" not in r.headers and 'id="today-map"' in body and 'class="hubs"' in body, u
            assert '<link rel="canonical" href="https://bwtf-sf.vercel.app/">' in body, u                  # search engines index the root
            assert 'href="/" class="on" aria-current="page">Today' in body and 'aria-current="page"' in body.split('class="tabbar"')[0], u   # the Today tab lights at both addresses
        assert c.get("/index.html").status_code == 200 and "Location" not in c.get("/index.html").headers
        assert ">Main</a>" not in g and "Main</a>" not in g and 'class="mark" href="/"' in g          # no Main tab in either bar; the logo goes home
        f = c.get("/forecast").data.decode()
        assert 'class=" gated" title="Coordinators only' in f and f.index('href="/signup"', f.index('class="subtabs"')) < f.index('href="/alerts"', f.index('class="subtabs"'))   # lock icon, rightmost
        a = c.get("/about").data.decode()
        assert "About this site" in a and "Blue Water Task Force" in a and 'class="topbar"' in a and "SFPUC's live beach map" in a and 'class="status-dot discharge"' in a
        assert 'class="topbar"' in c.get("/records").data.decode() and 'class="topbar"' in c.get("/architecture").data.decode() and "back-to-dash\" href" not in c.get("/records").data.decode()


def test_today_page_offline_shows_the_board_then_the_hubs_and_hood():
    """What the Main page carried below its board now sits below Today's (Chase, 2026-09-30)."""
    from app.wsgi import app
    from features.today import page as T

    class Sfpuc:
        def get_status_summary(self): return {"cso_active_count": 0, "posted_count": 1, "safe_count": 15}

    with app.test_request_context("/today"):
        html = T.render_page(L.board_context(Sfpuc(), None, live_facts=False))
    for needle in ("Today &amp; alerts", "The water record", "Postings &amp; discharges", "What the city reported, and when",
                   'class="cta" href="/signup"', "Online Postings Timeline", "Under the hood", 'href="/architecture"', 'href="/about"',  # the ⓘ About link in the frame
                   "1 site(s) posted for elevated bacteria", "is the water safe today?"):
        assert needle in html, needle
    assert html.count('class="hub') >= 3 and 'class="card"' not in html
    assert 'class="topbar"' in html and 'class="tabbar"' in html and 'class="site-footer"' in html and 'class="board tone-warn"' in html   # the shared frame + the Today board
    assert 'id="today"' in html and 'id="water"' in html and 'id="record"' in html                                                       # the hub anchors
    assert html.index('class="board') < html.index('class="hubs"') < html.index('class="hood"') < html.index('class="site-footer"')   # board, then the directory
    cards = html[html.index('class="hubs"'):html.index('class="hood"')]
    assert '<li><a href="/"' not in cards and '<li><a href="/today"' not in cards and '<li><a href="/forecast"' in cards and '<li><a href="/postings"' in cards        # no card row links to the page it sits on
    assert not (ROOT / "app/templates/landing.html").exists() and not hasattr(L, "render_landing")


def test_the_board_waits_for_its_slowest_source_not_all_four_in_a_row():
    """SFPUC's feed, the live facts, rain and tides are fetched at the same time (2026-10-01)."""
    import types
    nap = 0.4
    def slow(value):
        def fn(*a, **kw): time.sleep(nap); return value
        return fn
    sfpuc = types.SimpleNamespace(fetch_stations=slow([]), get_status_summary=lambda: {"safe_count": 3})
    env = types.SimpleNamespace(weather=types.SimpleNamespace(get_rain_advisory=slow(types.SimpleNamespace(is_active=False, upcoming_rain=True))),
                                tides=types.SimpleNamespace(get_tide_info=slow(types.SimpleNamespace(current_trend="rising", next_high=None))))
    saved = L._FACT_SOURCES
    L._FACT_SOURCES = {"/forecast": slow("today 2% risk")}
    try:
        t0 = time.time(); ctx = L.board_context(sfpuc, env); took = time.time() - t0
    finally:
        L._FACT_SOURCES = saved
    assert took < 2.5 * nap, took                                                       # four 0.4 s sources in a row would take 1.6 s
    assert ctx["facts"] == {"/forecast": "today 2% risk"}
    assert [("Rain in the forecast" in c, "Tide" in c) for c in ctx["conditions"]] == [(True, False), (False, True)]   # rain chip first, then tide
    assert "meeting California water-quality standards" in ctx["board"]["headline"]     # the summary fallback still runs when the feed is empty
    broken = types.SimpleNamespace(fetch_stations=lambda: 1 / 0, get_status_summary=lambda: 1 / 0)
    bad_env = types.SimpleNamespace(weather=types.SimpleNamespace(get_rain_advisory=lambda: 1 / 0), tides=types.SimpleNamespace(get_tide_info=lambda: 1 / 0))
    out = L.board_context(broken, bad_env, live_facts=False)                               # every source failing still renders a board
    assert out["conditions"] == [] and out["board"]["feed_ok"] is False


def test_one_frame_width_on_every_page():
    """One page-width variable drives header, sub-tabs, content and footer, and no page
    overrides it — the frame must not jump between pages (Chase, 2026-09-29)."""
    css = (ROOT / "app/static/brand.css").read_text()
    assert "--page-w:1280px" in css and "body.wide" not in css
    for sel in (".wrap{", ".topbar .in{", ".site-footer .in{"):
        rule = css[css.index(sel):]; rule = rule[:rule.index("}")]
        assert "max-width:var(--page-w)" in rule, sel
    for f in sorted((ROOT / "app/templates").rglob("*.html")):
        t = f.read_text()
        assert 'class="wide"' not in t and "max-width:1100px" not in t and "max-width:1040px" not in t, f.name
        if f.parent.name not in ("alerts", "manage", "unsubscribe"):                    # only the form cards keep a narrower column inside the same frame
            assert not re.search(r"\.(wrap|container)\s*\{[^}]*max-width:\s*\d+px", t), f.name

if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
