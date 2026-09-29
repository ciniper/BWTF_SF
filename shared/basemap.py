"""The one basemap every Leaflet map on the site draws — /signup, /analysis,
/discharges — so the maps match and the choice lives in one place.

Chase, 2026-09-28: OpenStreetMap's standard tiles drew SF's hills as orange
peak triangles and its freeways in red, which fought the station dots. Nine
keyless basemaps were compared side by side at the sign-up map's view; Esri
World Topo won (OSM's palette — pale land, green parks, blue water — without
the clutter). Three runners-up are kept here for future consideration; pick
one by changing DEFAULT. Every entry needs no API key (CARTO Positron was
tried first and now demands one; Stadia/Stamen need a key off localhost).

Templates render ``BASEMAP`` (a Jinja global set in app/wsgi.py) as
``L.tileLayer(BASEMAP.url, BASEMAP.options)``; ``options`` is passed straight
to Leaflet.
"""
from __future__ import annotations

_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{service}/MapServer/tile/{{z}}/{{y}}/{{x}}"
_ESRI_CREDIT = "Tiles &copy; <a href=\"https://www.esri.com/\">Esri</a> &mdash; Esri, HERE, Garmin, USGS, and the GIS User Community"
_OSM_CREDIT = "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors"

BASEMAPS: dict[str, dict] = {
    # E — chosen: OSM-like colours, no peak triangles, no red highways, sparse labels
    "esri_topo": {"label": "Esri World Topo Map", "url": _ESRI.format(service="World_Topo_Map"),
                  "options": {"maxZoom": 18, "attribution": _ESRI_CREDIT}},
    # F — calmer still: sandy land, blue water with depth shading, almost no roads (tiles stop at zoom 13)
    "esri_ocean": {"label": "Esri World Ocean Base", "url": _ESRI.format(service="Ocean/World_Ocean_Base"),
                   "options": {"maxZoom": 13, "attribution": _ESRI_CREDIT}},
    # C — the OSM family's alternate style: no triangles, but purple-blue major roads and loud bay labels
    "hot": {"label": "Humanitarian OSM", "url": "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
            "options": {"maxZoom": 19, "attribution": _OSM_CREDIT + ", tiles style by <a href=\"https://www.hotosm.org/\">HOT</a>"}},
    # H — the interim choice: grey canvas, only the station dots carry colour
    "esri_gray": {"label": "Esri World Light Gray Canvas", "url": _ESRI.format(service="Canvas/World_Light_Gray_Base"),
                  "options": {"maxZoom": 16, "attribution": _ESRI_CREDIT}},
    # A — what the site used until 2026-09-28; kept so the comparison can be re-run
    "osm": {"label": "OpenStreetMap standard", "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "options": {"maxZoom": 19, "attribution": _OSM_CREDIT}},
}

DEFAULT = "esri_topo"


def basemap(key: str = DEFAULT) -> dict:
    """The tile layer the pages draw: {label, url, options}."""
    return BASEMAPS[key]
