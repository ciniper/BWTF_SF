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


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
