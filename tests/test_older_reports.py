"""The older SFPUC discharge reports as an opt-in training record (collectors/csd_pre2018.py,
train_v4.build_dataset(record=...), train_older_reports.py, stages_s2's longer-record folds).
Offline. Run: venv/bin/python tests/test_older_reports.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "features/forecast/src/models"), str(ROOT / "features/forecast/src/collectors")):
    if p not in sys.path:
        sys.path.insert(0, p)

import csd_pre2018 as P  # noqa: E402
import stages_s2 as S2  # noqa: E402
import train_older_reports as TO  # noqa: E402
import train_v4 as T  # noqa: E402

QUICK = {"labels": "csd_pre2018", "day_rule": "first", "start": "2016-03-01"}
LONG = {"labels": "csd_pre2018", "day_rule": "every", "start": "2011-03-01"}
_FRAMES = {}


def _frames(record):
    key = json.dumps(record, sort_keys=True)
    if key not in _FRAMES:
        _FRAMES[key] = T.build_dataset(sources=["avg", "SF Downtown"], record=record)
    return _FRAMES[key]


def test_labels_are_the_files_rows():
    rows = P.load_rows()
    assert set(rows["app_basin"]) == set(T.APP_BASINS)
    # the 2011 Bayside group 31–38 (Islais with Evans and Hudson) and every Islais row are Southeast
    b = pd.read_csv(P.BAYSIDE_CSV, dtype=str, keep_default_na=False)
    assert set(rows.loc[rows["facility"] == "Bayside"].assign(rb=b["report_basin"].values)
               .query("rb == 'Central (Islais Creek)'")["app_basin"]) == {"Southeast"}
    every, first = P.daily_labels("every").set_index("date"), P.daily_labels("first").set_index("date")
    for basin in T.APP_BASINS:
        wet = set(rows.loc[rows["app_basin"] == basin, "date"]) - {d for (bb, d) in P.NOT_RAIN if bb == basin}
        assert set(every.index[every[f"{basin}_csd"] == 1]) == wet, basin
        starts = {d for d in wet if d - pd.Timedelta(days=1) not in set(rows.loc[rows["app_basin"] == basin, "date"])}
        assert set(first.index[first[f"{basin}_csd"] == 1]) == starts, basin
        assert (every[f"{basin}_csd"] <= every[f"{basin}_covered"]).all()
    # what the files add, per basin (NOTES.md; Westside's equipment-failure day left out)
    assert {b: int(every[f"{b}_csd"].sum()) for b in T.APP_BASINS} == {"Westside": 65, "North Shore": 22, "Central": 47, "Southeast": 52}
    late = every.index >= T.TRAIN_START
    assert int(every.loc[late, "Westside_csd"].sum()) == 26                   # the quick win: Westside Mar 2016 – Dec 2017
    # the left-out day is neither positive nor negative; Dec 2012's basin-day rows (no outfall) are positives
    d = pd.Timestamp("2015-06-10")
    assert every.at[d, "Westside_covered"] == 0 and every.at[d, "Westside_status"] == "not_rain"
    dec12 = every.loc["2012-12-01":"2012-12-31"]
    assert int(dec12["Westside_csd"].sum()) == 5 and set(dec12["Westside_status"]) == {"basin_days_esmr"}
    # every month of each file is covered (events / zero / stated_zero, Westside's eSMR month too)
    cov = P.coverage()
    assert cov["covered"].all() and set(cov["status"]) == {"events", "zero", "stated_zero", "basin_days_esmr"}
    assert every.index.max() == pd.Timestamp("2017-12-31") and every["North Shore_covered"].loc["2016-10-01":].sum() == 0


def test_the_record_adds_days_and_moves_nothing_else():
    base, _ = T.build_dataset(sources=["avg", "SF Downtown"])
    quick, notes = _frames(QUICK)
    feats = T.get_feature_columns() + list(T.INTENSITY_FEATURES)
    for src in base:
        a, b = base[src], quick[src]
        assert (a["date"].values == b["date"].values).all() and np.array_equal(a[feats].values, b[feats].values), src
    a, b = base["avg"], quick["avg"]
    for basin in T.APP_BASINS:
        ciwqs = a[f"{basin}_label_source"] == "ciwqs"                          # a CIWQS day never changes
        for col in ("csd", "covered", "volume_mg", "volume_known", "outfalls", "label_source"):
            assert a.loc[ciwqs, f"{basin}_{col}"].equals(b.loc[ciwqs, f"{basin}_{col}"]), (basin, col)
        old = b[f"{basin}_label_source"] == P.SOURCE
        assert old.any() and (b.loc[old, f"{basin}_volume_known"] == 0).all() and (b.loc[old, f"{basin}_covered"] == 1).all()
        assert not (b[f"{basin}_label_source"] == "poobot").any(), basin   # the filed reports replace the feed archive
        assert notes["record"]["basins"][basin]["replaced_archive_days"] == int((a[f"{basin}_label_source"] == "poobot").sum())
    assert notes["record"]["basins"]["Westside"]["discharge_days"] == 22


def test_the_long_record_reads_the_older_rain_masked():
    fr, notes = _frames(LONG)
    f = fr["SF Downtown"]
    assert f["date"].min() == pd.Timestamp("2011-03-01") and notes["record"]["start"] == "2011-03-01"
    assert not f[T.get_feature_columns() + list(T.INTENSITY_FEATURES)].isna().any().any()
    o = notes["rain_SF Downtown"]["older_rain"]
    assert o["first"] == "2011-01-01" and o["outage_runs_masked"] == 2 and o["missing_gauge_days"]["SF Oceanside"] == 329
    # Downtown read 0.00 through 2011-10-11 … 11-02 while Oceanside had 1.64": the run is masked, Oceanside stands in
    s, _ = T.rain_series("SF Downtown", older_rain=True)
    raw = pd.read_csv(T.OLDER_RAIN_CSV, parse_dates=["date"]).pivot_table(index="date", columns="rain_station_name",
                                                                         values="precip_inches", aggfunc="first")
    run = slice("2011-10-11", "2011-11-02")
    assert (raw.loc[run, "SF Downtown"] == 0).all() and np.allclose(s.loc[run].values, raw.loc[run, "SF Oceanside"].fillna(0).values)
    # the served record's own days read the same rain with or without the older span
    new, _ = T.rain_series("avg", older_rain=True)
    plain, _ = T.rain_series("avg")
    assert new.loc[plain.index].equals(plain)


def test_a_record_is_checked():
    for bad in ({"labels": "csd_pre2018", "day_rule": "first"}, {**QUICK, "labels": "poobot"}, {**QUICK, "day_rule": "runs"},
                {**QUICK, "start": "2010-03-01"}, {**QUICK, "start": "2016-04-01"}, {**QUICK, "start": "2012-03-15"}):
        try:
            T.check_record(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    from shared import geography as G
    try:
        T.build_dataset(sources=["avg"], record=QUICK, geo=G.get("sfpuc4_v1"))
    except ValueError:
        pass
    else:
        raise AssertionError("an SFPUC-basin frame took the Bayside outfall groups")


def test_t2_folds_fit_the_longer_record_never_score_it():
    frame = pd.DataFrame({"season": list(range(2010, 2026))})
    for p, q in zip(S2._plan(("T2",)), S2._plan(("T2",), ())):              # no record: the served folds
        assert p[:5] == q[:5] and frame[p[5](frame)].equals(frame[q[5](frame)])
        assert set(frame[p[5](frame)]["season"]) == set(S2.T2_SEASONS) - {p[4]}
    try:
        S2._plan(("T2",), (2016,))
    except ValueError:
        pass
    else:
        raise AssertionError("a scored season accepted as an extra training season")
    plan = {p[1]: p for p in S2._plan(("T2",), tuple(range(2010, 2016)))}
    kept = set(frame[plan["2019-20"][5](frame)]["season"])
    assert kept == set(range(2010, 2016)) | (set(S2.T2_SEASONS) - {2019}), kept
    served = S2.load_set(json.loads((T.SERVE_DIR / "served.json").read_text())["name"], "served")
    assert served.train_record is None and S2.extra_seasons(served) == ()


def test_the_candidates_are_the_served_design_on_their_record():
    served = {k: S2.load_set(json.loads((T.SERVE_DIR / "served.json").read_text())["name"], "served").models[k]
              for k in ("westside", "north_shore", "central", "southeast")}
    for stage1, rec in TO.RECORDS.items():
        name = TO.set_name(stage1)
        s = S2.load_set(name, "candidates")
        assert s.train_record == rec and s.stage1 == stage1, name
        assert S2.extra_seasons(s) == (tuple(range(2010, 2016)) if rec["start"] == "2011-03-01" else (2015,)), name
        tr = S2.training_frames(s)
        S2.check_training_record(s, tr)                                     # the finals refit from the record exactly
        for key, m in s.models.items():
            sv = served[key]
            assert m["features"] == sv["features"] and m["rain_source"] == sv["rain_source"] and m["C"] == sv["C"], (name, key)
            assert m["model"].get_params()["lr__C"] == sv["model"].get_params()["lr__C"]
            assert not S2.head_rows(s, tr, key)[f"{s.geo.basin(key).name}_label_source"].eq(P.SOURCE).any()   # no volume
        man = json.loads((TO.CAND.candidate_dir(name) / "manifest.json").read_text())
        assert man["stage2"]["variant"] == "v2" and man["stage1"] == {"name": stage1, "from": "fit", "family": "logit"}
        # stage 2 v2, the served one when they were made (the live set's table moved to more samples on 2026-10-07)
        assert json.loads((TO.CAND.candidate_dir(name) / "stage2.json").read_text()) == TO.ST2.load_variant("v2")


def test_the_write_up_is_current():
    """OLDER_REPORTS.md is the script's output on the committed stage scores (rerun --report after a rebuild)."""
    assert TO.REPORT.read_text() == TO.report(), "OLDER_REPORTS.md is stale: run train_older_reports.py --report"


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
