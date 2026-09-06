"""Forecast geography shared by training (train_v4) and serving (live_dashboard).

    basin  → stage-1 model (P(discharge | rain)); keys are the pickle names
    group  → stage-2 beach response (impact table); beaches within one basin
             sit in different flushing regimes
    zone   → what people sign up for (shared/zones.py); unions of groups

Everything is validated against the station registry at import: every
registry station is in exactly one group, every zone is exactly the union of
its groups.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from shared.outfalls import APP_BASINS  # noqa: E402
from shared.stations import STATIONS  # noqa: E402
from shared.zones import ZONES  # noqa: E402

BASIN_KEYS = {"Westside": "westside", "North Shore": "north_shore",
              "Central": "central", "Southeast": "southeast"}
assert list(BASIN_KEYS) == list(APP_BASINS), (list(BASIN_KEYS), list(APP_BASINS))
KEY_TO_BASIN = {v: k for k, v in BASIN_KEYS.items()}

# group → (parent basin, registry station ids)
SITE_GROUPS = {
    "Ocean Beach": ("Westside", ["OCEAN#18_SL", "OCEAN#19_SL", "OCEAN#20_SL",
                                 "OCEAN#21_SL", "OCEAN#21.1_SL", "OCEAN#22_SL"]),
    "Baker-China": ("Westside", ["OCEAN#15_SL", "OCEAN#15EAST_SL", "OCEAN#16_SL", "OCEAN#17_SL"]),
    "Crissy Field": ("North Shore", ["BAY#202.4_SL", "BAY#202.5_SL"]),
    "Aquatic Park": ("North Shore", ["BAY#210.1_SL", "BAY#211_SL"]),
    "Mission Creek": ("Central", ["BAY#220_SL"]),
    "Southeast": ("Southeast", ["BAY#230_SL", "BAY#300.1_SL", "BAY#301.1_SL",
                                "BAY#301.2_SL", "BAY#320_SL"]),
}
assert all(STATIONS[sid].basin == basin for basin, sids in SITE_GROUPS.values() for sid in sids), \
    "SITE_GROUPS disagrees with the shared/stations.py registry"
assert sorted(sid for _, sids in SITE_GROUPS.values() for sid in sids) == sorted(STATIONS), \
    "SITE_GROUPS must cover every registry station exactly once"

GROUPS_BY_BASIN = {BASIN_KEYS[b]: [g for g, (gb, _) in SITE_GROUPS.items() if gb == b] for b in APP_BASINS}
GROUP_OF_STATION = {sid: g for g, (_, sids) in SITE_GROUPS.items() for sid in sids}

# zone key (shared/zones.py) → groups
ZONE_GROUPS = {"ocean": ["Ocean Beach"], "baker_china": ["Baker-China"],
               "north": ["Crissy Field", "Aquatic Park"], "east": ["Southeast", "Mission Creek"]}
assert set(ZONE_GROUPS) == set(ZONES)
for _zk, _groups in ZONE_GROUPS.items():
    assert set(ZONES[_zk].source_ids) == {sid for g in _groups for sid in SITE_GROUPS[g][1]}, _zk

# SFPUC LIMS numeric id → basin key (alert_log station_ids are LIMS ids)
OBSERVED_STATION_BASIN = {s.sfpuc_id: BASIN_KEYS[s.basin] for s in STATIONS.values()}


def zone_risks(group_risks: dict) -> dict:
    """Zone risk = worst of its groups (a zone is bad if any beach in it is)."""
    return {zk: max((group_risks.get(g, 0.0) for g in groups), default=0.0) for zk, groups in ZONE_GROUPS.items()}
