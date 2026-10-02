"""Versioned forecast geography: which basin an outfall drains, which zone it
posts, and the links between them (STAGES_DESIGN.md §2; Chase, 2026-10-01).

    basin   SFPUC drainage basin, the unit stage S2 forecasts (P(overflow | rain))
    zone    the public grouping of stations (shared/zones.py), unchanged
    link    an edge (basin, zone): the outfalls of one basin that post one
            zone's stations. Internal only; never public copy.

Two versions, both derived from the registries (shared/outfalls.py,
shared/stations.py, shared/zones.py), never retyped:

    geo_v1     the served geography: our four app basins (Westside, North Shore,
               Central = Mission Creek, Southeast = Islais + Candlestick) and the
               six legacy groups as links (Crissy Field and Aquatic Park share
               north_shore>north). Zone = max of link risk (groups.zone_risks).
               It reproduces features/forecast/src/models/groups.py exactly;
               tests/test_geography.py pins that.
    sfpuc4_v1  SFPUC's four basins from Outfall.report_basin (CITY_BASIN_OF_REPORT):
               Islais Creek is Central, CSD-037/040-043 are South, CSD-119 stays
               North Shore. Five links; zone union 'max' by default.

Why a module and not a registry edit (Part B 11): groups.py and train_v2.py assert
the registry at import, and the served pickles are keyed by GEO_V1 basins, so an
in-place edit of Outfall.basin / Station.basin would silently re-point the live
forecast (Candlestick 0.0, Islais 0.60 vs 0.98). The registries stay GEO_V1; a new
geography is a new version here, and offline tools take ``geo=`` explicitly.

Import cost is nil: this imports only the three registries, reads no file and checks
nothing until a geography is first built (``get`` or the GEO_V1 / SFPUC4_V1 module
attributes), because app/wsgi.py imports the forecast page at load (Part C fix 11).
Data checks (every ledger outfall is in one link, event-day counts) live in the tests.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass

from shared.outfalls import APP_BASINS, OBSERVED, OUTFALLS, Outfall
from shared.stations import STATIONS
from shared.zones import ZONE_OF_STATION, ZONES

VERSIONS = ("geo_v1", "sfpuc4_v1")
MISSING_STAMP = "geo_v1"   # an artifact with no "geography" stamp was built for the served geography (§2.6)

# SFPUC's basins (CCSF23_v2 memo) from the CIWQS report basin — the definition of
# sfpuc4_v1. "South" is the memo's "South Basin", shortened so it doesn't clash with
# CSD-042's receiving water; "Southeast" from now on names the plant only.
CITY_BASIN_OF_REPORT = {"Oceanside": "westside", "North Shore": "north_shore", "Central (Mission Creek)": "central",
                        "Central (Islais Creek)": "central", "Southeast": "south"}
CITY_BASIN_NAMES = {"westside": "Westside", "north_shore": "North Shore", "central": "Central", "south": "South"}

# S2 rain series by treatment-plant permit: the served set's per-basin choice
# (served.json rain_sources: Oceanside basins on the two-gauge mean, Bayside on the
# Downtown gauge) and the sfpuc4_v1 design (§2.3). Values are train_v4.rain_series names.
RAIN_OF_FACILITY = {"Oceanside": "avg", "Bayside": "SF Downtown"}

# geo_v1's legacy group names per (app basin, zone) cell. The names are the served
# artifacts' keys (impact_table.json, stage2.json); which stations and outfalls each
# group holds is derived. North Shore's one cell held two groups: they are the two
# outfall → station components (Marina structures → Crissy; Embarcadero → Aquatic Park /
# Hyde St), named in the zone's station order.
_V1_GROUP_NAMES = {("Westside", "ocean"): ("Ocean Beach",), ("Westside", "baker_china"): ("Baker-China",),
                   ("North Shore", "north"): ("Crissy Field", "Aquatic Park"),
                   ("Central", "east"): ("Mission Creek",), ("Southeast", "east"): ("Southeast",)}


@dataclass(frozen=True)
class Basin:
    key: str                    # pickle / column key, e.g. "north_shore"
    name: str                   # display name, e.g. "North Shore"
    facility: str               # permit whose CIWQS coverage grid says the basin's ledger is known: Oceanside | Bayside
    report_basins: tuple        # CIWQS report basins it holds
    rain_series: str            # default S2 rain series (a set's own pickle stamp wins)


@dataclass(frozen=True)
class Link:
    id: str                     # "westside>ocean"; geo_v1's split cell adds the group: "north_shore>north:crissy_field"
    basin: str                  # basin key
    zone: str                   # zone key
    outfalls: tuple             # outfall ids, registry order
    identity: bool              # the basin's only link: its share of the basin's overflow is 1
    legacy_group: str | None = None   # geo_v1 only: the groups.py group this link is

    @property
    def stations(self) -> tuple:
        """SFPUC ids the link's outfalls post, in the zone's station order."""
        posted = {s for o in self.outfalls for s in OUTFALLS[o].stations}
        return tuple(s for s in ZONES[self.zone].station_ids if s in posted)


@dataclass(frozen=True)
class Geography:
    version: str
    basins: tuple               # Basin, in display order
    links: tuple                # Link, by basin then zone
    zone_union: str             # 'max_of_link_risk' (geo_v1) | 'max' | 'noisy_or' | 'cofire'

    @property
    def keys(self) -> tuple:
        return tuple(b.key for b in self.basins)

    def basin(self, key_or_name: str) -> Basin:
        for b in self.basins:
            if key_or_name in (b.key, b.name):
                return b
        raise KeyError(f"{key_or_name!r} is not a basin of {self.version}")

    def link_of_outfall(self, oid) -> Link:
        oid = getattr(oid, "id", oid)
        for lk in self.links:
            if oid in lk.outfalls:
                return lk
        raise KeyError(f"{oid!r} is in no link of {self.version}")

    def basin_of_outfall(self, oid) -> str:
        return self.link_of_outfall(oid).basin

    def zone_of_outfall(self, oid) -> str:
        return self.link_of_outfall(oid).zone

    def basins_of(self, oids) -> tuple:
        """Basin keys of several outfalls (a feed structure string, an archive onset),
        each once, in display order. Callers split on these, never keep the first."""
        found = {self.basin_of_outfall(o) for o in oids}
        return tuple(k for k in self.keys if k in found)

    def links_into(self, zone: str) -> tuple:
        return tuple(lk for lk in self.links if lk.zone == zone)

    def links_from(self, basin: str) -> tuple:
        return tuple(lk for lk in self.links if lk.basin == basin)

    def station_basin(self, sfpuc_id: str) -> str | None:
        """The basin a station-level observation (an S5 flag or sample) speaks for (§2.5):
        the basin of the observed-evidence outfalls posting it; failing those, of the
        geography-evidence ones if they agree; else None (attribute to the zone only).
        A structure name in the feed is more specific and overrides this (basins_of).
        An id that is no station (a registry key like 'BAY#320_SL') raises: None would
        silently mean "zone only"."""
        if sfpuc_id not in ZONE_OF_STATION:
            raise KeyError(f"{sfpuc_id!r} is not an SFPUC station id")
        posting = [o for o in OUTFALLS.values() if sfpuc_id in o.stations]
        for tier in ([o for o in posting if o.evidence == OBSERVED], posting):
            found = {self.basin_of_outfall(o.id) for o in tier}
            if found:
                return found.pop() if len(found) == 1 else None
        return None


def city_basin(outfall) -> str:
    """SFPUC basin key of an outfall (or id) from its CIWQS report basin. A function,
    not an Outfall property: shared/outfalls.py must not import this module."""
    o = outfall if isinstance(outfall, Outfall) else OUTFALLS[outfall]
    return CITY_BASIN_OF_REPORT[o.report_basin]


def _zone_of(o: Outfall) -> str:
    zones = {ZONE_OF_STATION[s] for s in o.stations}
    if len(zones) != 1:
        raise ValueError(f"{o.id} posts stations in zones {sorted(zones)}; a link needs exactly one")
    return zones.pop()


def _basins(basin_of: dict, names: dict, outfalls: dict) -> tuple:
    """Basin rows from an outfall → basin key map: facility, report basins and rain all
    read off the outfalls, in first-seen order of `names`."""
    out = []
    for key, name in names.items():
        members = [o for o in outfalls.values() if basin_of[o.id] == key]
        facilities = {o.facility for o in members}
        if len(facilities) != 1:
            raise ValueError(f"basin {key} spans facilities {sorted(facilities)}")
        fac = facilities.pop()
        out.append(Basin(key, name, fac, tuple(dict.fromkeys(o.report_basin for o in members)), RAIN_OF_FACILITY[fac]))
    return tuple(out)


def _links(cells: dict, basins: tuple) -> tuple:
    """cells: (basin key, zone, legacy group or None) → outfall ids. Identity = the basin's only link."""
    fan = {b.key: sum(1 for (bk, _, _) in cells if bk == b.key) for b in basins}
    order = {b.key: i for i, b in enumerate(basins)}
    zorder = {z: i for i, z in enumerate(ZONES)}
    shared = {(b, z) for (b, z, _) in cells if sum(1 for (bb, zz, _) in cells if (bb, zz) == (b, z)) > 1}
    out = []
    for (bk, zk, group), oids in sorted(cells.items(), key=lambda kv: (order[kv[0][0]], zorder[kv[0][1]])):
        lid = f"{bk}>{zk}:{group.lower().replace(' ', '_')}" if (bk, zk) in shared else f"{bk}>{zk}"
        out.append(Link(lid, bk, zk, tuple(oids), fan[bk] == 1, group))
    return tuple(out)


def build_sfpuc4(outfalls: dict | None = None) -> Geography:
    """sfpuc4_v1 from Outfall.report_basin alone (Outfall.basin is never read; the test
    scrambles it to prove that). `outfalls` defaults to the registry."""
    outfalls = OUTFALLS if outfalls is None else outfalls
    basin_of = {o.id: CITY_BASIN_OF_REPORT[o.report_basin] for o in outfalls.values()}
    names = {k: CITY_BASIN_NAMES[k] for k in dict.fromkeys(CITY_BASIN_OF_REPORT.values())}
    basins = _basins(basin_of, names, outfalls)
    cells = {}
    for o in outfalls.values():
        cells.setdefault((basin_of[o.id], _zone_of(o), None), []).append(o.id)
    return Geography("sfpuc4_v1", basins, _links(cells, basins), "max")


def build_geo_v1(outfalls: dict | None = None, stations: dict | None = None) -> Geography:
    """geo_v1 as groups.py draws it: a group is a (Station.basin, zone) cell of stations
    (North Shore's cell split into its outfall → station components); a group's outfalls
    are the ones posting its stations, and an outfall's basin is its group's. So
    basin_of_outfall comes from the stations' basins, and tests/test_geography.py checking
    it against Outfall.basin catches an in-place edit of either registry."""
    outfalls = OUTFALLS if outfalls is None else outfalls
    for o in outfalls.values():
        _zone_of(o)   # one posting two zones would sit in two links, and link_of_outfall would keep the first
    by_id = {s.sfpuc_id: s for s in (STATIONS if stations is None else stations).values()}
    key = {b: b.lower().replace(" ", "_") for b in APP_BASINS}          # "North Shore" → "north_shore" (groups.BASIN_KEYS)
    cells = {}
    for zk, z in ZONES.items():
        for sid in z.station_ids:
            cells.setdefault((by_id[sid].basin, zk), []).append(sid)
    links, basin_of = {}, {}
    for (bname, zk), sids in cells.items():
        groups = _V1_GROUP_NAMES[(bname, zk)]
        posting = [o for o in outfalls.values() if set(o.stations) & set(sids)]
        comps = _components(sids, posting) if len(groups) > 1 else [sids]
        if len(comps) != len(groups):
            raise ValueError(f"cell {bname}/{zk}: {len(comps)} station components for groups {groups}")
        for group, comp in zip(groups, comps):
            oids = [o.id for o in posting if set(o.stations) & set(comp)]
            links[(key[bname], zk, group)] = oids
            for oid in oids:
                if basin_of.setdefault(oid, key[bname]) != key[bname]:
                    raise ValueError(f"{oid} posts stations of basins {basin_of[oid]} and {key[bname]}")
    if set(basin_of) != set(outfalls):
        raise ValueError(f"outfalls posting no station: {sorted(set(outfalls) - set(basin_of))}")
    basins = _basins(basin_of, {k: b for b, k in key.items()}, outfalls)
    return Geography("geo_v1", basins, _links(links, basins), "max_of_link_risk")


def _components(sids: list, posting: list) -> list:
    """Stations of one cell joined when an outfall posts both, ordered by first station."""
    comp = {s: {s} for s in sids}
    for o in posting:
        hit = [s for s in sids if s in o.stations]
        merged = set().union(*(comp[s] for s in hit)) if hit else set()
        for s in merged:
            comp[s] = merged
    seen, out = set(), []
    for s in sids:
        if s not in seen:
            out.append([t for t in sids if t in comp[s]])
            seen |= comp[s]
    return out


@functools.lru_cache(maxsize=None)
def get(version: str) -> Geography:
    """The geography a version string names. KeyError on anything else: there is no default."""
    builders = {"geo_v1": build_geo_v1, "sfpuc4_v1": build_sfpuc4}
    if version not in builders:
        raise KeyError(f"unknown geography {version!r}; known: {VERSIONS}")
    return builders[version]()


def stamped(artifact: dict) -> Geography:
    """The geography an artifact (pickle dict, manifest) was built for; no stamp = geo_v1."""
    return get(artifact["geography"] if "geography" in artifact else MISSING_STAMP)


def __getattr__(name: str):
    """GEO_V1 / SFPUC4_V1 as module attributes, built on first use (PEP 562)."""
    if name == "GEO_V1":
        return get("geo_v1")
    if name == "SFPUC4_V1":
        return get("sfpuc4_v1")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
