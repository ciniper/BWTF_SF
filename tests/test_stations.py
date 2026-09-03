#!/usr/bin/env python3
"""Guard the canonical station registry (shared/stations.py).

The registry is the single source of truth for DataSF source id → beach
name/group/basin. Before it existed, three hand-typed copies (site_analysis,
alerts/monitoring, forecast/live_dashboard) disagreed and lab samples showed
under the wrong beach names — a public-health labeling bug. This test pins
the registry to the ground truth verified 2026-09-02 (SFPUC getBeaches
coordinates + the 2026-08-31 Windsurfer Circle 228 MPN event) so a bad edit
can't quietly reintroduce drift.

    venv/bin/python tests/test_stations.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.stations import SFPUC_TO_SFGOV_SOURCES, STATION_BASINS, STATION_NAMES, STATIONS

# The 20 distinct ``source`` ids in DataSF dataset v3fv-x3ux → display name.
# An independent copy of the ground-truth table, NOT derived from the
# registry — that's the point.
EXPECTED_NAMES = {
    "OCEAN#15_SL": "Baker Beach at Lobos Creek",
    "OCEAN#15EAST_SL": "Baker Beach East",
    "OCEAN#16_SL": "Baker Beach West",
    "OCEAN#17_SL": "China Beach",
    "OCEAN#18_SL": "Ocean Beach at Balboa",
    "OCEAN#19_SL": "Ocean Beach at Lincoln",
    "OCEAN#20_SL": "Ocean Beach at Pacheco",
    "OCEAN#21_SL": "Ocean Beach at Vicente",
    "OCEAN#21.1_SL": "Ocean Beach at Sloat",
    "OCEAN#22_SL": "Fort Funston",
    "BAY#202.4_SL": "Crissy Field East",
    "BAY#202.5_SL": "Crissy Field West",
    "BAY#210.1_SL": "Hyde Street Pier",
    "BAY#211_SL": "Aquatic Park",
    "BAY#220_SL": "Mission Creek",
    "BAY#230_SL": "Crane Cove Park",
    "BAY#300.1_SL": "Sunnydale Cove",
    "BAY#301.1_SL": "Windsurfer Circle",
    "BAY#301.2_SL": "Jackrabbit Beach",
    "BAY#320_SL": "Islais Creek",
}

# Exact station name strings in SFPUC's getBeaches feed (id → name).
EXPECTED_SFPUC = {
    "4601": "Fort Funston",
    "4602": "Ocean Beach at Sloat Boulevard",
    "4603": "Ocean Beach at Vicente Street",
    "4604": "Ocean Beach at Balboa Street",
    "4605": "Ocean Beach at Lincoln Way",
    "4606": "Ocean Beach at Pacheco Street",
    "4607": "China Beach",
    "4608": "Baker Beach West",
    "4609": "Baker Beach East",
    "4610": "Baker Beach at Lobos Creek",
    "4611": "Crissy Field Beach West",
    "4612": "Crissy Field Beach East",
    "4613": "Aquatic Park",
    "4614": "Hyde Street Pier",
    "4615": "Jackrabbit Beach",
    "4616": "Windsurfer Circle",
    "4617": "Sunnydale Cove",
    "4618": "Mission Creek",
    "4619": "Islais Creek",
    "4620": "Crane Cove Park",
}


def test_covers_exactly_the_20_dataset_sources():
    assert set(STATIONS) == set(EXPECTED_NAMES)


def test_display_names_match_ground_truth():
    assert STATION_NAMES == EXPECTED_NAMES


def test_sfpuc_feed_ids_and_names_match_ground_truth():
    assert {s.sfpuc_id: s.sfpuc_name for s in STATIONS.values()} == EXPECTED_SFPUC


def test_sfpuc_to_sfgov_mapping_is_one_to_one_over_feed_names():
    assert set(SFPUC_TO_SFGOV_SOURCES) == set(EXPECTED_SFPUC.values())
    sources = [sid for ids in SFPUC_TO_SFGOV_SOURCES.values() for sid in ids]
    assert sorted(sources) == sorted(STATIONS)  # every source once, none invented
    # The known regression: Baker Beach at Lobos Creek must resolve to
    # OCEAN#15_SL, and Windsurfer Circle to BAY#301.1_SL (2026-08-31 event).
    assert SFPUC_TO_SFGOV_SOURCES["Baker Beach at Lobos Creek"] == ["OCEAN#15_SL"]
    assert SFPUC_TO_SFGOV_SOURCES["Windsurfer Circle"] == ["BAY#301.1_SL"]


def test_groups_and_basins():
    for sid, s in STATIONS.items():
        assert s.group in {"Ocean", "North Shore", "East Bayshore"}, sid
        assert s.basin in {"Westside", "North Shore", "Southeast"}, sid
        # Ocean-facing stations drain Westside; bay stations never do.
        if sid.startswith("OCEAN#"):
            assert s.group == "Ocean" and s.basin == "Westside", sid
        else:
            assert s.group != "Ocean" and s.basin != "Westside", sid
    assert STATION_BASINS == {sid: s.basin for sid, s in STATIONS.items()}


def test_consumers_use_the_registry():
    from features.alerts import monitoring
    from features.site_analysis import page as site_analysis

    assert monitoring.STATION_NAMES is STATION_NAMES
    assert monitoring.SFPUC_TO_SFGOV_SOURCES is SFPUC_TO_SFGOV_SOURCES
    assert monitoring.STATION_DRAINAGE_BASINS is STATION_BASINS
    assert set(monitoring.BWTF_PRIORITY_SITES) <= set(STATIONS)
    assert site_analysis.STATIONS == {
        sid: (s.name, s.group, s.lat, s.lon) for sid, s in STATIONS.items()
    }


def test_forecast_training_tables_use_the_registry():
    # The forecast v3 retrain (2026-09) rebuilt these from the registry —
    # before that they carried the same wrong basins the app did (BAY#220
    # Mission Creek as "Baker Beach"/Westside, phantom ids like BAY#315_SL).
    forecast_src = Path(__file__).resolve().parents[1] / "features" / "forecast" / "src"
    sys.path.insert(0, str(forecast_src / "collectors"))
    sys.path.insert(0, str(forecast_src / "models"))
    import historical
    import train
    import train_v2

    assert historical.STATION_BASINS is STATION_BASINS
    assert train.STATION_BASINS is STATION_BASINS

    # Stage-2 site groups: every member must exist in the registry with the
    # group's parent basin, and together the groups cover every station
    # except BAY#220_SL — Mission Creek drains the Central basin, which has
    # no stage-1 model (its SFPUC id 4618 is likewise unmapped below).
    covered = set()
    for group, (basin, sids) in train_v2.SITE_GROUPS.items():
        for sid in sids:
            assert STATIONS[sid].basin == basin, (group, sid)
        covered.update(sids)
    assert covered == set(STATIONS) - {"BAY#220_SL"}


def test_forecast_observed_station_basins_match_registry():
    from features.forecast import live_dashboard

    key = {"Westside": "westside", "North Shore": "north_shore",
           "Southeast": "southeast"}
    expected = {s.sfpuc_id: key[s.basin]
                for s in STATIONS.values() if s.sfpuc_id != "4618"}
    assert live_dashboard.OBSERVED_STATION_BASIN == expected

    # The composition's group names must exist in the trained impact table.
    table_groups = set(live_dashboard.LIVE.impact_table)
    for groups in live_dashboard.LiveData.BASIN_IMPACT_GROUPS.values():
        assert set(groups) <= table_groups, (groups, table_groups)


def test_sfpuc_api_basins_match_registry():
    from shared import sfpuc_api

    assert sfpuc_api.STATION_NAME_BASINS == {
        s.sfpuc_name: s.basin for s in STATIONS.values()
    }
    api = sfpuc_api.SFPUCRealTimeAPI()
    for s in STATIONS.values():
        assert api._get_drainage_basin(s.sfpuc_name) == s.basin, s.sfpuc_name
    # Keyword fallbacks must never contradict the registry for feed names.
    for kw, basin in sfpuc_api.BEACH_DRAINAGE_BASINS.items():
        matched = {s.basin for s in STATIONS.values()
                   if kw.lower() in s.sfpuc_name.lower()}
        assert matched <= {basin}, kw


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print("PASS" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
