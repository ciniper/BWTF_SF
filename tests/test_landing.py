"""The page directory (app/landing.py HUBS): the feature pages grouped into hubs (Learn the fourth, 2026-10-18), each row
with a static fact that a live one replaces when its source answers in time — the hub cards under the Today board —
and the shared top bar (NAV_SECTIONS: Today, Forecast, Data, Learn; design/nav, 2026-11-06). The Main page at / that first showed the cards was deleted
2026-09-30; since 2026-10-01 Today IS / (and still answers at /today). Offline: stubbed status client, facts injected or disabled."""
import pathlib
import re, sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import landing as L  # noqa: E402

FEATURE_PAGES = {"/", "/signup", "/forecast", "/samples", "/graphs", "/compare", "/cso-history", "/analysis", "/discharges", "/postings",
                 "/learn", "/learn/collect"}   # the Lab SOP is an Admin tab since 2026-11-04, not a hub row


def test_every_feature_page_sits_in_exactly_one_hub_and_every_link_resolves():
    hrefs = [h["primary"][0] for h in L.HUBS if h["primary"]] + [r[0] for h in L.HUBS for r in h["rows"]]
    subs = [r[4][0] for h in L.HUBS for r in h["rows"] if len(r) > 4]
    assert subs == []             # Source Comparison is a row like the others (Chase, 2026-09-29)
    assert sorted(hrefs) == sorted(FEATURE_PAGES) and len(hrefs) == len(set(hrefs)) == 12   # Admin is a footer link (2026-11-04)
    assert [h["title"] for h in L.HUBS] == ["Today & alerts", "The water record", "Discharges & postings", "Learn"]   # discharges first (2026-11-04)
    assert [h["key"] for h in L.hubs_with_facts({})] == ["today", "water", "record"]   # Learn has its tab and Today's Learn bar, not a hub card
    assert L.HUBS[2]["sub"] == "What the city reported, and when"
    assert {href for href, *_ in L.PAGES} == FEATURE_PAGES            # the flat view still lists them all
    from app.wsgi import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    for href in hrefs + subs:
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
                assert "asset('tables.js')" in body, tpl
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
        assert 'id="siteBar"' in g and 'id="siteChange"' in g and ".tiles{display:none;" in kit and ".tiles.open{display:flex}" in kit and "@media (max-width:640px)" not in g and ".site-title" in kit   # tiles collapse to the chosen site at every width (picker styles live in the kit)
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
    assert rows["/discharges"]["fact"] == "filed with regulators · since 2011"
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
    # the headline says what the feed knows today — a warning is posted or not; what the samples showed and how old they are is the subhead's (2026-11-06)
    assert b["tone"] == "warn" and b["headline"] == "Posted at 1 beach. 3 others have no warning."
    assert b["headline_html"] == '<em class="posted">Posted at 1 beach.</em> 3 others have no warning.' and b["date"] == "Tue Sep 29" and b["checked"] == "6:12 AM" and b["feed_ok"]
    assert b["lead"] == "Posted: Crissy East. SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution."   # the news only: no "no discharge", no forecast (2026-10-18)
    assert b["facts"] == [{"label": "City samples", "text": "6 days ago (Sep 23)"}, {"label": "Surfrider samples", "text": "5 days ago (Sep 24)"}]   # one labelled row per fact, ages not bare dates (2026-11-06); the city's and Surfrider's only
    # the forecast's risk is the board's collapsed "Sewer-overflow forecast · Beta" line under the tiles (2026-11-08): no facts row for it
    assert b["risk_lead"] == "Sewer-overflow risk today: 4% at most (East Beaches), staying low through Sun Oct 04."
    assert not any(f["label"] == "Overflow risk today" for f in b["facts"])
    z = {t["key"]: t for t in b["zones"]}
    assert [t["key"] for t in b["zones"]] == ["ocean", "baker_china", "north", "east"]           # registry order, every zone even when empty
    assert [t["key"] for t in b["tiles"]] == ["baker_china", "north", "ocean", "east"]           # the tiles' 2×2 grid reads like the map: NW, N shore / W coast, bay (2026-11-08)
    assert [id(t) for t in b["tiles"]] == [id(z[k]) for k in ("baker_china", "north", "ocean", "east")]   # the same tiles, only reordered
    assert (z["north"]["status"], z["north"]["status_text"], z["north"]["meta"]) == ("posted", "1 beach posted", "Crissy East · sampled 6 days ago")   # the age in words, never a bare date (2026-11-06)
    assert (z["ocean"]["status"], z["ocean"]["status_text"], z["ocean"]["risk"], z["ocean"]["meta"]) == ("safe", "No warning", 0, "6 stations · sampled 6 days ago")     # every registry station, fed or not
    assert z["baker_china"]["status"] == "unknown" and z["baker_china"]["status_text"] == "Not sampled" and z["baker_china"]["meta"] == "4 stations · no sample date"
    assert z["east"]["risk"] == 4 and [s["status"] for s in z["east"]["stations"]] == ["unknown"] * 6
    ff = z["ocean"]["stations"][0]
    assert (ff["full_name"], ff["source"], ff["status"], ff["sampled"], ff["sampled_text"], ff["sampled_age_days"]) == ("Fort Funston", "OCEAN#22_SL", "safe", "2026-09-23", "sampled 6 days ago", 6) and 37 < ff["lat"] < 38 and -123 < ff["lon"] < -122
    # the words say what the colours report — the city's posting, not the water (2026-11-08: "safe" and "All clear" retired from the board)
    assert ff["status_text"] == "no warning" and {s["status_text"] for t in b["zones"] for s in t["stations"]} == {"no warning", "warning posted", "not sampled"}
    assert "safe" not in {s["status_text"] for t in b["zones"] for s in t["stations"]} and not any(t["status_text"] == "All clear" for t in b["zones"])
    assert sum(len(t["stations"]) for t in b["zones"]) == 20                                                          # the map draws all twenty
    now = datetime(2026, 9, 29, 6, 12)
    cso = L.today_board(sts + [S("4620", "Crane Cove Park", "posted", cso=True)], {}, "", now=now)
    assert cso["tone"] == "danger" and cso["headline"] == "Sewage discharge at 1 beach. Posted at 1 beach. 3 have no warning."
    assert cso["headline_html"] == '<em class="discharge">Sewage discharge at 1 beach.</em> <em class="posted">Posted at 1 beach.</em> 3 have no warning.'
    assert cso["lead"] == "Discharging: Crane Cove Park. Posted: Crissy East. SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution."   # facts only, no advice
    assert cso["facts"] == [{"label": "City samples", "text": "6 days ago (Sep 23)"}]
    ok = L.today_board([S("4601", "Fort Funston", "safe"), S("4602", "Ocean Beach at Sloat", "safe")], {}, "", now=now)
    assert ok["headline"] == "No warnings posted at any of the 2 monitored beaches." and ok["headline_html"] == '<em class="good">No warnings posted</em> at any of the 2 monitored beaches.'
    assert ok["lead"] == "" and ok["facts"] == [{"label": "City samples", "text": "6 days ago (Sep 23) · all 2 met the state standard · weekly; a warning is posted when a result fails"}]   # the age is the point: "meet standards today" was a week-old sample's claim
    assert ok["risk_lead"] == "" and L.today_board([], {}, "")["feed_ok"] is False and L.today_board([], {}, "", now=now)["facts"] == []
    import types as _t
    split = L.today_board([S("4601", "Fort Funston", "safe"), _t.SimpleNamespace(station_id="4602", station_name="Ocean Beach at Sloat", status=_t.SimpleNamespace(value="safe"), has_cso=False, sample_date=datetime(2026, 9, 16))], {}, "city 9/23 · 1 over", now=now)
    assert split["facts"][0]["text"] == "between 6 days ago and 13 days ago (Sep 16 to Sep 23) · 1 station over the state limit"   # a span when the round was split; an over count outranks "all met"
    one = L.today_board([S("4612", "Crissy Field East", "posted"), S("4613", "Aquatic Park", "safe")], {}, "", now=now)
    assert one["headline"] == "Posted at 1 beach. 1 other has no warning." and one["lead"].startswith("Posted: Crissy East.")
    assert L.today_board([S("4612", "Crissy Field East", "posted")], {}, "", now=now)["headline"] == "Posted at 1 beach."
    solo = L.today_board([S("4601", "Fort Funston", "safe")], {}, "Surfrider 12/28", now=datetime(2027, 1, 3))
    assert solo["headline"] == "No warnings posted at the 1 monitored beach." and solo["facts"][-1] == {"label": "Surfrider samples", "text": "6 days ago (Dec 28)"}   # a December date read in January is last year's
    # Surfrider older than the city's newest sample: the row says the city has sampled since — a fact, no verdict on either (Chase, 2026-11-07)
    older = L.today_board([S("4601", "Fort Funston", "safe")], {}, "city 9/23 · Surfrider 9/14", now=now)
    assert older["facts"][-1] == {"label": "Surfrider samples", "text": "15 days ago (Sep 14) · the city has a newer sample"}
    assert L.today_board(sts, {}, "Surfrider 9/23", now=now)["facts"][-1]["text"] == "6 days ago (Sep 23)"   # the same day is not older
    from shared.zones import ZONES as Z
    storm = [S(sid, st.name, "posted", cso=True) for sid, st in zip(Z["ocean"].station_ids, Z["ocean"].stations)] + [S("4619", "Islais Creek", "posted", cso=True), S("4620", "Crane Cove Park", "posted", cso=True),
             S("4612", "Crissy Field East", "posted"), S("4613", "Aquatic Park", "posted"), S("4611", "Crissy Field West", "safe")]
    st = L.today_board(storm, {}, "", now=now)
    assert st["headline"] == "Sewage discharge at 8 beaches. Posted at 2 beaches. 1 has no warning."
    assert st["lead"] == "Discharging: all of Ocean Beach, Islais Creek and Crane Cove Park. Posted: Crissy East and Aquatic Park. SFPUC posts a beach when samples show bacteria above State standards, and sometimes as a precaution."   # a whole zone collapses; no advice
    many = L.today_board([S(sid, st.name, "posted") for sid, st in zip(Z["east"].station_ids[:5], Z["east"].stations[:5])], {}, "")
    assert many["lead"].startswith("Posted: Jackrabbit Beach, Windsurfer Circle, Sunnydale Cove and 2 more.")                                       # the list stops at three
    down = L.today_board([], {}, "")
    assert down["tone"] == "warn" and down["headline_html"] == '<em class="warn">Beach status is unavailable right now.</em>'    # orange, like a posting
    nav = L.nav_model()
    assert [h["key"] for h in nav] == ["today", "forecast", "data", "learn"] and [h["href"] for h in nav] == ["/", "/forecast", "/data", "/learn"]   # one row, each tab a real page (design/nav, 2026-11-06)
    assert [h["title"] for h in nav] == ["Today", "Forecast", "Data", "Learn"] and [h["switcher"] for h in nav] == [False, False, True, True]   # only a section with several pages gets the switcher
    assert [p["title"] for g in nav[3]["groups"] for p in g["pages"]] == ["How it works", "Collect a sample"]
    assert not any("/alerts" in h["paths"] for h in nav) and L.GATED == {"/alerts"}   # Admin (the Sewage Alert System, renamed) is in no hub: a footer link (2026-11-04)
    assert {L.canonical_path(u) for u in ("/alerts", "/alerts/costs", "/alerts/lab-sop", "/alerts/ui-overflow")} == {"/alerts", "/alerts/costs", "/alerts/lab-sop", "/alerts/ui-overflow"}
    assert [(g["label"], [p["title"] for p in g["pages"]]) for g in nav[2]["groups"]] == [
        ("Lab results", ["Report card", "Samples", "Graphs", "Compare"]), ("City records", ["Discharges", "Postings", "Online timeline"])]
    assert nav[0]["paths"] == ["/"] and nav[1]["paths"] == ["/forecast"] and not any("/signup" in h["paths"] or "/about" in h["paths"] for h in nav)   # signup is the bar's button, About its link
    assert {"/analysis/testing", "/discharges/reporting", "/compare"} <= set(nav[2]["paths"])              # a page's subpage lights Data too
    paged = [p["href"] for h in nav for g in h["groups"] for p in g["pages"]]
    assert sorted(paged + ["/", "/forecast", "/signup"]) == sorted(FEATURE_PAGES)                           # every feature page is in the bar, once
    assert L.canonical_path("/today") == L.canonical_path("/index.html") == "/" and L.canonical_path("/graphs") == "/graphs"
    from app.wsgi import app
    with app.test_client() as c:
        g = c.get("/graphs").data.decode()
        assert 'class="subtabs"' not in g and g.count('aria-current="page"') == 1 and '<a href="/samples">Samples</a>' in g and 'href="/graphs" class="on" aria-current="page">Graphs' in g
        assert g.index('class="topbar"') < g.index("</header>") < g.index('class="secnav"') < g.index('class="page-head"')   # the switcher opens the page head, outside the top bar
        assert '<a href="/data" class="on">Data</a>' in g and '<span class="lbl">Lab results</span>' in g and '<span class="lbl">City records</span>' in g
        assert "The water record</a>" not in g and 'class="crumbs"' not in g and "navhub" not in g
        t = c.get("/analysis/testing").data.decode()
        assert '<a href="/analysis" class="on">Report card</a>' in t and '<a href="/data" class="on">Data</a>' in t   # a subpage lights its parent, without claiming to be it
        d = c.get("/data"); dh = d.data.decode()
        assert d.status_code == 200 and "{{" not in dh and 'class="secnav"' not in dh and '<a href="/data" class="on" aria-current="page">Data</a>' in dh   # the index: its cards are the list
        assert [dh.count('<li><a href="%s">' % u) for u in ("/samples", "/graphs", "/compare", "/postings", "/cso-history")] == [1] * 5   # the rest of each group, listed under its large card
        assert dh.count('class="feature"') == 2 and '<h2 id="feature-water"><a href="/analysis">Site Report Card</a></h2>' in dh and '<h2 id="feature-record"><a href="/discharges">Discharge Ledger</a></h2>' in dh   # the two the index leads with
        assert dh.index('id="water"') < dh.index("Site Report Card") < dh.index("Source Comparison") < dh.index('id="record"') < dh.index("Discharge Ledger") < dh.index("Online Postings Timeline")   # group by group, so a phone reads lab results then city records
        assert 'id="rc-stat"' in dh and 'fetch("/analysis/api/summary?start="' in dh and "Every combined-sewer discharge" in dh   # the Report Card's number arrives after the page has drawn
        assert re.search(r'<p class="stat"><b>[\d,]+ discharges?</b><span>reported in 20\d\d, totalling <strong>[\d.,]+ (million|billion) gallons</strong>\. Reports filed through [A-Z][a-z]{2} 20\d\d\.</span></p>', dh)   # the Ledger's, from the committed record
        beta = dh[dh.index('class="beta"'):dh.index('class="topbar"')]
        assert '<span class="more">' in beta and ".more" not in dh.split("</style>")[0]   # the page must not restyle the Beta notice's "more" span (it did: a white box over the sentence)
        for u in ("/", "/today"):   # Today IS the home page: served at the root, and at /today for the alert emails — never a redirect (2026-10-01)
            r = c.get(u); body = r.data.decode()
            assert r.status_code == 200 and "Location" not in r.headers and 'id="today-map"' in body and 'class="hubs"' not in body, u   # the cards moved to Admin's UI overflow (2026-11-04)
            assert '<link rel="canonical" href="https://bwtf-sf.vercel.app/">' in body, u                  # search engines index the root
            assert 'href="/" class="on" aria-current="page">Today' in body and 'aria-current="page"' in body.split('class="tabbar"')[0], u   # the Today tab lights at both addresses
            assert 'class="secnav"' not in body and body.split('class="tabbar"')[0].count('href="/signup"') == 1, u   # one bar on the home page, and one Get beach alerts in it
        assert c.get("/index.html").status_code == 200 and "Location" not in c.get("/index.html").headers
        assert ">Main</a>" not in g and "Main</a>" not in g and 'class="mark" href="/"' in g          # no Main tab in either bar; the logo goes home
        f = c.get("/forecast").data.decode()
        bar = f[f.index('class="topbar"'):f.index("</header>")]
        assert 'href="/alerts"' not in bar and 'class="secnav"' not in f and 'class="subtabs"' not in f     # no Admin tab (the footer, 2026-11-04); Forecast stands alone, so no switcher
        assert '<a href="/forecast" class="on" aria-current="page">Forecast</a>' in bar
        foot = f[f.index('class="site-footer"'):]
        assert '<a href="/alerts" title="Coordinators only — needs the passphrase">Admin</a>' in foot       # the coordinators' way in
        a = c.get("/about").data.decode()
        assert "About this site" in a and "Blue Water Task Force" in a and 'class="topbar"' in a and "SFPUC's live beach map" in a and 'class="status-dot discharge"' in a
        assert 'class="topbar"' in c.get("/records").data.decode() and "back-to-dash\" href" not in c.get("/records").data.decode()


def test_the_data_index_leads_with_the_report_card_and_the_ledger():
    """/data (features/data_index): one column per group of the Data section, its featured page first."""
    from datetime import date
    from features.data_index import page as DI
    groups = L.data_index()
    assert [(g["key"], g["label"], g["featured"]["href"], [p["href"] for p in g["more"]]) for g in groups] == [
        ("water", "Lab results", "/analysis", ["/samples", "/graphs", "/compare"]),
        ("record", "City records", "/discharges", ["/postings", "/cso-history"])]
    assert all(p["title"] and p["blurb"] for g in groups for p in g["pages"]) and set(L.DATA_FEATURED) == {"/analysis", "/discharges"}
    ev = [["2025-12-26", "Bayside", "CSD-031", "x", "Bay", 30.0, 400.0], ["2026-01-04", "Oceanside", "CSD-001", "x", "Pacific", 60.0, 150.25],
          ["2026-02-11", "Oceanside", "CSD-002", "x", "Pacific", 20.0, None], ["2026-04-22", "Oceanside", "CSD-007", "x", "Pacific", 9.0, 46.95]]
    assert DI.ledger_stat(ev, "2026-08", date(2026, 11, 6)) == {"year": 2026, "count": 3, "volume": "197 million gallons", "through": "Aug 2026"}   # this calendar year, as the Ledger opens; an unreported volume adds nothing
    assert DI.ledger_stat(ev, "2026-11", date(2027, 1, 20)) == {"year": 2026, "count": 3, "volume": "197 million gallons", "through": "Nov 2026"}   # no 2027 report filed yet: the last filed year stands in, named
    assert DI.ledger_stat(ev, "2027-02", date(2027, 3, 1)) == {"year": 2027, "count": 0, "volume": "0.00 million gallons", "through": "Feb 2027"}       # filed and empty is a real zero
    assert DI.ledger_stat(ev, "", date(2026, 11, 6)) is None and DI._volume(1960.8) == "1.96 billion gallons" and DI._volume(0.09) == "0.09 million gallons"
    real = DI.ledger_stat()
    assert real and real["count"] >= 0 and real["through"][-4:].isdigit()                                # the committed record answers


def test_today_page_offline_shows_the_board_and_admin_keeps_the_directory():
    """Today is the board and its Layers strip; what sat under it — the hub cards, the Learn bar and the rain and tide
    pills — moved to Admin's UI overflow tab for the beta (Chase, 2026-11-04)."""
    from app.wsgi import app
    from features.alerts import ui_overflow as UO
    from features.today import page as T

    class Sfpuc:
        def get_status_summary(self): return {"cso_active_count": 0, "posted_count": 1, "safe_count": 15}

    with app.test_request_context("/today"):
        html = T.render_page(L.board_context(Sfpuc(), None, live_facts=False))
    for needle in ('<a href="/forecast">Forecast</a>', '<a href="/data">Data</a>', '<a href="/learn">Learn</a>', 'href="/about"',  'href="/about#built"',  # the tabs and the ⓘ About link in the frame
                   "1 site(s) posted for elevated bacteria", "is the water safe today?", 'class="foot-note">Statuses from SFPUC'):   # the board's sources line stays
        assert needle in html, needle
    assert 'class="topbar"' in html and 'class="tabbar"' in html and 'class="site-footer"' in html and 'class="board tone-warn"' in html   # the shared frame + the Today board
    assert html.index('class="beta"') < html.index('class="topbar"') and "not an official beach advisory" in html   # the Beta notice, a banner above the bar (2026-10-18)
    assert '<span class="fb">Send Beta <a href="mailto:ciniper@gmail.com?subject=BWTF-SF%20BETA%20FEEDBACK">Feedback</a>.</span>' in html.split('class="topbar"')[0]   # the testers' feedback (2026-11-04)
    assert 'class="hubs"' not in html and 'class="learn"' not in html and 'class="conds' not in html and 'class="cta" href="/signup" title=' not in html   # gone from Today (the top bar keeps its own Get beach alerts)
    assert "<b>Under the hood</b>" not in html and 'class="hood"' not in html                           # gone from Today (2026-10-20); its links live on About (the footer's build tooltip can quote a commit message naming it)
    tabbar = html[html.index('class="tabbar"'):]
    assert [w in tabbar for w in ("Today</a>", "Forecast</a>", "Data</a>", "Learn</a>")] == [True] * 4 and "Discharges</a>" not in tabbar   # the phone tab bar: the same four (design/nav)
    ctx = {"hubs": L.hubs_with_facts({}, skip=("/",)), "conditions": ['<svg class="ic"><use href="#i-waves"/></svg> Tide rising'], "generated": "Nov 4"}
    with app.test_request_context("/alerts/ui-overflow"):
        ui = UO.render_page(ctx)
    assert ui.count('class="hub ') + ui.count('class="hub"') >= 3 and 'id="today"' in ui and 'id="water"' in ui and 'id="record"' in ui   # the hub cards and their anchors
    assert ui.index('class="hubs"') < ui.index('class="learn"') < ui.index('class="conds"') < ui.index('class="site-footer"')   # cards, the Learn bar, the pills
    assert 'class="cta" href="/signup" title=' in ui and "Online Postings Timeline" in ui and "Tide rising" in ui and "<h1>UI overflow</h1>" in ui
    cards = ui[ui.index('class="hubs"'):ui.index('class="learn"')]
    assert '<li><a href="/"' not in cards and '<li><a href="/forecast"' in cards and '<li><a href="/postings"' in cards        # as Today drew them: no row for Today itself
    assert 'href="/alerts/ui-overflow" class="on" aria-current="page"' in ui and 'class="secnav"' not in ui and 'class="subtabs"' not in ui      # its own tab lit; no hub in the top bar
    from features.learn import page as LP   # the Learn page's timeline, parked here (2026-11-04)
    assert ui.count('class="stop ') == len(LP.TIMELINE) and 'id="history"' in ui and 'id="tl-more"' in ui and "Used with permission" in ui
    assert ui.count('<figure') == sum(len(t.get("photos", [])) for t in LP.TIMELINE) >= 8   # the master plan's photos, with it
    assert ui.index('class="conds"') < ui.index('id="history"') < ui.index('class="site-footer"') < ui.index('const tl = $("tl")')
    with app.test_client() as c:   # behind the coordinators' passphrase with the rest of Admin
        locked = c.get("/alerts/ui-overflow").data.decode()
        assert 'name="next" value="/alerts/ui-overflow"' in locked and 'class="hubs"' not in locked and "<h1>Admin</h1>" in locked
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
    for sel in (".wrap{", ".beta .in{", ".topbar .in{", ".site-footer .in{"):
        rule = css[css.index(sel):]; rule = rule[:rule.index("}")]
        assert "max-width:var(--page-w)" in rule, sel
    for f in sorted((ROOT / "app/templates").rglob("*.html")):
        t = f.read_text()
        assert 'class="wide"' not in t and "max-width:1100px" not in t and "max-width:1040px" not in t, f.name
        if f.parent.name not in ("alerts", "manage", "unsubscribe"):                    # only the form cards keep a narrower column inside the same frame
            assert not re.search(r"\.(wrap|container)\s*\{[^}]*max-width:\s*\d+px", t), f.name


def test_a_segmented_toggle_wider_than_the_screen_scrolls_inside_its_pill():
    """The shared segmented toggle (brand.css) never clips an option: on a phone the Report Card's five
    standards squeezed onto two lines and the last was cut off, with no way to reach it (2026-11-06)."""
    css = (ROOT / "app/static/brand.css").read_text()
    rule = lambda sel: css[css.index(sel):].split("}")[0]   # noqa: E731
    seg, btn = rule(".seg-toggle{"), rule(".seg-btn{")
    assert "max-width:100%" in seg and "overflow:auto hidden" in seg and "overflow:hidden" not in seg   # as wide as its row at most, and it scrolls sideways
    assert "flex:none" in btn and "white-space:nowrap" in btn                                            # a label keeps its width and its one line
    for f in sorted((ROOT / "app/templates").rglob("*.html")):
        assert not re.search(r"\.seg-toggle\s*\{[^}]*overflow\s*:\s*hidden", f.read_text()), f.name       # no page clips it again


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
