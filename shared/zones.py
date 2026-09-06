"""Canonical alert/forecast zones — the product decision of which stations
form a zone, kept in one place.

Zones are what people sign up for (/signup) and what the forecast presents
first (/forecast "Your beaches"). Station facts (names, ids, coordinates,
basins) come from shared/stations.py; this module only groups them.

    ocean        Ocean Beach          6 stations  Fort Funston → Pacheco
    baker_china  Baker & China Beach  4 stations  China, Baker W/E, Lobos Creek
    north        North Beaches        4 stations  Crissy W/E, Aquatic Park, Hyde St Pier
    east         East Beaches         6 stations  Candlestick trio, Mission Creek,
                                                  Islais Creek, Crane Cove

Every registry station is in exactly one zone (pinned by tests/test_stations.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.stations import STATIONS, Station

_BY_SFPUC_ID = {s.sfpuc_id: sid for sid, s in STATIONS.items()}


@dataclass(frozen=True)
class Zone:
    key: str
    label: str
    station_ids: tuple  # SFPUC LIMS numeric ids (what subscriptions store)

    @property
    def source_ids(self) -> tuple:
        """Registry keys (DataSF `source` ids, e.g. OCEAN#15_SL)."""
        return tuple(_BY_SFPUC_ID[sid] for sid in self.station_ids)

    @property
    def stations(self) -> tuple:
        return tuple(STATIONS[_BY_SFPUC_ID[sid]] for sid in self.station_ids)

    @property
    def basins(self) -> tuple:
        return tuple(sorted({s.basin for s in self.stations}))


ZONES = {
    "ocean": Zone("ocean", "Ocean Beach", ("4601", "4602", "4603", "4604", "4605", "4606")),
    "baker_china": Zone("baker_china", "Baker & China Beach", ("4607", "4608", "4609", "4610")),
    "north": Zone("north", "North Beaches", ("4611", "4612", "4613", "4614")),
    "east": Zone("east", "East Beaches", ("4615", "4616", "4617", "4618", "4619", "4620")),
}

ZONE_OF_STATION = {sid: z.key for z in ZONES.values() for sid in z.station_ids}   # sfpuc id → zone key
ZONE_OF_SOURCE = {src: z.key for z in ZONES.values() for src in z.source_ids}     # registry key → zone key

assert set(ZONE_OF_STATION) == {s.sfpuc_id for s in STATIONS.values()}, "zones must cover every station once"
assert len(ZONE_OF_STATION) == sum(len(z.station_ids) for z in ZONES.values()), "a station is in two zones"


def zone_for_station(station: Station) -> Zone:
    return ZONES[ZONE_OF_STATION[station.sfpuc_id]]
