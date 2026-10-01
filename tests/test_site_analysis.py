"""Site Report Card (/analysis): the Standard toggle grades the same samples
four ways — Enterococcus (default), fecal coliform, total coliform with the
ratio rule, and ANY (the state posting rule). Runs offline on synthetic rows."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.site_analysis import page as P  # noqa: E402
from shared import standards  # noqa: E402


def _rows(sid, day, **vals):
    return [{"source": sid, "sample_date": f"{day}T00:00:00.000", "analyte": a, "data": str(v)}
            for a, v in vals.items()]


def test_indicator_toggle_grades_the_same_samples_four_ways():
    sid = next(iter(P.STATIONS))
    data = (_rows(sid, "2024-01-01", ENTERO=50, COLI_FECAL=100, COLI_TOTAL=12000)   # total over its plain limit
            + _rows(sid, "2024-01-08", ENTERO=120, COLI_FECAL=20, COLI_TOTAL=500)   # entero over
            + _rows(sid, "2024-01-15", ENTERO=30, COLI_FECAL=200, COLI_TOTAL=1500)  # total over only by the ratio rule
            + _rows(sid, "2024-01-22", ENTERO=10, COLI_FECAL=10, COLI_TOTAL=100)    # clean
            + _rows(sid, "2024-01-23", ENTERO=500))                                 # resample, entero only

    def grade(ind, weekly=True):
        out = P._compute(data, None, None, weekly, ind)
        s = next(x for x in out["sites"] if x["id"] == sid)
        assert out["indicator"] == ind and out["indicator_label"] == P.INDICATORS[ind]
        return s["exceed_pct"], s["samples"], s["caution_pct"], s["median"]

    # weekly regime: the 23rd is the second sample of its week and drops out
    assert grade("ENTERO") == (25.0, 4, 50.0, 50)
    assert grade("COLI_FECAL") == (0.0, 4, None, 100)
    assert grade("COLI_TOTAL") == (50.0, 4, None, 1500)          # 12,000 and the ratio-rule 1,500
    assert grade("ANY") == (75.0, 4, None, None)                 # every week but the clean one
    # all samples: the entero-only resample joins the entero and ANY counts, not the coliform ones
    assert grade("ENTERO", weekly=False) == (40.0, 5, 60.0, 50)
    assert grade("COLI_TOTAL", weekly=False) == (50.0, 4, None, 1500)
    assert grade("ANY", weekly=False) == (80.0, 5, None, None)
    # the ratio rule really is the shared one
    assert P._over("COLI_TOTAL", {"COLI_FECAL": 200, "COLI_TOTAL": 1500}) is True
    assert P._over("COLI_TOTAL", {"COLI_FECAL": 100, "COLI_TOTAL": 1500}) is False
    assert P._over("COLI_TOTAL", {"ENTERO": 5}) is None
    assert P._over("ANY", {"ENTERO": 5}) is False and P._over("ANY", {}) is None
    # the words on the page come from shared/standards.py too
    t = standards.STANDARDS["COLI_TOTAL"]
    assert f"{t['single_sample_max_ratio']:,}" in P.bad_text("COLI_TOTAL") and "10%" in P.bad_text("ANY")
    ctx = P.standards_context()
    assert [i["code"] for i in ctx["indicators"]] == list(P.INDICATORS) and ctx["graded_on"] == P.DEFAULT_INDICATOR


def test_report_card_groups_sites_by_zone_and_offers_year_presets():
    """Chase, 2026-10-01: the report card uses the four zones (shared/zones.py), not the stations' older
    three-way shoreline group, and its range presets run 1 year → 3 → 5 → Full record."""
    from shared.zones import ZONES, ZONE_OF_SOURCE
    sid = next(iter(P.STATIONS))
    out = P._compute(_rows(sid, "2024-01-01", ENTERO=50), None, None, False, "ENTERO")
    site = next(x for x in out["sites"] if x["id"] == sid)
    assert site["zone"] == ZONE_OF_SOURCE[sid] and site["zone_label"] == ZONES[site["zone"]].label and "group" not in site
    assert {z for _, z, _, _ in P.STATIONS.values()} == set(ZONES)
    ctx = P.standards_context()
    assert [z["key"] for z in ctx["zones"]] == list(ZONES) and ctx["zones"][1]["label"] == "Baker & China Beach"
    tpl = (ROOT / "app/templates/site_analysis/page.html").read_text()
    assert 'data-zone="{{ z.key }}"' in tpl and "East Bayshore" not in tpl and "activeGroup" not in tpl and ".chip.z-baker_china" in tpl
    assert [d for d in ("365", "1096", "1826", "") if f'data-days="{d}">' in tpl] == ["365", "1096", "1826", ""] and 'data-days="1826">5 years<' in tpl and "Last 3 mo" not in tpl
    assert "Date.now() - 365 * 864e5" in tpl   # opens on the smallest preset


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
