"""Source Comparison: the city's latest result keeps both samples of a
double-sampled day, and the sample viewer (features/comparison/samples.py)
lists every published result for the chosen sites and window. Offline: pure
functions on synthetic records."""
import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.comparison import comparison as C  # noqa: E402
from features.comparison import samples as S  # noqa: E402


def test_latest_city_result_keeps_both_samples_of_the_latest_day():
    recs = [  # newest first, as the query orders them
        {"source": "A", "sample_date": "2024-02-20T00:00:00.000", "data": "20"},
        {"source": "A", "sample_date": "2024-02-20T00:00:00.000", "data": "399"},
        {"source": "A", "sample_date": "2024-02-13T00:00:00.000", "data": "10"},
        {"source": "B", "sample_date": "2024-02-20T00:00:00.000", "data": "52"},
    ]
    latest = C._latest_by_source(recs)
    a, b = latest["A"], latest["B"]
    assert a["raw"] == "20 / 399" and a["raws"] == ["20", "399"] and a["value"] == 399 and a["n"] == 2
    assert a["date"].strftime("%Y-%m-%d") == "2024-02-20"       # the 13th is not the latest day
    assert b["raw"] == "52" and b["value"] == 52 and b["n"] == 1
    assert "city_raws" in C.ComparisonRow.__dataclass_fields__ and "city_n" in C.ComparisonRow.__dataclass_fields__


def _city(sid, day, **vals):
    return [{"source": sid, "sample_date": f"{day}T00:00:00.000", "analyte": a, "data": str(v)} for a, v in vals.items()]


def test_sample_viewer_lists_every_result_and_grades_each_indicator():
    start, end = datetime(2024, 1, 1), datetime(2024, 3, 1)
    baker, islais = "OCEAN#15_SL", "BAY#320_SL"           # Baker Beach at Lobos Creek (dual), Islais Creek (city only)
    city = (_city(baker, "2024-02-20", ENTERO=20, COLI_FECAL=100, COLI_TOTAL=1500)   # double-sampled day: a second Entero below
            + _city(baker, "2024-02-20", ENTERO=399)
            + _city(baker, "2024-02-13", ENTERO="<10", COLI_FECAL=200, COLI_TOTAL=1500)   # total over only by the ratio rule
            + _city(islais, "2024-02-20", ENTERO=500))
    bwtf = [
        {"site_name": "Baker Beach at Lobos Creek", "collection_time": datetime(2024, 2, 20, 10, 0), "substance": "Enterococcus", "result_value": 41.0, "result_raw": "41"},
        {"site_name": "Bayview Hunters Point", "collection_time": datetime(2024, 2, 19, 9, 30), "substance": "Enterococcus", "result_value": 120.0, "result_raw": "120"},
        {"site_name": "Baker Beach at Lobos Creek", "collection_time": datetime(2023, 12, 1, 9, 0), "substance": "Enterococcus", "result_value": 10.0, "result_raw": "10"},  # before the window
    ]
    dual = S.build_samples(city, bwtf, start, end, scope="dual")
    rows = dual["rows"]
    assert [(r["date"], r["source"], r["site"]) for r in rows] == [
        ("2024-02-20", "SFPUC", "Baker Beach at Lobos Creek"), ("2024-02-20", "BWTF", "Baker Beach at Lobos Creek"),
        ("2024-02-13", "SFPUC", "Baker Beach at Lobos Creek")]
    r20 = rows[0]
    assert r20["n_samples"] == 2 and [v["raw"] for v in r20["cells"]["ENTERO"]] == ["20", "399"]
    assert [v["over"] for v in r20["cells"]["ENTERO"]] == [False, True] and r20["over"] is True
    assert r20["cells"]["COLI_TOTAL"][0]["over"] is False                 # 100/1500 is under the 10% ratio: limit 10,000
    r13 = rows[2]
    assert r13["cells"]["COLI_TOTAL"][0]["over"] is True                  # 200/1500 is over it: limit 1,000
    assert r13["cells"]["ENTERO"][0]["raw"] == "<10" and r13["cells"]["ENTERO"][0]["value"] == 5.0
    rb = rows[1]
    assert rb["time"] == "10:00 AM" and rb["cells"]["ENTERO"][0]["caution"] is True and rb["over"] is False
    assert dual["counts"] == {"rows": 3, "city": 2, "bwtf": 1, "double_sampled_days": 1, "over": 2}
    # the site list carries every station plus the BWTF-only site; dual scope hides the city-only and BWTF-only ones
    keys = {s["key"]: s for s in dual["sites"]}
    assert keys[baker]["dual"] and not keys[islais]["dual"] and keys["bwtf:Bayview Hunters Point"]["city"] is False
    assert sum(s["dual"] for s in dual["sites"]) == len(C.BWTF_TO_SFPUC_NAME) == 6
    all_rows = S.build_samples(city, bwtf, start, end, scope="all")["rows"]
    assert len(all_rows) == 5 and {r["site"] for r in all_rows} >= {"Islais Creek", "Bayview Hunters Point"}
    only = S.build_samples(city, bwtf, start, end, scope="all", site=islais)["rows"]
    assert len(only) == 1 and only[0]["cells"]["ENTERO"][0]["over"] is True
    assert S.build_samples(city, bwtf, start, end, scope="bogus")["scope"] == "dual"


def test_default_range_is_the_last_year_and_bad_input_falls_back():
    s, e = S.parse_range("", "")
    assert (e - s).days == S.DEFAULT_DAYS == 365 and e.date() == datetime.now().date()
    s, e = S.parse_range("2021-01-01", "2021-12-31")
    assert (s.year, e.month, e.day) == (2021, 12, 31)
    s, e = S.parse_range("2025-01-01", "2024-01-01")   # reversed → last year before the end
    assert e == datetime(2024, 1, 1) and s == e - timedelta(days=365)
    s, e = S.parse_range("garbage", "2024-06-01")
    assert e == datetime(2024, 6, 1) and s == e - timedelta(days=365)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
