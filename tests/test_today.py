"""The Today tab (/today): the Main page's board with an experimental Surfrider layer
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
    assert 'id="today-map"' in h and 'id="zones"' in h and 'id="tg-bwtf"' in h and "Experimental." in h and "/api/today/surfrider" in h
    assert 'class="topbar"' in h and 'href="/today" class="on" aria-current="page">Today' in h and 'class="hubs"' not in h    # the board, not the hubs
    landing = c.get("/").data.decode() if False else None  # noqa: F841
    with app.test_client() as c:
        m = c.get("/").data.decode()
    assert 'id="tg-bwtf"' not in m and 'id="today-map"' in m and '<a href="/today"' in m                                       # Main keeps the plain board and links to Today
    assert "board_section" in (ROOT / "app/templates/landing.html").read_text() and "board_section" in (ROOT / "app/templates/today/page.html").read_text()   # one implementation


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
