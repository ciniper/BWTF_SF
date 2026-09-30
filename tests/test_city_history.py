"""SFPUC's lab export (2000 → Jul 2020) behind the Samples page, the Graphs and
the Site Report Card (shared/city_history.py). Offline except for the CSV in the
repo; the DataSF call is a stub that records what it was asked for."""
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import city_history as H  # noqa: E402
from shared.datasf import DATASET_FLOOR  # noqa: E402
from features.comparison import comparison as C  # noqa: E402
from features.comparison import samples as S  # noqa: E402
from features.site_analysis import page as P  # noqa: E402


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _Resp(self.rows)


class _Monitor:
    API_URL = "stub://datasf"

    def __init__(self, rows=()):
        self.session = _Session(list(rows))


def test_history_rows_come_in_datasf_shape_and_stop_at_the_floor():
    assert H.available() and H.city_record_floor() == H.HISTORY_FLOOR == "2000-01-03"
    rows = H.records(["OCEAN#15_SL"], "2005-01-01", "2005-12-31")
    assert rows and {r["source"] for r in rows} == {"OCEAN#15_SL"} and all(r["history"] for r in rows)
    assert all(r["sample_date"].endswith("T00:00:00.000") and "2005-01-01" <= r["sample_date"][:10] <= "2005-12-31" for r in rows)
    assert {r["analyte"] for r in rows} == {"ENTERO", "COLI_E", "COLI_TOTAL"}     # E. coli, not fecal coliform, in those years
    assert H.records(["OCEAN#15_SL"], "2001-01-01", "2001-12-31", ["ENTERO"]) == []   # Enterococcus starts Jul 2002
    seam = H.records(None, "2020-07-20", "2020-08-30")
    last = max(r["sample_date"][:10] for r in seam)
    assert seam and last <= H.HISTORY_END < DATASET_FLOOR and last == "2020-07-20"   # the last round before DataSF owns 2020-07-27 on
    assert H.records(None, DATASET_FLOOR, "2021-01-01") == [] and not H.covers(DATASET_FLOOR) and H.covers("2019-12-31")
    prov = H.provenance()
    assert prov["rows"] > 60000 and prov["entero_from"] == "2002-07-01" and prov["datasf_from"] == DATASET_FLOOR


def test_city_fetch_splices_the_export_ahead_of_datasf_without_doubling_the_seam_day():
    api_rows = [{"source": "OCEAN#15_SL", "sample_date": "2020-07-27T00:00:00.000", "analyte": "ENTERO", "data": "<10"},
                {"source": "OCEAN#15_SL", "sample_date": "2020-08-03T00:00:00.000", "analyte": "ENTERO", "data": "20"}]
    mon = _Monitor(api_rows)
    recs = C.fetch_city_records(mon, ["OCEAN#15_SL"], datetime(2020, 7, 1), datetime(2020, 8, 31))
    hist = [r for r in recs if r.get("history")]
    assert hist and all(r["sample_date"][:10] <= H.HISTORY_END for r in hist)
    assert [r for r in recs if not r.get("history")] == api_rows
    keys = [(r["source"], r["sample_date"][:10], r["analyte"]) for r in recs]
    assert len(keys) == len(set(keys))                                              # no station-day-analyte twice
    assert f"sample_date >= '{DATASET_FLOOR}T00:00:00'" in mon.session.calls[0]["$where"]   # DataSF asked only from its floor
    # a window entirely before the floor never calls DataSF
    mon2 = _Monitor([])
    old = C.fetch_city_records(mon2, ["OCEAN#15_SL"], datetime(2005, 1, 1), datetime(2005, 3, 1))
    assert old and mon2.session.calls == []
    # a window entirely after it never touches the export
    mon3 = _Monitor(api_rows)
    assert C.fetch_city_records(mon3, ["OCEAN#15_SL"], datetime(2021, 1, 1), datetime(2021, 2, 1)) == api_rows


def test_samples_page_shows_the_older_years_with_an_e_coli_column():
    start, end = datetime(2005, 1, 1), datetime(2005, 3, 31)
    recs = H.records(["OCEAN#15_SL", "BAY#320_SL"], start, end)
    data = S.build_samples(recs, [], start, end, source="city")
    rows = data["rows"]
    assert rows and all(r["source"] == "SFPUC" and r["history"] for r in rows)
    assert [a["code"] for a in data["analytes"]] == ["ENTERO", "COLI_FECAL", "COLI_E", "COLI_TOTAL"]
    assert any(r["cells"]["COLI_E"] for r in rows) and not any(r["cells"]["COLI_FECAL"] for r in rows)
    assert data["floor"] == "2000-01-03" and data["datasf_from"] == DATASET_FLOOR and data["history"]["rows"] > 60000
    day = S.sample_day_payload(recs, "OCEAN#15_SL", rows[0]["date"])
    assert day["found"] and day["history"] and day["results_url"] is None           # no DataSF page for an export day


def test_report_card_grades_the_export_years_and_calls_reactive_stations_sporadic():
    rows = H.records(analytes=P.INDICATOR_CODES)
    out = P._compute(rows, datetime(2005, 1, 1), datetime(2005, 12, 31, 23, 59, 59), weekly=True, indicator="ENTERO")
    baker = next(s for s in out["sites"] if s["id"] == "OCEAN#15_SL")
    assert baker["samples"] > 30 and baker["yearly"] == [{"year": 2005, "n": baker["samples"], "pct": baker["exceed_pct"]}]
    assert out["dataset_floor"] == "2000-01-03" and out["history"]["label"].startswith("SFPUC lab export")
    # E. coli is gradable in those years; fecal coliform has nothing to grade
    assert next(s for s in P._compute(rows, datetime(2005, 1, 1), datetime(2005, 12, 31), True, "COLI_E")["sites"] if s["id"] == "OCEAN#15_SL")["samples"] > 30
    assert next(s for s in P._compute(rows, datetime(2005, 1, 1), datetime(2005, 12, 31), True, "COLI_FECAL")["sites"] if s["id"] == "OCEAN#15_SL")["samples"] == 0
    # the whole record: Ocean Beach at Pacheco has been sampled only after discharges since 2004, so it is not rankable;
    # Sunnydale Cove is a weekly station
    full = P._compute(rows, None, None, weekly=True, indicator="ENTERO")
    by_id = {s["id"]: s for s in full["sites"]}
    assert by_id["OCEAN#20_SL"]["routine"] is False and by_id["BAY#300.1_SL"]["routine"] is True
    # but in 2000–2003, when Pacheco was on the weekly round, it was
    assert next(s for s in P._compute(rows, datetime(2000, 1, 1), datetime(2003, 12, 31), True, "COLI_TOTAL")["sites"] if s["id"] == "OCEAN#20_SL")["routine"] is True
    # ANY counts E. coli too
    assert P._over("ANY", {"COLI_E": 300}) is True and P._over("COLI_E", {"COLI_E": 100}) is False


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1; print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASS" if not failures else f"{failures} FAILED"); sys.exit(1 if failures else 0)
