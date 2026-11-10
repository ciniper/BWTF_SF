"""One basemap for every map (shared/basemap.py): the three Leaflet pages draw
the same keyless tile layer through the BASEMAP Jinja global, and the kept
alternatives stay well-formed so switching is a one-word change. Offline."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import basemap as B  # noqa: E402


def test_registry_has_the_chosen_layer_and_the_kept_alternatives():
    assert B.DEFAULT == "esri_topo" and B.basemap()["label"] == "Esri World Topo Map"
    assert set(B.BASEMAPS) >= {"esri_topo", "esri_ocean", "hot", "esri_gray", "osm"}     # E chosen; F, C, H kept; A for re-comparison
    for key, bm in B.BASEMAPS.items():
        assert "{z}" in bm["url"] and "{x}" in bm["url"] and "{y}" in bm["url"], key
        assert "api_key" not in bm["url"] and "apikey" not in bm["url"].lower(), key         # keyless only
        assert bm["options"]["attribution"] and bm["options"]["maxZoom"] >= 13, key
        assert {"label", "url", "options"} <= set(bm), key


def test_every_map_page_draws_the_shared_basemap():
    from app.wsgi import app
    url = B.basemap()["url"]
    with app.test_client() as c:
        for path in ("/signup", "/analysis", "/discharges"):
            html = c.get(path).data.decode()
            assert html.count(url) == 1, path                       # the one tile layer, from the global
            assert "tile.openstreetmap.org" not in html, path       # nobody hand-types a tile URL any more
            assert "L.tileLayer(" in html and "BASEMAP" not in html, path   # rendered, not leaked as a template tag
    for tpl in ("app/templates/signup/page.html", "app/templates/discharges/page.html", "app/templates/site_analysis/page.html"):
        assert "{{ BASEMAP.url|tojson }}" in (ROOT / tpl).read_text(), tpl


def test_map_pages_open_the_tile_and_leaflet_connections_early():
    """A preconnect to the tile server and to unpkg on every page that loads Leaflet, ahead of Leaflet's
    own tags (2026-10-02). The origin follows the basemap choice; a templated host gets no hint."""
    assert B.basemap()["origin"] == "https://server.arcgisonline.com"
    assert B.tile_origin("https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png") is None
    from app.wsgi import app
    # Learn loads Leaflet only when its map scrolls into view, so it gets no hint: the connections could go unused
    lazy = {ROOT / "app/templates/learn/page.html"}
    pages = [p for p in sorted((ROOT / "app/templates").rglob("*.html"))
             if "leaflet@1.9.4/dist/leaflet.js" in p.read_text() and p not in lazy]
    assert len(pages) == 4, [str(p.relative_to(ROOT)) for p in pages]
    with app.test_client() as c:
        for u in ("/", "/signup", "/discharges", "/analysis"):
            h = c.get(u).data.decode()
            tiles = h.index('<link rel="preconnect" href="https://server.arcgisonline.com">')
            unpkg = h.index('<link rel="preconnect" href="https://unpkg.com">')
            assert tiles < h.index("leaflet.css") and unpkg < h.index("leaflet.css"), u


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
