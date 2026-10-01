"""The site's own static files are cacheable without ever going stale (app/assets.py, 2026-10-01):
templates link them by content stamp, only a request with the CURRENT stamp gets the year-long
cache, and pages — where beach status lives — are never cached by it."""
import hashlib
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import assets as A  # noqa: E402

LONG = "max-age=31536000"


def test_the_stamp_is_the_files_contents():
    css = (ROOT / "app/static/brand.css").read_bytes()
    assert A.stamp("brand.css") == hashlib.sha1(css).hexdigest()[:10]
    assert A.asset("brand.css") == "/static/brand.css?v=" + A.stamp("brand.css")
    assert A.asset("/kit.js").startswith("/static/kit.js?v=")
    assert A.asset("no-such-file.js") == "/static/no-such-file.js"                     # a missing file: the plain address
    assert A.stamp("../wsgi.py") is None and A.asset("../wsgi.py") == "/static/../wsgi.py"   # nothing outside app/static is stamped


def test_a_changed_file_gets_a_new_stamp():
    saved_dir, saved = A.STATIC_DIR, dict(A._STAMPS)
    with tempfile.TemporaryDirectory() as d:
        A.STATIC_DIR = pathlib.Path(d).resolve(); A._STAMPS.clear()
        f = A.STATIC_DIR / "x.css"
        f.write_text("a{color:red}"); first = A.stamp("x.css")
        import os, time
        f.write_text("a{color:blue}"); os.utime(f, (time.time() + 5, time.time() + 5))
        assert A.stamp("x.css") != first                                                # edit the file, the address changes
    A.STATIC_DIR, A._STAMPS = saved_dir, saved


def test_only_the_current_stamp_is_cached_long_and_pages_never_are():
    from app.wsgi import app
    with app.test_client() as c:
        good = c.get(A.asset("brand.css"))
        assert good.status_code == 200 and LONG in good.headers["Cache-Control"] and "immutable" in good.headers["Cache-Control"]
        for url in ("/static/brand.css", "/static/brand.css?v=0000000000", "/static/emailmaps/4602.jpg"):
            r = c.get(url)
            assert r.status_code == 200 and LONG not in r.headers.get("Cache-Control", ""), url   # unstamped or stale: as before
        assert c.get("/static/nope.js?v=abc").status_code == 404
        for page in ("/about", "/graphs"):
            assert LONG not in c.get(page).headers.get("Cache-Control", ""), page                   # the pages are built fresh every time


def test_every_template_links_its_static_files_by_stamp():
    for f in sorted((ROOT / "app/templates").rglob("*.html")):
        assert not re.search(r"""["']/static/""", f.read_text()), f.name                  # through asset(), never a bare /static path
    from app.wsgi import app
    with app.test_client() as c:
        for page in ("/about", "/graphs", "/samples", "/signup", "/postings"):
            html = c.get(page).data.decode()
            links = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
            assert links and all("?v=" in u for u in links), (page, [u for u in links if "?v=" not in u])
            for u in set(links):
                assert LONG in c.get(u).headers["Cache-Control"], u


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
