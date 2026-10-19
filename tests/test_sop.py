"""Lab SOP (/learn/lab-sop, 2026-10-18): the lab's wall pages as a page — four phases, steps 1 to 25 in order,
every photo on disk, the empty frames shown as slots."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.learn import sop as S  # noqa: E402


def test_four_phases_and_25_steps_in_order():
    assert [ph["n"] for ph in S.PHASES] == [1, 2, 3, 4]
    assert [st["n"] for st in S.steps()] == list(range(1, 26))
    assert len({ph["key"] for ph in S.PHASES}) == 4 and all(ph["accent"].startswith("#") and ph["tint"].startswith("#") for ph in S.PHASES)


def test_every_photo_is_on_disk_and_described():
    photos = [p for st in S.steps() for p in st.get("photos", [])]
    assert len(photos) == 21 and len({p["src"] for p in photos}) == 21
    for p in photos:
        assert (ROOT / "app/static" / p["src"]).is_file() and p["src"].startswith("learn/sop/") and len(p["alt"]) > 10, p["src"]
    assert sorted(f.name for f in (ROOT / "app/static/learn/sop").glob("*.jpg")) == sorted(Path(p["src"]).name for p in photos)   # no stray files


def test_layout_pairs_steps_as_the_wall_pages_do():
    items = {it.get("n"): it for ph in S.PHASES for it in S.layout(ph["items"]) if "n" in it}
    assert [n for n, it in items.items() if not it["wide"]] == [3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 21, 22, 23, 24]
    assert items[1]["pics"][0]["inset"]["src"].endswith("s01_rinse_pole.jpg") and len(items[1]["pics"]) == 2   # the pole rides on the waders photo
    assert items[18]["checks"][2] == ("discard", "Discard", "Bad seal or 3+ empty wells")


def test_page_renders():
    from app.wsgi import app
    with app.test_client() as c:
        h = c.get("/learn/lab-sop").data.decode()
    assert "{{" not in h and "{%" not in h and "<h1>Lab SOP</h1>" in h and '<meta name="robots" content="noindex">' in h
    assert 'class="on" aria-current="page">Lab SOP' in h and "coming soon" not in h
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
