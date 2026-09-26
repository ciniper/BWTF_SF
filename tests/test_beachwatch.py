"""BeachWatch postings under data/beachwatch/ — offline checks on the stored files.

    venv/bin/python tests/test_beachwatch.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "features/forecast/src/collectors"))
import beachwatch as BW  # noqa: E402


def test_stored_advisories_are_sane():
    adv = BW.load_advisories()
    assert len(adv) >= 2000, len(adv)
    assert {"station_name", "zone", "cause_class", "posted_on", "reopened_on", "duration_days", "advisory_type", "cause"} <= set(adv.columns)
    assert adv.posted_on.notna().all() and adv.reopened_on.notna().all()
    assert (adv.reopened_on >= adv.posted_on).all()
    assert adv.posted_on.min().year >= 1999 and adv.reopened_on.max() <= pd.Timestamp.today() + pd.Timedelta(days=1)
    assert adv.cause_class.isin(BW.CAUSE_CLASSES).all()
    assert adv.zone.ne("").mean() >= 0.99, adv.loc[adv.zone.eq(""), "station_name"].value_counts().to_dict()
    assert set(adv.zone[adv.zone.ne("")]) == {"ocean", "baker_china", "north", "east"}
    cso = adv[adv.cause_class == "cso"]
    assert len(cso) >= 900 and cso.duration_days.median() <= 3, (len(cso), cso.duration_days.median())
    # every registry station that BeachWatch knows maps back to the same zone the registry gives it
    for sid, zk in BW.STATION_ZONE.items():
        sub = adv[adv.station_name == sid]
        assert sub.empty or (sub.zone == zk).all(), sid


def test_posted_zone_days_are_the_advisories_expanded():
    adv = BW.load_advisories()
    stored = BW.load_posted_zone_days()
    rebuilt = BW.posted_zone_days(adv)
    assert len(stored) == len(rebuilt), (len(stored), len(rebuilt))
    m = stored.merge(rebuilt, on=["zone", "date"], suffixes=("", "_r"))
    assert len(m) == len(stored) and (m.cause_class == m.cause_class_r).all() and (m.n_advisories == m.n_advisories_r).all()
    east_2025 = stored[(stored.zone == "east") & (stored.date.dt.year == 2025)]
    assert len(east_2025) >= 90, len(east_2025)          # the East is posted ~a quarter of the year
    look = BW.posted_lookup()
    assert look["east"][pd.Timestamp("2026-02-19")] == "cso"   # the Feb 2026 storm posted the East


def test_agrees_with_the_2016_17_feed_archive():
    """SFPUC's own POSTED rows (Poo Bot snapshots) vs BeachWatch's intervals,
    read inclusive of the reopening day."""
    adv = BW.load_advisories()
    fs = pd.read_csv(ROOT / "features/forecast/data/poobot/feed_status.csv")
    fs["day"] = pd.to_datetime(fs.snapshot).dt.normalize()
    arch: dict[str, set] = {}
    for r in fs.itertuples(index=False):
        for x in str(r.posted_stations).split("|"):
            if x and x != "nan":
                arch.setdefault(x, set()).add(r.day)
    snap_days = set(fs.day)
    hit = tot = 0
    for r in adv[(adv.reopened_on >= "2016-03-19") & (adv.posted_on <= "2017-01-10")].itertuples(index=False):
        for d in pd.date_range(r.posted_on, r.reopened_on):
            if d in snap_days:
                tot += 1
                hit += int(d in arch.get(r.station_name, set()))
    assert tot >= 150 and hit / tot >= 0.90, f"{hit}/{tot}"


def test_manifest_matches_files():
    man = json.loads(BW.MANIFEST.read_text())
    adv = BW.load_advisories()
    days = BW.load_posted_zone_days()
    assert man["sf_rows"] == len(adv)
    assert man["posted_zone_days"] == {z: int((days.zone == z).sum()) for z in man["posted_zone_days"]}
    assert man["span"] == [adv.posted_on.min().strftime("%Y-%m-%d"), adv.reopened_on.max().strftime("%Y-%m-%d")]
    assert "inclusive" in man["convention"]
    assert len(man["excluded_long_advisories"]) == 2 and all(int(float(x["duration_days"])) > BW.MAX_DURATION_DAYS for x in man["excluded_long_advisories"])
    assert BW.load_posted_zone_days().groupby("zone").size().max() < 2000   # no multi-year artifact inflating a zone


if __name__ == "__main__":
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
