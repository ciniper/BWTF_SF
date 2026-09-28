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
from shared import standards  # noqa: E402
assert S.parse_range is C.parse_range  # one range parser for the graph and the viewer


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


def test_resolve_site_accepts_station_ids_bwtf_keys_and_bwtf_names():
    st = C.resolve_site("OCEAN#15_SL")                       # a dual station by id
    assert st == {"key": "OCEAN#15_SL", "name": "Baker Beach at Lobos Creek", "bwtf_name": "Baker Beach at Lobos Creek", "sources": ["OCEAN#15_SL"], "dual": True}
    assert C.resolve_site("Ocean Beach at Vicente St")["key"] == "OCEAN#21_SL"   # the table's original key, a BWTF name
    city_only = C.resolve_site("BAY#320_SL")
    assert city_only["name"] == "Islais Creek" and city_only["bwtf_name"] is None and city_only["dual"] is False
    bw = C.resolve_site("bwtf:Bayview Hunters Point")
    assert bw == {"key": "bwtf:Bayview Hunters Point", "name": "Bayview Hunters Point", "bwtf_name": "Bayview Hunters Point", "sources": [], "dual": False}


class _Resp:
    def __init__(self, records): self._r = records
    def raise_for_status(self): pass
    def json(self): return self._r


class _Session:
    def __init__(self, records): self.records = records; self.calls = []
    def get(self, url, params=None, timeout=None): self.calls.append(params); return _Resp(self.records)


class _Monitor:
    API_URL = "https://example.test/rows.json"
    def __init__(self, records): self.session = _Session(records)


class _Bwtf:
    def __init__(self, lab): self._lab = lab
    def fetch_lab(self): return self._lab
    def fetch_history(self, since=None, **kw): return []


class _Sfpuc:
    def fetch_stations(self): return []


def test_comparison_lists_every_station_with_the_lab_sites_first():
    from features.comparison.bwtf_api import BWTFLab, BWTFSite, BWTFSample
    when = datetime(2026, 9, 24, 18, 30)
    site = BWTFSite("s1", "Aquatic Park", 37.8, -122.4, when, "20", 20.0,
                    [BWTFSample(when, "Enterococcus", "20", 20.0)])
    bay = BWTFSite("s2", "Bayview Hunters Point", 37.7, -122.38, when, "131", 131.0,
                   [BWTFSample(when, "Enterococcus", "131", 131.0)])
    lab = BWTFLab(76, "SF", "San Francisco", 36, 104, [site, bay])
    records = [  # newest first, ENTERO only (the query filters by analyte)
        {"source": "BAY#211_SL", "sample_date": "2026-09-22T00:00:00.000", "data": "20"},
        {"source": "BAY#211_SL", "sample_date": "2026-09-22T00:00:00.000", "data": "399"},
        {"source": "BAY#320_SL", "sample_date": "2026-09-22T00:00:00.000", "data": "148"},
    ]
    mon = _Monitor(records)
    data = C.build_comparison(bwtf_client=_Bwtf(lab), sf_gov_monitor=mon, sfpuc_api=_Sfpuc())
    rows = data["rows"]
    assert len(rows) == 21 and [r["site_name"] for r in rows[:2]] == ["Aquatic Park", "Bayview Hunters Point"]
    aq = rows[0]
    assert aq["dual"] and aq["bwtf_site"] and aq["site_key"] == "BAY#211_SL"
    assert aq["city_raw"] == "20 / 399" and aq["city_n"] == 2 and aq["city_exceeds"] is True and aq["agree"] is False
    assert rows[1]["site_key"] == "bwtf:Bayview Hunters Point" and rows[1]["city_raw"] is None and rows[1]["bwtf_site"]
    islais = next(r for r in rows if r["site_key"] == "BAY#320_SL")
    assert islais["bwtf_site"] is False and islais["dual"] is False and islais["city_raw"] == "148" and islais["bwtf_raw"] is None
    # one city query covered every station; the head-to-head summary is still about the lab's sites
    assert len(mon.session.calls) == 1 and all(f"source='{sid}'" in mon.session.calls[0]["$where"] for sid in ("BAY#320_SL", "OCEAN#22_SL"))
    s = data["summary"]
    assert (s["site_count"], s["all_site_count"], s["comparable_count"], s["disagree_count"], s["city_exceed_count"]) == (2, 21, 1, 1, 1)


def e_ratio_off(recs, station):
    return S.sample_day_payload(recs, station, "2024-02-13")["ratio_applied"]


def test_sample_day_payload_grades_one_station_day_with_both_values_and_the_ratio_limit():
    baker = "OCEAN#15_SL"
    recs = (_city(baker, "2024-02-20", ENTERO=20, COLI_FECAL=200, COLI_TOTAL=1500) + _city(baker, "2024-02-20", ENTERO=399)
            + _city(baker, "2024-02-13", ENTERO=41, COLI_FECAL=10, COLI_TOTAL=100))
    d = S.sample_day_payload(recs, baker)                      # no date → the newest published day
    assert d["date"] == "2024-02-20" and d["found"] and d["name"] == "Baker Beach at Lobos Creek" and d["n_samples"] == 2
    assert [v["raw"] for v in d["cells"]["ENTERO"]] == ["20", "399"] and [v["over"] for v in d["cells"]["ENTERO"]] == [False, True]
    ST = standards.STANDARDS
    assert d["limits"] == {"ENTERO": ST["ENTERO"]["single_sample_max"], "COLI_FECAL": ST["COLI_FECAL"]["single_sample_max"],
                           "COLI_TOTAL": ST["COLI_TOTAL"]["single_sample_max_ratio"]}   # fecal is 13% of total → the ratio limit
    assert d["cells"]["COLI_TOTAL"][0]["over"] is True and d["over"] is True and d["caution"] == standards.ENTERO_CAUTION
    assert d["ratio_applied"] is True and "10%" in d["ratio_note"] and e_ratio_off(recs, baker) is False
    assert d["results_url"].startswith("https://data.sf.gov/resource/") and "2024-02-20T00%3A00%3A00" in d["results_url"] and "OCEAN%2315_SL" in d["results_url"]
    assert d["viewer_url"] == "/samples?scope=all&site=OCEAN%2315_SL" and d["graph_url"] == "/graphs?site=OCEAN%2315_SL"
    e = S.sample_day_payload(recs, baker, "2024-02-13")
    assert e["cells"]["ENTERO"][0]["caution"] is True and e["over"] is False and e["limits"]["COLI_TOTAL"] == ST["COLI_TOTAL"]["single_sample_max"]
    none = S.sample_day_payload(recs, baker, "2024-01-01")
    assert none["found"] is False and none["n_samples"] == 0 and none["cells"] == {"ENTERO": [], "COLI_FECAL": [], "COLI_TOTAL": []}
    try:
        S.sample_day_payload(recs, "NOPE"); assert False, "unknown station must raise"
    except ValueError:
        pass
    # the popover script is one file both pages include, and it reads the same endpoint
    js = (ROOT / "app" / "static" / "sample_popover.js").read_text()
    assert "/api/sample-day?" in js and "data-sample-station" in js
    for tpl in ("app/templates/alerts/dashboard.html", "app/templates/forecast/page.html"):
        assert "/static/sample_popover.js" in (ROOT / tpl).read_text(), tpl


def test_site_series_grades_every_indicator_per_day_and_pairs_same_day_samples():
    site = C.resolve_site("OCEAN#15_SL")
    start, end = datetime(2024, 1, 1), datetime(2024, 3, 1)
    recs = (_city("OCEAN#15_SL", "2024-02-20", ENTERO=20, COLI_FECAL=200, COLI_TOTAL=1500) + _city("OCEAN#15_SL", "2024-02-20", ENTERO=399)
            + _city("OCEAN#15_SL", "2024-02-13", ENTERO="<10", COLI_FECAL=10, COLI_TOTAL=100) + _city("BAY#320_SL", "2024-02-13", ENTERO=500))
    hist = [{"site_name": "Baker Beach at Lobos Creek", "collection_time": datetime(2024, 2, 20, 10), "substance": "Enterococcus", "result_value": 41.0, "result_raw": "41"},
            {"site_name": "Aquatic Park", "collection_time": datetime(2024, 2, 20, 10), "substance": "Enterococcus", "result_value": 900.0, "result_raw": "900"}]
    d = C.site_series_payload(site, recs, hist, start, end)
    assert d["site"] == "Baker Beach at Lobos Creek" and d["dual"] and d["bwtf_sampled"]
    ent = d["city"]["ENTERO"]
    assert [(p["date"], p["value"], p["raw"], p["over"], p["n"]) for p in ent] == [("2024-02-13", 5.0, "<10", False, 1), ("2024-02-20", 399.0, "399", True, 2)]
    tot = d["city"]["COLI_TOTAL"]
    assert [(p["date"], p["over"], p["ratio"]) for p in tot] == [("2024-02-13", False, False), ("2024-02-20", True, True)]   # 200/1500 → limit 1,000
    assert [p["raw"] for p in d["bwtf"]] == ["41"] and d["bwtf"][0]["over"] is False        # the other lab site is filtered out
    assert d["paired"] == [{"date": "2024-02-20", "bwtf": 41.0, "bwtf_raw": "41", "city": 399.0, "city_raw": "399"}]
    assert d["counts"] == {"city_days": 2, "bwtf": 1, "paired": 1} and d["limits"]["ENTERO"] == standards.STANDARDS["ENTERO"]["single_sample_max"]
    only = C.site_series_payload(C.resolve_site("BAY#320_SL"), recs, hist, start, end)
    assert only["bwtf_sampled"] is False and only["bwtf"] == [] and only["paired"] == [] and only["city"]["ENTERO"][0]["over"] is True
    assert {s["key"] for s in C.graph_sites()} >= {"OCEAN#15_SL", "BAY#320_SL", "bwtf:Bayview Hunters Point"}


def test_sample_rows_carry_the_volunteer_field_notes_from_events():
    start, end = datetime(2026, 9, 1), datetime(2026, 9, 30)
    event = {"site_name": "Aquatic Park", "collection_time": datetime(2026, 9, 24, 18, 30), "tested_by": "A. Volunteer", "comments": "Kelp on the beach", "volume": "",
             "weather": {"air_temp": 64.0, "water_temp": 58.0, "current_weather": "Partly Cloudy", "precipitation": False, "tide": "rising", "wave_height": "1-2 ft", "wind_direction": "W", "wind_speed": 8.0},
             "samples": [{"substance": "Enterococcus", "result_raw": "20", "result_display": "20", "result_value": 20.0, "units": "MPN/100mL", "method": "", "modifier": ""}]}
    d = S.build_samples([], [event], start, end, scope="dual")
    r = d["rows"][0]
    assert r["source"] == "BWTF" and r["time"] == "6:30 PM" and r["cells"]["ENTERO"][0]["raw"] == "20"
    assert r["field"] == {"tested_by": "A. Volunteer", "air": "64°F", "water": "58°F", "sky": "Partly Cloudy", "wind": "W 8 mph", "tide": "Rising",
                          "waves": "1-2 ft", "rain": "No", "comments": "Kelp on the beach"}
    assert [c["key"] for c in d["field_columns"]] == ["tested_by", "air", "water", "sky", "wind", "tide", "waves", "rain", "comments"]
    # history-shaped input (no field record) still works and carries no notes
    plain = S.build_samples([], [{"site_name": "Aquatic Park", "collection_time": datetime(2026, 9, 24, 18, 30), "substance": "Enterococcus", "result_value": 20.0, "result_raw": "20"}], start, end)
    assert plain["rows"][0]["field"] is None


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
