"""Canonical registry of the 20 SFPUC shoreline monitoring stations.

One source of truth for DataSF lab ``source`` id → (names, groups,
coordinates), imported by features/site_analysis, features/alerts/monitoring,
and features/forecast/live_dashboard so their tables can't drift apart again
(before 2026-09 each had its own hand-typed copy and all three disagreed —
lab samples showed under the wrong beach names).

Ground truth (verified 2026-09-02):
  * The DataSF lab dataset (v3fv-x3ux) has exactly these 20 distinct
    ``source`` ids — BAY#305/310/315/320.1/320.2 do not exist.
  * SFPUC's getBeaches feed lists the same 20 monitoring points; each source
    id was matched to its feed station by identical coordinates.
  * Real-event confirmation: the 2026-08-31 sample of 228 MPN at
    BAY#301.1_SL matched SFPUC's posting (and this app's alert) at station
    4616 "Windsurfer Circle".

``sfpuc_name`` is the exact station name string in the getBeaches feed —
lookups keyed on live feed names must use it verbatim (note "Crissy Field
Beach East", "Ocean Beach at Sloat Boulevard").
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    name: str        # display name used across the app's pages
    sfpuc_id: str    # numeric station id in SFPUC's getBeaches feed
    sfpuc_name: str  # exact station name string in that feed
    group: str       # shoreline group for public pages: Ocean / North Shore / East Bayshore
    basin: str       # combined-sewer drainage basin: Westside / North Shore / Central / Southeast
    lat: float       # SFPUC's coordinates (fixed monitoring points, embedded
    lon: float       # rather than fetched live)


# Basins: the ocean-facing stations (Ocean Beach, Fort Funston, China, Baker)
# drain Westside; Crissy/Aquatic/Hyde drain North Shore; Mission Creek drains
# the Central basin (its own stage-1 forecast model since v4, 2026-09); the
# bayshore stations from Crane Cove south drain Southeast. Which outfalls post
# which station lives in shared/outfalls.py.
STATIONS = {
    "OCEAN#22_SL": Station("Fort Funston", "4601", "Fort Funston", "Ocean", "Westside", 37.71526, -122.50476),
    "OCEAN#21.1_SL": Station("Ocean Beach at Sloat", "4602", "Ocean Beach at Sloat Boulevard", "Ocean", "Westside", 37.73567, -122.50769),
    "OCEAN#21_SL": Station("Ocean Beach at Vicente", "4603", "Ocean Beach at Vicente Street", "Ocean", "Westside", 37.73782, -122.50825),
    "OCEAN#18_SL": Station("Ocean Beach at Balboa", "4604", "Ocean Beach at Balboa Street", "Ocean", "Westside", 37.77492, -122.51351),
    "OCEAN#19_SL": Station("Ocean Beach at Lincoln", "4605", "Ocean Beach at Lincoln Way", "Ocean", "Westside", 37.7638, -122.511),
    "OCEAN#20_SL": Station("Ocean Beach at Pacheco", "4606", "Ocean Beach at Pacheco Street", "Ocean", "Westside", 37.74891, -122.50996),
    "OCEAN#17_SL": Station("China Beach", "4607", "China Beach", "Ocean", "Westside", 37.78816, -122.49136),
    "OCEAN#16_SL": Station("Baker Beach West", "4608", "Baker Beach West", "Ocean", "Westside", 37.78977, -122.48741),
    "OCEAN#15EAST_SL": Station("Baker Beach East", "4609", "Baker Beach East", "Ocean", "Westside", 37.79258, -122.48465),
    "OCEAN#15_SL": Station("Baker Beach at Lobos Creek", "4610", "Baker Beach at Lobos Creek", "Ocean", "Westside", 37.79088, -122.48594),
    "BAY#202.5_SL": Station("Crissy Field West", "4611", "Crissy Field Beach West", "North Shore", "North Shore", 37.8069, -122.4683),
    "BAY#202.4_SL": Station("Crissy Field East", "4612", "Crissy Field Beach East", "North Shore", "North Shore", 37.8066, -122.4519),
    "BAY#211_SL": Station("Aquatic Park", "4613", "Aquatic Park", "North Shore", "North Shore", 37.8076, -122.4221),
    "BAY#210.1_SL": Station("Hyde Street Pier", "4614", "Hyde Street Pier", "North Shore", "North Shore", 37.8089, -122.4212),
    "BAY#301.2_SL": Station("Jackrabbit Beach", "4615", "Jackrabbit Beach", "East Bayshore", "Southeast", 37.7114, -122.3801),
    "BAY#301.1_SL": Station("Windsurfer Circle", "4616", "Windsurfer Circle", "East Bayshore", "Southeast", 37.7091, -122.3823),
    "BAY#300.1_SL": Station("Sunnydale Cove", "4617", "Sunnydale Cove", "East Bayshore", "Southeast", 37.7096, -122.3899),
    "BAY#220_SL": Station("Mission Creek", "4618", "Mission Creek", "East Bayshore", "Central", 37.7716, -122.397),
    "BAY#320_SL": Station("Islais Creek", "4619", "Islais Creek", "East Bayshore", "Southeast", 37.74703, -122.38793),
    "BAY#230_SL": Station("Crane Cove Park", "4620", "Crane Cove Park", "East Bayshore", "Southeast", 37.7634, -122.3868),
}

# Derived views in the shapes the consumers historically used.
STATION_NAMES = {sid: s.name for sid, s in STATIONS.items()}
STATION_BASINS = {sid: s.basin for sid, s in STATIONS.items()}

# SFPUC live-map station name → DataSF lab source ids. The two programs
# monitor the same 20 points, so this is 1:1 (kept as lists because
# consumers pick the freshest sample across the candidates).
SFPUC_TO_SFGOV_SOURCES = {s.sfpuc_name: [sid] for sid, s in STATIONS.items()}
