"""The CSO outfall registry (shared/outfalls.py) must agree with the data it
describes: every outfall in the CIWQS event records, every station id in the
station registry, every feed name unique, and the consumers derived from it.

    venv/bin/python tests/test_outfalls.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.outfalls import (APP_BASINS, BASIN_OUTFALLS, FEED_NAME_TO_OUTFALLS,  # noqa: E402
                             OUTFALLS, REPORT_BASIN_TO_APP, STATION_OUTFALLS,
                             stations_for_feed_name)
from shared.stations import STATIONS  # noqa: E402

EVENTS = Path(__file__).resolve().parents[1] / "features/forecast/data/csd/sf_csd_events.csv"


def test_every_ciwqs_outfall_is_in_the_registry():
    with open(EVENTS, newline="") as fh:
        rows = list(csv.DictReader(fh))
    ids = {r["outfall_id"] for r in rows}
    assert ids <= set(OUTFALLS), ids - set(OUTFALLS)
    for r in rows:
        o = OUTFALLS[r["outfall_id"]]
        assert o.report_basin == r["basin"], (o.id, o.report_basin, r["basin"])
        assert o.name == r["outfall_name"], (o.id, o.name, r["outfall_name"])


def test_stations_and_basins_are_valid():
    ids = {s.sfpuc_id for s in STATIONS.values()}
    for o in OUTFALLS.values():
        assert o.stations, o.id
        assert set(o.stations) <= ids, (o.id, o.stations)
        assert o.basin in APP_BASINS, (o.id, o.basin)
        assert o.facility in ("Oceanside", "Bayside"), o.id
        assert 37.70 < o.lat < 37.82 and -122.52 < o.lon < -122.36, (o.id, o.lat, o.lon)
    assert set(REPORT_BASIN_TO_APP.values()) <= set(APP_BASINS)
    # every station is reachable from at least one outfall
    assert all(STATION_OUTFALLS[sid] for sid in ids), [sid for sid in ids if not STATION_OUTFALLS[sid]]


def test_feed_names_resolve_to_the_observed_stations():
    # the 2016-17 archive's clearest signals
    assert set(stations_for_feed_name("SEA CLIFF II")) == {"4608", "4609", "4610"}
    assert set(stations_for_feed_name("ISLAIS CREEK")) == {"4619"}
    assert set(stations_for_feed_name("YOSEMITE")) == {"4615", "4616", "4617"}
    assert set(stations_for_feed_name("SUNNYDALE")) == {"4615", "4616", "4617"}
    assert set(stations_for_feed_name("MISSION CREEK")) == {"4618"}
    assert "4601" in stations_for_feed_name("LAKE MERCED")
    assert stations_for_feed_name("NOT A STRUCTURE") == []
    # feed names are case/space tolerant
    assert stations_for_feed_name(" sea cliff ii ") == stations_for_feed_name("SEA CLIFF II")


def test_sfpuc_api_consumers_derive_from_registry():
    from shared import sfpuc_api
    assert set(sfpuc_api.CSO_OUTFALLS) == set(OUTFALLS)
    api = sfpuc_api.SFPUCRealTimeAPI()
    assert api._get_cso_outfalls("Baker Beach at Lobos Creek") == sorted(STATION_OUTFALLS["4610"])
    assert "CSD-007" in api._get_cso_outfalls("Baker Beach at Lobos Creek")
    assert "CSD-004" not in api._get_cso_outfalls("Fort Funston")   # the old Mile Rock error
    assert api._get_cso_outfalls("Ocean Beach at Sloat Boulevard") == sorted(STATION_OUTFALLS["4602"])
    assert api._get_cso_outfalls("Nowhere Beach") == []


def test_discharges_page_locations_come_from_registry():
    from features.discharges import page
    payload = page._load()
    locs = payload["locations"]
    assert set(locs) == set(OUTFALLS)
    assert locs["CSD-007"]["stations"] == OUTFALLS["CSD-007"].station_names
    assert {r[2] for r in payload["events"]} <= set(locs)


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
