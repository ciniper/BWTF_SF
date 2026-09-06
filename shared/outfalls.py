"""Canonical registry of San Francisco's combined-sewer discharge (CSO) outfalls.

One row per permitted discharge structure: its CIWQS id and name, the name
SFPUC's LIMS feed uses when the structure is actively discharging (the
``cso`` field of getBeaches / getCSV), which combined-sewer basin it drains,
where it discharges, and — the part every consumer cares about — WHICH
MONITORING STATIONS SFPUC POSTS when it fires.

Evidence for the station mapping (2026-09-05):
  * "observed 2016-17 feed": John Brandon's Beach_Poo_Bot archived SFPUC's
    getCSV feed twice daily, Mar 2016 – Jan 2017 (552 snapshots). Across the
    55 snapshots with an active CSO flag, the stations SFPUC posted at the
    same time give a direct structure → station association, e.g. SEA CLIFF
    II (CSD-007) posts Lobos Creek 100%, Baker East 86%, Baker West 86%,
    never China Beach; YOSEMITE/SUNNYDALE post the Candlestick trio 100%;
    ISLAIS CREEK posts only Islais Creek (93%). The Marina and Embarcadero
    structures only ever fired together (Dec 2016), so within North Shore the
    split is by geography.
  * "permit + geography": structures that never fired in the archive
    (Mile Rock, Sea Cliff #1/Brick Sewer, Mariposa/20th/22nd, Evans Ave,
    Fitch St, Howard St): nearest station(s) by distance, checked against the
    NPDES permit's receiving-water text.
  * Every Bayside flag-day in the archive matched a CIWQS-reported discharge on
    the same or previous day, so the feed is a trustworthy discharge signal;
    its flag persists 2–4 days after the discharge (SFPUC's advisory window).

Coordinates: NPDES permit discharge-location tables (Oceanside R2-2019 Table
2; Bayside R2-2013-0029 Table 2) with two documented exceptions (see notes).

Consumers: shared/sfpuc_api.py (CSO_OUTFALLS / BEACH_CSO_OUTFALLS /
_get_cso_outfalls), features/discharges (map + popups), the forecast label
builder (report-basin → app-basin), tests/test_outfalls.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shared.stations import STATIONS

OBSERVED = "observed 2016-17 feed"
GEOGRAPHY = "permit + geography"


@dataclass(frozen=True)
class Outfall:
    id: str                      # CIWQS / permit id, e.g. "CSD-007"
    name: str                    # CIWQS outfall_name, e.g. "Sea Cliff #2"
    feed_name: Optional[str]     # SFPUC LIMS `cso` string when active (None = never seen)
    facility: str                # "Oceanside" | "Bayside"
    report_basin: str            # CIWQS basin string as written in the SMRs
    basin: str                   # app basin: Westside | North Shore | Central | Southeast
    receiving_water: str
    lat: float
    lon: float
    stations: tuple             # SFPUC station ids posted when this structure discharges
    evidence: str
    note: str = ""

    @property
    def station_names(self) -> list:
        return [_BY_ID[s].name for s in self.stations]


_BY_ID = {s.sfpuc_id: s for s in STATIONS.values()}

# Station id shorthands (see shared/stations.py)
FT_FUNSTON, SLOAT, VICENTE, BALBOA, LINCOLN, PACHECO = "4601", "4602", "4603", "4604", "4605", "4606"
CHINA, BAKER_W, BAKER_E, LOBOS = "4607", "4608", "4609", "4610"
CRISSY_W, CRISSY_E, AQUATIC, HYDE = "4611", "4612", "4613", "4614"
JACKRABBIT, WINDSURFER, SUNNYDALE, MISSION, ISLAIS, CRANE = "4615", "4616", "4617", "4618", "4619", "4620"
CANDLESTICK = (JACKRABBIT, WINDSURFER, SUNNYDALE)

OUTFALLS = {o.id: o for o in [
    # ── Oceanside (Westside basin) → Pacific Ocean ─────────────────────────
    Outfall("CSD-001", "Lake Merced", "LAKE MERCED", "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (Fort Funston, Ocean Beach)", 37.71528, -122.50444,
            (FT_FUNSTON, SLOAT), OBSERVED,
            "Fort Funston posted on 68% of active snapshots; the three Ocean Beach structures "
            "fired together in every 2016-17 storm, so the OB split among them is by geography."),
    Outfall("CSD-002", "Vicente", "VICENTE", "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (Vicente St., Ocean Beach)", 37.73778, -122.50806,
            (VICENTE, PACHECO, SLOAT), OBSERVED),
    Outfall("CSD-003", "Lincoln", "LINCOLN", "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (Lincoln Way, Ocean Beach)", 37.76389, -122.51167,
            (LINCOLN, BALBOA, PACHECO), OBSERVED),
    Outfall("CSD-004", "Mile Rock", None, "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (Mile Rock)", 37.78472, -122.51028,
            (CHINA,), GEOGRAPHY,
            "Lands End; never fired in the 2016-17 archive. Nearest station is China Beach (1.7 km). "
            "Previously (wrongly) mapped to Ocean Beach / Fort Funston."),
    Outfall("CSD-005", "Sea Cliff #1", None, "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (China Beach)", 37.78778, -122.49167,
            (CHINA,), GEOGRAPHY, "Discharges at China Beach per permit."),
    Outfall("CSD-006", "Sea Cliff Brick Sewer", None, "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (Baker Beach)", 37.78944, -122.48778,
            (LOBOS, BAKER_W), GEOGRAPHY),
    Outfall("CSD-007", "Sea Cliff #2", "SEA CLIFF II", "Oceanside", "Oceanside", "Westside",
            "Pacific Ocean (Baker Beach)", 37.78944, -122.48694,
            (LOBOS, BAKER_E, BAKER_W), OBSERVED,
            "n=14 active snapshots: Lobos Creek 100%, Baker East 86%, Baker West 86%, China Beach 0%."),
    # ── Bayside: North Shore basin → SF Bay north waterfront ───────────────
    Outfall("CSD-009", "Baker Street", "BAKER STREET CSD09", "Bayside", "North Shore", "North Shore",
            "Marina Beach (North Shore basin)", 37.808056, -122.446667,
            (CRISSY_E, CRISSY_W), OBSERVED,
            "Marina structures (009/010/011) fired only together with the Embarcadero ones (Dec 2016), "
            "when all four North Shore stations posted; Marina vs Embarcadero split is by geography."),
    Outfall("CSD-010", "Pierce Street", "PIERCE STREET CSD10", "Bayside", "North Shore", "North Shore",
            "Marina Beach (North Shore basin)", 37.806944, -122.440000,
            (CRISSY_E, CRISSY_W), OBSERVED),
    Outfall("CSD-011", "Laguna Street", "LAGUNA STREET CSD11", "Bayside", "North Shore", "North Shore",
            "Yacht Harbor #2 (North Shore basin)", 37.811667, -122.43189,
            (CRISSY_E, CRISSY_W), OBSERVED),
    Outfall("CSD-013", "Beach Street", "BEACH STREET CSD13", "Bayside", "North Shore", "North Shore",
            "Pier 39 (North Shore basin)", 37.808333, -122.406667,
            (AQUATIC, HYDE), OBSERVED),
    Outfall("CSD-015", "Sansome Street", "SANSOME STREET CSD15", "Bayside", "North Shore", "North Shore",
            "Pier 31 (North Shore basin)", 37.806667, -122.403056,
            (AQUATIC, HYDE), OBSERVED),
    Outfall("CSD-017", "Jackson Street", None, "Bayside", "North Shore", "North Shore",
            "Pier 3 (North Shore basin)", 37.798333, -122.39472,
            (HYDE, AQUATIC), GEOGRAPHY, "Fired in CIWQS records but no feed name seen in the archive."),
    Outfall("CSD-119", "Baker Street", None, "Bayside", "North Shore", "North Shore",
            "Marina Beach (North Shore basin)", 37.808056, -122.446667,
            (CRISSY_E, CRISSY_W), GEOGRAPHY, "Same location as CSD-009 — id appears in some SMR tables."),
    # ── Bayside: Central basin (Mission Creek / China Basin) ───────────────
    Outfall("CSD-018", "Howard Street", None, "Bayside", "Central (Mission Creek)", "Central",
            "Pier 14 (Central basin)", 37.793056, -122.390000,
            (MISSION,), GEOGRAPHY, "Embarcadero south of the Bay Bridge; no station within 2 km — "
            "grouped with Mission Creek per the CIWQS basin."),
    Outfall("CSD-022", "Third Street", "MISSION CREEK", "Bayside", "Central (Mission Creek)", "Central",
            "Mission Creek", 37.777222, -122.389444, (MISSION,), OBSERVED),
    Outfall("CSD-023", "Fourth Street North", "MISSION CREEK", "Bayside", "Central (Mission Creek)", "Central",
            "Mission Creek", 37.775556, -122.391389, (MISSION,), OBSERVED),
    Outfall("CSD-024", "Fifth Street North", "MISSION CREEK", "Bayside", "Central (Mission Creek)", "Central",
            "Mission Creek", 37.773889, -122.393889, (MISSION,), OBSERVED),
    Outfall("CSD-025", "Sixth Street North", "MISSION CREEK", "Bayside", "Central (Mission Creek)", "Central",
            "Mission Creek", 37.771944, -122.396111, (MISSION,), OBSERVED),
    Outfall("CSD-026", "Division Street", "MISSION CREEK", "Bayside", "Central (Mission Creek)", "Central",
            "Mission Creek", 37.770278, -122.397500, (MISSION,), OBSERVED,
            "The feed reports the Mission Creek structures as one 'MISSION CREEK' flag; the Mission Creek "
            "station was posted on 47% of active snapshots."),
    Outfall("CSD-027", "Sixth Street South", "MISSION CREEK", "Bayside", "Central (Mission Creek)", "Central",
            "Mission Creek", 37.771389, -122.395000, (MISSION,), OBSERVED),
    # ── Bayside: Central (Islais Creek) → app Southeast ─────────────────────
    Outfall("CSD-029", "Mariposa Street", None, "Bayside", "Central (Islais Creek)", "Southeast",
            "Central Basin", 37.764722, -122.385278, (CRANE,), GEOGRAPHY,
            "Permit prints the latitude without a decimal (37764722); decimal restored. Crane Cove Park "
            "station (150 m) did not exist in 2016, so no observation."),
    Outfall("CSD-030", "20th Street", None, "Bayside", "Central (Islais Creek)", "Southeast",
            "Central Basin", 37.761111, -122.380000, (CRANE,), GEOGRAPHY),
    Outfall("CSD-030A", "22nd Street", None, "Bayside", "Central (Islais Creek)", "Southeast",
            "Central Basin", 37.757778, -122.380278, (CRANE,), GEOGRAPHY),
    Outfall("CSD-031", "Third Street North", "ISLAIS CREEK", "Bayside", "Central (Islais Creek)", "Southeast",
            "Islais Creek", 37.747778, -122.386111, (ISLAIS,), OBSERVED),
    Outfall("CSD-031A", "Islais Creek North", "ISLAIS CREEK", "Bayside", "Central (Islais Creek)", "Southeast",
            "Islais Creek", 37.747778, -122.387500, (ISLAIS,), OBSERVED,
            "n=44 active snapshots: Islais Creek posted 93%; no other station attributable."),
    Outfall("CSD-032", "Marin Street", "ISLAIS CREEK", "Bayside", "Central (Islais Creek)", "Southeast",
            "Islais Creek", 37.748611, -122.390833, (ISLAIS,), OBSERVED),
    Outfall("CSD-033", "Selby Street", "ISLAIS CREEK", "Bayside", "Central (Islais Creek)", "Southeast",
            "Islais Creek", 37.747778, -122.390833, (ISLAIS,), OBSERVED),
    Outfall("CSD-035", "Third Street South", "ISLAIS CREEK", "Bayside", "Central (Islais Creek)", "Southeast",
            "Islais Creek", 37.747222, -122.386111, (ISLAIS,), OBSERVED),
    # ── Bayside: Southeast basin → SF Bay (India Basin, Yosemite/South Basin, Candlestick) ──
    Outfall("CSD-037", "Evans Avenue", None, "Bayside", "Southeast", "Southeast",
            "India Basin", 37.735833, -122.373889, (ISLAIS,), GEOGRAPHY,
            "India Basin has no monitoring station; nearest is Islais Creek (1.8 km)."),
    Outfall("CSD-040", "Griffith Street", "YOSEMITE", "Bayside", "Southeast", "Southeast",
            "Yosemite Creek", 37.723056, -122.382222, CANDLESTICK, OBSERVED,
            "YOSEMITE flag: Windsurfer Circle, Sunnydale Cove, Jackrabbit Beach posted 100% (n=6)."),
    Outfall("CSD-041", "Yosemite Avenue", "YOSEMITE", "Bayside", "Southeast", "Southeast",
            "Yosemite Creek", 37.723889, -122.385556, CANDLESTICK, OBSERVED),
    Outfall("CSD-042", "Fitch Street", None, "Bayside", "Southeast", "Southeast",
            "South Basin", 37.722222, -122.381944, CANDLESTICK, GEOGRAPHY,
            "Fired alongside Yosemite/Sunnydale on 2017-01-10; Candlestick trio posted."),
    Outfall("CSD-043", "Sunnydale Avenue", "SUNNYDALE", "Bayside", "Southeast", "Southeast",
            "Candlestick Cove", 37.7096, -122.3899, CANDLESTICK, OBSERVED,
            "Approximate location: permit Table 2 duplicates CSD-002's coordinates (typo); anchored at the "
            "Sunnydale Cove station by the Sunnydale Transport/Storage structure."),
]}

# ── Derived views ─────────────────────────────────────────────────────────────
APP_BASINS = ("Westside", "North Shore", "Central", "Southeast")

# CIWQS report basin → app basin (what csd_labels.BASIN_MAP must agree with)
REPORT_BASIN_TO_APP = {}
for _o in OUTFALLS.values():
    REPORT_BASIN_TO_APP.setdefault(_o.report_basin, _o.basin)
    assert REPORT_BASIN_TO_APP[_o.report_basin] == _o.basin, _o.id

# SFPUC LIMS feed structure name → outfall ids it stands for (many-to-one for creeks)
FEED_NAME_TO_OUTFALLS = {}
for _o in OUTFALLS.values():
    if _o.feed_name:
        FEED_NAME_TO_OUTFALLS.setdefault(_o.feed_name, []).append(_o.id)

# station id → outfalls whose discharge gets that station posted
STATION_OUTFALLS = {sid: [] for sid in _BY_ID}
for _o in OUTFALLS.values():
    for _sid in _o.stations:
        STATION_OUTFALLS[_sid].append(_o.id)

# basin → outfall ids
BASIN_OUTFALLS = {b: [o.id for o in OUTFALLS.values() if o.basin == b] for b in APP_BASINS}


def outfalls_for_station(sfpuc_id: str) -> list:
    return list(STATION_OUTFALLS.get(sfpuc_id, []))


def stations_for_feed_name(feed_name: str) -> list:
    """Station ids SFPUC is expected to post when the feed reports `feed_name` active."""
    out = []
    for oid in FEED_NAME_TO_OUTFALLS.get((feed_name or "").strip().upper(), []):
        for sid in OUTFALLS[oid].stations:
            if sid not in out:
                out.append(sid)
    return out
