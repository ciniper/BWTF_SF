"""The pre-modern discharge record (data/csd/pre2018/, Mar 2011 → the per-event format)
on the Discharge Ledger, its "How reporting changed" page, and the Site Report Card's
"How testing changed" page. Offline."""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PRE = ROOT / "features/forecast/data/csd/pre2018"


def rows(name):
    with open(PRE / name, newline="") as fh:
        return list(csv.DictReader(fh))


def test_westside_rows_add_up_to_the_printed_month_totals():
    cov = rows("westside_monthly_coverage_2011-03_2017-12.csv")
    months = [(int(c["year"]), int(c["month"])) for c in cov]
    want, y, m = [], 2011, 3
    while (y, m) <= (2017, 12):
        want.append((y, m)); y, m = (y + (m == 12), m % 12 + 1)
    assert months == want                                                   # every month Mar 2011 – Dec 2017, once, in order
    for c in cov:
        if c["month_total_MG"] and c["status"] != "basin_days_esmr" and (c["year"], c["month"]) != ("2014", "12"):
            assert abs(float(c["month_total_MG"]) - float(c["rows_volume_sum_MG"])) <= 0.02, c
    dec14 = [c for c in cov if (c["year"], c["month"]) == ("2014", "12")][0]   # the printed total leaves out the NA day
    na_day = sum(float(r["volume_MG"] or 0) for r in rows("westside_daily_2011-03_2017-12.csv") if r["event_date"] == "2014-12-11")
    assert abs(float(dec14["rows_volume_sum_MG"]) - na_day - float(dec14["month_total_MG"])) <= 0.02   # 96.97 - 0.40 vs 96.58 (rounding)


def test_every_row_names_its_source_and_outfalls_come_from_the_registry():
    from shared.outfalls import OUTFALLS
    for r in rows("westside_daily_2011-03_2017-12.csv"):
        assert r["source_document"], r
        assert r["outfall_id"] in OUTFALLS or (r["outfall_id"] == "" and r["event_date"].startswith("2012-12")), r
        if r["method"] == "hand_read":
            assert r["page"], r                                             # a hand-read row can be reopened at its page
    for r in rows("bayside_legacy_2011-03_2016-09.csv"):
        assert r["source_document"] and r["receiving_water"], r
        assert r["outfall_id"] in OUTFALLS or not r["outfall_id"].startswith("CSD-0") or any(c in r["outfall_id"] for c in "–/"), r


def test_the_forecast_record_is_untouched():
    """The served labels, the ledger, the protocol's truth and the live forecast read only the per-event record and
    its grid. The older reports are an opt-in training record (collectors/csd_pre2018.py, read through
    train_v4.build_dataset(record=...)): off by default, and named only by the candidates trained on it."""
    with open(ROOT / "features/forecast/data/csd/sf_csd_events.csv", newline="") as fh:
        ev = list(csv.DictReader(fh))
    assert min(e["event_date"] for e in ev if e["facility"].startswith("Oceanside")) >= "2018-01-01"
    assert min(e["event_date"] for e in ev) >= "2016-10-01"
    never = [ROOT / "features/forecast/src/collectors/csd_labels.py", ROOT / "features/forecast/live_dashboard.py"]
    models = sorted((ROOT / "features/forecast/src/models").glob("*.py"))
    for path in never:                                                      # the labels and the live page never name them
        assert "pre2018" not in path.read_text(), path.name
    # the only forecast modules that name the reader: the opt-in record (train_v4), the S2 refits that honour a
    # candidate's record (stages_s2), the build pinning its files (stages_build), the candidates' trainer and the
    # local term lab (term_lab: its "+ older reports" training record)
    readers = {p.name for p in models if "pre2018" in p.read_text()}
    assert readers == {"train_v4.py", "stages_s2.py", "stages_build.py", "train_older_reports.py", "term_lab.py"}, readers
    # off on the served and live paths: a fresh interpreter builds the served record and imports the live page
    # without loading the reader, and no day of the served frames is an older-report day
    probe = (
        "import sys; sys.path[:0] = [{r!r}, {r!r} + '/features/forecast/src/models', {r!r} + '/features/forecast/src/collectors']\n"
        "import train_v4 as T\n"
        "fr, notes = T.build_dataset(sources=['avg'])\n"
        "assert 'record' not in notes and fr['avg']['date'].min() == T.TRAIN_START\n"
        "assert not any((fr['avg'][b + '_label_source'] == 'csd_pre2018').any() for b in T.APP_BASINS)\n"
        "from features.forecast import live_dashboard, page\n"
        "import compose_v2, live_rules, truth, exclusions\n"
        "assert 'csd_pre2018' not in sys.modules, 'the reader loaded on a served path'\n"
        "print('ok')\n").format(r=str(ROOT))
    import subprocess
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-2000:]
    # the served set names no record (promote.py copies a promoted candidate's record into served.json: that commit
    # moves this line); a candidate that names one says so in its overflow model's name (its lineup's S2)
    models_dir = ROOT / "features/forecast/data/models"
    assert "record" not in json.loads((models_dir / "served.json").read_text())
    for man in sorted((models_dir / "candidates").glob("*/manifest.json")):
        m = json.loads(man.read_text())
        rec = m.get("record")
        assert (rec is not None) == ("_older" in m["lineup"]["s2"]), man.parent.name
        if rec:
            assert rec["labels"] == "csd_pre2018" and rec["day_rule"] in ("first", "every"), man.parent.name


def test_ledger_payload_carries_the_older_rows_with_their_kind():
    from features.discharges import page
    d = page._load()
    assert d["older_columns"][-1] == "kind" and {r[7] for r in d["older"]} == {"D", "G"}
    assert min(r[0] for r in d["older"]) == "2011-03-18"
    assert all(r[6] is None for r in d["older"] if r[7] == "G")             # no Bay volumes before Oct 2016
    cov = d["coverage"]
    assert cov["first"] == "2011-03" and cov["volume_from"] == {"Bayside": "2016-10", "Oceanside": "2011-03"}
    assert "Mar 2011" in cov["note"]
    for s in ("events", "westside", "bayside"):
        status, ctype, body = page.handle_csv({"set": [s]}, b"")
        assert status == 200 and ctype.startswith("text/csv") and body.count(b"\n") > 100, s
    assert page.handle_csv({"set": ["nope"]}, b"")[0] == 400


def test_ledger_and_reporting_pages_render_and_link():
    from app.wsgi import app
    c = app.test_client()
    ledger = c.get("/discharges").data.decode()
    assert 'href="/discharges/reporting"' in ledger and 'min="2011-03-01"' in ledger and 'id="rawSet"' in ledger
    rep = c.get("/discharges/reporting").data.decode()
    q = json.load(open(PRE / "qc_summary.json"))
    assert "How discharge reporting changed" in rep and 'href="/discharges"' in rep
    assert f"{q['counts']['westside']['agree']} of {q['counts']['westside']['months']} months" in rep
    assert f"{q['rain']['westside']['with_rain']} of {q['rain']['westside']['discharge_days']}" in rep
    for gap in ("Sep 2011", "Feb 2012", "Dec 2012", "Aug 2015", "Sep 2016"):
        assert gap in rep, gap


def test_testing_page_renders_from_the_lab_data():
    from app.wsgi import app
    from features.site_analysis import testing_history
    d = testing_history.load()
    assert d["first_enterococcus"] == {"bay": "2002-07-01", "ocean": "2003-10-02"}
    assert d["datasf_indicators"]["Fecal coliform"]["first"] == "2021-04-05"
    assert d["datasf_indicators"]["E. coli"]["last"] == "2021-03-31"
    assert d["median_sample_days"]["2000-2002"] > 120 > d["median_sample_days"]["2004-2019"]
    only = {r["id"]: r for r in d["after_discharge"] if r["discharge_only"]}
    assert set(only) == {"OCEAN#20_SL", "OCEAN#21_SL", "OCEAN#22_SL"} and all(r["other_pct"] <= 2 for r in only.values())
    html = app.test_client().get("/analysis/testing").data.decode()
    assert "How testing changed" in html and 'href="/analysis"' in html and "Fort Point" in html
    assert 'href="/analysis/testing"' in app.test_client().get("/analysis").data.decode()


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {name}")
            except AssertionError as exc:
                failures += 1; print(f"  FAIL {name}: {exc}")
    print("PASS" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
