"""Vercel Web Analytics + Speed Insights (2026-10-02): the two script tags ride the shared frame on every
production page, URLs go without their query string, the token pages (manage, unsubscribe) carry neither
script, a self-reload is not counted as a visit, and nothing loads outside production."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TAGS = ('src="/_vercel/insights/script.js"', 'src="/_vercel/speed-insights/script.js"')


def _pages(on: bool) -> dict:
    from app.wsgi import app
    saved = app.jinja_env.globals["VERCEL_INSIGHTS"]
    app.jinja_env.globals["VERCEL_INSIGHTS"] = on
    try:
        with app.test_client() as c:
            return {u: c.get(u).data.decode() for u in ("/about", "/graphs", "/samples", "/signup", "/manage?t=00000000-0000-0000-0000-000000000000",
                                                        "/unsubscribe?t=00000000-0000-0000-0000-000000000000")}
    finally:
        app.jinja_env.globals["VERCEL_INSIGHTS"] = saved


def test_production_pages_carry_both_scripts_and_send_urls_without_their_query():
    pages = _pages(True)
    for u in ("/about", "/graphs", "/samples", "/signup"):
        h = pages[u]
        assert all(t in h for t in TAGS), u
        assert 'u.search = ""' in h and 'window.va("beforeSend"' in h and 'window.si("beforeSend", bare)' in h and "window.speedInsightsBeforeSend = bare" in h, u
        assert 'n.type === "reload"' in h, u                                   # the board's five-minute self-reload is not a new visit
        assert h.index(TAGS[0]) > h.index('class="tabbar"'), u                  # at the end of the body, after the page


def test_the_token_pages_never_load_them():
    pages = _pages(True)
    for u in ("/manage?t=00000000-0000-0000-0000-000000000000", "/unsubscribe?t=00000000-0000-0000-0000-000000000000"):
        assert not any(t in pages[u] for t in TAGS) and "_vercel" not in pages[u], u
        assert 'class="tabbar"' in pages[u], u                                  # still the shared frame


def test_nothing_loads_outside_production():
    pages = _pages(False)
    for u, h in pages.items():
        assert "_vercel" not in h and "window.va" not in h, u
    import os
    from app.wsgi import app
    assert app.jinja_env.globals["VERCEL_INSIGHTS"] is (os.environ.get("VERCEL_ENV") == "production")   # local runs and tests: off


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
