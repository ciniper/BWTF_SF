"""Learn — PROTOTYPE page (/learn, branch design/learn-prototype): renders from the repo's own registries
and record files, never linked from the nav, kept out of search. Offline."""
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.learn import page as P  # noqa: E402


def test_numbers_come_from_the_registries_and_record_files():
    from shared.outfalls import OUTFALLS
    from shared.standards import STANDARDS, ENTERO_CAUTION
    c = P.learn_context()
    assert c["plants"]["total"] == len(OUTFALLS) == c["plants"]["oceanside"] + c["plants"]["bayside"]
    lim = {b["code"]: b["limit"] for b in c["indicators"]}
    assert lim == {k: f"{STANDARDS[k]['single_sample_max']:,}" for k in ("ENTERO", "COLI_E", "COLI_FECAL", "COLI_TOTAL")} and c["caution"] == ENTERO_CAUTION
    rows = list(csv.DictReader(open(P.POSTINGS_CSV, newline="")))
    assert sum(y["cso"] + y["rain"] + y["other"] for y in c["postings"]["years"]) == len(rows)            # every advisory counted once
    ev = list(csv.DictReader(open(P.CSD_CSV, newline="")))
    assert sum(s["days"] for s in c["discharges"]["seasons"]) == len({e["event_date"] for e in ev})        # every discharge day in one season
    assert c["discharges"]["seasons"][0]["label"].startswith("2016")


def test_page_renders_four_sections_and_stays_unlinked():
    from app.wsgi import app
    from app.landing import nav_model
    with app.test_client() as c:
        r = c.get("/learn"); h = r.data.decode()
        home = c.get("/").data.decode()
    assert r.status_code == 200 and "{{" not in h and "{%" not in h
    for sec in ('id="rain"', 'id="bugs"', 'id="record"', 'id="archive"'):
        assert sec in h, sec
    assert h.count('class="bug"') == 4 and 'id="tray"' in h and 'id="ch-post"' in h and 'id="ch-csd"' in h and 'id="compare"' in h
    assert '<meta name="robots" content="noindex">' in h and "prefers-reduced-motion:reduce" in h        # out of search; motion respects the setting
    assert 'href="/learn"' not in home and all("/learn" not in h2["paths"] for h2 in nav_model())     # a prototype: reachable by URL only


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
