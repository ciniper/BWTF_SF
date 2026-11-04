"""Lab SOP (/alerts/lab-sop, an Admin tab since 2026-11-04; /learn/lab-sop from 2026-10-18, which redirects): the lab's
wall pages as a page — four phases, steps 1 to 25 in order, every photo on disk, the empty frames shown as slots."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.alerts import lab_sop as S  # noqa: E402


def test_four_phases_and_25_steps_in_order():
    assert [ph["n"] for ph in S.PHASES] == [1, 2, 3, 4]
    assert [st["n"] for st in S.steps()] == list(range(1, 26))
    assert len({ph["key"] for ph in S.PHASES}) == 4 and all(ph["accent"].startswith("#") and ph["tint"].startswith("#") for ph in S.PHASES)


def test_every_photo_is_on_disk_and_described():
    photos = [p for st in S.steps() for p in st.get("photos", [])]
    assert len(photos) == 21 and len({p["src"] for p in photos}) == 21
    for p in photos:
        assert S.photo_path(p["src"]) == S.PHOTO_DIR / p["src"] and len(p["alt"]) > 10, p["src"]
    assert sorted(f.name for f in S.PHOTO_DIR.glob("*.jpg")) == sorted(p["src"] for p in photos)   # no stray files
    assert not (ROOT / "app/static/learn/sop").exists()                                              # nothing public under static (2026-10-20)
    assert S.photo_path("../sop.py") is None and S.photo_path("nope.jpg") is None


def test_layout_pairs_steps_as_the_wall_pages_do():
    items = {it.get("n"): it for ph in S.PHASES for it in S.layout(ph["items"]) if "n" in it}
    assert [n for n, it in items.items() if not it["wide"]] == [3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 21, 22, 23, 24]
    assert items[1]["pics"][0]["inset"]["src"].endswith("s01_rinse_pole.jpg") and len(items[1]["pics"]) == 2   # the pole rides on the waders photo
    assert items[18]["checks"][2] == ("discard", "Discard", "Bad seal or 3+ empty wells")


def test_page_and_photos_sit_behind_the_passphrase():
    """Chase, 2026-10-20: the SOP goes behind the alerts passphrase; unlocking returns you to it. 2026-11-04: an Admin
    tab at /alerts/lab-sop, its Learn address redirecting there."""
    from app.landing import GATED, nav_model
    from app.wsgi import ALERTS_PASSPHRASE as PASS, app
    name = S.steps()[0]["photos"][0]["src"]
    assert GATED == {"/alerts"} and not any("/learn/lab-sop" in h["paths"] or "/alerts/lab-sop" in h["paths"] for h in nav_model())   # a tab of Admin, not a hub row
    with app.test_client() as c:
        old = c.get("/learn/lab-sop")
        assert old.status_code == 302 and old.headers["Location"].endswith("/alerts/lab-sop")      # old links still land
        assert "lab-sop" not in c.get("/learn/collect").data.decode()                                 # the public Collect page no longer points at a locked page (2026-11-04)
        locked = c.get("/alerts/lab-sop").data.decode()
        assert "passphrase" in locked.lower() and 'class="step' not in locked and 'name="next" value="/alerts/lab-sop"' in locked and "<h1>Lab SOP</h1>" in locked
        assert c.get(f"/alerts/lab-sop/photos/{name}").status_code == 401 and c.get(f"/learn/lab-sop/photos/{name}").status_code == 404
        assert c.get(f"/static/learn/sop/{name}").status_code == 404
        bad = c.post("/alerts/unlock", data={"passphrase": "wrong", "next": "/alerts/lab-sop"})
        assert bad.status_code == 401 and 'name="next" value="/alerts/lab-sop"' in bad.data.decode()
        evil = c.post("/alerts/unlock", data={"passphrase": PASS, "next": "https://example.com/"})
        assert evil.status_code == 302 and evil.headers["Location"].endswith("/alerts")              # only back to a gated page
        ok = c.post("/alerts/unlock", data={"passphrase": PASS, "next": "/alerts/lab-sop"})
        assert ok.status_code == 302 and ok.headers["Location"].endswith("/alerts/lab-sop")
        h = c.get("/alerts/lab-sop").data.decode()
        img = c.get(f"/alerts/lab-sop/photos/{name}")
        assert img.status_code == 200 and img.mimetype == "image/jpeg" and "private" in img.headers["Cache-Control"]
        assert c.get("/alerts/lab-sop/photos/nope.jpg").status_code == 404
    assert "{{" not in h and "{%" not in h and "<h1>Lab SOP</h1>" in h and '<meta name="robots" content="noindex">' in h
    assert 'class="subtabs"' not in h and "coming soon" not in h   # Admin and its tabs sit in no hub (2026-11-04)
    assert 'href="/alerts/lab-sop" class="on" aria-current="page"' in h and f"/alerts/lab-sop/photos/{name}?v=" in h     # its own Admin tab, and its photos
    assert h.count('class="step') == 25 and h.count('<img class="main"') == 19 and h.count('<img class="inset"') == 2 and h.count('class="pic slot"') == 9
    assert "Begin aseptic protocol" in h and h.count("Contamination risk") == 2 and "Check control first" in h and "bwtf.surfrider.org" in h
    for ph in S.PHASES:
        assert f'id="{ph["key"]}"' in h and f'href="#{ph["key"]}"' in h


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
