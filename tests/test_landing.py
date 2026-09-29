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
        assert ".preset.active{" in g and "function markPreset" in g            # a chosen date-range preset lights up
        assert 'id="kind"' in g and 'data-k="line"' in g and 'data-k="bar"' in g and "CH.barSeries : CH.seriesChart" in g   # the Chart choice: lines or bars
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


def test_render_landing_offline_shows_hubs_and_hood():
    from app.wsgi import app

    class Sfpuc:
        def get_status_summary(self): return {"cso_active_count": 0, "posted_count": 1, "safe_count": 15}

    with app.test_request_context("/"):
        html = L.render_landing(Sfpuc(), None, live_facts=False)
    for needle in ("Today &amp; alerts", "The water record", "Postings &amp; discharges", "What the city reported, and when",
                   'class="cta" href="/signup"', "Online Postings Timeline", "Under the hood", "/reports/2026-09_model_analysis.html",
                   "1 site(s) posted for elevated bacteria"):
        assert needle in html, needle
    assert html.count('class="hub') >= 3 and 'class="card"' not in html


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
