"""About pages: /records (how each record is obtained) and /architecture (the
infrastructure diagram), plus the links that lead to them. Offline."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_records_page_covers_each_record_with_live_freshness():
    from app.wsgi import app
    from features.about import page as about
    html = app.test_client().get("/records").data.decode()
    for anchor in ('id="discharges"', 'id="postings"', 'id="timeline"', 'id="requests"'):
        assert anchor in html, anchor
    fresh = about._freshness()
    assert fresh["discharges"]["refreshed_at"] and fresh["discharges"]["refreshed_at"] in html   # read from data/csd/manifest.json
    assert fresh["postings"]["refreshed_at"] and fresh["postings"]["refreshed_at"] in html       # read from data/beachwatch/manifest.json
    assert "csd_ciwqs/" in html and "beachwatch.py --refresh" in html
    assert "records_request_draft.md" in html


def test_architecture_page_has_both_layouts():
    from app.wsgi import app
    html = app.test_client().get("/architecture").data.decode()
    assert 'class="arch d"' in html and 'class="arch m"' in html
    for name in ("Supabase", "Vercel", "GitHub", "Brevo", "Subscribers", "Visitors", "Healthchecks"):
        assert html.count(f">{name}<") >= 2, name          # in the wide diagram and the phone one
    icons = (ROOT / "app/templates/_icons.html").read_text()
    for icon in ("database", "server", "git-branch", "users", "globe", "clock"):
        assert f'id="i-{icon}"' in icons, icon


def test_record_pages_and_landing_link_here():
    from app import landing as L
    for page, anchor in (("discharges", "discharges"), ("postings", "postings"), ("cso_history", "timeline")):
        assert f'href="/records#{anchor}"' in (ROOT / f"app/templates/{page}/page.html").read_text(), page
    hood = [h for h, _ in L.UNDER_THE_HOOD]
    assert "/architecture" in hood and "/records" in hood


def test_sources_registry_dates_every_tile_and_the_boxes_open_it():
    """/api/sources: one registry of sources with coverage read from the files that already exist; every source box is a button."""
    import json
    from shared import sources as S
    from shared import supabase as sb
    from shared.datasf import DATASET_FLOOR
    reg = S.registry(include_supabase=False)
    tiles = reg["tiles"]
    assert set(tiles) == set(S.TILES) and all(tiles[k] for k in ("sfpuc", "datasf", "bwtf", "weather", "state", "supabase", "vercel", "github"))
    for entries in tiles.values():
        for e in entries:
            assert set(e) >= {"name", "what", "kind", "first", "last", "cadence", "stored", "used_by"}
            assert e["kind"] in ("live API", "repo file", "Supabase table", "service"), e["name"]
    ds = tiles["datasf"]
    assert ds[0]["first"] == DATASET_FLOOR and ds[0]["kind"] == "live API" and ds[0]["last"] >= "2026-08-31"   # newest sample from the training copy offline
    stardb = next(e for e in ds if "STARDB" in e["name"]); assert stardb["first"] == "2000-01-03" and stardb["last"] == "2020-07-26"
    poo = next(e for e in ds if "Poo Bot" in e["name"]); assert poo["first"] == "2016-03-19" and poo["last"] == "2017-01-10"
    ciwqs = next(e for e in tiles["state"] if "CIWQS" in e["name"])
    man = json.load(open(ROOT / "features/forecast/data/csd/manifest.json"))
    assert ciwqs["refreshed"] == man["refreshed_at"] and ciwqs["next_due"] and ciwqs["first"] == "2016-10-16" and ciwqs["last"].startswith("2026-")
    bw = next(e for e in tiles["state"] if "BeachWatch" in e["name"]); assert bw["first"] == "1999-01-31" and bw["last"] >= "2026-02-28" and bw["refreshed"]
    rain = next(e for e in tiles["weather"] if "ACIS" in e["name"]); assert rain["first"] == "2016-01-01" and rain["last"] == "live"
    assert [e["name"] for e in tiles["supabase"]][:3] == ["subscribers", "alert_log", "alert_deliveries"] and reg["supabase_reachable"] is False
    # the page: every source box is a button in both layouts, the panel exists, and the endpoint answers offline
    from app.wsgi import app
    c = app.test_client()
    html = c.get("/architecture").data.decode()
    for tile in ("sfpuc", "datasf", "bwtf", "weather", "state", "supabase", "vercel", "github", "brevo", "subscribers", "healthchecks", "visitors"):
        assert html.count(f'data-tile="{tile}"') >= 2, tile
    assert 'id="srcPanel"' in html and "/api/sources" in html and 'role="button"' in html
    orig = sb.is_configured
    sb.is_configured = lambda: False
    S._CACHE.update(at=0.0, supabase=None)
    try:
        api = c.get("/api/sources").get_json()
    finally:
        sb.is_configured = orig
        S._CACHE.update(at=0.0, supabase=None)
    assert api["labels"] == S.TILES and api["tiles"]["datasf"][0]["first"] == DATASET_FLOOR and api["supabase_reachable"] is False


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
