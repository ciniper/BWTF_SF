"""One date range on every page (Chase, 2026-09-30): the presets read 3 mo → 12 mo → 3 yrs → Full record,
smallest on the left, nothing opens on the full record, and a preset always fills both date boxes.
Postings counts by year, so its presets are years in the same order. Graphs and Samples share one
site picker (_site_picker.html + site_picker.js) and link to each other over the same site and range."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

T = lambda rel: (ROOT / rel).read_text()  # noqa: E731
DAY_PRESETS = [("91", "Last 3 mo"), ("365", "Last 12 mo"), ("1096", "Last 3 yrs"), ("", "Full record")]
YEAR_PRESETS = [("365", "1 year"), ("1096", "3 years"), ("1826", "5 years"), ("", "Full record")]   # pages whose record spans decades (Chase, 2026-10-01)
RANGE_PAGES = ("graphs", "samples", "comparison", "discharges")
YEAR_PAGES = ("site_analysis",)


def presets(html, attr="data-days"):
    return re.findall(r'<button class="[^"]*preset[^"]*" ' + attr + r'="([^"]*)">([^<]+)</button>', html)


def test_every_range_page_offers_the_same_four_presets_smallest_first():
    for page in RANGE_PAGES:
        html = T(f"app/templates/{page}/page.html")
        assert presets(html) == DAY_PRESETS, page
        assert 'preset active" data-days=""' not in html and 'preset active" data-days' not in html, page   # nothing is lit before the dates are known
    for page in YEAR_PAGES:   # the Site Report Card grades 2000 → today: 1 year / 3 / 5 / Full record, smallest first
        html = T(f"app/templates/{page}/page.html")
        assert presets(html) == YEAR_PRESETS, page
        assert 'preset active" data-days' not in html, page
    assert presets(T("app/templates/postings/page.html"), "data-years") == [("1", "1 year"), ("3", "3 years"), ("5", "5 years"), ("", "Full record")]


def test_nothing_opens_on_the_full_record():
    from features.comparison import comparison as C
    assert C.DEFAULT_DAYS == 91                                                                    # Graphs, Samples: the API's default window
    assert "var HIST = { start: isoDaysAgo(91)" in T("app/templates/comparison/page.html")          # the Compare page's history modal
    assert "applyPreset(365);" in T("app/templates/discharges/page.html")                          # the ledger: twelve months (three are dry-season empty)
    assert "start: new Date(Date.now() - 365 * 864e5)" in T("app/templates/site_analysis/page.html")   # its smallest preset is a year
    assert "setYears(D.lastYear - 2, D.lastYear);" in T("app/templates/postings/page.html")


def test_a_preset_fills_both_date_boxes():
    d = T("app/templates/discharges/page.html")
    assert "STATE.end = ed.value = newest;" in d and 'STATE.start = sd.value = days ? ' in d and 'sd.value = ""' not in d and 'ed.value = ""' not in d
    a = T("app/templates/site_analysis/page.html")
    assert "STATE.end = ed.value = DATA ? DATA.newest_sample" in a and 'sd.value = ""; ed.value = ""' not in a
    p = T("app/templates/postings/page.html")
    assert "setYears(n ? D.lastYear - n + 1 : D.firstYear, D.lastYear)" in p and "setYears(null, null)" not in p
    for page in ("graphs", "samples"):   # a preset writes both inputs, the API's echo fills them on first load
        html = T(f"app/templates/{page}/page.html")
        assert html.count('.value = ') >= 2 and "function markPreset" in html, page


def test_graphs_and_samples_share_the_site_picker_and_link_to_each_other():
    macro = T("app/templates/_site_picker.html")
    assert 'id="siteNow"' in macro and 'id="siteName"' in macro and 'id="siteZone"' in macro and 'id="siteChange"' in macro and "all_label" in macro and 'class="preset cross"' in macro
    js = T("app/static/site_picker.js")
    assert "window.SitePicker" in js and "function filter(fits)" in js and "onPick" in js and "var pick = all || first || dual || shown[0];" in js
    kit = T("app/static/brand.css")
    assert ".site-now{" in kit and ".site-now b{font-size:16px;font-weight:900" in kit and ".tile.active{" in kit and "a.preset.cross" in kit
    g, s = T("app/templates/graphs/page.html"), T("app/templates/samples/page.html")
    assert 'site_picker(site_groups, cross=("Samples →", "toSamples", "/samples"), first_site=first_site)' in g and '$("toSamples").href = "/samples?" + q' in g
    assert 'site_picker(site_groups, all_label="All sites", cross=("Graph this site →", "v-graph", "/graphs"))' in s and '$("v-graph").href = graphHref()' in s
    for page in (g, s):
        assert "/static/site_picker.js" in page and "SitePicker.init({" in page and "SitePicker.filter(tileFits)" in page
        assert ".tile{" not in page and ".site-bar{" not in page   # styles live in the kit
    assert "Latest by site" not in s and 'id="v-site"' not in s and "/api/compare" not in s   # the strip and the select are gone
    assert 'tr class="v-row' in s and "SamplePopover.open(r.source" in s                        # a row opens that day's results as a graph
    page_py = T("features/comparison/page.py")
    assert "def _site_groups()" in page_py and page_py.count("_site_groups()") == 3


def test_pages_render_with_the_picker():
    from app.wsgi import app
    with app.test_client() as c:
        g = c.get("/graphs").data.decode(); s = c.get("/samples").data.decode()
    for html in (g, s):
        assert 'id="siteNow"' in html and html.count('class="tile"') >= 20 and 'data-days="91">Last 3 mo</button>' in html
    assert 'data-key="" data-name="All sites"' in s and 'data-key="" data-name=' not in g
    assert g.count('data-first="1"') == 1 and 'data-name="Ocean Beach at Lincoln" data-city="1" data-bwtf="1" data-first="1"' in g and 'data-first' not in s   # a fresh Graphs visit opens on Lincoln: dual, city year-round
    assert 'id="toSamples" href="/samples"' in g and 'id="v-graph" href="/graphs"' in s


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
